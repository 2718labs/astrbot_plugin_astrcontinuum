from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .backends import (
    AutoLinearAlgebraBackend,
    LinearAlgebraBackend,
    SolvePolicy,
    SolveStatus,
)
from .constraints import CompiledConstraints
from .matrix import CsrMatrix


@dataclass(frozen=True, slots=True)
class ConstrainedSolveResult:
    status: SolveStatus
    solution: tuple[float, ...] | None
    multipliers: tuple[float, ...] | None
    backend_name: str
    backward_error: float
    condition_estimate: float | None


def solve_constrained_system(
    matrix: CsrMatrix,
    rhs: Sequence[float],
    constraints: CompiledConstraints,
    *,
    backend: LinearAlgebraBackend | None = None,
    policy: SolvePolicy | None = None,
) -> ConstrainedSolveResult:
    if matrix.shape[0] != matrix.shape[1]:
        raise ValueError("operator must be square")
    size = matrix.shape[0]
    materialized_rhs = tuple(float(value) for value in rhs)
    if len(materialized_rhs) != size:
        raise ValueError("rhs length does not match operator")
    if any(not math.isfinite(value) for value in materialized_rhs):
        return ConstrainedSolveResult(
            status=SolveStatus.NONFINITE_INPUT,
            solution=None,
            multipliers=None,
            backend_name="none",
            backward_error=math.inf,
            condition_estimate=None,
        )
    if constraints.matrix.shape[1] != size:
        raise ValueError("constraint width does not match operator")

    selected_backend = backend or AutoLinearAlgebraBackend()
    constraint_count = constraints.matrix.shape[0]
    if constraint_count == 0:
        result = selected_backend.solve(matrix, materialized_rhs, policy=policy)
        return ConstrainedSolveResult(
            status=result.status,
            solution=result.solution,
            multipliers=() if result.solution is not None else None,
            backend_name=result.backend_name,
            backward_error=result.backward_error,
            condition_estimate=result.condition_estimate,
        )

    entries = list(matrix.iter_entries())
    for row, column, value in constraints.matrix.iter_entries():
        entries.append((column, size + row, value))
        entries.append((size + row, column, value))
    system_size = size + constraint_count
    kkt = CsrMatrix.from_entries((system_size, system_size), entries)
    system_rhs = materialized_rhs + constraints.rhs
    result = selected_backend.solve(kkt, system_rhs, policy=policy)
    if result.solution is None:
        return ConstrainedSolveResult(
            status=result.status,
            solution=None,
            multipliers=None,
            backend_name=result.backend_name,
            backward_error=result.backward_error,
            condition_estimate=result.condition_estimate,
        )
    return ConstrainedSolveResult(
        status=result.status,
        solution=result.solution[:size],
        multipliers=result.solution[size:],
        backend_name=result.backend_name,
        backward_error=result.backward_error,
        condition_estimate=result.condition_estimate,
    )
