from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
LEASE_END = NOW + timedelta(minutes=5)


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


def repository(data_dir: Path) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    ac.SQLiteMigrator(factory).migrate()
    return ac.SQLiteRepository(factory)


def capture(store: ac.SQLiteRepository, sequence: int, key: ac.SessionKey) -> ac.EventEnvelope:
    return store.capture_user_event(
        event_id=f"event-{sequence}",
        session_key=key,
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW,
    )


def claim(store: ac.SQLiteRepository, key: ac.SessionKey) -> ac.CompactionJobEnvelope:
    store.raise_compaction_intent(
        job_id="job-1",
        session_key=key,
        target_high_water_mark=2,
        now=NOW,
    )
    job = store.claim_job(
        worker_id="worker-1",
        now=NOW,
        lease_expires_at=LEASE_END,
    )
    assert job is not None
    return job


def seed_committed_snapshot(
    store: ac.SQLiteRepository,
    key: ac.SessionKey,
    *,
    number: int,
    covered_event_end: int,
    base_snapshot_id: str | None,
    pointer_version: int,
) -> ac.SnapshotEnvelope:
    capsule = ac.ContextCapsuleEnvelope(
        capsule_id=f"capsule-{number}",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=key,
        covered_event_start=covered_event_end,
        covered_event_end=covered_event_end,
        source_event_ids=(f"event-{covered_event_end}",),
        goals=(
            ac.CapsuleClaim(
                claim_id=f"goal-{number}",
                text=f"committed context {number}",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=(f"event-{covered_event_end}",),
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
                anchor_id=f"anchor-{number}",
                anchor_type=ac.AnchorType.NAME,
                exact_text=f"AstrContinuum {number}",
                source_event_ids=(f"event-{covered_event_end}",),
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(),
        narrative_summary=f"Committed context {number}.",
        token_cost=12,
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
        snapshot_id=f"snapshot-{number}",
        session_key=key,
        base_snapshot_id=base_snapshot_id,
        covered_event_end=covered_event_end,
        source_high_water_mark=covered_event_end,
        capsule_ids=(capsule.capsule_id,),
        exact_anchor_ids=(f"anchor-{number}",),
        rendered_context=capsule.narrative_summary,
        token_cost=capsule.token_cost,
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.COMMITTED,
        created_at=NOW,
        committed_at=NOW,
    )
    timestamp = "2026-07-26T12:00:00.000000Z"
    with store.factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO capsules (
                capsule_id, session_key_hash, level, covered_event_start,
                covered_event_end, canonical_capsule_json, token_cost,
                source_coverage, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capsule.capsule_id,
                key.session_key_hash,
                capsule.level.value,
                capsule.covered_event_start,
                capsule.covered_event_end,
                json.dumps(capsule.model_dump(mode="json"), separators=(",", ":")),
                capsule.token_cost,
                capsule.quality.source_coverage,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO snapshots (
                snapshot_id, session_key_hash, base_snapshot_id, covered_event_end,
                source_high_water_mark, exact_anchor_ids_json, rendered_context,
                token_cost, audit_outcome, lifecycle_state, created_at, committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', ?, ?)
            """,
            (
                snapshot.snapshot_id,
                key.session_key_hash,
                snapshot.base_snapshot_id,
                snapshot.covered_event_end,
                snapshot.source_high_water_mark,
                json.dumps(list(snapshot.exact_anchor_ids), separators=(",", ":")),
                snapshot.rendered_context,
                snapshot.token_cost,
                json.dumps(snapshot.audit_outcome.model_dump(mode="json"), separators=(",", ":")),
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
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_key_hash) DO UPDATE SET
                snapshot_id = excluded.snapshot_id,
                pointer_version = excluded.pointer_version,
                updated_at = excluded.updated_at
            """,
            (key.session_key_hash, snapshot.snapshot_id, pointer_version, timestamp),
        )
    return snapshot


def test_claimed_view_excludes_events_appended_after_frozen_target(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    first = capture(store, 1, key)
    second = capture(store, 2, key)
    job = claim(store, key)
    capture(store, 3, key)

    view = store.read_claimed_request_view(
        job_id=job.job_id,
        owner="worker-1",
        lease_epoch=job.lease_epoch,
        now=NOW + timedelta(seconds=1),
    )

    assert view.session_key == key
    assert view.snapshot is None
    assert view.memberships == ()
    assert view.pointer_version == 0
    assert view.covered_event_end == 0
    assert view.high_water_mark == 2
    assert view.delta == (first, second)


@pytest.mark.parametrize(
    ("owner", "lease_epoch"),
    (("other-worker", 1), ("worker-1", 2)),
)
def test_claimed_view_rejects_stale_owner_or_epoch(
    tmp_path: Path,
    owner: str,
    lease_epoch: int,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    job = claim(store, key)

    with pytest.raises(ac.StaleLeaseError):
        store.read_claimed_request_view(
            job_id=job.job_id,
            owner=owner,
            lease_epoch=lease_epoch,
            now=NOW + timedelta(seconds=1),
        )


def test_claimed_view_rejects_lease_at_its_expiration(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    job = claim(store, key)

    with pytest.raises(ac.StaleLeaseError):
        store.read_claimed_request_view(
            job_id=job.job_id,
            owner="worker-1",
            lease_epoch=job.lease_epoch,
            now=LEASE_END,
        )


def test_claimed_view_uses_historical_committed_base_after_pointer_advances(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    first = capture(store, 1, key)
    second = capture(store, 2, key)
    third = capture(store, 3, key)
    frozen_snapshot = seed_committed_snapshot(
        store,
        key,
        number=1,
        covered_event_end=1,
        base_snapshot_id=None,
        pointer_version=1,
    )
    job = claim(store, key)
    seed_committed_snapshot(
        store,
        key,
        number=2,
        covered_event_end=2,
        base_snapshot_id=frozen_snapshot.snapshot_id,
        pointer_version=2,
    )

    view = store.read_claimed_request_view(
        job_id=job.job_id,
        owner="worker-1",
        lease_epoch=job.lease_epoch,
        now=NOW + timedelta(seconds=1),
    )

    assert view.snapshot == frozen_snapshot
    assert view.pointer_version == 1
    assert view.high_water_mark == 2
    assert view.delta == (second,)
    assert first not in view.delta
    assert third not in view.delta


def test_claimed_view_rejects_a_gap_in_its_frozen_delta(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key)
    capture(store, 2, key)
    job = claim(store, key)
    with store.factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE compaction_jobs
            SET target_high_water_mark = 3,
                intent_target_high_water_mark = 3
            WHERE job_id = ?
            """,
            (job.job_id,),
        )

    with pytest.raises(ac.RepositoryInvariantError, match="contiguous"):
        store.read_claimed_request_view(
            job_id=job.job_id,
            owner="worker-1",
            lease_epoch=job.lease_epoch,
            now=NOW + timedelta(seconds=1),
        )
