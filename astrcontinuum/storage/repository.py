"""Crash-safe SQLite repository transactions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from ..domain import (
    CompactionJobEnvelope,
    CompactionJobState,
    ContextCapsuleEnvelope,
    EventEnvelope,
    EventRole,
    EventType,
    PermanentValidationReport,
    SessionKey,
    SnapshotAuditOutcome,
    SnapshotEnvelope,
    SnapshotState,
    SourceHook,
    validate_permanent,
)
from .crypto import SecureCodec
from .sqlite import SQLiteConnectionFactory
from .token_metrics import (
    ArtifactKind,
    CanonicalMetricObservation,
    TokenMetric,
    TokenMetricStore,
)

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


class SQLiteRepository:
    """Execute the stable durable transactions over one SQLite database."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        codec: SecureCodec,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        if not isinstance(codec, SecureCodec):
            raise TypeError("codec must be a SecureCodec")
        self._factory = factory
        self._codec = codec
        self._metric_store = TokenMetricStore(codec)
        self._fault_injector = fault_injector

    @property
    def factory(self) -> SQLiteConnectionFactory:
        """Return the connection factory owned by this repository."""

        return self._factory

    @property
    def key_id(self) -> str:
        """Return the non-secret identifier of the repository codec."""

        return self._codec.key_id

    def _session_key_from_storage(
        self,
        session_key_hash: str,
        sentinel_json: str,
    ) -> SessionKey:
        canonical = self._codec.decrypt_object_json(
            "sessions",
            "canonical_session_key_json",
            session_key_hash,
            sentinel_json,
        )
        try:
            session_key = SessionKey.model_validate_json(canonical)
        except ValueError:
            raise RepositoryInvariantError("durable SessionKey cannot be reconstructed") from None
        if session_key.session_key_hash != session_key_hash:
            raise RepositoryInvariantError(
                "durable SessionKey hash does not match canonical identity"
            )
        return session_key

    def capture_user_event(
        self,
        *,
        event_id: str,
        session_key: SessionKey,
        content: str,
        idempotency_key: str,
        token_count: int,
        canonical: CanonicalMetricObservation,
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
            canonical=canonical,
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
        canonical: CanonicalMetricObservation,
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
            canonical=canonical,
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
        canonical: CanonicalMetricObservation,
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
            canonical=canonical,
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

    def read_compaction_view(
        self,
        *,
        job_id: str,
        owner: str,
        lease_epoch: int,
        now: datetime,
    ) -> RequestView:
        """Read the exact base and Delta frozen by one live compaction lease."""

        now_text = _normalize_datetime(now)
        with self._factory.connection(read_only=True) as connection:
            connection.execute("BEGIN")
            try:
                row = self._require_live_fence(
                    connection,
                    job_id=job_id,
                    owner=owner,
                    lease_epoch=lease_epoch,
                    now=now_text,
                )
                state = CompactionJobState(row["state"])
                if state not in {
                    CompactionJobState.COMPILING,
                    CompactionJobState.AUDITING,
                }:
                    raise JobTransitionError(
                        "compaction input requires a COMPILING or AUDITING Job"
                    )
                job = self._job_by_id(connection, job_id)
                if job.base_snapshot_id is None:
                    snapshot = None
                    memberships: tuple[SnapshotCapsuleMembership, ...] = ()
                    covered_event_end = 0
                else:
                    snapshot, memberships = self._snapshot_bundle_by_id(
                        connection,
                        job.base_snapshot_id,
                        session_key=job.session_key,
                    )
                    covered_event_end = snapshot.covered_event_end
                delta = self._events_between(
                    connection,
                    session_key=job.session_key,
                    start_exclusive=covered_event_end,
                    end_inclusive=job.target_high_water_mark,
                )
                expected_sequences = tuple(
                    range(covered_event_end + 1, job.target_high_water_mark + 1)
                )
                if tuple(item.sequence for item in delta) != expected_sequences:
                    raise RepositoryInvariantError(
                        "frozen compaction input is not a contiguous complete Delta"
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
            stored_session = self._session_key_from_storage(
                session_key.session_key_hash,
                str(session_row["canonical_session_key_json"]),
            )
            if stored_session != session_key:
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
            except sqlite3.IntegrityError:
                raise RepositoryConflict(
                    "compaction Job identity conflicts with durable state"
                ) from None
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
        token_ceiling: int,
        now: datetime,
    ) -> PublishResult:
        """Run ``TX_PUBLISH_SNAPSHOT`` with savepoint-isolated CAS conflict."""

        if token_ceiling < 0:
            raise ValueError("token_ceiling must be non-negative")
        now_text = _normalize_datetime(now)
        membership_tuple = tuple(memberships)

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

            if job.base_snapshot_id is None:
                previous_snapshot = None
                previous_memberships: tuple[SnapshotCapsuleMembership, ...] = ()
            else:
                previous_snapshot, previous_memberships = self._snapshot_bundle_by_id(
                    connection,
                    job.base_snapshot_id,
                    session_key=job.session_key,
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
        stored_session = self._session_key_from_storage(
            session_key.session_key_hash,
            str(session_row["canonical_session_key_json"]),
        )
        if stored_session != session_key:
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
            SELECT *
            FROM journal_events
            WHERE session_key_hash = ?
              AND sequence > ?
              AND sequence <= ?
            ORDER BY sequence
            """,
            (
                session_key.session_key_hash,
                covered_event_end,
                high_water_mark,
            ),
        ).fetchall()
        delta = tuple(self._event_from_row(row, session_key=session_key) for row in rows)
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
        canonical: CanonicalMetricObservation,
        created_at: datetime,
    ) -> EventEnvelope:
        if not isinstance(canonical, CanonicalMetricObservation):
            raise TypeError("canonical must be a CanonicalMetricObservation")
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
                session_key=session_key,
                source_hook=requested.source_hook,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if not self._is_exact_replay(existing, requested):
                    raise IdempotencyConflict(
                        "event idempotency tuple was reused with different immutable data"
                    )
                self._record_canonical_observation(
                    connection,
                    session_key_hash=session_key.session_key_hash,
                    artifact_id=existing.event_id,
                    canonical=canonical,
                    normalized_created_at=normalized_created_at,
                )
                self._inject("capture.after_metric")
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
                        token_count_envelope,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        session_key.session_key_hash,
                        event.sequence,
                        event.event_type.value,
                        event.role.value,
                        self._codec.encrypt_text(
                            "journal_events",
                            "content",
                            event.event_id,
                            event.content,
                        ),
                        event.source_hook.value,
                        event.idempotency_key,
                        self._codec.encrypt_non_negative_int(
                            "journal_events",
                            "token_count",
                            event.event_id,
                            event.token_count,
                        ),
                        normalized_created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                raise EventIdentityConflict(
                    "event identity or sequence conflicts with durable state"
                ) from None
            self._inject("capture.after_insert")
            self._record_canonical_observation(
                connection,
                session_key_hash=session_key.session_key_hash,
                artifact_id=event.event_id,
                canonical=canonical,
                normalized_created_at=normalized_created_at,
            )
            self._inject("capture.after_metric")
            return event

    def _record_canonical_observation(
        self,
        connection: sqlite3.Connection,
        *,
        session_key_hash: str,
        artifact_id: str,
        canonical: CanonicalMetricObservation,
        normalized_created_at: str,
    ) -> None:
        if canonical.token_count is not None:
            self._metric_store.put_in_transaction(
                connection,
                TokenMetric(
                    artifact_kind=ArtifactKind.EVENT,
                    artifact_id=artifact_id,
                    tokenizer_profile_id=canonical.tokenizer_profile_id,
                    token_count=canonical.token_count,
                ),
                created_at=_parse_datetime(normalized_created_at),
            )
            connection.execute(
                """
                DELETE FROM token_metric_backfill_intents
                WHERE artifact_kind = ?
                  AND artifact_id = ?
                  AND tokenizer_profile_id = ?
                """,
                (
                    ArtifactKind.EVENT.value,
                    artifact_id,
                    canonical.tokenizer_profile_id,
                ),
            )
            return

        existing = self._metric_store.get_in_transaction(
            connection,
            artifact_kind=ArtifactKind.EVENT,
            artifact_id=artifact_id,
            tokenizer_profile_id=canonical.tokenizer_profile_id,
        )
        if existing is not None:
            return
        connection.execute(
            """
            INSERT INTO token_metric_backfill_intents (
                session_key_hash,
                artifact_kind,
                artifact_id,
                tokenizer_profile_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (artifact_kind, artifact_id, tokenizer_profile_id)
            DO NOTHING
            """,
            (
                session_key_hash,
                ArtifactKind.EVENT.value,
                artifact_id,
                canonical.tokenizer_profile_id,
                normalized_created_at,
            ),
        )

    def _ensure_session(
        self,
        connection: sqlite3.Connection,
        *,
        session_key: SessionKey,
        timestamp: str,
    ) -> None:
        record_key = session_key.session_key_hash
        existing = connection.execute(
            """
            SELECT canonical_session_key_json
            FROM sessions
            WHERE session_key_hash = ?
            """,
            (record_key,),
        ).fetchone()
        if existing is not None:
            stored_session = self._session_key_from_storage(
                record_key,
                str(existing["canonical_session_key_json"]),
            )
            if stored_session != session_key:
                raise SessionIdentityConflict(
                    "session hash does not resolve to the exact canonical SessionKey"
                )
            return

        def encrypt_identity(column: str, value: str | None) -> str | None:
            if value is None:
                return None
            return self._codec.encrypt_text(
                "sessions",
                column,
                record_key,
                value,
            )

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
                    record_key,
                    self._codec.encrypt_object_json(
                        "sessions",
                        "canonical_session_key_json",
                        record_key,
                        session_key.canonical_json(),
                    ),
                    encrypt_identity(
                        "platform_instance_id",
                        session_key.platform_instance_id,
                    ),
                    encrypt_identity("message_type", session_key.message_type),
                    encrypt_identity("session_id", session_key.session_id),
                    encrypt_identity("group_id", session_key.group_id),
                    encrypt_identity("user_id", session_key.user_id),
                    encrypt_identity(
                        "conversation_id",
                        session_key.conversation_id,
                    ),
                    encrypt_identity("persona_id", session_key.persona_id),
                    timestamp,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError:
            raise SessionIdentityConflict(
                "canonical SessionKey conflicts with durable session identity"
            ) from None

        row = connection.execute(
            """
            SELECT canonical_session_key_json
            FROM sessions
            WHERE session_key_hash = ?
            """,
            (record_key,),
        ).fetchone()
        if row is None:
            raise SessionIdentityConflict(
                "session hash does not resolve to the exact canonical SessionKey"
            )
        stored_session = self._session_key_from_storage(
            record_key,
            str(row["canonical_session_key_json"]),
        )
        if stored_session != session_key:
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

    def _job_by_id(
        self,
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
        return self._job_from_row(row)

    def _job_from_row(self, row: sqlite3.Row) -> CompactionJobEnvelope:
        session_key = self._session_key_from_storage(
            str(row["session_key_hash"]),
            str(row["canonical_session_key_json"]),
        )
        try:
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
        except (TypeError, ValueError):
            raise RepositoryInvariantError(
                "compaction Job row does not form a canonical envelope"
            ) from None
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

    def _snapshot_bundle_by_id(
        self,
        connection: sqlite3.Connection,
        snapshot_id: str,
        *,
        session_key: SessionKey,
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
        stored_session_key = self._session_key_from_storage(
            str(row["session_key_hash"]),
            str(row["canonical_session_key_json"]),
        )
        if stored_session_key != session_key:
            raise RepositoryInvariantError("Snapshot belongs to a different durable session")
        memberships = self._memberships_for_snapshot(
            connection,
            snapshot_id=snapshot_id,
            session_key=session_key,
        )
        snapshot = self._snapshot_from_row(
            row,
            session_key=session_key,
            capsule_ids=tuple(item.capsule_id for item in memberships),
        )
        self._validate_snapshot_memberships(snapshot, memberships)
        return snapshot, memberships

    def _active_snapshot_bundle(
        self,
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
        snapshot, memberships = self._snapshot_bundle_by_id(
            connection,
            str(row["snapshot_id"]),
            session_key=session_key,
        )
        if snapshot.session_key != session_key:
            raise RepositoryInvariantError("publish winner belongs to another durable session")
        return snapshot, int(row["pointer_version"]), memberships

    def _events_between(
        self,
        connection: sqlite3.Connection,
        *,
        session_key: SessionKey,
        start_exclusive: int,
        end_inclusive: int,
    ) -> tuple[EventEnvelope, ...]:
        rows = connection.execute(
            """
            SELECT event.*
            FROM journal_events AS event
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
        return tuple(self._event_from_row(row, session_key=session_key) for row in rows)

    def _insert_or_verify_capsule(
        self,
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
            stored_canonical_json = self._codec.decrypt_object_json(
                "capsules",
                "canonical_capsule_json",
                capsule.capsule_id,
                str(row["canonical_capsule_json"]),
            )
            stored_token_cost = self._codec.decrypt_non_negative_int(
                "capsules",
                "token_cost",
                capsule.capsule_id,
                row["token_cost_envelope"],
            )
            if (
                row["session_key_hash"] != capsule.session_key.session_key_hash
                or row["level"] != capsule.level.value
                or int(row["covered_event_start"]) != capsule.covered_event_start
                or int(row["covered_event_end"]) != capsule.covered_event_end
                or stored_canonical_json != canonical_json
                or stored_token_cost != capsule.token_cost
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
                token_cost_envelope,
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
                self._codec.encrypt_object_json(
                    "capsules",
                    "canonical_capsule_json",
                    capsule.capsule_id,
                    canonical_json,
                ),
                self._codec.encrypt_non_negative_int(
                    "capsules",
                    "token_cost",
                    capsule.capsule_id,
                    capsule.token_cost,
                ),
                capsule.quality.source_coverage,
                _normalize_datetime(capsule.created_at),
            ),
        )

    def _insert_committed_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: SnapshotEnvelope,
    ) -> None:
        if snapshot.committed_at is None:
            raise RepositoryInvariantError("committed Snapshot insert requires committed_at")
        exact_anchor_ids_json = json.dumps(
            list(snapshot.exact_anchor_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        audit_outcome_json = _canonical_model_json(snapshot.audit_outcome)
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
                token_cost_envelope,
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
                self._codec.encrypt_array_json(
                    "snapshots",
                    "exact_anchor_ids_json",
                    snapshot.snapshot_id,
                    exact_anchor_ids_json,
                ),
                self._codec.encrypt_text(
                    "snapshots",
                    "rendered_context",
                    snapshot.snapshot_id,
                    snapshot.rendered_context,
                ),
                self._codec.encrypt_non_negative_int(
                    "snapshots",
                    "token_cost",
                    snapshot.snapshot_id,
                    snapshot.token_cost,
                ),
                self._codec.encrypt_object_json(
                    "snapshots",
                    "audit_outcome",
                    snapshot.snapshot_id,
                    audit_outcome_json,
                ),
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

    def _finish_publish_job(
        self,
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
        return self._job_by_id(connection, job.job_id)

    def _preserve_follow_up(
        self,
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

    def _find_idempotent_event(
        self,
        connection: sqlite3.Connection,
        *,
        session_key: SessionKey,
        source_hook: SourceHook,
        idempotency_key: str,
    ) -> EventEnvelope | None:
        row = connection.execute(
            """
            SELECT event.*
            FROM journal_events AS event
            WHERE event.session_key_hash = ?
              AND event.source_hook = ?
              AND event.idempotency_key = ?
            """,
            (
                session_key.session_key_hash,
                source_hook.value,
                idempotency_key,
            ),
        ).fetchone()
        return self._event_from_row(row, session_key=session_key) if row is not None else None

    def _event_from_row(
        self,
        row: sqlite3.Row,
        *,
        session_key: SessionKey,
    ) -> EventEnvelope:
        if row["session_key_hash"] != session_key.session_key_hash:
            raise RepositoryInvariantError("Journal event belongs to a different durable session")
        event_id = str(row["event_id"])
        content = self._codec.decrypt_text(
            "journal_events",
            "content",
            event_id,
            str(row["content"]),
        )
        token_count = self._codec.decrypt_non_negative_int(
            "journal_events",
            "token_count",
            event_id,
            row["token_count_envelope"],
        )
        try:
            return EventEnvelope(
                event_id=event_id,
                session_key=session_key,
                sequence=row["sequence"],
                event_type=EventType(row["event_type"]),
                role=EventRole(row["role"]),
                content=content,
                source_hook=SourceHook(row["source_hook"]),
                idempotency_key=row["idempotency_key"],
                token_count=token_count,
                created_at=_parse_datetime(row["created_at"]),
            )
        except (TypeError, ValueError):
            raise RepositoryInvariantError(
                "Journal event row does not form a canonical envelope"
            ) from None

    def _memberships_for_snapshot(
        self,
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
            capsule_id = str(row["capsule_id"])
            canonical_capsule_json = self._codec.decrypt_object_json(
                "capsules",
                "canonical_capsule_json",
                capsule_id,
                str(row["canonical_capsule_json"]),
            )
            try:
                capsule = ContextCapsuleEnvelope.model_validate_json(canonical_capsule_json)
            except (TypeError, ValueError):
                raise RepositoryInvariantError("durable Capsule cannot be reconstructed") from None
            stored_token_cost = self._codec.decrypt_non_negative_int(
                "capsules",
                "token_cost",
                capsule_id,
                row["token_cost_envelope"],
            )
            if (
                int(row["ordinal"]) != expected_ordinal
                or capsule_id != capsule.capsule_id
                or row["session_key_hash"] != session_key.session_key_hash
                or capsule.session_key != session_key
                or row["level"] != capsule.level.value
                or int(row["covered_event_start"]) != capsule.covered_event_start
                or int(row["covered_event_end"]) != capsule.covered_event_end
                or stored_token_cost != capsule.token_cost
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

    def _snapshot_from_row(
        self,
        row: sqlite3.Row,
        *,
        session_key: SessionKey,
        capsule_ids: tuple[str, ...],
    ) -> SnapshotEnvelope:
        snapshot_id = str(row["snapshot_id"])
        exact_anchor_ids_json = self._codec.decrypt_array_json(
            "snapshots",
            "exact_anchor_ids_json",
            snapshot_id,
            str(row["exact_anchor_ids_json"]),
        )
        rendered_context = self._codec.decrypt_text(
            "snapshots",
            "rendered_context",
            snapshot_id,
            str(row["rendered_context"]),
        )
        audit_outcome_json = self._codec.decrypt_object_json(
            "snapshots",
            "audit_outcome",
            snapshot_id,
            str(row["audit_outcome"]),
        )
        token_cost = self._codec.decrypt_non_negative_int(
            "snapshots",
            "token_cost",
            snapshot_id,
            row["token_cost_envelope"],
        )
        try:
            exact_anchor_ids = tuple(json.loads(exact_anchor_ids_json))
            audit_outcome = SnapshotAuditOutcome.model_validate_json(audit_outcome_json)
            snapshot = SnapshotEnvelope(
                snapshot_id=snapshot_id,
                session_key=session_key,
                base_snapshot_id=row["base_snapshot_id"],
                covered_event_end=row["covered_event_end"],
                source_high_water_mark=row["source_high_water_mark"],
                capsule_ids=capsule_ids,
                exact_anchor_ids=exact_anchor_ids,
                rendered_context=rendered_context,
                token_cost=token_cost,
                audit_outcome=audit_outcome,
                state=SnapshotState(row["lifecycle_state"]),
                created_at=_parse_datetime(row["created_at"]),
                committed_at=_parse_datetime(row["committed_at"]),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            raise RepositoryInvariantError(
                "committed Snapshot row does not form a canonical envelope"
            ) from None
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
