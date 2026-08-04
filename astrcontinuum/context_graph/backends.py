from __future__ import annotations

import importlib
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .matrix import CsrMatrix, backward_error, matrix_infinity_norm


class SolveStatus(str, Enum):
    CONVERGED = "CONVERGED"
    LEAST_SQUARES = "LEAST_SQUARES"
    ILL_CONDITIONED = "ILL_CONDITIONED"
    SINGULAR = "SINGULAR"
    NONFINITE_INPUT = "NONFINITE_INPUT"
    NONFINITE_OUTPUT = "NONFINITE_OUTPUT"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_CAPACITY_EXCEEDED = "BACKEND_CAPACITY_EXCEEDED"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True, slots=True)
class SolvePolicy:
    allow_least_squares: bool = False
    prefer_iterative: bool = False
    direct_backward_error_limit: float = 1.0e-10
    iterative_backward_error_limit: float = 1.0e-8
    condition_warning: float = 1.0e12
    condition_failure: float = 1.0e14
    python_max_n: int = 64
    python_max_nnz: int = 4_096
    numpy_max_n: int = 1_024
    numpy_max_dense_bytes: int = 64 * 1_024 * 1_024
    sparse_max_n: int = 100_000
    sparse_max_nnz: int = 5_000_000
    max_iterations: int = 10_000

    def __post_init__(self) -> None:
        if not isinstance(self.allow_least_squares, bool):
            raise TypeError("allow_least_squares must be a boolean")
        if not isinstance(self.prefer_iterative, bool):
            raise TypeError("prefer_iterative must be a boolean")
        for field_name in (
            "direct_backward_error_limit",
            "iterative_backward_error_limit",
            "condition_warning",
            "condition_failure",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be numeric")
            normalized = float(value)
            if not math.isfinite(normalized) or normalized <= 0.0:
                raise ValueError(f"{field_name} must be finite and positive")
            object.__setattr__(self, field_name, normalized)
        if self.condition_failure <= self.condition_warning:
            raise ValueError("condition thresholds must be ordered")

        for field_name in (
            "python_max_n",
            "python_max_nnz",
            "numpy_max_n",
            "numpy_max_dense_bytes",
            "sparse_max_n",
            "sparse_max_nnz",
            "max_iterations",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")
        if self.python_max_n > 64 or self.python_max_nnz > 4_096:
            raise ValueError("reference backend capacity exceeds the contract")
        if self.numpy_max_n > 1_024:
            raise ValueError("dense backend dimension exceeds the contract")
        if self.numpy_max_dense_bytes > 64 * 1_024 * 1_024:
            raise ValueError("dense backend memory exceeds the contract")


@dataclass(frozen=True, slots=True)
class LinearSolveResult:
    status: SolveStatus
    solution: tuple[float, ...] | None
    backend_name: str
    backward_error: float = math.inf
    condition_estimate: float | None = None
    iterations: int | None = None


class LinearAlgebraBackend(Protocol):
    def solve(
        self,
        matrix: CsrMatrix,
        rhs: Sequence[float],
        *,
        policy: SolvePolicy | None = None,
    ) -> LinearSolveResult: ...


def _stable_result(
    status: SolveStatus,
    backend_name: str,
    *,
    solution: tuple[float, ...] | None = None,
    error: float = math.inf,
    condition: float | None = None,
    iterations: int | None = None,
) -> LinearSolveResult:
    return LinearSolveResult(
        status=status,
        solution=solution,
        backend_name=backend_name,
        backward_error=error,
        condition_estimate=condition,
        iterations=iterations,
    )


def _validated_rhs(
    matrix: CsrMatrix,
    rhs: Sequence[float],
) -> tuple[SolveStatus | None, tuple[float, ...] | None]:
    if matrix.shape[0] != matrix.shape[1]:
        return SolveStatus.INVALID_INPUT, None
    try:
        materialized = tuple(float(value) for value in rhs)
    except (TypeError, ValueError, OverflowError):
        return SolveStatus.INVALID_INPUT, None
    if len(materialized) != matrix.shape[0]:
        return SolveStatus.INVALID_INPUT, None
    if any(not math.isfinite(value) for value in materialized):
        return SolveStatus.NONFINITE_INPUT, None
    return None, materialized


def _classified_status(
    error: float,
    condition: float | None,
    policy: SolvePolicy,
    *,
    iterative: bool,
) -> SolveStatus:
    if not math.isfinite(error):
        return SolveStatus.NONFINITE_OUTPUT
    limit = (
        policy.iterative_backward_error_limit if iterative else policy.direct_backward_error_limit
    )
    if error > limit:
        return SolveStatus.ILL_CONDITIONED
    if condition is not None:
        if not math.isfinite(condition):
            return SolveStatus.ILL_CONDITIONED
        if condition >= policy.condition_failure:
            return SolveStatus.ILL_CONDITIONED
        if condition >= policy.condition_warning:
            return SolveStatus.ILL_CONDITIONED
    return SolveStatus.CONVERGED


def _condition_failed(condition: float | None, policy: SolvePolicy) -> bool:
    return condition is not None and (
        not math.isfinite(condition) or condition >= policy.condition_failure
    )


class PythonReferenceBackend:
    name = "python-reference"

    def solve(
        self,
        matrix: CsrMatrix,
        rhs: Sequence[float],
        *,
        policy: SolvePolicy | None = None,
    ) -> LinearSolveResult:
        active_policy = policy or SolvePolicy()
        input_status, materialized_rhs = _validated_rhs(matrix, rhs)
        if input_status is not None or materialized_rhs is None:
            return _stable_result(input_status or SolveStatus.INVALID_INPUT, self.name)
        size = matrix.shape[0]
        if size > active_policy.python_max_n or matrix.nnz > active_policy.python_max_nnz:
            return _stable_result(SolveStatus.BACKEND_CAPACITY_EXCEEDED, self.name)
        if size == 0:
            return _stable_result(
                SolveStatus.CONVERGED,
                self.name,
                solution=(),
                error=0.0,
                condition=1.0,
            )

        dense = [list(row) for row in matrix.to_dense(max_n=64, max_bytes=64 * 64 * 8)]
        working_rhs = list(materialized_rhs)
        scale = matrix_infinity_norm(matrix)
        if scale == 0.0:
            return _stable_result(SolveStatus.SINGULAR, self.name)
        pivot_tolerance = sys.float_info.epsilon * scale * max(size, 1)
        pivot_magnitudes: list[float] = []

        for column in range(size):
            pivot_row = max(range(column, size), key=lambda row: abs(dense[row][column]))
            pivot = dense[pivot_row][column]
            if abs(pivot) <= pivot_tolerance:
                return _stable_result(SolveStatus.SINGULAR, self.name)
            if pivot_row != column:
                dense[column], dense[pivot_row] = dense[pivot_row], dense[column]
                working_rhs[column], working_rhs[pivot_row] = (
                    working_rhs[pivot_row],
                    working_rhs[column],
                )
            pivot = dense[column][column]
            pivot_magnitudes.append(abs(pivot))
            for row in range(column + 1, size):
                factor = dense[row][column] / pivot
                dense[row][column] = 0.0
                for offset in range(column + 1, size):
                    dense[row][offset] -= factor * dense[column][offset]
                working_rhs[row] -= factor * working_rhs[column]

        solution = [0.0] * size
        for row in range(size - 1, -1, -1):
            pivot = dense[row][row]
            if abs(pivot) <= pivot_tolerance:
                return _stable_result(SolveStatus.SINGULAR, self.name)
            known = sum(dense[row][column] * solution[column] for column in range(row + 1, size))
            solution[row] = (working_rhs[row] - known) / pivot
        if any(not math.isfinite(value) for value in solution):
            return _stable_result(SolveStatus.NONFINITE_OUTPUT, self.name)

        materialized_solution = tuple(solution)
        error = backward_error(matrix, materialized_solution, materialized_rhs)
        minimum_pivot = min(pivot_magnitudes)
        condition = max(pivot_magnitudes) / minimum_pivot if minimum_pivot > 0.0 else math.inf
        status = _classified_status(error, condition, active_policy, iterative=False)
        if _condition_failed(condition, active_policy):
            return _stable_result(
                status,
                self.name,
                error=error,
                condition=condition,
            )
        return _stable_result(
            status,
            self.name,
            solution=materialized_solution,
            error=error,
            condition=condition,
        )


class NumpyDenseBackend:
    name = "numpy-dense"

    def solve(
        self,
        matrix: CsrMatrix,
        rhs: Sequence[float],
        *,
        policy: SolvePolicy | None = None,
    ) -> LinearSolveResult:
        active_policy = policy or SolvePolicy()
        input_status, materialized_rhs = _validated_rhs(matrix, rhs)
        if input_status is not None or materialized_rhs is None:
            return _stable_result(input_status or SolveStatus.INVALID_INPUT, self.name)
        size = matrix.shape[0]
        required_bytes = size * size * 8
        if size > active_policy.numpy_max_n or required_bytes > active_policy.numpy_max_dense_bytes:
            return _stable_result(SolveStatus.BACKEND_CAPACITY_EXCEEDED, self.name)
        if size == 0:
            return _stable_result(
                SolveStatus.CONVERGED,
                self.name,
                solution=(),
                error=0.0,
                condition=1.0,
            )

        try:
            numpy: Any = importlib.import_module("numpy")
        except ImportError:
            return _stable_result(SolveStatus.BACKEND_UNAVAILABLE, self.name)
        try:
            dense_values = matrix.to_dense(
                max_n=active_policy.numpy_max_n,
                max_bytes=active_policy.numpy_max_dense_bytes,
            )
            dense = numpy.asarray(dense_values, dtype=numpy.float64)
            dense_rhs = numpy.asarray(materialized_rhs, dtype=numpy.float64)
            condition = float(numpy.linalg.cond(dense))
            used_least_squares = False
            least_squares_rank: int | None = None
            try:
                output = numpy.linalg.solve(dense, dense_rhs)
            except numpy.linalg.LinAlgError:
                if not active_policy.allow_least_squares:
                    return _stable_result(
                        SolveStatus.SINGULAR,
                        self.name,
                        condition=condition,
                    )
                output, _, rank, _ = numpy.linalg.lstsq(dense, dense_rhs, rcond=None)
                least_squares_rank = int(rank)
                used_least_squares = True
        except MemoryError:
            return _stable_result(SolveStatus.BACKEND_CAPACITY_EXCEEDED, self.name)
        except (TypeError, ValueError, OverflowError):
            return _stable_result(SolveStatus.NONFINITE_OUTPUT, self.name)

        if not bool(numpy.all(numpy.isfinite(output))):
            return _stable_result(SolveStatus.NONFINITE_OUTPUT, self.name, condition=condition)
        materialized_solution = tuple(float(value) for value in output.tolist())
        error = backward_error(matrix, materialized_solution, materialized_rhs)
        if used_least_squares:
            if least_squares_rank is None or least_squares_rank < size:
                return _stable_result(
                    SolveStatus.SINGULAR,
                    self.name,
                    error=error,
                    condition=condition,
                )
            classified = _classified_status(
                error,
                condition,
                active_policy,
                iterative=False,
            )
            if classified is not SolveStatus.CONVERGED:
                return _stable_result(
                    classified,
                    self.name,
                    error=error,
                    condition=condition,
                )
            status = SolveStatus.LEAST_SQUARES
        else:
            status = _classified_status(error, condition, active_policy, iterative=False)
            if _condition_failed(condition, active_policy):
                return _stable_result(
                    status,
                    self.name,
                    error=error,
                    condition=condition,
                )
        return _stable_result(
            status,
            self.name,
            solution=materialized_solution,
            error=error,
            condition=condition,
        )


class ScipySparseBackend:
    name = "scipy-sparse"

    def solve(
        self,
        matrix: CsrMatrix,
        rhs: Sequence[float],
        *,
        policy: SolvePolicy | None = None,
    ) -> LinearSolveResult:
        active_policy = policy or SolvePolicy()
        input_status, materialized_rhs = _validated_rhs(matrix, rhs)
        if input_status is not None or materialized_rhs is None:
            return _stable_result(input_status or SolveStatus.INVALID_INPUT, self.name)
        size = matrix.shape[0]
        if size > active_policy.sparse_max_n or matrix.nnz > active_policy.sparse_max_nnz:
            return _stable_result(SolveStatus.BACKEND_CAPACITY_EXCEEDED, self.name)
        if size == 0:
            return _stable_result(
                SolveStatus.CONVERGED,
                self.name,
                solution=(),
                error=0.0,
                condition=1.0,
            )

        try:
            numpy: Any = importlib.import_module("numpy")
            scipy_sparse: Any = importlib.import_module("scipy.sparse")
            sparse_linalg: Any = importlib.import_module("scipy.sparse.linalg")
        except ImportError:
            return _stable_result(SolveStatus.BACKEND_UNAVAILABLE, self.name)

        try:
            sparse_matrix = scipy_sparse.csr_matrix(
                (
                    numpy.asarray(matrix.data, dtype=numpy.float64),
                    numpy.asarray(matrix.indices, dtype=numpy.int64),
                    numpy.asarray(matrix.indptr, dtype=numpy.int64),
                ),
                shape=matrix.shape,
                dtype=numpy.float64,
            )
            dense_rhs = numpy.asarray(materialized_rhs, dtype=numpy.float64)
            if active_policy.prefer_iterative:
                iterations = [0]

                def count_iteration(_residual: object) -> None:
                    iterations[0] += 1

                output, info = sparse_linalg.gmres(
                    sparse_matrix,
                    dense_rhs,
                    rtol=active_policy.iterative_backward_error_limit,
                    atol=0.0,
                    maxiter=active_policy.max_iterations,
                    callback=count_iteration,
                    callback_type="pr_norm",
                )
                if info < 0:
                    return _stable_result(
                        SolveStatus.SINGULAR,
                        self.name,
                        iterations=iterations[0],
                    )
                materialized_solution = tuple(float(value) for value in output.tolist())
                if any(not math.isfinite(value) for value in materialized_solution):
                    return _stable_result(
                        SolveStatus.NONFINITE_OUTPUT,
                        self.name,
                        iterations=iterations[0],
                    )
                error = backward_error(matrix, materialized_solution, materialized_rhs)
                if info > 0:
                    return _stable_result(
                        SolveStatus.ITERATION_LIMIT,
                        self.name,
                        solution=materialized_solution,
                        error=error,
                        iterations=iterations[0],
                    )
                status = _classified_status(error, None, active_policy, iterative=True)
                return _stable_result(
                    status,
                    self.name,
                    solution=materialized_solution,
                    error=error,
                    iterations=iterations[0],
                )

            factor = sparse_linalg.splu(sparse_matrix.tocsc())
            output = factor.solve(dense_rhs)
            condition: float | None
            if size <= 256:
                condition = float(numpy.linalg.cond(sparse_matrix.toarray()))
            else:
                inverse_operator = sparse_linalg.LinearOperator(
                    shape=matrix.shape,
                    matvec=factor.solve,
                    rmatvec=lambda value: factor.solve(value, trans="T"),
                    dtype=numpy.float64,
                )
                condition = float(
                    sparse_linalg.onenormest(sparse_matrix)
                    * sparse_linalg.onenormest(inverse_operator)
                )
        except MemoryError:
            return _stable_result(SolveStatus.BACKEND_CAPACITY_EXCEEDED, self.name)
        except (RuntimeError, TypeError, ValueError, OverflowError):
            return _stable_result(SolveStatus.SINGULAR, self.name)

        materialized_solution = tuple(float(value) for value in output.tolist())
        if any(not math.isfinite(value) for value in materialized_solution):
            return _stable_result(
                SolveStatus.NONFINITE_OUTPUT,
                self.name,
                condition=condition,
            )
        error = backward_error(matrix, materialized_solution, materialized_rhs)
        status = _classified_status(error, condition, active_policy, iterative=False)
        if _condition_failed(condition, active_policy):
            return _stable_result(
                status,
                self.name,
                error=error,
                condition=condition,
            )
        return _stable_result(
            status,
            self.name,
            solution=materialized_solution,
            error=error,
            condition=condition,
        )


class AutoLinearAlgebraBackend:
    name = "auto"

    def __init__(self, *, prefer_sparse: bool = False) -> None:
        if not isinstance(prefer_sparse, bool):
            raise TypeError("prefer_sparse must be a boolean")
        self._prefer_sparse = prefer_sparse

    def solve(
        self,
        matrix: CsrMatrix,
        rhs: Sequence[float],
        *,
        policy: SolvePolicy | None = None,
    ) -> LinearSolveResult:
        active_policy = policy or SolvePolicy()
        if self._prefer_sparse:
            sparse_result = ScipySparseBackend().solve(matrix, rhs, policy=active_policy)
            if sparse_result.status not in {
                SolveStatus.BACKEND_UNAVAILABLE,
                SolveStatus.BACKEND_CAPACITY_EXCEEDED,
            }:
                return sparse_result
            if sparse_result.status is SolveStatus.BACKEND_CAPACITY_EXCEEDED:
                size = matrix.shape[0]
                if (
                    size > active_policy.numpy_max_n
                    or size * size * 8 > active_policy.numpy_max_dense_bytes
                ):
                    return sparse_result

        dense_result = NumpyDenseBackend().solve(matrix, rhs, policy=active_policy)
        if dense_result.status not in {
            SolveStatus.BACKEND_UNAVAILABLE,
            SolveStatus.BACKEND_CAPACITY_EXCEEDED,
        }:
            return dense_result
        return dense_result
