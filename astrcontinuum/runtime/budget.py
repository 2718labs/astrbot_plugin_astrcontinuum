from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NoReturn

from ..domain import EventType
from ..storage import RequestView
from .types import (
    RUNTIME_SLOT_PRIORITY,
    AssemblyMode,
    AssemblyResult,
    AssemblyTrace,
    BlockRejection,
    BlockSelection,
    BudgetConfig,
    BudgetErrorCode,
    CandidateBlock,
    RuntimeSlot,
    TokenCounter,
)

_SEPARATOR = "\n\n"
_SLOT_RANK = {slot: rank for rank, slot in enumerate(RUNTIME_SLOT_PRIORITY)}


class BudgetInvariantError(ValueError):
    """Report one stable budget failure without content."""

    def __init__(self, code: BudgetErrorCode) -> None:
        self.code = code.value
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class Utf8ByteTokenCounter:
    """Conservatively count one token per UTF-8 byte."""

    def count_text(self, text: str) -> int:
        return len(text.encode("utf-8"))


def _invalid(code: BudgetErrorCode) -> NoReturn:
    raise BudgetInvariantError(code)


def _non_negative_cost(value: object, code: BudgetErrorCode) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(code)
    return value


def _count_text(counter: TokenCounter, text: str) -> int:
    try:
        value = counter.count_text(text)
    except Exception as error:
        from ..tokenization.types import TokenizerError

        if isinstance(error, TokenizerError):
            raise
        raise BudgetInvariantError(BudgetErrorCode.TOKEN_COUNTER_FAILURE) from None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(BudgetErrorCode.TOKEN_COUNTER_INVALID)
    return value


def _candidate_sort_key(
    candidate: CandidateBlock,
) -> tuple[object, ...]:
    raw_order = (
        candidate.event_sequence
        if candidate.slot in (RuntimeSlot.RAW_DELTA, RuntimeSlot.RECENT_RAW)
        and candidate.event_sequence is not None
        else 0
    )
    score_order = (
        0.0
        if candidate.slot in (RuntimeSlot.RAW_DELTA, RuntimeSlot.RECENT_RAW)
        else -candidate.score
    )
    return (
        _SLOT_RANK[candidate.slot],
        raw_order,
        score_order,
        candidate.block_id,
        candidate.kind.value,
        candidate.text,
        candidate.source_event_ids,
        candidate.reason,
        candidate.required,
        candidate.capsule_id or "",
    )


def _ordered(candidates: Iterable[CandidateBlock]) -> tuple[CandidateBlock, ...]:
    return tuple(sorted(candidates, key=_candidate_sort_key))


def _projection_text(candidates: Iterable[CandidateBlock]) -> str:
    return _SEPARATOR.join(candidate.text for candidate in candidates)


def _rejection(
    candidate: CandidateBlock,
    *,
    block_token_counts: Mapping[str, int],
    reason: str,
) -> BlockRejection:
    return BlockRejection(
        block_id=candidate.block_id,
        slot=candidate.slot,
        source_event_ids=candidate.source_event_ids,
        token_cost=block_token_counts[candidate.block_id],
        score=candidate.score,
        reason=reason,
    )


def _deduplicate(
    candidates: Iterable[CandidateBlock],
) -> tuple[tuple[CandidateBlock, ...], tuple[CandidateBlock, ...]]:
    unique: list[CandidateBlock] = []
    duplicates: list[CandidateBlock] = []
    seen: set[str] = set()
    for candidate in _ordered(candidates):
        if candidate.block_id in seen:
            duplicates.append(candidate)
            continue
        seen.add(candidate.block_id)
        unique.append(candidate)
    return tuple(unique), tuple(duplicates)


def canonical_candidates(
    candidates: Iterable[CandidateBlock],
) -> tuple[CandidateBlock, ...]:
    """Return the deterministic candidate universe used by assembly."""

    unique, _duplicates = _deduplicate(candidates)
    return unique


def count_candidate_blocks(
    candidates: tuple[CandidateBlock, ...],
    counter: TokenCounter,
) -> dict[str, int]:
    """Count each unique candidate text exactly once into a content-free map."""

    counts: dict[str, int] = {}
    for candidate in candidates:
        if candidate.block_id in counts:
            _invalid(BudgetErrorCode.INTERNAL_BUDGET_INVARIANT)
        counts[candidate.block_id] = _count_text(counter, candidate.text)
    return counts


