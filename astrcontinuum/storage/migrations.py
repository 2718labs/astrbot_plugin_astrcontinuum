"""Checksum-verified, transactional SQLite schema migrations."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from .sqlite import SQLiteConnectionFactory

_BUSY_RETRY_ATTEMPTS = 4
_BUSY_RETRY_BASE_DELAY_SECONDS = 0.01


class MigrationError(RuntimeError):
    """Base class for durable schema migration failures."""


class MigrationPlanError(MigrationError):
    """Raised when the packaged migration sequence is not safe to apply."""


class MigrationChecksumError(MigrationError):
    """Raised when the durable ledger disagrees with packaged migration content."""


class MigrationApplyError(MigrationError):
    """Raised when one migration cannot be applied atomically."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable, ordered SQL migration."""

    version: int
    name: str
    sql: str
    requires_codec: bool = False

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("migration version must be positive")
        if not self.name.strip():
            raise ValueError("migration name must be non-empty")
        if not self.sql.strip():
            raise ValueError("migration SQL must be non-empty")
        if not isinstance(self.requires_codec, bool):
            raise TypeError("requires_codec must be a bool")

    @property
    def checksum(self) -> str:
        """Return the lowercase SHA-256 digest of the exact packaged SQL."""

        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


INITIAL_SCHEMA_SQL = """
CREATE TABLE sessions (
    session_key_hash TEXT NOT NULL PRIMARY KEY
        CHECK (
            length(session_key_hash) = 64
            AND session_key_hash = lower(session_key_hash)
            AND session_key_hash NOT GLOB '*[^0-9a-f]*'
        ),
    canonical_session_key_json TEXT NOT NULL UNIQUE
        CHECK (
            json_valid(canonical_session_key_json)
            AND json_type(canonical_session_key_json) = 'object'
        ),
    platform_instance_id TEXT NOT NULL CHECK (length(trim(platform_instance_id)) > 0),
    message_type TEXT NOT NULL CHECK (length(trim(message_type)) > 0),
    session_id TEXT NOT NULL CHECK (length(trim(session_id)) > 0),
    group_id TEXT NULL CHECK (group_id IS NULL OR length(trim(group_id)) > 0),
    user_id TEXT NOT NULL CHECK (length(trim(user_id)) > 0),
    conversation_id TEXT NOT NULL CHECK (length(trim(conversation_id)) > 0),
    persona_id TEXT NULL CHECK (persona_id IS NULL OR length(trim(persona_id)) > 0),
    next_event_sequence INTEGER NOT NULL CHECK (next_event_sequence >= 1),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
    CHECK (
        coalesce(
            json_extract(canonical_session_key_json, '$.platform_instance_id')
                = platform_instance_id
            AND json_extract(canonical_session_key_json, '$.message_type') = message_type
            AND json_extract(canonical_session_key_json, '$.session_id') = session_id
            AND (
                (
                    group_id IS NULL
                    AND json_type(canonical_session_key_json, '$.group_id') = 'null'
                )
                OR json_extract(canonical_session_key_json, '$.group_id') = group_id
            )
            AND json_extract(canonical_session_key_json, '$.user_id') = user_id
            AND json_extract(canonical_session_key_json, '$.conversation_id')
                = conversation_id
            AND (
                (
                    persona_id IS NULL
                    AND json_type(canonical_session_key_json, '$.persona_id') = 'null'
                )
                OR json_extract(canonical_session_key_json, '$.persona_id') = persona_id
            ),
            0
        )
    )
);

CREATE TABLE journal_events (
    event_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(event_id)) > 0),
    session_key_hash TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    source_hook TEXT NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(trim(idempotency_key)) > 0),
    token_count INTEGER NOT NULL CHECK (token_count >= 0),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    UNIQUE (session_key_hash, sequence),
    UNIQUE (session_key_hash, source_hook, idempotency_key),
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash),
    CHECK (
        (event_type = 'USER_MESSAGE' AND role = 'USER' AND source_hook = 'ON_LLM_REQUEST')
        OR (
            event_type = 'ASSISTANT_MESSAGE'
            AND role = 'ASSISTANT'
            AND source_hook = 'ON_AGENT_DONE'
        )
        OR (
            event_type = 'TOOL_CALL'
            AND role = 'TOOL'
            AND source_hook = 'ON_USING_LLM_TOOL'
        )
        OR (
            event_type = 'TOOL_RESULT'
            AND role = 'TOOL'
            AND source_hook = 'ON_LLM_TOOL_RESPOND'
        )
    )
);

CREATE INDEX idx_journal_events_session_created
ON journal_events (session_key_hash, created_at);

CREATE TABLE capsules (
    capsule_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(capsule_id)) > 0),
    session_key_hash TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('micro', 'episode', 'task', 'global')),
    covered_event_start INTEGER NOT NULL CHECK (covered_event_start >= 1),
    covered_event_end INTEGER NOT NULL CHECK (covered_event_end >= covered_event_start),
    canonical_capsule_json TEXT NOT NULL
        CHECK (
            json_valid(canonical_capsule_json)
            AND json_type(canonical_capsule_json) = 'object'
        ),
    token_cost INTEGER NOT NULL CHECK (token_cost >= 0),
    source_coverage REAL NOT NULL CHECK (source_coverage BETWEEN 0 AND 1),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash)
);

CREATE INDEX idx_capsules_session_coverage
ON capsules (session_key_hash, covered_event_end, covered_event_start);

CREATE TABLE snapshots (
    snapshot_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(snapshot_id)) > 0),
    session_key_hash TEXT NOT NULL,
    base_snapshot_id TEXT NULL
        CHECK (base_snapshot_id IS NULL OR length(trim(base_snapshot_id)) > 0),
    covered_event_end INTEGER NOT NULL CHECK (covered_event_end >= 1),
    source_high_water_mark INTEGER NOT NULL
        CHECK (source_high_water_mark >= covered_event_end),
    exact_anchor_ids_json TEXT NOT NULL
        CHECK (
            json_valid(exact_anchor_ids_json)
            AND json_type(exact_anchor_ids_json) = 'array'
        ),
    rendered_context TEXT NOT NULL CHECK (length(rendered_context) > 0),
    token_cost INTEGER NOT NULL CHECK (token_cost >= 0),
    audit_outcome TEXT NOT NULL
        CHECK (
            json_valid(audit_outcome)
            AND json_type(audit_outcome) = 'object'
            AND json_extract(audit_outcome, '$.mechanical_passed') IS 1
            AND coalesce(
                json_extract(audit_outcome, '$.semantic_status')
                    IN ('NOT_RUN', 'PASSED'),
                0
            )
            AND coalesce(
                json_type(audit_outcome, '$.failure_codes') = 'array'
                    AND json_array_length(
                        json_extract(audit_outcome, '$.failure_codes')
                    ) = 0,
                0
            )
        ),
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state = 'COMMITTED'),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    committed_at TEXT NOT NULL CHECK (length(trim(committed_at)) > 0),
    UNIQUE (session_key_hash, covered_event_end),
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash),
    FOREIGN KEY (base_snapshot_id) REFERENCES snapshots(snapshot_id)
);

CREATE TABLE snapshot_capsules (
    snapshot_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    capsule_id TEXT NOT NULL,
    slot TEXT NOT NULL CHECK (length(trim(slot)) > 0),
    PRIMARY KEY (snapshot_id, ordinal),
    UNIQUE (snapshot_id, capsule_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (capsule_id) REFERENCES capsules(capsule_id)
);

CREATE TABLE active_snapshots (
    session_key_hash TEXT NOT NULL PRIMARY KEY,
    snapshot_id TEXT NOT NULL UNIQUE,
    pointer_version INTEGER NOT NULL CHECK (pointer_version >= 1),
    updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
);

CREATE TABLE compaction_jobs (
    job_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(job_id)) > 0),
    session_key_hash TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (
            state IN (
                'PENDING',
                'LEASED',
                'COMPILING',
                'AUDITING',
                'READY_TO_COMMIT',
                'RETRY_WAIT',
                'COMMITTED',
                'SUPERSEDED',
                'FAILED',
                'CANCELLED'
            )
        ),
    target_high_water_mark INTEGER NOT NULL CHECK (target_high_water_mark >= 1),
    intent_target_high_water_mark INTEGER NOT NULL
        CHECK (intent_target_high_water_mark >= target_high_water_mark),
    base_snapshot_id TEXT NULL
        CHECK (base_snapshot_id IS NULL OR length(trim(base_snapshot_id)) > 0),
    base_pointer_version INTEGER NOT NULL CHECK (base_pointer_version >= 0),
    candidate_snapshot_id TEXT NULL
        CHECK (
            candidate_snapshot_id IS NULL
            OR length(trim(candidate_snapshot_id)) > 0
        ),
    lease_owner TEXT NULL CHECK (lease_owner IS NULL OR length(trim(lease_owner)) > 0),
    lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 0),
    lease_expires_at TEXT NULL
        CHECK (lease_expires_at IS NULL OR length(trim(lease_expires_at)) > 0),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    next_retry_at TEXT NULL
        CHECK (next_retry_at IS NULL OR length(trim(next_retry_at)) > 0),
    error_stage TEXT NULL CHECK (error_stage IS NULL OR length(trim(error_stage)) > 0),
    error_code TEXT NULL CHECK (error_code IS NULL OR length(trim(error_code)) > 0),
    error_message TEXT NULL CHECK (error_message IS NULL OR length(trim(error_message)) > 0),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
    committed_at TEXT NULL CHECK (committed_at IS NULL OR length(trim(committed_at)) > 0),
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash),
    FOREIGN KEY (base_snapshot_id) REFERENCES snapshots(snapshot_id),
    CHECK (
        (base_snapshot_id IS NULL AND base_pointer_version = 0)
        OR (base_snapshot_id IS NOT NULL AND base_pointer_version >= 1)
    ),
    CHECK (
        (
            state IN ('LEASED', 'COMPILING', 'AUDITING', 'READY_TO_COMMIT')
            AND lease_owner IS NOT NULL
            AND lease_epoch >= 1
            AND lease_expires_at IS NOT NULL
        )
        OR (
            state NOT IN ('LEASED', 'COMPILING', 'AUDITING', 'READY_TO_COMMIT')
            AND lease_owner IS NULL
            AND lease_expires_at IS NULL
        )
    ),
    CHECK (
        (
            state IN ('READY_TO_COMMIT', 'COMMITTED', 'SUPERSEDED')
            AND candidate_snapshot_id IS NOT NULL
        )
        OR (
            state NOT IN ('READY_TO_COMMIT', 'COMMITTED', 'SUPERSEDED')
            AND candidate_snapshot_id IS NULL
        )
    ),
    CHECK (
        (
            error_stage IS NULL
            AND error_code IS NULL
            AND error_message IS NULL
        )
        OR (
            error_stage IS NOT NULL
            AND error_code IS NOT NULL
            AND error_message IS NOT NULL
        )
    ),
    CHECK (
        state NOT IN ('RETRY_WAIT', 'FAILED')
        OR (
            error_stage IS NOT NULL
            AND error_code IS NOT NULL
            AND error_message IS NOT NULL
        )
    ),
    CHECK (
        (state = 'RETRY_WAIT' AND next_retry_at IS NOT NULL)
        OR (state <> 'RETRY_WAIT' AND next_retry_at IS NULL)
    ),
    CHECK (
        (state = 'COMMITTED' AND committed_at IS NOT NULL)
        OR (state <> 'COMMITTED' AND committed_at IS NULL)
    )
);

CREATE UNIQUE INDEX uq_compaction_jobs_nonterminal_session
ON compaction_jobs (session_key_hash)
WHERE state IN (
    'PENDING',
    'LEASED',
    'COMPILING',
    'AUDITING',
    'READY_TO_COMMIT',
    'RETRY_WAIT'
);

CREATE INDEX idx_compaction_jobs_eligibility
ON compaction_jobs (state, next_retry_at, created_at);

CREATE INDEX idx_compaction_jobs_session_state
ON compaction_jobs (session_key_hash, state);

CREATE TRIGGER journal_events_immutable_update
BEFORE UPDATE ON journal_events
BEGIN
    SELECT RAISE(ABORT, 'journal_events rows are immutable');
END;

CREATE TRIGGER journal_events_immutable_delete
BEFORE DELETE ON journal_events
BEGIN
    SELECT RAISE(ABORT, 'journal_events rows are immutable');
END;

CREATE TRIGGER capsules_immutable_update
BEFORE UPDATE ON capsules
BEGIN
    SELECT RAISE(ABORT, 'capsules rows are immutable');
END;

CREATE TRIGGER capsules_immutable_delete
BEFORE DELETE ON capsules
BEGIN
    SELECT RAISE(ABORT, 'capsules rows are immutable');
END;

CREATE TRIGGER snapshots_immutable_update
BEFORE UPDATE ON snapshots
BEGIN
    SELECT RAISE(ABORT, 'snapshots rows are immutable');
END;

CREATE TRIGGER snapshots_immutable_delete
BEFORE DELETE ON snapshots
BEGIN
    SELECT RAISE(ABORT, 'snapshots rows are immutable');
END;

CREATE TRIGGER snapshot_capsules_immutable_update
BEFORE UPDATE ON snapshot_capsules
BEGIN
    SELECT RAISE(ABORT, 'snapshot_capsules rows are immutable');
END;

CREATE TRIGGER snapshot_capsules_immutable_delete
BEFORE DELETE ON snapshot_capsules
BEGIN
    SELECT RAISE(ABORT, 'snapshot_capsules rows are immutable');
END;

CREATE TRIGGER schema_migrations_immutable_update
BEFORE UPDATE ON schema_migrations
BEGIN
    SELECT RAISE(ABORT, 'schema_migrations rows are immutable');
END;

CREATE TRIGGER schema_migrations_immutable_delete
BEFORE DELETE ON schema_migrations
BEGIN
    SELECT RAISE(ABORT, 'schema_migrations rows are immutable');
END;
""".strip()

