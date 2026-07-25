from __future__ import annotations

from enum import Enum

from pydantic import AwareDatetime, model_validator

from ._base import FrozenEnvelope, NonEmptyStr, NonNegativeInt, PositiveInt
from .identity import SessionKey


class CompactionJobState(str, Enum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    COMPILING = "COMPILING"
    AUDITING = "AUDITING"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    RETRY_WAIT = "RETRY_WAIT"
    COMMITTED = "COMMITTED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


WORKING_STATES = frozenset(
    {
        CompactionJobState.LEASED,
        CompactionJobState.COMPILING,
        CompactionJobState.AUDITING,
        CompactionJobState.READY_TO_COMMIT,
    }
)
CANDIDATE_STATES = frozenset(
    {
        CompactionJobState.READY_TO_COMMIT,
        CompactionJobState.COMMITTED,
        CompactionJobState.SUPERSEDED,
    }
)


class CompactionJobEnvelope(FrozenEnvelope):
    job_id: NonEmptyStr
    session_key: SessionKey
    state: CompactionJobState
    target_high_water_mark: PositiveInt
    intent_target_high_water_mark: PositiveInt
    base_snapshot_id: NonEmptyStr | None
    base_pointer_version: NonNegativeInt
    candidate_snapshot_id: NonEmptyStr | None
    attempt_count: NonNegativeInt
    lease_owner: NonEmptyStr | None
    lease_epoch: NonNegativeInt
    lease_expires_at: AwareDatetime | None
    next_retry_at: AwareDatetime | None
    error_stage: NonEmptyStr | None
    error_code: NonEmptyStr | None
    error_message: NonEmptyStr | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    committed_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_conditionals(self) -> CompactionJobEnvelope:
        errors = (self.error_stage, self.error_code, self.error_message)
        if any(value is None for value in errors) and any(value is not None for value in errors):
            raise ValueError("error_stage, error_code, and error_message must be all-null or set")

        if self.base_snapshot_id is None:
            if self.base_pointer_version != 0:
                raise ValueError("null base_snapshot_id requires base_pointer_version zero")
        elif self.base_pointer_version < 1:
            raise ValueError("non-null base_snapshot_id requires positive base_pointer_version")

        if self.state in WORKING_STATES:
            if (
                self.lease_owner is None
                or self.lease_epoch < 1
                or self.lease_expires_at is None
            ):
                raise ValueError("working job state requires an effective lease")
        elif self.lease_owner is not None or self.lease_expires_at is not None:
            raise ValueError("non-working job state must not expose an effective lease")

        if self.state in CANDIDATE_STATES:
            if self.candidate_snapshot_id is None:
                raise ValueError("job state requires candidate_snapshot_id")
        elif self.candidate_snapshot_id is not None:
            raise ValueError("job state must not have candidate_snapshot_id")

        if self.state in {CompactionJobState.RETRY_WAIT, CompactionJobState.FAILED} and any(
            value is None for value in errors
        ):
            raise ValueError("retry and failed jobs require an error tuple")

        if self.state == CompactionJobState.RETRY_WAIT:
            if self.next_retry_at is None:
                raise ValueError("retry job requires next_retry_at")
        elif self.next_retry_at is not None:
            raise ValueError("only retry jobs may have next_retry_at")

        if self.state == CompactionJobState.COMMITTED:
            if self.committed_at is None:
                raise ValueError("committed job requires committed_at")
        elif self.committed_at is not None:
            raise ValueError("only committed jobs may have committed_at")

        return self
