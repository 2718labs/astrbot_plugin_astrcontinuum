from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

TEST_MASTER_KEY = base64.urlsafe_b64encode(b"\x01" * 32).decode("ascii").rstrip("=")
TEST_MASTER_KEY_ID = hashlib.sha256(b"\x01" * 32).hexdigest()[:16]


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
        self.type = "text"
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


def fake_user_message(text: str) -> FakeMessage:
    return FakeMessage(role="user", content=[FakeTextPart(text=text)])


class FakeEvent:
    def __init__(
        self,
        *,
        message_id: str = "message-1",
        session_id: str = "session-1",
    ) -> None:
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            timestamp=1_727_000_000,
            type=SimpleNamespace(value="FriendMessage"),
            session_id=session_id,
        )
        self._extras: dict[str, object] = {}
        self.plain_results: list[str] = []
        self.unified_msg_origin = f"umo:platform-1:{session_id}"

    def get_platform_id(self) -> str:
        return "platform-1"

    def get_message_type(self) -> SimpleNamespace:
        return SimpleNamespace(value="FriendMessage")

    def get_session_id(self) -> str:
        return self.message_obj.session_id

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
    model: object = None,
    conversation_id: str = "conversation-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt=prompt,
        model=model,
        contexts=[FakeMessage(role="assistant", content="native context")],
        conversation=SimpleNamespace(
            cid=conversation_id,
            persona_id=None,
            token_usage=token_usage,
        ),
    )


def fake_context_trace(
    *,
    mode: object,
    outcome: object,
    error_code: object = None,
    candidate_count: object = 8,
    selected_count: object = 4,
    relation_count: object = 6,
    constraint_count: object = 2,
    retained_count: object = 7,
    reduced_count: object = 1,
    selected_budget_units: object = 1_234,
    required_passed: object = True,
    provenance_passed: object = True,
    residual_band: object = "VERIFIED",
    adaptive_retry_count: object = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        mode=mode,
        outcome=outcome,
        error_code=error_code,
        candidate_count=candidate_count,
        selected_count=selected_count,
        relation_count=relation_count,
        constraint_count=constraint_count,
        retained_count=retained_count,
        reduced_count=reduced_count,
        selected_budget_units=selected_budget_units,
        required_passed=required_passed,
        provenance_passed=provenance_passed,
        residual_band=residual_band,
        adaptive_retry_count=adaptive_retry_count,
    )


class FakeContext:
    def __init__(self, *, provider: object = None) -> None:
        self.provider = provider
        self.provider_requests: list[str] = []
        self.provider_by_id_requests: list[str] = []
        self.using_provider_requests: list[str] = []
        self.generate_calls: list[dict[str, object]] = []
        self.conversation_manager = FakeConversationManager()

    def get_using_provider(self, *, umo: str) -> object:
        self.using_provider_requests.append(umo)
        return self.provider

    async def get_current_chat_provider_id(self, *, umo: str) -> str:
        self.provider_requests.append(umo)
        return "conversation-provider"

    def get_provider_by_id(self, provider_id: str) -> object:
        self.provider_by_id_requests.append(provider_id)
        return self.provider

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


class FakeConversationManager:
    def __init__(self) -> None:
        self.current_id: str | None = "conversation-1"
        self.conversation: object | None = SimpleNamespace(
            cid="conversation-1",
            persona_id=None,
        )
        self.current_id_calls: list[str] = []
        self.conversation_calls: list[tuple[str, str]] = []

    async def get_curr_conversation_id(self, umo: str) -> str | None:
        self.current_id_calls.append(umo)
        return self.current_id

    async def get_conversation(self, umo: str, cid: str) -> object | None:
        self.conversation_calls.append((umo, cid))
        return self.conversation


def load_main(
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Path,
    *,
    projection_capability: bool = True,
    module_name: str = "main",
    master_key: str | None = TEST_MASTER_KEY,
) -> tuple[ModuleType, FakeLogger]:
    if master_key is None:
        monkeypatch.delenv("ASTRCONTINUUM_MASTER_KEY", raising=False)
    else:
        monkeypatch.setenv("ASTRCONTINUUM_MASTER_KEY", master_key)
    monkeypatch.delenv("ASTRCONTINUUM_PREVIOUS_KEY", raising=False)
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
        "Ayleovelle",
        "Non-blocking infinite context runtime for AstrBot",
        "0.2.0",
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


def test_key_source_status_labels_use_simple_chinese(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path, master_key=None)

    assert module.AstrContinuumPlugin._key_source_label(module.KeySource.LOCAL) == "自动管理"
    assert module.AstrContinuumPlugin._key_source_label(module.KeySource.FILE) == "服务器密钥文件"
    assert module.AstrContinuumPlugin._key_source_label(module.KeySource.ENVIRONMENT) == "环境变量"


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
        "数据保护：ACTIVE\n"
        "加密格式：AES-256-GCM / envelope-v1\n"
        "密钥来源：环境变量\n"
        f"活动密钥标识：{TEST_MASTER_KEY_ID}\n"
        "存储维护：ACTIVE\n"
        "安全代码：NONE\n"
        "上下文引擎：ACTIVE\n"
        "最近引擎状态：NONE\n"
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
    current = fake_user_message("current input")
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
async def test_initialize_injects_dedicated_canonical_o200k_counter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})

    await plugin.initialize()

    worker = plugin._worker
    assert worker is not None
    assert worker._canonical_profile_id == module.CANONICAL_O200K.profile_id
    assert not hasattr(plugin, "_counter")
    assert worker._canonical_counter is not worker._compatibility_counter
    assert worker._canonical_counter.profile == module.CANONICAL_O200K
    await plugin.terminate()


