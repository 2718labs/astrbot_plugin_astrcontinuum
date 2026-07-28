from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import datetime, timezone
from itertools import chain
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
SURFACE_NAMES = (
    "SegmentBoundaryReason",
    "SegmenterConfig",
    "EventSegment",
    "segment",
)


class GuardedPreferredBoundaries(Sequence[int]):
    def __init__(self, values: tuple[int, ...], *, max_reads: int) -> None:
        self._values = values
        self._max_reads = max_reads
        self.read_count = 0

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int | slice) -> int | Sequence[int]:
        if isinstance(index, slice):
            raise TypeError("preferred boundary slice access is forbidden")
        if index >= len(self._values):
            raise IndexError(index)
        if self.read_count >= self._max_reads:
            raise AssertionError("preferred boundary read limit exceeded")
        self.read_count += 1
        return self._values[index]


def session_key(name: str = "session-1") -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=name,
        group_id=None,
        user_id=f"user-{name}",
        conversation_id=f"conversation-{name}",
        persona_id=None,
    )


def event(
    sequence: int,
    *,
    key: ac.SessionKey | None = None,
    event_type: ac.EventType = ac.EventType.USER_MESSAGE,
    token_count: int = 1,
    content: str | None = None,
) -> ac.EventEnvelope:
    return ac.EventEnvelope.create(
        event_id=f"event-{sequence}-{event_type.value}",
        session_key=key if key is not None else session_key(),
        sequence=sequence,
        event_type=event_type,
        content=content if content is not None else f"message {sequence}",
        idempotency_key=f"idempotency-{sequence}-{event_type.value}",
        token_count=token_count,
        created_at=NOW,
    )


def surface() -> tuple[type[Any], type[Any], type[Any], Any]:
    values = tuple(getattr(ac, name, None) for name in SURFACE_NAMES)
    missing = tuple(
        name for name, value in zip(SURFACE_NAMES, values, strict=True) if value is None
    )
    assert not missing, f"canonical segmenter surface is missing: {', '.join(missing)}"
    reason_type, config_type, segment_type, segment_function = values
    assert isinstance(reason_type, type)
    assert isinstance(config_type, type)
    assert isinstance(segment_type, type)
    assert callable(segment_function)
    return reason_type, config_type, segment_type, segment_function


def segment_ranges(segments: tuple[Any, ...]) -> tuple[tuple[int, int], ...]:
    return tuple((item.start_sequence, item.end_sequence) for item in segments)


def compatibility_counts(events: Sequence[ac.EventEnvelope]) -> dict[str, int]:
    return {item.event_id: item.token_count for item in events}


def test_segmenter_is_exported_on_both_canonical_surfaces() -> None:
    root_values = surface()
    compaction = importlib.import_module("astrcontinuum.compaction")

    assert tuple(getattr(compaction, name) for name in SURFACE_NAMES) == root_values


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "expected_code"),
    [
        ("max_events_per_segment", 0, "SEGMENTER_INVALID_MAX_EVENTS"),
        ("max_events_per_segment", -1, "SEGMENTER_INVALID_MAX_EVENTS"),
        ("max_events_per_segment", True, "SEGMENTER_INVALID_MAX_EVENTS"),
        ("max_events_per_segment", 1.0, "SEGMENTER_INVALID_MAX_EVENTS"),
        ("max_events_per_segment", "1", "SEGMENTER_INVALID_MAX_EVENTS"),
        ("max_tokens_per_segment", 0, "SEGMENTER_INVALID_MAX_TOKENS"),
        ("max_tokens_per_segment", -1, "SEGMENTER_INVALID_MAX_TOKENS"),
        ("max_tokens_per_segment", True, "SEGMENTER_INVALID_MAX_TOKENS"),
        ("max_tokens_per_segment", 1.0, "SEGMENTER_INVALID_MAX_TOKENS"),
        ("max_tokens_per_segment", "1", "SEGMENTER_INVALID_MAX_TOKENS"),
        (
            "max_preferred_boundaries",
            -1,
            "SEGMENTER_INVALID_MAX_PREFERRED_BOUNDARIES",
        ),
        (
            "max_preferred_boundaries",
            False,
            "SEGMENTER_INVALID_MAX_PREFERRED_BOUNDARIES",
        ),
        (
            "max_preferred_boundaries",
            True,
            "SEGMENTER_INVALID_MAX_PREFERRED_BOUNDARIES",
        ),
        (
            "max_preferred_boundaries",
            1.0,
            "SEGMENTER_INVALID_MAX_PREFERRED_BOUNDARIES",
        ),
        (
            "max_preferred_boundaries",
            "1",
            "SEGMENTER_INVALID_MAX_PREFERRED_BOUNDARIES",
        ),
    ],
)
def test_segmenter_config_rejects_non_integer_or_out_of_range_values(
    field_name: str,
    invalid_value: object,
    expected_code: str,
) -> None:
    _, config_type, _, _ = surface()

    with pytest.raises(ValueError, match=f"^{expected_code}$"):
        config_type(**{field_name: invalid_value})


