"""Pure, canonical sparse evidence matrices for V21 planning.

The matrix is intentionally a sparse *witness* of a planner decision.  It is
not a numerical optimiser and it does not contain a dense matrix, floating
point score, epsilon, or ``x^T R x``-style surrogate. Every axis row and
explicit nonzero binary cell has a stable identity, and the root commits the
complete finite payload consumed by the V21 verifier transition.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Final, cast

from crm_experiment.contracts_v21 import CapsuleRoleV21, canonical_json_v21
from crm_experiment.reencoding_contracts_v21 import (
    EVIDENCED_POLICY_ROOT_DOMAIN_V21,
    EVIDENCED_STATE_HASH_DOMAIN_V21,
    MATRIX_ROOT_DOMAIN_V21,
    MAX_EVIDENCED_BRIDGE_VECTOR_V21,
    MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21,
    MAX_EVIDENCED_COVERAGE_VECTOR_V21,
    MAX_EVIDENCED_HARD_EDGES_V21,
    MAX_EVIDENCED_LOSS_UNITS_V21,
    MAX_EVIDENCED_NEW_SEGMENTS_V21,
    MAX_EVIDENCED_PLAN_EVALUATIONS_V21,
    MAX_EVIDENCED_ROOT_ROWS_V21,
    MAX_EVIDENCED_SOURCE_RECORDS_V21,
    RECORD_DECISION_ROOT_DOMAIN_V21,
    SEGMENT_DECISION_ROOT_DOMAIN_V21,
    SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
    ReencodingOutcomeV21,
    SegmentDispositionV21,
    SolverModeV21,
)

_RECORD_ID_DOMAIN: Final = "v21-record-id-s3"
_SOURCE_COMMITMENT_DOMAIN: Final = "v21-source-commitment-s3"
_SEGMENT_ID_DOMAIN: Final = "v21-segment-id-s3"
_DICTIONARY_ID_DOMAIN: Final = "v21-dictionary-id-s3"
_CANONICAL_SEGMENT_HASH_DOMAIN: Final = "crm-v21-canonical-segment-s3/v1"


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative plain integer")
    return value


def _require_domain_digest(value: object, domain: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", value) is None
    ):
        raise ValueError(f"{label} must be a {domain} domain digest")
    return value


def _require_canonical_indices(
    value: object, *, label: str, upper_exclusive: int
) -> tuple[int, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{label} must be a tuple")
    indices = tuple(
        _require_nonnegative_int(item, f"{label} entry")
        for item in cast(tuple[object, ...], value)
    )
    if any(item >= upper_exclusive for item in indices):
        raise ValueError(f"{label} entry is outside its axis")
    if indices != tuple(sorted(indices)):
        raise ValueError(f"{label} must be canonically sorted")
    if len(set(indices)) != len(indices):
        raise ValueError(f"{label} must be unique")
    return indices


def _require_role_counts(
    value: object, label: str
) -> tuple[tuple[CapsuleRoleV21, int], ...]:
    if type(value) is not tuple:
        raise ValueError(f"{label} must be a tuple")
    rows: list[tuple[CapsuleRoleV21, int]] = []
    for item in cast(tuple[object, ...], value):
        if type(item) is not tuple or len(item) != 2:
            raise ValueError(f"{label} must contain (role, count) pairs")
        role, count = cast(tuple[object, object], item)
        if type(role) is not CapsuleRoleV21:
            raise ValueError(f"{label} role must be a closed V21 role")
        rows.append(
            (cast(CapsuleRoleV21, role), _require_nonnegative_int(count, label))
        )
    if tuple(rows) != tuple(sorted(rows, key=lambda row: row[0].value)):
        raise ValueError(f"{label} must be canonically sorted")
    if len({role for role, _count in rows}) != len(rows):
        raise ValueError(f"{label} roles must be unique")
    return tuple(rows)


def _require_contribution_keys(value: object, label: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{label} must be a tuple")
    keys = tuple(
        _require_domain_digest(item, _DICTIONARY_ID_DOMAIN, f"{label} entry")
        for item in cast(tuple[object, ...], value)
    )
    if len(keys) > MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21:
        raise ValueError(f"{label} cap exceeded")
    if keys != tuple(sorted(keys)):
        raise ValueError(f"{label} must be canonically sorted")
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} must be unique")
    return keys


class MatrixChildKindV21(StrEnum):
    """The only child origins that V21 matrix planning may materialise."""

    ROOTLESS_SEGMENT = "ROOTLESS_SEGMENT"
    PARENT_COMPACT = "PARENT_COMPACT"


class MatrixDecisionAxisV21(StrEnum):
    """A one-hot X row belongs to either a source or current segment axis."""

    SOURCE = "SOURCE"
    SEGMENT = "SEGMENT"


@dataclass(frozen=True, slots=True)
class MatrixSourceAxisV21:
    """Canonical source axis including the public core-required M predicate."""

    record_id: str
    commitment: str
    role: CapsuleRoleV21
    core_required: bool
    contribution_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_domain_digest(self.record_id, _RECORD_ID_DOMAIN, "source record_id")
        _require_domain_digest(
            self.commitment, _SOURCE_COMMITMENT_DOMAIN, "source commitment"
        )
        if type(self.role) is not CapsuleRoleV21:
            raise ValueError("source role must be a closed V21 role")
        if type(self.core_required) is not bool:
            raise ValueError("source core_required must be a plain boolean")
        _require_contribution_keys(self.contribution_keys, "source contribution_keys")


@dataclass(frozen=True, slots=True)
class MatrixSegmentAxisV21:
    """Canonical current-segment axis used by P and segment X rows."""

    segment_id: str
    canonical_hash: str
    role_counts: tuple[tuple[CapsuleRoleV21, int], ...]
    contribution_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_domain_digest(self.segment_id, _SEGMENT_ID_DOMAIN, "segment id")
        _require_domain_digest(
            self.canonical_hash,
            _CANONICAL_SEGMENT_HASH_DOMAIN,
            "segment canonical_hash",
        )
        _require_role_counts(self.role_counts, "segment role_counts")
        _require_contribution_keys(self.contribution_keys, "segment contribution_keys")


@dataclass(frozen=True, slots=True)
class MatrixChildAxisV21:
    """A sparse planned child and the source/parent rows it consumes."""

    child_id: str
    kind: MatrixChildKindV21
    source_rows: tuple[int, ...]
    parent_rows: tuple[int, ...]

    def __post_init__(self) -> None:
        _require_domain_digest(self.child_id, _SEGMENT_ID_DOMAIN, "child id")
        if type(self.kind) is not MatrixChildKindV21:
            raise ValueError("child kind must be a closed V21 child kind")
        if type(self.source_rows) is not tuple or type(self.parent_rows) is not tuple:
            raise ValueError("child rows must be tuples")
        for label, rows in (
            ("child source_rows", self.source_rows),
            ("child parent_rows", self.parent_rows),
        ):
            for row in cast(tuple[object, ...], rows):
                _require_nonnegative_int(row, label)


@dataclass(frozen=True, slots=True, order=True)
class MatrixDirectedEdgeV21:
    """One directed H edge from a source row to a source row."""

    source_row: int
    target_row: int

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.source_row, "H source_row")
        _require_nonnegative_int(self.target_row, "H target_row")


@dataclass(frozen=True, slots=True, order=True)
class MatrixParentChildEdgeV21:
    """One directed P edge from a current segment row to a planned child row."""

    parent_row: int
    child_row: int

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.parent_row, "P parent_row")
        _require_nonnegative_int(self.child_row, "P child_row")


@dataclass(frozen=True, slots=True, order=True)
class SparseBinaryCellV21:
    """One explicit nonzero F/G cell; omitted coordinates are implicit zeroes."""

    row_index: int
    column_index: int

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.row_index, "sparse cell row_index")
        _require_nonnegative_int(self.column_index, "sparse cell column_index")


@dataclass(frozen=True, slots=True)
class MatrixDecisionXV21:
    """A one-hot X decision row with an optional selected planned child."""

    axis: MatrixDecisionAxisV21
    row_index: int
    record_outcome: ReencodingOutcomeV21 | None
    segment_disposition: SegmentDispositionV21 | None
    child_row: int | None

    def __post_init__(self) -> None:
        if type(self.axis) is not MatrixDecisionAxisV21:
            raise ValueError("X axis must be a closed V21 decision axis")
        _require_nonnegative_int(self.row_index, "X row_index")
        if self.child_row is not None:
            _require_nonnegative_int(self.child_row, "X child_row")
        if self.axis is MatrixDecisionAxisV21.SOURCE:
            if type(self.record_outcome) is not ReencodingOutcomeV21:
                raise ValueError("source X row must choose exactly one record outcome")
            if self.segment_disposition is not None:
                raise ValueError("source X row cannot contain a segment disposition")
            has_child = self.child_row is not None
            if has_child != (self.record_outcome is ReencodingOutcomeV21.SEGMENT):
                raise ValueError("source X child selection must match SEGMENT outcome")
            return
        if type(self.segment_disposition) is not SegmentDispositionV21:
            raise ValueError(
                "segment X row must choose exactly one segment disposition"
            )
        if self.record_outcome is not None:
            raise ValueError("segment X row cannot contain a record outcome")
        has_child = self.child_row is not None
        if has_child != (self.segment_disposition is SegmentDispositionV21.COMPACT):
            raise ValueError("segment X child selection must match COMPACT disposition")


@dataclass(frozen=True, slots=True)
class MatrixObjectiveV21:
    """Integer C/B/R accounting and integer loss/saving objective only."""

    control_bytes: int
    source_body_bytes: int
    resident_bytes: int
    loss_units: int
    saved_bytes: int

    def __post_init__(self) -> None:
        control = _require_nonnegative_int(
            self.control_bytes, "objective control_bytes"
        )
        body = _require_nonnegative_int(
            self.source_body_bytes, "objective source_body_bytes"
        )
        resident = _require_nonnegative_int(
            self.resident_bytes, "objective resident_bytes"
        )
        _require_nonnegative_int(self.loss_units, "objective loss_units")
        _require_nonnegative_int(self.saved_bytes, "objective saved_bytes")
        if resident != control + body:
            raise ValueError("objective resident_bytes must equal C plus B")


def _validate_source_axes(value: object) -> tuple[MatrixSourceAxisV21, ...]:
    if type(value) is not tuple:
        raise ValueError("source_axes must be a tuple")
    items = cast(tuple[object, ...], value)
    if len(items) > MAX_EVIDENCED_SOURCE_RECORDS_V21:
        raise ValueError("source_axes cap exceeded")
    axes: list[MatrixSourceAxisV21] = []
    for item in items:
        if type(item) is not MatrixSourceAxisV21:
            raise ValueError("source_axes require nominal MatrixSourceAxisV21 rows")
        axis = cast(MatrixSourceAxisV21, item)
        MatrixSourceAxisV21.__post_init__(axis)
        axes.append(axis)
    identities = tuple((axis.record_id, axis.commitment) for axis in axes)
    if identities != tuple(sorted(identities)):
        raise ValueError("source_axes must be canonically sorted")
    if len({axis.record_id for axis in axes}) != len(axes):
        raise ValueError("source_axes record ids must be unique")
    return tuple(axes)


def _validate_segment_axes(value: object) -> tuple[MatrixSegmentAxisV21, ...]:
    if type(value) is not tuple:
        raise ValueError("segment_axes must be a tuple")
    items = cast(tuple[object, ...], value)
    if len(items) > MAX_EVIDENCED_ROOT_ROWS_V21:
        raise ValueError("segment_axes cap exceeded")
    axes: list[MatrixSegmentAxisV21] = []
    for item in items:
        if type(item) is not MatrixSegmentAxisV21:
            raise ValueError("segment_axes require nominal MatrixSegmentAxisV21 rows")
        axis = cast(MatrixSegmentAxisV21, item)
        MatrixSegmentAxisV21.__post_init__(axis)
        axes.append(axis)
    ids = tuple(axis.segment_id for axis in axes)
    if ids != tuple(sorted(ids)):
        raise ValueError("segment_axes must be canonically sorted")
    if len(set(ids)) != len(ids):
        raise ValueError("segment_axes ids must be unique")
    return tuple(axes)


def _validate_child_axes(
    value: object, *, source_count: int, segment_count: int
) -> tuple[MatrixChildAxisV21, ...]:
    if type(value) is not tuple:
        raise ValueError("child_axes must be a tuple")
    items = cast(tuple[object, ...], value)
    if len(items) > MAX_EVIDENCED_NEW_SEGMENTS_V21:
        raise ValueError("child_axes cap exceeded")
    axes: list[MatrixChildAxisV21] = []
    used_sources: set[int] = set()
    used_parents: set[int] = set()
    for item in items:
        if type(item) is not MatrixChildAxisV21:
            raise ValueError("child_axes require nominal MatrixChildAxisV21 rows")
        child = cast(MatrixChildAxisV21, item)
        MatrixChildAxisV21.__post_init__(child)
        sources = _require_canonical_indices(
            child.source_rows, label="child source_rows", upper_exclusive=source_count
        )
        parents = _require_canonical_indices(
            child.parent_rows, label="child parent_rows", upper_exclusive=segment_count
        )
        if child.kind is MatrixChildKindV21.ROOTLESS_SEGMENT:
            if not sources or parents:
                raise ValueError("rootless child must consume sources and no parent")
        elif sources or len(parents) != 1:
            raise ValueError("parent compact child must consume exactly one parent")
        if used_sources.intersection(sources):
            raise ValueError("child source rows may not be consumed twice")
        if used_parents.intersection(parents):
            raise ValueError("child parent rows may not be consumed twice")
        used_sources.update(sources)
        used_parents.update(parents)
        axes.append(child)
    ids = tuple(axis.child_id for axis in axes)
    if ids != tuple(sorted(ids)):
        raise ValueError("child_axes must be canonically sorted")
    if len(set(ids)) != len(ids):
        raise ValueError("child_axes ids must be unique")
    return tuple(axes)


def _validate_h_edges(
    value: object, *, source_count: int
) -> tuple[MatrixDirectedEdgeV21, ...]:
    if type(value) is not tuple:
        raise ValueError("h_edges must be a tuple")
    items = cast(tuple[object, ...], value)
    if len(items) > MAX_EVIDENCED_HARD_EDGES_V21:
        raise ValueError("H edge cap exceeded")
    rows: list[MatrixDirectedEdgeV21] = []
    for item in items:
        if type(item) is not MatrixDirectedEdgeV21:
            raise ValueError("h_edges require nominal MatrixDirectedEdgeV21 rows")
        edge = cast(MatrixDirectedEdgeV21, item)
        MatrixDirectedEdgeV21.__post_init__(edge)
        if edge.source_row >= source_count or edge.target_row >= source_count:
            raise ValueError("H edge is outside the source axis")
        rows.append(edge)
    if tuple(rows) != tuple(sorted(rows)):
        raise ValueError("h_edges must be canonically sorted")
    if len(set(rows)) != len(rows):
        raise ValueError("h_edges must be unique")
    return tuple(rows)


def _validate_sparse_cells(
    value: object, *, label: str, source_count: int, width: int
) -> tuple[SparseBinaryCellV21, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{label} must be a tuple")
    cells: list[SparseBinaryCellV21] = []
    for item in cast(tuple[object, ...], value):
        if type(item) is not SparseBinaryCellV21:
            raise ValueError(f"{label} requires nominal SparseBinaryCellV21 cells")
        cell = cast(SparseBinaryCellV21, item)
        SparseBinaryCellV21.__post_init__(cell)
        if cell.row_index >= source_count or cell.column_index >= width:
            raise ValueError(f"{label} sparse cell is outside its declared axis")
        cells.append(cell)
    coordinates = tuple((cell.row_index, cell.column_index) for cell in cells)
    if coordinates != tuple(sorted(coordinates)):
        raise ValueError(f"{label} sparse cells must be canonically sorted")
    if len(set(coordinates)) != len(coordinates):
        raise ValueError(f"{label} sparse cells must be unique")
    if len(cells) > source_count * width:
        raise ValueError(f"{label} sparse cell count exceeds its declared matrix area")
    return tuple(cells)


def _validate_p_edges(
    value: object,
    *,
    segment_count: int,
    child_axes: tuple[MatrixChildAxisV21, ...],
) -> tuple[MatrixParentChildEdgeV21, ...]:
    if type(value) is not tuple:
        raise ValueError("p_edges must be a tuple")
    rows: list[MatrixParentChildEdgeV21] = []
    for item in cast(tuple[object, ...], value):
        if type(item) is not MatrixParentChildEdgeV21:
            raise ValueError("p_edges require nominal MatrixParentChildEdgeV21 rows")
        edge = cast(MatrixParentChildEdgeV21, item)
        MatrixParentChildEdgeV21.__post_init__(edge)
        if edge.parent_row >= segment_count or edge.child_row >= len(child_axes):
            raise ValueError("P edge is outside its parent or child axis")
        rows.append(edge)
    if tuple(rows) != tuple(sorted(rows)):
        raise ValueError("p_edges must be canonically sorted")
    if len(set(rows)) != len(rows):
        raise ValueError("p_edges must be unique")
    expected = tuple(
        sorted(
            MatrixParentChildEdgeV21(parent_row=parent, child_row=child_index)
            for child_index, child in enumerate(child_axes)
            if child.kind is MatrixChildKindV21.PARENT_COMPACT
            for parent in child.parent_rows
        )
    )
    if tuple(rows) != expected:
        raise ValueError("P must exactly equal the parent-to-child child axis relation")
    return tuple(rows)


def _validate_x_rows(
    value: object,
    *,
    source_count: int,
    segment_count: int,
    child_axes: tuple[MatrixChildAxisV21, ...],
) -> tuple[MatrixDecisionXV21, ...]:
    if type(value) is not tuple:
        raise ValueError("x_rows must be a tuple")
    rows: list[MatrixDecisionXV21] = []
    for item in cast(tuple[object, ...], value):
        if type(item) is not MatrixDecisionXV21:
            raise ValueError("x_rows require nominal MatrixDecisionXV21 rows")
        row = cast(MatrixDecisionXV21, item)
        MatrixDecisionXV21.__post_init__(row)
        upper = (
            source_count if row.axis is MatrixDecisionAxisV21.SOURCE else segment_count
        )
        if row.row_index >= upper:
            raise ValueError("X row is outside its selected axis")
        if row.child_row is not None:
            if row.child_row >= len(child_axes):
                raise ValueError("X child row is outside the child axis")
            child = child_axes[row.child_row]
            if row.axis is MatrixDecisionAxisV21.SOURCE:
                if (
                    child.kind is not MatrixChildKindV21.ROOTLESS_SEGMENT
                    or row.row_index not in child.source_rows
                ):
                    raise ValueError(
                        "source X child must be a rootless child that consumes its source row"
                    )
            elif (
                child.kind is not MatrixChildKindV21.PARENT_COMPACT
                or row.row_index not in child.parent_rows
            ):
                raise ValueError(
                    "segment X child must be a compact child that consumes its parent row"
                )
        rows.append(row)
    expected = tuple(
        (MatrixDecisionAxisV21.SOURCE, row) for row in range(source_count)
    ) + tuple((MatrixDecisionAxisV21.SEGMENT, row) for row in range(segment_count))
    actual = tuple((row.axis, row.row_index) for row in rows)
    if actual != expected:
        raise ValueError(
            "X must contain one canonical one-hot row per source and segment"
        )
    return tuple(rows)


def _validate_child_x_bijection(
    *,
    child_axes: tuple[MatrixChildAxisV21, ...],
    x_rows: tuple[MatrixDecisionXV21, ...],
    source_count: int,
) -> None:
    """Require every declared child incidence to be selected by its canonical X row."""

    for child_index, child in enumerate(child_axes):
        if child.kind is MatrixChildKindV21.ROOTLESS_SEGMENT:
            for source_row in child.source_rows:
                row = x_rows[source_row]
                if (
                    row.record_outcome is not ReencodingOutcomeV21.SEGMENT
                    or row.child_row != child_index
                ):
                    raise ValueError(
                        "rootless child source row must select its own SEGMENT X decision"
                    )
            continue
        for parent_row in child.parent_rows:
            row = x_rows[source_count + parent_row]
            if (
                row.segment_disposition is not SegmentDispositionV21.COMPACT
                or row.child_row != child_index
            ):
                raise ValueError(
                    "compact child parent row must select its own COMPACT X decision"
                )


def _validate_matrix_payload(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    solver_mode: SolverModeV21,
    source_axes: tuple[MatrixSourceAxisV21, ...],
    segment_axes: tuple[MatrixSegmentAxisV21, ...],
    child_axes: tuple[MatrixChildAxisV21, ...],
    h_edges: tuple[MatrixDirectedEdgeV21, ...],
    mandatory_rows: tuple[int, ...],
    f_rows: tuple[SparseBinaryCellV21, ...],
    g_rows: tuple[SparseBinaryCellV21, ...],
    p_edges: tuple[MatrixParentChildEdgeV21, ...],
    x_rows: tuple[MatrixDecisionXV21, ...],
    h_width: int,
    m_width: int,
    f_width: int,
    g_width: int,
    p_width: int,
    x_width: int,
    evaluation_count: int,
    objective: MatrixObjectiveV21,
    record_decision_root: str,
    segment_decision_root: str,
    proposed_hash: str,
    target_generation: int,
    target_high_water: int,
) -> tuple[
    tuple[MatrixSourceAxisV21, ...],
    tuple[MatrixSegmentAxisV21, ...],
    tuple[MatrixChildAxisV21, ...],
    tuple[MatrixDirectedEdgeV21, ...],
    tuple[int, ...],
    tuple[SparseBinaryCellV21, ...],
    tuple[SparseBinaryCellV21, ...],
    tuple[MatrixParentChildEdgeV21, ...],
    tuple[MatrixDecisionXV21, ...],
]:
    _require_domain_digest(
        base_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "matrix base_state_hash"
    )
    _require_domain_digest(
        source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "matrix source_envelope_root",
    )
    _require_domain_digest(
        policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "matrix policy_root"
    )
    if type(solver_mode) is not SolverModeV21:
        raise ValueError("matrix solver_mode must be closed")
    sources = _validate_source_axes(source_axes)
    segments = _validate_segment_axes(segment_axes)
    if len(sources) + len(segments) > MAX_EVIDENCED_ROOT_ROWS_V21:
        raise ValueError("matrix decision-row cap exceeded")
    children = _validate_child_axes(
        child_axes, source_count=len(sources), segment_count=len(segments)
    )
    checked_h = _validate_h_edges(h_edges, source_count=len(sources))
    mandatory = _require_canonical_indices(
        mandatory_rows, label="mandatory_rows", upper_exclusive=len(sources)
    )
    if len(mandatory) > len(sources):
        raise ValueError("mandatory_rows cannot exceed the source axis")
    hard_endpoints = {edge.source_row for edge in checked_h} | {
        edge.target_row for edge in checked_h
    }
    expected_mandatory = tuple(
        sorted(
            hard_endpoints
            | {
                row_index
                for row_index, source in enumerate(sources)
                if source.core_required
            }
        )
    )
    if mandatory != expected_mandatory:
        raise ValueError(
            "mandatory_rows must exactly equal core-required rows and H endpoints"
        )

    checked_h_width = _require_nonnegative_int(h_width, "H width")
    checked_m_width = _require_nonnegative_int(m_width, "M width")
    checked_f_width = _require_nonnegative_int(f_width, "F width")
    checked_g_width = _require_nonnegative_int(g_width, "G width")
    checked_p_width = _require_nonnegative_int(p_width, "P width")
    checked_x_width = _require_nonnegative_int(x_width, "X width")
    if checked_h_width != len(sources) or checked_m_width != len(sources):
        raise ValueError("H and M widths must equal the source axis width")
    if checked_p_width != len(children):
        raise ValueError("P width must equal the child axis width")
    if checked_x_width != len(sources) + len(segments):
        raise ValueError("X width must equal its one-hot decision axis width")
    if checked_f_width > MAX_EVIDENCED_COVERAGE_VECTOR_V21:
        raise ValueError("F width exceeds the V21 coverage-vector cap")
    if checked_g_width > MAX_EVIDENCED_BRIDGE_VECTOR_V21:
        raise ValueError("G width exceeds the V21 bridge-vector cap")
    checked_f = _validate_sparse_cells(
        f_rows, label="F", source_count=len(sources), width=checked_f_width
    )
    checked_g = _validate_sparse_cells(
        g_rows, label="G", source_count=len(sources), width=checked_g_width
    )
    checked_p = _validate_p_edges(
        p_edges, segment_count=len(segments), child_axes=children
    )
    if len(checked_p) > len(segments):
        raise ValueError("P edges cannot exceed the current segment axis")
    checked_x = _validate_x_rows(
        x_rows,
        source_count=len(sources),
        segment_count=len(segments),
        child_axes=children,
    )
    if len(checked_x) != len(sources) + len(segments):
        raise ValueError("X must exactly cover the source and segment axes")
    _validate_child_x_bijection(
        child_axes=children,
        x_rows=checked_x,
        source_count=len(sources),
    )
    for source_row in mandatory:
        if checked_x[source_row].record_outcome is not ReencodingOutcomeV21.EXACT:
            raise ValueError("mandatory_rows must choose EXACT in X")
    checked_evaluation_count = _require_nonnegative_int(
        evaluation_count, "matrix evaluation_count"
    )
    if checked_evaluation_count == 0:
        raise ValueError("matrix evaluation_count must be positive")
    if checked_evaluation_count > MAX_EVIDENCED_PLAN_EVALUATIONS_V21:
        raise ValueError("matrix evaluation_count exceeds the public global cap")
    if type(objective) is not MatrixObjectiveV21:
        raise ValueError("matrix objective requires nominal MatrixObjectiveV21")
    MatrixObjectiveV21.__post_init__(objective)
    if objective.loss_units > MAX_EVIDENCED_LOSS_UNITS_V21:
        raise ValueError("matrix objective loss_units exceeds the public global cap")
    _require_domain_digest(
        record_decision_root,
        RECORD_DECISION_ROOT_DOMAIN_V21,
        "matrix record_decision_root",
    )
    _require_domain_digest(
        segment_decision_root,
        SEGMENT_DECISION_ROOT_DOMAIN_V21,
        "matrix segment_decision_root",
    )
    _require_domain_digest(
        proposed_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "matrix proposed_hash"
    )
    _require_nonnegative_int(target_generation, "matrix target_generation")
    _require_nonnegative_int(target_high_water, "matrix target_high_water")
    return (
        sources,
        segments,
        children,
        checked_h,
        mandatory,
        checked_f,
        checked_g,
        checked_p,
        checked_x,
    )


def evidenced_matrix_root_v21(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    solver_mode: SolverModeV21,
    source_axes: tuple[MatrixSourceAxisV21, ...],
    segment_axes: tuple[MatrixSegmentAxisV21, ...],
    child_axes: tuple[MatrixChildAxisV21, ...],
    h_edges: tuple[MatrixDirectedEdgeV21, ...],
    mandatory_rows: tuple[int, ...],
    f_rows: tuple[SparseBinaryCellV21, ...],
    g_rows: tuple[SparseBinaryCellV21, ...],
    p_edges: tuple[MatrixParentChildEdgeV21, ...],
    x_rows: tuple[MatrixDecisionXV21, ...],
    h_width: int,
    m_width: int,
    f_width: int,
    g_width: int,
    p_width: int,
    x_width: int,
    evaluation_count: int,
    objective: MatrixObjectiveV21,
    record_decision_root: str,
    segment_decision_root: str,
    proposed_hash: str,
    target_generation: int,
    target_high_water: int,
) -> str:
    """Return the canonical root for the complete sparse V21 matrix witness."""
    _validate_matrix_payload(
        base_state_hash=base_state_hash,
        source_envelope_root=source_envelope_root,
        policy_root=policy_root,
        solver_mode=solver_mode,
        source_axes=source_axes,
        segment_axes=segment_axes,
        child_axes=child_axes,
        h_edges=h_edges,
        mandatory_rows=mandatory_rows,
        f_rows=f_rows,
        g_rows=g_rows,
        p_edges=p_edges,
        x_rows=x_rows,
        h_width=h_width,
        m_width=m_width,
        f_width=f_width,
        g_width=g_width,
        p_width=p_width,
        x_width=x_width,
        evaluation_count=evaluation_count,
        objective=objective,
        record_decision_root=record_decision_root,
        segment_decision_root=segment_decision_root,
        proposed_hash=proposed_hash,
        target_generation=target_generation,
        target_high_water=target_high_water,
    )
    payload = {
        "base_state_hash": base_state_hash,
        "source_envelope_root": source_envelope_root,
        "policy_root": policy_root,
        "solver_mode": solver_mode,
        "source_axes": source_axes,
        "segment_axes": segment_axes,
        "child_axes": child_axes,
        "h_edges": h_edges,
        "mandatory_rows": mandatory_rows,
        "f_rows": f_rows,
        "g_rows": g_rows,
        "p_edges": p_edges,
        "x_rows": x_rows,
        "widths": {
            "h": h_width,
            "m": m_width,
            "f": f_width,
            "g": g_width,
            "p": p_width,
            "x": x_width,
        },
        "evaluation_count": evaluation_count,
        "objective": objective,
        "record_decision_root": record_decision_root,
        "segment_decision_root": segment_decision_root,
        "proposed_hash": proposed_hash,
        "target_generation": target_generation,
        "target_high_water": target_high_water,
    }
    digest = sha256(
        canonical_json_v21({"domain": MATRIX_ROOT_DOMAIN_V21, "value": payload}).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{MATRIX_ROOT_DOMAIN_V21}:{digest}"


@dataclass(frozen=True, slots=True)
class EvidencedMatrixPlanV21:
    """The immutable, fully rooted sparse matrix witness for one V21 candidate."""

    base_state_hash: str
    source_envelope_root: str
    policy_root: str
    solver_mode: SolverModeV21
    source_axes: tuple[MatrixSourceAxisV21, ...]
    segment_axes: tuple[MatrixSegmentAxisV21, ...]
    child_axes: tuple[MatrixChildAxisV21, ...]
    h_edges: tuple[MatrixDirectedEdgeV21, ...]
    mandatory_rows: tuple[int, ...]
    f_rows: tuple[SparseBinaryCellV21, ...]
    g_rows: tuple[SparseBinaryCellV21, ...]
    p_edges: tuple[MatrixParentChildEdgeV21, ...]
    x_rows: tuple[MatrixDecisionXV21, ...]
    h_width: int
    m_width: int
    f_width: int
    g_width: int
    p_width: int
    x_width: int
    evaluation_count: int
    objective: MatrixObjectiveV21
    record_decision_root: str
    segment_decision_root: str
    proposed_hash: str
    target_generation: int
    target_high_water: int
    matrix_root: str

    def __post_init__(self) -> None:
        _validate_matrix_payload(
            base_state_hash=self.base_state_hash,
            source_envelope_root=self.source_envelope_root,
            policy_root=self.policy_root,
            solver_mode=self.solver_mode,
            source_axes=self.source_axes,
            segment_axes=self.segment_axes,
            child_axes=self.child_axes,
            h_edges=self.h_edges,
            mandatory_rows=self.mandatory_rows,
            f_rows=self.f_rows,
            g_rows=self.g_rows,
            p_edges=self.p_edges,
            x_rows=self.x_rows,
            h_width=self.h_width,
            m_width=self.m_width,
            f_width=self.f_width,
            g_width=self.g_width,
            p_width=self.p_width,
            x_width=self.x_width,
            evaluation_count=self.evaluation_count,
            objective=self.objective,
            record_decision_root=self.record_decision_root,
            segment_decision_root=self.segment_decision_root,
            proposed_hash=self.proposed_hash,
            target_generation=self.target_generation,
            target_high_water=self.target_high_water,
        )
        claimed = _require_domain_digest(
            self.matrix_root, MATRIX_ROOT_DOMAIN_V21, "matrix_root"
        )
        expected = evidenced_matrix_root_v21(
            base_state_hash=self.base_state_hash,
            source_envelope_root=self.source_envelope_root,
            policy_root=self.policy_root,
            solver_mode=self.solver_mode,
            source_axes=self.source_axes,
            segment_axes=self.segment_axes,
            child_axes=self.child_axes,
            h_edges=self.h_edges,
            mandatory_rows=self.mandatory_rows,
            f_rows=self.f_rows,
            g_rows=self.g_rows,
            p_edges=self.p_edges,
            x_rows=self.x_rows,
            h_width=self.h_width,
            m_width=self.m_width,
            f_width=self.f_width,
            g_width=self.g_width,
            p_width=self.p_width,
            x_width=self.x_width,
            evaluation_count=self.evaluation_count,
            objective=self.objective,
            record_decision_root=self.record_decision_root,
            segment_decision_root=self.segment_decision_root,
            proposed_hash=self.proposed_hash,
            target_generation=self.target_generation,
            target_high_water=self.target_high_water,
        )
        if not hmac.compare_digest(claimed, expected):
            raise ValueError("matrix_root does not bind the sparse matrix payload")