def _validated_block_token_counts(
    candidates: tuple[CandidateBlock, ...],
    *,
    counter: TokenCounter,
    provided: Mapping[str, int] | None,
) -> dict[str, int]:
    if provided is None:
        return count_candidate_blocks(candidates, counter)
    if not isinstance(provided, Mapping):
        _invalid(BudgetErrorCode.TOKEN_COUNTER_INVALID)
    counts: dict[str, int] = {}
    for candidate in candidates:
        if candidate.block_id not in provided:
            _invalid(BudgetErrorCode.TOKEN_COUNTER_INVALID)
        counts[candidate.block_id] = _non_negative_cost(
            provided[candidate.block_id],
            BudgetErrorCode.TOKEN_COUNTER_INVALID,
        )
    return counts


def _estimated_projection_cost(
    candidates: tuple[CandidateBlock, ...],
    *,
    block_token_counts: Mapping[str, int],
    separator_cost: int,
) -> int:
    if not candidates:
        return 0
    return sum(block_token_counts[item.block_id] for item in candidates) + (
        separator_cost * (len(candidates) - 1)
    )


def _dependency_groups(
    candidates: tuple[CandidateBlock, ...],
) -> tuple[tuple[CandidateBlock, ...], ...]:
    # This lazy import avoids a package-initialization cycle: context_graph.live
    # imports this module while the runtime package is being initialized.
    from ..context_graph.closure import dependency_groups

    return dependency_groups(candidates)


def _removable_dependency_groups(
    candidates: tuple[CandidateBlock, ...],
) -> tuple[tuple[CandidateBlock, ...], ...]:
    from ..context_graph.closure import removable_dependency_groups

    return removable_dependency_groups(candidates)


def _normal_selection(
    candidates: tuple[CandidateBlock, ...],
    *,
    block_token_counts: Mapping[str, int],
    separator_cost: int,
    b_ac: int,
) -> tuple[
    tuple[CandidateBlock, ...],
    tuple[BlockRejection, ...],
    bool,
]:
    dependency_groups = _dependency_groups(candidates)
    group_by_id = {candidate.block_id: group for group in dependency_groups for candidate in group}
    selected_ids: set[str] = set()
    selected_cost = 0
    selected_count = 0
    rejected: list[BlockRejection] = []
    handled_ids: set[str] = set()
    required_missing = False
    for candidate in candidates:
        if candidate.block_id in handled_ids:
            continue
        group = group_by_id.get(candidate.block_id, (candidate,))
        handled_ids.update(item.block_id for item in group)
        additional_cost = sum(block_token_counts[item.block_id] for item in group)
        additional_cost += separator_cost * (len(group) - 1)
        if selected_count:
            additional_cost += separator_cost
        if selected_cost + additional_cost <= b_ac:
            selected_ids.update(item.block_id for item in group)
            selected_cost += additional_cost
            selected_count += len(group)
            continue
        rejected.extend(
            _rejection(
                item,
                block_token_counts=block_token_counts,
                reason="INSUFFICIENT_AC_BUDGET",
            )
            for item in group
        )
        required_missing = required_missing or any(item.required for item in group)
    return (
        tuple(item for item in candidates if item.block_id in selected_ids),
        tuple(rejected),
        required_missing,
    )


def _contiguous_raw_tail(
    candidates: tuple[CandidateBlock, ...],
) -> tuple[CandidateBlock, ...]:
    ordered = tuple(candidate for candidate in candidates if candidate.event_sequence is not None)
    if not ordered:
        return ()
    start = len(ordered) - 1
    while start > 0:
        previous = ordered[start - 1].event_sequence
        current = ordered[start].event_sequence
        if previous is None or current is None or previous + 1 != current:
            break
        start -= 1
    return ordered[start:]


def _is_tool_pair(group: tuple[CandidateBlock, ...]) -> bool:
    if len(group) != 2:
        return False
    left, right = group
    return (
        left.event_type is EventType.TOOL_CALL
        and right.event_type is EventType.TOOL_RESULT
        and left.tool_name is not None
        and left.tool_name == right.tool_name
        and left.event_sequence is not None
        and right.event_sequence == left.event_sequence + 1
    )