@pytest.mark.asyncio
async def test_explicit_compaction_provider_resolves_through_public_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = SimpleNamespace(
        get_model=lambda: "MiniMax-Text-01",
        provider_config={"max_context_tokens": 262_144},
    )
    context = FakeContext(provider=provider)
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        context,
        {
            "enabled": True,
            "compaction_provider_id": "private-provider-id",
            "model_context_limit": 0,
        },
    )

    await plugin.initialize()

    assert context.provider_by_id_requests == ["private-provider-id"]
    providers = plugin._providers
    assert providers is not None
    arbitrary_session = module.SessionKey(
        platform_instance_id="platform",
        message_type="friend_message",
        session_id="session",
        group_id=None,
        user_id="user",
        conversation_id="conversation",
        persona_id=None,
    )
    resolved = providers.resolve(arbitrary_session)
    assert resolved.context_limit == 130_000
    assert resolved.context_limit_source.value == "AUTO_ASTRBOT"
    assert "private-provider-id" not in repr(resolved)
    assert "MiniMax-Text-01" not in repr(resolved)
    await plugin.terminate()


@pytest.mark.asyncio
async def test_unavailable_explicit_provider_cannot_fall_back_to_live_session_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = FakeContext(provider=None)
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        context,
        {
            "enabled": True,
            "compaction_provider_id": "missing-private-provider",
        },
    )
    await plugin.initialize()
    providers = plugin._providers
    assert providers is not None
    session = module.SessionKey(
        platform_instance_id="platform",
        message_type="friend_message",
        session_id="session",
        group_id=None,
        user_id="user",
        conversation_id="conversation",
        persona_id=None,
    )
    providers.remember(
        session,
        module.CompactionProviderBinding(
            provider_id="live-provider",
            model_identity="gpt-4o",
            context_limit=1,
            context_limit_source=plugin._context_limit_resolver.resolve(200_000).source,
        ),
    )

    with pytest.raises(RuntimeError) as caught:
        providers.resolve(session)

    assert getattr(caught.value, "code", None) == "EXTRACTIVE_PROVIDER_UNAVAILABLE"
    assert context.provider_by_id_requests == ["missing-private-provider"]
    assert context.generate_calls == []
    await plugin.terminate()


@pytest.mark.asyncio
async def test_live_request_only_refreshes_binding_without_running_compaction_lane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = SimpleNamespace(
        get_model=lambda: "gpt-4o",
        provider_config={"max_context_tokens": 262_144},
    )
    context = FakeContext(provider=provider)
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        context,
        {"enabled": True, "model_context_limit": 0},
    )
    await plugin.initialize()
    worker = plugin._worker
    assert worker is not None

    async def forbidden_lane(*_args: object, **_kwargs: object) -> None:
        pytest.fail("live request awaited the background compaction lane")

    monkeypatch.setattr(worker, "_backfill_metrics", forbidden_lane)
    event = FakeEvent(message_id="binding-only")

    await plugin.on_llm_request(event, fake_request(model="gpt-4o"))

    assert context.generate_calls == []
    state = plugin._state(event)
    assert state is not None and state.prepared is not None
    providers = plugin._providers
    assert providers is not None
    resolved = providers.resolve(state.prepared.turn.session_key)
    assert resolved.model_identity == "gpt-4o"
    assert resolved.context_limit == 130_000
    await plugin.terminate()


@pytest.mark.asyncio
async def test_small_model_window_marks_live_provider_unavailable_before_compaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = SimpleNamespace(
        get_model=lambda: "gpt-4o-mini",
        provider_config={"max_context_tokens": 20_000},
    )
    context = FakeContext(provider=provider)
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        context,
        {"enabled": True, "model_context_limit": 0},
    )
    await plugin.initialize()
    event = FakeEvent(message_id="small-window")

    await plugin.on_llm_request(event, fake_request(model="gpt-4o-mini"))

    state = plugin._state(event)
    assert state is not None and state.prepared is not None
    providers = plugin._providers
    assert providers is not None
    with pytest.raises(RuntimeError) as caught:
        providers.resolve(state.prepared.turn.session_key)
    assert getattr(caught.value, "code", None) == "EXTRACTIVE_PROVIDER_UNAVAILABLE"
    assert context.generate_calls == []
    await plugin.terminate()


@pytest.mark.asyncio
async def test_failed_live_refresh_clears_stale_binding_and_later_success_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = SimpleNamespace(
        get_model=lambda: "gpt-4o",
        provider_config={"max_context_tokens": 262_144},
    )

    class MutableProviderContext(FakeContext):
        current_provider: str | BaseException = "provider-one"

        async def get_current_chat_provider_id(self, *, umo: str) -> str:
            self.provider_requests.append(umo)
            if isinstance(self.current_provider, BaseException):
                raise self.current_provider
            return self.current_provider

    context = MutableProviderContext(provider=provider)
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        context,
        {"enabled": True, "model_context_limit": 0},
    )
    await plugin.initialize()
    first = FakeEvent(message_id="refresh-one")
    await plugin.on_llm_request(first, fake_request(model="gpt-4o"))
    first_state = plugin._state(first)
    assert first_state is not None and first_state.prepared is not None
    session = first_state.prepared.turn.session_key
    providers = plugin._providers
    assert providers is not None
    assert providers.resolve(session).provider_id == "provider-one"

    original_metadata_resolver = module.resolve_astrbot_request_metadata

    def fail_metadata(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("private metadata failure")

    monkeypatch.setattr(module, "resolve_astrbot_request_metadata", fail_metadata)
    second = FakeEvent(message_id="refresh-two")
    await plugin.on_llm_request(second, fake_request(model="gpt-4o"))
    with pytest.raises(RuntimeError) as metadata_failure:
        providers.resolve(session)
    assert getattr(metadata_failure.value, "code", None) == "EXTRACTIVE_PROVIDER_UNAVAILABLE"

    monkeypatch.setattr(module, "resolve_astrbot_request_metadata", original_metadata_resolver)
    context.current_provider = RuntimeError("private provider id failure")
    third = FakeEvent(message_id="refresh-three")
    await plugin.on_llm_request(third, fake_request(model="gpt-4o"))
    with pytest.raises(RuntimeError) as provider_failure:
        providers.resolve(session)
    assert getattr(provider_failure.value, "code", None) == "EXTRACTIVE_PROVIDER_UNAVAILABLE"

    context.current_provider = "provider-two"
    fourth = FakeEvent(message_id="refresh-four")
    await plugin.on_llm_request(fourth, fake_request(model="gpt-4o"))
    assert providers.resolve(session).provider_id == "provider-two"
    assert context.generate_calls == []
    await plugin.terminate()


@pytest.mark.asyncio
async def test_canonical_counter_construction_failure_never_uses_byte_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)

    class FailingRegistry:
        def counter_for(self, _profile: object) -> None:
            raise RuntimeError("private tokenizer construction detail")

    monkeypatch.setattr(module, "TokenizerRegistry", FailingRegistry)
    plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})

    await plugin.initialize()

    worker = plugin._worker
    assert worker is not None
    assert worker._canonical_profile_id == module.CANONICAL_O200K.profile_id
    assert worker._canonical_counter is None
    assert not hasattr(plugin, "_counter")
    await plugin.terminate()


