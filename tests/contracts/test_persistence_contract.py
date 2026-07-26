from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_doc(name: str) -> str:
    return (ROOT / "docs" / name).read_text("utf-8")


def test_database_materializes_capsules_and_ordered_membership() -> None:
    database = read_doc("DATABASE_SCHEMA.md")

    assert "## `capsules`" in database
    assert "## `snapshot_capsules`" in database
    assert "PRIMARY KEY (`snapshot_id`, `ordinal`)" in database
    assert "UNIQUE (`snapshot_id`, `capsule_id`)" in database


def test_publish_rolls_back_all_candidate_content_on_cas_loss() -> None:
    corpus = "\n".join(
        [
            read_doc("ADR-007-V1-CAPSULE-PERSISTENCE.md"),
            read_doc("DATA_FLOW.md"),
            read_doc("CONCURRENCY_STATE_MACHINE.md"),
        ]
    )

    for required in (
        "capsules",
        "snapshot_capsules",
        "same savepoint",
        "roll back",
        "SUPERSEDED",
    ):
        assert required in corpus


def test_test_matrix_requires_capsule_integrity_and_crash_checks() -> None:
    matrix = read_doc("TEST_MATRIX.md")

    assert "Capsule closed envelope" in matrix
    assert "Capsule membership integrity" in matrix
    assert "Candidate Capsule rollback" in matrix
    assert "Narrative-only regression" in matrix
