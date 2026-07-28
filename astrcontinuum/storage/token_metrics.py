"""Encrypted, content-free Token metric sidecar storage."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import NoReturn

from .crypto import SecureCodec, SecurityErrorCode, StorageSecurityError

_TABLE = "token_metrics"
_ENVELOPE_COLUMN = "metric_envelope"
_SCHEMA_VERSION = 1


class ArtifactKind(str, Enum):
    """Durable artifact namespaces supported by the metric sidecar."""

    EVENT = "EVENT"
    CAPSULE = "CAPSULE"
    SNAPSHOT = "SNAPSHOT"


def _required_identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _non_negative_count(value: object, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token_count must be a non-negative integer or None")
    return value


@dataclass(frozen=True, slots=True)
class TokenMetric:
    """One exact derived count for an immutable artifact/profile identity."""

    artifact_kind: ArtifactKind
    artifact_id: str
    tokenizer_profile_id: str
    token_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_kind, ArtifactKind):
            raise TypeError("artifact_kind must be an ArtifactKind")
        _required_identifier(self.artifact_id, field_name="artifact_id")
        _required_identifier(
            self.tokenizer_profile_id,
            field_name="tokenizer_profile_id",
        )
        _non_negative_count(self.token_count)


@dataclass(frozen=True, slots=True)
class CanonicalMetricObservation:
    """One optional canonical count captured without retaining source content."""

    tokenizer_profile_id: str
    token_count: int | None

    def __post_init__(self) -> None:
        _required_identifier(
            self.tokenizer_profile_id,
            field_name="tokenizer_profile_id",
        )
        _non_negative_count(self.token_count, optional=True)


class TokenMetricConflict(RuntimeError):
    """Report an immutable metric disagreement without artifact details."""

    __slots__ = ()
    code = "TOKEN_METRIC_CONFLICT"

    def __init__(self) -> None:
        super().__init__(self.code)


def _metric_record_key(
    artifact_kind: ArtifactKind,
    artifact_id: str,
    tokenizer_profile_id: str,
) -> str:
    """Return the canonical AAD record key shared by storage maintenance."""

    if not isinstance(artifact_kind, ArtifactKind):
        raise TypeError("artifact_kind must be an ArtifactKind")
    _required_identifier(artifact_id, field_name="artifact_id")
    _required_identifier(
        tokenizer_profile_id,
        field_name="tokenizer_profile_id",
    )
    return json.dumps(
        [
            "token-metric-v1",
            artifact_kind.value,
            artifact_id,
            tokenizer_profile_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _metric_plaintext(token_count: int) -> str:
    return json.dumps(
        {
            "schema_version": _SCHEMA_VERSION,
            "token_count": token_count,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _invalid_row() -> NoReturn:
    raise StorageSecurityError(SecurityErrorCode.STORAGE_ENVELOPE_INVALID) from None


def _decode_metric_plaintext(plaintext: str) -> int:
    if not isinstance(plaintext, str):
        _invalid_row()
    try:
        payload = json.loads(plaintext)
    except (json.JSONDecodeError, TypeError, ValueError):
        _invalid_row()
    if (
        type(payload) is not dict
        or list(payload) != ["schema_version", "token_count"]
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != _SCHEMA_VERSION
        or type(payload["token_count"]) is not int
        or payload["token_count"] < 0
    ):
        _invalid_row()
    token_count = payload["token_count"]
    if plaintext != _metric_plaintext(token_count):
        _invalid_row()
    return token_count


def _normalize_datetime(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("created_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_created_at_text(value: object) -> None:
    if not isinstance(value, str):
        _invalid_row()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        normalized = _normalize_datetime(parsed)
    except (TypeError, ValueError):
        _invalid_row()
    if normalized != value:
        _invalid_row()


def _connection(value: object) -> sqlite3.Connection:
    if not isinstance(value, sqlite3.Connection):
        raise TypeError("connection must be a sqlite3.Connection")
    return value


class TokenMetricStore:
    """Apply encrypted logical CAS operations on a caller-owned connection."""

    __slots__ = ("_codec",)

    def __init__(self, codec: SecureCodec) -> None:
        if not isinstance(codec, SecureCodec):
            raise TypeError("codec must be a SecureCodec")
        self._codec = codec

    def put_in_transaction(
        self,
        connection: sqlite3.Connection,
        metric: TokenMetric,
        *,
        created_at: datetime,
    ) -> TokenMetric:
        """Insert one metric or accept only an exact decrypted replay."""

        opened = _connection(connection)
        if not isinstance(metric, TokenMetric):
            raise TypeError("metric must be a TokenMetric")
        created_at_text = _normalize_datetime(created_at)
        existing = self.get_in_transaction(
            opened,
            artifact_kind=metric.artifact_kind,
            artifact_id=metric.artifact_id,
            tokenizer_profile_id=metric.tokenizer_profile_id,
        )
        if existing is not None:
            if existing == metric:
                return existing
            raise TokenMetricConflict

        record_key = _metric_record_key(
            metric.artifact_kind,
            metric.artifact_id,
            metric.tokenizer_profile_id,
        )
        envelope = self._codec.encrypt_text(
            _TABLE,
            _ENVELOPE_COLUMN,
            record_key,
            _metric_plaintext(metric.token_count),
        )
        opened.execute(
            """
            INSERT INTO token_metrics (
                artifact_kind,
                artifact_id,
                tokenizer_profile_id,
                metric_envelope,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                metric.artifact_kind.value,
                metric.artifact_id,
                metric.tokenizer_profile_id,
                envelope,
                created_at_text,
            ),
        )
        return metric

    def get_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        artifact_kind: ArtifactKind,
        artifact_id: str,
        tokenizer_profile_id: str,
    ) -> TokenMetric | None:
        """Authenticate and return one exact metric from the caller's view."""

        opened = _connection(connection)
        if not isinstance(artifact_kind, ArtifactKind):
            raise TypeError("artifact_kind must be an ArtifactKind")
        _required_identifier(artifact_id, field_name="artifact_id")
        _required_identifier(
            tokenizer_profile_id,
            field_name="tokenizer_profile_id",
        )
        row = opened.execute(
            """
            SELECT
                artifact_kind,
                artifact_id,
                tokenizer_profile_id,
                metric_envelope,
                created_at
            FROM token_metrics
            WHERE artifact_kind = ?
              AND artifact_id = ?
              AND tokenizer_profile_id = ?
            """,
            (artifact_kind.value, artifact_id, tokenizer_profile_id),
        ).fetchone()
        if row is None:
            return None

        stored_kind_raw, stored_id, stored_profile, envelope, created_at = row
        if (
            not isinstance(stored_kind_raw, str)
            or not isinstance(stored_id, str)
            or not isinstance(stored_profile, str)
            or not isinstance(envelope, str)
        ):
            _invalid_row()
        try:
            stored_kind = ArtifactKind(stored_kind_raw)
            _required_identifier(stored_id, field_name="artifact_id")
            _required_identifier(
                stored_profile,
                field_name="tokenizer_profile_id",
            )
        except (TypeError, ValueError):
            _invalid_row()
        if (
            stored_kind is not artifact_kind
            or stored_id != artifact_id
            or stored_profile != tokenizer_profile_id
        ):
            _invalid_row()
        _validate_created_at_text(created_at)
        plaintext = self._codec.decrypt_text(
            _TABLE,
            _ENVELOPE_COLUMN,
            _metric_record_key(stored_kind, stored_id, stored_profile),
            envelope,
        )
        return TokenMetric(
            artifact_kind=stored_kind,
            artifact_id=stored_id,
            tokenizer_profile_id=stored_profile,
            token_count=_decode_metric_plaintext(plaintext),
        )
