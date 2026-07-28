from __future__ import annotations

import inspect
import json
import threading
from dataclasses import replace
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
    HostTurnIdentity,
    PreparedRequest,
    build_projection_objects,
    canonical_tool_metadata,
    deterministic_event_identity,
    estimate_opaque_token_cost,
    extract_host_session_key,
    extract_host_turn_identity,
    projection_factory_from_user_message,
)
from astrcontinuum.domain import EventEnvelope, EventType, SessionKey, SourceHook
from astrcontinuum.storage import RequestView
from astrcontinuum.tokenization import (
    OPENAI_O200K,
    ContextLimitSource,
    HostBudgetView,
    RequestBudgetProfile,
    TokenizerError,
    TokenizerErrorCode,
    TokenizerMode,
)


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


def _budget_profile() -> RequestBudgetProfile:
    return RequestBudgetProfile(
        model_identity="request-model",
        context_limit=100,
        context_limit_source=ContextLimitSource.MANUAL,
        tokenizer_profile=OPENAI_O200K,
        target_input_budget=90,
        hard_input_ceiling=90,
        reserved_output_and_tools=10,
        safety_margin=1,
        compaction_start_ratio=0.5,
        provider_view_switch_ratio=0.6,
    )


def _prepared_request(
    *,
    profile: RequestBudgetProfile | None = None,
    trusted_token_usage: int | None = 90,
) -> PreparedRequest:
    session_key = _session_key("request-budget")
    created_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
    user_event = EventEnvelope.create(
        event_id="evt-request-budget",
        session_key=session_key,
        sequence=1,
        event_type=EventType.USER_MESSAGE,
        content="hello",
        idempotency_key="idem-request-budget",
        token_count=5,
        created_at=created_at,
    )
    view = RequestView(
        session_key=session_key,
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=1,
        delta=(user_event,),
    )
    return PreparedRequest(
        turn=HostTurnIdentity(
            session_key=session_key,
            host_message_id="host-request-budget",
            created_at=created_at,
            request_identity=1,
        ),
        current_input="hello",
        user_event=user_event,
        view=view,
        candidates=(),
        trusted_token_usage=trusted_token_usage,
        budget_profile=profile or _budget_profile(),
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

    native = FakeMessage(
        role="user",
        content=[FakeTextPart(text="native input")],
    )
    factory = projection_factory_from_user_message(native)
    built = build_projection_objects("projected context", factory)

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


def test_projection_factory_is_derived_from_hook_objects_without_core_imports() -> None:
    class HookTextPart:
        def __init__(self, *, text: str) -> None:
            self.text = text
            self._no_save = False

        def mark_as_temp(self) -> HookTextPart:
            self._no_save = True
            return self

    class HookMessage:
        def __init__(self, *, role: str, content: list[object]) -> None:
            self.role = role
            self.content = content
            self._no_save = False

    source = inspect.getsource(astrbot_adapter)
    assert "astrbot.core." not in source

    hook_user_message = HookMessage(
        role="user",
        content=[HookTextPart(text="native current input")],
    )
    factory = astrbot_adapter.projection_factory_from_user_message(hook_user_message)
    built = build_projection_objects("projected context", factory)

    assert built.fault is None
    assert len(built.objects) == 1
    projected = built.objects[0]
    assert isinstance(projected, HookMessage)
    assert projected is not hook_user_message
    assert projected.role == "user"
    assert projected._no_save is True
    assert len(projected.content) == 1
    assert isinstance(projected.content[0], HookTextPart)
    assert projected.content[0].text == "projected context"
    assert projected.content[0]._no_save is True
    assert not hasattr(projected, "id")
    assert not hasattr(projected, "name")
    assert not hasattr(projected, "tool_calls")


def test_missing_or_broken_projection_capability_fails_open_without_content() -> None:
    with pytest.raises(AstrBotAdapterError) as missing:
        projection_factory_from_user_message(
            SimpleNamespace(content=[SimpleNamespace(binary=b"private")])
        )

    assert missing.value.code == AdapterErrorCode.PROJECTION_API_UNAVAILABLE.value
    assert "private" not in repr(missing.value)

    class BrokenTextPart:
        def __init__(self, *, text: str) -> None:
            self.text = text

    class BrokenMessage:
        def __init__(self, *, role: str, content: list[object]) -> None:
            self.role = role
            self.content = content

    broken_native = BrokenMessage(
        role="user",
        content=[BrokenTextPart(text="native")],
    )
    broken = projection_factory_from_user_message(broken_native)
    broken_build = build_projection_objects("still private", broken)

    assert broken_build.objects == ()
    assert broken_build.fault is not None
    assert broken_build.fault.code == AdapterErrorCode.PROJECTION_BUILD_FAILED.value
    assert "still private" not in repr(broken_build)


def test_projection_factory_redacts_hostile_hook_properties() -> None:
    secret = "SECRET-hook-property:/private/provider/path"

    class HostileMessage:
        @property
        def content(self) -> object:
            raise RuntimeError(secret)

    with pytest.raises(AstrBotAdapterError) as caught:
        projection_factory_from_user_message(HostileMessage())

    assert caught.value.code == AdapterErrorCode.PROJECTION_API_UNAVAILABLE.value
    assert secret not in repr(caught.value)


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


def test_opaque_text_extraction_redacts_hostile_hook_properties() -> None:
    secret = "SECRET-opaque-property:/private/provider/path"

    class HostileMessage:
        @property
        def content(self) -> object:
            raise RuntimeError(secret)

    class ByteCounter:
        def count_text(self, text: str) -> int:
            return len(text.encode("utf-8"))

    with pytest.raises(AstrBotAdapterError) as caught:
        estimate_opaque_token_cost((HostileMessage(),), ByteCounter())

    assert caught.value.code == AdapterErrorCode.OPAQUE_TOKEN_COUNT_INVALID.value
    assert secret not in repr(caught.value)


@pytest.mark.asyncio
async def test_bridge_runs_the_whole_live_budget_in_one_coarse_thread_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingPrimaryCounter:
        def count_text(self, _text: str) -> int:
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_FAILED)

    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []
    live_profiles: list[object] = []
    original_assemble_live_context = astrbot_adapter.assemble_live_context

    def counted_assemble_live_context(
        *args: object,
        **kwargs: object,
    ) -> object:
        live_profiles.append(kwargs["counter"].profile)  # type: ignore[attr-defined]
        return original_assemble_live_context(*args, **kwargs)  # type: ignore[arg-type]

    async def immediate_to_thread(
        function: object,
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((function, args, kwargs))
        assert callable(function)
        return function(*args, **kwargs)

    monkeypatch.setattr(astrbot_adapter.asyncio, "to_thread", immediate_to_thread)
    monkeypatch.setattr(
        astrbot_adapter,
        "assemble_live_context",
        counted_assemble_live_context,
    )
    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        counter_provider=lambda _profile: FailingPrimaryCounter(),
    )
    prepared = _prepared_request()
    host_view = HostBudgetView(
        all_host_texts=("system", "hello"),
        opaque_texts=(),
        fixed_required_texts=("system",),
    )

    outcome = await bridge.evaluate_prepared(
        prepared,
        host_budget_view=host_view,
    )

    assert len(calls) == 1
    function, args, kwargs = calls[0]
    assert function is astrbot_adapter.run_request_budget
    assert len(args) == 1
    assert args[0].prepared.current_input == "hello"  # type: ignore[attr-defined]
    assert args[0].host is host_view  # type: ignore[attr-defined]
    assert args[0].profile is prepared.budget_profile  # type: ignore[attr-defined]
    assert set(kwargs) == {"primary", "fallback", "assembly_runner"}
    assert outcome.tokenizer_mode == TokenizerMode.BYTE_FALLBACK.value
    assert outcome.fallback_code == "TOKENIZER_BYTE_FALLBACK"
    assert outcome.primary_result_discarded is True
    assert live_profiles == [astrbot_adapter.BYTE_FALLBACK]
    assert bridge.last_context_trace is not None
    assert bridge.inspect_context_trace(prepared.turn.session_key) is bridge.last_context_trace
    assert not hasattr(bridge, "_counter")
    assert not hasattr(bridge, "_budget_config")


