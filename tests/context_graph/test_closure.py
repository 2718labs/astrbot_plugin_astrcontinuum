from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

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


def test_closure_adds_required_companions_and_complete_tool_pairs() -> None:
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
    tool_call = replace(
        candidate("tool-call", text="[TOOL_CALL/TOOL event-call]\n{}"),
        kind=CandidateKind.RAW_EVENT,
        slot=RuntimeSlot.RECENT_RAW,
        event_sequence=10,
        event_type=EventType.TOOL_CALL,
        tool_name="weather",
    )
    tool_result = replace(
        candidate("tool-result", text="[TOOL_RESULT/TOOL event-result]\n{}"),
        kind=CandidateKind.RAW_EVENT,
        slot=RuntimeSlot.RECENT_RAW,
        event_sequence=11,
        event_type=EventType.TOOL_RESULT,
        tool_name="weather",
    )
    universe = (required, companion, tool_call, tool_result)

    closure = close_dependency_closure((tool_call,), universe, max_blocks=8)

    assert tuple(item.block_id for item in closure.blocks) == (
        "required",
        "companion",
        "tool-call",
        "tool-result",
    )
    assert validate_dependency_closure(closure.blocks, universe, max_blocks=8) is True
    assert validate_dependency_closure((required, tool_call), universe, max_blocks=8) is False


def test_real_retrieval_blocks_close_tool_call_and_result_as_one_pair() -> None:
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
        high_water_mark=2,
        delta=(
            tool_event(1, EventType.TOOL_CALL, "call"),
            tool_event(2, EventType.TOOL_RESULT, "result"),
        ),
    )
    blocks = select_candidates(view, "", RetrievalConfig())
    result = next(block for block in blocks if block.event_type is EventType.TOOL_RESULT)

    closure = close_dependency_closure((result,), blocks, max_blocks=8)

    assert tuple(block.event_type for block in closure.blocks) == (
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
    )


def test_closure_is_bounded_before_recursive_expansion() -> None:
    required = tuple(
        replace(candidate(f"required-{index}", required=True), capsule_id="capsule")
        for index in range(3)
    )

    with pytest.raises(ClosureError, match="DEPENDENCY_CLOSURE_CAPACITY_EXCEEDED"):
        close_dependency_closure((), required, max_blocks=2)