@pytest.mark.parametrize(
    (
        "request_model",
        "provider_model",
        "provider_limit",
        "configured_limit",
        "expected_limit",
        "expected_source",
        "expected_profile_id",
    ),
    [
        (
            "gpt-4o",
            "gpt-4o",
            262_144,
            0,
            262_144,
            "AUTO_ASTRBOT",
            "openai-o200k_base-v1",
        ),
        (
            "gpt-4o",
            "gpt-3.5-turbo",
            1_000_000,
            0,
            128_000,
            "AUTO_SAFE_FALLBACK",
            "openai-o200k_base-v1",
        ),
        (
            None,
            None,
            None,
            0,
            128_000,
            "AUTO_SAFE_FALLBACK",
            "reference-o200k-v1",
        ),
        (
            "MiniMax-Text-01",
            "provider-default",
            32_000,
            90_000,
            90_000,
            "MANUAL",
            "reference-o200k-v1",
        ),
    ],
)
@pytest.mark.asyncio
async def test_request_metadata_routes_one_immutable_profile_before_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request_model: str | None,
    provider_model: str | None,
    provider_limit: int | None,
    configured_limit: int,
    expected_limit: int,
    expected_source: str,
    expected_profile_id: str,
) -> None:
    provider = (
        None
        if provider_model is None and provider_limit is None
        else SimpleNamespace(
            get_model=lambda: provider_model,
            provider_config=(
                {} if provider_limit is None else {"max_context_tokens": provider_limit}
            ),
        )
    )
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(provider=provider),
        {
            "enabled": True,
            "model_context_limit": configured_limit,
        },
    )
    await plugin.initialize()
    event = FakeEvent(message_id=f"profile-{expected_source}")
    request = fake_request(model=request_model)

    await plugin.on_llm_request(event, request)

    state = plugin._state(event)
    assert state is not None
    assert state.prepared is not None
    profile = state.prepared.budget_profile
    assert profile.model_identity == request_model
    assert profile.context_limit == expected_limit
    assert profile.context_limit_source.value == expected_source
    assert profile.tokenizer_profile.profile_id == expected_profile_id
    assert state.prepared.user_event.token_count == len(request.prompt.encode("utf-8"))
    await plugin.terminate()


@pytest.mark.asyncio
async def test_concurrent_sessions_keep_profiles_local_and_model_switch_keeps_canonical_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    providers = {
        "umo:platform-1:session-openai": SimpleNamespace(
            get_model=lambda: "gpt-4o",
            provider_config={"max_context_tokens": 262_144},
        ),
        "umo:platform-1:session-minimax": SimpleNamespace(
            get_model=lambda: "MiniMax-Text-01",
            provider_config={"max_context_tokens": 1_000_000},
        ),
    }

    class PerSessionContext(FakeContext):
        def get_using_provider(self, *, umo: str) -> object:
            self.using_provider_requests.append(umo)
            return providers[umo]

    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        PerSessionContext(),
        {"enabled": True, "model_context_limit": 0},
    )
    await plugin.initialize()
    openai_event = FakeEvent(
        message_id="profile-openai",
        session_id="session-openai",
    )
    minimax_event = FakeEvent(
        message_id="profile-minimax",
        session_id="session-minimax",
    )
    openai_request = fake_request(
        "openai request",
        model="gpt-4o",
        conversation_id="conversation-openai",
    )
    minimax_request = fake_request(
        "minimax request",
        model="MiniMax-Text-01",
        conversation_id="conversation-minimax",
    )

    await asyncio.gather(
        plugin.on_llm_request(openai_event, openai_request),
        plugin.on_llm_request(minimax_event, minimax_request),
    )

    openai_state = plugin._state(openai_event)
    minimax_state = plugin._state(minimax_event)
    assert openai_state is not None and openai_state.prepared is not None
    assert minimax_state is not None and minimax_state.prepared is not None
    assert openai_state.prepared.budget_profile.tokenizer_profile is module.OPENAI_O200K
    assert minimax_state.prepared.budget_profile.tokenizer_profile is module.REFERENCE_O200K
    assert openai_state.prepared.budget_profile.context_limit == 262_144
    assert minimax_state.prepared.budget_profile.context_limit == 1_000_000

    bridge = plugin._bridge
    assert bridge is not None
    first_id = openai_state.prepared.user_event.event_id
    before = bridge.repository.read_event_token_counts(
        openai_state.prepared.turn.session_key,
        (first_id,),
        profile_id=module.CANONICAL_O200K.profile_id,
    )
    assert first_id in before

    switched_event = FakeEvent(
        message_id="profile-openai-switched",
        session_id="session-openai",
    )
    switched_request = fake_request(
        "same session switched model",
        model="MiniMax-Text-01",
        conversation_id="conversation-openai",
    )
    await plugin.on_llm_request(switched_event, switched_request)
    switched_state = plugin._state(switched_event)
    assert switched_state is not None and switched_state.prepared is not None
    assert switched_state.prepared.budget_profile.tokenizer_profile is module.REFERENCE_O200K
    after = bridge.repository.read_event_token_counts(
        openai_state.prepared.turn.session_key,
        (first_id, switched_state.prepared.user_event.event_id),
        profile_id=module.CANONICAL_O200K.profile_id,
    )
    assert after[first_id] == before[first_id]
    assert switched_state.prepared.user_event.event_id in after
    await plugin.terminate()