def test_segment_rejects_empty_input() -> None:
    _, config_type, _, segment_function = surface()

    with pytest.raises(ValueError, match="^SEGMENTER_EMPTY_EVENTS$"):
        segment_function((), token_counts={}, config=config_type())


def test_segment_rejects_mixed_session_keys() -> None:
    _, config_type, _, segment_function = surface()
    events = (
        event(1, key=session_key("one")),
        event(2, key=session_key("two")),
    )

    with pytest.raises(ValueError, match="^SEGMENTER_SESSION_MISMATCH$"):
        segment_function(events, token_counts=compatibility_counts(events), config=config_type())


@pytest.mark.parametrize(
    "sequences",
    [
        (1, 3),
        (2, 1),
        (1, 1),
    ],
)
def test_segment_rejects_noncontiguous_or_reordered_sequences(
    sequences: tuple[int, ...],
) -> None:
    _, config_type, _, segment_function = surface()
    events = tuple(event(sequence) for sequence in sequences)

    with pytest.raises(ValueError, match="^SEGMENTER_NONCONTIGUOUS_EVENTS$"):
        segment_function(events, token_counts=compatibility_counts(events), config=config_type())


def test_segment_preserves_every_event_once_in_original_order() -> None:
    reason_type, config_type, _, segment_function = surface()
    events = tuple(event(sequence, token_count=sequence % 3) for sequence in range(7, 14))

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(max_events_per_segment=2, max_tokens_per_segment=100),
        preferred_end_sequences=(8, 10),
    )

    flattened = tuple(chain.from_iterable(item.events for item in segments))
    assert len(flattened) == len(events)
    assert all(actual is expected for actual, expected in zip(flattened, events, strict=True))
    assert tuple(item.sequence for item in flattened) == tuple(range(7, 14))
    assert all(item.start_sequence == item.events[0].sequence for item in segments)
    assert all(item.end_sequence == item.events[-1].sequence for item in segments)
    assert all(
        item.token_cost == sum(event.token_count for event in item.events) for item in segments
    )
    assert all(isinstance(item.boundary_reason, reason_type) for item in segments)


def test_preferred_boundaries_are_deduplicated_sorted_and_out_of_range_values_are_ignored() -> None:
    _, config_type, _, segment_function = surface()
    events = tuple(event(sequence) for sequence in range(10, 18))
    preferred = (16, 12, 99, 14, 12, 9)

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(max_events_per_segment=20, max_tokens_per_segment=100),
        preferred_end_sequences=preferred,
    )

    assert segment_ranges(segments) == ((10, 12), (13, 14), (15, 16), (17, 17))


def test_preferred_boundaries_are_bounded_after_normalization() -> None:
    _, config_type, _, segment_function = surface()
    events = tuple(event(sequence) for sequence in range(10, 18))

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(
            max_events_per_segment=20,
            max_tokens_per_segment=100,
            max_preferred_boundaries=2,
        ),
        preferred_end_sequences=(12, 14, 16),
    )

    assert segment_ranges(segments) == ((10, 12), (13, 14), (15, 17))


def test_preferred_boundary_item_reads_are_bounded_before_normalization() -> None:
    _, config_type, _, segment_function = surface()
    events = tuple(event(sequence) for sequence in range(1, 9))
    preferred = GuardedPreferredBoundaries((4, 2, 6, 7), max_reads=2)

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(
            max_events_per_segment=20,
            max_tokens_per_segment=100,
            max_preferred_boundaries=2,
        ),
        preferred_end_sequences=preferred,
    )

    assert preferred.read_count == 2
    assert segment_ranges(segments) == ((1, 2), (3, 4), (5, 8))


def test_zero_preferred_boundary_limit_is_valid_and_reads_no_items() -> None:
    _, config_type, _, segment_function = surface()
    events = tuple(event(sequence) for sequence in range(1, 5))
    preferred = GuardedPreferredBoundaries((1, 2, 3), max_reads=0)

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(
            max_events_per_segment=20,
            max_tokens_per_segment=100,
            max_preferred_boundaries=0,
        ),
        preferred_end_sequences=preferred,
    )

    assert preferred.read_count == 0
    assert segment_ranges(segments) == ((1, 4),)


def test_soft_boundary_keeps_adjacent_tool_call_and_result_together() -> None:
    _, config_type, _, segment_function = surface()
    events = (
        event(1),
        event(2, event_type=ac.EventType.TOOL_CALL),
        event(3, event_type=ac.EventType.TOOL_RESULT),
        event(4, event_type=ac.EventType.ASSISTANT_MESSAGE),
    )

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(max_events_per_segment=10, max_tokens_per_segment=100),
        preferred_end_sequences=(2,),
    )

    assert segment_ranges(segments) == ((1, 3), (4, 4))
    assert segments[0].events[-2:] == events[1:3]


