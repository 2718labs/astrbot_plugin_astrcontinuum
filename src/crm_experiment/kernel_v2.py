"""Exact continuity-kernel selection and admission for CRM state v2."""

from __future__ import annotations

from collections import defaultdict

from crm_experiment.canonical import utf8_bytes
from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CapsuleStateV2,
    KernelSchemaV2,
    KernelSelectionV2,
    KernelSlotV2,
    KeyWinnerV2,
    SourceReceiptV2,
    WeightPolicyV2,
)
from crm_experiment.resident_v2 import resident_bytes_v2


def default_kernel_schema_v2() -> KernelSchemaV2:
    """Return the frozen v2 slot policy and non-negotiable admission floor."""
    return KernelSchemaV2(
        slots=(
            KernelSlotV2(AtomRole.ROOT_GOAL, 1, 192),
            KernelSlotV2(AtomRole.CURRENT_FOCUS, 1, 192),
            KernelSlotV2(AtomRole.OPEN_LOOP, 1, 192),
            KernelSlotV2(AtomRole.HARD_CONSTRAINT, 4, 128),
            KernelSlotV2(AtomRole.DECISION, 2, 160),
            KernelSlotV2(AtomRole.EXACT_ANCHOR, 2, 192),
        ),
        continuity_floor_bytes=4608,
    )


def _priority(record: ActiveRecordV2) -> tuple[int, str]:
    return -record.atom.revision, record.atom.source_id


def select_kernel_v2(
    records: tuple[ActiveRecordV2, ...],
    frontier: tuple[KeyWinnerV2, ...],
    receipts: tuple[SourceReceiptV2, ...],
    weight_policy: WeightPolicyV2,
    schema: KernelSchemaV2,
    *,
    generation: int,
    high_water: int,
    requested_budget: int,
    key_registry_limit: int,
    max_semantic_key_bytes: int,
    recomposition_policy_hash: str = "",
) -> KernelSelectionV2:
    """Validate all mandatory kernel payloads and exact mandatory bytes."""
    by_role: dict[AtomRole, list[ActiveRecordV2]] = defaultdict(list)
    for record in records:
        if record.atom.core_required:
            by_role[record.atom.role].append(record)

    selected: list[ActiveRecordV2] = []
    reasons: list[str] = []
    for slot in schema.slots:
        eligible = sorted(by_role[slot.role], key=_priority)
        if len(eligible) > slot.max_items:
            reasons.append(f"slot_overflow:{slot.role.value}")
        for record in eligible:
            if utf8_bytes(record.atom.text) > slot.max_text_bytes:
                reasons.append(f"text_overflow:{record.atom.source_id}")
            selected.append(record)

    expected = {
        winner.source_id
        for winner in frontier
        if winner.status is AtomStatus.ACTIVE and winner.core_required
    }
    actual = {record.atom.source_id for record in selected}
    if expected != actual:
        reasons.append("core_coverage_incomplete")
    for source_id in sorted(expected - actual):
        reasons.append(f"core_payload_missing:{source_id}")
    for record in selected:
        for dependency in record.atom.depends_on:
            if dependency not in actual:
                reasons.append(f"kernel_dependency_missing:{dependency}")
    if requested_budget < schema.continuity_floor_bytes:
        reasons.append("admission_below_continuity_floor")

    ordered = tuple(
        sorted(
            selected,
            key=lambda record: (record.atom.role.value, record.atom.source_id),
        )
    )
    if expected - actual:
        return KernelSelectionV2(
            records=ordered,
            resident_bytes=None,
            valid=False,
            reasons=tuple(sorted(set(reasons))),
        )
    kernel_state = CapsuleStateV2(
        schema_version=2,
        generation=generation,
        high_water=high_water,
        accepted_budget=requested_budget,
        key_registry_limit=key_registry_limit,
        max_semantic_key_bytes=max_semantic_key_bytes,
        frontier=frontier,
        receipts=receipts,
        weight_policy=weight_policy,
        kernel=ordered,
        body=(),
        recomposition_policy_hash=recomposition_policy_hash,
    )
    resident_bytes = resident_bytes_v2(kernel_state)
    if resident_bytes > requested_budget:
        reasons.append("kernel_budget_exceeded")

    return KernelSelectionV2(
        records=ordered,
        resident_bytes=resident_bytes,
        valid=not reasons,
        reasons=tuple(sorted(set(reasons))),
    )