@pytest.mark.asyncio
async def test_live_budget_uses_one_coarse_thread_call_and_atomic_byte_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "model_context_limit": 100_000,
        },
    )
    await plugin.initialize()
    event = FakeEvent(message_id="one-coarse-budget")
    request = fake_request(token_usage=60_000, model="gpt-4o")
    await plugin.on_llm_request(event, request)
    bridge = plugin._bridge
    assert bridge is not None

    class FailingCounter:
        def count_text(self, _text: str) -> int:
            raise RuntimeError("private tokenizer failure")

    bridge._counter_provider = lambda _profile: FailingCounter()
    original_to_thread = asyncio.to_thread
    live_budget_calls = 0

    async def counted_to_thread(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal live_budget_calls
        if getattr(function, "__name__", "") == "run_request_budget":
            live_budget_calls += 1
        assert callable(function)
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(module.asyncio, "to_thread", counted_to_thread)
    system = FakeMessage(role="system", content="system")
    history = FakeMessage(role="assistant", content="history")
    current = fake_user_message("current input")
    messages = [system, history, current]
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    await plugin.on_agent_begin_project(event, run_context)

    state = plugin._state(event)
    assert state is not None
    assert live_budget_calls == 1
    assert state.outcome is not None
    assert state.outcome.tokenizer_mode == "BYTE_FALLBACK"
    assert state.outcome.fallback_code == "TOKENIZER_BYTE_FALLBACK"
    assert state.outcome.primary_result_discarded is True
    await plugin.terminate()


@pytest.mark.parametrize(
    ("context_limit", "system_text", "expected_code"),
    [
        (1, "system", "CONTEXT_LIMIT_TOO_SMALL"),
        (40_000, " budget" * 20_000, "REQUIRED_INPUT_EXCEEDS_BUDGET"),
    ],
    ids=("context-too-small", "required-overflow"),
)
@pytest.mark.asyncio
async def test_budget_rejection_preserves_message_list_and_every_object_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context_limit: int,
    system_text: str,
    expected_code: str,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "model_context_limit": context_limit,
        },
    )
    await plugin.initialize()
    event = FakeEvent(message_id=f"reject-{expected_code}")
    request = fake_request(
        "current input",
        token_usage=60_000,
        model="MiniMax-Text-01",
    )
    original_conversation = request.conversation
    await plugin.on_llm_request(event, request)
    system = FakeMessage(role="system", content=system_text)
    history = FakeMessage(role="assistant", content="history")
    current = fake_user_message("current input")
    current_content = current.content
    assert isinstance(current_content, list)
    current_part = current_content[0]
    messages = [system, history, current]
    message_list = messages
    original_objects = tuple(messages)
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    await plugin.on_agent_begin_project(event, run_context)

    state = plugin._state(event)
    assert state is not None
    assert state.outcome is not None
    assert state.outcome.mutation_allowed is False
    assert messages is message_list
    assert tuple(messages) == original_objects
    assert all(actual is expected for actual, expected in zip(messages, original_objects))
    assert current.content is current_content
    assert current_content[0] is current_part
    assert request.conversation is original_conversation
    assert state.projected is None
    assert state.faults[-1].code == expected_code
    await plugin.terminate()


@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [
        ("tokenizer", "TOKENIZER_COUNT_FAILED"),
        ("certificate", "INTERNAL_BUDGET_INVARIANT"),
    ],
)
@pytest.mark.asyncio
async def test_terminal_budget_failures_never_mutate_native_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
    expected_code: str,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {"enabled": True, "model_context_limit": 100_000},
    )
    await plugin.initialize()
    event = FakeEvent(message_id=f"terminal-{failure_kind}")
    request = fake_request(token_usage=60_000)
    original_conversation = request.conversation
    await plugin.on_llm_request(event, request)
    bridge = plugin._bridge
    assert bridge is not None

    async def fail_budget(*_args: object, **_kwargs: object) -> object:
        if failure_kind == "tokenizer":
            raise module.TokenizerError(module.TokenizerErrorCode.TOKENIZER_COUNT_FAILED)
        raise module.BudgetInvariantError(module.BudgetErrorCode.INTERNAL_BUDGET_INVARIANT)

    monkeypatch.setattr(bridge, "evaluate_prepared", fail_budget)
    system = FakeMessage(role="system", content="system")
    history = FakeMessage(role="assistant", content="history")
    current = fake_user_message("current input")
    messages = [system, history, current]
    original_objects = tuple(messages)
    run_context = SimpleNamespace(messages=messages)

    await plugin.on_agent_begin_guard(event, run_context)
    await plugin.on_agent_begin_project(event, run_context)

    state = plugin._state(event)
    assert state is not None
    assert tuple(messages) == original_objects
    assert all(actual is expected for actual, expected in zip(messages, original_objects))
    assert request.conversation is original_conversation
    assert state.projected is None
    assert state.faults[-1].code == expected_code
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
    current = fake_user_message("保留精确原文")
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


