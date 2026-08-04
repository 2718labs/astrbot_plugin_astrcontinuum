from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac
from astrcontinuum.compaction.worker import CompactionWorker, CompactionWorkerConfig
from astrcontinuum.storage import CanonicalMetricObservation
from tests.storage.security_testkit import secure_repository, storage_test_codec

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
LEASE_DURATION = timedelta(minutes=5)
RETRY_DELAY = timedelta(minutes=1)
REDACTED = "redacted"
SECRET = "SENTINEL-PRIVATE-WORKER-ERROR"


class FixedClock:
    def __init__(self, *values: datetime) -> None:
        self._values = list(values)
        self._last = values[-1]

    def __call__(self) -> datetime:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


class ManualClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, duration: timedelta) -> None:
        self.now += duration


class GateSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.entered = asyncio.Event()
        self._permits: asyncio.Queue[None] = asyncio.Queue()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.entered.set()
        await self._permits.get()

    def release_once(self) -> None:
        self._permits.put_nowait(None)


class RecordingCounter:
    def __init__(self, result: int = 3) -> None:
        self.result = result
        self.texts: list[str] = []

    def count_text(self, text: str) -> int:
        self.texts.append(text)
        return self.result


class RecordingCompiler:
    def __init__(
        self,
        *,
        store: ac.SQLiteRepository | None = None,
        key: ac.SessionKey | None = None,
        error: Exception | None = None,
        invalid: bool = False,
    ) -> None:
        self.store = store
        self.key = key
        self.error = error
        self.invalid = invalid
        self.requests: list[ac.CompilationRequest] = []

    async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        self.requests.append(request)
        if self.store is not None and self.key is not None:
            capture(self.store, 3, self.key)
        if self.error is not None:
            raise self.error
        return self._output(request)

    def _output(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        source_ids = tuple(event.event_id for event in request.source_events)
        first = request.source_events[0]
        last = request.source_events[-1]
        token_cost = 101 if self.invalid else 3
        capsule = ac.ContextCapsuleEnvelope(
            capsule_id=f"compiled-{last.sequence}",
            schema_version="1.0.0",
            level=ac.CapsuleLevel.MICRO,
            session_key=first.session_key,
            covered_event_start=first.sequence,
            covered_event_end=last.sequence,
            source_event_ids=source_ids,
            goals=(
                ac.CapsuleClaim(
                    claim_id=f"goal-{last.sequence}",
                    text="compiled goal",
                    status=ac.SemanticStatus.ACTIVE,
                    confidence=1.0,
                    source_event_ids=source_ids,
                ),
            ),
            constraints=(),
            decisions=(),
            progress=(),
            open_loops=(),
            preferences=(),
            entities=(),
            emotional_context=(),
            exact_anchors=(
                ac.CapsuleAnchor(
                    anchor_id=f"anchor-{last.sequence}",
                    anchor_type=ac.AnchorType.NAME,
                    exact_text="compiled anchor",
                    source_event_ids=source_ids,
                    status=ac.AnchorStatus.ACTIVE,
                    importance=1.0,
                ),
            ),
            dependencies=(),
            narrative_summary="compiled context",
            token_cost=token_cost,
            quality=ac.CapsuleQuality(
                mechanical_passed=True,
                source_coverage=1.0,
                anchor_recall=1.0,
                unsupported_critical_claims=0,
                coverage_gap=0,
            ),
            created_at=NOW,
        )
        return ac.CompilerOutput(capsules=(capsule,), rendered_context="compiled context")


class BlockingCompiler(RecordingCompiler):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        self.requests.append(request)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self._output(request)


class RenewalTrackingRepository:
    def __init__(self, store: ac.SQLiteRepository, *, notify_on_renewal: int) -> None:
        self._store = store
        self._notify_on_renewal = notify_on_renewal
        self.renewal_count = 0
        self.renewed = asyncio.Event()

    def renew_job_lease(self, **kwargs: object) -> ac.CompactionJobEnvelope:
        job = self._store.renew_job_lease(**kwargs)
        self.renewal_count += 1
        if self.renewal_count >= self._notify_on_renewal:
            self.renewed.set()
        return job

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)


