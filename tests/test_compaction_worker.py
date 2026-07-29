from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac
import astrcontinuum.compaction.worker as worker_module
from astrcontinuum.compaction import (
    CompactionProviderBinding,
    CompactionWorker,
    CompactionWorkerConfig,
    SessionProviderRegistry,
    render_capsule,
)
from astrcontinuum.context_graph.candidate_verification import (
    CandidateVerificationError,
    CandidateVerificationErrorCode,
)
from astrcontinuum.storage import (
    ArtifactKind,
    CanonicalMetricObservation,
    TokenMetric,
    TokenMetricStore,
)
from astrcontinuum.tokenization import CANONICAL_O200K, ContextLimitSource

NOW = datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc)
TEST_KEY = bytes(range(32))


def storage_keys() -> ac.ResolvedKeyMaterial:
    return ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(TEST_KEY),
        previous=None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )


class FrozenClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class LengthCounter:
    def count_text(self, text: str) -> int:
        return len(text)


class RecordingFixedCounter:
    def __init__(self, result: int) -> None:
        self.result = result
        self.texts: list[str] = []

    def count_text(self, text: str) -> int:
        self.texts.append(text)
        return self.result


class ExactBackend:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[ac.CompilationRequest] = []

    async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        event = request.source_events[-1]
        claim = ac.CapsuleClaim(
            claim_id=f"claim-{event.event_id}",
            text=event.content,
            status=ac.SemanticStatus.ACTIVE,
            confidence=1.0,
            source_event_ids=(event.event_id,),
        )
        capsule = ac.ContextCapsuleEnvelope(
            capsule_id=f"capsule-{event.event_id}",
            schema_version="1.0.0",
            level=ac.CapsuleLevel.TASK,
            session_key=event.session_key,
            covered_event_start=request.source_events[0].sequence,
            covered_event_end=request.source_events[-1].sequence,
            source_event_ids=tuple(item.event_id for item in request.source_events),
            goals=(claim,),
            constraints=(),
            decisions=(),
            progress=(),
            open_loops=(),
            preferences=(),
            entities=(),
            emotional_context=(),
            exact_anchors=(),
            dependencies=(),
            narrative_summary="structured source map",
            token_cost=len(event.content),
            quality=ac.CapsuleQuality(
                mechanical_passed=True,
                source_coverage=1.0,
                anchor_recall=1.0,
                unsupported_critical_claims=0,
                coverage_gap=0,
            ),
            created_at=NOW,
        )
        return ac.CompilerOutput(
            capsules=(*request.base_capsules, capsule),
            rendered_context=f"Goal: {event.content}",
        )


class SlowExactBackend(ExactBackend):
    async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        await asyncio.sleep(0.15)
        return await super().compile(request)


class CapturedBindingBackend(ExactBackend):
    async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        if request.provider_binding_captured and request.provider_binding is None:
            self.requests.append(request)
            raise ac.CompilerBackendDeferred("EXTRACTIVE_PROVIDER_UNAVAILABLE")
        return await super().compile(request)


def key(name: str = "worker") -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=f"session-{name}",
        group_id=None,
        user_id=f"user-{name}",
        conversation_id=f"conversation-{name}",
        persona_id=None,
    )


def repository(path: Path) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(path)
    activation = ac.activate_storage_security(factory, storage_keys())
    return ac.SQLiteRepository(factory, codec=activation.codec)


def capture(
    store: ac.SQLiteRepository,
    session_key: ac.SessionKey,
    sequence: int,
) -> ac.EventEnvelope:
    return store.capture_user_event(
        event_id=f"{session_key.session_id}-event-{sequence}",
        session_key=session_key,
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        canonical=CanonicalMetricObservation(CANONICAL_O200K.profile_id, None),
        created_at=NOW + timedelta(seconds=sequence),
    )


