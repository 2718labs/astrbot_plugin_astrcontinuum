from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

import astrcontinuum as ac
from astrcontinuum.storage import (
    ArtifactKind,
    CanonicalMetricObservation,
    TokenMetric,
    TokenMetricConflict,
    TokenMetricStore,
)
from tests.storage.security_testkit import (
    activate_test_storage,
    secure_repository,
    storage_test_codec,
)

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
CANONICAL_PROFILE_ID = "canonical-o200k-v1"


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
    activation = activate_test_storage(factory)
    return repository_type()(
        factory,
        codec=activation.codec,
        fault_injector=fault_injector,
    )


def canonical_observation(
    token_count: int | None = 17,
) -> Any:
    return CanonicalMetricObservation(
        tokenizer_profile_id=CANONICAL_PROFILE_ID,
        token_count=token_count,
    )


def capture_user(
    repository: Any,
    *,
    event_id: str = "event-1",
    key: ac.SessionKey | None = None,
    content: str = "hello",
    idempotency_key: str = "request-1",
    canonical_count: int | None = 17,
    created_at: datetime = NOW,
) -> ac.EventEnvelope:
    return repository.capture_user_event(
        event_id=event_id,
        session_key=key or session_key(),
        content=content,
        idempotency_key=idempotency_key,
        token_count=2,
        canonical=canonical_observation(canonical_count),
        created_at=created_at,
    )


def test_capture_commits_event_and_canonical_metric_atomically(tmp_path: Path) -> None:
    repository = migrated_repository(tmp_path)
    canonical = canonical_observation()

    event = repository.capture_user_event(
        event_id="event-metric",
        session_key=session_key(),
        content="hello",
        idempotency_key="request-metric",
        token_count=5,
        canonical=canonical,
        created_at=NOW,
    )

    assert event.token_count == 5
    with repository.factory.connection(read_only=True) as connection:
        metric = TokenMetricStore(storage_test_codec()).get_in_transaction(
            connection,
            artifact_kind=ArtifactKind.EVENT,
            artifact_id=event.event_id,
            tokenizer_profile_id=canonical.tokenizer_profile_id,
        )
        assert metric == TokenMetric(
            artifact_kind=ArtifactKind.EVENT,
            artifact_id=event.event_id,
            tokenizer_profile_id=canonical.tokenizer_profile_id,
            token_count=17,
        )
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM token_metrics").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM token_metric_backfill_intents").fetchone()[0]
            == 0
        )


def test_missing_canonical_count_commits_event_and_one_backfill_intent(
    tmp_path: Path,
) -> None:
    repository = migrated_repository(tmp_path)

    event = capture_user(repository, event_id="event-missing", canonical_count=None)

    assert event.token_count == 2
    with repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM token_metrics").fetchone()[0] == 0
        intent = connection.execute(
            """
            SELECT session_key_hash, artifact_kind, artifact_id,
                   tokenizer_profile_id, created_at
            FROM token_metric_backfill_intents
            """
        ).fetchone()
        assert tuple(intent) == (
            event.session_key.session_key_hash,
            "EVENT",
            event.event_id,
            CANONICAL_PROFILE_ID,
            "2026-07-26T12:00:00.000000Z",
        )


def test_exact_replay_verifies_or_fills_the_same_logical_metric(tmp_path: Path) -> None:
    repository = migrated_repository(tmp_path)

    first = capture_user(repository, canonical_count=None)
    filled = capture_user(repository, canonical_count=17)
    duplicate = capture_user(repository, canonical_count=17)

    assert filled == first
    assert duplicate == first
    with repository.factory.connection(read_only=True) as connection:
        metric = TokenMetricStore(storage_test_codec()).get_in_transaction(
            connection,
            artifact_kind=ArtifactKind.EVENT,
            artifact_id=first.event_id,
            tokenizer_profile_id=CANONICAL_PROFILE_ID,
        )
        assert metric is not None
        assert metric.token_count == 17
        assert connection.execute("SELECT count(*) FROM token_metrics").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM token_metric_backfill_intents").fetchone()[0]
            == 0
        )


