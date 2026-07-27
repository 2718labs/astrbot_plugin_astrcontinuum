from __future__ import annotations

from collections.abc import Iterable
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
    except Exception:  # noqa: BLE001 - counter errors must cross a content-free boundary
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


def _projection_cost(
    candidates: Iterable[CandidateBlock],
    counter: TokenCounter,
) -> int:
    return _count_text(counter, _projection_text(candidates))


def _rejection(
    candidate: CandidateBlock,
    *,
    counter: TokenCounter,
    reason: str,
) -> BlockRejection:
    return BlockRejection(
        block_id=candidate.block_id,
        slot=candidate.slot,
        source_event_ids=candidate.source_event_ids,
        token_cost=_count_text(counter, candidate.text),
        score=candidate.score,
        reason=reason,
    )


def _deduplicate(
    candidates: Iterable[CandidateBlock],
    counter: TokenCounter,
) -> tuple[tuple[CandidateBlock, ...], tuple[BlockRejection, ...]]:
    unique: list[CandidateBlock] = []
    duplicates: list[BlockRejection] = []
    seen: set[str] = set()
    for candidate in _ordered(candidates):
        if candidate.block_id in seen:
            duplicates.append(
                _rejection(
                    candidate,
                    counter=counter,
                    reason="DUPLICATE_BLOCK_ID",
                )
            )
            continue
        seen.add(candidate.block_id)
        unique.append(candidate)
    return tuple(unique), tuple(duplicates)


def _normal_selection(
    candidates: tuple[CandidateBlock, ...],
    *,
    counter: TokenCounter,
    b_ac: int,
) -> tuple[
    tuple[CandidateBlock, ...],
    tuple[BlockRejection, ...],
    bool,
]:
    selected: list[CandidateBlock] = []
    rejected: list[BlockRejection] = []
    required_missing = False
    for candidate in candidates:
        proposed = (*selected, candidate)
        if _projection_cost(proposed, counter) <= b_ac:
            selected.append(candidate)
            continue
        rejected.append(
            _rejection(
                candidate,
                counter=counter,
                reason="INSUFFICIENT_AC_BUDGET",
            )
        )
        required_missing = required_missing or candidate.required
    return tuple(selected), tuple(rejected), required_missing


def _contiguous_raw_tail(
    candidates: tuple[CandidateBlock, ...],
) -> tuple[CandidateBlock, ...]:
    ordered = tuple(
        sorted(
            (candidate for candidate in candidates if candidate.event_sequence is not None),
            key=lambda candidate: (candidate.event_sequence or 0, candidate.block_id),
        )
    )
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


def _longest_raw_suffix(
    critical: tuple[CandidateBlock, ...],
    raw_tail: tuple[CandidateBlock, ...],
    *,
    counter: TokenCounter,
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

    for start in range(len(units) + 1):
        suffix = tuple(block for unit in units[start:] for block in unit)
        proposed = _ordered((*critical, *suffix))
        if _projection_cost(proposed, counter) <= b_ac:
            return suffix
    return ()


def _emergency_selection(
    candidates: tuple[CandidateBlock, ...],
    *,
    counter: TokenCounter,
    b_ac: int,
) -> tuple[tuple[CandidateBlock, ...], tuple[BlockRejection, ...]]:
    selected: list[CandidateBlock] = []
    rejected: list[BlockRejection] = []
    raw_slots = (RuntimeSlot.RAW_DELTA, RuntimeSlot.RECENT_RAW)
    for slot in RUNTIME_SLOT_PRIORITY:
        slot_candidates = tuple(candidate for candidate in candidates if candidate.slot == slot)
        if slot in raw_slots:
            raw_tail = _contiguous_raw_tail(slot_candidates)
            selected_raw = _longest_raw_suffix(
                tuple(selected),
                raw_tail,
                counter=counter,
                b_ac=b_ac,
            )
            selected_raw_ids = {candidate.block_id for candidate in selected_raw}
            selected.extend(selected_raw)
            for candidate in slot_candidates:
                if candidate.block_id not in selected_raw_ids:
                    rejected.append(
                        _rejection(
                            candidate,
                            counter=counter,
                            reason="EMERGENCY_RAW_PREFIX_OMITTED",
                        )
                    )
            continue

        for candidate in slot_candidates:
            if not candidate.required:
                rejected.append(
                    _rejection(
                        candidate,
                        counter=counter,
                        reason="EMERGENCY_OPTIONAL_OMITTED",
                    )
                )
                continue
            proposed = _ordered((*selected, candidate))
            if _projection_cost(proposed, counter) <= b_ac:
                selected.append(candidate)
                continue
            rejected.append(
                _rejection(
                    candidate,
                    counter=counter,
                    reason="EMERGENCY_REQUIRED_NOT_FIT",
                )
            )
    return _ordered(selected), tuple(rejected)


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
    counter: TokenCounter,
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
            token_cost=_count_text(counter, candidate.text),
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
    ac_selected_cost = _projection_cost(selected, counter)
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

    unique, duplicate_rejections = _deduplicate(candidates, counter)
    normal_selected, normal_rejections, required_missing = _normal_selection(
        unique,
        counter=counter,
        b_ac=b_ac,
    )
    if required_missing:
        mode = AssemblyMode.EMERGENCY_ASSEMBLY
        selected, emergency_rejections = _emergency_selection(
            unique,
            counter=counter,
            b_ac=b_ac,
        )
        rejections = (*duplicate_rejections, *emergency_rejections)
    else:
        mode = AssemblyMode.NORMAL
        selected = normal_selected
        rejections = (*duplicate_rejections, *normal_rejections)

    selected = _ordered(selected)
    trace = _trace(
        view,
        selected,
        tuple(rejections),
        mode=mode,
        counter=counter,
        b_input=b_input,
        current_input_cost=current_input_cost,
        fixed_required_cost=fixed_cost,
        safety_margin=config.safety_margin,
        b_required=b_required,
        opaque_token_cost=opaque_cost,
        b_ac=b_ac,
    )
    return AssemblyResult(
        projected_text=_projection_text(selected),
        selected_blocks=selected,
        trace=trace,
    )
