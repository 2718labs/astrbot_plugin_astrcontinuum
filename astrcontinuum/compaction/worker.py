from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, TypeVar

from ..context_graph.candidate_verification import (
    CandidateVerificationError,
    verify_candidate,
)
from ..domain import CompactionJobEnvelope, CompactionJobState, SessionKey, SnapshotEnvelope
from ..runtime.types import TokenCounter
from ..storage import (
    ArtifactKind,
    PublicationRejected,
    PublishResult,
    RepositoryError,
    RepositoryInvariantError,
    SQLiteRepository,
    StaleLeaseError,
    StorageSecurityError,
    TokenMetric,
)
from .auditor import audit_semantic
from .compiler import compile_candidate
from .rendering import render_capsule
from .types import (
    AuditedCandidate,
    CompactionProviderBinding,
    CompilerBackend,
    CompilerBackendDeferred,
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
class _FencedCompactionWorkerConfig:
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
        worker: _FencedCompactionWorker,
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


class _FencedCompactionWorker:
    """One dependency-injected executor for fenced durable compaction Jobs."""

    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        compiler_backend: CompilerBackend,
        counter: TokenCounter,
        config: _FencedCompactionWorkerConfig,
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
                    event_token_counts={
                        event.event_id: event.token_count for event in request_view.delta
                    },
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
                canonical_metrics=self._candidate_compatibility_metrics(snapshot, memberships),
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

    def _candidate_compatibility_metrics(
        self,
        snapshot: object,
        memberships: object,
    ) -> tuple[TokenMetric, ...]:
        """Bridge the v0.3 injected-worker lane into the v0.2.1 metric contract."""

        if not isinstance(snapshot, SnapshotEnvelope):
            raise RepositoryInvariantError("candidate Snapshot is invalid")
        if not isinstance(memberships, tuple):
            memberships = tuple(memberships)  # type: ignore[arg-type]
        profile_id = "legacy-compatibility"
        capsule_metrics = tuple(
            TokenMetric(
                artifact_kind=ArtifactKind.CAPSULE,
                artifact_id=membership.capsule_id,
                tokenizer_profile_id=profile_id,
                token_count=self._count_compatibility_metric(render_capsule(membership.capsule)),
            )
            for membership in memberships
        )
        return (
            *capsule_metrics,
            TokenMetric(
                artifact_kind=ArtifactKind.SNAPSHOT,
                artifact_id=snapshot.snapshot_id,
                tokenizer_profile_id=profile_id,
                token_count=self._count_compatibility_metric(snapshot.rendered_context),
            ),
        )

    def _count_compatibility_metric(self, text: str) -> int:
        try:
            count = self._counter.count_text(text)
        except Exception:  # noqa: BLE001 - map adapter details to a stable worker failure.
            raise CompilerInvariantError(CompilerErrorCode.TOKEN_COUNTER_FAILURE) from None
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise CompilerInvariantError(CompilerErrorCode.TOKEN_COUNTER_INVALID)
        return count

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


Clock = Callable[[], datetime]
FatalStorageCallback = Callable[[StorageSecurityError], Awaitable[None]]
_MIN_SQLITE_LEASE_HORIZON_SECONDS = 1.0


class ProviderBindingSource(Protocol):
    def resolve(self, session_key: SessionKey) -> CompactionProviderBinding: ...


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


@dataclass(frozen=True, slots=True)
class _RuntimeCompactionWorkerConfig:
    token_ceiling: int
    lease_seconds: float = 300.0
    poll_interval_seconds: float = 1.0
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 300.0
    max_attempts: int = 3
    strict_audit: bool = False
    metric_backfill_limit: int = 32
    segmenter_config: SegmenterConfig = field(default_factory=SegmenterConfig)

    def __post_init__(self) -> None:
        if (
            isinstance(self.token_ceiling, bool)
            or not isinstance(self.token_ceiling, int)
            or self.token_ceiling < 1
        ):
            raise ValueError("token_ceiling must be a positive integer")
        positive_numbers = (
            self.lease_seconds,
            self.poll_interval_seconds,
            self.retry_base_seconds,
            self.retry_max_seconds,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
            for value in positive_numbers
        ):
            raise ValueError("worker durations must be positive numbers")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_max_seconds must not be below retry_base_seconds")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer")
        if type(self.strict_audit) is not bool:
            raise ValueError("strict_audit must be a boolean")
        if (
            isinstance(self.metric_backfill_limit, bool)
            or not isinstance(self.metric_backfill_limit, int)
            or not 1 <= self.metric_backfill_limit <= 1024
        ):
            raise ValueError("metric_backfill_limit must be between 1 and 1024")


