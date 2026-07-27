from __future__ import annotations

import math
from collections.abc import Sequence

from .backends import AutoLinearAlgebraBackend, SolvePolicy, SolveStatus
from .constraints import CompiledConstraints, ConstraintSystemError, compile_constraints
from .matrix import (
    CsrMatrix,
    build_propagation_operator,
    matrix_infinity_norm,
    vector_infinity_norm,
)
from .reduction import ReductionError, reduce_state, solve_projected_system
from .solver import solve_constrained_system
from .types import (
    ContextEngineMode,
    ContextGraph,
    ContextGraphConfig,
    EngineOutcome,
    EngineResult,
    ErrorCertificate,
    QueryActivation,
)


def _normalized_residual(
    residual: Sequence[float],
    denominator: float,
) -> float:
    numerator = vector_infinity_norm(residual)
    if denominator == 0.0:
        return 0.0 if numerator == 0.0 else math.inf
    result = numerator / denominator
    return result if math.isfinite(result) else math.inf


def _stationarity_error(
    operator: CsrMatrix,
    query: tuple[float, ...],
    constraints: CompiledConstraints,
    solution: tuple[float, ...],
    multipliers: tuple[float, ...],
) -> float:
    operator_product = operator.matvec(solution)
    constraint_product = constraints.matrix.transpose_matvec(multipliers)
    residual = tuple(
        operator_value - query_value + constraint_value
        for operator_value, query_value, constraint_value in zip(
            operator_product,
            query,
            constraint_product,
        )
    )
    denominator = (
        matrix_infinity_norm(operator) * vector_infinity_norm(solution)
        + vector_infinity_norm(query)
        + vector_infinity_norm(constraint_product)
    )
    return _normalized_residual(residual, denominator)


def _constraint_error(
    constraints: CompiledConstraints,
    solution: tuple[float, ...],
) -> float:
    product = constraints.certificate_matrix.matvec(solution)
    residual = tuple(
        value - expected for value, expected in zip(product, constraints.certificate_rhs)
    )
    denominator = matrix_infinity_norm(constraints.certificate_matrix) * vector_infinity_norm(
        solution
    ) + vector_infinity_norm(constraints.certificate_rhs)
    return _normalized_residual(residual, denominator)


def _relation_expansion_exceeds_limit(
    graph: ContextGraph,
    limit: int,
) -> bool:
    expanded_entries = 0
    for relation in graph.relations:
        term_count = len(relation.terms)
        remaining = limit - expanded_entries
        if term_count > remaining // term_count:
            return True
        expanded_entries += term_count * term_count
    return False


