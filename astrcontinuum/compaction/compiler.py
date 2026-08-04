from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from ..domain.capsules import AnchorStatus, ContextCapsuleEnvelope
from ..domain.events import EventEnvelope
from ..domain.identity import SessionKey
from ..domain.snapshots import (
    SemanticAuditStatus,
    SnapshotAuditOutcome,
    SnapshotEnvelope,
    SnapshotState,
)
from ..domain.validation import PermanentValidationReport
from ..reorganization import (
    ReorganizationBudgetError,
    ReorganizationInvariantError,
    ReorganizationRecord,
    reorganize_capsules,
)
from ..runtime.types import TokenCounter
from ..storage.repository import SnapshotCapsuleMembership
from .rendering import render_capsule
from .segmenter import segment
from .types import (
    CompactionFitProvenance,
    CompactionProviderBinding,
    CompilationCandidate,
    CompilationRequest,
    CompilerBackend,
    CompilerBackendDeferred,
    CompilerErrorCode,
    CompilerInvariantError,
    CompilerOutput,
    EventSegment,
    SegmenterConfig,
)
from .validator import validate_candidate

_SNAPSHOT_ID_SCHEMA_TAG = "astrcontinuum.compilation-candidate.v1"
_MEMORY_MEMBERSHIP_SLOT = "memory"


async def compile_candidate(
    *,
    base_snapshot: SnapshotEnvelope | None,
    base_capsules: Sequence[ContextCapsuleEnvelope],
    source_events: Sequence[EventEnvelope],
    event_token_counts: Mapping[str, int],
    target_high_water_mark: int,
    token_ceiling: int,
    backend: CompilerBackend,
    now: datetime,
    segmenter_config: SegmenterConfig,
    preferred_end_sequences: Sequence[int] = (),
    compatibility_counter: TokenCounter | None = None,
    counter: TokenCounter | None = None,
    provider_binding: CompactionProviderBinding | None = None,
    provider_binding_captured: bool = False,
) -> CompilationCandidate:
    if compatibility_counter is not None and counter is not None:
        raise TypeError("provide only compatibility_counter")
    resolved_compatibility_counter = (
        compatibility_counter if compatibility_counter is not None else counter
    )
    if resolved_compatibility_counter is None:
        raise TypeError("compatibility_counter must be provided")
    base_capsule_tuple = tuple(base_capsules)
    source_event_tuple = tuple(source_events)
    session_key = _validate_inputs(
        base_snapshot=base_snapshot,
        base_capsules=base_capsule_tuple,
        source_events=source_event_tuple,
        target_high_water_mark=target_high_water_mark,
        token_ceiling=token_ceiling,
        now=now,
        segmenter_config=segmenter_config,
    )
    segments = segment(
        source_event_tuple,
        token_counts=event_token_counts,
        config=segmenter_config,
        preferred_end_sequences=preferred_end_sequences,
    )
    request = CompilationRequest(
        base_snapshot=base_snapshot,
        base_capsules=base_capsule_tuple,
        source_events=source_event_tuple,
        segments=segments,
        target_high_water_mark=target_high_water_mark,
        token_ceiling=token_ceiling,
        canonical_event_token_counts=event_token_counts,
        provider_binding=provider_binding,
        provider_binding_captured=provider_binding_captured,
    )

    try:
        output = await backend.compile(request)
    except CompilerBackendDeferred:
        raise
    except Exception:  # noqa: BLE001 - adapter boundary maps arbitrary failures.
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_FAILURE) from None

    (
        candidate_capsules,
        rendered_context,
        fitted_segments,
        fit_provenance,
    ) = _validate_backend_output(
        output,
        source_events=source_event_tuple,
        original_segments=segments,
    )
    token_cost = _count_rendered_context(resolved_compatibility_counter, rendered_context)

    active_anchor_ids = _active_anchor_ids(candidate_capsules)
    try:
        snapshot_id = _build_snapshot_id(
            session_key=session_key,
            base_snapshot_id=base_snapshot.snapshot_id if base_snapshot is not None else None,
            target_high_water_mark=target_high_water_mark,
            capsules=candidate_capsules,
            active_anchor_ids=active_anchor_ids,
            rendered_context=rendered_context,
            token_cost=token_cost,
        )
        candidate_snapshot = SnapshotEnvelope(
            snapshot_id=snapshot_id,
            session_key=session_key,
            base_snapshot_id=base_snapshot.snapshot_id if base_snapshot is not None else None,
            covered_event_end=target_high_water_mark,
            source_high_water_mark=target_high_water_mark,
            capsule_ids=tuple(item.capsule_id for item in candidate_capsules),
            exact_anchor_ids=active_anchor_ids,
            rendered_context=rendered_context,
            token_cost=token_cost,
            audit_outcome=SnapshotAuditOutcome(
                mechanical_passed=True,
                semantic_status=SemanticAuditStatus.NOT_RUN,
                failure_codes=(),
            ),
            state=SnapshotState.CANDIDATE,
            created_at=now,
            committed_at=None,
        )
    except (TypeError, ValueError):
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_OUTPUT_INVALID) from None

    memberships = tuple(
        SnapshotCapsuleMembership(
            ordinal=ordinal,
            slot=_MEMORY_MEMBERSHIP_SLOT,
            capsule=item,
        )
        for ordinal, item in enumerate(candidate_capsules)
    )
    try:
        report = validate_candidate(
            base_snapshot=base_snapshot,
            base_capsules=base_capsule_tuple,
            candidate_snapshot=candidate_snapshot,
            candidate_capsules=candidate_capsules,
            source_events=source_event_tuple,
            target_high_water_mark=target_high_water_mark,
            token_ceiling=token_ceiling,
        )
    except Exception:  # noqa: BLE001 - validator boundary maps arbitrary failures.
        raise CompilerInvariantError(CompilerErrorCode.PERMANENT_VALIDATOR_FAILURE) from None
    if not isinstance(report, PermanentValidationReport):
        raise CompilerInvariantError(CompilerErrorCode.PERMANENT_VALIDATOR_FAILURE)
    if not report.passed:
        raise CompilerInvariantError(
            CompilerErrorCode.PERMANENT_VALIDATION_FAILED,
            report=report,
        )

    return CompilationCandidate(
        snapshot=candidate_snapshot,
        memberships=memberships,
        segments=fitted_segments,
        permanent_report=report,
        fit_provenance=fit_provenance,
    )


