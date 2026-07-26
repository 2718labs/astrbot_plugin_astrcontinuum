from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
TEST_KEY = bytes(range(32))


def storage_keys() -> ac.ResolvedKeyMaterial:
    return ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(TEST_KEY),
        previous=None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )


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


def event(
    key: ac.SessionKey,
    sequence: int,
    *,
    content: str | None = None,
) -> ac.EventEnvelope:
    return ac.EventEnvelope.create(
        event_id=f"event-{sequence}",
        session_key=key,
        sequence=sequence,
        event_type=ac.EventType.USER_MESSAGE,
        content=content if content is not None else f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW,
    )


def capsule(key: ac.SessionKey) -> ac.ContextCapsuleEnvelope:
    return ac.ContextCapsuleEnvelope(
        capsule_id="capsule-1",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.TASK,
        session_key=key,
        covered_event_start=1,
        covered_event_end=1,
        source_event_ids=("event-1",),
        goals=(
            ac.CapsuleClaim(
                claim_id="goal-1",
                text="Keep the request view exact",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=("event-1",),
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
                anchor_id="anchor-1",
                anchor_type=ac.AnchorType.NAME,
                exact_text="AstrContinuum",
                source_event_ids=("event-1",),
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(),
        narrative_summary="Committed request context.",
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


def committed_snapshot(key: ac.SessionKey) -> ac.SnapshotEnvelope:
    return ac.SnapshotEnvelope(
        snapshot_id="snapshot-1",
        session_key=key,
        base_snapshot_id=None,
        covered_event_end=1,
        source_high_water_mark=1,
        capsule_ids=("capsule-1",),
        exact_anchor_ids=("anchor-1",),
        rendered_context="Committed request context.",
        token_cost=12,
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.COMMITTED,
        created_at=NOW,
        committed_at=NOW,
    )


def runtime_reader() -> Callable[[Any, ac.SessionKey], ac.RequestView]:
    reader = getattr(ac, "read_request_view", None)
    assert reader is not None, "canonical read_request_view export is missing"
    return reader


class RecordingSource:
    def __init__(self, view: ac.RequestView) -> None:
        self.view = view
        self.calls: list[ac.SessionKey] = []

    def read_request_view(self, key: ac.SessionKey) -> ac.RequestView:
        self.calls.append(key)
        return self.view


def test_read_facade_calls_source_once_and_returns_same_bootstrap_view() -> None:
    key = session_key()
    view = ac.RequestView(
        session_key=key,
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=2,
        delta=(event(key, 1), event(key, 2)),
    )
    source = RecordingSource(view)

    returned = runtime_reader()(source, key)

    assert returned is view
    assert source.calls == [key]
    assert returned.covered_event_end == 0
    assert returned.high_water_mark == 2
    assert tuple(item.sequence for item in returned.delta) == (1, 2)


def test_read_facade_accepts_committed_snapshot_with_exact_contiguous_delta() -> None:
    key = session_key()
    item = capsule(key)
    view = ac.RequestView(
        session_key=key,
        snapshot=committed_snapshot(key),
        memberships=(
            ac.SnapshotCapsuleMembership(
                ordinal=0,
                slot="active_task",
                capsule=item,
            ),
        ),
        pointer_version=1,
        covered_event_end=1,
        high_water_mark=3,
        delta=(event(key, 2), event(key, 3)),
    )

    returned = runtime_reader()(RecordingSource(view), key)

    assert returned.snapshot is view.snapshot
    assert returned.capsules == (item,)
    assert returned.covered_event_end == 1
    assert returned.high_water_mark == 3
    assert tuple(item.sequence for item in returned.delta) == (2, 3)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda view, other: replace(view, session_key=other),
            "REQUEST_VIEW_SESSION_MISMATCH",
        ),
        (
            lambda view, _other: replace(view, covered_event_end=1),
            "REQUEST_VIEW_BOOTSTRAP_INVALID",
        ),
        (
            lambda view, _other: replace(view, high_water_mark=3),
            "REQUEST_VIEW_DELTA_GAP",
        ),
    ],
)
def test_read_facade_rejects_invalid_identity_or_bootstrap_coverage_without_content(
    mutate: Callable[[ac.RequestView, ac.SessionKey], ac.RequestView],
    expected_code: str,
) -> None:
    key = session_key()
    secret = "TOP-SECRET-CONTENT"
    valid = ac.RequestView(
        session_key=key,
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=2,
        delta=(event(key, 1, content=secret), event(key, 2)),
    )
    source = RecordingSource(mutate(valid, session_key("other")))
    error_type = getattr(ac, "RequestViewInvariantError", ValueError)

    with pytest.raises(error_type) as caught:
        runtime_reader()(source, key)

    assert getattr(caught.value, "code", None) == expected_code
    assert str(caught.value) == expected_code
    assert secret not in str(caught.value)
    assert source.calls == [key]


