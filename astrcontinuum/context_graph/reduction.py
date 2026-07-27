from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .backends import (
    AutoLinearAlgebraBackend,
    LinearAlgebraBackend,
    SolvePolicy,
    SolveStatus,
)
from .constraints import CompiledConstraints
from .matrix import CsrMatrix, matrix_infinity_norm, vector_infinity_norm
from .solver import solve_constrained_system


class ReductionErrorCode(str, Enum):
    CONSTRAINT_SUPPORT_ELIMINATED = "CONSTRAINT_SUPPORT_ELIMINATED"
    SOLVE_FAILED = "SOLVE_FAILED"
    INVALID_INPUT = "INVALID_INPUT"
    NONFINITE_OUTPUT = "NONFINITE_OUTPUT"


class ReductionError(ValueError):
    def __init__(self, code: ReductionErrorCode) -> None:
        self.code = code
        super().__init__(f"REDUCTION_{code.value}")


@dataclass(frozen=True, slots=True)
class StateReduction:
    original_matrix: CsrMatrix
    original_rhs: tuple[float, ...]
    original_constraints: CompiledConstraints
    retained_indices: tuple[int, ...]
    eliminated_indices: tuple[int, ...]
    reduced_matrix: CsrMatrix
    reduced_rhs: tuple[float, ...]
    projected_constraints: CompiledConstraints
    reconstruction_operator: tuple[tuple[float, ...], ...]
    reconstruction_offset: tuple[float, ...]
    elimination_backend_name: str
    elimination_backward_error: float


@dataclass(frozen=True, slots=True)
class ProjectedSolveResult:
    status: SolveStatus
    solution: tuple[float, ...] | None
    multipliers: tuple[float, ...] | None
    backend_name: str
    backward_error: float
    condition_estimate: float | None
    reconstruction_error: float


def _accepted(status: SolveStatus, policy: SolvePolicy) -> bool:
    return status is SolveStatus.CONVERGED or (
        status is SolveStatus.LEAST_SQUARES and policy.allow_least_squares
    )


def _project_constraints(
    constraints: CompiledConstraints,
    retained_indices: tuple[int, ...],
) -> CompiledConstraints:
    solve_rows = tuple(range(constraints.matrix.shape[0]))
    certificate_rows = tuple(range(constraints.certificate_matrix.shape[0]))
    return CompiledConstraints(
        coordinate_ids=tuple(constraints.coordinate_ids[index] for index in retained_indices),
        matrix=constraints.matrix.submatrix(solve_rows, retained_indices),
        rhs=constraints.rhs,
        certificate_matrix=constraints.certificate_matrix.submatrix(
            certificate_rows,
            retained_indices,
        ),
        certificate_rhs=constraints.certificate_rhs,
        supports=constraints.supports,
    )


