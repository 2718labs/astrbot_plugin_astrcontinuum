"""RED-first public checks for bounded V21 multi-round reachability."""

from __future__ import annotations

from dataclasses import fields, replace
from typing import cast

import pytest
from test_evidenced_planner_v21 import _deep_compact_case, _hard_closure_case
from test_evidenced_reencoder_v21 import (
    _compact_segment_case,
    _contribution,
    _envelope,
    _exact_only_case,
    _record,
    _rootless_segment_case,
    _source,
)

import crm_experiment.multiround_v21 as multiround_module
from crm_experiment.contracts_v21 import CapsuleRoleV21, canonical_json_v21
from crm_experiment.evidenced_planner_v21 import (
    EvidencedPlannedCandidateV21,
    EvidencedPlanningRejectedV21,
    plan_evidenced_reencoding_v21,
)
from crm_experiment.evidenced_reencoder_v21 import (
    EvidencedReencodingCandidateV21,
    EvidencedReencodingStatusV21,
)
from crm_experiment.evidenced_state_v21 import (
    EvidencedCapsuleStateV21,
    evidenced_resident_layout_v21,
    evidenced_state_hash_v21,
)
from crm_experiment.matrix_v21 import (
    EvidencedMatrixPlanV21,
    MatrixChildAxisV21,
    MatrixDecisionAxisV21,
    MatrixDecisionXV21,
    MatrixDirectedEdgeV21,
    MatrixSourceAxisV21,
    SparseBinaryCellV21,
    evidenced_matrix_root_v21,
)
from crm_experiment.multiround_v21 import (
    EvidencedMultiroundResultV21,
    MultiroundExitReasonV21,
    MultiroundStatusV21,
    MultiroundTraceV21,
    RoundInputV21,
    evaluate_evidenced_multiround_v21,
    validate_evidenced_multiround_result_v21,
)
from crm_experiment.reencoding_contracts_v21 import (
    AdvanceChainHeadV21,
    EvidencedReencodingPolicyV21,
    ReencodingOutcomeV21,
    SolverModeV21,
    SourceEnvelopeV21,
    evidenced_reencoding_policy_root_v21,
    evidenced_transition_root_v21,
)


def _other_root(root: str) -> str:
    domain, _digest = root.rsplit(":", maxsplit=1)
    return f"{domain}:{'f' * 64}"


def _rebuilt_matrix_root(
    matrix: EvidencedMatrixPlanV21,
    *,
    solver_mode: SolverModeV21,
    evaluation_count: int,
    source_axes: tuple[MatrixSourceAxisV21, ...],
    child_axes: tuple[MatrixChildAxisV21, ...] | None = None,
    h_edges: tuple[MatrixDirectedEdgeV21, ...] | None = None,
    f_rows: tuple[SparseBinaryCellV21, ...] | None = None,
    g_rows: tuple[SparseBinaryCellV21, ...] | None = None,
    x_rows: tuple[MatrixDecisionXV21, ...] | None = None,
) -> str:
    return evidenced_matrix_root_v21(
        base_state_hash=matrix.base_state_hash,
        source_envelope_root=matrix.source_envelope_root,
        policy_root=matrix.policy_root,
        solver_mode=solver_mode,
        source_axes=source_axes,
        segment_axes=matrix.segment_axes,
        child_axes=matrix.child_axes if child_axes is None else child_axes,
        h_edges=matrix.h_edges if h_edges is None else h_edges,
        mandatory_rows=matrix.mandatory_rows,
        f_rows=matrix.f_rows if f_rows is None else f_rows,
        g_rows=matrix.g_rows if g_rows is None else g_rows,
        p_edges=matrix.p_edges,
        x_rows=matrix.x_rows if x_rows is None else x_rows,
        h_width=matrix.h_width,
        m_width=matrix.m_width,
        f_width=matrix.f_width,
        g_width=matrix.g_width,
        p_width=matrix.p_width,
        x_width=matrix.x_width,
        evaluation_count=evaluation_count,
        objective=matrix.objective,
        record_decision_root=matrix.record_decision_root,
        segment_decision_root=matrix.segment_decision_root,
        proposed_hash=matrix.proposed_hash,
        target_generation=matrix.target_generation,
        target_high_water=matrix.target_high_water,
    )


