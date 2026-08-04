from __future__ import annotations

import importlib
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Protocol

import pytest
import pytest_asyncio

import astrcontinuum.adapters.astrbot as astrbot_adapter
from astrcontinuum.storage import SecureCodec

TEST_MASTER_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
TEST_CODEC = SecureCodec(bytes(range(32)))


class TerminablePlugin(Protocol):
    async def terminate(self) -> None: ...


@pytest_asyncio.fixture
async def plugin_cleanup() -> AsyncIterator[list[TerminablePlugin]]:
    plugins: list[TerminablePlugin] = []
    yield plugins
    for plugin in reversed(plugins):
        await plugin.terminate()


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, tuple[object, ...]]] = []

    def __getattr__(self, level: str):
        def record(*args: object, **_kwargs: object) -> None:
            self.records.append((level, args))

        return record


class FakeFilter:
    PermissionType = SimpleNamespace(ADMIN="ADMIN")

    @staticmethod
    def _decorator(**_kwargs: object):
        return lambda function: function

    on_llm_request = _decorator
    on_agent_begin = _decorator
    on_agent_done = _decorator
    on_llm_response = _decorator
    on_using_llm_tool = _decorator
    on_llm_tool_respond = _decorator

    @staticmethod
    def command(_name: str, **_kwargs: object):
        return lambda function: function

    @staticmethod
    def permission_type(_permission: object):
        return lambda function: function


class FakeStar:
    def __init__(self, context: object, config: dict[str, object] | None = None) -> None:
        self.context = context
        self.config = config or {}


class FakeTextPart:
    def __init__(self, *, text: str) -> None:
        self.type = "text"
        self.text = text
        self._no_save = False

    def mark_as_temp(self) -> FakeTextPart:
        self._no_save = True
        return self


class FakeThinkingPart:
    def __init__(
        self,
        *,
        text: str,
        signature: object = None,
        encrypted_content: object = None,
    ) -> None:
        self.type = "thinking"
        self.text = text
        self.signature = signature
        self.encrypted_content = encrypted_content
        self._no_save = False

    def mark_as_temp(self) -> FakeThinkingPart:
        self._no_save = True
        return self


class FakeMessage:
    def __init__(self, *, role: str, content: object) -> None:
        self.role = role
        self.content = content
        self._no_save = False


class FakeEvent:
    def __init__(self, message_id: str) -> None:
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            timestamp=1_727_000_000,
            type=SimpleNamespace(value="FriendMessage"),
            session_id="session-1",
        )
        self._extras: dict[str, object] = {}

    def get_platform_id(self) -> str:
        return "platform-1"

    def get_message_type(self) -> SimpleNamespace:
        return SimpleNamespace(value="FriendMessage")

    def get_session_id(self) -> str:
        return "session-1"

    def get_group_id(self) -> str:
        return ""

    def get_sender_id(self) -> str:
        return "user-1"

    def set_extra(self, key: str, value: object) -> None:
        self._extras[key] = value

    def get_extra(self, key: str, default: object = None) -> object:
        return self._extras.get(key, default)

    def plain_result(self, text: str) -> tuple[str, str]:
        return ("plain", text)


def _request(prompt: str, *, token_usage: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        prompt=prompt,
        contexts=[FakeMessage(role="assistant", content="immutable request contexts")],
        conversation=SimpleNamespace(
            cid="conversation-1",
            persona_id=None,
            token_usage=token_usage,
        ),
    )


def _load_main(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
) -> tuple[ModuleType, FakeLogger]:
    monkeypatch.setenv("ASTRCONTINUUM_MASTER_KEY", TEST_MASTER_KEY)
    monkeypatch.delenv("ASTRCONTINUUM_PREVIOUS_KEY", raising=False)
    logger = FakeLogger()
    astrbot = ModuleType("astrbot")
    astrbot.__path__ = []
    api = ModuleType("astrbot.api")
    api.__path__ = []
    api.logger = logger
    event = ModuleType("astrbot.api.event")
    event.__path__ = []
    event.AstrMessageEvent = FakeEvent
    event.filter = FakeFilter()
    star = ModuleType("astrbot.api.star")
    star.__path__ = []

    class FakeStarTools:
        @classmethod
        def get_data_dir(cls, _plugin_name: str | None = None) -> Path:
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir

    star.Context = object
    star.Star = FakeStar
    star.StarTools = FakeStarTools
    star.register = lambda *_args, **_kwargs: lambda plugin_type: plugin_type

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "main", raising=False)
    return importlib.import_module("main"), logger


