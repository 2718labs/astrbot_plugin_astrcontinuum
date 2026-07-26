from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import datetime, timezone
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
SECRET = "SENTINEL_PRIVATE_AUDIT_CONTENT"
AUDIT_SURFACE_NAMES = (
    "SemanticAuditRequest",
    "SemanticAuditReport",
    "SemanticAuditBackend",
    "AuditedCandidate",
    "SemanticAuditErrorCode",
    "SemanticAuditInvariantError",
    "audit_semantic",
)


def audit_surface() -> dict[str, Any]:
    values = {name: getattr(ac, name, None) for name in AUDIT_SURFACE_NAMES}
    missing = tuple(name for name, value in values.items() if value is None)
    assert not missing, f"canonical semantic audit surface is missing: {', '.join(missing)}"
    return values


def session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="session-1",
        group_id=None,
        user_id="user-1",
        conversation_id="conversation-1",
        persona_id=None,
    )


def event(sequence: int) -> ac.EventEnvelope:
    return ac.EventEnvelope.create(
        event_id=f"event-{sequence}",
        session_key=session_key(),
        sequence=sequence,
        event_type=ac.EventType.USER_MESSAGE,
        content=f"{SECRET} event {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW,
    )


def claim(claim_id: str, source_event_id: str) -> ac.CapsuleClaim:
    return ac.CapsuleClaim(
        claim_id=claim_id,
        text=f"claim {claim_id}",
        status=ac.SemanticStatus.ACTIVE,
        confidence=1.0,
        source_event_ids=(source_event_id,),
    )


def anchor(anchor_id: str, source_event_id: str) -> ac.CapsuleAnchor:
    return ac.CapsuleAnchor(
        anchor_id=anchor_id,
        anchor_type=ac.AnchorType.NAME,
        exact_text=f"anchor {anchor_id}",
        source_event_ids=(source_event_id,),
        status=ac.AnchorStatus.ACTIVE,
        importance=1.0,
    )


def capsule(capsule_id: str, sequence: int) -> ac.ContextCapsuleEnvelope:
    source_event_id = f"event-{sequence}"
    return ac.ContextCapsuleEnvelope(
        capsule_id=capsule_id,
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=session_key(),
        covered_event_start=sequence,
        covered_event_end=sequence,
        source_event_ids=(source_event_id,),
        goals=(claim(f"goal-{capsule_id}", source_event_id),),
        constraints=(),
        decisions=(),
        progress=(),
        open_loops=(),
        preferences=(),
        entities=(),
        emotional_context=(),
        exact_anchors=(anchor(f"anchor-{capsule_id}", source_event_id),),
        dependencies=(),
        narrative_summary=f"{SECRET} capsule {capsule_id}",
        token_cost=3,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    )


def audit_outcome(
    *,
    mechanical_passed: bool = True,
    semantic_status: ac.SemanticAuditStatus = ac.SemanticAuditStatus.NOT_RUN,
    failure_codes: tuple[str, ...] = (),
) -> ac.SnapshotAuditOutcome:
    return ac.SnapshotAuditOutcome(
        mechanical_passed=mechanical_passed,
        semantic_status=semantic_status,
        failure_codes=failure_codes,
    )


def permanent_report(
    *,
    passed: bool = True,
    failure_codes: tuple[ac.PermanentFailureCode, ...] = (),
    source_coverage: float = 1.0,
    anchor_recall: float = 1.0,
    coverage_gap: int = 0,
    unsupported_critical_claims: int = 0,
) -> ac.PermanentValidationReport:
    return ac.PermanentValidationReport(
        passed=passed,
        failure_codes=failure_codes,
        source_coverage=source_coverage,
        anchor_recall=anchor_recall,
        coverage_gap=coverage_gap,
        unsupported_critical_claims=unsupported_critical_claims,
    )


