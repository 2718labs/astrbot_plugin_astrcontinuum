"""Bounded logical-source optimization with an exact state-cost oracle."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from itertools import combinations

from crm_experiment.contracts_v2 import (
    CandidatePlanV2,
    CandidatePolicyV2,
    OptimizerSelectionV2,
    OptimizerWorkV2,
    PlanEvaluationV2,
    SolverModeV2,
    SourceMatrixV2,
)

PlanOracleV2 = Callable[[CandidatePlanV2], PlanEvaluationV2]


class OptimizerWorkLimitV2(RuntimeError):
    """An explicitly requested exhaustive route exceeds frozen work bounds."""


class OptimizerAdmissionErrorV2(RuntimeError):
    """Even the mandatory kernel-only logical plan is not feasible."""


class _OracleRunner:
    def __init__(self, oracle: PlanOracleV2, limit: int) -> None:
        self._oracle = oracle
        self._limit = limit
        self._cache: dict[CandidatePlanV2, PlanEvaluationV2] = {}

    @property
    def evaluations(self) -> int:
        return len(self._cache)

    @property
    def remaining(self) -> int:
        return self._limit - self.evaluations

    def evaluate(self, source_ids: Iterable[str]) -> PlanEvaluationV2:
        plan = CandidatePlanV2(tuple(sorted(set(source_ids))))
        cached = self._cache.get(plan)
        if cached is not None:
            return cached
        if self.evaluations >= self._limit:
            raise OptimizerWorkLimitV2("optimizer oracle work limit reached")
        evaluation = self._oracle(plan)
        evaluation.__post_init__()
        if evaluation.plan != plan:
            raise ValueError("plan oracle returned an evaluation for another plan")
        self._cache[plan] = evaluation
        return evaluation


def _row_weights(matrix: SourceMatrixV2) -> dict[str, float]:
    return {row.source_id: row.weight for row in matrix.rows}


def _weights(
    matrix: SourceMatrixV2, retained_source_ids: tuple[str, ...]
) -> tuple[float, float]:
    retained = set(retained_source_ids)
    retained_weight = math.fsum(
        row.weight for row in matrix.rows if row.source_id in retained
    )
    omitted_weight = math.fsum(
        row.weight for row in matrix.rows if row.source_id not in retained
    )
    return retained_weight, omitted_weight


def _conflict_free(matrix: SourceMatrixV2, source_ids: tuple[str, ...]) -> bool:
    selected = set(source_ids)
    return not any(
        edge.source_id in selected and edge.target_id in selected
        for edge in matrix.conflicts
    )


def _exact_plan_objective(
    matrix: SourceMatrixV2, evaluation: PlanEvaluationV2
) -> tuple[float, int, tuple[str, ...]]:
    """Exact local reference comparator over canonical plan evaluations."""
    _, omitted_weight = _weights(matrix, evaluation.plan.retained_source_ids)
    return (
        omitted_weight,
        evaluation.resident_bytes,
        evaluation.plan.retained_source_ids,
    )


def _scores_are_near_tied(
    score: float,
    best_score: float,
    epsilon: float,
) -> bool:
    """Compare greedy scores without allowing same-sign infinities to become NaN."""
    if score == best_score:
        return True
    return (
        math.isfinite(score)
        and math.isfinite(best_score)
        and abs(score - best_score) <= epsilon
    )


def _selection(
    matrix: SourceMatrixV2,
    evaluation: PlanEvaluationV2,
    solver_mode: SolverModeV2,
    runner: _OracleRunner,
    *,
    greedy_steps: int = 0,
    refinement_evaluations: int = 0,
    work_limit_hit: bool = False,
    reference_tie_breaks: int = 0,
) -> OptimizerSelectionV2:
    retained = evaluation.plan.retained_source_ids
    retained_weight, omitted_weight = _weights(matrix, retained)
    selection = OptimizerSelectionV2(
        plan=evaluation.plan,
        released_source_ids=tuple(
            source_id
            for source_id in matrix.active_source_ids
            if source_id not in set(retained)
        ),
        retained_weight=retained_weight,
        omitted_weight=omitted_weight,
        evaluation=evaluation,
        solver_mode=solver_mode,
        work=OptimizerWorkV2(
            plans_evaluated=runner.evaluations,
            oracle_evaluations=runner.evaluations,
            greedy_steps=greedy_steps,
            refinement_evaluations=refinement_evaluations,
            work_limit_hit=work_limit_hit,
            reference_tie_breaks=reference_tie_breaks,
        ),
    )
    selection.__post_init__()
    return selection


def _feasible(
    matrix: SourceMatrixV2,
    evaluation: PlanEvaluationV2,
    accepted_budget: int,
) -> bool:
    return (
        evaluation.valid
        and evaluation.resident_bytes <= accepted_budget
        and set(matrix.mandatory_source_ids).issubset(
            evaluation.plan.retained_source_ids
        )
        and set(evaluation.plan.retained_source_ids).issubset(
            matrix.selectable_source_ids
        )
        and _conflict_free(matrix, evaluation.plan.retained_source_ids)
    )


def _exhaustive_exact(
    matrix: SourceMatrixV2,
    policy: CandidatePolicyV2,
    accepted_budget: int,
    oracle: PlanOracleV2,
) -> OptimizerSelectionV2:
    optional = tuple(
        source_id
        for source_id in matrix.selectable_source_ids
        if source_id not in set(matrix.mandatory_source_ids)
    )
    plan_count = 1 << len(optional)
    if (
        len(optional) > policy.exact_small_limit
        or plan_count > policy.max_plan_evaluations
    ):
        raise OptimizerWorkLimitV2("exact-small work limit exceeded before evaluation")
    runner = _OracleRunner(oracle, policy.max_plan_evaluations)
    best: PlanEvaluationV2 | None = None
    mandatory = matrix.mandatory_source_ids
    for mask in range(plan_count):
        retained = mandatory + tuple(
            source_id for index, source_id in enumerate(optional) if mask & (1 << index)
        )
        canonical = tuple(sorted(retained))
        if not _conflict_free(matrix, canonical):
            continue
        evaluation = runner.evaluate(canonical)
        if not _feasible(matrix, evaluation, accepted_budget):
            continue
        if best is None or _exact_plan_objective(
            matrix, evaluation
        ) < _exact_plan_objective(matrix, best):
            best = evaluation
    if best is None:
        raise OptimizerAdmissionErrorV2("mandatory plan is not feasible")
    return _selection(matrix, best, SolverModeV2.EXACT_SMALL, runner)


def optimize_reference_v2(
    matrix: SourceMatrixV2,
    policy: CandidatePolicyV2,
    accepted_budget: int,
    oracle: PlanOracleV2,
) -> OptimizerSelectionV2:
    """Independent test oracle over the same frozen logical-source universe."""
    if accepted_budget <= 0:
        raise ValueError("accepted_budget must be positive")
    optional = tuple(
        source_id
        for source_id in matrix.selectable_source_ids
        if source_id not in set(matrix.mandatory_source_ids)
    )
    plan_count = 1 << len(optional)
    if (
        len(optional) > policy.reference_row_limit
        or plan_count > policy.max_plan_evaluations
    ):
        raise OptimizerWorkLimitV2("reference work limit exceeded before evaluation")
    runner = _OracleRunner(oracle, policy.max_plan_evaluations)
    best: PlanEvaluationV2 | None = None
    mandatory = matrix.mandatory_source_ids
    for count in range(len(optional) + 1):
        for subset in combinations(optional, count):
            canonical = tuple(sorted((*mandatory, *subset)))
            if not _conflict_free(matrix, canonical):
                continue
            evaluation = runner.evaluate(canonical)
            if not _feasible(matrix, evaluation, accepted_budget):
                continue
            if best is None or _exact_plan_objective(
                matrix, evaluation
            ) < _exact_plan_objective(matrix, best):
                best = evaluation
    if best is None:
        raise OptimizerAdmissionErrorV2("mandatory plan is not feasible")
    return _selection(matrix, best, SolverModeV2.REFERENCE, runner)


def _greedy(
    matrix: SourceMatrixV2,
    policy: CandidatePolicyV2,
    accepted_budget: int,
    oracle: PlanOracleV2,
) -> OptimizerSelectionV2:
    runner = _OracleRunner(oracle, policy.max_plan_evaluations)
    mandatory = matrix.mandatory_source_ids
    current = runner.evaluate(mandatory)
    if not _feasible(matrix, current, accepted_budget):
        raise OptimizerAdmissionErrorV2("mandatory plan is not feasible")

    optional = tuple(
        source_id
        for source_id in matrix.selectable_source_ids
        if source_id not in set(mandatory)
    )
    all_evaluation = runner.evaluate((*mandatory, *optional))
    if _feasible(matrix, all_evaluation, accepted_budget):
        return _selection(matrix, all_evaluation, SolverModeV2.GREEDY, runner)

    selected = set(mandatory)
    remaining = set(optional)
    weights = _row_weights(matrix)
    greedy_steps = 0
    work_limit_hit = False
    reference_tie_breaks = 0
    while remaining and greedy_steps < policy.max_greedy_steps:
        ordered_remaining = tuple(sorted(remaining))
        if runner.remaining < len(ordered_remaining):
            work_limit_hit = True
            break
        best_addition: str | None = None
        best_evaluation: PlanEvaluationV2 | None = None
        best_score = -math.inf
        for source_id in ordered_remaining:
            evaluation = runner.evaluate((*selected, source_id))
            if not _feasible(matrix, evaluation, accepted_budget):
                continue
            marginal_bytes = evaluation.resident_bytes - current.resident_bytes
            score = (
                math.inf
                if marginal_bytes <= 0 and weights[source_id] > 0
                else weights[source_id] / max(1, marginal_bytes)
            )
            if best_evaluation is None:
                best_addition = source_id
                best_evaluation = evaluation
                best_score = score
            elif _scores_are_near_tied(score, best_score, policy.near_tie_epsilon):
                reference_tie_breaks += 1
                if _exact_plan_objective(matrix, evaluation) < _exact_plan_objective(
                    matrix, best_evaluation
                ):
                    best_addition = source_id
                    best_evaluation = evaluation
                    best_score = score
            elif score > best_score:
                best_addition = source_id
                best_evaluation = evaluation
                best_score = score
        if best_addition is None or best_evaluation is None:
            break
        if _exact_plan_objective(matrix, best_evaluation) >= _exact_plan_objective(
            matrix, current
        ):
            break
        selected.add(best_addition)
        remaining.remove(best_addition)
        current = best_evaluation
        greedy_steps += 1

    if remaining and greedy_steps >= policy.max_greedy_steps:
        work_limit_hit = True

    refinement_evaluations = 0
    selected_optional = tuple(sorted(selected - set(mandatory)))
    best_drop = current
    for dropped in selected_optional:
        if refinement_evaluations >= policy.max_refinement_evaluations:
            work_limit_hit = True
            break
        if runner.remaining <= 0:
            work_limit_hit = True
            break
        evaluation = runner.evaluate(selected - {dropped})
        refinement_evaluations += 1
        if _feasible(matrix, evaluation, accepted_budget) and _exact_plan_objective(
            matrix, evaluation
        ) < _exact_plan_objective(matrix, best_drop):
            best_drop = evaluation
    if best_drop != current:
        current = best_drop
        selected = set(current.plan.retained_source_ids)
        remaining = set(optional) - selected

    best_swap = current
    selected_optional = tuple(sorted(selected - set(mandatory)))
    for dropped in selected_optional:
        for added in sorted(remaining):
            if refinement_evaluations >= policy.max_refinement_evaluations:
                break
            if runner.remaining <= 0:
                work_limit_hit = True
                break
            swapped = (selected - {dropped}) | {added}
            evaluation = runner.evaluate(swapped)
            refinement_evaluations += 1
            if _feasible(matrix, evaluation, accepted_budget) and _exact_plan_objective(
                matrix, evaluation
            ) < _exact_plan_objective(matrix, best_swap):
                best_swap = evaluation
        if (
            refinement_evaluations >= policy.max_refinement_evaluations
            or runner.remaining <= 0
        ):
            break
    if (
        selected_optional
        and remaining
        and refinement_evaluations >= policy.max_refinement_evaluations
    ):
        work_limit_hit = True
    current = best_swap
    return _selection(
        matrix,
        current,
        SolverModeV2.GREEDY,
        runner,
        greedy_steps=greedy_steps,
        refinement_evaluations=refinement_evaluations,
        work_limit_hit=work_limit_hit,
        reference_tie_breaks=reference_tie_breaks,
    )


def optimize_sources_v2(
    matrix: SourceMatrixV2,
    policy: CandidatePolicyV2,
    accepted_budget: int,
    oracle: PlanOracleV2,
) -> OptimizerSelectionV2:
    """Select logical payloads; physical layout stays inside the exact oracle."""
    if accepted_budget <= 0:
        raise ValueError("accepted_budget must be positive")
    matrix.__post_init__()
    policy.__post_init__()
    optional_count = len(matrix.selectable_source_ids) - len(
        matrix.mandatory_source_ids
    )
    if policy.solver_mode is SolverModeV2.REFERENCE:
        return optimize_reference_v2(matrix, policy, accepted_budget, oracle)
    if policy.solver_mode is SolverModeV2.EXACT_SMALL:
        return _exhaustive_exact(matrix, policy, accepted_budget, oracle)
    if policy.solver_mode is SolverModeV2.GREEDY:
        return _greedy(matrix, policy, accepted_budget, oracle)
    if (
        optional_count <= policy.exact_small_limit
        and (1 << optional_count) <= policy.max_plan_evaluations
    ):
        return _exhaustive_exact(matrix, policy, accepted_budget, oracle)
    return _greedy(matrix, policy, accepted_budget, oracle)