SNAPSHOT_REORGANIZATION_LEDGER_V3_SQL = """
CREATE TABLE snapshot_reorganization_records (
    snapshot_id TEXT NOT NULL CHECK (length(trim(snapshot_id)) > 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    source_capsule_id TEXT NOT NULL CHECK (length(trim(source_capsule_id)) > 0),
    kind TEXT NOT NULL CHECK (length(trim(kind)) > 0),
    item_id TEXT NOT NULL CHECK (length(trim(item_id)) > 0),
    status TEXT NOT NULL CHECK (status IN ('retained', 'approximate', 'released')),
    before_tokens INTEGER NOT NULL CHECK (before_tokens >= 0),
    after_tokens INTEGER NOT NULL CHECK (after_tokens >= 0),
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    PRIMARY KEY (snapshot_id, ordinal),
    UNIQUE (snapshot_id, source_capsule_id, kind, item_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
);

CREATE INDEX idx_snapshot_reorganization_records_snapshot_ordinal
ON snapshot_reorganization_records (snapshot_id, ordinal);

CREATE TRIGGER snapshot_reorganization_records_immutable_update
BEFORE UPDATE ON snapshot_reorganization_records
BEGIN
    SELECT RAISE(ABORT, 'snapshot_reorganization_records rows are immutable');
END;

CREATE TRIGGER snapshot_reorganization_records_immutable_delete
BEFORE DELETE ON snapshot_reorganization_records
BEGIN
    SELECT RAISE(ABORT, 'snapshot_reorganization_records rows are immutable');
END;
""".strip()

