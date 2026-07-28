from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from astrcontinuum.runtime.types import BudgetSafetyConfig
from astrcontinuum.tokenization import (
    OPENAI_CL100K,
    OPENAI_O200K,
    REFERENCE_O200K,
    AstrBotRequestMetadata,
    ContextLimitDecision,
    ContextLimitResolver,
    ContextLimitSource,
    RequestBudgetProfile,
    resolve_astrbot_request_metadata,
)


@pytest.mark.parametrize("configured", [True, False, None, "128000", -1])
def test_invalid_configured_context_limit_uses_safe_fallback(
    configured: object,
) -> None:
    assert ContextLimitResolver().resolve(
        configured_limit=configured,
        provider_limit=64000,
        request_model="gpt-4",
        provider_model="gpt-4",
    ) == ContextLimitDecision(
        128000,
        ContextLimitSource.AUTO_SAFE_FALLBACK,
        "CONTEXT_LIMIT_CONFIG_INVALID",
    )


def test_positive_configured_context_limit_is_manual() -> None:
    assert ContextLimitResolver().resolve(
        configured_limit=200000,
        provider_limit=None,
        request_model=None,
        provider_model=None,
    ) == ContextLimitDecision(
        200000,
        ContextLimitSource.MANUAL,
        "NONE",
    )


def test_context_limit_resolver_accepts_positional_config_and_defaults() -> None:
    assert ContextLimitResolver().resolve(-1) == ContextLimitDecision(
        128000,
        ContextLimitSource.AUTO_SAFE_FALLBACK,
        "CONTEXT_LIMIT_CONFIG_INVALID",
    )


@pytest.mark.parametrize("provider_limit", [None, True, False, 0, -1, "128000"])
def test_auto_mode_without_valid_provider_limit_uses_safe_fallback(
    provider_limit: object,
) -> None:
    assert ContextLimitResolver().resolve(
        configured_limit=0,
        provider_limit=provider_limit,
        request_model="gpt-4",
        provider_model="gpt-4",
    ) == ContextLimitDecision(
        128000,
        ContextLimitSource.AUTO_SAFE_FALLBACK,
        "CONTEXT_LIMIT_UNAVAILABLE",
    )


@pytest.mark.parametrize(
    ("request_model", "provider_model"),
    [
        ("request-model", "provider-model"),
        ("request-model", None),
        (" request-model ", "provider-model"),
    ],
)
def test_auto_mode_caps_provider_limit_when_request_overrides_provider_model(
    request_model: str, provider_model: str | None
) -> None:
    assert ContextLimitResolver().resolve(
        configured_limit=0,
        provider_limit=200000,
        request_model=request_model,
        provider_model=provider_model,
    ) == ContextLimitDecision(
        128000,
        ContextLimitSource.AUTO_SAFE_FALLBACK,
        "CONTEXT_LIMIT_UNAVAILABLE",
    )


@pytest.mark.parametrize("provider_limit", [64_000, 32_000])
def test_auto_mode_keeps_smaller_provider_limit_on_model_mismatch(
    provider_limit: int,
) -> None:
    assert ContextLimitResolver().resolve(
        configured_limit=0,
        provider_limit=provider_limit,
        request_model="request-model",
        provider_model="provider-model",
    ) == ContextLimitDecision(
        provider_limit,
        ContextLimitSource.AUTO_SAFE_FALLBACK,
        "CONTEXT_LIMIT_UNAVAILABLE",
    )


@pytest.mark.parametrize(
    ("request_model", "provider_model"),
    [
        ("gpt-4", "gpt-4"),
        (" gpt-4 ", "gpt-4"),
        (None, "gpt-4"),
        ("bad model", "provider-model"),
    ],
)
def test_auto_mode_uses_provider_limit_without_valid_model_override(
    request_model: str | None, provider_model: str | None
) -> None:
    assert ContextLimitResolver().resolve(
        configured_limit=0,
        provider_limit=200000,
        request_model=request_model,
        provider_model=provider_model,
    ) == ContextLimitDecision(
        200000,
        ContextLimitSource.AUTO_ASTRBOT,
        "NONE",
    )


