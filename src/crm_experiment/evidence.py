from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

MANIFEST_SCHEMA_VERSION = 1
PROTECTED_PATTERNS = (
    "astrcontinuum/domain/capsules.py",
    "astrcontinuum/domain/validation.py",
    "astrcontinuum/compaction/compiler.py",
    "astrcontinuum/runtime/*.py",
    "astrcontinuum/storage/*.py",
    "astrcontinuum/adapters/*.py",
    "main.py",
    "pyproject.toml",
    "experiments/*.py",
    "experiments/*.json",
    "tests/**/*.py",
)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file's raw bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_protected_hashes(
    root: Path,
    patterns: Iterable[str],
) -> list[dict[str, object]]:
    """Collect sorted, deduplicated metadata for files selected by glob."""
    selected: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                selected[path.relative_to(root).as_posix()] = path

    return [
        {
            "path": relative,
            "bytes": selected[relative].stat().st_size,
            "sha256": sha256_file(selected[relative]),
        }
        for relative in sorted(selected)
    ]


def write_manifest(repo: Path, output: Path) -> None:
    """Write a metadata-only checkpoint of protected production files."""
    protected_root = repo.resolve()
    manifest_output = output.resolve()
    if manifest_output.is_relative_to(protected_root):
        raise ValueError("manifest output must be outside the protected repository")

    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "repo": str(protected_root),
        "files": collect_protected_hashes(repo, PROTECTED_PATTERNS),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_manifest(repo: Path, manifest: Path) -> bool:
    """Return whether a protected-source manifest matches the repository."""
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(recorded, dict):
        return False
    return (
        recorded.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and recorded.get("repo") == str(repo.resolve())
        and recorded.get("files") == collect_protected_hashes(repo, PROTECTED_PATTERNS)
    )


def main() -> int:
    """Write or verify a protected-source manifest from the command line."""
    parser = argparse.ArgumentParser(
        description="Write or verify protected-source metadata.",
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        return 0 if verify_manifest(args.repo, args.manifest) else 1
    write_manifest(args.repo, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