def reduce_state(
    matrix: CsrMatrix,
    rhs: Sequence[float],
    constraints: CompiledConstraints,
    *,
    retained_indices: Sequence[int],
    backend: LinearAlgebraBackend | None = None,
    policy: SolvePolicy | None = None,
) -> StateReduction:
    active_policy = policy or SolvePolicy()
    selected_backend = backend or AutoLinearAlgebraBackend()
    size = matrix.shape[0]
    materialized_rhs = tuple(float(value) for value in rhs)
    retained = tuple(retained_indices)
    if matrix.shape != (size, size) or len(materialized_rhs) != size:
        raise ReductionError(ReductionErrorCode.INVALID_INPUT)
    if any(not math.isfinite(value) for value in materialized_rhs):
        raise ReductionError(ReductionErrorCode.INVALID_INPUT)
    if constraints.matrix.shape[1] != size:
        raise ReductionError(ReductionErrorCode.INVALID_INPUT)
    if any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= size
        for index in retained
    ):
        raise ReductionError(ReductionErrorCode.INVALID_INPUT)
    if len(set(retained)) != len(retained):
        raise ReductionError(ReductionErrorCode.INVALID_INPUT)

    retained_set = set(retained)
    coordinate_index = {
        coordinate_id: index for index, coordinate_id in enumerate(constraints.coordinate_ids)
    }
    support_indices = {
        coordinate_index[coordinate_id] for coordinate_id in constraints.support_coordinate_ids
    }
    if not support_indices.issubset(retained_set):
        raise ReductionError(ReductionErrorCode.CONSTRAINT_SUPPORT_ELIMINATED)

    eliminated = tuple(index for index in range(size) if index not in retained_set)
    projected_constraints = _project_constraints(constraints, retained)
    reduced_matrix = matrix.submatrix(retained, retained)
    reduced_rhs = tuple(materialized_rhs[index] for index in retained)
    if not eliminated:
        return StateReduction(
            original_matrix=matrix,
            original_rhs=materialized_rhs,
            original_constraints=constraints,
            retained_indices=retained,
            eliminated_indices=(),
            reduced_matrix=reduced_matrix,
            reduced_rhs=reduced_rhs,
            projected_constraints=projected_constraints,
            reconstruction_operator=(),
            reconstruction_offset=(),
            elimination_backend_name="none",
            elimination_backward_error=0.0,
        )

    eliminated_matrix = matrix.submatrix(eliminated, eliminated)
    eliminated_retained = matrix.submatrix(eliminated, retained)
    retained_eliminated = matrix.submatrix(retained, eliminated)
    eliminated_rhs = tuple(materialized_rhs[index] for index in eliminated)

    offset_result = selected_backend.solve(
        eliminated_matrix,
        eliminated_rhs,
        policy=active_policy,
    )
    if not _accepted(offset_result.status, active_policy) or offset_result.solution is None:
        raise ReductionError(ReductionErrorCode.SOLVE_FAILED)
    reconstruction_offset = offset_result.solution
    maximum_error = offset_result.backward_error
    backend_name = offset_result.backend_name

    column_rhs = [[0.0] * len(eliminated) for _ in retained]
    for row, column, value in eliminated_retained.iter_entries():
        column_rhs[column][row] = value
    solve_columns: list[tuple[float, ...]] = []
    for values in column_rhs:
        column_result = selected_backend.solve(
            eliminated_matrix,
            values,
            policy=active_policy,
        )
        if not _accepted(column_result.status, active_policy) or column_result.solution is None:
            raise ReductionError(ReductionErrorCode.SOLVE_FAILED)
        solve_columns.append(column_result.solution)
        maximum_error = max(maximum_error, column_result.backward_error)
        backend_name = column_result.backend_name

    schur_entries = list(reduced_matrix.iter_entries())
    for column, solved_column in enumerate(solve_columns):
        correction = retained_eliminated.matvec(solved_column)
        schur_entries.extend(
            (row, column, -value) for row, value in enumerate(correction) if value != 0.0
        )
    reduced_matrix = CsrMatrix.from_entries(
        (len(retained), len(retained)),
        schur_entries,
    )
    rhs_correction = retained_eliminated.matvec(reconstruction_offset)
    reduced_rhs = tuple(
        value - correction for value, correction in zip(reduced_rhs, rhs_correction)
    )
    reconstruction_operator = tuple(
        tuple(-solve_columns[column][row] for column in range(len(retained)))
        for row in range(len(eliminated))
    )
    if any(not math.isfinite(value) for row in reconstruction_operator for value in row) or any(
        not math.isfinite(value) for value in reduced_rhs
    ):
        raise ReductionError(ReductionErrorCode.NONFINITE_OUTPUT)

    return StateReduction(
        original_matrix=matrix,
        original_rhs=materialized_rhs,
        original_constraints=constraints,
        retained_indices=retained,
        eliminated_indices=eliminated,
        reduced_matrix=reduced_matrix,
        reduced_rhs=reduced_rhs,
        projected_constraints=projected_constraints,
        reconstruction_operator=reconstruction_operator,
        reconstruction_offset=reconstruction_offset,
        elimination_backend_name=backend_name,
        elimination_backward_error=maximum_error,
    )


def solve_projected_system(
    reduction: StateReduction,
    *,
    backend: LinearAlgebraBackend | None = None,
    policy: SolvePolicy | None = None,
) -> ProjectedSolveResult:
    result = solve_constrained_system(
        reduction.reduced_matrix,
        reduction.reduced_rhs,
        reduction.projected_constraints,
        backend=backend,
        policy=policy,
    )
    if result.solution is None:
        return ProjectedSolveResult(
            status=result.status,
            solution=None,
            multipliers=result.multipliers,
            backend_name=result.backend_name,
            backward_error=result.backward_error,
            condition_estimate=result.condition_estimate,
            reconstruction_error=math.inf,
        )

    retained_solution = result.solution
    eliminated_solution = tuple(
        offset
        + sum(coefficient * retained_solution[column] for column, coefficient in enumerate(row))
        for offset, row in zip(
            reduction.reconstruction_offset,
            reduction.reconstruction_operator,
        )
    )
    full_solution = [0.0] * reduction.original_matrix.shape[0]
    for index, value in zip(reduction.retained_indices, retained_solution):
        full_solution[index] = value
    for index, value in zip(reduction.eliminated_indices, eliminated_solution):
        full_solution[index] = value
    if any(not math.isfinite(value) for value in full_solution):
        return ProjectedSolveResult(
            status=SolveStatus.NONFINITE_OUTPUT,
            solution=None,
            multipliers=result.multipliers,
            backend_name=result.backend_name,
            backward_error=math.inf,
            condition_estimate=result.condition_estimate,
            reconstruction_error=math.inf,
        )

    eliminated_rows = reduction.original_matrix.submatrix(
        reduction.eliminated_indices,
        tuple(range(reduction.original_matrix.shape[1])),
    )
    eliminated_rhs = tuple(reduction.original_rhs[index] for index in reduction.eliminated_indices)
    residual = tuple(
        left - right for left, right in zip(eliminated_rows.matvec(full_solution), eliminated_rhs)
    )
    denominator = matrix_infinity_norm(eliminated_rows) * vector_infinity_norm(
        full_solution
    ) + vector_infinity_norm(eliminated_rhs)
    numerator = vector_infinity_norm(residual)
    reconstruction_error = (
        0.0
        if numerator == 0.0 and denominator == 0.0
        else numerator / denominator
        if denominator > 0.0
        else math.inf
    )
    return ProjectedSolveResult(
        status=result.status,
        solution=tuple(full_solution),
        multipliers=result.multipliers,
        backend_name=result.backend_name,
        backward_error=result.backward_error,
        condition_estimate=result.condition_estimate,
        reconstruction_error=reconstruction_error,
    )
