#!/usr/bin/env python3
"""Verify the deterministic AstrContinuum AstrBot plugin archive."""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

ARCHIVE_PREFIX: Final = "astrbot_plugin_astrcontinuum"
MAX_ARCHIVE_SIZE: Final = 16 * 1024 * 1024
STABLE_ZIP_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)
ROOT_MEMBERS: Final = frozenset(
    {
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
)
ASSET_MEMBERS: Final = frozenset(
    {
        "astrcontinuum/tokenization/assets/cl100k_base.tiktoken",
        "astrcontinuum/tokenization/assets/o200k_base.tiktoken",
        "astrcontinuum/tokenization/assets/NOTICE.md",
        "astrcontinuum/tokenization/assets/TIKTOKEN_LICENSE",
    }
)
ASSET_MANIFEST: Final = {
    "astrcontinuum/tokenization/assets/cl100k_base.tiktoken": (
        1_681_126,
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
    "astrcontinuum/tokenization/assets/o200k_base.tiktoken": (
        3_613_922,
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    ),
}
FORBIDDEN_COMPONENTS: Final = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "htmlcov",
        "node_modules",
        "test",
        "tests",
        "venv",
    }
)
FORBIDDEN_SUFFIXES: Final = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".key",
    ".pem",
    ".pyo",
    ".pyc",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".wal",
    ".whl",
)
FORBIDDEN_FILENAMES: Final = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "master.key",
        "secret",
        "secrets",
    }
)
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_METADATA_VERSION = re.compile(r"^version\s*:\s*['\"]?([^'\"#\s]+)", re.MULTILINE)
_CHANGELOG_VERSION = re.compile(r"^## (v\d+\.\d+\.\d+)\b", re.MULTILINE)


class ArchiveContractError(ValueError):
    """The archive violates the public release contract."""


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    archive: Path
    compressed_size: int
    uncompressed_size: int
    sha256: str
    members: tuple[str, ...]
    version: str


def _read_text(archive: zipfile.ZipFile, member: str) -> str:
    archive_name = f"{ARCHIVE_PREFIX}/{member}"
    try:
        payload = archive.read(archive_name)
    except KeyError as error:
        raise ArchiveContractError(f"required member is missing: {member}") from error
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArchiveContractError(f"release text is not UTF-8: {member}") from error


def _relative_member(name: str) -> str:
    if "\\" in name or name.startswith("/") or _DRIVE_PATH.match(name):
        raise ArchiveContractError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveContractError(f"unsafe archive path: {name!r}")
    if len(path.parts) < 2 or path.parts[0] != ARCHIVE_PREFIX:
        raise ArchiveContractError(f"unexpected top-level path: {name!r}")
    return PurePosixPath(*path.parts[1:]).as_posix()


def _reject_sensitive_path(relative_name: str) -> None:
    path = PurePosixPath(relative_name)
    lowered_parts = tuple(part.lower() for part in path.parts)
    lowered_name = lowered_parts[-1]
    if any(part in FORBIDDEN_COMPONENTS for part in lowered_parts):
        raise ArchiveContractError(f"forbidden archive path: {relative_name!r}")
    if lowered_name.startswith("test_") or lowered_name in FORBIDDEN_FILENAMES:
        raise ArchiveContractError(f"forbidden archive filename: {relative_name!r}")
    if lowered_name.endswith(FORBIDDEN_SUFFIXES):
        raise ArchiveContractError(f"forbidden archive filename: {relative_name!r}")


def _validate_allowlist(relative_name: str) -> None:
    _reject_sensitive_path(relative_name)
    if relative_name in ROOT_MEMBERS or relative_name in ASSET_MEMBERS:
        return
    if (
        relative_name.startswith("astrcontinuum/")
        and relative_name.endswith(".py")
        and PurePosixPath(relative_name).name
    ):
        return
    if relative_name.endswith(".tiktoken"):
        raise ArchiveContractError(f"unapproved tokenizer asset: {relative_name!r}")
    raise ArchiveContractError(f"member is outside the release allowlist: {relative_name!r}")


def _register_version(main_source: str) -> str:
    try:
        module = ast.parse(main_source)
    except SyntaxError as error:
        raise ArchiveContractError("main.py is not valid Python") from error
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != "AstrContinuumPlugin":
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "register"
                and len(decorator.args) >= 4
            ):
                try:
                    value = ast.literal_eval(decorator.args[3])
                except (TypeError, ValueError) as error:
                    raise ArchiveContractError("main.py register version is not literal") from error
                if isinstance(value, str):
                    return value
    raise ArchiveContractError("main.py register version is missing")


