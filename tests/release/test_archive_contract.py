from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_PREFIX = "astrbot_plugin_astrcontinuum"
ROOT_MEMBERS = {
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "README.zh-CN.md",
    "CHANGELOG.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "logo.png",
}
ASSET_MEMBERS = {
    "astrcontinuum/tokenization/assets/cl100k_base.tiktoken",
    "astrcontinuum/tokenization/assets/o200k_base.tiktoken",
    "astrcontinuum/tokenization/assets/NOTICE.md",
    "astrcontinuum/tokenization/assets/TIKTOKEN_LICENSE",
}


def _load_script(name: str, relative_path: str) -> ModuleType:
    path = ROOT / relative_path
    assert path.is_file(), f"{relative_path} is missing"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _builder() -> ModuleType:
    return _load_script("release_archive_builder", "scripts/build_plugin_archive.py")


def _verifier() -> ModuleType:
    return _load_script("release_archive_verifier", "scripts/verify_plugin_archive.py")


def _expected_members() -> set[str]:
    package_python = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "astrcontinuum").rglob("*.py")
        if path.is_file()
    }
    relative_members = ROOT_MEMBERS | ASSET_MEMBERS | package_python
    return {f"{ARCHIVE_PREFIX}/{member}" for member in relative_members}


def _copy_release_source(destination: Path) -> Path:
    source = destination / "source"
    source.mkdir(parents=True)
    for relative_path in ROOT_MEMBERS | {"pyproject.toml", "uv.lock"}:
        target = source / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative_path, target)
    shutil.copytree(ROOT / "astrcontinuum", source / "astrcontinuum")
    return source


def test_archive_is_deterministic_strict_and_below_market_limit(tmp_path: Path) -> None:
    builder = _builder()
    verifier = _verifier()
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    builder.build_plugin_archive(source_root=ROOT, output_path=first)
    builder.build_plugin_archive(source_root=ROOT, output_path=second)

    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_size < 16 * 1024 * 1024
    assert (
        hashlib.sha256(first.read_bytes()).hexdigest()
        == hashlib.sha256(second.read_bytes()).hexdigest()
    )

    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == sorted(_expected_members())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
        assert {name.split("/", 1)[0] for name in archive.namelist()} == {ARCHIVE_PREFIX}

    report = verifier.verify_plugin_archive(first, expected_version="v0.3.0")
    assert report.members == tuple(sorted(_expected_members()))
    assert report.compressed_size == first.stat().st_size
    assert report.uncompressed_size > report.compressed_size


def test_builder_ignores_untracked_venv_but_rejects_unapproved_tokenizer(
    tmp_path: Path,
) -> None:
    builder = _builder()
    source = _copy_release_source(tmp_path)
    ignored_venv = source / ".venv/Lib/site-packages/ignored.py"
    ignored_venv.parent.mkdir(parents=True)
    ignored_venv.write_text("ignored = True\n", encoding="utf-8")

    builder.build_plugin_archive(source_root=source, output_path=tmp_path / "clean.zip")

    unapproved = source / "astrcontinuum/tokenization/assets/unapproved.tiktoken"
    unapproved.write_bytes(b"not approved")
    with pytest.raises(builder.ArchiveContractError, match="unapproved tokenizer"):
        builder.build_plugin_archive(source_root=source, output_path=tmp_path / "bad.zip")


@pytest.mark.parametrize(
    "member",
    [
        "../escape.py",
        "/absolute.py",
        "C:/absolute.py",
        f"{ARCHIVE_PREFIX}/tests/test_leak.py",
        f"{ARCHIVE_PREFIX}/.git/config",
        f"{ARCHIVE_PREFIX}/.venv/pyvenv.cfg",
        f"{ARCHIVE_PREFIX}/astrcontinuum/__pycache__/module.pyc",
        f"{ARCHIVE_PREFIX}/session.sqlite3",
        f"{ARCHIVE_PREFIX}/session.db-wal",
        f"{ARCHIVE_PREFIX}/master.key",
        f"{ARCHIVE_PREFIX}/astrcontinuum/tokenization/assets/unapproved.tiktoken",
    ],
)
def test_verifier_rejects_unsafe_or_unapproved_members(
    tmp_path: Path,
    member: str,
) -> None:
    verifier = _verifier()
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"unsafe")

    with pytest.raises(verifier.ArchiveContractError):
        verifier.verify_plugin_archive(archive_path, expected_version="v0.3.0")


def test_verifier_rejects_version_mismatch(tmp_path: Path) -> None:
    builder = _builder()
    verifier = _verifier()
    archive_path = tmp_path / "plugin.zip"
    builder.build_plugin_archive(source_root=ROOT, output_path=archive_path)

    with pytest.raises(verifier.ArchiveContractError, match="version"):
        verifier.verify_plugin_archive(archive_path, expected_version="v9.9.9")
