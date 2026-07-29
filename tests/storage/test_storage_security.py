from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac
from tests.storage.security_testkit import (
    install_v020_storage_fixture,
    v020_fixture_keys,
)

NOW = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
ACTIVE_KEY = bytes(range(32))
NEW_KEY = bytes(reversed(range(32)))
WRONG_KEY = bytes([37]) * 32
PLAINTEXT_MARKER = "legacy-plaintext-astrcontinuum-7f15c9"


def _keys(
    active: bytes = ACTIVE_KEY,
    *,
    previous: bytes | None = None,
) -> ac.ResolvedKeyMaterial:
    return ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(active),
        previous=ac.KeyMaterial.from_raw(previous) if previous is not None else None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )


def _factory(data_dir: Path) -> ac.SQLiteConnectionFactory:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    assert ac.SQLiteMigrator(factory).migrate() == 1
    return factory


def _session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id=f"{PLAINTEXT_MARKER}-platform",
        message_type=f"{PLAINTEXT_MARKER}-friend",
        session_id=f"{PLAINTEXT_MARKER}-session",
        group_id=None,
        user_id=f"{PLAINTEXT_MARKER}-user",
        conversation_id=f"{PLAINTEXT_MARKER}-conversation",
        persona_id=None,
    )


def _capsule(event: ac.EventEnvelope) -> ac.ContextCapsuleEnvelope:
    key = event.session_key
    return ac.ContextCapsuleEnvelope(
        capsule_id="capsule-1",
        schema_version="1.0.0",
        level=ac.CapsuleLevel.MICRO,
        session_key=key,
        covered_event_start=event.sequence,
        covered_event_end=event.sequence,
        source_event_ids=(event.event_id,),
        goals=(
            ac.CapsuleClaim(
                claim_id="goal-1",
                text=f"{PLAINTEXT_MARKER}-goal",
                status=ac.SemanticStatus.ACTIVE,
                confidence=1.0,
                source_event_ids=(event.event_id,),
            ),
        ),
        constraints=(),
        decisions=(),
        progress=(),
        open_loops=(),
        preferences=(),
        entities=(),
        emotional_context=(),
        exact_anchors=(
            ac.CapsuleAnchor(
                anchor_id="anchor-1",
                anchor_type=ac.AnchorType.NAME,
                exact_text=f"{PLAINTEXT_MARKER}-anchor",
                source_event_ids=(event.event_id,),
                status=ac.AnchorStatus.ACTIVE,
                importance=1.0,
            ),
        ),
        dependencies=(),
        narrative_summary=f"{PLAINTEXT_MARKER}-capsule",
        token_cost=10,
        quality=ac.CapsuleQuality(
            mechanical_passed=True,
            source_coverage=1.0,
            anchor_recall=1.0,
            unsupported_critical_claims=0,
            coverage_gap=0,
        ),
        created_at=NOW,
    )


def _legacy_snapshot(
    event: ac.EventEnvelope,
) -> tuple[ac.ContextCapsuleEnvelope, ac.SnapshotEnvelope]:
    capsule = _capsule(event)
    snapshot = ac.SnapshotEnvelope(
        snapshot_id="snapshot-1",
        session_key=event.session_key,
        base_snapshot_id=None,
        covered_event_end=event.sequence,
        source_high_water_mark=event.sequence,
        capsule_ids=(capsule.capsule_id,),
        exact_anchor_ids=("anchor-1",),
        rendered_context=f"{PLAINTEXT_MARKER}-rendered",
        token_cost=capsule.token_cost,
        audit_outcome=ac.SnapshotAuditOutcome(
            mechanical_passed=True,
            semantic_status=ac.SemanticAuditStatus.NOT_RUN,
            failure_codes=(),
        ),
        state=ac.SnapshotState.COMMITTED,
        created_at=NOW,
        committed_at=NOW + timedelta(minutes=1),
    )
    return capsule, snapshot


