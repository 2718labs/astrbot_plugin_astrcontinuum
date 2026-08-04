from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .matrix import CsrMatrix
from .types import ConstraintRow


class ConstraintErrorCode(str, Enum):
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTORY = "CONTRADICTORY"
    UNKNOWN_COORDINATE = "UNKNOWN_COORDINATE"
    INVALID_COORDINATES = "INVALID_COORDINATES"


class ConstraintSystemError(ValueError):
    def __init__(self, code: ConstraintErrorCode) -> None:
        self.code = code
        super().__init__(f"CONSTRAINT_{code.value}")


@dataclass(frozen=True, slots=True)
class CompiledConstraints:
    coordinate_ids: tuple[str, ...]
    matrix: CsrMatrix
    rhs: tuple[float, ...]
    certificate_matrix: CsrMatrix
    certificate_rhs: tuple[float, ...]
    supports: tuple[tuple[str, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        size = len(self.coordinate_ids)
        if len(set(self.coordinate_ids)) != size:
            raise ValueError("coordinate ids must be unique")
        if self.matrix.shape[1] != size:
            raise ValueError("constraint matrix width does not match coordinates")
        if self.matrix.shape[0] != len(self.rhs):
            raise ValueError("constraint rhs length does not match matrix")
        if self.certificate_matrix.shape[1] != size:
            raise ValueError("certificate matrix width does not match coordinates")
        if self.certificate_matrix.shape[0] != len(self.certificate_rhs):
            raise ValueError("certificate rhs length does not match matrix")
        if len(self.supports) != self.certificate_matrix.shape[0]:
            raise ValueError("constraint supports do not match certificate rows")

    @property
    def support_coordinate_ids(self) -> tuple[str, ...]:
        support = {
            coordinate_id for _, coordinate_ids in self.supports for coordinate_id in coordinate_ids
        }
        return tuple(
            coordinate_id for coordinate_id in self.coordinate_ids if coordinate_id in support
        )


class _DisjointSet:
    def __init__(self, values: Sequence[str]) -> None:
        self._parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parent[value]
        if parent != value:
            self._parent[value] = self.find(parent)
        return self._parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def _classify_constraint(row: ConstraintRow) -> tuple[str, tuple[str, ...], float | None]:
    if len(row.terms) == 1:
        coordinate_id, coefficient = row.terms[0]
        if coefficient == 1.0 and row.rhs in {0.0, 1.0}:
            return "fix", (coordinate_id,), row.rhs
        raise ConstraintSystemError(ConstraintErrorCode.UNSUPPORTED)
    if len(row.terms) == 2 and row.rhs == 0.0:
        (left_id, left_coefficient), (right_id, right_coefficient) = row.terms
        if left_coefficient in {-1.0, 1.0} and right_coefficient == -left_coefficient:
            return "equality", (left_id, right_id), None
    raise ConstraintSystemError(ConstraintErrorCode.UNSUPPORTED)


def compile_constraints(
    coordinate_ids: Sequence[str],
    rows: Sequence[ConstraintRow],
) -> CompiledConstraints:
    coordinates = tuple(coordinate_ids)
    constraints = tuple(rows)
    if any(not isinstance(value, str) or not value for value in coordinates):
        raise ConstraintSystemError(ConstraintErrorCode.INVALID_COORDINATES)
    if len(set(coordinates)) != len(coordinates):
        raise ConstraintSystemError(ConstraintErrorCode.INVALID_COORDINATES)
    if any(not isinstance(row, ConstraintRow) for row in constraints):
        raise TypeError("rows must contain ConstraintRow values")

    index = {coordinate_id: position for position, coordinate_id in enumerate(coordinates)}
    parsed: list[tuple[str, tuple[str, ...], float | None]] = []
    supports: list[tuple[str, tuple[str, ...]]] = []
    certificate_entries: list[tuple[int, int, float]] = []
    certificate_rhs: list[float] = []

    for row_index, row in enumerate(constraints):
        if any(coordinate_id not in index for coordinate_id, _ in row.terms):
            raise ConstraintSystemError(ConstraintErrorCode.UNKNOWN_COORDINATE)
        classified = _classify_constraint(row)
        parsed.append(classified)
        support = tuple(coordinate_id for coordinate_id, _ in row.terms)
        supports.append((row.constraint_id, support))
        for coordinate_id, coefficient in row.terms:
            certificate_entries.append((row_index, index[coordinate_id], coefficient))
        certificate_rhs.append(row.rhs)

    disjoint_set = _DisjointSet(coordinates)
    for kind, support, _ in parsed:
        if kind == "equality":
            disjoint_set.union(support[0], support[1])

    fixed_by_root: dict[str, float] = {}
    for kind, support, fixed_value in parsed:
        if kind != "fix" or fixed_value is None:
            continue
        root = disjoint_set.find(support[0])
        existing = fixed_by_root.get(root)
        if existing is not None and existing != fixed_value:
            raise ConstraintSystemError(ConstraintErrorCode.CONTRADICTORY)
        fixed_by_root[root] = fixed_value

    groups: dict[str, list[str]] = {}
    for coordinate_id in coordinates:
        root = disjoint_set.find(coordinate_id)
        groups.setdefault(root, []).append(coordinate_id)

    solve_entries: list[tuple[int, int, float]] = []
    solve_rhs: list[float] = []
    solve_row = 0
    for members in groups.values():
        anchor = members[0]
        for member in members[1:]:
            solve_entries.append((solve_row, index[anchor], 1.0))
            solve_entries.append((solve_row, index[member], -1.0))
            solve_rhs.append(0.0)
            solve_row += 1
        root = disjoint_set.find(anchor)
        if root in fixed_by_root:
            solve_entries.append((solve_row, index[anchor], 1.0))
            solve_rhs.append(fixed_by_root[root])
            solve_row += 1

    return CompiledConstraints(
        coordinate_ids=coordinates,
        matrix=CsrMatrix.from_entries(
            (solve_row, len(coordinates)),
            solve_entries,
        ),
        rhs=tuple(solve_rhs),
        certificate_matrix=CsrMatrix.from_entries(
            (len(constraints), len(coordinates)),
            certificate_entries,
        ),
        certificate_rhs=tuple(certificate_rhs),
        supports=tuple(supports),
    )