def reorganize_candidate(
    *,
    base_snapshot: SnapshotEnvelope | None,
    base_capsules: Sequence[ContextCapsuleEnvelope],
    candidate: CompilationCandidate,
    source_events: Sequence[EventEnvelope],
    target_high_water_mark: int,
    token_ceiling: int,
    token_budget: int,
    counter: TokenCounter,
) -> tuple[CompilationCandidate, tuple[ReorganizationRecord, ...]]:
    """Rebuild one validated candidate from its deterministic reorganization."""

    base_capsule_tuple = tuple(base_capsules)
    _validate_base_admission(
        base_snapshot=base_snapshot,
        base_capsules=base_capsule_tuple,
    )
    candidate_capsules = tuple(membership.capsule for membership in candidate.memberships)
    try:
        result = reorganize_capsules(
            candidate_capsules,
            token_budget=token_budget,
            counter=counter,
        )
    except (ReorganizationBudgetError, ReorganizationInvariantError, TypeError, ValueError):
        raise CompilerInvariantError(CompilerErrorCode.REORGANIZATION_FAILED) from None

    capsule = result.capsule
    capsules = (capsule,)
    rendered_context = render_capsule(capsule)
    token_cost = _count_rendered_context(counter, rendered_context)
    active_anchor_ids = _active_anchor_ids(capsules)
    source_snapshot = candidate.snapshot
    try:
        snapshot = SnapshotEnvelope(
            snapshot_id=_build_snapshot_id(
                session_key=source_snapshot.session_key,
                base_snapshot_id=source_snapshot.base_snapshot_id,
                target_high_water_mark=target_high_water_mark,
                capsules=capsules,
                active_anchor_ids=active_anchor_ids,
                rendered_context=rendered_context,
                token_cost=token_cost,
            ),
            session_key=source_snapshot.session_key,
            base_snapshot_id=source_snapshot.base_snapshot_id,
            covered_event_end=target_high_water_mark,
            source_high_water_mark=target_high_water_mark,
            capsule_ids=(capsule.capsule_id,),
            exact_anchor_ids=active_anchor_ids,
            rendered_context=rendered_context,
            token_cost=token_cost,
            audit_outcome=source_snapshot.audit_outcome,
            state=SnapshotState.CANDIDATE,
            created_at=source_snapshot.created_at,
            committed_at=None,
        )
        memberships = (
            SnapshotCapsuleMembership(
                ordinal=0,
                slot=_MEMORY_MEMBERSHIP_SLOT,
                capsule=capsule,
            ),
        )
    except (TypeError, ValueError):
        raise CompilerInvariantError(CompilerErrorCode.REORGANIZATION_FAILED) from None

    try:
        report = validate_candidate(
            base_snapshot=base_snapshot,
            base_capsules=base_capsule_tuple,
            candidate_snapshot=snapshot,
            candidate_capsules=capsules,
            source_events=source_events,
            target_high_water_mark=target_high_water_mark,
            token_ceiling=token_ceiling,
        )
    except Exception:  # noqa: BLE001 - validator boundary maps arbitrary failures.
        raise CompilerInvariantError(CompilerErrorCode.PERMANENT_VALIDATOR_FAILURE) from None
    if not isinstance(report, PermanentValidationReport):
        raise CompilerInvariantError(CompilerErrorCode.PERMANENT_VALIDATOR_FAILURE)
    if not report.passed:
        raise CompilerInvariantError(
            CompilerErrorCode.PERMANENT_VALIDATION_FAILED,
            report=report,
        )

    return (
        CompilationCandidate(
            snapshot=snapshot,
            memberships=memberships,
            segments=candidate.segments,
            permanent_report=report,
            fit_provenance=candidate.fit_provenance,
        ),
        result.records,
    )


