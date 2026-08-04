"""Explicit encrypted-storage composition shared by repository tests."""

from __future__ import annotations

import base64
import lzma
import sqlite3
from collections.abc import Callable

import astrcontinuum as ac

_TEST_MASTER_KEY = bytes.fromhex("6f7a6b8c0d1e2f30415263748596a7b8c9daebfc0d1e2f30415263748596a7b8")
_V020_FIXTURE_KEY = bytes(range(32))
_V020_SQL_DUMP_XZ_B64 = (
    "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"
    "AgAAAAAEWVo="
)


def storage_test_keys() -> ac.ResolvedKeyMaterial:
    """Return deterministic, non-production key material for isolated test databases."""

    return ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(_TEST_MASTER_KEY),
        previous=None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )


def storage_test_codec() -> ac.SecureCodec:
    """Return a codec matching :func:`storage_test_keys` for direct SQL fixtures."""

    return ac.SecureCodec(_TEST_MASTER_KEY)


def v020_fixture_keys() -> ac.ResolvedKeyMaterial:
    """Return the exact key used by the frozen v0.2.0 database fixture."""

    return ac.ResolvedKeyMaterial(
        active=ac.KeyMaterial.from_raw(_V020_FIXTURE_KEY),
        previous=None,
        source=ac.KeySource.ENVIRONMENT,
        local_degraded=False,
    )


def install_v020_storage_fixture(
    factory: ac.SQLiteConnectionFactory,
) -> None:
    """Install a populated database dump produced by the released v0.2.0 code."""

    database_path = factory.database_path
    if database_path.exists():
        raise AssertionError("v0.2.0 fixture target must not exist")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    dump = lzma.decompress(base64.b64decode(_V020_SQL_DUMP_XZ_B64)).decode("utf-8")
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.executescript(dump)
        connection.execute("PRAGMA user_version = 1")
    finally:
        connection.close()


def activate_test_storage(
    factory: ac.SQLiteConnectionFactory,
) -> ac.StorageSecurityActivation:
    """Migrate, encrypt, authenticate, and scrub one repository test database."""

    return ac.activate_storage_security(factory, storage_test_keys())


def secure_repository(
    factory: ac.SQLiteConnectionFactory,
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> ac.SQLiteRepository:
    """Construct a repository only after its database reaches secure ACTIVE state."""

    activation = activate_test_storage(factory)
    return ac.SQLiteRepository(
        factory,
        codec=activation.codec,
        fault_injector=fault_injector,
    )