def _longest_raw_suffix(
    critical: tuple[CandidateBlock, ...],
    raw_tail: tuple[CandidateBlock, ...],
    *,
    block_token_counts: Mapping[str, int],
    separator_cost: int,
    b_ac: int,
) -> tuple[CandidateBlock, ...]:
    units: list[tuple[CandidateBlock, ...]] = []
    index = 0
    while index < len(raw_tail):
        current = raw_tail[index]
        if index + 1 < len(raw_tail):
            following = raw_tail[index + 1]
            if (
                current.event_type is EventType.TOOL_CALL
                and following.event_type is EventType.TOOL_RESULT
                and current.tool_name is not None
                and current.tool_name == following.tool_name
                and current.event_sequence is not None
                and following.event_sequence == current.event_sequence + 1
            ):
                units.append((current, following))
                index += 2
                continue
        if current.event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT}:
            index += 1
            continue
        units.append((current,))
        index += 1

    suffix_costs = [0] * (len(units) + 1)
    suffix_counts = [0] * (len(units) + 1)
    for unit_index in range(len(units) - 1, -1, -1):
        suffix_costs[unit_index] = suffix_costs[unit_index + 1] + sum(
            block_token_counts[item.block_id] for item in units[unit_index]
        )
        suffix_counts[unit_index] = suffix_counts[unit_index + 1] + len(units[unit_index])
    critical_cost = sum(block_token_counts[item.block_id] for item in critical)
    critical_count = len(critical)
    for start in range(len(units) + 1):
        total_count = critical_count + suffix_counts[start]
        estimated = critical_cost + suffix_costs[start]
        if total_count:
            estimated += separator_cost * (total_count - 1)
        if estimated <= b_ac:
            return tuple(block for unit in units[start:] for block in unit)
    return ()


def _emergency_selection(
    candidates: tuple[CandidateBlock, ...],
    *,
    block_token_counts: Mapping[str, int],
    separator_cost: int,
    b_ac: int,
) -> tuple[tuple[CandidateBlock, ...], tuple[BlockRejection, ...]]:
    dependency_groups = _dependency_groups(candidates)
    group_by_id = {candidate.block_id: group for group in dependency_groups for candidate in group}
    selected_ids: set[str] = set()
    selected_cost = 0
    selected_count = 0
    rejected: list[BlockRejection] = []
    handled_ids: set[str] = set()
    raw_slots = (RuntimeSlot.RAW_DELTA, RuntimeSlot.RECENT_RAW)
    for slot in RUNTIME_SLOT_PRIORITY:
        slot_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.slot == slot and candidate.block_id not in handled_ids
        )
        if slot in raw_slots:
            atomic_groups: list[tuple[CandidateBlock, ...]] = []
            atomic_group_ids: set[str] = set()
            for candidate in slot_candidates:
                group = group_by_id.get(candidate.block_id, (candidate,))
                if (
                    len(group) == 1
                    or (all(item.slot is slot for item in group) and _is_tool_pair(group))
                    or candidate.block_id in atomic_group_ids
                ):
                    continue
                atomic_groups.append(group)
                atomic_group_ids.update(item.block_id for item in group)
            for group in atomic_groups:
                handled_ids.update(item.block_id for item in group)
                if not any(item.required for item in group):
                    rejected.extend(
                        _rejection(
                            item,
                            block_token_counts=block_token_counts,
                            reason="EMERGENCY_OPTIONAL_OMITTED",
                        )
                        for item in group
                    )
                    continue
                additional_cost = sum(block_token_counts[item.block_id] for item in group)
                additional_cost += separator_cost * (len(group) - 1)
                if selected_count:
                    additional_cost += separator_cost
                if selected_cost + additional_cost <= b_ac:
                    selected_ids.update(item.block_id for item in group)
                    selected_cost += additional_cost
                    selected_count += len(group)
                    continue
                rejected.extend(
                    _rejection(
                        item,
                        block_token_counts=block_token_counts,
                        reason="EMERGENCY_REQUIRED_NOT_FIT",
                    )
                    for item in group
                )
            slot_candidates = tuple(
                candidate for candidate in slot_candidates if candidate.block_id not in handled_ids
            )
            raw_tail = _contiguous_raw_tail(slot_candidates)
            selected_raw = _longest_raw_suffix(
                tuple(item for item in candidates if item.block_id in selected_ids),
                raw_tail,
                block_token_counts=block_token_counts,
                separator_cost=separator_cost,
                b_ac=b_ac,
            )
            selected_raw_ids = {candidate.block_id for candidate in selected_raw}
            for candidate in selected_raw:
                if selected_count:
                    selected_cost += separator_cost
                selected_cost += block_token_counts[candidate.block_id]
                selected_count += 1
                selected_ids.add(candidate.block_id)
            handled_ids.update(candidate.block_id for candidate in slot_candidates)
            for candidate in slot_candidates:
                if candidate.block_id not in selected_raw_ids:
                    rejected.append(
                        _rejection(
                            candidate,
                            block_token_counts=block_token_counts,
                            reason="EMERGENCY_RAW_PREFIX_OMITTED",
                        )
                    )
            continue

        for candidate in slot_candidates:
            if candidate.block_id in handled_ids:
                continue
            group = group_by_id.get(candidate.block_id, (candidate,))
            handled_ids.update(item.block_id for item in group)
            if not any(item.required for item in group):
                rejected.extend(
                    _rejection(
                        item,
                        block_token_counts=block_token_counts,
                        reason="EMERGENCY_OPTIONAL_OMITTED",
                    )
                    for item in group
                )
                continue
            additional_cost = sum(block_token_counts[item.block_id] for item in group)
            additional_cost += separator_cost * (len(group) - 1)
            if selected_count:
                additional_cost += separator_cost
            if selected_cost + additional_cost <= b_ac:
                selected_ids.update(item.block_id for item in group)
                selected_cost += additional_cost
                selected_count += len(group)
                continue
            rejected.extend(
                _rejection(
                    item,
                    block_token_counts=block_token_counts,
                    reason="EMERGENCY_REQUIRED_NOT_FIT",
                )
                for item in group
            )
    return (
        tuple(item for item in candidates if item.block_id in selected_ids),
        tuple(rejected),
    )


