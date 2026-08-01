"""Exact continuity-kernel admission tests for CRM v2."""

from __future__ import annotations

from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    KeyWinnerV2,
    LogicalAtomV2,
    SourceReceiptV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.kernel_v2 import default_kernel_schema_v2, select_kernel_v2


def _atom(
    label: str = "goal",
    *,
    key: str = "goal",
    role: AtomRole = AtomRole.ROOT_GOAL,
    text: str = "preserve continuity",
    provenance: tuple[str, ...] = ("fixture",),
    status: AtomStatus = AtomStatus.ACTIVE,
    revision: int = 1,
    core: bool = True,
) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=label,
        semantic_keys=(key,),
        role=role,
        text=text,
        status=status,
        revision=revision,
        as_of=1,
        provenance=provenance,
        exact=False,
        depends_on=(),
        core_required=core,
    )


def _record(atom: LogicalAtomV2) -> ActiveRecordV2:
    return ActiveRecordV2(atom, atom.semantic_keys)


def _frontier(*atoms: LogicalAtomV2) -> tuple[KeyWinnerV2, ...]:
    return tuple(
        sorted(
            (KeyWinnerV2.from_atom(atom, atom.semantic_keys[0]) for atom in atoms),
            key=lambda winner: winner.semantic_key,
        )
    )


def _receipts(*atoms: LogicalAtomV2) -> tuple[SourceReceiptV2, ...]:
    return tuple(
        sorted(
            (SourceReceiptV2.from_atom(atom) for atom in atoms),
            key=lambda receipt: receipt.source_id,
        )
    )


def _policy(frontier: tuple[KeyWinnerV2, ...]) -> WeightPolicyV2:
    source_ids = sorted(
        {winner.source_id for winner in frontier if winner.status is AtomStatus.ACTIVE}
    )
    return WeightPolicyV2.create(
        version="theta0",
        source_weights=tuple(
            SourceWeightV2(source_id, 1.0) for source_id in source_ids
        ),
    )


def _select(
    atom: LogicalAtomV2,
    budget: int = 4608,
    *,
    frontier: tuple[KeyWinnerV2, ...] | None = None,
    frontier_atoms: tuple[LogicalAtomV2, ...] = (),
):
    actual_frontier = _frontier(atom) if frontier is None else frontier
    control_atoms = (atom,) + frontier_atoms
    return select_kernel_v2(
        (_record(atom),),
        actual_frontier,
        _receipts(*control_atoms),
        _policy(actual_frontier),
        default_kernel_schema_v2(),
        generation=1,
        high_water=1,
        requested_budget=budget,
        key_registry_limit=64,
        max_semantic_key_bytes=64,
    )


def test_normal_kernel_is_exactly_sized_and_valid() -> None:
    goal = _atom()
    selected = _select(goal)

    assert selected.valid is True
    assert tuple(record.atom.source_id for record in selected.records) == (
        goal.source_id,
    )
    assert selected.resident_bytes is not None
    assert 0 < selected.resident_bytes <= 4608
    assert selected.reasons == ()


def test_long_metadata_cannot_bypass_the_continuity_budget() -> None:
    selected = _select(_atom(provenance=("p" * 5000,)))

    assert selected.valid is False
    assert selected.resident_bytes is not None
    assert selected.resident_bytes > 4608
    assert "kernel_budget_exceeded" in selected.reasons


def test_budget_below_k_is_admission_blocked_even_when_payload_is_small() -> None:
    selected = _select(_atom(), budget=4607)

    assert selected.valid is False
    assert "admission_below_continuity_floor" in selected.reasons


def test_text_and_slot_overflow_are_fail_closed() -> None:
    oversized = _atom(text="x" * 193)
    duplicate = _atom("goal-b", key="goal-b")
    records = (_record(oversized), _record(duplicate))
    frontier = _frontier(oversized, duplicate)
    selected = select_kernel_v2(
        records,
        frontier,
        _receipts(oversized, duplicate),
        _policy(frontier),
        default_kernel_schema_v2(),
        generation=1,
        high_water=1,
        requested_budget=4608,
        key_registry_limit=64,
        max_semantic_key_bytes=64,
    )

    assert selected.valid is False
    assert "slot_overflow:root_goal" in selected.reasons
    assert f"text_overflow:{oversized.source_id}" in selected.reasons


def test_kernel_dependencies_must_be_closed_inside_the_kernel() -> None:
    dependency = _atom(
        "dependency",
        key="dependency",
        role=AtomRole.CONTEXT,
        core=False,
    )
    dependent = LogicalAtomV2.create(
        source_label="goal-dependent",
        semantic_keys=("goal",),
        role=AtomRole.ROOT_GOAL,
        text="preserve continuity",
        status=AtomStatus.ACTIVE,
        revision=1,
        as_of=1,
        provenance=("fixture",),
        exact=False,
        depends_on=(dependency.source_id,),
        core_required=True,
    )

    selected = _select(
        dependent,
        frontier=_frontier(dependent, dependency),
        frontier_atoms=(dependency,),
    )

    assert selected.valid is False
    assert f"kernel_dependency_missing:{dependency.source_id}" in selected.reasons


def test_missing_core_payload_returns_invalid_selection_instead_of_raising() -> None:
    goal = _atom()
    frontier = _frontier(goal)

    selected = select_kernel_v2(
        (),
        frontier,
        _receipts(goal),
        _policy(frontier),
        default_kernel_schema_v2(),
        generation=1,
        high_water=1,
        requested_budget=4608,
        key_registry_limit=64,
        max_semantic_key_bytes=64,
    )

    assert selected.valid is False
    assert selected.resident_bytes is None
    assert f"core_payload_missing:{goal.source_id}" in selected.reasons


def test_large_mandatory_frontier_can_make_kernel_admission_fail() -> None:
    goal = _atom()
    tombstones = tuple(
        _atom(
            f"tombstone-{index}",
            key=f"released-context-key-{index:02d}",
            role=AtomRole.CONTEXT,
            status=AtomStatus.RETRACTED,
            revision=2,
            core=False,
        )
        for index in range(24)
    )
    frontier = _frontier(goal, *tombstones)

    selected = _select(goal, frontier=frontier, frontier_atoms=tombstones)

    assert selected.resident_bytes is not None
    assert selected.resident_bytes > 4608
    assert selected.valid is False
    assert "kernel_budget_exceeded" in selected.reasons
