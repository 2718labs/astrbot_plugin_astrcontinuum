from __future__ import annotations

import math

import pytest

from astrcontinuum.context_graph import (
    ConstraintRow,
    ContextEngineMode,
    ContextGraph,
    ContextGraphConfig,
    EngineOutcome,
    GraphCoordinate,
    SparseRelation,
)
from astrcontinuum.context_graph.matrix import build_propagation_operator
from tests.context_graph.helpers import candidate


def test_runtime_enums_are_closed_and_degraded_raw_is_not_a_mode() -> None:
    assert {item.value for item in ContextEngineMode} == {"ACTIVE", "SHADOW", "OFF"}
    assert {item.value for item in EngineOutcome} == {
        "ACTIVE",
        "SHADOW",
        "OFF",
        "DEGRADED_RAW",
    }
    with pytest.raises(ValueError):
        ContextEngineMode("DEGRADED_RAW")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("epsilon", math.nan),
        ("epsilon", math.inf),
        ("epsilon", 0.0),
        ("selection_threshold", -0.1),
        ("direct_backward_error_limit", 0.0),
        ("iterative_backward_error_limit", math.inf),
        ("condition_warning", 1.0e15),
        ("condition_failure", 1.0e11),
        ("max_coordinates", 0),
        ("numpy_max_n", 1_025),
        ("numpy_max_dense_bytes", 64 * 1024 * 1024 + 1),
    ],
)
def test_config_rejects_nonfinite_or_out_of_contract_values(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        ContextGraphConfig(**{field_name: value})


def test_graph_ids_are_unique_and_representations_exclude_candidate_text() -> None:
    secret = "never-render-this-candidate-text"
    block = candidate("block-1", text=secret)
    coordinate = GraphCoordinate("coordinate-1", block, diagonal=2.0)
    graph = ContextGraph(
        graph_id="graph-1",
        coordinates=(coordinate,),
        relations=(),
        constraints=(ConstraintRow.fix_one("required", "coordinate-1"),),
    )

    assert secret not in repr(coordinate)
    assert secret not in repr(graph)

    with pytest.raises(ValueError, match="unique"):
        ContextGraph(
            graph_id="graph-duplicate",
            coordinates=(coordinate, coordinate),
            relations=(),
            constraints=(),
        )


def test_operator_is_r_transpose_w_r_plus_diagonal_and_epsilon() -> None:
    graph = ContextGraph(
        graph_id="graph-q",
        coordinates=(
            GraphCoordinate("x", candidate("x"), diagonal=2.0),
            GraphCoordinate("y", candidate("y"), diagonal=3.0),
        ),
        relations=(
            SparseRelation(
                relation_id="relation-1",
                terms=(("x", 1.0), ("y", -2.0)),
                weight=4.0,
            ),
        ),
        constraints=(),
    )

    matrix = build_propagation_operator(graph, epsilon=0.5)

    assert matrix.to_dense(max_n=2, max_bytes=128) == (
        (6.5, -8.0),
        (-8.0, 19.5),
    )
