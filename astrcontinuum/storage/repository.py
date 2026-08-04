"""Crash-safe SQLite repository transactions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from ..domain import (
    CompactionJobEnvelope,
    CompactionJobState,
    ContextCapsuleEnvelope,
    EventEnvelope,
    EventRole,
    EventType,
    PermanentFailureCode,
    PermanentValidationReport,
    SessionKey,
    SnapshotAuditOutcome,
    SnapshotEnvelope,
    SnapshotState,
    SourceHook,
    validate_permanent,
)
from .sqlite import SQLiteConnectionFactory

if TYPE_CHECKING:
    from ..reorganization import ReorganizationRecord

FaultInjector = Callable[[str], None]

_NONTERMINAL_JOB_STATES = (
    CompactionJobState.PENDING,
    CompactionJobState.LEASED,
    CompactionJobState.COMPILING,
    CompactionJobState.AUDITING,
    CompactionJobState.READY_TO_COMMIT,
    CompactionJobState.RETRY_WAIT,
)
_WORKING_JOB_STATES = frozenset(
    {
        CompactionJobState.LEASED,
        CompactionJobState.COMPILING,
        CompactionJobState.AUDITING,
        CompactionJobState.READY_TO_COMMIT,
    }
)
_ALLOWED_WORKER_TRANSITIONS = {
    CompactionJobState.LEASED: frozenset({CompactionJobState.COMPILING}),
    CompactionJobState.COMPILING: frozenset(
        {
            CompactionJobState.AUDITING,
            CompactionJobState.READY_TO_COMMIT,
        }
    ),
    CompactionJobState.AUDITING: frozenset({CompactionJobState.READY_TO_COMMIT}),
    CompactionJobState.READY_TO_COMMIT: frozenset(),
}


class RepositoryError(RuntimeError):
    """Base class for durable repository failures."""


class RepositoryConflict(RepositoryError):
    """Raised when immutable durable identity conflicts with a new request."""


class SessionIdentityConflict(RepositoryConflict):
    """Raised when a session hash does not resolve to the exact SessionKey."""


class IdempotencyConflict(RepositoryConflict):
    """Raised when an event idempotency tuple is reused with different data."""


class EventIdentityConflict(RepositoryConflict):
    """Raised when an event id is reused outside its exact idempotent replay."""


class RepositoryInvariantError(RepositoryError):
    """Raised when durable rows cannot form a valid canonical view."""


class JobTransitionError(RepositoryError):
    """Raised when a requested Job transition is not in the frozen state machine."""


class StaleLeaseError(RepositoryError):
    """Raised when a worker mutation does not own a live matching fence."""


class PublicationRejected(RepositoryError):
    """Raised when the permanent validator rejects a publication candidate."""

    def __init__(self, report: PermanentValidationReport) -> None:
        self.report = report
        codes = ", ".join(code.value for code in report.failure_codes)
        super().__init__(f"publication candidate failed permanent validation: {codes}")


class PublishOutcome(str, Enum):
    COMMITTED = "COMMITTED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class SnapshotCapsuleMembership:
    """One ordered durable Snapshot-to-Capsule relation."""

    ordinal: int
    slot: str
    capsule: ContextCapsuleEnvelope

    @property
    def capsule_id(self) -> str:
        return self.capsule.capsule_id


@dataclass(frozen=True, slots=True)
class RequestView:
    """One consistent committed Snapshot plus its bounded Journal Delta."""

    session_key: SessionKey
    snapshot: SnapshotEnvelope | None
    memberships: tuple[SnapshotCapsuleMembership, ...]
    pointer_version: int
    covered_event_end: int
    high_water_mark: int
    delta: tuple[EventEnvelope, ...]

    @property
    def capsules(self) -> tuple[ContextCapsuleEnvelope, ...]:
        return tuple(membership.capsule for membership in self.memberships)


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Terminal publication branch and the active winning Snapshot."""

    outcome: PublishOutcome
    job: CompactionJobEnvelope
    winner: SnapshotEnvelope
    pointer_version: int


class _PublishConflict(Exception):
    pass


def _normalize_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_model_json(
    value: ContextCapsuleEnvelope | SnapshotAuditOutcome,
) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _apply_reorganization_record_quality_floor(
    report: PermanentValidationReport,
    records: tuple[ReorganizationRecord, ...],
) -> PermanentValidationReport:
    """Derive the non-configurable quality failure for released source records."""

    from ..reorganization import ReorganizationStatus

    non_summary_release_count = sum(
        record.status is ReorganizationStatus.RELEASED and record.kind != "narrative_summary"
        for record in records
    )
    if not non_summary_release_count:
        return report

    failures = set(report.failure_codes)
    failures.add(PermanentFailureCode.QUALITY_COVERAGE_GAP)
    return PermanentValidationReport(
        passed=False,
        failure_codes=tuple(code for code in PermanentFailureCode if code in failures),
        source_coverage=report.source_coverage,
        anchor_recall=report.anchor_recall,
        coverage_gap=max(report.coverage_gap, non_summary_release_count),
        unsupported_critical_claims=report.unsupported_critical_claims,
    )


