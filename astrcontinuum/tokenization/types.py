from __future__ import annotations

from collections.abc import Sequence, Sized
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol


class TokenizerMode(str, Enum):
    CANONICAL = "CANONICAL"
    EXACT_TEXT = "EXACT_TEXT"
    REFERENCE = "REFERENCE"
    BYTE_FALLBACK = "BYTE_FALLBACK"


class TokenizerErrorCode(str, Enum):
    TOKENIZER_IMPORT_FAILED = "TOKENIZER_IMPORT_FAILED"
    TOKENIZER_ASSET_MISSING = "TOKENIZER_ASSET_MISSING"
    TOKENIZER_ASSET_INVALID = "TOKENIZER_ASSET_INVALID"
    TOKENIZER_CONSTRUCTION_FAILED = "TOKENIZER_CONSTRUCTION_FAILED"
    TOKENIZER_COUNT_FAILED = "TOKENIZER_COUNT_FAILED"
    TOKENIZER_COUNT_INVALID = "TOKENIZER_COUNT_INVALID"


TOKENIZER_IMPORT_FAILED: Final = TokenizerErrorCode.TOKENIZER_IMPORT_FAILED.value


class TokenizerError(RuntimeError):
    """Report a stable tokenizer failure without content or dynamic details."""

    __slots__ = ("_code",)

    def __init__(self, code: TokenizerErrorCode) -> None:
        self._code = code
        super().__init__(code.value)

    @property
    def code(self) -> TokenizerErrorCode:
        return self._code


@dataclass(frozen=True, slots=True)
class TokenizerProfile:
    profile_id: str
    schema_version: int
    mode: TokenizerMode
    encoding_name: str | None
    implementation_name: str
    implementation_version: str
    asset_digest: str | None
    count_multiplier_basis_points: int


_CL100K_DIGEST: Final = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
_O200K_DIGEST: Final = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"

CANONICAL_O200K: Final = TokenizerProfile(
    profile_id="canonical-o200k-v1",
    schema_version=1,
    mode=TokenizerMode.CANONICAL,
    encoding_name="o200k_base",
    implementation_name="tiktoken",
    implementation_version="0.12.0",
    asset_digest=_O200K_DIGEST,
    count_multiplier_basis_points=10_000,
)
OPENAI_O200K: Final = TokenizerProfile(
    profile_id="openai-o200k_base-v1",
    schema_version=1,
    mode=TokenizerMode.EXACT_TEXT,
    encoding_name="o200k_base",
    implementation_name="tiktoken",
    implementation_version="0.12.0",
    asset_digest=_O200K_DIGEST,
    count_multiplier_basis_points=10_000,
)
OPENAI_CL100K: Final = TokenizerProfile(
    profile_id="openai-cl100k_base-v1",
    schema_version=1,
    mode=TokenizerMode.EXACT_TEXT,
    encoding_name="cl100k_base",
    implementation_name="tiktoken",
    implementation_version="0.12.0",
    asset_digest=_CL100K_DIGEST,
    count_multiplier_basis_points=10_000,
)
REFERENCE_O200K: Final = TokenizerProfile(
    profile_id="reference-o200k-v1",
    schema_version=1,
    mode=TokenizerMode.REFERENCE,
    encoding_name="o200k_base",
    implementation_name="tiktoken",
    implementation_version="0.12.0",
    asset_digest=_O200K_DIGEST,
    count_multiplier_basis_points=11_000,
)
BYTE_FALLBACK: Final = TokenizerProfile(
    profile_id="utf8-byte-v1",
    schema_version=1,
    mode=TokenizerMode.BYTE_FALLBACK,
    encoding_name=None,
    implementation_name="python-utf8",
    implementation_version="1",
    asset_digest=None,
    count_multiplier_basis_points=10_000,
)


def apply_multiplier(raw_count: int, basis_points: int) -> int:
    if (
        isinstance(raw_count, bool)
        or not isinstance(raw_count, int)
        or raw_count < 0
        or isinstance(basis_points, bool)
        or not isinstance(basis_points, int)
        or basis_points < 0
    ):
        raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_INVALID)
    return (raw_count * basis_points + 9_999) // 10_000


class OrdinaryEncoding(Protocol):
    def encode_ordinary(self, text: str) -> Sized: ...


@dataclass(frozen=True, slots=True)
class ProfileCounter:
    profile: TokenizerProfile
    _encoding: OrdinaryEncoding = field(repr=False, compare=False)

    def count_text(self, text: str) -> int:
        try:
            raw_count = len(self._encoding.encode_ordinary(text))
        except Exception:  # noqa: BLE001 - redact content and implementation failures
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_FAILED) from None
        return apply_multiplier(raw_count, self.profile.count_multiplier_basis_points)

    def count_texts(self, texts: Sequence[str]) -> tuple[int, ...]:
        return tuple(self.count_text(text) for text in texts)