def raise_intent(
    store: ac.SQLiteRepository,
    session_key: ac.SessionKey,
    target: int,
    *,
    job_id: str = "job-1",
) -> ac.CompactionJobEnvelope:
    job = store.raise_compaction_intent(
        job_id=job_id,
        session_key=session_key,
        target_high_water_mark=target,
        now=NOW + timedelta(minutes=1),
    )
    assert job is not None
    return job


def worker(
    store: ac.SQLiteRepository,
    backend: ExactBackend,
    *,
    clock: FrozenClock | None = None,
    canonical_counter: object | None = None,
    metric_backfill_limit: int = 32,
) -> CompactionWorker:
    return CompactionWorker(
        repository=store,
        backend=backend,
        counter=LengthCounter(),
        canonical_counter=canonical_counter or LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-1",
        clock=clock or FrozenClock(NOW + timedelta(minutes=2)),
        config=CompactionWorkerConfig(
            token_ceiling=10_000,
            lease_seconds=300,
            poll_interval_seconds=0.01,
            retry_base_seconds=10,
            retry_max_seconds=60,
            max_attempts=3,
            metric_backfill_limit=metric_backfill_limit,
        ),
    )


def job_row(store: ac.SQLiteRepository, job_id: str) -> tuple[object, ...]:
    with store.factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, attempt_count, error_stage, error_code, error_message,
                   next_retry_at
            FROM compaction_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    return tuple(row)


def read_metric(
    store: ac.SQLiteRepository,
    artifact_kind: ArtifactKind,
    artifact_id: str,
) -> TokenMetric | None:
    metric_store = TokenMetricStore(ac.SecureCodec(TEST_KEY))
    with store.factory.connection(read_only=True) as connection:
        connection.execute("BEGIN")
        try:
            return metric_store.get_in_transaction(
                connection,
                artifact_kind=artifact_kind,
                artifact_id=artifact_id,
                tokenizer_profile_id=CANONICAL_O200K.profile_id,
            )
        finally:
            connection.rollback()


