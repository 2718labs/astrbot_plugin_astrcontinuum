from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain import (
    AnchorStatus,
    AnchorType,
    CapsuleAnchor,
    CapsuleClaim,
    CapsuleLevel,
    CapsuleQuality,
    ContextCapsuleEnvelope,
    Decision,
    Entity,
    SemanticStatus,
    SessionKey,
)
from ..runtime.types import TokenCounter
from .types import (
    CompilationRequest,
    CompilerBackendDeferred,
    CompilerOutput,
    EventSegment,
)

LLMGenerator = Callable[[str, str, str], Awaitable[str]]

_SYSTEM_PROMPT = """You extract source-verifiable state from a conversation segment.
Do not summarize or paraphrase. Every quote, rationale, alternative, rejection reason,
entity name, alias, and anchor must be copied verbatim from the declared event.
Every non-empty event must contribute at least one selected quote.
Return exactly one JSON object matching the requested closed shape. Do not add prose,
Markdown, a summary field, or keys that are not in the shape."""

_OUTPUT_SHAPE = {
    "acknowledged_event_ids": ["event-id"],
    "goals": [{"event_id": "event-id", "quote": "exact quote", "confidence": 1.0}],
    "constraints": [],
    "decisions": [
        {
            "event_id": "event-id",
            "quote": "exact quote",
            "confidence": 1.0,
            "rationale": "",
            "alternatives": [],
            "rejected_because": "",
        }
    ],
    "progress": [],
    "open_loops": [],
    "preferences": [],
    "entities": [
        {
            "event_id": "event-id",
            "quote": "exact canonical name",
            "kind": "person|project|file|other",
            "aliases": [],
        }
    ],
    "emotional_context": [],
    "anchors": [
        {
            "event_id": "event-id",
            "quote": "exact quote",
            "anchor_type": "name",
            "importance": 1.0,
        }
    ],
}


class ExtractiveCompilerError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _invalid(code: str) -> NoReturn:
    raise ExtractiveCompilerError(code)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Span(_ClosedModel):
    event_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class _DecisionSelection(_Span):
    rationale: str
    alternatives: tuple[str, ...]
    rejected_because: str


class _EntitySelection(_ClosedModel):
    event_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    aliases: tuple[str, ...]


class _AnchorSelection(_ClosedModel):
    event_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    anchor_type: AnchorType
    importance: float = Field(ge=0.0, le=1.0)


class _SegmentExtraction(_ClosedModel):
    acknowledged_event_ids: tuple[str, ...]
    goals: tuple[_Span, ...]
    constraints: tuple[_Span, ...]
    decisions: tuple[_DecisionSelection, ...]
    progress: tuple[_Span, ...]
    open_loops: tuple[_Span, ...]
    preferences: tuple[_Span, ...]
    entities: tuple[_EntitySelection, ...]
    emotional_context: tuple[_Span, ...]
    anchors: tuple[_AnchorSelection, ...]


class SessionProviderRegistry:
    """Bounded request-local provider affinity with an explicit override."""

    def __init__(
        self,
        *,
        provider_override: str | None = None,
        max_entries: int = 1_024,
    ) -> None:
        override = provider_override.strip() if isinstance(provider_override, str) else ""
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self._override = override or None
        self._max_entries = max_entries
        self._providers: OrderedDict[str, str] = OrderedDict()

    def remember(self, session_key: SessionKey, provider_id: str) -> None:
        if not isinstance(session_key, SessionKey):
            raise TypeError("session_key must be a SessionKey")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        key = session_key.session_key_hash
        self._providers.pop(key, None)
        self._providers[key] = provider_id.strip()
        while len(self._providers) > self._max_entries:
            self._providers.popitem(last=False)

    def resolve(self, session_key: SessionKey) -> str:
        if self._override is not None:
            return self._override
        provider_id = self._providers.get(session_key.session_key_hash)
        if provider_id is None:
            raise CompilerBackendDeferred("EXTRACTIVE_PROVIDER_UNAVAILABLE")
        self._providers.move_to_end(session_key.session_key_hash)
        return provider_id


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


