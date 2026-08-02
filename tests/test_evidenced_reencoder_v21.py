"""RED-first checks for the pure V21 evidenced reencoding verifier."""

from __future__ import annotations

from dataclasses import replace

import pytest

from crm_experiment.contracts_v21 import (
    CONTROL_FOLD_PROTOCOL_V21,
    CapsuleRoleV21,
    CapsuleSegmentV21,
    CapsuleStateV21,
    ControlFoldBoundsV21,
    ControlFrameV21,
    DictionaryEntryV21,
    ExactRecordV21,
    FoldBarrierV21,
    HardDependencyV21,
    LossClassV21,
    LossLedgerV21,
    SparseWeightPolicyV21,
    capacity_policy_hash_v21,
    dictionary_commitment_v21,
    dictionary_identity_v21,
    fold_barrier_root_v21,
    fold_policy_hash_v21,
    folded_commitment_root_v21,
    loss_ledger_root_v21,
    namespace_identity_v21,
    record_identity_v21,
    reencoding_policy_hash_v21,
    segment_identity_v21,
    segment_input_commitment_root_v21,
    source_commitment_v21,
)
from crm_experiment.evidenced_reencoder_v21 import (
    EvidencedReencodingCandidateV21,
    EvidencedReencodingResultV21,
    EvidencedReencodingStatusV21,
    verify_evidenced_reencoding_candidate_v21,
)
from crm_experiment.evidenced_state_v21 import (
    ContributionLocatorV21,
    ContributionSupportV21,
    EvidencedCapsuleStateV21,
    evidenced_state_hash_v21,
)
from crm_experiment.reencoding_contracts_v21 import (
    AdvanceChainHeadV21,
    AdvanceCursorKindV21,
    BarrierAdvanceCursorV21,
    BarrierAdvanceV21,
    EvidencedReencodingPolicyV21,
    FirstFoldAuthorizationV21,
    FirstFoldSourceV21,
    FoldContributionEvidenceV21,
    HardGraphEdgeV21,
    LossLedgerAdvanceV21,
    RecordDecisionV21,
    ReencodingOutcomeV21,
    ReleasedSourceV21,
    SegmentDecisionV21,
    SegmentDispositionV21,
    SolverModeV21,
    SourceEnvelopeV21,
    SourceGraphNodeV21,
    SourceRecordV21,
    advance_chain_head_root_v21,
    barrier_advance_root_v21,
    barrier_advance_seed_root_v21,
    barrier_durable_projection_root_v21,
    canonical_segment_hash_v21,
    contribution_root_v21,
    evidenced_reencoding_policy_root_v21,
    evidenced_transition_root_v21,
    first_fold_authorization_root_v21,
    hard_graph_root_v21,
    ledger_durable_projection_root_v21,
    loss_ledger_advance_root_v21,
    record_decision_root_v21,
    seed_advance_chain_head_v21,
    segment_decision_root_v21,
    source_envelope_root_v21,
)

_NAMESPACE = namespace_identity_v21("v21-evidenced-verifier-tests")
_MATRIX_ROOT_DOMAIN = "crm-v21-evidenced-matrix-s3/v1"


def _digest(domain: str, digit: str = "0") -> str:
    return f"{domain}:{digit * 64}"


def _record(
    label: str,
    *,
    as_of: int,
    core_required: bool,
) -> ExactRecordV21:
    record_id = record_identity_v21(_NAMESPACE, label)
    body = f"body for {label}"
    return ExactRecordV21(
        record_id=record_id,
        namespace=_NAMESPACE,
        incarnation=1,
        as_of=as_of,
        commitment=source_commitment_v21(
            record_id=record_id,
            namespace=_NAMESPACE,
            incarnation=1,
            as_of=as_of,
            body=body,
            core_required=core_required,
            active=True,
        ),
        body=body,
        core_required=core_required,
        active=True,
        hard_depends_on=(),
    )


def _source(record: ExactRecordV21) -> SourceRecordV21:
    return SourceRecordV21(
        record_id=record.record_id,
        namespace=record.namespace,
        incarnation=record.incarnation,
        as_of=record.as_of,
        commitment=record.commitment,
        body=record.body,
        core_required=record.core_required,
        active=record.active,
        hard_depends_on=record.hard_depends_on,
    )


def _entry(label: str) -> DictionaryEntryV21:
    key = dictionary_identity_v21(_NAMESPACE, label)
    return DictionaryEntryV21(
        namespace=_NAMESPACE,
        key=key,
        commitment=dictionary_commitment_v21(_NAMESPACE, key),
    )


def _contribution(
    source: SourceRecordV21,
    key: str,
    role: CapsuleRoleV21,
) -> FoldContributionEvidenceV21:
    return FoldContributionEvidenceV21(
        record_id=source.record_id,
        source_commitment=source.commitment,
        contribution_keys=(key,),
        role=role,
        coverage_vector=(1,),
        bridge_vector=(0,),
        representative_candidates=(),
    )


def _envelope(
    *,
    base_hash: str,
    sources: tuple[SourceRecordV21, ...],
    incoming_ids: tuple[str, ...],
    contributions: tuple[FoldContributionEvidenceV21, ...],
    hard_edges: tuple[HardGraphEdgeV21, ...] = (),
) -> SourceEnvelopeV21:
    graph_nodes = tuple(
        SourceGraphNodeV21(record_id=row.record_id, source_commitment=row.commitment)
        for row in sources
    )
    hard_root = hard_graph_root_v21(graph_nodes=graph_nodes, hard_edges=hard_edges)
    contribution_root = contribution_root_v21(contributions)
    envelope_root = source_envelope_root_v21(
        base_state_hash=base_hash,
        source_records=sources,
        incoming_record_ids=incoming_ids,
        graph_nodes=graph_nodes,
        hard_edges=hard_edges,
        contribution_evidence=contributions,
        hard_graph_root=hard_root,
        contribution_root=contribution_root,
    )
    return SourceEnvelopeV21(
        base_state_hash=base_hash,
        source_records=sources,
        incoming_record_ids=incoming_ids,
        graph_nodes=graph_nodes,
        hard_edges=hard_edges,
        contribution_evidence=contributions,
        hard_graph_root=hard_root,
        contribution_root=contribution_root,
        envelope_root=envelope_root,
    )


