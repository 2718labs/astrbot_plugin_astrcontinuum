from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import astrcontinuum as ac

SESSION_HASH = "a" * 64
NOW = "2026-07-26T00:00:00Z"
SESSION_KEY_JSON = json.dumps(
    {
        "platform_instance_id": "astrbot-1",
        "message_type": "group",
        "session_id": "session-1",
        "group_id": "group-1",
        "user_id": "user-1",
        "conversation_id": "conversation-1",
        "persona_id": None,
    },
    separators=(",", ":"),
)


def _canonical_capsule_json() -> str:
    return ac.ContextCapsuleEnvelope(
        capsule_id="capsule-1",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=ac.SessionKey(**json.loads(SESSION_KEY_JSON)),
        covered_event_start=1,
        covered_event_end=1,
        source_event_ids=("event-1",),
        goals=(),
        constraints=(),
        decisions=(),
        progress=(),
        open_loops=(),
        preferences=(),
        entities=(),
        emotional_context=(),
        exact_anchors=(),
        dependencies=(),
        narrative_summary="context",
        token_cost=4,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    ).model_dump_json()


def _migration_api() -> tuple[Any, Any, Any, Any]:
    required = (
        "MIGRATIONS",
        "Migration",
        "MigrationChecksumError",
        "SQLiteMigrator",
    )
    missing = [name for name in required if not hasattr(ac, name)]
    assert not missing, f"V1-202 requires top-level migration exports: {missing}"
    return (
        ac.MIGRATIONS,
        ac.Migration,
        ac.MigrationChecksumError,
        ac.SQLiteMigrator,
    )


def _migrated_factory(
    tmp_path: Path,
    *,
    busy_timeout_ms: int = 100,
) -> Any:
    migrations, _, _, migrator_type = _migration_api()
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=busy_timeout_ms)
    assert migrator_type(factory).migrate() == migrations[-1].version
    return factory


def _insert_session(
    connection: sqlite3.Connection,
    *,
    session_hash: str = SESSION_HASH,
) -> None:
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_hash,
            SESSION_KEY_JSON,
            "astrbot-1",
            "group",
            "session-1",
            "group-1",
            "user-1",
            "conversation-1",
            None,
            1,
            NOW,
            NOW,
        ),
    )


def _insert_event(connection: sqlite3.Connection) -> None:
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
            "event-1",
            SESSION_HASH,
            1,
            "USER_MESSAGE",
            "USER",
            "hello",
            "ON_LLM_REQUEST",
            "request-1",
            2,
            NOW,
        ),
    )


def _insert_capsule_snapshot_membership(connection: sqlite3.Connection) -> None:
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
            "capsule-1",
            SESSION_HASH,
            "micro",
            1,
            1,
            _canonical_capsule_json(),
            4,
            1.0,
            NOW,
        ),
    )
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "snapshot-1",
            SESSION_HASH,
            None,
            1,
            1,
            "[]",
            "context",
            4,
            '{"mechanical_passed":true,"semantic_status":"NOT_RUN","failure_codes":[]}',
            "COMMITTED",
            NOW,
            NOW,
        ),
    )
    connection.execute(
        """
        INSERT INTO snapshot_capsules (snapshot_id, ordinal, capsule_id, slot)
        VALUES (?, ?, ?, ?)
        """,
        ("snapshot-1", 0, "capsule-1", "primary"),
    )


def _insert_snapshot_reorganization_record(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str = "snapshot-1",
    ordinal: int = 0,
    source_capsule_id: str = "source-capsule-1",
    kind: str = "goal",
    item_id: str = "goal-1",
    status: str = "retained",
    before_tokens: int = 10,
    after_tokens: int = 10,
    required: int = 0,
) -> None:
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
            snapshot_id,
            ordinal,
            source_capsule_id,
            kind,
            item_id,
            status,
            before_tokens,
            after_tokens,
            required,
        ),
    )