def _parse_output(raw: object) -> _SegmentExtraction:
    if not isinstance(raw, str):
        _invalid("EXTRACTIVE_OUTPUT_INVALID")
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith("```"):
            _invalid("EXTRACTIVE_OUTPUT_INVALID")
        text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
        return _SegmentExtraction.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
        _invalid("EXTRACTIVE_OUTPUT_INVALID")


def _exact_text(
    event_contents: dict[str, str],
    event_id: str,
    quote: str,
    *,
    allow_empty: bool = False,
) -> str:
    if allow_empty and quote == "":
        return ""
    content = event_contents.get(event_id)
    if content is None:
        _invalid("EXTRACTIVE_SOURCE_EVENT_UNKNOWN")
    if not quote or quote not in content:
        _invalid("EXTRACTIVE_QUOTE_NOT_FOUND")
    return quote


def _validate_extraction(
    extraction: _SegmentExtraction,
    segment: EventSegment,
) -> dict[str, str]:
    event_contents = {item.event_id: item.content for item in segment.events}
    expected_ids = tuple(item.event_id for item in segment.events)
    if extraction.acknowledged_event_ids != expected_ids:
        _invalid("EXTRACTIVE_EVENT_COVERAGE_MISMATCH")

    span_groups: tuple[Iterable[_Span], ...] = (
        extraction.goals,
        extraction.constraints,
        extraction.progress,
        extraction.open_loops,
        extraction.preferences,
        extraction.emotional_context,
    )
    selected_event_ids: set[str] = set()
    for group in span_groups:
        for span in group:
            _exact_text(event_contents, span.event_id, span.quote)
            selected_event_ids.add(span.event_id)
    for decision in extraction.decisions:
        _exact_text(event_contents, decision.event_id, decision.quote)
        selected_event_ids.add(decision.event_id)
        _exact_text(
            event_contents,
            decision.event_id,
            decision.rationale,
            allow_empty=True,
        )
        _exact_text(
            event_contents,
            decision.event_id,
            decision.rejected_because,
            allow_empty=True,
        )
        for alternative in decision.alternatives:
            _exact_text(event_contents, decision.event_id, alternative)
    for entity in extraction.entities:
        _exact_text(event_contents, entity.event_id, entity.quote)
        selected_event_ids.add(entity.event_id)
        for alias in entity.aliases:
            _exact_text(event_contents, entity.event_id, alias)
    for anchor in extraction.anchors:
        _exact_text(event_contents, anchor.event_id, anchor.quote)
        selected_event_ids.add(anchor.event_id)

    required_event_ids = {event_id for event_id, content in event_contents.items() if content}
    if not required_event_ids.issubset(selected_event_ids):
        _invalid("EXTRACTIVE_EVENT_SELECTION_MISSING")
    return event_contents


def _claim(
    *,
    category: str,
    item: _Span,
) -> CapsuleClaim:
    return CapsuleClaim(
        claim_id=_stable_id(
            category,
            {
                "event_id": item.event_id,
                "quote": item.quote,
            },
        ),
        text=item.quote,
        status=SemanticStatus.ACTIVE,
        confidence=item.confidence,
        source_event_ids=(item.event_id,),
    )


def _decision(item: _DecisionSelection) -> Decision:
    return Decision(
        decision_id=_stable_id(
            "decision",
            {
                "event_id": item.event_id,
                "quote": item.quote,
                "rationale": item.rationale,
                "alternatives": item.alternatives,
                "rejected_because": item.rejected_because,
            },
        ),
        text=item.quote,
        status=SemanticStatus.ACTIVE,
        confidence=item.confidence,
        source_event_ids=(item.event_id,),
        rationale=item.rationale,
        alternatives=item.alternatives,
        supersedes=(),
        rejected_because=item.rejected_because,
    )