def _exact_only_case(
    *,
    incoming_as_of: int = 11,
) -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    bounds = ControlFoldBoundsV21(
        max_exact_kernel_records=4,
        max_exact_kernel_bytes=4_096,
        max_hot_records=4,
        max_hot_bytes=4_096,
        max_hot_frontier_entries=4,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=8,
        max_dictionary_bytes=2_048,
        max_segments=4,
        max_representatives_per_segment=4,
        coverage_vector_size=2,
        bridge_vector_size=1,
        max_barriers=4,
        max_sparse_overrides=4,
        max_direct_parent_ids=2,
        max_parent_id_bytes=512,
        loss_ledger_bytes=512,
    )
    frame_policy = reencoding_policy_hash_v21("v21-evidenced-verifier-policy")
    frame = ControlFrameV21(
        protocol_id=CONTROL_FOLD_PROTOCOL_V21,
        schema_version=3,
        generation=3,
        high_water=10,
        accepted_budget=16_384,
        reencoding_policy_hash=frame_policy,
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )
    core = _record("core", as_of=10, core_required=True)
    incoming = _record("incoming", as_of=incoming_as_of, core_required=False)
    core_entry = _entry("core-key")
    incoming_entry = _entry("incoming-key")
    dictionary = tuple(sorted((core_entry, incoming_entry), key=lambda row: row.key))
    base_state = CapsuleStateV21(
        frame=frame,
        bounds=bounds,
        exact_kernel=(core,),
        hot_cache=(),
        hot_frontier=(),
        dictionary=dictionary,
        capsule_segments=(),
        fold_barriers=(),
        sparse_weight_policy=SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=LossLedgerV21(
            role_counts=(),
            cumulative_loss_root=loss_ledger_root_v21((), 0),
            last_fold_generation=0,
        ),
    )
    base = EvidencedCapsuleStateV21(
        state=base_state,
        contribution_index=(
            ContributionLocatorV21(
                contribution_key=core_entry.key,
                support=ContributionSupportV21.EXACT,
                resident_id=core.record_id,
                support_commitment=core.commitment,
            ),
        ),
    )
    base_hash = evidenced_state_hash_v21(base)
    current_head = seed_advance_chain_head_v21(base, expected_state_hash=base_hash)
    core_source = _source(core)
    incoming_source = _source(incoming)
    sources = tuple(
        sorted((core_source, incoming_source), key=lambda row: row.record_id)
    )
    contributions = tuple(
        sorted(
            (
                _contribution(core_source, core_entry.key, CapsuleRoleV21.ROOT_GOAL),
                _contribution(
                    incoming_source,
                    incoming_entry.key,
                    CapsuleRoleV21.CONTEXT,
                ),
            ),
            key=lambda row: row.record_id,
        )
    )
    envelope = _envelope(
        base_hash=base_hash,
        sources=sources,
        incoming_ids=(incoming.record_id,),
        contributions=contributions,
    )
    policy = EvidencedReencodingPolicyV21(
        frame_policy_hash=frame_policy,
        max_matrix_rows=2,
        max_hard_edges=0,
        max_decisions=2,
        max_plan_evaluations=1,
        max_new_segments=0,
        max_loss_units=0,
        per_role_loss_caps=(
            (CapsuleRoleV21.CONTEXT, 0),
            (CapsuleRoleV21.ROOT_GOAL, 0),
        ),
        segment_loss_units=0,
        drop_loss_units=0,
        compaction_loss_units=0,
        solver_mode=SolverModeV21.EXACT_SMALL,
    )
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    record_decisions = tuple(
        sorted(
            (
                RecordDecisionV21(
                    record_id=source.record_id,
                    source_commitment=source.commitment,
                    outcome=ReencodingOutcomeV21.EXACT,
                    child_segment_id=None,
                    contribution_keys=next(
                        row.contribution_keys
                        for row in contributions
                        if row.record_id == source.record_id
                    ),
                )
                for source in sources
            ),
            key=lambda row: (row.record_id, row.source_commitment),
        )
    )
    record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=record_decisions,
    )
    segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=(),
    )
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=(),
        new_role_counts=(),
        previous_generation=0,
        new_generation=4,
        loss_units_delta=0,
    )
    new_ledger_root = ledger_durable_projection_root_v21(
        previous_durable_root=current_head.ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=(),
        new_generation=4,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=(),
        new_role_counts=(),
        previous_generation=0,
        new_generation=4,
        loss_units_delta=0,
        new_durable_root=new_ledger_root,
        new_root=ledger_root,
    )
    proposed_frame = replace(frame, generation=4, high_water=11)
    proposed = EvidencedCapsuleStateV21(
        state=replace(
            base_state,
            frame=proposed_frame,
            hot_cache=(incoming,),
            loss_ledger=LossLedgerV21(
                role_counts=(),
                cumulative_loss_root=new_ledger_root,
                last_fold_generation=4,
            ),
        ),
        contribution_index=tuple(
            sorted(
                (
                    ContributionLocatorV21(
                        contribution_key=core_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=core.record_id,
                        support_commitment=core.commitment,
                    ),
                    ContributionLocatorV21(
                        contribution_key=incoming_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=incoming.record_id,
                        support_commitment=incoming.commitment,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    matrix_root = _digest(_MATRIX_ROOT_DOMAIN, "a")
    transition_root = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=11,
    )
    candidate = EvidencedReencodingCandidateV21(
        proposed_state=proposed,
        source_envelope=envelope,
        policy=policy,
        record_decisions=record_decisions,
        segment_decisions=(),
        first_fold_authorizations=(),
        barrier_advances=(),
        ledger_advance=ledger_advance,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=11,
        transition_root=transition_root,
    )
    return (
        base,
        current_head,
        candidate,
        base_hash,
        envelope.envelope_root,
        policy_root,
    )


def test_exact_only_candidate_verifies_and_advances_the_head() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case()
    )

    result = verify_evidenced_reencoding_candidate_v21(
        base,
        current_head,
        candidate,
        expected_base_state_hash=base_hash,
        expected_source_envelope_root=envelope_root,
        expected_policy_root=policy_root,
        expected_advance_head_root=current_head.head_root,
    )

    assert result.status is EvidencedReencodingStatusV21.VERIFIED
    assert result.state is candidate.proposed_state
    assert result.advance_head.previous_head_root == current_head.head_root
    assert result.consumed_delta is True
    assert result.transition_root == candidate.transition_root
    assert result.reason is None


def _verify(
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    candidate: EvidencedReencodingCandidateV21,
    base_hash: str,
    envelope_root: str,
    policy_root: str,
    *,
    expected_base_state_hash: str | None = None,
    expected_source_envelope_root: str | None = None,
    expected_policy_root: str | None = None,
    expected_advance_head_root: str | None = None,
) -> EvidencedReencodingResultV21:
    return verify_evidenced_reencoding_candidate_v21(
        base,
        current_head,
        candidate,
        expected_base_state_hash=expected_base_state_hash or base_hash,
        expected_source_envelope_root=expected_source_envelope_root or envelope_root,
        expected_policy_root=expected_policy_root or policy_root,
        expected_advance_head_root=expected_advance_head_root or current_head.head_root,
    )


def _assert_failure(
    result: EvidencedReencodingResultV21,
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    status: EvidencedReencodingStatusV21,
) -> None:
    assert result.status is status
    assert result.state is base
    assert result.advance_head is current_head
    assert result.consumed_delta is False
    assert result.transition_root is None
    assert result.reason


@pytest.mark.parametrize(
    ("anchor", "replacement"),
    (
        ("base", _digest("crm-v21-evidenced-state-s3/v1", "d")),
        ("envelope", _digest("crm-v21-source-envelope-s3/v1", "d")),
        ("policy", _digest("crm-v21-evidenced-policy-s3/v1", "d")),
        ("head", _digest("crm-v21-advance-head-s3/v1", "d")),
    ),
)
def test_each_external_anchor_rejects_stale_without_consuming_identity(
    anchor: str,
    replacement: str,
) -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case()
    )
    result = _verify(
        base,
        current_head,
        candidate,
        base_hash,
        envelope_root,
        policy_root,
        expected_base_state_hash=replacement if anchor == "base" else None,
        expected_source_envelope_root=replacement if anchor == "envelope" else None,
        expected_policy_root=replacement if anchor == "policy" else None,
        expected_advance_head_root=replacement if anchor == "head" else None,
    )

    _assert_failure(
        result,
        base,
        current_head,
        EvidencedReencodingStatusV21.STALE_REJECTED,
    )


def test_source_cap_and_current_head_correspondence_preserve_input_identity() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case()
    )
    constrained_policy = replace(candidate.policy, max_matrix_rows=1)
    cap_rejection = _verify(
        base,
        current_head,
        replace(candidate, policy=constrained_policy),
        base_hash,
        envelope_root,
        policy_root,
        expected_policy_root=evidenced_reencoding_policy_root_v21(constrained_policy),
    )
    _assert_failure(
        cap_rejection,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )

    wrong_state_hash = _digest("crm-v21-evidenced-state-s3/v1", "c")
    wrong_head = AdvanceChainHeadV21(
        state_hash=wrong_state_hash,
        generation=current_head.generation,
        barrier_cursors=current_head.barrier_cursors,
        ledger_cursor=current_head.ledger_cursor,
        previous_head_root=current_head.head_root,
        head_root=advance_chain_head_root_v21(
            state_hash=wrong_state_hash,
            generation=current_head.generation,
            barrier_cursors=current_head.barrier_cursors,
            ledger_cursor=current_head.ledger_cursor,
            previous_head_root=current_head.head_root,
        ),
    )
    stale_head = _verify(
        base,
        wrong_head,
        candidate,
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        stale_head,
        base,
        wrong_head,
        EvidencedReencodingStatusV21.STALE_REJECTED,
    )


def test_stale_incoming_and_missing_decision_roll_back_to_input_identity() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case(incoming_as_of=10)
    )
    stale = _verify(
        base,
        current_head,
        candidate,
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        stale,
        base,
        current_head,
        EvidencedReencodingStatusV21.STALE_REJECTED,
    )

    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case()
    )
    missing = _verify(
        base,
        current_head,
        replace(candidate, record_decisions=candidate.record_decisions[:-1]),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        missing,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def test_source_universe_and_hard_edge_closure_roll_back() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case()
    )
    core_id = base.state.exact_kernel[0].record_id
    incoming_id = candidate.source_envelope.incoming_record_ids[0]
    omitted_core = _envelope(
        base_hash=base_hash,
        sources=tuple(
            row
            for row in candidate.source_envelope.source_records
            if row.record_id != core_id
        ),
        incoming_ids=(incoming_id,),
        contributions=tuple(
            row
            for row in candidate.source_envelope.contribution_evidence
            if row.record_id != core_id
        ),
    )
    missing_resident = _verify(
        base,
        current_head,
        replace(candidate, source_envelope=omitted_core),
        base_hash,
        envelope_root,
        policy_root,
        expected_source_envelope_root=omitted_core.envelope_root,
    )
    _assert_failure(
        missing_resident,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )

    source_by_id = {
        row.record_id: row for row in candidate.source_envelope.source_records
    }
    dependent_incoming = replace(
        source_by_id[incoming_id],
        hard_depends_on=(
            HardDependencyV21(
                target_id=core_id,
                target_commitment=source_by_id[core_id].commitment,
            ),
        ),
    )
    hard_sources = tuple(
        sorted(
            (
                dependent_incoming if row.record_id == incoming_id else row
                for row in candidate.source_envelope.source_records
            ),
            key=lambda row: row.record_id,
        )
    )
    hard_edge = HardGraphEdgeV21(
        source_id=incoming_id,
        source_commitment=dependent_incoming.commitment,
        target_id=core_id,
        target_commitment=source_by_id[core_id].commitment,
    )
    hard_envelope = _envelope(
        base_hash=base_hash,
        sources=hard_sources,
        incoming_ids=candidate.source_envelope.incoming_record_ids,
        contributions=candidate.source_envelope.contribution_evidence,
        hard_edges=(hard_edge,),
    )
    hard_policy = replace(candidate.policy, max_hard_edges=1)
    hard_policy_root = evidenced_reencoding_policy_root_v21(hard_policy)
    dropped_target = replace(
        next(row for row in candidate.record_decisions if row.record_id == incoming_id),
        outcome=ReencodingOutcomeV21.DROP,
    )
    hard_rows = tuple(
        sorted(
            (
                dropped_target if row.record_id == incoming_id else row
                for row in candidate.record_decisions
            ),
            key=lambda row: (row.record_id, row.source_commitment),
        )
    )
    hard_record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=hard_envelope.envelope_root,
        policy_root=hard_policy_root,
        rows=hard_rows,
    )
    hard_segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=hard_envelope.envelope_root,
        policy_root=hard_policy_root,
        rows=(),
    )
    hard_closure = _verify(
        base,
        current_head,
        replace(
            candidate,
            source_envelope=hard_envelope,
            policy=hard_policy,
            record_decisions=hard_rows,
            record_decision_root=hard_record_root,
            segment_decision_root=hard_segment_root,
        ),
        base_hash,
        envelope_root,
        policy_root,
        expected_source_envelope_root=hard_envelope.envelope_root,
        expected_policy_root=hard_policy_root,
    )
    _assert_failure(
        hard_closure,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def test_exact_locator_repoint_and_final_hash_tampering_roll_back() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case()
    )
    core_locator, incoming_locator = candidate.proposed_state.contribution_index
    repointed = replace(
        candidate.proposed_state,
        contribution_index=tuple(
            sorted(
                (
                    core_locator,
                    replace(
                        incoming_locator,
                        resident_id=core_locator.resident_id,
                        support_commitment=core_locator.support_commitment,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    repoint = _verify(
        base,
        current_head,
        replace(candidate, proposed_state=repointed),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        repoint,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )

    bad_hash = _verify(
        base,
        current_head,
        replace(candidate, proposed_hash=_digest("crm-v21-evidenced-state-s3/v1", "e")),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        bad_hash,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )

    bad_transition = _verify(
        base,
        current_head,
        replace(
            candidate,
            transition_root=_digest("crm-v21-evidenced-transition-s3/v1", "e"),
        ),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        bad_transition,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def test_duplicate_and_core_nonexact_decisions_roll_back() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _exact_only_case()
    )
    duplicate = _verify(
        base,
        current_head,
        replace(
            candidate,
            record_decisions=candidate.record_decisions
            + (candidate.record_decisions[0],),
        ),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        duplicate,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )

    core_id = base.state.exact_kernel[0].record_id
    core_decision = next(
        row for row in candidate.record_decisions if row.record_id == core_id
    )
    nonexact_core = replace(
        core_decision,
        outcome=ReencodingOutcomeV21.SEGMENT,
        child_segment_id=segment_identity_v21(_NAMESPACE, "forbidden-core-child"),
    )
    rows = tuple(
        sorted(
            (
                nonexact_core if row.record_id == core_id else row
                for row in candidate.record_decisions
            ),
            key=lambda row: (row.record_id, row.source_commitment),
        )
    )
    bad_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        rows=rows,
    )
    bad_closure = _verify(
        base,
        current_head,
        replace(candidate, record_decisions=rows, record_decision_root=bad_root),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        bad_closure,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def _rootless_segment_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    bounds = ControlFoldBoundsV21(
        max_exact_kernel_records=4,
        max_exact_kernel_bytes=4_096,
        max_hot_records=4,
        max_hot_bytes=4_096,
        max_hot_frontier_entries=4,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=8,
        max_dictionary_bytes=2_048,
        max_segments=4,
        max_representatives_per_segment=4,
        coverage_vector_size=2,
        bridge_vector_size=1,
        max_barriers=4,
        max_sparse_overrides=4,
        max_direct_parent_ids=2,
        max_parent_id_bytes=512,
        loss_ledger_bytes=512,
    )
    frame_policy = reencoding_policy_hash_v21("v21-rootless-policy")
    frame = ControlFrameV21(
        protocol_id=CONTROL_FOLD_PROTOCOL_V21,
        schema_version=3,
        generation=3,
        high_water=10,
        accepted_budget=16_384,
        reencoding_policy_hash=frame_policy,
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )
    core = _record("rootless-core", as_of=10, core_required=True)
    foldable = _record("rootless-foldable", as_of=10, core_required=False)
    core_entry = _entry("rootless-core-key")
    foldable_entry = _entry("rootless-foldable-key")
    dictionary = tuple(sorted((core_entry, foldable_entry), key=lambda row: row.key))
    base_state = CapsuleStateV21(
        frame=frame,
        bounds=bounds,
        exact_kernel=(core,),
        hot_cache=(foldable,),
        hot_frontier=(),
        dictionary=dictionary,
        capsule_segments=(),
        fold_barriers=(),
        sparse_weight_policy=SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=LossLedgerV21(
            role_counts=(),
            cumulative_loss_root=loss_ledger_root_v21((), 0),
            last_fold_generation=0,
        ),
    )
    base = EvidencedCapsuleStateV21(
        state=base_state,
        contribution_index=tuple(
            sorted(
                (
                    ContributionLocatorV21(
                        contribution_key=core_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=core.record_id,
                        support_commitment=core.commitment,
                    ),
                    ContributionLocatorV21(
                        contribution_key=foldable_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=foldable.record_id,
                        support_commitment=foldable.commitment,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    base_hash = evidenced_state_hash_v21(base)
    current_head = seed_advance_chain_head_v21(base, expected_state_hash=base_hash)
    core_source = _source(core)
    foldable_source = _source(foldable)
    sources = tuple(
        sorted((core_source, foldable_source), key=lambda row: row.record_id)
    )
    contributions = tuple(
        sorted(
            (
                _contribution(core_source, core_entry.key, CapsuleRoleV21.ROOT_GOAL),
                _contribution(
                    foldable_source,
                    foldable_entry.key,
                    CapsuleRoleV21.CONTEXT,
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
    policy = EvidencedReencodingPolicyV21(
        frame_policy_hash=frame_policy,
        max_matrix_rows=2,
        max_hard_edges=0,
        max_decisions=2,
        max_plan_evaluations=1,
        max_new_segments=1,
        max_loss_units=3,
        per_role_loss_caps=(
            (CapsuleRoleV21.CONTEXT, 1),
            (CapsuleRoleV21.ROOT_GOAL, 0),
        ),
        segment_loss_units=3,
        drop_loss_units=0,
        compaction_loss_units=0,
        solver_mode=SolverModeV21.EXACT_SMALL,
    )
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    child_id = segment_identity_v21(_NAMESPACE, "rootless-child")
    child_policy = fold_policy_hash_v21("rootless-child-policy")
    child_roles = ((CapsuleRoleV21.CONTEXT, 1),)
    child_folded = folded_commitment_root_v21(
        segment_id=child_id,
        namespace=_NAMESPACE,
        generation_start=4,
        generation_end=4,
        source_count=1,
        role_counts=child_roles,
        representatives=(),
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=1,
        fold_policy_hash=child_policy,
    )
    child = CapsuleSegmentV21(
        segment_id=child_id,
        parent_ids=(),
        input_commitment_root=segment_input_commitment_root_v21(
            (),
            folded_commitment_root=child_folded,
            generation_start=4,
            generation_end=4,
            fold_policy_hash=child_policy,
        ),
        folded_commitment_root=child_folded,
        namespace=_NAMESPACE,
        generation_start=4,
        generation_end=4,
        source_count=1,
        role_counts=child_roles,
        representatives=(),
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=1,
        fold_policy_hash=child_policy,
    )
    record_decisions = tuple(
        sorted(
            (
                RecordDecisionV21(
                    record_id=core_source.record_id,
                    source_commitment=core_source.commitment,
                    outcome=ReencodingOutcomeV21.EXACT,
                    child_segment_id=None,
                    contribution_keys=(core_entry.key,),
                ),
                RecordDecisionV21(
                    record_id=foldable_source.record_id,
                    source_commitment=foldable_source.commitment,
                    outcome=ReencodingOutcomeV21.SEGMENT,
                    child_segment_id=child.segment_id,
                    contribution_keys=(foldable_entry.key,),
                ),
            ),
            key=lambda row: (row.record_id, row.source_commitment),
        )
    )
    record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=record_decisions,
    )
    segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=(),
    )
    authorization_sources = (
        FirstFoldSourceV21(
            record_id=foldable_source.record_id,
            source_commitment=foldable_source.commitment,
            contribution_keys=(foldable_entry.key,),
        ),
    )
    authorization_root = first_fold_authorization_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        namespace=_NAMESPACE,
        incarnation=1,
        generation=4,
        child_segment_id=child.segment_id,
        sources=authorization_sources,
    )
    authorization = FirstFoldAuthorizationV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        namespace=_NAMESPACE,
        incarnation=1,
        generation=4,
        child_segment_id=child.segment_id,
        sources=authorization_sources,
        authorization_root=authorization_root,
    )
    first_fold_cursor = BarrierAdvanceCursorV21(
        namespace=_NAMESPACE,
        incarnation=1,
        durable_root=None,
        high_water=None,
        kind=AdvanceCursorKindV21.SEED,
        transient_root=barrier_advance_seed_root_v21(
            base_state_hash=base_hash,
            prior_advance_head_root=current_head.head_root,
            namespace=_NAMESPACE,
            incarnation=1,
            durable_root=None,
            high_water=None,
        ),
    )
    released = (
        ReleasedSourceV21(
            record_id=foldable_source.record_id,
            source_commitment=foldable_source.commitment,
        ),
    )
    barrier_root = barrier_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=first_fold_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=0,
        new_high_water=10,
        generation=4,
        released_sources=released,
    )
    durable_barrier_root = barrier_durable_projection_root_v21(
        previous_durable_root=None,
        barrier_advance_root=barrier_root,
        namespace=_NAMESPACE,
        incarnation=1,
        new_high_water=10,
        generation=4,
    )
    barrier_advance = BarrierAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=first_fold_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=0,
        new_high_water=10,
        generation=4,
        released_sources=released,
        new_durable_root=durable_barrier_root,
        new_root=barrier_root,
    )
    role_delta = ((CapsuleRoleV21.CONTEXT, 1),)
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_root,),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=0,
        new_generation=4,
        loss_units_delta=3,
    )
    durable_ledger_root = ledger_durable_projection_root_v21(
        previous_durable_root=current_head.ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=role_delta,
        new_generation=4,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_root,),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=0,
        new_generation=4,
        loss_units_delta=3,
        new_durable_root=durable_ledger_root,
        new_root=ledger_root,
    )
    proposed = EvidencedCapsuleStateV21(
        state=replace(
            base_state,
            frame=replace(frame, generation=4),
            hot_cache=(),
            capsule_segments=(child,),
            fold_barriers=(
                FoldBarrierV21(
                    namespace=_NAMESPACE,
                    incarnation=1,
                    folded_through_high_water=10,
                    cumulative_root=durable_barrier_root,
                ),
            ),
            loss_ledger=LossLedgerV21(
                role_counts=role_delta,
                cumulative_loss_root=durable_ledger_root,
                last_fold_generation=4,
            ),
        ),
        contribution_index=tuple(
            sorted(
                (
                    ContributionLocatorV21(
                        contribution_key=core_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=core.record_id,
                        support_commitment=core.commitment,
                    ),
                    ContributionLocatorV21(
                        contribution_key=foldable_entry.key,
                        support=ContributionSupportV21.SEGMENT,
                        resident_id=child.segment_id,
                        support_commitment=child.folded_commitment_root,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    matrix_root = _digest(_MATRIX_ROOT_DOMAIN, "b")
    transition_root = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(authorization_root,),
        barrier_roots=(barrier_root,),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=10,
    )
    candidate = EvidencedReencodingCandidateV21(
        proposed_state=proposed,
        source_envelope=envelope,
        policy=policy,
        record_decisions=record_decisions,
        segment_decisions=(),
        first_fold_authorizations=(authorization,),
        barrier_advances=(barrier_advance,),
        ledger_advance=ledger_advance,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(authorization_root,),
        barrier_roots=(barrier_root,),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=10,
        transition_root=transition_root,
    )
    return (
        base,
        current_head,
        candidate,
        base_hash,
        envelope.envelope_root,
        policy_root,
    )


def test_rootless_segment_uses_a_same_transition_seed_and_authorization() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _rootless_segment_case()
    )

    result = verify_evidenced_reencoding_candidate_v21(
        base,
        current_head,
        candidate,
        expected_base_state_hash=base_hash,
        expected_source_envelope_root=envelope_root,
        expected_policy_root=policy_root,
        expected_advance_head_root=current_head.head_root,
    )

    assert result.status is EvidencedReencodingStatusV21.VERIFIED
    assert result.state is candidate.proposed_state
    assert result.advance_head.previous_head_root == current_head.head_root
    assert result.advance_head.barrier_cursors[0].kind is AdvanceCursorKindV21.ADVANCE


def test_rootless_authorization_and_barrier_ledger_tampering_roll_back() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _rootless_segment_case()
    )
    missing_auth = _verify(
        base,
        current_head,
        replace(candidate, first_fold_authorizations=()),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        missing_auth,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )

    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _rootless_segment_case()
    )
    bad_barrier = replace(candidate.barrier_advances[0])
    object.__setattr__(bad_barrier, "released_sources", ())
    bad_release = _verify(
        base,
        current_head,
        replace(candidate, barrier_advances=(bad_barrier,)),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        bad_release,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )

    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _rootless_segment_case()
    )
    bad_ledger = replace(candidate.ledger_advance)
    object.__setattr__(
        bad_ledger,
        "new_durable_root",
        _digest("v21-ledger-root-s3", "e"),
    )
    bad_projection = _verify(
        base,
        current_head,
        replace(candidate, ledger_advance=bad_ledger),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        bad_projection,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def _retained_segment_escape_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    original_base, _ignored_head, compacted, _ignored_hash, _ignored_root, _policy = (
        _compact_segment_case()
    )
    core = original_base.state.exact_kernel[0]
    retained = original_base.state.capsule_segments[0]
    core_locator = next(
        row
        for row in original_base.contribution_index
        if row.support is ContributionSupportV21.EXACT
    )
    retained_locator = next(
        row
        for row in original_base.contribution_index
        if row.support is ContributionSupportV21.SEGMENT
    )
    incoming = _record("retained-segment-escape", as_of=11, core_required=False)
    incoming_entry = _entry("retained-segment-escape-key")
    base_state = replace(
        original_base.state,
        dictionary=tuple(
            sorted(
                (*original_base.state.dictionary, incoming_entry),
                key=lambda row: row.key,
            )
        ),
    )
    base = EvidencedCapsuleStateV21(
        state=base_state,
        contribution_index=original_base.contribution_index,
    )
    base_hash = evidenced_state_hash_v21(base)
    current_head = seed_advance_chain_head_v21(base, expected_state_hash=base_hash)
    core_source = _source(core)
    incoming_source = _source(incoming)
    sources = tuple(
        sorted((core_source, incoming_source), key=lambda row: row.record_id)
    )
    contributions = tuple(
        sorted(
            (
                _contribution(
                    core_source,
                    core_locator.contribution_key,
                    CapsuleRoleV21.ROOT_GOAL,
                ),
                _contribution(
                    incoming_source,
                    incoming_entry.key,
                    CapsuleRoleV21.CONTEXT,
                ),
            ),
            key=lambda row: row.record_id,
        )
    )
    envelope = _envelope(
        base_hash=base_hash,
        sources=sources,
        incoming_ids=(incoming.record_id,),
        contributions=contributions,
    )
    policy = replace(
        compacted.policy,
        max_matrix_rows=2,
        max_decisions=3,
        max_new_segments=0,
        max_loss_units=2,
        segment_loss_units=2,
    )
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    record_decisions = tuple(
        sorted(
            (
                RecordDecisionV21(
                    record_id=core_source.record_id,
                    source_commitment=core_source.commitment,
                    outcome=ReencodingOutcomeV21.EXACT,
                    child_segment_id=None,
                    contribution_keys=(core_locator.contribution_key,),
                ),
                RecordDecisionV21(
                    record_id=incoming_source.record_id,
                    source_commitment=incoming_source.commitment,
                    outcome=ReencodingOutcomeV21.SEGMENT,
                    child_segment_id=retained.segment_id,
                    contribution_keys=(incoming_entry.key,),
                ),
            ),
            key=lambda row: (row.record_id, row.source_commitment),
        )
    )
    segment_decisions = (
        SegmentDecisionV21(
            segment_id=retained.segment_id,
            canonical_segment_hash=canonical_segment_hash_v21(
                base_state, retained.segment_id
            ),
            disposition=SegmentDispositionV21.RETAIN,
            child_segment_id=None,
            contribution_keys=(retained_locator.contribution_key,),
        ),
    )
    record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=record_decisions,
    )
    segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=segment_decisions,
    )
    prior_cursor = BarrierAdvanceCursorV21(
        namespace=_NAMESPACE,
        incarnation=1,
        durable_root=None,
        high_water=None,
        kind=AdvanceCursorKindV21.SEED,
        transient_root=barrier_advance_seed_root_v21(
            base_state_hash=base_hash,
            prior_advance_head_root=current_head.head_root,
            namespace=_NAMESPACE,
            incarnation=1,
            durable_root=None,
            high_water=None,
        ),
    )
    released = (
        ReleasedSourceV21(
            record_id=incoming_source.record_id,
            source_commitment=incoming_source.commitment,
        ),
    )
    barrier_root = barrier_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=prior_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=0,
        new_high_water=11,
        generation=4,
        released_sources=released,
    )
    durable_barrier_root = barrier_durable_projection_root_v21(
        previous_durable_root=None,
        barrier_advance_root=barrier_root,
        namespace=_NAMESPACE,
        incarnation=1,
        new_high_water=11,
        generation=4,
    )
    barrier_advance = BarrierAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=prior_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=0,
        new_high_water=11,
        generation=4,
        released_sources=released,
        new_durable_root=durable_barrier_root,
        new_root=barrier_root,
    )
    role_delta = ((CapsuleRoleV21.CONTEXT, 1),)
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_root,),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=base_state.loss_ledger.role_counts,
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=base_state.loss_ledger.last_fold_generation,
        new_generation=4,
        loss_units_delta=2,
    )
    durable_ledger_root = ledger_durable_projection_root_v21(
        previous_durable_root=current_head.ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=role_delta,
        new_generation=4,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_root,),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=base_state.loss_ledger.role_counts,
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=base_state.loss_ledger.last_fold_generation,
        new_generation=4,
        loss_units_delta=2,
        new_durable_root=durable_ledger_root,
        new_root=ledger_root,
    )
    proposed = EvidencedCapsuleStateV21(
        state=replace(
            base_state,
            frame=replace(base_state.frame, generation=4, high_water=11),
            fold_barriers=(
                FoldBarrierV21(
                    namespace=_NAMESPACE,
                    incarnation=1,
                    folded_through_high_water=11,
                    cumulative_root=durable_barrier_root,
                ),
            ),
            loss_ledger=LossLedgerV21(
                role_counts=role_delta,
                cumulative_loss_root=durable_ledger_root,
                last_fold_generation=4,
            ),
        ),
        contribution_index=tuple(
            sorted(
                (
                    core_locator,
                    retained_locator,
                    ContributionLocatorV21(
                        contribution_key=incoming_entry.key,
                        support=ContributionSupportV21.SEGMENT,
                        resident_id=retained.segment_id,
                        support_commitment=retained.folded_commitment_root,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    matrix_root = _digest(_MATRIX_ROOT_DOMAIN, "9")
    transition_root = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(barrier_root,),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=11,
    )
    candidate = EvidencedReencodingCandidateV21(
        proposed_state=proposed,
        source_envelope=envelope,
        policy=policy,
        record_decisions=record_decisions,
        segment_decisions=segment_decisions,
        first_fold_authorizations=(),
        barrier_advances=(barrier_advance,),
        ledger_advance=ledger_advance,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(barrier_root,),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=11,
        transition_root=transition_root,
    )
    return base, current_head, candidate, base_hash, envelope.envelope_root, policy_root


def test_segment_cannot_target_a_retained_rootless_segment_without_auth() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _retained_segment_escape_case()
    )

    result = verify_evidenced_reencoding_candidate_v21(
        base,
        current_head,
        candidate,
        expected_base_state_hash=base_hash,
        expected_source_envelope_root=envelope_root,
        expected_policy_root=policy_root,
        expected_advance_head_root=current_head.head_root,
    )

    _assert_failure(
        result,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def _drop_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    bounds = ControlFoldBoundsV21(
        max_exact_kernel_records=4,
        max_exact_kernel_bytes=4_096,
        max_hot_records=4,
        max_hot_bytes=4_096,
        max_hot_frontier_entries=4,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=8,
        max_dictionary_bytes=2_048,
        max_segments=4,
        max_representatives_per_segment=4,
        coverage_vector_size=2,
        bridge_vector_size=1,
        max_barriers=4,
        max_sparse_overrides=4,
        max_direct_parent_ids=2,
        max_parent_id_bytes=512,
        loss_ledger_bytes=512,
    )
    frame_policy = reencoding_policy_hash_v21("v21-drop-policy")
    frame = ControlFrameV21(
        protocol_id=CONTROL_FOLD_PROTOCOL_V21,
        schema_version=3,
        generation=3,
        high_water=10,
        accepted_budget=16_384,
        reencoding_policy_hash=frame_policy,
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )
    core = _record("drop-core", as_of=10, core_required=True)
    dropped = _record("drop-foldable", as_of=10, core_required=False)
    core_entry = _entry("drop-core-key")
    drop_entry = _entry("drop-key")
    dictionary = tuple(sorted((core_entry, drop_entry), key=lambda row: row.key))
    prior_barrier = FoldBarrierV21(
        namespace=_NAMESPACE,
        incarnation=1,
        folded_through_high_water=8,
        cumulative_root=fold_barrier_root_v21(_NAMESPACE, 1, 8),
    )
    base_state = CapsuleStateV21(
        frame=frame,
        bounds=bounds,
        exact_kernel=(core,),
        hot_cache=(dropped,),
        hot_frontier=(),
        dictionary=dictionary,
        capsule_segments=(),
        fold_barriers=(prior_barrier,),
        sparse_weight_policy=SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=LossLedgerV21(
            role_counts=(),
            cumulative_loss_root=loss_ledger_root_v21((), 0),
            last_fold_generation=0,
        ),
    )
    base = EvidencedCapsuleStateV21(
        state=base_state,
        contribution_index=tuple(
            sorted(
                (
                    ContributionLocatorV21(
                        contribution_key=core_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=core.record_id,
                        support_commitment=core.commitment,
                    ),
                    ContributionLocatorV21(
                        contribution_key=drop_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=dropped.record_id,
                        support_commitment=dropped.commitment,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    base_hash = evidenced_state_hash_v21(base)
    current_head = seed_advance_chain_head_v21(base, expected_state_hash=base_hash)
    core_source = _source(core)
    dropped_source = _source(dropped)
    sources = tuple(
        sorted((core_source, dropped_source), key=lambda row: row.record_id)
    )
    contributions = tuple(
        sorted(
            (
                _contribution(core_source, core_entry.key, CapsuleRoleV21.ROOT_GOAL),
                _contribution(dropped_source, drop_entry.key, CapsuleRoleV21.CONTEXT),
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
    policy = EvidencedReencodingPolicyV21(
        frame_policy_hash=frame_policy,
        max_matrix_rows=2,
        max_hard_edges=0,
        max_decisions=2,
        max_plan_evaluations=1,
        max_new_segments=0,
        max_loss_units=4,
        per_role_loss_caps=(
            (CapsuleRoleV21.CONTEXT, 1),
            (CapsuleRoleV21.ROOT_GOAL, 0),
        ),
        segment_loss_units=0,
        drop_loss_units=4,
        compaction_loss_units=0,
        solver_mode=SolverModeV21.EXACT_SMALL,
    )
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    record_decisions = tuple(
        sorted(
            (
                RecordDecisionV21(
                    record_id=core_source.record_id,
                    source_commitment=core_source.commitment,
                    outcome=ReencodingOutcomeV21.EXACT,
                    child_segment_id=None,
                    contribution_keys=(core_entry.key,),
                ),
                RecordDecisionV21(
                    record_id=dropped_source.record_id,
                    source_commitment=dropped_source.commitment,
                    outcome=ReencodingOutcomeV21.DROP,
                    child_segment_id=None,
                    contribution_keys=(drop_entry.key,),
                ),
            ),
            key=lambda row: (row.record_id, row.source_commitment),
        )
    )
    record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=record_decisions,
    )
    segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=(),
    )
    released = (
        ReleasedSourceV21(
            record_id=dropped_source.record_id,
            source_commitment=dropped_source.commitment,
        ),
    )
    prior_cursor = current_head.barrier_cursors[0]
    barrier_root = barrier_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=prior_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=8,
        new_high_water=10,
        generation=4,
        released_sources=released,
    )
    durable_barrier_root = barrier_durable_projection_root_v21(
        previous_durable_root=prior_cursor.durable_root,
        barrier_advance_root=barrier_root,
        namespace=_NAMESPACE,
        incarnation=1,
        new_high_water=10,
        generation=4,
    )
    barrier_advance = BarrierAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=prior_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=8,
        new_high_water=10,
        generation=4,
        released_sources=released,
        new_durable_root=durable_barrier_root,
        new_root=barrier_root,
    )
    role_delta = ((CapsuleRoleV21.CONTEXT, 1),)
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_root,),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=0,
        new_generation=4,
        loss_units_delta=4,
    )
    durable_ledger_root = ledger_durable_projection_root_v21(
        previous_durable_root=current_head.ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=role_delta,
        new_generation=4,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_root,),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=0,
        new_generation=4,
        loss_units_delta=4,
        new_durable_root=durable_ledger_root,
        new_root=ledger_root,
    )
    proposed = EvidencedCapsuleStateV21(
        state=replace(
            base_state,
            frame=replace(frame, generation=4),
            hot_cache=(),
            fold_barriers=(
                FoldBarrierV21(
                    namespace=_NAMESPACE,
                    incarnation=1,
                    folded_through_high_water=10,
                    cumulative_root=durable_barrier_root,
                ),
            ),
            loss_ledger=LossLedgerV21(
                role_counts=role_delta,
                cumulative_loss_root=durable_ledger_root,
                last_fold_generation=4,
            ),
        ),
        contribution_index=(
            ContributionLocatorV21(
                contribution_key=core_entry.key,
                support=ContributionSupportV21.EXACT,
                resident_id=core.record_id,
                support_commitment=core.commitment,
            ),
        ),
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    matrix_root = _digest(_MATRIX_ROOT_DOMAIN, "d")
    transition_root = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(barrier_root,),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=10,
    )
    candidate = EvidencedReencodingCandidateV21(
        proposed_state=proposed,
        source_envelope=envelope,
        policy=policy,
        record_decisions=record_decisions,
        segment_decisions=(),
        first_fold_authorizations=(),
        barrier_advances=(barrier_advance,),
        ledger_advance=ledger_advance,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(barrier_root,),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=10,
        transition_root=transition_root,
    )
    return (
        base,
        current_head,
        candidate,
        base_hash,
        envelope.envelope_root,
        policy_root,
    )


def test_drop_removes_body_and_locator_through_existing_barrier() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = _drop_case()

    result = _verify(
        base,
        current_head,
        candidate,
        base_hash,
        envelope_root,
        policy_root,
    )

    assert result.status is EvidencedReencodingStatusV21.VERIFIED
    assert len(result.state.state.hot_cache) == 0
    assert len(result.state.contribution_index) == 1
    assert result.advance_head.barrier_cursors[0].high_water == 10


def test_existing_barrier_cursor_and_projection_roll_back() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = _drop_case()
    barrier = base.state.fold_barriers[0]
    stale_high_water = barrier.folded_through_high_water - 1
    stale_cursor = BarrierAdvanceCursorV21(
        namespace=barrier.namespace,
        incarnation=barrier.incarnation,
        durable_root=barrier.cumulative_root,
        high_water=stale_high_water,
        kind=AdvanceCursorKindV21.SEED,
        transient_root=barrier_advance_seed_root_v21(
            base_state_hash=base_hash,
            prior_advance_head_root=None,
            namespace=barrier.namespace,
            incarnation=barrier.incarnation,
            durable_root=barrier.cumulative_root,
            high_water=stale_high_water,
        ),
    )
    stale_head = AdvanceChainHeadV21(
        state_hash=base_hash,
        generation=current_head.generation,
        barrier_cursors=(stale_cursor,),
        ledger_cursor=current_head.ledger_cursor,
        previous_head_root=None,
        head_root=advance_chain_head_root_v21(
            state_hash=base_hash,
            generation=current_head.generation,
            barrier_cursors=(stale_cursor,),
            ledger_cursor=current_head.ledger_cursor,
            previous_head_root=None,
        ),
    )
    stale_cursor_result = _verify(
        base,
        stale_head,
        candidate,
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        stale_cursor_result,
        base,
        stale_head,
        EvidencedReencodingStatusV21.STALE_REJECTED,
    )

    missing_projection = replace(
        candidate.proposed_state,
        state=replace(candidate.proposed_state.state, fold_barriers=()),
    )
    projection_result = _verify(
        base,
        current_head,
        replace(candidate, proposed_state=missing_projection),
        base_hash,
        envelope_root,
        policy_root,
    )
    _assert_failure(
        projection_result,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def _compact_segment_case() -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    bounds = ControlFoldBoundsV21(
        max_exact_kernel_records=4,
        max_exact_kernel_bytes=4_096,
        max_hot_records=4,
        max_hot_bytes=4_096,
        max_hot_frontier_entries=4,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=8,
        max_dictionary_bytes=2_048,
        max_segments=4,
        max_representatives_per_segment=4,
        coverage_vector_size=2,
        bridge_vector_size=1,
        max_barriers=4,
        max_sparse_overrides=4,
        max_direct_parent_ids=2,
        max_parent_id_bytes=512,
        loss_ledger_bytes=512,
    )
    frame_policy = reencoding_policy_hash_v21("v21-compact-policy")
    frame = ControlFrameV21(
        protocol_id=CONTROL_FOLD_PROTOCOL_V21,
        schema_version=3,
        generation=3,
        high_water=10,
        accepted_budget=16_384,
        reencoding_policy_hash=frame_policy,
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )
    core = _record("compact-core", as_of=10, core_required=True)
    core_entry = _entry("compact-core-key")
    parent_entry = _entry("compact-parent-key")
    dictionary = tuple(sorted((core_entry, parent_entry), key=lambda row: row.key))
    parent_id = segment_identity_v21(_NAMESPACE, "compact-parent")
    fold_policy = fold_policy_hash_v21("compact-parent-policy")
    roles = ((CapsuleRoleV21.CONTEXT, 1),)
    parent_folded = folded_commitment_root_v21(
        segment_id=parent_id,
        namespace=_NAMESPACE,
        generation_start=1,
        generation_end=1,
        source_count=1,
        role_counts=roles,
        representatives=(),
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=1,
        fold_policy_hash=fold_policy,
    )
    parent = CapsuleSegmentV21(
        segment_id=parent_id,
        parent_ids=(),
        input_commitment_root=segment_input_commitment_root_v21(
            (),
            folded_commitment_root=parent_folded,
            generation_start=1,
            generation_end=1,
            fold_policy_hash=fold_policy,
        ),
        folded_commitment_root=parent_folded,
        namespace=_NAMESPACE,
        generation_start=1,
        generation_end=1,
        source_count=1,
        role_counts=roles,
        representatives=(),
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=1,
        fold_policy_hash=fold_policy,
    )
    base_state = CapsuleStateV21(
        frame=frame,
        bounds=bounds,
        exact_kernel=(core,),
        hot_cache=(),
        hot_frontier=(),
        dictionary=dictionary,
        capsule_segments=(parent,),
        fold_barriers=(),
        sparse_weight_policy=SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=LossLedgerV21(
            role_counts=(),
            cumulative_loss_root=loss_ledger_root_v21((), 0),
            last_fold_generation=0,
        ),
    )
    base = EvidencedCapsuleStateV21(
        state=base_state,
        contribution_index=tuple(
            sorted(
                (
                    ContributionLocatorV21(
                        contribution_key=core_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=core.record_id,
                        support_commitment=core.commitment,
                    ),
                    ContributionLocatorV21(
                        contribution_key=parent_entry.key,
                        support=ContributionSupportV21.SEGMENT,
                        resident_id=parent.segment_id,
                        support_commitment=parent.folded_commitment_root,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    base_hash = evidenced_state_hash_v21(base)
    current_head = seed_advance_chain_head_v21(base, expected_state_hash=base_hash)
    core_source = _source(core)
    contributions = (
        _contribution(core_source, core_entry.key, CapsuleRoleV21.ROOT_GOAL),
    )
    envelope = _envelope(
        base_hash=base_hash,
        sources=(core_source,),
        incoming_ids=(),
        contributions=contributions,
    )
    policy = EvidencedReencodingPolicyV21(
        frame_policy_hash=frame_policy,
        max_matrix_rows=1,
        max_hard_edges=0,
        max_decisions=2,
        max_plan_evaluations=1,
        max_new_segments=1,
        max_loss_units=2,
        per_role_loss_caps=(
            (CapsuleRoleV21.CONTEXT, 1),
            (CapsuleRoleV21.ROOT_GOAL, 0),
        ),
        segment_loss_units=0,
        drop_loss_units=0,
        compaction_loss_units=2,
        solver_mode=SolverModeV21.EXACT_SMALL,
    )
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    child_id = segment_identity_v21(_NAMESPACE, "compact-child")
    child_policy = fold_policy_hash_v21("compact-child-policy")
    child_folded = folded_commitment_root_v21(
        segment_id=child_id,
        namespace=_NAMESPACE,
        generation_start=4,
        generation_end=4,
        source_count=1,
        role_counts=roles,
        representatives=(),
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=1,
        fold_policy_hash=child_policy,
    )
    child = CapsuleSegmentV21(
        segment_id=child_id,
        parent_ids=(parent.segment_id,),
        input_commitment_root=segment_input_commitment_root_v21(
            (parent,),
            folded_commitment_root=child_folded,
            generation_start=4,
            generation_end=4,
            fold_policy_hash=child_policy,
        ),
        folded_commitment_root=child_folded,
        namespace=_NAMESPACE,
        generation_start=4,
        generation_end=4,
        source_count=1,
        role_counts=roles,
        representatives=(),
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=1,
        fold_policy_hash=child_policy,
    )
    record_decisions = (
        RecordDecisionV21(
            record_id=core_source.record_id,
            source_commitment=core_source.commitment,
            outcome=ReencodingOutcomeV21.EXACT,
            child_segment_id=None,
            contribution_keys=(core_entry.key,),
        ),
    )
    segment_decisions = (
        SegmentDecisionV21(
            segment_id=parent.segment_id,
            canonical_segment_hash=canonical_segment_hash_v21(
                base_state, parent.segment_id
            ),
            disposition=SegmentDispositionV21.COMPACT,
            child_segment_id=child.segment_id,
            contribution_keys=(parent_entry.key,),
        ),
    )
    record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=record_decisions,
    )
    segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        rows=segment_decisions,
    )
    role_delta = ((CapsuleRoleV21.CONTEXT, 1),)
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=0,
        new_generation=4,
        loss_units_delta=2,
    )
    durable_ledger_root = ledger_durable_projection_root_v21(
        previous_durable_root=current_head.ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=role_delta,
        new_generation=4,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=(),
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=0,
        new_generation=4,
        loss_units_delta=2,
        new_durable_root=durable_ledger_root,
        new_root=ledger_root,
    )
    proposed = EvidencedCapsuleStateV21(
        state=replace(
            base_state,
            frame=replace(frame, generation=4),
            capsule_segments=(child,),
            loss_ledger=LossLedgerV21(
                role_counts=role_delta,
                cumulative_loss_root=durable_ledger_root,
                last_fold_generation=4,
            ),
        ),
        contribution_index=tuple(
            sorted(
                (
                    ContributionLocatorV21(
                        contribution_key=core_entry.key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=core.record_id,
                        support_commitment=core.commitment,
                    ),
                    ContributionLocatorV21(
                        contribution_key=parent_entry.key,
                        support=ContributionSupportV21.SEGMENT,
                        resident_id=child.segment_id,
                        support_commitment=child.folded_commitment_root,
                    ),
                ),
                key=lambda row: row.contribution_key,
            )
        ),
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    matrix_root = _digest(_MATRIX_ROOT_DOMAIN, "c")
    transition_root = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=10,
    )
    candidate = EvidencedReencodingCandidateV21(
        proposed_state=proposed,
        source_envelope=envelope,
        policy=policy,
        record_decisions=record_decisions,
        segment_decisions=segment_decisions,
        first_fold_authorizations=(),
        barrier_advances=(),
        ledger_advance=ledger_advance,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=10,
        transition_root=transition_root,
    )
    return (
        base,
        current_head,
        candidate,
        base_hash,
        envelope.envelope_root,
        policy_root,
    )


def _drop_segment_case(
    *,
    max_loss_units: int = 2,
) -> tuple[
    EvidencedCapsuleStateV21,
    AdvanceChainHeadV21,
    EvidencedReencodingCandidateV21,
    str,
    str,
    str,
]:
    base, current_head, compacted, base_hash, envelope_root, _policy_root = (
        _compact_segment_case()
    )
    parent = base.state.capsule_segments[0]
    policy = replace(
        compacted.policy,
        compaction_loss_units=0,
        drop_loss_units=2,
        max_loss_units=max_loss_units,
    )
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    record_decisions = compacted.record_decisions
    segment_decisions = (
        SegmentDecisionV21(
            segment_id=parent.segment_id,
            canonical_segment_hash=canonical_segment_hash_v21(
                base.state, parent.segment_id
            ),
            disposition=SegmentDispositionV21.DROP,
            child_segment_id=None,
            contribution_keys=compacted.segment_decisions[0].contribution_keys,
        ),
    )
    record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        rows=record_decisions,
    )
    segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        rows=segment_decisions,
    )
    role_delta = parent.role_counts
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=base.state.loss_ledger.role_counts,
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=base.state.loss_ledger.last_fold_generation,
        new_generation=4,
        loss_units_delta=2,
    )
    durable_ledger_root = ledger_durable_projection_root_v21(
        previous_durable_root=current_head.ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=role_delta,
        new_generation=4,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(),
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=base.state.loss_ledger.role_counts,
        delta_role_counts=role_delta,
        new_role_counts=role_delta,
        previous_generation=base.state.loss_ledger.last_fold_generation,
        new_generation=4,
        loss_units_delta=2,
        new_durable_root=durable_ledger_root,
        new_root=ledger_root,
    )
    proposed = EvidencedCapsuleStateV21(
        state=replace(
            base.state,
            frame=replace(base.state.frame, generation=4),
            capsule_segments=(),
            loss_ledger=LossLedgerV21(
                role_counts=role_delta,
                cumulative_loss_root=durable_ledger_root,
                last_fold_generation=4,
            ),
        ),
        contribution_index=tuple(
            row
            for row in base.contribution_index
            if row.support is ContributionSupportV21.EXACT
        ),
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    matrix_root = _digest(_MATRIX_ROOT_DOMAIN, "f")
    transition_root = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope_root,
        policy_root=policy_root,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=base.state.frame.high_water,
    )
    candidate = EvidencedReencodingCandidateV21(
        proposed_state=proposed,
        source_envelope=compacted.source_envelope,
        policy=policy,
        record_decisions=record_decisions,
        segment_decisions=segment_decisions,
        first_fold_authorizations=(),
        barrier_advances=(),
        ledger_advance=ledger_advance,
        matrix_root=matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(),
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=4,
        target_high_water=base.state.frame.high_water,
        transition_root=transition_root,
    )
    return base, current_head, candidate, base_hash, envelope_root, policy_root


def test_segment_drop_charges_parent_loss_and_removes_its_locator() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _drop_segment_case()
    )

    result = _verify(
        base,
        current_head,
        candidate,
        base_hash,
        envelope_root,
        policy_root,
    )

    assert result.status is EvidencedReencodingStatusV21.VERIFIED
    assert result.state.state.capsule_segments == ()
    assert result.state.state.loss_ledger.role_counts == ((CapsuleRoleV21.CONTEXT, 1),)
    assert result.state.contribution_index == (base.contribution_index[0],)


def test_segment_drop_loss_cap_rolls_back_to_input_identity() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _drop_segment_case(max_loss_units=1)
    )

    result = _verify(
        base,
        current_head,
        candidate,
        base_hash,
        envelope_root,
        policy_root,
    )

    _assert_failure(
        result,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )


def test_parent_compaction_consumes_once_and_repoints_current_locator() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _compact_segment_case()
    )

    result = verify_evidenced_reencoding_candidate_v21(
        base,
        current_head,
        candidate,
        expected_base_state_hash=base_hash,
        expected_source_envelope_root=envelope_root,
        expected_policy_root=policy_root,
        expected_advance_head_root=current_head.head_root,
    )

    assert result.status is EvidencedReencodingStatusV21.VERIFIED
    assert result.state is candidate.proposed_state
    assert result.state.contribution_index[1].resident_id == (
        candidate.segment_decisions[0].child_segment_id
    )


def test_parent_compaction_cannot_reuse_one_parent_for_two_children() -> None:
    base, current_head, candidate, base_hash, envelope_root, policy_root = (
        _compact_segment_case()
    )
    parent = base.state.capsule_segments[0]
    child = candidate.proposed_state.state.capsule_segments[0]
    sibling_id = segment_identity_v21(_NAMESPACE, "compact-sibling")
    sibling_folded = folded_commitment_root_v21(
        segment_id=sibling_id,
        namespace=child.namespace,
        generation_start=child.generation_start,
        generation_end=child.generation_end,
        source_count=child.source_count,
        role_counts=child.role_counts,
        representatives=child.representatives,
        coverage_vector=child.coverage_vector,
        bridge_vector=child.bridge_vector,
        loss_class=child.loss_class,
        budget_used=child.budget_used,
        fold_policy_hash=child.fold_policy_hash,
    )
    sibling = CapsuleSegmentV21(
        segment_id=sibling_id,
        parent_ids=(parent.segment_id,),
        input_commitment_root=segment_input_commitment_root_v21(
            (parent,),
            folded_commitment_root=sibling_folded,
            generation_start=child.generation_start,
            generation_end=child.generation_end,
            fold_policy_hash=child.fold_policy_hash,
        ),
        folded_commitment_root=sibling_folded,
        namespace=child.namespace,
        generation_start=child.generation_start,
        generation_end=child.generation_end,
        source_count=child.source_count,
        role_counts=child.role_counts,
        representatives=child.representatives,
        coverage_vector=child.coverage_vector,
        bridge_vector=child.bridge_vector,
        loss_class=child.loss_class,
        budget_used=child.budget_used,
        fold_policy_hash=child.fold_policy_hash,
    )
    duplicated_parent = replace(
        candidate.proposed_state,
        state=replace(
            candidate.proposed_state.state,
            capsule_segments=tuple(
                sorted((child, sibling), key=lambda segment: segment.segment_id)
            ),
        ),
    )
    result = _verify(
        base,
        current_head,
        replace(candidate, proposed_state=duplicated_parent),
        base_hash,
        envelope_root,
        policy_root,
    )

    _assert_failure(
        result,
        base,
        current_head,
        EvidencedReencodingStatusV21.ROLLED_BACK,
    )
