from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

import astrcontinuum.context_graph.closure as closure_module
from astrcontinuum.context_graph.closure import (
    ClosureError,
    close_dependency_closure,
    validate_dependency_closure,
)
from astrcontinuum.domain import EventEnvelope, EventType, SessionKey
from astrcontinuum.runtime.retrieval import select_candidates
from astrcontinuum.runtime.types import CandidateKind, RetrievalConfig, RuntimeSlot
from astrcontinuum.storage import RequestView
from tests.context_graph.helpers import candidate


def test_closure_adds_required_companions_and_keeps_a_tool_unit_indivisible() -> None:
    required = replace(
        candidate("required", required=True, text="required"),
        kind=CandidateKind.CONSTRAINT,
        capsule_id="capsule",
    )
    companion = replace(
        candidate("companion", text="dependency"),
        kind=CandidateKind.DEPENDENCY,
        capsule_id="capsule",
    )
    tool_round = replace(
        candidate("tool-round", text="[TOOL_CALL/TOOL event-call]\n{}\n[assistant]"),
        kind=CandidateKind.RAW_EVENT,
        slot=RuntimeSlot.RECENT_RAW,
        event_sequence=10,
        source_event_ids=("tool-call", "tool-result", "assistant"),
    )
    universe = (required, companion, tool_round)

    closure = close_dependency_closure((tool_round,), universe, max_blocks=8)

    assert tuple(item.block_id for item in closure.blocks) == (
        "required",
        "companion",
        "tool-round",
    )
    assert validate_dependency_closure(closure.blocks, universe, max_blocks=8) is True
    assert validate_dependency_closure((required, tool_round), universe, max_blocks=8) is False


def test_real_retrieval_blocks_keep_a_closed_tool_continuation_as_one_unit() -> None:
    key = SessionKey(
        platform_instance_id="platform",
        message_type="friend",
        session_id="session",
        group_id=None,
        user_id="user",
        conversation_id="conversation",
        persona_id=None,
    )
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)

    def tool_event(sequence: int, event_type: EventType, kind: str) -> EventEnvelope:
        return EventEnvelope.create(
            event_id=f"event-{sequence}",
            session_key=key,
            sequence=sequence,
            event_type=event_type,
            content=f'{{"arguments":{{}},"kind":"{kind}","tool":"weather"}}',
            idempotency_key=f"tool-{sequence}",
            token_count=1,
            created_at=now,
        )

    view = RequestView(
        session_key=key,
        snapshot=None,
        memberships=(),
        pointer_version=0,
        covered_event_end=0,
        high_water_mark=3,
        delta=(
            tool_event(1, EventType.TOOL_CALL, "call"),
            tool_event(2, EventType.TOOL_RESULT, "result"),
            tool_event(3, EventType.ASSISTANT_MESSAGE, "assistant"),
        ),
    )
    blocks = select_candidates(view, "", RetrievalConfig())
    continuation = next(block for block in blocks if block.kind is CandidateKind.RAW_EVENT)

    closure = close_dependency_closure((continuation,), blocks, max_blocks=8)

    assert closure.blocks == (continuation,)
    assert continuation.source_event_ids == ("event-1", "event-2", "event-3")


def test_closure_is_bounded_before_recursive_expansion() -> None:
    required = tuple(
        replace(candidate(f"required-{index}", required=True), capsule_id="capsule")
        for index in range(3)
    )

    with pytest.raises(ClosureError, match="DEPENDENCY_CLOSURE_CAPACITY_EXCEEDED"):
        close_dependency_closure((), required, max_blocks=2)


def test_removable_groups_keep_dependency_components_and_tool_units_atomic() -> None:
    dependency_left = replace(
        candidate("dependency-left"),
        kind=CandidateKind.DEPENDENCY,
        capsule_id="capsule-dependency",
    )
    dependency_right = replace(
        candidate("dependency-right"),
        kind=CandidateKind.DEPENDENCY,
        capsule_id="capsule-dependency",
    )
    tool_round = replace(
        candidate("tool-round"),
        kind=CandidateKind.RAW_EVENT,
        slot=RuntimeSlot.RECENT_RAW,
        event_sequence=10,
        source_event_ids=("tool-call", "tool-result", "assistant"),
    )
    required = candidate("required", required=True)

    groups = closure_module.removable_dependency_groups(
        (
            dependency_left,
            dependency_right,
            tool_round,
            required,
        )
    )
    group_ids = tuple(tuple(block.block_id for block in group) for group in groups)

    assert ("dependency-left", "dependency-right") in group_ids
    assert ("tool-round",) in group_ids
    assert all(not any(block.required for block in group) for group in groups)
