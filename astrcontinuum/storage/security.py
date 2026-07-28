"""Key-source resolution and startup storage-security orchestration."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import stat
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import NoReturn, cast, overload

from ..domain import (
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
from .crypto import (
    ENCRYPTED_JSON_MARKER,
    KEY_BYTES,
    SecureCodec,
    SecurityErrorCode,
    StorageSecurityError,
    encode_key_text,
    key_id_for,
    parse_key_text,
)
from .migrations import (
    MIGRATIONS,
    SECURE_FORMAT_V2_DDL,
    MigrationError,
    SQLiteMigrator,
)
from .sqlite import SQLiteConnectionFactory

ACTIVE_KEY_ENV = "ASTRCONTINUUM_MASTER_KEY"
PREVIOUS_KEY_ENV = "ASTRCONTINUUM_PREVIOUS_KEY"
LOCAL_KEY_FILENAME = "astrcontinuum.key"
MAX_KEY_FILE_BYTES = 128
STORAGE_FORMAT_VERSION = 2
_LEGACY_STORAGE_FORMAT_VERSION = 1
_SECURITY_SINGLETON_ID = 1
_VERIFIER_PLAINTEXT = "astrcontinuum-storage-verifier:v1"
_ENVELOPE_SQL_PREFIX = "acenc:v1:"

StorageFaultInjector = Callable[[str], None]


class KeySource(str, Enum):
    """Supported explicit master-key sources."""

    ENVIRONMENT = "environment"
    FILE = "file"
    LOCAL = "local"


class StorageMaintenanceState(str, Enum):
    """Durable startup-only storage maintenance states."""

    NEEDS_MIGRATION = "NEEDS_MIGRATION"
    NEEDS_REKEY = "NEEDS_REKEY"
    NEEDS_SCRUB = "NEEDS_SCRUB"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True, slots=True, repr=False)
class KeyMaterial:
    """One validated key whose repr never includes raw bytes."""

    raw_key: bytes = field(repr=False)
    key_id: str

    @classmethod
    def from_raw(cls, raw_key: bytes) -> KeyMaterial:
        """Validate and copy one raw 256-bit key."""

        if not isinstance(raw_key, bytes) or len(raw_key) != KEY_BYTES:
            raise StorageSecurityError(SecurityErrorCode.STORAGE_KEY_INVALID) from None
        copied = bytes(raw_key)
        return cls(raw_key=copied, key_id=key_id_for(copied))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(key_id={self.key_id!r})"


@dataclass(frozen=True, slots=True)
class ResolvedKeyMaterial:
    """Active and optional previous key plus non-secret source metadata."""

    active: KeyMaterial
    previous: KeyMaterial | None
    source: KeySource
    local_degraded: bool
    permissions_hardened: bool = True


@dataclass(frozen=True, slots=True)
class GeneratedKeyFile:
    """Content-free result of one exclusive offline key-file creation."""

    path: Path
    key_id: str
    permissions_hardened: bool


@dataclass(frozen=True, slots=True)
class StorageSecurityActivation:
    """Safe result returned only after verifier authentication and scrub."""

    codec: SecureCodec = field(repr=False)
    key_id: str
    state: StorageMaintenanceState
    migrated: bool
    rekeyed: bool
    scrubbed: bool


def _raise(code: SecurityErrorCode) -> NoReturn:
    raise StorageSecurityError(code) from None


def _material(key_text: object, *, missing_is_error: bool) -> KeyMaterial | None:
    if key_text is None or key_text == "":
        if missing_is_error:
            _raise(SecurityErrorCode.STORAGE_KEY_MISSING)
        return None
    return KeyMaterial.from_raw(parse_key_text(key_text))


def _ensure_distinct(
    active: KeyMaterial,
    previous: KeyMaterial | None,
) -> None:
    if previous is not None and hmac.compare_digest(active.raw_key, previous.raw_key):
        _raise(SecurityErrorCode.STORAGE_KEY_INVALID)


def _resolved_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    try:
        return Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)


def _data_root(data_dir: Path) -> Path:
    try:
        return data_dir.expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)


def _is_inside(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _read_key_path(path: Path, *, external_root: Path | None) -> KeyMaterial:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    if external_root is not None and _is_inside(resolved, external_root):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    try:
        with resolved.open("rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_KEY_FILE_BYTES:
                _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
            payload = handle.read(MAX_KEY_FILE_BYTES + 1)
    except StorageSecurityError:
        raise
    except OSError:
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    if len(payload) > MAX_KEY_FILE_BYTES:
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        _raise(SecurityErrorCode.STORAGE_KEY_INVALID)
    text = text.removesuffix("\n")
    if "\n" in text or "\r" in text:
        _raise(SecurityErrorCode.STORAGE_KEY_INVALID)
    return KeyMaterial.from_raw(parse_key_text(text))


def _harden_local_file(path: Path) -> bool:
    try:
        os.chmod(path, 0o600)
        if os.name == "nt":
            return False
        return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    except OSError:
        return False


def _exclusive_write(path: Path, payload: bytes, *, failure_code: SecurityErrorCode) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _raise(failure_code)
    return _harden_local_file(path)


def _create_local_key(path: Path) -> tuple[KeyMaterial, bool]:
    raw_key = secrets.token_bytes(KEY_BYTES)
    payload = (encode_key_text(raw_key) + "\n").encode("ascii")
    try:
        hardened = _exclusive_write(
            path,
            payload,
            failure_code=SecurityErrorCode.STORAGE_LOCAL_KEY_CREATE_FAILED,
        )
    except FileExistsError:
        return _read_key_path(path, external_root=None), _harden_local_file(path)
    return KeyMaterial.from_raw(raw_key), hardened


def _environment_keys(
    environ: Mapping[str, str],
) -> tuple[KeyMaterial, KeyMaterial | None]:
    active = _material(environ.get(ACTIVE_KEY_ENV), missing_is_error=True)
    if active is None:
        raise AssertionError("required key parser returned None")
    previous = _material(environ.get(PREVIOUS_KEY_ENV), missing_is_error=False)
    _ensure_distinct(active, previous)
    return active, previous


def _file_keys(
    config: Mapping[str, object],
    data_root: Path,
) -> tuple[KeyMaterial, KeyMaterial | None]:
    active_value = config.get("encryption_key_file")
    if active_value is None or active_value == "":
        _raise(SecurityErrorCode.STORAGE_KEY_MISSING)
    active_path = _resolved_path(active_value)
    active = _read_key_path(active_path, external_root=data_root)

    previous_value = config.get("encryption_previous_key_file")
    previous = None
    if previous_value is not None and previous_value != "":
        previous_path = _resolved_path(previous_value)
        previous = _read_key_path(previous_path, external_root=data_root)
    _ensure_distinct(active, previous)
    return active, previous


def _local_keys(data_root: Path) -> tuple[KeyMaterial, bool]:
    try:
        data_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        _raise(SecurityErrorCode.STORAGE_LOCAL_KEY_CREATE_FAILED)
    path = data_root / LOCAL_KEY_FILENAME
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return _create_local_key(path)
    except OSError:
        _raise(SecurityErrorCode.STORAGE_LOCAL_KEY_CREATE_FAILED)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    return _read_key_path(path, external_root=None), _harden_local_file(path)


def resolve_key_material(
    config: Mapping[str, object],
    data_dir: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> ResolvedKeyMaterial:
    """Resolve exactly the configured source without fallback."""

    if not isinstance(config, Mapping):
        _raise(SecurityErrorCode.STORAGE_KEY_INVALID)
    source_value = config.get("encryption_key_source", KeySource.ENVIRONMENT.value)
    try:
        source = KeySource(source_value)
    except (TypeError, ValueError):
        _raise(SecurityErrorCode.STORAGE_KEY_INVALID)
    root = _data_root(Path(data_dir))

    if source is KeySource.ENVIRONMENT:
        active, previous = _environment_keys(os.environ if environ is None else environ)
        return ResolvedKeyMaterial(
            active=active,
            previous=previous,
            source=source,
            local_degraded=False,
        )
    if source is KeySource.FILE:
        active, previous = _file_keys(config, root)
        return ResolvedKeyMaterial(
            active=active,
            previous=previous,
            source=source,
            local_degraded=False,
        )

    active, hardened = _local_keys(root)
    return ResolvedKeyMaterial(
        active=active,
        previous=None,
        source=source,
        local_degraded=True,
        permissions_hardened=hardened,
    )


def generate_key_file(
    output: str | Path,
    *,
    data_dir: str | Path | None = None,
) -> GeneratedKeyFile:
    """Generate one external key file with exclusive creation."""

    try:
        requested = Path(output).expanduser()
        parent = requested.parent.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    if not parent.is_dir() or not requested.name:
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    path = parent / requested.name
    if path.exists() or path.is_symlink():
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    if data_dir is not None and _is_inside(path, _data_root(Path(data_dir))):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)

    raw_key = secrets.token_bytes(KEY_BYTES)
    try:
        hardened = _exclusive_write(
            path,
            (encode_key_text(raw_key) + "\n").encode("ascii"),
            failure_code=SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE,
        )
    except FileExistsError:
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    return GeneratedKeyFile(
        path=path,
        key_id=key_id_for(raw_key),
        permissions_hardened=hardened,
    )


def fingerprint_key_file(key_file: str | Path) -> str:
    """Validate an existing key file and return only its short key id."""

    try:
        path = Path(key_file)
    except (TypeError, ValueError):
        _raise(SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    return _read_key_path(path, external_root=None).key_id


_SECURE_SESSIONS_TABLE = "_astrcontinuum_secure_sessions"
_SECURE_SNAPSHOTS_TABLE = "_astrcontinuum_secure_snapshots"
_PROTECTED_TABLES = (
    "sessions",
    "journal_events",
    "capsules",
    "snapshots",
)
_COUNTED_TABLES = (
    "sessions",
    "journal_events",
    "capsules",
    "snapshots",
    "snapshot_capsules",
    "active_snapshots",
    "compaction_jobs",
)
_SESSION_IDENTITY_COLUMNS = (
    "platform_instance_id",
    "message_type",
    "session_id",
    "group_id",
    "user_id",
    "conversation_id",
    "persona_id",
)
_IMMUTABLE_UPDATE_TRIGGERS = {
    "journal_events": "journal_events_immutable_update",
    "capsules": "capsules_immutable_update",
}

_SECURE_SESSIONS_SQL = f"""
CREATE TABLE {_SECURE_SESSIONS_TABLE} (
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
            AND json_type(
                canonical_session_key_json,
                '$."{ENCRYPTED_JSON_MARKER}"'
            ) = 'text'
            AND json_remove(
                canonical_session_key_json,
                '$."{ENCRYPTED_JSON_MARKER}"'
            ) = '{{}}'
        ),
    platform_instance_id TEXT NOT NULL
        CHECK (substr(platform_instance_id, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    message_type TEXT NOT NULL
        CHECK (substr(message_type, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    session_id TEXT NOT NULL CHECK (substr(session_id, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    group_id TEXT NULL
        CHECK (group_id IS NULL OR substr(group_id, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    user_id TEXT NOT NULL CHECK (substr(user_id, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    conversation_id TEXT NOT NULL
        CHECK (substr(conversation_id, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    persona_id TEXT NULL
        CHECK (persona_id IS NULL OR substr(persona_id, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    next_event_sequence INTEGER NOT NULL CHECK (next_event_sequence >= 1),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
)
""".strip()

_SECURE_SNAPSHOTS_SQL = f"""
CREATE TABLE {_SECURE_SNAPSHOTS_TABLE} (
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
            AND json_extract(exact_anchor_ids_json, '$[0]') = '{ENCRYPTED_JSON_MARKER}'
            AND json_type(exact_anchor_ids_json, '$[1]') = 'text'
        ),
    rendered_context TEXT NOT NULL
        CHECK (substr(rendered_context, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    token_cost INTEGER NOT NULL CHECK (token_cost >= 0),
    audit_outcome TEXT NOT NULL
        CHECK (
            json_valid(audit_outcome)
            AND json_type(audit_outcome) = 'object'
            AND json_type(audit_outcome, '$."{ENCRYPTED_JSON_MARKER}"') = 'text'
            AND json_remove(audit_outcome, '$."{ENCRYPTED_JSON_MARKER}"') = '{{}}'
        ),
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state = 'COMMITTED'),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    committed_at TEXT NOT NULL CHECK (length(trim(committed_at)) > 0),
    UNIQUE (session_key_hash, covered_event_end),
    FOREIGN KEY (session_key_hash)
        REFERENCES {_SECURE_SESSIONS_TABLE}(session_key_hash),
    FOREIGN KEY (base_snapshot_id)
        REFERENCES {_SECURE_SNAPSHOTS_TABLE}(snapshot_id)
)
""".strip()

_STORAGE_SECURITY_SQL = f"""
CREATE TABLE storage_security (
    singleton_id INTEGER NOT NULL PRIMARY KEY CHECK (singleton_id = {_SECURITY_SINGLETON_ID}),
    format_version INTEGER NOT NULL
        CHECK (format_version = {_LEGACY_STORAGE_FORMAT_VERSION}),
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
        CHECK (substr(key_verifier, 1, 9) = '{_ENVELOPE_SQL_PREFIX}'),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
)
""".strip()

_SNAPSHOT_TRIGGERS = (
    """
    CREATE TRIGGER snapshots_immutable_update
    BEFORE UPDATE ON snapshots
    BEGIN
        SELECT RAISE(ABORT, 'snapshots rows are immutable');
    END
    """,
    """
    CREATE TRIGGER snapshots_immutable_delete
    BEFORE DELETE ON snapshots
    BEGIN
        SELECT RAISE(ABORT, 'snapshots rows are immutable');
    END
    """,
)
_SECURE_INSERT_TRIGGERS = (
    f"""
    CREATE TRIGGER journal_events_secure_insert
    BEFORE INSERT ON journal_events
    WHEN substr(NEW.content, 1, 9) <> '{_ENVELOPE_SQL_PREFIX}'
    BEGIN
        SELECT RAISE(ABORT, 'journal_events content must be encrypted');
    END
    """,
    f"""
    CREATE TRIGGER capsules_secure_insert
    BEFORE INSERT ON capsules
    WHEN coalesce(
        json_valid(NEW.canonical_capsule_json)
        AND json_type(NEW.canonical_capsule_json) = 'object'
        AND json_type(
            NEW.canonical_capsule_json,
            '$."{ENCRYPTED_JSON_MARKER}"'
        ) = 'text'
        AND json_remove(
            NEW.canonical_capsule_json,
            '$."{ENCRYPTED_JSON_MARKER}"'
        ) = '{{}}'
    , 0) = 0
    BEGIN
        SELECT RAISE(ABORT, 'capsules canonical JSON must be encrypted');
    END
    """,
)


class _LegacyValidationError(ValueError):
    pass


class _InjectedStorageFault(RuntimeError):
    pass


def _inject(
    fault_injector: StorageFaultInjector | None,
    stage: str,
) -> None:
    if fault_injector is None:
        return
    try:
        fault_injector(stage)
    except Exception:  # noqa: BLE001 - sanitize arbitrary test fault callbacks
        raise _InjectedStorageFault from None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_model_json(value: ContextCapsuleEnvelope) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise _LegacyValidationError from None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _LegacyValidationError from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _LegacyValidationError from None
    return parsed.astimezone(timezone.utc)


@overload
def _reject_legacy_sentinel(
    value: object,
    expected: type[dict],
) -> dict[str, object]: ...


@overload
def _reject_legacy_sentinel(
    value: object,
    expected: type[list],
) -> list[object]: ...


def _reject_legacy_sentinel(
    value: object,
    expected: type[dict | list],
) -> dict[str, object] | list[object]:
    if not isinstance(value, str):
        raise _LegacyValidationError from None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise _LegacyValidationError from None
    if type(parsed) is not expected:
        raise _LegacyValidationError from None
    if isinstance(parsed, dict) and ENCRYPTED_JSON_MARKER in parsed:
        raise _LegacyValidationError from None
    if isinstance(parsed, list) and parsed and parsed[0] == ENCRYPTED_JSON_MARKER:
        raise _LegacyValidationError from None
    return cast(dict[str, object] | list[object], parsed)


def _batched_rows(
    connection: sqlite3.Connection,
    table: str,
    primary_key: str,
) -> Iterator[sqlite3.Row]:
    last_key = ""
    while True:
        batch = connection.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE {primary_key} > ?
            ORDER BY {primary_key}
            LIMIT 128
            """,
            (last_key,),
        ).fetchall()
        if not batch:
            return
        yield from batch
        last_key = str(batch[-1][primary_key])


def _validate_legacy_session(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> SessionKey:
    canonical = str(row["canonical_session_key_json"])
    _reject_legacy_sentinel(canonical, dict)
    session = SessionKey.model_validate_json(canonical)
    if (
        session.canonical_json() != canonical
        or session.session_key_hash != row["session_key_hash"]
        or any(getattr(session, column) != row[column] for column in _SESSION_IDENTITY_COLUMNS)
    ):
        raise _LegacyValidationError from None
    max_sequence = connection.execute(
        """
        SELECT coalesce(max(sequence), 0)
        FROM journal_events
        WHERE session_key_hash = ?
        """,
        (session.session_key_hash,),
    ).fetchone()[0]
    if int(row["next_event_sequence"]) != int(max_sequence) + 1:
        raise _LegacyValidationError from None
    return session


def _validate_legacy_rows(connection: sqlite3.Connection) -> dict[str, int]:
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise _LegacyValidationError from None
    for temporary in (_SECURE_SESSIONS_TABLE, _SECURE_SNAPSHOTS_TABLE):
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (temporary,),
        ).fetchone():
            raise _LegacyValidationError from None

    sessions: dict[str, SessionKey] = {}
    for row in _batched_rows(connection, "sessions", "session_key_hash"):
        session = _validate_legacy_session(connection, row)
        sessions[session.session_key_hash] = session

    for row in _batched_rows(connection, "journal_events", "event_id"):
        event_session = sessions.get(str(row["session_key_hash"]))
        if event_session is None:
            raise _LegacyValidationError from None
        EventEnvelope(
            event_id=row["event_id"],
            session_key=event_session,
            sequence=row["sequence"],
            event_type=EventType(row["event_type"]),
            role=EventRole(row["role"]),
            content=row["content"],
            source_hook=SourceHook(row["source_hook"]),
            idempotency_key=row["idempotency_key"],
            token_count=row["token_count"],
            created_at=_parse_datetime(row["created_at"]),
        )

    for row in _batched_rows(connection, "capsules", "capsule_id"):
        canonical = str(row["canonical_capsule_json"])
        _reject_legacy_sentinel(canonical, dict)
        capsule = ContextCapsuleEnvelope.model_validate_json(canonical)
        if (
            _canonical_model_json(capsule) != canonical
            or capsule.capsule_id != row["capsule_id"]
            or capsule.session_key.session_key_hash != row["session_key_hash"]
            or capsule.level.value != row["level"]
            or capsule.covered_event_start != row["covered_event_start"]
            or capsule.covered_event_end != row["covered_event_end"]
            or capsule.token_cost != row["token_cost"]
            or capsule.quality.source_coverage != row["source_coverage"]
            or capsule.created_at != _parse_datetime(row["created_at"])
        ):
            raise _LegacyValidationError from None
        for event_id in capsule.source_event_ids:
            source = connection.execute(
                """
                SELECT session_key_hash
                FROM journal_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            if source is None or source["session_key_hash"] != row["session_key_hash"]:
                raise _LegacyValidationError from None

    for row in _batched_rows(connection, "snapshots", "snapshot_id"):
        snapshot_session = sessions.get(str(row["session_key_hash"]))
        if snapshot_session is None:
            raise _LegacyValidationError from None
        exact_anchor_ids = _reject_legacy_sentinel(
            row["exact_anchor_ids_json"],
            list,
        )
        if any(not isinstance(item, str) for item in exact_anchor_ids):
            raise _LegacyValidationError from None
        exact_anchor_text = cast(list[str], exact_anchor_ids)
        audit_value = _reject_legacy_sentinel(row["audit_outcome"], dict)
        memberships = connection.execute(
            """
            SELECT membership.ordinal, capsule.capsule_id, capsule.session_key_hash
            FROM snapshot_capsules AS membership
            JOIN capsules AS capsule ON capsule.capsule_id = membership.capsule_id
            WHERE membership.snapshot_id = ?
            ORDER BY membership.ordinal
            """,
            (row["snapshot_id"],),
        ).fetchall()
        if any(
            int(item["ordinal"]) != ordinal or item["session_key_hash"] != row["session_key_hash"]
            for ordinal, item in enumerate(memberships)
        ):
            raise _LegacyValidationError from None
        SnapshotEnvelope(
            snapshot_id=row["snapshot_id"],
            session_key=snapshot_session,
            base_snapshot_id=row["base_snapshot_id"],
            covered_event_end=row["covered_event_end"],
            source_high_water_mark=row["source_high_water_mark"],
            capsule_ids=tuple(str(item["capsule_id"]) for item in memberships),
            exact_anchor_ids=tuple(exact_anchor_text),
            rendered_context=row["rendered_context"],
            token_cost=row["token_cost"],
            audit_outcome=SnapshotAuditOutcome.model_validate(audit_value),
            state=SnapshotState(row["lifecycle_state"]),
            created_at=_parse_datetime(row["created_at"]),
            committed_at=_parse_datetime(row["committed_at"]),
        )

    active_mismatch = connection.execute(
        """
        SELECT 1
        FROM active_snapshots AS active
        JOIN snapshots AS snapshot ON snapshot.snapshot_id = active.snapshot_id
        WHERE active.session_key_hash <> snapshot.session_key_hash
        LIMIT 1
        """
    ).fetchone()
    if active_mismatch is not None:
        raise _LegacyValidationError from None

    return {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in _COUNTED_TABLES
    }


def _encrypt_nullable(
    codec: SecureCodec,
    *,
    table: str,
    column: str,
    record_key: str,
    value: object,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _LegacyValidationError from None
    return codec.encrypt_text(table, column, record_key, value)


def _assert_wire(actual: str, expected: object) -> None:
    if not isinstance(expected, str) or actual != expected:
        raise StorageSecurityError(SecurityErrorCode.STORAGE_MIGRATION_FAILED) from None


def _transform_legacy(
    connection: sqlite3.Connection,
    codec: SecureCodec,
    *,
    counts: Mapping[str, int],
    fault_injector: StorageFaultInjector | None,
) -> None:
    connection.execute(_SECURE_SESSIONS_SQL)
    connection.execute(_SECURE_SNAPSHOTS_SQL)
    _inject(fault_injector, "security.after_secure_schema")

    for row in _batched_rows(connection, "sessions", "session_key_hash"):
        record_key = str(row["session_key_hash"])
        canonical = str(row["canonical_session_key_json"])
        protected = {
            column: _encrypt_nullable(
                codec,
                table="sessions",
                column=column,
                record_key=record_key,
                value=row[column],
            )
            for column in _SESSION_IDENTITY_COLUMNS
        }
        canonical_encrypted = codec.encrypt_object_json(
            "sessions",
            "canonical_session_key_json",
            record_key,
            canonical,
        )
        connection.execute(
            f"""
            INSERT INTO {_SECURE_SESSIONS_TABLE} (
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
                record_key,
                canonical_encrypted,
                protected["platform_instance_id"],
                protected["message_type"],
                protected["session_id"],
                protected["group_id"],
                protected["user_id"],
                protected["conversation_id"],
                protected["persona_id"],
                row["next_event_sequence"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        stored = connection.execute(
            f"SELECT * FROM {_SECURE_SESSIONS_TABLE} WHERE session_key_hash = ?",
            (record_key,),
        ).fetchone()
        _assert_wire(
            codec.decrypt_object_json(
                "sessions",
                "canonical_session_key_json",
                record_key,
                stored["canonical_session_key_json"],
            ),
            canonical,
        )
        for column in _SESSION_IDENTITY_COLUMNS:
            if row[column] is None:
                if stored[column] is not None:
                    raise StorageSecurityError(SecurityErrorCode.STORAGE_MIGRATION_FAILED) from None
                continue
            _assert_wire(
                codec.decrypt_text(
                    "sessions",
                    column,
                    record_key,
                    stored[column],
                ),
                row[column],
            )

    for table, trigger in _IMMUTABLE_UPDATE_TRIGGERS.items():
        connection.execute(f"DROP TRIGGER {trigger}")

    for row in _batched_rows(connection, "journal_events", "event_id"):
        record_key = str(row["event_id"])
        plaintext = str(row["content"])
        connection.execute(
            "UPDATE journal_events SET content = ? WHERE event_id = ?",
            (
                codec.encrypt_text(
                    "journal_events",
                    "content",
                    record_key,
                    plaintext,
                ),
                record_key,
            ),
        )
        stored = connection.execute(
            "SELECT content FROM journal_events WHERE event_id = ?",
            (record_key,),
        ).fetchone()
        _assert_wire(
            codec.decrypt_text(
                "journal_events",
                "content",
                record_key,
                stored["content"],
            ),
            plaintext,
        )

    for row in _batched_rows(connection, "capsules", "capsule_id"):
        record_key = str(row["capsule_id"])
        plaintext = str(row["canonical_capsule_json"])
        connection.execute(
            """
            UPDATE capsules
            SET canonical_capsule_json = ?
            WHERE capsule_id = ?
            """,
            (
                codec.encrypt_object_json(
                    "capsules",
                    "canonical_capsule_json",
                    record_key,
                    plaintext,
                ),
                record_key,
            ),
        )
        stored = connection.execute(
            "SELECT canonical_capsule_json FROM capsules WHERE capsule_id = ?",
            (record_key,),
        ).fetchone()
        _assert_wire(
            codec.decrypt_object_json(
                "capsules",
                "canonical_capsule_json",
                record_key,
                stored["canonical_capsule_json"],
            ),
            plaintext,
        )

    for row in _batched_rows(connection, "snapshots", "snapshot_id"):
        record_key = str(row["snapshot_id"])
        exact_anchors = str(row["exact_anchor_ids_json"])
        rendered = str(row["rendered_context"])
        audit = str(row["audit_outcome"])
        connection.execute(
            f"""
            INSERT INTO {_SECURE_SNAPSHOTS_TABLE} (
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
                record_key,
                row["session_key_hash"],
                row["base_snapshot_id"],
                row["covered_event_end"],
                row["source_high_water_mark"],
                codec.encrypt_array_json(
                    "snapshots",
                    "exact_anchor_ids_json",
                    record_key,
                    exact_anchors,
                ),
                codec.encrypt_text(
                    "snapshots",
                    "rendered_context",
                    record_key,
                    rendered,
                ),
                row["token_cost"],
                codec.encrypt_object_json(
                    "snapshots",
                    "audit_outcome",
                    record_key,
                    audit,
                ),
                row["lifecycle_state"],
                row["created_at"],
                row["committed_at"],
            ),
        )
        stored = connection.execute(
            f"SELECT * FROM {_SECURE_SNAPSHOTS_TABLE} WHERE snapshot_id = ?",
            (record_key,),
        ).fetchone()
        _assert_wire(
            codec.decrypt_array_json(
                "snapshots",
                "exact_anchor_ids_json",
                record_key,
                stored["exact_anchor_ids_json"],
            ),
            exact_anchors,
        )
        _assert_wire(
            codec.decrypt_text(
                "snapshots",
                "rendered_context",
                record_key,
                stored["rendered_context"],
            ),
            rendered,
        )
        _assert_wire(
            codec.decrypt_object_json(
                "snapshots",
                "audit_outcome",
                record_key,
                stored["audit_outcome"],
            ),
            audit,
        )

    _inject(fault_injector, "security.after_encrypt")

    connection.execute("DROP TABLE snapshots")
    connection.execute("DROP TABLE sessions")
    connection.execute(f"ALTER TABLE {_SECURE_SESSIONS_TABLE} RENAME TO sessions")
    connection.execute(f"ALTER TABLE {_SECURE_SNAPSHOTS_TABLE} RENAME TO snapshots")
    for table, trigger in _IMMUTABLE_UPDATE_TRIGGERS.items():
        connection.execute(
            f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} rows are immutable');
            END
            """
        )
    for statement in _SNAPSHOT_TRIGGERS:
        connection.execute(statement)
    for statement in _SECURE_INSERT_TRIGGERS:
        connection.execute(statement)

    now = _utc_now()
    verifier = codec.encrypt_text(
        "storage_security",
        "key_verifier",
        str(_SECURITY_SINGLETON_ID),
        _VERIFIER_PLAINTEXT,
    )
    connection.execute(_STORAGE_SECURITY_SQL)
    connection.execute(
        """
        INSERT INTO storage_security (
            singleton_id,
            format_version,
            active_key_id,
            state,
            key_verifier,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _SECURITY_SINGLETON_ID,
            _LEGACY_STORAGE_FORMAT_VERSION,
            codec.key_id,
            StorageMaintenanceState.NEEDS_SCRUB.value,
            verifier,
            now,
            now,
        ),
    )
    _assert_wire(
        codec.decrypt_text(
            "storage_security",
            "key_verifier",
            str(_SECURITY_SINGLETON_ID),
            verifier,
        ),
        _VERIFIER_PLAINTEXT,
    )
    for table, expected in counts.items():
        actual = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        if actual != expected:
            raise StorageSecurityError(SecurityErrorCode.STORAGE_MIGRATION_FAILED) from None
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise StorageSecurityError(SecurityErrorCode.STORAGE_MIGRATION_FAILED) from None
    _inject(fault_injector, "security.after_verify")


def _security_table_exists(factory: SQLiteConnectionFactory) -> bool:
    try:
        with factory.connection(read_only=True) as connection:
            return (
                connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'storage_security'
                    """
                ).fetchone()
                is not None
            )
    except (OSError, RuntimeError, sqlite3.Error):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)


def _read_security_metadata(
    factory: SQLiteConnectionFactory,
) -> tuple[int, str, StorageMaintenanceState, str]:
    try:
        with factory.connection(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT format_version, active_key_id, state, key_verifier
                FROM storage_security
                ORDER BY singleton_id
                """
            ).fetchall()
    except (OSError, RuntimeError, sqlite3.Error):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    if len(rows) != 1:
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    row = rows[0]
    try:
        state = StorageMaintenanceState(row["state"])
        format_version = int(row["format_version"])
        active_key_id = str(row["active_key_id"])
        verifier = str(row["key_verifier"])
    except (TypeError, ValueError):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    if format_version not in (_LEGACY_STORAGE_FORMAT_VERSION, STORAGE_FORMAT_VERSION):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    return format_version, active_key_id, state, verifier


def _authenticate_verifier(codec: SecureCodec, verifier: str) -> None:
    plaintext = codec.decrypt_text(
        "storage_security",
        "key_verifier",
        str(_SECURITY_SINGLETON_ID),
        verifier,
    )
    if not hmac.compare_digest(plaintext, _VERIFIER_PLAINTEXT):
        _raise(SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED)


def _checkpoint_truncate(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is None or int(row[0]) != 0:
        _raise(SecurityErrorCode.STORAGE_SCRUB_FAILED)


def _scrub_storage(
    factory: SQLiteConnectionFactory,
    *,
    fault_injector: StorageFaultInjector | None,
) -> None:
    try:
        with factory.connection() as connection:
            _checkpoint_truncate(connection)
            secure_delete = connection.execute("PRAGMA secure_delete = ON").fetchone()
            if secure_delete is None or int(secure_delete[0]) != 1:
                _raise(SecurityErrorCode.STORAGE_SCRUB_FAILED)
            _inject(fault_injector, "security.before_vacuum")
            connection.execute("VACUUM")
            _inject(fault_injector, "security.after_vacuum")
            _checkpoint_truncate(connection)
        with factory.transaction(immediate=True) as connection:
            updated = connection.execute(
                """
                UPDATE storage_security
                SET state = ?, updated_at = ?
                WHERE singleton_id = ? AND state = ?
                """,
                (
                    StorageMaintenanceState.ACTIVE.value,
                    _utc_now(),
                    _SECURITY_SINGLETON_ID,
                    StorageMaintenanceState.NEEDS_SCRUB.value,
                ),
            )
            if updated.rowcount != 1:
                _raise(SecurityErrorCode.STORAGE_SCRUB_FAILED)
    except StorageSecurityError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
        _raise(SecurityErrorCode.STORAGE_SCRUB_FAILED)


def _rekey_text(
    previous: SecureCodec,
    active: SecureCodec,
    *,
    table: str,
    column: str,
    record_key: str,
    envelope: str,
) -> tuple[str, str]:
    plaintext = previous.decrypt_text(table, column, record_key, envelope)
    return plaintext, active.encrypt_text(table, column, record_key, plaintext)


def _rekey_object(
    previous: SecureCodec,
    active: SecureCodec,
    *,
    table: str,
    column: str,
    record_key: str,
    sentinel: str,
) -> tuple[str, str]:
    plaintext = previous.decrypt_object_json(table, column, record_key, sentinel)
    return (
        plaintext,
        active.encrypt_object_json(table, column, record_key, plaintext),
    )


def _rekey_array(
    previous: SecureCodec,
    active: SecureCodec,
    *,
    table: str,
    column: str,
    record_key: str,
    sentinel: str,
) -> tuple[str, str]:
    plaintext = previous.decrypt_array_json(table, column, record_key, sentinel)
    return (
        plaintext,
        active.encrypt_array_json(table, column, record_key, plaintext),
    )


def _rekey_non_negative_int(
    previous: SecureCodec,
    active: SecureCodec,
    *,
    table: str,
    column: str,
    record_key: str,
    envelope: object,
) -> tuple[int, str]:
    plaintext = previous.decrypt_non_negative_int(
        table,
        column,
        record_key,
        envelope,
    )
    return (
        plaintext,
        active.encrypt_non_negative_int(
            table,
            column,
            record_key,
            plaintext,
        ),
    )


_FORMAT_V2_DDL = dict(SECURE_FORMAT_V2_DDL)


def _execute_format_v2_ddl(
    connection: sqlite3.Connection,
    *names: str,
) -> None:
    for name in names:
        statement = _FORMAT_V2_DDL.get(name)
        if statement is None:
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
        connection.execute(statement)


def _format_one_count(value: object) -> int:
    if type(value) is not int or value < 0:
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    return value


def _rewrite_format_two_sessions(
    connection: sqlite3.Connection,
    *,
    source: SecureCodec,
    target: SecureCodec,
) -> None:
    for row in _batched_rows(connection, "sessions", "session_key_hash"):
        record_key = str(row["session_key_hash"])
        canonical_plaintext, canonical_encrypted = _rekey_object(
            source,
            target,
            table="sessions",
            column="canonical_session_key_json",
            record_key=record_key,
            sentinel=str(row["canonical_session_key_json"]),
        )
        identity_plaintext: dict[str, str | None] = {}
        identity_encrypted: dict[str, str | None] = {}
        for column in _SESSION_IDENTITY_COLUMNS:
            if row[column] is None:
                identity_plaintext[column] = None
                identity_encrypted[column] = None
                continue
            plaintext, encrypted = _rekey_text(
                source,
                target,
                table="sessions",
                column=column,
                record_key=record_key,
                envelope=str(row[column]),
            )
            identity_plaintext[column] = plaintext
            identity_encrypted[column] = encrypted

        connection.execute(
            """
            UPDATE sessions
            SET
                canonical_session_key_json = ?,
                platform_instance_id = ?,
                message_type = ?,
                session_id = ?,
                group_id = ?,
                user_id = ?,
                conversation_id = ?,
                persona_id = ?
            WHERE session_key_hash = ?
            """,
            (
                canonical_encrypted,
                identity_encrypted["platform_instance_id"],
                identity_encrypted["message_type"],
                identity_encrypted["session_id"],
                identity_encrypted["group_id"],
                identity_encrypted["user_id"],
                identity_encrypted["conversation_id"],
                identity_encrypted["persona_id"],
                record_key,
            ),
        )
        stored = connection.execute(
            "SELECT * FROM sessions WHERE session_key_hash = ?",
            (record_key,),
        ).fetchone()
        if stored is None:
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
        _assert_wire(
            target.decrypt_object_json(
                "sessions",
                "canonical_session_key_json",
                record_key,
                stored["canonical_session_key_json"],
            ),
            canonical_plaintext,
        )
        for column in _SESSION_IDENTITY_COLUMNS:
            expected_plaintext = identity_plaintext[column]
            if expected_plaintext is None:
                if stored[column] is not None:
                    _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
                continue
            _assert_wire(
                target.decrypt_text(
                    "sessions",
                    column,
                    record_key,
                    stored[column],
                ),
                expected_plaintext,
            )


def _copy_format_two_journal_events(
    connection: sqlite3.Connection,
    *,
    source: SecureCodec,
    target: SecureCodec,
    fault_injector: StorageFaultInjector | None,
) -> None:
    for row in _batched_rows(connection, "journal_events", "event_id"):
        record_key = str(row["event_id"])
        content = source.decrypt_text(
            "journal_events",
            "content",
            record_key,
            str(row["content"]),
        )
        count = _format_one_count(row["token_count"])
        encrypted_content = target.encrypt_text(
            "journal_events",
            "content",
            record_key,
            content,
        )
        encrypted_count = target.encrypt_non_negative_int(
            "journal_events",
            "token_count",
            record_key,
            count,
        )
        connection.execute(
            """
            INSERT INTO _astrcontinuum_v2_journal_events (
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
                record_key,
                row["session_key_hash"],
                row["sequence"],
                row["event_type"],
                row["role"],
                encrypted_content,
                row["source_hook"],
                row["idempotency_key"],
                encrypted_count,
                row["created_at"],
            ),
        )
        stored = connection.execute(
            """
            SELECT content, token_count_envelope
            FROM _astrcontinuum_v2_journal_events
            WHERE event_id = ?
            """,
            (record_key,),
        ).fetchone()
        if stored is None:
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
        _assert_wire(
            target.decrypt_text(
                "journal_events",
                "content",
                record_key,
                stored["content"],
            ),
            content,
        )
        if (
            target.decrypt_non_negative_int(
                "journal_events",
                "token_count",
                record_key,
                stored["token_count_envelope"],
            )
            != count
        ):
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    _inject(fault_injector, "security_v2.after_journal_copy")


def _copy_format_two_capsules(
    connection: sqlite3.Connection,
    *,
    source: SecureCodec,
    target: SecureCodec,
    fault_injector: StorageFaultInjector | None,
) -> None:
    for row in _batched_rows(connection, "capsules", "capsule_id"):
        record_key = str(row["capsule_id"])
        canonical = source.decrypt_object_json(
            "capsules",
            "canonical_capsule_json",
            record_key,
            str(row["canonical_capsule_json"]),
        )
        count = _format_one_count(row["token_cost"])
        encrypted_canonical = target.encrypt_object_json(
            "capsules",
            "canonical_capsule_json",
            record_key,
            canonical,
        )
        encrypted_count = target.encrypt_non_negative_int(
            "capsules",
            "token_cost",
            record_key,
            count,
        )
        connection.execute(
            """
            INSERT INTO _astrcontinuum_v2_capsules (
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
                record_key,
                row["session_key_hash"],
                row["level"],
                row["covered_event_start"],
                row["covered_event_end"],
                encrypted_canonical,
                encrypted_count,
                row["source_coverage"],
                row["created_at"],
            ),
        )
        stored = connection.execute(
            """
            SELECT canonical_capsule_json, token_cost_envelope
            FROM _astrcontinuum_v2_capsules
            WHERE capsule_id = ?
            """,
            (record_key,),
        ).fetchone()
        if stored is None:
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
        _assert_wire(
            target.decrypt_object_json(
                "capsules",
                "canonical_capsule_json",
                record_key,
                stored["canonical_capsule_json"],
            ),
            canonical,
        )
        if (
            target.decrypt_non_negative_int(
                "capsules",
                "token_cost",
                record_key,
                stored["token_cost_envelope"],
            )
            != count
        ):
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    _inject(fault_injector, "security_v2.after_capsule_copy")


def _copy_format_two_snapshots(
    connection: sqlite3.Connection,
    *,
    source: SecureCodec,
    target: SecureCodec,
    fault_injector: StorageFaultInjector | None,
) -> None:
    for row in _batched_rows(connection, "snapshots", "snapshot_id"):
        record_key = str(row["snapshot_id"])
        exact_anchors = source.decrypt_array_json(
            "snapshots",
            "exact_anchor_ids_json",
            record_key,
            str(row["exact_anchor_ids_json"]),
        )
        rendered = source.decrypt_text(
            "snapshots",
            "rendered_context",
            record_key,
            str(row["rendered_context"]),
        )
        audit = source.decrypt_object_json(
            "snapshots",
            "audit_outcome",
            record_key,
            str(row["audit_outcome"]),
        )
        count = _format_one_count(row["token_cost"])
        encrypted_exact = target.encrypt_array_json(
            "snapshots",
            "exact_anchor_ids_json",
            record_key,
            exact_anchors,
        )
        encrypted_rendered = target.encrypt_text(
            "snapshots",
            "rendered_context",
            record_key,
            rendered,
        )
        encrypted_count = target.encrypt_non_negative_int(
            "snapshots",
            "token_cost",
            record_key,
            count,
        )
        encrypted_audit = target.encrypt_object_json(
            "snapshots",
            "audit_outcome",
            record_key,
            audit,
        )
        connection.execute(
            """
            INSERT INTO _astrcontinuum_v2_snapshots (
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_key,
                row["session_key_hash"],
                row["base_snapshot_id"],
                row["covered_event_end"],
                row["source_high_water_mark"],
                encrypted_exact,
                encrypted_rendered,
                encrypted_count,
                encrypted_audit,
                row["lifecycle_state"],
                row["created_at"],
                row["committed_at"],
            ),
        )
        stored = connection.execute(
            """
            SELECT
                exact_anchor_ids_json,
                rendered_context,
                token_cost_envelope,
                audit_outcome
            FROM _astrcontinuum_v2_snapshots
            WHERE snapshot_id = ?
            """,
            (record_key,),
        ).fetchone()
        if stored is None:
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
        _assert_wire(
            target.decrypt_array_json(
                "snapshots",
                "exact_anchor_ids_json",
                record_key,
                stored["exact_anchor_ids_json"],
            ),
            exact_anchors,
        )
        _assert_wire(
            target.decrypt_text(
                "snapshots",
                "rendered_context",
                record_key,
                stored["rendered_context"],
            ),
            rendered,
        )
        if (
            target.decrypt_non_negative_int(
                "snapshots",
                "token_cost",
                record_key,
                stored["token_cost_envelope"],
            )
            != count
        ):
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
        _assert_wire(
            target.decrypt_object_json(
                "snapshots",
                "audit_outcome",
                record_key,
                stored["audit_outcome"],
            ),
            audit,
        )
    _inject(fault_injector, "security_v2.after_snapshot_copy")


def _apply_secure_format_two(
    connection: sqlite3.Connection,
    *,
    source: SecureCodec,
    target: SecureCodec,
    fault_injector: StorageFaultInjector | None,
) -> None:
    metadata = connection.execute(
        """
        SELECT *
        FROM storage_security
        WHERE singleton_id = ?
        """,
        (_SECURITY_SINGLETON_ID,),
    ).fetchone()
    if (
        metadata is None
        or metadata["format_version"] != _LEGACY_STORAGE_FORMAT_VERSION
        or metadata["active_key_id"] != source.key_id
        or metadata["state"] != StorageMaintenanceState.ACTIVE.value
    ):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    _authenticate_verifier(source, str(metadata["key_verifier"]))
    counts = {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in _COUNTED_TABLES
    }

    _execute_format_v2_ddl(
        connection,
        "create_journal_events",
        "create_capsules",
        "create_snapshots",
        "create_storage_security",
    )
    _rewrite_format_two_sessions(connection, source=source, target=target)
    _copy_format_two_journal_events(
        connection,
        source=source,
        target=target,
        fault_injector=fault_injector,
    )
    _copy_format_two_capsules(
        connection,
        source=source,
        target=target,
        fault_injector=fault_injector,
    )
    _copy_format_two_snapshots(
        connection,
        source=source,
        target=target,
        fault_injector=fault_injector,
    )

    verifier = target.encrypt_text(
        "storage_security",
        "key_verifier",
        str(_SECURITY_SINGLETON_ID),
        _VERIFIER_PLAINTEXT,
    )
    now = _utc_now()
    connection.execute(
        """
        INSERT INTO _astrcontinuum_v2_storage_security (
            singleton_id,
            format_version,
            active_key_id,
            state,
            key_verifier,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _SECURITY_SINGLETON_ID,
            STORAGE_FORMAT_VERSION,
            target.key_id,
            StorageMaintenanceState.NEEDS_SCRUB.value,
            verifier,
            metadata["created_at"],
            now,
        ),
    )

    _execute_format_v2_ddl(
        connection,
        "drop_journal_events",
        "drop_capsules",
        "drop_snapshots",
        "drop_storage_security",
        "rename_journal_events",
        "rename_capsules",
        "rename_snapshots",
        "rename_storage_security",
        "index_journal_events",
        "index_capsules",
        "journal_events_immutable_update",
        "journal_events_immutable_delete",
        "capsules_immutable_update",
        "capsules_immutable_delete",
        "snapshots_immutable_update",
        "snapshots_immutable_delete",
        "journal_events_secure_insert",
        "capsules_secure_insert",
        "token_metrics",
        "token_metric_backfill_intents",
        "idx_token_metric_backfill_session",
    )

    for table, expected in counts.items():
        actual = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        if actual != expected:
            _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    _authenticate_verifier(target, verifier)
    _inject(fault_injector, "security_v2.after_verify")

    migration = MIGRATIONS[1]
    connection.execute(
        """
        INSERT INTO schema_migrations (version, name, checksum, applied_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            migration.version,
            migration.name,
            migration.checksum,
            now,
        ),
    )
    connection.execute(f"PRAGMA user_version = {STORAGE_FORMAT_VERSION}")


def _upgrade_secure_format_one(
    factory: SQLiteConnectionFactory,
    *,
    source: SecureCodec,
    target: SecureCodec,
    rekeyed: bool,
    fault_injector: StorageFaultInjector | None,
) -> StorageSecurityActivation:
    try:
        with factory.startup_exclusive_transaction(enforce_foreign_keys=False) as connection:
            _apply_secure_format_two(
                connection,
                source=source,
                target=target,
                fault_injector=fault_injector,
            )
    except StorageSecurityError:
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)

    try:
        _inject(fault_injector, "security_v2.after_commit")
    except _InjectedStorageFault:
        _raise(SecurityErrorCode.STORAGE_SCRUB_FAILED)
    _scrub_storage(factory, fault_injector=fault_injector)
    format_version, key_id, state, verifier = _read_security_metadata(factory)
    if (
        format_version != STORAGE_FORMAT_VERSION
        or key_id != target.key_id
        or state is not StorageMaintenanceState.ACTIVE
    ):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    _authenticate_verifier(target, verifier)
    return StorageSecurityActivation(
        codec=target,
        key_id=target.key_id,
        state=state,
        migrated=True,
        rekeyed=rekeyed,
        scrubbed=True,
    )


def _create_immutable_update_trigger(
    connection: sqlite3.Connection,
    table: str,
    trigger: str,
) -> None:
    connection.execute(
        f"""
        CREATE TRIGGER {trigger}
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} rows are immutable');
        END
        """
    )


def _rekey_storage(
    connection: sqlite3.Connection,
    *,
    previous: SecureCodec,
    active: SecureCodec,
    fault_injector: StorageFaultInjector | None,
) -> None:
    counts = {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in _COUNTED_TABLES
    }
    update_triggers = {
        **_IMMUTABLE_UPDATE_TRIGGERS,
        "snapshots": "snapshots_immutable_update",
    }
    for trigger in update_triggers.values():
        connection.execute(f"DROP TRIGGER {trigger}")

    for row in _batched_rows(connection, "sessions", "session_key_hash"):
        record_key = str(row["session_key_hash"])
        canonical_plaintext, canonical_encrypted = _rekey_object(
            previous,
            active,
            table="sessions",
            column="canonical_session_key_json",
            record_key=record_key,
            sentinel=str(row["canonical_session_key_json"]),
        )
        identity_plaintext: dict[str, str | None] = {}
        identity_encrypted: dict[str, str | None] = {}
        for column in _SESSION_IDENTITY_COLUMNS:
            if row[column] is None:
                identity_plaintext[column] = None
                identity_encrypted[column] = None
            else:
                plain, cipher = _rekey_text(
                    previous,
                    active,
                    table="sessions",
                    column=column,
                    record_key=record_key,
                    envelope=str(row[column]),
                )
                identity_plaintext[column] = plain
                identity_encrypted[column] = cipher
        connection.execute(
            """
            UPDATE sessions
            SET
                canonical_session_key_json = ?,
                platform_instance_id = ?,
                message_type = ?,
                session_id = ?,
                group_id = ?,
                user_id = ?,
                conversation_id = ?,
                persona_id = ?
            WHERE session_key_hash = ?
            """,
            (
                canonical_encrypted,
                identity_encrypted["platform_instance_id"],
                identity_encrypted["message_type"],
                identity_encrypted["session_id"],
                identity_encrypted["group_id"],
                identity_encrypted["user_id"],
                identity_encrypted["conversation_id"],
                identity_encrypted["persona_id"],
                record_key,
            ),
        )
        stored = connection.execute(
            "SELECT * FROM sessions WHERE session_key_hash = ?",
            (record_key,),
        ).fetchone()
        _assert_wire(
            active.decrypt_object_json(
                "sessions",
                "canonical_session_key_json",
                record_key,
                stored["canonical_session_key_json"],
            ),
            canonical_plaintext,
        )
        for column in _SESSION_IDENTITY_COLUMNS:
            if identity_plaintext[column] is None:
                if stored[column] is not None:
                    _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
                continue
            _assert_wire(
                active.decrypt_text(
                    "sessions",
                    column,
                    record_key,
                    stored[column],
                ),
                identity_plaintext[column],
            )

    for row in _batched_rows(connection, "journal_events", "event_id"):
        record_key = str(row["event_id"])
        plaintext, encrypted = _rekey_text(
            previous,
            active,
            table="journal_events",
            column="content",
            record_key=record_key,
            envelope=str(row["content"]),
        )
        count_plaintext, count_encrypted = _rekey_non_negative_int(
            previous,
            active,
            table="journal_events",
            column="token_count",
            record_key=record_key,
            envelope=row["token_count_envelope"],
        )
        connection.execute(
            """
            UPDATE journal_events
            SET content = ?, token_count_envelope = ?
            WHERE event_id = ?
            """,
            (encrypted, count_encrypted, record_key),
        )
        stored = connection.execute(
            """
            SELECT content, token_count_envelope
            FROM journal_events
            WHERE event_id = ?
            """,
            (record_key,),
        ).fetchone()
        _assert_wire(
            active.decrypt_text(
                "journal_events",
                "content",
                record_key,
                stored["content"],
            ),
            plaintext,
        )
        if (
            active.decrypt_non_negative_int(
                "journal_events",
                "token_count",
                record_key,
                stored["token_count_envelope"],
            )
            != count_plaintext
        ):
            _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)

    for row in _batched_rows(connection, "capsules", "capsule_id"):
        record_key = str(row["capsule_id"])
        plaintext, encrypted = _rekey_object(
            previous,
            active,
            table="capsules",
            column="canonical_capsule_json",
            record_key=record_key,
            sentinel=str(row["canonical_capsule_json"]),
        )
        count_plaintext, count_encrypted = _rekey_non_negative_int(
            previous,
            active,
            table="capsules",
            column="token_cost",
            record_key=record_key,
            envelope=row["token_cost_envelope"],
        )
        connection.execute(
            """
            UPDATE capsules
            SET canonical_capsule_json = ?, token_cost_envelope = ?
            WHERE capsule_id = ?
            """,
            (encrypted, count_encrypted, record_key),
        )
        stored = connection.execute(
            """
            SELECT canonical_capsule_json, token_cost_envelope
            FROM capsules
            WHERE capsule_id = ?
            """,
            (record_key,),
        ).fetchone()
        _assert_wire(
            active.decrypt_object_json(
                "capsules",
                "canonical_capsule_json",
                record_key,
                stored["canonical_capsule_json"],
            ),
            plaintext,
        )
        if (
            active.decrypt_non_negative_int(
                "capsules",
                "token_cost",
                record_key,
                stored["token_cost_envelope"],
            )
            != count_plaintext
        ):
            _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)

    for row in _batched_rows(connection, "snapshots", "snapshot_id"):
        record_key = str(row["snapshot_id"])
        exact_plaintext, exact_encrypted = _rekey_array(
            previous,
            active,
            table="snapshots",
            column="exact_anchor_ids_json",
            record_key=record_key,
            sentinel=str(row["exact_anchor_ids_json"]),
        )
        rendered_plaintext, rendered_encrypted = _rekey_text(
            previous,
            active,
            table="snapshots",
            column="rendered_context",
            record_key=record_key,
            envelope=str(row["rendered_context"]),
        )
        count_plaintext, count_encrypted = _rekey_non_negative_int(
            previous,
            active,
            table="snapshots",
            column="token_cost",
            record_key=record_key,
            envelope=row["token_cost_envelope"],
        )
        audit_plaintext, audit_encrypted = _rekey_object(
            previous,
            active,
            table="snapshots",
            column="audit_outcome",
            record_key=record_key,
            sentinel=str(row["audit_outcome"]),
        )
        connection.execute(
            """
            UPDATE snapshots
            SET
                exact_anchor_ids_json = ?,
                rendered_context = ?,
                token_cost_envelope = ?,
                audit_outcome = ?
            WHERE snapshot_id = ?
            """,
            (
                exact_encrypted,
                rendered_encrypted,
                count_encrypted,
                audit_encrypted,
                record_key,
            ),
        )
        stored = connection.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?",
            (record_key,),
        ).fetchone()
        _assert_wire(
            active.decrypt_array_json(
                "snapshots",
                "exact_anchor_ids_json",
                record_key,
                stored["exact_anchor_ids_json"],
            ),
            exact_plaintext,
        )
        _assert_wire(
            active.decrypt_text(
                "snapshots",
                "rendered_context",
                record_key,
                stored["rendered_context"],
            ),
            rendered_plaintext,
        )
        if (
            active.decrypt_non_negative_int(
                "snapshots",
                "token_cost",
                record_key,
                stored["token_cost_envelope"],
            )
            != count_plaintext
        ):
            _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
        _assert_wire(
            active.decrypt_object_json(
                "snapshots",
                "audit_outcome",
                record_key,
                stored["audit_outcome"],
            ),
            audit_plaintext,
        )

    _inject(fault_injector, "security.after_rekey_encrypt")

    for table, trigger in update_triggers.items():
        _create_immutable_update_trigger(connection, table, trigger)

    verifier = active.encrypt_text(
        "storage_security",
        "key_verifier",
        str(_SECURITY_SINGLETON_ID),
        _VERIFIER_PLAINTEXT,
    )
    updated = connection.execute(
        """
        UPDATE storage_security
        SET active_key_id = ?, state = ?, key_verifier = ?, updated_at = ?
        WHERE singleton_id = ? AND active_key_id = ? AND state = ?
        """,
        (
            active.key_id,
            StorageMaintenanceState.NEEDS_SCRUB.value,
            verifier,
            _utc_now(),
            _SECURITY_SINGLETON_ID,
            previous.key_id,
            StorageMaintenanceState.ACTIVE.value,
        ),
    )
    if updated.rowcount != 1:
        _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
    _authenticate_verifier(active, verifier)
    for table, expected in counts.items():
        actual = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        if actual != expected:
            _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
    _inject(fault_injector, "security.after_rekey_verify")


def _rotate_storage(
    factory: SQLiteConnectionFactory,
    keys: ResolvedKeyMaterial,
    *,
    durable_key_id: str,
    verifier: str,
    fault_injector: StorageFaultInjector | None,
) -> StorageSecurityActivation:
    if keys.previous is None or keys.previous.key_id != durable_key_id:
        _raise(SecurityErrorCode.STORAGE_PREVIOUS_KEY_REQUIRED)
    previous = SecureCodec(keys.previous.raw_key)
    active = SecureCodec(keys.active.raw_key)
    try:
        _authenticate_verifier(previous, verifier)
    except StorageSecurityError:
        _raise(SecurityErrorCode.STORAGE_PREVIOUS_KEY_REQUIRED)

    try:
        with factory.startup_exclusive_transaction() as connection:
            durable = connection.execute(
                """
                SELECT active_key_id, state, key_verifier
                FROM storage_security
                WHERE singleton_id = ?
                """,
                (_SECURITY_SINGLETON_ID,),
            ).fetchone()
            if (
                durable is None
                or durable["active_key_id"] != durable_key_id
                or durable["state"] != StorageMaintenanceState.ACTIVE.value
            ):
                _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
            _authenticate_verifier(previous, str(durable["key_verifier"]))
            _rekey_storage(
                connection,
                previous=previous,
                active=active,
                fault_injector=fault_injector,
            )
    except StorageSecurityError as error:
        if error.code is SecurityErrorCode.STORAGE_PREVIOUS_KEY_REQUIRED:
            raise
        _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
        _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)

    try:
        _inject(fault_injector, "security.after_rekey_commit")
    except _InjectedStorageFault:
        _raise(SecurityErrorCode.STORAGE_SCRUB_FAILED)
    _scrub_storage(factory, fault_injector=fault_injector)
    _, final_key_id, final_state, final_verifier = _read_security_metadata(factory)
    if final_key_id != active.key_id or final_state is not StorageMaintenanceState.ACTIVE:
        _raise(SecurityErrorCode.STORAGE_REKEY_FAILED)
    _authenticate_verifier(active, final_verifier)
    return StorageSecurityActivation(
        codec=active,
        key_id=active.key_id,
        state=final_state,
        migrated=False,
        rekeyed=True,
        scrubbed=True,
    )


def _activate_existing(
    factory: SQLiteConnectionFactory,
    keys: ResolvedKeyMaterial,
    *,
    fault_injector: StorageFaultInjector | None,
) -> StorageSecurityActivation:
    format_version, durable_key_id, state, verifier = _read_security_metadata(factory)
    target = SecureCodec(keys.active.raw_key)
    rekeyed = durable_key_id != target.key_id
    if rekeyed:
        if keys.previous is None or keys.previous.key_id != durable_key_id:
            _raise(SecurityErrorCode.STORAGE_PREVIOUS_KEY_REQUIRED)
        source = SecureCodec(keys.previous.raw_key)
        try:
            _authenticate_verifier(source, verifier)
        except StorageSecurityError:
            _raise(SecurityErrorCode.STORAGE_PREVIOUS_KEY_REQUIRED)
    else:
        source = target
        _authenticate_verifier(source, verifier)

    scrubbed = False
    if state is StorageMaintenanceState.NEEDS_SCRUB:
        _scrub_storage(factory, fault_injector=fault_injector)
        scrubbed = True
    elif state is not StorageMaintenanceState.ACTIVE:
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)

    (
        current_format,
        current_key_id,
        current_state,
        current_verifier,
    ) = _read_security_metadata(factory)
    if (
        current_format != format_version
        or current_key_id != durable_key_id
        or current_state is not StorageMaintenanceState.ACTIVE
    ):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    _authenticate_verifier(source, current_verifier)

    if format_version == _LEGACY_STORAGE_FORMAT_VERSION:
        return _upgrade_secure_format_one(
            factory,
            source=source,
            target=target,
            rekeyed=rekeyed,
            fault_injector=fault_injector,
        )
    if rekeyed:
        return _rotate_storage(
            factory,
            keys,
            durable_key_id=durable_key_id,
            verifier=current_verifier,
            fault_injector=fault_injector,
        )

    return StorageSecurityActivation(
        codec=target,
        key_id=target.key_id,
        state=current_state,
        migrated=False,
        rekeyed=False,
        scrubbed=scrubbed,
    )


def activate_storage_security(
    factory: SQLiteConnectionFactory,
    keys: ResolvedKeyMaterial,
    *,
    fault_injector: StorageFaultInjector | None = None,
) -> StorageSecurityActivation:
    """Authenticate, migrate, and scrub storage before repository construction."""

    if not isinstance(factory, SQLiteConnectionFactory) or not isinstance(
        keys,
        ResolvedKeyMaterial,
    ):
        _raise(SecurityErrorCode.STORAGE_KEY_INVALID)
    if (
        keys.active.key_id != key_id_for(keys.active.raw_key)
        or (keys.previous is not None and keys.previous.key_id != key_id_for(keys.previous.raw_key))
        or (
            keys.previous is not None
            and hmac.compare_digest(keys.active.raw_key, keys.previous.raw_key)
        )
    ):
        _raise(SecurityErrorCode.STORAGE_KEY_INVALID)
    try:
        SQLiteMigrator(factory).migrate()
    except (MigrationError, OSError, RuntimeError):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)
    if _security_table_exists(factory):
        return _activate_existing(
            factory,
            keys,
            fault_injector=fault_injector,
        )

    codec = SecureCodec(keys.active.raw_key)
    try:
        with factory.startup_exclusive_transaction(enforce_foreign_keys=False) as connection:
            counts = _validate_legacy_rows(connection)
            _inject(fault_injector, "security.after_preflight")
            _transform_legacy(
                connection,
                codec,
                counts=counts,
                fault_injector=fault_injector,
            )
    except StorageSecurityError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError):
        _raise(SecurityErrorCode.STORAGE_LEGACY_VALIDATION_FAILED)
    except (OSError, RuntimeError, OverflowError, sqlite3.Error):
        _raise(SecurityErrorCode.STORAGE_MIGRATION_FAILED)

    try:
        _inject(fault_injector, "security.after_transform_commit")
    except _InjectedStorageFault:
        _raise(SecurityErrorCode.STORAGE_SCRUB_FAILED)
    _scrub_storage(factory, fault_injector=fault_injector)
    return _activate_existing(
        factory,
        keys,
        fault_injector=fault_injector,
    )
