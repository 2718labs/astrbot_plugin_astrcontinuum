from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

import pytest

from astrcontinuum.adapters import (
    AdapterErrorCode,
    AdapterStage,
    AstrBotAdapterError,
    build_projection_objects,
    canonical_tool_metadata,
    deterministic_event_identity,
    estimate_opaque_token_cost,
    extract_host_turn_identity,
    probe_projection_capability,
)
from astrcontinuum.domain import SourceHook


class FakeMessageType(str, Enum):
    FRIEND = "FriendMessage"


class FakeEvent:
    def __init__(
        self,
        *,
        message_id: str = "message-42",
        timestamp: int = 1_727_000_000,
        sender_id: str = "user-5",
    ) -> None:
        self.message_obj = SimpleNamespace(message_id=message_id, timestamp=timestamp)
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


def _request(*, persona_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        prompt="current user input",
        conversation=SimpleNamespace(cid="conversation-3", persona_id=persona_id),
    )


def test_extract_host_turn_identity_uses_exact_seven_field_session_key() -> None:
    request = _request()

    identity = extract_host_turn_identity(FakeEvent(), request)

    assert identity.request_identity == id(request)
    assert identity.host_message_id == "message-42"
    assert identity.created_at == datetime.fromtimestamp(1_727_000_000, UTC)
    assert identity.session_key.model_dump() == {
        "platform_instance_id": "platform-instance-1",
        "message_type": "FriendMessage",
        "session_id": "session-9",
        "group_id": None,
        "user_id": "user-5",
        "conversation_id": "conversation-3",
        "persona_id": None,
    }


def test_missing_stable_host_identity_fails_with_content_free_error() -> None:
    secret = "DO-NOT-LEAK-user-name-or-message"
    event = FakeEvent(sender_id="")
    event.message_obj.secret = secret

    with pytest.raises(AstrBotAdapterError) as caught:
        extract_host_turn_identity(event, _request(persona_id="persona-2"))

    error = caught.value
    assert error.code == AdapterErrorCode.HOST_IDENTITY_MISSING.value
    assert error.stage == AdapterStage.IDENTITY.value
    assert error.missing_field_count == 1
    assert secret not in str(error)
    assert secret not in repr(error)


def test_event_identity_is_deterministic_and_separates_hook_ordinal_and_metadata() -> None:
    turn = extract_host_turn_identity(FakeEvent(), _request())

    first = deterministic_event_identity(turn, SourceHook.ON_LLM_REQUEST)
    replay = deterministic_event_identity(turn, SourceHook.ON_LLM_REQUEST)
    assistant = deterministic_event_identity(turn, SourceHook.ON_AGENT_DONE)
    tool_zero = deterministic_event_identity(
        turn,
        SourceHook.ON_USING_LLM_TOOL,
        ordinal=0,
        canonical_metadata='{"tool":"weather"}',
    )
    tool_one = deterministic_event_identity(
        turn,
        SourceHook.ON_USING_LLM_TOOL,
        ordinal=1,
        canonical_metadata='{"tool":"weather"}',
    )

    assert first == replay
    assert first != assistant
    assert tool_zero != tool_one
    assert first.idempotency_key.startswith("ac1:")
    assert first.event_id.startswith("evt:")
    assert len(first.idempotency_key) == len("ac1:") + 64
    assert len(first.event_id) == len("evt:") + 64


def test_tool_metadata_is_canonical_result_aware_and_bounded() -> None:
    tool = SimpleNamespace(name="weather")
    left = canonical_tool_metadata(
        tool,
        {"city": "杭州", "options": {"units": "metric", "days": 3}},
    )
    right = canonical_tool_metadata(
        tool,
        {"options": {"days": 3, "units": "metric"}, "city": "杭州"},
    )
    result = canonical_tool_metadata(
        tool,
        {"city": "杭州", "options": {"units": "metric", "days": 3}},
        tool_result={"temperature": 28},
    )
    bounded = canonical_tool_metadata(
        tool,
        {"payload": "x" * 20_000},
        max_utf8_bytes=256,
    )

    assert left == right
    assert left != result
    assert json.loads(left)["kind"] == "call"
    assert json.loads(result)["kind"] == "result"
    assert len(bounded.encode("utf-8")) <= 256
    assert json.loads(bounded)["truncated"] is True


def test_verified_projection_capability_marks_part_and_message_provider_only() -> None:
    class FakeTextPart:
        def __init__(self, *, text: str) -> None:
            self.text = text
            self._no_save = False

        def mark_as_temp(self) -> FakeTextPart:
            self._no_save = True
            return self

    class FakeMessage:
        def __init__(self, *, role: str, content: list[object]) -> None:
            self.role = role
            self.content = content
            self._no_save = False

    module = SimpleNamespace(Message=FakeMessage, TextPart=FakeTextPart)
    capability = probe_projection_capability(lambda _name: module)
    built = build_projection_objects("projected context", capability)

    assert capability.available is True
    assert built.fault is None
    assert len(built.objects) == 1
    message = built.objects[0]
    assert isinstance(message, FakeMessage)
    assert message.role == "user"
    assert message._no_save is True
    assert len(message.content) == 1
    assert isinstance(message.content[0], FakeTextPart)
    assert message.content[0].text == "projected context"
    assert message.content[0]._no_save is True


def test_missing_or_broken_projection_capability_fails_open_without_content() -> None:
    def missing_import(_name: str) -> object:
        raise ImportError("secret host path")

    missing = probe_projection_capability(missing_import)
    missing_build = build_projection_objects("private projection text", missing)

    assert missing.available is False
    assert missing.fault is not None
    assert missing.fault.code == AdapterErrorCode.PROJECTION_IMPORT_UNAVAILABLE.value
    assert missing_build.objects == ()
    assert missing_build.fault == missing.fault
    assert "private projection text" not in repr(missing_build)

    class BrokenTextPart:
        def mark_as_temp(self) -> BrokenTextPart:
            return self

    broken_module = SimpleNamespace(Message=object, TextPart=BrokenTextPart)
    broken = probe_projection_capability(lambda _name: broken_module)
    broken_build = build_projection_objects("still private", broken)

    assert broken_build.objects == ()
    assert broken_build.fault is not None
    assert broken_build.fault.code == AdapterErrorCode.PROJECTION_BUILD_FAILED.value
    assert "still private" not in repr(broken_build)


def test_opaque_token_cost_is_ephemeral_and_uses_only_the_supplied_counter() -> None:
    class ByteCounter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def count_text(self, text: str) -> int:
            self.calls.append(text)
            return len(text.encode("utf-8"))

    first = SimpleNamespace(
        role="system",
        content=[SimpleNamespace(text="你好")],
    )
    second = SimpleNamespace(role="user", content="abc")
    counter = ByteCounter()

    cost = estimate_opaque_token_cost((first, second), counter)

    assert cost == 9
    assert counter.calls == ["你好", "abc"]