@pytest.mark.asyncio
async def test_missing_key_latches_locked_without_touching_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, logger = load_main(monkeypatch, tmp_path, master_key=None)
    plugin = module.AstrContinuumPlugin(
        object(),
        {
            "enabled": True,
            "encryption_key_source": "environment",
        },
    )

    await plugin.initialize()

    assert plugin._initialized is True
    assert plugin._bridge is None
    assert plugin._worker is None
    assert not (tmp_path / "astrcontinuum.sqlite3").exists()

    request = fake_request()
    original_contexts = request.contexts
    event = FakeEvent()
    await plugin.on_llm_request(event, request)
    assert request.contexts is original_contexts
    assert plugin._state(event).prepared is None

    status = [item async for item in plugin.context_status(event)]
    status_text = status[0][1]
    assert "数据保护：LOCKED" in status_text
    assert "安全代码：STORAGE_KEY_MISSING" in status_text
    assert "已记录事件" not in status_text
    assert "已发布 Checkpoint" not in status_text
    assert "待处理任务" not in status_text
    assert "密钥来源设为“环境变量”" in status_text
    assert "ASTRCONTINUUM_MASTER_KEY" in status_text
    assert "自动管理" in status_text
    assert "服务器密钥文件" in status_text
    assert "重载" in status_text
    assert TEST_MASTER_KEY not in status_text
    assert str(tmp_path) not in status_text
    assert "astrcontinuum.key" not in status_text

    inspection = [item async for item in plugin.context_inspect(event)]
    inspection_text = inspection[0][1]
    assert "检查代码：STORAGE_KEY_MISSING" in inspection_text
    assert "密钥来源设为“环境变量”" in inspection_text
    assert "ASTRCONTINUUM_MASTER_KEY" in inspection_text
    assert "自动管理" in inspection_text
    assert "服务器密钥文件" in inspection_text
    assert "重载" in inspection_text
    assert TEST_MASTER_KEY not in inspection_text
    assert str(tmp_path) not in inspection_text
    assert "astrcontinuum.key" not in inspection_text
    assert not (tmp_path / "astrcontinuum.sqlite3").exists()
    assert all(TEST_MASTER_KEY not in str(record) for record in logger.records)

    monkeypatch.setenv("ASTRCONTINUUM_MASTER_KEY", TEST_MASTER_KEY)
    await plugin.initialize()
    assert plugin._bridge is None
    assert not (tmp_path / "astrcontinuum.sqlite3").exists()

    await plugin.terminate()
    await plugin.initialize()
    assert plugin._bridge is not None
    assert plugin._worker is not None
    await plugin.terminate()


@pytest.mark.asyncio
async def test_missing_source_uses_server_managed_default_and_reuses_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path, master_key=None)
    first = module.AstrContinuumPlugin(object(), {"enabled": True})

    assert first._storage_status.key_source == "自动管理"
    await first.initialize()

    local_path = tmp_path / "astrcontinuum.key"
    first_text = local_path.read_text(encoding="ascii")
    status = [item async for item in first.context_status(FakeEvent())]
    assert "数据保护：LOCAL_KEY_DEGRADED" in status[0][1]
    assert "密钥来源：自动管理" in status[0][1]
    assert "安全代码：NONE" in status[0][1]
    await first.terminate()

    second = module.AstrContinuumPlugin(object(), {"enabled": True})
    await second.initialize()

    assert local_path.read_text(encoding="ascii") == first_text
    assert second._bridge is not None
    second_status = [item async for item in second.context_status(FakeEvent())]
    assert "数据保护：LOCAL_KEY_DEGRADED" in second_status[0][1]
    assert "密钥来源：自动管理" in second_status[0][1]
    await second.terminate()


@pytest.mark.asyncio
async def test_explicit_local_key_mode_is_visibly_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path, master_key=None)
    plugin = module.AstrContinuumPlugin(
        object(),
        {
            "enabled": True,
            "encryption_key_source": "local",
        },
    )

    await plugin.initialize()

    assert plugin._bridge is not None
    assert (tmp_path / "astrcontinuum.key").is_file()
    status = [item async for item in plugin.context_status(FakeEvent())]
    assert "数据保护：LOCAL_KEY_DEGRADED" in status[0][1]
    assert "密钥来源：自动管理" in status[0][1]
    assert "安全代码：NONE" in status[0][1]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_context_inspect_reports_current_session_provenance_without_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    context = FakeContext()
    plugin = module.AstrContinuumPlugin(context, {"enabled": True})
    await plugin.initialize()

    secret = "CURRENT-SESSION-SECRET-MUST-NOT-LEAK"
    request_event = FakeEvent(message_id="request")
    await plugin.on_llm_request(request_event, fake_request(secret))

    inspect_event = FakeEvent(message_id="inspect")
    results = [item async for item in plugin.context_inspect(inspect_event)]
    text = results[0][1]

    assert "AstrContinuum 当前会话检查" in text
    assert "数据保护：ACTIVE" in text
    assert "活动 Checkpoint：NONE" in text
    assert "指针版本：0" in text
    assert "覆盖范围：EMPTY" in text
    assert "Journal 高水位：1" in text
    assert "Delta 范围：1-1" in text
    assert "事件计数：USER_MESSAGE=1, ASSISTANT_MESSAGE=0, TOOL_CALL=0, TOOL_RESULT=0" in text
    assert "Capsule 槽位：NONE" in text
    assert "待处理任务：NONE" in text
    assert "重试代码：NONE" in text
    assert secret not in text
    assert "session-1" not in text
    assert "conversation-1" not in text
    assert TEST_MASTER_KEY not in text
    assert plugin.context_inspect.__func__.__astrbot_permission__ == "ADMIN"
    assert context.conversation_manager.current_id_calls == [
        inspect_event.unified_msg_origin,
        inspect_event.unified_msg_origin,
        inspect_event.unified_msg_origin,
        inspect_event.unified_msg_origin,
    ]
    assert context.conversation_manager.conversation_calls == [
        (inspect_event.unified_msg_origin, "conversation-1"),
        (inspect_event.unified_msg_origin, "conversation-1"),
    ]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_context_inspect_rejects_same_cid_when_persona_changes_after_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    context = FakeContext()
    plugin = module.AstrContinuumPlugin(context, {"enabled": True})
    await plugin.initialize()
    await plugin.on_llm_request(FakeEvent(message_id="request"), fake_request("current input"))
    original_read = plugin._read_session_inspection

    def read_then_change_persona(bridge: object, session_key: object) -> object:
        inspection = original_read(bridge, session_key)
        context.conversation_manager.conversation = SimpleNamespace(
            cid="conversation-1",
            persona_id="persona-after-query",
        )
        return inspection

    monkeypatch.setattr(plugin, "_read_session_inspection", read_then_change_persona)

    results = [item async for item in plugin.context_inspect(FakeEvent(message_id="inspect"))]
    text = results[0][1]

    assert "检查状态：不可用" in text
    assert "检查代码：CURRENT_CONVERSATION_CHANGED" in text
    assert "Journal 高水位" not in text
    await plugin.terminate()


