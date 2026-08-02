"""RED-first guards for transient evidenced-reencoding contract primitives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

import pytest

import crm_experiment.reencoding_contracts_v21 as reencoding_contracts_v21
from crm_experiment.contracts_v21 import (
    CONTROL_FOLD_PROTOCOL_V21,
    CapsuleRepresentativeV21,
    CapsuleRoleV21,
    CapsuleSegmentV21,
    CapsuleStateV21,
    ControlFoldBoundsV21,
    ControlFrameV21,
    ExactRecordV21,
    FoldBarrierV21,
    HardDependencyV21,
    LossClassV21,
    LossLedgerV21,
    SparseWeightPolicyV21,
    capacity_policy_hash_v21,
    dictionary_identity_v21,
    fold_barrier_root_v21,
    fold_policy_hash_v21,
    folded_commitment_root_v21,
    loss_ledger_root_v21,
    namespace_identity_v21,
    record_identity_v21,
    reencoding_policy_hash_v21,
    representative_commitment_v21,
    representative_identity_v21,
    segment_identity_v21,
    segment_input_commitment_root_v21,
    source_commitment_v21,
)
from crm_experiment.evidenced_state_v21 import (
    EvidencedCapsuleStateV21,
    EvidencedStateMismatchV21,
    evidenced_state_hash_v21,
)
from crm_experiment.reencoding_contracts_v21 import (
    MAX_EVIDENCED_BRIDGE_VECTOR_V21,
    MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21,
    MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21,
    MAX_EVIDENCED_COVERAGE_VECTOR_V21,
    MAX_EVIDENCED_HARD_EDGES_V21,
    MAX_EVIDENCED_LOSS_UNITS_V21,
    MAX_EVIDENCED_NEW_SEGMENTS_V21,
    MAX_EVIDENCED_PLAN_EVALUATIONS_V21,
    MAX_EVIDENCED_REPRESENTATIVE_CANDIDATES_V21,
    MAX_EVIDENCED_ROOT_ROWS_V21,
    MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21,
    MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21,
    MAX_EVIDENCED_SOURCE_RECORDS_V21,
    AdvanceChainHeadV21,
    AdvanceCursorKindV21,
    BarrierAdvanceCursorV21,
    BarrierAdvanceV21,
    EvidencedReencodingPolicyV21,
    FirstFoldAuthorizationV21,
    FirstFoldSourceV21,
    FoldContributionEvidenceV21,
    HardGraphEdgeV21,
    LedgerAdvanceCursorV21,
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
    next_advance_chain_head_v21,
    record_decision_root_v21,
    seed_advance_chain_head_v21,
    segment_decision_root_v21,
    source_envelope_root_v21,
)

_NAMESPACE = namespace_identity_v21("v21-evidenced-reencoding-tests")
_BASE_STATE_DOMAIN = "crm-v21-evidenced-state-s3/v1"
_HARD_GRAPH_DOMAIN = "crm-v21-hard-graph-s3/v1"
_SOURCE_ENVELOPE_DOMAIN = "crm-v21-source-envelope-s3/v1"
_MATRIX_DOMAIN = "crm-v21-evidenced-matrix-s3/v1"
_RECORD_DECISION_DOMAIN = "crm-v21-record-decisions-s3/v1"
_SEGMENT_DECISION_DOMAIN = "crm-v21-segment-decisions-s3/v1"
_BARRIER_ADVANCE_DOMAIN = "crm-v21-barrier-advance-s3/v1"
_LEDGER_ADVANCE_DOMAIN = "crm-v21-ledger-advance-s3/v1"
_BARRIER_SEED_DOMAIN = "crm-v21-barrier-advance-seed-s3/v1"
_LEDGER_SEED_DOMAIN = "crm-v21-ledger-advance-seed-s3/v1"
_ADVANCE_HEAD_DOMAIN = "crm-v21-advance-head-s3/v1"
_DURABLE_BARRIER_DOMAIN = "v21-barrier-root-s3"
_DURABLE_LEDGER_DOMAIN = "v21-ledger-root-s3"


def _digest(domain: str, digit: str) -> str:
    return f"{domain}:{digit * 64}"


def _source_record(
    label: str,
    *,
    hard_depends_on: tuple[HardDependencyV21, ...] = (),
    body: str | None = None,
) -> SourceRecordV21:
    record_id = record_identity_v21(_NAMESPACE, label)
    selected_body = body or f"source body for {label}"
    return SourceRecordV21(
        record_id=record_id,
        namespace=_NAMESPACE,
        incarnation=1,
        as_of=10,
        commitment=source_commitment_v21(
            record_id=record_id,
            namespace=_NAMESPACE,
            incarnation=1,
            as_of=10,
            body=selected_body,
            core_required=False,
            active=True,
        ),
        body=selected_body,
        core_required=False,
        active=True,
        hard_depends_on=hard_depends_on,
    )


def _contribution(record: SourceRecordV21) -> FoldContributionEvidenceV21:
    contribution_key = dictionary_identity_v21(
        _NAMESPACE, f"contribution-{record.record_id[-12:]}"
    )
    segment_id = segment_identity_v21(_NAMESPACE, "evidence-segment")
    feature_id = representative_identity_v21(segment_id, record.record_id[-12:])
    representative = CapsuleRepresentativeV21(
        feature_id=feature_id,
        feature_commitment=representative_commitment_v21(feature_id),
        weight=1.0,
    )
    return FoldContributionEvidenceV21(
        record_id=record.record_id,
        source_commitment=record.commitment,
        contribution_keys=(contribution_key,),
        role=CapsuleRoleV21.CONTEXT,
        coverage_vector=(1, 0),
        bridge_vector=(0, 1),
        representative_candidates=(representative,),
    )


@dataclass(frozen=True)
class _EnvelopeContext:
    base_state_hash: str
    target: SourceRecordV21
    source: SourceRecordV21
    envelope: SourceEnvelopeV21
    policy: EvidencedReencodingPolicyV21

    @property
    def source_key(self) -> str:
        evidence = next(
            row
            for row in self.envelope.contribution_evidence
            if row.record_id == self.source.record_id
        )
        return evidence.contribution_keys[0]


def _policy() -> EvidencedReencodingPolicyV21:
    return EvidencedReencodingPolicyV21(
        frame_policy_hash=reencoding_policy_hash_v21("v21-evidenced-policy"),
        max_matrix_rows=2,
        max_hard_edges=2,
        max_decisions=2,
        max_plan_evaluations=16,
        max_new_segments=2,
        max_loss_units=20,
        per_role_loss_caps=((CapsuleRoleV21.CONTEXT, 10),),
        segment_loss_units=3,
        drop_loss_units=4,
        compaction_loss_units=2,
        solver_mode=SolverModeV21.EXACT_SMALL,
    )


def _envelope_context() -> _EnvelopeContext:
    target = _source_record("target")
    source = _source_record(
        "source",
        hard_depends_on=(
            HardDependencyV21(
                target_id=target.record_id,
                target_commitment=target.commitment,
            ),
        ),
    )
    source_records = tuple(
        sorted((target, source), key=lambda record: record.record_id)
    )
    graph_nodes = tuple(
        SourceGraphNodeV21(
            record_id=record.record_id,
            source_commitment=record.commitment,
        )
        for record in source_records
    )
    hard_edges = tuple(
        sorted(
            (
                HardGraphEdgeV21(
                    source_id=record.record_id,
                    source_commitment=record.commitment,
                    target_id=dependency.target_id,
                    target_commitment=dependency.target_commitment,
                )
                for record in source_records
                for dependency in record.hard_depends_on
            ),
            key=lambda edge: (edge.source_id, edge.target_id),
        )
    )
    contribution_evidence = tuple(
        sorted(
            (_contribution(record) for record in source_records),
            key=lambda row: row.record_id,
        )
    )
    base_state_hash = _digest(_BASE_STATE_DOMAIN, "a")
    hard_root = hard_graph_root_v21(graph_nodes=graph_nodes, hard_edges=hard_edges)
    contribution_root = contribution_root_v21(contribution_evidence)
    envelope_root = source_envelope_root_v21(
        base_state_hash=base_state_hash,
        source_records=source_records,
        incoming_record_ids=(source.record_id,),
        graph_nodes=graph_nodes,
        hard_edges=hard_edges,
        contribution_evidence=contribution_evidence,
        hard_graph_root=hard_root,
        contribution_root=contribution_root,
    )
    envelope = SourceEnvelopeV21(
        base_state_hash=base_state_hash,
        source_records=source_records,
        incoming_record_ids=(source.record_id,),
        graph_nodes=graph_nodes,
        hard_edges=hard_edges,
        contribution_evidence=contribution_evidence,
        hard_graph_root=hard_root,
        contribution_root=contribution_root,
        envelope_root=envelope_root,
    )
    return _EnvelopeContext(
        base_state_hash=base_state_hash,
        target=target,
        source=source,
        envelope=envelope,
        policy=_policy(),
    )


def _graph_rows(
    source_records: tuple[SourceRecordV21, ...],
) -> tuple[tuple[SourceGraphNodeV21, ...], tuple[HardGraphEdgeV21, ...]]:
    nodes = tuple(
        SourceGraphNodeV21(record.record_id, record.commitment)
        for record in source_records
    )
    edges = tuple(
        sorted(
            (
                HardGraphEdgeV21(
                    source_id=record.record_id,
                    source_commitment=record.commitment,
                    target_id=dependency.target_id,
                    target_commitment=dependency.target_commitment,
                )
                for record in source_records
                for dependency in record.hard_depends_on
            ),
            key=lambda edge: (edge.source_id, edge.target_id),
        )
    )
    return nodes, edges


def _full_envelope_rows(
    source_records: tuple[SourceRecordV21, ...],
) -> tuple[
    tuple[SourceGraphNodeV21, ...],
    tuple[HardGraphEdgeV21, ...],
    tuple[FoldContributionEvidenceV21, ...],
    str,
    str,
]:
    graph_nodes, hard_edges = _graph_rows(source_records)
    contribution_evidence = tuple(
        sorted(
            (_contribution(record) for record in source_records),
            key=lambda row: row.record_id,
        )
    )
    return (
        graph_nodes,
        hard_edges,
        contribution_evidence,
        hard_graph_root_v21(graph_nodes=graph_nodes, hard_edges=hard_edges),
        contribution_root_v21(contribution_evidence),
    )


def _source_envelope_from_records(
    source_records: tuple[SourceRecordV21, ...],
    *,
    envelope_root: str,
) -> SourceEnvelopeV21:
    (
        graph_nodes,
        hard_edges,
        contribution_evidence,
        hard_graph_root,
        contribution_root,
    ) = _full_envelope_rows(source_records)
    return SourceEnvelopeV21(
        base_state_hash=_digest(_BASE_STATE_DOMAIN, "a"),
        source_records=source_records,
        incoming_record_ids=(),
        graph_nodes=graph_nodes,
        hard_edges=hard_edges,
        contribution_evidence=contribution_evidence,
        hard_graph_root=hard_graph_root,
        contribution_root=contribution_root,
        envelope_root=envelope_root,
    )


def _resident_segment(
    label: str = "resident", *, budget_used: int = 64
) -> CapsuleSegmentV21:
    segment_id = segment_identity_v21(_NAMESPACE, label)
    policy_hash = fold_policy_hash_v21("v21-evidenced-fold-policy")
    representative_id = representative_identity_v21(segment_id, "feature")
    representatives = (
        CapsuleRepresentativeV21(
            feature_id=representative_id,
            feature_commitment=representative_commitment_v21(representative_id),
            weight=1.0,
        ),
    )
    role_counts = ((CapsuleRoleV21.CONTEXT, 1),)
    folded_root = folded_commitment_root_v21(
        segment_id=segment_id,
        namespace=_NAMESPACE,
        generation_start=1,
        generation_end=1,
        source_count=1,
        role_counts=role_counts,
        representatives=representatives,
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=budget_used,
        fold_policy_hash=policy_hash,
    )
    return CapsuleSegmentV21(
        segment_id=segment_id,
        parent_ids=(),
        input_commitment_root=segment_input_commitment_root_v21(
            (),
            folded_commitment_root=folded_root,
            generation_start=1,
            generation_end=1,
            fold_policy_hash=policy_hash,
        ),
        folded_commitment_root=folded_root,
        namespace=_NAMESPACE,
        generation_start=1,
        generation_end=1,
        source_count=1,
        role_counts=role_counts,
        representatives=representatives,
        coverage_vector=(1, 0),
        bridge_vector=(0,),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=budget_used,
        fold_policy_hash=policy_hash,
    )


def _validated_state(segment: CapsuleSegmentV21) -> CapsuleStateV21:
    bounds = ControlFoldBoundsV21(
        max_exact_kernel_records=4,
        max_exact_kernel_bytes=4_096,
        max_hot_records=4,
        max_hot_bytes=4_096,
        max_hot_frontier_entries=4,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=4,
        max_dictionary_bytes=512,
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
    frame = ControlFrameV21(
        protocol_id=CONTROL_FOLD_PROTOCOL_V21,
        schema_version=3,
        generation=3,
        high_water=10,
        accepted_budget=16_384,
        reencoding_policy_hash=reencoding_policy_hash_v21("state-policy"),
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )
    core_id = record_identity_v21(_NAMESPACE, "state-core")
    body = "state core body"
    core = ExactRecordV21(
        record_id=core_id,
        namespace=_NAMESPACE,
        incarnation=1,
        as_of=10,
        commitment=source_commitment_v21(
            record_id=core_id,
            namespace=_NAMESPACE,
            incarnation=1,
            as_of=10,
            body=body,
            core_required=True,
            active=True,
        ),
        body=body,
        core_required=True,
        active=True,
        hard_depends_on=(),
    )
    return CapsuleStateV21(
        frame=frame,
        bounds=bounds,
        exact_kernel=(core,),
        hot_cache=(),
        hot_frontier=(),
        dictionary=(),
        capsule_segments=(segment,),
        fold_barriers=(),
        sparse_weight_policy=SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=LossLedgerV21(
            role_counts=(),
            cumulative_loss_root=loss_ledger_root_v21((), 0),
            last_fold_generation=0,
        ),
    )


def _anchored_wrapper() -> tuple[EvidencedCapsuleStateV21, FoldBarrierV21]:
    state = _validated_state(_resident_segment())
    barrier = FoldBarrierV21(
        namespace=_NAMESPACE,
        incarnation=1,
        folded_through_high_water=8,
        cumulative_root=fold_barrier_root_v21(_NAMESPACE, 1, 8),
    )
    ledger_counts = ((CapsuleRoleV21.CONTEXT, 1),)
    anchored_state = replace(
        state,
        fold_barriers=(barrier,),
        loss_ledger=LossLedgerV21(
            role_counts=ledger_counts,
            cumulative_loss_root=loss_ledger_root_v21(ledger_counts, 2),
            last_fold_generation=2,
        ),
    )
    return EvidencedCapsuleStateV21(
        state=anchored_state,
        contribution_index=(),
    ), barrier


def _decision_roots(
    context: _EnvelopeContext,
) -> tuple[RecordDecisionV21, SegmentDecisionV21, str, str]:
    child_segment_id = segment_identity_v21(_NAMESPACE, "child")
    record_decision = RecordDecisionV21(
        record_id=context.source.record_id,
        source_commitment=context.source.commitment,
        outcome=ReencodingOutcomeV21.SEGMENT,
        child_segment_id=child_segment_id,
        contribution_keys=(context.source_key,),
    )
    state = _validated_state(_resident_segment())
    parent_segment = state.capsule_segments[0]
    segment_decision = SegmentDecisionV21(
        segment_id=parent_segment.segment_id,
        canonical_segment_hash=canonical_segment_hash_v21(
            state, parent_segment.segment_id
        ),
        disposition=SegmentDispositionV21.COMPACT,
        child_segment_id=child_segment_id,
        contribution_keys=(context.source_key,),
    )
    policy_root = evidenced_reencoding_policy_root_v21(context.policy)
    record_root = record_decision_root_v21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        rows=(record_decision,),
    )
    segment_root = segment_decision_root_v21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        rows=(segment_decision,),
    )
    return record_decision, segment_decision, record_root, segment_root


def test_reencoding_outcomes_and_frozen_caps_are_closed() -> None:
    assert ReencodingOutcomeV21.EXACT.value == "EXACT"
    assert SegmentDispositionV21.COMPACT.value == "COMPACT"
    assert SolverModeV21.DETERMINISTIC_GREEDY.value == "DETERMINISTIC_GREEDY"
    assert MAX_EVIDENCED_SOURCE_RECORDS_V21 == 256
    assert MAX_EVIDENCED_HARD_EDGES_V21 == 1024
    assert MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21 == 64
    assert MAX_EVIDENCED_REPRESENTATIVE_CANDIDATES_V21 == 64
    assert MAX_EVIDENCED_ROOT_ROWS_V21 == 256
    assert MAX_EVIDENCED_PLAN_EVALUATIONS_V21 == 65536
    assert MAX_EVIDENCED_LOSS_UNITS_V21 == 1_000_000
    assert MAX_EVIDENCED_NEW_SEGMENTS_V21 == 64
    assert MAX_EVIDENCED_COVERAGE_VECTOR_V21 == 64
    assert MAX_EVIDENCED_BRIDGE_VECTOR_V21 == 64
    assert MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21 == 64 * 1024
    assert MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21 == 8_388_608
    assert MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21 == 33_554_432


def test_policy_roots_all_bounds_and_rejects_cap_or_nominal_attacks() -> None:
    policy = _policy()
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    assert policy_root.startswith("crm-v21-evidenced-policy-s3/v1:")

    changes = {
        "frame_policy_hash": reencoding_policy_hash_v21("another-policy"),
        "max_matrix_rows": 3,
        "max_hard_edges": 3,
        "max_decisions": 3,
        "max_plan_evaluations": 17,
        "max_new_segments": 3,
        "max_loss_units": 21,
        "per_role_loss_caps": ((CapsuleRoleV21.CONTEXT, 9),),
        "segment_loss_units": 4,
        "drop_loss_units": 5,
        "compaction_loss_units": 3,
        "solver_mode": SolverModeV21.DETERMINISTIC_GREEDY,
    }
    for field, value in changes.items():
        assert (
            evidenced_reencoding_policy_root_v21(replace(policy, **{field: value}))
            != policy_root
        )

    for field, cap in (
        ("max_matrix_rows", MAX_EVIDENCED_SOURCE_RECORDS_V21),
        ("max_hard_edges", MAX_EVIDENCED_HARD_EDGES_V21),
        ("max_decisions", MAX_EVIDENCED_ROOT_ROWS_V21),
        ("max_plan_evaluations", MAX_EVIDENCED_PLAN_EVALUATIONS_V21),
        ("max_new_segments", MAX_EVIDENCED_NEW_SEGMENTS_V21),
        ("max_loss_units", MAX_EVIDENCED_LOSS_UNITS_V21),
        ("segment_loss_units", MAX_EVIDENCED_LOSS_UNITS_V21),
        ("drop_loss_units", MAX_EVIDENCED_LOSS_UNITS_V21),
        ("compaction_loss_units", MAX_EVIDENCED_LOSS_UNITS_V21),
    ):
        with pytest.raises(ValueError):
            replace(policy, **{field: cap + 1})
    with pytest.raises(ValueError):
        replace(policy, max_hard_edges=True)


def test_source_envelope_binds_full_universe_and_hard_edges_outside_commitment() -> (
    None
):
    context = _envelope_context()
    envelope = context.envelope
    assert envelope.hard_graph_root.startswith(f"{_HARD_GRAPH_DOMAIN}:")
    assert envelope.contribution_root.startswith("crm-v21-fold-contributions-s3/v1:")
    assert envelope.envelope_root.startswith("crm-v21-source-envelope-s3/v1:")

    removed_edge_source = replace(context.source, hard_depends_on=())
    assert removed_edge_source.commitment == context.source.commitment
    changed_records = tuple(
        sorted(
            (context.target, removed_edge_source), key=lambda record: record.record_id
        )
    )
    changed_nodes = tuple(
        SourceGraphNodeV21(record.record_id, record.commitment)
        for record in changed_records
    )
    changed_edges: tuple[HardGraphEdgeV21, ...] = ()
    changed_hard_root = hard_graph_root_v21(
        graph_nodes=changed_nodes, hard_edges=changed_edges
    )
    changed_contributions = tuple(
        sorted(
            (_contribution(record) for record in changed_records),
            key=lambda row: row.record_id,
        )
    )
    changed_contribution_root = contribution_root_v21(changed_contributions)
    changed_envelope_root = source_envelope_root_v21(
        base_state_hash=context.base_state_hash,
        source_records=changed_records,
        incoming_record_ids=(context.source.record_id,),
        graph_nodes=changed_nodes,
        hard_edges=changed_edges,
        contribution_evidence=changed_contributions,
        hard_graph_root=changed_hard_root,
        contribution_root=changed_contribution_root,
    )
    assert changed_hard_root != envelope.hard_graph_root
    assert changed_envelope_root != envelope.envelope_root

    with pytest.raises(ValueError):
        SourceEnvelopeV21(
            base_state_hash=context.base_state_hash,
            source_records=changed_records,
            incoming_record_ids=(context.source.record_id,),
            graph_nodes=envelope.graph_nodes,
            hard_edges=envelope.hard_edges,
            contribution_evidence=changed_contributions,
            hard_graph_root=envelope.hard_graph_root,
            contribution_root=changed_contribution_root,
            envelope_root=envelope.envelope_root,
        )


def test_hard_edge_addition_and_retargeting_change_a_same_commitment_envelope() -> None:
    context = _envelope_context()
    extra_target = _source_record("extra-target")
    original_dependency = context.source.hard_depends_on[0]
    added_source = replace(
        context.source,
        hard_depends_on=tuple(
            sorted(
                (
                    original_dependency,
                    HardDependencyV21(
                        target_id=extra_target.record_id,
                        target_commitment=extra_target.commitment,
                    ),
                ),
                key=lambda dependency: dependency.target_id,
            )
        ),
    )
    retargeted_source = replace(
        context.source,
        hard_depends_on=(
            HardDependencyV21(
                target_id=extra_target.record_id,
                target_commitment=extra_target.commitment,
            ),
        ),
    )
    assert added_source.commitment == context.source.commitment
    assert retargeted_source.commitment == context.source.commitment

    for changed_source in (added_source, retargeted_source):
        records = tuple(
            sorted(
                (context.target, extra_target, changed_source),
                key=lambda record: record.record_id,
            )
        )
        nodes, edges = _graph_rows(records)
        hard_root = hard_graph_root_v21(graph_nodes=nodes, hard_edges=edges)
        contributions = tuple(
            sorted(
                (_contribution(record) for record in records),
                key=lambda row: row.record_id,
            )
        )
        envelope_root = source_envelope_root_v21(
            base_state_hash=context.base_state_hash,
            source_records=records,
            incoming_record_ids=(context.source.record_id,),
            graph_nodes=nodes,
            hard_edges=edges,
            contribution_evidence=contributions,
            hard_graph_root=hard_root,
            contribution_root=contribution_root_v21(contributions),
        )
        assert hard_root != context.envelope.hard_graph_root
        assert envelope_root != context.envelope.envelope_root


def test_envelope_rejects_unordered_duplicate_and_cross_domain_rows() -> None:
    context = _envelope_context()
    envelope = context.envelope
    with pytest.raises(ValueError):
        SourceEnvelopeV21(
            base_state_hash=envelope.base_state_hash,
            source_records=tuple(reversed(envelope.source_records)),
            incoming_record_ids=envelope.incoming_record_ids,
            graph_nodes=envelope.graph_nodes,
            hard_edges=envelope.hard_edges,
            contribution_evidence=envelope.contribution_evidence,
            hard_graph_root=envelope.hard_graph_root,
            contribution_root=envelope.contribution_root,
            envelope_root=envelope.envelope_root,
        )
    with pytest.raises(ValueError):
        hard_graph_root_v21(
            graph_nodes=envelope.graph_nodes + (envelope.graph_nodes[0],),
            hard_edges=envelope.hard_edges,
        )
    with pytest.raises(ValueError):
        contribution_root_v21(
            (
                replace(
                    envelope.contribution_evidence[0],
                    contribution_keys=(context.source_key, context.source_key),
                ),
            )
        )
    with pytest.raises(ValueError):
        source_envelope_root_v21(
            base_state_hash=envelope.base_state_hash,
            source_records=envelope.source_records,
            incoming_record_ids=envelope.incoming_record_ids,
            graph_nodes=envelope.graph_nodes,
            hard_edges=envelope.hard_edges,
            contribution_evidence=envelope.contribution_evidence,
            hard_graph_root=envelope.contribution_root,
            contribution_root=envelope.contribution_root,
        )


def test_hard_graph_helper_revalidates_every_runtime_mutated_row_field() -> None:
    context = _envelope_context()

    def assert_rejected_after_mutation(
        *,
        node_field: str,
        edge_field: str,
        invalid_value: str,
        source_side: bool,
    ) -> None:
        source_node = SourceGraphNodeV21(
            context.source.record_id, context.source.commitment
        )
        target_node = SourceGraphNodeV21(
            context.target.record_id, context.target.commitment
        )
        edge = HardGraphEdgeV21(
            source_id=context.source.record_id,
            source_commitment=context.source.commitment,
            target_id=context.target.record_id,
            target_commitment=context.target.commitment,
        )
        selected_node = source_node if source_side else target_node
        object.__setattr__(selected_node, node_field, invalid_value)
        object.__setattr__(edge, edge_field, invalid_value)
        nodes = tuple(
            sorted((source_node, target_node), key=lambda node: node.record_id)
        )
        with pytest.raises(ValueError):
            hard_graph_root_v21(graph_nodes=nodes, hard_edges=(edge,))

    assert_rejected_after_mutation(
        node_field="record_id",
        edge_field="source_id",
        invalid_value="invalid-source-id",
        source_side=True,
    )
    assert_rejected_after_mutation(
        node_field="source_commitment",
        edge_field="source_commitment",
        invalid_value="invalid-source-commitment",
        source_side=True,
    )
    assert_rejected_after_mutation(
        node_field="record_id",
        edge_field="target_id",
        invalid_value="invalid-target-id",
        source_side=False,
    )
    assert_rejected_after_mutation(
        node_field="source_commitment",
        edge_field="target_commitment",
        invalid_value="invalid-target-commitment",
        source_side=False,
    )


def test_contribution_vectors_and_transient_caps_fail_closed() -> None:
    context = _envelope_context()
    row = context.envelope.contribution_evidence[0]
    with pytest.raises(ValueError):
        replace(row, coverage_vector=(1,) * (MAX_EVIDENCED_COVERAGE_VECTOR_V21 + 1))
    with pytest.raises(ValueError):
        replace(row, bridge_vector=(0,) * (MAX_EVIDENCED_BRIDGE_VECTOR_V21 + 1))
    with pytest.raises(ValueError):
        replace(row, coverage_vector=(2,))
    with pytest.raises(ValueError):
        replace(row, bridge_vector=(True,))
    with pytest.raises(ValueError):
        replace(
            row,
            contribution_keys=(row.contribution_keys[0],)
            * (MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21 + 1),
        )
    with pytest.raises(ValueError):
        replace(
            row,
            representative_candidates=row.representative_candidates
            * (MAX_EVIDENCED_REPRESENTATIVE_CANDIDATES_V21 + 1),
        )
    with pytest.raises(ValueError):
        source_envelope_root_v21(
            base_state_hash=context.base_state_hash,
            source_records=(context.target,) * (MAX_EVIDENCED_SOURCE_RECORDS_V21 + 1),
            incoming_record_ids=(),
            graph_nodes=(),
            hard_edges=(),
            contribution_evidence=(),
            hard_graph_root=context.envelope.hard_graph_root,
            contribution_root=context.envelope.contribution_root,
        )


def test_source_body_and_aggregate_hard_edge_caps_fail_before_envelope_hashing() -> (
    None
):
    with pytest.raises(ValueError):
        _source_record(
            "body-too-large",
            body="x" * (MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21 + 1),
        )

    aggregate_records = tuple(
        sorted(
            (
                _source_record(
                    f"body-{index}",
                    body="y" * MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21,
                )
                for index in range(
                    MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21
                    // MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21
                    + 1
                )
            ),
            key=lambda record: record.record_id,
        )
    )
    with pytest.raises(ValueError):
        source_envelope_root_v21(
            base_state_hash=_digest(_BASE_STATE_DOMAIN, "a"),
            source_records=aggregate_records,
            incoming_record_ids=(),
            graph_nodes=(),
            hard_edges=(),
            contribution_evidence=(),
            hard_graph_root=_digest(_HARD_GRAPH_DOMAIN, "b"),
            contribution_root=_digest("crm-v21-fold-contributions-s3/v1", "c"),
        )

    shared_target = _digest("v21-source-commitment-s3", "d")
    dependencies = tuple(
        sorted(
            (
                HardDependencyV21(
                    target_id=record_identity_v21(_NAMESPACE, f"edge-{index}"),
                    target_commitment=shared_target,
                )
                for index in range(MAX_EVIDENCED_HARD_EDGES_V21)
            ),
            key=lambda dependency: dependency.target_id,
        )
    )
    aggregate_edges = tuple(
        sorted(
            (
                _source_record("edge-source-a", hard_depends_on=dependencies),
                _source_record("edge-source-b", hard_depends_on=(dependencies[0],)),
            ),
            key=lambda record: record.record_id,
        )
    )
    with pytest.raises(ValueError):
        source_envelope_root_v21(
            base_state_hash=_digest(_BASE_STATE_DOMAIN, "a"),
            source_records=aggregate_edges,
            incoming_record_ids=(),
            graph_nodes=(),
            hard_edges=(),
            contribution_evidence=(),
            hard_graph_root=_digest(_HARD_GRAPH_DOMAIN, "b"),
            contribution_root=_digest("crm-v21-fold-contributions-s3/v1", "c"),
        )


def test_canonical_envelope_cap_is_reachable_without_relaxing_raw_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_count = 96
    raw_body_bytes = record_count * MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21
    assert raw_body_bytes == 6_291_456
    assert raw_body_bytes < MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21

    ordinary_records = tuple(
        sorted(
            (
                _source_record(
                    f"ordinary-canonical-{index:03d}",
                    body="x" * MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21,
                )
                for index in range(record_count)
            ),
            key=lambda record: record.record_id,
        )
    )
    (
        ordinary_nodes,
        ordinary_edges,
        ordinary_contributions,
        ordinary_hard_root,
        ordinary_contribution_root,
    ) = _full_envelope_rows(ordinary_records)
    ordinary_root = source_envelope_root_v21(
        base_state_hash=_digest(_BASE_STATE_DOMAIN, "a"),
        source_records=ordinary_records,
        incoming_record_ids=(),
        graph_nodes=ordinary_nodes,
        hard_edges=ordinary_edges,
        contribution_evidence=ordinary_contributions,
        hard_graph_root=ordinary_hard_root,
        contribution_root=ordinary_contribution_root,
    )
    ordinary = _source_envelope_from_records(
        ordinary_records,
        envelope_root=ordinary_root,
    )
    assert ordinary.envelope_root == ordinary_root

    escaped_records = tuple(
        sorted(
            (
                _source_record(
                    f"escaped-canonical-{index:03d}",
                    body="\x00" * MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21,
                )
                for index in range(record_count)
            ),
            key=lambda record: record.record_id,
        )
    )
    (
        escaped_nodes,
        escaped_edges,
        escaped_contributions,
        escaped_hard_root,
        escaped_contribution_root,
    ) = _full_envelope_rows(escaped_records)
    original_domain_hash = reencoding_contracts_v21._domain_hash_v21
    envelope_root_calls: list[str] = []

    def spy_domain_hash(domain: str, value: object) -> str:
        if domain == _SOURCE_ENVELOPE_DOMAIN:
            envelope_root_calls.append(domain)
        return original_domain_hash(domain, value)

    monkeypatch.setattr(
        reencoding_contracts_v21,
        "_domain_hash_v21",
        spy_domain_hash,
    )
    with pytest.raises(ValueError, match="canonical envelope byte cap exceeded"):
        source_envelope_root_v21(
            base_state_hash=_digest(_BASE_STATE_DOMAIN, "a"),
            source_records=escaped_records,
            incoming_record_ids=(),
            graph_nodes=escaped_nodes,
            hard_edges=escaped_edges,
            contribution_evidence=escaped_contributions,
            hard_graph_root=escaped_hard_root,
            contribution_root=escaped_contribution_root,
        )
    assert envelope_root_calls == []


def test_source_envelope_root_requires_full_payload_rows() -> None:
    context = _envelope_context()
    compact_only_root = cast(Callable[..., str], source_envelope_root_v21)
    with pytest.raises(TypeError):
        compact_only_root(
            base_state_hash=context.base_state_hash,
            source_records=context.envelope.source_records,
            incoming_record_ids=context.envelope.incoming_record_ids,
            hard_graph_root=context.envelope.hard_graph_root,
            contribution_root=context.envelope.contribution_root,
        )


def test_source_envelope_root_rejects_mismatched_full_payload_rows() -> None:
    context = _envelope_context()
    with pytest.raises(ValueError):
        source_envelope_root_v21(
            base_state_hash=context.base_state_hash,
            source_records=context.envelope.source_records,
            incoming_record_ids=context.envelope.incoming_record_ids,
            graph_nodes=context.envelope.graph_nodes,
            hard_edges=(),
            contribution_evidence=context.envelope.contribution_evidence,
            hard_graph_root=context.envelope.hard_graph_root,
            contribution_root=context.envelope.contribution_root,
        )


def test_source_envelope_root_keeps_the_pre_amendment_compact_payload_hash() -> None:
    context = _envelope_context()
    assert (
        source_envelope_root_v21(
            base_state_hash=context.base_state_hash,
            source_records=context.envelope.source_records,
            incoming_record_ids=context.envelope.incoming_record_ids,
            graph_nodes=context.envelope.graph_nodes,
            hard_edges=context.envelope.hard_edges,
            contribution_evidence=context.envelope.contribution_evidence,
            hard_graph_root=context.envelope.hard_graph_root,
            contribution_root=context.envelope.contribution_root,
        )
        == "crm-v21-source-envelope-s3/v1:bd9698ec2294643be666979f2276071adae412d84d7084ecb3c2474f9834e6d2"
    )


def test_source_envelope_validates_roots_before_canonical_byte_measurement() -> None:
    context = _envelope_context()
    with pytest.raises(
        ValueError,
        match="envelope hard_graph_root must be a crm-v21-hard-graph-s3/v1 domain digest",
    ):
        SourceEnvelopeV21(
            base_state_hash=context.base_state_hash,
            source_records=context.envelope.source_records,
            incoming_record_ids=context.envelope.incoming_record_ids,
            graph_nodes=context.envelope.graph_nodes,
            hard_edges=context.envelope.hard_edges,
            contribution_evidence=context.envelope.contribution_evidence,
            hard_graph_root="x" * (MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21 + 1),
            contribution_root=context.envelope.contribution_root,
            envelope_root=context.envelope.envelope_root,
        )


def test_canonical_segment_hash_resolves_only_a_validated_current_segment() -> None:
    first_segment = _resident_segment(budget_used=64)
    first_state = _validated_state(first_segment)
    second_state = _validated_state(_resident_segment(budget_used=65))
    first_hash = canonical_segment_hash_v21(first_state, first_segment.segment_id)
    second_hash = canonical_segment_hash_v21(second_state, first_segment.segment_id)
    assert first_hash.startswith("crm-v21-canonical-segment-s3/v1:")
    assert first_hash != second_hash
    with pytest.raises(ValueError):
        canonical_segment_hash_v21(
            first_state, record_identity_v21(_NAMESPACE, "missing")
        )


def test_decision_roots_bind_context_rows_and_closed_child_rules() -> None:
    context = _envelope_context()
    record_decision, segment_decision, record_root, segment_root = _decision_roots(
        context
    )
    assert record_root.startswith(f"{_RECORD_DECISION_DOMAIN}:")
    assert segment_root.startswith(f"{_SEGMENT_DECISION_DOMAIN}:")
    policy_root = evidenced_reencoding_policy_root_v21(context.policy)
    assert (
        record_decision_root_v21(
            base_state_hash=_digest(_BASE_STATE_DOMAIN, "b"),
            source_envelope_root=context.envelope.envelope_root,
            policy_root=policy_root,
            rows=(record_decision,),
        )
        != record_root
    )
    assert (
        segment_decision_root_v21(
            base_state_hash=context.base_state_hash,
            source_envelope_root=context.envelope.envelope_root,
            policy_root=policy_root,
            rows=(replace(segment_decision, contribution_keys=()),),
        )
        != segment_root
    )
    alternate_child = segment_identity_v21(_NAMESPACE, "alternate-child")
    record_variants = (
        replace(record_decision, record_id=context.target.record_id),
        replace(record_decision, source_commitment=context.target.commitment),
        replace(
            record_decision,
            outcome=ReencodingOutcomeV21.EXACT,
            child_segment_id=None,
        ),
        replace(record_decision, child_segment_id=alternate_child),
        replace(record_decision, contribution_keys=()),
    )
    for variant in record_variants:
        assert (
            record_decision_root_v21(
                base_state_hash=context.base_state_hash,
                source_envelope_root=context.envelope.envelope_root,
                policy_root=policy_root,
                rows=(variant,),
            )
            != record_root
        )
    segment_variants = (
        replace(segment_decision, segment_id=alternate_child),
        replace(
            segment_decision,
            canonical_segment_hash=_digest("crm-v21-canonical-segment-s3/v1", "c"),
        ),
        replace(
            segment_decision,
            disposition=SegmentDispositionV21.RETAIN,
            child_segment_id=None,
        ),
        replace(segment_decision, child_segment_id=alternate_child),
        replace(segment_decision, contribution_keys=()),
    )
    for variant in segment_variants:
        assert (
            segment_decision_root_v21(
                base_state_hash=context.base_state_hash,
                source_envelope_root=context.envelope.envelope_root,
                policy_root=policy_root,
                rows=(variant,),
            )
            != segment_root
        )
    with pytest.raises(ValueError):
        RecordDecisionV21(
            record_id=context.source.record_id,
            source_commitment=context.source.commitment,
            outcome=ReencodingOutcomeV21.EXACT,
            child_segment_id=segment_identity_v21(_NAMESPACE, "forbidden"),
            contribution_keys=(),
        )
    with pytest.raises(ValueError):
        SegmentDecisionV21(
            segment_id=segment_decision.segment_id,
            canonical_segment_hash=segment_decision.canonical_segment_hash,
            disposition=SegmentDispositionV21.COMPACT,
            child_segment_id=segment_decision.segment_id,
            contribution_keys=(),
        )
    with pytest.raises(ValueError):
        record_decision_root_v21(
            base_state_hash=context.base_state_hash,
            source_envelope_root=context.envelope.envelope_root,
            policy_root=policy_root,
            rows=(record_decision,) * (MAX_EVIDENCED_ROOT_ROWS_V21 + 1),
        )


def test_first_fold_authorization_binds_its_structured_source_anchor() -> None:
    context = _envelope_context()
    policy_root = evidenced_reencoding_policy_root_v21(context.policy)
    source = FirstFoldSourceV21(
        record_id=context.source.record_id,
        source_commitment=context.source.commitment,
        contribution_keys=(context.source_key,),
    )
    child_segment_id = segment_identity_v21(_NAMESPACE, "first-fold-child")
    authorization_root = first_fold_authorization_root_v21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        namespace=_NAMESPACE,
        incarnation=1,
        generation=3,
        child_segment_id=child_segment_id,
        sources=(source,),
    )
    authorization = FirstFoldAuthorizationV21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        namespace=_NAMESPACE,
        incarnation=1,
        generation=3,
        child_segment_id=child_segment_id,
        sources=(source,),
        authorization_root=authorization_root,
    )
    assert authorization.authorization_root.startswith("crm-v21-first-fold-auth-s3/v1:")
    with pytest.raises(ValueError):
        replace(authorization, generation=4)
    with pytest.raises(ValueError):
        FirstFoldSourceV21(
            record_id=source.record_id,
            source_commitment=source.source_commitment,
            contribution_keys=(context.source_key, context.source_key),
        )


def test_seed_advance_chain_requires_an_external_wrapper_anchor() -> None:
    wrapper, barrier = _anchored_wrapper()
    expected_state_hash = evidenced_state_hash_v21(wrapper)
    head = seed_advance_chain_head_v21(
        wrapper,
        expected_state_hash=expected_state_hash,
    )
    assert head.state_hash == expected_state_hash
    assert head.previous_head_root is None
    assert head.head_root.startswith(f"{_ADVANCE_HEAD_DOMAIN}:")
    assert len(head.barrier_cursors) == 1
    barrier_cursor = head.barrier_cursors[0]
    assert barrier_cursor.kind is AdvanceCursorKindV21.SEED
    assert barrier_cursor.durable_root == barrier.cumulative_root
    assert barrier_cursor.high_water == barrier.folded_through_high_water
    assert barrier_cursor.transient_root.startswith(f"{_BARRIER_SEED_DOMAIN}:")
    assert head.ledger_cursor.kind is AdvanceCursorKindV21.SEED
    assert head.ledger_cursor.transient_root.startswith(f"{_LEDGER_SEED_DOMAIN}:")

    with pytest.raises(EvidencedStateMismatchV21):
        seed_advance_chain_head_v21(
            wrapper,
            expected_state_hash=_digest(_BASE_STATE_DOMAIN, "a"),
        )
    with pytest.raises(TypeError):
        seed_advance_chain_head_v21(
            cast(EvidencedCapsuleStateV21, wrapper.state),
            expected_state_hash=expected_state_hash,
        )
    with pytest.raises(ValueError):
        seed_advance_chain_head_v21(
            wrapper,
            expected_state_hash=_digest(_HARD_GRAPH_DOMAIN, "b"),
        )
    with pytest.raises(ValueError):
        BarrierAdvanceCursorV21(
            namespace=_NAMESPACE,
            incarnation=1,
            durable_root=barrier.cumulative_root,
            high_water=8,
            kind=AdvanceCursorKindV21.SEED,
            transient_root=_digest(_BARRIER_ADVANCE_DOMAIN, "c"),
        )
    with pytest.raises(ValueError):
        LedgerAdvanceCursorV21(
            durable_root=wrapper.state.loss_ledger.cumulative_loss_root,
            role_counts=wrapper.state.loss_ledger.role_counts,
            generation=wrapper.state.loss_ledger.last_fold_generation,
            kind=AdvanceCursorKindV21.SEED,
            transient_root=_digest(_BARRIER_SEED_DOMAIN, "d"),
        )

    tampered_seed = barrier_advance_seed_root_v21(
        base_state_hash=_digest(_BASE_STATE_DOMAIN, "e"),
        prior_advance_head_root=None,
        namespace=_NAMESPACE,
        incarnation=1,
        durable_root=barrier.cumulative_root,
        high_water=barrier.folded_through_high_water,
    )
    assert tampered_seed != barrier_cursor.transient_root
    tampered_cursor = replace(barrier_cursor, transient_root=tampered_seed)
    with pytest.raises(ValueError):
        AdvanceChainHeadV21(
            state_hash=head.state_hash,
            generation=head.generation,
            barrier_cursors=(tampered_cursor,),
            ledger_cursor=head.ledger_cursor,
            previous_head_root=None,
            head_root=advance_chain_head_root_v21(
                state_hash=head.state_hash,
                generation=head.generation,
                barrier_cursors=(tampered_cursor,),
                ledger_cursor=head.ledger_cursor,
                previous_head_root=None,
            ),
        )
    null_seed = barrier_advance_seed_root_v21(
        base_state_hash=expected_state_hash,
        prior_advance_head_root=None,
        namespace=_NAMESPACE,
        incarnation=1,
        durable_root=None,
        high_water=None,
    )
    assert null_seed.startswith(f"{_BARRIER_SEED_DOMAIN}:")
    assert barrier_cursor.durable_root is not None
    assert barrier_cursor.high_water is not None
    null_cursor = replace(
        barrier_cursor,
        durable_root=None,
        high_water=None,
        transient_root=null_seed,
    )
    with pytest.raises(ValueError):
        AdvanceChainHeadV21(
            state_hash=head.state_hash,
            generation=head.generation,
            barrier_cursors=(null_cursor,),
            ledger_cursor=head.ledger_cursor,
            previous_head_root=None,
            head_root=advance_chain_head_root_v21(
                state_hash=head.state_hash,
                generation=head.generation,
                barrier_cursors=(null_cursor,),
                ledger_cursor=head.ledger_cursor,
                previous_head_root=None,
            ),
        )


def test_anchored_advances_projection_and_successor_head_bind_every_link() -> None:
    context = _envelope_context()
    wrapper, _ = _anchored_wrapper()
    head = seed_advance_chain_head_v21(
        wrapper,
        expected_state_hash=evidenced_state_hash_v21(wrapper),
    )
    record_decision, segment_decision, record_root, segment_root = _decision_roots(
        context
    )
    policy_root = evidenced_reencoding_policy_root_v21(context.policy)
    released = ReleasedSourceV21(
        record_id=context.source.record_id,
        source_commitment=context.source.commitment,
    )
    prior_barrier_cursor = head.barrier_cursors[0]
    assert prior_barrier_cursor.durable_root is not None
    barrier_root = barrier_advance_root_v21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=head.head_root,
        prior_cursor=prior_barrier_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=8,
        new_high_water=10,
        generation=4,
        released_sources=(released,),
    )
    new_barrier_durable_root = barrier_durable_projection_root_v21(
        previous_durable_root=prior_barrier_cursor.durable_root,
        barrier_advance_root=barrier_root,
        namespace=_NAMESPACE,
        incarnation=1,
        new_high_water=10,
        generation=4,
    )
    barrier_advance = BarrierAdvanceV21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        prior_advance_head_root=head.head_root,
        prior_cursor=prior_barrier_cursor,
        namespace=_NAMESPACE,
        incarnation=1,
        previous_high_water=8,
        new_high_water=10,
        generation=4,
        released_sources=(released,),
        new_durable_root=new_barrier_durable_root,
        new_root=barrier_root,
    )
    assert barrier_advance.new_root.startswith(f"{_BARRIER_ADVANCE_DOMAIN}:")
    assert barrier_advance.new_durable_root.startswith(f"{_DURABLE_BARRIER_DOMAIN}:")
    with pytest.raises(ValueError):
        barrier_advance_root_v21(
            base_state_hash=context.base_state_hash,
            source_envelope_root=context.envelope.envelope_root,
            policy_root=policy_root,
            record_decision_root=record_root,
            segment_decision_root=segment_root,
            prior_advance_head_root=prior_barrier_cursor.durable_root,
            prior_cursor=prior_barrier_cursor,
            namespace=_NAMESPACE,
            incarnation=1,
            previous_high_water=8,
            new_high_water=10,
            generation=4,
            released_sources=(released,),
        )
    with pytest.raises(ValueError):
        replace(
            barrier_advance,
            prior_cursor=replace(prior_barrier_cursor, high_water=7),
        )
    with pytest.raises(ValueError):
        replace(
            barrier_advance,
            new_durable_root=prior_barrier_cursor.durable_root,
        )

    prior_ledger_cursor = head.ledger_cursor
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_advance.new_root,),
        prior_advance_head_root=head.head_root,
        prior_cursor=prior_ledger_cursor,
        previous_role_counts=((CapsuleRoleV21.CONTEXT, 1),),
        delta_role_counts=((CapsuleRoleV21.CONTEXT, 2),),
        new_role_counts=((CapsuleRoleV21.CONTEXT, 3),),
        previous_generation=2,
        new_generation=4,
        loss_units_delta=2,
    )
    new_ledger_durable_root = ledger_durable_projection_root_v21(
        previous_durable_root=prior_ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=((CapsuleRoleV21.CONTEXT, 3),),
        new_generation=4,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=(barrier_advance.new_root,),
        prior_advance_head_root=head.head_root,
        prior_cursor=prior_ledger_cursor,
        previous_role_counts=((CapsuleRoleV21.CONTEXT, 1),),
        delta_role_counts=((CapsuleRoleV21.CONTEXT, 2),),
        new_role_counts=((CapsuleRoleV21.CONTEXT, 3),),
        previous_generation=2,
        new_generation=4,
        loss_units_delta=2,
        new_durable_root=new_ledger_durable_root,
        new_root=ledger_root,
    )
    assert ledger_advance.new_root.startswith(f"{_LEDGER_ADVANCE_DOMAIN}:")
    assert ledger_advance.new_durable_root.startswith(f"{_DURABLE_LEDGER_DOMAIN}:")
    with pytest.raises(ValueError):
        replace(ledger_advance, new_durable_root=prior_ledger_cursor.durable_root)
    with pytest.raises(ValueError):
        loss_ledger_advance_root_v21(
            base_state_hash=context.base_state_hash,
            source_envelope_root=context.envelope.envelope_root,
            policy_root=policy_root,
            record_decision_root=record_root,
            segment_decision_root=segment_root,
            barrier_roots=(barrier_advance.new_root,),
            prior_advance_head_root=head.head_root,
            prior_cursor=prior_ledger_cursor,
            previous_role_counts=((CapsuleRoleV21.CONTEXT, 1),),
            delta_role_counts=((CapsuleRoleV21.CONTEXT, 2),),
            new_role_counts=((CapsuleRoleV21.CONTEXT, 3),),
            previous_generation=2,
            new_generation=4,
            loss_units_delta=MAX_EVIDENCED_LOSS_UNITS_V21 + 1,
        )

    next_barrier_cursor = BarrierAdvanceCursorV21(
        namespace=_NAMESPACE,
        incarnation=1,
        durable_root=barrier_advance.new_durable_root,
        high_water=10,
        kind=AdvanceCursorKindV21.ADVANCE,
        transient_root=barrier_advance.new_root,
    )
    next_ledger_cursor = LedgerAdvanceCursorV21(
        durable_root=ledger_advance.new_durable_root,
        role_counts=ledger_advance.new_role_counts,
        generation=4,
        kind=AdvanceCursorKindV21.ADVANCE,
        transient_root=ledger_advance.new_root,
    )
    next_head = next_advance_chain_head_v21(
        head,
        proposed_state_hash=_digest(_BASE_STATE_DOMAIN, "f"),
        target_generation=4,
        barrier_cursors=(next_barrier_cursor,),
        ledger_cursor=next_ledger_cursor,
    )
    assert next_head.previous_head_root == head.head_root
    assert next_head.head_root.startswith(f"{_ADVANCE_HEAD_DOMAIN}:")
    with pytest.raises(TypeError):
        next_advance_chain_head_v21(
            cast(AdvanceChainHeadV21, None),
            proposed_state_hash=_digest(_BASE_STATE_DOMAIN, "f"),
            target_generation=4,
            barrier_cursors=(next_barrier_cursor,),
            ledger_cursor=next_ledger_cursor,
        )
    with pytest.raises(ValueError):
        next_advance_chain_head_v21(
            head,
            proposed_state_hash=_digest(_MATRIX_DOMAIN, "f"),
            target_generation=4,
            barrier_cursors=(next_barrier_cursor,),
            ledger_cursor=next_ledger_cursor,
        )
    with pytest.raises(ValueError):
        next_advance_chain_head_v21(
            head,
            proposed_state_hash=_digest(_BASE_STATE_DOMAIN, "f"),
            target_generation=2,
            barrier_cursors=(),
            ledger_cursor=next_ledger_cursor,
        )

    transition_root = evidenced_transition_root_v21(
        base_state_hash=context.base_state_hash,
        source_envelope_root=context.envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=_digest(_MATRIX_DOMAIN, "e"),
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=(),
        barrier_roots=(barrier_advance.new_root,),
        ledger_root=ledger_advance.new_root,
        proposed_hash=_digest(_BASE_STATE_DOMAIN, "f"),
        target_generation=4,
        target_high_water=10,
    )
    assert transition_root.startswith("crm-v21-evidenced-transition-s3/v1:")
