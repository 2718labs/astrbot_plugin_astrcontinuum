from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ._base import FrozenEnvelope, NonNegativeInt, UnitFloat
from .capsules import (
    AnchorStatus,
    CapsuleAnchor,
    ContextCapsuleEnvelope,
    SemanticStatus,
)
from .events import EventEnvelope
from .snapshots import SnapshotEnvelope


class PermanentFailureCode(str, Enum):
    EMPTY_COMPILER_OUTPUT = "EMPTY_COMPILER_OUTPUT"
    SESSION_KEY_MISMATCH = "SESSION_KEY_MISMATCH"
    BASE_SNAPSHOT_MISMATCH = "BASE_SNAPSHOT_MISMATCH"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    COVERAGE_GAP = "COVERAGE_GAP"
    CAPSULE_MEMBERSHIP_MISMATCH = "CAPSULE_MEMBERSHIP_MISMATCH"
    SNAPSHOT_ANCHOR_MISMATCH = "SNAPSHOT_ANCHOR_MISMATCH"
    INVALID_CAPSULE_COVERAGE = "INVALID_CAPSULE_COVERAGE"
    UNSUPPORTED_ACTIVE_SEMANTIC = "UNSUPPORTED_ACTIVE_SEMANTIC"
    MISSING_PRIOR_SEMANTIC = "MISSING_PRIOR_SEMANTIC"
    MISSING_REQUIRED_ANCHOR = "MISSING_REQUIRED_ANCHOR"
    MECHANICAL_NOT_PASSED = "MECHANICAL_NOT_PASSED"
    SOURCE_COVERAGE_NOT_FULL = "SOURCE_COVERAGE_NOT_FULL"
    ANCHOR_RECALL_NOT_FULL = "ANCHOR_RECALL_NOT_FULL"
    UNSUPPORTED_CRITICAL_CLAIMS = "UNSUPPORTED_CRITICAL_CLAIMS"
    QUALITY_COVERAGE_GAP = "QUALITY_COVERAGE_GAP"
    TOKEN_CEILING_EXCEEDED = "TOKEN_CEILING_EXCEEDED"


class PermanentValidationReport(FrozenEnvelope):
    passed: bool
    failure_codes: tuple[PermanentFailureCode, ...]
    source_coverage: UnitFloat
    anchor_recall: UnitFloat
    coverage_gap: NonNegativeInt
    unsupported_critical_claims: NonNegativeInt


@dataclass(frozen=True, slots=True)
class _SemanticRecord:
    stable_id: str
    source_event_ids: tuple[str, ...]
    status: SemanticStatus
    supports_transition: bool


_APPROVED_TRANSITION_STATUSES = frozenset({SemanticStatus.SUPERSEDED, SemanticStatus.RETRACTED})


def _semantic_records(
    capsules: Sequence[ContextCapsuleEnvelope],
) -> list[_SemanticRecord]:
    records: list[_SemanticRecord] = []
    for capsule in capsules:
        claim_groups = (
            capsule.goals,
            capsule.constraints,
            capsule.progress,
            capsule.open_loops,
            capsule.preferences,
            capsule.emotional_context,
        )
        for group in claim_groups:
            records.extend(
                _SemanticRecord(
                    stable_id=item.claim_id,
                    source_event_ids=item.source_event_ids,
                    status=item.status,
                    supports_transition=True,
                )
                for item in group
            )
        records.extend(
            _SemanticRecord(
                stable_id=item.decision_id,
                source_event_ids=item.source_event_ids,
                status=item.status,
                supports_transition=True,
            )
            for item in capsule.decisions
        )
        records.extend(
            _SemanticRecord(
                stable_id=item.entity_id,
                source_event_ids=item.source_event_ids,
                status=SemanticStatus.ACTIVE,
                supports_transition=False,
            )
            for item in capsule.entities
        )
        records.extend(
            _SemanticRecord(
                stable_id=item.dependency_id,
                source_event_ids=item.source_event_ids,
                status=SemanticStatus.ACTIVE,
                supports_transition=False,
            )
            for item in capsule.dependencies
        )
    return records


def _active_anchors(
    capsules: Sequence[ContextCapsuleEnvelope],
) -> list[CapsuleAnchor]:
    return [
        anchor
        for capsule in capsules
        for anchor in capsule.exact_anchors
        if anchor.status == AnchorStatus.ACTIVE
    ]


