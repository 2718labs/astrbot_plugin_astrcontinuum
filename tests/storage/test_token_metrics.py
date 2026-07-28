from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import astrcontinuum as ac
from astrcontinuum.storage import (
    ArtifactKind,
    CanonicalMetricObservation,
    TokenMetric,
    TokenMetricConflict,
    TokenMetricStore,
)
from astrcontinuum.storage.token_metrics import _metric_record_key

NOW = datetime(2026, 7, 28, 8, 30, tzinfo=timezone.utc)
ACTIVE_KEY = bytes(range(32))
WRONG_KEY = bytes(reversed(range(32)))
PROFILE_ID = "canonical-o200k-v1"


@pytest.fixture
def active_storage(
    tmp_path: Path,
) -> tuple[ac.SQLiteConnectionFactory, ac.SecureCodec]:
    factory = ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=5_000)
    keys = ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(ACTIVE_KEY),
        previous=None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )
    activation = ac.activate_storage_security(factory, keys)
    return factory, activation.codec


@pytest.fixture
def connection(
    active_storage: tuple[ac.SQLiteConnectionFactory, ac.SecureCodec],
) -> Iterator[sqlite3.Connection]:
    factory, _ = active_storage
    with factory.connection() as opened:
        opened.execute("BEGIN IMMEDIATE")
        yield opened
        opened.rollback()


@pytest.fixture
def store(
    active_storage: tuple[ac.SQLiteConnectionFactory, ac.SecureCodec],
) -> TokenMetricStore:
    _, codec = active_storage
    return TokenMetricStore(codec)


def test_metric_contracts_are_public_and_frozen() -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-1", PROFILE_ID, 17)
    observation = CanonicalMetricObservation(PROFILE_ID, 17)

    assert metric.artifact_kind is ArtifactKind.EVENT
    assert observation.token_count == 17
    with pytest.raises(AttributeError):
        metric.token_count = 18  # type: ignore[misc]


