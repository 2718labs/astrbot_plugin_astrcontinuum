from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace

import pytest

import astrcontinuum.adapters.astrbot as astrbot_adapter
from astrcontinuum.adapters import (
    AdapterErrorCode,
    AdapterStage,
    AstrBotAdapterError,
    AstrBotHookBridge,
    ContextEngineMode,
    LiveContextTrace,
    build_projection_objects,
    canonical_tool_metadata,
    deterministic_event_identity,
    estimate_opaque_token_cost,
    extract_host_session_key,
    extract_host_turn_identity,
    probe_projection_capability,
)
from astrcontinuum.context_graph import EngineOutcome, ResidualBand
from astrcontinuum.domain import SessionKey, SourceHook
from astrcontinuum.runtime import BudgetConfig, Utf8ByteTokenCounter


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
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            timestamp=timestamp,
            type=FakeMessageType.FRIEND,
            session_id="session-9",
        )
        self._sender_id = sender_id

    def get_platform_id(self) -> str:
        return "platform-instance-1"

    def get_group_id(self) -> str:
        return ""

    def get_sender_id(self) -> str:
        return self._sender_id


def _request(*, persona_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        prompt="current user input",
        conversation=SimpleNamespace(cid="conversation-3", persona_id=persona_id),
    )


def _session_key(suffix: str) -> SessionKey:
    return SessionKey(
        platform_instance_id="platform-instance-1",
        message_type="FriendMessage",
        session_id=f"session-{suffix}",
        group_id=None,
        user_id="user-5",
        conversation_id=f"conversation-{suffix}",
        persona_id=None,
    )


def _live_trace(label: str) -> LiveContextTrace:
    return LiveContextTrace(
        mode=ContextEngineMode.ACTIVE,
        outcome=EngineOutcome.ACTIVE,
        error_code=label,
        candidate_count=1,
        selected_count=1,
        relation_count=0,
        constraint_count=0,
        retained_count=1,
        reduced_count=0,
        selected_budget_units=1,
        required_passed=True,
        provenance_passed=True,
        residual_band=ResidualBand.VERIFIED,
        adaptive_retry_count=0,
    )


def test_extract_host_turn_identity_uses_exact_seven_field_session_key() -> None:
    request = _request()

    identity = extract_host_turn_identity(FakeEvent(), request)

    assert identity.request_identity == id(request)
    assert identity.host_message_id == "message-42"
    assert identity.created_at == datetime.fromtimestamp(1_727_000_000, timezone.utc)
    assert identity.session_key.model_dump() == {
        "platform_instance_id": "platform-instance-1",
        "message_type": "FriendMessage",
        "session_id": "session-9",
        "group_id": None,
        "user_id": "user-5",
        "conversation_id": "conversation-3",
        "persona_id": None,
    }


def test_session_key_extractor_is_shared_by_hooks_and_admin_inspection() -> None:
    event = FakeEvent()
    conversation = SimpleNamespace(cid="conversation-3", persona_id="")

    inspected = extract_host_session_key(event, conversation)
    prepared = extract_host_turn_identity(
        event,
        SimpleNamespace(prompt="input", conversation=conversation),
    )

    assert inspected == prepared.session_key
    assert inspected.persona_id is None


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


@pytest.mark.asyncio
async def test_bridge_calls_live_assembly_once_and_keeps_content_free_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = object()
    candidates = (object(),)
    session_key = _session_key("current")
    prepared = SimpleNamespace(
        turn=SimpleNamespace(session_key=session_key),
        current_input="current user input",
        view=view,
        candidates=candidates,
    )
    counter = Utf8ByteTokenCounter()
    budget_config = BudgetConfig()
    assembly = object()
    trace = _live_trace("VERIFIED")
    calls: list[tuple[object, object, dict[str, object]]] = []

    def fake_live_assembly(
        actual_view: object,
        actual_candidates: object,
        **kwargs: object,
    ) -> tuple[object, LiveContextTrace]:
        calls.append((actual_view, actual_candidates, kwargs))
        return assembly, trace

    monkeypatch.setattr(
        astrbot_adapter,
        "assemble_live_context",
        fake_live_assembly,
    )
    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        budget_config=budget_config,
        counter=counter,
        context_engine_mode=ContextEngineMode.ACTIVE,
    )

    assert bridge.context_engine_mode is ContextEngineMode.ACTIVE
    assert bridge.last_context_trace is None
    assert bridge.inspect_context_trace(session_key) is None

    result = await bridge.assemble_prepared(
        prepared,  # type: ignore[arg-type]
        opaque_token_cost=17,
        fixed_required_cost=23,
    )

    assert result is assembly
    assert len(calls) == 1
    actual_view, actual_candidates, kwargs = calls[0]
    assert actual_view is view
    assert actual_candidates is candidates
    assert kwargs == {
        "current_input": "current user input",
        "opaque_token_cost": 17,
        "fixed_required_cost": 23,
        "counter": counter,
        "budget_config": budget_config,
        "mode": ContextEngineMode.ACTIVE,
    }
    assert bridge.last_context_trace is trace
    assert bridge.inspect_context_trace(session_key) is trace
    assert bridge.inspect_context_trace(_session_key("unknown")) is None


