from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

import pytest

import astrcontinuum as ac
import astrcontinuum.storage.repository as repository_module
from astrcontinuum.storage import CanonicalMetricObservation
from astrcontinuum.tokenization import CANONICAL_O200K
from tests.storage.security_testkit import secure_repository, storage_test_codec

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


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


def repository(
    data_dir: Path,
    *,
    fault_injector: Any | None = None,
) -> ac.SQLiteRepository:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    return secure_repository(factory, fault_injector=fault_injector)


def capture(
    store: ac.SQLiteRepository,
    sequence: int,
    *,
    key: ac.SessionKey | None = None,
) -> ac.EventEnvelope:
    return store.capture_user_event(
        event_id=f"event-{sequence}",
        session_key=key or session_key(),
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        canonical=CanonicalMetricObservation(CANONICAL_O200K.profile_id, None),
        created_at=NOW,
    )


def capsule(key: ac.SessionKey) -> ac.ContextCapsuleEnvelope:
    return ac.ContextCapsuleEnvelope(
        capsule_id="capsule-1",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
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


def canonical_json(value: Any) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def seed_active_snapshot(store: ac.SQLiteRepository, key: ac.SessionKey) -> None:
    item = capsule(key)
    snapshot = committed_snapshot(key)
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
                token_cost_envelope,
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
                codec.encrypt_non_negative_int(
                    "capsules",
                    "token_cost",
                    item.capsule_id,
                    item.token_cost,
                ),
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
                token_cost_envelope,
                audit_outcome,
                lifecycle_state,
                created_at,
                committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', ?, ?)
            """,
            (
                snapshot.snapshot_id,
                key.session_key_hash,
                snapshot.base_snapshot_id,
                snapshot.covered_event_end,
                snapshot.source_high_water_mark,
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
                    canonical_json(snapshot.audit_outcome),
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
            (snapshot.snapshot_id, item.capsule_id),
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
            (key.session_key_hash, snapshot.snapshot_id, timestamp),
        )


def test_bootstrap_view_uses_logical_empty_base_and_ordered_delta(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    first = capture(store, 1, key=key)
    second = capture(store, 2, key=key)

    view = store.read_request_view(key)

    assert view.session_key == key
    assert view.snapshot is None
    assert view.memberships == ()
    assert view.capsules == ()
    assert view.pointer_version == 0
    assert view.covered_event_end == 0
    assert view.high_water_mark == 2
    assert view.delta == (first, second)


def test_read_view_round_trips_committed_snapshot_capsules_and_delta(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key=key)
    second = capture(store, 2, key=key)
    third = capture(store, 3, key=key)
    seed_active_snapshot(store, key)

    view = store.read_request_view(key)

    assert view.snapshot == committed_snapshot(key)
    assert view.pointer_version == 1
    assert view.covered_event_end == 1
    assert view.high_water_mark == 3
    assert view.delta == (second, third)
    assert len(view.memberships) == 1
    assert view.memberships[0].ordinal == 0
    assert view.memberships[0].slot == "memory"
    assert view.memberships[0].capsule == capsule(key)
    assert view.capsules == (capsule(key),)


def test_missing_session_has_an_empty_non_durable_view(tmp_path: Path) -> None:
    store = repository(tmp_path)

    view = store.read_request_view(session_key("missing"))

    assert view.snapshot is None
    assert view.pointer_version == 0
    assert view.covered_event_end == 0
    assert view.high_water_mark == 0
    assert view.delta == ()


def test_read_view_retries_a_transient_sqlite_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    first = capture(store, 1, key=key)
    original_read = store._read_request_view
    attempts = 0
    delays: list[float] = []

    class FakeTime:
        @staticmethod
        def sleep(seconds: float) -> None:
            delays.append(seconds)

    def locked_once(
        connection: sqlite3.Connection,
        requested_key: ac.SessionKey,
    ) -> ac.RequestView:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_read(connection, requested_key)

    monkeypatch.setattr(repository_module, "time", FakeTime, raising=False)
    monkeypatch.setattr(store, "_read_request_view", locked_once)

    view = store.read_request_view(key)

    assert attempts == 2
    assert delays == [0.01]
    assert view.delta == (first,)


def test_read_view_does_not_retry_non_lock_operational_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    attempts = 0

    def broken_read(
        _connection: sqlite3.Connection,
        _requested_key: ac.SessionKey,
    ) -> ac.RequestView:
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "_read_request_view", broken_read)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store.read_request_view(key)

    assert attempts == 1


def test_read_view_does_not_retry_non_lock_error_code_despite_lock_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    attempts = 0
    delays: list[float] = []
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR

    class FakeTime:
        @staticmethod
        def sleep(seconds: float) -> None:
            delays.append(seconds)

    def misleading_error(
        _connection: sqlite3.Connection,
        _requested_key: ac.SessionKey,
    ) -> ac.RequestView:
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr(repository_module, "time", FakeTime)
    monkeypatch.setattr(store, "_read_request_view", misleading_error)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        store.read_request_view(key)

    assert attempts == 1
    assert delays == []


def test_read_view_re_raises_after_bounded_sqlite_lock_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = repository(tmp_path)
    key = session_key()
    attempts = 0
    delays: list[float] = []

    class FakeTime:
        @staticmethod
        def sleep(seconds: float) -> None:
            delays.append(seconds)

    def always_locked(
        _connection: sqlite3.Connection,
        _requested_key: ac.SessionKey,
    ) -> ac.RequestView:
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository_module, "time", FakeTime, raising=False)
    monkeypatch.setattr(store, "_read_request_view", always_locked)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        store.read_request_view(key)

    assert attempts == 3
    assert delays == [0.01, 0.025]


def test_read_transaction_fixes_high_water_before_concurrent_append(
    tmp_path: Path,
) -> None:
    reached_high_water = Event()
    release_reader = Event()

    def failpoint(name: str) -> None:
        if name == "read.after_high_water":
            reached_high_water.set()
            assert release_reader.wait(timeout=5)

    setup = repository(tmp_path)
    key = session_key()
    first = capture(setup, 1, key=key)
    second = capture(setup, 2, key=key)
    reader = secure_repository(
        ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000),
        fault_injector=failpoint,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(reader.read_request_view, key)
        assert reached_high_water.wait(timeout=5)
        third = capture(
            secure_repository(ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)),
            3,
            key=key,
        )
        release_reader.set()
        fixed = pending.result(timeout=5)

    assert fixed.high_water_mark == 2
    assert fixed.delta == (first, second)
    following = setup.read_request_view(key)
    assert following.high_water_mark == 3
    assert following.delta == (first, second, third)


def test_no_gap_violation_is_reported_instead_of_fabricated(tmp_path: Path) -> None:
    store = repository(tmp_path)
    key = session_key()
    capture(store, 1, key=key)
    capture(store, 2, key=key)

    with store.factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE sessions
            SET next_event_sequence = 4
            WHERE session_key_hash = ?
            """,
            (key.session_key_hash,),
        )

    error_type = getattr(ac, "RepositoryInvariantError", None)
    assert error_type is not None, "RepositoryInvariantError export is missing"
    with pytest.raises(error_type, match="contiguous"):
        store.read_request_view(key)
