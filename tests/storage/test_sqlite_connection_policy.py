from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

import astrcontinuum as ac


def _factory(tmp_path: Path, *, busy_timeout_ms: int = 100) -> Any:
    assert hasattr(ac, "SQLiteConnectionFactory"), (
        "V1-202 requires a top-level SQLiteConnectionFactory export"
    )
    return ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=busy_timeout_ms)


def test_factory_owns_database_file_inside_supplied_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "plugin-data"

    factory = _factory(data_dir)

    assert factory.database_path == data_dir / "astrcontinuum.sqlite3"
    with factory.connection():
        pass
    assert factory.database_path.is_file()


def test_writable_connections_apply_the_complete_policy(tmp_path: Path) -> None:
    factory = _factory(tmp_path, busy_timeout_ms=250)

    with factory.connection() as connection:
        row = connection.execute("SELECT 7 AS value").fetchone()

        assert connection.isolation_level is None
        assert connection.row_factory is sqlite3.Row
        assert row["value"] == 7
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 250


def test_each_connection_reapplies_foreign_keys_and_timeout(tmp_path: Path) -> None:
    factory = _factory(tmp_path)

    with factory.connection() as first:
        first.execute("PRAGMA foreign_keys = OFF")
        first.execute("PRAGMA busy_timeout = 1")

    with factory.connection() as second:
        assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert second.execute("PRAGMA busy_timeout").fetchone()[0] == 100


def test_transaction_commits_on_success_and_rolls_back_on_exception(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory.transaction() as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('committed')")

    with pytest.raises(RuntimeError, match="boom"), factory.transaction() as connection:
        connection.execute("INSERT INTO records VALUES ('rolled-back')")
        raise RuntimeError("boom")

    with factory.connection(read_only=True) as connection:
        values = [
            row["value"] for row in connection.execute("SELECT value FROM records").fetchall()
        ]

    assert values == ["committed"]


def test_read_only_connection_can_read_but_cannot_mutate(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory.transaction() as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('visible')")

    with factory.connection(read_only=True) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()["value"] == "visible"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 100
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO records VALUES ('forbidden')")


@pytest.mark.parametrize("busy_timeout_ms", [0, -1, 5_001])
def test_busy_timeout_must_be_positive_and_bounded(
    tmp_path: Path,
    busy_timeout_ms: int,
) -> None:
    assert hasattr(ac, "SQLiteConnectionFactory"), (
        "V1-202 requires a top-level SQLiteConnectionFactory export"
    )

    with pytest.raises(ValueError, match="busy_timeout_ms"):
        ac.SQLiteConnectionFactory(tmp_path, busy_timeout_ms=busy_timeout_ms)
