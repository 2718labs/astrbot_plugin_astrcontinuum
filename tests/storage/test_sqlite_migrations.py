from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
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
    _, _, _, migrator_type = _migration_api()
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=busy_timeout_ms)
    assert migrator_type(factory).migrate() == 1
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
        ("capsule-1", SESSION_HASH, "micro", 1, 1, "{}", 4, 1.0, NOW),
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

    assert migrator.migrate() == 1
    assert migrator.migrate() == 1

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
            "SELECT version, name, checksum FROM schema_migrations"
        ).fetchone()
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert tables == {
        "active_snapshots",
        "capsules",
        "compaction_jobs",
        "journal_events",
        "schema_migrations",
        "sessions",
        "snapshot_capsules",
        "snapshots",
    }
    assert ledger["version"] == migrations[0].version == 1
    assert ledger["name"] == migrations[0].name
    assert ledger["checksum"] == migrations[0].checksum
    assert migrations[0].checksum == hashlib.sha256(migrations[0].sql.encode("utf-8")).hexdigest()
    assert user_version == 1


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

    assert versions == [1, 1, 1, 1]
    assert ledger_count == 1


def test_applied_migration_checksum_drift_is_rejected(tmp_path: Path) -> None:
    migrations, migration_type, checksum_error, migrator_type = _migration_api()
    factory = _migrated_factory(tmp_path)
    original = migrations[0]
    drifted = migration_type(
        version=original.version,
        name=original.name,
        sql=f"{original.sql}\n-- unauthorized drift\n",
    )

    with pytest.raises(checksum_error, match="checksum"):
        migrator_type(factory, migrations=(drifted,)).migrate()


def test_failed_migration_rolls_back_its_ddl_and_ledger_entry(tmp_path: Path) -> None:
    migrations, migration_type, _, migrator_type = _migration_api()
    factory = _migrated_factory(tmp_path)
    broken = migration_type(
        version=2,
        name="broken_injected_migration",
        sql="""
        CREATE TABLE should_rollback (value INTEGER NOT NULL);
        INSERT INTO table_that_does_not_exist VALUES (1);
        """,
    )

    with pytest.raises(ac.MigrationError, match="migration 2"):
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

    assert versions == [1]
    assert rolled_back_table is None
    assert user_version == 1


def test_migration_plan_must_be_contiguous_and_start_at_one(tmp_path: Path) -> None:
    _, migration_type, _, migrator_type = _migration_api()
    migration_two = migration_type(
        version=2,
        name="out_of_order",
        sql="CREATE TABLE out_of_order (value INTEGER);",
    )

    with pytest.raises(ac.MigrationPlanError, match="contiguous"):
        migrator_type(ac.SQLiteConnectionFactory(tmp_path), migrations=(migration_two,))