def _synchronize_forged_matrix(
    planned: EvidencedPlannedCandidateV21,
    matrix: EvidencedMatrixPlanV21,
) -> EvidencedPlannedCandidateV21:
    candidate = planned.candidate
    transition_root = evidenced_transition_root_v21(
        base_state_hash=candidate.source_envelope.base_state_hash,
        source_envelope_root=candidate.source_envelope.envelope_root,
        policy_root=evidenced_reencoding_policy_root_v21(candidate.policy),
        matrix_root=matrix.matrix_root,
        record_decision_root=candidate.record_decision_root,
        segment_decision_root=candidate.segment_decision_root,
        authorization_roots=candidate.authorization_roots,
        barrier_roots=candidate.barrier_roots,
        ledger_root=candidate.ledger_root,
        proposed_hash=candidate.proposed_hash,
        target_generation=candidate.target_generation,
        target_high_water=candidate.target_high_water,
    )
    forged_candidate = replace(
        candidate,
        matrix_root=matrix.matrix_root,
        transition_root=transition_root,
    )
    forged_receipt = replace(planned.verification, transition_root=transition_root)
    return replace(
        planned,
        matrix_plan=matrix,
        candidate=forged_candidate,
        verification=forged_receipt,
    )


def _assert_forged_matrix_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial_state: EvidencedCapsuleStateV21,
    initial_head: AdvanceChainHeadV21,
    rounds: tuple[RoundInputV21, ...],
    forged: EvidencedPlannedCandidateV21,
) -> None:
    def return_forged(
        *_args: object, **_kwargs: object
    ) -> EvidencedPlannedCandidateV21:
        return forged

    monkeypatch.setattr(
        multiround_module, "plan_evidenced_reencoding_v21", return_forged
    )
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )
    assert result.status is MultiroundStatusV21.POSTCONDITION_REJECTED
    assert result.traces == ()
    assert result.final_state_hash == evidenced_state_hash_v21(initial_state)
    assert result.final_advance_head_root == initial_head.head_root


def _make_round(
    *,
    round_index: int,
    base: EvidencedCapsuleStateV21,
    head: AdvanceChainHeadV21,
    envelope: SourceEnvelopeV21,
    policy: EvidencedReencodingPolicyV21,
) -> RoundInputV21:
    return RoundInputV21(
        round_index=round_index,
        source_envelope=envelope,
        policy=policy,
        expected_base_state_hash=evidenced_state_hash_v21(base),
        expected_source_envelope_root=envelope.envelope_root,
        expected_policy_root=evidenced_reencoding_policy_root_v21(policy),
        expected_advance_head_root=head.head_root,
    )