@pytest.mark.asyncio
async def test_context_inspect_does_not_create_or_scan_when_no_conversation_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    context = FakeContext()
    context.conversation_manager.current_id = None
    context.conversation_manager.conversation = None
    plugin = module.AstrContinuumPlugin(context, {"enabled": True})
    await plugin.initialize()

    result = [item async for item in plugin.context_inspect(FakeEvent())]

    assert "检查状态：不可用" in result[0][1]
    assert "检查代码：NO_CURRENT_CONVERSATION" in result[0][1]
    assert context.conversation_manager.conversation_calls == []
    await plugin.terminate()


@pytest.mark.asyncio
async def test_runtime_authentication_failure_locks_plugin_and_stops_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})
    await plugin.initialize()
    bridge = plugin._bridge
    worker = plugin._worker
    assert bridge is not None
    assert worker is not None

    async def fail_authentication(
        _event: object,
        _request: object,
        *,
        budget_profile: object,
    ) -> None:
        del budget_profile
        raise module.StorageSecurityError(module.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)

    monkeypatch.setattr(bridge, "prepare_request", fail_authentication)
    request = fake_request("native request remains untouched")
    original_contexts = request.contexts

    await plugin.on_llm_request(FakeEvent(), request)

    assert request.contexts is original_contexts
    assert plugin._bridge is None
    assert plugin._worker is None
    assert worker.task is None
    status = [item async for item in plugin.context_status(FakeEvent())]
    assert "数据保护：LOCKED" in status[0][1]
    assert "安全代码：STORAGE_AUTHENTICATION_FAILED" in status[0][1]


@pytest.mark.asyncio
async def test_failed_rotation_does_not_report_configured_key_as_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    original_plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})
    await original_plugin.initialize()
    await original_plugin.terminate()

    replacement_raw = b"\x02" * 32
    replacement_key = base64.urlsafe_b64encode(replacement_raw).decode("ascii").rstrip("=")
    replacement_key_id = hashlib.sha256(replacement_raw).hexdigest()[:16]
    monkeypatch.setenv("ASTRCONTINUUM_MASTER_KEY", replacement_key)
    monkeypatch.delenv("ASTRCONTINUUM_PREVIOUS_KEY", raising=False)
    replacement_plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})

    await replacement_plugin.initialize()

    status = [item async for item in replacement_plugin.context_status(FakeEvent())]
    text = status[0][1]
    assert "数据保护：LOCKED" in text
    assert "安全代码：STORAGE_PREVIOUS_KEY_REQUIRED" in text
    assert f"活动密钥标识：{replacement_key_id}" not in text
    assert "活动密钥标识：NONE" in text
    await replacement_plugin.terminate()


@pytest.mark.asyncio
async def test_missing_source_never_silently_rekeys_an_existing_environment_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    original = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "encryption_key_source": "environment",
        },
    )
    await original.initialize()
    assert original._bridge is not None
    await original.terminate()

    monkeypatch.delenv("ASTRCONTINUUM_MASTER_KEY", raising=False)
    replacement = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})
    await replacement.initialize()

    assert replacement._bridge is None
    assert (tmp_path / "astrcontinuum.key").is_file()
    replacement_status = [item async for item in replacement.context_status(FakeEvent())]
    replacement_text = replacement_status[0][1]
    assert "数据保护：LOCKED" in replacement_text
    assert "密钥来源：自动管理" in replacement_text
    assert "安全代码：STORAGE_PREVIOUS_KEY_REQUIRED" in replacement_text
    assert "活动密钥标识：NONE" in replacement_text
    await replacement.terminate()

    monkeypatch.setenv("ASTRCONTINUUM_MASTER_KEY", TEST_MASTER_KEY)
    recovered = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "encryption_key_source": "environment",
        },
    )
    await recovered.initialize()

    assert recovered._bridge is not None
    recovered_status = [item async for item in recovered.context_status(FakeEvent())]
    assert "数据保护：ACTIVE" in recovered_status[0][1]
    assert f"活动密钥标识：{TEST_MASTER_KEY_ID}" in recovered_status[0][1]
    await recovered.terminate()