def test_context_limit_decision_is_frozen_and_slotted() -> None:
    decision = ContextLimitDecision(128000, ContextLimitSource.AUTO_SAFE_FALLBACK, "NONE")

    with pytest.raises(FrozenInstanceError):
        decision.limit = 1  # type: ignore[misc]
    assert not hasattr(decision, "__dict__")


def test_request_budget_profile_computes_mechanical_effective_budget() -> None:
    profile = RequestBudgetProfile(
        model_identity="customer-secret-model",
        context_limit=128000,
        context_limit_source=ContextLimitSource.MANUAL,
        tokenizer_profile=OPENAI_O200K,
        target_input_budget=130000,
        hard_input_ceiling=150000,
    )

    assert profile.effective_input_budget == 96000
    assert profile.reserved_output_and_tools == 32000
    assert profile.safety_margin == 2000
    assert profile.stable_code == "NONE"
    assert "customer-secret-model" not in repr(profile)


def test_request_budget_profile_does_not_subtract_safety_or_clamp() -> None:
    profile = RequestBudgetProfile(
        model_identity=None,
        context_limit=1000,
        context_limit_source=ContextLimitSource.AUTO_SAFE_FALLBACK,
        tokenizer_profile=REFERENCE_O200K,
        target_input_budget=100,
        hard_input_ceiling=200,
        reserved_output_and_tools=2000,
        safety_margin=999,
    )

    assert profile.effective_input_budget == -1000


@pytest.mark.parametrize(
    "changes",
    [
        {"model_identity": 1},
        {"context_limit": True},
        {"context_limit": 0},
        {"context_limit_source": "MANUAL"},
        {"tokenizer_profile": "OPENAI_O200K"},
        {"target_input_budget": True},
        {"target_input_budget": 0},
        {"hard_input_ceiling": -1},
        {"reserved_output_and_tools": True},
        {"reserved_output_and_tools": -1},
        {"safety_margin": True},
        {"safety_margin": -1},
        {"stable_code": 1},
    ],
)
def test_request_budget_profile_validates_field_types(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "model_identity": "gpt-4",
        "context_limit": 128000,
        "context_limit_source": ContextLimitSource.MANUAL,
        "tokenizer_profile": OPENAI_CL100K,
        "target_input_budget": 130000,
        "hard_input_ceiling": 150000,
        "reserved_output_and_tools": 32000,
        "safety_margin": 2000,
        "stable_code": "NONE",
    }
    values.update(changes)

    with pytest.raises((TypeError, ValueError)):
        RequestBudgetProfile(**values)  # type: ignore[arg-type]


def test_request_budget_profile_is_frozen_and_slotted() -> None:
    profile = RequestBudgetProfile(
        model_identity=None,
        context_limit=128000,
        context_limit_source=ContextLimitSource.MANUAL,
        tokenizer_profile=OPENAI_CL100K,
        target_input_budget=130000,
        hard_input_ceiling=150000,
    )

    with pytest.raises(FrozenInstanceError):
        profile.context_limit = 1  # type: ignore[misc]
    assert not hasattr(profile, "__dict__")


def test_budget_safety_config_defaults_and_slots() -> None:
    config = BudgetSafetyConfig()

    assert config == BudgetSafetyConfig(
        target_input_budget=130_000,
        hard_input_ceiling=150_000,
        reserved_output_and_tools=32_000,
        safety_margin=2_000,
    )
    assert not hasattr(config, "__dict__")