def _prepare_exact_rounds(
    count: int,
) -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    tuple[RoundInputV21, ...],
    tuple[EvidencedPlannedCandidateV21, ...],
]:
    """Build concrete, chained source snapshots using V21-006 as test setup."""
    base, head, fixture, _base_hash, _envelope_root, _policy_root = _exact_only_case()
    initial_state = base
    initial_head = head
    policy = fixture.policy
    core_id = base.state.exact_kernel[0].record_id
    incoming_id = fixture.source_envelope.incoming_record_ids[0]
    core_key = base.contribution_index[0].contribution_key
    incoming_key = next(
        row.contribution_keys[0]
        for row in fixture.source_envelope.contribution_evidence
        if row.record_id == incoming_id
    )
    rounds: list[RoundInputV21] = []
    expected: list[EvidencedPlannedCandidateV21] = []
    for round_index in range(count):
        core = next(row for row in base.state.exact_kernel if row.record_id == core_id)
        incoming = _record(
            "incoming",
            as_of=initial_state.state.frame.high_water + round_index + 1,
            core_required=False,
        )
        sources = tuple(
            sorted((_source(core), _source(incoming)), key=lambda row: row.record_id)
        )
        source_by_id = {row.record_id: row for row in sources}
        envelope = _envelope(
            base_hash=evidenced_state_hash_v21(base),
            sources=sources,
            incoming_ids=(incoming.record_id,),
            contributions=tuple(
                sorted(
                    (
                        _contribution(
                            source_by_id[core.record_id],
                            core_key,
                            CapsuleRoleV21.ROOT_GOAL,
                        ),
                        _contribution(
                            source_by_id[incoming.record_id],
                            incoming_key,
                            CapsuleRoleV21.CONTEXT,
                        ),
                    ),
                    key=lambda row: row.record_id,
                )
            ),
        )
        round_input = _make_round(
            round_index=round_index,
            base=base,
            head=head,
            envelope=envelope,
            policy=policy,
        )
        planned = plan_evidenced_reencoding_v21(
            base,
            head,
            envelope,
            policy,
            expected_base_state_hash=round_input.expected_base_state_hash,
            expected_source_envelope_root=round_input.expected_source_envelope_root,
            expected_policy_root=round_input.expected_policy_root,
            expected_advance_head_root=round_input.expected_advance_head_root,
        )
        assert planned.verification.status is EvidencedReencodingStatusV21.VERIFIED
        rounds.append(round_input)
        expected.append(planned)
        base = planned.verification.state
        head = planned.verification.advance_head
    return initial_state, initial_head, tuple(rounds), tuple(expected)


def _one_round_from_case(
    case: tuple[
        EvidencedCapsuleStateV21,
        AdvanceChainHeadV21,
        EvidencedReencodingCandidateV21,
        str,
        str,
        str,
    ],
    *,
    max_plan_evaluations: int,
) -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    tuple[RoundInputV21, ...],
    EvidencedPlannedCandidateV21,
]:
    base, head, fixture, _base_hash, _envelope_root, _policy_root = case
    policy = replace(fixture.policy, max_plan_evaluations=max_plan_evaluations)
    round_input = _make_round(
        round_index=0,
        base=base,
        head=head,
        envelope=fixture.source_envelope,
        policy=policy,
    )
    expected = plan_evidenced_reencoding_v21(
        base,
        head,
        round_input.source_envelope,
        policy,
        expected_base_state_hash=round_input.expected_base_state_hash,
        expected_source_envelope_root=round_input.expected_source_envelope_root,
        expected_policy_root=round_input.expected_policy_root,
        expected_advance_head_root=round_input.expected_advance_head_root,
    )
    return base, head, (round_input,), expected


def test_multiround_evaluator_boundary_is_public() -> None:
    assert callable(evaluate_evidenced_multiround_v21)


def test_three_real_chained_rounds_use_one_planner_call_per_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _prepare_exact_rounds(3)
    calls = 0
    original = plan_evidenced_reencoding_v21

    def counted(
        base: EvidencedCapsuleStateV21,
        current_head: AdvanceChainHeadV21,
        source_envelope: SourceEnvelopeV21,
        policy: EvidencedReencodingPolicyV21,
        *,
        expected_base_state_hash: str,
        expected_source_envelope_root: str,
        expected_policy_root: str,
        expected_advance_head_root: str,
    ) -> EvidencedPlannedCandidateV21:
        nonlocal calls
        calls += 1
        return original(
            base,
            current_head,
            source_envelope,
            policy,
            expected_base_state_hash=expected_base_state_hash,
            expected_source_envelope_root=expected_source_envelope_root,
            expected_policy_root=expected_policy_root,
            expected_advance_head_root=expected_advance_head_root,
        )

    monkeypatch.setattr(multiround_module, "plan_evidenced_reencoding_v21", counted)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )

    assert calls == 3
    assert result.status is MultiroundStatusV21.COMPLETED
    assert result.exit_reason is MultiroundExitReasonV21.COMPLETED
    assert result.final_state_hash == evidenced_state_hash_v21(
        expected[-1].verification.state
    )
    assert (
        result.final_advance_head_root
        == expected[-1].verification.advance_head.head_root
    )
    assert [trace.round_index for trace in result.traces] == [0, 1, 2]
    assert all(trace.release_count == 0 for trace in result.traces)


