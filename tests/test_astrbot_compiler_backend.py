from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import astrcontinuum as ac
from astrcontinuum.compaction import (
    AstrBotExtractiveCompilerBackend,
    CompilerBackendDeferred,
    ExtractiveCompilerError,
    SessionProviderRegistry,
)

NOW = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)


class LengthCounter:
    def count_text(self, text: str) -> int:
        return len(text)


class RecordingGenerator:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(
        self,
        provider_id: str,
        system_prompt: str,
        prompt: str,
    ) -> str:
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
    creator = {
        ac.EventType.USER_MESSAGE: ac.EventEnvelope.create,
        ac.EventType.ASSISTANT_MESSAGE: ac.EventEnvelope.create,
    }[event_type]
    return creator(
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


def request(
    events: tuple[ac.EventEnvelope, ...],
    *,
    base_capsules: tuple[ac.ContextCapsuleEnvelope, ...] = (),
) -> ac.CompilationRequest:
    return ac.CompilationRequest(
        base_snapshot=None,
        base_capsules=base_capsules,
        source_events=events,
        segments=ac.segment(
            events,
            config=ac.SegmenterConfig(
                max_events_per_segment=16,
                max_tokens_per_segment=10_000,
            ),
        ),
        target_high_water_mark=events[-1].sequence,
        token_ceiling=10_000,
    )


def backend(
    generator: RecordingGenerator,
    *,
    provider_id: str = "minimax-compiler",
) -> AstrBotExtractiveCompilerBackend:
    registry = SessionProviderRegistry(max_entries=8)
    registry.remember(key(), provider_id)
    return AstrBotExtractiveCompilerBackend(
        generator=generator,
        providers=registry,
        counter=LengthCounter(),
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
    registry.remember(first, "conversation-model")

    assert registry.resolve(first) == "conversation-model"
    with pytest.raises(CompilerBackendDeferred) as missing:
        registry.resolve(second)
    assert missing.value.code == "EXTRACTIVE_PROVIDER_UNAVAILABLE"

    override = SessionProviderRegistry(
        provider_override="minimax-explicit",
        max_entries=2,
    )
    assert override.resolve(second) == "minimax-explicit"


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
