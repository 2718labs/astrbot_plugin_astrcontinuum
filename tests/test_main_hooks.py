from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

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


class RecordingCompiler:
    def __init__(self) -> None:
        self.calls = 0

    async def compile(self, _request: object) -> object:
        self.calls += 1
        raise AssertionError("main hook must not invoke the compiler")


class RecordingWorker:
    instances: ClassVar[list[RecordingWorker]] = []
    fail_construction = False
    fail_run_once = False

    def __init__(self, **kwargs: object) -> None:
        if self.__class__.fail_construction:
            raise RuntimeError("worker construction secret")
        self.kwargs = kwargs
        self.run_once_calls = 0
        self.__class__.instances.append(self)

    async def run_once(self) -> None:
        self.run_once_calls += 1
        if self.__class__.fail_run_once:
            raise RuntimeError("worker run secret")

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.fail_construction = False
        cls.fail_run_once = False


class RecordingScheduler:
    instances: ClassVar[list[RecordingScheduler]] = []
    fail_start = False
    fail_notify = False
    fail_close = False

    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.start_calls = 0
        self.notify_calls: list[str] = []
        self.close_calls = 0
        self.__class__.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.fail_start = False
        cls.fail_notify = False
        cls.fail_close = False

    async def start(self) -> None:
        self.start_calls += 1
        if self.__class__.fail_start:
            raise RuntimeError("start secret")

    async def notify(self, session_key_hash: str) -> None:
        self.notify_calls.append(session_key_hash)
        if self.__class__.fail_notify:
            raise RuntimeError("notify secret")

    async def close(self) -> None:
        self.close_calls += 1
        if self.__class__.fail_close:
            raise RuntimeError("close secret")


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


