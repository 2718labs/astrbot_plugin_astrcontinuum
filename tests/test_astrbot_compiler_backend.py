from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import astrcontinuum as ac
from astrcontinuum.compaction import (
    AstrBotExtractiveCompilerBackend,
    CompactionProviderBinding,
    CompilerBackendDeferred,
    ExtractiveCompilerError,
    SessionProviderRegistry,
)
from astrcontinuum.tokenization import (
    BYTE_FALLBACK,
    OPENAI_O200K,
    ContextLimitSource,
    TokenizerError,
    TokenizerErrorCode,
    TokenizerRouter,
)

NOW = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)


class LengthCounter:
    def count_text(self, text: str) -> int:
        return len(text)


class ProfiledCounter:
    def __init__(self, profile: object, *, fail_on: str | None = None) -> None:
        self.profile = profile
        self.fail_on = fail_on
        self.texts: list[str] = []
        self.failed = False

    def count_text(self, text: str) -> int:
        self.texts.append(text)
        if self.fail_on is not None and self.fail_on in text:
            self.failed = True
            raise RuntimeError("private tokenizer failure")
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return 0
        events = payload.get("events", ())
        return len(events) * 4 if isinstance(events, list) else 0


class RecordingGenerator:
    def __init__(self, *responses: str, before_call: object | None = None) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []
        self.before_call = before_call

    async def __call__(
        self,
        provider_id: str,
        system_prompt: str,
        prompt: str,
    ) -> str:
        if callable(self.before_call):
            self.before_call()
        self.calls.append((provider_id, system_prompt, prompt))
        return self.responses.pop(0)


def key(name: str = "extractive") -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=f"session-{name}",
        group_id=None,
        user_id=f"user-{name}",
        conversation_id=f"conversation-{name}",
        persona_id=None,
    )


def event(
    sequence: int,
    content: str,
    *,
    session_key: ac.SessionKey | None = None,
    event_type: ac.EventType = ac.EventType.USER_MESSAGE,
) -> ac.EventEnvelope:
    selected_key = session_key or key()
    return ac.EventEnvelope.create(
        event_id=f"event-{sequence}",
        session_key=selected_key,
        sequence=sequence,
        event_type=event_type,
        content=content,
        idempotency_key=f"request-{sequence}",
        token_count=len(content),
        created_at=NOW + timedelta(seconds=sequence),
    )