@pytest.mark.asyncio
async def test_live_counter_construction_and_counting_stay_in_the_worker_thread() -> None:
    event_loop_thread = threading.get_ident()
    provider_threads: list[int] = []
    count_threads: list[int] = []

    class RecordingCounter:
        def __init__(self, profile: object) -> None:
            self.profile = profile

        def count_text(self, text: str) -> int:
            count_threads.append(threading.get_ident())
            return len(text.encode("utf-8"))

    def counter_provider(profile: object) -> RecordingCounter:
        provider_threads.append(threading.get_ident())
        return RecordingCounter(profile)

    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        counter_provider=counter_provider,  # type: ignore[arg-type]
    )

    await bridge.evaluate_prepared(
        _prepared_request(),
        host_budget_view=HostBudgetView(
            all_host_texts=("system", "hello"),
            opaque_texts=(),
            fixed_required_texts=("system",),
        ),
    )

    assert provider_threads
    assert count_threads
    assert all(thread_id != event_loop_thread for thread_id in provider_threads)
    assert all(thread_id != event_loop_thread for thread_id in count_threads)


@pytest.mark.asyncio
async def test_mismatched_primary_counter_profile_uses_true_byte_fallback() -> None:
    class MismatchedCounter:
        profile = astrbot_adapter.BYTE_FALLBACK

        def __init__(self) -> None:
            self.calls = 0

        def count_text(self, _text: str) -> int:
            self.calls += 1
            return 1

    mismatched = MismatchedCounter()
    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        counter_provider=lambda _profile: mismatched,
    )

    outcome = await bridge.evaluate_prepared(
        _prepared_request(),
        host_budget_view=HostBudgetView(
            all_host_texts=("system", "hello"),
            opaque_texts=(),
            fixed_required_texts=("system",),
        ),
    )

    assert mismatched.calls == 0
    assert outcome.tokenizer_mode == TokenizerMode.BYTE_FALLBACK.value
    assert outcome.fallback_code == "TOKENIZER_BYTE_FALLBACK"
    assert outcome.primary_result_discarded is True


