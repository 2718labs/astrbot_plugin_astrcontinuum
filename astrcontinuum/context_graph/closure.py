"""Bounded dependency closure and final-pack validation."""

from __future__ import annotations

from dataclasses import dataclass

from ..runtime.types import CandidateBlock, CandidateKind


class ClosureError(RuntimeError):
    """Stable failure raised before dependency expansion exceeds its bound."""


@dataclass(frozen=True, slots=True)
class DependencyClosure:
    blocks: tuple[CandidateBlock, ...]


def _companions(left: CandidateBlock, right: CandidateBlock) -> bool:
    dependency_pair = (
        left.kind is CandidateKind.DEPENDENCY or right.kind is CandidateKind.DEPENDENCY
    )
    if dependency_pair:
        if left.capsule_id is not None and left.capsule_id == right.capsule_id:
            return True
        if set(left.source_event_ids) & set(right.source_event_ids):
            return True
    return False


def dependency_groups(
    candidates: tuple[CandidateBlock, ...],
) -> tuple[tuple[CandidateBlock, ...], ...]:
    """Return deterministic structural connected components."""

    if not isinstance(candidates, tuple) or any(
        not isinstance(item, CandidateBlock) for item in candidates
    ):
        raise TypeError("candidates must be a tuple of CandidateBlock values")
    if len({item.block_id for item in candidates}) != len(candidates):
        raise ClosureError("DEPENDENCY_GROUP_DUPLICATE_BLOCK")

    groups: list[tuple[CandidateBlock, ...]] = []
    remaining = set(range(len(candidates)))
    while remaining:
        pending = [min(remaining)]
        component_indexes: set[int] = set()
        while pending:
            index = pending.pop()
            if index in component_indexes:
                continue
            component_indexes.add(index)
            remaining.discard(index)
            for other in tuple(remaining):
                if _companions(candidates[index], candidates[other]):
                    pending.append(other)
        groups.append(tuple(candidates[index] for index in sorted(component_indexes)))
    return tuple(groups)


def removable_dependency_groups(
    candidates: tuple[CandidateBlock, ...],
) -> tuple[tuple[CandidateBlock, ...], ...]:
    """Return all-optional structural components that can be removed atomically."""

    return tuple(
        group for group in dependency_groups(candidates) if not any(item.required for item in group)
    )


def close_dependency_closure(
    selected_blocks: tuple[CandidateBlock, ...],
    universe: tuple[CandidateBlock, ...],
    *,
    max_blocks: int,
) -> DependencyClosure:
    """Add required blocks and recursively close structural companions."""

    if isinstance(max_blocks, bool) or not isinstance(max_blocks, int) or max_blocks < 1:
        raise ValueError("max_blocks must be a positive integer")
    available = tuple(universe)
    if any(not isinstance(item, CandidateBlock) for item in available):
        raise TypeError("universe must contain CandidateBlock values")
    by_id = {item.block_id: item for item in available}
    if len(by_id) != len(available):
        raise ClosureError("DEPENDENCY_CLOSURE_DUPLICATE_BLOCK")
    selected_ids: set[str] = set()
    for item in selected_blocks:
        if not isinstance(item, CandidateBlock) or item.block_id not in by_id:
            raise ClosureError("DEPENDENCY_CLOSURE_UNKNOWN_BLOCK")
        selected_ids.add(item.block_id)
    selected_ids.update(item.block_id for item in available if item.required)
    if len(selected_ids) > max_blocks:
        raise ClosureError("DEPENDENCY_CLOSURE_CAPACITY_EXCEEDED")

    changed = True
    while changed:
        changed = False
        active = tuple(item for item in available if item.block_id in selected_ids)
        for left in active:
            for right in available:
                if right.block_id in selected_ids or not _companions(left, right):
                    continue
                if len(selected_ids) >= max_blocks:
                    raise ClosureError("DEPENDENCY_CLOSURE_CAPACITY_EXCEEDED")
                selected_ids.add(right.block_id)
                changed = True
    return DependencyClosure(
        blocks=tuple(item for item in available if item.block_id in selected_ids)
    )


def validate_dependency_closure(
    packed_blocks: tuple[CandidateBlock, ...],
    universe: tuple[CandidateBlock, ...],
    *,
    max_blocks: int,
) -> bool:
    """Mechanically prove that a final pack already contains its full closure."""

    try:
        closure = close_dependency_closure(
            packed_blocks,
            universe,
            max_blocks=max_blocks,
        )
    except (ClosureError, TypeError, ValueError):
        return False
    packed_ids = tuple(item.block_id for item in packed_blocks)
    return packed_ids == tuple(item.block_id for item in closure.blocks)