SECURE_FORMAT_V2_DDL = (
    (
        "create_journal_events",
        """
        CREATE TABLE _astrcontinuum_v2_journal_events (
            event_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(event_id)) > 0),
            session_key_hash TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            event_type TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            source_hook TEXT NOT NULL,
            idempotency_key TEXT NOT NULL CHECK (length(trim(idempotency_key)) > 0),
            token_count_envelope TEXT NOT NULL
                CHECK (substr(token_count_envelope, 1, 9) = 'acenc:v1:'),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            UNIQUE (session_key_hash, sequence),
            UNIQUE (session_key_hash, source_hook, idempotency_key),
            FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash),
            CHECK (
                (
                    event_type = 'USER_MESSAGE'
                    AND role = 'USER'
                    AND source_hook = 'ON_LLM_REQUEST'
                )
                OR (
                    event_type = 'ASSISTANT_MESSAGE'
                    AND role = 'ASSISTANT'
                    AND source_hook = 'ON_AGENT_DONE'
                )
                OR (
                    event_type = 'TOOL_CALL'
                    AND role = 'TOOL'
                    AND source_hook = 'ON_USING_LLM_TOOL'
                )
                OR (
                    event_type = 'TOOL_RESULT'
                    AND role = 'TOOL'
                    AND source_hook = 'ON_LLM_TOOL_RESPOND'
                )
            )
        )
        """.strip(),
    ),
    (
        "create_capsules",
        """
        CREATE TABLE _astrcontinuum_v2_capsules (
            capsule_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(capsule_id)) > 0),
            session_key_hash TEXT NOT NULL,
            level TEXT NOT NULL CHECK (level IN ('micro', 'episode', 'task', 'global')),
            covered_event_start INTEGER NOT NULL CHECK (covered_event_start >= 1),
            covered_event_end INTEGER NOT NULL
                CHECK (covered_event_end >= covered_event_start),
            canonical_capsule_json TEXT NOT NULL
                CHECK (
                    json_valid(canonical_capsule_json)
                    AND json_type(canonical_capsule_json) = 'object'
                ),
            token_cost_envelope TEXT NOT NULL
                CHECK (substr(token_cost_envelope, 1, 9) = 'acenc:v1:'),
            source_coverage REAL NOT NULL CHECK (source_coverage BETWEEN 0 AND 1),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash)
        )
        """.strip(),
    ),
    (
        "create_snapshots",
        """
        CREATE TABLE _astrcontinuum_v2_snapshots (
            snapshot_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(snapshot_id)) > 0),
            session_key_hash TEXT NOT NULL,
            base_snapshot_id TEXT NULL
                CHECK (base_snapshot_id IS NULL OR length(trim(base_snapshot_id)) > 0),
            covered_event_end INTEGER NOT NULL CHECK (covered_event_end >= 1),
            source_high_water_mark INTEGER NOT NULL
                CHECK (source_high_water_mark >= covered_event_end),
            exact_anchor_ids_json TEXT NOT NULL
                CHECK (
                    json_valid(exact_anchor_ids_json)
                    AND json_type(exact_anchor_ids_json) = 'array'
                    AND json_array_length(exact_anchor_ids_json) = 2
                    AND json_extract(exact_anchor_ids_json, '$[0]')
                        = '$astrcontinuum_encrypted'
                    AND json_type(exact_anchor_ids_json, '$[1]') = 'text'
                ),
            rendered_context TEXT NOT NULL
                CHECK (substr(rendered_context, 1, 9) = 'acenc:v1:'),
            token_cost_envelope TEXT NOT NULL
                CHECK (substr(token_cost_envelope, 1, 9) = 'acenc:v1:'),
            audit_outcome TEXT NOT NULL
                CHECK (
                    json_valid(audit_outcome)
                    AND json_type(audit_outcome) = 'object'
                    AND json_type(
                        audit_outcome,
                        '$."$astrcontinuum_encrypted"'
                    ) = 'text'
                    AND json_remove(
                        audit_outcome,
                        '$."$astrcontinuum_encrypted"'
                    ) = '{}'
                ),
            lifecycle_state TEXT NOT NULL CHECK (lifecycle_state = 'COMMITTED'),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            committed_at TEXT NOT NULL CHECK (length(trim(committed_at)) > 0),
            UNIQUE (session_key_hash, covered_event_end),
            FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash),
            FOREIGN KEY (base_snapshot_id)
                REFERENCES _astrcontinuum_v2_snapshots(snapshot_id)
        )
        """.strip(),
    ),
    (
        "create_storage_security",
        """
        CREATE TABLE _astrcontinuum_v2_storage_security (
            singleton_id INTEGER NOT NULL PRIMARY KEY CHECK (singleton_id = 1),
            format_version INTEGER NOT NULL CHECK (format_version = 2),
            active_key_id TEXT NOT NULL
                CHECK (
                    length(active_key_id) = 16
                    AND active_key_id = lower(active_key_id)
                    AND active_key_id NOT GLOB '*[^0-9a-f]*'
                ),
            state TEXT NOT NULL
                CHECK (
                    state IN ('NEEDS_MIGRATION', 'NEEDS_REKEY', 'NEEDS_SCRUB', 'ACTIVE')
                ),
            key_verifier TEXT NOT NULL
                CHECK (substr(key_verifier, 1, 9) = 'acenc:v1:'),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
        )
        """.strip(),
    ),
    ("drop_journal_events", "DROP TABLE journal_events"),
    ("drop_capsules", "DROP TABLE capsules"),
    ("drop_snapshots", "DROP TABLE snapshots"),
    ("drop_storage_security", "DROP TABLE storage_security"),
    (
        "rename_journal_events",
        "ALTER TABLE _astrcontinuum_v2_journal_events RENAME TO journal_events",
    ),
    (
        "rename_capsules",
        "ALTER TABLE _astrcontinuum_v2_capsules RENAME TO capsules",
    ),
    (
        "rename_snapshots",
        "ALTER TABLE _astrcontinuum_v2_snapshots RENAME TO snapshots",
    ),
    (
        "rename_storage_security",
        "ALTER TABLE _astrcontinuum_v2_storage_security RENAME TO storage_security",
    ),
    (
        "index_journal_events",
        """
        CREATE INDEX idx_journal_events_session_created
        ON journal_events (session_key_hash, created_at)
        """.strip(),
    ),
    (
        "index_capsules",
        """
        CREATE INDEX idx_capsules_session_coverage
        ON capsules (session_key_hash, covered_event_end, covered_event_start)
        """.strip(),
    ),
    (
        "journal_events_immutable_update",
        """
        CREATE TRIGGER journal_events_immutable_update
        BEFORE UPDATE ON journal_events
        BEGIN
            SELECT RAISE(ABORT, 'journal_events rows are immutable');
        END
        """.strip(),
    ),
    (
        "journal_events_immutable_delete",
        """
        CREATE TRIGGER journal_events_immutable_delete
        BEFORE DELETE ON journal_events
        BEGIN
            SELECT RAISE(ABORT, 'journal_events rows are immutable');
        END
        """.strip(),
    ),
    (
        "capsules_immutable_update",
        """
        CREATE TRIGGER capsules_immutable_update
        BEFORE UPDATE ON capsules
        BEGIN
            SELECT RAISE(ABORT, 'capsules rows are immutable');
        END
        """.strip(),
    ),
    (
        "capsules_immutable_delete",
        """
        CREATE TRIGGER capsules_immutable_delete
        BEFORE DELETE ON capsules
        BEGIN
            SELECT RAISE(ABORT, 'capsules rows are immutable');
        END
        """.strip(),
    ),
    (
        "snapshots_immutable_update",
        """
        CREATE TRIGGER snapshots_immutable_update
        BEFORE UPDATE ON snapshots
        BEGIN
            SELECT RAISE(ABORT, 'snapshots rows are immutable');
        END
        """.strip(),
    ),
    (
        "snapshots_immutable_delete",
        """
        CREATE TRIGGER snapshots_immutable_delete
        BEFORE DELETE ON snapshots
        BEGIN
            SELECT RAISE(ABORT, 'snapshots rows are immutable');
        END
        """.strip(),
    ),
    (
        "journal_events_secure_insert",
        """
        CREATE TRIGGER journal_events_secure_insert
        BEFORE INSERT ON journal_events
        WHEN substr(NEW.content, 1, 9) <> 'acenc:v1:'
        BEGIN
            SELECT RAISE(ABORT, 'journal_events content must be encrypted');
        END
        """.strip(),
    ),
    (
        "capsules_secure_insert",
        """
        CREATE TRIGGER capsules_secure_insert
        BEFORE INSERT ON capsules
        WHEN coalesce(
            json_valid(NEW.canonical_capsule_json)
            AND json_type(NEW.canonical_capsule_json) = 'object'
            AND json_type(
                NEW.canonical_capsule_json,
                '$."$astrcontinuum_encrypted"'
            ) = 'text'
            AND json_remove(
                NEW.canonical_capsule_json,
                '$."$astrcontinuum_encrypted"'
            ) = '{}'
        , 0) = 0
        BEGIN
            SELECT RAISE(ABORT, 'capsules canonical JSON must be encrypted');
        END
        """.strip(),
    ),
    (
        "token_metrics",
        """
        CREATE TABLE token_metrics (
            artifact_kind TEXT NOT NULL
                CHECK (artifact_kind IN ('EVENT', 'CAPSULE', 'SNAPSHOT')),
            artifact_id TEXT NOT NULL CHECK (length(trim(artifact_id)) > 0),
            tokenizer_profile_id TEXT NOT NULL
                CHECK (length(trim(tokenizer_profile_id)) > 0),
            metric_envelope TEXT NOT NULL
                CHECK (substr(metric_envelope, 1, 9) = 'acenc:v1:'),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            PRIMARY KEY (artifact_kind, artifact_id, tokenizer_profile_id)
        )
        """.strip(),
    ),
    (
        "token_metric_backfill_intents",
        """
        CREATE TABLE token_metric_backfill_intents (
            session_key_hash TEXT NOT NULL,
            artifact_kind TEXT NOT NULL
                CHECK (artifact_kind IN ('EVENT', 'CAPSULE', 'SNAPSHOT')),
            artifact_id TEXT NOT NULL CHECK (length(trim(artifact_id)) > 0),
            tokenizer_profile_id TEXT NOT NULL
                CHECK (length(trim(tokenizer_profile_id)) > 0),
            created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
            PRIMARY KEY (artifact_kind, artifact_id, tokenizer_profile_id),
            FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash)
        )
        """.strip(),
    ),
    (
        "idx_token_metric_backfill_session",
        """
        CREATE INDEX idx_token_metric_backfill_session
        ON token_metric_backfill_intents
            (session_key_hash, created_at, artifact_kind, artifact_id)
        """.strip(),
    ),
)