def validate_permanent(
    *,
    previous_snapshot: SnapshotEnvelope | None,
    previous_capsules: Sequence[ContextCapsuleEnvelope],
    candidate_snapshot: SnapshotEnvelope,
    candidate_capsules: Sequence[ContextCapsuleEnvelope],
    source_events: Sequence[EventEnvelope],
    target_high_water_mark: int,
    token_ceiling: int,
) -> PermanentValidationReport:
    """Apply the non-configurable mechanical publication gate."""

    failures: set[PermanentFailureCode] = set()
    candidate_capsules = tuple(candidate_capsules)
    previous_capsules = tuple(previous_capsules)
    source_events = tuple(source_events)

    if not candidate_capsules:
        failures.add(PermanentFailureCode.EMPTY_COMPILER_OUTPUT)

    expected_base_id = previous_snapshot.snapshot_id if previous_snapshot else None
    if candidate_snapshot.base_snapshot_id != expected_base_id:
        failures.add(PermanentFailureCode.BASE_SNAPSHOT_MISMATCH)

    if (
        candidate_snapshot.covered_event_end != target_high_water_mark
        or candidate_snapshot.source_high_water_mark != target_high_water_mark
    ):
        failures.add(PermanentFailureCode.TARGET_MISMATCH)

    base_coverage = previous_snapshot.covered_event_end if previous_snapshot else 0
    expected_sequences = tuple(range(base_coverage + 1, target_high_water_mark + 1))
    actual_sequences = tuple(event.sequence for event in source_events)
    missing_sequences = set(expected_sequences) - set(actual_sequences)
    if (
        target_high_water_mark <= base_coverage
        or actual_sequences != expected_sequences
        or len({event.event_id for event in source_events}) != len(source_events)
    ):
        failures.add(PermanentFailureCode.COVERAGE_GAP)

    expected_capsule_ids = tuple(capsule.capsule_id for capsule in candidate_capsules)
    if candidate_snapshot.capsule_ids != expected_capsule_ids:
        failures.add(PermanentFailureCode.CAPSULE_MEMBERSHIP_MISMATCH)

    candidate_anchor_records = _active_anchors(candidate_capsules)
    candidate_anchor_ids = tuple(item.anchor_id for item in candidate_anchor_records)
    if candidate_snapshot.exact_anchor_ids != candidate_anchor_ids:
        failures.add(PermanentFailureCode.SNAPSHOT_ANCHOR_MISMATCH)

    session_key = candidate_snapshot.session_key
    if (
        any(capsule.session_key != session_key for capsule in candidate_capsules)
        or any(event.session_key != session_key for event in source_events)
        or (previous_snapshot is not None and previous_snapshot.session_key != session_key)
        or any(capsule.session_key != session_key for capsule in previous_capsules)
    ):
        failures.add(PermanentFailureCode.SESSION_KEY_MISMATCH)

    if any(
        capsule.covered_event_end > target_high_water_mark
        or capsule.covered_event_start > capsule.covered_event_end
        for capsule in candidate_capsules
    ):
        failures.add(PermanentFailureCode.INVALID_CAPSULE_COVERAGE)

    previous_source_ids = {
        source_id for capsule in previous_capsules for source_id in capsule.source_event_ids
    }
    current_source_ids = {event.event_id for event in source_events}
    valid_source_ids = previous_source_ids | current_source_ids

    previous_semantic_records = _semantic_records(previous_capsules)
    candidate_semantic_records = _semantic_records(candidate_capsules)
    previous_active_semantics = [
        item for item in previous_semantic_records if item.status == SemanticStatus.ACTIVE
    ]
    candidate_active_semantics = [
        item for item in candidate_semantic_records if item.status == SemanticStatus.ACTIVE
    ]
    previous_transitionable_ids = {
        item.stable_id for item in previous_active_semantics if item.supports_transition
    }
    candidate_transition_records = [
        item
        for item in candidate_semantic_records
        if item.supports_transition
        and item.stable_id in previous_transitionable_ids
        and item.status in _APPROVED_TRANSITION_STATUSES
    ]
    provenance_records = [
        (item.stable_id, item.source_event_ids)
        for item in candidate_active_semantics + candidate_transition_records
    ] + [(item.anchor_id, item.source_event_ids) for item in candidate_anchor_records]
    unsupported_records = [
        item_id
        for item_id, source_ids in provenance_records
        if not source_ids or any(source_id not in valid_source_ids for source_id in source_ids)
    ]
    capsule_source_mismatch = any(
        any(source_id not in valid_source_ids for source_id in capsule.source_event_ids)
        for capsule in candidate_capsules
    )
    if unsupported_records or capsule_source_mismatch:
        failures.add(PermanentFailureCode.UNSUPPORTED_ACTIVE_SEMANTIC)

    previous_semantic_ids = {item.stable_id for item in previous_active_semantics}
    candidate_active_semantic_ids = {item.stable_id for item in candidate_active_semantics}
    approved_transition_ids = {
        item.stable_id
        for item in candidate_transition_records
        if all(source_id in valid_source_ids for source_id in item.source_event_ids)
        and any(source_id in current_source_ids for source_id in item.source_event_ids)
    }
    retained_semantic_ids = candidate_active_semantic_ids | approved_transition_ids
    if not previous_semantic_ids.issubset(retained_semantic_ids):
        failures.add(PermanentFailureCode.MISSING_PRIOR_SEMANTIC)

    previous_anchor_records = _active_anchors(previous_capsules)
    preserved_anchor_count = sum(
        any(candidate_anchor == previous_anchor for candidate_anchor in candidate_anchor_records)
        for previous_anchor in previous_anchor_records
    )
    computed_anchor_recall = (
        preserved_anchor_count / len(previous_anchor_records) if previous_anchor_records else 1.0
    )
    if computed_anchor_recall < 1.0:
        failures.add(PermanentFailureCode.MISSING_REQUIRED_ANCHOR)

    provenance_count = len(provenance_records)
    computed_source_coverage = (
        (provenance_count - len(unsupported_records)) / provenance_count
        if provenance_count
        else 1.0
    )

    declared_source_coverage = min(
        (capsule.quality.source_coverage for capsule in candidate_capsules),
        default=0.0,
    )
    declared_anchor_recall = min(
        (capsule.quality.anchor_recall for capsule in candidate_capsules),
        default=0.0,
    )
    declared_unsupported = sum(
        capsule.quality.unsupported_critical_claims for capsule in candidate_capsules
    )
    declared_coverage_gap = sum(capsule.quality.coverage_gap for capsule in candidate_capsules)

    source_coverage = min(computed_source_coverage, declared_source_coverage)
    anchor_recall = min(computed_anchor_recall, declared_anchor_recall)
    unsupported_critical_claims = max(
        len(unsupported_records),
        declared_unsupported,
    )
    coverage_gap = max(len(missing_sequences), declared_coverage_gap)

    if not candidate_snapshot.audit_outcome.mechanical_passed or any(
        not capsule.quality.mechanical_passed for capsule in candidate_capsules
    ):
        failures.add(PermanentFailureCode.MECHANICAL_NOT_PASSED)
    if source_coverage < 1.0:
        failures.add(PermanentFailureCode.SOURCE_COVERAGE_NOT_FULL)
    if anchor_recall < 1.0:
        failures.add(PermanentFailureCode.ANCHOR_RECALL_NOT_FULL)
    if unsupported_critical_claims:
        failures.add(PermanentFailureCode.UNSUPPORTED_CRITICAL_CLAIMS)
    if declared_coverage_gap:
        failures.add(PermanentFailureCode.QUALITY_COVERAGE_GAP)

    if (
        token_ceiling < 0
        or candidate_snapshot.token_cost > token_ceiling
        or sum(capsule.token_cost for capsule in candidate_capsules) > token_ceiling
    ):
        failures.add(PermanentFailureCode.TOKEN_CEILING_EXCEEDED)

    ordered_failures = tuple(code for code in PermanentFailureCode if code in failures)
    return PermanentValidationReport(
        passed=not ordered_failures,
        failure_codes=ordered_failures,
        source_coverage=source_coverage,
        anchor_recall=anchor_recall,
        coverage_gap=coverage_gap,
        unsupported_critical_claims=unsupported_critical_claims,
    )
