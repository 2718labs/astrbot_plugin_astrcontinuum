from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

import astrcontinuum.context_graph.backends as backend_module
from astrcontinuum.context_graph.backends import (
    AutoLinearAlgebraBackend,
    NumpyDenseBackend,
    PythonReferenceBackend,
    SolvePolicy,
    SolveStatus,
)
from astrcontinuum.context_graph.matrix import CsrMatrix


def diagonal_matrix(values: tuple[float, ...]) -> CsrMatrix:
    size = len(values)
    return CsrMatrix(
        shape=(size, size),
        indptr=tuple(range(size + 1)),
        indices=tuple(range(size)),
        data=values,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "shape": (2, 2),
            "indptr": (0, 1),
            "indices": (0,),
            "data": (1.0,),
        },
        {
            "shape": (2, 2),
            "indptr": (0, 2, 2),
            "indices": (0, 0),
            "data": (1.0, 2.0),
        },
        {
            "shape": (1, 1),
            "indptr": (0, 1),
            "indices": (1,),
            "data": (1.0,),
        },
        {
            "shape": (1, 1),
            "indptr": (0, 1),
            "indices": (0,),
            "data": (float("nan"),),
        },
    ],
)
def test_csr_validation_is_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        CsrMatrix(**kwargs)  # type: ignore[arg-type]


def test_csr_matvec_and_bounded_dense_conversion() -> None:
    matrix = CsrMatrix(
        shape=(2, 2),
        indptr=(0, 2, 3),
        indices=(0, 1, 1),
        data=(2.0, -1.0, 3.0),
    )

    assert matrix.matvec((4.0, 5.0)) == (3.0, 15.0)
    assert matrix.transpose_matvec((4.0, 5.0)) == (8.0, 11.0)
    with pytest.raises(ValueError, match="capacity"):
        matrix.to_dense(max_n=1, max_bytes=128)
    with pytest.raises(ValueError, match="capacity"):
        matrix.to_dense(max_n=2, max_bytes=31)


def test_python_reference_backend_solves_tiny_system_and_recomputes_error() -> None:
    matrix = CsrMatrix.from_dense(((4.0, 1.0), (1.0, 3.0)))
    result = PythonReferenceBackend().solve(matrix, (1.0, 2.0))

    assert result.status is SolveStatus.CONVERGED
    assert result.solution == pytest.approx((1.0 / 11.0, 7.0 / 11.0))
    assert result.backward_error <= 1.0e-10


def test_python_reference_backend_is_scale_invariant_for_tiny_values() -> None:
    matrix = diagonal_matrix((1.0e-300, 2.0e-300))

    result = PythonReferenceBackend().solve(
        matrix,
        (1.0e-300, 4.0e-300),
    )

    assert result.status is SolveStatus.CONVERGED
    assert result.solution == pytest.approx((1.0, 2.0))
    assert result.backward_error <= 1.0e-10


def test_singular_system_does_not_silently_use_least_squares() -> None:
    matrix = CsrMatrix.from_dense(((1.0, 1.0), (2.0, 2.0)))

    result = NumpyDenseBackend().solve(
        matrix,
        (1.0, 2.0),
        policy=SolvePolicy(allow_least_squares=False),
    )

    assert result.status is SolveStatus.SINGULAR
    assert result.solution is None


def test_rank_deficient_least_squares_is_rejected_even_with_zero_residual() -> None:
    matrix = CsrMatrix.from_dense(((1.0, 1.0), (1.0, 1.0)))
    policy = SolvePolicy(allow_least_squares=True)

    result = NumpyDenseBackend().solve(
        matrix,
        (1.0, 1.0),
        policy=policy,
    )

    assert result.status is SolveStatus.SINGULAR
    assert result.solution is None
    assert result.condition_estimate is not None
    assert (
        not math.isfinite(result.condition_estimate)
        or result.condition_estimate >= policy.condition_failure
    )


def test_nonfinite_input_and_bounded_numpy_conversion_return_stable_status() -> None:
    backend = NumpyDenseBackend()
    nonfinite = backend.solve(diagonal_matrix((1.0, 2.0)), (1.0, float("inf")))
    oversized = backend.solve(diagonal_matrix((1.0,) * 1_025), (1.0,) * 1_025)

    assert nonfinite.status is SolveStatus.NONFINITE_INPUT
    assert oversized.status is SolveStatus.BACKEND_CAPACITY_EXCEEDED


def test_condition_threshold_is_not_reported_as_normal_convergence() -> None:
    result = NumpyDenseBackend().solve(
        diagonal_matrix((1.0, 1.0e-13)),
        (1.0, 1.0),
    )

    assert result.status is SolveStatus.ILL_CONDITIONED
    assert result.condition_estimate is not None
    assert result.condition_estimate >= 1.0e12


def test_condition_failure_discards_the_unreliable_solution() -> None:
    policy = SolvePolicy()

    result = NumpyDenseBackend().solve(
        diagonal_matrix((1.0, 1.0e-15)),
        (1.0, 1.0),
        policy=policy,
    )

    assert result.status is SolveStatus.ILL_CONDITIONED
    assert result.condition_estimate is not None
    assert result.condition_estimate >= policy.condition_failure
    assert result.solution is None


def test_auto_backend_uses_numpy_beyond_tiny_reference_capacity() -> None:
    result = AutoLinearAlgebraBackend(prefer_sparse=False).solve(
        diagonal_matrix((2.0,) * 65),
        (4.0,) * 65,
    )

    assert result.status is SolveStatus.CONVERGED
    assert result.backend_name == "numpy-dense"
    assert result.solution == pytest.approx((2.0,) * 65)


def test_auto_backend_never_uses_reference_backend_for_production_certification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    numpy = pytest.importorskip("numpy")
    size = 8
    random = numpy.random.default_rng(70)
    orthogonal, _ = numpy.linalg.qr(random.normal(size=(size, size)))
    eigenvalues = numpy.geomspace(1.0, 1.0e-15, size)
    dense = orthogonal @ numpy.diag(eigenvalues) @ orthogonal.T
    matrix = CsrMatrix.from_dense(tuple(tuple(float(value) for value in row) for row in dense))
    policy = SolvePolicy()
    assert float(numpy.linalg.cond(dense)) >= policy.condition_failure

    original_import = backend_module.importlib.import_module

    def import_without_numpy(name: str, package: str | None = None) -> object:
        if name == "numpy":
            raise ImportError
        return original_import(name, package)

    monkeypatch.setattr(backend_module.importlib, "import_module", import_without_numpy)

    result = AutoLinearAlgebraBackend(prefer_sparse=False).solve(
        matrix,
        (1.0,) * size,
        policy=policy,
    )

    assert result.status is SolveStatus.BACKEND_UNAVAILABLE
    assert result.solution is None


def test_package_import_does_not_eagerly_import_numerical_dependencies(
    tmp_path: Path,
) -> None:
    script = (
        "import sys\n"
        "import astrcontinuum\n"
        "import astrcontinuum.context_graph\n"
        "assert 'numpy' not in sys.modules\n"
        "assert 'scipy' not in sys.modules\n"
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
