"""Exact-small and bounded deterministic source selection for CRM v2."""

from __future__ import annotations

from dataclasses import replace

import pytest

from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    CandidatePlanV2,
    CandidatePolicyV2,
    DeltaEnvelopeV2,
    LogicalAtomV2,
    LogicalResolutionV2,
    PackingPolicyV2,
    PlanEvaluationV2,
    SolverModeV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.logical_v2 import resolve_latest_v2
from crm_experiment.matrix_v2 import build_source_matrix_v2
from crm_experiment.optimizer_v2 import (
    OptimizerWorkLimitV2,
    optimize_reference_v2,
    optimize_sources_v2,
)


def _atom(
    index: int,
    *,
    core: bool = False,
    role: AtomRole = AtomRole.CONTEXT,
    depends_on: tuple[str, ...] = (),
) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=f"optimizer-{index}",
        semantic_keys=(f"topic:{index:03d}",),
        role=role,
        text=f"shared laboratory payload {index:03d}",
        status=AtomStatus.ACTIVE,
        revision=1,
        as_of=1,
        provenance=(f"fixture:{index:03d}",),
        exact=False,
        depends_on=depends_on,
        core_required=core,
    )


def _resolution(*atoms: LogicalAtomV2) -> LogicalResolutionV2:
    return resolve_latest_v2(
        None,
        DeltaEnvelopeV2(0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))),
        key_registry_limit=256,
        max_semantic_key_bytes=128,
    )


def _matrix(
    atoms: tuple[LogicalAtomV2, ...],
    weights: tuple[float, ...],
    policy: CandidatePolicyV2,
    *,
    resident_ids: set[str] | None = None,
):
    complete = _resolution(*atoms)
    if resident_ids is None:
        resolution = complete
    else:
        resolution = replace(
            complete,
            active_records=tuple(
                record
                for record in complete.active_records
                if record.atom.source_id in resident_ids
            ),
        )
    weight_by_id = {
        atom.source_id: weight for atom, weight in zip(atoms, weights, strict=True)
    }
    weight_policy = WeightPolicyV2.create(
        version="optimizer-fixture-v1",
        source_weights=tuple(
            SourceWeightV2(source_id, weight_by_id[source_id])
            for source_id in sorted(weight_by_id)
        ),
    )
    packing = PackingPolicyV2(
        codec="dmc1-lcp-lcs-v1",
        immutable_source_ids=tuple(
            sorted(atom.source_id for atom in atoms if not atom.core_required)
        ),
        max_records_per_block=8,
        max_decoded_block_bytes=65_536,
    )
    return build_source_matrix_v2(resolution, weight_policy, packing, policy)


def _additive_oracle(cost_by_id: dict[str, int], *, fixed_bytes: int = 100):
    calls: list[CandidatePlanV2] = []

    def evaluate(plan: CandidatePlanV2) -> PlanEvaluationV2:
        calls.append(plan)
        resident_bytes = fixed_bytes + sum(
            cost_by_id[source_id] for source_id in plan.retained_source_ids
        )
        return PlanEvaluationV2(
            plan=plan,
            resident_bytes=resident_bytes,
            direct_equivalent_bytes=resident_bytes,
            packing_savings_bytes=0,
            emitted_pack_count=0,
            valid=True,
            reasons=(),
        )

    return evaluate, calls


def test_exact_small_uses_full_plan_oracle_and_finds_non_greedy_subset() -> None:
    atoms = tuple(_atom(index) for index in range(3))
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.EXACT_SMALL,
        exact_small_limit=3,
        reference_row_limit=3,
    )
    matrix = _matrix(atoms, (6.0, 5.0, 5.0), policy)
    costs = {
        atoms[0].source_id: 7,
        atoms[1].source_id: 5,
        atoms[2].source_id: 5,
    }
    oracle, _ = _additive_oracle(costs)

    selection = optimize_sources_v2(matrix, policy, 110, oracle)

    assert selection.solver_mode is SolverModeV2.EXACT_SMALL
    assert selection.plan.retained_source_ids == tuple(
        sorted((atoms[1].source_id, atoms[2].source_id))
    )
    assert selection.retained_weight == 10.0
    assert selection.omitted_weight == 6.0
    assert selection.evaluation.resident_bytes == 110
    assert selection.work.plans_evaluated == 8
    assert not selection.work.work_limit_hit