@pytest.mark.parametrize(
    "changes",
    [
        {"target_input_budget": True},
        {"target_input_budget": 0},
        {"hard_input_ceiling": False},
        {"hard_input_ceiling": -1},
        {"reserved_output_and_tools": True},
        {"reserved_output_and_tools": -1},
        {"safety_margin": False},
        {"safety_margin": -1},
    ],
)
def test_budget_safety_config_validates_mechanical_bounds(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "target_input_budget": 130_000,
        "hard_input_ceiling": 150_000,
        "reserved_output_and_tools": 32_000,
        "safety_margin": 2_000,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        BudgetSafetyConfig(**values)  # type: ignore[arg-type]


def _metadata_event(umo: object = "umo:platform:session") -> SimpleNamespace:
    return SimpleNamespace(unified_msg_origin=umo)


def _metadata_request(model: object = None) -> SimpleNamespace:
    return SimpleNamespace(model=model)


def _metadata_provider(
    *,
    model: object = "provider-model",
    provider_config: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        get_model=lambda: model,
        provider_config=(
            {"max_context_tokens": 128_000} if provider_config is None else provider_config
        ),
    )


def _metadata_context(provider: object) -> SimpleNamespace:
    return SimpleNamespace(get_using_provider=lambda *, umo: provider)


def test_metadata_resolver_is_a_synchronous_public_read() -> None:
    assert inspect.iscoroutinefunction(resolve_astrbot_request_metadata) is False

    metadata = resolve_astrbot_request_metadata(
        _metadata_context(_metadata_provider(model="provider-model")),
        _metadata_event(),
        _metadata_request("request-model"),
    )

    assert metadata == AstrBotRequestMetadata(
        model_identity="request-model",
        request_model="request-model",
        provider_model="provider-model",
        provider_limit=128_000,
    )


def test_metadata_prefers_valid_request_model_over_provider_model() -> None:
    metadata = resolve_astrbot_request_metadata(
        _metadata_context(_metadata_provider(model=" provider-model ")),
        _metadata_event(),
        _metadata_request(" Request-Model "),
    )

    assert metadata == AstrBotRequestMetadata(
        model_identity="Request-Model",
        request_model="Request-Model",
        provider_model="provider-model",
        provider_limit=128_000,
    )


def test_metadata_falls_back_from_invalid_request_model_to_provider() -> None:
    metadata = resolve_astrbot_request_metadata(
        _metadata_context(_metadata_provider()),
        _metadata_event(),
        _metadata_request("invalid request model"),
    )

    assert metadata == AstrBotRequestMetadata(
        model_identity="provider-model",
        request_model=None,
        provider_model="provider-model",
        provider_limit=128_000,
    )


@pytest.mark.parametrize(
    "context",
    [
        object(),
        SimpleNamespace(get_using_provider=None),
        SimpleNamespace(get_using_provider=lambda *, umo: None),
    ],
)
def test_metadata_tolerates_unavailable_get_using_provider(
    context: object,
) -> None:
    metadata = resolve_astrbot_request_metadata(
        context,
        _metadata_event(),
        _metadata_request("request-model"),
    )

    assert metadata == AstrBotRequestMetadata(
        "request-model",
        "request-model",
        None,
        None,
    )


def test_metadata_tolerates_throwing_get_using_provider() -> None:
    class ThrowingContext:
        def get_using_provider(self, *, umo: str) -> object:
            raise RuntimeError(f"SECRET-provider:{umo}")

    metadata = resolve_astrbot_request_metadata(
        ThrowingContext(),
        _metadata_event(),
        _metadata_request("request-model"),
    )

    assert metadata == AstrBotRequestMetadata(
        "request-model",
        "request-model",
        None,
        None,
    )


@pytest.mark.parametrize(
    "provider",
    [
        object(),
        SimpleNamespace(get_model=None, provider_config={}),
        SimpleNamespace(get_model=lambda: None, provider_config={}),
    ],
)
def test_metadata_tolerates_unavailable_get_model(provider: object) -> None:
    metadata = resolve_astrbot_request_metadata(
        _metadata_context(provider),
        _metadata_event(),
        _metadata_request(),
    )

    assert metadata.model_identity is None
    assert metadata.provider_model is None


def test_metadata_tolerates_throwing_get_model() -> None:
    def get_model() -> object:
        raise RuntimeError("SECRET-model")

    provider = SimpleNamespace(
        get_model=get_model,
        provider_config={"max_context_tokens": 64_000},
    )

    metadata = resolve_astrbot_request_metadata(
        _metadata_context(provider),
        _metadata_event(),
        _metadata_request(),
    )

    assert metadata.provider_model is None
    assert metadata.provider_limit == 64_000


@pytest.mark.parametrize(
    "provider_config",
    [None, True, "config", 128_000, ("max_context_tokens", 128_000)],
)
def test_metadata_requires_provider_config_mapping(
    provider_config: object,
) -> None:
    provider = (
        SimpleNamespace(get_model=lambda: "provider-model")
        if provider_config is None
        else _metadata_provider(provider_config=provider_config)
    )
    metadata = resolve_astrbot_request_metadata(
        _metadata_context(provider),
        _metadata_event(),
        _metadata_request(),
    )

    assert metadata.provider_limit is None


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (True, None),
        (False, None),
        ("128000", None),
        (0, None),
        (-1, None),
        (1, 1),
        (200_000, 200_000),
    ],
)
def test_metadata_validates_exact_max_context_tokens(limit: object, expected: int | None) -> None:
    metadata = resolve_astrbot_request_metadata(
        _metadata_context(_metadata_provider(provider_config={"max_context_tokens": limit})),
        _metadata_event(),
        _metadata_request(),
    )

    assert metadata.provider_limit == expected


