from __future__ import annotations

import asyncio
import importlib
import json
import shutil
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
    PermissionType = SimpleNamespace(ADMIN="ADMIN")

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

    @staticmethod
    def permission_type(permission: object):
        def decorate(function):
            function.__astrbot_permission__ = permission
            return function

        return decorate


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
        self.unified_msg_origin = "umo:platform-1:session-1"

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


def fake_request(
    prompt: str = "current input",
    *,
    token_usage: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt=prompt,
        contexts=[FakeMessage(role="assistant", content="native context")],
        conversation=SimpleNamespace(
            cid="conversation-1",
            persona_id=None,
            token_usage=token_usage,
        ),
    )


class FakeContext:
    def __init__(self) -> None:
        self.provider_requests: list[str] = []
        self.generate_calls: list[dict[str, object]] = []

    async def get_current_chat_provider_id(self, *, umo: str) -> str:
        self.provider_requests.append(umo)
        return "conversation-provider"

    async def llm_generate(self, **kwargs: object) -> SimpleNamespace:
        self.generate_calls.append(kwargs)
        prompt = kwargs["prompt"]
        assert isinstance(prompt, str)
        payload = json.loads(prompt)
        events = payload["events"]
        response = {
            "acknowledged_event_ids": [item["event_id"] for item in events],
            "goals": [
                {
                    "event_id": item["event_id"],
                    "quote": item["content"],
                    "confidence": 1.0,
                }
                for item in events
                if item["content"]
            ],
            "constraints": [],
            "decisions": [],
            "progress": [],
            "open_loops": [],
            "preferences": [],
            "entities": [],
            "emotional_context": [],
            "anchors": [],
        }
        return SimpleNamespace(completion_text=json.dumps(response, ensure_ascii=False))