def _count_rendered_context(counter: TokenCounter, rendered_context: str) -> int:
    try:
        token_cost = counter.count_text(rendered_context)
    except Exception:  # noqa: BLE001 - adapter boundary maps arbitrary failures.
        raise CompilerInvariantError(CompilerErrorCode.TOKEN_COUNTER_FAILURE) from None
    if isinstance(token_cost, bool) or not isinstance(token_cost, int) or token_cost < 0:
        raise CompilerInvariantError(CompilerErrorCode.TOKEN_COUNTER_INVALID)
    return token_cost


def _validate_inputs(
    *,
    base_snapshot: SnapshotEnvelope | None,
    base_capsules: tuple[ContextCapsuleEnvelope, ...],
    source_events: tuple[EventEnvelope, ...],
    target_high_water_mark: object,
    token_ceiling: object,
    now: object,
    segmenter_config: object,
) -> SessionKey:
    if (
        isinstance(target_high_water_mark, bool)
        or not isinstance(target_high_water_mark, int)
        or target_high_water_mark < 1
    ):
        raise CompilerInvariantError(CompilerErrorCode.TARGET_INVALID)
    if isinstance(token_ceiling, bool) or not isinstance(token_ceiling, int) or token_ceiling < 0:
        raise CompilerInvariantError(CompilerErrorCode.TOKEN_CEILING_INVALID)
    if not _is_aware_datetime(now):
        raise CompilerInvariantError(CompilerErrorCode.NOW_INVALID)
    if not isinstance(segmenter_config, SegmenterConfig):
        raise CompilerInvariantError(CompilerErrorCode.SEGMENTER_CONFIG_INVALID)
    if not source_events:
        raise CompilerInvariantError(CompilerErrorCode.SOURCE_EVENTS_EMPTY)
    if any(not isinstance(item, EventEnvelope) for item in source_events):
        raise CompilerInvariantError(CompilerErrorCode.SOURCE_EVENTS_INVALID)

    base_coverage = _validate_base_admission(
        base_snapshot=base_snapshot,
        base_capsules=base_capsules,
    )
    session_key = (
        source_events[0].session_key if base_snapshot is None else base_snapshot.session_key
    )

    if any(item.session_key != session_key for item in source_events):
        raise CompilerInvariantError(CompilerErrorCode.SOURCE_SESSION_MISMATCH)
    if (
        target_high_water_mark <= base_coverage
        or len(source_events) != target_high_water_mark - base_coverage
        or any(
            item.sequence != base_coverage + offset
            for offset, item in enumerate(source_events, start=1)
        )
        or len({item.event_id for item in source_events}) != len(source_events)
    ):
        raise CompilerInvariantError(CompilerErrorCode.SOURCE_COVERAGE_MISMATCH)
    return session_key