def _dump_messages(messages: list[object]) -> bytes:
    def content_value(content: object) -> object:
        if isinstance(content, list):
            return [
                {"text": getattr(part, "text", None), "no_save": part._no_save} for part in content
            ]
        return content

    return json.dumps(
        [
            {
                "role": message.role,
                "content": content_value(message.content),
                "no_save": message._no_save,
            }
            for message in messages
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_projection_restores_native_history_and_never_persists_projection(
    monkeypatch: pytest.MonkeyPatch,
    plugin_cleanup: list[TerminablePlugin],
    tmp_path: Path,
) -> None:
    read_count = 0
    request_budget_count = 0
    original_read_request_view = astrbot_adapter.read_request_view
    original_run_request_budget = astrbot_adapter.run_request_budget

    def counted_read_request_view(*args: object, **kwargs: object) -> object:
        nonlocal read_count
        read_count += 1
        return original_read_request_view(*args, **kwargs)  # type: ignore[arg-type]

    def counted_run_request_budget(
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal request_budget_count
        request_budget_count += 1
        return original_run_request_budget(*args, **kwargs)  # type: ignore[arg-type, return-value]

    monkeypatch.setattr(
        astrbot_adapter,
        "read_request_view",
        counted_read_request_view,
    )
    monkeypatch.setattr(
        astrbot_adapter,
        "run_request_budget",
        counted_run_request_budget,
    )
    module, _logger = _load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        object(),
        {
            "enabled": True,
            "model_context_limit": 100_000,
            "compaction_start_ratio": 0.75,
            "provider_view_switch_ratio": 0.80,
        },
    )
    plugin_cleanup.append(plugin)
    await plugin.initialize()

    prior_event = FakeEvent("message-prior")
    prior_request = _request("old user")
    await plugin.on_llm_request(prior_event, prior_request)
    prior_state = next(iter(prior_event._extras.values()))
    await plugin._bridge.capture_assistant(prior_state.prepared, "old assistant")

    event = FakeEvent("message-current")
    request = _request("fresh user", token_usage=60_000)
    request_contexts = request.contexts
    request_context_bytes = _dump_messages(request.contexts)
    await plugin.on_llm_request(event, request)
    reads_before_projection = read_count

    system_part = FakeTextPart(text="system prompt")
    system_content = [system_part]
    system = FakeMessage(role="system", content=system_content)
    history_user = FakeMessage(role="user", content="old user")
    assistant_signature = object()
    assistant_encrypted_content = object()
    assistant_thinking_part = FakeThinkingPart(
        text="old assistant reasoning",
        signature=assistant_signature,
        encrypted_content=assistant_encrypted_content,
    )
    assistant_content = [assistant_thinking_part]
    history_assistant = FakeMessage(role="assistant", content=assistant_content)
    current_signature = object()
    current_encrypted_content = object()
    current_thinking_part = FakeThinkingPart(
        text="current signed reasoning",
        signature=current_signature,
        encrypted_content=current_encrypted_content,
    )
    current_text_part = FakeTextPart(text="fresh user")
    current_content = [current_thinking_part, current_text_part]
    current = FakeMessage(role="user", content=current_content)
    messages = [system, history_user, history_assistant, current]
    message_list = messages
    native_before = _dump_messages(messages)
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    opaque_part = FakeTextPart(text="foreign projection")
    opaque_content = [opaque_part]
    opaque = FakeMessage(role="user", content=opaque_content)
    messages.insert(1, opaque)
    await plugin.on_agent_begin_project(event, run_context)

    assert request_budget_count == 1
    assert read_count == reads_before_projection
    assert request.contexts is request_contexts
    assert _dump_messages(request.contexts) == request_context_bytes
    assert messages is message_list
    assert messages[0] is system
    assert messages[1] is opaque
    assert messages[-1] is current
    assert system.content is system_content
    assert system_content[0] is system_part
    assert opaque.content is opaque_content
    assert opaque_content[0] is opaque_part
    assert current.content is current_content
    assert current_content[0] is current_thinking_part
    assert current_content[1] is current_text_part
    assert current_thinking_part.signature is current_signature
    assert current_thinking_part.encrypted_content is current_encrypted_content
    assert history_user not in messages
    assert history_assistant not in messages
    projection_message = messages[-2]
    assert projection_message._no_save is True
    assert len(projection_message.content) == 1
    assert isinstance(projection_message.content[0], FakeTextPart)
    assert projection_message.content[0].type == "text"
    assert not hasattr(projection_message.content[0], "signature")
    assert not hasattr(projection_message.content[0], "thinking")
    assert not hasattr(projection_message.content[0], "think")
    assert not hasattr(projection_message.content[0], "encrypted_content")
    assert projection_message.content[0]._no_save is True
    projected_text = projection_message.content[0].text

    delta_signature = object()
    delta_encrypted_content = object()
    delta_thinking_part = FakeThinkingPart(
        text="fresh assistant reasoning",
        signature=delta_signature,
        encrypted_content=delta_encrypted_content,
    )
    delta_text_part = FakeTextPart(text="fresh assistant")
    delta_content = [delta_thinking_part, delta_text_part]
    assistant_delta = FakeMessage(role="assistant", content=delta_content)
    messages.append(assistant_delta)
    await plugin.on_agent_done_restore(
        event,
        run_context,
        SimpleNamespace(completion_text="fresh assistant"),
    )
    assert messages == [
        system,
        opaque,
        history_user,
        history_assistant,
        current,
        assistant_delta,
    ]
    assert messages is message_list
    assert messages[0] is system
    assert messages[1] is opaque
    assert messages[2] is history_user
    assert messages[3] is history_assistant
    assert messages[4] is current
    assert messages[5] is assistant_delta
    assert system.content is system_content
    assert system_content[0] is system_part
    assert opaque.content is opaque_content
    assert opaque_content[0] is opaque_part
    assert history_assistant.content is assistant_content
    assert assistant_content[0] is assistant_thinking_part
    assert assistant_thinking_part.signature is assistant_signature
    assert assistant_thinking_part.encrypted_content is assistant_encrypted_content
    assert current.content is current_content
    assert current_content[0] is current_thinking_part
    assert current_content[1] is current_text_part
    assert current_thinking_part.signature is current_signature
    assert current_thinking_part.encrypted_content is current_encrypted_content
    assert assistant_delta.content is delta_content
    assert delta_content[0] is delta_thinking_part
    assert delta_content[1] is delta_text_part
    assert delta_thinking_part.signature is delta_signature
    assert delta_thinking_part.encrypted_content is delta_encrypted_content

    messages.remove(opaque)
    await plugin.on_agent_done_finalize(
        event,
        run_context,
        SimpleNamespace(completion_text="fresh assistant"),
    )
    assert _dump_messages(messages[:-1]) == native_before
    assert messages[-1] is assistant_delta
    assert assistant_delta.content is delta_content
    assert delta_content[0] is delta_thinking_part
    assert delta_content[1] is delta_text_part
    assert delta_thinking_part.signature is delta_signature
    assert delta_thinking_part.encrypted_content is delta_encrypted_content
    assert projected_text not in _dump_messages(messages).decode("utf-8")

    bridge = plugin._bridge
    assert bridge is not None
    with bridge.repository.factory.connection(read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT event_id, sequence, event_type, content
            FROM journal_events
            ORDER BY sequence
            """
        ).fetchall()
        decoded_rows = [
            (
                row["sequence"],
                row["event_type"],
                TEST_CODEC.decrypt_text(
                    "journal_events",
                    "content",
                    row["event_id"],
                    row["content"],
                ),
            )
            for row in rows
        ]
        assert decoded_rows == [
            (1, "USER_MESSAGE", "old user"),
            (2, "ASSISTANT_MESSAGE", "old assistant"),
            (3, "USER_MESSAGE", "fresh user"),
            (4, "ASSISTANT_MESSAGE", "fresh assistant"),
        ]
        assert all(row["content"] not in {projected_text, "old user", "fresh user"} for row in rows)
        before_response = len(rows)

    await plugin.on_llm_response(
        event,
        SimpleNamespace(completion_text="observed only"),
    )
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert (
            connection.execute("SELECT count(*) FROM journal_events").fetchone()[0]
            == before_response
        )


@pytest.mark.parametrize(
    ("part_type", "expected_code"),
    [
        ("text", "PROJECTION_BUILD_FAILED"),
        ("thinking", "PROJECTION_API_UNAVAILABLE"),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_hook_projection_surface_fails_open_after_user_capture(
    monkeypatch: pytest.MonkeyPatch,
    plugin_cleanup: list[TerminablePlugin],
    tmp_path: Path,
    part_type: str,
    expected_code: str,
) -> None:
    module, _logger = _load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        object(),
        {
            "enabled": True,
            "model_context_limit": 100_000,
            "compaction_start_ratio": 0.75,
            "provider_view_switch_ratio": 0.80,
        },
    )
    plugin_cleanup.append(plugin)
    await plugin.initialize()

    prior_event = FakeEvent("message-prior")
    await plugin.on_llm_request(prior_event, _request("old user"))
    prior_state = next(iter(prior_event._extras.values()))
    await plugin._bridge.capture_assistant(prior_state.prepared, "old assistant")

    event = FakeEvent("message-current")
    request = _request("fresh user", token_usage=60_000)
    original_conversation = request.conversation
    await plugin.on_llm_request(event, request)
    state = next(iter(event._extras.values()))
    signature = object()

    class UnsupportedTextPart:
        def __init__(self, *, text: str) -> None:
            self.type = part_type
            self.text = text
            self.signature = signature

    system = FakeMessage(role="system", content="system")
    history_user = FakeMessage(role="user", content="old user")
    history_assistant = FakeMessage(role="assistant", content="old assistant")
    current_part = UnsupportedTextPart(text="fresh user")
    current_content = [current_part]
    current = FakeMessage(role="user", content=current_content)
    messages = [system, history_user, history_assistant, current]
    message_list = messages
    original_objects = tuple(messages)
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    await plugin.on_agent_begin_project(event, run_context)

    assert messages is message_list
    assert tuple(messages) == original_objects
    assert all(actual is expected for actual, expected in zip(messages, original_objects))
    assert current.content is current_content
    assert current_content[0] is current_part
    assert current_part.signature is signature
    assert request.conversation is original_conversation
    assert state.projected is None
    assert state.faults[-1].code == expected_code
    bridge = plugin._bridge
    assert bridge is not None
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 3
