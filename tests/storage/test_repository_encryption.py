from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import astrcontinuum as ac
from astrcontinuum.storage import (
    ArtifactKind,
    CanonicalMetricObservation,
    TokenMetricStore,
)

TEST_KEY = bytes(range(32))
NEW_KEY = bytes(range(32, 64))
NOW = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
PLAINTEXT_MARKER = "repository-plaintext-6b4e3f"
CANONICAL_PROFILE_ID = "canonical-o200k-v1"
COMPATIBILITY_COUNT = 246_813_579
CANONICAL_COUNT = 918_273_645
CANONICAL_METRIC_PLAINTEXT = b'{"schema_version":1,"token_count":918273645}'


def _keys(
    active: bytes = TEST_KEY,
    *,
    previous: bytes | None = None,
) -> ac.ResolvedKeyMaterial:
    return ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(active),
        previous=None if previous is None else ac.KeyMaterial.from_raw(previous),
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )


def _active_factory(
    data_dir: Path,
) -> tuple[ac.SQLiteConnectionFactory, ac.StorageSecurityActivation]:
    factory = ac.SQLiteConnectionFactory(data_dir, busy_timeout_ms=5_000)
    activation = ac.activate_storage_security(factory, _keys())
    return factory, activation


def _session_key() -> ac.SessionKey:
    return ac.SessionKey(
        platform_instance_id=f"{PLAINTEXT_MARKER}-platform",
        message_type="friend_message",
        session_id=f"{PLAINTEXT_MARKER}-session",
        group_id=None,
        user_id=f"{PLAINTEXT_MARKER}-user",
        conversation_id=f"{PLAINTEXT_MARKER}-conversation",
        persona_id=None,
    )


def _canonical(token_count: int | None = CANONICAL_COUNT) -> CanonicalMetricObservation:
    return CanonicalMetricObservation(
        tokenizer_profile_id=CANONICAL_PROFILE_ID,
        token_count=token_count,
    )


def test_repository_requires_an_explicit_secure_codec(tmp_path: Path) -> None:
    factory, _ = _active_factory(tmp_path)

    with pytest.raises(TypeError):
        ac.SQLiteRepository(factory)


