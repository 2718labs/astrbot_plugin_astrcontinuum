from __future__ import annotations

import pytest

from astrcontinuum.context_graph import ConstraintRow
from astrcontinuum.context_graph.constraints import (
    ConstraintErrorCode,
    ConstraintSystemError,
    compile_constraints,
)


def test_fix_one_fix_zero_and_equality_compile_exact_rows() -> None:
    compiled = compile_constraints(
        ("a", "b", "c"),
        (
            ConstraintRow.fix_one("fix-a", "a"),
            ConstraintRow.fix_zero("fix-c", "c"),
            ConstraintRow.equality("equal-a-b", "a", "b"),
        ),
    )

    assert compiled.supports == (
        ("fix-a", ("a",)),
        ("fix-c", ("c",)),
        ("equal-a-b", ("a", "b")),
    )
    assert compiled.certificate_matrix.matvec((1.0, 1.0, 0.0)) == (1.0, 0.0, 0.0)
    assert compiled.certificate_rhs == (1.0, 0.0, 0.0)


def test_equality_rows_are_grouped_into_an_independent_system() -> None:
    compiled = compile_constraints(
        ("a", "b", "c"),
        (
            ConstraintRow.equality("a-b", "a", "b"),
            ConstraintRow.equality("b-c", "b", "c"),
            ConstraintRow.equality("a-c", "a", "c"),
        ),
    )

    assert compiled.matrix.shape == (2, 3)
    assert compiled.supports[-1] == ("a-c", ("a", "c"))


def test_contradictory_equality_group_is_rejected_content_free() -> None:
    with pytest.raises(ConstraintSystemError) as raised:
        compile_constraints(
            ("a", "b"),
            (
                ConstraintRow.fix_one("fix-a", "a"),
                ConstraintRow.equality("a-b", "a", "b"),
                ConstraintRow.fix_zero("fix-b", "b"),
            ),
        )

    assert raised.value.code is ConstraintErrorCode.CONTRADICTORY
    assert str(raised.value) == "CONSTRAINT_CONTRADICTORY"


def test_unsupported_constraint_shape_is_rejected() -> None:
    row = ConstraintRow(
        constraint_id="unsupported",
        terms=(("a", 2.0),),
        rhs=1.0,
    )

    with pytest.raises(ConstraintSystemError) as raised:
        compile_constraints(("a",), (row,))

    assert raised.value.code is ConstraintErrorCode.UNSUPPORTED