def _seed_legacy_database(
    data_dir: Path,
) -> tuple[
    ac.SQLiteConnectionFactory,
    ac.SessionKey,
    ac.EventEnvelope,
    ac.ContextCapsuleEnvelope,
    ac.SnapshotEnvelope,
]:
    factory = _factory(data_dir)
    key = _session_key()
    event = ac.EventEnvelope.create(
        event_id="event-1",
        session_key=key,
        sequence=1,
        event_type=ac.EventType.USER_MESSAGE,
        content=f"{PLAINTEXT_MARKER}-message",
        idempotency_key="request-1",
        token_count=5,
        created_at=NOW,
    )
    capsule, snapshot = _legacy_snapshot(event)
    timestamp = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    committed_at = (
        snapshot.committed_at.isoformat(timespec="microseconds").replace(
            "+00:00",
            "Z",
        )
        if snapshot.committed_at is not None
        else None
    )
    with factory.transaction(immediate=True) as connection:
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?)
            """,
            (
                key.session_key_hash,
                key.canonical_json(),
                key.platform_instance_id,
                key.message_type,
                key.session_id,
                key.group_id,
                key.user_id,
                key.conversation_id,
                key.persona_id,
                timestamp,
                timestamp,
            ),
        )
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
                key.session_key_hash,
                event.sequence,
                event.event_type.value,
                event.role.value,
                event.content,
                event.source_hook.value,
                event.idempotency_key,
                event.token_count,
                timestamp,
            ),
        )
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
                key.session_key_hash,
                capsule.level.value,
                capsule.covered_event_start,
                capsule.covered_event_end,
                _canonical_model_json(capsule),
                capsule.token_cost,
                capsule.quality.source_coverage,
                timestamp,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', ?, ?)
            """,
            (
                snapshot.snapshot_id,
                key.session_key_hash,
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
                timestamp,
                committed_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO snapshot_capsules (
                snapshot_id,
                ordinal,
                capsule_id,
                slot
            ) VALUES (?, 0, ?, 'memory')
            """,
            (snapshot.snapshot_id, capsule.capsule_id),
        )
        connection.execute(
            """
            INSERT INTO active_snapshots (
                session_key_hash,
                snapshot_id,
                pointer_version,
                updated_at
            ) VALUES (?, ?, 1, ?)
            """,
            (key.session_key_hash, snapshot.snapshot_id, committed_at),
        )
    return factory, key, event, capsule, snapshot


def _sentinel_envelope(value: str) -> str:
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        assert list(parsed) == ["$astrcontinuum_encrypted"]
        return parsed["$astrcontinuum_encrypted"]
    assert parsed[0] == "$astrcontinuum_encrypted"
    assert len(parsed) == 2
    return parsed[1]


def _physical_storage(factory: ac.SQLiteConnectionFactory) -> dict[str, list[tuple[object, ...]]]:
    queries = {
        "sessions": """
            SELECT
                session_key_hash,
                canonical_session_key_json,
                platform_instance_id,
                message_type,
                session_id,
                group_id,
                user_id,
                conversation_id,
                persona_id
            FROM sessions
            ORDER BY session_key_hash
        """,
        "journal_events": """
            SELECT event_id, content
            FROM journal_events
            ORDER BY event_id
        """,
        "capsules": """
            SELECT capsule_id, canonical_capsule_json
            FROM capsules
            ORDER BY capsule_id
        """,
        "snapshots": """
            SELECT snapshot_id, exact_anchor_ids_json, rendered_context, audit_outcome
            FROM snapshots
            ORDER BY snapshot_id
        """,
    }
    with factory.connection(read_only=True) as connection:
        result = {
            table: [tuple(row) for row in connection.execute(query).fetchall()]
            for table, query in queries.items()
        }
        if connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'storage_security'
            """
        ).fetchone():
            result["storage_security"] = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT active_key_id, state, key_verifier
                    FROM storage_security
                    ORDER BY singleton_id
                    """
                ).fetchall()
            ]
        return result


def _ciphertext_envelopes(
    factory: ac.SQLiteConnectionFactory,
) -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    with factory.connection(read_only=True) as connection:
        for row in connection.execute("SELECT * FROM sessions"):
            record = str(row["session_key_hash"])
            result[("sessions", record, "canonical_session_key_json")] = _sentinel_envelope(
                str(row["canonical_session_key_json"])
            )
            for column in (
                "platform_instance_id",
                "message_type",
                "session_id",
                "group_id",
                "user_id",
                "conversation_id",
                "persona_id",
            ):
                if row[column] is not None:
                    result[("sessions", record, column)] = str(row[column])
        event_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(journal_events)")
        }
        for row in connection.execute("SELECT * FROM journal_events"):
            record = str(row["event_id"])
            result[("journal_events", record, "content")] = str(row["content"])
            if "token_count_envelope" in event_columns:
                result[("journal_events", record, "token_count")] = str(row["token_count_envelope"])
        capsule_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(capsules)")
        }
        for row in connection.execute("SELECT * FROM capsules"):
            record = str(row["capsule_id"])
            result[("capsules", record, "canonical_capsule_json")] = _sentinel_envelope(
                str(row["canonical_capsule_json"])
            )
            if "token_cost_envelope" in capsule_columns:
                result[("capsules", record, "token_cost")] = str(row["token_cost_envelope"])
        snapshot_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(snapshots)")
        }
        for row in connection.execute(
            """
            SELECT *
            FROM snapshots
            """
        ):
            record = str(row["snapshot_id"])
            result[("snapshots", record, "exact_anchor_ids_json")] = _sentinel_envelope(
                str(row["exact_anchor_ids_json"])
            )
            result[("snapshots", record, "rendered_context")] = str(row["rendered_context"])
            result[("snapshots", record, "audit_outcome")] = _sentinel_envelope(
                str(row["audit_outcome"])
            )
            if "token_cost_envelope" in snapshot_columns:
                result[("snapshots", record, "token_cost")] = str(row["token_cost_envelope"])
        security = connection.execute(
            "SELECT key_verifier FROM storage_security WHERE singleton_id = 1"
        ).fetchone()
        result[("storage_security", "1", "key_verifier")] = str(security["key_verifier"])
    return result


def _index_contract(
    connection: sqlite3.Connection,
    tables: tuple[str, ...],
) -> dict[str, tuple[tuple[object, ...], ...]]:
    contract: dict[str, tuple[tuple[object, ...], ...]] = {}
    for table in tables:
        indices = []
        for row in connection.execute(f"PRAGMA index_list({table})"):
            columns = tuple(
                str(column["name"])
                for column in connection.execute(f"PRAGMA index_info({row['name']})")
            )
            indices.append(
                (
                    str(row["name"]),
                    int(row["unique"]),
                    str(row["origin"]),
                    int(row["partial"]),
                    columns,
                )
            )
        contract[table] = tuple(sorted(indices))
    return contract


def _foreign_key_contract(
    connection: sqlite3.Connection,
    tables: tuple[str, ...],
) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: tuple(
            sorted(
                (
                    str(row["table"]),
                    str(row["from"]),
                    str(row["to"]),
                    str(row["on_update"]),
                    str(row["on_delete"]),
                    str(row["match"]),
                )
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            )
        )
        for table in tables
    }


def _normalized_trigger_sql(
    connection: sqlite3.Connection,
    names: tuple[str, ...],
) -> dict[str, str]:
    rows = connection.execute(
        f"""
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'trigger'
          AND name IN ({",".join("?" for _ in names)})
        """,
        names,
    )
    return {str(row["name"]): " ".join(str(row["sql"]).split()) for row in rows}


def _normalized_schema_sql(connection: sqlite3.Connection, name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?",
        (name,),
    ).fetchone()
    assert row is not None
    return " ".join(str(row["sql"]).split())


def _normalized_expected_sql(sql: str) -> str:
    return " ".join(sql.split())


def _database_snapshot_digest(factory: ac.SQLiteConnectionFactory) -> str:
    with factory.connection(read_only=True) as connection:
        schema = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ]
        tables = [
            str(row["name"])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        rows = {
            table: [
                tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            ]
            for table in tables
        }
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    payload = repr((schema, rows, user_version)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert_code(
    raised: pytest.ExceptionInfo[ac.StorageSecurityError],
    code: ac.SecurityErrorCode,
) -> None:
    assert raised.value.code is code
    assert str(raised.value) == code.value
    assert raised.value.__cause__ is None
    for secret in (
        ac.encode_key_text(ACTIVE_KEY),
        ac.encode_key_text(NEW_KEY),
        ac.encode_key_text(WRONG_KEY),
        PLAINTEXT_MARKER,
    ):
        assert secret not in repr(raised.value)


def test_fresh_database_activates_secure_schema_and_verifier(tmp_path: Path) -> None:
    factory = _factory(tmp_path)

    result = ac.activate_storage_security(factory, _keys())

    assert result.state is ac.StorageMaintenanceState.ACTIVE
    assert result.key_id == ac.key_id_for(ACTIVE_KEY)
    assert result.codec.key_id == result.key_id
    assert result.migrated is True
    assert result.rekeyed is False
    assert result.scrubbed is True
    with factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT format_version, active_key_id, state, key_verifier
            FROM storage_security
            """
        ).fetchone()
        assert tuple(row[:3]) == (2, result.key_id, "ACTIVE")
        assert str(row["key_verifier"]).startswith(f"acenc:v1:{result.key_id}:")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 2