def test_stale_second_round_stops_before_a_third_planner_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, _expected = _prepare_exact_rounds(3)
    stale_round = replace(
        rounds[1],
        expected_base_state_hash=_other_root(rounds[1].expected_base_state_hash),
    )
    calls = 0
    original = plan_evidenced_reencoding_v21

    def counted(
        base: EvidencedCapsuleStateV21,
        current_head: AdvanceChainHeadV21,
        source_envelope: SourceEnvelopeV21,
        policy: EvidencedReencodingPolicyV21,
        *,
        expected_base_state_hash: str,
        expected_source_envelope_root: str,
        expected_policy_root: str,
        expected_advance_head_root: str,
    ) -> EvidencedPlannedCandidateV21:
        nonlocal calls
        calls += 1
        return original(
            base,
            current_head,
            source_envelope,
            policy,
            expected_base_state_hash=expected_base_state_hash,
            expected_source_envelope_root=expected_source_envelope_root,
            expected_policy_root=expected_policy_root,
            expected_advance_head_root=expected_advance_head_root,
        )

    monkeypatch.setattr(multiround_module, "plan_evidenced_reencoding_v21", counted)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        (rounds[0], stale_round, rounds[2]),
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )

    assert calls == 1
    assert result.status is MultiroundStatusV21.STALE
    assert result.exit_reason is MultiroundExitReasonV21.STALE_ROUND_ANCHOR
    assert len(result.traces) == 1
    assert result.failed_round_index == 1
    assert result.reason == "round anchor mismatch"
    assert result.final_state_hash == result.traces[0].after_state_hash
    assert result.final_advance_head_root == result.traces[0].after_advance_head_root


def test_planner_rejection_never_releases_or_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, _expected = _one_round_from_case(
        _rootless_segment_case(), max_plan_evaluations=2
    )

    def reject(*_args: object, **_kwargs: object) -> EvidencedPlannedCandidateV21:
        raise EvidencedPlanningRejectedV21("test planner rejection")

    monkeypatch.setattr(multiround_module, "plan_evidenced_reencoding_v21", reject)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )

    assert result.status is MultiroundStatusV21.REJECTED
    assert result.exit_reason is MultiroundExitReasonV21.PLANNER_REJECTED
    assert result.final_state_hash == evidenced_state_hash_v21(initial_state)
    assert result.final_advance_head_root == initial_head.head_root
    assert result.traces == ()
    assert result.failed_round_index == 0
    assert result.reason == "planner rejected"


def test_bad_verified_postcondition_never_releases_or_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _one_round_from_case(
        _rootless_segment_case(), max_plan_evaluations=2
    )

    def corrupted(*_args: object, **_kwargs: object) -> EvidencedPlannedCandidateV21:
        return replace(
            expected,
            verification=replace(expected.verification, consumed_delta=False),
        )

    monkeypatch.setattr(multiround_module, "plan_evidenced_reencoding_v21", corrupted)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )

    assert result.status is MultiroundStatusV21.POSTCONDITION_REJECTED
    assert result.exit_reason is MultiroundExitReasonV21.POSTCONDITION_REJECTED
    assert result.final_state_hash == evidenced_state_hash_v21(initial_state)
    assert result.final_advance_head_root == initial_head.head_root
    assert result.traces == ()
    assert result.failed_round_index == 0
    assert result.reason == "postcondition rejected"


def test_forged_matrix_solver_mode_is_rejected_before_success_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _prepare_exact_rounds(1)
    matrix = expected[0].matrix_plan
    forged_mode = (
        SolverModeV21.DETERMINISTIC_GREEDY
        if matrix.solver_mode is SolverModeV21.EXACT_SMALL
        else SolverModeV21.EXACT_SMALL
    )
    forged_matrix = replace(
        matrix,
        solver_mode=forged_mode,
        matrix_root=_rebuilt_matrix_root(
            matrix,
            solver_mode=forged_mode,
            evaluation_count=matrix.evaluation_count,
            source_axes=matrix.source_axes,
        ),
    )

    _assert_forged_matrix_is_rejected(
        monkeypatch,
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=rounds,
        forged=_synchronize_forged_matrix(expected[0], forged_matrix),
    )


def test_forged_matrix_evaluation_cap_is_rejected_before_success_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _prepare_exact_rounds(1)
    matrix = expected[0].matrix_plan
    forged_evaluations = matrix.evaluation_count + 1
    assert forged_evaluations > rounds[0].policy.max_plan_evaluations
    forged_matrix = replace(
        matrix,
        evaluation_count=forged_evaluations,
        matrix_root=_rebuilt_matrix_root(
            matrix,
            solver_mode=matrix.solver_mode,
            evaluation_count=forged_evaluations,
            source_axes=matrix.source_axes,
        ),
    )

    _assert_forged_matrix_is_rejected(
        monkeypatch,
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=rounds,
        forged=_synchronize_forged_matrix(expected[0], forged_matrix),
    )


def test_forged_matrix_source_role_is_rejected_before_success_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _prepare_exact_rounds(1)
    matrix = expected[0].matrix_plan
    original_axis = matrix.source_axes[0]
    forged_role = next(
        role for role in CapsuleRoleV21 if role is not original_axis.role
    )
    forged_axes = (
        replace(original_axis, role=forged_role),
        *matrix.source_axes[1:],
    )
    forged_matrix = replace(
        matrix,
        source_axes=forged_axes,
        matrix_root=_rebuilt_matrix_root(
            matrix,
            solver_mode=matrix.solver_mode,
            evaluation_count=matrix.evaluation_count,
            source_axes=forged_axes,
        ),
    )

    _assert_forged_matrix_is_rejected(
        monkeypatch,
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=rounds,
        forged=_synchronize_forged_matrix(expected[0], forged_matrix),
    )


def test_forged_matrix_h_direction_is_rejected_before_success_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _one_round_from_case(
        _hard_closure_case(), max_plan_evaluations=2
    )
    matrix = expected.matrix_plan
    assert len(matrix.h_edges) == 1
    edge = matrix.h_edges[0]
    forged_edges = (
        MatrixDirectedEdgeV21(
            source_row=edge.target_row,
            target_row=edge.source_row,
        ),
    )
    forged_matrix = replace(
        matrix,
        h_edges=forged_edges,
        matrix_root=_rebuilt_matrix_root(
            matrix,
            solver_mode=matrix.solver_mode,
            evaluation_count=matrix.evaluation_count,
            source_axes=matrix.source_axes,
            h_edges=forged_edges,
        ),
    )

    _assert_forged_matrix_is_rejected(
        monkeypatch,
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=rounds,
        forged=_synchronize_forged_matrix(expected, forged_matrix),
    )


