from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def session_key(**overrides: object) -> ac.SessionKey:
    values: dict[str, object] = {
        "platform_instance_id": "astrbot-local",
        "message_type": "friend_message",
        "session_id": "session-1",
        "group_id": None,
        "user_id": "user-1",
        "conversation_id": "conversation-1",
        "persona_id": None,
    }
    values.update(overrides)
    return ac.SessionKey(**values)


def repository_type() -> type[Any]:
    value = getattr(ac, "SQLiteRepository", None)
    assert value is not None, "SQLiteRepository export is missing"
    return value


def migrated_repository(
    data_dir: Path,
    *,
    fault_injector: Any | None = None,
) -> Any:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    ac.SQLiteMigrator(factory).migrate()
    return repository_type()(factory, fault_injector=fault_injector)


def capture_user(
    repository: Any,
    *,
    event_id: str = "event-1",
    key: ac.SessionKey | None = None,
    content: str = "hello",
    idempotency_key: str = "request-1",
    created_at: datetime = NOW,
) -> ac.EventEnvelope:
    return repository.capture_user_event(
        event_id=event_id,
        session_key=key or session_key(),
        content=content,
        idempotency_key=idempotency_key,
        token_count=2,
        created_at=created_at,
    )


def test_capture_transactions_round_trip_all_authoritative_mappings(tmp_path: Path) -> None:
    repository = migrated_repository(tmp_path)
    key = session_key()

    user = capture_user(repository, key=key)
    assistant = repository.capture_assistant_event(
        event_id="event-2",
        session_key=key,
        content="hi",
        idempotency_key="done-1",
        token_count=1,
        created_at=NOW,
    )
    tool_call = repository.capture_tool_event(
        event_id="event-3",
        session_key=key,
        event_type=ac.EventType.TOOL_CALL,
        content='{"name":"status"}',
        idempotency_key="tool-call-1",
        token_count=3,
        created_at=NOW,
    )
    tool_result = repository.capture_tool_event(
        event_id="event-4",
        session_key=key,
        event_type=ac.EventType.TOOL_RESULT,
        content='{"ok":true}',
        idempotency_key="tool-result-1",
        token_count=2,
        created_at=NOW,
    )

    assert [item.sequence for item in (user, assistant, tool_call, tool_result)] == [
        1,
        2,
        3,
        4,
    ]
    assert user.session_key == key
    assert (user.event_type, user.role, user.source_hook) == (
        ac.EventType.USER_MESSAGE,
        ac.EventRole.USER,
        ac.SourceHook.ON_LLM_REQUEST,
    )
    assert (assistant.event_type, assistant.role, assistant.source_hook) == (
        ac.EventType.ASSISTANT_MESSAGE,
        ac.EventRole.ASSISTANT,
        ac.SourceHook.ON_AGENT_DONE,
    )
    assert (tool_call.event_type, tool_call.role, tool_call.source_hook) == (
        ac.EventType.TOOL_CALL,
        ac.EventRole.TOOL,
        ac.SourceHook.ON_USING_LLM_TOOL,
    )
    assert (tool_result.event_type, tool_result.role, tool_result.source_hook) == (
        ac.EventType.TOOL_RESULT,
        ac.EventRole.TOOL,
        ac.SourceHook.ON_LLM_TOOL_RESPOND,
    )


def test_exact_duplicate_is_idempotent_and_conflicting_replay_is_rejected(
    tmp_path: Path,
) -> None:
    repository = migrated_repository(tmp_path)

    first = capture_user(repository)
    duplicate = capture_user(repository)

    assert duplicate == first
    conflict_type = getattr(ac, "IdempotencyConflict", None)
    assert conflict_type is not None, "IdempotencyConflict export is missing"
    with pytest.raises(conflict_type):
        capture_user(repository, event_id="event-other")
    with pytest.raises(conflict_type):
        capture_user(repository, content="changed")

    with repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT next_event_sequence FROM sessions").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 1


def test_capture_failure_rolls_back_counter_and_event(tmp_path: Path) -> None:
    armed = True

    def failpoint(name: str) -> None:
        nonlocal armed
        if armed and name == "capture.before_insert":
            armed = False
            raise RuntimeError("injected capture crash")

    repository = migrated_repository(tmp_path, fault_injector=failpoint)

    with pytest.raises(RuntimeError, match="injected capture crash"):
        capture_user(repository)

    event = capture_user(repository)
    assert event.sequence == 1
    with repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT next_event_sequence FROM sessions").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 1


def test_concurrent_connections_allocate_one_contiguous_sequence(tmp_path: Path) -> None:
    migrated_repository(tmp_path)
    count = 12
    barrier = Barrier(count)

    def capture(index: int) -> ac.EventEnvelope:
        repository = repository_type()(ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000))
        barrier.wait()
        return capture_user(
            repository,
            event_id=f"event-{index}",
            content=f"message {index}",
            idempotency_key=f"request-{index}",
        )

    with ThreadPoolExecutor(max_workers=count) as pool:
        events = list(pool.map(capture, range(count)))

    assert sorted(item.sequence for item in events) == list(range(1, count + 1))
    with ac.SQLiteConnectionFactory(tmp_path).connection(read_only=True) as connection:
        rows = connection.execute(
            "SELECT sequence FROM journal_events ORDER BY sequence"
        ).fetchall()
        assert [row[0] for row in rows] == list(range(1, count + 1))


def test_session_hash_collision_or_corruption_is_not_merged(tmp_path: Path) -> None:
    repository = migrated_repository(tmp_path)
    expected = session_key()
    other = session_key(session_id="other-session")

    with repository.factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO sessions (
                session_key_hash,
                canonical_session_key_json,
                platform_instance_id,
                message_type,
                session_id,
                group_id,
                user_id,
                conversation_id,
                persona_id,
                next_event_sequence,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                expected.session_key_hash,
                other.canonical_json(),
                other.platform_instance_id,
                other.message_type,
                other.session_id,
                other.group_id,
                other.user_id,
                other.conversation_id,
                other.persona_id,
                "2026-07-26T12:00:00.000000Z",
                "2026-07-26T12:00:00.000000Z",
            ),
        )

    conflict_type = getattr(ac, "SessionIdentityConflict", None)
    assert conflict_type is not None, "SessionIdentityConflict export is missing"
    with pytest.raises(conflict_type):
        capture_user(repository, key=expected)

    with repository.factory.connection(read_only=True) as connection:
        row = connection.execute(
            "SELECT canonical_session_key_json, next_event_sequence FROM sessions"
        ).fetchone()
        assert json.loads(row[0]) == other.model_dump(mode="json")
        assert row[1] == 1
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0


def test_capture_rejects_invalid_tool_type_and_naive_time_without_writes(
    tmp_path: Path,
) -> None:
    repository = migrated_repository(tmp_path)

    with pytest.raises(ValueError):
        repository.capture_tool_event(
            event_id="event-1",
            session_key=session_key(),
            event_type=ac.EventType.USER_MESSAGE,
            content="invalid",
            idempotency_key="invalid-tool",
            token_count=1,
            created_at=NOW,
        )
    with pytest.raises(ValueError):
        capture_user(repository, created_at=NOW.replace(tzinfo=None))

    with repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0