def install_compaction_fakes(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingWorker.reset()
    RecordingScheduler.reset()
    monkeypatch.setattr(module, "CompactionWorker", RecordingWorker, raising=False)
    monkeypatch.setattr(module, "CoalescingCompactionScheduler", RecordingScheduler, raising=False)


async def finalize_intent(module: ModuleType, plugin: object) -> tuple[FakeEvent, object]:
    event = FakeEvent()
    await plugin.on_llm_request(event, fake_request())
    state = plugin._state(event)
    assert state is not None and state.prepared is not None
    await plugin.on_agent_done_finalize(
        event,
        SimpleNamespace(messages=[]),
        SimpleNamespace(completion_text="assistant completion"),
    )
    return event, state


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
        "Ayleovelle",
        "Non-blocking infinite context runtime for AstrBot",
        "0.3.0",
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
    assert results == [("plain", "AstrContinuum is ready; background compaction unavailable.")]
    assert event.plain_results == ["AstrContinuum is ready; background compaction unavailable."]

    await plugin.terminate()
    await plugin.terminate()
    assert plugin._bridge is None
    not_ready_event = FakeEvent()
    assert [item async for item in plugin.context_status(not_ready_event)] == [
        ("plain", "AstrContinuum is not ready.")
    ]

    await plugin.initialize()
    assert plugin._bridge is not None


@pytest.mark.asyncio
async def test_default_lifecycle_persists_intent_without_constructing_compaction_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(object(), {"enabled": True})

    await plugin.initialize()
    _event, state = await finalize_intent(module, plugin)

    assert plugin._worker is None
    assert plugin._scheduler is None
    bridge = plugin._bridge
    assert bridge is not None
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM compaction_jobs").fetchone()[0] == 1
    assert state.intent_raised is True
    assert [item async for item in plugin.context_status(FakeEvent())] == [
        ("plain", "AstrContinuum is ready; background compaction unavailable.")
    ]


@pytest.mark.asyncio
async def test_explicit_backend_starts_once_notifies_first_intent_and_never_compiles_in_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    install_compaction_fakes(module, monkeypatch)
    backend = RecordingCompiler()
    plugin = module.AstrContinuumPlugin(
        object(),
        {"enabled": True},
        compiler_backend=backend,
    )

    await plugin.initialize()
    await plugin.initialize()
    event, state = await finalize_intent(module, plugin)
    await plugin.on_agent_done_finalize(
        event,
        SimpleNamespace(messages=[]),
        SimpleNamespace(completion_text="ignored"),
    )

    assert len(RecordingWorker.instances) == 1
    assert len(RecordingScheduler.instances) == 1
    scheduler = RecordingScheduler.instances[0]
    assert scheduler.start_calls == 1
    assert scheduler.notify_calls == [state.prepared.turn.session_key.session_key_hash]
    assert backend.calls == 0
    assert RecordingWorker.instances[0].run_once_calls == 0
    assert [item async for item in plugin.context_status(FakeEvent())] == [
        ("plain", "AstrContinuum is ready; background compaction active.")
    ]


@pytest.mark.asyncio
async def test_scheduler_start_and_notify_fail_open_without_losing_durable_intent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, logger = load_main(monkeypatch, tmp_path)
    install_compaction_fakes(module, monkeypatch)
    RecordingScheduler.fail_start = True
    plugin = module.AstrContinuumPlugin(object(), {"enabled": True}, compiler_backend=RecordingCompiler())

    await plugin.initialize()
    _event, state = await finalize_intent(module, plugin)

    assert plugin._bridge is not None
    assert plugin._worker is None
    assert plugin._scheduler is None
    assert state.intent_raised is True
    assert any("COMPACTION_SCHEDULER_START_FAILED" in str(args) for _, args in logger.records)

    module, logger = load_main(monkeypatch, tmp_path / "notify")
    install_compaction_fakes(module, monkeypatch)
    RecordingScheduler.fail_notify = True
    plugin = module.AstrContinuumPlugin(object(), {"enabled": True}, compiler_backend=RecordingCompiler())
    await plugin.initialize()
    _event, state = await finalize_intent(module, plugin)

    assert state.intent_raised is True
    bridge = plugin._bridge
    assert bridge is not None
    with bridge.repository.factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM compaction_jobs").fetchone()[0] == 1
    assert any("COMPACTION_SCHEDULER_NOTIFY_FAILED" in str(args) for _, args in logger.records)


@pytest.mark.asyncio
async def test_worker_setup_and_background_callback_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, logger = load_main(monkeypatch, tmp_path)
    install_compaction_fakes(module, monkeypatch)
    RecordingWorker.fail_construction = True
    plugin = module.AstrContinuumPlugin(
        object(),
        {"enabled": True},
        compiler_backend=RecordingCompiler(),
    )

    await plugin.initialize()

    assert plugin._bridge is not None
    assert plugin._worker is None
    assert plugin._scheduler is None
    assert [item async for item in plugin.context_status(FakeEvent())] == [
        ("plain", "AstrContinuum is ready; background compaction unavailable.")
    ]
    assert any("COMPACTION_SCHEDULER_START_FAILED" in str(args) for _, args in logger.records)

    module, logger = load_main(monkeypatch, tmp_path / "callback")
    install_compaction_fakes(module, monkeypatch)
    RecordingWorker.fail_run_once = True
    plugin = module.AstrContinuumPlugin(
        object(),
        {"enabled": True},
        compiler_backend=RecordingCompiler(),
    )
    await plugin.initialize()

    scheduler = plugin._scheduler
    assert scheduler is not None
    await scheduler.callback("session-key-hash")

    assert any("COMPACTION_WORKER_RUN_FAILED" in str(args) for _, args in logger.records)


@pytest.mark.asyncio
async def test_old_scheduler_callback_never_targets_worker_from_reinitialized_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    install_compaction_fakes(module, monkeypatch)
    plugin = module.AstrContinuumPlugin(
        object(),
        {"enabled": True},
        compiler_backend=RecordingCompiler(),
    )

    await plugin.initialize()
    old_scheduler = plugin._scheduler
    old_worker = plugin._worker
    assert old_scheduler is not None and old_worker is not None
    await plugin.terminate()
    await plugin.initialize()
    new_worker = plugin._worker
    assert new_worker is not None and new_worker is not old_worker

    await old_scheduler.callback("old-session-key-hash")

    assert old_worker.run_once_calls == 1
    assert new_worker.run_once_calls == 0


@pytest.mark.asyncio
async def test_terminate_detaches_scheduler_fail_open_and_reinitializes_fresh_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, logger = load_main(monkeypatch, tmp_path)
    install_compaction_fakes(module, monkeypatch)
    RecordingScheduler.fail_close = True
    plugin = module.AstrContinuumPlugin(object(), {"enabled": True}, compiler_backend=RecordingCompiler())

    await plugin.initialize()
    first_scheduler = plugin._scheduler
    await plugin.terminate()
    await plugin.terminate()

    assert first_scheduler is not None
    assert first_scheduler.close_calls == 1
    assert plugin._bridge is None
    assert plugin._worker is None
    assert plugin._scheduler is None
    assert plugin._capability is None
    assert any("COMPACTION_SCHEDULER_CLOSE_FAILED" in str(args) for _, args in logger.records)

    RecordingScheduler.fail_close = False
    await plugin.initialize()
    assert plugin._scheduler is not None
    assert plugin._scheduler is not first_scheduler
