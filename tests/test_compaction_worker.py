from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac
from astrcontinuum.compaction import CompactionWorker, CompactionWorkerConfig

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
) -> CompactionWorker:
    return CompactionWorker(
        repository=store,
        backend=backend,
        counter=LengthCounter(),
        worker_id="worker-1",
        clock=clock or FrozenClock(NOW + timedelta(minutes=2)),
        config=CompactionWorkerConfig(
            token_ceiling=10_000,
            lease_seconds=300,
            poll_interval_seconds=0.01,
            retry_base_seconds=10,
            retry_max_seconds=60,
            max_attempts=3,
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
async def test_worker_renews_lease_while_a_slow_model_is_compiling(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("slow")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-slow")
    instance = CompactionWorker(
        repository=store,
        backend=SlowExactBackend(),
        counter=LengthCounter(),
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


@pytest.mark.asyncio
async def test_missing_session_provider_waits_without_exhausting_attempts(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    session_key = key("provider-wait")
    capture(store, session_key, 1)
    raise_intent(store, session_key, 1, job_id="job-provider-wait")
    clock = FrozenClock(NOW + timedelta(minutes=2))
    backend = ExactBackend(error=ac.CompilerBackendDeferred("EXTRACTIVE_PROVIDER_UNAVAILABLE"))
    instance = CompactionWorker(
        repository=store,
        backend=backend,
        counter=LengthCounter(),
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
    assert first_wait[1] == 1
    assert first_wait[3] == "EXTRACTIVE_PROVIDER_UNAVAILABLE"

    clock.now += timedelta(seconds=11)
    assert await instance.run_iteration() is True
    second_wait = job_row(store, "job-provider-wait")
    assert second_wait[0] == "RETRY_WAIT"
    assert second_wait[1] == 2
