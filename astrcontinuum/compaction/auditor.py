from __future__ import annotations

import re

from ..domain.snapshots import (
    SemanticAuditStatus,
    SnapshotAuditOutcome,
    SnapshotEnvelope,
    SnapshotState,
)
from ..domain.validation import PermanentValidationReport
from ..storage.repository import SnapshotCapsuleMembership
from .types import (
    AuditedCandidate,
    CompilationCandidate,
    EventSegment,
    SemanticAuditBackend,
    SemanticAuditErrorCode,
    SemanticAuditInvariantError,
    SemanticAuditReport,
    SemanticAuditRequest,
)

_MAX_FAILURE_CODES = 32
_FAILURE_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


async def audit_semantic(
    candidate: CompilationCandidate,
    *,
    strict_audit: bool,
    backend: SemanticAuditBackend | None,
) -> AuditedCandidate:
    if not _is_mechanically_eligible(candidate):
        raise SemanticAuditInvariantError(SemanticAuditErrorCode.MECHANICAL_INVALID)
    if type(strict_audit) is not bool:
        raise SemanticAuditInvariantError(SemanticAuditErrorCode.STRICT_AUDIT_INVALID)
    if not strict_audit:
        return _build_audited_candidate(
            candidate,
            snapshot=candidate.snapshot,
            semantic_report=None,
        )
    if backend is None:
        raise SemanticAuditInvariantError(SemanticAuditErrorCode.BACKEND_REQUIRED)

    request = SemanticAuditRequest(
        snapshot=candidate.snapshot,
        capsules=tuple(item.capsule for item in candidate.memberships),
        segments=candidate.segments,
        permanent_report=candidate.permanent_report,
    )
    try:
        report = await backend.audit(request)
    except Exception:  # noqa: BLE001 - adapter boundary maps arbitrary failures.
        raise SemanticAuditInvariantError(SemanticAuditErrorCode.BACKEND_FAILURE) from None
    if not _is_valid_backend_report(report):
        raise SemanticAuditInvariantError(SemanticAuditErrorCode.BACKEND_OUTPUT_INVALID)

    if report.passed:
        return _build_audited_candidate(
            candidate,
            snapshot=_snapshot_with_semantic_result(
                candidate.snapshot,
                status=SemanticAuditStatus.PASSED,
                failure_codes=(),
            ),
            semantic_report=report,
        )

    failed_candidate = _build_audited_candidate(
        candidate,
        snapshot=_snapshot_with_semantic_result(
            candidate.snapshot,
            status=SemanticAuditStatus.FAILED,
            failure_codes=report.failure_codes,
        ),
        semantic_report=report,
    )
    raise SemanticAuditInvariantError(
        SemanticAuditErrorCode.SEMANTIC_REJECTED,
        failed_candidate=failed_candidate,
    )


def _is_mechanically_eligible(candidate: object) -> bool:
    if not isinstance(candidate, CompilationCandidate):
        return False
    report = candidate.permanent_report
    if (
        not isinstance(report, PermanentValidationReport)
        or report.passed is not True
        or report.failure_codes != ()
        or type(report.source_coverage) is not float
        or report.source_coverage != 1.0
        or type(report.anchor_recall) is not float
        or report.anchor_recall != 1.0
        or type(report.coverage_gap) is not int
        or report.coverage_gap != 0
        or type(report.unsupported_critical_claims) is not int
        or report.unsupported_critical_claims != 0
    ):
        return False
    snapshot = candidate.snapshot
    if not isinstance(snapshot, SnapshotEnvelope):
        return False
    outcome = snapshot.audit_outcome
    if (
        snapshot.state is not SnapshotState.CANDIDATE
        or outcome.mechanical_passed is not True
        or outcome.semantic_status is not SemanticAuditStatus.NOT_RUN
        or outcome.failure_codes != ()
    ):
        return False
    if not isinstance(candidate.memberships, tuple) or any(
        not isinstance(item, SnapshotCapsuleMembership) for item in candidate.memberships
    ):
        return False
    return isinstance(candidate.segments, tuple) and all(
        isinstance(item, EventSegment) for item in candidate.segments
    )


def _is_valid_backend_report(report: object) -> bool:
    if (
        not isinstance(report, SemanticAuditReport)
        or type(report.passed) is not bool
        or not isinstance(report.failure_codes, tuple)
        or len(report.failure_codes) > _MAX_FAILURE_CODES
        or any(type(code) is not str for code in report.failure_codes)
    ):
        return False
    failure_codes = report.failure_codes
    if report.passed is not (not failure_codes):
        return False
    if len(set(failure_codes)) != len(failure_codes):
        return False
    return all(_FAILURE_CODE_PATTERN.fullmatch(code) is not None for code in failure_codes)


def _snapshot_with_semantic_result(
    snapshot: SnapshotEnvelope,
    *,
    status: SemanticAuditStatus,
    failure_codes: tuple[str, ...],
) -> SnapshotEnvelope:
    return snapshot.model_copy(
        update={
            "audit_outcome": SnapshotAuditOutcome(
                mechanical_passed=True,
                semantic_status=status,
                failure_codes=failure_codes,
            )
        }
    )


def _build_audited_candidate(
    candidate: CompilationCandidate,
    *,
    snapshot: SnapshotEnvelope,
    semantic_report: SemanticAuditReport | None,
) -> AuditedCandidate:
    return AuditedCandidate(
        snapshot=snapshot,
        memberships=candidate.memberships,
        segments=candidate.segments,
        permanent_report=candidate.permanent_report,
        semantic_report=semantic_report,
    )
