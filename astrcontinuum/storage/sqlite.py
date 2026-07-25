"""SQLite connection policy and explicit transaction boundaries."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_BUSY_TIMEOUT_MS = 100
MAX_BUSY_TIMEOUT_MS = 5_000
DATABASE_FILENAME = "astrcontinuum.sqlite3"


class SQLiteConnectionFactory:
    """Create independently configured SQLite connections for one data directory."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if not 1 <= busy_timeout_ms <= MAX_BUSY_TIMEOUT_MS:
            raise ValueError(f"busy_timeout_ms must be between 1 and {MAX_BUSY_TIMEOUT_MS}")
        self._data_dir = Path(data_dir)
        self._busy_timeout_ms = busy_timeout_ms

    @property
    def database_path(self) -> Path:
        """Return the sole database file owned by this factory."""

        return self._data_dir / DATABASE_FILENAME

    @property
    def busy_timeout_ms(self) -> int:
        """Return the finite lock-wait budget applied to every connection."""

        return self._busy_timeout_ms

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        """Open one manually controlled, policy-configured connection."""

        if read_only:
            target = f"{self.database_path.resolve().as_uri()}?mode=ro"
        else:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            target = str(self.database_path)

        connection = sqlite3.connect(
            target,
            timeout=self._busy_timeout_ms / 1_000,
            isolation_level=None,
            uri=read_only,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            if not read_only:
                journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if str(journal_mode).lower() != "wal":
                    raise RuntimeError("SQLite refused the required WAL journal mode")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def connection(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield one connection and always close it without implicit commit."""

        connection = self.connect(read_only=read_only)
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Commit a short explicit transaction or roll it back on any failure."""

        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
