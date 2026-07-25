"""Crash-safe SQLite repository transactions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from astrcontinuum.domain import (
    CompactionJobEnvelope,
    CompactionJobState,
    ContextCapsuleEnvelope,
    EventEnvelope,
    EventRole,
    EventType,
    SessionKey,
    SnapshotAuditOutcome,
    SnapshotEnvelope,
    SnapshotState,
    SourceHook,
)

from .sqlite import SQLiteConnectionFactory

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


def _normalize_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