def _validate_base_admission(
    *,
    base_snapshot: SnapshotEnvelope | None,
    base_capsules: Sequence[ContextCapsuleEnvelope],
) -> int:
    if base_snapshot is None:
        if base_capsules:
            raise CompilerInvariantError(CompilerErrorCode.BASE_CAPSULE_MISMATCH)
        return 0
    if (
        not isinstance(base_snapshot, SnapshotEnvelope)
        or base_snapshot.state is not SnapshotState.COMMITTED
        or base_snapshot.covered_event_end != base_snapshot.source_high_water_mark
    ):
        raise CompilerInvariantError(CompilerErrorCode.BASE_INVALID)
    if (
        any(not isinstance(item, ContextCapsuleEnvelope) for item in base_capsules)
        or base_snapshot.capsule_ids != tuple(item.capsule_id for item in base_capsules)
        or base_snapshot.exact_anchor_ids != _active_anchor_ids(base_capsules)
        or any(item.session_key != base_snapshot.session_key for item in base_capsules)
    ):
        raise CompilerInvariantError(CompilerErrorCode.BASE_CAPSULE_MISMATCH)
    return base_snapshot.covered_event_end


def _validate_backend_output(
    output: object,
    *,
    source_events: tuple[EventEnvelope, ...],
    original_segments: tuple[EventSegment, ...],
) -> tuple[
    tuple[ContextCapsuleEnvelope, ...],
    str,
    tuple[EventSegment, ...],
    CompactionFitProvenance | None,
]:
    if not isinstance(output, CompilerOutput):
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_OUTPUT_INVALID)
    if not isinstance(output.capsules, tuple) or not isinstance(output.rendered_context, str):
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_OUTPUT_INVALID)
    if not output.capsules or not output.rendered_context:
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_OUTPUT_EMPTY)
    if any(not isinstance(item, ContextCapsuleEnvelope) for item in output.capsules):
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_OUTPUT_INVALID)
    fitted_segments = (
        original_segments if output.fitted_segments is None else output.fitted_segments
    )
    if (
        not isinstance(fitted_segments, tuple)
        or not fitted_segments
        or any(not isinstance(item, EventSegment) for item in fitted_segments)
        or tuple(event for segment in fitted_segments for event in segment.events) != source_events
    ):
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_OUTPUT_INVALID)
    fit_provenance = output.fit_provenance
    if fit_provenance is not None and not isinstance(
        fit_provenance,
        CompactionFitProvenance,
    ):
        raise CompilerInvariantError(CompilerErrorCode.BACKEND_OUTPUT_INVALID)
    return (
        output.capsules,
        output.rendered_context,
        fitted_segments,
        fit_provenance,
    )


def _active_anchor_ids(
    capsules: Sequence[ContextCapsuleEnvelope],
) -> tuple[str, ...]:
    return tuple(
        anchor.anchor_id
        for capsule in capsules
        for anchor in capsule.exact_anchors
        if anchor.status is AnchorStatus.ACTIVE
    )


def _build_snapshot_id(
    *,
    session_key: SessionKey,
    base_snapshot_id: str | None,
    target_high_water_mark: int,
    capsules: tuple[ContextCapsuleEnvelope, ...],
    active_anchor_ids: tuple[str, ...],
    rendered_context: str,
    token_cost: int,
) -> str:
    identity_payload = {
        "schema": _SNAPSHOT_ID_SCHEMA_TAG,
        "session_key": session_key.canonical_json(),
        "base_snapshot_id": base_snapshot_id,
        "target_high_water_mark": target_high_water_mark,
        "capsules": [item.model_dump(mode="json") for item in capsules],
        "active_anchor_ids": list(active_anchor_ids),
        "rendered_context": rendered_context,
        "token_cost": token_cost,
    }
    canonical_json = json.dumps(
        identity_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _is_aware_datetime(value: object) -> bool:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except Exception:  # noqa: BLE001 - custom tzinfo may execute user code.
        return False
