from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

from ..runtime.types import CandidateBlock


def _require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{field_name} must be finite")
    return converted


def _require_positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _require_unique(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ValueError(f"{field_name} must be unique")
    return materialized


class ContextEngineMode(str, Enum):
    ACTIVE = "ACTIVE"
    SHADOW = "SHADOW"
    OFF = "OFF"


class EngineOutcome(str, Enum):
    ACTIVE = "ACTIVE"
    SHADOW = "SHADOW"
    OFF = "OFF"
    DEGRADED_RAW = "DEGRADED_RAW"


@dataclass(frozen=True, slots=True)
class ContextGraphConfig:
    epsilon: float = 1.0e-9
    selection_threshold: float = 0.5
    direct_backward_error_limit: float = 1.0e-10
    iterative_backward_error_limit: float = 1.0e-8
    condition_warning: float = 1.0e12
    condition_failure: float = 1.0e14
    max_coordinates: int = 1_024
    max_relations: int = 4_096
    max_relation_entries: int = 4_096
    max_constraints: int = 1_024
    python_max_n: int = 64
    python_max_nnz: int = 4_096
    numpy_max_n: int = 1_024
    numpy_max_dense_bytes: int = 64 * 1_024 * 1_024
    sparse_max_n: int = 100_000
    sparse_max_nnz: int = 5_000_000
    max_iterations: int = 10_000
    allow_least_squares: bool = False
    prefer_sparse: bool = False

    def __post_init__(self) -> None:
        epsilon = _require_finite(self.epsilon, "epsilon")
        threshold = _require_finite(self.selection_threshold, "selection_threshold")
        direct_limit = _require_finite(
            self.direct_backward_error_limit,
            "direct_backward_error_limit",
        )
        iterative_limit = _require_finite(
            self.iterative_backward_error_limit,
            "iterative_backward_error_limit",
        )
        warning = _require_finite(self.condition_warning, "condition_warning")
        failure = _require_finite(self.condition_failure, "condition_failure")

        if epsilon <= 0.0:
            raise ValueError("epsilon must be positive")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("selection_threshold must be between zero and one")
        if direct_limit <= 0.0 or iterative_limit <= 0.0:
            raise ValueError("backward error limits must be positive")
        if warning <= 0.0 or failure <= warning:
            raise ValueError("condition thresholds must be positive and ordered")

        integer_fields = (
            ("max_coordinates", self.max_coordinates),
            ("max_relations", self.max_relations),
            ("max_relation_entries", self.max_relation_entries),
            ("max_constraints", self.max_constraints),
            ("python_max_n", self.python_max_n),
            ("python_max_nnz", self.python_max_nnz),
            ("numpy_max_n", self.numpy_max_n),
            ("numpy_max_dense_bytes", self.numpy_max_dense_bytes),
            ("sparse_max_n", self.sparse_max_n),
            ("sparse_max_nnz", self.sparse_max_nnz),
            ("max_iterations", self.max_iterations),
        )
        for field_name, value in integer_fields:
            _require_positive_integer(value, field_name)

        if self.max_coordinates > 1_024:
            raise ValueError("max_coordinates exceeds the contract capacity")
        if self.python_max_n > 64:
            raise ValueError("python_max_n exceeds the reference capacity")
        if self.python_max_nnz > 4_096:
            raise ValueError("python_max_nnz exceeds the reference capacity")
        if self.numpy_max_n > 1_024:
            raise ValueError("numpy_max_n exceeds the dense capacity")
        if self.numpy_max_dense_bytes > 64 * 1_024 * 1_024:
            raise ValueError("numpy_max_dense_bytes exceeds the dense capacity")
        if not isinstance(self.allow_least_squares, bool):
            raise TypeError("allow_least_squares must be a boolean")
        if not isinstance(self.prefer_sparse, bool):
            raise TypeError("prefer_sparse must be a boolean")

        object.__setattr__(self, "epsilon", epsilon)
        object.__setattr__(self, "selection_threshold", threshold)
        object.__setattr__(self, "direct_backward_error_limit", direct_limit)
        object.__setattr__(self, "iterative_backward_error_limit", iterative_limit)
        object.__setattr__(self, "condition_warning", warning)
        object.__setattr__(self, "condition_failure", failure)


@dataclass(frozen=True, slots=True)
class GraphCoordinate:
    coordinate_id: str
    candidate: CandidateBlock = field(repr=False)
    diagonal: float = 1.0

    def __post_init__(self) -> None:
        _require_identifier(self.coordinate_id, "coordinate_id")
        if not isinstance(self.candidate, CandidateBlock):
            raise TypeError("candidate must be a CandidateBlock")
        diagonal = _require_finite(self.diagonal, "diagonal")
        if diagonal < 0.0:
            raise ValueError("diagonal must be nonnegative")
        object.__setattr__(self, "diagonal", diagonal)


@dataclass(frozen=True, slots=True)
class SparseRelation:
    relation_id: str
    terms: tuple[tuple[str, float], ...]
    weight: float = 1.0

    def __post_init__(self) -> None:
        _require_identifier(self.relation_id, "relation_id")
        terms = tuple(self.terms)
        if not terms:
            raise ValueError("relation terms must not be empty")
        normalized: list[tuple[str, float]] = []
        coordinate_ids: list[str] = []
        for coordinate_id, coefficient in terms:
            normalized_id = _require_identifier(coordinate_id, "relation coordinate_id")
            normalized_coefficient = _require_finite(coefficient, "relation coefficient")
            if normalized_coefficient == 0.0:
                raise ValueError("relation coefficient must be nonzero")
            coordinate_ids.append(normalized_id)
            normalized.append((normalized_id, normalized_coefficient))
        _require_unique(coordinate_ids, "relation coordinate ids")
        weight = _require_finite(self.weight, "weight")
        if weight < 0.0:
            raise ValueError("weight must be nonnegative")
        object.__setattr__(self, "terms", tuple(normalized))
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True, slots=True)
class ConstraintRow:
    constraint_id: str
    terms: tuple[tuple[str, float], ...]
    rhs: float

    def __post_init__(self) -> None:
        _require_identifier(self.constraint_id, "constraint_id")
        terms = tuple(self.terms)
        if not terms:
            raise ValueError("constraint terms must not be empty")
        normalized: list[tuple[str, float]] = []
        coordinate_ids: list[str] = []
        for coordinate_id, coefficient in terms:
            normalized_id = _require_identifier(coordinate_id, "constraint coordinate_id")
            normalized_coefficient = _require_finite(coefficient, "constraint coefficient")
            if normalized_coefficient == 0.0:
                raise ValueError("constraint coefficient must be nonzero")
            coordinate_ids.append(normalized_id)
            normalized.append((normalized_id, normalized_coefficient))
        _require_unique(coordinate_ids, "constraint coordinate ids")
        object.__setattr__(self, "terms", tuple(normalized))
        object.__setattr__(self, "rhs", _require_finite(self.rhs, "rhs"))

    @classmethod
    def fix_one(cls, constraint_id: str, coordinate_id: str) -> ConstraintRow:
        return cls(constraint_id, ((coordinate_id, 1.0),), 1.0)

    @classmethod
    def fix_zero(cls, constraint_id: str, coordinate_id: str) -> ConstraintRow:
        return cls(constraint_id, ((coordinate_id, 1.0),), 0.0)

    @classmethod
    def equality(
        cls,
        constraint_id: str,
        left_coordinate_id: str,
        right_coordinate_id: str,
    ) -> ConstraintRow:
        if left_coordinate_id == right_coordinate_id:
            raise ValueError("equality coordinate ids must be unique")
        return cls(
            constraint_id,
            ((left_coordinate_id, 1.0), (right_coordinate_id, -1.0)),
            0.0,
        )