def test_capture_round_trips_all_event_mappings_with_only_ciphertext_in_sql(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    key = _session_key()
    events = (
        store.capture_user_event(
            event_id="event-user",
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-user-message",
            idempotency_key="request-user",
            token_count=3,
            canonical=_canonical(31),
            created_at=NOW,
        ),
        store.capture_assistant_event(
            event_id="event-assistant",
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-assistant-message",
            idempotency_key="request-assistant",
            token_count=4,
            canonical=_canonical(41),
            created_at=NOW,
        ),
        store.capture_tool_event(
            event_id="event-tool-call",
            session_key=key,
            event_type=ac.EventType.TOOL_CALL,
            content=f"{PLAINTEXT_MARKER}-tool-call",
            idempotency_key="request-tool-call",
            token_count=5,
            canonical=_canonical(51),
            created_at=NOW,
        ),
        store.capture_tool_event(
            event_id="event-tool-result",
            session_key=key,
            event_type=ac.EventType.TOOL_RESULT,
            content=f"{PLAINTEXT_MARKER}-tool-result",
            idempotency_key="request-tool-result",
            token_count=6,
            canonical=_canonical(61),
            created_at=NOW,
        ),
    )

    view = store.read_request_view(key)

    assert view.delta == events
    assert tuple(item.sequence for item in view.delta) == (1, 2, 3, 4)
    with factory.connection(read_only=True) as connection:
        session = connection.execute("SELECT * FROM sessions").fetchone()
        assert session is not None
        assert str(session["canonical_session_key_json"]) != key.canonical_json()
        assert "$astrcontinuum_encrypted" in str(session["canonical_session_key_json"])
        for column in (
            "platform_instance_id",
            "message_type",
            "session_id",
            "user_id",
            "conversation_id",
        ):
            assert str(session[column]).startswith(f"acenc:v1:{activation.key_id}:")
            assert getattr(key, column) not in str(session[column])
        assert session["group_id"] is None
        assert session["persona_id"] is None

        rows = connection.execute(
            "SELECT event_id, content FROM journal_events ORDER BY sequence"
        ).fetchall()
        assert [str(row["event_id"]) for row in rows] == [item.event_id for item in events]
        assert all(str(row["content"]).startswith(f"acenc:v1:{activation.key_id}:") for row in rows)
        assert all(
            event.content not in str(row["content"])
            for event, row in zip(events, rows, strict=True)
        )

    marker = PLAINTEXT_MARKER.encode()
    for path in (
        factory.database_path,
        Path(f"{factory.database_path}-wal"),
        Path(f"{factory.database_path}-shm"),
    ):
        if path.exists():
            assert marker not in path.read_bytes()


def test_cross_row_ciphertext_swap_fails_authenticated_read_without_echo(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    key = _session_key()
    for index in (1, 2):
        store.capture_user_event(
            event_id=f"event-{index}",
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-message-{index}",
            idempotency_key=f"request-{index}",
            token_count=3,
            canonical=_canonical(30 + index),
            created_at=NOW,
        )
    with factory.transaction(immediate=True) as connection:
        connection.execute("DROP TRIGGER journal_events_immutable_update")
        first = connection.execute(
            "SELECT content FROM journal_events WHERE event_id = 'event-1'"
        ).fetchone()["content"]
        second = connection.execute(
            "SELECT content FROM journal_events WHERE event_id = 'event-2'"
        ).fetchone()["content"]
        connection.execute(
            "UPDATE journal_events SET content = ? WHERE event_id = 'event-1'",
            (second,),
        )
        connection.execute(
            "UPDATE journal_events SET content = ? WHERE event_id = 'event-2'",
            (first,),
        )
        connection.execute(
            """
            CREATE TRIGGER journal_events_immutable_update
            BEFORE UPDATE ON journal_events
            BEGIN
                SELECT RAISE(ABORT, 'journal_events rows are immutable');
            END
            """
        )

    with pytest.raises(ac.StorageSecurityError) as raised:
        store.read_request_view(key)

    assert raised.value.code is ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED
    assert PLAINTEXT_MARKER not in repr(raised.value)


def test_compaction_rejects_cross_session_base_snapshot_relationship(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    first_key = _session_key()
    second_key = first_key.model_copy(
        update={
            "session_id": f"{PLAINTEXT_MARKER}-second-session",
            "conversation_id": f"{PLAINTEXT_MARKER}-second-conversation",
        }
    )
    for event_id, key in (
        ("event-first-session", first_key),
        ("event-second-session", second_key),
    ):
        store.capture_user_event(
            event_id=event_id,
            session_key=key,
            content=f"{PLAINTEXT_MARKER}-{event_id}",
            idempotency_key=f"request-{event_id}",
            token_count=3,
            canonical=_canonical(),
            created_at=NOW,
        )

    snapshot_id = "snapshot-first-session"
    timestamp = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    with factory.transaction(immediate=True) as connection:
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
            ) VALUES (?, ?, NULL, 1, 1, ?, ?, ?, ?, 'COMMITTED', ?, ?)
            """,
            (
                snapshot_id,
                first_key.session_key_hash,
                activation.codec.encrypt_array_json(
                    "snapshots",
                    "exact_anchor_ids_json",
                    snapshot_id,
                    "[]",
                ),
                activation.codec.encrypt_text(
                    "snapshots",
                    "rendered_context",
                    snapshot_id,
                    f"{PLAINTEXT_MARKER}-foreign-snapshot",
                ),
                activation.codec.encrypt_non_negative_int(
                    "snapshots",
                    "token_cost",
                    snapshot_id,
                    0,
                ),
                activation.codec.encrypt_object_json(
                    "snapshots",
                    "audit_outcome",
                    snapshot_id,
                    ('{"mechanical_passed":true,"semantic_status":"NOT_RUN","failure_codes":[]}'),
                ),
                timestamp,
                timestamp,
            ),
        )

    job = store.raise_compaction_intent(
        job_id="job-second-session",
        session_key=second_key,
        target_high_water_mark=1,
        now=NOW,
    )
    assert job is not None
    leased = store.claim_job(
        worker_id="worker-cross-session",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert leased is not None
    with factory.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE compaction_jobs
            SET base_snapshot_id = ?, base_pointer_version = 1
            WHERE job_id = ?
            """,
            (snapshot_id, job.job_id),
        )
    compiling = store.transition_job(
        job_id=leased.job_id,
        owner="worker-cross-session",
        lease_epoch=leased.lease_epoch,
        to_state=ac.CompactionJobState.COMPILING,
        now=NOW,
    )

    with pytest.raises(ac.RepositoryInvariantError, match="different durable session"):
        store.read_compaction_view(
            job_id=compiling.job_id,
            owner="worker-cross-session",
            lease_epoch=compiling.lease_epoch,
            now=NOW,
        )


def test_metric_and_compatibility_counts_never_appear_in_db_wal_or_dump(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    event = store.capture_user_event(
        event_id="event-sensitive-counts",
        session_key=_session_key(),
        content=f"{PLAINTEXT_MARKER}-sensitive-counts",
        idempotency_key="request-sensitive-counts",
        token_count=COMPATIBILITY_COUNT,
        canonical=_canonical(),
        created_at=NOW,
    )

    assert event.token_count == COMPATIBILITY_COUNT
    with factory.connection(read_only=True) as connection:
        row = connection.execute(
            """
            SELECT token_count_envelope
            FROM journal_events
            WHERE event_id = ?
            """,
            (event.event_id,),
        ).fetchone()
        metric = connection.execute(
            """
            SELECT metric_envelope
            FROM token_metrics
            WHERE artifact_kind = 'EVENT'
              AND artifact_id = ?
              AND tokenizer_profile_id = ?
            """,
            (event.event_id, CANONICAL_PROFILE_ID),
        ).fetchone()
        assert str(row["token_count_envelope"]).startswith(f"acenc:v1:{activation.key_id}:")
        assert str(metric["metric_envelope"]).startswith(f"acenc:v1:{activation.key_id}:")
        dump = "\n".join(connection.iterdump()).encode()

    forbidden = (
        str(COMPATIBILITY_COUNT).encode(),
        CANONICAL_METRIC_PLAINTEXT,
    )
    for plaintext in forbidden:
        assert plaintext not in dump
        for path in (
            factory.database_path,
            Path(f"{factory.database_path}-wal"),
            Path(f"{factory.database_path}-shm"),
        ):
            if path.exists():
                assert plaintext not in path.read_bytes()


def test_rotation_rekeys_metric_envelopes_and_preserves_intents(
    tmp_path: Path,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    event = store.capture_user_event(
        event_id="event-rekey-metric",
        session_key=_session_key(),
        content=f"{PLAINTEXT_MARKER}-rekey-metric",
        idempotency_key="request-rekey-metric",
        token_count=COMPATIBILITY_COUNT,
        canonical=_canonical(),
        created_at=NOW,
    )
    store.capture_assistant_event(
        event_id="event-rekey-intent",
        session_key=_session_key(),
        content=f"{PLAINTEXT_MARKER}-rekey-intent",
        idempotency_key="request-rekey-intent",
        token_count=7,
        canonical=_canonical(None),
        created_at=NOW,
    )
    with factory.connection(read_only=True) as connection:
        before_metric = str(
            connection.execute(
                """
                SELECT metric_envelope
                FROM token_metrics
                WHERE artifact_id = ?
                """,
                (event.event_id,),
            ).fetchone()["metric_envelope"]
        )
        before_intent = tuple(
            connection.execute(
                """
                SELECT session_key_hash, artifact_kind, artifact_id,
                       tokenizer_profile_id, created_at
                FROM token_metric_backfill_intents
                """
            ).fetchone()
        )

    rotated = ac.activate_storage_security(
        factory,
        _keys(NEW_KEY, previous=TEST_KEY),
    )

    assert rotated.rekeyed is True
    with factory.connection(read_only=True) as connection:
        after_metric = str(
            connection.execute(
                """
                SELECT metric_envelope
                FROM token_metrics
                WHERE artifact_id = ?
                """,
                (event.event_id,),
            ).fetchone()["metric_envelope"]
        )
        after_intent = tuple(
            connection.execute(
                """
                SELECT session_key_hash, artifact_kind, artifact_id,
                       tokenizer_profile_id, created_at
                FROM token_metric_backfill_intents
                """
            ).fetchone()
        )
        metric = TokenMetricStore(rotated.codec).get_in_transaction(
            connection,
            artifact_kind=ArtifactKind.EVENT,
            artifact_id=event.event_id,
            tokenizer_profile_id=CANONICAL_PROFILE_ID,
        )

    assert after_metric != before_metric
    assert after_metric.startswith(f"acenc:v1:{rotated.key_id}:")
    assert after_intent == before_intent
    assert metric is not None
    assert metric.token_count == CANONICAL_COUNT
    assert (
        ac.SQLiteRepository(factory, codec=rotated.codec)
        .read_request_view(_session_key())
        .delta[0]
        .token_count
        == COMPATIBILITY_COUNT
    )


@pytest.mark.parametrize(
    ("statement", "value"),
    (
        ("UPDATE token_metrics SET artifact_kind = ?", "BROKEN"),
        ("UPDATE token_metrics SET artifact_id = ?", "   "),
        ("UPDATE token_metrics SET tokenizer_profile_id = ?", "   "),
    ),
)
def test_rotation_rejects_invalid_metric_identity_with_content_free_code(
    tmp_path: Path,
    statement: str,
    value: str,
) -> None:
    factory, activation = _active_factory(tmp_path)
    store = ac.SQLiteRepository(factory, codec=activation.codec)
    store.capture_user_event(
        event_id="event-invalid-metric-identity",
        session_key=_session_key(),
        content=f"{PLAINTEXT_MARKER}-invalid-metric-identity",
        idempotency_key="request-invalid-metric-identity",
        token_count=3,
        canonical=_canonical(),
        created_at=NOW,
    )
    with factory.connection() as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(statement, (value,))

    with pytest.raises(ac.StorageSecurityError) as caught:
        ac.activate_storage_security(
            factory,
            _keys(NEW_KEY, previous=TEST_KEY),
        )

    assert caught.value.code is ac.SecurityErrorCode.STORAGE_REKEY_FAILED
    assert PLAINTEXT_MARKER not in repr(caught.value)
    with factory.connection(read_only=True) as connection:
        security = connection.execute(
            "SELECT active_key_id, state FROM storage_security"
        ).fetchone()
        assert tuple(security) == (activation.key_id, "ACTIVE")
