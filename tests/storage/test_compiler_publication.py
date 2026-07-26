from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
TOKEN_CEILING = 10_000
WORKER_ID = "compiler-worker"
SEGMENTER_CONFIG = ac.SegmenterConfig()

_PUBLICATION_TABLE_ORDER = {
    "capsules": "capsule_id",
    "snapshots": "snapshot_id",
    "snapshot_capsules": "snapshot_id, ordinal",
    "active_snapshots": "session_key_hash",
}


class RecordingBackend:
    def __init__(self, output: ac.CompilerOutput) -> None:
        self.output = output
        self.requests: list[ac.CompilationRequest] = []

    async def compile(self, request: ac.CompilationRequest) -> ac.CompilerOutput:
        self.requests.append(request)
        return self.output


class NonLenCounter:
    """A token counter adapter deliberately offering no ``__len__`` shortcut."""

    def __init__(self, result: int) -> None:
        self.result = result
        self.texts: list[str] = []

    def count_text(self, text: str) -> int:
        self.texts.append(text)
        return self.result


def session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="session-compiler-publication",
        group_id=None,
        user_id="user-compiler-publication",
        conversation_id="conversation-compiler-publication",
        persona_id=None,
    )


def repository(data_dir: Path) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    ac.SQLiteMigrator(factory).migrate()
    return ac.SQLiteRepository(factory)


def capture(store: ac.SQLiteRepository, sequence: int) -> ac.EventEnvelope:
    return store.capture_user_event(
        event_id=f"event-{sequence}",
        session_key=session_key(),
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW + timedelta(seconds=sequence),
    )