def extraction(
    event_id: str,
    quote: str,
    *,
    acknowledged: tuple[str, ...] | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {
        "acknowledged_event_ids": list(acknowledged or (event_id,)),
        "goals": [
            {
                "event_id": event_id,
                "quote": quote,
                "confidence": 0.95,
            }
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
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def extraction_for(events: tuple[ac.EventEnvelope, ...]) -> str:
    payload = json.loads(
        extraction(
            events[0].event_id,
            events[0].content,
            acknowledged=tuple(item.event_id for item in events),
        )
    )
    payload["goals"] = [
        {
            "event_id": item.event_id,
            "quote": item.content,
            "confidence": 0.95,
        }
        for item in events
        if item.content
    ]
    return json.dumps(payload, ensure_ascii=False)


def request(
    events: tuple[ac.EventEnvelope, ...],
    *,
    base_capsules: tuple[ac.ContextCapsuleEnvelope, ...] = (),
    max_events_per_segment: int = 16,
    canonical_counts: dict[str, int] | None = None,
    token_ceiling: int = 10_000,
) -> ac.CompilationRequest:
    resolved_counts = canonical_counts or {item.event_id: item.token_count for item in events}
    return ac.CompilationRequest(
        base_snapshot=None,
        base_capsules=base_capsules,
        source_events=events,
        segments=ac.segment(
            events,
            token_counts=resolved_counts,
            config=ac.SegmenterConfig(
                max_events_per_segment=max_events_per_segment,
                max_tokens_per_segment=10_000,
            ),
        ),
        target_high_water_mark=events[-1].sequence,
        token_ceiling=token_ceiling,
        canonical_event_token_counts=resolved_counts,
    )


def binding(
    provider_id: str,
    *,
    model_identity: str = "gpt-4o",
    context_limit: int = 10_000,
) -> CompactionProviderBinding:
    return CompactionProviderBinding(
        provider_id=provider_id,
        model_identity=model_identity,
        context_limit=context_limit,
        context_limit_source=ContextLimitSource.AUTO_ASTRBOT,
    )


def backend(
    generator: RecordingGenerator,
    *,
    provider_id: str = "minimax-compiler",
) -> AstrBotExtractiveCompilerBackend:
    registry = SessionProviderRegistry(max_entries=8)
    registry.remember(key(), binding(provider_id))
    return AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )


@pytest.mark.asyncio
async def test_backend_builds_deterministic_exact_source_capsule_without_summary() -> None:
    source = event(1, "继续实现后台 worker，不要改写原始历史。")
    generator = RecordingGenerator(extraction(source.event_id, "不要改写原始历史"))
    compiler = backend(generator)

    first = await compiler.compile(request((source,)))
    second_generator = RecordingGenerator(extraction(source.event_id, "不要改写原始历史"))
    second = await backend(second_generator).compile(request((source,)))

    assert len(generator.calls) == 1
    provider_id, system_prompt, prompt = generator.calls[0]
    assert provider_id == "minimax-compiler"
    assert "Do not summarize or paraphrase" in system_prompt
    assert '"role":"USER"' in prompt
    assert source.content in prompt

    assert len(first.capsules) == 1
    capsule = first.capsules[0]
    assert capsule.goals[0].text == "不要改写原始历史"
    assert capsule.goals[0].source_event_ids == (source.event_id,)
    assert capsule.narrative_summary == "Structured source map for events 1-1."
    assert source.content not in capsule.narrative_summary
    assert "不要改写原始历史" in first.rendered_context
    assert "summary" not in first.rendered_context.casefold()
    assert capsule.capsule_id == second.capsules[0].capsule_id
    assert capsule.goals[0].claim_id == second.capsules[0].goals[0].claim_id
    assert first.rendered_context == second.rendered_context


@pytest.mark.asyncio
async def test_render_capsule_matches_existing_backend_bytes() -> None:
    from astrcontinuum.compaction.rendering import render_capsule

    source = event(1, "继续实现后台 worker，不要改写原始历史。")
    output = await backend(
        RecordingGenerator(extraction(source.event_id, "不要改写原始历史"))
    ).compile(request((source,)))
    capsule = output.capsules[0]

    assert (
        render_capsule(capsule).encode("utf-8")
        == (
            f"[CAPSULE {capsule.capsule_id} EVENTS 1-1]\nGOAL: 不要改写原始历史 [source:event-1]"
        ).encode()
    )


@pytest.mark.asyncio
async def test_backend_preserves_base_capsules_and_compiles_each_segment() -> None:
    first_event = event(1, "第一条目标")
    first_output = await backend(
        RecordingGenerator(extraction(first_event.event_id, "第一条目标"))
    ).compile(request((first_event,)))
    second_event = event(2, "第二条约束")
    generator = RecordingGenerator(extraction(second_event.event_id, "第二条约束"))
    compiler = backend(generator)

    output = await compiler.compile(request((second_event,), base_capsules=first_output.capsules))

    assert output.capsules[0] is first_output.capsules[0]
    assert len(output.capsules) == 2
    assert output.capsules[1].covered_event_start == 2
    assert "第一条目标" in output.rendered_context
    assert "第二条约束" in output.rendered_context


@pytest.mark.asyncio
async def test_backend_rejects_paraphrased_quote_with_content_free_error() -> None:
    source = event(1, "必须保留这段精确原文")
    secret_paraphrase = "PRIVATE PARAPHRASED OUTPUT"
    compiler = backend(RecordingGenerator(extraction(source.event_id, secret_paraphrase)))

    with pytest.raises(ExtractiveCompilerError) as caught:
        await compiler.compile(request((source,)))

    assert caught.value.code == "EXTRACTIVE_QUOTE_NOT_FOUND"
    assert str(caught.value) == "EXTRACTIVE_QUOTE_NOT_FOUND"
    assert secret_paraphrase not in str(caught.value)


@pytest.mark.asyncio
async def test_backend_rejects_missing_event_acknowledgement_and_extra_summary() -> None:
    first = event(1, "one")
    second = event(2, "two")
    missing = backend(
        RecordingGenerator(extraction(first.event_id, "one", acknowledged=(first.event_id,)))
    )

    with pytest.raises(ExtractiveCompilerError) as missing_error:
        await missing.compile(request((first, second)))
    assert missing_error.value.code == "EXTRACTIVE_EVENT_COVERAGE_MISMATCH"

    extra_summary = backend(
        RecordingGenerator(
            extraction(
                first.event_id,
                "one",
                extra={"summary": "model-authored narrative must not be accepted"},
            )
        )
    )
    with pytest.raises(ExtractiveCompilerError) as extra_error:
        await extra_summary.compile(request((first,)))
    assert extra_error.value.code == "EXTRACTIVE_OUTPUT_INVALID"


@pytest.mark.asyncio
async def test_backend_rejects_a_segment_that_selects_nothing_from_one_event() -> None:
    first = event(1, "第一条必须留下证据")
    second = event(2, "第二条也必须留下证据")
    compiler = backend(
        RecordingGenerator(
            extraction(
                first.event_id,
                "第一条必须留下证据",
                acknowledged=(first.event_id, second.event_id),
            )
        )
    )

    with pytest.raises(ExtractiveCompilerError) as caught:
        await compiler.compile(request((first, second)))

    assert caught.value.code == "EXTRACTIVE_EVENT_SELECTION_MISSING"


def test_provider_registry_defaults_to_current_session_and_override_is_explicit() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    first = key("one")
    second = key("two")
    first_binding = binding("conversation-model")
    registry.remember(first, first_binding)

    assert registry.resolve(first) is first_binding
    with pytest.raises(CompilerBackendDeferred) as missing:
        registry.resolve(second)
    assert missing.value.code == "EXTRACTIVE_PROVIDER_UNAVAILABLE"

    override = SessionProviderRegistry(
        provider_override=binding("minimax-explicit", model_identity="MiniMax-M2"),
        max_entries=2,
    )
    assert override.resolve(second).context_limit == 10_000
    assert "minimax-explicit" not in repr(override.resolve(second))
    assert "MiniMax-M2" not in repr(override.resolve(second))


def test_configured_but_unavailable_override_never_falls_back_to_session_binding() -> None:
    session = key("configured-unavailable")
    registry = SessionProviderRegistry(
        provider_override=None,
        provider_override_configured=True,
        max_entries=2,
    )
    registry.remember(session, binding("conversation-model"))

    with pytest.raises(CompilerBackendDeferred) as caught:
        registry.resolve(session)

    assert caught.value.code == "EXTRACTIVE_PROVIDER_UNAVAILABLE"


def test_session_unavailable_replaces_stale_binding_and_later_success_recovers() -> None:
    session = key("refresh")
    registry = SessionProviderRegistry(max_entries=2)
    stale = binding("stale-provider")
    refreshed = binding("refreshed-provider")
    registry.remember(session, stale)

    registry.remember_unavailable(session)

    with pytest.raises(CompilerBackendDeferred) as caught:
        registry.resolve(session)
    assert caught.value.code == "EXTRACTIVE_PROVIDER_UNAVAILABLE"

    registry.remember(session, refreshed)
    assert registry.resolve(session) is refreshed


@pytest.mark.asyncio
async def test_provider_binding_refreshes_between_jobs_but_is_frozen_within_one_job() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    first_binding = binding("provider-first")
    second_binding = binding("provider-second", model_identity="MiniMax-M2")
    registry.remember(key(), first_binding)
    events = (event(1, "one"), event(2, "two"))
    switched = False

    def refresh_after_preflight() -> None:
        nonlocal switched
        if not switched:
            switched = True
            registry.remember(key(), second_binding)

    generator = RecordingGenerator(
        extraction_for((events[0],)),
        extraction_for((events[1],)),
        extraction_for((events[0],)),
        extraction_for((events[1],)),
        before_call=refresh_after_preflight,
    )
    compiler = AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )

    await compiler.compile(request(events, max_events_per_segment=1))
    await compiler.compile(request(events, max_events_per_segment=1))

    assert tuple(call[0] for call in generator.calls) == (
        "provider-first",
        "provider-first",
        "provider-second",
        "provider-second",
    )


@pytest.mark.asyncio
async def test_preflight_counter_failure_replays_whole_request_before_provider_side_effect() -> (
    None
):
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-primary"))
    events = (event(1, "primary-one"), event(2, "primary-two"))
    primary = ProfiledCounter(OPENAI_O200K, fail_on=events[1].content)
    fallback = ProfiledCounter(BYTE_FALLBACK)
    generator = RecordingGenerator(
        extraction_for((events[0],)),
        extraction_for((events[1],)),
        before_call=lambda: (
            pytest.fail("provider called before complete primary preflight")
            if not primary.failed
            else None
        ),
    )
    compiler = AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: fallback if profile is BYTE_FALLBACK else primary,
    )

    output = await compiler.compile(request(events, max_events_per_segment=1))

    assert len(generator.calls) == 2
    assert all(
        events[0].content in text or events[1].content in text for text in fallback.texts[1::2]
    )
    assert output.fit_provenance.tokenizer_profile_id == BYTE_FALLBACK.profile_id
    assert output.fit_provenance.primary_result_discarded is True
    assert output.fitted_segments == request(events, max_events_per_segment=1).segments