class _RuntimeCompactionWorker:
    """One non-blocking durable worker over the fenced SQLite job state machine."""

    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        backend: CompilerBackend,
        canonical_counter: TokenCounter | None,
        canonical_profile_id: str,
        worker_id: str,
        config: _RuntimeCompactionWorkerConfig,
        semantic_backend: SemanticAuditBackend | None = None,
        clock: Clock = _utc_now,
        fatal_storage_callback: FatalStorageCallback | None = None,
        compatibility_counter: TokenCounter | None = None,
        counter: TokenCounter | None = None,
        provider_bindings: ProviderBindingSource | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        if not isinstance(canonical_profile_id, str) or not canonical_profile_id.strip():
            raise ValueError("canonical_profile_id must be non-empty")
        if compatibility_counter is not None and counter is not None:
            raise TypeError("provide only compatibility_counter")
        resolved_compatibility_counter = (
            compatibility_counter if compatibility_counter is not None else counter
        )
        if resolved_compatibility_counter is None:
            raise TypeError("compatibility_counter must be provided")
        self._repository = repository
        self._backend = backend
        self._compatibility_counter = resolved_compatibility_counter
        self._canonical_counter = canonical_counter
        self._canonical_profile_id = canonical_profile_id
        self._worker_id = worker_id
        self._config = config
        self._semantic_backend = semantic_backend
        self._clock = clock
        self._fatal_storage_callback = fatal_storage_callback
        self._provider_bindings = provider_bindings
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._closed = False
        self._task = asyncio.create_task(
            self._run(),
            name=f"astrcontinuum-compactor:{self._worker_id}",
        )

    def wake(self) -> None:
        if not self._closed:
            self._wake_event.set()

    async def close(self) -> None:
        self._closed = True
        self._wake_event.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def run_iteration(self) -> bool:
        leased = await asyncio.to_thread(
            self._claim_job,
        )
        if leased is None:
            return False

        heartbeat = asyncio.create_task(
            self._renew_lease(leased),
            name=f"astrcontinuum-lease:{leased.job_id}",
        )
        stage = "LEASED"
        provider_binding: CompactionProviderBinding | None = None
        provider_binding_captured = self._provider_bindings is not None
        primary_exception_escaping = False
        try:
            try:
                if self._provider_bindings is not None:
                    # No await has occurred since claim returned: freeze before yielding.
                    try:
                        provider_binding = self._provider_bindings.resolve(leased.session_key)
                    except CompilerBackendDeferred as error:
                        if error.code != "EXTRACTIVE_PROVIDER_UNAVAILABLE":
                            raise
                stage = "COMPILING"
                await self._backfill_metrics(leased.session_key)
                compiling = await asyncio.to_thread(
                    self._repository.transition_job,
                    job_id=leased.job_id,
                    owner=self._worker_id,
                    lease_epoch=leased.lease_epoch,
                    to_state=CompactionJobState.COMPILING,
                    now=self._clock(),
                )
                view = await asyncio.to_thread(
                    self._repository.read_compaction_view,
                    job_id=compiling.job_id,
                    owner=self._worker_id,
                    lease_epoch=compiling.lease_epoch,
                    now=self._clock(),
                )
                event_token_counts = await asyncio.to_thread(
                    self._repository.read_event_token_counts,
                    view.session_key,
                    tuple(event.event_id for event in view.delta),
                    profile_id=self._canonical_profile_id,
                )
                if set(event_token_counts) != {event.event_id for event in view.delta}:
                    raise CompilerBackendDeferred("TOKEN_METRIC_MISSING")
                candidate = await compile_candidate(
                    base_snapshot=view.snapshot,
                    base_capsules=view.capsules,
                    source_events=view.delta,
                    event_token_counts=event_token_counts,
                    target_high_water_mark=compiling.target_high_water_mark,
                    token_ceiling=self._config.token_ceiling,
                    backend=self._backend,
                    compatibility_counter=self._compatibility_counter,
                    provider_binding=provider_binding,
                    provider_binding_captured=provider_binding_captured,
                    now=self._clock(),
                    segmenter_config=self._config.segmenter_config,
                )

                if self._config.strict_audit:
                    stage = "AUDITING"
                    await asyncio.to_thread(
                        self._repository.transition_job,
                        job_id=compiling.job_id,
                        owner=self._worker_id,
                        lease_epoch=compiling.lease_epoch,
                        to_state=CompactionJobState.AUDITING,
                        now=self._clock(),
                    )
                audited = await audit_semantic(
                    candidate,
                    strict_audit=self._config.strict_audit,
                    backend=self._semantic_backend,
                )

                stage = "VERIFYING"
                await asyncio.to_thread(verify_candidate, audited)
                canonical_metrics = await asyncio.to_thread(
                    self._candidate_canonical_metrics,
                    audited,
                )

                stage = "READY_TO_COMMIT"
                ready = await asyncio.to_thread(
                    self._repository.transition_job,
                    job_id=compiling.job_id,
                    owner=self._worker_id,
                    lease_epoch=compiling.lease_epoch,
                    to_state=CompactionJobState.READY_TO_COMMIT,
                    candidate_snapshot_id=audited.snapshot.snapshot_id,
                    now=self._clock(),
                )
                stage = "PUBLISHING"
                await asyncio.to_thread(
                    self._repository.publish_snapshot,
                    job_id=ready.job_id,
                    owner=self._worker_id,
                    lease_epoch=ready.lease_epoch,
                    candidate_snapshot=audited.snapshot,
                    memberships=audited.memberships,
                    canonical_metrics=canonical_metrics,
                    token_ceiling=self._config.token_ceiling,
                    now=self._clock(),
                )
            except asyncio.CancelledError:
                raise
            except StorageSecurityError:
                raise
            except CompilerBackendDeferred as error:
                await self._persist_deferred(leased, stage=stage, error=error)
            except StaleLeaseError:
                return True
            except Exception as error:  # noqa: BLE001 - iteration boundary persists stable data
                await self._persist_failure(leased, stage=stage, error=error)
        except BaseException:
            primary_exception_escaping = True
            raise
        finally:
            heartbeat.cancel()
            heartbeat_result = (await asyncio.gather(heartbeat, return_exceptions=True))[0]
            if not primary_exception_escaping and isinstance(
                heartbeat_result, StorageSecurityError
            ):
                raise heartbeat_result
        return True

    async def _persist_deferred(
        self,
        leased: CompactionJobEnvelope,
        *,
        stage: str,
        error: CompilerBackendDeferred,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._repository.fail_job,
                job_id=leased.job_id,
                owner=self._worker_id,
                lease_epoch=leased.lease_epoch,
                now=self._clock(),
                error_stage=stage,
                error_code=error.code,
                error_message=error.code,
                retry_at=self._clock() + timedelta(seconds=self._config.retry_base_seconds),
                preserve_attempt=error.code
                in {
                    "EXTRACTIVE_PROVIDER_UNAVAILABLE",
                    "TOKEN_METRIC_MISSING",
                    "TOKEN_METRIC_UNAVAILABLE",
                },
            )
        except StaleLeaseError:
            return

    async def _backfill_metrics(self, session_key: SessionKey) -> None:
        if self._canonical_counter is None:
            raise CompilerBackendDeferred("TOKEN_METRIC_UNAVAILABLE")
        batch = await asyncio.to_thread(
            self._repository.read_metric_backfill_batch,
            session_key,
            profile_id=self._canonical_profile_id,
            limit=self._config.metric_backfill_limit,
        )
        metrics = tuple(
            TokenMetric(
                artifact_kind=artifact.artifact_kind,
                artifact_id=artifact.artifact_id,
                tokenizer_profile_id=self._canonical_profile_id,
                token_count=self._count_canonical(artifact.text),
            )
            for artifact in batch
        )
        if metrics:
            await asyncio.to_thread(
                self._repository.write_metric_backfill_batch,
                session_key,
                metrics,
                now=self._clock(),
            )

    def _candidate_canonical_metrics(
        self,
        candidate: AuditedCandidate,
    ) -> tuple[TokenMetric, ...]:
        memberships = candidate.memberships
        snapshot = candidate.snapshot
        return (
            *(
                TokenMetric(
                    artifact_kind=ArtifactKind.CAPSULE,
                    artifact_id=membership.capsule_id,
                    tokenizer_profile_id=self._canonical_profile_id,
                    token_count=self._count_canonical(render_capsule(membership.capsule)),
                )
                for membership in memberships
            ),
            TokenMetric(
                artifact_kind=ArtifactKind.SNAPSHOT,
                artifact_id=snapshot.snapshot_id,
                tokenizer_profile_id=self._canonical_profile_id,
                token_count=self._count_canonical(snapshot.rendered_context),
            ),
        )

    def _count_canonical(self, text: str) -> int:
        counter = self._canonical_counter
        if counter is None:
            raise CompilerBackendDeferred("TOKEN_METRIC_UNAVAILABLE")
        try:
            count = counter.count_text(text)
        except Exception:  # noqa: BLE001 - stable deferral hides adapter details
            raise CompilerBackendDeferred("TOKEN_METRIC_UNAVAILABLE") from None
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise CompilerBackendDeferred("TOKEN_METRIC_UNAVAILABLE")
        return count

    async def _renew_lease(self, leased: CompactionJobEnvelope) -> None:
        interval = max(self._config.lease_seconds / 3.0, 0.01)
        while True:
            await asyncio.to_thread(
                self._renew_job_lease,
                leased,
            )
            await asyncio.sleep(interval)

    def _claim_job(self) -> CompactionJobEnvelope | None:
        now = self._clock()
        return self._repository.claim_job(
            worker_id=self._worker_id,
            now=now,
            lease_expires_at=now + timedelta(seconds=self._lease_horizon_seconds()),
        )

    def _renew_job_lease(self, leased: CompactionJobEnvelope) -> None:
        now = self._clock()
        self._repository.renew_job_lease(
            job_id=leased.job_id,
            owner=self._worker_id,
            lease_epoch=leased.lease_epoch,
            now=now,
            lease_expires_at=now + timedelta(seconds=self._lease_horizon_seconds()),
        )

    def _lease_horizon_seconds(self) -> float:
        # Sub-second absolute expiries can elapse while an IMMEDIATE SQLite
        # transaction waits for its write lock. Keep production-sized leases
        # unchanged while giving deliberately tiny leases a bounded horizon.
        return max(
            self._config.lease_seconds,
            _MIN_SQLITE_LEASE_HORIZON_SECONDS,
        )

    async def _persist_failure(
        self,
        leased: CompactionJobEnvelope,
        *,
        stage: str,
        error: Exception,
    ) -> None:
        code = self._error_code(error)
        retry_at = None
        if leased.attempt_count < self._config.max_attempts:
            exponent = max(0, leased.attempt_count - 1)
            delay = min(
                self._config.retry_base_seconds * (2**exponent),
                self._config.retry_max_seconds,
            )
            retry_at = self._clock() + timedelta(seconds=delay)
        try:
            await asyncio.to_thread(
                self._repository.fail_job,
                job_id=leased.job_id,
                owner=self._worker_id,
                lease_epoch=leased.lease_epoch,
                now=self._clock(),
                error_stage=stage,
                error_code=code,
                error_message=code,
                retry_at=retry_at,
            )
        except StaleLeaseError:
            return

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, CompilerInvariantError):
            return error.code.value
        if isinstance(error, SemanticAuditInvariantError):
            return error.code.value
        if isinstance(error, CandidateVerificationError):
            return error.code.value
        return "COMPACTION_WORKER_FAILURE"

    async def _run(self) -> None:
        while not self._closed:
            self._wake_event.clear()
            try:
                await asyncio.to_thread(
                    self._repository.recover_expired_leases,
                    now=self._clock(),
                )
                did_work = await self.run_iteration()
            except asyncio.CancelledError:
                raise
            except StorageSecurityError as error:
                self._closed = True
                callback = self._fatal_storage_callback
                try:
                    if callback is not None:
                        await callback(error)
                finally:
                    self._task = None
                return
            except Exception:  # noqa: BLE001 - scheduler must survive unrelated failures
                did_work = False
            if did_work:
                continue
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._config.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass


@dataclass(frozen=True, slots=True)
class CompactionWorkerConfig:
    """One public configuration surface for the retained v0.2.1 and v0.3 lanes."""

    token_ceiling: int
    segmenter_config: SegmenterConfig = field(default_factory=SegmenterConfig)
    strict_audit: bool = False
    max_attempts: int = 3
    worker_id: str | None = None
    lease_duration: timedelta | None = None
    retry_delay: timedelta | None = None
    heartbeat_interval: timedelta | None = None
    lease_seconds: float = 300.0
    poll_interval_seconds: float = 1.0
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 300.0
    metric_backfill_limit: int = 32

    def __post_init__(self) -> None:
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
        legacy_fields = (self.worker_id, self.lease_duration, self.retry_delay)
        if any(value is not None for value in legacy_fields) and any(
            value is None for value in legacy_fields
        ):
            raise ValueError("worker_id, lease_duration, and retry_delay must be supplied together")

    def _fenced(self) -> _FencedCompactionWorkerConfig:
        if self.worker_id is None or self.lease_duration is None or self.retry_delay is None:
            raise TypeError(
                "v0.3 injected workers require worker_id, lease_duration, and retry_delay"
            )
        return _FencedCompactionWorkerConfig(
            worker_id=self.worker_id,
            lease_duration=self.lease_duration,
            token_ceiling=self.token_ceiling,
            segmenter_config=self.segmenter_config,
            strict_audit=self.strict_audit,
            max_attempts=self.max_attempts,
            retry_delay=self.retry_delay,
            heartbeat_interval=self.heartbeat_interval,
        )

    def _runtime(self) -> _RuntimeCompactionWorkerConfig:
        return _RuntimeCompactionWorkerConfig(
            token_ceiling=self.token_ceiling,
            lease_seconds=self.lease_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
            retry_base_seconds=self.retry_base_seconds,
            retry_max_seconds=self.retry_max_seconds,
            max_attempts=self.max_attempts,
            strict_audit=self.strict_audit,
            metric_backfill_limit=self.metric_backfill_limit,
            segmenter_config=self.segmenter_config,
        )


