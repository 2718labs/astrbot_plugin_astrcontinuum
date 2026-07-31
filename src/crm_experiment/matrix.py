"""Deterministic atom, candidate, and relation-matrix construction."""

from __future__ import annotations

from itertools import combinations

from .canonical import canonical_json, sha256_text, utf8_bytes
from .contracts import (
    AtomRole,
    AtomStatus,
    Candidate,
    CapsuleState,
    MatrixBundle,
    SemanticAtom,
)

_ROLE_PRIORITY = {
    role: (len(AtomRole) - index) / len(AtomRole) for index, role in enumerate(AtomRole)
}


def atomize(
    base: CapsuleState | None,
    delta: tuple[SemanticAtom, ...],
) -> tuple[SemanticAtom, ...]:
    """Replace every base block touched by the latest revision of a delta key."""
    base_atoms = () if base is None else base.kernel + base.body
    updated_keys = {key for atom in delta for key in atom.semantic_keys}
    retained = [
        atom for atom in base_atoms if updated_keys.isdisjoint(atom.semantic_keys)
    ]

    latest: dict[str, SemanticAtom] = {}
    for atom in sorted(
        delta, key=lambda item: (item.as_of, item.revision, item.atom_id)
    ):
        for key in atom.semantic_keys:
            previous = latest.get(key)
            if previous is None or (atom.revision, atom.atom_id) > (
                previous.revision,
                previous.atom_id,
            ):
                latest[key] = atom

    unique_delta = {atom.atom_id: atom for atom in latest.values()}
    return tuple(
        sorted(retained + list(unique_delta.values()), key=lambda atom: atom.atom_id)
    )


def _candidate_id(kind: str, payload: dict[str, object]) -> str:
    return f"{kind}-{sha256_text(canonical_json(payload))}"


def _direct_candidate(atom: SemanticAtom) -> Candidate:
    payload: dict[str, object] = {
        "kind": "direct",
        "role": atom.role,
        "text": atom.text,
        "covers": (atom.atom_id,),
        "semantic_keys": atom.semantic_keys,
        "depends_on": atom.depends_on,
        "exact": atom.exact,
        "core_required": atom.core_required,
        "merge_depth": atom.merge_depth,
    }
    return Candidate(
        candidate_id=_candidate_id("direct", payload),
        role=atom.role,
        text=atom.text,
        covers=(atom.atom_id,),
        semantic_keys=atom.semantic_keys,
        byte_cost=utf8_bytes(atom.text),
        error_risk=0.0,
        depends_on=atom.depends_on,
        exact=atom.exact,
        core_required=atom.core_required,
        merge_depth=atom.merge_depth,
    )


def _can_merge(left: SemanticAtom, right: SemanticAtom) -> bool:
    if left.status is not AtomStatus.ACTIVE or right.status is not AtomStatus.ACTIVE:
        return False
    if left.exact or right.exact or left.core_required or right.core_required:
        return False
    if left.role is not right.role or left.merge_depth != 0 or right.merge_depth != 0:
        return False
    if left.atom_id in right.depends_on or right.atom_id in left.depends_on:
        return False
    return set(left.semantic_keys).isdisjoint(right.semantic_keys)


def _merge_candidate(left: SemanticAtom, right: SemanticAtom) -> Candidate:
    text = f"{left.text}; {right.text}"
    covers = tuple(sorted((left.atom_id, right.atom_id)))
    semantic_keys = tuple(sorted(set(left.semantic_keys) | set(right.semantic_keys)))
    depends_on = tuple(sorted(set(left.depends_on) | set(right.depends_on)))
    payload: dict[str, object] = {
        "kind": "merge",
        "role": left.role,
        "text": text,
        "covers": covers,
        "semantic_keys": semantic_keys,
        "depends_on": depends_on,
        "exact": False,
        "core_required": False,
        "merge_depth": 1,
    }
    return Candidate(
        candidate_id=_candidate_id("merge", payload),
        role=left.role,
        text=text,
        covers=covers,
        semantic_keys=semantic_keys,
        byte_cost=utf8_bytes(text),
        error_risk=0.001,
        depends_on=depends_on,
        exact=False,
        core_required=False,
        merge_depth=1,
    )


