from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac

TEST_KEY = bytes(range(32))
NOW = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
PLAINTEXT_MARKER = "repository-plaintext-6b4e3f"


def _keys() -> ac.ResolvedKeyMaterial:
    return ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(TEST_KEY),
        previous=None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )


def _active_factory(
    data_dir: Path,
) -> tuple[ac.SQLiteConnectionFactory, ac.StorageSecurityActivation]:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    activation = ac.activate_storage_security(factory, _keys())
    return factory, activation


def _session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id=f"{PLAINTEXT_MARKER}-platform",
        message_type="friend_message",
        session_id=f"{PLAINTEXT_MARKER}-session",
        group_id=None,
        user_id=f"{PLAINTEXT_MARKER}-user",
        conversation_id=f"{PLAINTEXT_MARKER}-conversation",
        persona_id=None,
    )


def test_repository_requires_an_explicit_secure_codec(tmp_path: Path) -> None:
    factory, _ = _active_factory(tmp_path)

    with pytest.raises(TypeError):
        ac.SQLiteRepository(factory)


def test_capture_round_trips_all_event_mappings_with_only_ciphertext_in_sql(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    key = _session_key()
    events = (
        store.capture_user_event(
            event_id="event-user",
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-user-message",
            idempotency_key="request-user",
            token_count=3,
            created_at=NOW,
        ),
        store.capture_assistant_event(
            event_id="event-assistant",
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-assistant-message",
            idempotency_key="request-assistant",
            token_count=4,
            created_at=NOW,
        ),
        store.capture_tool_event(
            event_id="event-tool-call",
            session_key=key,
            event_type=ac.EventType.TOOL_CALL,
            content=f"{PLAINTEXT_MARKER}-tool-call",
            idempotency_key="request-tool-call",
            token_count=5,
            created_at=NOW,
        ),
        store.capture_tool_event(
            event_id="event-tool-result",
            session_key=key,
            event_type=ac.EventType.TOOL_RESULT,
            content=f"{PLAINTEXT_MARKER}-tool-result",
            idempotency_key="request-tool-result",
            token_count=6,
            created_at=NOW,
        ),
    )

    view = store.read_request_view(key)

    assert view.delta == events
    assert tuple(item.sequence for item in view.delta) == (1, 2, 3, 4)
    with factory.connection(read_only=True) as connection:
        session = connection.execute("SELECT * FROM sessions").fetchone()
        assert session is not None
        assert str(session["canonical_session_key_json"]) != key.canonical_json()
        assert "$astrcontinuum_encrypted" in str(session["canonical_session_key_json"])
        for column in (
            "platform_instance_id",
            "message_type",
            "session_id",
            "user_id",
            "conversation_id",
        ):
            assert str(session[column]).startswith(f"acenc:v1:{activation.key_id}:")
            assert getattr(key, column) not in str(session[column])
        assert session["group_id"] is None
        assert session["persona_id"] is None

        rows = connection.execute(
            "SELECT event_id, content FROM journal_events ORDER BY sequence"
        ).fetchall()
        assert [str(row["event_id"]) for row in rows] == [item.event_id for item in events]
        assert all(str(row["content"]).startswith(f"acenc:v1:{activation.key_id}:") for row in rows)
        assert all(
            event.content not in str(row["content"])
            for event, row in zip(events, rows, strict=True)
        )

    marker = PLAINTEXT_MARKER.encode()
    for path in (
        factory.database_path,
        Path(f"{factory.database_path}-wal"),
        Path(f"{factory.database_path}-shm"),
    ):
        if path.exists():
            assert marker not in path.read_bytes()


def test_cross_row_ciphertext_swap_fails_authenticated_read_without_echo(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    key = _session_key()
    for index in (1, 2):
        store.capture_user_event(
            event_id=f"event-{index}",
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-message-{index}",
            idempotency_key=f"request-{index}",
            token_count=3,
            created_at=NOW,
        )
    with factory.transaction(immediate=True) as connection:
        connection.execute("DROP TRIGGER journal_events_immutable_update")
        first = connection.execute(
            "SELECT content FROM journal_events WHERE event_id = 'event-1'"
        ).fetchone()["content"]
        second = connection.execute(
            "SELECT content FROM journal_events WHERE event_id = 'event-2'"
        ).fetchone()["content"]
        connection.execute(
            "UPDATE journal_events SET content = ? WHERE event_id = 'event-1'",
            (second,),
        )
        connection.execute(
            "UPDATE journal_events SET content = ? WHERE event_id = 'event-2'",
            (first,),
        )
        connection.execute(
            """
            CREATE TRIGGER journal_events_immutable_update
            BEFORE UPDATE ON journal_events
            BEGIN
                SELECT RAISE(ABORT, 'journal_events rows are immutable');
            END
            """
        )

    with pytest.raises(ac.StorageSecurityError) as raised:
        store.read_request_view(key)

    assert raised.value.code is ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED
    assert PLAINTEXT_MARKER not in repr(raised.value)


def test_compaction_rejects_cross_session_base_snapshot_relationship(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    first_key = _session_key()
    second_key = first_key.model_copy(
        update={
            "session_id": f"{PLAINTEXT_MARKER}-second-session",
            "conversation_id": f"{PLAINTEXT_MARKER}-second-conversation",
        }
    )
    for event_id, key in (
        ("event-first-session", first_key),
        ("event-second-session", second_key),
    ):
        store.capture_user_event(
            event_id=event_id,
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-{event_id}",
            idempotency_key=f"request-{event_id}",
            token_count=3,
            created_at=NOW,
        )

    snapshot_id = "snapshot-first-session"
    timestamp = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    with factory.transaction(immediate=True) as connection:
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
            ) VALUES (?, ?, NULL, 1, 1, ?, ?, 0, ?, 'COMMITTED', ?, ?)
            """,
            (
                snapshot_id,
                first_key.session_key_hash,
                activation.codec.encrypt_array_json(
                    "snapshots",
                    "exact_anchor_ids_json",
                    snapshot_id,
                    "[]",
                ),
                activation.codec.encrypt_text(
                    "snapshots",
                    "rendered_context",
                    snapshot_id,
                    f"{PLAINTEXT_MARKER}-foreign-snapshot",
                ),
                activation.codec.encrypt_object_json(
                    "snapshots",
                    "audit_outcome",
                    snapshot_id,
                    ('{"mechanical_passed":true,"semantic_status":"NOT_RUN","failure_codes":[]}'),
                ),
                timestamp,
                timestamp,
            ),
        )

    job = store.raise_compaction_intent(
        job_id="job-second-session",
        session_key=second_key,
        target_high_water_mark=1,
        now=NOW,
    )
    assert job is not None
    leased = store.claim_job(
        worker_id="worker-cross-session",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert leased is not None
    with factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE compaction_jobs
            SET base_snapshot_id = ?, base_pointer_version = 1
            WHERE job_id = ?
            """,
            (snapshot_id, job.job_id),
        )
    compiling = store.transition_job(
        job_id=leased.job_id,
        owner="worker-cross-session",
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=NOW,
    )

    with pytest.raises(ac.RepositoryInvariantError, match="different durable session"):
        store.read_compaction_view(
            job_id=compiling.job_id,
            owner="worker-cross-session",
            lease_epoch=compiling.lease_epoch,
            now=NOW,
        )
