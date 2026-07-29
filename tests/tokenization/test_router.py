from __future__ import annotations

import pytest

from astrcontinuum.tokenization import (
    BYTE_FALLBACK,
    OPENAI_CL100K,
    OPENAI_O200K,
    REFERENCE_O200K,
    TokenizerProfile,
    TokenizerRoute,
    TokenizerRouter,
    normalize_model_identity,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gpt-4o-2024-08-06", "gpt-4o-2024-08-06"),
        ("  gpt-4  ", "gpt-4"),
        ("MiniMax-M2", "MiniMax-M2"),
        ("claude-sonnet-4", "claude-sonnet-4"),
        ("vendor/gpt-compatible", "vendor/gpt-compatible"),
        ("模型-A", "模型-A"),
    ],
)
def test_normalize_model_identity_accepts_content_without_changing_case(
    raw: str, expected: str
) -> None:
    assert normalize_model_identity(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,
        42,
        "",
        "   ",
        "gpt 4",
        "gpt\t4",
        "gpt\n4",
        "gpt\u200b4",
        "gpt\u202e4",
        "x" * 257,
    ],
)
def test_normalize_model_identity_rejects_invalid_values(raw: object) -> None:
    assert normalize_model_identity(raw) is None


def test_normalize_model_identity_accepts_exactly_256_codepoints() -> None:
    assert normalize_model_identity("x" * 256) == "x" * 256


@pytest.mark.parametrize(
    ("model", "encoding", "expected"),
    [
        ("gpt-4o-2024-08-06", "o200k_base", OPENAI_O200K),
        ("gpt-4", "cl100k_base", OPENAI_CL100K),
    ],
)
def test_router_accepts_only_known_tiktoken_encodings(
    model: str, encoding: str, expected: TokenizerProfile
) -> None:
    router = TokenizerRouter(lambda value: encoding)

    assert router.route(model) == TokenizerRoute(expected, "NONE")


@pytest.mark.parametrize(
    "model",
    [
        "MiniMax-M2",
        "claude-sonnet-4",
        "vendor/gpt-compatible",
        "openai-compatible/non-openai-model",
    ],
)
def test_router_unknown_model_uses_reference_profile(model: str) -> None:
    def unknown(_: str) -> str:
        raise KeyError(model)

    assert TokenizerRouter(unknown).route(model) == TokenizerRoute(
        REFERENCE_O200K,
        "TOKENIZER_MODEL_UNKNOWN",
    )


@pytest.mark.parametrize("encoding", [None, "", "p50k_base", 123, object()])
def test_router_rejects_non_whitelisted_mapper_results(encoding: object) -> None:
    router = TokenizerRouter(lambda _: encoding)  # type: ignore[arg-type,return-value]

    assert router.route("gpt-compatible") == TokenizerRoute(
        REFERENCE_O200K,
        "TOKENIZER_MODEL_UNKNOWN",
    )


def test_router_mapper_failure_uses_byte_fallback() -> None:
    def broken(_: str) -> str:
        raise RuntimeError("construction details must not escape")

    assert TokenizerRouter(broken).route("gpt-4") == TokenizerRoute(
        BYTE_FALLBACK,
        "TOKENIZER_CONSTRUCTION_FAILED",
    )


def test_router_explicit_none_mapper_reports_import_failure() -> None:
    assert TokenizerRouter(None).route("gpt-4") == TokenizerRoute(
        BYTE_FALLBACK,
        "TOKENIZER_IMPORT_FAILED",
    )
    assert TokenizerRouter(None).route(None) == TokenizerRoute(
        BYTE_FALLBACK,
        "TOKENIZER_IMPORT_FAILED",
    )


@pytest.mark.parametrize("model", [None, "", " ", "gpt 4", "x" * 257])
def test_router_invalid_identity_does_not_call_mapper(model: object) -> None:
    called = False

    def mapper(_: str) -> str:
        nonlocal called
        called = True
        return "o200k_base"

    assert TokenizerRouter(mapper).route(model) == TokenizerRoute(
        REFERENCE_O200K,
        "TOKENIZER_MODEL_UNKNOWN",
    )
    assert called is False


def test_tokenizer_route_repr_never_contains_original_model() -> None:
    secret = "customer-secret-model"
    route = TokenizerRouter(lambda _: "o200k_base").route(secret)

    assert secret not in repr(route)
    assert not hasattr(route, "model")


def test_tokenizer_route_is_frozen_and_slotted() -> None:
    route = TokenizerRoute(REFERENCE_O200K, "NONE")

    with pytest.raises((AttributeError, TypeError)):
        route.profile = BYTE_FALLBACK  # type: ignore[misc]
    assert not hasattr(route, "__dict__")
