from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from ..domain import CompactionJobEnvelope, CompactionJobState
from ..runtime.types import TokenCounter
from ..storage import (
    PublicationRejected,
    PublishResult,
    RepositoryError,
    RepositoryInvariantError,
    SQLiteRepository,
    StaleLeaseError,
)
from .auditor import audit_semantic
from .compiler import compile_candidate
from .types import (
    CompilerBackend,
    CompilerErrorCode,
    CompilerInvariantError,
    SegmenterConfig,
    SemanticAuditBackend,
    SemanticAuditErrorCode,
    SemanticAuditInvariantError,
)

_REDACTED_ERROR_MESSAGE = "redacted"
_RETRYABLE_COMPILER_ERRORS = frozenset(
    {
        CompilerErrorCode.BACKEND_FAILURE,
        CompilerErrorCode.TOKEN_COUNTER_FAILURE,
    }
)
_RETRYABLE_AUDIT_ERRORS = frozenset({SemanticAuditErrorCode.BACKEND_FAILURE})
_T = TypeVar("_T")
_U = TypeVar("_U")


@dataclass(frozen=True, slots=True)
class CompactionWorkerConfig:
    worker_id: str
    lease_duration: timedelta
    token_ceiling: int
    segmenter_config: SegmenterConfig
    strict_audit: bool
    max_attempts: int
    retry_delay: timedelta
    heartbeat_interval: timedelta | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or not self.worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        if not isinstance(self.lease_duration, timedelta) or self.lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if (
            isinstance(self.token_ceiling, bool)
            or not isinstance(self.token_ceiling, int)
            or self.token_ceiling < 0
        ):
            raise ValueError("token_ceiling must be non-negative")
        if not isinstance(self.segmenter_config, SegmenterConfig):
            raise TypeError("segmenter_config must be a SegmenterConfig")
        if type(self.strict_audit) is not bool:
            raise TypeError("strict_audit must be a bool")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be positive")
        if not isinstance(self.retry_delay, timedelta) or self.retry_delay <= timedelta(0):
            raise ValueError("retry_delay must be positive")
        interval = self.heartbeat_interval
        if interval is not None and (
            not isinstance(interval, timedelta)
            or interval <= timedelta(0)
            or interval >= self.lease_duration
        ):
            raise ValueError("heartbeat_interval must be positive and shorter than lease_duration")
        if interval is None and self.lease_duration / 3 <= timedelta(0):
            raise ValueError("lease_duration is too short for the default heartbeat_interval")

    @property
    def effective_heartbeat_interval(self) -> timedelta:
        return self.heartbeat_interval or self.lease_duration / 3


@dataclass(frozen=True, slots=True)
class CompactionRunResult:
    job: CompactionJobEnvelope
    publication: PublishResult | None


@dataclass(frozen=True, slots=True)
class _Failure:
    stage: str
    code: str
    retryable: bool


class _LeaseAbandoned(Exception):
    pass


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        worker: CompactionWorker,
        job: CompactionJobEnvelope,
    ) -> None:
        if job.lease_expires_at is None:
            raise RepositoryInvariantError("working Job has no lease expiration")
        self._worker = worker
        self._job = job
        self._lease_expires_at = job.lease_expires_at
        self._lost = False
        self._error: Exception | None = None
        self._unhealthy = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    def ensure_live(self) -> None:
        if self._lost:
            raise _LeaseAbandoned
        if self._error is not None:
            raise self._error

    async def wait_until_unhealthy(self) -> None:
        await self._unhealthy.wait()

    async def _run(self) -> None:
        try:
            while True:
                await self._worker._sleep(
                    self._worker._config.effective_heartbeat_interval.total_seconds()
                )
                now = self._worker._now()
                renewed = self._worker._renew(self._job, now=now)
                if renewed.lease_expires_at is None:
                    raise RepositoryInvariantError("renewed Job has no lease expiration")
                self._job = renewed
                self._lease_expires_at = renewed.lease_expires_at
        except asyncio.CancelledError:
            raise
        except StaleLeaseError:
            self._lost = True
            self._unhealthy.set()
        except Exception as error:  # noqa: BLE001 - terminal mapping stays content-free.
            self._error = error
            self._unhealthy.set()


