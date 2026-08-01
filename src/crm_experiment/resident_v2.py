"""Canonical resident sizing and domain-separated hashes for CRM v2."""

from __future__ import annotations

from dataclasses import replace

from crm_experiment.canonical import canonical_json, sha256_text, utf8_bytes
from crm_experiment.contracts_v2 import CapsuleStateV2, ResidentBreakdownV2

_RESIDENT_HASH_DOMAIN = "crm-v2-resident/v1"
_LOGICAL_HASH_DOMAIN = "crm-v2-logical/v1"


def resident_bytes_v2(state: CapsuleStateV2) -> int:
    """Return exact UTF-8 bytes of the canonical frozen state."""
    return utf8_bytes(canonical_json(state))


def resident_breakdown_v2(state: CapsuleStateV2) -> ResidentBreakdownV2:
    """Decompose full bytes into an exact frame plus canonical body items."""
    fixed_bytes = resident_bytes_v2(replace(state, body=()))
    body_item_bytes = tuple(utf8_bytes(canonical_json(item)) for item in state.body)
    separator_bytes = max(0, len(state.body) - 1)
    persistent_bytes = fixed_bytes + sum(body_item_bytes) + separator_bytes
    actual = resident_bytes_v2(state)
    if persistent_bytes != actual:
        raise ValueError(
            f"resident byte model mismatch: predicted={persistent_bytes} actual={actual}"
        )
    return ResidentBreakdownV2(
        fixed_bytes=fixed_bytes,
        body_item_bytes=body_item_bytes,
        body_separator_bytes=separator_bytes,
        persistent_bytes=persistent_bytes,
    )


def resident_hash_v2(state: CapsuleStateV2) -> str:
    """Hash the complete physical resident layout and frame."""
    return sha256_text(
        canonical_json({"domain": _RESIDENT_HASH_DOMAIN, "state": state})
    )


def logical_semantic_hash_v2(state: CapsuleStateV2) -> str:
    """Hash latest logical winners independently of payload residency/layout."""
    return sha256_text(
        canonical_json(
            {
                "domain": _LOGICAL_HASH_DOMAIN,
                "frontier": state.frontier,
                "receipts": state.receipts,
                "weight_policy": state.weight_policy,
            }
        )
    )
