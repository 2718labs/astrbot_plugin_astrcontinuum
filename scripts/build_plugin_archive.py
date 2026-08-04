#!/usr/bin/env python3
"""Build the deterministic AstrContinuum AstrBot plugin archive."""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import zipfile
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
_METADATA_VERSION = re.compile(r"^version\s*:\s*['\"]?([^'\"#\s]+)", re.MULTILINE)
_PROJECT_VERSION = re.compile(
    r"^\[project\]\s*$.*?^version\s*=\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE | re.DOTALL,
)
_LOCK_ROOT_VERSION = re.compile(
    r"\[\[package\]\]\s+name\s*=\s*\"astrbot-plugin-astrcontinuum\"\s+"
    r"version\s*=\s*\"([^\"]+)\"",
    re.MULTILINE,
)
_CHANGELOG_VERSION = re.compile(r"^## (v\d+\.\d+\.\d+)\b", re.MULTILINE)


class ArchiveContractError(ValueError):
    """The source tree violates the public release contract."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ArchiveContractError(f"release text is not UTF-8: {path.name}") from error


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


def _source_version(source_root: Path) -> str:
    metadata = _read_text(source_root / "metadata.yaml")
    match = _METADATA_VERSION.search(metadata)
    if match is None or not re.fullmatch(r"v\d+\.\d+\.\d+", match.group(1)):
        raise ArchiveContractError("metadata version must use vMAJOR.MINOR.PATCH")
    version = match.group(1)
    package_version = version.removeprefix("v")

    if _register_version(_read_text(source_root / "main.py")) != package_version:
        raise ArchiveContractError("main.py register version does not match metadata")

    project_match = _PROJECT_VERSION.search(_read_text(source_root / "pyproject.toml"))
    if project_match is None or project_match.group(1) != package_version:
        raise ArchiveContractError("pyproject version does not match metadata")

    lock_match = _LOCK_ROOT_VERSION.search(_read_text(source_root / "uv.lock"))
    if lock_match is None or lock_match.group(1) != package_version:
        raise ArchiveContractError("uv.lock root package version does not match metadata")

    badge = re.compile(rf"version-{re.escape(version)}-[A-Za-z0-9]+")
    for readme in ("README.md", "README.zh-CN.md"):
        if badge.search(_read_text(source_root / readme)) is None:
            raise ArchiveContractError(f"{readme} version badge does not match metadata")

    changelog_match = _CHANGELOG_VERSION.search(_read_text(source_root / "CHANGELOG.md"))
    if changelog_match is None or changelog_match.group(1) != version:
        raise ArchiveContractError("CHANGELOG newest version does not match metadata")
    return version


def _reject_sensitive_path(relative_name: str) -> None:
    path = PurePosixPath(relative_name)
    lowered_parts = tuple(part.lower() for part in path.parts)
    lowered_name = lowered_parts[-1]
    if any(part in FORBIDDEN_COMPONENTS for part in lowered_parts):
        raise ArchiveContractError(f"forbidden release path: {relative_name!r}")
    if lowered_name.startswith("test_"):
        raise ArchiveContractError(f"forbidden release filename: {relative_name!r}")


def _approved_paths(source_root: Path) -> list[tuple[str, Path]]:
    package_root = source_root / "astrcontinuum"
    if not package_root.is_dir():
        raise ArchiveContractError("astrcontinuum package directory is missing")

    approved_assets = {
        source_root / member for member in ASSET_MEMBERS if member.endswith(".tiktoken")
    }
    discovered_assets = {path for path in package_root.rglob("*.tiktoken") if path.is_file()}
    unapproved_assets = sorted(path for path in discovered_assets if path not in approved_assets)
    if unapproved_assets:
        relative = [path.relative_to(source_root).as_posix() for path in unapproved_assets]
        raise ArchiveContractError(f"unapproved tokenizer assets: {relative}")

    relative_names = set(ROOT_MEMBERS | ASSET_MEMBERS)
    relative_names.update(
        path.relative_to(source_root).as_posix()
        for path in package_root.rglob("*.py")
        if path.is_file()
    )
    relative_names.add("astrcontinuum/__init__.py")

    approved: list[tuple[str, Path]] = []
    for relative_name in sorted(relative_names):
        _reject_sensitive_path(relative_name)
        source_path = source_root / PurePosixPath(relative_name)
        if not source_path.is_file():
            raise ArchiveContractError(f"required release member is missing: {relative_name}")
        if source_path.is_symlink():
            raise ArchiveContractError(f"symbolic links are not permitted: {relative_name}")
        try:
            source_path.resolve().relative_to(source_root)
        except ValueError as error:
            raise ArchiveContractError(
                f"release path escapes source root: {relative_name}"
            ) from error
        approved.append((f"{ARCHIVE_PREFIX}/{relative_name}", source_path))
    return approved


def _validate_assets(source_root: Path) -> None:
    for relative_name, (expected_size, expected_digest) in ASSET_MANIFEST.items():
        payload = (source_root / relative_name).read_bytes()
        if len(payload) != expected_size:
            raise ArchiveContractError(f"tokenizer asset size mismatch: {relative_name}")
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise ArchiveContractError(f"tokenizer asset digest mismatch: {relative_name}")


def build_plugin_archive(
    *,
    source_root: Path | str,
    output_path: Path | str,
) -> Path:
    """Build a sorted, fixed-timestamp, strict-allowlist ZIP archive."""

    root = Path(source_root).resolve()
    output = Path(output_path).resolve()
    if not root.is_dir():
        raise ArchiveContractError(f"source root does not exist: {root}")
    output.parent.mkdir(parents=True, exist_ok=True)

    _source_version(root)
    _validate_assets(root)
    approved = _approved_paths(root)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for archive_name, source_path in approved:
                info = zipfile.ZipInfo(archive_name, date_time=STABLE_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o100644 & 0xFFFF) << 16
                archive.writestr(info, source_path.read_bytes(), compresslevel=9)
        if temporary.stat().st_size >= MAX_ARCHIVE_SIZE:
            raise ArchiveContractError(
                f"archive is {temporary.stat().st_size} bytes; limit is below {MAX_ARCHIVE_SIZE}"
            )
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        archive = build_plugin_archive(
            source_root=args.source_root,
            output_path=args.output,
        )
    except ArchiveContractError as error:
        print(f"archive build failed: {error}")
        return 1

    with zipfile.ZipFile(archive) as built:
        uncompressed_size = sum(info.file_size for info in built.infolist())
        member_count = len(built.infolist())
    payload = archive.read_bytes()
    print(f"archive={archive}")
    print(f"members={member_count}")
    print(f"compressed_bytes={len(payload)}")
    print(f"uncompressed_bytes={uncompressed_size}")
    print(f"sha256={hashlib.sha256(payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
