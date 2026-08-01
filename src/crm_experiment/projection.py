"""Bounded query projections from one frozen Capsule state."""

from __future__ import annotations

from crm_experiment.canonical import utf8_bytes
from crm_experiment.contracts import CapsuleState, ProjectionResult, QuerySpec

UNKNOWN = "[CONTEXT_INSUFFICIENT]"


def project_query(
    state: CapsuleState,
    query: QuerySpec,
    byte_budget: int,
) -> ProjectionResult:
    """Return matching frozen atoms that fit within the injection byte budget."""
    matching = [
        atom
        for atom in state.kernel + state.body
        if atom.role is query.role and query.semantic_key in atom.semantic_keys
    ]
    matching.sort(key=lambda atom: (-atom.revision, atom.atom_id))

    selected = []
    lines = []
    used = 0
    for atom in matching:
        line = atom.text
        cost = utf8_bytes(line) + (1 if lines else 0)
        if used + cost <= byte_budget:
            selected.append(atom)
            lines.append(line)
            used += cost

    if not selected:
        return ProjectionResult(
            query_id=query.query_id,
            text=UNKNOWN,
            selected_atom_ids=(),
            selected_covered_ids=(),
            byte_cost=utf8_bytes(UNKNOWN),
            supported=False,
        )

    covered = tuple(
        sorted({item for atom in selected for item in atom.covered_atom_ids})
    )
    return ProjectionResult(
        query_id=query.query_id,
        text="\n".join(lines),
        selected_atom_ids=tuple(atom.atom_id for atom in selected),
        selected_covered_ids=covered,
        byte_cost=used,
        supported=True,
    )