def _entity(item: _EntitySelection) -> Entity:
    return Entity(
        entity_id=_stable_id(
            "entity",
            {
                "event_id": item.event_id,
                "quote": item.quote,
                "kind": item.kind,
                "aliases": item.aliases,
            },
        ),
        kind=item.kind,
        canonical_name=item.quote,
        aliases=item.aliases,
        source_event_ids=(item.event_id,),
    )


def _anchor(item: _AnchorSelection) -> CapsuleAnchor:
    return CapsuleAnchor(
        anchor_id=_stable_id(
            "anchor",
            {
                "event_id": item.event_id,
                "quote": item.quote,
                "type": item.anchor_type.value,
            },
        ),
        anchor_type=item.anchor_type,
        exact_text=item.quote,
        source_event_ids=(item.event_id,),
        status=AnchorStatus.ACTIVE,
        importance=item.importance,
    )


def _render_capsule(capsule: ContextCapsuleEnvelope) -> str:
    lines = [
        (
            f"[CAPSULE {capsule.capsule_id} "
            f"EVENTS {capsule.covered_event_start}-{capsule.covered_event_end}]"
        )
    ]
    claim_groups = (
        ("GOAL", capsule.goals),
        ("CONSTRAINT", capsule.constraints),
        ("PROGRESS", capsule.progress),
        ("OPEN_LOOP", capsule.open_loops),
        ("PREFERENCE", capsule.preferences),
        ("EMOTIONAL_CONTEXT", capsule.emotional_context),
    )
    for label, group in claim_groups:
        lines.extend(
            f"{label}: {item.text} [source:{','.join(item.source_event_ids)}]"
            for item in group
            if item.status is SemanticStatus.ACTIVE
        )
    for item in capsule.decisions:
        if item.status is not SemanticStatus.ACTIVE:
            continue
        lines.append(f"DECISION: {item.text} [source:{','.join(item.source_event_ids)}]")
        if item.rationale:
            lines.append(f"RATIONALE: {item.rationale}")
        lines.extend(f"ALTERNATIVE: {value}" for value in item.alternatives)
        if item.rejected_because:
            lines.append(f"REJECTED_BECAUSE: {item.rejected_because}")
    lines.extend(
        (f"ENTITY: {item.canonical_name} ({item.kind}) [source:{','.join(item.source_event_ids)}]")
        for item in capsule.entities
    )
    lines.extend(
        (
            f"EXACT_{item.anchor_type.value.upper()}: {item.exact_text} "
            f"[source:{','.join(item.source_event_ids)}]"
        )
        for item in capsule.exact_anchors
        if item.status is AnchorStatus.ACTIVE
    )
    return "\n".join(lines)


def _fallback_anchor(
    segment: EventSegment,
    event_contents: dict[str, str],
) -> CapsuleAnchor | None:
    event = segment.events[-1]
    content = event_contents[event.event_id]
    quote = content[-512:] if len(content) > 512 else content
    if not quote:
        return None
    return CapsuleAnchor(
        anchor_id=_stable_id(
            "anchor",
            {
                "event_id": event.event_id,
                "quote": quote,
                "type": AnchorType.MANUAL.value,
            },
        ),
        anchor_type=AnchorType.MANUAL,
        exact_text=quote,
        source_event_ids=(event.event_id,),
        status=AnchorStatus.ACTIVE,
        importance=0.5,
    )


