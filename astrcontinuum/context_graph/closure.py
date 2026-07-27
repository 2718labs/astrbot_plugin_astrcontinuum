"""Bounded dependency closure and final-pack validation."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import EventType
from ..runtime.types import CandidateBlock, CandidateKind


class ClosureError(RuntimeError):
    """Stable failure raised before dependency expansion exceeds its bound."""


@dataclass(frozen=True, slots=True)
class DependencyClosure:
    blocks: tuple[CandidateBlock, ...]


def _tool_coordinate(block: CandidateBlock) -> tuple[EventType, str] | None:
    if (
        block.kind is not CandidateKind.RAW_EVENT
        or block.event_type not in {EventType.TOOL_CALL, EventType.TOOL_RESULT}
        or block.tool_name is None
    ):
        return None
    return block.event_type, block.tool_name


def _companions(left: CandidateBlock, right: CandidateBlock) -> bool:
    dependency_pair = (
        left.kind is CandidateKind.DEPENDENCY or right.kind is CandidateKind.DEPENDENCY
    )
    if dependency_pair:
        if left.capsule_id is not None and left.capsule_id == right.capsule_id:
            return True
        if set(left.source_event_ids) & set(right.source_event_ids):
            return True
    left_tool = _tool_coordinate(left)
    right_tool = _tool_coordinate(right)
    if left_tool is None or right_tool is None or left_tool[1] != right_tool[1]:
        return False
    call, result = (left, right) if left_tool[0] is EventType.TOOL_CALL else (right, left)
    return (
        call.event_type is EventType.TOOL_CALL
        and result.event_type is EventType.TOOL_RESULT
        and call.event_sequence is not None
        and result.event_sequence == call.event_sequence + 1
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
