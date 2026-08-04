from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from astrcontinuum.adapters.astrbot import canonical_tool_metadata
from astrcontinuum.domain import EventEnvelope, EventType, SessionKey
from astrcontinuum.runtime.tool_loop import (
    ToolLoopState,
    durable_event_units,
    evaluate_tool_loop,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
SESSION_KEY = SessionKey(
    platform_instance_id="astrbot-local",
    message_type="friend_message",
    session_id="tool-loop-session",
    group_id=None,
    user_id="user-1",
    conversation_id="conversation-tool-loop",
    persona_id=None,
)


def _event(
    sequence: int,
    event_type: EventType,
    content: str,
) -> EventEnvelope:
    return EventEnvelope.create(
        event_id=f"event-{sequence}",
        session_key=SESSION_KEY,
        sequence=sequence,
        event_type=event_type,
        content=content,
        idempotency_key=f"idempotency-{sequence}",
        token_count=0,
        created_at=NOW,
    )


def _tool_metadata(
    kind: str,
    tool: object,
    arguments: object,
    **extra: object,
) -> str:
    return json.dumps(
        {"kind": kind, "tool": tool, "arguments": arguments, **extra},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_stable_history_with_user_and_assistant_events_is_stable() -> None:
    assessment = evaluate_tool_loop(
        (
            _event(1, EventType.USER_MESSAGE, "visible user"),
            _event(2, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        )
    )

    assert assessment.state is ToolLoopState.STABLE
    assert assessment.pending == ()


def test_matching_call_result_and_assistant_complete_one_tool_loop() -> None:
    metadata = _tool_metadata("call", "search", {"query": "weather"})
    result_metadata = _tool_metadata("result", "search", {"query": "weather"}, result="ok")

    assessment = evaluate_tool_loop(
        (
            _event(1, EventType.TOOL_CALL, metadata),
            _event(2, EventType.TOOL_RESULT, result_metadata),
            _event(3, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        )
    )

    assert assessment.state is ToolLoopState.STABLE
    assert assessment.pending == ()


def test_parallel_results_match_durable_multiset_out_of_order() -> None:
    search_call = _tool_metadata("call", "search", {"query": "weather", "days": 2})
    lookup_call = _tool_metadata("call", "lookup", {"id": 7})
    lookup_result = _tool_metadata("result", "lookup", {"id": 7}, result="found")
    search_result = _tool_metadata(
        "result", "search", {"days": 2, "query": "weather"}, result="sunny"
    )

    assessment = evaluate_tool_loop(
        (
            _event(1, EventType.TOOL_CALL, search_call),
            _event(2, EventType.TOOL_CALL, lookup_call),
            _event(3, EventType.TOOL_RESULT, lookup_result),
            _event(4, EventType.TOOL_RESULT, search_result),
            _event(5, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        )
    )

    assert assessment.state is ToolLoopState.STABLE
    assert assessment.pending == ()


def test_duplicate_fingerprint_requires_matching_result_count() -> None:
    call = _tool_metadata("call", "search", {"query": "weather"})
    result = _tool_metadata("result", "search", {"query": "weather"}, result="ok")

    partial = evaluate_tool_loop(
        (
            _event(1, EventType.TOOL_CALL, call),
            _event(2, EventType.TOOL_CALL, call),
            _event(3, EventType.TOOL_RESULT, result),
        )
    )
    complete = evaluate_tool_loop(
        (
            _event(1, EventType.TOOL_CALL, call),
            _event(2, EventType.TOOL_CALL, call),
            _event(3, EventType.TOOL_RESULT, result),
            _event(4, EventType.TOOL_RESULT, result),
        )
    )

    assert partial.state is ToolLoopState.AWAITING_RESULTS
    assert partial.pending[0].count == 1
    assert complete.state is ToolLoopState.AWAITING_ASSISTANT
    assert complete.pending == ()


def test_awaiting_assistant_can_begin_the_next_tool_round() -> None:
    first_call = _tool_metadata("call", "first", {"value": 1})
    first_result = _tool_metadata("result", "first", {"value": 1}, result="done")
    second_call = _tool_metadata("call", "second", {"value": 2})
    second_result = _tool_metadata("result", "second", {"value": 2}, result="done")

    assessment = evaluate_tool_loop(
        (
            _event(1, EventType.TOOL_CALL, first_call),
            _event(2, EventType.TOOL_RESULT, first_result),
            _event(3, EventType.TOOL_CALL, second_call),
            _event(4, EventType.TOOL_RESULT, second_result),
            _event(5, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        )
    )

    assert assessment.state is ToolLoopState.STABLE


@pytest.mark.parametrize(
    "events",
    [
        (
            _event(
                1,
                EventType.TOOL_RESULT,
                _tool_metadata("result", "search", {"query": "weather"}, result="orphan"),
            ),
        ),
        (
            _event(1, EventType.TOOL_CALL, _tool_metadata("call", "search", {"query": "weather"})),
            _event(2, EventType.USER_MESSAGE, "interleaved user"),
        ),
        (
            _event(1, EventType.TOOL_CALL, _tool_metadata("call", "search", {"query": "weather"})),
            _event(2, EventType.ASSISTANT_MESSAGE, "interleaved assistant"),
        ),
        (
            _event(
                1,
                EventType.TOOL_CALL,
                _tool_metadata("result", "search", {"query": "weather"}),
            ),
        ),
        (_event(1, EventType.TOOL_CALL, "{not valid json"),),
        (_event(1, EventType.TOOL_CALL, '{"kind":"call","truncated":true,"utf8_bytes":8192}'),),
    ],
)
def test_invalid_tool_history_fails_closed(events: tuple[EventEnvelope, ...]) -> None:
    assessment = evaluate_tool_loop(events)

    assert assessment.state is ToolLoopState.INVALID
    assert assessment.pending == ()


def test_duplicate_or_mismatched_result_is_invalid_and_absorbing() -> None:
    call = _tool_metadata("call", "search", {"query": "weather"})
    result = _tool_metadata("result", "search", {"query": "weather"}, result="ok")

    assessment = evaluate_tool_loop(
        (
            _event(1, EventType.TOOL_CALL, call),
            _event(2, EventType.TOOL_RESULT, result),
            _event(3, EventType.TOOL_RESULT, result),
            _event(4, EventType.ASSISTANT_MESSAGE, "must not recover"),
        )
    )

    assert assessment.state is ToolLoopState.INVALID
    assert assessment.pending == ()


@pytest.mark.parametrize(
    "arguments",
    [
        {"nested": {"truncated": True, "text": "prefix"}},
        {"nested": {"$truncated_items": 1}},
        {"nested": [{"truncated_items": 1}]},
    ],
)
def test_lossy_metadata_markers_are_rejected_at_any_nesting_depth(arguments: object) -> None:
    assessment = evaluate_tool_loop(
        (_event(1, EventType.TOOL_CALL, _tool_metadata("call", "search", arguments)),)
    )

    assert assessment.state is ToolLoopState.INVALID


def test_bounded_argument_prefix_collision_cannot_pair() -> None:
    left = canonical_tool_metadata("search", {"query": "a" * 600 + "left"})
    right = canonical_tool_metadata(
        "search",
        {"query": "a" * 600 + "right"},
        tool_result="result",
    )

    assessment = evaluate_tool_loop(
        (
            _event(1, EventType.TOOL_CALL, left),
            _event(2, EventType.TOOL_RESULT, right),
            _event(3, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        )
    )

    assert assessment.state is ToolLoopState.INVALID


def test_tool_name_must_be_a_nonempty_string() -> None:
    assessment = evaluate_tool_loop(
        (_event(1, EventType.TOOL_CALL, _tool_metadata("call", {"name": "search"}, {})),)
    )

    assert assessment.state is ToolLoopState.INVALID


def test_sequence_must_be_contiguous_but_may_start_above_one() -> None:
    complete = evaluate_tool_loop(
        (
            _event(9, EventType.USER_MESSAGE, "visible user"),
            _event(10, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        )
    )
    gap = evaluate_tool_loop(
        (
            _event(9, EventType.USER_MESSAGE, "visible user"),
            _event(11, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        )
    )

    assert complete.state is ToolLoopState.STABLE
    assert gap.state is ToolLoopState.INVALID


def test_full_session_key_is_compared_even_if_hashes_collide() -> None:
    other_key = SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="different-session",
        group_id=None,
        user_id="user-1",
        conversation_id="conversation-tool-loop",
        persona_id=None,
    )
    first = _event(1, EventType.USER_MESSAGE, "visible user")
    second = _event(2, EventType.ASSISTANT_MESSAGE, "visible assistant").model_copy(
        update={"session_key": other_key}
    )

    with patch.object(SessionKey, "session_key_hash", new=property(lambda _self: "collision")):
        assessment = evaluate_tool_loop((first, second))

    assert assessment.state is ToolLoopState.INVALID


def test_iterator_failure_is_invalid_not_an_escape() -> None:
    def events() -> object:
        yield _event(1, EventType.USER_MESSAGE, "visible user")
        raise RuntimeError("broken durable cursor")

    assessment = evaluate_tool_loop(events())  # type: ignore[arg-type]

    assert assessment.state is ToolLoopState.INVALID


def test_iter_construction_failure_is_invalid_and_has_no_durable_units() -> None:
    class BrokenIterable:
        def __iter__(self) -> object:
            raise RuntimeError("broken durable iterator construction")

    events = BrokenIterable()

    assessment = evaluate_tool_loop(events)  # type: ignore[arg-type]
    assert assessment.state is ToolLoopState.INVALID
    assert durable_event_units(events) is None  # type: ignore[arg-type]


def test_durable_units_keep_an_unordered_parallel_round_and_closing_assistant_atomic() -> None:
    events = (
        _event(1, EventType.USER_MESSAGE, "visible user"),
        _event(2, EventType.TOOL_CALL, _tool_metadata("call", "search", {"q": "a"})),
        _event(3, EventType.TOOL_CALL, _tool_metadata("call", "lookup", {"id": 7})),
        _event(
            4,
            EventType.TOOL_RESULT,
            _tool_metadata("result", "lookup", {"id": 7}, result="found"),
        ),
        _event(
            5,
            EventType.TOOL_RESULT,
            _tool_metadata("result", "search", {"q": "a"}, result="ok"),
        ),
        _event(6, EventType.ASSISTANT_MESSAGE, "visible assistant"),
        _event(7, EventType.USER_MESSAGE, "next visible user"),
    )

    units = durable_event_units(events)

    assert units is not None
    assert tuple(tuple(item.sequence for item in unit) for unit in units) == (
        (1,),
        (2, 3, 4, 5, 6),
        (7,),
    )


def test_durable_units_reject_open_or_invalid_tool_rounds_without_partial_output() -> None:
    open_round = (
        _event(1, EventType.TOOL_CALL, _tool_metadata("call", "search", {"q": "a"})),
        _event(
            2,
            EventType.TOOL_RESULT,
            _tool_metadata("result", "search", {"q": "a"}, result="ok"),
        ),
    )
    malformed = (_event(1, EventType.TOOL_CALL, "not-json"),)

    assert durable_event_units(open_round) is None
    assert durable_event_units(malformed) is None


def test_durable_units_use_empty_tuple_only_for_a_legal_empty_history() -> None:
    assert durable_event_units(()) == ()