def test_hard_event_limit_may_split_a_tool_pair() -> None:
    reason_type, config_type, _, segment_function = surface()
    events = (
        event(1, event_type=ac.EventType.TOOL_CALL),
        event(2, event_type=ac.EventType.TOOL_RESULT),
    )

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(max_events_per_segment=1, max_tokens_per_segment=100),
        preferred_end_sequences=(1,),
    )

    assert segment_ranges(segments) == ((1, 1), (2, 2))
    assert segments[0].boundary_reason is reason_type.MAX_EVENTS


def test_hard_event_limit_bounds_each_segment() -> None:
    reason_type, config_type, _, segment_function = surface()
    events = tuple(event(sequence) for sequence in range(1, 6))

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(max_events_per_segment=2, max_tokens_per_segment=100),
    )

    assert segment_ranges(segments) == ((1, 2), (3, 4), (5, 5))
    assert all(len(item.events) <= 2 for item in segments)
    assert tuple(item.boundary_reason for item in segments[:-1]) == (
        reason_type.MAX_EVENTS,
        reason_type.MAX_EVENTS,
    )


def test_hard_token_limit_uses_event_token_count_not_content_length() -> None:
    reason_type, config_type, _, segment_function = surface()
    counts = (2, 3, 4, 1)
    events = tuple(
        event(sequence, token_count=count, content="长" * 10_000)
        for sequence, count in enumerate(counts, start=1)
    )

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(max_events_per_segment=10, max_tokens_per_segment=5),
    )

    assert segment_ranges(segments) == ((1, 2), (3, 4))
    assert tuple(item.token_cost for item in segments) == (5, 5)
    assert segments[0].boundary_reason is reason_type.MAX_TOKENS
    assert all(item.token_cost <= 5 for item in segments)


def test_single_oversized_event_is_isolated_and_preserved_verbatim() -> None:
    reason_type, config_type, _, segment_function = surface()
    oversized = event(2, token_count=11, content="EXACT TOOL PAYLOAD")
    events = (event(1, token_count=1), oversized, event(3, token_count=1))

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config_type(max_events_per_segment=10, max_tokens_per_segment=5),
    )

    assert segment_ranges(segments) == ((1, 1), (2, 2), (3, 3))
    assert segments[1].events == (oversized,)
    assert segments[1].events[0] is oversized
    assert segments[1].events[0].content == "EXACT TOOL PAYLOAD"
    assert segments[1].token_cost == 11
    assert segments[1].boundary_reason is reason_type.OVERSIZED_EVENT


def test_config_result_and_mutable_inputs_remain_immutable() -> None:
    _, config_type, _, segment_function = surface()
    config = config_type(
        max_events_per_segment=3,
        max_tokens_per_segment=10,
        max_preferred_boundaries=3,
    )
    events = [event(sequence) for sequence in range(1, 5)]
    preferred = [3, 1, 3]
    original_events = tuple(events)
    original_preferred = tuple(preferred)

    segments = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config,
        preferred_end_sequences=preferred,
    )

    assert is_dataclass(config)
    assert is_dataclass(segments[0])
    assert events == list(original_events)
    assert preferred == list(original_preferred)
    assert isinstance(segments, tuple)
    assert all(isinstance(item.events, tuple) for item in segments)
    with pytest.raises(FrozenInstanceError):
        config.max_events_per_segment = 99
    with pytest.raises(FrozenInstanceError):
        segments[0].token_cost = 99


def test_segment_is_deterministic_for_identical_inputs() -> None:
    _, config_type, _, segment_function = surface()
    events = tuple(event(sequence, token_count=(sequence % 4) + 1) for sequence in range(3, 12))
    config = config_type(
        max_events_per_segment=4,
        max_tokens_per_segment=9,
        max_preferred_boundaries=3,
    )
    preferred = (10, 5, 7, 5)

    first = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config,
        preferred_end_sequences=preferred,
    )
    second = segment_function(
        events,
        token_counts=compatibility_counts(events),
        config=config,
        preferred_end_sequences=preferred,
    )

    assert first == second


def test_segment_requires_one_exact_metric_for_every_event() -> None:
    _, config_type, _, segment_function = surface()
    events = (event(1), event(2))

    with pytest.raises(ValueError, match="^TOKEN_METRIC_MISSING$"):
        segment_function(
            events,
            token_counts={events[0].event_id: 1},
            config=config_type(),
        )

    with pytest.raises(ValueError, match="^TOKEN_METRIC_MISSING$"):
        segment_function(
            events,
            token_counts={
                events[0].event_id: 1,
                events[1].event_id: 1,
                "unrelated-event": 1,
            },
            config=config_type(),
        )


def test_segment_boundaries_use_explicit_canonical_metrics_not_compatibility_counts() -> None:
    _, config_type, _, segment_function = surface()
    events = (
        event(1, token_count=100),
        event(2, token_count=100),
        event(3, token_count=100),
    )

    segments = segment_function(
        events,
        token_counts={item.event_id: count for item, count in zip(events, (2, 3, 4), strict=True)},
        config=config_type(max_events_per_segment=10, max_tokens_per_segment=5),
    )

    assert segment_ranges(segments) == ((1, 2), (3, 3))
    assert tuple(item.token_cost for item in segments) == (5, 4)
