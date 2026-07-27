from __future__ import annotations

import pytest

import astrcontinuum.context_graph.engine as context_engine
from astrcontinuum.context_graph import (
    ConstraintRow,
    ContextEngineMode,
    ContextGraph,
    ContextGraphConfig,
    EngineOutcome,
    GraphCoordinate,
    QueryActivation,
    SparseContextEngine,
    SparseRelation,
)
from tests.context_graph.helpers import candidate


def graph_with_required_coordinate() -> ContextGraph:
    return ContextGraph(
        graph_id="graph-engine",
        coordinates=(
            GraphCoordinate(
                "required",
                candidate(
                    "required-block",
                    required=True,
                    source_event_ids=("event-required",),
                ),
                diagonal=1.0,
            ),
            GraphCoordinate(
                "optional",
                candidate(
                    "optional-block",
                    source_event_ids=("event-optional",),
                ),
                diagonal=1.0,
            ),
        ),
        relations=(
            SparseRelation(
                relation_id="relation",
                terms=(("required", 1.0), ("optional", -1.0)),
                weight=0.25,
            ),
        ),
        constraints=(ConstraintRow.fix_one("required-one", "required"),),
    )


def test_success_recomputes_every_certificate_gate() -> None:
    engine = SparseContextEngine(ContextGraphConfig(selection_threshold=0.5))
    activation = QueryActivation(
        query_id="query",
        scores=(("required", 1.0), ("optional", 0.8)),
        retained_coordinate_ids=("required",),
        provenance_event_ids=("event-required", "event-optional"),
    )

    result = engine.solve(
        graph_with_required_coordinate(),
        activation,
        mode=ContextEngineMode.ACTIVE,
    )

    assert result.outcome is EngineOutcome.ACTIVE
    assert result.error_code is None
    assert result.certificate is not None
    assert result.certificate.passed is True
    assert result.certificate.stationarity_error <= 1.0e-10
    assert result.certificate.constraint_error <= 1.0e-10
    assert result.certificate.reconstruction_error <= 1.0e-10
    assert result.certificate.required_blocks_passed is True
    assert result.certificate.provenance_passed is True
    assert [item.block_id for item in result.selected_blocks] == [
        "required-block",
        "optional-block",
    ]


def test_failed_provenance_gate_cannot_replace_fallback_output() -> None:
    engine = SparseContextEngine()
    activation = QueryActivation(
        query_id="query",
        scores=(("required", 1.0), ("optional", 1.0)),
        provenance_event_ids=("event-required",),
    )

    result = engine.solve(
        graph_with_required_coordinate(),
        activation,
        mode=ContextEngineMode.ACTIVE,
    )

    assert result.outcome is EngineOutcome.DEGRADED_RAW
    assert result.selected_blocks == ()
    assert result.error_code == "CERTIFICATE_PROVENANCE_FAILED"


def test_required_block_gate_rejects_unselected_required_coordinate() -> None:
    graph = ContextGraph(
        graph_id="required-gate",
        coordinates=(
            GraphCoordinate(
                "required",
                candidate("required", required=True),
                diagonal=1.0,
            ),
        ),
        relations=(),
        constraints=(),
    )
    result = SparseContextEngine(ContextGraphConfig(selection_threshold=0.5)).solve(
        graph,
        QueryActivation(
            query_id="query",
            scores=(("required", 0.1),),
            provenance_event_ids=("event-required",),
        ),
        mode=ContextEngineMode.ACTIVE,
    )

    assert result.outcome is EngineOutcome.DEGRADED_RAW
    assert result.error_code == "CERTIFICATE_REQUIRED_BLOCK_FAILED"


def test_shadow_computes_certificate_but_discards_selection() -> None:
    result = SparseContextEngine().solve(
        graph_with_required_coordinate(),
        QueryActivation(
            query_id="query",
            scores=(("required", 1.0), ("optional", 0.8)),
            provenance_event_ids=("event-required", "event-optional"),
        ),
        mode=ContextEngineMode.SHADOW,
    )

    assert result.outcome is EngineOutcome.SHADOW
    assert result.selected_blocks == ()
    assert result.certificate is not None
    assert result.certificate.passed is True


def test_off_mode_skips_graph_computation() -> None:
    result = SparseContextEngine().solve(
        graph_with_required_coordinate(),
        QueryActivation(query_id="query", scores=()),
        mode=ContextEngineMode.OFF,
    )

    assert result.outcome is EngineOutcome.OFF
    assert result.selected_blocks == ()
    assert result.certificate is None


def test_relation_expansion_capacity_is_checked_before_operator_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    term_count = 65
    graph = ContextGraph(
        graph_id="expanded-capacity",
        coordinates=tuple(
            GraphCoordinate(
                f"coordinate-{index}",
                candidate(f"block-{index}"),
                diagonal=1.0,
            )
            for index in range(term_count)
        ),
        relations=(
            SparseRelation(
                relation_id="large-relation",
                terms=tuple((f"coordinate-{index}", 1.0) for index in range(term_count)),
                weight=1.0,
            ),
        ),
        constraints=(),
    )
    builder_called = False

    def forbidden_builder(*_args: object, **_kwargs: object) -> None:
        nonlocal builder_called
        builder_called = True
        raise AssertionError("operator builder called before the expansion capacity gate")

    monkeypatch.setattr(context_engine, "build_propagation_operator", forbidden_builder)

    result = SparseContextEngine(ContextGraphConfig(max_relation_entries=4_096)).solve(
        graph,
        QueryActivation(query_id="query", scores=()),
        mode=ContextEngineMode.ACTIVE,
    )

    assert builder_called is False
    assert result.outcome is EngineOutcome.DEGRADED_RAW
    assert result.selected_blocks == ()
    assert result.error_code == "GRAPH_CAPACITY_EXCEEDED"


def test_rank_deficient_least_squares_cannot_pass_the_engine_certificate() -> None:
    graph = ContextGraph(
        graph_id="rank-deficient",
        coordinates=(
            GraphCoordinate("left", candidate("left"), diagonal=0.0),
            GraphCoordinate("right", candidate("right"), diagonal=0.0),
        ),
        relations=(
            SparseRelation(
                relation_id="rank-one",
                terms=(("left", 1.0), ("right", 1.0)),
                weight=1.0,
            ),
        ),
        constraints=(),
    )

    result = SparseContextEngine(
        ContextGraphConfig(
            epsilon=1.0e-300,
            selection_threshold=0.4,
            allow_least_squares=True,
        )
    ).solve(
        graph,
        QueryActivation(
            query_id="query",
            scores=(("left", 1.0), ("right", 1.0)),
            provenance_event_ids=("event-left", "event-right"),
        ),
        mode=ContextEngineMode.ACTIVE,
    )

    assert result.outcome is EngineOutcome.DEGRADED_RAW
    assert result.selected_blocks == ()
    assert result.error_code == "SOLVE_SINGULAR"


def test_query_activation_rejects_duplicate_or_nonfinite_scores() -> None:
    with pytest.raises(ValueError, match="unique"):
        QueryActivation(
            query_id="query",
            scores=(("a", 1.0), ("a", 2.0)),
        )
    with pytest.raises(ValueError, match="finite"):
        QueryActivation(
            query_id="query",
            scores=(("a", float("nan")),),
        )
