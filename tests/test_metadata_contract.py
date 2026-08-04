from __future__ import annotations

import re
from pathlib import Path


def test_metadata_declares_the_conservative_verified_astrbot_floor() -> None:
    metadata = (Path(__file__).resolve().parents[1] / "metadata.yaml").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^astrbot_version:\s*"(?P<value>[^"]+)"\s*$', metadata, re.MULTILINE)

    assert match is not None
    assert match.group("value") == ">=4.24.2,<5.0.0"


def test_metadata_points_to_the_maintained_repository() -> None:
    metadata = (Path(__file__).resolve().parents[1] / "metadata.yaml").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^repo:\s*"(?P<value>[^"]+)"\s*$', metadata, re.MULTILINE)

    assert match is not None
    assert match.group("value") == "https://github.com/2718labs/astrbot_plugin_astrcontinuum"