def test_fenced_compaction_view_uses_frozen_target_not_latest_journal(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key()
    first = capture(store, session_key, 1)
    raise_intent(store, session_key, 1)
    capture(store, session_key, 2)

    leased = store.claim_job(
        worker_id="worker-1",
        now=NOW + timedelta(minutes=2),
        lease_expires_at=NOW + timedelta(minutes=7),
    )
    assert leased is not None
    compiling = store.transition_job(
        job_id=leased.job_id,
        owner="worker-1",
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=NOW + timedelta(minutes=2),
    )

    view = store.read_compaction_view(
        job_id=compiling.job_id,
        owner="worker-1",
        lease_epoch=compiling.lease_epoch,
        now=NOW + timedelta(minutes=2),
    )

    assert view.snapshot is None
    assert view.pointer_version == 0
    assert view.covered_event_end == 0
    assert view.high_water_mark == 1
    assert view.delta == (first,)


@pytest.mark.asyncio
async def test_worker_compiles_and_atomically_publishes_checkpoint(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key()
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1)
    backend = ExactBackend()

    did_work = await worker(store, backend).run_iteration()

    assert did_work is True
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.target_high_water_mark == 1
    assert tuple(item.sequence for item in request.source_events) == (1,)
    view = store.read_request_view(session_key)
    assert view.snapshot is not None
    assert view.covered_event_end == 1
    assert view.high_water_mark == 1
    assert view.delta == ()
    assert job_row(store, "job-1")[:2] == ("COMMITTED", 1)


@pytest.mark.asyncio
async def test_worker_backfills_event_and_publishes_post_audit_canonical_metrics(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("canonical")
    event = capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-canonical")
    canonical_counter = RecordingFixedCounter(1)

    assert (
        await worker(
            store,
            ExactBackend(),
            canonical_counter=canonical_counter,
        ).run_iteration()
        is True
    )

    view = store.read_request_view(session_key)
    assert view.snapshot is not None
    assert view.snapshot.token_cost == len("Goal: message 1")
    assert view.capsules[0].token_cost == len("message 1")
    artifact_ids = (
        (ArtifactKind.EVENT, event.event_id),
        (ArtifactKind.CAPSULE, view.capsules[0].capsule_id),
        (ArtifactKind.SNAPSHOT, view.snapshot.snapshot_id),
    )
    assert tuple(
        read_metric(store, artifact_kind, artifact_id).token_count
        for artifact_kind, artifact_id in artifact_ids
    ) == (1, 1, 1)
    assert canonical_counter.texts == [
        event.content,
        render_capsule(view.capsules[0]),
        view.snapshot.rendered_context,
    ]


@pytest.mark.asyncio
async def test_worker_defers_incomplete_metric_mapping_without_spending_attempt(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("bounded-missing")
    first = capture(store, session_key, 1)
    second = capture(store, session_key, 2)
    raise_intent(store, session_key, 2, job_id="job-bounded-missing")
    clock = FrozenClock(NOW + timedelta(minutes=2))
    backend = ExactBackend()
    instance = worker(
        store,
        backend,
        clock=clock,
        canonical_counter=RecordingFixedCounter(1),
        metric_backfill_limit=1,
    )

    assert await instance.run_iteration() is True

    deferred = job_row(store, "job-bounded-missing")
    assert deferred[0] == "RETRY_WAIT"
    assert deferred[1] == 0
    assert deferred[2] == "COMPILING"
    assert deferred[3] == "TOKEN_METRIC_MISSING"
    assert backend.requests == []
    assert read_metric(store, ArtifactKind.EVENT, first.event_id) is not None
    assert read_metric(store, ArtifactKind.EVENT, second.event_id) is None

    clock.now += timedelta(seconds=11)
    assert await instance.run_iteration() is True
    assert job_row(store, "job-bounded-missing")[:2] == ("COMMITTED", 1)


@pytest.mark.asyncio
async def test_worker_defers_unavailable_canonical_counter_without_byte_fallback(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("canonical-unavailable")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-canonical-unavailable")
    backend = ExactBackend()
    instance = CompactionWorker(
        repository=store,
        backend=backend,
        counter=LengthCounter(),
        canonical_counter=None,
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-canonical-unavailable",
        clock=FrozenClock(NOW + timedelta(minutes=2)),
        config=CompactionWorkerConfig(
            token_ceiling=10_000,
            retry_base_seconds=10,
        ),
    )

    assert await instance.run_iteration() is True

    deferred = job_row(store, "job-canonical-unavailable")
    assert deferred[:2] == ("RETRY_WAIT", 0)
    assert deferred[3] == "TOKEN_METRIC_UNAVAILABLE"
    assert backend.requests == []


@pytest.mark.asyncio
async def test_worker_captures_provider_binding_immediately_after_claim(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("binding-at-claim")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-binding-first")
    providers = SessionProviderRegistry(max_entries=2)
    first_binding = CompactionProviderBinding(
        provider_id="provider-first",
        model_identity="gpt-4o",
        context_limit=262_144,
        context_limit_source=ContextLimitSource.AUTO_ASTRBOT,
    )
    second_binding = CompactionProviderBinding(
        provider_id="provider-second",
        model_identity="MiniMax-Text-01",
        context_limit=128_000,
        context_limit_source=ContextLimitSource.AUTO_SAFE_FALLBACK,
    )
    providers.remember(session_key, first_binding)
    backend = ExactBackend()
    instance = CompactionWorker(
        repository=store,
        backend=backend,
        compatibility_counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        provider_bindings=providers,
        worker_id="worker-binding-at-claim",
        clock=FrozenClock(NOW + timedelta(minutes=2)),
        config=CompactionWorkerConfig(token_ceiling=10_000),
    )
    entered_backfill = asyncio.Event()
    release_backfill = asyncio.Event()
    original_backfill = instance._backfill_metrics

    async def blocked_backfill(claimed_session: ac.SessionKey) -> None:
        entered_backfill.set()
        await release_backfill.wait()
        await original_backfill(claimed_session)

    instance._backfill_metrics = blocked_backfill  # type: ignore[method-assign]
    first_iteration = asyncio.create_task(instance.run_iteration())
    await entered_backfill.wait()

    providers.remember(session_key, second_binding)
    release_backfill.set()
    assert await first_iteration is True

    assert backend.requests[0].provider_binding is first_binding
    capture(store, session_key, 2)
    raise_intent(store, session_key, 2, job_id="job-binding-second")
    assert await instance.run_iteration() is True
    assert backend.requests[1].provider_binding is second_binding


@pytest.mark.asyncio
async def test_missing_claim_binding_still_backfills_before_safe_deferral(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("binding-missing-backfill")
    source = capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-binding-missing")
    providers = SessionProviderRegistry(max_entries=2)
    backend = CapturedBindingBackend()
    instance = CompactionWorker(
        repository=store,
        backend=backend,
        compatibility_counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        provider_bindings=providers,
        worker_id="worker-binding-missing",
        clock=FrozenClock(NOW + timedelta(minutes=2)),
        config=CompactionWorkerConfig(token_ceiling=10_000),
    )

    assert await instance.run_iteration() is True

    assert read_metric(store, ArtifactKind.EVENT, source.event_id) is not None
    assert backend.requests[0].provider_binding is None
    assert backend.requests[0].provider_binding_captured is True
    assert job_row(store, "job-binding-missing")[3] == "EXTRACTIVE_PROVIDER_UNAVAILABLE"


def test_attempt_preservation_rejects_nontransient_failures(tmp_path: Path) -> None:
    store = repository(tmp_path)
    session_key = key("attempt-guard")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-attempt-guard")
    leased = store.claim_job(
        worker_id="worker-attempt-guard",
        now=NOW + timedelta(minutes=2),
        lease_expires_at=NOW + timedelta(minutes=7),
    )
    assert leased is not None

    with pytest.raises(
        ValueError,
        match="^attempt preservation is limited to retryable transient deferrals$",
    ):
        store.fail_job(
            job_id=leased.job_id,
            owner="worker-attempt-guard",
            lease_epoch=leased.lease_epoch,
            now=NOW + timedelta(minutes=2),
            error_stage="COMPILING",
            error_code="COMPILER_BACKEND_FAILURE",
            error_message="COMPILER_BACKEND_FAILURE",
            retry_at=NOW + timedelta(minutes=3),
            preserve_attempt=True,
        )

    assert job_row(store, leased.job_id)[:2] == ("LEASED", 1)


@pytest.mark.asyncio
async def test_worker_rejects_unverified_candidate_before_ready_to_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("verification-rejected")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-verification-rejected")
    observed: list[object] = []

    def reject(candidate: object) -> None:
        observed.append(candidate)
        raise CandidateVerificationError(CandidateVerificationErrorCode.REJECTED)

    monkeypatch.setattr(worker_module, "verify_candidate", reject)

    assert await worker(store, ExactBackend()).run_iteration() is True

    failed = job_row(store, "job-verification-rejected")
    assert observed
    assert failed[0] == "RETRY_WAIT"
    assert failed[2] == "VERIFYING"
    assert failed[3] == "CANDIDATE_GRAPH_VERIFICATION_FAILED"
    assert failed[4] == "CANDIDATE_GRAPH_VERIFICATION_FAILED"
    assert store.read_request_view(session_key).snapshot is None


@pytest.mark.asyncio
async def test_worker_persists_redacted_retry_and_keeps_scheduler_usable(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    first_key = key("failed")
    capture(store, first_key, 1)
    raise_intent(store, first_key, 1, job_id="job-failed")
    secret = "PRIVATE-MODEL-OUTPUT"
    failed_backend = ExactBackend(error=RuntimeError(secret))
    clock = FrozenClock(NOW + timedelta(minutes=2))
    failed_worker = worker(store, failed_backend, clock=clock)

    assert await failed_worker.run_iteration() is True

    failed = job_row(store, "job-failed")
    assert failed[0] == "RETRY_WAIT"
    assert failed[1] == 1
    assert failed[2] == "COMPILING"
    assert failed[3] == "COMPILER_BACKEND_FAILURE"
    assert failed[4] == "COMPILER_BACKEND_FAILURE"
    assert secret not in str(failed)
    assert failed[5] is not None

    second_key = key("healthy")
    capture(store, second_key, 1)
    raise_intent(store, second_key, 1, job_id="job-healthy")
    healthy_backend = ExactBackend()
    healthy_worker = worker(store, healthy_backend, clock=clock)

    assert await healthy_worker.run_iteration() is True
    assert job_row(store, "job-healthy")[0] == "COMMITTED"


@pytest.mark.asyncio
async def test_worker_lifecycle_is_idempotent_and_wake_is_nonblocking(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    instance = worker(store, ExactBackend())

    await instance.start()
    first_task = instance.task
    await instance.start()
    assert instance.task is first_task
    assert first_task is not None

    instance.wake()
    instance.wake()
    await instance.close()
    await instance.close()
    assert instance.task is None


@pytest.mark.asyncio
async def test_worker_survives_python_310_asyncio_timeout_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    instance = worker(store, ExactBackend())
    wait_calls = 0

    class LegacyAsyncioTimeout(Exception):
        pass

    async def legacy_wait_for(awaitable: object, *, timeout: float) -> None:
        nonlocal wait_calls
        wait_calls += 1
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        instance._closed = True
        raise LegacyAsyncioTimeout

    monkeypatch.setattr(worker_module.asyncio, "TimeoutError", LegacyAsyncioTimeout)
    monkeypatch.setattr(worker_module.asyncio, "wait_for", legacy_wait_for)

    await instance._run()

    assert wait_calls == 1


@pytest.mark.asyncio
async def test_worker_renews_lease_while_a_slow_model_is_compiling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("slow")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-slow")
    renewal_count = 0
    renew_job_lease = store.renew_job_lease

    def record_renewal(**kwargs: object) -> ac.CompactionJobEnvelope:
        nonlocal renewal_count
        renewed = renew_job_lease(**kwargs)
        renewal_count += 1
        return renewed

    monkeypatch.setattr(store, "renew_job_lease", record_renewal)
    instance = CompactionWorker(
        repository=store,
        backend=SlowExactBackend(),
        counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-slow",
        clock=lambda: datetime.now(timezone.utc),
        config=CompactionWorkerConfig(
            token_ceiling=10_000,
            lease_seconds=0.06,
            poll_interval_seconds=0.01,
            retry_base_seconds=10,
            retry_max_seconds=60,
            max_attempts=3,
        ),
    )

    assert await instance.run_iteration() is True
    assert job_row(store, "job-slow")[0] == "COMMITTED"
    assert renewal_count >= 1


@pytest.mark.asyncio
async def test_missing_session_provider_waits_without_exhausting_attempts(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("provider-wait")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-provider-wait")
    clock = FrozenClock(NOW + timedelta(minutes=2))
    providers = SessionProviderRegistry(max_entries=2)
    backend = CapturedBindingBackend()
    instance = CompactionWorker(
        repository=store,
        backend=backend,
        compatibility_counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        provider_bindings=providers,
        worker_id="worker-provider-wait",
        clock=clock,
        config=CompactionWorkerConfig(
            token_ceiling=10_000,
            lease_seconds=300,
            poll_interval_seconds=0.01,
            retry_base_seconds=10,
            retry_max_seconds=60,
            max_attempts=1,
        ),
    )

    assert await instance.run_iteration() is True
    first_wait = job_row(store, "job-provider-wait")
    assert first_wait[0] == "RETRY_WAIT"
    assert first_wait[1] == 0
    assert first_wait[3] == "EXTRACTIVE_PROVIDER_UNAVAILABLE"

    clock.now += timedelta(seconds=11)
    assert await instance.run_iteration() is True
    second_wait = job_row(store, "job-provider-wait")
    assert second_wait[0] == "RETRY_WAIT"
    assert second_wait[1] == 0

    providers.remember(
        session_key,
        CompactionProviderBinding(
            provider_id="provider-recovered",
            model_identity="gpt-4o",
            context_limit=10_000,
            context_limit_source=ContextLimitSource.AUTO_ASTRBOT,
        ),
    )
    clock.now += timedelta(seconds=11)
    assert await instance.run_iteration() is True
    assert job_row(store, "job-provider-wait")[:2] == ("COMMITTED", 1)
    assert backend.requests[-1].provider_binding is providers.resolve(session_key)


@pytest.mark.asyncio
async def test_storage_authentication_failure_is_fatal_and_never_retried() -> None:
    class AuthenticationFailingRepository:
        def __init__(self) -> None:
            self.fail_job_calls = 0

        def recover_expired_leases(self, **_kwargs: object) -> None:
            return None

        def claim_job(self, **_kwargs: object) -> None:
            raise ac.StorageSecurityError(ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)

        def fail_job(self, **_kwargs: object) -> None:
            self.fail_job_calls += 1

    repository = AuthenticationFailingRepository()
    fatal_codes: list[str] = []

    async def on_fatal(error: ac.StorageSecurityError) -> None:
        fatal_codes.append(error.code.value)

    instance = CompactionWorker(
        repository=repository,
        backend=ExactBackend(),
        counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-auth-failure",
        config=CompactionWorkerConfig(
            token_ceiling=10_000,
            poll_interval_seconds=0.01,
        ),
        fatal_storage_callback=on_fatal,
    )

    await instance.start()
    task = instance.task
    assert task is not None
    await asyncio.wait_for(task, timeout=1)

    assert fatal_codes == ["STORAGE_AUTHENTICATION_FAILED"]
    assert repository.fail_job_calls == 0


@pytest.mark.asyncio
async def test_lease_heartbeat_authentication_failure_escapes_iteration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("heartbeat-auth-failure")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-heartbeat-auth-failure")
    loop = asyncio.get_running_loop()
    renewal_attempted = asyncio.Event()

    def fail_renewal(**_kwargs: object) -> None:
        loop.call_soon_threadsafe(renewal_attempted.set)
        raise ac.StorageSecurityError(ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)

    class WaitForHeartbeatBackend(ExactBackend):
        async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
            await asyncio.wait_for(renewal_attempted.wait(), timeout=1)
            return await super().compile(request)

    monkeypatch.setattr(store, "renew_job_lease", fail_renewal)
    instance = CompactionWorker(
        repository=store,
        backend=WaitForHeartbeatBackend(),
        counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-heartbeat-auth-failure",
        clock=lambda: datetime.now(timezone.utc),
        config=CompactionWorkerConfig(
            token_ceiling=10_000,
            lease_seconds=0.03,
            poll_interval_seconds=0.01,
        ),
    )

    with pytest.raises(ac.StorageSecurityError) as captured:
        await instance.run_iteration()

    assert captured.value.code is ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED


@pytest.mark.asyncio
async def test_iteration_cancellation_wins_over_heartbeat_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("heartbeat-cancel-race")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-heartbeat-cancel-race")
    compiling_started = asyncio.Event()
    heartbeat_failed = asyncio.Event()
    keep_compiling = asyncio.Event()

    class BlockingBackend(ExactBackend):
        async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
            compiling_started.set()
            await heartbeat_failed.wait()
            await keep_compiling.wait()
            return await super().compile(request)

    instance = CompactionWorker(
        repository=store,
        backend=BlockingBackend(),
        counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-heartbeat-cancel-race",
        config=CompactionWorkerConfig(token_ceiling=10_000),
    )

    async def fail_heartbeat(_leased: ac.CompactionJobEnvelope) -> None:
        await compiling_started.wait()
        heartbeat_failed.set()
        raise ac.StorageSecurityError(ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)

    monkeypatch.setattr(instance, "_renew_lease", fail_heartbeat)
    iteration = asyncio.create_task(instance.run_iteration())
    await asyncio.wait_for(heartbeat_failed.wait(), timeout=1)
    iteration.cancel()

    with pytest.raises(asyncio.CancelledError):
        await iteration


@pytest.mark.asyncio
async def test_primary_storage_error_wins_over_heartbeat_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("heartbeat-primary-error")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-heartbeat-primary-error")
    loop = asyncio.get_running_loop()
    transition_started = asyncio.Event()
    heartbeat_failed = threading.Event()

    def fail_transition(**_kwargs: object) -> None:
        loop.call_soon_threadsafe(transition_started.set)
        if not heartbeat_failed.wait(timeout=1):
            raise AssertionError("heartbeat did not fail before the primary exception")
        raise ac.StorageSecurityError(ac.SecurityErrorCode.STORAGE_KEY_MISMATCH)

    instance = CompactionWorker(
        repository=store,
        backend=ExactBackend(),
        counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-heartbeat-primary-error",
        config=CompactionWorkerConfig(token_ceiling=10_000),
    )

    async def fail_heartbeat(_leased: ac.CompactionJobEnvelope) -> None:
        await transition_started.wait()
        heartbeat_failed.set()
        raise ac.StorageSecurityError(ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)

    monkeypatch.setattr(store, "transition_job", fail_transition)
    monkeypatch.setattr(instance, "_renew_lease", fail_heartbeat)

    with pytest.raises(ac.StorageSecurityError) as captured:
        await instance.run_iteration()

    assert captured.value.code is ac.SecurityErrorCode.STORAGE_KEY_MISMATCH


@pytest.mark.asyncio
async def test_unopposed_heartbeat_authentication_failure_reaches_fatal_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("heartbeat-fatal-callback")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-heartbeat-fatal-callback")
    heartbeat_failed = asyncio.Event()
    fatal_codes: list[str] = []

    class WaitForHeartbeatBackend(ExactBackend):
        async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
            await heartbeat_failed.wait()
            return await super().compile(request)

    async def on_fatal(error: ac.StorageSecurityError) -> None:
        fatal_codes.append(error.code.value)

    instance = CompactionWorker(
        repository=store,
        backend=WaitForHeartbeatBackend(),
        counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-heartbeat-fatal-callback",
        config=CompactionWorkerConfig(token_ceiling=10_000),
        fatal_storage_callback=on_fatal,
    )

    async def fail_heartbeat(_leased: ac.CompactionJobEnvelope) -> None:
        heartbeat_failed.set()
        raise ac.StorageSecurityError(ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)

    monkeypatch.setattr(instance, "_renew_lease", fail_heartbeat)
    await instance.start()
    task = instance.task
    assert task is not None

    await asyncio.wait_for(task, timeout=1)

    assert fatal_codes == ["STORAGE_AUTHENTICATION_FAILED"]
    assert instance.task is None


@pytest.mark.asyncio
async def test_caller_exception_context_does_not_hide_heartbeat_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("heartbeat-caller-exception")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-heartbeat-caller-exception")
    heartbeat_failed = asyncio.Event()

    class WaitForHeartbeatBackend(ExactBackend):
        async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
            await heartbeat_failed.wait()
            return await super().compile(request)

    instance = CompactionWorker(
        repository=store,
        backend=WaitForHeartbeatBackend(),
        counter=LengthCounter(),
        canonical_counter=LengthCounter(),
        canonical_profile_id=CANONICAL_O200K.profile_id,
        worker_id="worker-heartbeat-caller-exception",
        config=CompactionWorkerConfig(token_ceiling=10_000),
    )

    async def fail_heartbeat(_leased: ac.CompactionJobEnvelope) -> None:
        heartbeat_failed.set()
        raise ac.StorageSecurityError(ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)

    monkeypatch.setattr(instance, "_renew_lease", fail_heartbeat)

    try:
        raise LookupError("caller exception context")
    except LookupError:
        with pytest.raises(ac.StorageSecurityError) as captured:
            await instance.run_iteration()

    assert captured.value.code is ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED
