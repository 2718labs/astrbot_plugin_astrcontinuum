from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from astrcontinuum.tokenization import (
    BYTE_FALLBACK,
    CANONICAL_O200K,
    OPENAI_CL100K,
    OPENAI_O200K,
    REFERENCE_O200K,
    ProfileCounter,
    TokenizerError,
    TokenizerErrorCode,
    TokenizerMode,
    TokenizerProfile,
    apply_multiplier,
)


class StubEncoding:
    def __init__(self, responses: dict[str, tuple[int, ...]]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def encode_ordinary(self, text: str) -> tuple[int, ...]:
        self.calls.append(text)
        return self._responses[text]

    def __repr__(self) -> str:
        return "StubEncoding(private)"


class BrokenEncoding:
    def encode_ordinary(self, text: str) -> tuple[int, ...]:
        raise RuntimeError(f"must not escape: {text}")


def test_tokenizer_profile_has_exact_frozen_slots_contract() -> None:
    assert tuple(field.name for field in fields(TokenizerProfile)) == (
        "profile_id",
        "schema_version",
        "mode",
        "encoding_name",
        "implementation_name",
        "implementation_version",
        "asset_digest",
        "count_multiplier_basis_points",
    )
    assert not hasattr(CANONICAL_O200K, "__dict__")

    with pytest.raises(FrozenInstanceError):
        CANONICAL_O200K.profile_id = "changed"  # type: ignore[misc]


def test_profile_ids_modes_and_multipliers_are_stable() -> None:
    assert (
        (
            CANONICAL_O200K.profile_id,
            CANONICAL_O200K.mode,
            CANONICAL_O200K.count_multiplier_basis_points,
        ),
        (
            OPENAI_O200K.profile_id,
            OPENAI_O200K.mode,
            OPENAI_O200K.count_multiplier_basis_points,
        ),
        (
            OPENAI_CL100K.profile_id,
            OPENAI_CL100K.mode,
            OPENAI_CL100K.count_multiplier_basis_points,
        ),
        (
            REFERENCE_O200K.profile_id,
            REFERENCE_O200K.mode,
            REFERENCE_O200K.count_multiplier_basis_points,
        ),
        (
            BYTE_FALLBACK.profile_id,
            BYTE_FALLBACK.mode,
            BYTE_FALLBACK.count_multiplier_basis_points,
        ),
    ) == (
        ("canonical-o200k-v1", TokenizerMode.CANONICAL, 10_000),
        ("openai-o200k_base-v1", TokenizerMode.EXACT_TEXT, 10_000),
        ("openai-cl100k_base-v1", TokenizerMode.EXACT_TEXT, 10_000),
        ("reference-o200k-v1", TokenizerMode.REFERENCE, 11_000),
        ("utf8-byte-v1", TokenizerMode.BYTE_FALLBACK, 10_000),
    )


@pytest.mark.parametrize(
    ("raw_count", "basis_points", "expected"),
    [
        (0, 10_000, 0),
        (1, 10_000, 1),
        (3, 11_000, 4),
        (10, 11_000, 11),
        (11, 11_000, 13),
    ],
)
def test_apply_multiplier_uses_integer_ceiling(
    raw_count: int,
    basis_points: int,
    expected: int,
) -> None:
    assert apply_multiplier(raw_count, basis_points) == expected


@pytest.mark.parametrize(
    ("raw_count", "basis_points"),
    [
        (True, 10_000),
        (-1, 10_000),
        ("1", 10_000),
        (1, True),
        (1, -1),
        (1, 10_000.0),
    ],
)
def test_apply_multiplier_rejects_invalid_integer_inputs(
    raw_count: object,
    basis_points: object,
) -> None:
    with pytest.raises(TokenizerError) as raised:
        apply_multiplier(  # type: ignore[arg-type]
            raw_count,
            basis_points,
        )

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_COUNT_INVALID
    assert str(raised.value) == "TOKENIZER_COUNT_INVALID"


def test_profile_counter_handles_empty_text_and_integer_ceiling() -> None:
    encoding = StubEncoding({"": (), "private": (0, 1, 2)})
    counter = ProfileCounter(REFERENCE_O200K, encoding)

    assert counter.count_text("") == 0
    assert counter.count_text("private") == 4
    assert encoding.calls == ["", "private"]


def test_count_texts_counts_each_text_sequentially_and_returns_tuple() -> None:
    encoding = StubEncoding({"first": (0,), "second": (0, 1), "third": ()})
    counter = ProfileCounter(CANONICAL_O200K, encoding)

    assert counter.count_texts(["first", "second", "third"]) == (1, 2, 0)
    assert encoding.calls == ["first", "second", "third"]


def test_counter_is_frozen_and_repr_never_contains_encoding_or_content() -> None:
    encoding = StubEncoding({"private": ()})
    counter = ProfileCounter(CANONICAL_O200K, encoding)
    counter.count_text("private")

    assert "private" not in repr(counter)
    assert "StubEncoding" not in repr(counter)
    with pytest.raises(FrozenInstanceError):
        counter.profile = REFERENCE_O200K  # type: ignore[misc]


def test_encoding_failure_is_translated_without_content() -> None:
    counter = ProfileCounter(CANONICAL_O200K, BrokenEncoding())

    with pytest.raises(TokenizerError) as raised:
        counter.count_text("private")

    assert raised.value.code is TokenizerErrorCode.TOKENIZER_COUNT_FAILED
    assert "private" not in str(raised.value)
    assert "private" not in repr(raised.value)