def test_real_v020_fixture_upgrades_to_format_two_without_changing_logical_artifacts(
    tmp_path: Path,
) -> None:
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    install_v020_storage_fixture(factory)
    artifact_tables = ("journal_events", "capsules", "snapshots")
    immutable_triggers = (
        "journal_events_immutable_update",
        "journal_events_immutable_delete",
        "capsules_immutable_update",
        "capsules_immutable_delete",
        "snapshots_immutable_update",
        "snapshots_immutable_delete",
    )
    with factory.connection(read_only=True) as connection:
        v1_ledger = tuple(
            connection.execute(
                """
                SELECT version, name, checksum, applied_at
                FROM schema_migrations
                WHERE version = 1
                """
            ).fetchone()
        )
        before_event = tuple(
            connection.execute(
                """
                SELECT event_id, session_key_hash, sequence, token_count
                FROM journal_events
                """
            ).fetchone()
        )
        before_capsule = tuple(
            connection.execute(
                """
                SELECT capsule_id, session_key_hash, covered_event_start,
                       covered_event_end, token_cost, source_coverage
                FROM capsules
                """
            ).fetchone()
        )
        before_snapshot = tuple(
            connection.execute(
                """
                SELECT snapshot_id, session_key_hash, base_snapshot_id,
                       covered_event_end, source_high_water_mark, token_cost
                FROM snapshots
                """
            ).fetchone()
        )
        before_membership = tuple(
            connection.execute(
                "SELECT snapshot_id, ordinal, capsule_id, slot FROM snapshot_capsules"
            ).fetchone()
        )
        before_active = tuple(
            connection.execute(
                """
                SELECT session_key_hash, snapshot_id, pointer_version
                FROM active_snapshots
                """
            ).fetchone()
        )
        old_index_contract = _index_contract(connection, artifact_tables)
        old_foreign_key_contract = _foreign_key_contract(connection, artifact_tables)
        old_immutable_sql = _normalized_trigger_sql(connection, immutable_triggers)

    activation = ac.activate_storage_security(factory, v020_fixture_keys())

    assert activation.state is ac.StorageMaintenanceState.ACTIVE
    assert activation.migrated is True
    assert activation.rekeyed is False
    assert activation.scrubbed is True
    with factory.connection(read_only=True) as connection:
        ledger = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT version, name, checksum, applied_at
                FROM schema_migrations
                ORDER BY version
                """
            )
        ]
        metadata = tuple(
            connection.execute(
                "SELECT format_version, active_key_id, state FROM storage_security"
            ).fetchone()
        )
        event = connection.execute("SELECT * FROM journal_events").fetchone()
        capsule = connection.execute("SELECT * FROM capsules").fetchone()
        snapshot = connection.execute("SELECT * FROM snapshots").fetchone()
        event_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(journal_events)")
        }
        capsule_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(capsules)")
        }
        snapshot_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(snapshots)")
        }
        indices = {
            str(row["name"])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        triggers = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        index_contract = _index_contract(connection, artifact_tables)
        foreign_key_contract = _foreign_key_contract(connection, artifact_tables)
        immutable_sql = _normalized_trigger_sql(connection, immutable_triggers)
        journal_sql = _normalized_schema_sql(connection, "journal_events")
        capsule_sql = _normalized_schema_sql(connection, "capsules")
        snapshot_sql = _normalized_schema_sql(connection, "snapshots")
        metric_sql = _normalized_schema_sql(connection, "token_metrics")
        intent_sql = _normalized_schema_sql(connection, "token_metric_backfill_intents")
        backfill_index_sql = _normalized_schema_sql(
            connection,
            "idx_token_metric_backfill_session",
        )

        assert ledger[0] == v1_ledger
        assert ledger[1][0:2] == (2, "encrypted_token_metrics")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert metadata == (
            2,
            activation.key_id,
            ac.StorageMaintenanceState.ACTIVE.value,
        )
        assert "token_count" not in event_columns
        assert "token_cost" not in capsule_columns
        assert "token_cost" not in snapshot_columns
        assert "token_count_envelope" in event_columns
        assert "token_cost_envelope" in capsule_columns
        assert "token_cost_envelope" in snapshot_columns
        assert (
            "token_count_envelope TEXT NOT NULL "
            "CHECK (substr(token_count_envelope, 1, 9) = 'acenc:v1:')"
        ) in journal_sql
        assert (
            "token_cost_envelope TEXT NOT NULL "
            "CHECK (substr(token_cost_envelope, 1, 9) = 'acenc:v1:')"
        ) in capsule_sql
        assert (
            "token_cost_envelope TEXT NOT NULL "
            "CHECK (substr(token_cost_envelope, 1, 9) = 'acenc:v1:')"
        ) in snapshot_sql
        assert (
            activation.codec.decrypt_non_negative_int(
                "journal_events",
                "token_count",
                str(event["event_id"]),
                event["token_count_envelope"],
            )
            == before_event[3]
        )
        assert (
            activation.codec.decrypt_non_negative_int(
                "capsules",
                "token_cost",
                str(capsule["capsule_id"]),
                capsule["token_cost_envelope"],
            )
            == before_capsule[4]
        )
        assert (
            activation.codec.decrypt_non_negative_int(
                "snapshots",
                "token_cost",
                str(snapshot["snapshot_id"]),
                snapshot["token_cost_envelope"],
            )
            == before_snapshot[5]
        )
        assert (
            event["event_id"],
            event["session_key_hash"],
            event["sequence"],
        ) == before_event[:3]
        assert (
            capsule["capsule_id"],
            capsule["session_key_hash"],
            capsule["covered_event_start"],
            capsule["covered_event_end"],
            capsule["source_coverage"],
        ) == (*before_capsule[:4], before_capsule[5])
        assert (
            snapshot["snapshot_id"],
            snapshot["session_key_hash"],
            snapshot["base_snapshot_id"],
            snapshot["covered_event_end"],
            snapshot["source_high_water_mark"],
        ) == before_snapshot[:5]
        assert (
            tuple(
                connection.execute(
                    "SELECT snapshot_id, ordinal, capsule_id, slot FROM snapshot_capsules"
                ).fetchone()
            )
            == before_membership
        )
        assert (
            tuple(
                connection.execute(
                    """
                SELECT session_key_hash, snapshot_id, pointer_version
                FROM active_snapshots
                """
                ).fetchone()
            )
            == before_active
        )
        assert index_contract == old_index_contract
        assert foreign_key_contract == old_foreign_key_contract
        assert immutable_sql == old_immutable_sql
        assert set(immutable_triggers) <= triggers
        assert {
            "journal_events_secure_insert",
            "capsules_secure_insert",
        } <= triggers
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM token_metrics").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM token_metric_backfill_intents").fetchone()[0]
            == 0
        )
        assert "idx_token_metric_backfill_session" in indices
        assert metric_sql == _normalized_expected_sql(
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
            """
        )
        assert intent_sql == _normalized_expected_sql(
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
            """
        )
        assert backfill_index_sql == _normalized_expected_sql(
            """
            CREATE INDEX idx_token_metric_backfill_session
            ON token_metric_backfill_intents
                (session_key_hash, created_at, artifact_kind, artifact_id)
            """
        )

    with (
        pytest.raises(sqlite3.IntegrityError, match="journal_events rows are immutable"),
        factory.transaction(immediate=True) as connection,
    ):
        connection.execute(
            """
            UPDATE journal_events
            SET token_count_envelope = token_count_envelope
            WHERE event_id = 'event-1'
            """
        )

    with (
        pytest.raises(sqlite3.IntegrityError),
        factory.transaction(immediate=True) as connection,
    ):
        connection.execute(
            """
            INSERT INTO journal_events (
                event_id, session_key_hash, sequence, event_type, role, content,
                source_hook, idempotency_key, token_count_envelope, created_at
            )
            SELECT
                'plaintext-count-event', session_key_hash, sequence + 100,
                event_type, role, content, source_hook, 'plaintext-count-request',
                '17', created_at
            FROM journal_events
            WHERE event_id = 'event-1'
            """
        )


@pytest.mark.parametrize(
    "stage",
    [
        "security_v2.after_journal_copy",
        "security_v2.after_capsule_copy",
        "security_v2.after_snapshot_copy",
        "security_v2.after_verify",
    ],
)
def test_format_two_precommit_faults_roll_back_schema_rows_and_ledger(
    tmp_path: Path,
    stage: str,
) -> None:
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    install_v020_storage_fixture(factory)
    before = _database_snapshot_digest(factory)

    def fail_at_boundary(boundary: str) -> None:
        if boundary == stage:
            raise RuntimeError(PLAINTEXT_MARKER)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            v020_fixture_keys(),
            fault_injector=fail_at_boundary,
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    assert _database_snapshot_digest(factory) == before
    with factory.connection(read_only=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert tuple(
            connection.execute("SELECT format_version, state FROM storage_security").fetchone()
        ) == (1, ac.StorageMaintenanceState.ACTIVE.value)
        assert (
            connection.execute(
                """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'token_metrics'
            """
            ).fetchone()
            is None
        )


def test_format_one_needs_scrub_recovers_before_format_two_upgrade(
    tmp_path: Path,
) -> None:
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    install_v020_storage_fixture(factory)
    with factory.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE storage_security SET state = 'NEEDS_SCRUB' WHERE singleton_id = 1"
        )
    stages: list[str] = []

    activation = ac.activate_storage_security(
        factory,
        v020_fixture_keys(),
        fault_injector=stages.append,
    )

    assert activation.state is ac.StorageMaintenanceState.ACTIVE
    assert activation.migrated is True
    assert stages.index("security.after_vacuum") < stages.index("security_v2.after_journal_copy")
    with factory.connection(read_only=True) as connection:
        assert tuple(
            connection.execute("SELECT format_version, state FROM storage_security").fetchone()
        ) == (2, ac.StorageMaintenanceState.ACTIVE.value)


def test_format_two_upgrade_and_key_rotation_use_only_the_target_key(
    tmp_path: Path,
) -> None:
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    install_v020_storage_fixture(factory)

    activation = ac.activate_storage_security(
        factory,
        _keys(NEW_KEY, previous=ACTIVE_KEY),
    )

    assert activation.migrated is True
    assert activation.rekeyed is True
    assert activation.state is ac.StorageMaintenanceState.ACTIVE
    assert activation.key_id == ac.key_id_for(NEW_KEY)
    encrypted = _ciphertext_envelopes(factory)
    assert {
        ("journal_events", "event-1", "token_count"),
        ("capsules", "capsule-1", "token_cost"),
        ("snapshots", "snapshot-1", "token_cost"),
    } <= encrypted.keys()
    assert all(
        envelope.startswith(f"acenc:v1:{activation.key_id}:") for envelope in encrypted.values()
    )


def test_format_two_postcommit_failure_stays_needs_scrub_and_restarts(
    tmp_path: Path,
) -> None:
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    install_v020_storage_fixture(factory)

    def fail_after_commit(stage: str) -> None:
        if stage == "security_v2.after_commit":
            raise RuntimeError(PLAINTEXT_MARKER)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            v020_fixture_keys(),
            fault_injector=fail_after_commit,
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_SCRUB_FAILED)
    with factory.connection(read_only=True) as connection:
        assert tuple(
            connection.execute("SELECT format_version, state FROM storage_security").fetchone()
        ) == (2, ac.StorageMaintenanceState.NEEDS_SCRUB.value)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT count(*) FROM schema_migrations WHERE version = 2"
            ).fetchone()[0]
            == 1
        )

    resumed = ac.activate_storage_security(factory, v020_fixture_keys())
    assert resumed.state is ac.StorageMaintenanceState.ACTIVE
    assert resumed.migrated is False
    assert resumed.rekeyed is False
    assert resumed.scrubbed is True


def test_format_two_postcommit_cancellation_stays_needs_scrub_and_restarts(
    tmp_path: Path,
) -> None:
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    install_v020_storage_fixture(factory)

    class SimulatedCancellation(BaseException):
        pass

    def cancel_after_commit(stage: str) -> None:
        if stage == "security_v2.after_commit":
            raise SimulatedCancellation

    with pytest.raises(SimulatedCancellation):
        ac.activate_storage_security(
            factory,
            v020_fixture_keys(),
            fault_injector=cancel_after_commit,
        )

    with factory.connection(read_only=True) as connection:
        assert tuple(
            connection.execute("SELECT format_version, state FROM storage_security").fetchone()
        ) == (2, ac.StorageMaintenanceState.NEEDS_SCRUB.value)

    resumed = ac.activate_storage_security(factory, v020_fixture_keys())
    assert resumed.state is ac.StorageMaintenanceState.ACTIVE
    assert resumed.scrubbed is True


def test_populated_v01_database_is_atomically_encrypted_and_scrubbed(
    tmp_path: Path,
) -> None:
    factory, key, event, capsule, snapshot = _seed_legacy_database(tmp_path)

    result = ac.activate_storage_security(factory, _keys())

    codec = result.codec
    with factory.connection(read_only=True) as connection:
        session = connection.execute("SELECT * FROM sessions").fetchone()
        stored_event = connection.execute("SELECT * FROM journal_events").fetchone()
        stored_capsule = connection.execute("SELECT * FROM capsules").fetchone()
        stored_snapshot = connection.execute("SELECT * FROM snapshots").fetchone()
        assert session is not None
        assert stored_event is not None
        assert stored_capsule is not None
        assert stored_snapshot is not None

        session_record = str(session["session_key_hash"])
        assert (
            codec.decrypt_object_json(
                "sessions",
                "canonical_session_key_json",
                session_record,
                str(session["canonical_session_key_json"]),
            )
            == key.canonical_json()
        )
        for column in (
            "platform_instance_id",
            "message_type",
            "session_id",
            "user_id",
            "conversation_id",
        ):
            assert codec.decrypt_text(
                "sessions",
                column,
                session_record,
                str(session[column]),
            ) == getattr(key, column)
        assert session["group_id"] is None
        assert session["persona_id"] is None

        assert (
            codec.decrypt_text(
                "journal_events",
                "content",
                str(stored_event["event_id"]),
                str(stored_event["content"]),
            )
            == event.content
        )
        assert codec.decrypt_object_json(
            "capsules",
            "canonical_capsule_json",
            str(stored_capsule["capsule_id"]),
            str(stored_capsule["canonical_capsule_json"]),
        ) == json.dumps(
            capsule.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        assert codec.decrypt_array_json(
            "snapshots",
            "exact_anchor_ids_json",
            str(stored_snapshot["snapshot_id"]),
            str(stored_snapshot["exact_anchor_ids_json"]),
        ) == json.dumps(
            list(snapshot.exact_anchor_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        assert (
            codec.decrypt_text(
                "snapshots",
                "rendered_context",
                str(stored_snapshot["snapshot_id"]),
                str(stored_snapshot["rendered_context"]),
            )
            == snapshot.rendered_context
        )
        assert codec.decrypt_object_json(
            "snapshots",
            "audit_outcome",
            str(stored_snapshot["snapshot_id"]),
            str(stored_snapshot["audit_outcome"]),
        ) == json.dumps(
            snapshot.audit_outcome.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

        assert _sentinel_envelope(str(session["canonical_session_key_json"])).startswith(
            f"acenc:v1:{result.key_id}:"
        )
        assert _sentinel_envelope(str(stored_capsule["canonical_capsule_json"])).startswith(
            f"acenc:v1:{result.key_id}:"
        )
        assert _sentinel_envelope(str(stored_snapshot["audit_outcome"])).startswith(
            f"acenc:v1:{result.key_id}:"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM journal_events").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM capsules").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
        trigger_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
        assert {
            "journal_events_immutable_update",
            "journal_events_immutable_delete",
            "capsules_immutable_update",
            "capsules_immutable_delete",
            "snapshots_immutable_update",
            "snapshots_immutable_delete",
        } <= trigger_names

    marker = PLAINTEXT_MARKER.encode()
    for path in (
        factory.database_path,
        Path(f"{factory.database_path}-wal"),
        Path(f"{factory.database_path}-shm"),
    ):
        if path.exists():
            assert marker not in path.read_bytes()


def test_secure_schema_rejects_new_plaintext_event_and_capsule_rows(
    tmp_path: Path,
) -> None:
    factory, key, *_ = _seed_legacy_database(tmp_path)
    ac.activate_storage_security(factory, _keys())

    with pytest.raises(sqlite3.IntegrityError), factory.transaction(immediate=True) as connection:
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
            ) VALUES (?, ?, 2, 'USER_MESSAGE', 'USER', ?, 'ON_LLM_REQUEST', ?, 1, ?)
            """,
            (
                "plaintext-event",
                key.session_key_hash,
                PLAINTEXT_MARKER,
                "plaintext-request",
                NOW.isoformat(),
            ),
        )

    with pytest.raises(sqlite3.IntegrityError), factory.transaction(immediate=True) as connection:
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
            ) VALUES (?, ?, 'micro', 1, 1, '{}', 1, 1.0, ?)
            """,
            (
                "plaintext-capsule",
                key.session_key_hash,
                NOW.isoformat(),
            ),
        )


def test_matching_active_key_authenticates_without_rewriting_ciphertext(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)
    first = ac.activate_storage_security(factory, _keys())
    before = _physical_storage(factory)

    second = ac.activate_storage_security(factory, _keys())

    assert second.state is ac.StorageMaintenanceState.ACTIVE
    assert second.migrated is False
    assert second.rekeyed is False
    assert second.scrubbed is False
    assert second.key_id == first.key_id
    assert _physical_storage(factory) == before


def test_wrong_active_key_requires_previous_without_any_logical_write(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)
    ac.activate_storage_security(factory, _keys())
    before = _physical_storage(factory)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(factory, _keys(NEW_KEY))

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_PREVIOUS_KEY_REQUIRED)
    assert _physical_storage(factory) == before


def test_wrong_previous_key_is_rejected_without_partial_rotation(tmp_path: Path) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)
    ac.activate_storage_security(factory, _keys())
    before = _physical_storage(factory)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            _keys(NEW_KEY, previous=WRONG_KEY),
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_PREVIOUS_KEY_REQUIRED)
    assert _physical_storage(factory) == before


def test_rotation_reencrypts_every_protected_value_and_changes_verifier(
    tmp_path: Path,
) -> None:
    factory, key, event, capsule, snapshot = _seed_legacy_database(tmp_path)
    ac.activate_storage_security(factory, _keys())
    before = _ciphertext_envelopes(factory)

    rotated = ac.activate_storage_security(
        factory,
        _keys(NEW_KEY, previous=ACTIVE_KEY),
    )

    after = _ciphertext_envelopes(factory)
    assert rotated.rekeyed is True
    assert rotated.migrated is False
    assert rotated.scrubbed is True
    assert rotated.state is ac.StorageMaintenanceState.ACTIVE
    assert rotated.key_id == ac.key_id_for(NEW_KEY)
    assert after.keys() == before.keys()
    assert all(after[item] != before[item] for item in before)
    assert all(envelope.startswith(f"acenc:v1:{rotated.key_id}:") for envelope in after.values())

    codec = rotated.codec
    with factory.connection(read_only=True) as connection:
        session = connection.execute("SELECT * FROM sessions").fetchone()
        stored_event = connection.execute("SELECT * FROM journal_events").fetchone()
        stored_capsule = connection.execute("SELECT * FROM capsules").fetchone()
        stored_snapshot = connection.execute("SELECT * FROM snapshots").fetchone()
        record = str(session["session_key_hash"])
        assert (
            codec.decrypt_object_json(
                "sessions",
                "canonical_session_key_json",
                record,
                str(session["canonical_session_key_json"]),
            )
            == key.canonical_json()
        )
        assert (
            codec.decrypt_text(
                "journal_events",
                "content",
                str(stored_event["event_id"]),
                str(stored_event["content"]),
            )
            == event.content
        )
        assert codec.decrypt_object_json(
            "capsules",
            "canonical_capsule_json",
            str(stored_capsule["capsule_id"]),
            str(stored_capsule["canonical_capsule_json"]),
        ) == _canonical_model_json(capsule)
        assert (
            codec.decrypt_text(
                "snapshots",
                "rendered_context",
                str(stored_snapshot["snapshot_id"]),
                str(stored_snapshot["rendered_context"]),
            )
            == snapshot.rendered_context
        )


def _canonical_model_json(value: object) -> str:
    return json.dumps(
        value.model_dump(mode="json"),  # type: ignore[attr-defined]
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_legacy_precommit_failure_rolls_back_to_v01_readable_rows(
    tmp_path: Path,
) -> None:
    factory, key, *_ = _seed_legacy_database(tmp_path)
    before = _physical_storage(factory)

    def fail_after_encrypt(stage: str) -> None:
        if stage == "security.after_encrypt":
            raise RuntimeError(PLAINTEXT_MARKER)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            _keys(),
            fault_injector=fail_after_encrypt,
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    assert _physical_storage(factory) == before
    with factory.connection(read_only=True) as connection:
        session_row = connection.execute(
            """
            SELECT canonical_session_key_json
            FROM sessions
            WHERE session_key_hash = ?
            """,
            (key.session_key_hash,),
        ).fetchone()
        snapshot_row = connection.execute(
            """
            SELECT rendered_context
            FROM snapshots
            WHERE session_key_hash = ?
            """,
            (key.session_key_hash,),
        ).fetchone()
    assert session_row is not None
    assert snapshot_row is not None
    assert ac.SessionKey.model_validate_json(session_row["canonical_session_key_json"]) == key
    assert snapshot_row["rendered_context"] == f"{PLAINTEXT_MARKER}-rendered"


def test_postcommit_interruption_resumes_format_one_scrub_then_upgrades(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)

    def stop_after_commit(stage: str) -> None:
        if stage == "security.after_transform_commit":
            raise RuntimeError(PLAINTEXT_MARKER)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            _keys(),
            fault_injector=stop_after_commit,
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_SCRUB_FAILED)
    after_commit = _physical_storage(factory)
    assert after_commit["storage_security"][0][1] == "NEEDS_SCRUB"
    resumed = ac.activate_storage_security(factory, _keys())
    assert resumed.state is ac.StorageMaintenanceState.ACTIVE
    assert resumed.migrated is True
    assert resumed.scrubbed is True


def test_scrub_failure_remains_resumable_and_never_reports_active(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)

    def fail_before_vacuum(stage: str) -> None:
        if stage == "security.before_vacuum":
            raise RuntimeError(PLAINTEXT_MARKER)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            _keys(),
            fault_injector=fail_before_vacuum,
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_SCRUB_FAILED)
    assert _physical_storage(factory)["storage_security"][0][1] == "NEEDS_SCRUB"
    resumed = ac.activate_storage_security(factory, _keys())
    assert resumed.state is ac.StorageMaintenanceState.ACTIVE
    assert resumed.scrubbed is True


def test_malformed_legacy_sentinel_is_rejected_before_schema_change(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)
    with factory.transaction(immediate=True) as connection:
        connection.execute("DROP TRIGGER capsules_immutable_update")
        connection.execute(
            """
            UPDATE capsules
            SET canonical_capsule_json =
                '{"$astrcontinuum_encrypted":"malformed-legacy-value"}'
            """
        )
        connection.execute(
            """
            CREATE TRIGGER capsules_immutable_update
            BEFORE UPDATE ON capsules
            BEGIN
                SELECT RAISE(ABORT, 'capsules rows are immutable');
            END
            """
        )
    before = _physical_storage(factory)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(factory, _keys())

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_LEGACY_VALIDATION_FAILED)
    assert _physical_storage(factory) == before
    with factory.connection(read_only=True) as connection:
        assert (
            connection.execute(
                """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'storage_security'
            """
            ).fetchone()
            is None
        )


def test_rotation_precommit_failure_preserves_old_key_and_all_ciphertext(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)
    ac.activate_storage_security(factory, _keys())
    before = _physical_storage(factory)

    def fail_rekey(stage: str) -> None:
        if stage == "security.after_rekey_encrypt":
            raise RuntimeError(PLAINTEXT_MARKER)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            _keys(NEW_KEY, previous=ACTIVE_KEY),
            fault_injector=fail_rekey,
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_REKEY_FAILED)
    assert _physical_storage(factory) == before
    recovered = ac.activate_storage_security(factory, _keys())
    assert recovered.state is ac.StorageMaintenanceState.ACTIVE
    assert recovered.key_id == ac.key_id_for(ACTIVE_KEY)


def test_rotation_postcommit_interruption_resumes_with_new_active_key(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)
    ac.activate_storage_security(factory, _keys())

    def stop_after_rekey_commit(stage: str) -> None:
        if stage == "security.after_rekey_commit":
            raise RuntimeError(PLAINTEXT_MARKER)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(
            factory,
            _keys(NEW_KEY, previous=ACTIVE_KEY),
            fault_injector=stop_after_rekey_commit,
        )

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_SCRUB_FAILED)
    committed = _physical_storage(factory)
    assert committed["storage_security"][0][:2] == (
        ac.key_id_for(NEW_KEY),
        "NEEDS_SCRUB",
    )
    resumed = ac.activate_storage_security(factory, _keys(NEW_KEY))
    assert resumed.state is ac.StorageMaintenanceState.ACTIVE
    assert resumed.key_id == ac.key_id_for(NEW_KEY)
    assert resumed.rekeyed is False
    assert resumed.scrubbed is True


def test_forged_key_metadata_is_rejected_before_storage_access(tmp_path: Path) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)
    forged = ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial(raw_key=ACTIVE_KEY, key_id="0" * 16),
        previous=None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )
    before = _physical_storage(factory)

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(factory, forged)

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_KEY_INVALID)
    assert _physical_storage(factory) == before


def test_arbitrary_fault_exception_is_reduced_to_content_free_code(
    tmp_path: Path,
) -> None:
    factory, *_ = _seed_legacy_database(tmp_path)

    class SecretBearingFailure(Exception):
        pass

    def fail(stage: str) -> None:
        if stage == "security.after_encrypt":
            raise SecretBearingFailure(f"{PLAINTEXT_MARKER}:{ac.encode_key_text(ACTIVE_KEY)}")

    with pytest.raises(ac.StorageSecurityError) as raised:
        ac.activate_storage_security(factory, _keys(), fault_injector=fail)

    _assert_code(raised, ac.SecurityErrorCode.STORAGE_MIGRATION_FAILED)
