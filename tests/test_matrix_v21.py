"""RED-first public contract checks for V21 evidenced sparse matrices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from crm_experiment.contracts_v21 import CapsuleRoleV21
from crm_experiment.matrix_v21 import (
    EvidencedMatrixPlanV21,
    MatrixChildAxisV21,
    MatrixChildKindV21,
    MatrixDecisionAxisV21,
    MatrixDecisionXV21,
    MatrixDirectedEdgeV21,
    MatrixObjectiveV21,
    MatrixParentChildEdgeV21,
    MatrixSegmentAxisV21,
    MatrixSourceAxisV21,
    SparseBinaryCellV21,
    evidenced_matrix_root_v21,
)
from crm_experiment.reencoding_contracts_v21 import (
    MAX_EVIDENCED_COVERAGE_VECTOR_V21,
    MAX_EVIDENCED_HARD_EDGES_V21,
    MAX_EVIDENCED_LOSS_UNITS_V21,
    MAX_EVIDENCED_NEW_SEGMENTS_V21,
    MAX_EVIDENCED_PLAN_EVALUATIONS_V21,
    MAX_EVIDENCED_ROOT_ROWS_V21,
    MAX_EVIDENCED_SOURCE_RECORDS_V21,
    ReencodingOutcomeV21,
    SegmentDispositionV21,
    SolverModeV21,
)


def _digest(domain: str, digit: str) -> str:
    return f"{domain}:{digit * 64}"


def _matrix_payload() -> dict[str, Any]:
    source = MatrixSourceAxisV21(
        record_id=_digest("v21-record-id-s3", "1"),
        commitment=_digest("v21-source-commitment-s3", "2"),
        role=CapsuleRoleV21.CONTEXT,
        core_required=False,
        contribution_keys=(_digest("v21-dictionary-id-s3", "3"),),
    )
    return {
        "base_state_hash": _digest("crm-v21-evidenced-state-s3/v1", "4"),
        "source_envelope_root": _digest("crm-v21-source-envelope-s3/v1", "5"),
        "policy_root": _digest("crm-v21-evidenced-policy-s3/v1", "6"),
        "solver_mode": SolverModeV21.EXACT_SMALL,
        "source_axes": (source,),
        "segment_axes": (),
        "child_axes": (),
        "h_edges": (MatrixDirectedEdgeV21(source_row=0, target_row=0),),
        "mandatory_rows": (0,),
        "f_rows": (SparseBinaryCellV21(row_index=0, column_index=0),),
        "g_rows": (),
        "p_edges": (),
        "x_rows": (
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SOURCE,
                row_index=0,
                record_outcome=ReencodingOutcomeV21.EXACT,
                segment_disposition=None,
                child_row=None,
            ),
        ),
        "h_width": 1,
        "m_width": 1,
        "f_width": 1,
        "g_width": 1,
        "p_width": 0,
        "x_width": 1,
        "evaluation_count": 1,
        "objective": MatrixObjectiveV21(
            control_bytes=1,
            source_body_bytes=2,
            resident_bytes=3,
            loss_units=0,
            saved_bytes=0,
        ),
        "record_decision_root": _digest("crm-v21-record-decisions-s3/v1", "7"),
        "segment_decision_root": _digest("crm-v21-segment-decisions-s3/v1", "8"),
        "proposed_hash": _digest("crm-v21-evidenced-state-s3/v1", "9"),
        "target_generation": 1,
        "target_high_water": 1,
    }


def _directed_h_payload() -> dict[str, Any]:
    payload = _matrix_payload()
    first = payload["source_axes"][0]
    second = MatrixSourceAxisV21(
        record_id=_digest("v21-record-id-s3", "a"),
        commitment=_digest("v21-source-commitment-s3", "b"),
        role=CapsuleRoleV21.ROOT_GOAL,
        core_required=False,
        contribution_keys=(_digest("v21-dictionary-id-s3", "c"),),
    )
    payload.update(
        source_axes=(first, second),
        h_edges=(MatrixDirectedEdgeV21(source_row=0, target_row=1),),
        mandatory_rows=(0, 1),
        f_rows=(SparseBinaryCellV21(row_index=0, column_index=0),),
        g_rows=(SparseBinaryCellV21(row_index=1, column_index=0),),
        x_rows=(
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SOURCE,
                row_index=0,
                record_outcome=ReencodingOutcomeV21.EXACT,
                segment_disposition=None,
                child_row=None,
            ),
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SOURCE,
                row_index=1,
                record_outcome=ReencodingOutcomeV21.EXACT,
                segment_disposition=None,
                child_row=None,
            ),
        ),
        h_width=2,
        m_width=2,
        x_width=2,
    )
    return payload


def _compact_payload() -> dict[str, Any]:
    payload = _matrix_payload()
    segment = MatrixSegmentAxisV21(
        segment_id=_digest("v21-segment-id-s3", "d"),
        canonical_hash=_digest("crm-v21-canonical-segment-s3/v1", "e"),
        role_counts=((CapsuleRoleV21.CONTEXT, 1),),
        contribution_keys=(_digest("v21-dictionary-id-s3", "3"),),
    )
    child = MatrixChildAxisV21(
        child_id=_digest("v21-segment-id-s3", "f"),
        kind=MatrixChildKindV21.PARENT_COMPACT,
        source_rows=(),
        parent_rows=(0,),
    )
    payload.update(
        segment_axes=(segment,),
        child_axes=(child,),
        p_edges=(MatrixParentChildEdgeV21(parent_row=0, child_row=0),),
        x_rows=(
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SOURCE,
                row_index=0,
                record_outcome=ReencodingOutcomeV21.EXACT,
                segment_disposition=None,
                child_row=None,
            ),
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SEGMENT,
                row_index=0,
                record_outcome=None,
                segment_disposition=SegmentDispositionV21.COMPACT,
                child_row=0,
            ),
        ),
        p_width=1,
        x_width=2,
    )
    return payload


def _rootless_child_without_segment_x_payload() -> dict[str, Any]:
    payload = _matrix_payload()
    payload.update(
        child_axes=(
            MatrixChildAxisV21(
                child_id=_digest("v21-segment-id-s3", "b"),
                kind=MatrixChildKindV21.ROOTLESS_SEGMENT,
                source_rows=(0,),
                parent_rows=(),
            ),
        ),
        p_width=1,
    )
    return payload


def _indexed_digest(domain: str, index: int) -> str:
    return f"{domain}:{index:064x}"


def _many_source_payload(source_count: int) -> dict[str, Any]:
    payload = _matrix_payload()
    sources = tuple(
        MatrixSourceAxisV21(
            record_id=_indexed_digest("v21-record-id-s3", index + 1),
            commitment=_indexed_digest("v21-source-commitment-s3", index + 1),
            role=CapsuleRoleV21.CONTEXT,
            core_required=False,
            contribution_keys=(_digest("v21-dictionary-id-s3", "3"),),
        )
        for index in range(source_count)
    )
    payload.update(
        source_axes=sources,
        h_edges=(),
        mandatory_rows=(),
        f_rows=(),
        g_rows=(),
        x_rows=tuple(
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SOURCE,
                row_index=index,
                record_outcome=ReencodingOutcomeV21.EXACT,
                segment_disposition=None,
                child_row=None,
            )
            for index in range(source_count)
        ),
        h_width=source_count,
        m_width=source_count,
        f_width=0,
        g_width=0,
        p_width=0,
        x_width=source_count,
    )
    return payload


def _oversized_h_payload() -> dict[str, Any]:
    source_count = 33
    payload = _many_source_payload(source_count)
    payload.update(
        h_edges=tuple(
            MatrixDirectedEdgeV21(source_row=source, target_row=target)
            for source in range(source_count)
            for target in range(source_count)
        )[: MAX_EVIDENCED_HARD_EDGES_V21 + 1],
        mandatory_rows=tuple(range(source_count)),
    )
    return payload


def _oversized_child_payload() -> dict[str, Any]:
    child_count = MAX_EVIDENCED_NEW_SEGMENTS_V21 + 1
    payload = _many_source_payload(child_count)
    payload.update(
        child_axes=tuple(
            MatrixChildAxisV21(
                child_id=_indexed_digest("v21-segment-id-s3", index + 1),
                kind=MatrixChildKindV21.ROOTLESS_SEGMENT,
                source_rows=(index,),
                parent_rows=(),
            )
            for index in range(child_count)
        ),
        x_rows=tuple(
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SOURCE,
                row_index=index,
                record_outcome=ReencodingOutcomeV21.SEGMENT,
                segment_disposition=None,
                child_row=index,
            )
            for index in range(child_count)
        ),
        p_width=child_count,
    )
    return payload


def _oversized_decision_payload() -> dict[str, Any]:
    payload = _matrix_payload()
    segment_count = MAX_EVIDENCED_ROOT_ROWS_V21
    payload.update(
        segment_axes=tuple(
            MatrixSegmentAxisV21(
                segment_id=_indexed_digest("v21-segment-id-s3", index + 1),
                canonical_hash=_indexed_digest(
                    "crm-v21-canonical-segment-s3/v1", index + 1
                ),
                role_counts=(),
                contribution_keys=(),
            )
            for index in range(segment_count)
        ),
        x_rows=payload["x_rows"]
        + tuple(
            MatrixDecisionXV21(
                axis=MatrixDecisionAxisV21.SEGMENT,
                row_index=index,
                record_outcome=None,
                segment_disposition=SegmentDispositionV21.RETAIN,
                child_row=None,
            )
            for index in range(segment_count)
        ),
        x_width=1 + segment_count,
    )
    return payload


def test_matrix_root_boundary_is_public() -> None:
    assert EvidencedMatrixPlanV21 is not None
    assert callable(evidenced_matrix_root_v21)


def test_sparse_matrix_root_binds_ordered_axes_one_hot_x_and_objective() -> None:
    payload = _matrix_payload()
    root = evidenced_matrix_root_v21(**payload)
    plan = EvidencedMatrixPlanV21(**payload, matrix_root=root)

    assert plan.matrix_root == root
    assert plan.x_rows[0].record_outcome is ReencodingOutcomeV21.EXACT

    with pytest.raises(ValueError, match="matrix_root"):
        replace(plan, matrix_root=_digest("crm-v21-evidenced-matrix-s3/v1", "a"))

    tampered = dict(payload)
    tampered["mandatory_rows"] = ()
    with pytest.raises(ValueError, match="mandatory"):
        EvidencedMatrixPlanV21(
            **tampered, matrix_root=evidenced_matrix_root_v21(**tampered)
        )


def test_h_is_directed_and_must_close_both_m_endpoints() -> None:
    payload = _directed_h_payload()
    forward_root = evidenced_matrix_root_v21(**payload)

    reverse = dict(payload)
    reverse["h_edges"] = (MatrixDirectedEdgeV21(source_row=1, target_row=0),)
    assert evidenced_matrix_root_v21(**reverse) != forward_root

    missing_endpoint = dict(payload)
    missing_endpoint["mandatory_rows"] = (0,)
    with pytest.raises(ValueError, match="mandatory"):
        evidenced_matrix_root_v21(**missing_endpoint)


def test_m_exactly_binds_core_required_rows_and_h_endpoints() -> None:
    payload = _matrix_payload()
    payload.update(
        source_axes=(replace(payload["source_axes"][0], core_required=True),),
        h_edges=(),
        mandatory_rows=(0,),
    )
    assert evidenced_matrix_root_v21(**payload)

    missing_core = dict(payload)
    missing_core["mandatory_rows"] = ()
    with pytest.raises(ValueError, match="mandatory"):
        evidenced_matrix_root_v21(**missing_core)

    extra_mandatory = _many_source_payload(2)
    extra_mandatory["mandatory_rows"] = (0,)
    with pytest.raises(ValueError, match="mandatory"):
        evidenced_matrix_root_v21(**extra_mandatory)


def test_public_root_revalidates_runtime_mutated_sparse_cells() -> None:
    payload = _matrix_payload()
    object.__setattr__(payload["f_rows"][0], "row_index", -1)

    with pytest.raises(ValueError, match="sparse cell row_index"):
        evidenced_matrix_root_v21(**payload)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("record_id", "not-a-domain-digest", "source record_id"),
        ("core_required", 1, "core_required"),
    ),
)
def test_public_root_revalidates_runtime_mutated_source_axes(
    field: str, value: object, match: str
) -> None:
    payload = _matrix_payload()
    object.__setattr__(payload["source_axes"][0], field, value)

    with pytest.raises(ValueError, match=match):
        evidenced_matrix_root_v21(**payload)


def test_public_root_revalidates_runtime_mutated_objectives() -> None:
    payload = _matrix_payload()
    object.__setattr__(payload["objective"], "resident_bytes", 999)

    with pytest.raises(ValueError, match="resident_bytes"):
        evidenced_matrix_root_v21(**payload)


@pytest.mark.parametrize(
    ("payload_factory", "field_name", "row_index", "attribute", "value", "match"),
    (
        (
            _compact_payload,
            "segment_axes",
            0,
            "canonical_hash",
            "bad",
            "segment canonical_hash",
        ),
        (_compact_payload, "child_axes", 0, "child_id", "bad", "child id"),
        (_matrix_payload, "h_edges", 0, "source_row", 0.0, "H source_row"),
        (_directed_h_payload, "g_rows", 0, "column_index", -1, "sparse cell"),
        (_compact_payload, "p_edges", 0, "parent_row", 0.0, "P parent_row"),
        (
            lambda: _many_source_payload(1),
            "x_rows",
            0,
            "record_outcome",
            "BOGUS",
            "source X row",
        ),
    ),
)
def test_public_root_revalidates_every_runtime_mutated_nominal_row(
    payload_factory: Callable[[], dict[str, Any]],
    field_name: str,
    row_index: int,
    attribute: str,
    value: object,
    match: str,
) -> None:
    payload = payload_factory()
    object.__setattr__(payload[field_name][row_index], attribute, value)

    with pytest.raises(ValueError, match=match):
        evidenced_matrix_root_v21(**payload)


def test_plan_path_reuses_the_public_runtime_mutation_validation() -> None:
    payload = _matrix_payload()
    root = evidenced_matrix_root_v21(**payload)
    plan = EvidencedMatrixPlanV21(**payload, matrix_root=root)
    object.__setattr__(plan.objective, "resident_bytes", 999)

    with pytest.raises(ValueError, match="resident_bytes"):
        replace(plan)


def test_f_g_p_and_x_require_canonical_sparse_relations() -> None:
    payload = _compact_payload()
    root = evidenced_matrix_root_v21(**payload)
    plan = EvidencedMatrixPlanV21(**payload, matrix_root=root)
    assert plan.p_edges == (MatrixParentChildEdgeV21(parent_row=0, child_row=0),)
    assert plan.f_rows == (SparseBinaryCellV21(row_index=0, column_index=0),)
    assert plan.g_rows == ()

    missing_parent_edge = dict(payload)
    missing_parent_edge["p_edges"] = ()
    with pytest.raises(ValueError, match="P"):
        evidenced_matrix_root_v21(**missing_parent_edge)

    noncanonical_x = dict(payload)
    noncanonical_x["x_rows"] = tuple(reversed(payload["x_rows"]))
    with pytest.raises(ValueError, match="one-hot"):
        evidenced_matrix_root_v21(**noncanonical_x)

    noncanonical_f = dict(payload)
    noncanonical_f["f_width"] = 2
    noncanonical_f["f_rows"] = (
        SparseBinaryCellV21(row_index=0, column_index=1),
        SparseBinaryCellV21(row_index=0, column_index=0),
    )
    with pytest.raises(ValueError, match="F"):
        evidenced_matrix_root_v21(**noncanonical_f)


def test_sparse_f_g_cells_reject_dense_zero_duplicate_and_out_of_range_payloads() -> (
    None
):
    payload = _matrix_payload()

    dense_f = dict(payload)
    dense_f["f_rows"] = ((0, (1,)),)
    with pytest.raises(ValueError, match="F"):
        evidenced_matrix_root_v21(**dense_f)

    valued_f = dict(payload)
    valued_f["f_rows"] = ({"row_index": 0, "column_index": 0, "value": 0},)
    with pytest.raises(ValueError, match="SparseBinaryCell"):
        evidenced_matrix_root_v21(**valued_f)

    duplicate_f = dict(payload)
    duplicate_f["f_rows"] = (
        SparseBinaryCellV21(row_index=0, column_index=0),
        SparseBinaryCellV21(row_index=0, column_index=0),
    )
    with pytest.raises(ValueError, match="F"):
        evidenced_matrix_root_v21(**duplicate_f)

    outside_g = dict(payload)
    outside_g["g_rows"] = (SparseBinaryCellV21(row_index=1, column_index=0),)
    with pytest.raises(ValueError, match="G"):
        evidenced_matrix_root_v21(**outside_g)


def test_sparse_f_g_widths_bind_the_axes_without_dense_zero_padding() -> None:
    payload = _matrix_payload()
    payload.update(
        f_width=MAX_EVIDENCED_COVERAGE_VECTOR_V21,
        f_rows=(
            SparseBinaryCellV21(
                row_index=0,
                column_index=MAX_EVIDENCED_COVERAGE_VECTOR_V21 - 1,
            ),
        ),
    )

    root = evidenced_matrix_root_v21(**payload)
    plan = EvidencedMatrixPlanV21(**payload, matrix_root=root)
    assert plan.f_width == MAX_EVIDENCED_COVERAGE_VECTOR_V21
    assert len(plan.f_rows) == 1


def test_child_axes_require_reverse_one_hot_x_ownership_before_rooting() -> None:
    rootless_without_segment_x = _rootless_child_without_segment_x_payload()
    with pytest.raises(ValueError, match="rootless child"):
        evidenced_matrix_root_v21(**rootless_without_segment_x)

    compact_without_compact_x = _compact_payload()
    compact_without_compact_x["x_rows"] = (
        compact_without_compact_x["x_rows"][0],
        MatrixDecisionXV21(
            axis=MatrixDecisionAxisV21.SEGMENT,
            row_index=0,
            record_outcome=None,
            segment_disposition=SegmentDispositionV21.RETAIN,
            child_row=None,
        ),
    )
    with pytest.raises(ValueError, match="compact child"):
        evidenced_matrix_root_v21(**compact_without_compact_x)


def test_matrix_evaluation_count_has_the_public_global_cap() -> None:
    payload = _matrix_payload()
    payload["evaluation_count"] = MAX_EVIDENCED_PLAN_EVALUATIONS_V21 + 1

    with pytest.raises(ValueError, match="evaluation_count"):
        evidenced_matrix_root_v21(**payload)


def test_matrix_public_bounds_reject_oversized_f_h_and_child_relations() -> None:
    oversized_f = _matrix_payload()
    oversized_f.update(
        f_width=MAX_EVIDENCED_COVERAGE_VECTOR_V21 + 1,
        f_rows=(
            SparseBinaryCellV21(
                row_index=0,
                column_index=MAX_EVIDENCED_COVERAGE_VECTOR_V21,
            ),
        ),
    )
    with pytest.raises(ValueError, match="F width"):
        evidenced_matrix_root_v21(**oversized_f)

    with pytest.raises(ValueError, match="H"):
        evidenced_matrix_root_v21(**_oversized_h_payload())

    with pytest.raises(ValueError, match="child"):
        evidenced_matrix_root_v21(**_oversized_child_payload())

    with pytest.raises(ValueError, match="source_axes"):
        evidenced_matrix_root_v21(
            **_many_source_payload(MAX_EVIDENCED_SOURCE_RECORDS_V21 + 1)
        )

    with pytest.raises(ValueError, match="decision-row"):
        evidenced_matrix_root_v21(**_oversized_decision_payload())

    oversized_loss = _matrix_payload()
    oversized_loss["objective"] = replace(
        oversized_loss["objective"],
        loss_units=MAX_EVIDENCED_LOSS_UNITS_V21 + 1,
    )
    with pytest.raises(ValueError, match="loss_units"):
        evidenced_matrix_root_v21(**oversized_loss)
