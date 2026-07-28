from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from .router import normalize_model_identity
from .types import (
    AstrBotRequestMetadata,
    ContextLimitDecision,
    ContextLimitSource,
)

_SAFE_FALLBACK_LIMIT: Final = 128_000


class ContextLimitResolver:
    """Resolve manual and AstrBot-provided context limits conservatively."""

    __slots__ = ()

    def resolve(
        self,
        configured_limit: object,
        *,
        provider_limit: object = None,
        request_model: object = None,
        provider_model: object = None,
    ) -> ContextLimitDecision:
        if (
            isinstance(configured_limit, bool)
            or not isinstance(configured_limit, int)
            or configured_limit < 0
        ):
            return ContextLimitDecision(
                _SAFE_FALLBACK_LIMIT,
                ContextLimitSource.AUTO_SAFE_FALLBACK,
                "CONTEXT_LIMIT_CONFIG_INVALID",
            )
        if configured_limit > 0:
            return ContextLimitDecision(
                configured_limit,
                ContextLimitSource.MANUAL,
                "NONE",
            )
        if (
            isinstance(provider_limit, bool)
            or not isinstance(provider_limit, int)
            or provider_limit < 1
        ):
            return ContextLimitDecision(
                _SAFE_FALLBACK_LIMIT,
                ContextLimitSource.AUTO_SAFE_FALLBACK,
                "CONTEXT_LIMIT_UNAVAILABLE",
            )

        normalized_request = normalize_model_identity(request_model)
        normalized_provider = normalize_model_identity(provider_model)
        if normalized_request is not None and normalized_request != normalized_provider:
            return ContextLimitDecision(
                min(provider_limit, _SAFE_FALLBACK_LIMIT),
                ContextLimitSource.AUTO_SAFE_FALLBACK,
                "CONTEXT_LIMIT_UNAVAILABLE",
            )
        return ContextLimitDecision(
            provider_limit,
            ContextLimitSource.AUTO_ASTRBOT,
            "NONE",
        )


def _safe_attribute(instance: object, name: str) -> object | None:
    try:
        return getattr(instance, name)
    except Exception:  # noqa: BLE001 - public host objects may expose properties
        return None


def resolve_astrbot_request_metadata(
    context: object,
    event: object,
    request: object,
) -> AstrBotRequestMetadata:
    """Read only bounded metadata through AstrBot's public synchronous API."""

    request_model = normalize_model_identity(_safe_attribute(request, "model"))
    provider_model: str | None = None
    provider_limit: int | None = None

    umo = _safe_attribute(event, "unified_msg_origin")
    get_using_provider = _safe_attribute(context, "get_using_provider")
    provider: object | None = None
    if isinstance(umo, str) and umo.strip() and callable(get_using_provider):
        try:
            provider = get_using_provider(umo=umo)
        except Exception:  # noqa: BLE001 - convert host failures to unavailable metadata
            provider = None

    if provider is not None:
        get_model = _safe_attribute(provider, "get_model")
        if callable(get_model):
            try:
                provider_model = normalize_model_identity(get_model())
            except Exception:  # noqa: BLE001 - redact host failure details
                provider_model = None

        provider_config = _safe_attribute(provider, "provider_config")
        if isinstance(provider_config, Mapping):
            try:
                configured_provider_limit = provider_config["max_context_tokens"]
            except Exception:  # noqa: BLE001 - hostile Mapping implementations are optional
                configured_provider_limit = None
            if (
                not isinstance(configured_provider_limit, bool)
                and isinstance(configured_provider_limit, int)
                and configured_provider_limit > 0
            ):
                provider_limit = configured_provider_limit

    return AstrBotRequestMetadata(
        model_identity=request_model if request_model is not None else provider_model,
        request_model=request_model,
        provider_model=provider_model,
        provider_limit=provider_limit,
    )