@pytest.mark.asyncio
async def test_context_status_rechecks_lifecycle_after_count_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})
    await plugin.initialize()
    query_finished = asyncio.Event()
    release_query = asyncio.Event()
    original_to_thread = asyncio.to_thread

    async def controlled_to_thread(function, /, *args: object, **kwargs: object):
        result = await original_to_thread(function, *args, **kwargs)
        if getattr(function, "__name__", "") == "read_counts":
            query_finished.set()
            await release_query.wait()
        return result

    monkeypatch.setattr(module.asyncio, "to_thread", controlled_to_thread)
    status_task = asyncio.create_task(anext(plugin.context_status(FakeEvent())))
    await asyncio.wait_for(query_finished.wait(), timeout=1)
    await plugin.terminate()
    release_query.set()

    result = await asyncio.wait_for(status_task, timeout=1)
    text = result[1]
    assert "AstrContinuum：运行中" not in text
    assert "数据保护：LOCKED" in text
    assert "安全代码：STORAGE_NOT_INITIALIZED" in text


@pytest.mark.asyncio
async def test_detected_ciphertext_tamper_locks_before_the_next_journal_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})
    await plugin.initialize()

    await plugin.on_llm_request(FakeEvent(message_id="first"), fake_request("first input"))
    bridge = plugin._bridge
    assert bridge is not None
    factory = bridge.repository.factory
    with factory.transaction(immediate=True) as connection:
        connection.execute("DROP TRIGGER journal_events_immutable_update")
        row = connection.execute("SELECT event_id, content FROM journal_events").fetchone()
        replacement = "A" if row["content"][-1] != "A" else "B"
        connection.execute(
            "UPDATE journal_events SET content = ? WHERE event_id = ?",
            (row["content"][:-1] + replacement, row["event_id"]),
        )

    await plugin.on_llm_request(
        FakeEvent(message_id="second"),
        fake_request("second input must not be written"),
    )

    assert plugin._bridge is None
    assert plugin._worker is None
    with factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 1
    status = [item async for item in plugin.context_status(FakeEvent())]
    assert "数据保护：LOCKED" in status[0][1]
    assert "安全代码：STORAGE_AUTHENTICATION_FAILED" in status[0][1]


@pytest.mark.asyncio
async def test_partial_worker_start_failure_leaks_no_background_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, logger = load_main(monkeypatch, tmp_path)
    started: list[object] = []
    original_start = module.CompactionWorker.start

    async def fail_after_start(worker: object) -> None:
        await original_start(worker)
        started.append(worker)
        raise RuntimeError("DO-NOT-LEAK-worker-start-detail")

    monkeypatch.setattr(module.CompactionWorker, "start", fail_after_start)
    plugin = module.AstrContinuumPlugin(FakeContext(), {"enabled": True})

    await plugin.initialize()

    assert len(started) == 1
    assert started[0].task is None
    assert plugin._bridge is None
    assert plugin._worker is None
    status = [item async for item in plugin.context_status(FakeEvent())]
    assert "数据保护：LOCKED" in status[0][1]
    assert "安全代码：STORAGE_STARTUP_FAILED" in status[0][1]
    assert all("DO-NOT-LEAK" not in str(record) for record in logger.records)


@pytest.mark.parametrize(
    ("configured_mode", "expected_mode"),
    [
        ("active", "ACTIVE"),
        ("AcTiVe", "ACTIVE"),
        (" SHADOW ", "SHADOW"),
        ("off", "OFF"),
        ("unsupported", "ACTIVE"),
        (object(), "ACTIVE"),
    ],
)
@pytest.mark.asyncio
async def test_context_engine_mode_is_safe_case_insensitive_and_passed_to_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured_mode: object,
    expected_mode: str,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "context_engine_mode": configured_mode,
        },
    )

    await plugin.initialize()

    bridge = plugin._bridge
    assert bridge is not None
    assert plugin._context_engine_mode.value == expected_mode
    assert bridge.context_engine_mode.value == expected_mode
    await plugin.terminate()


@pytest.mark.parametrize(
    ("configured_mode", "outcome", "recovery_count", "expected_state"),
    [
        ("active", "ACTIVE", 0, "VERIFIED"),
        ("active", "ACTIVE", 2, "REFINED"),
        ("active", "DEGRADED_RAW", 0, "DEGRADED_RAW"),
        ("shadow", "SHADOW", 0, "UNVERIFIED"),
        ("off", "OFF", 0, "NONE"),
    ],
)
@pytest.mark.asyncio
async def test_context_status_reports_mode_and_latest_content_free_state_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured_mode: str,
    outcome: str,
    recovery_count: int,
    expected_state: str,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "context_engine_mode": configured_mode,
        },
    )
    await plugin.initialize()
    bridge = plugin._bridge
    assert bridge is not None
    trace = fake_context_trace(
        mode=configured_mode.upper(),
        outcome=outcome,
        error_code="TRACE-DETAIL-MUST-NOT-LEAK",
        adaptive_retry_count=recovery_count,
    )
    plugin._bridge = SimpleNamespace(
        repository=bridge.repository,
        last_context_trace=trace,
    )

    result = [item async for item in plugin.context_status(FakeEvent())]
    text = result[0][1]

    assert f"上下文引擎：{configured_mode.upper()}" in text
    assert f"最近引擎状态：{expected_state}" in text
    assert "TRACE-DETAIL-MUST-NOT-LEAK" not in text
    assert "候选块" not in text
    await plugin.terminate()


@pytest.mark.asyncio
async def test_context_status_reports_none_before_any_engine_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "context_engine_mode": "ACTIVE",
        },
    )
    await plugin.initialize()

    result = [item async for item in plugin.context_status(FakeEvent())]

    assert "上下文引擎：ACTIVE" in result[0][1]
    assert "最近引擎状态：NONE" in result[0][1]
    await plugin.terminate()


