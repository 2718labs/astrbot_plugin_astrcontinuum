from __future__ import annotations

import unicodedata
from collections.abc import Callable
from typing import Final

from .types import (
    BYTE_FALLBACK,
    OPENAI_CL100K,
    OPENAI_O200K,
    REFERENCE_O200K,
    TokenizerRoute,
)

_DEFAULT_MAPPER: Final = object()
_MAX_MODEL_CODEPOINTS: Final = 256
_KNOWN_ENCODINGS: Final = {
    "o200k_base": OPENAI_O200K,
    "cl100k_base": OPENAI_CL100K,
}


def normalize_model_identity(value: object) -> str | None:
    """Return a bounded model identity, or None without retaining invalid input."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_MODEL_CODEPOINTS:
        return None
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in normalized
    ):
        return None
    return normalized


class TokenizerRouter:
    """Resolve model identities without provider-prefix guessing."""

    __slots__ = ("_encoding_name_for_model",)

    def __init__(
        self,
        encoding_name_for_model: Callable[[str], object] | None | object = _DEFAULT_MAPPER,
    ) -> None:
        mapper: Callable[[str], object] | None
        if encoding_name_for_model is _DEFAULT_MAPPER:
            try:
                from tiktoken import encoding_name_for_model as imported_mapper
            except Exception:  # noqa: BLE001 - convert imports to a stable code
                mapper = None
            else:
                mapper = imported_mapper
        elif callable(encoding_name_for_model):
            mapper = encoding_name_for_model
        else:
            mapper = None
        self._encoding_name_for_model = mapper

    def route(self, model_identity: object) -> TokenizerRoute:
        mapper = self._encoding_name_for_model
        if mapper is None:
            return TokenizerRoute(BYTE_FALLBACK, "TOKENIZER_IMPORT_FAILED")
        normalized = normalize_model_identity(model_identity)
        if normalized is None:
            return TokenizerRoute(REFERENCE_O200K, "TOKENIZER_MODEL_UNKNOWN")
        try:
            encoding_name = mapper(normalized)
        except KeyError:
            return TokenizerRoute(REFERENCE_O200K, "TOKENIZER_MODEL_UNKNOWN")
        except Exception:  # noqa: BLE001 - redact provider and tokenizer details
            return TokenizerRoute(BYTE_FALLBACK, "TOKENIZER_CONSTRUCTION_FAILED")
        profile = _KNOWN_ENCODINGS.get(encoding_name) if isinstance(encoding_name, str) else None
        if profile is None:
            return TokenizerRoute(REFERENCE_O200K, "TOKENIZER_MODEL_UNKNOWN")
        return TokenizerRoute(profile, "NONE")
