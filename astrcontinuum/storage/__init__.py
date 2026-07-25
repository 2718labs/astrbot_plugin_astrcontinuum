"""Durable SQLite foundations for AstrContinuum."""

from .migrations import (
    MIGRATIONS,
    Migration,
    MigrationApplyError,
    MigrationChecksumError,
    MigrationError,
    MigrationPlanError,
    SQLiteMigrator,
)
from .repository import (
    EventIdentityConflict,
    IdempotencyConflict,
    JobTransitionError,
    RepositoryConflict,
    RepositoryError,
    RepositoryInvariantError,
    RequestView,
    SessionIdentityConflict,
    SnapshotCapsuleMembership,
    SQLiteRepository,
    StaleLeaseError,
)
from .sqlite import (
    DATABASE_FILENAME,
    DEFAULT_BUSY_TIMEOUT_MS,
    MAX_BUSY_TIMEOUT_MS,
    SQLiteConnectionFactory,
)

__all__ = [
    "DATABASE_FILENAME",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "MAX_BUSY_TIMEOUT_MS",
    "MIGRATIONS",
    "EventIdentityConflict",
    "IdempotencyConflict",
    "JobTransitionError",
    "Migration",
    "MigrationApplyError",
    "MigrationChecksumError",
    "MigrationError",
    "MigrationPlanError",
    "RepositoryConflict",
    "RepositoryError",
    "RepositoryInvariantError",
    "RequestView",
    "SQLiteConnectionFactory",
    "SQLiteMigrator",
    "SQLiteRepository",
    "SessionIdentityConflict",
    "SnapshotCapsuleMembership",
    "StaleLeaseError",
]