def test_forged_matrix_in_width_sparse_cell_is_rejected_before_success_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _prepare_exact_rounds(1)
    matrix = expected[0].matrix_plan
    if matrix.f_width:
        forged_f_rows = (
            matrix.f_rows[1:]
            if matrix.f_rows
            else (SparseBinaryCellV21(row_index=0, column_index=0),)
        )
        forged_matrix = replace(
            matrix,
            f_rows=forged_f_rows,
            matrix_root=_rebuilt_matrix_root(
                matrix,
                solver_mode=matrix.solver_mode,
                evaluation_count=matrix.evaluation_count,
                source_axes=matrix.source_axes,
                f_rows=forged_f_rows,
            ),
        )
    else:
        forged_g_rows = (
            matrix.g_rows[1:]
            if matrix.g_rows
            else (SparseBinaryCellV21(row_index=0, column_index=0),)
        )
        forged_matrix = replace(
            matrix,
            g_rows=forged_g_rows,
            matrix_root=_rebuilt_matrix_root(
                matrix,
                solver_mode=matrix.solver_mode,
                evaluation_count=matrix.evaluation_count,
                source_axes=matrix.source_axes,
                g_rows=forged_g_rows,
            ),
        )

    _assert_forged_matrix_is_rejected(
        monkeypatch,
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=rounds,
        forged=_synchronize_forged_matrix(expected[0], forged_matrix),
    )


def test_forged_matrix_source_x_drop_is_rejected_before_success_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _prepare_exact_rounds(1)
    matrix = expected[0].matrix_plan
    source_row = next(
        row
        for row in matrix.x_rows
        if row.axis is MatrixDecisionAxisV21.SOURCE
        and row.record_outcome is ReencodingOutcomeV21.EXACT
        and row.row_index not in matrix.mandatory_rows
    )
    forged_x_rows = tuple(
        replace(
            row,
            record_outcome=ReencodingOutcomeV21.DROP,
            child_row=None,
        )
        if row == source_row
        else row
        for row in matrix.x_rows
    )
    forged_matrix = replace(
        matrix,
        x_rows=forged_x_rows,
        matrix_root=_rebuilt_matrix_root(
            matrix,
            solver_mode=matrix.solver_mode,
            evaluation_count=matrix.evaluation_count,
            source_axes=matrix.source_axes,
            x_rows=forged_x_rows,
        ),
    )

    _assert_forged_matrix_is_rejected(
        monkeypatch,
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=rounds,
        forged=_synchronize_forged_matrix(expected[0], forged_matrix),
    )


def test_forged_compact_child_axis_is_rejected_before_success_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, expected = _one_round_from_case(
        _deep_compact_case(), max_plan_evaluations=2
    )
    matrix = expected.matrix_plan
    assert len(matrix.child_axes) == 1
    child = matrix.child_axes[0]
    forged_children = (replace(child, child_id=_other_root(child.child_id)),)
    forged_matrix = replace(
        matrix,
        child_axes=forged_children,
        matrix_root=_rebuilt_matrix_root(
            matrix,
            solver_mode=matrix.solver_mode,
            evaluation_count=matrix.evaluation_count,
            source_axes=matrix.source_axes,
            child_axes=forged_children,
        ),
    )

    _assert_forged_matrix_is_rejected(
        monkeypatch,
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=rounds,
        forged=_synchronize_forged_matrix(expected, forged_matrix),
    )


def test_rootless_and_compact_paths_keep_only_compact_release_counts() -> None:
    rootless_state, rootless_head, rootless_rounds, rootless_expected = (
        _one_round_from_case(_rootless_segment_case(), max_plan_evaluations=2)
    )
    compact_state, compact_head, compact_rounds, compact_expected = (
        _one_round_from_case(_compact_segment_case(), max_plan_evaluations=2)
    )

    rootless = evaluate_evidenced_multiround_v21(
        rootless_state,
        rootless_head,
        rootless_rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(rootless_state),
        expected_initial_advance_head_root=rootless_head.head_root,
    )
    compact = evaluate_evidenced_multiround_v21(
        compact_state,
        compact_head,
        compact_rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(compact_state),
        expected_initial_advance_head_root=compact_head.head_root,
    )

    assert rootless.status is MultiroundStatusV21.COMPLETED
    assert rootless.traces[0].release_count == sum(
        len(advance.released_sources)
        for advance in rootless_expected.candidate.barrier_advances
    )
    assert compact.status is MultiroundStatusV21.COMPLETED
    assert compact.traces[0].release_count == sum(
        len(advance.released_sources)
        for advance in compact_expected.candidate.barrier_advances
    )
    assert rootless.traces[0].matrix_root == rootless_expected.matrix_plan.matrix_root
    assert compact.traces[0].matrix_root == compact_expected.matrix_plan.matrix_root


