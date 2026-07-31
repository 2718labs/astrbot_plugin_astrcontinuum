"""Deterministic exact and greedy solvers for CRM candidate selection."""

from __future__ import annotations

from .contracts import LossPolicy, MatrixBundle, Selection


def optimize(matrix: MatrixBundle, policy: LossPolicy) -> Selection:
    """Select the minimum-loss feasible candidate subset."""
    candidate_count = len(matrix.candidate_ids)
    if candidate_count <= policy.exact_threshold:
        return _exact_selection(matrix, policy)
    return _greedy_selection(matrix, policy)


def _evaluate(
    matrix: MatrixBundle,
    policy: LossPolicy,
    selected: tuple[int, ...],
) -> tuple[float, tuple[int, ...]] | None:
    if sum(matrix.costs[index] for index in selected) > matrix.budget:
        return None

    risk = sum(matrix.risks[index] for index in selected)
    if risk > policy.risk_ceiling:
        return None

    for offset, left in enumerate(selected):
        for right in selected[offset + 1 :]:
            if matrix.conflicts[left][right]:
                return None

    covered = tuple(
        atom_index
        for atom_index, row in enumerate(matrix.coverage)
        if any(row[candidate_index] for candidate_index in selected)
    )
    covered_set = set(covered)
    for atom_index in covered:
        required = {
            dependency_index
            for dependency_index, flag in enumerate(matrix.dependencies[atom_index])
            if flag
        }
        if not required <= covered_set:
            return None

    omission = sum(
        weight
        for atom_index, weight in enumerate(matrix.weights)
        if atom_index not in covered_set
    )
    redundancy = sum(
        matrix.redundancy[left][right]
        for offset, left in enumerate(selected)
        for right in selected[offset + 1 :]
    )
    return omission + policy.gamma * risk + policy.rho * redundancy, covered


def _selection(
    matrix: MatrixBundle,
    selected: tuple[int, ...],
    objective: float,
    covered: tuple[int, ...],
    solver: str,
) -> Selection:
    return Selection(
        candidate_ids=tuple(matrix.candidate_ids[index] for index in selected),
        covered_atom_ids=tuple(matrix.atom_ids[index] for index in covered),
        objective=round(objective, 12),
        byte_cost=sum(matrix.costs[index] for index in selected),
        solver=solver,
    )


def _exact_selection(matrix: MatrixBundle, policy: LossPolicy) -> Selection:
    best: tuple[float, tuple[str, ...], tuple[int, ...], tuple[int, ...]] | None = None
    for mask in range(1 << len(matrix.candidate_ids)):
        selected = tuple(
            index for index in range(len(matrix.candidate_ids)) if mask & (1 << index)
        )
        evaluated = _evaluate(matrix, policy, selected)
        if evaluated is None:
            continue
        objective, covered = evaluated
        ids = tuple(matrix.candidate_ids[index] for index in selected)
        key = (objective, ids, selected, covered)
        if best is None or key[:2] < best[:2]:
            best = key

    if best is None:
        raise ValueError("no feasible candidate selection")
    return _selection(matrix, best[2], best[0], best[3], "exact")


def _greedy_selection(matrix: MatrixBundle, policy: LossPolicy) -> Selection:
    selected: tuple[int, ...] = ()
    current = _evaluate(matrix, policy, selected)
    if current is None:
        raise ValueError("empty selection must be feasible")

    while True:
        choices: list[tuple[float, str, int, float, tuple[int, ...]]] = []
        for index, candidate_id in enumerate(matrix.candidate_ids):
            if index in selected:
                continue
            trial = tuple(sorted(selected + (index,)))
            evaluated = _evaluate(matrix, policy, trial)
            if evaluated is None:
                continue
            objective, covered = evaluated
            improvement = current[0] - objective
            if improvement > 0:
                choices.append(
                    (
                        -(improvement / max(1, matrix.costs[index])),
                        candidate_id,
                        index,
                        objective,
                        covered,
                    )
                )
        if not choices:
            break
        _, _, index, objective, covered = min(choices)
        selected = tuple(sorted(selected + (index,)))
        current = (objective, covered)

    return _selection(matrix, selected, current[0], current[1], "greedy")