@pytest.mark.asyncio
async def test_preflight_splits_only_on_atomic_event_group_boundaries() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-atomic", context_limit=8))
    events = (
        event(1, "user", event_type=ac.EventType.USER_MESSAGE),
        event(2, "tool-call", event_type=ac.EventType.TOOL_CALL),
        event(3, "tool-result", event_type=ac.EventType.TOOL_RESULT),
        event(4, "assistant", event_type=ac.EventType.ASSISTANT_MESSAGE),
    )
    generator = RecordingGenerator(
        extraction_for((events[0],)),
        extraction_for((events[1], events[2])),
        extraction_for((events[3],)),
    )
    compiler = AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )

    output = await compiler.compile(request(events))

    assert tuple(
        tuple(item.event_type for item in segment.events) for segment in output.fitted_segments
    ) == (
        (ac.EventType.USER_MESSAGE,),
        (ac.EventType.TOOL_CALL, ac.EventType.TOOL_RESULT),
        (ac.EventType.ASSISTANT_MESSAGE,),
    )
    assert all(
        segment.events[-1].event_type is not ac.EventType.TOOL_CALL
        for segment in output.fitted_segments
    )


@pytest.mark.asyncio
async def test_preflight_repairs_an_existing_segment_boundary_that_splits_tool_pair() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-cross-segment", context_limit=8))
    events = (
        event(1, "user", event_type=ac.EventType.USER_MESSAGE),
        event(2, "tool-call", event_type=ac.EventType.TOOL_CALL),
        event(3, "tool-result", event_type=ac.EventType.TOOL_RESULT),
        event(4, "assistant", event_type=ac.EventType.ASSISTANT_MESSAGE),
    )
    source_request = request(events, max_events_per_segment=2)
    assert source_request.segments[0].events[-1].event_type is ac.EventType.TOOL_CALL
    assert source_request.segments[1].events[0].event_type is ac.EventType.TOOL_RESULT
    generator = RecordingGenerator(
        extraction_for((events[0],)),
        extraction_for((events[1], events[2])),
        extraction_for((events[3],)),
    )
    compiler = AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )

    output = await compiler.compile(source_request)

    fitted_event_groups = tuple(
        tuple(item.event_type for item in segment.events) for segment in output.fitted_segments
    )
    assert (ac.EventType.TOOL_CALL, ac.EventType.TOOL_RESULT) in fitted_event_groups
    assert all(
        segment.events[-1].event_type is not ac.EventType.TOOL_CALL
        for segment in output.fitted_segments
    )