def test_replay_with_different_canonical_value_raises_stable_conflict(
    tmp_path: Path,
) -> None:
    repository = migrated_repository(tmp_path)
    capture_user(repository, canonical_count=17)

    with pytest.raises(TokenMetricConflict) as caught:
        capture_user(repository, canonical_count=18)

    assert caught.value.code == "TOKEN_METRIC_CONFLICT"
    with repository.factory.connection(read_only=True) as connection:
        metric = TokenMetricStore(storage_test_codec()).get_in_transaction(
            connection,
            artifact_kind=ArtifactKind.EVENT,
            artifact_id="event-1",
            tokenizer_profile_id=CANONICAL_PROFILE_ID,
        )
        assert metric is not None
        assert metric.token_count == 17
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 1


def test_replay_without_count_does_not_create_intent_for_existing_metric(
    tmp_path: Path,
) -> None:
    repository = migrated_repository(tmp_path)
    first = capture_user(repository, canonical_count=17)

    duplicate = capture_user(repository, canonical_count=None)

    assert duplicate == first
    with repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM token_metrics").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM token_metric_backfill_intents").fetchone()[0]
            == 0
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
        canonical=canonical_observation(11),
        created_at=NOW,
    )
    tool_call = repository.capture_tool_event(
        event_id="event-3",
        session_key=key,
        event_type=ac.EventType.TOOL_CALL,
        content='{"name":"status"}',
        idempotency_key="tool-call-1",
        token_count=3,
        canonical=canonical_observation(12),
        created_at=NOW,
    )
    tool_result = repository.capture_tool_event(
        event_id="event-4",
        session_key=key,
        event_type=ac.EventType.TOOL_RESULT,
        content='{"ok":true}',
        idempotency_key="tool-result-1",
        token_count=2,
        canonical=canonical_observation(13),
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
        repository = secure_repository(ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000))
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
    codec = storage_test_codec()
    record_key = expected.session_key_hash
    encrypted_identities = {
        column: (
            None if value is None else codec.encrypt_text("sessions", column, record_key, value)
        )
        for column, value in (
            ("platform_instance_id", other.platform_instance_id),
            ("message_type", other.message_type),
            ("session_id", other.session_id),
            ("group_id", other.group_id),
            ("user_id", other.user_id),
            ("conversation_id", other.conversation_id),
            ("persona_id", other.persona_id),
        )
    }

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
                record_key,
                codec.encrypt_object_json(
                    "sessions",
                    "canonical_session_key_json",
                    record_key,
                    other.canonical_json(),
                ),
                encrypted_identities["platform_instance_id"],
                encrypted_identities["message_type"],
                encrypted_identities["session_id"],
                encrypted_identities["group_id"],
                encrypted_identities["user_id"],
                encrypted_identities["conversation_id"],
                encrypted_identities["persona_id"],
                "2026-07-26T12:00:00.000000Z",
                "2026-07-26T12:00:00.000000Z",
            ),
        )

    invariant_type = getattr(ac, "RepositoryInvariantError", None)
    assert invariant_type is not None, "RepositoryInvariantError export is missing"
    with pytest.raises(invariant_type):
        capture_user(repository, key=expected)

    with repository.factory.connection(read_only=True) as connection:
        row = connection.execute(
            "SELECT canonical_session_key_json, next_event_sequence FROM sessions"
        ).fetchone()
        canonical = codec.decrypt_object_json(
            "sessions",
            "canonical_session_key_json",
            record_key,
            row[0],
        )
        assert json.loads(canonical) == other.model_dump(mode="json")
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
            canonical=canonical_observation(),
            created_at=NOW,
        )
    with pytest.raises(ValueError):
        capture_user(repository, created_at=NOW.replace(tzinfo=None))

    with repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0
