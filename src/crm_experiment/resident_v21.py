"""Canonical resident accounting for the independent schema-3 control plane."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Final

from crm_experiment.contracts_v21 import (
    CapsuleStateV21,
    body_elided_state_v21,
    canonical_bytes_v21,
    canonical_json_v21,
    resident_layout_v21,
    validate_capsule_state_v21,
)

_RESIDENT_HASH_DOMAIN: Final = "crm-v21-resident-s3/v1"


@dataclass(frozen=True, slots=True)
class ResidentBreakdownV21:
    """Complete V21 resident accounting with source bodies reported separately."""

    frame_bytes: int
    exact_kernel_bytes: int
    hot_cache_bytes: int
    hot_frontier_bytes: int
    dictionary_bytes: int
    barrier_bytes: int
    sparse_weight_bytes: int
    segment_bytes: int
    loss_ledger_bytes: int
    structural_bytes: int
    source_body_bytes: int
    canonical_source_body_bytes: int
    control_bytes: int
    resident_bytes: int
    frame_hash: str
    exact_kernel_hash: str
    hot_cache_hash: str
    hot_frontier_hash: str
    dictionary_hash: str
    barrier_hash: str
    sparse_weight_hash: str
    segment_hash: str
    loss_ledger_hash: str


def _component_hash_v21(component: str, value: object) -> str:
    return sha256(
        canonical_json_v21(
            {
                "domain": f"{_RESIDENT_HASH_DOMAIN}/component/{component}",
                "value": value,
            }
        ).encode("utf-8")
    ).hexdigest()


def resident_bytes_v21(state: CapsuleStateV21) -> int:
    """Return storage accounting with raw source bytes separate from control JSON."""
    return resident_layout_v21(state).resident_bytes


def resident_breakdown_v21(state: CapsuleStateV21) -> ResidentBreakdownV21:
    """Account for every control field and separately expose exact source bodies."""
    layout = resident_layout_v21(state)
    body_elided_state = body_elided_state_v21(state)
    source_body_bytes = layout.source_body_bytes
    control_bytes = layout.control_bytes
    canonical_source_body_bytes = canonical_bytes_v21(state) - control_bytes
    resident_bytes = layout.resident_bytes
    frame_bytes = canonical_bytes_v21(state.frame)
    exact_kernel_bytes = canonical_bytes_v21(body_elided_state.exact_kernel)
    hot_cache_bytes = canonical_bytes_v21(body_elided_state.hot_cache)
    hot_frontier_bytes = canonical_bytes_v21(state.hot_frontier)
    dictionary_bytes = canonical_bytes_v21(state.dictionary)
    barrier_bytes = canonical_bytes_v21(state.fold_barriers)
    sparse_weight_bytes = canonical_bytes_v21(state.sparse_weight_policy)
    segment_bytes = canonical_bytes_v21(state.capsule_segments)
    loss_ledger_bytes = canonical_bytes_v21(state.loss_ledger)
    known_control_bytes = (
        frame_bytes
        + exact_kernel_bytes
        + hot_cache_bytes
        + hot_frontier_bytes
        + dictionary_bytes
        + barrier_bytes
        + sparse_weight_bytes
        + segment_bytes
        + loss_ledger_bytes
    )
    structural_bytes = control_bytes - known_control_bytes
    if structural_bytes < 0:
        raise ValueError("V21 resident breakdown double-counted control bytes")
    return ResidentBreakdownV21(
        frame_bytes=frame_bytes,
        exact_kernel_bytes=exact_kernel_bytes,
        hot_cache_bytes=hot_cache_bytes,
        hot_frontier_bytes=hot_frontier_bytes,
        dictionary_bytes=dictionary_bytes,
        barrier_bytes=barrier_bytes,
        sparse_weight_bytes=sparse_weight_bytes,
        segment_bytes=segment_bytes,
        loss_ledger_bytes=loss_ledger_bytes,
        structural_bytes=structural_bytes,
        source_body_bytes=source_body_bytes,
        canonical_source_body_bytes=canonical_source_body_bytes,
        control_bytes=control_bytes,
        resident_bytes=resident_bytes,
        frame_hash=_component_hash_v21("frame", state.frame),
        exact_kernel_hash=_component_hash_v21("exact-kernel", state.exact_kernel),
        hot_cache_hash=_component_hash_v21("hot-cache", state.hot_cache),
        hot_frontier_hash=_component_hash_v21("hot-frontier", state.hot_frontier),
        dictionary_hash=_component_hash_v21("dictionary", state.dictionary),
        barrier_hash=_component_hash_v21("barriers", state.fold_barriers),
        sparse_weight_hash=_component_hash_v21(
            "sparse-weights", state.sparse_weight_policy
        ),
        segment_hash=_component_hash_v21("segments", state.capsule_segments),
        loss_ledger_hash=_component_hash_v21("loss-ledger", state.loss_ledger),
    )


def resident_hash_v21(state: CapsuleStateV21) -> str:
    """Hash the complete schema-3 resident layout in its own domain."""
    validate_capsule_state_v21(state)
    digest = sha256(
        canonical_json_v21({"domain": _RESIDENT_HASH_DOMAIN, "state": state}).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{_RESIDENT_HASH_DOMAIN}:{digest}"