@pytest.mark.asyncio
async def test_fitted_segment_token_cost_remains_the_canonical_lane_sum() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-canonical-cost", context_limit=8))
    events = (
        event(1, "user", event_type=ac.EventType.USER_MESSAGE),
        event(2, "tool-call", event_type=ac.EventType.TOOL_CALL),
        event(3, "tool-result", event_type=ac.EventType.TOOL_RESULT),
        event(4, "assistant", event_type=ac.EventType.ASSISTANT_MESSAGE),
    )
    canonical_counts = {
        events[0].event_id: 10,
        events[1].event_id: 20,
        events[2].event_id: 30,
        events[3].event_id: 40,
    }
    compiler = AstrBotExtractiveCompilerBackend(
        generator=RecordingGenerator(
            extraction_for((events[0],)),
            extraction_for((events[1], events[2])),
            extraction_for((events[3],)),
        ),
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )

    output = await compiler.compile(request(events, canonical_counts=canonical_counts))

    assert tuple(segment.token_cost for segment in output.fitted_segments) == (10, 50, 40)


@pytest.mark.asyncio
async def test_router_tokenizer_failure_replays_the_complete_byte_preflight() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-route-fallback"))
    events = (event(1, "route-one"), event(2, "route-two"))
    route_failed = False
    fallback = ProfiledCounter(BYTE_FALLBACK)

    class ExplodingRouter:
        def route(self, _model_identity: object) -> object:
            nonlocal route_failed
            route_failed = True
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_FAILED)

    generator = RecordingGenerator(
        extraction_for((events[0],)),
        extraction_for((events[1],)),
        before_call=lambda: (
            None if route_failed else pytest.fail("provider called before route fallback preflight")
        ),
    )
    compiler = AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=ExplodingRouter(),
        counter_provider=lambda profile: fallback,
    )

    output = await compiler.compile(request(events, max_events_per_segment=1))

    assert len(generator.calls) == 2
    assert output.fit_provenance.tokenizer_profile_id == BYTE_FALLBACK.profile_id
    assert output.fit_provenance.primary_result_discarded is True