class StartupOperationalErrorRepository:
    def __init__(self, operation: str) -> None:
        self.operation = operation
        self.calls: list[str] = []

    def recover_expired_leases(self, *, now: datetime) -> tuple[object, ...]:
        self.calls.append("recover")
        if self.operation == "recover":
            raise sqlite3.OperationalError("database is locked")
        return ()

    def claim_job(self, **kwargs: object) -> None:
        self.calls.append("claim")
        if self.operation == "claim":
            raise sqlite3.OperationalError("database is busy")


class OperationalReadRepository:
    def __init__(self, store: ac.SQLiteRepository) -> None:
        self._store = store

    def read_claimed_request_view(self, **kwargs: object) -> ac.RequestView:
        raise sqlite3.OperationalError(f"database is locked: {SECRET}")

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)


class RecordingAuditor:
    def __init__(self) -> None:
        self.requests: list[ac.SemanticAuditRequest] = []

    async def audit(self, request: ac.SemanticAuditRequest) -> ac.SemanticAuditReport:
        self.requests.append(request)
        return ac.SemanticAuditReport(passed=True, failure_codes=())


class BlockingAuditor(RecordingAuditor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def audit(self, request: ac.SemanticAuditRequest) -> ac.SemanticAuditReport:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return ac.SemanticAuditReport(passed=True, failure_codes=())


def session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="worker-session",
        group_id=None,
        user_id="worker-user",
        conversation_id="worker-conversation",
        persona_id=None,
    )


def repository(data_dir: Path) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    return secure_repository(factory)


def capture(store: ac.SQLiteRepository, sequence: int, key: ac.SessionKey) -> ac.EventEnvelope:
    return store.capture_user_event(
        event_id=f"event-{sequence}",
        session_key=key,
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        canonical=CanonicalMetricObservation("test-canonical-v1", None),
        created_at=NOW,
    )


def raise_intent(store: ac.SQLiteRepository, key: ac.SessionKey) -> ac.CompactionJobEnvelope:
    return store.raise_compaction_intent(
        job_id="job-1",
        session_key=key,
        target_high_water_mark=2,
        now=NOW,
    )


