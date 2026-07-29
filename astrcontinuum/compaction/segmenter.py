from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import islice

from ..domain.events import EventEnvelope
from ..runtime.tool_loop import durable_event_units
from .types import EventSegment, SegmentBoundaryReason, SegmenterConfig


def segment(
    events: Sequence[EventEnvelope],
    *,
    token_counts: Mapping[str, int],
    config: SegmenterConfig,
    preferred_end_sequences: Sequence[int] = (),
) -> tuple[EventSegment, ...]:
    source_events = tuple(events)
    _validate_events(source_events)
    if set(token_counts) != {event.event_id for event in source_events}:
        raise ValueError("TOKEN_METRIC_MISSING")
    preferred_boundaries = _normalize_preferred_boundaries(
        source_events,
        preferred_end_sequences,
        config.max_preferred_boundaries,
    )

    units = durable_event_units(source_events)
    if units is None:
        raise ValueError("SEGMENTER_TOOL_LOOP_INVALID")

    segments: list[EventSegment] = []
    current: list[EventEnvelope] = []
    current_token_cost = 0

    def close_current(reason: SegmentBoundaryReason) -> None:
        nonlocal current_token_cost
        event_tuple = tuple(current)
        segments.append(
            EventSegment(
                start_sequence=event_tuple[0].sequence,
                end_sequence=event_tuple[-1].sequence,
                events=event_tuple,
                token_cost=current_token_cost,
                boundary_reason=reason,
            )
        )
        current.clear()
        current_token_cost = 0

    for index, unit in enumerate(units):
        unit_token_count = sum(token_counts[event.event_id] for event in unit)
        if current and len(current) + len(unit) > config.max_events_per_segment:
            close_current(SegmentBoundaryReason.MAX_EVENTS)
        elif current and current_token_cost + unit_token_count > config.max_tokens_per_segment:
            close_current(SegmentBoundaryReason.MAX_TOKENS)

        current.extend(unit)
        current_token_cost += unit_token_count
        is_last = index == len(units) - 1
        unit_is_oversized = (
            len(unit) > config.max_events_per_segment
            or unit_token_count > config.max_tokens_per_segment
        )

        if unit_is_oversized:
            close_current(SegmentBoundaryReason.OVERSIZED_EVENT)
        elif is_last:
            close_current(SegmentBoundaryReason.END_OF_INPUT)
        elif len(current) >= config.max_events_per_segment:
            close_current(SegmentBoundaryReason.MAX_EVENTS)
        elif current_token_cost >= config.max_tokens_per_segment:
            close_current(SegmentBoundaryReason.MAX_TOKENS)
        elif any(event.sequence in preferred_boundaries for event in unit):
            close_current(SegmentBoundaryReason.PREFERRED)

    return tuple(segments)


def _validate_events(events: tuple[EventEnvelope, ...]) -> None:
    if not events:
        raise ValueError("SEGMENTER_EMPTY_EVENTS")

    session_key = events[0].session_key
    first_sequence = events[0].sequence
    for offset, event in enumerate(events):
        if event.session_key != session_key:
            raise ValueError("SEGMENTER_SESSION_MISMATCH")
        if event.sequence != first_sequence + offset:
            raise ValueError("SEGMENTER_NONCONTIGUOUS_EVENTS")


def _normalize_preferred_boundaries(
    events: tuple[EventEnvelope, ...],
    preferred_end_sequences: Sequence[int],
    limit: int,
) -> frozenset[int]:
    first_sequence = events[0].sequence
    last_sequence = events[-1].sequence
    normalized = sorted(
        {
            sequence
            for sequence in islice(preferred_end_sequences, limit)
            if first_sequence <= sequence < last_sequence
        }
    )
    return frozenset(normalized)