def _insert_job(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    **overrides: object,
) -> None:
    values: dict[str, object] = {
        "job_id": job_id,
        "session_key_hash": SESSION_HASH,
        "state": "PENDING",
        "target_high_water_mark": 1,
        "intent_target_high_water_mark": 1,
        "base_snapshot_id": None,
        "base_pointer_version": 0,
        "candidate_snapshot_id": None,
        "lease_owner": None,
        "lease_epoch": 0,
        "lease_expires_at": None,
        "attempt_count": 0,
        "next_retry_at": None,
        "error_stage": None,
        "error_code": None,
        "error_message": None,
        "created_at": NOW,
        "updated_at": NOW,
        "committed_at": None,
    }
    values.update(overrides)
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
            :job_id,
            :session_key_hash,
            :state,
            :target_high_water_mark,
            :intent_target_high_water_mark,
            :base_snapshot_id,
            :base_pointer_version,
            :candidate_snapshot_id,
            :lease_owner,
            :lease_epoch,
            :lease_expires_at,
            :attempt_count,
            :next_retry_at,
            :error_stage,
            :error_code,
            :error_message,
            :created_at,
            :updated_at,
            :committed_at
        )
        """,
        values,
    )


def test_initial_migration_creates_complete_versioned_schema(tmp_path: Path) -> None:
    migrations, _, _, migrator_type = _migration_api()
    factory = ac.SQLiteConnectionFactory(tmp_path)
    migrator = migrator_type(factory)

    assert [migration.version for migration in migrations] == [1, 2]
    assert migrator.migrate() == 2
    assert migrator.migrate() == 2

    with factory.connection(read_only=True) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        ledger = connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        indexes = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                    AND tbl_name = 'snapshot_reorganization_records'
                """
            )
        }

    assert tables == {
        "active_snapshots",
        "capsules",
        "compaction_jobs",
        "journal_events",
        "schema_migrations",
        "sessions",
        "snapshot_capsules",
        "snapshot_reorganization_records",
        "snapshots",
    }
    assert [row["version"] for row in ledger] == [1, 2]
    assert [row["name"] for row in ledger] == [migration.name for migration in migrations]
    assert [row["checksum"] for row in ledger] == [migration.checksum for migration in migrations]
    assert migrations[0].checksum == hashlib.sha256(migrations[0].sql.encode("utf-8")).hexdigest()
    assert "idx_snapshot_reorganization_records_snapshot_ordinal" in indexes
    assert user_version == 2


