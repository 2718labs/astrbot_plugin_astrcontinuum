from __future__ import annotations

from pathlib import Path


def test_license_contains_the_complete_agpl_v3_text() -> None:
    license_text = (Path(__file__).resolve().parents[1] / "LICENSE").read_text(
        encoding="utf-8"
    )

    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 19 November 2007" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
