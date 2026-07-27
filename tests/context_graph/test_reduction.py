from __future__ import annotations

import pytest

from astrcontinuum.context_graph import ConstraintRow
from astrcontinuum.context_graph.backends import NumpyDenseBackend, SolveStatus
from astrcontinuum.context_graph.constraints import compile_constraints
from astrcontinuum.context_graph.matrix import CsrMatrix
from astrcontinuum.context_graph.reduction import (
    ReductionError,
    ReductionErrorCode,
    reduce_state,
    solve_projected_system,
)
from astrcontinuum.context_graph.solver import solve_constrained_system


def test_solve_based_reduction_matches_full_solution_and_reconstructs() -> None:
    matrix = CsrMatrix.from_dense(
        (
            (5.0, 1.0, 1.0),
            (1.0, 4.0, 1.0),
            (1.0, 1.0, 3.0),
        )
    )
    rhs = (2.0, 1.0, 3.0)
    constraints = compile_constraints(
        ("a", "b", "c"),
        (ConstraintRow.fix_one("fix-a", "a"),),
    )
    backend = NumpyDenseBackend()

    full = solve_constrained_system(matrix, rhs, constraints, backend=backend)
    reduction = reduce_state(
        matrix,
        rhs,
        constraints,
        retained_indices=(0, 1),
        backend=backend,
    )
    projected = solve_projected_system(reduction, backend=backend)

    assert full.status is SolveStatus.CONVERGED
    assert projected.status is SolveStatus.CONVERGED
    assert projected.solution == pytest.approx(full.solution)
    assert projected.reconstruction_error <= 1.0e-10


def test_reduction_never_eliminates_constraint_support() -> None:
    matrix = CsrMatrix.from_dense(((2.0, 0.0), (0.0, 2.0)))
    constraints = compile_constraints(
        ("a", "b"),
        (ConstraintRow.fix_one("fix-a", "a"),),
    )

    with pytest.raises(ReductionError) as raised:
        reduce_state(
            matrix,
            (1.0, 1.0),
            constraints,
            retained_indices=(1,),
            backend=NumpyDenseBackend(),
        )

    assert raised.value.code is ReductionErrorCode.CONSTRAINT_SUPPORT_ELIMINATED


def test_reduction_does_not_call_explicit_inverse(monkeypatch: pytest.MonkeyPatch) -> None:
    numpy = pytest.importorskip("numpy")
    monkeypatch.setattr(
        numpy.linalg,
        "inv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("inverse called")),
    )
    matrix = CsrMatrix.from_dense(
        (
            (4.0, 1.0, 0.5),
            (1.0, 3.0, 0.25),
            (0.5, 0.25, 2.0),
        )
    )
    constraints = compile_constraints(("a", "b", "c"), ())

    reduction = reduce_state(
        matrix,
        (1.0, 2.0, 3.0),
        constraints,
        retained_indices=(0, 1),
        backend=NumpyDenseBackend(),
    )

    assert reduction.eliminated_indices == (2,)
