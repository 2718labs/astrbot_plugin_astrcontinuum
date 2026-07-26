from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def permanent_validator() -> Callable[..., Any]:
    value = getattr(ac, "validate_permanent", None)
    assert value is not None, "permanent validator export is missing"
    return value


def failure_code(name: str) -> Any:
    enum_type = getattr(ac, "PermanentFailureCode", None)
    assert enum_type is not None, "permanent failure-code export is missing"
    return getattr(enum_type, name)


def session_key(session_id: str = "session-1") -> Any:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id=session_id,
        group_id=None,
        user_id="user-1",
        conversation_id="conversation-1",
        persona_id=None,
    )


def event(sequence: int, *, key: Any | None = None) -> Any:
    return ac.EventEnvelope.create(
        event_id=f"event-{sequence}",
        session_key=key or session_key(),
        sequence=sequence,
        event_type=ac.EventType.USER_MESSAGE,
        content=f"message {sequence}",
        idempotency_key=f"request-{sequence}",
        token_count=2,
        created_at=NOW,
    )


def claim(
    claim_id: str,
    source_event_id: str,
    *,
    status: Any = ac.SemanticStatus.ACTIVE,
    source_event_ids: tuple[str, ...] | None = None,
) -> Any:
    return ac.CapsuleClaim(
        claim_id=claim_id,
        text=f"claim {claim_id}",
        status=status,
        confidence=1.0,
        source_event_ids=source_event_ids if source_event_ids is not None else (source_event_id,),
    )


def decision(
    decision_id: str,
    source_event_id: str,
    *,
    status: Any = ac.SemanticStatus.ACTIVE,
    source_event_ids: tuple[str, ...] | None = None,
) -> Any:
    return ac.Decision(
        decision_id=decision_id,
        text=f"decision {decision_id}",
        status=status,
        confidence=1.0,
        source_event_ids=source_event_ids if source_event_ids is not None else (source_event_id,),
        rationale="rationale",
        alternatives=(),
        supersedes=(),
        rejected_because="",
    )


def entity(entity_id: str, source_event_id: str) -> Any:
    return ac.Entity(
        entity_id=entity_id,
        kind="person",
        canonical_name=f"entity {entity_id}",
        aliases=(),
        source_event_ids=(source_event_id,),
    )


def dependency(dependency_id: str, source_event_id: str) -> Any:
    return ac.Dependency(
        dependency_id=dependency_id,
        kind="requires",
        target_id="target-1",
        source_event_ids=(source_event_id,),
    )


def anchor(
    anchor_id: str,
    source_event_id: str,
    *,
    anchor_type: Any = ac.AnchorType.NAME,
    exact_text: str | None = None,
    source_event_ids: tuple[str, ...] | None = None,
    status: Any = ac.AnchorStatus.ACTIVE,
    importance: float = 1.0,
) -> Any:
    return ac.CapsuleAnchor(
        anchor_id=anchor_id,
        anchor_type=anchor_type,
        exact_text=exact_text if exact_text is not None else f"anchor {anchor_id}",
        source_event_ids=source_event_ids if source_event_ids is not None else (source_event_id,),
        status=status,
        importance=importance,
    )


def quality(**overrides: object) -> Any:
    values: dict[str, object] = {
        "mechanical_passed": True,
        "source_coverage": 1.0,
        "anchor_recall": 1.0,
        "unsupported_critical_claims": 0,
        "coverage_gap": 0,
    }
    values.update(overrides)
    return ac.CapsuleQuality(**values)


