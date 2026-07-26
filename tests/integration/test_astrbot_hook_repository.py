from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrcontinuum.adapters import (
    AdapterErrorCode,
    AstrBotAdapterError,
    AstrBotHookBridge,
)
from astrcontinuum.storage import SQLiteConnectionFactory, SQLiteMigrator, SQLiteRepository


class FakeMessageType(str, Enum):
    FRIEND = "FriendMessage"


class FakeEvent:
    def __init__(
        self,
        *,
        message_id: str = "message-42",
        sender_id: str = "user-5",
    ) -> None:
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            timestamp=1_727_000_000,
        )
        self._sender_id = sender_id

    def get_platform_id(self) -> str:
        return "platform-instance-1"

    def get_message_type(self) -> FakeMessageType:
        return FakeMessageType.FRIEND

    def get_session_id(self) -> str:
        return "session-9"

    def get_group_id(self) -> str:
        return ""

    def get_sender_id(self) -> str:
        return self._sender_id


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        prompt="hello from the user",
        contexts=[{"role": "assistant", "content": "native history"}],
        conversation=SimpleNamespace(cid="conversation-3", persona_id=None),
    )


def _bridge(tmp_path: Path) -> AstrBotHookBridge:
    factory = SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    SQLiteMigrator(factory).migrate()
    return AstrBotHookBridge(SQLiteRepository(factory))


@pytest.mark.asyncio
async def test_authoritative_hooks_are_idempotent_and_allocate_contiguous_rows(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path)
    event = FakeEvent()
    request = _request()
    contexts = request.contexts
    contexts_before = tuple(contexts)

    first_prepare = await bridge.prepare_request(event, request)
    replay_prepare = await bridge.prepare_request(event, request)
    tool = SimpleNamespace(name="weather")
    first_call = await bridge.capture_tool_call(
        first_prepare,
        tool,
        {"city": "杭州"},
        ordinal=0,
    )
    replay_call = await bridge.capture_tool_call(
        replay_prepare,
        tool,
        {"city": "杭州"},
        ordinal=0,
    )
    first_result = await bridge.capture_tool_result(
        first_prepare,
        tool,
        {"city": "杭州"},
        {"temperature": 28},
        ordinal=0,
    )
    replay_result = await bridge.capture_tool_result(
        replay_prepare,
        tool,
        {"city": "杭州"},
        {"temperature": 28},
        ordinal=0,
    )
    first_assistant = await bridge.capture_assistant(first_prepare, "done")
    replay_assistant = await bridge.capture_assistant(replay_prepare, "done")
    first_job = await bridge.raise_compaction_intent(
        first_prepare,
        target_high_water_mark=first_assistant.sequence,
    )
    replay_job = await bridge.raise_compaction_intent(
        replay_prepare,
        target_high_water_mark=replay_assistant.sequence,
    )

    assert first_prepare.user_event == replay_prepare.user_event
    assert first_call == replay_call
    assert first_result == replay_result
    assert first_assistant == replay_assistant
    assert first_job == replay_job
    assert request.contexts is contexts
    assert tuple(request.contexts) == contexts_before
    assert first_prepare.view.high_water_mark == 1
    assert [item.event_id for item in first_prepare.view.delta] == [
        first_prepare.user_event.event_id
    ]
    assert first_prepare.candidates == ()

    with bridge.repository.factory.connection(read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT sequence, event_type, role, source_hook, content
            FROM journal_events
            ORDER BY sequence
            """
        ).fetchall()
        assert [tuple(row)[:4] for row in rows] == [
            (1, "USER_MESSAGE", "USER", "ON_LLM_REQUEST"),
            (2, "TOOL_CALL", "TOOL", "ON_USING_LLM_TOOL"),
            (3, "TOOL_RESULT", "TOOL", "ON_LLM_TOOL_RESPOND"),
            (4, "ASSISTANT_MESSAGE", "ASSISTANT", "ON_AGENT_DONE"),
        ]
        assert rows[0]["content"] == "hello from the user"
        assert json.loads(rows[1]["content"])["kind"] == "call"
        assert json.loads(rows[2]["content"])["kind"] == "result"
        assert rows[3]["content"] == "done"
        assert connection.execute("SELECT next_event_sequence FROM sessions").fetchone()[0] == 5
        job_rows = connection.execute(
            """
            SELECT state, target_high_water_mark, intent_target_high_water_mark
            FROM compaction_jobs
            """
        ).fetchall()
        assert [tuple(row) for row in job_rows] == [("PENDING", 4, 4)]


@pytest.mark.asyncio
async def test_missing_stable_identity_skips_every_durable_write(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    request = _request()
    contexts_before = tuple(request.contexts)

    with pytest.raises(AstrBotAdapterError) as caught:
        await bridge.prepare_request(FakeEvent(sender_id=""), request)

    assert caught.value.code == AdapterErrorCode.HOST_IDENTITY_MISSING.value
    assert tuple(request.contexts) == contexts_before
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM compaction_jobs").fetchone()[0] == 0