def compilation_candidate() -> ac.CompilationCandidate:
    events = (event(1), event(2))
    capsules = (capsule("capsule-a", 1), capsule("capsule-b", 2))
    snapshot = ac.SnapshotEnvelope(
        snapshot_id="candidate-snapshot",
        session_key=session_key(),
        base_snapshot_id=None,
        covered_event_end=2,
        source_high_water_mark=2,
        capsule_ids=tuple(item.capsule_id for item in capsules),
        exact_anchor_ids=tuple(
            item.anchor_id for capsule_item in capsules for item in capsule_item.exact_anchors
        ),
        rendered_context=f"{SECRET} rendered context",
        token_cost=sum(item.token_cost for item in capsules),
        audit_outcome=audit_outcome(),
        state=ac.SnapshotState.CANDIDATE,
        created_at=NOW,
        committed_at=None,
    )
    memberships = tuple(
        ac.SnapshotCapsuleMembership(
            ordinal=ordinal,
            slot="memory",
            capsule=item,
        )
        for ordinal, item in enumerate(capsules)
    )
    segments = (
        ac.EventSegment(
            start_sequence=1,
            end_sequence=1,
            events=(events[0],),
            token_cost=events[0].token_count,
            boundary_reason=ac.SegmentBoundaryReason.MAX_EVENTS,
        ),
        ac.EventSegment(
            start_sequence=2,
            end_sequence=2,
            events=(events[1],),
            token_cost=events[1].token_count,
            boundary_reason=ac.SegmentBoundaryReason.END_OF_INPUT,
        ),
    )
    return ac.CompilationCandidate(
        snapshot=snapshot,
        memberships=memberships,
        segments=segments,
        permanent_report=permanent_report(),
    )


def semantic_report(passed: object, failure_codes: object) -> Any:
    report_type = audit_surface()["SemanticAuditReport"]
    return report_type(passed=passed, failure_codes=failure_codes)