class CompactionWorker:
    """One dependency-injected executor for fenced durable compaction Jobs."""

    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        compiler_backend: CompilerBackend,
        counter: TokenCounter,
        config: CompactionWorkerConfig,
        audit_backend: SemanticAuditBackend | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._repository = repository
        self._compiler_backend = compiler_backend
        self._counter = counter
        self._config = config
        self._audit_backend = audit_backend
        self._clock = clock if clock is not None else _utc_now
        self._sleep = sleep if sleep is not None else asyncio.sleep

    async def run_once(self) -> CompactionRunResult | None:
        try:
            now = self._now()
            self._repository.recover_expired_leases(now=now)
            job = self._repository.claim_job(
                worker_id=self._config.worker_id,
                now=now,
                lease_expires_at=now + self._config.lease_duration,
            )
        except asyncio.CancelledError:
            raise
        except sqlite3.OperationalError:
            return None
        if job is None:
            return None

        try:
            job = self._repository.transition_job(
                job_id=job.job_id,
                owner=self._config.worker_id,
                lease_epoch=job.lease_epoch,
                to_state=CompactionJobState.COMPILING,
                now=self._now(),
            )
            request_view = self._repository.read_claimed_request_view(
                job_id=job.job_id,
                owner=self._config.worker_id,
                lease_epoch=job.lease_epoch,
                now=self._now(),
            )
            compile_now = self._now()
            job = self._renew(job, now=compile_now)
            candidate = await self._await_with_heartbeat(
                job,
                compile_candidate(
                    base_snapshot=request_view.snapshot,
                    base_capsules=request_view.capsules,
                    source_events=request_view.delta,
                    target_high_water_mark=request_view.high_water_mark,
                    token_ceiling=self._config.token_ceiling,
                    backend=self._compiler_backend,
                    counter=self._counter,
                    now=compile_now,
                    segmenter_config=self._config.segmenter_config,
                ),
            )

            snapshot = candidate.snapshot
            memberships = candidate.memberships
            if self._config.strict_audit:
                job = self._repository.transition_job(
                    job_id=job.job_id,
                    owner=self._config.worker_id,
                    lease_epoch=job.lease_epoch,
                    to_state=CompactionJobState.AUDITING,
                    now=self._now(),
                )
                audit_now = self._now()
                job = self._renew(job, now=audit_now)
                audited = await self._await_with_heartbeat(
                    job,
                    audit_semantic(
                        candidate,
                        strict_audit=True,
                        backend=self._audit_backend,
                    ),
                )
                snapshot = audited.snapshot
                memberships = audited.memberships

            job = self._repository.transition_job(
                job_id=job.job_id,
                owner=self._config.worker_id,
                lease_epoch=job.lease_epoch,
                to_state=CompactionJobState.READY_TO_COMMIT,
                candidate_snapshot_id=snapshot.snapshot_id,
                now=self._now(),
            )
            publication = self._repository.publish_snapshot(
                job_id=job.job_id,
                owner=self._config.worker_id,
                lease_epoch=job.lease_epoch,
                candidate_snapshot=snapshot,
                memberships=memberships,
                token_ceiling=self._config.token_ceiling,
                now=self._now(),
            )
            return CompactionRunResult(job=publication.job, publication=publication)
        except asyncio.CancelledError:
            raise
        except _LeaseAbandoned:
            return CompactionRunResult(job=job, publication=None)
        except StaleLeaseError:
            return CompactionRunResult(job=job, publication=None)
        except Exception as error:  # noqa: BLE001 - persist only a stable failure tuple.
            return self._record_failure(job, self._failure_for(error))

    def _renew(self, job: CompactionJobEnvelope, *, now: datetime) -> CompactionJobEnvelope:
        if job.lease_expires_at is None:
            raise RepositoryInvariantError("working Job has no lease expiration")
        lease_expires_at = now + self._config.lease_duration
        if lease_expires_at <= job.lease_expires_at:
            lease_expires_at = job.lease_expires_at + self._config.effective_heartbeat_interval
        return self._repository.renew_job_lease(
            job_id=job.job_id,
            owner=self._config.worker_id,
            lease_epoch=job.lease_epoch,
            now=now,
            lease_expires_at=lease_expires_at,
        )

    async def _await_with_heartbeat(
        self,
        job: CompactionJobEnvelope,
        operation: Awaitable[_T],
    ) -> _T:
        heartbeat = _LeaseHeartbeat(worker=self, job=job)
        heartbeat.start()
        operation_task: asyncio.Future[_T] = asyncio.ensure_future(operation)
        unhealthy_task = asyncio.create_task(heartbeat.wait_until_unhealthy())
        result: _T | None = None
        operation_error: BaseException | None = None
        try:
            done, _ = await asyncio.wait(
                (operation_task, unhealthy_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation_task in done:
                try:
                    result = operation_task.result()
                except BaseException as error:  # noqa: BLE001 - preserve CancelledError exactly.
                    operation_error = error
            if unhealthy_task in done and not operation_task.done():
                await _cancel_and_wait(operation_task)
        except asyncio.CancelledError:
            await _cancel_and_wait(operation_task)
            raise
        finally:
            await _cancel_and_wait(unhealthy_task)
            await heartbeat.stop()
        if isinstance(operation_error, asyncio.CancelledError):
            raise operation_error
        heartbeat.ensure_live()
        if operation_error is not None:
            raise operation_error
        if result is None:
            raise RepositoryInvariantError("heartbeat wait ended without an operation result")
        return result

    def _record_failure(
        self,
        job: CompactionJobEnvelope,
        failure: _Failure,
    ) -> CompactionRunResult:
        now = self._now()
        retry_at = (
            now + self._config.retry_delay
            if failure.retryable and job.attempt_count < self._config.max_attempts
            else None
        )
        try:
            failed = self._repository.fail_job(
                job_id=job.job_id,
                owner=self._config.worker_id,
                lease_epoch=job.lease_epoch,
                now=now,
                error_stage=failure.stage,
                error_code=failure.code,
                error_message=_REDACTED_ERROR_MESSAGE,
                retry_at=retry_at,
            )
        except (StaleLeaseError, sqlite3.OperationalError):
            return CompactionRunResult(job=job, publication=None)
        return CompactionRunResult(job=failed, publication=None)

    @staticmethod
    def _failure_for(error: Exception) -> _Failure:
        if isinstance(error, sqlite3.OperationalError):
            return _Failure(
                stage="REPOSITORY",
                code="SQLITE_OPERATIONAL_ERROR",
                retryable=True,
            )
        if isinstance(error, CompilerInvariantError):
            return _Failure(
                stage="COMPILING",
                code=error.code.value,
                retryable=error.code in _RETRYABLE_COMPILER_ERRORS,
            )
        if isinstance(error, SemanticAuditInvariantError):
            return _Failure(
                stage="AUDITING",
                code=error.code.value,
                retryable=error.code in _RETRYABLE_AUDIT_ERRORS,
            )
        if isinstance(error, PublicationRejected):
            return _Failure(
                stage="PUBLISHING",
                code="PUBLICATION_REJECTED",
                retryable=False,
            )
        if isinstance(error, RepositoryInvariantError):
            return _Failure(
                stage="REPOSITORY",
                code="REPOSITORY_INVARIANT",
                retryable=False,
            )
        if isinstance(error, RepositoryError):
            return _Failure(
                stage="REPOSITORY",
                code="REPOSITORY_FAILURE",
                retryable=False,
            )
        return _Failure(
            stage="WORKER",
            code="WORKER_UNEXPECTED_FAILURE",
            retryable=False,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return now


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _cancel_and_wait(task: asyncio.Future[_U]) -> None:
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 - consume an exception from a cancelled child task.
        return