def seed_winning_snapshot(store: ac.SQLiteRepository, key: ac.SessionKey) -> ac.SnapshotEnvelope:
    codec = storage_test_codec()
    source_ids = ("event-1", "event-2")
    capsule = ac.ContextCapsuleEnvelope(
        capsule_id="winner-capsule",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=key,
        covered_event_start=1,
        covered_event_end=2,
        source_event_ids=source_ids,
        goals=(
            ac.CapsuleClaim(
                claim_id="winner-goal",
                text="winner goal",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=source_ids,
            ),
        ),
        constraints=(),
        decisions=(),
        progress=(),
        open_loops=(),
        preferences=(),
        entities=(),
        emotional_context=(),
        exact_anchors=(
            ac.CapsuleAnchor(
                anchor_id="winner-anchor",
                anchor_type=ac.AnchorType.NAME,
                exact_text="winner anchor",
                source_event_ids=source_ids,
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(),
        narrative_summary="winner context",
        token_cost=3,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    )
    snapshot = ac.SnapshotEnvelope(
        snapshot_id="winner-snapshot",
        session_key=key,
        base_snapshot_id=None,
        covered_event_end=2,
        source_high_water_mark=2,
        capsule_ids=(capsule.capsule_id,),
        exact_anchor_ids=("winner-anchor",),
        rendered_context="winner context",
        token_cost=3,
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.COMMITTED,
        created_at=NOW,
        committed_at=NOW,
    )
    timestamp = "2026-08-03T12:00:00.000000Z"
    with store.factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO capsules (
                capsule_id, session_key_hash, level, covered_event_start,
                covered_event_end, canonical_capsule_json, token_cost_envelope,
                source_coverage, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capsule.capsule_id,
                key.session_key_hash,
                capsule.level.value,
                capsule.covered_event_start,
                capsule.covered_event_end,
                codec.encrypt_object_json(
                    "capsules",
                    "canonical_capsule_json",
                    capsule.capsule_id,
                    json.dumps(capsule.model_dump(mode="json"), separators=(",", ":")),
                ),
                codec.encrypt_non_negative_int(
                    "capsules",
                    "token_cost",
                    capsule.capsule_id,
                    capsule.token_cost,
                ),
                capsule.quality.source_coverage,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO snapshots (
                snapshot_id, session_key_hash, base_snapshot_id, covered_event_end,
                source_high_water_mark, exact_anchor_ids_json, rendered_context,
                token_cost_envelope, audit_outcome, lifecycle_state, created_at, committed_at
            ) VALUES (?, ?, NULL, 2, 2, ?, ?, ?, ?, 'COMMITTED', ?, ?)
            """,
            (
                snapshot.snapshot_id,
                key.session_key_hash,
                codec.encrypt_array_json(
                    "snapshots",
                    "exact_anchor_ids_json",
                    snapshot.snapshot_id,
                    json.dumps(list(snapshot.exact_anchor_ids), separators=(",", ":")),
                ),
                codec.encrypt_text(
                    "snapshots",
                    "rendered_context",
                    snapshot.snapshot_id,
                    snapshot.rendered_context,
                ),
                codec.encrypt_non_negative_int(
                    "snapshots",
                    "token_cost",
                    snapshot.snapshot_id,
                    snapshot.token_cost,
                ),
                codec.encrypt_object_json(
                    "snapshots",
                    "audit_outcome",
                    snapshot.snapshot_id,
                    json.dumps(
                        snapshot.audit_outcome.model_dump(mode="json"), separators=(",", ":")
                    ),
                ),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO snapshot_capsules (snapshot_id, ordinal, capsule_id, slot)
            VALUES (?, 0, ?, 'memory')
            """,
            (snapshot.snapshot_id, capsule.capsule_id),
        )
        connection.execute(
            """
            INSERT INTO active_snapshots (session_key_hash, snapshot_id, pointer_version, updated_at)
            VALUES (?, ?, 1, ?)
            """,
            (key.session_key_hash, snapshot.snapshot_id, timestamp),
        )
    return snapshot


def worker(
    store: object,
    compiler: RecordingCompiler,
    counter: RecordingCounter,
    *,
    strict_audit: bool = False,
    auditor: RecordingAuditor | None = None,
    clock: FixedClock | ManualClock | None = None,
    max_attempts: int = 2,
    lease_duration: timedelta = LEASE_DURATION,
    heartbeat_interval: timedelta | None = None,
    sleep: GateSleep | None = None,
    worker_id: str = "worker-1",
) -> CompactionWorker:
    kwargs: dict[str, object] = {
        "repository": store,
        "compiler_backend": compiler,
        "counter": counter,
        "audit_backend": auditor,
        "clock": clock if clock is not None else FixedClock(NOW),
        "config": CompactionWorkerConfig(
            worker_id=worker_id,
            lease_duration=lease_duration,
            token_ceiling=100,
            segmenter_config=ac.SegmenterConfig(),
            strict_audit=strict_audit,
            max_attempts=max_attempts,
            retry_delay=RETRY_DELAY,
            heartbeat_interval=heartbeat_interval,
        ),
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    return CompactionWorker(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_disabled_audit_compiles_frozen_claim_and_commits(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    first = capture(store, 1, key)
    second = capture(store, 2, key)
    raise_intent(store, key)
    compiler = RecordingCompiler(store=store, key=key)

    result = await worker(store, compiler, RecordingCounter()).run_once()

    assert result is not None
    assert result.publication is not None
    assert result.publication.outcome is ac.PublishOutcome.COMMITTED
    assert result.job.state is ac.CompactionJobState.COMMITTED
    assert len(compiler.requests) == 1
    assert compiler.requests[0].source_events == (first, second)
    assert store.read_request_view(key).high_water_mark == 3
    assert store.read_request_view(key).snapshot == result.publication.winner
    assert tuple(event.sequence for event in store.read_request_view(key).delta) == (3,)


@pytest.mark.asyncio
async def test_strict_audit_calls_auditor_before_commit(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)
    compiler = RecordingCompiler()
    auditor = RecordingAuditor()

    result = await worker(
        store,
        compiler,
        RecordingCounter(),
        strict_audit=True,
        auditor=auditor,
    ).run_once()

    assert result is not None
    assert result.publication is not None
    assert len(auditor.requests) == 1
    assert result.publication.winner.audit_outcome.semantic_status is ac.SemanticAuditStatus.PASSED


@pytest.mark.asyncio
async def test_transient_compiler_backend_failure_enters_redacted_retry_wait(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)

    result = await worker(
        store,
        RecordingCompiler(error=RuntimeError(SECRET)),
        RecordingCounter(),
    ).run_once()

    assert result is not None
    assert result.publication is None
    assert result.job.state is ac.CompactionJobState.RETRY_WAIT
    assert (result.job.error_stage, result.job.error_code, result.job.error_message) == (
        "COMPILING",
        ac.CompilerErrorCode.BACKEND_FAILURE.value,
        REDACTED,
    )
    assert SECRET not in str(result.job)


@pytest.mark.asyncio
async def test_permanent_compiler_validation_failure_is_not_bypassed_by_disabled_audit(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)

    result = await worker(
        store,
        RecordingCompiler(invalid=True),
        RecordingCounter(),
        strict_audit=False,
    ).run_once()

    assert result is not None
    assert result.publication is None
    assert result.job.state is ac.CompactionJobState.FAILED
    assert result.job.error_code == ac.CompilerErrorCode.PERMANENT_VALIDATION_FAILED.value
    assert result.job.error_message == REDACTED


@pytest.mark.asyncio
async def test_stale_lease_abandons_without_publication_or_failure_mutation(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)
    stale_at = NOW + LEASE_DURATION
    clock = FixedClock(NOW, NOW, stale_at)
    compiler = RecordingCompiler()

    result = await worker(
        store,
        compiler,
        RecordingCounter(),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.publication is None
    assert compiler.requests == []
    assert store.read_request_view(key).snapshot is None
    recovered = store.recover_expired_leases(now=stale_at)
    assert len(recovered) == 1
    assert recovered[0].state is ac.CompactionJobState.PENDING
    assert recovered[0].error_code is None


@pytest.mark.asyncio
async def test_iteration_recovers_expired_ready_job_then_recompiles_it(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)
    expired_now = NOW - timedelta(minutes=10)
    leased = store.claim_job(
        worker_id="previous-worker",
        now=expired_now,
        lease_expires_at=expired_now + timedelta(minutes=1),
    )
    assert leased is not None
    compiling = store.transition_job(
        job_id=leased.job_id,
        owner="previous-worker",
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=expired_now,
    )
    store.transition_job(
        job_id=compiling.job_id,
        owner="previous-worker",
        lease_epoch=compiling.lease_epoch,
        to_state=ac.CompactionJobState.READY_TO_COMMIT,
        candidate_snapshot_id="expired-candidate",
        now=expired_now,
    )
    compiler = RecordingCompiler()

    result = await worker(store, compiler, RecordingCounter()).run_once()

    assert result is not None
    assert result.publication is not None
    assert result.job.state is ac.CompactionJobState.COMMITTED
    assert result.job.attempt_count == 2
    assert result.publication.winner.snapshot_id != "expired-candidate"
    assert len(compiler.requests) == 1


@pytest.mark.asyncio
async def test_short_heartbeat_interval_does_not_shorten_initial_claim_lease(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)
    clock = FixedClock(NOW, NOW, NOW + timedelta(seconds=1))

    result = await worker(
        store,
        RecordingCompiler(),
        RecordingCounter(),
        clock=clock,
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(milliseconds=5),
    ).run_once()

    assert result is not None
    assert result.publication is not None
    assert result.publication.outcome is ac.PublishOutcome.COMMITTED


@pytest.mark.asyncio
async def test_heartbeat_keeps_blocked_compiler_live_beyond_foreground_lease(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)
    clock = ManualClock(NOW)
    tracked = RenewalTrackingRepository(store, notify_on_renewal=2)
    compiler = BlockingCompiler()
    sleep = GateSleep()

    task = asyncio.create_task(
        worker(
            tracked,
            compiler,
            RecordingCounter(),
            clock=clock,
            lease_duration=timedelta(seconds=30),
            heartbeat_interval=timedelta(milliseconds=5),
            sleep=sleep,
        ).run_once()
    )
    await compiler.started.wait()
    await sleep.entered.wait()
    clock.advance(timedelta(seconds=29))
    sleep.release_once()
    await tracked.renewed.wait()
    clock.advance(timedelta(seconds=20))
    compiler.release.set()

    result = await task

    assert result is not None
    assert result.publication is not None
    assert result.publication.outcome is ac.PublishOutcome.COMMITTED
    assert tracked.renewal_count >= 2


@pytest.mark.asyncio
async def test_heartbeat_keeps_blocked_strict_auditor_live_beyond_lease(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)
    clock = ManualClock(NOW)
    tracked = RenewalTrackingRepository(store, notify_on_renewal=3)
    auditor = BlockingAuditor()
    sleep = GateSleep()

    task = asyncio.create_task(
        worker(
            tracked,
            RecordingCompiler(),
            RecordingCounter(),
            strict_audit=True,
            auditor=auditor,
            clock=clock,
            lease_duration=timedelta(seconds=30),
            heartbeat_interval=timedelta(milliseconds=5),
            sleep=sleep,
        ).run_once()
    )
    await auditor.started.wait()
    await sleep.entered.wait()
    clock.advance(timedelta(seconds=29))
    sleep.release_once()
    await tracked.renewed.wait()
    clock.advance(timedelta(seconds=20))
    auditor.release.set()

    result = await task

    assert result is not None
    assert result.publication is not None
    assert result.publication.outcome is ac.PublishOutcome.COMMITTED
    assert tracked.renewal_count >= 3


@pytest.mark.asyncio
async def test_heartbeat_lease_loss_cancels_blocked_compiler_and_abandons_old_attempt(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)
    clock = ManualClock(NOW)
    compiler = BlockingCompiler()
    sleep = GateSleep()

    task = asyncio.create_task(
        worker(
            store,
            compiler,
            RecordingCounter(),
            clock=clock,
            lease_duration=timedelta(seconds=30),
            heartbeat_interval=timedelta(milliseconds=5),
            sleep=sleep,
        ).run_once()
    )
    await compiler.started.wait()
    await sleep.entered.wait()
    clock.advance(timedelta(seconds=31))
    sleep.release_once()

    result = await task

    assert result is not None
    assert result.publication is None
    assert compiler.cancelled is True
    recovered = store.recover_expired_leases(now=clock())
    assert len(recovered) == 1
    assert recovered[0].state is ac.CompactionJobState.PENDING
    assert recovered[0].error_code is None

    replacement = await worker(
        store,
        RecordingCompiler(),
        RecordingCounter(),
        clock=FixedClock(clock()),
        lease_duration=timedelta(seconds=30),
        heartbeat_interval=timedelta(milliseconds=5),
        worker_id="worker-2",
    ).run_once()

    assert replacement is not None
    assert replacement.publication is not None
    assert replacement.job.state is ac.CompactionJobState.COMMITTED
    assert replacement.job.lease_epoch == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["recover", "claim"])
async def test_startup_operational_error_returns_none(operation: str) -> None:
    store = StartupOperationalErrorRepository(operation)

    result = await worker(store, RecordingCompiler(), RecordingCounter()).run_once()

    assert result is None
    assert store.calls == (["recover"] if operation == "recover" else ["recover", "claim"])


@pytest.mark.asyncio
async def test_live_sqlite_operational_error_becomes_redacted_retry_wait(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)

    result = await worker(
        OperationalReadRepository(store),
        RecordingCompiler(),
        RecordingCounter(),
    ).run_once()

    assert result is not None
    assert result.publication is None
    assert result.job.state is ac.CompactionJobState.RETRY_WAIT
    assert (result.job.error_stage, result.job.error_code, result.job.error_message) == (
        "REPOSITORY",
        "SQLITE_OPERATIONAL_ERROR",
        REDACTED,
    )


@pytest.mark.asyncio
async def test_superseded_publication_is_returned_as_normal_terminal_result(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    raise_intent(store, key)

    class SupersedingCompiler(RecordingCompiler):
        async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
            self.requests.append(request)
            seed_winning_snapshot(store, key)
            return self._output(request)

    result = await worker(store, SupersedingCompiler(), RecordingCounter()).run_once()

    assert result is not None
    assert result.publication is not None
    assert result.publication.outcome is ac.PublishOutcome.SUPERSEDED
    assert result.job.state is ac.CompactionJobState.SUPERSEDED
