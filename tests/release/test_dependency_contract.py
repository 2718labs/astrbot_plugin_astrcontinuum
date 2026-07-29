from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
TIKTOKEN_REQUIREMENT = "tiktoken>=0.12,<0.14"
VENDORED_VALIDATOR_SHA256 = "57fb4d2005e631638b1b62cc4641eef809da1f79d6cb72d12cd469dbf06ec77c"
ASSETS = {
    "astrcontinuum/tokenization/assets/cl100k_base.tiktoken": (
        1_681_126,
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
    "astrcontinuum/tokenization/assets/o200k_base.tiktoken": (
        3_613_922,
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    ),
}


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        return tomllib.load(source)


def _root_lock_package() -> dict[str, object]:
    packages = _read_toml(ROOT / "uv.lock")["package"]
    assert isinstance(packages, list)
    return next(
        package
        for package in packages
        if isinstance(package, dict) and package.get("name") == "astrbot-plugin-astrcontinuum"
    )


def _requirement_lines(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_tiktoken_dependency_range_matches_all_release_surfaces() -> None:
    project = _read_toml(ROOT / "pyproject.toml")["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)
    assert [item for item in dependencies if str(item).startswith("tiktoken")] == [
        TIKTOKEN_REQUIREMENT
    ]

    requirements = _requirement_lines(ROOT / "requirements.txt")
    assert sorted(item for item in requirements if item.startswith("tiktoken")) == [
        TIKTOKEN_REQUIREMENT
    ]

    metadata = _root_lock_package()["metadata"]
    assert isinstance(metadata, dict)
    locked_requirements = metadata["requires-dist"]
    assert isinstance(locked_requirements, list)
    tiktoken = [
        requirement
        for requirement in locked_requirements
        if isinstance(requirement, dict) and requirement.get("name") == "tiktoken"
    ]
    assert tiktoken == [{"name": "tiktoken", "specifier": ">=0.12,<0.14"}]


def test_offline_tokenizer_assets_are_pinned_binary_files() -> None:
    for relative_path, (expected_size, expected_digest) in ASSETS.items():
        path = ROOT / relative_path
        payload = path.read_bytes()
        assert len(payload) == expected_size
        assert hashlib.sha256(payload).hexdigest() == expected_digest

        result = subprocess.run(
            ["git", "check-attr", "text", "--", relative_path],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip().endswith(": text: unset")


def test_vendored_devkit_validator_is_an_exact_pinned_snapshot() -> None:
    validator = ROOT / "scripts/vendor/2718lab_validate_plugin.py"
    assert validator.is_file(), "vendored 2718lab validator is missing"
    assert hashlib.sha256(validator.read_bytes()).hexdigest() == VENDORED_VALIDATOR_SHA256


def test_third_party_notices_cover_tiktoken_assets_and_validator() -> None:
    notices_path = ROOT / "THIRD_PARTY_NOTICES.md"
    assert notices_path.is_file(), "THIRD_PARTY_NOTICES.md is missing"
    notices = notices_path.read_text(encoding="utf-8")
    assert "tiktoken" in notices
    assert "cl100k_base.tiktoken" in notices
    assert "o200k_base.tiktoken" in notices
    assert VENDORED_VALIDATOR_SHA256 in notices
    assert "AGPL-3.0" in notices
    assert "strict release ZIP contains the `tiktoken` encoding assets" in notices
    assert "CI-only validator" in notices
    assert "not a release-ZIP member" in notices


def test_astrbot_probe_has_a_dependency_free_help_path() -> None:
    probe = ROOT / "scripts/probe_astrbot.py"
    assert probe.is_file(), "AstrBot probe is missing"
    result = subprocess.run(
        [sys.executable, str(probe), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "without sending an LLM request" in result.stdout