@pytest.mark.asyncio
async def test_provider_binding_effective_cap_controls_compaction_input_limit() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-effective-cap", context_limit=8))
    events = (event(1, "one"), event(2, "two"), event(3, "three"))
    compiler = AstrBotExtractiveCompilerBackend(
        generator=RecordingGenerator(
            extraction_for((events[0], events[1])),
            extraction_for((events[2],)),
        ),
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )

    output = await compiler.compile(request(events, token_ceiling=100))

    assert tuple(len(segment.events) for segment in output.fitted_segments) == (2, 1)


@pytest.mark.asyncio
async def test_indivisible_over_budget_tool_pair_defers_before_provider_call() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-too-large", context_limit=7))
    events = (
        event(1, "tool-call", event_type=ac.EventType.TOOL_CALL),
        event(2, "tool-result", event_type=ac.EventType.TOOL_RESULT),
    )
    generator = RecordingGenerator()
    compiler = AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )

    with pytest.raises(CompilerBackendDeferred) as caught:
        await compiler.compile(request(events))

    assert caught.value.code == "COMPACTION_INPUT_TOO_LARGE"
    assert generator.calls == []


@pytest.mark.asyncio
async def test_compiler_returns_backend_fitted_segments_and_profile_provenance() -> None:
    registry = SessionProviderRegistry(max_entries=2)
    registry.remember(key(), binding("provider-candidate", context_limit=8))
    events = (
        event(1, "user", event_type=ac.EventType.USER_MESSAGE),
        event(2, "tool-call", event_type=ac.EventType.TOOL_CALL),
        event(3, "tool-result", event_type=ac.EventType.TOOL_RESULT),
        event(4, "assistant", event_type=ac.EventType.ASSISTANT_MESSAGE),
    )
    compiler = AstrBotExtractiveCompilerBackend(
        generator=RecordingGenerator(
            extraction_for((events[0],)),
            extraction_for((events[1], events[2])),
            extraction_for((events[3],)),
        ),
        providers=registry,
        compatibility_counter=LengthCounter(),
        tokenizer_router=TokenizerRouter(lambda _model: "o200k_base"),
        counter_provider=lambda profile: ProfiledCounter(profile),
    )

    candidate = await ac.compile_candidate(
        base_snapshot=None,
        base_capsules=(),
        source_events=events,
        event_token_counts={item.event_id: 1 for item in events},
        target_high_water_mark=events[-1].sequence,
        token_ceiling=10_000,
        backend=compiler,
        compatibility_counter=LengthCounter(),
        now=NOW + timedelta(minutes=1),
        segmenter_config=ac.SegmenterConfig(max_tokens_per_segment=10_000),
    )

    assert tuple(
        tuple(item.event_type for item in segment.events) for segment in candidate.segments
    ) == (
        (ac.EventType.USER_MESSAGE,),
        (ac.EventType.TOOL_CALL, ac.EventType.TOOL_RESULT),
        (ac.EventType.ASSISTANT_MESSAGE,),
    )
    assert candidate.fit_provenance is not None
    assert candidate.fit_provenance.tokenizer_profile_id == OPENAI_O200K.profile_id


@pytest.mark.asyncio
async def test_empty_source_event_never_creates_a_synthetic_exact_quote() -> None:
    source = event(1, "")
    payload = {
        "acknowledged_event_ids": [source.event_id],
        "goals": [],
        "constraints": [],
        "decisions": [],
        "progress": [],
        "open_loops": [],
        "preferences": [],
        "entities": [],
        "emotional_context": [],
        "anchors": [],
    }
    compiler = backend(RecordingGenerator(json.dumps(payload)))

    output = await compiler.compile(request((source,)))

    assert output.capsules[0].exact_anchors == ()
    assert "[USER_MESSAGE/USER]" not in output.rendered_context
