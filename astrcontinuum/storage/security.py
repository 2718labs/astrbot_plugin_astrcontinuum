"""Key-source resolution and startup storage-security orchestration."""

from __future__ import annotations

import hmac
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import NoReturn

from .crypto import (
    KEY_BYTES,
    SecurityErrorCode,
    StorageSecurityError,
    encode_key_text,
    key_id_for,
    parse_key_text,
)

ACTIVE_KEY_ENV = "ASTRCONTINUUM_MASTER_KEY"
PREVIOUS_KEY_ENV = "ASTRCONTINUUM_PREVIOUS_KEY"
LOCAL_KEY_FILENAME = "astrcontinuum.key"
MAX_KEY_FILE_BYTES = 128


class KeySource(str, Enum):
    """Supported explicit master-key sources."""

    ENVIRONMENT = "environment"
    FILE = "file"
    LOCAL = "local"


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