def test_metric_store_inserts_gets_and_preserves_the_caller_transaction(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-1", PROFILE_ID, 17)

    assert (
        store.get_in_transaction(
            connection,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )
        is None
    )
    assert store.put_in_transaction(connection, metric, created_at=NOW) == metric
    assert connection.in_transaction
    assert (
        store.get_in_transaction(
            connection,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )
        == metric
    )

    row = connection.execute(
        """
        SELECT metric_envelope, created_at
        FROM token_metrics
        WHERE artifact_kind = ? AND artifact_id = ? AND tokenizer_profile_id = ?
        """,
        (metric.artifact_kind.value, metric.artifact_id, metric.tokenizer_profile_id),
    ).fetchone()
    assert row is not None
    assert str(row["metric_envelope"]).startswith("acenc:v1:")
    assert row["created_at"] == "2026-07-28T08:30:00.000000Z"


def test_metric_store_uses_canonical_plaintext_and_record_key(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
    active_storage: tuple[ac.SQLiteConnectionFactory, ac.SecureCodec],
) -> None:
    _, codec = active_storage
    metric = TokenMetric(ArtifactKind.CAPSULE, "胶囊-1", PROFILE_ID, 17)
    store.put_in_transaction(connection, metric, created_at=NOW)
    envelope = connection.execute(
        """
        SELECT metric_envelope
        FROM token_metrics
        WHERE artifact_kind = ? AND artifact_id = ? AND tokenizer_profile_id = ?
        """,
        (metric.artifact_kind.value, metric.artifact_id, metric.tokenizer_profile_id),
    ).fetchone()[0]
    record_key = json.dumps(
        [
            "token-metric-v1",
            metric.artifact_kind.value,
            metric.artifact_id,
            metric.tokenizer_profile_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert codec.decrypt_text(
        "token_metrics",
        "metric_envelope",
        record_key,
        envelope,
    ) == json.dumps(
        {"schema_version": 1, "token_count": 17},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_metric_cas_compares_the_decrypted_logical_value(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-1", PROFILE_ID, 17)
    store.put_in_transaction(connection, metric, created_at=NOW)
    first_envelope = connection.execute("SELECT metric_envelope FROM token_metrics").fetchone()[0]

    assert store.put_in_transaction(connection, metric, created_at=NOW) == metric
    assert (
        connection.execute("SELECT metric_envelope FROM token_metrics").fetchone()[0]
        == first_envelope
    )

    with pytest.raises(TokenMetricConflict) as caught:
        store.put_in_transaction(
            connection,
            replace(metric, token_count=18),
            created_at=NOW,
        )
    assert caught.value.code == "TOKEN_METRIC_CONFLICT"
    assert str(caught.value) == "TOKEN_METRIC_CONFLICT"
    assert metric.artifact_id not in repr(caught.value)
    assert PROFILE_ID not in repr(caught.value)


def test_metric_store_uses_a_fresh_random_ciphertext_for_a_fresh_insert(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-random", PROFILE_ID, 17)
    store.put_in_transaction(connection, metric, created_at=NOW)
    first = connection.execute("SELECT metric_envelope FROM token_metrics").fetchone()[0]
    connection.execute("DELETE FROM token_metrics")

    store.put_in_transaction(connection, metric, created_at=NOW)
    second = connection.execute("SELECT metric_envelope FROM token_metrics").fetchone()[0]

    assert first != second


def test_metric_store_does_not_commit_the_caller_transaction(
    active_storage: tuple[ac.SQLiteConnectionFactory, ac.SecureCodec],
) -> None:
    factory, codec = active_storage
    store = TokenMetricStore(codec)
    metric = TokenMetric(ArtifactKind.EVENT, "evt-rollback", PROFILE_ID, 3)
    with factory.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        store.put_in_transaction(connection, metric, created_at=NOW)
        assert connection.in_transaction
        connection.rollback()

    with factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM token_metrics").fetchone()[0] == 0


def test_metric_read_with_the_wrong_key_fails_without_identity_echo(
    connection: sqlite3.Connection,
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-wrong-key", PROFILE_ID, 17)
    TokenMetricStore(ac.SecureCodec(ACTIVE_KEY)).put_in_transaction(
        connection,
        metric,
        created_at=NOW,
    )

    with pytest.raises(ac.StorageSecurityError) as caught:
        TokenMetricStore(ac.SecureCodec(WRONG_KEY)).get_in_transaction(
            connection,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )

    assert caught.value.code is ac.SecurityErrorCode.STORAGE_KEY_MISMATCH
    assert metric.artifact_id not in repr(caught.value)
    assert PROFILE_ID not in repr(caught.value)


def test_metric_tag_tamper_fails_authenticated_read(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-tamper", PROFILE_ID, 17)
    store.put_in_transaction(connection, metric, created_at=NOW)
    envelope = str(connection.execute("SELECT metric_envelope FROM token_metrics").fetchone()[0])
    parts = envelope.split(":")
    ciphertext = parts[4]
    parts[4] = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
    connection.execute(
        "UPDATE token_metrics SET metric_envelope = ?",
        (":".join(parts),),
    )

    with pytest.raises(ac.StorageSecurityError) as caught:
        store.get_in_transaction(
            connection,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )

    assert caught.value.code is ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED


@pytest.mark.parametrize(
    "other",
    [
        TokenMetric(ArtifactKind.CAPSULE, "evt-swap", PROFILE_ID, 21),
        TokenMetric(ArtifactKind.EVENT, "evt-other", PROFILE_ID, 21),
        TokenMetric(ArtifactKind.EVENT, "evt-swap", "canonical-other-v1", 21),
    ],
    ids=("kind", "artifact-id", "profile"),
)
def test_metric_ciphertext_cannot_be_swapped_across_aad_dimensions(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
    other: TokenMetric,
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-swap", PROFILE_ID, 17)
    store.put_in_transaction(connection, metric, created_at=NOW)
    store.put_in_transaction(connection, other, created_at=NOW)
    other_envelope = connection.execute(
        """
        SELECT metric_envelope
        FROM token_metrics
        WHERE artifact_kind = ? AND artifact_id = ? AND tokenizer_profile_id = ?
        """,
        (other.artifact_kind.value, other.artifact_id, other.tokenizer_profile_id),
    ).fetchone()[0]
    connection.execute(
        """
        UPDATE token_metrics
        SET metric_envelope = ?
        WHERE artifact_kind = ? AND artifact_id = ? AND tokenizer_profile_id = ?
        """,
        (
            other_envelope,
            metric.artifact_kind.value,
            metric.artifact_id,
            metric.tokenizer_profile_id,
        ),
    )

    with pytest.raises(ac.StorageSecurityError) as caught:
        store.get_in_transaction(
            connection,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )

    assert caught.value.code is ac.SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"artifact_kind": "EVENT"}, TypeError),
        ({"artifact_id": ""}, ValueError),
        ({"artifact_id": "   "}, ValueError),
        ({"artifact_id": 1}, TypeError),
        ({"tokenizer_profile_id": ""}, ValueError),
        ({"tokenizer_profile_id": 1}, TypeError),
        ({"token_count": -1}, ValueError),
        ({"token_count": True}, ValueError),
        ({"token_count": 1.5}, ValueError),
    ],
)
def test_token_metric_rejects_invalid_values(
    changes: dict[str, Any],
    error: type[Exception],
) -> None:
    values: dict[str, Any] = {
        "artifact_kind": ArtifactKind.EVENT,
        "artifact_id": "evt-1",
        "tokenizer_profile_id": PROFILE_ID,
        "token_count": 17,
    }
    values.update(changes)

    with pytest.raises(error):
        TokenMetric(**values)


@pytest.mark.parametrize("token_count", [-1, True, 1.5])
def test_canonical_observation_rejects_invalid_counts(token_count: Any) -> None:
    with pytest.raises(ValueError):
        CanonicalMetricObservation(PROFILE_ID, token_count)


@pytest.mark.parametrize(
    ("created_at", "error"),
    [
        ("2026-07-28T08:30:00Z", TypeError),
        (NOW.replace(tzinfo=None), ValueError),
    ],
)
def test_metric_store_rejects_invalid_created_at_without_writing(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
    created_at: Any,
    error: type[Exception],
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-1", PROFILE_ID, 17)

    with pytest.raises(error):
        store.put_in_transaction(connection, metric, created_at=created_at)

    assert connection.execute("SELECT COUNT(*) FROM token_metrics").fetchone()[0] == 0


@pytest.mark.parametrize(
    "plaintext",
    [
        '{"token_count":17,"schema_version":1}',
        '{"schema_version":1, "token_count":17}',
        '{"schema_version":2,"token_count":17}',
        '{"schema_version":1,"token_count":true}',
        '{"schema_version":1,"token_count":-1}',
        '{"schema_version":1,"token_count":"17"}',
        '{"schema_version":1,"token_count":17,"extra":0}',
        '{"schema_version":1,"token_count":17,"token_count":17}',
        "not-json",
    ],
)
def test_metric_store_rejects_noncanonical_or_invalid_decrypted_rows(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
    active_storage: tuple[ac.SQLiteConnectionFactory, ac.SecureCodec],
    plaintext: str,
) -> None:
    _, codec = active_storage
    metric = TokenMetric(ArtifactKind.SNAPSHOT, "snapshot-invalid", PROFILE_ID, 17)
    store.put_in_transaction(connection, metric, created_at=NOW)
    envelope = codec.encrypt_text(
        "token_metrics",
        "metric_envelope",
        _metric_record_key(
            metric.artifact_kind,
            metric.artifact_id,
            metric.tokenizer_profile_id,
        ),
        plaintext,
    )
    connection.execute(
        "UPDATE token_metrics SET metric_envelope = ?",
        (envelope,),
    )

    with pytest.raises(ac.StorageSecurityError) as caught:
        store.get_in_transaction(
            connection,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )

    assert caught.value.code is ac.SecurityErrorCode.STORAGE_ENVELOPE_INVALID


@pytest.mark.parametrize(
    "invalid_timestamp",
    ["not-a-timestamp", "2026-07-28T08:30:00.000000"],
)
def test_metric_store_rejects_an_invalid_durable_timestamp(
    store: TokenMetricStore,
    connection: sqlite3.Connection,
    invalid_timestamp: str,
) -> None:
    metric = TokenMetric(ArtifactKind.EVENT, "evt-bad-time", PROFILE_ID, 17)
    store.put_in_transaction(connection, metric, created_at=NOW)
    connection.execute(
        "UPDATE token_metrics SET created_at = ?",
        (invalid_timestamp,),
    )

    with pytest.raises(ac.StorageSecurityError) as caught:
        store.get_in_transaction(
            connection,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )

    assert caught.value.code is ac.SecurityErrorCode.STORAGE_ENVELOPE_INVALID