def test_twelve_chained_rounds_are_byte_identical_and_thirteen_preflights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, _expected = _prepare_exact_rounds(12)
    first = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )
    second = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )

    assert first == second
    assert canonical_json_v21(first) == canonical_json_v21(second)
    assert first.run_root == second.run_root
    assert first.status is MultiroundStatusV21.COMPLETED
    assert len(first.traces) == 12

    calls = 0

    def must_not_plan(
        *_args: object, **_kwargs: object
    ) -> EvidencedPlannedCandidateV21:
        nonlocal calls
        calls += 1
        raise AssertionError("over-cap input reached planner")

    monkeypatch.setattr(
        multiround_module, "plan_evidenced_reencoding_v21", must_not_plan
    )
    with pytest.raises(ValueError, match="12"):
        evaluate_evidenced_multiround_v21(
            initial_state,
            initial_head,
            rounds + (rounds[0],),
            expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
            expected_initial_advance_head_root=initial_head.head_root,
        )
    assert calls == 0


def test_zero_rounds_complete_without_retaining_state_or_head() -> None:
    initial_state, initial_head, _rounds, _expected = _prepare_exact_rounds(0)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        (),
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )

    assert result.status is MultiroundStatusV21.COMPLETED
    assert result.exit_reason is MultiroundExitReasonV21.COMPLETED
    assert result.traces == ()
    assert result.failed_round_index is None
    assert result.reason is None
    assert result.final_state_hash == evidenced_state_hash_v21(initial_state)
    assert result.final_advance_head_root == initial_head.head_root
    assert not any(
        field.name in {"state", "head", "candidate", "source_envelope", "policy"}
        for field in fields(EvidencedMultiroundResultV21)
    )
    assert not hasattr(result, "state")
    assert not hasattr(result, "advance_head")
    assert not any(
        field.name
        in {
            "state",
            "head",
            "candidate",
            "source_envelope",
            "policy",
            "released_sources",
        }
        for field in fields(MultiroundTraceV21)
    )


def test_input_must_be_exact_concrete_tuple_and_runtime_nested_mutation_revalidates() -> (
    None
):
    initial_state, initial_head, rounds, _expected = _prepare_exact_rounds(1)
    anchors = {
        "expected_initial_state_hash": evidenced_state_hash_v21(initial_state),
        "expected_initial_advance_head_root": initial_head.head_root,
    }
    with pytest.raises(TypeError, match="tuple"):
        evaluate_evidenced_multiround_v21(
            initial_state,
            initial_head,
            cast(tuple[RoundInputV21, ...], list(rounds)),
            **anchors,
        )
    with pytest.raises(TypeError):
        evaluate_evidenced_multiround_v21(
            initial_state,
            initial_head,
            cast(tuple[RoundInputV21, ...], (lambda: rounds,)),
            **anchors,
        )

    mutated_round = rounds[0]
    object.__setattr__(
        mutated_round.policy,
        "max_matrix_rows",
        mutated_round.policy.max_matrix_rows + 1,
    )
    with pytest.raises(ValueError, match="policy"):
        evaluate_evidenced_multiround_v21(
            initial_state, initial_head, (mutated_round,), **anchors
        )


def test_trace_and_run_root_tampering_fail_closed() -> None:
    initial_state, initial_head, rounds, _expected = _prepare_exact_rounds(1)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )
    with pytest.raises(ValueError, match="run root"):
        replace(result, run_root=_other_root(result.run_root))
    with pytest.raises(ValueError, match="trace"):
        replace(
            result,
            traces=(
                replace(
                    result.traces[0],
                    after_resident_bytes=result.traces[0].after_resident_bytes + 1,
                ),
            ),
        )

    object.__setattr__(result, "run_root", _other_root(result.run_root))
    with pytest.raises(ValueError, match="run root"):
        validate_evidenced_multiround_result_v21(result)


