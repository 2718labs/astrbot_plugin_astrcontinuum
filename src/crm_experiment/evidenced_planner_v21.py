"""Pure deterministic planning for one fully evidenced V21 reencoding.

This module plans only finite, already-attested state/envelope/policy inputs.
It performs no I/O, history lookup, solver call, model call, storage write, or
CAS operation.  The one external authority is the public V21-005 verifier,
which is invoked exactly once for the selected, fully materialised candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from itertools import combinations
from math import comb
from typing import Final, NoReturn

from crm_experiment.contracts_v21 import (
    CapsuleRepresentativeV21,
    CapsuleRoleV21,
    CapsuleSegmentV21,
    ExactRecordV21,
    FoldBarrierV21,
    LossClassV21,
    LossLedgerV21,
    canonical_json_v21,
    fold_policy_hash_v21,
    folded_commitment_root_v21,
    segment_identity_v21,
    segment_input_commitment_root_v21,
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
    evidenced_resident_layout_v21,
    evidenced_state_hash_v21,
)
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
    AdvanceChainHeadV21,
    AdvanceCursorKindV21,
    BarrierAdvanceCursorV21,
    BarrierAdvanceV21,
    EvidencedReencodingPolicyV21,
    FirstFoldAuthorizationV21,
    FirstFoldSourceV21,
    FoldContributionEvidenceV21,
    LossLedgerAdvanceV21,
    RecordDecisionV21,
    ReencodingOutcomeV21,
    ReleasedSourceV21,
    SegmentDecisionV21,
    SegmentDispositionV21,
    SolverModeV21,
    SourceEnvelopeV21,
    SourceRecordV21,
    barrier_advance_root_v21,
    barrier_advance_seed_root_v21,
    barrier_durable_projection_root_v21,
    canonical_segment_hash_v21,
    evidenced_reencoding_policy_root_v21,
    evidenced_transition_root_v21,
    first_fold_authorization_root_v21,
    ledger_durable_projection_root_v21,
    loss_ledger_advance_root_v21,
    record_decision_root_v21,
    segment_decision_root_v21,
)

_ACTION_ROOT_DOMAIN_V21: Final = "crm-v21-evidenced-action-s3/v1"


class EvidencedPlanningRejectedV21(ValueError):
    """A bounded V21 plan has no safe, verified candidate to return."""


class _PlanningActionKindV21(StrEnum):
    ROOTLESS_SEGMENT = "ROOTLESS_SEGMENT"
    PARENT_COMPACT = "PARENT_COMPACT"


@dataclass(frozen=True, slots=True)
class _PlanningActionV21:
    action_id: str
    kind: _PlanningActionKindV21
    namespace: str
    incarnation: int
    source_ids: tuple[str, ...]
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class _MaterializedCandidateV21:
    matrix_plan: EvidencedMatrixPlanV21
    candidate: EvidencedReencodingCandidateV21
    action_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _MaterializationInputsV21:
    base: EvidencedCapsuleStateV21
    current_head: AdvanceChainHeadV21
    envelope: SourceEnvelopeV21
    policy: EvidencedReencodingPolicyV21
    base_hash: str
    policy_root: str


@dataclass(frozen=True, slots=True)
class EvidencedPlannedCandidateV21:
    """One verified V21 matrix plan and its transient candidate witness."""

    matrix_plan: EvidencedMatrixPlanV21
    candidate: EvidencedReencodingCandidateV21
    verification: EvidencedReencodingResultV21


def _domain_hash_v21(domain: str, value: object) -> str:
    digest = sha256(
        canonical_json_v21({"domain": domain, "value": value}).encode("utf-8")
    ).hexdigest()
    return f"{domain}:{digest}"


def _reject(message: str) -> NoReturn:
    raise EvidencedPlanningRejectedV21(message)


def _source_to_exact_v21(source: SourceRecordV21) -> ExactRecordV21:
    return ExactRecordV21(
        record_id=source.record_id,
        namespace=source.namespace,
        incarnation=source.incarnation,
        as_of=source.as_of,
        commitment=source.commitment,
        body=source.body,
        core_required=source.core_required,
        active=source.active,
        hard_depends_on=source.hard_depends_on,
    )


def _role_counts_from_rows_v21(
    rows: tuple[FoldContributionEvidenceV21, ...],
) -> tuple[tuple[CapsuleRoleV21, int], ...]:
    counts: dict[CapsuleRoleV21, int] = {}
    for row in rows:
        counts[row.role] = counts.get(row.role, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: item[0].value))


def _canonical_representatives_v21(
    rows: tuple[FoldContributionEvidenceV21, ...],
    *,
    maximum: int,
) -> tuple[CapsuleRepresentativeV21, ...]:
    by_id: dict[str, CapsuleRepresentativeV21] = {}
    for row in rows:
        for representative in row.representative_candidates:
            current = by_id.get(representative.feature_id)
            if current is None or canonical_json_v21(
                representative
            ) < canonical_json_v21(current):
                by_id[representative.feature_id] = representative
    representatives = tuple(by_id[key] for key in sorted(by_id))
    if len(representatives) > maximum:
        _reject("rootless child representative bound would be exceeded")
    return representatives


def _aggregate_rootless_vectors_v21(
    rows: tuple[FoldContributionEvidenceV21, ...],
    *,
    coverage_width: int,
    bridge_width: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if any(
        len(row.coverage_vector) > coverage_width
        or len(row.bridge_vector) > bridge_width
        for row in rows
    ):
        _reject("source evidence vector exceeds frozen state bounds")
    coverage = tuple(
        1
        if any(
            index < len(row.coverage_vector) and row.coverage_vector[index]
            for row in rows
        )
        else 0
        for index in range(coverage_width)
    )
    bridge = tuple(
        max(
            row.bridge_vector[index] if index < len(row.bridge_vector) else 0
            for row in rows
        )
        for index in range(bridge_width)
    )
    return coverage, bridge


def _action_id_v21(
    *,
    base_hash: str,
    envelope_root: str,
    policy_root: str,
    kind: _PlanningActionKindV21,
    namespace: str,
    incarnation: int,
    source_ids: tuple[str, ...],
    parent_id: str | None,
) -> str:
    return _domain_hash_v21(
        _ACTION_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_envelope_root": envelope_root,
            "policy_root": policy_root,
            "kind": kind,
            "namespace": namespace,
            "incarnation": incarnation,
            "source_ids": source_ids,
            "parent_id": parent_id,
        },
    )


def _mandatory_source_ids_v21(envelope: SourceEnvelopeV21) -> set[str]:
    mandatory = {
        source.record_id for source in envelope.source_records if source.core_required
    }
    for edge in envelope.hard_edges:
        mandatory.add(edge.source_id)
        mandatory.add(edge.target_id)
    return mandatory


def _canonical_actions_v21(
    *,
    base: EvidencedCapsuleStateV21,
    envelope: SourceEnvelopeV21,
    policy: EvidencedReencodingPolicyV21,
    base_hash: str,
    policy_root: str,
) -> tuple[_PlanningActionV21, ...]:
    """Build finite rootless and one-parent action atoms in canonical order."""
    mandatory = _mandatory_source_ids_v21(envelope)
    groups: dict[tuple[str, int], list[str]] = {}
    for source in envelope.source_records:
        if source.record_id not in mandatory:
            groups.setdefault((source.namespace, source.incarnation), []).append(
                source.record_id
            )
    actions: list[_PlanningActionV21] = []
    for (namespace, incarnation), source_ids in sorted(groups.items()):
        ordered_ids = tuple(sorted(source_ids))
        action_id = _action_id_v21(
            base_hash=base_hash,
            envelope_root=envelope.envelope_root,
            policy_root=policy_root,
            kind=_PlanningActionKindV21.ROOTLESS_SEGMENT,
            namespace=namespace,
            incarnation=incarnation,
            source_ids=ordered_ids,
            parent_id=None,
        )
        actions.append(
            _PlanningActionV21(
                action_id=action_id,
                kind=_PlanningActionKindV21.ROOTLESS_SEGMENT,
                namespace=namespace,
                incarnation=incarnation,
                source_ids=ordered_ids,
                parent_id=None,
            )
        )
    for parent in sorted(base.state.capsule_segments, key=lambda row: row.segment_id):
        action_id = _action_id_v21(
            base_hash=base_hash,
            envelope_root=envelope.envelope_root,
            policy_root=policy_root,
            kind=_PlanningActionKindV21.PARENT_COMPACT,
            namespace=parent.namespace,
            incarnation=0,
            source_ids=(),
            parent_id=parent.segment_id,
        )
        actions.append(
            _PlanningActionV21(
                action_id=action_id,
                kind=_PlanningActionKindV21.PARENT_COMPACT,
                namespace=parent.namespace,
                incarnation=0,
                source_ids=(),
                parent_id=parent.segment_id,
            )
        )
    return tuple(sorted(actions, key=lambda action: action.action_id))


def _selection_count_v21(action_count: int, max_new_segments: int) -> int:
    return sum(
        comb(action_count, size)
        for size in range(min(action_count, max_new_segments) + 1)
    )


def _build_children_v21(
    *,
    actions: tuple[_PlanningActionV21, ...],
    base: EvidencedCapsuleStateV21,
    envelope: SourceEnvelopeV21,
    target_generation: int,
) -> tuple[
    dict[str, CapsuleSegmentV21],
    dict[str, CapsuleSegmentV21],
    dict[str, CapsuleSegmentV21],
]:
    """Return children keyed by action, source id, and compacted parent id."""
    source_by_id = {source.record_id: source for source in envelope.source_records}
    evidence_by_id = {row.record_id: row for row in envelope.contribution_evidence}
    parent_by_id = {
        segment.segment_id: segment for segment in base.state.capsule_segments
    }
    children_by_action: dict[str, CapsuleSegmentV21] = {}
    child_by_source: dict[str, CapsuleSegmentV21] = {}
    child_by_parent: dict[str, CapsuleSegmentV21] = {}
    base_segment_ids = set(parent_by_id)
    for action in actions:
        if action.kind is _PlanningActionKindV21.ROOTLESS_SEGMENT:
            rows = tuple(evidence_by_id[source_id] for source_id in action.source_ids)
            coverage, bridge = _aggregate_rootless_vectors_v21(
                rows,
                coverage_width=base.state.bounds.coverage_vector_size,
                bridge_width=base.state.bounds.bridge_vector_size,
            )
            child_id = segment_identity_v21(
                action.namespace, f"v21-matrix-rootless/{action.action_id}"
            )
            fold_policy = fold_policy_hash_v21(
                f"v21-matrix-rootless/{action.action_id}"
            )
            roles = _role_counts_from_rows_v21(rows)
            representatives = _canonical_representatives_v21(
                rows, maximum=base.state.bounds.max_representatives_per_segment
            )
            budget_used = sum(
                len(source_by_id[source_id].body.encode("utf-8"))
                for source_id in action.source_ids
            )
            folded = folded_commitment_root_v21(
                segment_id=child_id,
                namespace=action.namespace,
                generation_start=target_generation,
                generation_end=target_generation,
                source_count=len(action.source_ids),
                role_counts=roles,
                representatives=representatives,
                coverage_vector=coverage,
                bridge_vector=bridge,
                loss_class=LossClassV21.BOUNDED_FOLD,
                budget_used=budget_used,
                fold_policy_hash=fold_policy,
            )
            child = CapsuleSegmentV21(
                segment_id=child_id,
                parent_ids=(),
                input_commitment_root=segment_input_commitment_root_v21(
                    (),
                    folded_commitment_root=folded,
                    generation_start=target_generation,
                    generation_end=target_generation,
                    fold_policy_hash=fold_policy,
                ),
                folded_commitment_root=folded,
                namespace=action.namespace,
                generation_start=target_generation,
                generation_end=target_generation,
                source_count=len(action.source_ids),
                role_counts=roles,
                representatives=representatives,
                coverage_vector=coverage,
                bridge_vector=bridge,
                loss_class=LossClassV21.BOUNDED_FOLD,
                budget_used=budget_used,
                fold_policy_hash=fold_policy,
            )
            for source_id in action.source_ids:
                child_by_source[source_id] = child
        else:
            if action.parent_id is None:
                _reject("parent compaction action omitted its parent")
            parent = parent_by_id[action.parent_id]
            child_id = segment_identity_v21(
                parent.namespace, f"v21-matrix-compact/{action.action_id}"
            )
            fold_policy = fold_policy_hash_v21(f"v21-matrix-compact/{action.action_id}")
            folded = folded_commitment_root_v21(
                segment_id=child_id,
                namespace=parent.namespace,
                generation_start=target_generation,
                generation_end=target_generation,
                source_count=parent.source_count,
                role_counts=parent.role_counts,
                representatives=parent.representatives,
                coverage_vector=parent.coverage_vector,
                bridge_vector=parent.bridge_vector,
                loss_class=parent.loss_class,
                budget_used=parent.budget_used,
                fold_policy_hash=fold_policy,
            )
            child = CapsuleSegmentV21(
                segment_id=child_id,
                parent_ids=(parent.segment_id,),
                input_commitment_root=segment_input_commitment_root_v21(
                    (parent,),
                    folded_commitment_root=folded,
                    generation_start=target_generation,
                    generation_end=target_generation,
                    fold_policy_hash=fold_policy,
                ),
                folded_commitment_root=folded,
                namespace=parent.namespace,
                generation_start=target_generation,
                generation_end=target_generation,
                source_count=parent.source_count,
                role_counts=parent.role_counts,
                representatives=parent.representatives,
                coverage_vector=parent.coverage_vector,
                bridge_vector=parent.bridge_vector,
                loss_class=parent.loss_class,
                budget_used=parent.budget_used,
                fold_policy_hash=fold_policy,
            )
            child_by_parent[parent.segment_id] = child
        if child.segment_id in base_segment_ids:
            _reject("deterministic child id collides with a current segment")
        children_by_action[action.action_id] = child
    return children_by_action, child_by_source, child_by_parent


def _role_delta_v21(
    *,
    actions: tuple[_PlanningActionV21, ...],
    evidence_by_id: dict[str, FoldContributionEvidenceV21],
    parent_by_id: dict[str, CapsuleSegmentV21],
    policy: EvidencedReencodingPolicyV21,
) -> tuple[tuple[tuple[CapsuleRoleV21, int], ...], int]:
    counts: dict[CapsuleRoleV21, int] = {}
    loss_units = 0
    for action in actions:
        if action.kind is _PlanningActionKindV21.ROOTLESS_SEGMENT:
            for source_id in action.source_ids:
                role = evidence_by_id[source_id].role
                counts[role] = counts.get(role, 0) + 1
                loss_units += policy.segment_loss_units
        else:
            if action.parent_id is None:
                _reject("parent compaction action omitted its parent")
            for role, count in parent_by_id[action.parent_id].role_counts:
                counts[role] = counts.get(role, 0) + count
            loss_units += policy.compaction_loss_units
    return tuple(sorted(counts.items(), key=lambda item: item[0].value)), loss_units


def _build_matrix_plan_v21(
    *,
    base: EvidencedCapsuleStateV21,
    envelope: SourceEnvelopeV21,
    policy: EvidencedReencodingPolicyV21,
    base_hash: str,
    policy_root: str,
    record_decisions: tuple[RecordDecisionV21, ...],
    segment_decisions: tuple[SegmentDecisionV21, ...],
    children_by_action: dict[str, CapsuleSegmentV21],
    actions: tuple[_PlanningActionV21, ...],
    proposed_hash: str,
    target_generation: int,
    target_high_water: int,
    evaluation_count: int,
    objective: MatrixObjectiveV21,
) -> EvidencedMatrixPlanV21:
    evidence_by_id = {row.record_id: row for row in envelope.contribution_evidence}
    source_axes = tuple(
        MatrixSourceAxisV21(
            record_id=source.record_id,
            commitment=source.commitment,
            role=evidence_by_id[source.record_id].role,
            core_required=source.core_required,
            contribution_keys=evidence_by_id[source.record_id].contribution_keys,
        )
        for source in envelope.source_records
    )
    source_rows = {axis.record_id: index for index, axis in enumerate(source_axes)}
    base_segment_keys = {
        segment.segment_id: tuple(
            locator.contribution_key
            for locator in base.contribution_index
            if locator.support is ContributionSupportV21.SEGMENT
            and locator.resident_id == segment.segment_id
        )
        for segment in base.state.capsule_segments
    }
    segment_axes = tuple(
        MatrixSegmentAxisV21(
            segment_id=segment.segment_id,
            canonical_hash=canonical_segment_hash_v21(base.state, segment.segment_id),
            role_counts=segment.role_counts,
            contribution_keys=base_segment_keys[segment.segment_id],
        )
        for segment in sorted(
            base.state.capsule_segments, key=lambda row: row.segment_id
        )
    )
    segment_rows = {axis.segment_id: index for index, axis in enumerate(segment_axes)}
    actions_by_child = {
        child.segment_id: action
        for action in actions
        for child in (children_by_action[action.action_id],)
    }

    def parent_rows_for_child(child_id: str) -> tuple[int, ...]:
        parent_id = actions_by_child[child_id].parent_id
        return () if parent_id is None else (segment_rows[parent_id],)

    child_axes = tuple(
        MatrixChildAxisV21(
            child_id=child_id,
            kind=(
                MatrixChildKindV21.ROOTLESS_SEGMENT
                if actions_by_child[child_id].kind
                is _PlanningActionKindV21.ROOTLESS_SEGMENT
                else MatrixChildKindV21.PARENT_COMPACT
            ),
            source_rows=tuple(
                source_rows[source_id]
                for source_id in actions_by_child[child_id].source_ids
            ),
            parent_rows=parent_rows_for_child(child_id),
        )
        for child_id in sorted(actions_by_child)
    )
    child_rows = {axis.child_id: index for index, axis in enumerate(child_axes)}
    h_edges = tuple(
        sorted(
            MatrixDirectedEdgeV21(
                source_row=source_rows[edge.source_id],
                target_row=source_rows[edge.target_id],
            )
            for edge in envelope.hard_edges
        )
    )
    mandatory_ids = _mandatory_source_ids_v21(envelope)
    mandatory_rows = tuple(
        sorted(source_rows[record_id] for record_id in mandatory_ids)
    )
    f_width = max(
        (len(row.coverage_vector) for row in envelope.contribution_evidence), default=0
    )
    g_width = max(
        (len(row.bridge_vector) for row in envelope.contribution_evidence), default=0
    )
    f_rows = tuple(
        SparseBinaryCellV21(row_index=index, column_index=column)
        for index, source in enumerate(envelope.source_records)
        for column, bit in enumerate(evidence_by_id[source.record_id].coverage_vector)
        if bit == 1
    )
    g_rows = tuple(
        SparseBinaryCellV21(row_index=index, column_index=column)
        for index, source in enumerate(envelope.source_records)
        for column, bit in enumerate(evidence_by_id[source.record_id].bridge_vector)
        if bit == 1
    )
    p_edges = tuple(
        sorted(
            MatrixParentChildEdgeV21(parent_row=parent_row, child_row=child_row)
            for child_row, child in enumerate(child_axes)
            for parent_row in child.parent_rows
        )
    )
    records_by_id = {row.record_id: row for row in record_decisions}
    segments_by_id = {row.segment_id: row for row in segment_decisions}

    def child_row_for_record(decision: RecordDecisionV21) -> int | None:
        return (
            None
            if decision.child_segment_id is None
            else child_rows[decision.child_segment_id]
        )

    def child_row_for_segment(decision: SegmentDecisionV21) -> int | None:
        return (
            None
            if decision.child_segment_id is None
            else child_rows[decision.child_segment_id]
        )

    x_rows = tuple(
        MatrixDecisionXV21(
            axis=MatrixDecisionAxisV21.SOURCE,
            row_index=index,
            record_outcome=records_by_id[source.record_id].outcome,
            segment_disposition=None,
            child_row=child_row_for_record(records_by_id[source.record_id]),
        )
        for index, source in enumerate(envelope.source_records)
    ) + tuple(
        MatrixDecisionXV21(
            axis=MatrixDecisionAxisV21.SEGMENT,
            row_index=index,
            record_outcome=None,
            segment_disposition=segments_by_id[axis.segment_id].disposition,
            child_row=child_row_for_segment(segments_by_id[axis.segment_id]),
        )
        for index, axis in enumerate(segment_axes)
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
    root = evidenced_matrix_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        solver_mode=policy.solver_mode,
        source_axes=source_axes,
        segment_axes=segment_axes,
        child_axes=child_axes,
        h_edges=h_edges,
        mandatory_rows=mandatory_rows,
        f_rows=f_rows,
        g_rows=g_rows,
        p_edges=p_edges,
        x_rows=x_rows,
        h_width=len(source_axes),
        m_width=len(source_axes),
        f_width=f_width,
        g_width=g_width,
        p_width=len(child_axes),
        x_width=len(source_axes) + len(segment_axes),
        evaluation_count=evaluation_count,
        objective=objective,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        proposed_hash=proposed_hash,
        target_generation=target_generation,
        target_high_water=target_high_water,
    )
    return EvidencedMatrixPlanV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        solver_mode=policy.solver_mode,
        source_axes=source_axes,
        segment_axes=segment_axes,
        child_axes=child_axes,
        h_edges=h_edges,
        mandatory_rows=mandatory_rows,
        f_rows=f_rows,
        g_rows=g_rows,
        p_edges=p_edges,
        x_rows=x_rows,
        h_width=len(source_axes),
        m_width=len(source_axes),
        f_width=f_width,
        g_width=g_width,
        p_width=len(child_axes),
        x_width=len(source_axes) + len(segment_axes),
        evaluation_count=evaluation_count,
        objective=objective,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        proposed_hash=proposed_hash,
        target_generation=target_generation,
        target_high_water=target_high_water,
        matrix_root=root,
    )


def _materialize_candidate_v21(
    *,
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    envelope: SourceEnvelopeV21,
    policy: EvidencedReencodingPolicyV21,
    base_hash: str,
    policy_root: str,
    actions: tuple[_PlanningActionV21, ...],
    evaluation_count: int,
) -> _MaterializedCandidateV21:
    """Build every concrete V21 candidate field before the oracle is called."""
    if len(actions) > policy.max_new_segments:
        _reject("new segment count exceeds policy")
    target_generation = base.state.frame.generation + 1
    target_high_water = max(
        base.state.frame.high_water,
        *(source.as_of for source in envelope.source_records),
    )
    evidence_by_id = {row.record_id: row for row in envelope.contribution_evidence}
    parent_by_id = {
        segment.segment_id: segment for segment in base.state.capsule_segments
    }
    children_by_action, child_by_source, child_by_parent = _build_children_v21(
        actions=actions,
        base=base,
        envelope=envelope,
        target_generation=target_generation,
    )
    mandatory = _mandatory_source_ids_v21(envelope)
    if mandatory.intersection(child_by_source):
        _reject("a core or hard-closure source cannot be folded")
    record_decisions = tuple(
        RecordDecisionV21(
            record_id=source.record_id,
            source_commitment=source.commitment,
            outcome=(
                ReencodingOutcomeV21.SEGMENT
                if source.record_id in child_by_source
                else ReencodingOutcomeV21.EXACT
            ),
            child_segment_id=(
                None
                if source.record_id not in child_by_source
                else child_by_source[source.record_id].segment_id
            ),
            contribution_keys=evidence_by_id[source.record_id].contribution_keys,
        )
        for source in envelope.source_records
    )
    segment_decisions = tuple(
        SegmentDecisionV21(
            segment_id=segment.segment_id,
            canonical_segment_hash=canonical_segment_hash_v21(
                base.state, segment.segment_id
            ),
            disposition=(
                SegmentDispositionV21.COMPACT
                if segment.segment_id in child_by_parent
                else SegmentDispositionV21.RETAIN
            ),
            child_segment_id=(
                None
                if segment.segment_id not in child_by_parent
                else child_by_parent[segment.segment_id].segment_id
            ),
            contribution_keys=tuple(
                locator.contribution_key
                for locator in base.contribution_index
                if locator.support is ContributionSupportV21.SEGMENT
                and locator.resident_id == segment.segment_id
            ),
        )
        for segment in sorted(
            base.state.capsule_segments, key=lambda row: row.segment_id
        )
    )
    if len(record_decisions) + len(segment_decisions) > policy.max_decisions:
        _reject("decision count exceeds policy")
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
    authorizations: list[FirstFoldAuthorizationV21] = []
    for action in actions:
        if action.kind is not _PlanningActionKindV21.ROOTLESS_SEGMENT:
            continue
        child = children_by_action[action.action_id]
        sources = tuple(
            FirstFoldSourceV21(
                record_id=source_id,
                source_commitment=next(
                    source.commitment
                    for source in envelope.source_records
                    if source.record_id == source_id
                ),
                contribution_keys=evidence_by_id[source_id].contribution_keys,
            )
            for source_id in action.source_ids
        )
        authorization_root = first_fold_authorization_root_v21(
            base_state_hash=base_hash,
            source_envelope_root=envelope.envelope_root,
            policy_root=policy_root,
            namespace=action.namespace,
            incarnation=action.incarnation,
            generation=target_generation,
            child_segment_id=child.segment_id,
            sources=sources,
        )
        authorizations.append(
            FirstFoldAuthorizationV21(
                base_state_hash=base_hash,
                source_envelope_root=envelope.envelope_root,
                policy_root=policy_root,
                namespace=action.namespace,
                incarnation=action.incarnation,
                generation=target_generation,
                child_segment_id=child.segment_id,
                sources=sources,
                authorization_root=authorization_root,
            )
        )
    authorizations_tuple = tuple(
        sorted(authorizations, key=lambda authorization: authorization.child_segment_id)
    )
    authorization_roots = tuple(
        sorted(
            authorization.authorization_root for authorization in authorizations_tuple
        )
    )
    source_by_id = {source.record_id: source for source in envelope.source_records}
    head_cursors = {
        (cursor.namespace, cursor.incarnation): cursor
        for cursor in current_head.barrier_cursors
    }
    barrier_advances: list[BarrierAdvanceV21] = []
    next_barriers = {
        (barrier.namespace, barrier.incarnation): barrier
        for barrier in base.state.fold_barriers
    }
    rootless_actions = tuple(
        sorted(
            (
                action
                for action in actions
                if action.kind is _PlanningActionKindV21.ROOTLESS_SEGMENT
            ),
            key=lambda action: (action.namespace, action.incarnation),
        )
    )
    for action in rootless_actions:
        prior = head_cursors.get((action.namespace, action.incarnation))
        if prior is None:
            prior = BarrierAdvanceCursorV21(
                namespace=action.namespace,
                incarnation=action.incarnation,
                durable_root=None,
                high_water=None,
                kind=AdvanceCursorKindV21.SEED,
                transient_root=barrier_advance_seed_root_v21(
                    base_state_hash=base_hash,
                    prior_advance_head_root=current_head.head_root,
                    namespace=action.namespace,
                    incarnation=action.incarnation,
                    durable_root=None,
                    high_water=None,
                ),
            )
        previous_water = prior.high_water or 0
        released = tuple(
            ReleasedSourceV21(
                record_id=source_id,
                source_commitment=source_by_id[source_id].commitment,
            )
            for source_id in action.source_ids
        )
        new_water = max(
            previous_water,
            *(source_by_id[source_id].as_of for source_id in action.source_ids),
        )
        root = barrier_advance_root_v21(
            base_state_hash=base_hash,
            source_envelope_root=envelope.envelope_root,
            policy_root=policy_root,
            record_decision_root=record_root,
            segment_decision_root=segment_root,
            prior_advance_head_root=current_head.head_root,
            prior_cursor=prior,
            namespace=action.namespace,
            incarnation=action.incarnation,
            previous_high_water=previous_water,
            new_high_water=new_water,
            generation=target_generation,
            released_sources=released,
        )
        durable = barrier_durable_projection_root_v21(
            previous_durable_root=prior.durable_root,
            barrier_advance_root=root,
            namespace=action.namespace,
            incarnation=action.incarnation,
            new_high_water=new_water,
            generation=target_generation,
        )
        barrier_advances.append(
            BarrierAdvanceV21(
                base_state_hash=base_hash,
                source_envelope_root=envelope.envelope_root,
                policy_root=policy_root,
                record_decision_root=record_root,
                segment_decision_root=segment_root,
                prior_advance_head_root=current_head.head_root,
                prior_cursor=prior,
                namespace=action.namespace,
                incarnation=action.incarnation,
                previous_high_water=previous_water,
                new_high_water=new_water,
                generation=target_generation,
                released_sources=released,
                new_durable_root=durable,
                new_root=root,
            )
        )
        next_barriers[(action.namespace, action.incarnation)] = FoldBarrierV21(
            namespace=action.namespace,
            incarnation=action.incarnation,
            folded_through_high_water=new_water,
            cumulative_root=durable,
        )
    barrier_advances_tuple = tuple(barrier_advances)
    barrier_roots = tuple(
        sorted(advance.new_root for advance in barrier_advances_tuple)
    )
    role_delta, loss_units = _role_delta_v21(
        actions=actions,
        evidence_by_id=evidence_by_id,
        parent_by_id=parent_by_id,
        policy=policy,
    )
    previous_counts = base.state.loss_ledger.role_counts
    previous_by_role = dict(previous_counts)
    delta_by_role = dict(role_delta)
    new_counts = tuple(
        sorted(
            (
                (role, previous_by_role.get(role, 0) + delta_by_role.get(role, 0))
                for role in set(previous_by_role) | set(delta_by_role)
            ),
            key=lambda item: item[0].value,
        )
    )
    caps = dict(policy.per_role_loss_caps)
    if loss_units > policy.max_loss_units or any(
        count > caps.get(role, 0) for role, count in new_counts
    ):
        _reject("loss ledger delta exceeds policy")
    ledger_root = loss_ledger_advance_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=barrier_roots,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=previous_counts,
        delta_role_counts=role_delta,
        new_role_counts=new_counts,
        previous_generation=base.state.loss_ledger.last_fold_generation,
        new_generation=target_generation,
        loss_units_delta=loss_units,
    )
    durable_ledger = ledger_durable_projection_root_v21(
        previous_durable_root=current_head.ledger_cursor.durable_root,
        ledger_advance_root=ledger_root,
        new_role_counts=new_counts,
        new_generation=target_generation,
    )
    ledger_advance = LossLedgerAdvanceV21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        barrier_roots=barrier_roots,
        prior_advance_head_root=current_head.head_root,
        prior_cursor=current_head.ledger_cursor,
        previous_role_counts=previous_counts,
        delta_role_counts=role_delta,
        new_role_counts=new_counts,
        previous_generation=base.state.loss_ledger.last_fold_generation,
        new_generation=target_generation,
        loss_units_delta=loss_units,
        new_durable_root=durable_ledger,
        new_root=ledger_root,
    )
    exact_records = tuple(
        _source_to_exact_v21(source)
        for source in envelope.source_records
        if source.record_id in mandatory
    )
    hot_records = tuple(
        _source_to_exact_v21(source)
        for source in envelope.source_records
        if source.record_id not in mandatory and source.record_id not in child_by_source
    )
    retained_segments = [
        segment
        for segment in base.state.capsule_segments
        if segment.segment_id not in child_by_parent
    ]
    proposed_segments = tuple(
        sorted(
            retained_segments + list(children_by_action.values()),
            key=lambda segment: segment.segment_id,
        )
    )
    locators: dict[str, ContributionLocatorV21] = {}

    def add_locator(locator: ContributionLocatorV21) -> None:
        if locator.contribution_key in locators:
            _reject("decision contribution keys overlap")
        locators[locator.contribution_key] = locator

    for source in envelope.source_records:
        evidence = evidence_by_id[source.record_id]
        child = child_by_source.get(source.record_id)
        for key in evidence.contribution_keys:
            if child is None:
                add_locator(
                    ContributionLocatorV21(
                        contribution_key=key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=source.record_id,
                        support_commitment=source.commitment,
                    )
                )
            else:
                add_locator(
                    ContributionLocatorV21(
                        contribution_key=key,
                        support=ContributionSupportV21.SEGMENT,
                        resident_id=child.segment_id,
                        support_commitment=child.folded_commitment_root,
                    )
                )
    for segment in base.state.capsule_segments:
        target = child_by_parent.get(segment.segment_id, segment)
        keys = tuple(
            locator.contribution_key
            for locator in base.contribution_index
            if locator.support is ContributionSupportV21.SEGMENT
            and locator.resident_id == segment.segment_id
        )
        for key in keys:
            add_locator(
                ContributionLocatorV21(
                    contribution_key=key,
                    support=ContributionSupportV21.SEGMENT,
                    resident_id=target.segment_id,
                    support_commitment=target.folded_commitment_root,
                )
            )
    proposed = EvidencedCapsuleStateV21(
        state=replace(
            base.state,
            frame=replace(
                base.state.frame,
                generation=target_generation,
                high_water=target_high_water,
            ),
            exact_kernel=exact_records,
            hot_cache=hot_records,
            capsule_segments=proposed_segments,
            fold_barriers=tuple(
                sorted(
                    next_barriers.values(),
                    key=lambda barrier: (barrier.namespace, barrier.incarnation),
                )
            ),
            loss_ledger=LossLedgerV21(
                role_counts=new_counts,
                cumulative_loss_root=durable_ledger,
                last_fold_generation=target_generation,
            ),
        ),
        contribution_index=tuple(
            sorted(locators.values(), key=lambda locator: locator.contribution_key)
        ),
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    breakdown = evidenced_resident_layout_v21(proposed)
    base_breakdown = evidenced_resident_layout_v21(base)
    objective = MatrixObjectiveV21(
        control_bytes=breakdown.control_bytes,
        source_body_bytes=breakdown.source_body_bytes,
        resident_bytes=breakdown.resident_bytes,
        loss_units=loss_units,
        saved_bytes=max(0, base_breakdown.resident_bytes - breakdown.resident_bytes),
    )
    matrix_plan = _build_matrix_plan_v21(
        base=base,
        envelope=envelope,
        policy=policy,
        base_hash=base_hash,
        policy_root=policy_root,
        record_decisions=record_decisions,
        segment_decisions=segment_decisions,
        children_by_action=children_by_action,
        actions=actions,
        proposed_hash=proposed_hash,
        target_generation=target_generation,
        target_high_water=target_high_water,
        evaluation_count=evaluation_count,
        objective=objective,
    )
    transition_root = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=matrix_plan.matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=authorization_roots,
        barrier_roots=barrier_roots,
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=target_generation,
        target_high_water=target_high_water,
    )
    candidate = EvidencedReencodingCandidateV21(
        proposed_state=proposed,
        source_envelope=envelope,
        policy=policy,
        record_decisions=record_decisions,
        segment_decisions=segment_decisions,
        first_fold_authorizations=authorizations_tuple,
        barrier_advances=barrier_advances_tuple,
        ledger_advance=ledger_advance,
        matrix_root=matrix_plan.matrix_root,
        record_decision_root=record_root,
        segment_decision_root=segment_root,
        authorization_roots=authorization_roots,
        barrier_roots=barrier_roots,
        ledger_root=ledger_root,
        proposed_hash=proposed_hash,
        target_generation=target_generation,
        target_high_water=target_high_water,
        transition_root=transition_root,
    )
    return _MaterializedCandidateV21(
        matrix_plan=matrix_plan,
        candidate=candidate,
        action_ids=tuple(sorted(action.action_id for action in actions)),
    )


def _try_materialize_v21(
    inputs: _MaterializationInputsV21,
    *,
    actions: tuple[_PlanningActionV21, ...],
    evaluation_count: int,
) -> _MaterializedCandidateV21 | None:
    try:
        return _materialize_candidate_v21(
            base=inputs.base,
            current_head=inputs.current_head,
            envelope=inputs.envelope,
            policy=inputs.policy,
            base_hash=inputs.base_hash,
            policy_root=inputs.policy_root,
            actions=actions,
            evaluation_count=evaluation_count,
        )
    except (TypeError, ValueError):
        return None


def _objective_key_v21(
    materialized: _MaterializedCandidateV21,
) -> tuple[int, int, tuple[str, ...]]:
    objective = materialized.matrix_plan.objective
    return (objective.resident_bytes, objective.loss_units, materialized.action_ids)


def _plan_exact_small_v21(
    *,
    actions: tuple[_PlanningActionV21, ...],
    policy: EvidencedReencodingPolicyV21,
    inputs: _MaterializationInputsV21,
) -> _MaterializedCandidateV21:
    total = _selection_count_v21(len(actions), policy.max_new_segments)
    if total > policy.max_plan_evaluations:
        _reject("exact-small evaluation cap would prevent full enumeration")
    candidates: list[_MaterializedCandidateV21] = []
    evaluation = 0
    for size in range(min(len(actions), policy.max_new_segments) + 1):
        for selected in combinations(actions, size):
            evaluation += 1
            materialized = _try_materialize_v21(
                inputs,
                actions=selected,
                evaluation_count=evaluation,
            )
            if materialized is not None:
                candidates.append(materialized)
    if not candidates:
        _reject("no feasible exact-small candidate")
    selected = min(candidates, key=_objective_key_v21)
    return _materialize_candidate_v21(
        base=inputs.base,
        current_head=inputs.current_head,
        envelope=inputs.envelope,
        policy=inputs.policy,
        base_hash=inputs.base_hash,
        policy_root=inputs.policy_root,
        actions=tuple(
            action for action in actions if action.action_id in set(selected.action_ids)
        ),
        evaluation_count=evaluation,
    )


def _ratio_precedes_v21(
    *,
    loss_left: int,
    saved_left: int,
    action_ids_left: tuple[str, ...],
    loss_right: int,
    saved_right: int,
    action_ids_right: tuple[str, ...],
) -> bool:
    """Compare integer loss/saving ratios without floats or epsilon."""
    left_cross = loss_left * saved_right
    right_cross = loss_right * saved_left
    if left_cross != right_cross:
        return left_cross < right_cross
    return action_ids_left < action_ids_right


def _plan_deterministic_greedy_v21(
    *,
    actions: tuple[_PlanningActionV21, ...],
    policy: EvidencedReencodingPolicyV21,
    inputs: _MaterializationInputsV21,
) -> _MaterializedCandidateV21:
    if policy.max_plan_evaluations < 1:
        _reject("greedy evaluation cap cannot materialize its baseline")
    evaluations = 1
    current = _try_materialize_v21(inputs, actions=(), evaluation_count=evaluations)
    if current is None:
        _reject("all-EXACT/RETAIN baseline is infeasible")
    selected: tuple[_PlanningActionV21, ...] = ()
    remaining = list(actions)
    while remaining:
        eligible = tuple(
            action
            for action in remaining
            if len(selected) + 1 <= policy.max_new_segments
        )
        if not eligible:
            break
        if policy.max_plan_evaluations - evaluations < len(eligible):
            _reject("greedy evaluation cap would prevent a complete sweep")
        best: tuple[_PlanningActionV21, _MaterializedCandidateV21, int, int] | None = (
            None
        )
        for action in eligible:
            proposal_actions = tuple(
                sorted(selected + (action,), key=lambda item: item.action_id)
            )
            evaluations += 1
            proposal = _try_materialize_v21(
                inputs,
                actions=proposal_actions,
                evaluation_count=evaluations,
            )
            if proposal is None:
                continue
            saved = (
                current.matrix_plan.objective.resident_bytes
                - proposal.matrix_plan.objective.resident_bytes
            )
            if saved <= 0:
                continue
            loss = (
                proposal.matrix_plan.objective.loss_units
                - current.matrix_plan.objective.loss_units
            )
            if best is None or _ratio_precedes_v21(
                loss_left=loss,
                saved_left=saved,
                action_ids_left=proposal.action_ids,
                loss_right=best[2],
                saved_right=best[3],
                action_ids_right=best[1].action_ids,
            ):
                best = (action, proposal, loss, saved)
        if best is None:
            break
        action, proposal, _loss, _saved = best
        selected = tuple(sorted(selected + (action,), key=lambda item: item.action_id))
        current = proposal
        remaining.remove(action)
    # This deterministic rebuild is a non-scoring finalize, not a solver probe.
    return _materialize_candidate_v21(
        base=inputs.base,
        current_head=inputs.current_head,
        envelope=inputs.envelope,
        policy=inputs.policy,
        base_hash=inputs.base_hash,
        policy_root=inputs.policy_root,
        actions=selected,
        evaluation_count=evaluations,
    )


def plan_evidenced_reencoding_v21(
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
    """Plan once, then return only a candidate accepted by the V21-005 oracle.

    ``EXACT_SMALL`` enumerates every action subset allowed by the new-segment
    limit, or fails closed before an incomplete search.  Its frozen objective
    is lexicographic ``(resident_bytes, loss_units, canonical_action_ids)``.
    ``DETERMINISTIC_GREEDY`` starts EXACT/RETAIN and accepts only positive real
    C/B/R savings; competing actions compare integer loss/saved ratios by
    cross multiplication, then their complete canonical action-id tuple.
    """
    try:
        base_hash = evidenced_state_hash_v21(base)
        policy_root = evidenced_reencoding_policy_root_v21(policy)
        if source_envelope.base_state_hash != base_hash:
            _reject("source envelope does not bind the supplied base state")
        if len(source_envelope.source_records) > policy.max_matrix_rows:
            _reject("source universe exceeds policy matrix rows")
        if len(source_envelope.hard_edges) > policy.max_hard_edges:
            _reject("source hard graph exceeds policy edge bound")
        if (
            len(source_envelope.source_records) + len(base.state.capsule_segments)
            > policy.max_decisions
        ):
            _reject("decision count exceeds policy")
        actions = _canonical_actions_v21(
            base=base,
            envelope=source_envelope,
            policy=policy,
            base_hash=base_hash,
            policy_root=policy_root,
        )
        inputs = _MaterializationInputsV21(
            base=base,
            current_head=current_head,
            envelope=source_envelope,
            policy=policy,
            base_hash=base_hash,
            policy_root=policy_root,
        )
        if policy.solver_mode is SolverModeV21.EXACT_SMALL:
            selected = _plan_exact_small_v21(
                actions=actions,
                policy=policy,
                inputs=inputs,
            )
        elif policy.solver_mode is SolverModeV21.DETERMINISTIC_GREEDY:
            selected = _plan_deterministic_greedy_v21(
                actions=actions,
                policy=policy,
                inputs=inputs,
            )
        else:
            _reject("unsupported solver mode")
    except EvidencedPlanningRejectedV21:
        raise
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError) as error:
        raise EvidencedPlanningRejectedV21(str(error)) from error

    verification = verify_evidenced_reencoding_candidate_v21(
        base,
        current_head,
        selected.candidate,
        expected_base_state_hash=expected_base_state_hash,
        expected_source_envelope_root=expected_source_envelope_root,
        expected_policy_root=expected_policy_root,
        expected_advance_head_root=expected_advance_head_root,
    )
    if verification.status is not EvidencedReencodingStatusV21.VERIFIED:
        raise EvidencedPlanningRejectedV21(
            f"V21 verifier rejected selected candidate: {verification.reason}"
        )
    return EvidencedPlannedCandidateV21(
        matrix_plan=selected.matrix_plan,
        candidate=selected.candidate,
        verification=verification,
    )
