from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from .types import ContextGraph


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{field_name} must be finite")
    return converted


@dataclass(frozen=True, slots=True)
class CsrMatrix:
    shape: tuple[int, int]
    indptr: tuple[int, ...]
    indices: tuple[int, ...]
    data: tuple[float, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.shape, tuple)
            or len(self.shape) != 2
            or any(not _is_integer(value) for value in self.shape)
        ):
            raise TypeError("shape must contain two integers")
        rows, columns = self.shape
        if rows < 0 or columns < 0:
            raise ValueError("shape dimensions must be nonnegative")

        indptr = tuple(self.indptr)
        indices = tuple(self.indices)
        data = tuple(_finite_float(value, "matrix data") for value in self.data)
        if any(not _is_integer(value) for value in indptr):
            raise TypeError("indptr must contain integers")
        if any(not _is_integer(value) for value in indices):
            raise TypeError("indices must contain integers")
        if len(indptr) != rows + 1:
            raise ValueError("indptr length does not match shape")
        if not indptr or indptr[0] != 0:
            raise ValueError("indptr must start at zero")
        if any(left > right for left, right in pairwise(indptr)):
            raise ValueError("indptr must be nondecreasing")
        if indptr[-1] != len(indices) or len(indices) != len(data):
            raise ValueError("CSR arrays have inconsistent lengths")

        for row in range(rows):
            row_indices = indices[indptr[row] : indptr[row + 1]]
            if any(index < 0 or index >= columns for index in row_indices):
                raise ValueError("matrix index is out of bounds")
            if any(left >= right for left, right in pairwise(row_indices)):
                raise ValueError("matrix row indices must be strictly increasing")

        object.__setattr__(self, "indptr", indptr)
        object.__setattr__(self, "indices", indices)
        object.__setattr__(self, "data", data)

    @property
    def nnz(self) -> int:
        return len(self.data)

    @classmethod
    def zeros(cls, rows: int, columns: int) -> CsrMatrix:
        if not _is_integer(rows) or not _is_integer(columns):
            raise TypeError("matrix dimensions must be integers")
        if rows < 0 or columns < 0:
            raise ValueError("matrix dimensions must be nonnegative")
        return cls((rows, columns), (0,) * (rows + 1), (), ())

    @classmethod
    def from_dense(cls, values: Sequence[Sequence[float]]) -> CsrMatrix:
        rows = tuple(tuple(row) for row in values)
        columns = len(rows[0]) if rows else 0
        if any(len(row) != columns for row in rows):
            raise ValueError("dense rows must have equal lengths")
        entries: list[tuple[int, int, float]] = []
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                converted = _finite_float(value, "matrix data")
                if converted != 0.0:
                    entries.append((row_index, column_index, converted))
        return cls.from_entries((len(rows), columns), entries)

    @classmethod
    def from_entries(
        cls,
        shape: tuple[int, int],
        entries: Iterable[tuple[int, int, float]],
    ) -> CsrMatrix:
        if (
            not isinstance(shape, tuple)
            or len(shape) != 2
            or any(not _is_integer(value) for value in shape)
        ):
            raise TypeError("shape must contain two integers")
        rows, columns = shape
        if rows < 0 or columns < 0:
            raise ValueError("shape dimensions must be nonnegative")

        accumulated: dict[tuple[int, int], float] = {}
        for row, column, value in entries:
            if not _is_integer(row) or not _is_integer(column):
                raise TypeError("matrix coordinates must be integers")
            if row < 0 or row >= rows or column < 0 or column >= columns:
                raise ValueError("matrix coordinate is out of bounds")
            converted = _finite_float(value, "matrix data")
            key = (row, column)
            combined = accumulated.get(key, 0.0) + converted
            if not math.isfinite(combined):
                raise ValueError("combined matrix data must be finite")
            accumulated[key] = combined

        ordered = sorted(
            (row, column, value) for (row, column), value in accumulated.items() if value != 0.0
        )
        indptr = [0]
        indices: list[int] = []
        data: list[float] = []
        cursor = 0
        for row in range(rows):
            while cursor < len(ordered) and ordered[cursor][0] == row:
                _, column, value = ordered[cursor]
                indices.append(column)
                data.append(value)
                cursor += 1
            indptr.append(len(indices))
        return cls(shape, tuple(indptr), tuple(indices), tuple(data))

    def iter_entries(self) -> Iterable[tuple[int, int, float]]:
        rows, _ = self.shape
        for row in range(rows):
            for offset in range(self.indptr[row], self.indptr[row + 1]):
                yield row, self.indices[offset], self.data[offset]

    def matvec(self, vector: Sequence[float]) -> tuple[float, ...]:
        rows, columns = self.shape
        materialized = tuple(_finite_float(value, "vector value") for value in vector)
        if len(materialized) != columns:
            raise ValueError("vector length does not match matrix shape")
        result: list[float] = []
        for row in range(rows):
            total = 0.0
            for offset in range(self.indptr[row], self.indptr[row + 1]):
                total += self.data[offset] * materialized[self.indices[offset]]
            if not math.isfinite(total):
                raise ValueError("matrix product is nonfinite")
            result.append(total)
        return tuple(result)

    def transpose_matvec(self, vector: Sequence[float]) -> tuple[float, ...]:
        rows, columns = self.shape
        materialized = tuple(_finite_float(value, "vector value") for value in vector)
        if len(materialized) != rows:
            raise ValueError("vector length does not match matrix shape")
        result = [0.0] * columns
        for row in range(rows):
            for offset in range(self.indptr[row], self.indptr[row + 1]):
                result[self.indices[offset]] += self.data[offset] * materialized[row]
        if any(not math.isfinite(value) for value in result):
            raise ValueError("matrix product is nonfinite")
        return tuple(result)

    def submatrix(
        self,
        row_indices: Sequence[int],
        column_indices: Sequence[int],
    ) -> CsrMatrix:
        rows = tuple(row_indices)
        columns = tuple(column_indices)
        if any(not _is_integer(value) for value in rows + columns):
            raise TypeError("submatrix indices must be integers")
        if len(set(rows)) != len(rows) or len(set(columns)) != len(columns):
            raise ValueError("submatrix indices must be unique")
        if any(value < 0 or value >= self.shape[0] for value in rows):
            raise ValueError("submatrix row index is out of bounds")
        if any(value < 0 or value >= self.shape[1] for value in columns):
            raise ValueError("submatrix column index is out of bounds")
        row_map = {original: projected for projected, original in enumerate(rows)}
        column_map = {original: projected for projected, original in enumerate(columns)}
        entries = (
            (row_map[row], column_map[column], value)
            for row, column, value in self.iter_entries()
            if row in row_map and column in column_map
        )
        return CsrMatrix.from_entries((len(rows), len(columns)), entries)

    def to_dense(
        self,
        *,
        max_n: int,
        max_bytes: int,
    ) -> tuple[tuple[float, ...], ...]:
        if not _is_integer(max_n) or not _is_integer(max_bytes):
            raise TypeError("dense capacity values must be integers")
        if max_n <= 0 or max_bytes <= 0:
            raise ValueError("dense capacity values must be positive")
        rows, columns = self.shape
        required_bytes = rows * columns * 8
        if max(rows, columns) > max_n or required_bytes > max_bytes:
            raise ValueError("dense conversion capacity exceeded")
        dense = [[0.0] * columns for _ in range(rows)]
        for row, column, value in self.iter_entries():
            dense[row][column] = value
        return tuple(tuple(row) for row in dense)


