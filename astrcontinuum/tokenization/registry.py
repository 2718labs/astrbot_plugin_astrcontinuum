from __future__ import annotations

import importlib
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

from .assets import ASSET_MANIFEST, TokenizerAsset, load_mergeable_ranks
from .types import (
    OrdinaryEncoding,
    ProfileCounter,
    TokenizerError,
    TokenizerErrorCode,
    TokenizerMode,
    TokenizerProfile,
)

try:
    _tiktoken_runtime: Any = importlib.import_module("tiktoken")
except Exception:  # noqa: BLE001 - optional dependency must not prevent package import
    _tiktoken_runtime = None


_CL100K_PATTERN: Final = (
    r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+|"
    r" ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"
)
_O200K_PATTERN: Final = "|".join(  # noqa: FLY002 - mirror tiktoken 0.12.0 definition
    (
        (
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*"
            r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?"
        ),
        (
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+"
            r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?"
        ),
        r"\p{N}{1,3}",
        r" ?[^\s\p{L}\p{N}]+[\r\n/]*",
        r"\s*[\r\n]+",
        r"\s+(?!\S)",
        r"\s+",
    )
)


@dataclass(frozen=True, slots=True)
class _EncodingSpec:
    asset: TokenizerAsset
    pattern: str
    special_tokens: tuple[tuple[str, int], ...]


_ENCODING_SPECS: Final[Mapping[str, _EncodingSpec]] = MappingProxyType(
    {
        "cl100k_base": _EncodingSpec(
            asset=ASSET_MANIFEST["cl100k_base"],
            pattern=_CL100K_PATTERN,
            special_tokens=(
                ("<|endoftext|>", 100257),
                ("<|fim_prefix|>", 100258),
                ("<|fim_middle|>", 100259),
                ("<|fim_suffix|>", 100260),
                ("<|endofprompt|>", 100276),
            ),
        ),
        "o200k_base": _EncodingSpec(
            asset=ASSET_MANIFEST["o200k_base"],
            pattern=_O200K_PATTERN,
            special_tokens=(
                ("<|endoftext|>", 199999),
                ("<|endofprompt|>", 200018),
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class _Utf8ByteEncoding:
    def encode_ordinary(self, text: str) -> bytes:
        return text.encode("utf-8")


_BYTE_ENCODING: Final = _Utf8ByteEncoding()


class TokenizerRegistry:
    """Build and share verified local encodings without caching any content."""

    __slots__ = ("_asset_root", "_encoding_cache", "_lock")

    def __init__(self, *, asset_root: Path | None = None) -> None:
        self._asset_root = (
            Path(__file__).with_name("assets") if asset_root is None else Path(asset_root)
        )
        self._encoding_cache: dict[str, OrdinaryEncoding] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "TokenizerRegistry()"

    def counter_for(self, profile: TokenizerProfile) -> ProfileCounter:
        if not isinstance(profile, TokenizerProfile):
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
        if profile.mode is TokenizerMode.BYTE_FALLBACK:
            if profile.encoding_name is not None:
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
            return ProfileCounter(profile, _BYTE_ENCODING)

        encoding_name = profile.encoding_name
        if encoding_name is None:
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
        spec = _ENCODING_SPECS.get(encoding_name)
        if (
            spec is None
            or profile.asset_digest is None
            or profile.asset_digest != spec.asset.digest
        ):
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED)
        if _tiktoken_runtime is None:
            raise TokenizerError(TokenizerErrorCode.TOKENIZER_IMPORT_FAILED)
        return ProfileCounter(profile, self._encoding_for(encoding_name, spec))

    def _encoding_for(self, encoding_name: str, spec: _EncodingSpec) -> OrdinaryEncoding:
        with self._lock:
            cached = self._encoding_cache.get(encoding_name)
            if cached is not None:
                return cached

            ranks = load_mergeable_ranks(
                self._asset_root / spec.asset.filename,
                spec.asset.digest,
            )
            try:
                encoding = _tiktoken_runtime.Encoding(
                    encoding_name,
                    pat_str=spec.pattern,
                    mergeable_ranks=ranks,
                    special_tokens=dict(spec.special_tokens),
                )
            except Exception:  # noqa: BLE001 - redact construction implementation details
                raise TokenizerError(TokenizerErrorCode.TOKENIZER_CONSTRUCTION_FAILED) from None
            ordinary_encoding = cast(OrdinaryEncoding, encoding)
            self._encoding_cache[encoding_name] = ordinary_encoding
            return ordinary_encoding
