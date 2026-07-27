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
        self.text = text
        self._no_save = False

    def mark_as_temp(self) -> FakeTextPart:
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
    *,
    projection_capability: bool = True,
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

    core = ModuleType("astrbot.core")
    core.__path__ = []
    agent = ModuleType("astrbot.core.agent")
    agent.__path__ = []
    message = ModuleType("astrbot.core.agent.message")
    message.Message = FakeMessage
    message.TextPart = FakeTextPart if projection_capability else object

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.agent": agent,
        "astrbot.core.agent.message": message,
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
    live_assembly_count = 0
    original_read_request_view = astrbot_adapter.read_request_view
    original_assemble_live_context = astrbot_adapter.assemble_live_context

    def counted_read_request_view(*args: object, **kwargs: object) -> object:
        nonlocal read_count
        read_count += 1
        return original_read_request_view(*args, **kwargs)  # type: ignore[arg-type]

    def counted_assemble_live_context(
        *args: object,
        **kwargs: object,
    ) -> tuple[object, object]:
        nonlocal live_assembly_count
        live_assembly_count += 1
        return original_assemble_live_context(*args, **kwargs)  # type: ignore[arg-type, return-value]

    monkeypatch.setattr(
        astrbot_adapter,
        "read_request_view",
        counted_read_request_view,
    )
    monkeypatch.setattr(
        astrbot_adapter,
        "assemble_live_context",
        counted_assemble_live_context,
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

    system = FakeMessage(role="system", content="system prompt")
    history_user = FakeMessage(role="user", content="old user")
    history_assistant = FakeMessage(role="assistant", content="old assistant")
    current = FakeMessage(role="user", content="fresh user")
    messages = [system, history_user, history_assistant, current]
    native_before = _dump_messages(messages)
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    opaque = FakeMessage(role="user", content=[FakeTextPart(text="foreign projection")])
    messages.insert(1, opaque)
    await plugin.on_agent_begin_project(event, run_context)

    assert live_assembly_count == 1
    assert read_count == reads_before_projection
    assert request.contexts is request_contexts
    assert _dump_messages(request.contexts) == request_context_bytes
    assert messages[0] is system
    assert messages[1] is opaque
    assert messages[-1] is current
    assert history_user not in messages
    assert history_assistant not in messages
    projection_message = messages[-2]
    assert projection_message._no_save is True
    assert projection_message.content[0]._no_save is True
    projected_text = projection_message.content[0].text

    assistant_delta = FakeMessage(role="assistant", content="fresh assistant")
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

    messages.remove(opaque)
    await plugin.on_agent_done_finalize(
        event,
        run_context,
        SimpleNamespace(completion_text="fresh assistant"),
    )
    assert _dump_messages(messages[:-1]) == native_before
    assert messages[-1] is assistant_delta
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


@pytest.mark.asyncio
async def test_missing_projection_capability_fails_open_after_user_capture(
    monkeypatch: pytest.MonkeyPatch,
    plugin_cleanup: list[TerminablePlugin],
    tmp_path: Path,
) -> None:
    module, _logger = _load_main(
        monkeypatch,
        tmp_path,
        projection_capability=False,
    )
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
    await plugin.on_llm_request(event, request)
    state = next(iter(event._extras.values()))
    messages = [
        FakeMessage(role="system", content="system"),
        FakeMessage(role="user", content="old user"),
        FakeMessage(role="assistant", content="old assistant"),
        FakeMessage(role="user", content="fresh user"),
    ]
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    await plugin.on_agent_begin_project(event, run_context)

    assert messages == [messages[0], messages[-1]]
    assert state.projected is not None
    assert state.faults[-1].code == "PROJECTION_API_UNAVAILABLE"
    bridge = plugin._bridge
    assert bridge is not None
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 3