class CompactionWorker:
    """Dispatch to the fenced v0.3 worker or the provider-bound v0.2.1 runtime."""

    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        config: CompactionWorkerConfig,
        compiler_backend: CompilerBackend | None = None,
        counter: TokenCounter | None = None,
        audit_backend: SemanticAuditBackend | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        backend: CompilerBackend | None = None,
        canonical_counter: TokenCounter | None = None,
        canonical_profile_id: str | None = None,
        worker_id: str | None = None,
        semantic_backend: SemanticAuditBackend | None = None,
        clock: Clock | None = None,
        fatal_storage_callback: FatalStorageCallback | None = None,
        compatibility_counter: TokenCounter | None = None,
        provider_bindings: ProviderBindingSource | None = None,
    ) -> None:
        if not isinstance(config, CompactionWorkerConfig):
            raise TypeError("config must be a CompactionWorkerConfig")
        if compiler_backend is not None:
            if backend is not None:
                raise TypeError("provide only one compaction backend")
            if counter is None:
                raise TypeError("v0.3 injected workers require counter")
            self._fenced: _FencedCompactionWorker | None = _FencedCompactionWorker(
                repository=repository,
                compiler_backend=compiler_backend,
                counter=counter,
                config=config._fenced(),
                audit_backend=audit_backend,
                clock=clock,
                sleep=sleep,
            )
            self._runtime: _RuntimeCompactionWorker | None = None
            return
        if backend is None:
            raise TypeError("provider-bound workers require backend")
        if canonical_profile_id is None or worker_id is None:
            raise TypeError("provider-bound workers require canonical_profile_id and worker_id")
        self._fenced = None
        self._runtime = _RuntimeCompactionWorker(
            repository=repository,
            backend=backend,
            canonical_counter=canonical_counter,
            canonical_profile_id=canonical_profile_id,
            worker_id=worker_id,
            config=config._runtime(),
            semantic_backend=semantic_backend,
            clock=clock or _utc_now,
            fatal_storage_callback=fatal_storage_callback,
            compatibility_counter=compatibility_counter,
            counter=counter,
            provider_bindings=provider_bindings,
        )

    def __getattr__(self, name: str) -> object:
        """Retain the runtime worker's established diagnostic surface."""

        runtime = self._runtime
        if runtime is not None:
            return getattr(runtime, name)
        fenced = self._fenced
        if fenced is not None:
            return getattr(fenced, name)
        raise AttributeError(name)

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._runtime.task if self._runtime is not None else None

    async def start(self) -> None:
        if self._runtime is not None:
            await self._runtime.start()

    def wake(self) -> None:
        if self._runtime is not None:
            self._runtime.wake()

    async def close(self) -> None:
        if self._runtime is not None:
            await self._runtime.close()

    async def run_iteration(self) -> bool:
        if self._runtime is not None:
            return await self._runtime.run_iteration()
        assert self._fenced is not None
        return await self._fenced.run_once() is not None

    async def run_once(self) -> CompactionRunResult | None:
        if self._fenced is not None:
            return await self._fenced.run_once()
        assert self._runtime is not None
        await self._runtime.run_iteration()
        return None