def test_metadata_tolerates_throwing_properties_and_invalid_origin() -> None:
    class ThrowingRequest:
        @property
        def model(self) -> object:
            raise RuntimeError("SECRET-request")

    class ThrowingEvent:
        @property
        def unified_msg_origin(self) -> object:
            raise RuntimeError("SECRET-event")

    context_calls = 0

    def get_using_provider(*, umo: str) -> object:
        nonlocal context_calls
        context_calls += 1
        return _metadata_provider()

    metadata = resolve_astrbot_request_metadata(
        SimpleNamespace(get_using_provider=get_using_provider),
        ThrowingEvent(),
        ThrowingRequest(),
    )

    assert metadata == AstrBotRequestMetadata(None, None, None, None)
    assert context_calls == 0


def test_metadata_tolerates_throwing_provider_config_property() -> None:
    class ThrowingProvider:
        def get_model(self) -> str:
            return "provider-model"

        @property
        def provider_config(self) -> object:
            raise RuntimeError("SECRET-config")

    metadata = resolve_astrbot_request_metadata(
        _metadata_context(ThrowingProvider()),
        _metadata_event(),
        _metadata_request(),
    )

    assert metadata.provider_model == "provider-model"
    assert metadata.provider_limit is None


def test_metadata_skips_provider_for_empty_or_non_string_origin() -> None:
    calls = 0

    def get_using_provider(*, umo: str) -> object:
        nonlocal calls
        calls += 1
        return _metadata_provider()

    context = SimpleNamespace(get_using_provider=get_using_provider)
    for umo in ("", " ", None, 1):
        metadata = resolve_astrbot_request_metadata(
            context,
            _metadata_event(umo),
            _metadata_request("request-model"),
        )
        assert metadata.provider_model is None
        assert metadata.provider_limit is None

    assert calls == 0


def test_metadata_is_frozen_slotted_and_repr_is_content_free() -> None:
    secret = "customer-secret-model"
    metadata = AstrBotRequestMetadata(secret, secret, secret, 128_000)

    assert repr(metadata) == "AstrBotRequestMetadata()"
    assert secret not in repr(metadata)
    assert not hasattr(metadata, "__dict__")
    assert not hasattr(metadata, "provider")
    assert not hasattr(metadata, "event")
    assert not hasattr(metadata, "request")
    assert not hasattr(metadata, "context")
    assert not hasattr(metadata, "config")
    with pytest.raises(FrozenInstanceError):
        metadata.model_identity = "changed"  # type: ignore[misc]
