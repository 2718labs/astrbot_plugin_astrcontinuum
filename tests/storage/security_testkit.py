"""Explicit encrypted-storage composition shared by repository tests."""

from __future__ import annotations

from collections.abc import Callable

import astrcontinuum as ac

_TEST_MASTER_KEY = bytes.fromhex("6f7a6b8c0d1e2f30415263748596a7b8c9daebfc0d1e2f30415263748596a7b8")


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