@pytest.mark.asyncio
async def test_context_inspect_uses_only_current_session_trace_and_bounded_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    context = FakeContext()
    plugin = module.AstrContinuumPlugin(
        context,
        {
            "enabled": True,
            "context_engine_mode": "active",
        },
    )
    await plugin.initialize()
    request_event = FakeEvent(message_id="request")
    await plugin.on_llm_request(
        request_event,
        fake_request("CURRENT-SESSION-CONTENT-MUST-NOT-LEAK"),
    )
    state = plugin._state(request_event)
    assert state is not None
    assert state.prepared is not None
    current_key = state.prepared.turn.session_key
    bridge = plugin._bridge
    assert bridge is not None
    current_trace = fake_context_trace(
        mode="ACTIVE",
        outcome="ACTIVE",
        candidate_count=8,
        selected_count=4,
        relation_count=6,
        constraint_count=2,
        retained_count=7,
        reduced_count=1,
        selected_budget_units=1_234,
        required_passed=True,
        provenance_passed=True,
        residual_band="VERIFIED",
        adaptive_retry_count=1,
    )
    other_session_trace = fake_context_trace(
        mode="ACTIVE",
        outcome="DEGRADED_RAW",
        candidate_count=999,
        error_code="OTHER-SESSION-CODE-MUST-NOT-LEAK",
    )
    inspected_keys: list[object] = []

    def inspect_context_trace(session_key: object) -> object:
        inspected_keys.append(session_key)
        return current_trace if session_key == current_key else other_session_trace

    plugin._bridge = SimpleNamespace(
        repository=bridge.repository,
        last_context_trace=other_session_trace,
        inspect_context_trace=inspect_context_trace,
    )

    result = [item async for item in plugin.context_inspect(FakeEvent(message_id="inspect"))]
    text = result[0][1]

    assert inspected_keys == [current_key]
    assert "上下文引擎：ACTIVE" in text
    assert "最近引擎状态：REFINED" in text
    assert "候选块：8" in text
    assert "选中块：4" in text
    assert "关系数：6" in text
    assert "约束数：2" in text
    assert "保留块：7" in text
    assert "归约块：1" in text
    assert "选择预算单位：1234" in text
    assert "归约比例：12.50%" in text
    assert "残差带：VERIFIED" in text
    assert "恢复次数：1" in text
    assert "必选覆盖：PASS" in text
    assert "来源覆盖：PASS" in text
    assert "稳定代码：NONE" in text
    assert "999" not in text
    assert "OTHER-SESSION-CODE-MUST-NOT-LEAK" not in text
    assert "CURRENT-SESSION-CONTENT-MUST-NOT-LEAK" not in text
    assert "session-1" not in text
    assert "conversation-1" not in text
    await plugin.terminate()


@pytest.mark.parametrize(
    ("mode", "outcome", "expected_state"),
    [
        ("SHADOW", "SHADOW", "UNVERIFIED"),
        ("OFF", "OFF", "NONE"),
        ("ACTIVE", "DEGRADED_RAW", "DEGRADED_RAW"),
    ],
)
@pytest.mark.asyncio
async def test_context_inspect_labels_shadow_off_and_degraded_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    outcome: str,
    expected_state: str,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "context_engine_mode": mode,
        },
    )
    await plugin.initialize()
    bridge = plugin._bridge
    assert bridge is not None
    trace = fake_context_trace(
        mode=mode,
        outcome=outcome,
        candidate_count=0,
        selected_count=0,
        relation_count=0,
        constraint_count=0,
        retained_count=0,
        reduced_count=0,
        selected_budget_units=0,
        required_passed=True,
        provenance_passed=True,
        residual_band="UNAVAILABLE",
    )
    plugin._bridge = SimpleNamespace(
        repository=bridge.repository,
        last_context_trace=trace,
        inspect_context_trace=lambda _session_key: trace,
    )

    result = [item async for item in plugin.context_inspect(FakeEvent())]
    text = result[0][1]

    assert f"上下文引擎：{mode}" in text
    assert f"最近引擎状态：{expected_state}" in text
    assert "归约比例：0.00%" in text
    await plugin.terminate()


@pytest.mark.asyncio
async def test_context_inspect_bounds_and_sanitizes_hostile_trace_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, _logger = load_main(monkeypatch, tmp_path)
    plugin = module.AstrContinuumPlugin(
        FakeContext(),
        {
            "enabled": True,
            "context_engine_mode": "active",
        },
    )
    await plugin.initialize()
    bridge = plugin._bridge
    assert bridge is not None
    trace = fake_context_trace(
        mode="ACTIVE",
        outcome="ACTIVE",
        error_code="SECRET exception /provider/path",
        candidate_count=10**100,
        selected_count=float("nan"),
        relation_count=float("inf"),
        constraint_count="SECRET-CONSTRAINT",
        retained_count=-1,
        reduced_count=10**101,
        selected_budget_units=10**102,
        required_passed="SECRET-REQUIRED",
        provenance_passed=None,
        residual_band="SECRET RESIDUAL",
        adaptive_retry_count=10**103,
    )
    plugin._bridge = SimpleNamespace(
        repository=bridge.repository,
        last_context_trace=trace,
        inspect_context_trace=lambda _session_key: trace,
    )

    result = [item async for item in plugin.context_inspect(FakeEvent())]
    text = result[0][1]
    lowered = text.lower()

    assert "候选块：1000000+" in text
    assert "选中块：INVALID" in text
    assert "关系数：INVALID" in text
    assert "约束数：INVALID" in text
    assert "保留块：INVALID" in text
    assert "归约块：1000000+" in text
    assert "选择预算单位：1000000+" in text
    assert "归约比例：100.00%" in text
    assert "残差带：INVALID" in text
    assert "恢复次数：1000000+" in text
    assert "必选覆盖：UNKNOWN" in text
    assert "来源覆盖：UNKNOWN" in text
    assert "稳定代码：INVALID" in text
    assert "secret" not in lowered
    assert "exception" not in lowered
    assert "provider" not in lowered
    assert "/path" not in lowered
    assert "nan" not in lowered
    assert "inf" not in lowered
    await plugin.terminate()