def _validate_versions(archive: zipfile.ZipFile, expected_version: str) -> None:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", expected_version):
        raise ArchiveContractError("expected version must use vMAJOR.MINOR.PATCH")
    metadata = _read_text(archive, "metadata.yaml")
    metadata_match = _METADATA_VERSION.search(metadata)
    if metadata_match is None or metadata_match.group(1) != expected_version:
        raise ArchiveContractError("metadata version does not match expected version")

    package_version = expected_version.removeprefix("v")
    if _register_version(_read_text(archive, "main.py")) != package_version:
        raise ArchiveContractError("main.py register version does not match expected version")

    badge = re.compile(rf"version-{re.escape(expected_version)}-[A-Za-z0-9]+")
    for readme in ("README.md", "README.zh-CN.md"):
        if badge.search(_read_text(archive, readme)) is None:
            raise ArchiveContractError(f"{readme} version badge does not match expected version")

    changelog_match = _CHANGELOG_VERSION.search(_read_text(archive, "CHANGELOG.md"))
    if changelog_match is None or changelog_match.group(1) != expected_version:
        raise ArchiveContractError("CHANGELOG newest version does not match expected version")


def _validate_assets(archive: zipfile.ZipFile) -> None:
    for relative_name, (expected_size, expected_digest) in ASSET_MANIFEST.items():
        archive_name = f"{ARCHIVE_PREFIX}/{relative_name}"
        try:
            payload = archive.read(archive_name)
        except KeyError as error:
            raise ArchiveContractError(
                f"required tokenizer asset is missing: {relative_name}"
            ) from error
        if len(payload) != expected_size:
            raise ArchiveContractError(f"tokenizer asset size mismatch: {relative_name}")
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ArchiveContractError(f"tokenizer asset digest mismatch: {relative_name}")


def verify_plugin_archive(
    archive_path: Path | str,
    *,
    expected_version: str,
) -> ArchiveReport:
    """Verify archive paths, contents, versions, assets, and market size."""

    path = Path(archive_path).resolve()
    if not path.is_file():
        raise ArchiveContractError(f"archive does not exist: {path}")
    compressed_size = path.stat().st_size
    if compressed_size >= MAX_ARCHIVE_SIZE:
        raise ArchiveContractError(
            f"archive is {compressed_size} bytes; limit is below {MAX_ARCHIVE_SIZE}"
        )

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos:
                raise ArchiveContractError("archive is empty")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ArchiveContractError("archive contains duplicate members")
            if names != sorted(names):
                raise ArchiveContractError("archive members are not sorted")

            relative_names: list[str] = []
            for info in infos:
                if info.is_dir():
                    raise ArchiveContractError("directory entries are not permitted")
                if info.flag_bits & 0x1:
                    raise ArchiveContractError("encrypted ZIP members are not permitted")
                if info.date_time != STABLE_ZIP_TIMESTAMP:
                    raise ArchiveContractError("archive timestamp is not deterministic")
                relative_name = _relative_member(info.filename)
                _validate_allowlist(relative_name)
                relative_names.append(relative_name)

            required = ROOT_MEMBERS | ASSET_MEMBERS | {"astrcontinuum/__init__.py"}
            missing = sorted(required.difference(relative_names))
            if missing:
                raise ArchiveContractError(f"required members are missing: {missing}")

            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise ArchiveContractError(f"archive CRC failed: {corrupt_member}")
            _validate_assets(archive)
            _validate_versions(archive, expected_version)
            uncompressed_size = sum(info.file_size for info in infos)
    except zipfile.BadZipFile as error:
        raise ArchiveContractError("archive is not a valid ZIP file") from error

    return ArchiveReport(
        archive=path,
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        members=tuple(names),
        version=expected_version,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = verify_plugin_archive(
            args.archive,
            expected_version=args.expected_version,
        )
    except ArchiveContractError as error:
        print(f"archive verification failed: {error}")
        return 1
    print(f"archive={report.archive}")
    print(f"members={len(report.members)}")
    print(f"compressed_bytes={report.compressed_size}")
    print(f"uncompressed_bytes={report.uncompressed_size}")
    print(f"sha256={report.sha256}")
    print(f"version={report.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