class SparseContextEngine:
    def __init__(self, config: ContextGraphConfig | None = None) -> None:
        if config is not None and not isinstance(config, ContextGraphConfig):
            raise TypeError("config must be a ContextGraphConfig")
        self._config = config or ContextGraphConfig()

    @property
    def config(self) -> ContextGraphConfig:
        return self._config

    def _policy(self) -> SolvePolicy:
        return SolvePolicy(
            allow_least_squares=self._config.allow_least_squares,
            direct_backward_error_limit=self._config.direct_backward_error_limit,
            iterative_backward_error_limit=self._config.iterative_backward_error_limit,
            condition_warning=self._config.condition_warning,
            condition_failure=self._config.condition_failure,
            python_max_n=self._config.python_max_n,
            python_max_nnz=self._config.python_max_nnz,
            numpy_max_n=self._config.numpy_max_n,
            numpy_max_dense_bytes=self._config.numpy_max_dense_bytes,
            sparse_max_n=self._config.sparse_max_n,
            sparse_max_nnz=self._config.sparse_max_nnz,
            max_iterations=self._config.max_iterations,
        )

    @staticmethod
    def _degraded(
        error_code: str,
        *,
        certificate: ErrorCertificate | None = None,
        activation_scores: tuple[tuple[str, float], ...] = (),
        solve_status: str | None = None,
        backend_name: str | None = None,
    ) -> EngineResult:
        return EngineResult(
            outcome=EngineOutcome.DEGRADED_RAW,
            selected_blocks=(),
            activation_scores=activation_scores,
            certificate=certificate,
            error_code=error_code,
            solve_status=solve_status,
            backend_name=backend_name,
        )

    def solve(
        self,
        graph: ContextGraph,
        activation: QueryActivation,
        *,
        mode: ContextEngineMode = ContextEngineMode.ACTIVE,
    ) -> EngineResult:
        if not isinstance(graph, ContextGraph):
            raise TypeError("graph must be a ContextGraph")
        if not isinstance(activation, QueryActivation):
            raise TypeError("activation must be a QueryActivation")
        if not isinstance(mode, ContextEngineMode):
            raise TypeError("mode must be a ContextEngineMode")
        if mode is ContextEngineMode.OFF:
            return EngineResult(outcome=EngineOutcome.OFF)

        if (
            len(graph.coordinates) > self._config.max_coordinates
            or len(graph.relations) > self._config.max_relations
            or _relation_expansion_exceeds_limit(
                graph,
                self._config.max_relation_entries,
            )
            or len(graph.constraints) > self._config.max_constraints
        ):
            return self._degraded("GRAPH_CAPACITY_EXCEEDED")

        coordinate_ids = tuple(coordinate.coordinate_id for coordinate in graph.coordinates)
        known_coordinate_ids = set(coordinate_ids)
        if any(
            coordinate_id not in known_coordinate_ids for coordinate_id, _ in activation.scores
        ) or any(
            coordinate_id not in known_coordinate_ids
            for coordinate_id in activation.retained_coordinate_ids
        ):
            return self._degraded("QUERY_UNKNOWN_COORDINATE")

        try:
            operator = build_propagation_operator(
                graph,
                epsilon=self._config.epsilon,
            )
            constraints = compile_constraints(coordinate_ids, graph.constraints)
        except ConstraintSystemError as error:
            return self._degraded(f"CONSTRAINT_{error.code.value}")
        except (TypeError, ValueError, OverflowError):
            return self._degraded("GRAPH_INVALID")

        score_by_coordinate = dict(activation.scores)
        query = tuple(
            score_by_coordinate.get(coordinate_id, 0.0) for coordinate_id in coordinate_ids
        )
        retained_ids: set[str]
        if activation.retained_coordinate_ids:
            retained_ids = set(activation.retained_coordinate_ids)
            retained_ids.update(constraints.support_coordinate_ids)
            retained_ids.update(
                coordinate.coordinate_id
                for coordinate in graph.coordinates
                if coordinate.candidate.required
            )
        else:
            retained_ids = set(coordinate_ids)
        retained_indices = tuple(
            index
            for index, coordinate_id in enumerate(coordinate_ids)
            if coordinate_id in retained_ids
        )

        policy = self._policy()
        backend = AutoLinearAlgebraBackend(prefer_sparse=self._config.prefer_sparse)
        reconstruction_error = 0.0
        try:
            if len(retained_indices) < len(coordinate_ids):
                reduction = reduce_state(
                    operator,
                    query,
                    constraints,
                    retained_indices=retained_indices,
                    backend=backend,
                    policy=policy,
                )
                projected = solve_projected_system(
                    reduction,
                    backend=backend,
                    policy=policy,
                )
                status = projected.status
                solution = projected.solution
                multipliers = projected.multipliers
                backend_name = projected.backend_name
                reconstruction_error = projected.reconstruction_error
            else:
                full = solve_constrained_system(
                    operator,
                    query,
                    constraints,
                    backend=backend,
                    policy=policy,
                )
                status = full.status
                solution = full.solution
                multipliers = full.multipliers
                backend_name = full.backend_name
        except ReductionError as error:
            return self._degraded(f"REDUCTION_{error.code.value}")
        except (TypeError, ValueError, OverflowError):
            return self._degraded("NUMERICAL_INPUT_INVALID")

        accepted = status is SolveStatus.CONVERGED or (
            status is SolveStatus.LEAST_SQUARES and policy.allow_least_squares
        )
        if not accepted or solution is None or multipliers is None:
            return self._degraded(
                f"SOLVE_{status.value}",
                solve_status=status.value,
                backend_name=backend_name,
            )

        activation_scores = tuple(zip(coordinate_ids, solution))
        try:
            stationarity_error = _stationarity_error(
                operator,
                query,
                constraints,
                solution,
                multipliers,
            )
            constraint_error = _constraint_error(constraints, solution)
        except (TypeError, ValueError, OverflowError):
            return self._degraded(
                "CERTIFICATE_NONFINITE",
                activation_scores=activation_scores,
                solve_status=status.value,
                backend_name=backend_name,
            )
        if any(
            not math.isfinite(value)
            for value in (
                stationarity_error,
                constraint_error,
                reconstruction_error,
            )
        ):
            return self._degraded(
                "CERTIFICATE_NONFINITE",
                activation_scores=activation_scores,
                solve_status=status.value,
                backend_name=backend_name,
            )

        selected_coordinate_ids = {
            coordinate_id
            for coordinate_id, value in activation_scores
            if value >= self._config.selection_threshold
        }
        selected_coordinates = tuple(
            coordinate
            for coordinate in graph.coordinates
            if coordinate.coordinate_id in selected_coordinate_ids
        )
        required_passed = all(
            not coordinate.candidate.required or coordinate.coordinate_id in selected_coordinate_ids
            for coordinate in graph.coordinates
        )
        available_provenance = set(activation.provenance_event_ids)
        provenance_passed = all(
            set(coordinate.candidate.source_event_ids).issubset(available_provenance)
            for coordinate in selected_coordinates
        )

        numerical_limit = self._config.direct_backward_error_limit
        numerical_passed = (
            stationarity_error <= numerical_limit
            and constraint_error <= numerical_limit
            and reconstruction_error <= numerical_limit
        )
        certificate = ErrorCertificate(
            stationarity_error=stationarity_error,
            constraint_error=constraint_error,
            reconstruction_error=reconstruction_error,
            required_blocks_passed=required_passed,
            provenance_passed=provenance_passed,
            passed=numerical_passed and required_passed and provenance_passed,
        )
        failure_code: str | None = None
        if stationarity_error > numerical_limit:
            failure_code = "CERTIFICATE_STATIONARITY_FAILED"
        elif constraint_error > numerical_limit:
            failure_code = "CERTIFICATE_CONSTRAINT_FAILED"
        elif reconstruction_error > numerical_limit:
            failure_code = "CERTIFICATE_RECONSTRUCTION_FAILED"
        elif not required_passed:
            failure_code = "CERTIFICATE_REQUIRED_BLOCK_FAILED"
        elif not provenance_passed:
            failure_code = "CERTIFICATE_PROVENANCE_FAILED"
        if failure_code is not None:
            return self._degraded(
                failure_code,
                certificate=certificate,
                activation_scores=activation_scores,
                solve_status=status.value,
                backend_name=backend_name,
            )

        outcome = EngineOutcome.ACTIVE if mode is ContextEngineMode.ACTIVE else EngineOutcome.SHADOW
        selected_blocks = (
            tuple(coordinate.candidate for coordinate in selected_coordinates)
            if mode is ContextEngineMode.ACTIVE
            else ()
        )
        return EngineResult(
            outcome=outcome,
            selected_blocks=selected_blocks,
            activation_scores=activation_scores,
            certificate=certificate,
            error_code=None,
            solve_status=status.value,
            backend_name=backend_name,
        )
