from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
LEASE_END = NOW + timedelta(minutes=10)


class InjectedCrash(RuntimeError):
    """Deterministic process-boundary failure used by repository tests."""


def session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="session-1",
        group_id=None,
        user_id="user-1",
        conversation_id="conversation-session-1",
        persona_id=None,
    )


def migrated_factory(data_dir: Path) -> ac.SQLiteConnectionFactory:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    ac.SQLiteMigrator(factory).migrate()
    return factory


def capture(store: ac.SQLiteRepository, sequence: int) -> ac.EventEnvelope:
    return store.capture_user_event(
        event_id=f"event-{sequence}",
        session_key=session_key(),
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW,
    )


def capsule(sequence: int = 1) -> ac.ContextCapsuleEnvelope:
    event_id = f"event-{sequence}"
    return ac.ContextCapsuleEnvelope(
        capsule_id=f"capsule-{sequence}",
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
        token_cost=10,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    )


def candidate_bundle(
    snapshot_id: str = "snapshot-1",
) -> tuple[
    ac.SnapshotEnvelope,
    tuple[ac.SnapshotCapsuleMembership, ...],
]:
    item = capsule()
    memberships = (
        ac.SnapshotCapsuleMembership(
            ordinal=0,
            slot="memory",
            capsule=item,
        ),
    )
    snapshot = ac.SnapshotEnvelope(
        snapshot_id=snapshot_id,
        session_key=session_key(),
        base_snapshot_id=None,
        covered_event_end=1,
        source_high_water_mark=1,
        capsule_ids=(item.capsule_id,),
        exact_anchor_ids=(item.exact_anchors[0].anchor_id,),
        rendered_context="Snapshot through 1",
        token_cost=item.token_cost,
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.CANDIDATE,
        created_at=NOW,
        committed_at=None,
    )
    return snapshot, memberships


