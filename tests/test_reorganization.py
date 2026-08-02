from __future__ import annotations

from datetime import datetime, timezone

import pytest

import astrcontinuum as ac


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id="astrbot-local",
        message_type="friend_message",
        session_id="session-reorg",
        group_id=None,
        user_id="user-1",
        conversation_id="conversation-reorg",
        persona_id=None,
    )


def _claim(claim_id: str, text: str, event_id: str) -> ac.CapsuleClaim:
    return ac.CapsuleClaim(
        claim_id=claim_id,
        text=text,
        status=ac.SemanticStatus.ACTIVE,
        confidence=1.0,
        source_event_ids=(event_id,),
    )


def _capsule(
    capsule_id: str,
    *,
    event_id: str,
    goal_text: str,
    progress_text: str,
    token_cost: int,
) -> ac.ContextCapsuleEnvelope:
    key = _session_key()
    return ac.ContextCapsuleEnvelope(
        capsule_id=capsule_id,
        schema_version="1.0.0",
        level=ac.CapsuleLevel.TASK,
        session_key=key,
        covered_event_start=1,
        covered_event_end=2,
        source_event_ids=(event_id,),
        goals=(_claim(f"{capsule_id}-goal", goal_text, event_id),),
        constraints=(),
        decisions=(),
        progress=(_claim(f"{capsule_id}-progress", progress_text, event_id),),
        open_loops=(),
        preferences=(),
        entities=(),
        emotional_context=(),
        exact_anchors=(
            ac.CapsuleAnchor(
                anchor_id=f"{capsule_id}-anchor",
                anchor_type=ac.AnchorType.CODE,
                exact_text="reorganize_capsules",
                source_event_ids=(event_id,),
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(
            ac.Dependency(
                dependency_id=f"{capsule_id}-dependency",
                kind="runtime",
                target_id="astrcontinuum.domain",
                source_event_ids=(event_id,),
            ),
        ),
        narrative_summary="Do not use this summary as a fallback.",
        token_cost=token_cost,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    )


def test_reorganize_capsules_is_deterministic_and_preserves_core_edges() -> None:
    from astrcontinuum.reorganization import ReorganizationStatus, reorganize_capsules

    capsules = (
        _capsule(
            "capsule-a",
            event_id="event-a",
            goal_text="Ship the production capsule reorganization runtime.",
            progress_text="The deterministic planner is being implemented.",
            token_cost=80,
        ),
        _capsule(
            "capsule-b",
            event_id="event-b",
            goal_text="Keep the runtime path independent from summary generation.",
            progress_text="The loss ledger remains explicit at the boundary.",
            token_cost=80,
        ),
    )

    first = reorganize_capsules(capsules, token_budget=180)
    second = reorganize_capsules(capsules, token_budget=180)

    assert first == second
    assert first.capsule.exact_anchors == (
        capsules[0].exact_anchors[0],
        capsules[1].exact_anchors[0],
    )
    assert first.capsule.dependencies == (
        capsules[0].dependencies[0],
        capsules[1].dependencies[0],
    )
    assert first.before_tokens == 160
    assert first.after_tokens <= 180
    assert first.compression_ratio == first.after_tokens / first.before_tokens
    assert first.reduction_ratio == 1.0 - first.compression_ratio
    assert first.loss_count == sum(
        record.status is not ReorganizationStatus.RETAINED for record in first.records
    )
    assert first.release_count == sum(
        record.status is ReorganizationStatus.RELEASED for record in first.records
    )
    assert all(
        record.status is ReorganizationStatus.RETAINED
        for record in first.records
        if record.kind in {"exact_anchor", "dependency"}
    )


def test_reorganize_capsules_exposes_approximation_and_release_without_summary_fallback() -> None:
    from astrcontinuum.reorganization import ReorganizationStatus, reorganize_capsules

    capsule = _capsule(
        "capsule-a",
        event_id="event-a",
        goal_text="A long goal whose body may be approximated under pressure.",
        progress_text="A long progress body that can be released explicitly.",
        token_cost=120,
    )

    result = reorganize_capsules((capsule,), token_budget=45)
    statuses = {record.kind: record.status for record in result.records}

    assert statuses["exact_anchor"] is ReorganizationStatus.RETAINED
    assert statuses["dependency"] is ReorganizationStatus.RETAINED
    assert ReorganizationStatus.APPROXIMATE in statuses.values()
    assert ReorganizationStatus.RELEASED in statuses.values()
    assert result.release_count >= 1
    assert "Do not use this summary as a fallback." not in result.capsule.narrative_summary
    assert result.capsule.token_cost == result.after_tokens


def test_reorganize_capsules_fails_closed_when_core_edges_cannot_fit() -> None:
    from astrcontinuum.reorganization import ReorganizationBudgetError, reorganize_capsules

    capsule = _capsule(
        "capsule-a",
        event_id="event-a",
        goal_text="goal",
        progress_text="progress",
        token_cost=10,
    )

    with pytest.raises(ReorganizationBudgetError) as caught:
        reorganize_capsules((capsule,), token_budget=1)

    assert caught.value.required_tokens > caught.value.budget


def test_reorganization_is_exported_from_production_package() -> None:
    from astrcontinuum.reorganization import reorganize_capsules

    assert ac.reorganize_capsules is reorganize_capsules
    assert ac.ReorganizationStatus.RETAINED.value == "retained"


def test_reorganization_rejects_conflicting_duplicate_core_identity() -> None:
    from astrcontinuum.reorganization import ReorganizationInvariantError, reorganize_capsules

    first = _capsule(
        "capsule-a",
        event_id="event-a",
        goal_text="goal",
        progress_text="progress",
        token_cost=10,
    )
    conflicting_anchor = first.exact_anchors[0].model_copy(
        update={"anchor_id": "shared-anchor", "exact_text": "one"}
    )
    second_anchor = conflicting_anchor.model_copy(update={"exact_text": "two"})
    second = first.model_copy(
        update={
            "capsule_id": "capsule-b",
            "source_event_ids": ("event-b",),
            "exact_anchors": (second_anchor,),
        }
    )
    first = first.model_copy(update={"exact_anchors": (conflicting_anchor,)})

    with pytest.raises(ReorganizationInvariantError):
        reorganize_capsules((first, second), token_budget=100)