@dataclass(frozen=True, slots=True)
class ContextGraph:
    graph_id: str
    coordinates: tuple[GraphCoordinate, ...]
    relations: tuple[SparseRelation, ...]
    constraints: tuple[ConstraintRow, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.graph_id, "graph_id")
        coordinates = tuple(self.coordinates)
        relations = tuple(self.relations)
        constraints = tuple(self.constraints)

        for coordinate in coordinates:
            if not isinstance(coordinate, GraphCoordinate):
                raise TypeError("coordinates must contain GraphCoordinate values")
        for relation in relations:
            if not isinstance(relation, SparseRelation):
                raise TypeError("relations must contain SparseRelation values")
        for constraint in constraints:
            if not isinstance(constraint, ConstraintRow):
                raise TypeError("constraints must contain ConstraintRow values")

        coordinate_ids = _require_unique(
            (item.coordinate_id for item in coordinates),
            "coordinate ids",
        )
        _require_unique((item.candidate.block_id for item in coordinates), "candidate block ids")
        _require_unique((item.relation_id for item in relations), "relation ids")
        _require_unique((item.constraint_id for item in constraints), "constraint ids")

        known = set(coordinate_ids)
        for relation in relations:
            if any(coordinate_id not in known for coordinate_id, _ in relation.terms):
                raise ValueError("relation references an unknown coordinate")
        for constraint in constraints:
            if any(coordinate_id not in known for coordinate_id, _ in constraint.terms):
                raise ValueError("constraint references an unknown coordinate")

        object.__setattr__(self, "coordinates", coordinates)
        object.__setattr__(self, "relations", relations)
        object.__setattr__(self, "constraints", constraints)