def test_exact_small_and_independent_reference_match_integer_corpus() -> None:
    atoms = tuple(_atom(index) for index in range(5))
    exact_policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.EXACT_SMALL,
        exact_small_limit=5,
        reference_row_limit=5,
    )
    reference_policy = replace(exact_policy, solver_mode=SolverModeV2.REFERENCE)
    matrix = _matrix(atoms, (1.0, 2.0, 3.0, 4.0, 5.0), exact_policy)
    costs = {
        atom.source_id: cost for atom, cost in zip(atoms, (3, 5, 7, 9, 11), strict=True)
    }
    exact_oracle, _ = _additive_oracle(costs, fixed_bytes=200)
    reference_oracle, _ = _additive_oracle(costs, fixed_bytes=200)

    exact = optimize_sources_v2(matrix, exact_policy, 221, exact_oracle)
    reference = optimize_reference_v2(matrix, reference_policy, 221, reference_oracle)

    assert exact.plan == reference.plan
    assert exact.retained_weight == reference.retained_weight
    assert exact.omitted_weight == reference.omitted_weight
    assert exact.evaluation.resident_bytes == reference.evaluation.resident_bytes
    assert exact.work.plans_evaluated == reference.work.plans_evaluated == 32


def test_noncore_dependency_receipt_does_not_force_released_payload_resurrection() -> (
    None
):
    dependency = _atom(0)
    dependent = _atom(1, depends_on=(dependency.source_id,))
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.EXACT_SMALL,
        exact_small_limit=2,
        reference_row_limit=2,
    )
    matrix = _matrix(
        (dependency, dependent),
        (1.0, 5.0),
        policy,
        resident_ids={dependent.source_id},
    )
    oracle, _ = _additive_oracle({dependent.source_id: 20})

    selection = optimize_sources_v2(matrix, policy, 120, oracle)

    assert selection.plan.retained_source_ids == (dependent.source_id,)
    assert selection.released_source_ids == (dependency.source_id,)


def test_greedy_n72_is_deterministic_and_respects_frozen_work_limits() -> None:
    atoms = tuple(_atom(index) for index in range(72))
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.GREEDY,
        exact_small_limit=8,
        reference_row_limit=8,
        max_plan_evaluations=400,
        max_greedy_steps=8,
        max_refinement_evaluations=24,
    )
    matrix = _matrix(atoms, tuple(float(index + 1) for index in range(72)), policy)
    costs = {atom.source_id: 10 for atom in atoms}
    first_oracle, _ = _additive_oracle(costs, fixed_bytes=100)
    second_oracle, _ = _additive_oracle(costs, fixed_bytes=100)

    first = optimize_sources_v2(matrix, policy, 180, first_oracle)
    second = optimize_sources_v2(matrix, policy, 180, second_oracle)

    assert first == second
    assert first.solver_mode is SolverModeV2.GREEDY
    assert first.evaluation.resident_bytes <= 180
    assert first.work.greedy_steps <= policy.max_greedy_steps
    assert first.work.refinement_evaluations <= policy.max_refinement_evaluations
    assert first.work.oracle_evaluations <= policy.max_plan_evaluations


def test_explicit_exact_work_overflow_fails_before_calling_oracle() -> None:
    atoms = tuple(_atom(index) for index in range(10))
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.EXACT_SMALL,
        exact_small_limit=10,
        reference_row_limit=10,
        max_plan_evaluations=100,
        max_refinement_evaluations=0,
    )
    matrix = _matrix(atoms, (1.0,) * 10, policy)
    oracle, calls = _additive_oracle({atom.source_id: 1 for atom in atoms})

    with pytest.raises(OptimizerWorkLimitV2, match="before evaluation"):
        optimize_sources_v2(matrix, policy, 200, oracle)

    assert calls == []


def test_kernel_only_is_a_valid_optimizer_result_without_budget_overshoot() -> None:
    core = _atom(0, core=True, role=AtomRole.ROOT_GOAL)
    body = _atom(1)
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.EXACT_SMALL,
        exact_small_limit=1,
        reference_row_limit=1,
    )
    matrix = _matrix((core, body), (10.0, 1.0), policy)
    oracle, _ = _additive_oracle(
        {core.source_id: 50, body.source_id: 100}, fixed_bytes=100
    )

    selection = optimize_sources_v2(matrix, policy, 150, oracle)

    assert selection.plan.retained_source_ids == (core.source_id,)
    assert selection.released_source_ids == (body.source_id,)
    assert selection.evaluation.resident_bytes == 150
    assert selection.evaluation.resident_bytes <= 150


