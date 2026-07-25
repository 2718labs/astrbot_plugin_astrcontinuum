from __future__ import annotations

from enum import Enum

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ._base import FrozenEnvelope, NonEmptyStr, NonNegativeInt, PositiveInt, ensure_unique
from .identity import SessionKey


class SemanticAuditStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


class SnapshotState(str, Enum):
    CANDIDATE = "CANDIDATE"
    COMMITTED = "COMMITTED"


class SnapshotAuditOutcome(FrozenEnvelope):
    mechanical_passed: bool
    semantic_status: SemanticAuditStatus
    failure_codes: tuple[NonEmptyStr, ...]

    @field_validator("failure_codes")
    @classmethod
    def validate_unique_failure_codes(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ensure_unique(values, "failure_codes")


class SnapshotEnvelope(FrozenEnvelope):
    snapshot_id: NonEmptyStr
    session_key: SessionKey
    base_snapshot_id: NonEmptyStr | None
    covered_event_end: PositiveInt
    source_high_water_mark: PositiveInt
    capsule_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    exact_anchor_ids: tuple[NonEmptyStr, ...]
    rendered_context: NonEmptyStr
    token_cost: NonNegativeInt
    audit_outcome: SnapshotAuditOutcome
    state: SnapshotState
    created_at: AwareDatetime
    committed_at: AwareDatetime | None

    @field_validator("capsule_ids", "exact_anchor_ids")
    @classmethod
    def validate_unique_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return ensure_unique(values, "snapshot ids")

    @model_validator(mode="after")
    def validate_state(self) -> SnapshotEnvelope:
        if self.state == SnapshotState.COMMITTED:
            if self.committed_at is None:
                raise ValueError("committed Snapshot requires committed_at")
            if not self.audit_outcome.mechanical_passed:
                raise ValueError("committed Snapshot requires mechanical pass")
            if self.audit_outcome.semantic_status == SemanticAuditStatus.FAILED:
                raise ValueError("committed Snapshot cannot have failed semantic audit")
            if self.audit_outcome.failure_codes:
                raise ValueError("committed Snapshot cannot have failure codes")
        elif self.committed_at is not None:
            raise ValueError("candidate Snapshot must not have committed_at")
        return self