def build_propagation_operator(graph: ContextGraph, *, epsilon: float) -> CsrMatrix:
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)):
        raise TypeError("epsilon must be numeric")
    normalized_epsilon = float(epsilon)
    if not math.isfinite(normalized_epsilon) or normalized_epsilon <= 0.0:
        raise ValueError("epsilon must be finite and positive")

    coordinate_indices = {
        coordinate.coordinate_id: index for index, coordinate in enumerate(graph.coordinates)
    }
    entries: list[tuple[int, int, float]] = []
    for index, coordinate in enumerate(graph.coordinates):
        entries.append((index, index, coordinate.diagonal + normalized_epsilon))
    for relation in graph.relations:
        for left_id, left_coefficient in relation.terms:
            left_index = coordinate_indices[left_id]
            for right_id, right_coefficient in relation.terms:
                right_index = coordinate_indices[right_id]
                entries.append(
                    (
                        left_index,
                        right_index,
                        relation.weight * left_coefficient * right_coefficient,
                    )
                )
    size = len(graph.coordinates)
    return CsrMatrix.from_entries((size, size), entries)


def matrix_infinity_norm(matrix: CsrMatrix) -> float:
    maximum = 0.0
    for row in range(matrix.shape[0]):
        row_sum = sum(
            abs(matrix.data[offset]) for offset in range(matrix.indptr[row], matrix.indptr[row + 1])
        )
        maximum = max(maximum, row_sum)
    return maximum


def vector_infinity_norm(vector: Sequence[float]) -> float:
    return max((abs(float(value)) for value in vector), default=0.0)


def backward_error(
    matrix: CsrMatrix,
    solution: Sequence[float],
    rhs: Sequence[float],
) -> float:
    product = matrix.matvec(solution)
    materialized_rhs = tuple(_finite_float(value, "rhs value") for value in rhs)
    if len(product) != len(materialized_rhs):
        raise ValueError("rhs length does not match matrix shape")
    residual = tuple(left - right for left, right in zip(product, materialized_rhs))
    numerator = vector_infinity_norm(residual)
    denominator = matrix_infinity_norm(matrix) * vector_infinity_norm(
        solution
    ) + vector_infinity_norm(materialized_rhs)
    if denominator == 0.0:
        return 0.0 if numerator == 0.0 else math.inf
    result = numerator / denominator
    return result if math.isfinite(result) else math.inf
