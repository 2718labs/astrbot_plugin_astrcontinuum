from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timezone
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def candidate_inputs() -> tuple[
    ac.SnapshotEnvelope,
    tuple[ac.ContextCapsuleEnvelope, ...],
    tuple[ac.EventEnvelope, ...],
]:
    key = ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="session-1",
        group_id=None,
        user_id="user-1",
        conversation_id="conversation-1",
        persona_id=None,
    )
    source_event = ac.EventEnvelope.create(
        event_id="event-1",
        session_key=key,
        sequence=1,
        event_type=ac.EventType.USER_MESSAGE,
        content="message 1",
        idempotency_key="request-1",
        token_count=2,
        created_at=NOW,
    )
    capsule = ac.ContextCapsuleEnvelope(
        capsule_id="capsule-1",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=key,
        covered_event_start=1,
        covered_event_end=1,
        source_event_ids=("event-1",),
        goals=(
            ac.CapsuleClaim(
                claim_id="goal-1",
                text="goal",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=("event-1",),
            ),
        ),
        constraints=(),
        decisions=(),
        progress=(),
        open_loops=(),
        preferences=(),
        entities=(),
        emotional_context=(),
        exact_anchors=(
            ac.CapsuleAnchor(
                anchor_id="anchor-1",
                anchor_type=ac.AnchorType.NAME,
                exact_text="exact name",
                source_event_ids=("event-1",),
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(),
        narrative_summary="candidate capsule",
        token_cost=10,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    )
    snapshot = ac.SnapshotEnvelope(
        snapshot_id="snapshot-1",
        session_key=key,
        base_snapshot_id=None,
        covered_event_end=1,
        source_high_water_mark=1,
        capsule_ids=("capsule-1",),
        exact_anchor_ids=("anchor-1",),
        rendered_context="candidate context",
        token_cost=10,
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.CANDIDATE,
        created_at=NOW,
        committed_at=None,
    )
    return snapshot, (capsule,), (source_event,)


def test_validate_candidate_is_exact_once_transparent_domain_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = importlib.import_module("astrcontinuum.domain")
    facade = importlib.import_module("astrcontinuum.compaction.validator")
    candidate_snapshot, candidate_capsules, source_events = candidate_inputs()
    expected_report = ac.PermanentValidationReport(
        passed=True,
        failure_codes=(),
        source_coverage=1.0,
        anchor_recall=1.0,
        coverage_gap=0,
        unsupported_critical_claims=0,
    )
    calls: list[dict[str, Any]] = []

    def recording_validator(**kwargs: Any) -> ac.PermanentValidationReport:
        calls.append(kwargs)
        return expected_report

    monkeypatch.setattr(domain, "validate_permanent", recording_validator)

    report = facade.validate_candidate(
        base_snapshot=None,
        base_capsules=(),
        candidate_snapshot=candidate_snapshot,
        candidate_capsules=candidate_capsules,
        source_events=source_events,
        target_high_water_mark=1,
        token_ceiling=100,
    )

    assert report is expected_report
    assert len(calls) == 1
    assert calls[0] == {
        "previous_snapshot": None,
        "previous_capsules": (),
        "candidate_snapshot": candidate_snapshot,
        "candidate_capsules": candidate_capsules,
        "source_events": source_events,
        "target_high_water_mark": 1,
        "token_ceiling": 100,
    }
    assert "strict_audit" not in inspect.signature(facade.validate_candidate).parameters