class RecordingAuditBackend:
    def __init__(self, output: object = None, *, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.requests: list[Any] = []

    async def audit(self, request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.output


async def audit_with(
    candidate: object,
    *,
    strict_audit: object,
    backend: object,
) -> Any:
    return await audit_surface()["audit_semantic"](
        candidate,
        strict_audit=strict_audit,
        backend=backend,
    )


def assert_stable_error(error: Any, expected_name: str) -> None:
    surface = audit_surface()
    expected_code = getattr(surface["SemanticAuditErrorCode"], expected_name)
    assert error.code is expected_code
    assert str(error) == expected_code.value
    assert SECRET not in str(error)


def invalid_mechanical_candidate(case: str) -> object:
    candidate = compilation_candidate()
    report = candidate.permanent_report
    snapshot = candidate.snapshot
    if case == "candidate_type":
        return object()
    if case == "permanent_type":
        return replace(candidate, permanent_report=object())
    if case == "permanent_passed_not_bool":
        return replace(
            candidate,
            permanent_report=report.model_copy(update={"passed": 1}),
        )
    if case == "permanent_failed":
        return replace(
            candidate,
            permanent_report=permanent_report(
                passed=False,
                failure_codes=(ac.PermanentFailureCode.COVERAGE_GAP,),
            ),
        )
    if case == "permanent_codes":
        return replace(
            candidate,
            permanent_report=permanent_report(
                failure_codes=(ac.PermanentFailureCode.COVERAGE_GAP,),
            ),
        )
    if case == "permanent_source_coverage":
        return replace(
            candidate, permanent_report=report.model_copy(update={"source_coverage": 0.9})
        )
    if case == "permanent_anchor_recall":
        return replace(candidate, permanent_report=report.model_copy(update={"anchor_recall": 0.9}))
    if case == "permanent_gap":
        return replace(candidate, permanent_report=report.model_copy(update={"coverage_gap": 1}))
    if case == "permanent_unsupported":
        return replace(
            candidate,
            permanent_report=report.model_copy(update={"unsupported_critical_claims": 1}),
        )
    if case == "snapshot_type":
        return replace(candidate, snapshot=object())
    if case == "snapshot_state":
        return replace(
            candidate,
            snapshot=snapshot.model_copy(
                update={
                    "state": ac.SnapshotState.COMMITTED,
                    "committed_at": NOW,
                }
            ),
        )
    if case == "snapshot_mechanical":
        return replace(
            candidate,
            snapshot=snapshot.model_copy(
                update={"audit_outcome": audit_outcome(mechanical_passed=False)}
            ),
        )
    if case == "snapshot_semantic":
        return replace(
            candidate,
            snapshot=snapshot.model_copy(
                update={
                    "audit_outcome": audit_outcome(semantic_status=ac.SemanticAuditStatus.PASSED)
                }
            ),
        )
    if case == "snapshot_codes":
        return replace(
            candidate,
            snapshot=snapshot.model_copy(
                update={"audit_outcome": audit_outcome(failure_codes=("PRIOR_FAILURE",))}
            ),
        )
    if case == "memberships":
        return replace(candidate, memberships=(object(),))
    if case == "segments":
        return replace(candidate, segments=(object(),))
    raise AssertionError(f"unknown test case: {case}")


def invalid_backend_output(case: str) -> object:
    if case == "not_report":
        return object()
    if case == "passed_not_bool":
        return semantic_report(1, ())
    if case == "codes_not_tuple":
        return semantic_report(True, [])
    if case == "passed_with_codes":
        return semantic_report(True, ("VALID_CODE",))
    if case == "failed_without_codes":
        return semantic_report(False, ())
    if case == "duplicate_codes":
        return semantic_report(False, ("DUPLICATE", "DUPLICATE"))
    if case == "too_many_codes":
        return semantic_report(False, tuple(f"CODE_{index}" for index in range(33)))
    if case == "empty_code":
        return semantic_report(False, ("",))
    if case == "lowercase_code":
        return semantic_report(False, ("lowercase",))
    if case == "invalid_character":
        return semantic_report(False, (f"{SECRET}-BAD",))
    if case == "too_long":
        return semantic_report(False, ("A" * 65,))
    if case == "non_string":
        return semantic_report(False, (object(),))
    raise AssertionError(f"unknown test case: {case}")


def test_semantic_auditor_is_exported_on_both_canonical_surfaces() -> None:
    root_surface = audit_surface()
    compaction = importlib.import_module("astrcontinuum.compaction")

    assert {name: getattr(compaction, name) for name in AUDIT_SURFACE_NAMES} == root_surface
    assert getattr(root_surface["SemanticAuditBackend"], "_is_protocol", False)
    for name in ("SemanticAuditRequest", "SemanticAuditReport", "AuditedCandidate"):
        data_type = root_surface[name]
        assert is_dataclass(data_type)
        assert data_type.__dataclass_params__.frozen
        assert "__dict__" not in data_type.__slots__
    error_codes = root_surface["SemanticAuditErrorCode"]
    assert {
        name: getattr(error_codes, name).value
        for name in (
            "MECHANICAL_INVALID",
            "STRICT_AUDIT_INVALID",
            "BACKEND_REQUIRED",
            "BACKEND_FAILURE",
            "BACKEND_OUTPUT_INVALID",
            "SEMANTIC_REJECTED",
        )
    } == {
        "MECHANICAL_INVALID": "SEMANTIC_AUDIT_MECHANICAL_INVALID",
        "STRICT_AUDIT_INVALID": "SEMANTIC_AUDIT_STRICT_AUDIT_INVALID",
        "BACKEND_REQUIRED": "SEMANTIC_AUDIT_BACKEND_REQUIRED",
        "BACKEND_FAILURE": "SEMANTIC_AUDIT_BACKEND_FAILURE",
        "BACKEND_OUTPUT_INVALID": "SEMANTIC_AUDIT_BACKEND_OUTPUT_INVALID",
        "SEMANTIC_REJECTED": "SEMANTIC_AUDIT_SEMANTIC_REJECTED",
    }


@pytest.mark.parametrize("strict_audit", [False, True])
@pytest.mark.asyncio
async def test_mechanical_invalid_precedes_disabled_and_enabled_branches(
    strict_audit: bool,
) -> None:
    backend = RecordingAuditBackend(semantic_report(True, ()))
    error_type = audit_surface()["SemanticAuditInvariantError"]

    with pytest.raises(error_type) as caught:
        await audit_with(
            invalid_mechanical_candidate("permanent_failed"),
            strict_audit=strict_audit,
            backend=backend,
        )

    assert_stable_error(caught.value, "MECHANICAL_INVALID")
    assert caught.value.failed_candidate is None
    assert backend.requests == []


@pytest.mark.parametrize(
    "case",
    [
        "candidate_type",
        "permanent_type",
        "permanent_passed_not_bool",
        "permanent_codes",
        "permanent_source_coverage",
        "permanent_anchor_recall",
        "permanent_gap",
        "permanent_unsupported",
        "snapshot_type",
        "snapshot_state",
        "snapshot_mechanical",
        "snapshot_semantic",
        "snapshot_codes",
        "memberships",
        "segments",
    ],
)
@pytest.mark.asyncio
async def test_mechanical_qualification_rejects_incomplete_candidate(case: str) -> None:
    backend = RecordingAuditBackend(semantic_report(True, ()))
    error_type = audit_surface()["SemanticAuditInvariantError"]

    with pytest.raises(error_type) as caught:
        await audit_with(
            invalid_mechanical_candidate(case),
            strict_audit=True,
            backend=backend,
        )

    assert_stable_error(caught.value, "MECHANICAL_INVALID")
    assert backend.requests == []


@pytest.mark.parametrize("strict_audit", [0, 1, None, "true", ()])
@pytest.mark.asyncio
async def test_strict_audit_must_be_a_real_bool(strict_audit: object) -> None:
    backend = RecordingAuditBackend(semantic_report(True, ()))
    error_type = audit_surface()["SemanticAuditInvariantError"]

    with pytest.raises(error_type) as caught:
        await audit_with(
            compilation_candidate(),
            strict_audit=strict_audit,
            backend=backend,
        )

    assert_stable_error(caught.value, "STRICT_AUDIT_INVALID")
    assert backend.requests == []


@pytest.mark.parametrize("provide_backend", [False, True])
@pytest.mark.asyncio
async def test_disabled_audit_keeps_not_run_and_never_calls_backend(
    provide_backend: bool,
) -> None:
    candidate = compilation_candidate()
    backend = RecordingAuditBackend(error=RuntimeError(SECRET)) if provide_backend else None

    audited = await audit_with(
        candidate,
        strict_audit=False,
        backend=backend,
    )

    if backend is not None:
        assert backend.requests == []
    assert audited.snapshot is candidate.snapshot
    assert audited.snapshot.audit_outcome.semantic_status is ac.SemanticAuditStatus.NOT_RUN
    assert audited.semantic_report is None
    assert audited.memberships is candidate.memberships
    assert audited.segments is candidate.segments
    assert audited.permanent_report is candidate.permanent_report
    with pytest.raises(FrozenInstanceError):
        audited.semantic_report = semantic_report(True, ())


@pytest.mark.asyncio
async def test_enabled_pass_calls_backend_once_with_exact_owned_request() -> None:
    candidate = compilation_candidate()
    report = semantic_report(True, ())
    backend = RecordingAuditBackend(report)

    audited = await audit_with(
        candidate,
        strict_audit=True,
        backend=backend,
    )

    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert tuple(item.name for item in fields(request)) == (
        "snapshot",
        "capsules",
        "segments",
        "permanent_report",
    )
    assert request.snapshot is candidate.snapshot
    assert request.capsules == tuple(item.capsule for item in candidate.memberships)
    assert all(
        actual is membership.capsule
        for actual, membership in zip(
            request.capsules,
            candidate.memberships,
            strict=True,
        )
    )
    assert request.segments is candidate.segments
    assert request.permanent_report is candidate.permanent_report
    assert audited.snapshot is not candidate.snapshot
    assert audited.snapshot.snapshot_id == candidate.snapshot.snapshot_id
    assert (
        audited.snapshot.model_copy(update={"audit_outcome": candidate.snapshot.audit_outcome})
        == candidate.snapshot
    )
    assert audited.snapshot.audit_outcome == audit_outcome(
        semantic_status=ac.SemanticAuditStatus.PASSED
    )
    assert candidate.snapshot.audit_outcome.semantic_status is ac.SemanticAuditStatus.NOT_RUN
    assert audited.memberships is candidate.memberships
    assert audited.segments is candidate.segments
    assert audited.permanent_report is candidate.permanent_report
    assert audited.semantic_report is report
    with pytest.raises(FrozenInstanceError):
        request.snapshot = candidate.snapshot
    with pytest.raises(FrozenInstanceError):
        report.passed = False
    with pytest.raises(FrozenInstanceError):
        audited.semantic_report = None


@pytest.mark.asyncio
async def test_enabled_failure_raises_with_read_only_failed_evidence() -> None:
    candidate = compilation_candidate()
    report = semantic_report(False, (SECRET,))
    backend = RecordingAuditBackend(report)
    error_type = audit_surface()["SemanticAuditInvariantError"]

    with pytest.raises(error_type) as caught:
        await audit_with(
            candidate,
            strict_audit=True,
            backend=backend,
        )

    assert len(backend.requests) == 1
    assert_stable_error(caught.value, "SEMANTIC_REJECTED")
    failed = caught.value.failed_candidate
    assert failed is not None
    assert failed.snapshot.snapshot_id == candidate.snapshot.snapshot_id
    assert (
        failed.snapshot.model_copy(update={"audit_outcome": candidate.snapshot.audit_outcome})
        == candidate.snapshot
    )
    assert failed.snapshot.audit_outcome.mechanical_passed is True
    assert failed.snapshot.audit_outcome.semantic_status is ac.SemanticAuditStatus.FAILED
    assert failed.snapshot.audit_outcome.failure_codes == (SECRET,)
    assert failed.memberships is candidate.memberships
    assert failed.segments is candidate.segments
    assert failed.permanent_report is candidate.permanent_report
    assert failed.semantic_report is report
    assert candidate.snapshot.audit_outcome.semantic_status is ac.SemanticAuditStatus.NOT_RUN
    with pytest.raises(FrozenInstanceError):
        failed.semantic_report = None
    with pytest.raises(AttributeError):
        caught.value.failed_candidate = None


@pytest.mark.asyncio
async def test_enabled_audit_requires_backend() -> None:
    error_type = audit_surface()["SemanticAuditInvariantError"]

    with pytest.raises(error_type) as caught:
        await audit_with(
            compilation_candidate(),
            strict_audit=True,
            backend=None,
        )

    assert_stable_error(caught.value, "BACKEND_REQUIRED")
    assert caught.value.failed_candidate is None


@pytest.mark.asyncio
async def test_backend_exception_is_stable_and_redacted() -> None:
    backend = RecordingAuditBackend(error=RuntimeError(f"{SECRET}: backend exploded"))
    error_type = audit_surface()["SemanticAuditInvariantError"]

    with pytest.raises(error_type) as caught:
        await audit_with(
            compilation_candidate(),
            strict_audit=True,
            backend=backend,
        )

    assert len(backend.requests) == 1
    assert_stable_error(caught.value, "BACKEND_FAILURE")
    assert caught.value.failed_candidate is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "case",
    [
        "not_report",
        "passed_not_bool",
        "codes_not_tuple",
        "passed_with_codes",
        "failed_without_codes",
        "duplicate_codes",
        "too_many_codes",
        "empty_code",
        "lowercase_code",
        "invalid_character",
        "too_long",
        "non_string",
    ],
)
@pytest.mark.asyncio
async def test_invalid_backend_output_is_rejected_without_content_leak(case: str) -> None:
    backend = RecordingAuditBackend(invalid_backend_output(case))
    error_type = audit_surface()["SemanticAuditInvariantError"]

    with pytest.raises(error_type) as caught:
        await audit_with(
            compilation_candidate(),
            strict_audit=True,
            backend=backend,
        )

    assert len(backend.requests) == 1
    assert_stable_error(caught.value, "BACKEND_OUTPUT_INVALID")
    assert caught.value.failed_candidate is None
