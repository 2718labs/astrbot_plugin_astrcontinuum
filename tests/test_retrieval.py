from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Any

import pytest

import astrcontinuum as ac

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def runtime_surface() -> tuple[Any, Any, Any, Any]:
    config_type = getattr(ac, "RetrievalConfig", None)
    candidate_type = getattr(ac, "CandidateBlock", None)
    kind_type = getattr(ac, "CandidateKind", None)
    selector = getattr(ac, "select_candidates", None)
    assert config_type is not None, "RetrievalConfig export is missing"
    assert candidate_type is not None, "CandidateBlock export is missing"
    assert kind_type is not None, "CandidateKind export is missing"
    assert selector is not None, "select_candidates export is missing"
    return config_type, candidate_type, kind_type, selector


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


def event(key: ac.SessionKey, sequence: int, content: str) -> ac.EventEnvelope:
    return ac.EventEnvelope.create(
        event_id=f"event-{sequence}",
        session_key=key,
        sequence=sequence,
        event_type=ac.EventType.USER_MESSAGE,
        content=content,
        idempotency_key=f"request-{sequence}",
        token_count=3,
        created_at=NOW,
    )


def capsule(
    key: ac.SessionKey,
    *,
    capsule_id: str = "capsule-1",
    entity_ids: tuple[str, ...] = ("entity-atlas",),
    narrative_summary: str = "Navigation only; do not use as semantic truth.",
) -> ac.ContextCapsuleEnvelope:
    source_id = "event-1"
    return ac.ContextCapsuleEnvelope(
        capsule_id=capsule_id,
        schema_version="1.0.0",
        level=ac.CapsuleLevel.TASK,
        session_key=key,
        covered_event_start=1,
        covered_event_end=1,
        source_event_ids=(source_id,),
        goals=(
            ac.CapsuleClaim(
                claim_id=f"goal-{capsule_id}",
                text="Ship Atlas safely",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=(source_id,),
            ),
        ),
        constraints=(
            ac.CapsuleClaim(
                claim_id=f"constraint-{capsule_id}",
                text="Never expose private payloads",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=(source_id,),
            ),
        ),
        decisions=(
            ac.Decision(
                decision_id=f"decision-{capsule_id}",
                text="Use the local deterministic path",
                status=ac.SemanticStatus.ACTIVE,
                confidence=0.9,
                source_event_ids=(source_id,),
                rationale="It remains available offline",
                alternatives=("remote semantic provider", "unbounded scan"),
                supersedes=("decision-old",),
                rejected_because="External content is opaque",
            ),
        ),
        progress=(
            ac.CapsuleClaim(
                claim_id=f"progress-{capsule_id}",
                text="Read view completed",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=(source_id,),
            ),
        ),
        open_loops=(),
        preferences=(),
        entities=tuple(
            ac.Entity(
                entity_id=entity_id,
                kind="project",
                canonical_name=f"Atlas {entity_id}",
                aliases=(f"alias-{entity_id}",),
                source_event_ids=(source_id,),
            )
            for entity_id in entity_ids
        ),
        emotional_context=(),
        exact_anchors=(
            ac.CapsuleAnchor(
                anchor_id=f"anchor-{capsule_id}",
                anchor_type=ac.AnchorType.PATH,
                exact_text=r"D:\Atlas\state.json",
                source_event_ids=(source_id,),
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(
            ac.Dependency(
                dependency_id=f"dependency-{capsule_id}",
                kind="blocks",
                target_id=f"decision-{capsule_id}",
                source_event_ids=(source_id,),
            ),
        ),
        narrative_summary=narrative_summary,
        token_cost=80,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    )


def request_view(
    items: tuple[ac.ContextCapsuleEnvelope, ...],
    *,
    delta_count: int = 3,
) -> ac.RequestView:
    key = session_key()
    snapshot = ac.SnapshotEnvelope(
        snapshot_id="snapshot-1",
        session_key=key,
        base_snapshot_id=None,
        covered_event_end=1,
        source_high_water_mark=1,
        capsule_ids=tuple(item.capsule_id for item in items),
        exact_anchor_ids=tuple(anchor.anchor_id for item in items for anchor in item.exact_anchors),
        rendered_context="Committed projection.",
        token_cost=100,
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.COMMITTED,
        created_at=NOW,
        committed_at=NOW,
    )
    delta = tuple(
        event(key, sequence, f"raw delta {sequence}") for sequence in range(2, delta_count + 2)
    )
    return ac.RequestView(
        session_key=key,
        snapshot=snapshot,
        memberships=tuple(
            ac.SnapshotCapsuleMembership(
                ordinal=ordinal,
                slot="active_task",
                capsule=item,
            )
            for ordinal, item in enumerate(items)
        ),
        pointer_version=1,
        covered_event_end=1,
        high_water_mark=delta_count + 1,
        delta=delta,
    )


def test_structured_fields_and_raw_delta_are_independently_retrievable() -> None:
    config_type, _candidate_type, kind_type, selector = runtime_surface()
    view = request_view((capsule(session_key(), narrative_summary="Unhelpful."),))

    candidates = selector(view, "nothing in the navigation prose", config_type())

    kinds = {candidate.kind for candidate in candidates}
    assert {
        kind_type.GOAL,
        kind_type.CONSTRAINT,
        kind_type.RAW_EVENT,
        kind_type.EXACT_ANCHOR,
        kind_type.ENTITY,
        kind_type.DEPENDENCY,
        kind_type.DECISION,
    } <= kinds
    assert all(candidate.capsule_id != "Unhelpful." for candidate in candidates)


def test_decision_block_keeps_complete_reasoning_and_dependency_context() -> None:
    config_type, _candidate_type, kind_type, selector = runtime_surface()
    view = request_view((capsule(session_key()),))

    candidates = selector(view, "local path", config_type())
    decision = next(item for item in candidates if item.kind == kind_type.DECISION)

    assert "Use the local deterministic path" in decision.text
    assert "It remains available offline" in decision.text
    assert "remote semantic provider" in decision.text
    assert "decision-old" in decision.text
    assert "External content is opaque" in decision.text
    assert "dependency-capsule-1" in decision.text
    assert "blocks" in decision.text


def test_retrieval_bounds_query_capsules_delta_and_candidate_count() -> None:
    config_type, _candidate_type, kind_type, selector = runtime_surface()
    first = capsule(session_key(), capsule_id="capsule-1")
    second = capsule(session_key(), capsule_id="capsule-2")
    view = request_view((first, second), delta_count=4)
    config = config_type(
        max_query_characters=5,
        max_capsules=1,
        max_delta_events=2,
        max_candidates=8,
    )

    candidates = selector(view, "Atlas SHOULD-NOT-BE-SCANNED", config)

    assert len(candidates) == 8
    assert all(candidate.capsule_id != "capsule-2" for candidate in candidates)
    raw = [item for item in candidates if item.kind == kind_type.RAW_EVENT]
    assert [item.event_sequence for item in raw] == [4, 5]
    assert [item.text for item in raw] == [
        "[USER_MESSAGE/USER event-4]\nraw delta 4",
        "[USER_MESSAGE/USER event-5]\nraw delta 5",
    ]
    assert all("SHOULD-NOT-BE-SCANNED" not in item.reason for item in candidates)


def test_ties_use_stable_ids_and_output_is_repeatable() -> None:
    config_type, _candidate_type, kind_type, selector = runtime_surface()
    item = capsule(
        session_key(),
        entity_ids=("entity-z", "entity-a"),
        narrative_summary="No lexical match.",
    )
    view = request_view((item,), delta_count=1)
    config = config_type()

    first = selector(view, "unmatched", config)
    second = selector(view, "unmatched", config)
    entity_ids = [candidate.block_id for candidate in first if candidate.kind == kind_type.ENTITY]

    assert first == second
    assert entity_ids == sorted(entity_ids)


def test_public_surface_is_immutable_and_has_no_provider_input() -> None:
    config_type, candidate_type, kind_type, selector = runtime_surface()
    config = config_type()
    candidate = candidate_type(
        block_id="entity:capsule-1:entity-1",
        slot=ac.RuntimeSlot.RELEVANT_EVIDENCE,
        kind=kind_type.ENTITY,
        text="AC-owned entity",
        source_event_ids=("event-1",),
        score=1.0,
        reason="STRUCTURED_FALLBACK",
        required=False,
        capsule_id="capsule-1",
        event_sequence=None,
    )

    with pytest.raises(FrozenInstanceError):
        config.max_candidates = 1
    with pytest.raises(FrozenInstanceError):
        candidate.score = 2.0
    assert list(inspect.signature(selector).parameters) == ["view", "query", "config"]
    assert "provider" not in inspect.getsource(selector).casefold()
    assert "embedding" not in inspect.getsource(selector).casefold()