def test_v1_database_upgrades_to_snapshot_reorganization_ledger_v2_idempotently(
    tmp_path: Path,
) -> None:
    migrations, _, _, migrator_type = _migration_api()
    assert [migration.version for migration in migrations] == [1, 2]
    factory = ac.SQLiteConnectionFactory(tmp_path)

    assert migrator_type(factory, migrations=migrations[:1]).migrate() == 1
    with factory.transaction() as connection:
        _insert_session(connection)
        _insert_event(connection)
        _insert_capsule_snapshot_membership(connection)

    migrator = migrator_type(factory)
    assert migrator.migrate() == 2
    assert migrator.migrate() == 2

    with factory.connection(read_only=True) as connection:
        versions = [
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        session = connection.execute(
            """
            SELECT session_key_hash, canonical_session_key_json, next_event_sequence
            FROM sessions
            """
        ).fetchone()
        event = connection.execute(
            """
            SELECT event_id, session_key_hash, sequence, content, source_hook
            FROM journal_events
            """
        ).fetchone()
        capsule = connection.execute(
            """
            SELECT capsule_id, session_key_hash, token_cost, source_coverage
            FROM capsules
            """
        ).fetchone()
        membership = connection.execute(
            """
            SELECT snapshot_id, ordinal, capsule_id, slot
            FROM snapshot_capsules
            """
        ).fetchone()
        snapshot_count = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        record_count = connection.execute(
            "SELECT COUNT(*) FROM snapshot_reorganization_records"
        ).fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert versions == [1, 2]
    assert tuple(session) == (SESSION_HASH, SESSION_KEY_JSON, 1)
    assert tuple(event) == ("event-1", SESSION_HASH, 1, "hello", "ON_LLM_REQUEST")
    assert tuple(capsule) == ("capsule-1", SESSION_HASH, 4, 1.0)
    assert tuple(membership) == ("snapshot-1", 0, "capsule-1", "primary")
    assert snapshot_count == 1
    assert record_count == 0
    assert user_version == 2


def test_text_primary_identity_columns_are_explicitly_not_null(tmp_path: Path) -> None:
    factory = _migrated_factory(tmp_path)
    identity_columns = {
        "active_snapshots": "session_key_hash",
        "capsules": "capsule_id",
        "compaction_jobs": "job_id",
        "journal_events": "event_id",
        "sessions": "session_key_hash",
        "snapshots": "snapshot_id",
    }

    with factory.connection(read_only=True) as connection:
        nullable_primary_keys = []
        for table, column in identity_columns.items():
            columns = {
                row["name"]: row for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if columns[column]["notnull"] != 1:
                nullable_primary_keys.append(f"{table}.{column}")

    assert nullable_primary_keys == []


def test_foreign_keys_identity_and_event_triples_are_enforced(tmp_path: Path) -> None:
    factory = _migrated_factory(tmp_path)

    with factory.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            _insert_session(connection, session_hash="not-a-sha256")
        _insert_session(connection)

        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                """
                INSERT INTO journal_events VALUES (
                    'orphan', ?, 1, 'USER_MESSAGE', 'USER', 'x',
                    'ON_LLM_REQUEST', 'orphan', 1, ?
                )
                """,
                ("b" * 64, NOW),
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(
                """
                INSERT INTO journal_events VALUES (
                    'bad-triple', ?, 1, 'USER_MESSAGE', 'ASSISTANT', 'x',
                    'ON_LLM_REQUEST', 'bad-triple', 1, ?
                )
                """,
                (SESSION_HASH, NOW),
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(
                """
                INSERT INTO journal_events VALUES (
                    'legacy-hook', ?, 1, 'ASSISTANT_MESSAGE', 'ASSISTANT', 'x',
                    'ON_LLM_RESPONSE', 'legacy-hook', 1, ?
                )
                """,
                (SESSION_HASH, NOW),
            )

        _insert_event(connection)


def test_snapshot_reorganization_record_schema_enforces_contract(tmp_path: Path) -> None:
    migrations, _, _, _ = _migration_api()
    assert [migration.version for migration in migrations] == [1, 2]
    factory = _migrated_factory(tmp_path)

    with factory.transaction() as connection:
        _insert_session(connection)
        _insert_event(connection)
        _insert_capsule_snapshot_membership(connection)
        _insert_snapshot_reorganization_record(connection)
        _insert_snapshot_reorganization_record(
            connection,
            ordinal=1,
            source_capsule_id="source-retained",
            kind="constraint",
            item_id="retained-boundary",
            before_tokens=0,
            after_tokens=0,
            required=1,
        )
        _insert_snapshot_reorganization_record(
            connection,
            ordinal=2,
            source_capsule_id="source-approximate",
            kind="constraint",
            item_id="approximate-boundary",
            status="approximate",
            before_tokens=0,
            after_tokens=0,
        )
        _insert_snapshot_reorganization_record(
            connection,
            ordinal=3,
            source_capsule_id="source-released",
            kind="constraint",
            item_id="released-boundary",
            status="released",
            after_tokens=0,
        )

        columns = (
            "snapshot_id",
            "ordinal",
            "source_capsule_id",
            "kind",
            "item_id",
            "status",
            "before_tokens",
            "after_tokens",
            "required",
        )
        values: list[object] = [
            "snapshot-1",
            4,
            "source-null",
            "constraint",
            "null-boundary",
            "retained",
            0,
            0,
            0,
        ]
        for index, column in enumerate(columns):
            null_values = list(values)
            null_values[index] = None
            with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
                connection.execute(
                    f"INSERT INTO snapshot_reorganization_records ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    tuple(null_values),
                )

        for overrides in (
            {"snapshot_id": " "},
            {"ordinal": -1},
            {"source_capsule_id": "\t"},
            {"kind": "\n"},
            {"item_id": " "},
            {"status": "unknown"},
            {"before_tokens": -1},
            {"after_tokens": -1},
            {"required": 2},
            {"required": -1},
            {"snapshot_id": "missing-snapshot", "ordinal": 1},
            {"ordinal": 0, "item_id": "different-item"},
            {"ordinal": 1},
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _insert_snapshot_reorganization_record(connection, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_pointer_version": 1},
        {"intent_target_high_water_mark": 0},
        {"state": "LEASED"},
        {
            "state": "PENDING",
            "lease_owner": "worker-1",
            "lease_epoch": 1,
            "lease_expires_at": NOW,
        },
        {
            "state": "READY_TO_COMMIT",
            "lease_owner": "worker-1",
            "lease_epoch": 1,
            "lease_expires_at": NOW,
        },
        {"state": "RETRY_WAIT", "next_retry_at": NOW},
        {
            "state": "PENDING",
            "error_stage": "compile",
            "error_code": None,
            "error_message": "redacted",
        },
        {
            "state": "COMMITTED",
            "candidate_snapshot_id": "candidate-1",
        },
    ],
)
def test_job_cross_field_constraints_reject_invalid_rows(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    factory = _migrated_factory(tmp_path)
    with factory.transaction() as connection:
        _insert_session(connection)

        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            _insert_job(connection, job_id="invalid-job", **overrides)


def test_nonterminal_job_chain_is_unique_per_session(tmp_path: Path) -> None:
    factory = _migrated_factory(tmp_path)
    with factory.transaction() as connection:
        _insert_session(connection)
        _insert_job(connection, job_id="pending-1")

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert_job(connection, job_id="pending-2")

        _insert_job(
            connection,
            job_id="committed-1",
            state="COMMITTED",
            candidate_snapshot_id="candidate-1",
            committed_at=NOW,
        )


@pytest.mark.parametrize(
    ("table", "update_sql", "delete_sql"),
    [
        (
            "journal_events",
            "UPDATE journal_events SET content = 'changed' WHERE event_id = 'event-1'",
            "DELETE FROM journal_events WHERE event_id = 'event-1'",
        ),
        (
            "capsules",
            "UPDATE capsules SET token_cost = 5 WHERE capsule_id = 'capsule-1'",
            "DELETE FROM capsules WHERE capsule_id = 'capsule-1'",
        ),
        (
            "snapshots",
            "UPDATE snapshots SET token_cost = 5 WHERE snapshot_id = 'snapshot-1'",
            "DELETE FROM snapshots WHERE snapshot_id = 'snapshot-1'",
        ),
        (
            "snapshot_capsules",
            """
            UPDATE snapshot_capsules
            SET slot = 'changed'
            WHERE snapshot_id = 'snapshot-1' AND ordinal = 0
            """,
            """
            DELETE FROM snapshot_capsules
            WHERE snapshot_id = 'snapshot-1' AND ordinal = 0
            """,
        ),
        (
            "snapshot_reorganization_records",
            """
            UPDATE snapshot_reorganization_records
            SET after_tokens = 5
            WHERE snapshot_id = 'snapshot-1' AND ordinal = 0
            """,
            """
            DELETE FROM snapshot_reorganization_records
            WHERE snapshot_id = 'snapshot-1' AND ordinal = 0
            """,
        ),
    ],
)
def test_append_only_and_snapshot_content_rows_are_immutable(
    tmp_path: Path,
    table: str,
    update_sql: str,
    delete_sql: str,
) -> None:
    factory = _migrated_factory(tmp_path)
    with factory.transaction() as connection:
        _insert_session(connection)
        _insert_event(connection)
        _insert_capsule_snapshot_membership(connection)
        _insert_snapshot_reorganization_record(connection)

    with (
        pytest.raises(
            sqlite3.IntegrityError,
            match=f"{table} rows are immutable",
        ),
        factory.transaction() as connection,
    ):
        connection.execute(update_sql)

    with (
        pytest.raises(
            sqlite3.IntegrityError,
            match=f"{table} rows are immutable",
        ),
        factory.transaction() as connection,
    ):
        connection.execute(delete_sql)


def test_concurrent_initializers_converge_on_one_ledger_row(tmp_path: Path) -> None:
    _, _, _, migrator_type = _migration_api()
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=1_000)

    def migrate() -> int:
        return migrator_type(factory).migrate()

    with ThreadPoolExecutor(max_workers=4) as executor:
        versions = list(executor.map(lambda _: migrate(), range(4)))

    with factory.connection(read_only=True) as connection:
        ledger_count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

    assert versions == [2, 2, 2, 2]
    assert ledger_count == 2


def test_migrator_retries_transient_busy_before_initialization(tmp_path: Path) -> None:
    _, _, _, migrator_type = _migration_api()
    real_factory = ac.SQLiteConnectionFactory(tmp_path)

    class TransientBusyFactory:
        attempts = 0

        @contextmanager
        def transaction(self, *, immediate: bool = False) -> Iterator[Any]:
            self.attempts += 1
            if self.attempts < 3:
                raise sqlite3.OperationalError("database is locked")
            with real_factory.transaction(immediate=immediate) as connection:
                yield connection

    transient_factory = TransientBusyFactory()

    assert migrator_type(transient_factory).migrate() == 2
    assert transient_factory.attempts == 3


def test_missing_json_functions_raise_a_diagnostic_migration_error(tmp_path: Path) -> None:
    _, _, _, migrator_type = _migration_api()
    real_factory = ac.SQLiteConnectionFactory(tmp_path)

    class MissingJsonConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, sql: str, parameters: object = ()) -> Any:
            if "json_valid('{}')" in sql and "json_array_length" in sql:
                raise sqlite3.OperationalError("no such function: json_valid")
            return self._connection.execute(sql, parameters)

    class MissingJsonFactory:
        @contextmanager
        def transaction(self, *, immediate: bool = False) -> Iterator[Any]:
            with real_factory.transaction(immediate=immediate) as connection:
                yield MissingJsonConnection(connection)

    with pytest.raises(ac.MigrationApplyError, match="JSON functions"):
        migrator_type(MissingJsonFactory()).migrate()


def test_applied_v1_migration_checksum_drift_is_rejected(tmp_path: Path) -> None:
    migrations, migration_type, checksum_error, migrator_type = _migration_api()
    factory = ac.SQLiteConnectionFactory(tmp_path)
    assert migrator_type(factory, migrations=migrations[:1]).migrate() == 1
    original = migrations[0]
    drifted = migration_type(
        version=original.version,
        name=original.name,
        sql=f"{original.sql}\n-- unauthorized drift\n",
    )

    with pytest.raises(checksum_error, match="checksum"):
        migrator_type(factory, migrations=(drifted,)).migrate()


def test_applied_v2_migration_checksum_drift_is_rejected(tmp_path: Path) -> None:
    migrations, migration_type, checksum_error, migrator_type = _migration_api()
    assert [migration.version for migration in migrations] == [1, 2]
    factory = _migrated_factory(tmp_path)
    original = migrations[1]
    drifted = migration_type(
        version=original.version,
        name=original.name,
        sql=f"{original.sql}\n-- unauthorized drift\n",
    )

    with pytest.raises(checksum_error, match="checksum"):
        migrator_type(factory, migrations=(migrations[0], drifted)).migrate()


def test_failed_migration_rolls_back_its_ddl_and_ledger_entry(tmp_path: Path) -> None:
    migrations, migration_type, _, migrator_type = _migration_api()
    assert [migration.version for migration in migrations] == [1, 2]
    factory = _migrated_factory(tmp_path)
    broken = migration_type(
        version=3,
        name="broken_injected_migration",
        sql="""
        CREATE TABLE should_rollback (value INTEGER NOT NULL);
        INSERT INTO table_that_does_not_exist VALUES (1);
        """,
    )

    with pytest.raises(ac.MigrationError, match="migration 3"):
        migrator_type(factory, migrations=(*migrations, broken)).migrate()

    with factory.connection(read_only=True) as connection:
        versions = [
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        rolled_back_table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'should_rollback'
            """
        ).fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert versions == [1, 2]
    assert rolled_back_table is None
    assert user_version == 2


def test_sql_splitter_preserves_semicolons_and_trailing_comments(tmp_path: Path) -> None:
    migrations, migration_type, _, migrator_type = _migration_api()
    assert [migration.version for migration in migrations] == [1, 2]
    factory = _migrated_factory(tmp_path)
    syntax_edges = migration_type(
        version=3,
        name="sql_syntax_edges",
        sql="""
        -- A line-comment semicolon must not split a statement;
        CREATE TABLE "edge;table" ("value;column" TEXT NOT NULL);
        /* A block-comment semicolon must not split a statement; */
        INSERT INTO "edge;table" VALUES ('before;trigger');
        CREATE TABLE trigger_audit (value TEXT NOT NULL);
        CREATE TRIGGER edge_table_audit
        AFTER INSERT ON "edge;table"
        BEGIN
            INSERT INTO trigger_audit VALUES (NEW."value;column" || ';trigger');
        END;
        INSERT INTO "edge;table" VALUES ('after;trigger');
        -- A trailing comment is not an incomplete SQL statement.
        """,
    )

    assert migrator_type(factory, migrations=(*migrations, syntax_edges)).migrate() == 3

    with factory.connection(read_only=True) as connection:
        values = [
            row[0]
            for row in connection.execute('SELECT "value;column" FROM "edge;table" ORDER BY rowid')
        ]
        audit_values = [row[0] for row in connection.execute("SELECT value FROM trigger_audit")]

    assert values == ["before;trigger", "after;trigger"]
    assert audit_values == ["after;trigger;trigger"]


def test_migration_plan_must_be_contiguous_and_start_at_one(tmp_path: Path) -> None:
    _, migration_type, _, migrator_type = _migration_api()
    migration_two = migration_type(
        version=2,
        name="out_of_order",
        sql="CREATE TABLE out_of_order (value INTEGER);",
    )

    with pytest.raises(ac.MigrationPlanError, match="contiguous"):
        migrator_type(ac.SQLiteConnectionFactory(tmp_path), migrations=(migration_two,))
