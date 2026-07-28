from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from astrcontinuum.storage.crypto import (
    SecureCodec,
    SecurityErrorCode,
    StorageSecurityError,
    encode_key_text,
    key_id_for,
)
from astrcontinuum.storage.security import (
    LOCAL_KEY_FILENAME,
    KeyMaterial,
    KeySource,
    ResolvedKeyMaterial,
    resolve_key_material,
)

ACTIVE_KEY = bytes(range(32))
PREVIOUS_KEY = bytes(reversed(range(32)))
ACTIVE_TEXT = encode_key_text(ACTIVE_KEY)
PREVIOUS_TEXT = encode_key_text(PREVIOUS_KEY)


def _assert_code(
    error: pytest.ExceptionInfo[StorageSecurityError], code: SecurityErrorCode
) -> None:
    assert error.value.code is code
    assert str(error.value) == code.value
    assert ACTIVE_TEXT not in repr(error.value)
    assert PREVIOUS_TEXT not in repr(error.value)


def test_environment_source_resolves_active_and_optional_previous_key(tmp_path: Path) -> None:
    resolved = resolve_key_material(
        {"encryption_key_source": "environment"},
        tmp_path / "plugin-data",
        environ={
            "ASTRCONTINUUM_MASTER_KEY": ACTIVE_TEXT,
            "ASTRCONTINUUM_PREVIOUS_KEY": PREVIOUS_TEXT,
        },
    )

    assert resolved.source is KeySource.ENVIRONMENT
    assert resolved.active.raw_key == ACTIVE_KEY
    assert resolved.active.key_id == key_id_for(ACTIVE_KEY)
    assert resolved.previous is not None
    assert resolved.previous.raw_key == PREVIOUS_KEY
    assert resolved.local_degraded is False
    assert ACTIVE_TEXT not in repr(resolved)
    assert repr(resolved.active) == f"KeyMaterial(key_id={key_id_for(ACTIVE_KEY)!r})"


def test_environment_source_uses_no_process_environment_when_mapping_is_supplied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASTRCONTINUUM_MASTER_KEY", ACTIVE_TEXT)

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {"encryption_key_source": "environment"},
            tmp_path / "plugin-data",
            environ={},
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_MISSING)


@pytest.mark.parametrize(
    ("environ", "code"),
    [
        ({}, SecurityErrorCode.STORAGE_KEY_MISSING),
        (
            {"ASTRCONTINUUM_MASTER_KEY": ""},
            SecurityErrorCode.STORAGE_KEY_MISSING,
        ),
        (
            {"ASTRCONTINUUM_MASTER_KEY": "invalid"},
            SecurityErrorCode.STORAGE_KEY_INVALID,
        ),
        (
            {
                "ASTRCONTINUUM_MASTER_KEY": ACTIVE_TEXT,
                "ASTRCONTINUUM_PREVIOUS_KEY": "invalid",
            },
            SecurityErrorCode.STORAGE_KEY_INVALID,
        ),
        (
            {
                "ASTRCONTINUUM_MASTER_KEY": ACTIVE_TEXT,
                "ASTRCONTINUUM_PREVIOUS_KEY": ACTIVE_TEXT,
            },
            SecurityErrorCode.STORAGE_KEY_INVALID,
        ),
    ],
)
def test_environment_source_fails_closed(
    tmp_path: Path,
    environ: dict[str, str],
    code: SecurityErrorCode,
) -> None:
    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {"encryption_key_source": "environment"},
            tmp_path / "plugin-data",
            environ=environ,
        )

    _assert_code(raised, code)