def ready_job(
    store: ac.SQLiteRepository,
    *,
    candidate_snapshot_id: str,
) -> ac.CompactionJobEnvelope:
    raised = store.raise_compaction_intent(
        job_id="job-1",
        session_key=session_key(),
        target_high_water_mark=1,
        now=NOW,
    )
    assert raised is not None
    leased = store.claim_job(
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert leased is not None
    compiling = store.transition_job(
        job_id=leased.job_id,
        owner="worker-1",
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=NOW + timedelta(seconds=1),
    )
    return store.transition_job(
        job_id=compiling.job_id,
        owner="worker-1",
        lease_epoch=compiling.lease_epoch,
        to_state=ac.CompactionJobState.READY_TO_COMMIT,
        candidate_snapshot_id=candidate_snapshot_id,
        now=NOW + timedelta(seconds=2),
    )


def publish(
    store: ac.SQLiteRepository,
    job: ac.CompactionJobEnvelope,
    snapshot: ac.SnapshotEnvelope,
    memberships: tuple[ac.SnapshotCapsuleMembership, ...],
) -> ac.PublishResult:
    return store.publish_snapshot(
        job_id=job.job_id,
        owner="worker-1",
        lease_epoch=job.lease_epoch,
        candidate_snapshot=snapshot,
        memberships=memberships,
        token_ceiling=1_000,
        now=NOW + timedelta(minutes=1),
    )


def fail_at(boundary: str) -> Any:
    def injector(name: str) -> None:
        if name == boundary:
            raise InjectedCrash(f"injected crash at {boundary}")

    return injector


def assert_candidate_writes_absent(
    factory: ac.SQLiteConnectionFactory,
    *,
    expected_intent: int = 1,
) -> None:
    with factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, lease_owner, lease_epoch, candidate_snapshot_id,
                   intent_target_high_water_mark
            FROM compaction_jobs
            """
        ).fetchone()
        assert tuple(row) == (
            "READY_TO_COMMIT",
            "worker-1",
            1,
            "snapshot-1",
            expected_intent,
        )
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshot_capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM active_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize(
    "boundary",
    (
        "capture.after_allocate",
        "capture.before_insert",
        "capture.after_insert",
    ),
)
def test_capture_crash_rolls_back_session_sequence_and_event(
    tmp_path: Path,
    boundary: str,
) -> None:
    factory = migrated_factory(tmp_path)
    crashing = ac.SQLiteRepository(factory, fault_injector=fail_at(boundary))

    with pytest.raises(InjectedCrash, match=boundary):
        capture(crashing, 1)

    with factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0

    recovered = capture(ac.SQLiteRepository(factory), 1)
    assert recovered.sequence == 1


def test_claim_crash_rolls_back_owner_epoch_and_attempt(tmp_path: Path) -> None:
    factory = migrated_factory(tmp_path)
    stable = ac.SQLiteRepository(factory)
    capture(stable, 1)
    raised = stable.raise_compaction_intent(
        job_id="job-1",
        session_key=session_key(),
        target_high_water_mark=1,
        now=NOW,
    )
    assert raised is not None
    crashing = ac.SQLiteRepository(
        factory,
        fault_injector=fail_at("claim.after_update"),
    )

    with pytest.raises(InjectedCrash, match="claim.after_update"):
        crashing.claim_job(
            worker_id="worker-1",
            now=NOW,
            lease_expires_at=LEASE_END,
        )

    with factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, lease_owner, lease_epoch, attempt_count
            FROM compaction_jobs
            """
        ).fetchone()
        assert tuple(row) == ("PENDING", None, 0, 0)

    recovered = stable.claim_job(
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert recovered is not None
    assert (recovered.lease_epoch, recovered.attempt_count) == (1, 1)


@pytest.mark.parametrize(
    "boundary",
    (
        "publish.before_savepoint",
        "publish.after_capsule",
        "publish.after_snapshot",
        "publish.after_membership",
        "publish.after_pointer",
        "publish.after_terminal",
    ),
)
def test_publish_crash_rolls_back_every_candidate_and_terminal_boundary(
    tmp_path: Path,
    boundary: str,
) -> None:
    factory = migrated_factory(tmp_path)
    stable = ac.SQLiteRepository(factory)
    capture(stable, 1)
    snapshot, memberships = candidate_bundle()
    job = ready_job(stable, candidate_snapshot_id=snapshot.snapshot_id)
    crashing = ac.SQLiteRepository(factory, fault_injector=fail_at(boundary))

    with pytest.raises(InjectedCrash, match=boundary):
        publish(crashing, job, snapshot, memberships)

    assert_candidate_writes_absent(factory)
    recovered = publish(stable, job, snapshot, memberships)
    assert recovered.outcome == ac.PublishOutcome.COMMITTED


def test_follow_up_creation_crash_rolls_back_publication_and_pending_job(
    tmp_path: Path,
) -> None:
    factory = migrated_factory(tmp_path)
    stable = ac.SQLiteRepository(factory)
    capture(stable, 1)
    snapshot, memberships = candidate_bundle()
    job = ready_job(stable, candidate_snapshot_id=snapshot.snapshot_id)
    capture(stable, 2)
    raised = stable.raise_compaction_intent(
        job_id="ignored",
        session_key=session_key(),
        target_high_water_mark=2,
        now=NOW + timedelta(seconds=3),
    )
    assert raised is not None
    assert raised.intent_target_high_water_mark == 2
    crashing = ac.SQLiteRepository(
        factory,
        fault_injector=fail_at("publish.after_follow_up"),
    )

    with pytest.raises(InjectedCrash, match="publish.after_follow_up"):
        publish(crashing, job, snapshot, memberships)

    assert_candidate_writes_absent(factory, expected_intent=2)
    recovered = publish(stable, job, snapshot, memberships)
    assert recovered.outcome == ac.PublishOutcome.COMMITTED
    with factory.connection(read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT state, target_high_water_mark, base_snapshot_id, base_pointer_version
            FROM compaction_jobs
            ORDER BY created_at, job_id
            """
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("COMMITTED", 1, None, 0),
            ("PENDING", 2, "snapshot-1", 1),
        ]


def test_injected_integrity_error_is_not_misclassified_as_publish_conflict(
    tmp_path: Path,
) -> None:
    factory = migrated_factory(tmp_path)
    stable = ac.SQLiteRepository(factory)
    capture(stable, 1)
    snapshot, memberships = candidate_bundle()
    job = ready_job(stable, candidate_snapshot_id=snapshot.snapshot_id)

    def crash_after_snapshot(name: str) -> None:
        if name == "publish.after_snapshot":
            raise sqlite3.IntegrityError("injected crash after Snapshot insert")

    crashing = ac.SQLiteRepository(factory, fault_injector=crash_after_snapshot)

    with pytest.raises(sqlite3.IntegrityError, match="injected crash"):
        publish(crashing, job, snapshot, memberships)

    assert_candidate_writes_absent(factory)