@pytest.mark.asyncio
async def test_unprofiled_primary_counter_uses_true_profiled_byte_fallback() -> None:
    class UnprofiledByteCounter:
        def __init__(self) -> None:
            self.calls = 0

        def count_text(self, text: str) -> int:
            self.calls += 1
            return len(text.encode("utf-8"))

    unprofiled = UnprofiledByteCounter()
    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        counter_provider=lambda _profile: unprofiled,
    )

    outcome = await bridge.evaluate_prepared(
        _prepared_request(),
        host_budget_view=HostBudgetView(
            all_host_texts=("system", "hello"),
            opaque_texts=(),
            fixed_required_texts=("system",),
        ),
    )

    assert unprofiled.calls == 0
    assert outcome.tokenizer_mode == TokenizerMode.BYTE_FALLBACK.value
    assert outcome.fallback_code == "TOKENIZER_BYTE_FALLBACK"
    assert outcome.primary_result_discarded is True


@pytest.mark.asyncio
async def test_no_mutation_budget_outcome_does_not_replace_the_last_trace() -> None:
    class RecordingCounter:
        def __init__(self, profile: object) -> None:
            self.profile = profile

        def count_text(self, text: str) -> int:
            return len(text.encode("utf-8"))

    bridge = AstrBotHookBridge(
        object(),  # type: ignore[arg-type]
        counter_provider=lambda profile: RecordingCounter(profile),  # type: ignore[arg-type]
    )
    prepared = _prepared_request()
    host = HostBudgetView(
        all_host_texts=("system", "hello"),
        opaque_texts=(),
        fixed_required_texts=("system",),
    )

    accepted = await bridge.evaluate_prepared(prepared, host_budget_view=host)
    previous_trace = bridge.last_context_trace
    rejected = await bridge.evaluate_prepared(
        replace(
            prepared,
            budget_profile=replace(
                prepared.budget_profile,
                context_limit=1,
            ),
        ),
        host_budget_view=host,
    )

    assert accepted.mutation_allowed is True
    assert previous_trace is not None
    assert rejected.mutation_allowed is False
    assert rejected.stable_code == "CONTEXT_LIMIT_TOO_SMALL"
    assert bridge.last_context_trace is previous_trace
    assert bridge.inspect_context_trace(prepared.turn.session_key) is previous_trace


def test_prepared_request_owns_one_immutable_content_free_profile() -> None:
    secret = "private-model-identity"
    profile = RequestBudgetProfile(
        model_identity=secret,
        context_limit=128_000,
        context_limit_source=ContextLimitSource.AUTO_SAFE_FALLBACK,
        tokenizer_profile=OPENAI_O200K,
        target_input_budget=90_000,
        hard_input_ceiling=100_000,
    )
    prepared = _prepared_request(profile=profile)

    assert prepared.budget_profile is profile
    assert not hasattr(prepared, "__dict__")
    assert secret not in repr(prepared)
