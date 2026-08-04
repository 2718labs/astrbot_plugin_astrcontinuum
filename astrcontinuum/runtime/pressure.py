from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class PressureSource(str, Enum):
    TRUSTED_PROVIDER_USAGE = "TRUSTED_PROVIDER_USAGE"
    CONSERVATIVE_ESTIMATE = "CONSERVATIVE_ESTIMATE"


@dataclass(frozen=True, slots=True)
class PressureConfig:
    context_limit: int
    reserved_output_and_tools: int
    compact_ratio: float = 0.75
    project_ratio: float = 0.80

    def __post_init__(self) -> None:
        if (
            isinstance(self.context_limit, bool)
            or not isinstance(self.context_limit, int)
            or self.context_limit < 1
        ):
            raise ValueError("context_limit must be a positive integer")
        if (
            isinstance(self.reserved_output_and_tools, bool)
            or not isinstance(self.reserved_output_and_tools, int)
            or self.reserved_output_and_tools < 0
            or self.reserved_output_and_tools >= self.context_limit
        ):
            raise ValueError(
                "reserved_output_and_tools must be a non-negative integer below context_limit"
            )
        ratios = (self.compact_ratio, self.project_ratio)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in ratios
        ):
            raise ValueError("pressure ratios must be finite numbers")
        if not 0 < self.compact_ratio <= self.project_ratio <= 1:
            raise ValueError("pressure ratios must satisfy 0 < compact <= project <= 1")

    @property
    def capacity(self) -> int:
        return self.context_limit - self.reserved_output_and_tools


@dataclass(frozen=True, slots=True)
class PressureDecision:
    used: int
    capacity: int
    compact_at: int
    project_at: int
    should_compact: bool
    should_project: bool
    source: PressureSource


def _usage(value: object, name: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def assess_pressure(
    *,
    trusted_token_usage: int | None,
    estimated_input_usage: int,
    current_input_cost: int,
    config: PressureConfig,
) -> PressureDecision:
    trusted = _usage(trusted_token_usage, "trusted_token_usage", optional=True)
    estimated = _usage(estimated_input_usage, "estimated_input_usage")
    current = _usage(current_input_cost, "current_input_cost")
    assert estimated is not None
    assert current is not None

    if trusted is not None and trusted > 0:
        used = trusted + current
        source = PressureSource.TRUSTED_PROVIDER_USAGE
    else:
        used = estimated
        source = PressureSource.CONSERVATIVE_ESTIMATE

    capacity = config.capacity
    compact_at = math.ceil(capacity * config.compact_ratio)
    project_at = math.ceil(capacity * config.project_ratio)
    return PressureDecision(
        used=used,
        capacity=capacity,
        compact_at=compact_at,
        project_at=project_at,
        should_compact=used >= compact_at,
        should_project=used >= project_at,
        source=source,
    )
