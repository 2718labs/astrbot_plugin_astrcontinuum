from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, tuple[object, ...]]] = []

    def debug(self, *args: object, **_kwargs: object) -> None:
        self.records.append(("debug", args))

    def info(self, *args: object, **_kwargs: object) -> None:
        self.records.append(("info", args))

    def warning(self, *args: object, **_kwargs: object) -> None:
        self.records.append(("warning", args))

    def error(self, *args: object, **_kwargs: object) -> None:
        self.records.append(("error", args))


class FakeFilter:
    @staticmethod
    def _hook(kind: str, **kwargs: object):
        def decorate(function):
            function.__astrbot_hook__ = kind
            function.__astrbot_priority__ = kwargs.get("priority", 0)
            return function

        return decorate

    def on_llm_request(self, **kwargs: object):
        return self._hook("on_llm_request", **kwargs)

    def on_agent_begin(self, **kwargs: object):
        return self._hook("on_agent_begin", **kwargs)

    def on_agent_done(self, **kwargs: object):
        return self._hook("on_agent_done", **kwargs)

    def on_llm_response(self, **kwargs: object):
        return self._hook("on_llm_response", **kwargs)

    def on_using_llm_tool(self, **kwargs: object):
        return self._hook("on_using_llm_tool", **kwargs)

    def on_llm_tool_respond(self, **kwargs: object):
        return self._hook("on_llm_tool_respond", **kwargs)

    def command(self, name: str, **kwargs: object):
        return self._hook(f"command:{name}", **kwargs)


class FakeStar:
    def __init__(self, context: object, config: dict[str, Any] | None = None) -> None:
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
    def __init__(self, *, message_id: str = "message-1") -> None:
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            timestamp=1_727_000_000,
        )
        self._extras: dict[str, object] = {}
        self.plain_results: list[str] = []

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
        self.plain_results.append(text)
        return ("plain", text)


def fake_request(prompt: str = "current input") -> SimpleNamespace:
    return SimpleNamespace(
        prompt=prompt,
        contexts=[FakeMessage(role="assistant", content="native context")],
        conversation=SimpleNamespace(cid="conversation-1", persona_id=None),
    )


def load_main(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
    *,
    projection_capability: bool = True,
) -> tuple[ModuleType, FakeLogger]:
    logger = FakeLogger()
    fake_filter = FakeFilter()

    astrbot = ModuleType("astrbot")
    astrbot.__path__ = []
    api = ModuleType("astrbot.api")
    api.__path__ = []
    api.logger = logger
    event = ModuleType("astrbot.api.event")
    event.__path__ = []
    event.AstrMessageEvent = FakeEvent
    event.filter = fake_filter
    star = ModuleType("astrbot.api.star")
    star.__path__ = []

    class FakeStarTools:
        @classmethod
        def get_data_dir(cls, _plugin_name: str | None = None) -> Path:
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir

    def register(*args: object, **kwargs: object):
        def decorate(plugin_class):
            plugin_class.__register_args__ = args
            plugin_class.__register_kwargs__ = kwargs
            return plugin_class

        return decorate

    star.Context = object
    star.Star = FakeStar
    star.StarTools = FakeStarTools
    star.register = register

    core = ModuleType("astrbot.core")
    core.__path__ = []
    agent = ModuleType("astrbot.core.agent")
    agent.__path__ = []
    message = ModuleType("astrbot.core.agent.message")
    message.Message = FakeMessage
    if projection_capability:
        message.TextPart = FakeTextPart
    else:
        message.TextPart = object

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.agent": agent,
        "astrbot.core.agent.message": message,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "main", raising=False)
    imported = importlib.import_module("main")
    return imported, logger


def test_main_declares_one_star_and_exact_hook_priorities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin_type = module.AstrContinuumPlugin

    assert issubclass(plugin_type, FakeStar)
    assert plugin_type.__register_args__ == (
        "astrbot_plugin_astrcontinuum",
        "Ayleovelle",
        "Non-blocking infinite context runtime for AstrBot",
        "0.1.0-alpha",
    )
    assert plugin_type.on_llm_request.__astrbot_priority__ == 2000
    assert plugin_type.on_agent_begin_guard.__astrbot_priority__ == 2000
    assert plugin_type.on_agent_begin_project.__astrbot_priority__ == -100
    assert plugin_type.on_agent_done_restore.__astrbot_priority__ == 2000
    assert plugin_type.on_agent_done_finalize.__astrbot_priority__ == 900
    assert plugin_type.on_llm_response.__astrbot_hook__ == "on_llm_response"
    assert "__del__" not in plugin_type.__dict__
    star_types = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, FakeStar) and value is not FakeStar
    ]
    assert star_types == [plugin_type]


@pytest.mark.asyncio
async def test_lifecycle_command_and_llm_response_are_idempotent_and_observational(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(object(), {"enabled": True})

    await plugin.initialize()
    first_bridge = plugin._bridge
    await plugin.initialize()
    assert plugin._bridge is first_bridge
    assert first_bridge is not None
    assert first_bridge.repository.factory.database_path.exists()

    event = FakeEvent()
    await plugin.on_llm_response(event, SimpleNamespace(completion_text="must not persist"))
    with first_bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0

    results = [item async for item in plugin.context_status(event)]
    assert results == [("plain", "AstrContinuum is ready.")]
    assert event.plain_results == ["AstrContinuum is ready."]

    await plugin.terminate()
    await plugin.terminate()
    assert plugin._bridge is None
    await plugin.initialize()
    assert plugin._bridge is not None
