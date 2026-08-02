"""Pure V21 evidenced-reencoding verifier boundary.

This module deliberately consumes only already-validated V21/V21-004 values.
It owns no persistence, planner, solver, model, network, or source archive.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from crm_experiment.contracts_v21 import (
    CapsuleRoleV21,
    CapsuleSegmentV21,
    ExactRecordV21,
    FoldBarrierV21,
    LossLedgerV21,
    segment_input_commitment_root_v21,
)
from crm_experiment.evidenced_state_v21 import (
    ContributionLocatorV21,
    ContributionSupportV21,
    EvidencedCapsuleStateV21,
    evidenced_state_hash_v21,
    validate_evidenced_capsule_state_v21,
)
from crm_experiment.reencoding_contracts_v21 import (
    ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
    EVIDENCED_POLICY_ROOT_DOMAIN_V21,
    EVIDENCED_STATE_HASH_DOMAIN_V21,
    MATRIX_ROOT_DOMAIN_V21,
    SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
    AdvanceChainHeadV21,
    AdvanceCursorKindV21,
    BarrierAdvanceCursorV21,
    BarrierAdvanceV21,
    EvidencedReencodingPolicyV21,
    FirstFoldAuthorizationV21,
    FirstFoldSourceV21,
    LedgerAdvanceCursorV21,
    LossLedgerAdvanceV21,
    RecordDecisionV21,
    ReencodingOutcomeV21,
    ReleasedSourceV21,
    SegmentDecisionV21,
    SegmentDispositionV21,
    SourceEnvelopeV21,
    SourceRecordV21,
    advance_chain_head_root_v21,
    barrier_advance_seed_root_v21,
    canonical_segment_hash_v21,
    evidenced_reencoding_policy_root_v21,
    evidenced_transition_root_v21,
    record_decision_root_v21,
    segment_decision_root_v21,
    validate_advance_chain_head_v21,
    validate_barrier_advance_v21,
    validate_evidenced_reencoding_policy_v21,
    validate_first_fold_authorization_v21,
    validate_loss_ledger_advance_v21,
    validate_source_envelope_v21,
)


class EvidencedReencodingStatusV21(StrEnum):
    """Closed outcomes for one non-persistent verification attempt."""

    VERIFIED = "VERIFIED"
    STALE_REJECTED = "STALE_REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class EvidencedReencodingCandidateV21:
    """A transition-scoped candidate assembled outside the verifier."""

    proposed_state: EvidencedCapsuleStateV21
    source_envelope: SourceEnvelopeV21
    policy: EvidencedReencodingPolicyV21
    record_decisions: tuple[RecordDecisionV21, ...]
    segment_decisions: tuple[SegmentDecisionV21, ...]
    first_fold_authorizations: tuple[FirstFoldAuthorizationV21, ...]
    barrier_advances: tuple[BarrierAdvanceV21, ...]
    ledger_advance: LossLedgerAdvanceV21
    matrix_root: str
    record_decision_root: str
    segment_decision_root: str
    authorization_roots: tuple[str, ...]
    barrier_roots: tuple[str, ...]
    ledger_root: str
    proposed_hash: str
    target_generation: int
    target_high_water: int
    transition_root: str


@dataclass(frozen=True, slots=True)
class EvidencedReencodingResultV21:
    """Atomic verifier result; failure keeps the exact input identities."""

    state: EvidencedCapsuleStateV21
    advance_head: AdvanceChainHeadV21
    status: EvidencedReencodingStatusV21
    consumed_delta: bool
    transition_root: str | None
    reason: str | None


class _StaleCandidateV21(ValueError):
    """A trusted anchor or source snapshot is no longer current."""


class _RollbackCandidateV21(ValueError):
    """A candidate is malformed or cannot consume the requested delta."""


def _failure_v21(
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    *,
    status: EvidencedReencodingStatusV21,
    reason: str,
) -> EvidencedReencodingResultV21:
    return EvidencedReencodingResultV21(
        state=base,
        advance_head=current_head,
        status=status,
        consumed_delta=False,
        transition_root=None,
        reason=reason,
    )


def _require_domain_digest_v21(value: object, domain: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", value) is None
    ):
        raise _RollbackCandidateV21(f"{label} must be a {domain} domain digest")
    return value


def _require_nonnegative_int_v21(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _RollbackCandidateV21(f"{label} must be a non-negative integer")
    return value


def _require_expected_anchor_v21(
    actual: object,
    expected: object,
    *,
    domain: str,
    label: str,
) -> None:
    checked_actual = _require_domain_digest_v21(actual, domain, f"actual {label}")
    if (
        not isinstance(expected, str)
        or re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", expected) is None
    ):
        raise _StaleCandidateV21(f"expected {label} has the wrong domain")
    if not hmac.compare_digest(checked_actual, expected):
        raise _StaleCandidateV21(f"expected {label} does not name the current value")


def _record_matches_source_v21(record: ExactRecordV21, source: SourceRecordV21) -> bool:
    return (
        getattr(source, "record_id", None) == record.record_id
        and getattr(source, "namespace", None) == record.namespace
        and getattr(source, "incarnation", None) == record.incarnation
        and getattr(source, "as_of", None) == record.as_of
        and getattr(source, "commitment", None) == record.commitment
        and getattr(source, "body", None) == record.body
        and getattr(source, "core_required", None) == record.core_required
        and getattr(source, "active", None) == record.active
        and getattr(source, "hard_depends_on", None) == record.hard_depends_on
    )


def _validate_current_head_for_base_v21(
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    base_hash: str,
) -> None:
    if type(current_head) is not AdvanceChainHeadV21:
        raise _RollbackCandidateV21("current advance head must be nominal V21")
    validate_advance_chain_head_v21(current_head)
    if not hmac.compare_digest(current_head.state_hash, base_hash):
        raise _StaleCandidateV21("current advance head does not name base")
    if current_head.generation != base.state.frame.generation:
        raise _StaleCandidateV21("current advance head generation does not name base")
    base_barriers = {
        (barrier.namespace, barrier.incarnation): barrier
        for barrier in base.state.fold_barriers
    }
    head_cursors = {
        (cursor.namespace, cursor.incarnation): cursor
        for cursor in current_head.barrier_cursors
    }
    if set(head_cursors) != set(base_barriers):
        raise _StaleCandidateV21("current barrier cursors do not exactly name base")
    for identity, barrier in base_barriers.items():
        cursor = head_cursors[identity]
        if (
            cursor.durable_root != barrier.cumulative_root
            or cursor.high_water != barrier.folded_through_high_water
        ):
            raise _StaleCandidateV21("current barrier cursor does not name base")
    ledger = base.state.loss_ledger
    cursor = current_head.ledger_cursor
    if (
        cursor.durable_root != ledger.cumulative_loss_root
        or cursor.role_counts != ledger.role_counts
        or cursor.generation != ledger.last_fold_generation
    ):
        raise _StaleCandidateV21("current ledger cursor does not name base")


def _effective_sources_v21(
    base: EvidencedCapsuleStateV21,
    candidate: EvidencedReencodingCandidateV21,
    base_hash: str,
) -> dict[str, SourceRecordV21]:
    envelope = candidate.source_envelope
    if not hmac.compare_digest(envelope.base_state_hash, base_hash):
        raise _StaleCandidateV21("source envelope does not name base")
    base_records = {
        record.record_id: record
        for record in base.state.exact_kernel + base.state.hot_cache
    }
    sources = {source.record_id: source for source in envelope.source_records}
    incoming_ids = set(envelope.incoming_record_ids)
    if set(sources) != set(base_records).union(incoming_ids):
        raise _RollbackCandidateV21(
            "source envelope is not the base universe plus incoming supersession"
        )
    highest_incarnation: dict[str, int] = {}
    for record in base_records.values():
        highest_incarnation[record.namespace] = max(
            highest_incarnation.get(record.namespace, -1), record.incarnation
        )
    for barrier in base.state.fold_barriers:
        highest_incarnation[barrier.namespace] = max(
            highest_incarnation.get(barrier.namespace, -1), barrier.incarnation
        )
    barriers: dict[str, FoldBarrierV21] = {}
    for barrier in base.state.fold_barriers:
        known = barriers.get(barrier.namespace)
        if known is None or barrier.incarnation > known.incarnation:
            barriers[barrier.namespace] = barrier
    for record_id, base_record in base_records.items():
        source = sources.get(record_id)
        if source is None:
            raise _RollbackCandidateV21("source envelope omits a base resident")
        if record_id not in incoming_ids:
            if not _record_matches_source_v21(base_record, source):
                raise _RollbackCandidateV21(
                    "non-incoming source differs from its base resident"
                )
            continue
        _validate_fresh_incoming_v21(
            source, base, barriers, highest_incarnation, candidate.target_high_water
        )
    for record_id in incoming_ids:
        source = sources[record_id]
        if record_id not in base_records:
            _validate_fresh_incoming_v21(
                source, base, barriers, highest_incarnation, candidate.target_high_water
            )
    return sources


def _validate_fresh_incoming_v21(
    source: SourceRecordV21,
    base: EvidencedCapsuleStateV21,
    barriers: Mapping[str, FoldBarrierV21],
    highest_incarnation: Mapping[str, int],
    target_high_water: int,
) -> None:
    namespace = source.namespace
    incarnation = source.incarnation
    as_of = source.as_of
    if as_of <= base.state.frame.high_water:
        raise _StaleCandidateV21("incoming source is not newer than base high_water")
    barrier = barriers.get(namespace)
    if barrier is not None:
        if incarnation < getattr(barrier, "incarnation"):
            raise _StaleCandidateV21("incoming source is below barrier incarnation")
        if incarnation == getattr(barrier, "incarnation") and as_of <= getattr(
            barrier, "folded_through_high_water"
        ):
            raise _StaleCandidateV21("incoming source is at or below barrier")
    if incarnation < highest_incarnation.get(namespace, -1):
        raise _StaleCandidateV21("incoming source is below known incarnation")
    if as_of > target_high_water:
        raise _RollbackCandidateV21("incoming source exceeds target high_water")


def _verify_decisions_v21(
    base: EvidencedCapsuleStateV21,
    candidate: EvidencedReencodingCandidateV21,
    *,
    base_hash: str,
    policy_root: str,
    sources: Mapping[str, SourceRecordV21],
) -> tuple[
    dict[str, RecordDecisionV21],
    dict[str, SegmentDecisionV21],
    dict[str, tuple[str, ...]],
]:
    if type(candidate.record_decisions) is not tuple:
        raise _RollbackCandidateV21("record decisions must be a tuple")
    if type(candidate.segment_decisions) is not tuple:
        raise _RollbackCandidateV21("segment decisions must be a tuple")
    if len(candidate.record_decisions) + len(candidate.segment_decisions) > (
        candidate.policy.max_decisions
    ):
        raise _RollbackCandidateV21("decision count exceeds policy")
    expected_record_root = record_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=candidate.source_envelope.envelope_root,
        policy_root=policy_root,
        rows=candidate.record_decisions,
    )
    expected_segment_root = segment_decision_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=candidate.source_envelope.envelope_root,
        policy_root=policy_root,
        rows=candidate.segment_decisions,
    )
    if not hmac.compare_digest(candidate.record_decision_root, expected_record_root):
        raise _RollbackCandidateV21("record decision root does not bind decisions")
    if not hmac.compare_digest(candidate.segment_decision_root, expected_segment_root):
        raise _RollbackCandidateV21("segment decision root does not bind decisions")
    record_by_id = {row.record_id: row for row in candidate.record_decisions}
    if len(record_by_id) != len(candidate.record_decisions) or set(record_by_id) != set(
        sources
    ):
        raise _RollbackCandidateV21("every effective source requires one decision")
    contribution_by_record = {
        row.record_id: row.contribution_keys
        for row in candidate.source_envelope.contribution_evidence
    }
    mandatory_exact = {
        record_id
        for record_id, source in sources.items()
        if getattr(source, "core_required")
    }
    for edge in candidate.source_envelope.hard_edges:
        mandatory_exact.add(edge.source_id)
        mandatory_exact.add(edge.target_id)
    for record_id, source in sources.items():
        decision = record_by_id[record_id]
        if (
            decision.source_commitment != getattr(source, "commitment")
            or decision.contribution_keys != contribution_by_record[record_id]
        ):
            raise _RollbackCandidateV21("record decision does not bind source")
        if (
            record_id in mandatory_exact
            and decision.outcome is not ReencodingOutcomeV21.EXACT
        ):
            raise _RollbackCandidateV21("core or hard closure source must remain EXACT")
    base_segments = {
        segment.segment_id: segment for segment in base.state.capsule_segments
    }
    segment_by_id = {row.segment_id: row for row in candidate.segment_decisions}
    if len(segment_by_id) != len(candidate.segment_decisions) or set(
        segment_by_id
    ) != set(base_segments):
        raise _RollbackCandidateV21("every base segment requires one decision")
    for segment_id, segment in base_segments.items():
        if segment_by_id[
            segment_id
        ].canonical_segment_hash != canonical_segment_hash_v21(base.state, segment_id):
            raise _RollbackCandidateV21("segment decision does not bind base segment")
    return record_by_id, segment_by_id, contribution_by_record


def _verify_proposed_frame_v21(
    base: EvidencedCapsuleStateV21,
    candidate: EvidencedReencodingCandidateV21,
    sources: Mapping[str, SourceRecordV21],
) -> tuple[EvidencedCapsuleStateV21, int, int]:
    if type(candidate.proposed_state) is not EvidencedCapsuleStateV21:
        raise _RollbackCandidateV21("proposed state must be nominal evidenced V21")
    validate_evidenced_capsule_state_v21(candidate.proposed_state)
    proposed = candidate.proposed_state
    target_generation = _require_nonnegative_int_v21(
        candidate.target_generation, "target_generation"
    )
    target_high_water = _require_nonnegative_int_v21(
        candidate.target_high_water, "target_high_water"
    )
    if target_generation != base.state.frame.generation + 1:
        raise _RollbackCandidateV21("target generation must advance exactly once")
    expected_high_water = max(
        base.state.frame.high_water,
        *(getattr(source, "as_of") for source in sources.values()),
    )
    if target_high_water != expected_high_water:
        raise _RollbackCandidateV21("target high_water does not cover source universe")
    if proposed.state.bounds != base.state.bounds:
        raise _RollbackCandidateV21("proposed state cannot replace frozen bounds")
    expected_frame = replace(
        base.state.frame,
        generation=target_generation,
        high_water=target_high_water,
    )
    if proposed.state.frame != expected_frame:
        raise _RollbackCandidateV21("proposed frame exceeds permitted delta")
    if (
        proposed.state.dictionary != base.state.dictionary
        or proposed.state.hot_frontier != base.state.hot_frontier
        or proposed.state.sparse_weight_policy != base.state.sparse_weight_policy
    ):
        raise _RollbackCandidateV21("candidate changed unrelated resident controls")
    return proposed, target_generation, target_high_water


def _verify_segments_and_auth_v21(
    base: EvidencedCapsuleStateV21,
    candidate: EvidencedReencodingCandidateV21,
    record_decisions: dict[str, RecordDecisionV21],
    segment_decisions: dict[str, SegmentDecisionV21],
    sources: Mapping[str, SourceRecordV21],
    *,
    base_hash: str,
    policy_root: str,
    target_generation: int,
) -> tuple[
    dict[str, CapsuleSegmentV21],
    dict[str, CapsuleSegmentV21],
    frozenset[str],
]:
    base_segments = {
        segment.segment_id: segment for segment in base.state.capsule_segments
    }
    proposed_segments = {
        segment.segment_id: segment
        for segment in candidate.proposed_state.state.capsule_segments
    }
    new_segments = {
        segment_id: segment
        for segment_id, segment in proposed_segments.items()
        if segment_id not in base_segments
    }
    if len(new_segments) > candidate.policy.max_new_segments:
        raise _RollbackCandidateV21("new segment count exceeds policy")
    consumed_parents: set[str] = set()
    for segment_id, base_segment in base_segments.items():
        decision = segment_decisions[segment_id]
        proposed_segment = proposed_segments.get(segment_id)
        if decision.disposition is SegmentDispositionV21.RETAIN:
            if proposed_segment != base_segment:
                raise _RollbackCandidateV21("RETAIN segment changed or disappeared")
        elif proposed_segment is not None:
            raise _RollbackCandidateV21("consumed or dropped segment remains resident")
    for child in new_segments.values():
        if child.parent_ids:
            parents: list[CapsuleSegmentV21] = []
            for parent_id in child.parent_ids:
                parent = base_segments.get(parent_id)
                decision = segment_decisions.get(parent_id)
                if (
                    parent is None
                    or decision is None
                    or decision.disposition is not SegmentDispositionV21.COMPACT
                    or decision.child_segment_id != child.segment_id
                    or parent_id in consumed_parents
                ):
                    raise _RollbackCandidateV21("parent compaction is not one-to-one")
                if parent.generation_end >= child.generation_start:
                    raise _RollbackCandidateV21(
                        "compaction child does not follow parent"
                    )
                parents.append(parent)
                consumed_parents.add(parent_id)
            expected_input = segment_input_commitment_root_v21(
                tuple(parents),
                folded_commitment_root=child.folded_commitment_root,
                generation_start=child.generation_start,
                generation_end=child.generation_end,
                fold_policy_hash=child.fold_policy_hash,
            )
            if child.input_commitment_root != expected_input:
                raise _RollbackCandidateV21(
                    "compaction child input root does not bind parent"
                )
    for segment_id, decision in segment_decisions.items():
        if decision.disposition is SegmentDispositionV21.COMPACT:
            if segment_id not in consumed_parents:
                raise _RollbackCandidateV21(
                    "COMPACT parent is not consumed by its child"
                )
    if type(candidate.first_fold_authorizations) is not tuple:
        raise _RollbackCandidateV21("first-fold authorizations must be a tuple")
    auth_by_child: dict[str, FirstFoldAuthorizationV21] = {}
    roots: list[str] = []
    for authorization in candidate.first_fold_authorizations:
        validate_first_fold_authorization_v21(authorization)
        if authorization.child_segment_id in auth_by_child:
            raise _RollbackCandidateV21("rootless child has duplicate authorization")
        auth_by_child[authorization.child_segment_id] = authorization
        roots.append(authorization.authorization_root)
    if tuple(sorted(roots)) != candidate.authorization_roots or len(set(roots)) != len(
        roots
    ):
        raise _RollbackCandidateV21(
            "authorization roots do not exactly name authorizations"
        )
    rootless = {
        segment_id: segment
        for segment_id, segment in new_segments.items()
        if not segment.parent_ids
    }
    if set(auth_by_child) != set(rootless):
        raise _RollbackCandidateV21("every rootless child requires one authorization")
    for child_id, child in rootless.items():
        authorization = auth_by_child[child_id]
        if (
            authorization.base_state_hash != base_hash
            or authorization.source_envelope_root
            != candidate.source_envelope.envelope_root
            or authorization.policy_root != policy_root
            or authorization.namespace != child.namespace
            or authorization.generation != target_generation
        ):
            raise _RollbackCandidateV21(
                "rootless authorization does not bind transition"
            )
        expected_sources = tuple(
            sorted(
                (
                    FirstFoldSourceV21(
                        record_id=record_id,
                        source_commitment=getattr(sources[record_id], "commitment"),
                        contribution_keys=decision.contribution_keys,
                    )
                    for record_id, decision in record_decisions.items()
                    if decision.outcome is ReencodingOutcomeV21.SEGMENT
                    and decision.child_segment_id == child_id
                    and getattr(sources[record_id], "namespace") == child.namespace
                    and getattr(sources[record_id], "incarnation")
                    == authorization.incarnation
                ),
                key=lambda row: row.record_id,
            )
        )
        if not expected_sources or authorization.sources != expected_sources:
            raise _RollbackCandidateV21(
                "rootless authorization source set is incomplete"
            )
    for record_id, decision in record_decisions.items():
        if decision.outcome is ReencodingOutcomeV21.SEGMENT:
            child_id = decision.child_segment_id
            if child_id is None:
                raise _RollbackCandidateV21("SEGMENT outcome lacks a child")
            child = new_segments.get(child_id)
            if child is None or child.namespace != sources[record_id].namespace:
                raise _RollbackCandidateV21(
                    "SEGMENT outcome must target a matching new child"
                )
    return base_segments, proposed_segments, frozenset(rootless)


def _verify_records_and_locators_v21(
    base: EvidencedCapsuleStateV21,
    candidate: EvidencedReencodingCandidateV21,
    record_decisions: dict[str, RecordDecisionV21],
    segment_decisions: dict[str, SegmentDecisionV21],
    sources: Mapping[str, SourceRecordV21],
    proposed_segments: dict[str, CapsuleSegmentV21],
) -> None:
    proposed_records = {
        record.record_id: record
        for record in candidate.proposed_state.state.exact_kernel
        + candidate.proposed_state.state.hot_cache
    }
    exact_ids = {
        record_id
        for record_id, decision in record_decisions.items()
        if decision.outcome is ReencodingOutcomeV21.EXACT
    }
    if set(proposed_records) != exact_ids:
        raise _RollbackCandidateV21("record outcomes do not exactly project bodies")
    for record_id in exact_ids:
        if not _record_matches_source_v21(
            proposed_records[record_id], sources[record_id]
        ):
            raise _RollbackCandidateV21("EXACT outcome body differs from source")
    expected: dict[str, ContributionLocatorV21] = {}

    def put(locator: ContributionLocatorV21) -> None:
        if locator.contribution_key in expected:
            raise _RollbackCandidateV21("decision contribution keys overlap")
        expected[locator.contribution_key] = locator

    for record_id, decision in record_decisions.items():
        source = sources[record_id]
        if decision.outcome is ReencodingOutcomeV21.EXACT:
            for key in decision.contribution_keys:
                put(
                    ContributionLocatorV21(
                        contribution_key=key,
                        support=ContributionSupportV21.EXACT,
                        resident_id=record_id,
                        support_commitment=getattr(source, "commitment"),
                    )
                )
        elif decision.outcome is ReencodingOutcomeV21.SEGMENT:
            child_id = decision.child_segment_id
            if child_id is None:
                raise _RollbackCandidateV21("SEGMENT outcome lacks a child")
            child = proposed_segments[child_id]
            for key in decision.contribution_keys:
                put(
                    ContributionLocatorV21(
                        contribution_key=key,
                        support=ContributionSupportV21.SEGMENT,
                        resident_id=child.segment_id,
                        support_commitment=child.folded_commitment_root,
                    )
                )
    base_segments = {
        segment.segment_id: segment for segment in base.state.capsule_segments
    }
    base_locator_keys = {
        segment_id: tuple(
            locator.contribution_key
            for locator in base.contribution_index
            if locator.support is ContributionSupportV21.SEGMENT
            and locator.resident_id == segment_id
        )
        for segment_id in base_segments
    }
    for segment_id, decision in segment_decisions.items():
        if decision.contribution_keys != base_locator_keys[segment_id]:
            raise _RollbackCandidateV21(
                "segment decision keys do not cover base locators"
            )
        if decision.disposition is SegmentDispositionV21.RETAIN:
            target = base_segments[segment_id]
        elif decision.disposition is SegmentDispositionV21.COMPACT:
            child_id = decision.child_segment_id
            if child_id is None:
                raise _RollbackCandidateV21("COMPACT disposition lacks a child")
            target = proposed_segments[child_id]
        else:
            continue
        for key in decision.contribution_keys:
            put(
                ContributionLocatorV21(
                    contribution_key=key,
                    support=ContributionSupportV21.SEGMENT,
                    resident_id=target.segment_id,
                    support_commitment=target.folded_commitment_root,
                )
            )
    expected_index = tuple(
        sorted(expected.values(), key=lambda row: row.contribution_key)
    )
    if candidate.proposed_state.contribution_index != expected_index:
        raise _RollbackCandidateV21(
            "locator conversion leaves residue or repoints support"
        )


def _verify_barriers_v21(
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    candidate: EvidencedReencodingCandidateV21,
    record_decisions: dict[str, RecordDecisionV21],
    sources: Mapping[str, SourceRecordV21],
    proposed_segments: dict[str, CapsuleSegmentV21],
    authorized_rootless_new_children: frozenset[str],
    *,
    base_hash: str,
    policy_root: str,
    target_generation: int,
    target_high_water: int,
) -> tuple[tuple[BarrierAdvanceV21, ...], tuple[BarrierAdvanceCursorV21, ...]]:
    if type(candidate.barrier_advances) is not tuple:
        raise _RollbackCandidateV21("barrier advances must be a tuple")
    advances = candidate.barrier_advances
    identities = tuple((advance.namespace, advance.incarnation) for advance in advances)
    if identities != tuple(sorted(identities)) or len(set(identities)) != len(
        identities
    ):
        raise _RollbackCandidateV21("barrier advances must be sorted and unique")
    groups: dict[tuple[str, int], list[tuple[str, SourceRecordV21]]] = {}
    for record_id, decision in record_decisions.items():
        if decision.outcome is ReencodingOutcomeV21.EXACT:
            continue
        source = sources[record_id]
        groups.setdefault((source.namespace, source.incarnation), []).append(
            (record_id, source)
        )
    advance_by_identity = {
        (advance.namespace, advance.incarnation): advance for advance in advances
    }
    if set(advance_by_identity) != set(groups):
        raise _RollbackCandidateV21(
            "released sources require exactly one barrier advance"
        )
    roots = tuple(sorted(advance.new_root for advance in advances))
    if candidate.barrier_roots != roots:
        raise _RollbackCandidateV21("barrier roots do not exactly name advances")
    head_cursors = {
        (cursor.namespace, cursor.incarnation): cursor
        for cursor in current_head.barrier_cursors
    }
    base_barriers = {
        (barrier.namespace, barrier.incarnation): barrier
        for barrier in base.state.fold_barriers
    }
    next_cursors = dict(head_cursors)
    expected_barriers = dict(base_barriers)
    for identity, released_rows in groups.items():
        advance = advance_by_identity[identity]
        validate_barrier_advance_v21(advance)
        namespace, incarnation = identity
        prior = head_cursors.get(identity)
        if prior is None:
            child_ids = {
                record_decisions[record_id].child_segment_id
                for record_id, _source in released_rows
            }
            if (
                any(
                    record_decisions[record_id].outcome
                    is not ReencodingOutcomeV21.SEGMENT
                    for record_id, _source in released_rows
                )
                or len(child_ids) != 1
            ):
                raise _RollbackCandidateV21(
                    "new barrier identity requires a rootless SEGMENT first fold"
                )
            child_id = next(iter(child_ids))
            if child_id is None or child_id not in authorized_rootless_new_children:
                raise _RollbackCandidateV21(
                    "new barrier identity requires a rootless SEGMENT first fold"
                )
            child = proposed_segments.get(child_id)
            if child is None or child.parent_ids:
                raise _RollbackCandidateV21(
                    "new barrier identity requires a rootless SEGMENT first fold"
                )
            prior = BarrierAdvanceCursorV21(
                namespace=namespace,
                incarnation=incarnation,
                durable_root=None,
                high_water=None,
                kind=AdvanceCursorKindV21.SEED,
                transient_root=barrier_advance_seed_root_v21(
                    base_state_hash=base_hash,
                    prior_advance_head_root=current_head.head_root,
                    namespace=namespace,
                    incarnation=incarnation,
                    durable_root=None,
                    high_water=None,
                ),
            )
        if (
            advance.base_state_hash != base_hash
            or advance.source_envelope_root != candidate.source_envelope.envelope_root
            or advance.policy_root != policy_root
            or advance.record_decision_root != candidate.record_decision_root
            or advance.segment_decision_root != candidate.segment_decision_root
            or advance.prior_advance_head_root != current_head.head_root
            or advance.prior_cursor != prior
            or advance.generation != target_generation
        ):
            raise _RollbackCandidateV21(
                "barrier advance does not bind current evidence"
            )
        expected_released = tuple(
            ReleasedSourceV21(
                record_id=record_id,
                source_commitment=source.commitment,
            )
            for record_id, source in sorted(released_rows)
        )
        if advance.released_sources != expected_released:
            raise _RollbackCandidateV21("barrier release set does not match decisions")
        prior_water = prior.high_water or 0
        expected_water = max(
            prior_water, *(source.as_of for _record_id, source in released_rows)
        )
        if (
            advance.previous_high_water != prior_water
            or advance.new_high_water != expected_water
        ):
            raise _RollbackCandidateV21("barrier high_water does not cover releases")
        if advance.new_high_water > target_high_water:
            raise _RollbackCandidateV21("barrier advance exceeds target high_water")
        expected_barriers[identity] = FoldBarrierV21(
            namespace=namespace,
            incarnation=incarnation,
            folded_through_high_water=advance.new_high_water,
            cumulative_root=advance.new_durable_root,
        )
        next_cursors[identity] = BarrierAdvanceCursorV21(
            namespace=namespace,
            incarnation=incarnation,
            durable_root=advance.new_durable_root,
            high_water=advance.new_high_water,
            kind=AdvanceCursorKindV21.ADVANCE,
            transient_root=advance.new_root,
        )
    proposed_barriers = {
        (barrier.namespace, barrier.incarnation): barrier
        for barrier in candidate.proposed_state.state.fold_barriers
    }
    if proposed_barriers != expected_barriers:
        raise _RollbackCandidateV21("proposed barriers do not project advances")
    return advances, tuple(
        sorted(next_cursors.values(), key=lambda row: (row.namespace, row.incarnation))
    )


def _verify_ledger_v21(
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    candidate: EvidencedReencodingCandidateV21,
    record_decisions: dict[str, RecordDecisionV21],
    segment_decisions: dict[str, SegmentDecisionV21],
    sources: Mapping[str, SourceRecordV21],
    base_segments: dict[str, CapsuleSegmentV21],
    *,
    base_hash: str,
    policy_root: str,
    target_generation: int,
) -> LedgerAdvanceCursorV21:
    if type(candidate.ledger_advance) is not LossLedgerAdvanceV21:
        raise _RollbackCandidateV21("ledger advance must be nominal V21")
    validate_loss_ledger_advance_v21(candidate.ledger_advance)
    advance = candidate.ledger_advance
    role_delta: dict[CapsuleRoleV21, int] = {}
    loss_units = 0
    for record_id, decision in record_decisions.items():
        if decision.outcome is ReencodingOutcomeV21.SEGMENT:
            role = next(
                row.role
                for row in candidate.source_envelope.contribution_evidence
                if row.record_id == record_id
            )
            role_delta[role] = role_delta.get(role, 0) + 1
            loss_units += candidate.policy.segment_loss_units
        elif decision.outcome is ReencodingOutcomeV21.DROP:
            role = next(
                row.role
                for row in candidate.source_envelope.contribution_evidence
                if row.record_id == record_id
            )
            role_delta[role] = role_delta.get(role, 0) + 1
            loss_units += candidate.policy.drop_loss_units
    for segment_id, decision in segment_decisions.items():
        if decision.disposition in {
            SegmentDispositionV21.COMPACT,
            SegmentDispositionV21.DROP,
        }:
            for role, count in base_segments[segment_id].role_counts:
                role_delta[role] = role_delta.get(role, 0) + count
            loss_units += (
                candidate.policy.compaction_loss_units
                if decision.disposition is SegmentDispositionV21.COMPACT
                else candidate.policy.drop_loss_units
            )
    expected_delta = tuple(sorted(role_delta.items(), key=lambda row: row[0].value))
    base_counts = dict(base.state.loss_ledger.role_counts)
    next_counts = {
        role: base_counts.get(role, 0) + role_delta.get(role, 0)
        for role in set(base_counts) | set(role_delta)
    }
    expected_counts = tuple(sorted(next_counts.items(), key=lambda row: row[0].value))
    caps = dict(candidate.policy.per_role_loss_caps)
    if loss_units > candidate.policy.max_loss_units or any(
        count > caps.get(role, 0) for role, count in next_counts.items()
    ):
        raise _RollbackCandidateV21("ledger delta exceeds loss policy")
    if (
        advance.base_state_hash != base_hash
        or advance.source_envelope_root != candidate.source_envelope.envelope_root
        or advance.policy_root != policy_root
        or advance.record_decision_root != candidate.record_decision_root
        or advance.segment_decision_root != candidate.segment_decision_root
        or advance.barrier_roots != candidate.barrier_roots
        or advance.prior_advance_head_root != current_head.head_root
        or advance.prior_cursor != current_head.ledger_cursor
        or advance.previous_role_counts != base.state.loss_ledger.role_counts
        or advance.delta_role_counts != expected_delta
        or advance.new_role_counts != expected_counts
        or advance.previous_generation != base.state.loss_ledger.last_fold_generation
        or advance.new_generation != target_generation
        or advance.loss_units_delta != loss_units
        or candidate.ledger_root != advance.new_root
    ):
        raise _RollbackCandidateV21("ledger advance does not project decision delta")
    expected_ledger = LossLedgerV21(
        role_counts=expected_counts,
        cumulative_loss_root=advance.new_durable_root,
        last_fold_generation=target_generation,
    )
    if candidate.proposed_state.state.loss_ledger != expected_ledger:
        raise _RollbackCandidateV21("proposed ledger does not project advance")
    return LedgerAdvanceCursorV21(
        durable_root=advance.new_durable_root,
        role_counts=advance.new_role_counts,
        generation=advance.new_generation,
        kind=AdvanceCursorKindV21.ADVANCE,
        transient_root=advance.new_root,
    )


def _verify_candidate_v21(
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    candidate: EvidencedReencodingCandidateV21,
    *,
    base_hash: str,
    policy_root: str,
    sources: Mapping[str, SourceRecordV21],
) -> AdvanceChainHeadV21:
    record_decisions, segment_decisions, _contributions = _verify_decisions_v21(
        base, candidate, base_hash=base_hash, policy_root=policy_root, sources=sources
    )
    proposed, target_generation, target_high_water = _verify_proposed_frame_v21(
        base, candidate, sources
    )
    proposed_segments = {
        segment.segment_id: segment for segment in proposed.state.capsule_segments
    }
    _verify_records_and_locators_v21(
        base,
        candidate,
        record_decisions,
        segment_decisions,
        sources,
        proposed_segments,
    )
    (
        base_segments,
        proposed_segments,
        authorized_rootless_new_children,
    ) = _verify_segments_and_auth_v21(
        base,
        candidate,
        record_decisions,
        segment_decisions,
        sources,
        base_hash=base_hash,
        policy_root=policy_root,
        target_generation=target_generation,
    )
    _advances, next_barriers = _verify_barriers_v21(
        base,
        current_head,
        candidate,
        record_decisions,
        sources,
        proposed_segments,
        authorized_rootless_new_children,
        base_hash=base_hash,
        policy_root=policy_root,
        target_generation=target_generation,
        target_high_water=target_high_water,
    )
    next_ledger = _verify_ledger_v21(
        base,
        current_head,
        candidate,
        record_decisions,
        segment_decisions,
        sources,
        base_segments,
        base_hash=base_hash,
        policy_root=policy_root,
        target_generation=target_generation,
    )
    proposed_hash = evidenced_state_hash_v21(proposed)
    if not hmac.compare_digest(candidate.proposed_hash, proposed_hash):
        raise _RollbackCandidateV21("proposed hash does not name proposed state")
    _require_domain_digest_v21(
        candidate.matrix_root, MATRIX_ROOT_DOMAIN_V21, "matrix_root"
    )
    expected_transition = evidenced_transition_root_v21(
        base_state_hash=base_hash,
        source_envelope_root=candidate.source_envelope.envelope_root,
        policy_root=policy_root,
        matrix_root=candidate.matrix_root,
        record_decision_root=candidate.record_decision_root,
        segment_decision_root=candidate.segment_decision_root,
        authorization_roots=candidate.authorization_roots,
        barrier_roots=candidate.barrier_roots,
        ledger_root=candidate.ledger_root,
        proposed_hash=proposed_hash,
        target_generation=target_generation,
        target_high_water=target_high_water,
    )
    if not hmac.compare_digest(candidate.transition_root, expected_transition):
        raise _RollbackCandidateV21("transition root does not bind candidate")
    return AdvanceChainHeadV21(
        state_hash=proposed_hash,
        generation=target_generation,
        barrier_cursors=next_barriers,
        ledger_cursor=next_ledger,
        previous_head_root=current_head.head_root,
        head_root=advance_chain_head_root_v21(
            state_hash=proposed_hash,
            generation=target_generation,
            barrier_cursors=next_barriers,
            ledger_cursor=next_ledger,
            previous_head_root=current_head.head_root,
        ),
    )


def verify_evidenced_reencoding_candidate_v21(
    base: EvidencedCapsuleStateV21,
    current_head: AdvanceChainHeadV21,
    candidate: EvidencedReencodingCandidateV21,
    *,
    expected_base_state_hash: str,
    expected_source_envelope_root: str,
    expected_policy_root: str,
    expected_advance_head_root: str,
) -> EvidencedReencodingResultV21:
    """Verify one evidenced candidate atomically without executing a planner."""
    try:
        if type(base) is not EvidencedCapsuleStateV21:
            raise _RollbackCandidateV21("base must be nominal evidenced V21")
        validate_evidenced_capsule_state_v21(base)
        base_hash = evidenced_state_hash_v21(base)
        _require_expected_anchor_v21(
            base_hash,
            expected_base_state_hash,
            domain=EVIDENCED_STATE_HASH_DOMAIN_V21,
            label="base state hash",
        )
        _require_expected_anchor_v21(
            getattr(current_head, "head_root", None),
            expected_advance_head_root,
            domain=ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
            label="advance head root",
        )
        _validate_current_head_for_base_v21(base, current_head, base_hash)
        if type(candidate) is not EvidencedReencodingCandidateV21:
            raise _RollbackCandidateV21("candidate must be nominal V21")
        validate_source_envelope_v21(candidate.source_envelope)
        validate_evidenced_reencoding_policy_v21(candidate.policy)
        _require_expected_anchor_v21(
            candidate.source_envelope.envelope_root,
            expected_source_envelope_root,
            domain=SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
            label="source envelope root",
        )
        policy_root = evidenced_reencoding_policy_root_v21(candidate.policy)
        _require_expected_anchor_v21(
            policy_root,
            expected_policy_root,
            domain=EVIDENCED_POLICY_ROOT_DOMAIN_V21,
            label="policy root",
        )
        if not hmac.compare_digest(
            candidate.policy.frame_policy_hash,
            base.state.frame.reencoding_policy_hash,
        ):
            raise _RollbackCandidateV21("policy does not bind base frame policy")
        if (
            len(candidate.source_envelope.source_records)
            > candidate.policy.max_matrix_rows
        ):
            raise _RollbackCandidateV21("source universe exceeds policy matrix rows")
        if len(candidate.source_envelope.hard_edges) > candidate.policy.max_hard_edges:
            raise _RollbackCandidateV21("source hard graph exceeds policy edge bound")
        _require_nonnegative_int_v21(candidate.target_generation, "target_generation")
        _require_nonnegative_int_v21(candidate.target_high_water, "target_high_water")
        sources = _effective_sources_v21(base, candidate, base_hash)
        successor = _verify_candidate_v21(
            base,
            current_head,
            candidate,
            base_hash=base_hash,
            policy_root=policy_root,
            sources=sources,
        )
    except _StaleCandidateV21 as error:
        return _failure_v21(
            base,
            current_head,
            status=EvidencedReencodingStatusV21.STALE_REJECTED,
            reason=str(error),
        )
    except (
        AttributeError,
        LookupError,
        StopIteration,
        TypeError,
        ValueError,
        _RollbackCandidateV21,
    ) as error:
        return _failure_v21(
            base,
            current_head,
            status=EvidencedReencodingStatusV21.ROLLED_BACK,
            reason=str(error),
        )
    return EvidencedReencodingResultV21(
        state=candidate.proposed_state,
        advance_head=successor,
        status=EvidencedReencodingStatusV21.VERIFIED,
        consumed_delta=True,
        transition_root=candidate.transition_root,
        reason=None,
    )
