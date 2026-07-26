from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import astrcontinuum as ac
from tests.storage.security_testkit import secure_repository, storage_test_codec

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
LEASE_END = NOW + timedelta(minutes=10)


def session_key(session_id: str = "session-1") -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=session_id,
        group_id=None,
        user_id="user-1",
        conversation_id=f"conversation-{session_id}",
        persona_id=None,
    )


def repository(data_dir: Path) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    return secure_repository(factory)


def capture(
    store: ac.SQLiteRepository,
    sequence: int,
    *,
    key: ac.SessionKey | None = None,
) -> ac.EventEnvelope:
    key = key or session_key()
    return store.capture_user_event(
        event_id=f"{key.session_id}-event-{sequence}",
        session_key=key,
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW,
    )


def capsule(
    sequence: int,
    *,
    key: ac.SessionKey | None = None,
    capsule_id: str | None = None,
    goal_id: str | None = None,
    anchor_id: str | None = None,
) -> ac.ContextCapsuleEnvelope:
    key = key or session_key()
    event_id = f"{key.session_id}-event-{sequence}"
    return ac.ContextCapsuleEnvelope(
        capsule_id=capsule_id or f"capsule-{sequence}",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=key,
        covered_event_start=sequence,
        covered_event_end=sequence,
        source_event_ids=(event_id,),
        goals=(
            ac.CapsuleClaim(
                claim_id=goal_id or f"goal-{sequence}",
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
                anchor_id=anchor_id or f"anchor-{sequence}",
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


def memberships(
    *capsules: ac.ContextCapsuleEnvelope,
) -> tuple[ac.SnapshotCapsuleMembership, ...]:
    return tuple(
        ac.SnapshotCapsuleMembership(
            ordinal=ordinal,
            slot="memory",
            capsule=item,
        )
        for ordinal, item in enumerate(capsules)
    )


def candidate_snapshot(
    snapshot_id: str,
    members: tuple[ac.SnapshotCapsuleMembership, ...],
    *,
    key: ac.SessionKey | None = None,
    base_snapshot_id: str | None = None,
    target: int,
) -> ac.SnapshotEnvelope:
    key = key or session_key()
    return ac.SnapshotEnvelope(
        snapshot_id=snapshot_id,
        session_key=key,
        base_snapshot_id=base_snapshot_id,
        covered_event_end=target,
        source_high_water_mark=target,
        capsule_ids=tuple(item.capsule_id for item in members),
        exact_anchor_ids=tuple(
            anchor.anchor_id
            for item in members
            for anchor in item.capsule.exact_anchors
            if anchor.status == ac.AnchorStatus.ACTIVE
        ),
        rendered_context=f"Snapshot through {target}",
        token_cost=sum(item.capsule.token_cost for item in members),
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.CANDIDATE,
        created_at=NOW,
        committed_at=None,
    )


def ready_job(
    store: ac.SQLiteRepository,
    *,
    target: int,
    candidate_snapshot_id: str,
    job_id: str,
    worker_id: str = "worker-1",
    key: ac.SessionKey | None = None,
) -> ac.CompactionJobEnvelope:
    key = key or session_key()
    raised = store.raise_compaction_intent(
        job_id=job_id,
        session_key=key,
        target_high_water_mark=target,
        now=NOW,
    )
    assert raised is not None
    leased = store.claim_job(
        worker_id=worker_id,
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert leased is not None
    compiling = store.transition_job(
        job_id=leased.job_id,
        owner=worker_id,
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=NOW + timedelta(seconds=1),
    )
    return store.transition_job(
        job_id=compiling.job_id,
        owner=worker_id,
        lease_epoch=compiling.lease_epoch,
        to_state=ac.CompactionJobState.READY_TO_COMMIT,
        candidate_snapshot_id=candidate_snapshot_id,
        now=NOW + timedelta(seconds=2),
    )


def publish(
    store: ac.SQLiteRepository,
    job: ac.CompactionJobEnvelope,
    snapshot: ac.SnapshotEnvelope,
    members: tuple[ac.SnapshotCapsuleMembership, ...],
    *,
    owner: str = "worker-1",
) -> Any:
    return store.publish_snapshot(
        job_id=job.job_id,
        owner=owner,
        lease_epoch=job.lease_epoch,
        candidate_snapshot=snapshot,
        memberships=members,
        token_ceiling=1_000,
        now=NOW + timedelta(minutes=1),
    )


def test_bootstrap_publish_commits_candidate_pointer_and_job_atomically(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    item = capsule(1)
    members = memberships(item)
    snapshot = candidate_snapshot("snapshot-1", members, target=1)
    job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=snapshot.snapshot_id,
        job_id="job-1",
    )

    result = publish(store, job, snapshot, members)

    outcome_type = getattr(ac, "PublishOutcome", None)
    assert outcome_type is not None, "PublishOutcome export is missing"
    assert result.outcome == outcome_type.COMMITTED
    assert result.job.state == ac.CompactionJobState.COMMITTED
    assert result.job.candidate_snapshot_id == snapshot.snapshot_id
    assert result.job.lease_owner is None
    assert result.job.committed_at == NOW + timedelta(minutes=1)
    assert result.winner.state == ac.SnapshotState.COMMITTED
    assert result.winner.committed_at == NOW + timedelta(minutes=1)
    assert result.pointer_version == 1

    view = store.read_request_view(session_key())
    assert view.snapshot == result.winner
    assert view.capsules == (item,)
    assert view.delta == ()
    with store.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM snapshot_capsules").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM active_snapshots").fetchone()[0] == 1


def test_existing_pointer_publish_reuses_base_capsule_and_advances_version(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    first_capsule = capsule(1)
    first_members = memberships(first_capsule)
    first_snapshot = candidate_snapshot("snapshot-1", first_members, target=1)
    first_job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=first_snapshot.snapshot_id,
        job_id="job-1",
    )
    publish(store, first_job, first_snapshot, first_members)

    capture(store, 2)
    second_capsule = capsule(2)
    second_members = memberships(first_capsule, second_capsule)
    second_snapshot = candidate_snapshot(
        "snapshot-2",
        second_members,
        base_snapshot_id="snapshot-1",
        target=2,
    )
    second_job = ready_job(
        store,
        target=2,
        candidate_snapshot_id=second_snapshot.snapshot_id,
        job_id="job-2",
    )

    result = publish(store, second_job, second_snapshot, second_members)

    assert result.outcome == ac.PublishOutcome.COMMITTED
    assert result.pointer_version == 2
    assert result.winner.snapshot_id == "snapshot-2"
    with store.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        rows = connection.execute(
            """
            SELECT capsule_id
            FROM snapshot_capsules
            WHERE snapshot_id = 'snapshot-2'
            ORDER BY ordinal
            """
        ).fetchall()
        assert [row[0] for row in rows] == ["capsule-1", "capsule-2"]


def test_permanent_validation_rejects_cross_session_candidate_without_writes(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    wrong = capsule(1, key=session_key("other"))
    members = memberships(wrong)
    snapshot = candidate_snapshot("snapshot-invalid", members, target=1)
    job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=snapshot.snapshot_id,
        job_id="job-1",
    )

    rejected_type = getattr(ac, "PublicationRejected", None)
    assert rejected_type is not None, "PublicationRejected export is missing"
    with pytest.raises(rejected_type) as captured:
        publish(store, job, snapshot, members)

    assert ac.PermanentFailureCode.SESSION_KEY_MISMATCH in captured.value.report.failure_codes
    with store.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        row = connection.execute(
            "SELECT state, candidate_snapshot_id FROM compaction_jobs"
        ).fetchone()
        assert tuple(row) == ("READY_TO_COMMIT", "snapshot-invalid")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def seed_winning_snapshot(store: ac.SQLiteRepository) -> ac.SnapshotEnvelope:
    key = session_key()
    item = capsule(
        1,
        capsule_id="winner-capsule",
        goal_id="winner-goal",
        anchor_id="winner-anchor",
    )
    members = memberships(item)
    candidate = candidate_snapshot("snapshot-winner", members, target=1)
    committed = ac.SnapshotEnvelope(
        **{
            **candidate.model_dump(),
            "state": ac.SnapshotState.COMMITTED,
            "committed_at": NOW,
        }
    )
    timestamp = "2026-07-26T12:00:00.000000Z"
    codec = storage_test_codec()
    with store.factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO capsules (
                capsule_id,
                session_key_hash,
                level,
                covered_event_start,
                covered_event_end,
                canonical_capsule_json,
                token_cost,
                source_coverage,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.capsule_id,
                key.session_key_hash,
                item.level.value,
                item.covered_event_start,
                item.covered_event_end,
                codec.encrypt_object_json(
                    "capsules",
                    "canonical_capsule_json",
                    item.capsule_id,
                    canonical_json(item),
                ),
                item.token_cost,
                item.quality.source_coverage,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO snapshots (
                snapshot_id,
                session_key_hash,
                base_snapshot_id,
                covered_event_end,
                source_high_water_mark,
                exact_anchor_ids_json,
                rendered_context,
                token_cost,
                audit_outcome,
                lifecycle_state,
                created_at,
                committed_at
            ) VALUES (?, ?, NULL, 1, 1, ?, ?, ?, ?, 'COMMITTED', ?, ?)
            """,
            (
                committed.snapshot_id,
                key.session_key_hash,
                codec.encrypt_array_json(
                    "snapshots",
                    "exact_anchor_ids_json",
                    committed.snapshot_id,
                    json.dumps(list(committed.exact_anchor_ids), separators=(",", ":")),
                ),
                codec.encrypt_text(
                    "snapshots",
                    "rendered_context",
                    committed.snapshot_id,
                    committed.rendered_context,
                ),
                committed.token_cost,
                codec.encrypt_object_json(
                    "snapshots",
                    "audit_outcome",
                    committed.snapshot_id,
                    canonical_json(committed.audit_outcome),
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
            (committed.snapshot_id, item.capsule_id),
        )
        connection.execute(
            """
            INSERT INTO active_snapshots (
                session_key_hash,
                snapshot_id,
                pointer_version,
                updated_at
            ) VALUES (?, ?, 1, ?)
            """,
            (key.session_key_hash, committed.snapshot_id, timestamp),
        )
    return committed


def test_same_prefix_conflict_rolls_back_candidates_then_supersedes_job(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    item = capsule(1)
    members = memberships(item)
    candidate = candidate_snapshot("snapshot-loser", members, target=1)
    job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=candidate.snapshot_id,
        job_id="job-loser",
    )
    winner = seed_winning_snapshot(store)

    result = publish(store, job, candidate, members)

    assert result.outcome == ac.PublishOutcome.SUPERSEDED
    assert result.job.state == ac.CompactionJobState.SUPERSEDED
    assert result.job.candidate_snapshot_id == "snapshot-loser"
    assert result.winner == winner
    assert result.pointer_version == 1
    with store.factory.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM capsules WHERE capsule_id = 'capsule-1'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM snapshots WHERE snapshot_id = 'snapshot-loser'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM snapshot_capsules WHERE snapshot_id = 'snapshot-loser'"
            ).fetchone()[0]
            == 0
        )


def test_stale_fence_rejects_whole_publish_without_superseding(tmp_path: Path) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    item = capsule(1)
    members = memberships(item)
    candidate = candidate_snapshot("snapshot-stale", members, target=1)
    old_job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=candidate.snapshot_id,
        job_id="job-1",
    )
    store.recover_expired_leases(now=LEASE_END)
    reclaimed = store.claim_job(
        worker_id="worker-2",
        now=LEASE_END,
        lease_expires_at=LEASE_END + timedelta(minutes=5),
    )
    assert reclaimed is not None

    stale_type = getattr(ac, "StaleLeaseError", None)
    assert stale_type is not None
    with pytest.raises(stale_type):
        publish(store, old_job, candidate, members)

    with store.factory.connection(read_only=True) as connection:
        row = connection.execute(
            "SELECT state, lease_owner, lease_epoch FROM compaction_jobs"
        ).fetchone()
        assert tuple(row) == ("LEASED", "worker-2", 2)
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0


def test_success_preserves_higher_intent_as_pending_follow_up(tmp_path: Path) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    item = capsule(1)
    members = memberships(item)
    snapshot = candidate_snapshot("snapshot-1", members, target=1)
    ready_job(
        store,
        target=1,
        candidate_snapshot_id=snapshot.snapshot_id,
        job_id="job-1",
    )
    capture(store, 2)
    raised = store.raise_compaction_intent(
        job_id="ignored",
        session_key=session_key(),
        target_high_water_mark=2,
        now=NOW + timedelta(seconds=3),
    )
    assert raised is not None
    assert raised.target_high_water_mark == 1
    assert raised.intent_target_high_water_mark == 2

    result = publish(store, raised, snapshot, members)

    assert result.outcome == ac.PublishOutcome.COMMITTED
    with store.factory.connection(read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT state, target_high_water_mark, intent_target_high_water_mark,
                   base_snapshot_id, base_pointer_version
            FROM compaction_jobs
            ORDER BY created_at, job_id
            """
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("COMMITTED", 1, 2, None, 0),
            ("PENDING", 2, 2, "snapshot-1", 1),
        ]


def test_pointer_cas_loss_rolls_back_candidate_and_uses_winner_for_follow_up(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    first_capsule = capsule(1)
    first_members = memberships(first_capsule)
    first_snapshot = candidate_snapshot("snapshot-1", first_members, target=1)
    first_job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=first_snapshot.snapshot_id,
        job_id="job-1",
    )
    first_result = publish(store, first_job, first_snapshot, first_members)

    capture(store, 2)
    second_capsule = capsule(2)
    second_members = memberships(first_capsule, second_capsule)
    second_snapshot = candidate_snapshot(
        "snapshot-loser",
        second_members,
        base_snapshot_id="snapshot-1",
        target=2,
    )
    second_job = ready_job(
        store,
        target=2,
        candidate_snapshot_id=second_snapshot.snapshot_id,
        job_id="job-2",
    )
    with store.factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE active_snapshots
            SET pointer_version = 2
            WHERE session_key_hash = ?
            """,
            (session_key().session_key_hash,),
        )

    result = publish(store, second_job, second_snapshot, second_members)

    assert result.outcome == ac.PublishOutcome.SUPERSEDED
    assert result.winner == first_result.winner
    assert result.pointer_version == 2
    with store.factory.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM snapshots WHERE snapshot_id = 'snapshot-loser'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM capsules WHERE capsule_id = 'capsule-2'"
            ).fetchone()[0]
            == 0
        )
        follow_up = connection.execute(
            """
            SELECT state, target_high_water_mark, base_snapshot_id, base_pointer_version
            FROM compaction_jobs
            WHERE state = 'PENDING'
            """
        ).fetchone()
        assert tuple(follow_up) == ("PENDING", 2, "snapshot-1", 2)


def test_dangling_source_is_rejected_by_permanent_gate_without_writes(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    original = capsule(1)
    dangling = original.model_copy(
        update={
            "source_event_ids": ("missing-event",),
            "goals": (
                original.goals[0].model_copy(update={"source_event_ids": ("missing-event",)}),
            ),
            "exact_anchors": (
                original.exact_anchors[0].model_copy(
                    update={"source_event_ids": ("missing-event",)}
                ),
            ),
        }
    )
    members = memberships(dangling)
    snapshot = candidate_snapshot("snapshot-dangling", members, target=1)
    job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=snapshot.snapshot_id,
        job_id="job-1",
    )

    with pytest.raises(ac.PublicationRejected) as captured:
        publish(store, job, snapshot, members)

    assert ac.PermanentFailureCode.UNSUPPORTED_ACTIVE_SEMANTIC in (
        captured.value.report.failure_codes
    )
    assert ac.PermanentFailureCode.SOURCE_COVERAGE_NOT_FULL in (captured.value.report.failure_codes)
    with store.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT state FROM compaction_jobs").fetchone()[0] == (
            "READY_TO_COMMIT"
        )


def test_noncontiguous_membership_is_rejected_without_candidate_writes(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    item = capsule(1)
    invalid_members = (
        ac.SnapshotCapsuleMembership(
            ordinal=1,
            slot="memory",
            capsule=item,
        ),
    )
    snapshot = candidate_snapshot("snapshot-membership", invalid_members, target=1)
    job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=snapshot.snapshot_id,
        job_id="job-1",
    )

    with pytest.raises(ac.RepositoryInvariantError, match="ordinals"):
        publish(store, job, snapshot, invalid_members)

    with store.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT state FROM compaction_jobs").fetchone()[0] == (
            "READY_TO_COMMIT"
        )


def test_exact_anchor_list_mismatch_is_rejected_without_candidate_writes(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    item = capsule(1)
    members = memberships(item)
    snapshot = candidate_snapshot("snapshot-anchor-mismatch", members, target=1).model_copy(
        update={"exact_anchor_ids": ("wrong-anchor",)}
    )
    job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=snapshot.snapshot_id,
        job_id="job-1",
    )

    with pytest.raises(ac.PublicationRejected) as captured:
        publish(store, job, snapshot, members)

    assert ac.PermanentFailureCode.SNAPSHOT_ANCHOR_MISMATCH in (captured.value.report.failure_codes)
    with store.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT state FROM compaction_jobs").fetchone()[0] == (
            "READY_TO_COMMIT"
        )


def test_immutable_capsule_collision_preserves_winner_and_supersedes_loser(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    conflicting = capsule(
        1,
        capsule_id="winner-capsule",
        goal_id="loser-goal",
        anchor_id="loser-anchor",
    )
    members = memberships(conflicting)
    candidate = candidate_snapshot("snapshot-loser", members, target=1)
    job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=candidate.snapshot_id,
        job_id="job-loser",
    )
    winner = seed_winning_snapshot(store)

    result = publish(store, job, candidate, members)

    assert result.outcome == ac.PublishOutcome.SUPERSEDED
    assert result.winner == winner
    with store.factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT canonical_capsule_json
            FROM capsules
            WHERE capsule_id = 'winner-capsule'
            """
        ).fetchone()
        persisted = storage_test_codec().decrypt_object_json(
            "capsules",
            "canonical_capsule_json",
            "winner-capsule",
            row[0],
        )
        assert persisted != canonical_json(conflicting)
        assert (
            connection.execute(
                "SELECT count(*) FROM snapshots WHERE snapshot_id = 'snapshot-loser'"
            ).fetchone()[0]
            == 0
        )


def test_stale_fence_precedes_candidate_membership_validation(tmp_path: Path) -> None:
    store = repository(tmp_path)
    capture(store, 1)
    item = capsule(1)
    invalid_members = (
        ac.SnapshotCapsuleMembership(
            ordinal=1,
            slot="memory",
            capsule=item,
        ),
    )
    candidate = candidate_snapshot("snapshot-stale", invalid_members, target=1)
    old_job = ready_job(
        store,
        target=1,
        candidate_snapshot_id=candidate.snapshot_id,
        job_id="job-1",
    )
    store.recover_expired_leases(now=LEASE_END)
    reclaimed = store.claim_job(
        worker_id="worker-2",
        now=LEASE_END,
        lease_expires_at=LEASE_END + timedelta(minutes=5),
    )
    assert reclaimed is not None

    with pytest.raises(ac.StaleLeaseError):
        publish(store, old_job, candidate, invalid_members)

    with store.factory.connection(read_only=True) as connection:
        row = connection.execute(
            "SELECT state, lease_owner, lease_epoch FROM compaction_jobs"
        ).fetchone()
        assert tuple(row) == ("LEASED", "worker-2", 2)
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
