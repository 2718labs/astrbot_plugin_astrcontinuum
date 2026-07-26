from __future__ import annotations

import pytest

from astrcontinuum.runtime import (
    PressureConfig,
    PressureSource,
    assess_pressure,
)


def config() -> PressureConfig:
    return PressureConfig(
        context_limit=100,
        reserved_output_and_tools=0,
        compact_ratio=0.75,
        project_ratio=0.80,
    )


def test_trusted_provider_usage_drives_compaction_and_projection() -> None:
    decision = assess_pressure(
        trusted_token_usage=80,
        estimated_input_usage=10,
        current_input_cost=1,
        config=config(),
    )

    assert decision.used == 81
    assert decision.capacity == 100
    assert decision.compact_at == 75
    assert decision.project_at == 80
    assert decision.should_compact is True
    assert decision.should_project is True
    assert decision.source is PressureSource.TRUSTED_PROVIDER_USAGE


def test_unknown_provider_usage_uses_complete_request_estimate() -> None:
    decision = assess_pressure(
        trusted_token_usage=0,
        estimated_input_usage=76,
        current_input_cost=4,
        config=config(),
    )

    assert decision.used == 76
    assert decision.should_compact is True
    assert decision.should_project is False
    assert decision.source is PressureSource.CONSERVATIVE_ESTIMATE


def test_below_threshold_does_not_schedule_or_project() -> None:
    decision = assess_pressure(
        trusted_token_usage=None,
        estimated_input_usage=74,
        current_input_cost=50,
        config=config(),
    )

    assert decision.used == 74
    assert decision.should_compact is False
    assert decision.should_project is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"context_limit": 0},
        {"reserved_output_and_tools": -1},
        {"reserved_output_and_tools": 100},
        {"compact_ratio": 0.0},
        {"compact_ratio": 0.81},
        {"project_ratio": 1.01},
    ],
)
def test_invalid_pressure_config_is_rejected(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "context_limit": 100,
        "reserved_output_and_tools": 0,
        "compact_ratio": 0.75,
        "project_ratio": 0.80,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        PressureConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("trusted", "estimated", "current"),
    [
        (True, 1, 1),
        (-1, 1, 1),
        (1, True, 1),
        (1, -1, 1),
        (1, 1, True),
        (1, 1, -1),
    ],
)
def test_invalid_usage_values_are_rejected(
    trusted: object,
    estimated: object,
    current: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        assess_pressure(
            trusted_token_usage=trusted,  # type: ignore[arg-type]
            estimated_input_usage=estimated,  # type: ignore[arg-type]
            current_input_cost=current,  # type: ignore[arg-type]
            config=config(),
        )