SECURE_FORMAT_V2_CONTRACT_SQL = "\n\n".join(
    f"-- {name}\n{statement.rstrip(';')};" for name, statement in SECURE_FORMAT_V2_DDL
)

MIGRATIONS = (
    Migration(version=1, name="initial_schema", sql=INITIAL_SCHEMA_SQL),
    Migration(
        version=2,
        name="encrypted_token_metrics",
        sql=SECURE_FORMAT_V2_CONTRACT_SQL,
        requires_codec=True,
    ),
    Migration(
        version=3,
        name="snapshot_reorganization_ledger",
        sql=SNAPSHOT_REORGANIZATION_LEDGER_V3_SQL,
    ),
)

_MIGRATION_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version >= 1),
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    checksum TEXT NOT NULL
        CHECK (
            length(checksum) = 64
            AND checksum = lower(checksum)
            AND checksum NOT GLOB '*[^0-9a-f]*'
        ),
    applied_at TEXT NOT NULL CHECK (length(trim(applied_at)) > 0)
)
"""


def _iter_sql_statements(script: str) -> Iterator[str]:
    buffer: list[str] = []
    for character in script:
        buffer.append(character)
        if character != ";":
            continue
        candidate = "".join(buffer)
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            if statement:
                yield statement
            buffer.clear()

    remainder = "".join(buffer).strip()
    if remainder and not _is_comment_only(remainder):
        raise MigrationPlanError("migration SQL contains an incomplete statement")


def _is_comment_only(sql: str) -> bool:
    position = 0
    while position < len(sql):
        if sql[position].isspace():
            position += 1
            continue
        if sql.startswith("--", position):
            newline = sql.find("\n", position + 2)
            if newline == -1:
                return True
            position = newline + 1
            continue
        if sql.startswith("/*", position):
            comment_end = sql.find("*/", position + 2)
            if comment_end == -1:
                return False
            position = comment_end + 2
            continue
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SQLiteMigrator:
    """Apply a contiguous migration plan under one immediate transaction."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        migrations: Sequence[Migration] = MIGRATIONS,
    ) -> None:
        self._factory = factory
        self._migrations = tuple(migrations)
        self._validate_plan()

    @property
    def migrations(self) -> tuple[Migration, ...]:
        """Return the immutable migration plan."""

        return self._migrations

    def migrate(self, *, include_keyed: bool = False) -> int:
        """Apply all pending migrations and return the durable schema version."""

        for attempt in range(_BUSY_RETRY_ATTEMPTS):
            try:
                return self._migrate_once(include_keyed=include_keyed)
            except MigrationError:
                raise
            except sqlite3.OperationalError as error:
                if _is_busy_error(error) and attempt + 1 < _BUSY_RETRY_ATTEMPTS:
                    time.sleep(_BUSY_RETRY_BASE_DELAY_SECONDS * (attempt + 1))
                    continue
                raise MigrationApplyError("SQLite migration initialization failed") from error
            except sqlite3.Error as error:
                raise MigrationApplyError("SQLite migration initialization failed") from error

        raise AssertionError("unreachable migration retry state")

    def _migrate_once(self, *, include_keyed: bool) -> int:
        with self._factory.transaction(immediate=True) as connection:
            self._check_sqlite_capabilities(connection)
            connection.execute(_MIGRATION_LEDGER_SQL)
            applied_count = self._validate_ledger(connection)

            for migration in self._migrations[applied_count:]:
                if migration.requires_codec:
                    if include_keyed:
                        raise MigrationPlanError(
                            "keyed migrations require the storage-security runner"
                        )
                    break
                self._apply_one(connection, migration)
                applied_count += 1

            return self._migrations[applied_count - 1].version if applied_count else 0

    @staticmethod
    def _check_sqlite_capabilities(connection: sqlite3.Connection) -> None:
        try:
            capability_row = connection.execute(
                """
                SELECT
                    json_valid('{}'),
                    json_type('{}'),
                    json_extract('{"value": 1}', '$.value'),
                    json_array_length('[]')
                """
            ).fetchone()
        except sqlite3.Error as error:
            raise MigrationApplyError("required SQLite JSON functions are unavailable") from error

        if capability_row is None or tuple(capability_row) != (1, "object", 1, 0):
            raise MigrationApplyError(
                "required SQLite JSON functions returned incompatible results"
            )

    def _validate_plan(self) -> None:
        versions = [migration.version for migration in self._migrations]
        expected = list(range(1, len(self._migrations) + 1))
        if versions != expected:
            raise MigrationPlanError("migration versions must be contiguous and start at one")
        names = [migration.name for migration in self._migrations]
        if len(names) != len(set(names)):
            raise MigrationPlanError("migration names must be unique")

    def _validate_ledger(self, connection: sqlite3.Connection) -> int:
        rows = connection.execute(
            """
            SELECT version, name, checksum
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
        if len(rows) > len(self._migrations):
            raise MigrationChecksumError("database contains migrations unknown to this package")

        for index, row in enumerate(rows):
            expected = self._migrations[index]
            if row["version"] != expected.version:
                raise MigrationChecksumError(
                    "applied migration ledger is not a contiguous plan prefix"
                )
            legacy_v1_alias = (
                expected.version == 1
                and row["name"] == "initial_v1_schema"
                and row["checksum"] == expected.checksum
            )
            if row["name"] != expected.name and not legacy_v1_alias:
                raise MigrationChecksumError(
                    f"migration {expected.version} name does not match the ledger"
                )
            if row["checksum"] != expected.checksum:
                raise MigrationChecksumError(
                    f"migration {expected.version} checksum does not match the ledger"
                )

        durable_version = rows[-1]["version"] if rows else 0
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if user_version != durable_version:
            raise MigrationChecksumError("SQLite user_version does not match the migration ledger")
        return len(rows)

    def _apply_one(
        self,
        connection: sqlite3.Connection,
        migration: Migration,
    ) -> None:
        try:
            for statement in _iter_sql_statements(migration.sql):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    _utc_now(),
                ),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
        except MigrationError:
            raise
        except sqlite3.Error as error:
            raise MigrationApplyError(
                f"migration {migration.version} ({migration.name}) failed"
            ) from error


def _is_busy_error(error: sqlite3.OperationalError) -> bool:
    message = str(error).lower()
    return "locked" in message or "busy" in message
