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
# These identify AstrContinuum's stable offline adapter contract. They are not
# the version of the installed tiktoken package.
_OFFLINE_IMPLEMENTATION_NAME: Final = "astrcontinuum-offline-tiktoken"
_OFFLINE_IMPLEMENTATION_VERSION: Final = "1"

CANONICAL_O200K: Final = TokenizerProfile(
    profile_id="canonical-o200k-v1",
    schema_version=1,
    mode=TokenizerMode.CANONICAL,
    encoding_name="o200k_base",
    implementation_name=_OFFLINE_IMPLEMENTATION_NAME,
    implementation_version=_OFFLINE_IMPLEMENTATION_VERSION,
    asset_digest=_O200K_DIGEST,
    count_multiplier_basis_points=10_000,
)
OPENAI_O200K: Final = TokenizerProfile(
    profile_id="openai-o200k_base-v1",
    schema_version=1,
    mode=TokenizerMode.EXACT_TEXT,
    encoding_name="o200k_base",
    implementation_name=_OFFLINE_IMPLEMENTATION_NAME,
    implementation_version=_OFFLINE_IMPLEMENTATION_VERSION,
    asset_digest=_O200K_DIGEST,
    count_multiplier_basis_points=10_000,
)
OPENAI_CL100K: Final = TokenizerProfile(
    profile_id="openai-cl100k_base-v1",
    schema_version=1,
    mode=TokenizerMode.EXACT_TEXT,
    encoding_name="cl100k_base",
    implementation_name=_OFFLINE_IMPLEMENTATION_NAME,
    implementation_version=_OFFLINE_IMPLEMENTATION_VERSION,
    asset_digest=_CL100K_DIGEST,
    count_multiplier_basis_points=10_000,
)
REFERENCE_O200K: Final = TokenizerProfile(
    profile_id="reference-o200k-v1",
    schema_version=1,
    mode=TokenizerMode.REFERENCE,
    encoding_name="o200k_base",
    implementation_name=_OFFLINE_IMPLEMENTATION_NAME,
    implementation_version=_OFFLINE_IMPLEMENTATION_VERSION,
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


@dataclass(frozen=True, slots=True)
class TokenizerRoute:
    """Content-free result of resolving one model to a tokenizer profile."""

    profile: TokenizerProfile
    stable_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.profile, TokenizerProfile):
            raise TypeError("profile must be a TokenizerProfile")
        if not isinstance(self.stable_code, str) or not self.stable_code:
            raise ValueError("stable_code must be a non-empty string")


class ContextLimitSource(str, Enum):
    """Stable source labels for one resolved context limit."""

    MANUAL = "MANUAL"
    AUTO_ASTRBOT = "AUTO_ASTRBOT"
    AUTO_SAFE_FALLBACK = "AUTO_SAFE_FALLBACK"


@dataclass(frozen=True, slots=True)
class ContextLimitDecision:
    """Content-free context-limit resolution result."""

    limit: int
    source: ContextLimitSource
    stable_code: str

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(self.source, ContextLimitSource):
            raise TypeError("source must be a ContextLimitSource")
        if not isinstance(self.stable_code, str) or not self.stable_code:
            raise ValueError("stable_code must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RequestBudgetProfile:
    """Resolved, content-free request budget inputs."""

    model_identity: str | None = field(repr=False)
    context_limit: int
    context_limit_source: ContextLimitSource
    tokenizer_profile: TokenizerProfile
    target_input_budget: int
    hard_input_ceiling: int
    reserved_output_and_tools: int = 32_000
    safety_margin: int = 2_000
    stable_code: str = "NONE"

    def __post_init__(self) -> None:
        if self.model_identity is not None and not isinstance(self.model_identity, str):
            raise TypeError("model_identity must be a string or None")
        positive_fields = (
            "context_limit",
            "target_input_budget",
            "hard_input_ceiling",
        )
        non_negative_fields = (
            "reserved_output_and_tools",
            "safety_margin",
        )
        for field_name in positive_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name in non_negative_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.context_limit_source, ContextLimitSource):
            raise TypeError("context_limit_source must be a ContextLimitSource")
        if not isinstance(self.tokenizer_profile, TokenizerProfile):
            raise TypeError("tokenizer_profile must be a TokenizerProfile")
        if not isinstance(self.stable_code, str) or not self.stable_code:
            raise ValueError("stable_code must be a non-empty string")

    @property
    def effective_input_budget(self) -> int:
        return min(
            self.target_input_budget,
            self.hard_input_ceiling,
            self.context_limit - self.reserved_output_and_tools,
        )


@dataclass(frozen=True, slots=True, repr=False)
class AstrBotRequestMetadata:
    """Minimal normalized metadata obtained through AstrBot's public API."""

    model_identity: str | None
    request_model: str | None
    provider_model: str | None
    provider_limit: int | None

    def __repr__(self) -> str:
        return "AstrBotRequestMetadata()"


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
        raw_count: int | None = None
        try:
            raw_count = len(self._encoding.encode_ordinary(text))
        except Exception:  # noqa: BLE001,S110 - redact implementation failures
            pass
        if raw_count is None:
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_COUNT_FAILED)
        return apply_multiplier(raw_count, self.profile.count_multiplier_basis_points)

    def count_texts(self, texts: Sequence[str]) -> tuple[int, ...]:
        return tuple(self.count_text(text) for text in texts)