def test_external_file_source_reads_bounded_canonical_files_outside_data_dir(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "plugin-data"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    active_path = secrets_dir / "active.key"
    previous_path = secrets_dir / "previous.key"
    active_path.write_text(ACTIVE_TEXT + "\n", encoding="ascii", newline="\n")
    previous_path.write_text(PREVIOUS_TEXT, encoding="ascii", newline="\n")

    resolved = resolve_key_material(
        {
            "encryption_key_source": "file",
            "encryption_key_file": str(active_path),
            "encryption_previous_key_file": str(previous_path),
        },
        data_dir,
        environ={},
    )

    assert resolved.source is KeySource.FILE
    assert resolved.active.raw_key == ACTIVE_KEY
    assert resolved.previous is not None
    assert resolved.previous.raw_key == PREVIOUS_KEY
    assert resolved.local_degraded is False


@pytest.mark.parametrize("unsafe_kind", ["inside", "directory", "oversized", "missing"])
def test_external_file_source_rejects_unsafe_or_nonregular_paths(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    if unsafe_kind == "inside":
        path = data_dir / "master.key"
        path.write_text(ACTIVE_TEXT, encoding="ascii")
    elif unsafe_kind == "directory":
        path = outside
    elif unsafe_kind == "oversized":
        path = outside / "master.key"
        path.write_text(ACTIVE_TEXT + ("x" * 512), encoding="ascii")
    else:
        path = outside / "missing.key"

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {
                "encryption_key_source": "file",
                "encryption_key_file": str(path),
            },
            data_dir,
            environ={},
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)
    assert str(path) not in repr(raised.value)


def test_external_file_source_resolves_symlink_before_boundary_check(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    inside = data_dir / "master.key"
    inside.write_text(ACTIVE_TEXT, encoding="ascii")
    link = tmp_path / "external-looking.key"
    try:
        link.symlink_to(inside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {
                "encryption_key_source": "file",
                "encryption_key_file": str(link),
            },
            data_dir,
            environ={},
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_FILE_UNSAFE)


def test_external_file_source_rejects_noncanonical_contents_without_fallback(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    local_path = data_dir / LOCAL_KEY_FILENAME
    local_path.write_text(ACTIVE_TEXT, encoding="ascii")
    external_path = tmp_path / "external.key"
    external_path.write_text("invalid\n", encoding="ascii")

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {
                "encryption_key_source": "file",
                "encryption_key_file": str(external_path),
            },
            data_dir,
            environ={},
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_INVALID)
    assert local_path.read_text(encoding="ascii") == ACTIVE_TEXT


def test_explicit_local_source_creates_once_and_reuses_the_same_key(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"

    first = resolve_key_material(
        {"encryption_key_source": "local"},
        data_dir,
        environ={},
    )
    local_path = data_dir / LOCAL_KEY_FILENAME
    first_text = local_path.read_text(encoding="ascii")
    second = resolve_key_material(
        {"encryption_key_source": "local"},
        data_dir,
        environ={},
    )

    assert first.source is KeySource.LOCAL
    assert first.local_degraded is True
    assert first.active.raw_key == second.active.raw_key
    assert first_text == encode_key_text(first.active.raw_key) + "\n"
    assert local_path.read_text(encoding="ascii") == first_text
    if os.name != "nt":
        assert stat.S_IMODE(local_path.stat().st_mode) & 0o077 == 0


def test_missing_source_creates_once_and_reuses_the_server_managed_key(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "plugin-data"

    first = resolve_key_material({}, data_dir, environ={})
    local_path = data_dir / LOCAL_KEY_FILENAME
    first_text = local_path.read_text(encoding="ascii")
    second = resolve_key_material({}, data_dir, environ={})

    assert first.source is KeySource.LOCAL
    assert first.local_degraded is True
    assert first.active.raw_key == second.active.raw_key
    assert first_text == encode_key_text(first.active.raw_key) + "\n"
    assert local_path.read_text(encoding="ascii") == first_text


def test_missing_source_preserves_an_existing_environment_deployment(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "plugin-data"

    resolved = resolve_key_material(
        {},
        data_dir,
        environ={"ASTRCONTINUUM_MASTER_KEY": ACTIVE_TEXT},
    )

    assert resolved.source is KeySource.ENVIRONMENT
    assert resolved.active.raw_key == ACTIVE_KEY
    assert resolved.local_degraded is False
    assert not (data_dir / LOCAL_KEY_FILENAME).exists()


@pytest.mark.parametrize(
    ("environ", "code"),
    [
        (
            {"ASTRCONTINUUM_PREVIOUS_KEY": PREVIOUS_TEXT},
            SecurityErrorCode.STORAGE_KEY_MISSING,
        ),
        (
            {"ASTRCONTINUUM_PREVIOUS_KEY": ""},
            SecurityErrorCode.STORAGE_KEY_MISSING,
        ),
        (
            {"ASTRCONTINUUM_MASTER_KEY": ""},
            SecurityErrorCode.STORAGE_KEY_MISSING,
        ),
        (
            {"ASTRCONTINUUM_MASTER_KEY": "invalid"},
            SecurityErrorCode.STORAGE_KEY_INVALID,
        ),
    ],
)
def test_missing_source_with_any_environment_key_present_never_falls_back_to_local(
    tmp_path: Path,
    environ: dict[str, str],
    code: SecurityErrorCode,
) -> None:
    data_dir = tmp_path / "plugin-data"

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material({}, data_dir, environ=environ)

    _assert_code(raised, code)
    assert not (data_dir / LOCAL_KEY_FILENAME).exists()


def test_local_source_never_overwrites_an_existing_invalid_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    local_path = data_dir / LOCAL_KEY_FILENAME
    local_path.write_text("operator-owned-invalid-value", encoding="ascii")

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {"encryption_key_source": "local"},
            data_dir,
            environ={},
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_INVALID)
    assert local_path.read_text(encoding="ascii") == "operator-owned-invalid-value"


def test_local_creation_failure_is_content_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise PermissionError("secret host detail")

    monkeypatch.setattr(os, "open", fail_open)

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {"encryption_key_source": "local"},
            tmp_path / "plugin-data",
            environ={},
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_LOCAL_KEY_CREATE_FAILED)
    assert "secret host detail" not in repr(raised.value)


def test_invalid_source_does_not_fall_back_to_environment_or_local(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    (data_dir / LOCAL_KEY_FILENAME).write_text(ACTIVE_TEXT, encoding="ascii")

    with pytest.raises(StorageSecurityError) as raised:
        resolve_key_material(
            {"encryption_key_source": "automatic"},
            data_dir,
            environ={"ASTRCONTINUUM_MASTER_KEY": ACTIVE_TEXT},
        )

    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_INVALID)


def test_key_material_constructor_is_closed() -> None:
    material = KeyMaterial.from_raw(ACTIVE_KEY)
    assert material.raw_key == ACTIVE_KEY
    assert material.key_id == key_id_for(ACTIVE_KEY)

    with pytest.raises(StorageSecurityError) as raised:
        KeyMaterial.from_raw(b"short")
    _assert_code(raised, SecurityErrorCode.STORAGE_KEY_INVALID)

    assert (
        ResolvedKeyMaterial(
            active=material,
            previous=None,
            source=KeySource.ENVIRONMENT,
            local_degraded=False,
        ).active
        is material
    )


def test_storage_security_types_are_available_from_public_packages() -> None:
    import astrcontinuum as public_api
    import astrcontinuum.storage as storage_api

    expected = {
        "KeyMaterial": KeyMaterial,
        "KeySource": KeySource,
        "ResolvedKeyMaterial": ResolvedKeyMaterial,
        "SecureCodec": SecureCodec,
        "SecurityErrorCode": SecurityErrorCode,
        "StorageSecurityError": StorageSecurityError,
        "resolve_key_material": resolve_key_material,
    }
    for name, value in expected.items():
        assert getattr(storage_api, name) is value
        assert getattr(public_api, name) is value
