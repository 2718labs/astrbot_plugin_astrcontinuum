from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ..context_graph.candidate_verification import (
    CandidateVerificationError,
    verify_candidate,
)
from ..domain import CompactionJobEnvelope, CompactionJobState, SessionKey
from ..runtime.types import TokenCounter
from ..storage import (
    ArtifactKind,
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
    CompilerInvariantError,
    SegmenterConfig,
    SemanticAuditBackend,
    SemanticAuditInvariantError,
)

Clock = Callable[[], datetime]
FatalStorageCallback = Callable[[StorageSecurityError], Awaitable[None]]
_MIN_SQLITE_LEASE_HORIZON_SECONDS = 1.0


class ProviderBindingSource(Protocol):
    def resolve(self, session_key: SessionKey) -> CompactionProviderBinding: ...


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


class CompactionWorker:
    """One non-blocking durable worker over the fenced SQLite job state machine."""

    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        backend: CompilerBackend,
        canonical_counter: TokenCounter | None,
        canonical_profile_id: str,
        worker_id: str,
        config: CompactionWorkerConfig,
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