def load_main(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
    *,
    projection_capability: bool = True,
    module_name: str = "main",
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
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    imported = importlib.import_module(module_name)
    return imported, logger


def test_main_imports_from_real_astrbot_plugin_package_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BlockTopLevelAstrContinuum:
        def find_spec(
            self,
            fullname: str,
            _path: object = None,
            _target: object = None,
        ) -> None:
            if fullname == "astrcontinuum" or fullname.startswith("astrcontinuum."):
                raise ModuleNotFoundError(
                    "top-level astrcontinuum is unavailable in an AstrBot plugin install"
                )

    project_root = Path(__file__).resolve().parents[1]
    plugin_root = tmp_path / "data" / "plugins" / "astrbot_plugin_astrcontinuum"
    plugin_root.mkdir(parents=True)
    shutil.copy2(project_root / "main.py", plugin_root / "main.py")
    shutil.copytree(project_root / "astrcontinuum", plugin_root / "astrcontinuum")

    isolated_sys_path = [
        entry for entry in sys.path if Path(entry or ".").resolve() != project_root
    ]
    monkeypatch.setattr(sys, "path", [str(tmp_path), *isolated_sys_path])
    monkeypatch.setattr(
        sys,
        "meta_path",
        [BlockTopLevelAstrContinuum(), *sys.meta_path],
    )
    for name in tuple(sys.modules):
        if name == "astrcontinuum" or name.startswith("astrcontinuum."):
            monkeypatch.delitem(sys.modules, name)

    module, _logger = load_main(
        monkeypatch,
        tmp_path / "plugin-data",
        module_name="data.plugins.astrbot_plugin_astrcontinuum.main",
    )

    assert module.AstrContinuumPlugin.__module__ == (
        "data.plugins.astrbot_plugin_astrcontinuum.main"
    )


def test_main_declares_one_star_and_exact_hook_priorities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin_type = module.AstrContinuumPlugin

    assert issubclass(plugin_type, FakeStar)
    assert plugin_type.__register_args__ == (
        "astrbot_plugin_astrcontinuum",
        "2718labs",
        "Non-blocking infinite context runtime for AstrBot",
        "0.1.0",
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
    first_worker = plugin._worker
    await plugin.initialize()
    assert plugin._bridge is first_bridge
    assert plugin._worker is first_worker
    assert first_worker is not None
    assert first_worker.task is not None
    assert first_bridge is not None
    assert first_bridge.repository.factory.database_path.exists()

    event = FakeEvent()
    await plugin.on_llm_response(event, SimpleNamespace(completion_text="must not persist"))
    with first_bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 0

    results = [item async for item in plugin.context_status(event)]
    expected_status = (
        "AstrContinuum：运行中\n"
        "后台归约：运行中\n"
        "已记录事件：0\n"
        "已发布 Checkpoint：0\n"
        "待处理任务：0"
    )
    assert results == [("plain", expected_status)]
    assert event.plain_results == [expected_status]
    assert plugin.context_status.__func__.__astrbot_permission__ == "ADMIN"

    await plugin.terminate()
    await plugin.terminate()
    assert plugin._bridge is None
    assert plugin._worker is None
    assert first_worker.task is None
    await plugin.initialize()
    assert plugin._bridge is not None
    assert plugin._worker is not None
    await plugin.terminate()


@pytest.mark.parametrize(
    ("trusted_usage", "expected_projection", "expected_jobs"),
    [
        (10_000, False, 0),
        (52_000, False, 1),
        (60_000, True, 1),
    ],
)
@pytest.mark.asyncio
async def test_pressure_controls_projection_and_durable_intent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    trusted_usage: int,
    expected_projection: bool,
    expected_jobs: int,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        object(),
        {
            "enabled": True,
            "model_context_limit": 100_000,
            "compaction_start_ratio": 0.75,
            "provider_view_switch_ratio": 0.80,
        },
    )
    await plugin.initialize()

    event = FakeEvent(message_id=f"message-{trusted_usage}")
    request = fake_request(token_usage=trusted_usage)
    original_conversation = request.conversation
    await plugin.on_llm_request(event, request)

    system = FakeMessage(role="system", content="system")
    history = FakeMessage(role="assistant", content="native history")
    current = FakeMessage(role="user", content="current input")
    messages = [system, history, current]
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    await plugin.on_agent_begin_project(event, run_context)
    state = plugin._state(event)
    assert state is not None
    assert state.pressure is not None
    assert state.pressure.should_project is expected_projection

    if expected_projection:
        assert messages == [system, current]
        assert request.conversation is not original_conversation
        assert request.conversation.token_usage == 0
        assert original_conversation.token_usage == trusted_usage
    else:
        assert messages == [system, history, current]
        assert request.conversation is original_conversation

    assistant = FakeMessage(role="assistant", content="assistant response")
    messages.append(assistant)
    await plugin.on_agent_done_restore(
        event,
        run_context,
        SimpleNamespace(completion_text="assistant response"),
    )
    await plugin.on_agent_done_finalize(
        event,
        run_context,
        SimpleNamespace(completion_text="assistant response"),
    )

    assert request.conversation is original_conversation
    assert original_conversation.token_usage == trusted_usage
    assert messages == [system, history, current, assistant]
    bridge = plugin._bridge
    assert bridge is not None
    with bridge.repository.factory.connection(read_only=True) as connection:
        job_count = connection.execute("SELECT count(*) FROM compaction_jobs").fetchone()[0]
    assert job_count == expected_jobs
    await plugin.terminate()


@pytest.mark.asyncio
async def test_soft_pressure_wakes_worker_and_publishes_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    context = FakeContext()
    plugin = module.AstrContinuumPlugin(
        context,
        {
            "enabled": True,
            "model_context_limit": 100_000,
            "compaction_start_ratio": 0.75,
            "provider_view_switch_ratio": 0.80,
            "worker_poll_interval_seconds": 0.01,
        },
    )
    await plugin.initialize()

    event = FakeEvent(message_id="message-worker-e2e")
    request = fake_request("保留精确原文", token_usage=52_000)
    await plugin.on_llm_request(event, request)
    system = FakeMessage(role="system", content="system")
    history = FakeMessage(role="assistant", content="native history")
    current = FakeMessage(role="user", content="保留精确原文")
    assistant = FakeMessage(role="assistant", content="已完成")
    messages = [system, history, current]
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    await plugin.on_agent_begin_project(event, run_context)
    assert messages == [system, history, current]
    messages.append(assistant)
    await plugin.on_agent_done_restore(
        event,
        run_context,
        SimpleNamespace(completion_text="已完成"),
    )
    await plugin.on_agent_done_finalize(
        event,
        run_context,
        SimpleNamespace(completion_text="已完成"),
    )

    bridge = plugin._bridge
    assert bridge is not None
    for _ in range(100):
        view = bridge.repository.read_request_view(plugin._state(event).prepared.turn.session_key)
        if view.snapshot is not None:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("background worker did not publish a checkpoint")

    assert view.covered_event_end == 2
    assert view.high_water_mark == 2
    assert view.delta == ()
    assert context.provider_requests == ["umo:platform-1:session-1"]
    assert context.generate_calls
    assert context.generate_calls[0]["chat_provider_id"] == "conversation-provider"
    assert context.generate_calls[0].get("tools") is None
    assert messages == [system, history, current, assistant]
    status_event = FakeEvent(message_id="status")
    status = [item async for item in plugin.context_status(status_event)]
    assert "已记录事件：2" in status[0][1]
    assert "已发布 Checkpoint：1" in status[0][1]
    assert "待处理任务：0" in status[0][1]
    await plugin.terminate()
