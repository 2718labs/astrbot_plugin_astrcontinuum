from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import astrcontinuum as ac
import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def domain_type(name: str) -> Any:
    value = getattr(ac, name, None)
    assert value is not None, f"canonical domain export is missing: {name}"
    return value


def session_key(**overrides: object) -> Any:
    values: dict[str, object] = {
        "platform_instance_id": "astrbot-local",
        "message_type": "friend_message",
        "session_id": "session-1",
        "group_id": None,
        "user_id": "user-1",
        "conversation_id": "conversation-1",
        "persona_id": None,
    }
    values.update(overrides)
    return domain_type("SessionKey")(**values)


def capsule_quality(**overrides: object) -> Any:
    values: dict[str, object] = {
        "mechanical_passed": True,
        "source_coverage": 1.0,
        "anchor_recall": 1.0,
        "unsupported_critical_claims": 0,
        "coverage_gap": 0,
    }
    values.update(overrides)
    return domain_type("CapsuleQuality")(**values)


def capsule(**overrides: object) -> Any:
    semantic_status = domain_type("SemanticStatus")
    anchor_status = domain_type("AnchorStatus")
    anchor_type = domain_type("AnchorType")
    capsule_level = domain_type("CapsuleLevel")
    values: dict[str, object] = {
        "capsule_id": "capsule-1",
        "schema_version": "1.0.0",
        "level": capsule_level.MICRO,
        "session_key": session_key(),
        "covered_event_start": 1,
        "covered_event_end": 1,
        "source_event_ids": ("event-1",),
        "goals": (
            domain_type("CapsuleClaim")(
                claim_id="goal-1",
                text="Ship the runtime",
                status=semantic_status.ACTIVE,
                confidence=1.0,
                source_event_ids=("event-1",),
            ),
        ),
        "constraints": (),
        "decisions": (
            domain_type("Decision")(
                decision_id="decision-1",
                text="Use SQLite",
                status=semantic_status.ACTIVE,
                confidence=1.0,
                source_event_ids=("event-1",),
                rationale="Crash recovery",
                alternatives=(),
                supersedes=(),
                rejected_because="",
            ),
        ),
        "progress": (),
        "open_loops": (),
        "preferences": (),
        "entities": (
            domain_type("Entity")(
                entity_id="entity-1",
                kind="repository",
                canonical_name="AstrContinuum",
                aliases=(),
                source_event_ids=("event-1",),
            ),
        ),
        "emotional_context": (),
        "exact_anchors": (
            domain_type("CapsuleAnchor")(
                anchor_id="anchor-1",
                anchor_type=anchor_type.NAME,
                exact_text="AstrContinuum",
                source_event_ids=("event-1",),
                status=anchor_status.ACTIVE,
                importance=1.0,
            ),
        ),
        "dependencies": (
            domain_type("Dependency")(
                dependency_id="dependency-1",
                kind="repository",
                target_id="AstrContinuum",
                source_event_ids=("event-1",),
            ),
        ),
        "narrative_summary": "Runtime delivery context.",
        "token_cost": 24,
        "quality": capsule_quality(),
        "created_at": NOW,
    }
    values.update(overrides)
    return domain_type("ContextCapsuleEnvelope")(**values)


def audit_outcome(**overrides: object) -> Any:
    semantic_audit_status = domain_type("SemanticAuditStatus")
    values: dict[str, object] = {
        "mechanical_passed": True,
        "semantic_status": semantic_audit_status.NOT_RUN,
        "failure_codes": (),
    }
    values.update(overrides)
    return domain_type("SnapshotAuditOutcome")(**values)


def snapshot(**overrides: object) -> Any:
    snapshot_state = domain_type("SnapshotState")
    values: dict[str, object] = {
        "snapshot_id": "snapshot-1",
        "session_key": session_key(),
        "base_snapshot_id": None,
        "covered_event_end": 1,
        "source_high_water_mark": 1,
        "capsule_ids": ("capsule-1",),
        "exact_anchor_ids": ("anchor-1",),
        "rendered_context": "Runtime delivery context.",
        "token_cost": 24,
        "audit_outcome": audit_outcome(),
        "state": snapshot_state.CANDIDATE,
        "created_at": NOW,
        "committed_at": None,
    }
    values.update(overrides)
    return domain_type("SnapshotEnvelope")(**values)


def job(**overrides: object) -> Any:
    job_state = domain_type("CompactionJobState")
    values: dict[str, object] = {
        "job_id": "job-1",
        "session_key": session_key(),
        "state": job_state.PENDING,
        "target_high_water_mark": 1,
        "intent_target_high_water_mark": 1,
        "base_snapshot_id": None,
        "base_pointer_version": 0,
        "candidate_snapshot_id": None,
        "attempt_count": 0,
        "lease_owner": None,
        "lease_epoch": 0,
        "lease_expires_at": None,
        "next_retry_at": None,
        "error_stage": None,
        "error_code": None,
        "error_message": None,
        "created_at": NOW,
        "updated_at": NOW,
        "committed_at": None,
    }
    values.update(overrides)
    return domain_type("CompactionJobEnvelope")(**values)


def schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "specs" / name).read_text(encoding="utf-8"))


def test_session_key_has_stable_canonical_json_and_hash() -> None:
    key = session_key()
    expected = (
        '{"platform_instance_id":"astrbot-local","message_type":"friend_message",'
        '"session_id":"session-1","group_id":null,"user_id":"user-1",'
        '"conversation_id":"conversation-1","persona_id":null}'
    )

    assert key.canonical_json() == expected
    assert key.session_key_hash == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert session_key(group_id="group-1").session_key_hash != key.session_key_hash


def test_session_key_is_closed_frozen_and_rejects_empty_non_null_fields() -> None:
    key_type = domain_type("SessionKey")
    key = session_key()

    with pytest.raises(ValidationError):
        session_key(user_id="")
    with pytest.raises(ValidationError):
        session_key(group_id="")
    with pytest.raises(ValidationError):
        key_type(
            **key.model_dump(),
            unknown_identity="forbidden",
        )
    with pytest.raises(ValidationError):
        key.user_id = "other"


def test_event_envelope_accepts_only_the_four_authoritative_mappings() -> None:
    event_type = domain_type("EventType")
    event_role = domain_type("EventRole")
    source_hook = domain_type("SourceHook")
    event_envelope = domain_type("EventEnvelope")

    event = event_envelope(
        event_id="event-1",
        session_key=session_key(),
        sequence=1,
        event_type=event_type.USER_MESSAGE,
        role=event_role.USER,
        content="hello",
        source_hook=source_hook.ON_LLM_REQUEST,
        idempotency_key="request-1",
        token_count=1,
        created_at=NOW,
    )
    assert event.model_dump(mode="json")["session_key"] == session_key().model_dump(
        mode="json"
    )

    with pytest.raises(ValidationError):
        event_envelope(
            **{
                **event.model_dump(),
                "role": event_role.ASSISTANT,
            }
        )


def test_capsule_matches_frozen_schema_and_rejects_invalid_coverage() -> None:
    item = capsule()
    required = set(schema("capsule.schema.json")["required"])

    assert set(item.model_dump(mode="json")) == required
    assert item.model_config["frozen"] is True
    assert item.model_config["extra"] == "forbid"

    with pytest.raises(ValidationError):
        capsule(covered_event_start=2, covered_event_end=1)
    with pytest.raises(ValidationError):
        capsule(source_event_ids=("event-1", "event-1"))
    with pytest.raises(ValidationError):
        domain_type("ContextCapsuleEnvelope")(
            **item.model_dump(),
            unknown_field="forbidden",
        )


def test_snapshot_state_rules_match_frozen_schema() -> None:
    snapshot_state = domain_type("SnapshotState")
    semantic_audit_status = domain_type("SemanticAuditStatus")
    candidate = snapshot()

    assert set(candidate.model_dump(mode="json")) == set(
        schema("snapshot.schema.json")["required"]
    )

    with pytest.raises(ValidationError):
        snapshot(committed_at=NOW)
    with pytest.raises(ValidationError):
        snapshot(state=snapshot_state.COMMITTED, committed_at=None)
    with pytest.raises(ValidationError):
        snapshot(
            state=snapshot_state.COMMITTED,
            committed_at=NOW,
            audit_outcome=audit_outcome(
                semantic_status=semantic_audit_status.FAILED,
                failure_codes=("semantic_failure",),
            ),
        )

    committed = snapshot(state=snapshot_state.COMMITTED, committed_at=NOW)
    assert committed.committed_at == NOW


def test_compaction_job_conditional_fields_match_frozen_schema() -> None:
    job_state = domain_type("CompactionJobState")
    pending = job()

    assert set(pending.model_dump(mode="json")) == set(
        schema("compaction_job.schema.json")["required"]
    )

    with pytest.raises(ValidationError):
        job(base_snapshot_id="snapshot-0", base_pointer_version=0)
    with pytest.raises(ValidationError):
        job(state=job_state.COMPILING)
    with pytest.raises(ValidationError):
        job(error_stage="compile")

    compiling = job(
        state=job_state.COMPILING,
        lease_owner="worker-1",
        lease_epoch=1,
        lease_expires_at=NOW,
    )
    assert compiling.lease_owner == "worker-1"

    with pytest.raises(ValidationError):
        job(
            state=job_state.RETRY_WAIT,
            error_stage="compile",
            error_code="provider_timeout",
            error_message="timed out",
        )

    retrying = job(
        state=job_state.RETRY_WAIT,
        next_retry_at=NOW,
        error_stage="compile",
        error_code="provider_timeout",
        error_message="timed out",
    )
    assert retrying.next_retry_at == NOW

    committed = job(
        state=job_state.COMMITTED,
        candidate_snapshot_id="snapshot-1",
        committed_at=NOW,
    )
    assert committed.candidate_snapshot_id == "snapshot-1"