class SQLiteRepository:
    """Execute the stable durable transactions over one SQLite database."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._factory = factory
        self._fault_injector = fault_injector

    @property
    def factory(self) -> SQLiteConnectionFactory:
        """Return the connection factory owned by this repository."""

        return self._factory

    def capture_user_event(
        self,
        *,
        event_id: str,
        session_key: SessionKey,
        content: str,
        idempotency_key: str,
        token_count: int,
        created_at: datetime,
    ) -> EventEnvelope:
        """Run ``TX_CAPTURE_USER_EVENT``."""

        return self._capture_event(
            event_id=event_id,
            session_key=session_key,
            event_type=EventType.USER_MESSAGE,
            content=content,
            idempotency_key=idempotency_key,
            token_count=token_count,
            created_at=created_at,
        )

    def capture_assistant_event(
        self,
        *,
        event_id: str,
        session_key: SessionKey,
        content: str,
        idempotency_key: str,
        token_count: int,
        created_at: datetime,
    ) -> EventEnvelope:
        """Run ``TX_CAPTURE_ASSISTANT_EVENT``."""

        return self._capture_event(
            event_id=event_id,
            session_key=session_key,
            event_type=EventType.ASSISTANT_MESSAGE,
            content=content,
            idempotency_key=idempotency_key,
            token_count=token_count,
            created_at=created_at,
        )

    def capture_tool_event(
        self,
        *,
        event_id: str,
        session_key: SessionKey,
        event_type: EventType,
        content: str,
        idempotency_key: str,
        token_count: int,
        created_at: datetime,
    ) -> EventEnvelope:
        """Run ``TX_CAPTURE_TOOL_EVENT`` for a call or result."""

        if event_type not in {EventType.TOOL_CALL, EventType.TOOL_RESULT}:
            raise ValueError("tool capture accepts only TOOL_CALL or TOOL_RESULT")
        return self._capture_event(
            event_id=event_id,
            session_key=session_key,
            event_type=event_type,
            content=content,
            idempotency_key=idempotency_key,
            token_count=token_count,
            created_at=created_at,
        )

    def read_request_view(self, session_key: SessionKey) -> RequestView:
        """Run ``TX_READ_REQUEST_VIEW`` in one read transaction."""

        with self._factory.connection(read_only=True) as connection:
            connection.execute("BEGIN")
            try:
                view = self._read_request_view(connection, session_key)
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
                return view

    def read_snapshot_reorganization_records(
        self,
        snapshot_id: str,
    ) -> tuple[ReorganizationRecord, ...]:
        """Read the immutable audit ledger of one committed Snapshot only."""

        with self._factory.connection(read_only=True) as connection:
            connection.execute("BEGIN")
            try:
                snapshot_row = connection.execute(
                    """
                    SELECT lifecycle_state
                    FROM snapshots
                    WHERE snapshot_id = ?
                    """,
                    (snapshot_id,),
                ).fetchone()
                if (
                    snapshot_row is None
                    or snapshot_row["lifecycle_state"] != SnapshotState.COMMITTED.value
                ):
                    raise RepositoryInvariantError(
                        "reorganization ledger requires a committed Snapshot"
                    )
                from ..reorganization import (
                    ReorganizationInvariantError,
                    ReorganizationRecord,
                    ReorganizationStatus,
                    canonicalize_reorganization_records,
                )

                rows = connection.execute(
                    """
                    SELECT
                        source_capsule_id,
                        kind,
                        item_id,
                        status,
                        before_tokens,
                        after_tokens,
                        required
                    FROM snapshot_reorganization_records
                    WHERE snapshot_id = ?
                    ORDER BY ordinal
                    """,
                    (snapshot_id,),
                ).fetchall()
                try:
                    records_list: list[ReorganizationRecord] = []
                    for row in rows:
                        required = row["required"]
                        if type(required) is not int or required not in (0, 1):
                            raise ReorganizationInvariantError(
                                "durable reorganization ledger required flag is invalid"
                            )
                        records_list.append(
                            ReorganizationRecord(
                                source_capsule_id=row["source_capsule_id"],
                                kind=row["kind"],
                                item_id=row["item_id"],
                                status=ReorganizationStatus(row["status"]),
                                before_tokens=row["before_tokens"],
                                after_tokens=row["after_tokens"],
                                required=bool(required),
                            )
                        )
                    records = canonicalize_reorganization_records(tuple(records_list))
                except (ReorganizationInvariantError, TypeError, ValueError) as error:
                    raise RepositoryInvariantError(
                        "durable reorganization ledger rows are invalid"
                    ) from error
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
                return records

    def read_claimed_request_view(
        self,
        *,
        job_id: str,
        owner: str,
        lease_epoch: int,
        now: datetime,
    ) -> RequestView:
        """Read the immutable compaction input frozen by one live Job lease."""

        now_text = _normalize_datetime(now)
        with self._factory.connection(read_only=True) as connection:
            connection.execute("BEGIN")
            try:
                self._require_live_fence(
                    connection,
                    job_id=job_id,
                    owner=owner,
                    lease_epoch=lease_epoch,
                    now=now_text,
                )
                job = self._job_by_id(connection, job_id)
                if job.base_snapshot_id is None:
                    snapshot = None
                    memberships: tuple[SnapshotCapsuleMembership, ...] = ()
                    covered_event_end = 0
                    if job.base_pointer_version != 0:
                        raise RepositoryInvariantError(
                            "empty Job base must have pointer version zero"
                        )
                else:
                    snapshot, memberships = self._snapshot_bundle_by_id(
                        connection,
                        job.base_snapshot_id,
                    )
                    if snapshot.session_key != job.session_key:
                        raise SessionIdentityConflict(
                            "Job base Snapshot belongs to a different durable session"
                        )
                    if snapshot.state != SnapshotState.COMMITTED:
                        raise RepositoryInvariantError(
                            "Job base Snapshot is not committed"
                        )
                    covered_event_end = snapshot.covered_event_end

                if covered_event_end > job.target_high_water_mark:
                    raise RepositoryInvariantError(
                        "Job base Snapshot coverage exceeds its frozen target"
                    )
                delta = self._events_between(
                    connection,
                    session_key=job.session_key,
                    start_exclusive=covered_event_end,
                    end_inclusive=job.target_high_water_mark,
                )
                expected_sequences = tuple(
                    range(covered_event_end + 1, job.target_high_water_mark + 1)
                )
                if tuple(event.sequence for event in delta) != expected_sequences:
                    raise RepositoryInvariantError(
                        "claimed request Delta is not contiguous through the frozen target"
                    )
                view = RequestView(
                    session_key=job.session_key,
                    snapshot=snapshot,
                    memberships=memberships,
                    pointer_version=job.base_pointer_version,
                    covered_event_end=covered_event_end,
                    high_water_mark=job.target_high_water_mark,
                    delta=delta,
                )
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
                return view

    def raise_compaction_intent(
        self,
        *,
        job_id: str,
        session_key: SessionKey,
        target_high_water_mark: int,
        now: datetime,
    ) -> CompactionJobEnvelope | None:
        """Run ``TX_RAISE_COMPACTION_INTENT``."""

        if not job_id.strip():
            raise ValueError("job_id must be non-empty")
        if target_high_water_mark < 1:
            raise ValueError("target_high_water_mark must be positive")
        now_text = _normalize_datetime(now)

        with self._factory.transaction(immediate=True) as connection:
            session_row = connection.execute(
                """
                SELECT canonical_session_key_json, next_event_sequence
                FROM sessions
                WHERE session_key_hash = ?
                """,
                (session_key.session_key_hash,),
            ).fetchone()
            if session_row is None:
                raise RepositoryInvariantError(
                    "compaction intent requires a durable session with Journal events"
                )
            if session_row["canonical_session_key_json"] != session_key.canonical_json():
                raise SessionIdentityConflict(
                    "session hash does not resolve to the exact canonical SessionKey"
                )
            high_water_mark = int(session_row["next_event_sequence"]) - 1
            if target_high_water_mark > high_water_mark:
                raise RepositoryInvariantError(
                    "compaction intent exceeds the durable Journal high-water mark"
                )

            base_snapshot_id, base_pointer_version, base_coverage = self._active_base(
                connection,
                session_key.session_key_hash,
            )
            if target_high_water_mark <= base_coverage:
                return None

            existing = connection.execute(
                """
                SELECT job_id, intent_target_high_water_mark
                FROM compaction_jobs
                WHERE session_key_hash = ?
                  AND state IN (
                      'PENDING',
                      'LEASED',
                      'COMPILING',
                      'AUDITING',
                      'READY_TO_COMMIT',
                      'RETRY_WAIT'
                  )
                """,
                (session_key.session_key_hash,),
            ).fetchone()
            if existing is not None:
                if target_high_water_mark > int(existing["intent_target_high_water_mark"]):
                    connection.execute(
                        """
                        UPDATE compaction_jobs
                        SET intent_target_high_water_mark = ?,
                            updated_at = ?
                        WHERE job_id = ?
                        """,
                        (target_high_water_mark, now_text, existing["job_id"]),
                    )
                    self._inject("intent.after_fold")
                return self._job_by_id(connection, str(existing["job_id"]))

            try:
                connection.execute(
                    """
                    INSERT INTO compaction_jobs (
                        job_id,
                        session_key_hash,
                        state,
                        target_high_water_mark,
                        intent_target_high_water_mark,
                        base_snapshot_id,
                        base_pointer_version,
                        candidate_snapshot_id,
                        lease_owner,
                        lease_epoch,
                        lease_expires_at,
                        attempt_count,
                        next_retry_at,
                        error_stage,
                        error_code,
                        error_message,
                        created_at,
                        updated_at,
                        committed_at
                    ) VALUES (
                        ?, ?, 'PENDING', ?, ?, ?, ?, NULL, NULL, 0, NULL, 0,
                        NULL, NULL, NULL, NULL, ?, ?, NULL
                    )
                    """,
                    (
                        job_id,
                        session_key.session_key_hash,
                        target_high_water_mark,
                        target_high_water_mark,
                        base_snapshot_id,
                        base_pointer_version,
                        now_text,
                        now_text,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RepositoryConflict(
                    "compaction Job identity conflicts with durable state"
                ) from error
            self._inject("intent.after_insert")
            return self._job_by_id(connection, job_id)

    def claim_job(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CompactionJobEnvelope | None:
        """Run ``TX_CLAIM_JOB`` and return the single eligible winner."""

        if not worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        now_text = _normalize_datetime(now)
        lease_text = _normalize_datetime(lease_expires_at)
        if lease_text <= now_text:
            raise ValueError("lease_expires_at must be later than now")

        with self._factory.transaction(immediate=True) as connection:
            while True:
                row = connection.execute(
                    """
                    SELECT *
                    FROM compaction_jobs
                    WHERE state = 'PENDING'
                       OR (state = 'RETRY_WAIT' AND next_retry_at <= ?)
                    ORDER BY created_at, job_id
                    LIMIT 1
                    """,
                    (now_text,),
                ).fetchone()
                if row is None:
                    return None

                base_snapshot_id, base_pointer_version, base_coverage = self._active_base(
                    connection, row["session_key_hash"]
                )
                target = int(row["intent_target_high_water_mark"])
                if target <= base_coverage:
                    connection.execute(
                        """
                        UPDATE compaction_jobs
                        SET state = 'CANCELLED',
                            next_retry_at = NULL,
                            updated_at = ?
                        WHERE job_id = ?
                        """,
                        (now_text, row["job_id"]),
                    )
                    continue

                cursor = connection.execute(
                    """
                    UPDATE compaction_jobs
                    SET state = 'LEASED',
                        target_high_water_mark = intent_target_high_water_mark,
                        base_snapshot_id = ?,
                        base_pointer_version = ?,
                        candidate_snapshot_id = NULL,
                        lease_owner = ?,
                        lease_epoch = lease_epoch + 1,
                        lease_expires_at = ?,
                        attempt_count = attempt_count + 1,
                        next_retry_at = NULL,
                        error_stage = NULL,
                        error_code = NULL,
                        error_message = NULL,
                        updated_at = ?,
                        committed_at = NULL
                    WHERE job_id = ?
                      AND (
                          state = 'PENDING'
                          OR (state = 'RETRY_WAIT' AND next_retry_at <= ?)
                      )
                    """,
                    (
                        base_snapshot_id,
                        base_pointer_version,
                        worker_id,
                        lease_text,
                        now_text,
                        row["job_id"],
                        now_text,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                self._inject("claim.after_update")
                return self._job_by_id(connection, str(row["job_id"]))

    def transition_job(
        self,
        *,
        job_id: str,
        owner: str,
        lease_epoch: int,
        to_state: CompactionJobState,
        now: datetime,
        candidate_snapshot_id: str | None = None,
    ) -> CompactionJobEnvelope:
        """Apply one repository-private fenced worker transition."""

        now_text = _normalize_datetime(now)
        with self._factory.transaction(immediate=True) as connection:
            row = self._require_live_fence(
                connection,
                job_id=job_id,
                owner=owner,
                lease_epoch=lease_epoch,
                now=now_text,
            )
            current_state = CompactionJobState(row["state"])
            if to_state not in _ALLOWED_WORKER_TRANSITIONS[current_state]:
                raise JobTransitionError(
                    f"transition {current_state.value} -> {to_state.value} is not allowed"
                )
            if to_state == CompactionJobState.READY_TO_COMMIT:
                if candidate_snapshot_id is None or not candidate_snapshot_id.strip():
                    raise JobTransitionError(
                        "READY_TO_COMMIT requires a non-empty candidate_snapshot_id"
                    )
            elif candidate_snapshot_id is not None:
                raise JobTransitionError(
                    "candidate_snapshot_id is allowed only for READY_TO_COMMIT"
                )

            cursor = connection.execute(
                """
                UPDATE compaction_jobs
                SET state = ?,
                    candidate_snapshot_id = ?,
                    updated_at = ?
                WHERE job_id = ?
                  AND lease_owner = ?
                  AND lease_epoch = ?
                  AND lease_expires_at > ?
                """,
                (
                    to_state.value,
                    candidate_snapshot_id,
                    now_text,
                    job_id,
                    owner,
                    lease_epoch,
                    now_text,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLeaseError("worker transition lost its lease fence")
            self._inject("transition.after_update")
            return self._job_by_id(connection, job_id)

    def renew_job_lease(
        self,
        *,
        job_id: str,
        owner: str,
        lease_epoch: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CompactionJobEnvelope:
        """Renew one live worker lease without changing its fencing epoch."""

        now_text = _normalize_datetime(now)
        lease_text = _normalize_datetime(lease_expires_at)
        if lease_text <= now_text:
            raise ValueError("lease_expires_at must be later than now")
        with self._factory.transaction(immediate=True) as connection:
            row = self._require_live_fence(
                connection,
                job_id=job_id,
                owner=owner,
                lease_epoch=lease_epoch,
                now=now_text,
            )
            if lease_text <= str(row["lease_expires_at"]):
                raise JobTransitionError("lease renewal must strictly extend the lease")
            cursor = connection.execute(
                """
                UPDATE compaction_jobs
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                  AND lease_owner = ?
                  AND lease_epoch = ?
                  AND lease_expires_at > ?
                """,
                (
                    lease_text,
                    now_text,
                    job_id,
                    owner,
                    lease_epoch,
                    now_text,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLeaseError("lease renewal lost its fencing predicate")
            self._inject("lease.after_renew")
            return self._job_by_id(connection, job_id)

    def fail_job(
        self,
        *,
        job_id: str,
        owner: str,
        lease_epoch: int,
        now: datetime,
        error_stage: str,
        error_code: str,
        error_message: str,
        retry_at: datetime | None,
    ) -> CompactionJobEnvelope:
        """Run ``TX_FAIL_JOB`` under the worker's live fence."""

        error_values = (error_stage, error_code, error_message)
        if any(not value.strip() for value in error_values):
            raise ValueError("failure error tuple must contain non-empty strings")
        if len(error_message) > 512 or any(
            character in error_message for character in ("\r", "\n", "\x00")
        ):
            raise ValueError("error_message must be one bounded redacted line")

        now_text = _normalize_datetime(now)
        if retry_at is None:
            next_state = CompactionJobState.FAILED
            retry_text = None
        else:
            next_state = CompactionJobState.RETRY_WAIT
            retry_text = _normalize_datetime(retry_at)
            if retry_text <= now_text:
                raise ValueError("retry_at must be later than now")

        with self._factory.transaction(immediate=True) as connection:
            self._require_live_fence(
                connection,
                job_id=job_id,
                owner=owner,
                lease_epoch=lease_epoch,
                now=now_text,
            )
            cursor = connection.execute(
                """
                UPDATE compaction_jobs
                SET state = ?,
                    candidate_snapshot_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_retry_at = ?,
                    error_stage = ?,
                    error_code = ?,
                    error_message = ?,
                    updated_at = ?,
                    committed_at = NULL
                WHERE job_id = ?
                  AND lease_owner = ?
                  AND lease_epoch = ?
                  AND lease_expires_at > ?
                """,
                (
                    next_state.value,
                    retry_text,
                    error_stage,
                    error_code,
                    error_message,
                    now_text,
                    job_id,
                    owner,
                    lease_epoch,
                    now_text,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLeaseError("failure transition lost its fencing predicate")
            self._inject("failure.after_update")
            return self._job_by_id(connection, job_id)

    def recover_expired_leases(
        self,
        *,
        now: datetime,
    ) -> tuple[CompactionJobEnvelope, ...]:
        """Run ``TX_RECOVER_EXPIRED_LEASES`` as one atomic recovery scan."""

        now_text = _normalize_datetime(now)
        with self._factory.transaction(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT job_id
                FROM compaction_jobs
                WHERE state IN (
                    'LEASED',
                    'COMPILING',
                    'AUDITING',
                    'READY_TO_COMMIT'
                )
                  AND lease_expires_at <= ?
                ORDER BY created_at, job_id
                """,
                (now_text,),
            ).fetchall()
            recovered_ids: list[str] = []
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE compaction_jobs
                    SET state = 'PENDING',
                        candidate_snapshot_id = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        next_retry_at = NULL,
                        updated_at = ?,
                        committed_at = NULL
                    WHERE job_id = ?
                      AND state IN (
                          'LEASED',
                          'COMPILING',
                          'AUDITING',
                          'READY_TO_COMMIT'
                      )
                      AND lease_expires_at <= ?
                    """,
                    (now_text, row["job_id"], now_text),
                )
                if cursor.rowcount != 1:
                    continue
                recovered_ids.append(str(row["job_id"]))
                self._inject("recovery.after_update")
            return tuple(self._job_by_id(connection, job_id) for job_id in recovered_ids)

    def publish_snapshot(
        self,
        *,
        job_id: str,
        owner: str,
        lease_epoch: int,
        candidate_snapshot: SnapshotEnvelope,
        memberships: Sequence[SnapshotCapsuleMembership],
        reorganization_records: Sequence[ReorganizationRecord] = (),
        token_ceiling: int,
        now: datetime,
    ) -> PublishResult:
        """Run ``TX_PUBLISH_SNAPSHOT`` with savepoint-isolated CAS conflict."""

        if token_ceiling < 0:
            raise ValueError("token_ceiling must be non-negative")
        now_text = _normalize_datetime(now)
        membership_tuple = tuple(memberships)
        record_tuple = tuple(reorganization_records)

        with self._factory.transaction(immediate=True) as connection:
            fence_row = self._require_live_fence(
                connection,
                job_id=job_id,
                owner=owner,
                lease_epoch=lease_epoch,
                now=now_text,
            )
            if CompactionJobState(fence_row["state"]) != (CompactionJobState.READY_TO_COMMIT):
                raise JobTransitionError("publication requires a READY_TO_COMMIT Job")
            if fence_row["candidate_snapshot_id"] != candidate_snapshot.snapshot_id:
                raise JobTransitionError("candidate Snapshot id does not match the fenced Job")
            if candidate_snapshot.state != SnapshotState.CANDIDATE:
                raise JobTransitionError(
                    "publication accepts only a worker-local CANDIDATE envelope"
                )

            job = self._job_by_id(connection, job_id)
            if candidate_snapshot.session_key != job.session_key:
                raise JobTransitionError("candidate Snapshot belongs to a different Job session")
            self._validate_candidate_memberships(candidate_snapshot, membership_tuple)
            from ..reorganization import (
                ReorganizationInvariantError,
                canonicalize_reorganization_records,
            )

            try:
                canonical_records = canonicalize_reorganization_records(record_tuple)
            except ReorganizationInvariantError as error:
                raise RepositoryInvariantError(
                    "candidate reorganization records are invalid"
                ) from error

            if job.base_snapshot_id is None:
                previous_snapshot = None
                previous_memberships: tuple[SnapshotCapsuleMembership, ...] = ()
            else:
                previous_snapshot, previous_memberships = self._snapshot_bundle_by_id(
                    connection,
                    job.base_snapshot_id,
                )
            source_events = self._events_between(
                connection,
                session_key=job.session_key,
                start_exclusive=(
                    previous_snapshot.covered_event_end if previous_snapshot is not None else 0
                ),
                end_inclusive=job.target_high_water_mark,
            )
            candidate_capsules = tuple(membership.capsule for membership in membership_tuple)
            report = validate_permanent(
                previous_snapshot=previous_snapshot,
                previous_capsules=tuple(membership.capsule for membership in previous_memberships),
                candidate_snapshot=candidate_snapshot,
                candidate_capsules=candidate_capsules,
                source_events=source_events,
                target_high_water_mark=job.target_high_water_mark,
                token_ceiling=token_ceiling,
            )
            report = _apply_reorganization_record_quality_floor(report, canonical_records)
            if not report.passed:
                raise PublicationRejected(report)

            committed_snapshot = SnapshotEnvelope.model_validate(
                {
                    **candidate_snapshot.model_dump(),
                    "state": SnapshotState.COMMITTED,
                    "committed_at": _parse_datetime(now_text),
                }
            )
            self._inject("publish.before_savepoint")
            connection.execute("SAVEPOINT publish_candidate")
            conflict = False
            for membership in membership_tuple:
                try:
                    self._insert_or_verify_capsule(
                        connection,
                        membership.capsule,
                    )
                except sqlite3.IntegrityError:
                    conflict = True
                    break
                self._inject("publish.after_capsule")

            if not conflict:
                try:
                    self._insert_committed_snapshot(connection, committed_snapshot)
                except sqlite3.IntegrityError:
                    conflict = True
                else:
                    self._inject("publish.after_snapshot")

            if not conflict:
                for membership in membership_tuple:
                    try:
                        connection.execute(
                            """
                            INSERT INTO snapshot_capsules (
                                snapshot_id,
                                ordinal,
                                capsule_id,
                                slot
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                committed_snapshot.snapshot_id,
                                membership.ordinal,
                                membership.capsule_id,
                                membership.slot,
                            ),
                        )
                    except sqlite3.IntegrityError:
                        conflict = True
                        break
                    self._inject("publish.after_membership")

            if not conflict:
                for ordinal, record in enumerate(canonical_records):
                    connection.execute(
                        """
                        INSERT INTO snapshot_reorganization_records (
                            snapshot_id,
                            ordinal,
                            source_capsule_id,
                            kind,
                            item_id,
                            status,
                            before_tokens,
                            after_tokens,
                            required
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            committed_snapshot.snapshot_id,
                            ordinal,
                            record.source_capsule_id,
                            record.kind,
                            record.item_id,
                            record.status.value,
                            record.before_tokens,
                            record.after_tokens,
                            int(record.required),
                        ),
                    )
                    self._inject("publish.after_ledger")

            if not conflict:
                conflict = not self._cas_active_pointer(
                    connection,
                    job=job,
                    snapshot=committed_snapshot,
                    updated_at=now_text,
                )

            if conflict:
                connection.execute("ROLLBACK TO SAVEPOINT publish_candidate")
                connection.execute("RELEASE SAVEPOINT publish_candidate")
                superseded = self._finish_publish_job(
                    connection,
                    job=job,
                    owner=owner,
                    lease_epoch=lease_epoch,
                    now=now_text,
                    outcome=PublishOutcome.SUPERSEDED,
                )
                self._inject("publish.after_terminal")
                winner, pointer_version, _ = self._active_snapshot_bundle(
                    connection,
                    job.session_key,
                )
                self._preserve_follow_up(
                    connection,
                    terminal_job=superseded,
                    winner=winner,
                    pointer_version=pointer_version,
                    now=now_text,
                )
                self._inject("publish.after_follow_up")
                return PublishResult(
                    outcome=PublishOutcome.SUPERSEDED,
                    job=superseded,
                    winner=winner,
                    pointer_version=pointer_version,
                )

            pointer_version = job.base_pointer_version + 1
            self._inject("publish.after_pointer")
            committed_job = self._finish_publish_job(
                connection,
                job=job,
                owner=owner,
                lease_epoch=lease_epoch,
                now=now_text,
                outcome=PublishOutcome.COMMITTED,
            )
            self._inject("publish.after_terminal")
            self._preserve_follow_up(
                connection,
                terminal_job=committed_job,
                winner=committed_snapshot,
                pointer_version=pointer_version,
                now=now_text,
            )
            self._inject("publish.after_follow_up")
            connection.execute("RELEASE SAVEPOINT publish_candidate")
            return PublishResult(
                outcome=PublishOutcome.COMMITTED,
                job=committed_job,
                winner=committed_snapshot,
                pointer_version=pointer_version,
            )

    def _read_request_view(
        self,
        connection: sqlite3.Connection,
        session_key: SessionKey,
    ) -> RequestView:
        session_row = connection.execute(
            """
            SELECT canonical_session_key_json, next_event_sequence
            FROM sessions
            WHERE session_key_hash = ?
            """,
            (session_key.session_key_hash,),
        ).fetchone()
        if session_row is None:
            return RequestView(
                session_key=session_key,
                snapshot=None,
                memberships=(),
                pointer_version=0,
                covered_event_end=0,
                high_water_mark=0,
                delta=(),
            )
        if session_row["canonical_session_key_json"] != session_key.canonical_json():
            raise SessionIdentityConflict(
                "session hash does not resolve to the exact canonical SessionKey"
            )

        high_water_mark = int(session_row["next_event_sequence"]) - 1
        self._inject("read.after_high_water")
        active_row = connection.execute(
            """
            SELECT
                active.pointer_version,
                snapshot.*
            FROM active_snapshots AS active
            JOIN snapshots AS snapshot
              ON snapshot.snapshot_id = active.snapshot_id
            WHERE active.session_key_hash = ?
            """,
            (session_key.session_key_hash,),
        ).fetchone()
        self._inject("read.after_pointer")

        if active_row is None:
            snapshot = None
            memberships: tuple[SnapshotCapsuleMembership, ...] = ()
            pointer_version = 0
            covered_event_end = 0
        else:
            memberships = self._memberships_for_snapshot(
                connection,
                snapshot_id=active_row["snapshot_id"],
                session_key=session_key,
            )
            snapshot = self._snapshot_from_row(
                active_row,
                session_key=session_key,
                capsule_ids=tuple(item.capsule_id for item in memberships),
            )
            pointer_version = int(active_row["pointer_version"])
            covered_event_end = snapshot.covered_event_end
            self._validate_snapshot_memberships(snapshot, memberships)

        if covered_event_end > high_water_mark:
            raise RepositoryInvariantError(
                "active Snapshot coverage exceeds the Journal high-water mark"
            )

        rows = connection.execute(
            """
            SELECT
                event.*,
                session.canonical_session_key_json
            FROM journal_events AS event
            JOIN sessions AS session
              ON session.session_key_hash = event.session_key_hash
            WHERE event.session_key_hash = ?
              AND event.sequence > ?
              AND event.sequence <= ?
            ORDER BY event.sequence
            """,
            (
                session_key.session_key_hash,
                covered_event_end,
                high_water_mark,
            ),
        ).fetchall()
        delta = tuple(self._event_from_row(row) for row in rows)
        expected_sequences = tuple(range(covered_event_end + 1, high_water_mark + 1))
        if tuple(event.sequence for event in delta) != expected_sequences:
            raise RepositoryInvariantError(
                "request Delta is not contiguous through the fixed high-water mark"
            )

        return RequestView(
            session_key=session_key,
            snapshot=snapshot,
            memberships=memberships,
            pointer_version=pointer_version,
            covered_event_end=covered_event_end,
            high_water_mark=high_water_mark,
            delta=delta,
        )

    def _capture_event(
        self,
        *,
        event_id: str,
        session_key: SessionKey,
        event_type: EventType,
        content: str,
        idempotency_key: str,
        token_count: int,
        created_at: datetime,
    ) -> EventEnvelope:
        normalized_created_at = _normalize_datetime(created_at)
        requested = EventEnvelope.create(
            event_id=event_id,
            session_key=session_key,
            sequence=1,
            event_type=event_type,
            content=content,
            idempotency_key=idempotency_key,
            token_count=token_count,
            created_at=_parse_datetime(normalized_created_at),
        )

        with self._factory.transaction(immediate=True) as connection:
            self._ensure_session(
                connection,
                session_key=session_key,
                timestamp=normalized_created_at,
            )
            self._inject("capture.after_session")
            existing = self._find_idempotent_event(
                connection,
                session_key_hash=session_key.session_key_hash,
                source_hook=requested.source_hook,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if not self._is_exact_replay(existing, requested):
                    raise IdempotencyConflict(
                        "event idempotency tuple was reused with different immutable data"
                    )
                return existing

            row = connection.execute(
                """
                SELECT next_event_sequence
                FROM sessions
                WHERE session_key_hash = ?
                """,
                (session_key.session_key_hash,),
            ).fetchone()
            if row is None:
                raise SessionIdentityConflict("session disappeared during event capture")
            sequence = int(row["next_event_sequence"])
            connection.execute(
                """
                UPDATE sessions
                SET next_event_sequence = next_event_sequence + 1,
                    updated_at = ?
                WHERE session_key_hash = ?
                  AND next_event_sequence = ?
                """,
                (
                    normalized_created_at,
                    session_key.session_key_hash,
                    sequence,
                ),
            )
            self._inject("capture.after_allocate")

            event = EventEnvelope.create(
                event_id=event_id,
                session_key=session_key,
                sequence=sequence,
                event_type=event_type,
                content=content,
                idempotency_key=idempotency_key,
                token_count=token_count,
                created_at=_parse_datetime(normalized_created_at),
            )
            self._inject("capture.before_insert")
            try:
                connection.execute(
                    """
                    INSERT INTO journal_events (
                        event_id,
                        session_key_hash,
                        sequence,
                        event_type,
                        role,
                        content,
                        source_hook,
                        idempotency_key,
                        token_count,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        session_key.session_key_hash,
                        event.sequence,
                        event.event_type.value,
                        event.role.value,
                        event.content,
                        event.source_hook.value,
                        event.idempotency_key,
                        event.token_count,
                        normalized_created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise EventIdentityConflict(
                    "event identity or sequence conflicts with durable state"
                ) from error
            self._inject("capture.after_insert")
            return event

    def _ensure_session(
        self,
        connection: sqlite3.Connection,
        *,
        session_key: SessionKey,
        timestamp: str,
    ) -> None:
        try:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_key_hash,
                    canonical_session_key_json,
                    platform_instance_id,
                    message_type,
                    session_id,
                    group_id,
                    user_id,
                    conversation_id,
                    persona_id,
                    next_event_sequence,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(session_key_hash) DO NOTHING
                """,
                (
                    session_key.session_key_hash,
                    session_key.canonical_json(),
                    session_key.platform_instance_id,
                    session_key.message_type,
                    session_key.session_id,
                    session_key.group_id,
                    session_key.user_id,
                    session_key.conversation_id,
                    session_key.persona_id,
                    timestamp,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise SessionIdentityConflict(
                "canonical SessionKey conflicts with durable session identity"
            ) from error

        row = connection.execute(
            """
            SELECT canonical_session_key_json
            FROM sessions
            WHERE session_key_hash = ?
            """,
            (session_key.session_key_hash,),
        ).fetchone()
        if row is None or str(row["canonical_session_key_json"]) != session_key.canonical_json():
            raise SessionIdentityConflict(
                "session hash does not resolve to the exact canonical SessionKey"
            )

    @staticmethod
    def _active_base(
        connection: sqlite3.Connection,
        session_key_hash: str,
    ) -> tuple[str | None, int, int]:
        row = connection.execute(
            """
            SELECT
                active.snapshot_id,
                active.pointer_version,
                snapshot.covered_event_end,
                snapshot.session_key_hash AS snapshot_session_key_hash
            FROM active_snapshots AS active
            JOIN snapshots AS snapshot
              ON snapshot.snapshot_id = active.snapshot_id
            WHERE active.session_key_hash = ?
            """,
            (session_key_hash,),
        ).fetchone()
        if row is None:
            return None, 0, 0
        if row["snapshot_session_key_hash"] != session_key_hash:
            raise RepositoryInvariantError(
                "active pointer resolves to a Snapshot from another session"
            )
        return (
            str(row["snapshot_id"]),
            int(row["pointer_version"]),
            int(row["covered_event_end"]),
        )

    @staticmethod
    def _job_by_id(
        connection: sqlite3.Connection,
        job_id: str,
    ) -> CompactionJobEnvelope:
        row = connection.execute(
            """
            SELECT
                job.*,
                session.canonical_session_key_json
            FROM compaction_jobs AS job
            JOIN sessions AS session
              ON session.session_key_hash = job.session_key_hash
            WHERE job.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise RepositoryInvariantError("durable compaction Job is missing")
        return SQLiteRepository._job_from_row(row)

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> CompactionJobEnvelope:
        try:
            session_key = SessionKey.model_validate_json(row["canonical_session_key_json"])
            job = CompactionJobEnvelope(
                job_id=row["job_id"],
                session_key=session_key,
                state=CompactionJobState(row["state"]),
                target_high_water_mark=row["target_high_water_mark"],
                intent_target_high_water_mark=row["intent_target_high_water_mark"],
                base_snapshot_id=row["base_snapshot_id"],
                base_pointer_version=row["base_pointer_version"],
                candidate_snapshot_id=row["candidate_snapshot_id"],
                attempt_count=row["attempt_count"],
                lease_owner=row["lease_owner"],
                lease_epoch=row["lease_epoch"],
                lease_expires_at=(
                    _parse_datetime(row["lease_expires_at"])
                    if row["lease_expires_at"] is not None
                    else None
                ),
                next_retry_at=(
                    _parse_datetime(row["next_retry_at"])
                    if row["next_retry_at"] is not None
                    else None
                ),
                error_stage=row["error_stage"],
                error_code=row["error_code"],
                error_message=row["error_message"],
                created_at=_parse_datetime(row["created_at"]),
                updated_at=_parse_datetime(row["updated_at"]),
                committed_at=(
                    _parse_datetime(row["committed_at"])
                    if row["committed_at"] is not None
                    else None
                ),
            )
        except (TypeError, ValueError) as error:
            raise RepositoryInvariantError(
                "compaction Job row does not form a canonical envelope"
            ) from error
        if row["session_key_hash"] != session_key.session_key_hash:
            raise RepositoryInvariantError(
                "compaction Job physical session identity does not round-trip"
            )
        return job

    @staticmethod
    def _require_live_fence(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        owner: str,
        lease_epoch: int,
        now: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM compaction_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if (
            row is None
            or CompactionJobState(row["state"]) not in _WORKING_JOB_STATES
            or row["lease_owner"] != owner
            or int(row["lease_epoch"]) != lease_epoch
            or row["lease_expires_at"] is None
            or str(row["lease_expires_at"]) <= now
        ):
            raise StaleLeaseError("worker does not own a matching unexpired lease")
        return row

    @staticmethod
    def _validate_candidate_memberships(
        snapshot: SnapshotEnvelope,
        memberships: tuple[SnapshotCapsuleMembership, ...],
    ) -> None:
        if not memberships:
            raise RepositoryInvariantError("publication requires at least one Capsule membership")
        if tuple(item.ordinal for item in memberships) != tuple(range(len(memberships))):
            raise RepositoryInvariantError(
                "candidate Capsule ordinals must be contiguous from zero"
            )
        if any(not item.slot.strip() for item in memberships):
            raise RepositoryInvariantError("candidate Capsule membership slot must be non-empty")
        capsule_ids = tuple(item.capsule_id for item in memberships)
        if len(set(capsule_ids)) != len(capsule_ids):
            raise RepositoryInvariantError("candidate Capsule membership ids must be unique")
        if snapshot.capsule_ids != capsule_ids:
            raise RepositoryInvariantError(
                "candidate Snapshot ids disagree with ordered membership"
            )

    @staticmethod
    def _snapshot_bundle_by_id(
        connection: sqlite3.Connection,
        snapshot_id: str,
    ) -> tuple[SnapshotEnvelope, tuple[SnapshotCapsuleMembership, ...]]:
        row = connection.execute(
            """
            SELECT
                snapshot.*,
                session.canonical_session_key_json
            FROM snapshots AS snapshot
            JOIN sessions AS session
              ON session.session_key_hash = snapshot.session_key_hash
            WHERE snapshot.snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise RepositoryInvariantError("Job base Snapshot is missing")
        try:
            session_key = SessionKey.model_validate_json(row["canonical_session_key_json"])
        except ValueError as error:
            raise RepositoryInvariantError("Snapshot SessionKey cannot be reconstructed") from error
        memberships = SQLiteRepository._memberships_for_snapshot(
            connection,
            snapshot_id=snapshot_id,
            session_key=session_key,
        )
        snapshot = SQLiteRepository._snapshot_from_row(
            row,
            session_key=session_key,
            capsule_ids=tuple(item.capsule_id for item in memberships),
        )
        SQLiteRepository._validate_snapshot_memberships(snapshot, memberships)
        return snapshot, memberships

    @staticmethod
    def _active_snapshot_bundle(
        connection: sqlite3.Connection,
        session_key: SessionKey,
    ) -> tuple[
        SnapshotEnvelope,
        int,
        tuple[SnapshotCapsuleMembership, ...],
    ]:
        row = connection.execute(
            """
            SELECT snapshot_id, pointer_version
            FROM active_snapshots
            WHERE session_key_hash = ?
            """,
            (session_key.session_key_hash,),
        ).fetchone()
        if row is None:
            raise RepositoryInvariantError("publish conflict has no durable active winner")
        snapshot, memberships = SQLiteRepository._snapshot_bundle_by_id(
            connection,
            str(row["snapshot_id"]),
        )
        if snapshot.session_key != session_key:
            raise RepositoryInvariantError("publish winner belongs to another durable session")
        return snapshot, int(row["pointer_version"]), memberships

    @staticmethod
    def _events_between(
        connection: sqlite3.Connection,
        *,
        session_key: SessionKey,
        start_exclusive: int,
        end_inclusive: int,
    ) -> tuple[EventEnvelope, ...]:
        rows = connection.execute(
            """
            SELECT
                event.*,
                session.canonical_session_key_json
            FROM journal_events AS event
            JOIN sessions AS session
              ON session.session_key_hash = event.session_key_hash
            WHERE event.session_key_hash = ?
              AND event.sequence > ?
              AND event.sequence <= ?
            ORDER BY event.sequence
            """,
            (
                session_key.session_key_hash,
                start_exclusive,
                end_inclusive,
            ),
        ).fetchall()
        return tuple(SQLiteRepository._event_from_row(row) for row in rows)

    @staticmethod
    def _insert_or_verify_capsule(
        connection: sqlite3.Connection,
        capsule: ContextCapsuleEnvelope,
    ) -> None:
        canonical_json = _canonical_model_json(capsule)
        row = connection.execute(
            """
            SELECT *
            FROM capsules
            WHERE capsule_id = ?
            """,
            (capsule.capsule_id,),
        ).fetchone()
        if row is not None:
            if (
                row["session_key_hash"] != capsule.session_key.session_key_hash
                or row["level"] != capsule.level.value
                or int(row["covered_event_start"]) != capsule.covered_event_start
                or int(row["covered_event_end"]) != capsule.covered_event_end
                or row["canonical_capsule_json"] != canonical_json
                or int(row["token_cost"]) != capsule.token_cost
                or float(row["source_coverage"]) != capsule.quality.source_coverage
                or row["created_at"] != _normalize_datetime(capsule.created_at)
            ):
                raise sqlite3.IntegrityError(
                    "Capsule id conflicts with different immutable content"
                )
            return

        connection.execute(
            """
            INSERT INTO capsules (
                capsule_id,
                session_key_hash,
                level,
                covered_event_start,
                covered_event_end,
                canonical_capsule_json,
                token_cost,
                source_coverage,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capsule.capsule_id,
                capsule.session_key.session_key_hash,
                capsule.level.value,
                capsule.covered_event_start,
                capsule.covered_event_end,
                canonical_json,
                capsule.token_cost,
                capsule.quality.source_coverage,
                _normalize_datetime(capsule.created_at),
            ),
        )

    @staticmethod
    def _insert_committed_snapshot(
        connection: sqlite3.Connection,
        snapshot: SnapshotEnvelope,
    ) -> None:
        if snapshot.committed_at is None:
            raise RepositoryInvariantError("committed Snapshot insert requires committed_at")
        connection.execute(
            """
            INSERT INTO snapshots (
                snapshot_id,
                session_key_hash,
                base_snapshot_id,
                covered_event_end,
                source_high_water_mark,
                exact_anchor_ids_json,
                rendered_context,
                token_cost,
                audit_outcome,
                lifecycle_state,
                created_at,
                committed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.session_key.session_key_hash,
                snapshot.base_snapshot_id,
                snapshot.covered_event_end,
                snapshot.source_high_water_mark,
                json.dumps(
                    list(snapshot.exact_anchor_ids),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                snapshot.rendered_context,
                snapshot.token_cost,
                _canonical_model_json(snapshot.audit_outcome),
                _normalize_datetime(snapshot.created_at),
                _normalize_datetime(snapshot.committed_at),
            ),
        )

    @staticmethod
    def _cas_active_pointer(
        connection: sqlite3.Connection,
        *,
        job: CompactionJobEnvelope,
        snapshot: SnapshotEnvelope,
        updated_at: str,
    ) -> bool:
        if job.base_snapshot_id is None:
            cursor = connection.execute(
                """
                INSERT INTO active_snapshots (
                    session_key_hash,
                    snapshot_id,
                    pointer_version,
                    updated_at
                )
                SELECT ?, ?, 1, ?
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM active_snapshots
                    WHERE session_key_hash = ?
                )
                """,
                (
                    job.session_key.session_key_hash,
                    snapshot.snapshot_id,
                    updated_at,
                    job.session_key.session_key_hash,
                ),
            )
            return cursor.rowcount == 1

        cursor = connection.execute(
            """
            UPDATE active_snapshots
            SET snapshot_id = ?,
                pointer_version = pointer_version + 1,
                updated_at = ?
            WHERE session_key_hash = ?
              AND snapshot_id = ?
              AND pointer_version = ?
            """,
            (
                snapshot.snapshot_id,
                updated_at,
                job.session_key.session_key_hash,
                job.base_snapshot_id,
                job.base_pointer_version,
            ),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _finish_publish_job(
        connection: sqlite3.Connection,
        *,
        job: CompactionJobEnvelope,
        owner: str,
        lease_epoch: int,
        now: str,
        outcome: PublishOutcome,
    ) -> CompactionJobEnvelope:
        committed_at = now if outcome == PublishOutcome.COMMITTED else None
        cursor = connection.execute(
            """
            UPDATE compaction_jobs
            SET state = ?,
                lease_owner = NULL,
                lease_expires_at = NULL,
                next_retry_at = NULL,
                updated_at = ?,
                committed_at = ?
            WHERE job_id = ?
              AND state = 'READY_TO_COMMIT'
              AND candidate_snapshot_id = ?
              AND lease_owner = ?
              AND lease_epoch = ?
              AND lease_expires_at > ?
            """,
            (
                outcome.value,
                now,
                committed_at,
                job.job_id,
                job.candidate_snapshot_id,
                owner,
                lease_epoch,
                now,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleLeaseError("publication lost its fenced terminal transition")
        return SQLiteRepository._job_by_id(connection, job.job_id)

    @staticmethod
    def _preserve_follow_up(
        connection: sqlite3.Connection,
        *,
        terminal_job: CompactionJobEnvelope,
        winner: SnapshotEnvelope,
        pointer_version: int,
        now: str,
    ) -> None:
        intent = terminal_job.intent_target_high_water_mark
        if intent <= winner.covered_event_end:
            return

        existing = connection.execute(
            """
            SELECT job_id, intent_target_high_water_mark
            FROM compaction_jobs
            WHERE session_key_hash = ?
              AND state IN (
                  'PENDING',
                  'LEASED',
                  'COMPILING',
                  'AUDITING',
                  'READY_TO_COMMIT',
                  'RETRY_WAIT'
              )
            """,
            (terminal_job.session_key.session_key_hash,),
        ).fetchone()
        if existing is not None:
            if intent > int(existing["intent_target_high_water_mark"]):
                connection.execute(
                    """
                    UPDATE compaction_jobs
                    SET intent_target_high_water_mark = ?,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (intent, now, existing["job_id"]),
                )
            return

        digest = hashlib.sha256(
            (f"{terminal_job.job_id}\0{winner.snapshot_id}\0{pointer_version}\0{intent}").encode()
        ).hexdigest()
        follow_up_id = f"followup-{digest[:32]}"
        connection.execute(
            """
            INSERT INTO compaction_jobs (
                job_id,
                session_key_hash,
                state,
                target_high_water_mark,
                intent_target_high_water_mark,
                base_snapshot_id,
                base_pointer_version,
                candidate_snapshot_id,
                lease_owner,
                lease_epoch,
                lease_expires_at,
                attempt_count,
                next_retry_at,
                error_stage,
                error_code,
                error_message,
                created_at,
                updated_at,
                committed_at
            ) VALUES (
                ?, ?, 'PENDING', ?, ?, ?, ?, NULL, NULL, 0, NULL, 0,
                NULL, NULL, NULL, NULL, ?, ?, NULL
            )
            """,
            (
                follow_up_id,
                terminal_job.session_key.session_key_hash,
                intent,
                intent,
                winner.snapshot_id,
                pointer_version,
                now,
                now,
            ),
        )

    @staticmethod
    def _find_idempotent_event(
        connection: sqlite3.Connection,
        *,
        session_key_hash: str,
        source_hook: SourceHook,
        idempotency_key: str,
    ) -> EventEnvelope | None:
        row = connection.execute(
            """
            SELECT
                event.*,
                session.canonical_session_key_json
            FROM journal_events AS event
            JOIN sessions AS session
              ON session.session_key_hash = event.session_key_hash
            WHERE event.session_key_hash = ?
              AND event.source_hook = ?
              AND event.idempotency_key = ?
            """,
            (session_key_hash, source_hook.value, idempotency_key),
        ).fetchone()
        return SQLiteRepository._event_from_row(row) if row is not None else None

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            event_id=row["event_id"],
            session_key=SessionKey.model_validate_json(row["canonical_session_key_json"]),
            sequence=row["sequence"],
            event_type=EventType(row["event_type"]),
            role=EventRole(row["role"]),
            content=row["content"],
            source_hook=SourceHook(row["source_hook"]),
            idempotency_key=row["idempotency_key"],
            token_count=row["token_count"],
            created_at=_parse_datetime(row["created_at"]),
        )

    @staticmethod
    def _memberships_for_snapshot(
        connection: sqlite3.Connection,
        *,
        snapshot_id: str,
        session_key: SessionKey,
    ) -> tuple[SnapshotCapsuleMembership, ...]:
        rows = connection.execute(
            """
            SELECT
                membership.ordinal,
                membership.slot,
                capsule.*
            FROM snapshot_capsules AS membership
            JOIN capsules AS capsule
              ON capsule.capsule_id = membership.capsule_id
            WHERE membership.snapshot_id = ?
            ORDER BY membership.ordinal
            """,
            (snapshot_id,),
        ).fetchall()
        memberships: list[SnapshotCapsuleMembership] = []
        for expected_ordinal, row in enumerate(rows):
            capsule = ContextCapsuleEnvelope.model_validate_json(row["canonical_capsule_json"])
            if (
                int(row["ordinal"]) != expected_ordinal
                or row["capsule_id"] != capsule.capsule_id
                or row["session_key_hash"] != session_key.session_key_hash
                or capsule.session_key != session_key
                or row["level"] != capsule.level.value
                or int(row["covered_event_start"]) != capsule.covered_event_start
                or int(row["covered_event_end"]) != capsule.covered_event_end
                or int(row["token_cost"]) != capsule.token_cost
                or float(row["source_coverage"]) != capsule.quality.source_coverage
                or row["created_at"] != _normalize_datetime(capsule.created_at)
            ):
                raise RepositoryInvariantError(
                    "Snapshot membership does not round-trip its canonical Capsule"
                )
            memberships.append(
                SnapshotCapsuleMembership(
                    ordinal=expected_ordinal,
                    slot=str(row["slot"]),
                    capsule=capsule,
                )
            )
        return tuple(memberships)

    @staticmethod
    def _snapshot_from_row(
        row: sqlite3.Row,
        *,
        session_key: SessionKey,
        capsule_ids: tuple[str, ...],
    ) -> SnapshotEnvelope:
        try:
            exact_anchor_ids = tuple(json.loads(row["exact_anchor_ids_json"]))
            audit_outcome = SnapshotAuditOutcome.model_validate(json.loads(row["audit_outcome"]))
            snapshot = SnapshotEnvelope(
                snapshot_id=row["snapshot_id"],
                session_key=session_key,
                base_snapshot_id=row["base_snapshot_id"],
                covered_event_end=row["covered_event_end"],
                source_high_water_mark=row["source_high_water_mark"],
                capsule_ids=capsule_ids,
                exact_anchor_ids=exact_anchor_ids,
                rendered_context=row["rendered_context"],
                token_cost=row["token_cost"],
                audit_outcome=audit_outcome,
                state=SnapshotState(row["lifecycle_state"]),
                created_at=_parse_datetime(row["created_at"]),
                committed_at=_parse_datetime(row["committed_at"]),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RepositoryInvariantError(
                "committed Snapshot row does not form a canonical envelope"
            ) from error
        if row["session_key_hash"] != session_key.session_key_hash:
            raise RepositoryInvariantError("active Snapshot belongs to a different durable session")
        return snapshot

    @staticmethod
    def _validate_snapshot_memberships(
        snapshot: SnapshotEnvelope,
        memberships: tuple[SnapshotCapsuleMembership, ...],
    ) -> None:
        if not memberships or snapshot.capsule_ids != tuple(
            item.capsule_id for item in memberships
        ):
            raise RepositoryInvariantError(
                "committed Snapshot has invalid ordered Capsule membership"
            )
        active_anchor_ids = tuple(
            anchor.anchor_id
            for membership in memberships
            for anchor in membership.capsule.exact_anchors
            if anchor.status.value == "active"
        )
        if snapshot.exact_anchor_ids != active_anchor_ids:
            raise RepositoryInvariantError(
                "committed Snapshot exact anchors disagree with Capsule membership"
            )

    @staticmethod
    def _is_exact_replay(
        existing: EventEnvelope,
        requested: EventEnvelope,
    ) -> bool:
        return (
            existing.event_id == requested.event_id
            and existing.session_key == requested.session_key
            and existing.event_type == requested.event_type
            and existing.role == requested.role
            and existing.content == requested.content
            and existing.source_hook == requested.source_hook
            and existing.idempotency_key == requested.idempotency_key
            and existing.token_count == requested.token_count
            and existing.created_at == requested.created_at
        )

    def _inject(self, boundary: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(boundary)
