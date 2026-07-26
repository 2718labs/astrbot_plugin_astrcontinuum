from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.events import EventEnvelope


class SegmentBoundaryReason(str, Enum):
    PREFERRED = "PREFERRED"
    MAX_EVENTS = "MAX_EVENTS"
    MAX_TOKENS = "MAX_TOKENS"
    OVERSIZED_EVENT = "OVERSIZED_EVENT"
    END_OF_INPUT = "END_OF_INPUT"


@dataclass(frozen=True, slots=True)
class SegmenterConfig:
    max_events_per_segment: int = 16
    max_tokens_per_segment: int = 4096
    max_preferred_boundaries: int = 128

    def __post_init__(self) -> None:
        if not _is_non_bool_int(self.max_events_per_segment) or self.max_events_per_segment <= 0:
            raise ValueError("SEGMENTER_INVALID_MAX_EVENTS")
        if not _is_non_bool_int(self.max_tokens_per_segment) or self.max_tokens_per_segment <= 0:
            raise ValueError("SEGMENTER_INVALID_MAX_TOKENS")
        if not _is_non_bool_int(self.max_preferred_boundaries) or self.max_preferred_boundaries < 0:
            raise ValueError("SEGMENTER_INVALID_MAX_PREFERRED_BOUNDARIES")


@dataclass(frozen=True, slots=True)
class EventSegment:
    start_sequence: int
    end_sequence: int
    events: tuple[EventEnvelope, ...]
    token_cost: int
    boundary_reason: SegmentBoundaryReason


def _is_non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