def _lowest_priority_optional_group(
    selected: tuple[CandidateBlock, ...],
) -> tuple[CandidateBlock, ...] | None:
    groups = _removable_dependency_groups(selected)
    if not groups:
        return None
    positions = {candidate.block_id: index for index, candidate in enumerate(selected)}
    return max(
        groups,
        key=lambda group: (
            min(positions[item.block_id] for item in group),
            tuple(item.block_id for item in group),
        ),
    )


def _exact_final_selection(
    selected: tuple[CandidateBlock, ...],
    *,
    counter: TokenCounter,
    block_token_counts: Mapping[str, int],
    b_ac: int,
) -> tuple[
    tuple[CandidateBlock, ...],
    str,
    int,
    tuple[BlockRejection, ...],
]:
    removed: list[BlockRejection] = []
    while True:
        projected_text = _projection_text(selected)
        exact_cost = _count_text(counter, projected_text)
        if exact_cost <= b_ac:
            return selected, projected_text, exact_cost, tuple(removed)
        group = _lowest_priority_optional_group(selected)
        if group is None:
            _invalid(BudgetErrorCode.REQUIRED_INPUT_EXCEEDS_BUDGET)
        removed_ids = {item.block_id for item in group}
        removed.extend(
            _rejection(
                item,
                block_token_counts=block_token_counts,
                reason="EXACT_BUDGET_COMPONENT_REMOVED",
            )
            for item in group
        )
        selected = tuple(item for item in selected if item.block_id not in removed_ids)


def _rejection_sort_key(rejection: BlockRejection) -> tuple[object, ...]:
    return (
        _SLOT_RANK[rejection.slot],
        rejection.block_id,
        rejection.reason,
        -rejection.score,
    )


def _trace(
    view: RequestView,
    selected: tuple[CandidateBlock, ...],
    rejections: tuple[BlockRejection, ...],
    *,
    mode: AssemblyMode,
    block_token_counts: Mapping[str, int],
    exact_projection_cost: int,
    b_input: int,
    current_input_cost: int,
    fixed_required_cost: int,
    safety_margin: int,
    b_required: int,
    opaque_token_cost: int,
    b_ac: int,
) -> AssemblyTrace:
    selections = tuple(
        BlockSelection(
            block_id=candidate.block_id,
            slot=candidate.slot,
            source_event_ids=candidate.source_event_ids,
            token_cost=block_token_counts[candidate.block_id],
            score=candidate.score,
            reason=("SELECTED_NORMAL" if mode == AssemblyMode.NORMAL else "SELECTED_EMERGENCY"),
        )
        for candidate in selected
    )
    slot_costs: list[tuple[RuntimeSlot, int]] = []
    for slot in RUNTIME_SLOT_PRIORITY:
        cost = sum(item.token_cost for item in selections if item.slot == slot)
        if cost:
            slot_costs.append((slot, cost))
    ac_selected_cost = exact_projection_cost
    projection_overhead_cost = ac_selected_cost - sum(item.token_cost for item in selections)
    total_input_cost = opaque_token_cost + b_required + ac_selected_cost
    if total_input_cost > b_input:
        _invalid(BudgetErrorCode.INTERNAL_BUDGET_INVARIANT)
    return AssemblyTrace(
        mode=mode,
        snapshot_id=view.snapshot.snapshot_id if view.snapshot is not None else None,
        pointer_version=view.pointer_version,
        covered_event_end=view.covered_event_end,
        high_water_mark=view.high_water_mark,
        b_input=b_input,
        current_input_cost=current_input_cost,
        fixed_required_cost=fixed_required_cost,
        safety_margin=safety_margin,
        b_required=b_required,
        opaque_token_cost=opaque_token_cost,
        b_ac=b_ac,
        ac_selected_cost=ac_selected_cost,
        projection_overhead_cost=projection_overhead_cost,
        total_input_cost=total_input_cost,
        slot_token_costs=tuple(slot_costs),
        selections=selections,
        rejections=tuple(sorted(rejections, key=_rejection_sort_key)),
    )


