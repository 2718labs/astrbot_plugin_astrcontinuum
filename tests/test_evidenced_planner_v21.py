"""RED-first public boundary checks for the V21 evidenced planner."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_evidenced_reencoder_v21 import (
    _compact_segment_case,
    _contribution,
    _envelope,
    _exact_only_case,
    _rootless_segment_case,
    _source,
)

import crm_experiment.evidenced_planner_v21 as planner_module
from crm_experiment.contracts_v21 import (
    CapsuleRoleV21,
    HardDependencyV21,
    folded_commitment_root_v21,
    segment_identity_v21,
    segment_input_commitment_root_v21,
    source_commitment_v21,
)
from crm_experiment.evidenced_planner_v21 import (
    EvidencedPlannedCandidateV21,
    EvidencedPlanningRejectedV21,
    plan_evidenced_reencoding_v21,
)
from crm_experiment.evidenced_reencoder_v21 import (
    EvidencedReencodingCandidateV21,
    EvidencedReencodingResultV21,
    EvidencedReencodingStatusV21,
)
from crm_experiment.evidenced_state_v21 import (
    EvidencedCapsuleStateV21,
    evidenced_state_hash_v21,
)
from crm_experiment.matrix_v21 import SparseBinaryCellV21
from crm_experiment.reencoding_contracts_v21 import (
    AdvanceChainHeadV21,
    EvidencedReencodingPolicyV21,
    HardGraphEdgeV21,
    ReencodingOutcomeV21,
    SegmentDispositionV21,
    SolverModeV21,
    evidenced_reencoding_policy_root_v21,
    seed_advance_chain_head_v21,
)


def test_planner_boundary_is_public() -> None:
    assert callable(plan_evidenced_reencoding_v21)


def _plan_from_case(
    case: tuple[
        EvidencedCapsuleStateV21,
        AdvanceChainHeadV21,
        EvidencedReencodingCandidateV21,
        str,
        str,
        str,
    ],
    *,
    max_plan_evaluations: int | None = None,
    policy_override: EvidencedReencodingPolicyV21 | None = None,
) -> EvidencedPlannedCandidateV21:
    base, current_head, fixture_candidate, base_hash, envelope_root, policy_root = case
    candidate = fixture_candidate
    if policy_override is not None:
        policy = policy_override
        policy_root = evidenced_reencoding_policy_root_v21(policy)
    elif max_plan_evaluations is not None:
        policy = replace(candidate.policy, max_plan_evaluations=max_plan_evaluations)
        policy_root = evidenced_reencoding_policy_root_v21(policy)
    else:
        policy = candidate.policy
    return plan_evidenced_reencoding_v21(
        base,
        current_head,
        candidate.source_envelope,
        policy,
        expected_base_state_hash=base_hash,
        expected_source_envelope_root=envelope_root,
        expected_policy_root=policy_root,
        expected_advance_head_root=current_head.head_root,
    )


def _budget_fold_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    base, _head, fixture, _base_hash, _envelope_root, _policy_root = _exact_only_case()
    updated_base = EvidencedCapsuleStateV21(
        state=replace(
            base.state,
            frame=replace(base.state.frame, accepted_budget=5_000),
        ),
        contribution_index=base.contribution_index,
    )
    base_hash = evidenced_state_hash_v21(updated_base)
    current_head = seed_advance_chain_head_v21(
        updated_base, expected_state_hash=base_hash
    )
    incoming_id = fixture.source_envelope.incoming_record_ids[0]
    sources = []
    for source in fixture.source_envelope.source_records:
        if source.record_id != incoming_id:
            sources.append(source)
            continue
        body = "x" * 3_072
        sources.append(
            replace(
                source,
                body=body,
                commitment=source_commitment_v21(
                    record_id=source.record_id,
                    namespace=source.namespace,
                    incarnation=source.incarnation,
                    as_of=source.as_of,
                    body=body,
                    core_required=source.core_required,
                    active=source.active,
                ),
            )
        )
    source_by_id = {source.record_id: source for source in sources}
    contributions = tuple(
        sorted(
            (
                replace(row, source_commitment=source_by_id[row.record_id].commitment)
                for row in fixture.source_envelope.contribution_evidence
            ),
            key=lambda row: row.record_id,
        )
    )
    envelope = _envelope(
        base_hash=base_hash,
        sources=tuple(sorted(sources, key=lambda row: row.record_id)),
        incoming_ids=(incoming_id,),
        contributions=contributions,
    )
    policy = replace(
        fixture.policy,
        max_plan_evaluations=2,
        max_new_segments=1,
        max_loss_units=3,
        per_role_loss_caps=(
            (CapsuleRoleV21.CONTEXT, 1),
            (CapsuleRoleV21.ROOT_GOAL, 0),
        ),
        segment_loss_units=3,
    )
    return (
        updated_base,
        current_head,
        replace(fixture, source_envelope=envelope, policy=policy),
        base_hash,
        envelope.envelope_root,
        evidenced_reencoding_policy_root_v21(policy),
    )


def _hard_closure_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    base, current_head, fixture, base_hash, _envelope_root, _policy_root = (
        _exact_only_case()
    )
    incoming_id = fixture.source_envelope.incoming_record_ids[0]
    source_by_id = {
        source.record_id: source for source in fixture.source_envelope.source_records
    }
    incoming = source_by_id[incoming_id]
    core = next(
        source
        for source in fixture.source_envelope.source_records
        if source.record_id != incoming_id
    )
    hard_dependency = HardDependencyV21(
        target_id=core.record_id, target_commitment=core.commitment
    )
    sources = tuple(
        sorted(
            (
                core,
                replace(incoming, hard_depends_on=(hard_dependency,)),
            ),
            key=lambda row: row.record_id,
        )
    )
    hard_edge = HardGraphEdgeV21(
        source_id=incoming.record_id,
        source_commitment=incoming.commitment,
        target_id=core.record_id,
        target_commitment=core.commitment,
    )
    envelope = _envelope(
        base_hash=base_hash,
        sources=sources,
        incoming_ids=(incoming_id,),
        contributions=fixture.source_envelope.contribution_evidence,
        hard_edges=(hard_edge,),
    )
    policy = replace(
        fixture.policy,
        max_hard_edges=1,
        max_new_segments=1,
        max_plan_evaluations=2,
    )
    return (
        base,
        current_head,
        replace(fixture, source_envelope=envelope, policy=policy),
        base_hash,
        envelope.envelope_root,
        evidenced_reencoding_policy_root_v21(policy),
    )


def _long_rootless_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    base, _head, fixture, _base_hash, _envelope_root, _policy_root = (
        _rootless_segment_case()
    )
    core = base.state.exact_kernel[0]
    previous_foldable = base.state.hot_cache[0]
    body = "x" * 3_072
    foldable = replace(
        previous_foldable,
        body=body,
        commitment=source_commitment_v21(
            record_id=previous_foldable.record_id,
            namespace=previous_foldable.namespace,
            incarnation=previous_foldable.incarnation,
            as_of=previous_foldable.as_of,
            body=body,
            core_required=previous_foldable.core_required,
            active=previous_foldable.active,
        ),
    )
    index = tuple(
        replace(locator, support_commitment=foldable.commitment)
        if locator.resident_id == previous_foldable.record_id
        else locator
        for locator in base.contribution_index
    )
    updated_base = EvidencedCapsuleStateV21(
        state=replace(base.state, hot_cache=(foldable,)), contribution_index=index
    )
    base_hash = evidenced_state_hash_v21(updated_base)
    current_head = seed_advance_chain_head_v21(
        updated_base, expected_state_hash=base_hash
    )
    sources = tuple(
        sorted((_source(core), _source(foldable)), key=lambda row: row.record_id)
    )
    key_by_record = {locator.resident_id: locator.contribution_key for locator in index}
    role_by_record = {
        row.record_id: row.role for row in fixture.source_envelope.contribution_evidence
    }
    contributions = tuple(
        sorted(
            (
                _contribution(
                    _source(core),
                    key_by_record[core.record_id],
                    role_by_record[core.record_id],
                ),
                _contribution(
                    _source(foldable),
                    key_by_record[foldable.record_id],
                    role_by_record[foldable.record_id],
                ),
            ),
            key=lambda row: row.record_id,
        )
    )
    envelope = _envelope(
        base_hash=base_hash,
        sources=sources,
        incoming_ids=(),
        contributions=contributions,
    )
    return (
        updated_base,
        current_head,
        replace(fixture, source_envelope=envelope),
        base_hash,
        envelope.envelope_root,
        evidenced_reencoding_policy_root_v21(fixture.policy),
    )


def _deep_compact_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    base, _head, fixture, _base_hash, _envelope_root, _policy_root = (
        _compact_segment_case()
    )
    parent = base.state.capsule_segments[0]
    ancestor_ids = tuple(
        sorted(
            (
                segment_identity_v21(parent.namespace, "planner-old-a"),
                segment_identity_v21(parent.namespace, "planner-old-b"),
            )
        )
    )
    deep_parent = replace(parent, parent_ids=ancestor_ids)
    updated_base = EvidencedCapsuleStateV21(
        state=replace(base.state, capsule_segments=(deep_parent,)),
        contribution_index=base.contribution_index,
    )
    base_hash = evidenced_state_hash_v21(updated_base)
    current_head = seed_advance_chain_head_v21(
        updated_base, expected_state_hash=base_hash
    )
    core = _source(updated_base.state.exact_kernel[0])
    envelope = _envelope(
        base_hash=base_hash,
        sources=(core,),
        incoming_ids=(),
        contributions=fixture.source_envelope.contribution_evidence,
    )
    return (
        updated_base,
        current_head,
        replace(fixture, source_envelope=envelope),
        base_hash,
        envelope.envelope_root,
        evidenced_reencoding_policy_root_v21(fixture.policy),
    )


def _two_parent_compaction_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    base, _head, fixture, _base_hash, _envelope_root, _policy_root = (
        _compact_segment_case()
    )
    parent = base.state.capsule_segments[0]
    peer_id = segment_identity_v21(parent.namespace, "planner-greedy-peer")
    peer_folded = folded_commitment_root_v21(
        segment_id=peer_id,
        namespace=parent.namespace,
        generation_start=parent.generation_start,
        generation_end=parent.generation_end,
        source_count=parent.source_count,
        role_counts=parent.role_counts,
        representatives=parent.representatives,
        coverage_vector=parent.coverage_vector,
        bridge_vector=parent.bridge_vector,
        loss_class=parent.loss_class,
        budget_used=parent.budget_used,
        fold_policy_hash=parent.fold_policy_hash,
    )
    peer = replace(
        parent,
        segment_id=peer_id,
        input_commitment_root=segment_input_commitment_root_v21(
            (),
            folded_commitment_root=peer_folded,
            generation_start=parent.generation_start,
            generation_end=parent.generation_end,
            fold_policy_hash=parent.fold_policy_hash,
        ),
        folded_commitment_root=peer_folded,
    )
    updated_base = EvidencedCapsuleStateV21(
        state=replace(
            base.state,
            capsule_segments=tuple(
                sorted((parent, peer), key=lambda segment: segment.segment_id)
            ),
        ),
        contribution_index=base.contribution_index,
    )
    base_hash = evidenced_state_hash_v21(updated_base)
    current_head = seed_advance_chain_head_v21(
        updated_base, expected_state_hash=base_hash
    )
    envelope = _envelope(
        base_hash=base_hash,
        sources=fixture.source_envelope.source_records,
        incoming_ids=fixture.source_envelope.incoming_record_ids,
        contributions=fixture.source_envelope.contribution_evidence,
    )
    policy = replace(
        fixture.policy,
        max_decisions=3,
        max_plan_evaluations=2,
        max_new_segments=1,
        solver_mode=SolverModeV21.DETERMINISTIC_GREEDY,
    )
    return (
        updated_base,
        current_head,
        replace(fixture, source_envelope=envelope, policy=policy),
        base_hash,
        envelope.envelope_root,
        evidenced_reencoding_policy_root_v21(policy),
    )


def test_exact_baseline_materializes_and_is_verified() -> None:
    planned = _plan_from_case(_exact_only_case())

    assert planned.verification.status is EvidencedReencodingStatusV21.VERIFIED
    assert all(
        row.outcome is ReencodingOutcomeV21.EXACT
        for row in planned.candidate.record_decisions
    )


def test_planner_emits_zero_implicit_sparse_f_g_cells() -> None:
    planned = _plan_from_case(_exact_only_case())
    matrix = planned.matrix_plan

    assert all(
        type(cell) is SparseBinaryCellV21
        and not hasattr(cell, "value")
        and not hasattr(cell, "values")
        for cell in matrix.f_rows + matrix.g_rows
    )
    assert len(matrix.f_rows) <= len(matrix.source_axes) * matrix.f_width
    assert len(matrix.g_rows) <= len(matrix.source_axes) * matrix.g_width


def test_rootless_segment_and_parent_compaction_materialize_verified_children() -> None:
    rootless = _plan_from_case(_long_rootless_case(), max_plan_evaluations=2)
    compact = _plan_from_case(_deep_compact_case(), max_plan_evaluations=2)

    assert rootless.verification.status is EvidencedReencodingStatusV21.VERIFIED
    assert any(
        row.outcome is ReencodingOutcomeV21.SEGMENT
        for row in rootless.candidate.record_decisions
    )
    assert compact.verification.status is EvidencedReencodingStatusV21.VERIFIED
    assert any(
        row.disposition is SegmentDispositionV21.COMPACT
        for row in compact.candidate.segment_decisions
    )


def test_exact_small_fails_closed_when_full_enumeration_exceeds_policy_cap() -> None:
    with pytest.raises(EvidencedPlanningRejectedV21, match="evaluation"):
        _plan_from_case(_rootless_segment_case())


def test_hard_edge_closure_is_mandatory_exact_and_never_folded() -> None:
    planned = _plan_from_case(_hard_closure_case())

    assert planned.verification.status is EvidencedReencodingStatusV21.VERIFIED
    assert planned.matrix_plan.mandatory_rows == (0, 1)
    assert all(
        row.outcome is ReencodingOutcomeV21.EXACT
        for row in planned.candidate.record_decisions
    )


def test_loss_and_new_segment_infeasibility_fail_closed_when_budget_needs_fold() -> (
    None
):
    case = _budget_fold_case()
    planned = _plan_from_case(case)
    assert any(
        row.outcome is ReencodingOutcomeV21.SEGMENT
        for row in planned.candidate.record_decisions
    )

    policy = case[2].policy
    with pytest.raises(EvidencedPlanningRejectedV21, match="feasible"):
        _plan_from_case(
            case,
            policy_override=replace(
                policy,
                max_loss_units=0,
                per_role_loss_caps=(
                    (CapsuleRoleV21.CONTEXT, 0),
                    (CapsuleRoleV21.ROOT_GOAL, 0),
                ),
            ),
        )
    with pytest.raises(EvidencedPlanningRejectedV21, match="feasible"):
        _plan_from_case(
            case,
            policy_override=replace(policy, max_new_segments=0),
        )


def test_greedy_and_repeated_plans_are_byte_identical() -> None:
    case = _long_rootless_case()
    policy = replace(
        case[2].policy,
        max_plan_evaluations=2,
        solver_mode=SolverModeV21.DETERMINISTIC_GREEDY,
    )
    first = _plan_from_case(case, policy_override=policy)
    second = _plan_from_case(case, policy_override=policy)

    assert any(
        row.outcome is ReencodingOutcomeV21.SEGMENT
        for row in first.candidate.record_decisions
    )
    assert first.matrix_plan == second.matrix_plan
    assert first.candidate.transition_root == second.candidate.transition_root


def test_greedy_fails_closed_when_a_full_parent_compaction_sweep_exceeds_cap() -> None:
    with pytest.raises(EvidencedPlanningRejectedV21, match="complete sweep"):
        _plan_from_case(_two_parent_compaction_case())


def test_oracle_refusal_returns_no_candidate_and_receives_all_original_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def refuse(
        base: EvidencedCapsuleStateV21,
        current_head: AdvanceChainHeadV21,
        _candidate: EvidencedReencodingCandidateV21,
        **anchors: object,
    ) -> EvidencedReencodingResultV21:
        calls.append(anchors)
        return EvidencedReencodingResultV21(
            state=base,
            advance_head=current_head,
            status=EvidencedReencodingStatusV21.ROLLED_BACK,
            consumed_delta=False,
            transition_root=None,
            reason="test oracle refusal",
        )

    monkeypatch.setattr(
        planner_module, "verify_evidenced_reencoding_candidate_v21", refuse
    )
    case = _exact_only_case()
    with pytest.raises(EvidencedPlanningRejectedV21, match="oracle refusal"):
        _plan_from_case(case)

    assert calls == [
        {
            "expected_base_state_hash": case[3],
            "expected_source_envelope_root": case[4],
            "expected_policy_root": case[5],
            "expected_advance_head_root": case[1].head_root,
        }
    ]


def test_stale_anchor_fails_closed_after_the_selected_candidate_reaches_oracle() -> (
    None
):
    case = _exact_only_case()
    base, current_head, candidate, base_hash, envelope_root, policy_root = case
    stale_base_hash = f"{base_hash.rsplit(':', maxsplit=1)[0]}:{'f' * 64}"

    with pytest.raises(EvidencedPlanningRejectedV21, match="V21 verifier rejected"):
        plan_evidenced_reencoding_v21(
            base,
            current_head,
            candidate.source_envelope,
            candidate.policy,
            expected_base_state_hash=stale_base_hash,
            expected_source_envelope_root=envelope_root,
            expected_policy_root=policy_root,
            expected_advance_head_root=current_head.head_root,
        )