def capsule(
    capsule_id: str = "capsule-1",
    *,
    key: Any | None = None,
    start: int = 1,
    end: int = 1,
    source_event_ids: tuple[str, ...] = ("event-1",),
    goals: tuple[Any, ...] | None = None,
    constraints: tuple[Any, ...] = (),
    decisions: tuple[Any, ...] = (),
    progress: tuple[Any, ...] = (),
    open_loops: tuple[Any, ...] = (),
    preferences: tuple[Any, ...] = (),
    entities: tuple[Any, ...] = (),
    emotional_context: tuple[Any, ...] = (),
    anchors: tuple[Any, ...] | None = None,
    dependencies: tuple[Any, ...] = (),
    capsule_quality: Any | None = None,
    token_cost: int = 10,
) -> Any:
    return ac.ContextCapsuleEnvelope(
        capsule_id=capsule_id,
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=key or session_key(),
        covered_event_start=start,
        covered_event_end=end,
        source_event_ids=source_event_ids,
        goals=goals if goals is not None else (claim("goal-1", source_event_ids[0]),),
        constraints=constraints,
        decisions=decisions,
        progress=progress,
        open_loops=open_loops,
        preferences=preferences,
        entities=entities,
        emotional_context=emotional_context,
        exact_anchors=anchors
        if anchors is not None
        else (anchor("anchor-1", source_event_ids[0]),),
        dependencies=dependencies,
        narrative_summary=f"capsule {capsule_id}",
        token_cost=token_cost,
        quality=capsule_quality or quality(),
        created_at=NOW,
    )


def audit_outcome() -> Any:
    return ac.SnapshotAuditOutcome(
        mechanical_passed=True,
        semantic_status=ac.SemanticAuditStatus.NOT_RUN,
        failure_codes=(),
    )


def snapshot(
    capsules: tuple[Any, ...],
    *,
    snapshot_id: str = "snapshot-1",
    key: Any | None = None,
    base_snapshot_id: str | None = None,
    covered_event_end: int = 1,
    source_high_water_mark: int = 1,
    token_cost: int = 10,
) -> Any:
    active_anchor_ids = tuple(
        item.anchor_id
        for item in capsules
        for item in item.exact_anchors
        if item.status == ac.AnchorStatus.ACTIVE
    )
    return ac.SnapshotEnvelope(
        snapshot_id=snapshot_id,
        session_key=key or session_key(),
        base_snapshot_id=base_snapshot_id,
        covered_event_end=covered_event_end,
        source_high_water_mark=source_high_water_mark,
        capsule_ids=tuple(item.capsule_id for item in capsules),
        exact_anchor_ids=active_anchor_ids,
        rendered_context="candidate context",
        token_cost=token_cost,
        audit_outcome=audit_outcome(),
        state=ac.SnapshotState.CANDIDATE,
        created_at=NOW,
        committed_at=None,
    )


def validate(
    *,
    previous_snapshot: Any | None = None,
    previous_capsules: tuple[Any, ...] = (),
    candidate_capsules: tuple[Any, ...] | None = None,
    source_events: tuple[Any, ...] | None = None,
    target: int = 1,
    token_ceiling: int = 100,
    candidate_snapshot: Any | None = None,
) -> Any:
    capsules = candidate_capsules if candidate_capsules is not None else (capsule(),)
    candidate = candidate_snapshot or snapshot(
        capsules,
        base_snapshot_id=previous_snapshot.snapshot_id if previous_snapshot else None,
        covered_event_end=target,
        source_high_water_mark=target,
        token_cost=sum(item.token_cost for item in capsules),
    )
    events = source_events if source_events is not None else (event(target),)
    return permanent_validator()(
        previous_snapshot=previous_snapshot,
        previous_capsules=previous_capsules,
        candidate_snapshot=candidate,
        candidate_capsules=capsules,
        source_events=events,
        target_high_water_mark=target,
        token_ceiling=token_ceiling,
    )


def capsule_with_claim_group(
    group_name: str,
    record: Any,
    **kwargs: Any,
) -> Any:
    return capsule(
        goals=(record,) if group_name == "goals" else (),
        constraints=(record,) if group_name == "constraints" else (),
        progress=(record,) if group_name == "progress" else (),
        open_loops=(record,) if group_name == "open_loops" else (),
        preferences=(record,) if group_name == "preferences" else (),
        emotional_context=(record,) if group_name == "emotional_context" else (),
        **kwargs,
    )