@dataclass(frozen=True, slots=True)
class QueryActivation:
    query_id: str
    scores: tuple[tuple[str, float], ...]
    retained_coordinate_ids: tuple[str, ...] = ()
    provenance_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.query_id, "query_id")
        scores = tuple(self.scores)
        normalized_scores: list[tuple[str, float]] = []
        score_ids: list[str] = []
        for coordinate_id, score in scores:
            normalized_id = _require_identifier(coordinate_id, "score coordinate_id")
            normalized_score = _require_finite(score, "score")
            score_ids.append(normalized_id)
            normalized_scores.append((normalized_id, normalized_score))
        _require_unique(score_ids, "score coordinate ids")

        retained = tuple(
            _require_identifier(value, "retained coordinate_id")
            for value in self.retained_coordinate_ids
        )
        provenance = tuple(
            _require_identifier(value, "provenance event_id") for value in self.provenance_event_ids
        )
        _require_unique(retained, "retained coordinate ids")
        _require_unique(provenance, "provenance event ids")

        object.__setattr__(self, "scores", tuple(normalized_scores))
        object.__setattr__(self, "retained_coordinate_ids", retained)
        object.__setattr__(self, "provenance_event_ids", provenance)


@dataclass(frozen=True, slots=True)
class ErrorCertificate:
    stationarity_error: float
    constraint_error: float
    reconstruction_error: float
    required_blocks_passed: bool
    provenance_passed: bool
    passed: bool

    def __post_init__(self) -> None:
        for field_name in (
            "stationarity_error",
            "constraint_error",
            "reconstruction_error",
        ):
            value = _require_finite(getattr(self, field_name), field_name)
            if value < 0.0:
                raise ValueError(f"{field_name} must be nonnegative")
            object.__setattr__(self, field_name, value)
        for field_name in (
            "required_blocks_passed",
            "provenance_passed",
            "passed",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a boolean")


@dataclass(frozen=True, slots=True)
class EngineResult:
    outcome: EngineOutcome
    selected_blocks: tuple[CandidateBlock, ...] = field(default=(), repr=False)
    activation_scores: tuple[tuple[str, float], ...] = ()
    certificate: ErrorCertificate | None = None
    error_code: str | None = None
    solve_status: str | None = None
    backend_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, EngineOutcome):
            raise TypeError("outcome must be an EngineOutcome")
        selected_blocks = tuple(self.selected_blocks)
        if any(not isinstance(item, CandidateBlock) for item in selected_blocks):
            raise TypeError("selected_blocks must contain CandidateBlock values")
        normalized_scores: list[tuple[str, float]] = []
        score_ids: list[str] = []
        for coordinate_id, score in self.activation_scores:
            normalized_id = _require_identifier(coordinate_id, "activation coordinate_id")
            normalized_score = _require_finite(score, "activation score")
            normalized_scores.append((normalized_id, normalized_score))
            score_ids.append(normalized_id)
        _require_unique(score_ids, "activation coordinate ids")
        if self.certificate is not None and not isinstance(self.certificate, ErrorCertificate):
            raise TypeError("certificate must be an ErrorCertificate")
        for field_name in ("error_code", "solve_status", "backend_name"):
            value = getattr(self, field_name)
            if value is not None:
                _require_identifier(value, field_name)
        object.__setattr__(self, "selected_blocks", selected_blocks)
        object.__setattr__(self, "activation_scores", tuple(normalized_scores))