def capsule(sequence: int, *, capsule_id: str | None = None) -> ac.ContextCapsuleEnvelope:
    event_id = f"event-{sequence}"
    return ac.ContextCapsuleEnvelope(
        capsule_id=capsule_id or f"capsule-{sequence}",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=session_key(),
        covered_event_start=sequence,
        covered_event_end=sequence,
        source_event_ids=(event_id,),
        goals=(
            ac.CapsuleClaim(
                claim_id=f"goal-{sequence}",
                text=f"Goal {sequence}",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=(event_id,),
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
                anchor_id=f"anchor-{sequence}",
                anchor_type=ac.AnchorType.NAME,
                exact_text=f"Anchor {sequence}",
                source_event_ids=(event_id,),
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(),
        narrative_summary=f"Capsule {sequence}",
        token_cost=11,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW + timedelta(seconds=sequence),
    )


def claim_compiling(
    store: ac.SQLiteRepository,
    *,
    job_id: str,
    target: int,
    now: datetime,
) -> ac.CompactionJobEnvelope:
    raised = store.raise_compaction_intent(
        job_id=job_id,
        session_key=session_key(),
        target_high_water_mark=target,
        now=now,
    )
    assert raised is not None
    leased = store.claim_job(
        worker_id=WORKER_ID,
        now=now + timedelta(seconds=1),
        lease_expires_at=now + timedelta(minutes=10),
    )
    assert leased is not None
    assert leased.job_id == job_id
    assert leased.state is ac.CompactionJobState.LEASED
    return store.transition_job(
        job_id=job_id,
        owner=WORKER_ID,
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=now + timedelta(seconds=2),
    )


async def compile_view(
    view: ac.RequestView,
    *,
    backend: RecordingBackend,
    counter: NonLenCounter,
    now: datetime,
) -> ac.CompilationCandidate:
    return await ac.compile_candidate(
        base_snapshot=view.snapshot,
        base_capsules=view.capsules,
        source_events=view.delta,
        target_high_water_mark=view.high_water_mark,
        token_ceiling=TOKEN_CEILING,
        backend=backend,
        counter=counter,
        now=now,
        segmenter_config=SEGMENTER_CONFIG,
    )


def publish_candidate(
    store: ac.SQLiteRepository,
    *,
    compiling: ac.CompactionJobEnvelope,
    candidate: ac.CompilationCandidate,
    now: datetime,
) -> ac.PublishResult:
    ready = store.transition_job(
        job_id=compiling.job_id,
        owner=WORKER_ID,
        lease_epoch=compiling.lease_epoch,
        to_state=ac.CompactionJobState.READY_TO_COMMIT,
        candidate_snapshot_id=candidate.snapshot.snapshot_id,
        now=now,
    )
    return store.publish_snapshot(
        job_id=ready.job_id,
        owner=WORKER_ID,
        lease_epoch=ready.lease_epoch,
        candidate_snapshot=candidate.snapshot,
        memberships=candidate.memberships,
        token_ceiling=TOKEN_CEILING,
        now=now + timedelta(seconds=1),
    )


def table_rows(
    store: ac.SQLiteRepository,
    table: str,
    *,
    order_by: str,
) -> tuple[tuple[object, ...], ...]:
    with store.factory.connection(read_only=True) as connection:
        rows = connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
    return tuple(tuple(row) for row in rows)


def publication_rows(
    store: ac.SQLiteRepository,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: table_rows(store, table, order_by=order_by)
        for table, order_by in _PUBLICATION_TABLE_ORDER.items()
    }


def journal_rows(store: ac.SQLiteRepository) -> tuple[tuple[object, ...], ...]:
    return table_rows(
        store,
        "journal_events",
        order_by="session_key_hash, sequence, event_id",
    )


def job_state(
    store: ac.SQLiteRepository,
    job_id: str,
) -> tuple[object, object]:
    with store.factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, candidate_snapshot_id
            FROM compaction_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    assert row is not None
    return tuple(row)


@pytest.mark.asyncio
async def test_real_compiler_candidate_is_invisible_until_atomic_publication(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    first_event = capture(store, 1)
    first_journal = journal_rows(store)
    first_job = claim_compiling(
        store,
        job_id="job-1",
        target=1,
        now=NOW + timedelta(minutes=1),
    )
    first_view = store.read_request_view(session_key())
    assert first_view.snapshot is None
    assert first_view.capsules == ()
    assert first_view.delta == (first_event,)

    first_capsule = capsule(1)
    first_backend = RecordingBackend(
        ac.CompilerOutput(
            capsules=(first_capsule,),
            rendered_context="compiled context through event 1",
        )
    )
    first_counter = NonLenCounter(17)
    first_tables_before_compile = publication_rows(store)
    first_candidate = await compile_view(
        first_view,
        backend=first_backend,
        counter=first_counter,
        now=NOW + timedelta(minutes=1, seconds=3),
    )

    assert len(first_backend.requests) == 1
    first_request = first_backend.requests[0]
    assert first_request.base_snapshot is first_view.snapshot
    assert first_request.base_capsules == first_view.capsules
    assert first_request.source_events == first_view.delta
    assert first_counter.texts == ["compiled context through event 1"]
    assert store.read_request_view(session_key()) == first_view
    assert publication_rows(store) == first_tables_before_compile
    assert job_state(store, first_job.job_id) == ("COMPILING", None)

    first_result = publish_candidate(
        store,
        compiling=first_job,
        candidate=first_candidate,
        now=NOW + timedelta(minutes=1, seconds=4),
    )
    assert first_result.outcome is ac.PublishOutcome.COMMITTED
    assert first_result.pointer_version == 1
    assert (
        first_result.winner.model_copy(
            update={
                "state": ac.SnapshotState.CANDIDATE,
                "committed_at": None,
            }
        )
        == first_candidate.snapshot
    )
    assert journal_rows(store) == first_journal

    second_event = capture(store, 2)
    second_journal = journal_rows(store)
    second_job = claim_compiling(
        store,
        job_id="job-2",
        target=2,
        now=NOW + timedelta(minutes=2),
    )
    second_view = store.read_request_view(session_key())
    assert second_view.snapshot == first_result.winner
    assert second_view.memberships == first_candidate.memberships
    assert second_view.capsules == (first_capsule,)
    assert second_view.pointer_version == 1
    assert second_view.covered_event_end == 1
    assert second_view.high_water_mark == 2
    assert second_view.delta == (second_event,)

    second_capsule = capsule(2)
    complete_output = (*second_view.capsules, second_capsule)
    second_backend = RecordingBackend(
        ac.CompilerOutput(
            capsules=complete_output,
            rendered_context="compiled context through event 2",
        )
    )
    second_counter = NonLenCounter(23)
    second_tables_before_compile = publication_rows(store)
    second_candidate = await compile_view(
        second_view,
        backend=second_backend,
        counter=second_counter,
        now=NOW + timedelta(minutes=2, seconds=3),
    )

    assert len(second_backend.requests) == 1
    second_request = second_backend.requests[0]
    assert second_request.base_snapshot is second_view.snapshot
    assert second_request.base_capsules == second_view.capsules
    assert second_request.base_capsules[0] is second_view.capsules[0]
    assert second_request.source_events == second_view.delta
    assert second_request.source_events[0] is second_view.delta[0]
    assert second_counter.texts == ["compiled context through event 2"]
    assert store.read_request_view(session_key()) == second_view
    assert publication_rows(store) == second_tables_before_compile
    assert job_state(store, second_job.job_id) == ("COMPILING", None)

    second_result = publish_candidate(
        store,
        compiling=second_job,
        candidate=second_candidate,
        now=NOW + timedelta(minutes=2, seconds=4),
    )

    assert second_result.outcome is ac.PublishOutcome.COMMITTED
    assert second_result.job.state is ac.CompactionJobState.COMMITTED
    assert second_result.pointer_version == 2
    assert (
        second_result.winner.model_copy(
            update={
                "state": ac.SnapshotState.CANDIDATE,
                "committed_at": None,
            }
        )
        == second_candidate.snapshot
    )

    committed_view = store.read_request_view(session_key())
    assert committed_view.snapshot == second_result.winner
    assert committed_view.memberships == second_candidate.memberships
    assert tuple(item.ordinal for item in committed_view.memberships) == (0, 1)
    assert tuple(item.slot for item in committed_view.memberships) == ("memory", "memory")
    assert committed_view.capsules == complete_output
    assert committed_view.pointer_version == 2
    assert committed_view.covered_event_end == 2
    assert committed_view.high_water_mark == 2
    assert committed_view.delta == ()
    assert journal_rows(store) == second_journal

    final_tables = publication_rows(store)
    assert len(final_tables["capsules"]) == 2
    assert len(final_tables["snapshots"]) == 2
    assert len(final_tables["snapshot_capsules"]) == 3
    assert len(final_tables["active_snapshots"]) == 1
    with store.factory.connection(read_only=True) as connection:
        capsule_ids = connection.execute(
            "SELECT capsule_id FROM capsules ORDER BY capsule_id"
        ).fetchall()
        memberships = connection.execute(
            """
            SELECT snapshot_id, ordinal, slot, capsule_id
            FROM snapshot_capsules
            ORDER BY snapshot_id, ordinal
            """
        ).fetchall()
        active = connection.execute(
            """
            SELECT snapshot_id, pointer_version
            FROM active_snapshots
            WHERE session_key_hash = ?
            """,
            (session_key().session_key_hash,),
        ).fetchone()
    assert tuple(row[0] for row in capsule_ids) == ("capsule-1", "capsule-2")
    assert tuple(tuple(row) for row in memberships) == tuple(
        sorted(
            (
                (first_candidate.snapshot.snapshot_id, 0, "memory", "capsule-1"),
                (second_candidate.snapshot.snapshot_id, 0, "memory", "capsule-1"),
                (second_candidate.snapshot.snapshot_id, 1, "memory", "capsule-2"),
            )
        )
    )
    assert active is not None
    assert tuple(active) == (second_candidate.snapshot.snapshot_id, 2)


@pytest.mark.asyncio
async def test_dropped_base_is_rejected_without_publication_writes(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    bootstrap_job = claim_compiling(
        store,
        job_id="job-bootstrap",
        target=1,
        now=NOW + timedelta(minutes=1),
    )
    bootstrap_view = store.read_request_view(session_key())
    base_capsule = capsule(1)
    bootstrap_backend = RecordingBackend(
        ac.CompilerOutput(
            capsules=(base_capsule,),
            rendered_context="committed base context",
        )
    )
    bootstrap_candidate = await compile_view(
        bootstrap_view,
        backend=bootstrap_backend,
        counter=NonLenCounter(13),
        now=NOW + timedelta(minutes=1, seconds=3),
    )
    bootstrap_result = publish_candidate(
        store,
        compiling=bootstrap_job,
        candidate=bootstrap_candidate,
        now=NOW + timedelta(minutes=1, seconds=4),
    )
    assert bootstrap_result.outcome is ac.PublishOutcome.COMMITTED

    second_event = capture(store, 2)
    compiling = claim_compiling(
        store,
        job_id="job-malicious",
        target=2,
        now=NOW + timedelta(minutes=2),
    )
    view_before = store.read_request_view(session_key())
    assert view_before.snapshot == bootstrap_result.winner
    assert view_before.capsules == (base_capsule,)
    assert view_before.delta == (second_event,)
    assert view_before.pointer_version == 1
    assert view_before.covered_event_end == 1
    assert view_before.high_water_mark == 2

    malicious_capsule = capsule(2, capsule_id="capsule-malicious-new-only")
    malicious_backend = RecordingBackend(
        ac.CompilerOutput(
            capsules=(malicious_capsule,),
            rendered_context="complete-looking context with only the new event",
        )
    )
    counter = NonLenCounter(19)
    rows_before = publication_rows(store)
    journal_before = journal_rows(store)

    with pytest.raises(ac.CompilerInvariantError) as caught:
        await compile_view(
            view_before,
            backend=malicious_backend,
            counter=counter,
            now=NOW + timedelta(minutes=2, seconds=3),
        )

    assert caught.value.code is ac.CompilerErrorCode.PERMANENT_VALIDATION_FAILED
    assert caught.value.report is not None
    assert ac.PermanentFailureCode.MISSING_PRIOR_SEMANTIC in (caught.value.report.failure_codes)
    assert ac.PermanentFailureCode.MISSING_REQUIRED_ANCHOR in (caught.value.report.failure_codes)
    assert len(malicious_backend.requests) == 1
    request = malicious_backend.requests[0]
    assert request.base_snapshot is view_before.snapshot
    assert request.base_capsules == view_before.capsules
    assert request.source_events == view_before.delta
    assert counter.texts == ["complete-looking context with only the new event"]

    assert publication_rows(store) == rows_before
    assert journal_rows(store) == journal_before
    assert store.read_request_view(session_key()) == view_before
    assert job_state(store, compiling.job_id) == ("COMPILING", None)
    with store.factory.connection(read_only=True) as connection:
        active = connection.execute(
            """
            SELECT snapshot_id, pointer_version
            FROM active_snapshots
            WHERE session_key_hash = ?
            """,
            (session_key().session_key_hash,),
        ).fetchone()
        snapshot_ids = connection.execute(
            "SELECT snapshot_id FROM snapshots ORDER BY snapshot_id"
        ).fetchall()
    assert active is not None
    assert tuple(active) == (bootstrap_result.winner.snapshot_id, 1)
    assert tuple(row[0] for row in snapshot_ids) == (bootstrap_result.winner.snapshot_id,)