def validate_history(
    previous_capsule: Any,
    candidate_capsule: Any,
) -> Any:
    previous = snapshot(
        (previous_capsule,),
        snapshot_id="snapshot-old",
        covered_event_end=1,
        source_high_water_mark=1,
    )
    return validate(
        previous_snapshot=previous,
        previous_capsules=(previous_capsule,),
        candidate_capsules=(candidate_capsule,),
        source_events=(event(2),),
        target=2,
    )


def test_valid_candidate_passes_with_exact_metrics() -> None:
    report = validate()

    assert report.passed
    assert report.failure_codes == ()
    assert report.source_coverage == 1.0
    assert report.anchor_recall == 1.0
    assert report.coverage_gap == 0
    assert report.unsupported_critical_claims == 0


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        (
            {
                "target": 3,
                "source_events": (event(1), event(3)),
                "candidate_snapshot": snapshot(
                    (capsule(end=3, source_event_ids=("event-1", "event-3")),),
                    covered_event_end=3,
                    source_high_water_mark=3,
                ),
                "candidate_capsules": (capsule(end=3, source_event_ids=("event-1", "event-3")),),
            },
            "COVERAGE_GAP",
        ),
        (
            {
                "target": 2,
                "source_events": (event(1), event(2)),
                "candidate_snapshot": snapshot(
                    (capsule(end=1),),
                    covered_event_end=1,
                    source_high_water_mark=1,
                ),
            },
            "TARGET_MISMATCH",
        ),
        (
            {
                "candidate_capsules": (capsule(key=session_key("other-session")),),
            },
            "SESSION_KEY_MISMATCH",
        ),
        (
            {
                "candidate_capsules": (),
                "candidate_snapshot": snapshot((capsule(),)),
            },
            "EMPTY_COMPILER_OUTPUT",
        ),
    ],
)
def test_structural_failures_return_stable_codes(
    changes: dict[str, object],
    expected_code: str,
) -> None:
    report = validate(**changes)

    assert failure_code(expected_code) in report.failure_codes
    assert not report.passed


def test_active_semantics_require_valid_source_events() -> None:
    unsupported = capsule(goals=(claim("goal-1", "missing-event"),))
    report = validate(candidate_capsules=(unsupported,))

    assert failure_code("UNSUPPORTED_ACTIVE_SEMANTIC") in report.failure_codes
    assert report.source_coverage < 1.0
    assert report.unsupported_critical_claims == 1


def test_previous_active_semantics_and_anchors_must_be_preserved() -> None:
    previous_capsule = capsule(
        capsule_id="capsule-old",
        goals=(claim("goal-old", "event-1"),),
        anchors=(anchor("anchor-old", "event-1"),),
    )
    previous = snapshot(
        (previous_capsule,),
        snapshot_id="snapshot-old",
        covered_event_end=1,
        source_high_water_mark=1,
    )
    replacement = capsule(
        capsule_id="capsule-new",
        start=2,
        end=2,
        source_event_ids=("event-2",),
        goals=(claim("goal-new", "event-2"),),
        anchors=(anchor("anchor-new", "event-2"),),
    )

    report = validate(
        previous_snapshot=previous,
        previous_capsules=(previous_capsule,),
        candidate_capsules=(replacement,),
        source_events=(event(2),),
        target=2,
    )

    assert failure_code("MISSING_PRIOR_SEMANTIC") in report.failure_codes
    assert failure_code("MISSING_REQUIRED_ANCHOR") in report.failure_codes
    assert report.anchor_recall == 0.0


def test_previous_active_claim_can_remain_active() -> None:
    required_anchor = anchor("anchor-old", "event-1")
    retained_claim = claim("stable-claim", "event-1")
    previous_capsule = capsule(
        capsule_id="capsule-old",
        source_event_ids=("event-1",),
        goals=(retained_claim,),
        anchors=(required_anchor,),
    )
    candidate_capsule = capsule(
        capsule_id="capsule-new",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=(retained_claim,),
        anchors=(required_anchor,),
    )

    report = validate_history(previous_capsule, candidate_capsule)

    assert report.passed
    assert report.failure_codes == ()


