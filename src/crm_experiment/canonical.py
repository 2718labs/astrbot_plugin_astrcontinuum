"""Deterministic canonical serialization for CRM records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum

from crm_experiment.contracts import CapsuleState


def utf8_bytes(value: str) -> int:
    """Return the exact number of bytes in the UTF-8 representation."""
    return len(value.encode("utf-8"))


def canonical_value(value: object) -> object:
    """Convert a supported value into a deterministic JSON-compatible value."""
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: canonical_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"mapping keys must be strings, got {type(key).__name__}"
                )
            converted[key] = canonical_value(item)
        return converted
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialize a supported value as compact, key-sorted canonical JSON."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    """Return the lowercase SHA-256 digest of a string's UTF-8 bytes."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def semantic_hash(state: CapsuleState) -> str:
    """Hash only content and weight metadata that define CRM semantics."""
    return sha256_text(
        canonical_json(
            {
                "kernel": state.kernel,
                "body": state.body,
                "weight_version": state.weight_version,
            }
        )
    )
