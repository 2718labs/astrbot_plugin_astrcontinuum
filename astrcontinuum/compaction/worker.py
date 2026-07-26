from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..domain import CompactionJobEnvelope, CompactionJobState
from ..runtime.types import TokenCounter
from ..storage import SQLiteRepository, StaleLeaseError
from .auditor import audit_semantic
from .compiler import compile_candidate
from .types import (
    CompilerBackend,
    CompilerBackendDeferred,
    CompilerInvariantError,
    SegmenterConfig,
    SemanticAuditBackend,
    SemanticAuditInvariantError,
)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class CompactionWorkerConfig:
    token_ceiling: int
    lease_seconds: float = 300.0
    poll_interval_seconds: float = 1.0
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 300.0
    max_attempts: int = 3
    strict_audit: bool = False
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


class CompactionWorker:
    """One non-blocking durable worker over the fenced SQLite job state machine."""

    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        backend: CompilerBackend,
        counter: TokenCounter,
        worker_id: str,
        config: CompactionWorkerConfig,
        semantic_backend: SemanticAuditBackend | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        self._repository = repository
        self._backend = backend
        self._counter = counter
        self._worker_id = worker_id
        self._config = config
        self._semantic_backend = semantic_backend
        self._clock = clock
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
        now = self._clock()
        leased = await asyncio.to_thread(
            self._repository.claim_job,
            worker_id=self._worker_id,
            now=now,
            lease_expires_at=now + timedelta(seconds=self._config.lease_seconds),
        )
        if leased is None:
            return False

        heartbeat = asyncio.create_task(
            self._renew_lease(leased),
            name=f"astrcontinuum-lease:{leased.job_id}",
        )
        stage = "LEASED"
        try:
            stage = "COMPILING"
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
            candidate = await compile_candidate(
                base_snapshot=view.snapshot,
                base_capsules=view.capsules,
                source_events=view.delta,
                target_high_water_mark=compiling.target_high_water_mark,
                token_ceiling=self._config.token_ceiling,
                backend=self._backend,
                counter=self._counter,
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
                token_ceiling=self._config.token_ceiling,
                now=self._clock(),
            )
        except asyncio.CancelledError:
            raise
        except CompilerBackendDeferred as error:
            await self._persist_deferred(leased, stage=stage, error=error)
        except StaleLeaseError:
            return True
        except Exception as error:  # noqa: BLE001 - iteration boundary persists stable data
            await self._persist_failure(leased, stage=stage, error=error)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
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
            )
        except StaleLeaseError:
            return

    async def _renew_lease(self, leased: CompactionJobEnvelope) -> None:
        interval = max(self._config.lease_seconds / 3.0, 0.01)
        while True:
            await asyncio.sleep(interval)
            now = self._clock()
            await asyncio.to_thread(
                self._repository.renew_job_lease,
                job_id=leased.job_id,
                owner=self._worker_id,
                lease_epoch=leased.lease_epoch,
                now=now,
                lease_expires_at=now + timedelta(seconds=self._config.lease_seconds),
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
            except Exception:  # noqa: BLE001 - scheduler must survive unrelated failures
                did_work = False
            if did_work:
                continue
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._config.poll_interval_seconds,
                )
            except TimeoutError:
                pass