def test_nested_plan_mutation_is_revalidated_before_any_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, _expected = _prepare_exact_rounds(1)
    original = plan_evidenced_reencoding_v21

    def corrupt(
        base: EvidencedCapsuleStateV21,
        current_head: AdvanceChainHeadV21,
        source_envelope: SourceEnvelopeV21,
        policy: EvidencedReencodingPolicyV21,
        *,
        expected_base_state_hash: str,
        expected_source_envelope_root: str,
        expected_policy_root: str,
        expected_advance_head_root: str,
    ) -> EvidencedPlannedCandidateV21:
        planned = original(
            base,
            current_head,
            source_envelope,
            policy,
            expected_base_state_hash=expected_base_state_hash,
            expected_source_envelope_root=expected_source_envelope_root,
            expected_policy_root=expected_policy_root,
            expected_advance_head_root=expected_advance_head_root,
        )
        object.__setattr__(
            planned.matrix_plan,
            "matrix_root",
            _other_root(planned.matrix_plan.matrix_root),
        )
        return planned

    monkeypatch.setattr(multiround_module, "plan_evidenced_reencoding_v21", corrupt)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )

    assert result.status is MultiroundStatusV21.POSTCONDITION_REJECTED
    assert result.traces == ()
    assert result.failed_round_index == 0


def test_unexpected_planner_exception_propagates_without_success_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_state, initial_head, rounds, _expected = _prepare_exact_rounds(1)

    def boom(*_args: object, **_kwargs: object) -> EvidencedPlannedCandidateV21:
        raise RuntimeError("unexpected planner defect")

    monkeypatch.setattr(multiround_module, "plan_evidenced_reencoding_v21", boom)
    with pytest.raises(RuntimeError, match="unexpected planner defect"):
        evaluate_evidenced_multiround_v21(
            initial_state,
            initial_head,
            rounds,
            expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
            expected_initial_advance_head_root=initial_head.head_root,
        )


def test_compact_trace_records_exact_c_b_r_generation_highwater_chain() -> None:
    initial_state, initial_head, rounds, expected = _prepare_exact_rounds(3)
    result = evaluate_evidenced_multiround_v21(
        initial_state,
        initial_head,
        rounds,
        expected_initial_state_hash=evidenced_state_hash_v21(initial_state),
        expected_initial_advance_head_root=initial_head.head_root,
    )
    prior_state = initial_state
    prior_head = initial_head
    for trace, planned in zip(result.traces, expected, strict=True):
        before = evidenced_resident_layout_v21(prior_state)
        after = evidenced_resident_layout_v21(planned.verification.state)
        assert (
            trace.before_control_bytes,
            trace.before_source_body_bytes,
            trace.before_resident_bytes,
        ) == (before.control_bytes, before.source_body_bytes, before.resident_bytes)
        assert (
            trace.after_control_bytes,
            trace.after_source_body_bytes,
            trace.after_resident_bytes,
        ) == (after.control_bytes, after.source_body_bytes, after.resident_bytes)
        assert trace.resident_delta == after.resident_bytes - before.resident_bytes
        assert trace.loss_delta == planned.matrix_plan.objective.loss_units
        assert trace.before_generation == prior_state.state.frame.generation
        assert (
            trace.after_generation == planned.verification.state.state.frame.generation
        )
        assert trace.before_high_water == prior_state.state.frame.high_water
        assert (
            trace.after_high_water == planned.verification.state.state.frame.high_water
        )
        assert trace.before_advance_head_root == prior_head.head_root
        assert (
            trace.after_advance_head_root == planned.verification.advance_head.head_root
        )
        prior_state = planned.verification.state
        prior_head = planned.verification.advance_head