def test_read_facade_rejects_committed_pointer_or_delta_identity_mismatch() -> None:
    key = session_key()
    item = capsule(key)
    valid = ac.RequestView(
        session_key=key,
        snapshot=committed_snapshot(key),
        memberships=(
            ac.SnapshotCapsuleMembership(
                ordinal=0,
                slot="active_task",
                capsule=item,
            ),
        ),
        pointer_version=1,
        covered_event_end=1,
        high_water_mark=2,
        delta=(event(key, 2),),
    )
    wrong_event = event(session_key("other"), 2, content="PRIVATE-EVENT")
    invalid_views = (
        (replace(valid, pointer_version=0), "REQUEST_VIEW_SNAPSHOT_INVALID"),
        (replace(valid, delta=(wrong_event,)), "REQUEST_VIEW_DELTA_IDENTITY_MISMATCH"),
    )
    error_type = getattr(ac, "RequestViewInvariantError", ValueError)

    for invalid, expected_code in invalid_views:
        with pytest.raises(error_type) as caught:
            runtime_reader()(RecordingSource(invalid), key)
        assert getattr(caught.value, "code", None) == expected_code
        assert str(caught.value) == expected_code
        assert "PRIVATE-EVENT" not in str(caught.value)


def repository(
    data_dir: Path,
) -> tuple[ac.SQLiteRepository, ac.SecureCodec]:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    activation = ac.activate_storage_security(factory, storage_keys())
    return (
        ac.SQLiteRepository(factory, codec=activation.codec),
        activation.codec,
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def seed_active_snapshot(
    store: ac.SQLiteRepository,
    codec: ac.SecureCodec,
    key: ac.SessionKey,
) -> None:
    item = capsule(key)
    snapshot = committed_snapshot(key)
    timestamp = "2026-07-26T12:00:00.000000Z"
    protected_capsule = codec.encrypt_object_json(
        "capsules",
        "canonical_capsule_json",
        item.capsule_id,
        canonical_json(item),
    )
    protected_anchor_ids = codec.encrypt_array_json(
        "snapshots",
        "exact_anchor_ids_json",
        snapshot.snapshot_id,
        json.dumps(list(snapshot.exact_anchor_ids), separators=(",", ":")),
    )
    protected_rendered_context = codec.encrypt_text(
        "snapshots",
        "rendered_context",
        snapshot.snapshot_id,
        snapshot.rendered_context,
    )
    protected_audit_outcome = codec.encrypt_object_json(
        "snapshots",
        "audit_outcome",
        snapshot.snapshot_id,
        canonical_json(snapshot.audit_outcome),
    )
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
                item.capsule_id,
                key.session_key_hash,
                item.level.value,
                item.covered_event_start,
                item.covered_event_end,
                protected_capsule,
                item.token_cost,
                item.quality.source_coverage,
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
                protected_anchor_ids,
                protected_rendered_context,
                snapshot.token_cost,
                protected_audit_outcome,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO snapshot_capsules (snapshot_id, ordinal, capsule_id, slot)
            VALUES (?, 0, ?, 'active_task')
            """,
            (snapshot.snapshot_id, item.capsule_id),
        )
        connection.execute(
            """
            INSERT INTO active_snapshots (
                session_key_hash, snapshot_id, pointer_version, updated_at
            ) VALUES (?, ?, 1, ?)
            """,
            (key.session_key_hash, snapshot.snapshot_id, timestamp),
        )


def durable_fingerprint(store: ac.SQLiteRepository) -> tuple[tuple[object, ...], ...]:
    statements = (
        "SELECT event_id, sequence, content FROM journal_events ORDER BY sequence",
        """
        SELECT session_key_hash, snapshot_id, pointer_version
        FROM active_snapshots
        ORDER BY session_key_hash
        """,
        """
        SELECT snapshot_id, covered_event_end, source_high_water_mark, lifecycle_state
        FROM snapshots
        ORDER BY snapshot_id
        """,
        """
        SELECT job_id, state, target_high_water_mark, lease_epoch
        FROM compaction_jobs
        ORDER BY job_id
        """,
    )
    with store.factory.connection(read_only=True) as connection:
        return tuple(
            tuple(tuple(row) for row in connection.execute(statement).fetchall())
            for statement in statements
        )


def test_file_backed_read_facade_preserves_all_durable_runtime_state(
    tmp_path: Path,
) -> None:
    store, codec = repository(tmp_path)
    key = session_key()
    for sequence in (1, 2):
        store.capture_user_event(
            event_id=f"event-{sequence}",
            session_key=key,
            content=f"message {sequence}",
            idempotency_key=f"request-{sequence}",
            token_count=2,
            created_at=NOW,
        )
    seed_active_snapshot(store, codec, key)
    pending = store.raise_compaction_intent(
        job_id="job-1",
        session_key=key,
        target_high_water_mark=2,
        now=NOW,
    )
    assert pending is not None
    before = durable_fingerprint(store)

    view = runtime_reader()(store, key)

    after = durable_fingerprint(store)
    assert view.covered_event_end == 1
    assert view.high_water_mark == 2
    assert tuple(item.sequence for item in view.delta) == (2,)
    assert after == before