class AstrBotExtractiveCompilerBackend:
    """Compile exact model-selected spans into deterministic structured Capsules."""

    def __init__(
        self,
        *,
        generator: LLMGenerator,
        providers: SessionProviderRegistry,
        counter: TokenCounter,
    ) -> None:
        self._generator = generator
        self._providers = providers
        self._counter = counter

    async def compile(self, request: CompilationRequest) -> CompilerOutput:
        provider_id = self._providers.resolve(request.source_events[0].session_key)
        new_capsules: list[ContextCapsuleEnvelope] = []
        for segment in request.segments:
            prompt = self._segment_prompt(segment)
            raw = await self._generator(provider_id, _SYSTEM_PROMPT, prompt)
            extraction = _parse_output(raw)
            event_contents = _validate_extraction(extraction, segment)
            capsule = self._capsule(segment, extraction, event_contents)
            new_capsules.append(capsule)

        capsules = (*request.base_capsules, *new_capsules)
        rendered_context = "\n\n".join(_render_capsule(item) for item in capsules)
        if not rendered_context:
            _invalid("EXTRACTIVE_RENDER_EMPTY")
        return CompilerOutput(
            capsules=capsules,
            rendered_context=rendered_context,
        )

    @staticmethod
    def _segment_prompt(segment: EventSegment) -> str:
        payload = {
            "required_output_shape": _OUTPUT_SHAPE,
            "events": [
                {
                    "event_id": item.event_id,
                    "sequence": item.sequence,
                    "event_type": item.event_type.value,
                    "role": item.role.value,
                    "content": item.content,
                }
                for item in segment.events
            ],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _capsule(
        self,
        segment: EventSegment,
        extraction: _SegmentExtraction,
        event_contents: dict[str, str],
    ) -> ContextCapsuleEnvelope:
        anchors = tuple(_anchor(item) for item in extraction.anchors)
        has_structured_content = any(
            (
                extraction.goals,
                extraction.constraints,
                extraction.decisions,
                extraction.progress,
                extraction.open_loops,
                extraction.preferences,
                extraction.entities,
                extraction.emotional_context,
                anchors,
            )
        )
        if not has_structured_content:
            fallback = _fallback_anchor(segment, event_contents)
            anchors = (fallback,) if fallback is not None else ()

        event_ids = tuple(item.event_id for item in segment.events)
        identity = {
            "session": segment.events[0].session_key.session_key_hash,
            "start": segment.start_sequence,
            "end": segment.end_sequence,
            "extraction": extraction.model_dump(mode="json"),
        }
        capsule = ContextCapsuleEnvelope(
            capsule_id=_stable_id("capsule", identity),
            schema_version="1.0.0",
            level=CapsuleLevel.TASK,
            session_key=segment.events[0].session_key,
            covered_event_start=segment.start_sequence,
            covered_event_end=segment.end_sequence,
            source_event_ids=event_ids,
            goals=tuple(_claim(category="goal", item=item) for item in extraction.goals),
            constraints=tuple(
                _claim(category="constraint", item=item) for item in extraction.constraints
            ),
            decisions=tuple(_decision(item) for item in extraction.decisions),
            progress=tuple(_claim(category="progress", item=item) for item in extraction.progress),
            open_loops=tuple(
                _claim(category="open_loop", item=item) for item in extraction.open_loops
            ),
            preferences=tuple(
                _claim(category="preference", item=item) for item in extraction.preferences
            ),
            entities=tuple(_entity(item) for item in extraction.entities),
            emotional_context=tuple(
                _claim(category="emotional", item=item) for item in extraction.emotional_context
            ),
            exact_anchors=anchors,
            dependencies=(),
            narrative_summary=(
                f"Structured source map for events {segment.start_sequence}-{segment.end_sequence}."
            ),
            token_cost=0,
            quality=CapsuleQuality(
                mechanical_passed=True,
                source_coverage=1.0,
                anchor_recall=1.0,
                unsupported_critical_claims=0,
                coverage_gap=0,
            ),
            created_at=segment.events[-1].created_at,
        )
        try:
            token_cost = self._counter.count_text(_render_capsule(capsule))
        except Exception:  # noqa: BLE001 - pluggable counter details stay private
            _invalid("EXTRACTIVE_TOKEN_COUNTER_FAILURE")
        if isinstance(token_cost, bool) or not isinstance(token_cost, int) or token_cost < 0:
            _invalid("EXTRACTIVE_TOKEN_COUNTER_INVALID")
        return capsule.model_copy(update={"token_cost": token_cost})