@pytest.mark.asyncio
async def test_bridge_context_trace_registry_is_bounded_to_256_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_live_assembly(
        actual_view: object,
        _actual_candidates: object,
        **_kwargs: object,
    ) -> tuple[object, LiveContextTrace]:
        return object(), actual_view.trace  # type: ignore[attr-defined, no-any-return]

    monkeypatch.setattr(
        astrbot_adapter,
        "assemble_live_context",
        fake_live_assembly,
    )
    bridge = AstrBotHookBridge(object())  # type: ignore[arg-type]
    keys: list[SessionKey] = []
    traces: list[LiveContextTrace] = []
    for index in range(257):
        session_key = _session_key(str(index))
        trace = _live_trace(str(index))
        keys.append(session_key)
        traces.append(trace)
        await bridge.assemble_prepared(
            SimpleNamespace(
                turn=SimpleNamespace(session_key=session_key),
                current_input="input",
                view=SimpleNamespace(trace=trace),
                candidates=(),
            ),  # type: ignore[arg-type]
            opaque_token_cost=0,
            fixed_required_cost=0,
        )

    assert bridge.context_engine_mode is ContextEngineMode.OFF
    assert bridge.inspect_context_trace(keys[0]) is None
    assert bridge.inspect_context_trace(keys[1]) is traces[1]
    assert bridge.inspect_context_trace(keys[-1]) is traces[-1]
    assert bridge.last_context_trace is traces[-1]


@pytest.mark.asyncio
async def test_bridge_live_assembly_keeps_the_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_key = _session_key("responsive")
    assembly = object()
    trace = _live_trace("VERIFIED")
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_thread_ids: list[int] = []

    def waiting_live_assembly(
        _view: object,
        _candidates: object,
        **_kwargs: object,
    ) -> tuple[object, LiveContextTrace]:
        worker_thread_ids.append(threading.get_ident())
        worker_started.set()
        if not release_worker.wait(timeout=0.25):
            raise AssertionError("live assembly blocked the event loop")
        return assembly, trace

    monkeypatch.setattr(
        astrbot_adapter,
        "assemble_live_context",
        waiting_live_assembly,
    )
    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        context_engine_mode=ContextEngineMode.ACTIVE,
    )
    prepared = SimpleNamespace(
        turn=SimpleNamespace(session_key=session_key),
        current_input="input",
        view=object(),
        candidates=(object(),),
    )
    event_loop_thread_id = threading.get_ident()

    assembly_task = asyncio.create_task(
        bridge.assemble_prepared(
            prepared,  # type: ignore[arg-type]
            opaque_token_cost=0,
            fixed_required_cost=0,
        )
    )
    for _ in range(100):
        if worker_started.is_set():
            break
        await asyncio.sleep(0.001)

    assert worker_started.is_set()
    release_worker.set()
    result = await asyncio.wait_for(assembly_task, timeout=1.0)

    assert result is assembly
    assert worker_thread_ids
    assert worker_thread_ids[0] != event_loop_thread_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_mode",
    (ContextEngineMode.ACTIVE, ContextEngineMode.SHADOW),
)
async def test_bridge_256_candidate_capacity_returns_raw_fallback_without_live_graph(
    monkeypatch: pytest.MonkeyPatch,
    configured_mode: ContextEngineMode,
) -> None:
    session_key = _session_key(f"capacity-{configured_mode.value}")
    candidates = tuple(object() for _ in range(256))
    raw_fallback = object()
    unverified_graph = object()
    calls: list[ContextEngineMode] = []

    def capacity_aware_live_assembly(
        _view: object,
        actual_candidates: object,
        **kwargs: object,
    ) -> tuple[object, LiveContextTrace]:
        mode = kwargs["mode"]
        assert isinstance(mode, ContextEngineMode)
        calls.append(mode)
        candidate_count = len(actual_candidates)  # type: ignore[arg-type]
        trace = LiveContextTrace(
            mode=mode,
            outcome=EngineOutcome.OFF,
            error_code=None,
            candidate_count=candidate_count,
            selected_count=1,
            relation_count=0,
            constraint_count=0,
            retained_count=0,
            reduced_count=candidate_count,
            selected_budget_units=1,
            required_passed=False,
            provenance_passed=False,
            residual_band=ResidualBand.UNAVAILABLE,
            adaptive_retry_count=0,
        )
        return (raw_fallback if mode is ContextEngineMode.OFF else unverified_graph), trace

    monkeypatch.setattr(
        astrbot_adapter,
        "assemble_live_context",
        capacity_aware_live_assembly,
    )
    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        context_engine_mode=configured_mode,
    )

    result = await bridge.assemble_prepared(
        SimpleNamespace(
            turn=SimpleNamespace(session_key=session_key),
            current_input="input",
            view=object(),
            candidates=candidates,
        ),  # type: ignore[arg-type]
        opaque_token_cost=0,
        fixed_required_cost=0,
    )

    assert result is raw_fallback
    assert result is not unverified_graph
    assert calls == [ContextEngineMode.OFF]
    trace = bridge.last_context_trace
    assert trace is not None
    assert trace.mode is configured_mode
    assert trace.outcome is EngineOutcome.DEGRADED_RAW
    assert trace.error_code == "GRAPH_LIVE_CAPACITY_EXCEEDED"
    assert trace.candidate_count == 256
    assert bridge.inspect_context_trace(session_key) is trace