def compose_candidates(atoms: tuple[SemanticAtom, ...]) -> tuple[Candidate, ...]:
    """Create deterministic direct and verified pair-merge candidates."""
    active = tuple(
        sorted(
            (atom for atom in atoms if atom.status is AtomStatus.ACTIVE),
            key=lambda atom: atom.atom_id,
        )
    )
    direct = [_direct_candidate(atom) for atom in active]
    merged = [
        _merge_candidate(left, right)
        for left, right in combinations(active, 2)
        if _can_merge(left, right)
    ]
    return tuple(
        sorted(
            direct + merged,
            key=lambda candidate: (len(candidate.covers), candidate.candidate_id),
        )
    )


def _feature_rows(atoms: tuple[SemanticAtom, ...]) -> tuple[tuple[float, ...], ...]:
    max_revision = max((atom.revision for atom in atoms), default=0)
    denominator = max(1, max_revision)
    return tuple(
        (
            _ROLE_PRIORITY[atom.role],
            1.0 if atom.status is AtomStatus.ACTIVE else 0.0,
            atom.revision / denominator,
            1.0 if atom.exact else 0.0,
            float(len(atom.depends_on)),
            1.0 if atom.core_required else 0.0,
        )
        for atom in atoms
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _candidate_conflict(
    left: Candidate,
    right: Candidate,
    atom_by_id: dict[str, SemanticAtom],
) -> int:
    for left_id in left.covers:
        left_atom = atom_by_id.get(left_id)
        if left_atom is None or left_atom.status is not AtomStatus.ACTIVE:
            continue
        for right_id in right.covers:
            right_atom = atom_by_id.get(right_id)
            if right_atom is None or right_atom.status is not AtomStatus.ACTIVE:
                continue
            if left_atom.revision == right_atom.revision:
                continue
            if not set(left_atom.semantic_keys).isdisjoint(right_atom.semantic_keys):
                return 1
    return 0


def build_matrix(
    atoms: tuple[SemanticAtom, ...],
    candidates: tuple[Candidate, ...],
    budget: int,
) -> MatrixBundle:
    """Build immutable CRM feature and relation matrices."""
    ordered_atoms = tuple(sorted(atoms, key=lambda atom: atom.atom_id))
    ordered_candidates = tuple(
        sorted(candidates, key=lambda candidate: candidate.candidate_id)
    )
    atom_by_id = {atom.atom_id: atom for atom in ordered_atoms}
    atom_ids = tuple(atom.atom_id for atom in ordered_atoms)
    coverage_sets = tuple(
        frozenset(candidate.covers) for candidate in ordered_candidates
    )

    coverage = tuple(
        tuple(1 if atom.atom_id in covered else 0 for covered in coverage_sets)
        for atom in ordered_atoms
    )
    redundancy = tuple(
        tuple(_jaccard(left, right) for right in coverage_sets)
        for left in coverage_sets
    )
    conflicts = tuple(
        tuple(
            0
            if left_index == right_index
            else _candidate_conflict(left, right, atom_by_id)
            for right_index, right in enumerate(ordered_candidates)
        )
        for left_index, left in enumerate(ordered_candidates)
    )
    dependencies = tuple(
        tuple(1 if atom_id in atom.depends_on else 0 for atom_id in atom_ids)
        for atom in ordered_atoms
    )

    return MatrixBundle(
        atom_ids=atom_ids,
        candidate_ids=tuple(candidate.candidate_id for candidate in ordered_candidates),
        features=_feature_rows(ordered_atoms),
        coverage=coverage,
        redundancy=redundancy,
        conflicts=conflicts,
        dependencies=dependencies,
        costs=tuple(candidate.byte_cost for candidate in ordered_candidates),
        risks=tuple(candidate.error_risk for candidate in ordered_candidates),
        weights=tuple(atom.weight for atom in ordered_atoms),
        budget=budget,
    )