def test_near_float_tie_routes_to_exact_local_reference_comparator() -> None:
    atoms = tuple(_atom(index) for index in range(2))
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.GREEDY,
        exact_small_limit=0,
        reference_row_limit=2,
        max_plan_evaluations=20,
        max_greedy_steps=1,
        max_refinement_evaluations=0,
        near_tie_epsilon=1e-9,
    )
    matrix = _matrix(atoms, (1.0, 1.0), policy)
    oracle, _ = _additive_oracle(
        {atoms[0].source_id: 10, atoms[1].source_id: 10}, fixed_bytes=100
    )

    selection = optimize_sources_v2(matrix, policy, 110, oracle)

    assert selection.plan.retained_source_ids == (
        min(atom.source_id for atom in atoms),
    )
    assert selection.work.reference_tie_breaks >= 1


def test_nonpositive_marginal_infinity_tie_uses_exact_local_comparator() -> None:
    atoms = tuple(_atom(index) for index in range(2))
    low_id, high_id = sorted(atom.source_id for atom in atoms)
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.GREEDY,
        exact_small_limit=0,
        reference_row_limit=2,
        max_plan_evaluations=20,
        max_greedy_steps=1,
        max_refinement_evaluations=0,
    )
    matrix = _matrix(
        atoms,
        tuple(1.0 if atom.source_id == low_id else 10.0 for atom in atoms),
        policy,
    )
    resident_by_plan = {
        (): 100,
        (low_id,): 90,
        (high_id,): 90,
        (low_id, high_id): 110,
    }

    def oracle(plan: CandidatePlanV2) -> PlanEvaluationV2:
        resident_bytes = resident_by_plan[plan.retained_source_ids]
        return PlanEvaluationV2(
            plan=plan,
            resident_bytes=resident_bytes,
            direct_equivalent_bytes=resident_bytes,
            packing_savings_bytes=0,
            emitted_pack_count=0,
            valid=True,
            reasons=(),
        )

    selection = optimize_sources_v2(matrix, policy, 100, oracle)

    assert selection.plan.retained_source_ids == (high_id,)
    assert selection.retained_weight == 10.0
    assert selection.work.reference_tie_breaks >= 1
    assert selection.work.reference_tie_breaks <= selection.work.oracle_evaluations


def test_greedy_drop_only_refinement_removes_zero_weight_nonmonotonic_source() -> None:
    atoms = tuple(_atom(index) for index in range(5))
    source_ids = tuple(sorted(atom.source_id for atom in atoms))
    source_a, source_b, source_c, source_d, source_e = source_ids
    policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.GREEDY,
        exact_small_limit=0,
        reference_row_limit=5,
        max_plan_evaluations=60,
        max_greedy_steps=5,
        max_refinement_evaluations=12,
    )
    weights_by_source = {
        source_a: 10.0,
        source_b: 0.0,
        source_c: 1.0,
        source_d: 1.0,
        source_e: 0.0,
    }
    matrix = _matrix(
        atoms,
        tuple(weights_by_source[atom.source_id] for atom in atoms),
        policy,
    )
    resident_by_sources = {
        frozenset(): 100,
        frozenset((source_a,)): 105,
        frozenset((source_b,)): 100,
        frozenset((source_a, source_b)): 100,
        frozenset((source_a, source_b, source_c)): 110,
        frozenset((source_a, source_b, source_c, source_d)): 115,
        frozenset((source_a, source_c, source_d)): 105,
    }

    def oracle(plan: CandidatePlanV2) -> PlanEvaluationV2:
        resident_bytes = resident_by_sources.get(
            frozenset(plan.retained_source_ids), 130
        )
        return PlanEvaluationV2(
            plan=plan,
            resident_bytes=resident_bytes,
            direct_equivalent_bytes=resident_bytes,
            packing_savings_bytes=0,
            emitted_pack_count=0,
            valid=True,
            reasons=(),
        )

    selection = optimize_sources_v2(matrix, policy, 120, oracle)

    assert selection.plan.retained_source_ids == (source_a, source_c, source_d)
    assert selection.evaluation.resident_bytes == 105
    assert selection.work.refinement_evaluations >= 1
    assert selection.work.refinement_evaluations <= policy.max_refinement_evaluations
