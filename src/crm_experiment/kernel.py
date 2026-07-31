"""Bounded continuity-kernel selection for CRM states."""

from __future__ import annotations

from collections import defaultdict

from .canonical import utf8_bytes
from .contracts import (
    AtomRole,
    AtomStatus,
    KernelSchema,
    KernelSelection,
    KernelSlot,
    SemanticAtom,
)


def default_kernel_schema() -> KernelSchema:
    """Return the frozen KernelSchemaV1 used by the experiment."""
    return KernelSchema(
        slots=(
            KernelSlot(AtomRole.ROOT_GOAL, 1, 192),
            KernelSlot(AtomRole.CURRENT_FOCUS, 1, 192),
            KernelSlot(AtomRole.OPEN_LOOP, 1, 192),
            KernelSlot(AtomRole.HARD_CONSTRAINT, 4, 128),
            KernelSlot(AtomRole.DECISION, 2, 160),
            KernelSlot(AtomRole.EXACT_ANCHOR, 2, 192),
        ),
        metadata_reserve_bytes=1024,
    )


def derive_kernel_ceiling(schema: KernelSchema) -> int:
    """Return the preregistered worst-case UTF-8 kernel byte ceiling."""
    text_bytes = sum(slot.max_items * slot.max_text_bytes for slot in schema.slots)
    return schema.metadata_reserve_bytes + 2 * text_bytes


def _priority(atom: SemanticAtom) -> tuple[int, int, int, str]:
    return (
        0 if atom.status is AtomStatus.ACTIVE else 1,
        0 if atom.core_required else 1,
        -atom.revision,
        atom.atom_id,
    )


def select_kernel(
    atoms: tuple[SemanticAtom, ...],
    schema: KernelSchema,
) -> KernelSelection:
    """Select active core atoms into fixed role slots without hiding overflow."""
    by_role: dict[AtomRole, list[SemanticAtom]] = defaultdict(list)
    for atom in atoms:
        if atom.core_required and atom.status is AtomStatus.ACTIVE:
            by_role[atom.role].append(atom)

    selected: list[SemanticAtom] = []
    reasons: list[str] = []
    for slot in schema.slots:
        eligible = sorted(by_role[slot.role], key=_priority)
        if len(eligible) > slot.max_items:
            reasons.append(f"slot_overflow:{slot.role.value}")
        for atom in eligible[: slot.max_items]:
            if utf8_bytes(atom.text) > slot.max_text_bytes:
                reasons.append(f"text_overflow:{atom.atom_id}")
            selected.append(atom)

    expected = {
        atom.atom_id
        for atom in atoms
        if atom.core_required and atom.status is AtomStatus.ACTIVE
    }
    actual = {atom.atom_id for atom in selected}
    if expected != actual:
        reasons.append("core_coverage_incomplete")

    ordered = tuple(sorted(selected, key=lambda atom: (atom.role.value, atom.atom_id)))
    return KernelSelection(
        atoms=ordered,
        byte_ceiling=derive_kernel_ceiling(schema),
        valid=not reasons,
        reasons=tuple(sorted(set(reasons))),
    )