def assemble(
    view: RequestView,
    candidates: Iterable[CandidateBlock],
    *,
    current_input: str,
    opaque_token_cost: int,
    fixed_required_cost: int,
    counter: TokenCounter,
    config: BudgetConfig,
    block_token_counts: Mapping[str, int] | None = None,
) -> AssemblyResult:
    """Assemble one deterministic AC-owned projection within the exact input budget."""

    if not isinstance(current_input, str):
        _invalid(BudgetErrorCode.CURRENT_INPUT_INVALID)
    opaque_cost = _non_negative_cost(
        opaque_token_cost,
        BudgetErrorCode.OPAQUE_TOKEN_COST_INVALID,
    )
    fixed_cost = _non_negative_cost(
        fixed_required_cost,
        BudgetErrorCode.FIXED_REQUIRED_COST_INVALID,
    )
    current_input_cost = _count_text(counter, current_input)
    b_input = min(
        config.target_input_budget,
        config.hard_input_ceiling,
        config.model_context_limit - config.reserved_output_and_tools,
    )
    b_required = current_input_cost + fixed_cost + config.safety_margin
    b_ac = max(0, b_input - opaque_cost - b_required)
    if opaque_cost + b_required > b_input:
        _invalid(BudgetErrorCode.REQUIRED_INPUT_EXCEEDS_BUDGET)

    unique, duplicate_candidates = _deduplicate(candidates)
    counts = _validated_block_token_counts(
        unique,
        counter=counter,
        provided=block_token_counts,
    )
    separator_cost = _count_text(counter, _SEPARATOR)
    duplicate_rejections = tuple(
        BlockRejection(
            block_id=candidate.block_id,
            slot=candidate.slot,
            source_event_ids=candidate.source_event_ids,
            token_cost=_count_text(counter, candidate.text),
            score=candidate.score,
            reason="DUPLICATE_BLOCK_ID",
        )
        for candidate in duplicate_candidates
    )
    normal_selected, normal_rejections, required_missing = _normal_selection(
        unique,
        block_token_counts=counts,
        separator_cost=separator_cost,
        b_ac=b_ac,
    )
    if required_missing:
        mode = AssemblyMode.EMERGENCY_ASSEMBLY
        selected, emergency_rejections = _emergency_selection(
            unique,
            block_token_counts=counts,
            separator_cost=separator_cost,
            b_ac=b_ac,
        )
        rejections = (*duplicate_rejections, *emergency_rejections)
    else:
        mode = AssemblyMode.NORMAL
        selected = normal_selected
        rejections = (*duplicate_rejections, *normal_rejections)

    required_ids = {item.block_id for item in unique if item.required}
    selected_ids = {item.block_id for item in selected}
    if not required_ids.issubset(selected_ids):
        _invalid(BudgetErrorCode.REQUIRED_INPUT_EXCEEDS_BUDGET)

    selected, projected_text, exact_projection_cost, exact_rejections = _exact_final_selection(
        selected,
        counter=counter,
        block_token_counts=counts,
        b_ac=b_ac,
    )
    rejections = (*rejections, *exact_rejections)
    trace = _trace(
        view,
        selected,
        tuple(rejections),
        mode=mode,
        block_token_counts=counts,
        exact_projection_cost=exact_projection_cost,
        b_input=b_input,
        current_input_cost=current_input_cost,
        fixed_required_cost=fixed_cost,
        safety_margin=config.safety_margin,
        b_required=b_required,
        opaque_token_cost=opaque_cost,
        b_ac=b_ac,
    )
    return AssemblyResult(
        projected_text=projected_text,
        selected_blocks=selected,
        trace=trace,
    )