@pytest.mark.parametrize(
    "group_name",
    [
        "goals",
        "constraints",
        "progress",
        "open_loops",
        "preferences",
        "emotional_context",
    ],
)
@pytest.mark.parametrize(
    "transition_status",
    [ac.SemanticStatus.SUPERSEDED, ac.SemanticStatus.RETRACTED],
)
def test_previous_active_claim_accepts_sourced_transition(
    group_name: str,
    transition_status: Any,
) -> None:
    required_anchor = anchor("anchor-old", "event-1")
    previous_capsule = capsule_with_claim_group(
        group_name,
        claim("stable-claim", "event-1"),
        capsule_id="capsule-old",
        source_event_ids=("event-1",),
        anchors=(required_anchor,),
    )
    candidate_capsule = capsule_with_claim_group(
        group_name,
        claim(
            "stable-claim",
            "event-2",
            status=transition_status,
            source_event_ids=("event-1", "event-2"),
        ),
        capsule_id="capsule-new",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        anchors=(required_anchor,),
    )

    report = validate_history(previous_capsule, candidate_capsule)

    assert report.passed
    assert report.failure_codes == ()
    assert report.source_coverage == 1.0


@pytest.mark.parametrize(
    "transition_status",
    [ac.SemanticStatus.SUPERSEDED, ac.SemanticStatus.RETRACTED],
)
def test_previous_active_decision_accepts_sourced_transition(
    transition_status: Any,
) -> None:
    required_anchor = anchor("anchor-old", "event-1")
    previous_capsule = capsule(
        capsule_id="capsule-old",
        source_event_ids=("event-1",),
        goals=(),
        decisions=(decision("stable-decision", "event-1"),),
        anchors=(required_anchor,),
    )
    candidate_capsule = capsule(
        capsule_id="capsule-new",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=(),
        decisions=(
            decision(
                "stable-decision",
                "event-2",
                status=transition_status,
                source_event_ids=("event-1", "event-2"),
            ),
        ),
        anchors=(required_anchor,),
    )

    report = validate_history(previous_capsule, candidate_capsule)

    assert report.passed
    assert report.failure_codes == ()


@pytest.mark.parametrize(
    "case",
    ["old_source_only", "uncertain", "new_id", "removed"],
)
def test_invalid_or_missing_prior_claim_transition_is_rejected(case: str) -> None:
    required_anchor = anchor("anchor-old", "event-1")
    previous_capsule = capsule(
        capsule_id="capsule-old",
        source_event_ids=("event-1",),
        goals=(claim("stable-claim", "event-1"),),
        anchors=(required_anchor,),
    )
    if case == "old_source_only":
        candidate_claims = (
            claim(
                "stable-claim",
                "event-1",
                status=ac.SemanticStatus.SUPERSEDED,
            ),
        )
    elif case == "uncertain":
        candidate_claims = (
            claim(
                "stable-claim",
                "event-2",
                status=ac.SemanticStatus.UNCERTAIN,
            ),
        )
    elif case == "new_id":
        candidate_claims = (claim("replacement-claim", "event-2"),)
    else:
        candidate_claims = ()
    candidate_capsule = capsule(
        capsule_id="capsule-new",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=candidate_claims,
        anchors=(required_anchor,),
    )

    report = validate_history(previous_capsule, candidate_capsule)

    assert failure_code("MISSING_PRIOR_SEMANTIC") in report.failure_codes
    assert not report.passed


def test_transition_with_unknown_source_keeps_provenance_failures() -> None:
    required_anchor = anchor("anchor-old", "event-1")
    previous_capsule = capsule(
        capsule_id="capsule-old",
        source_event_ids=("event-1",),
        goals=(claim("stable-claim", "event-1"),),
        anchors=(required_anchor,),
    )
    candidate_capsule = capsule(
        capsule_id="capsule-new",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=(
            claim(
                "stable-claim",
                "event-2",
                status=ac.SemanticStatus.SUPERSEDED,
                source_event_ids=("event-2", "unknown-event"),
            ),
        ),
        anchors=(required_anchor,),
    )

    report = validate_history(previous_capsule, candidate_capsule)

    assert failure_code("MISSING_PRIOR_SEMANTIC") in report.failure_codes
    assert failure_code("UNSUPPORTED_ACTIVE_SEMANTIC") in report.failure_codes
    assert failure_code("SOURCE_COVERAGE_NOT_FULL") in report.failure_codes
    assert failure_code("UNSUPPORTED_CRITICAL_CLAIMS") in report.failure_codes
    assert report.source_coverage < 1.0
    assert report.unsupported_critical_claims == 1


@pytest.mark.parametrize("record_kind", ["entity", "dependency"])
def test_entity_and_dependency_cannot_disappear_via_transition_rule(
    record_kind: str,
) -> None:
    required_anchor = anchor("anchor-old", "event-1")
    previous_capsule = capsule(
        capsule_id="capsule-old",
        source_event_ids=("event-1",),
        goals=(),
        entities=(entity("stable-entity", "event-1"),) if record_kind == "entity" else (),
        dependencies=(dependency("stable-dependency", "event-1"),)
        if record_kind == "dependency"
        else (),
        anchors=(required_anchor,),
    )
    candidate_capsule = capsule(
        capsule_id="capsule-new",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=(),
        anchors=(required_anchor,),
    )

    report = validate_history(previous_capsule, candidate_capsule)

    assert failure_code("MISSING_PRIOR_SEMANTIC") in report.failure_codes
    assert not report.passed


@pytest.mark.parametrize(
    "change",
    ["exact_text", "anchor_type", "source", "status"],
)
def test_previous_active_anchor_requires_exact_active_record(change: str) -> None:
    retained_claim = claim("stable-claim", "event-1")
    required_anchor = anchor("stable-anchor", "event-1")
    if change == "exact_text":
        candidate_anchor = anchor(
            "stable-anchor",
            "event-1",
            exact_text="changed exact text",
        )
    elif change == "anchor_type":
        candidate_anchor = anchor(
            "stable-anchor",
            "event-1",
            anchor_type=ac.AnchorType.URL,
        )
    elif change == "source":
        candidate_anchor = anchor("stable-anchor", "event-2")
    else:
        candidate_anchor = anchor(
            "stable-anchor",
            "event-1",
            status=ac.AnchorStatus.SUPERSEDED,
        )
    previous_capsule = capsule(
        capsule_id="capsule-old",
        source_event_ids=("event-1",),
        goals=(retained_claim,),
        anchors=(required_anchor,),
    )
    candidate_capsule = capsule(
        capsule_id="capsule-new",
        start=1,
        end=2,
        source_event_ids=("event-1", "event-2"),
        goals=(retained_claim,),
        anchors=(candidate_anchor,),
    )

    report = validate_history(previous_capsule, candidate_capsule)

    assert failure_code("MISSING_REQUIRED_ANCHOR") in report.failure_codes
    assert failure_code("ANCHOR_RECALL_NOT_FULL") in report.failure_codes
    assert report.anchor_recall < 1.0
    assert not report.passed


@pytest.mark.parametrize(
    ("quality_changes", "expected_code"),
    [
        ({"mechanical_passed": False}, "MECHANICAL_NOT_PASSED"),
        ({"source_coverage": 0.9}, "SOURCE_COVERAGE_NOT_FULL"),
        ({"anchor_recall": 0.9}, "ANCHOR_RECALL_NOT_FULL"),
        ({"unsupported_critical_claims": 1}, "UNSUPPORTED_CRITICAL_CLAIMS"),
        ({"coverage_gap": 1}, "QUALITY_COVERAGE_GAP"),
    ],
)
def test_all_permanent_quality_gates_are_unconditional(
    quality_changes: dict[str, object],
    expected_code: str,
) -> None:
    item = capsule(capsule_quality=quality(**quality_changes))
    report = validate(candidate_capsules=(item,))

    assert failure_code(expected_code) in report.failure_codes
    assert not report.passed


def test_token_ceiling_is_permanent_and_strict_audit_is_not_an_escape() -> None:
    report = validate(token_ceiling=9)

    assert failure_code("TOKEN_CEILING_EXCEEDED") in report.failure_codes
    assert "strict_audit" not in inspect.signature(permanent_validator()).parameters
