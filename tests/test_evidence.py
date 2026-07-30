import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from unittest import SkipTest

import crm_experiment.evidence as evidence_module
from crm_experiment.evidence import (
    collect_protected_hashes,
    sha256_file,
    verify_manifest,
    write_manifest,
)

BOUNDARY_ERROR = "manifest output must be outside the protected repository"
PROTECTED_PATH_ERROR = "protected file resolves outside the protected repository"


def _assert_manifest_boundary_rejected(repo: Path, output: Path) -> None:
    try:
        write_manifest(repo, output)
    except ValueError as error:
        assert str(error) == BOUNDARY_ERROR
    else:
        raise AssertionError("write_manifest accepted a protected output path")


def test_sha256_file_hashes_raw_bytes(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    payload = b"\x00capsule\xff"
    source.write_bytes(payload)

    assert sha256_file(source) == sha256(payload).hexdigest()


def test_collect_protected_hashes_is_sorted_deduplicated_and_portable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    nested = root / "nested"
    nested.mkdir(parents=True)
    root_payload = b"b"
    nested_payload = "é".encode()
    (root / "b.py").write_bytes(root_payload)
    (nested / "a.py").write_bytes(nested_payload)

    rows = collect_protected_hashes(
        root,
        ("**/*.py", "nested/*.py", "*.py"),
    )

    assert rows == [
        {
            "path": "b.py",
            "bytes": len(root_payload),
            "sha256": sha256(root_payload).hexdigest(),
        },
        {
            "path": "nested/a.py",
            "bytes": len(nested_payload),
            "sha256": sha256(nested_payload).hexdigest(),
        },
    ]
    for row in rows:
        digest = row["sha256"]
        assert isinstance(digest, str)
        assert len(digest) == 64


def test_protected_path_helper_rejects_outside_file_without_links(
    tmp_path: Path,
) -> None:
    protected_repo = tmp_path / "protected-repo"
    protected_repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside", encoding="utf-8")
    resolver = getattr(evidence_module, "_resolve_protected_file", None)
    assert callable(resolver), "protected path resolver is missing"

    try:
        resolver(protected_repo, outside)
    except ValueError as error:
        assert str(error) == PROTECTED_PATH_ERROR
    else:
        raise AssertionError("protected path resolver accepted an outside file")


def test_collect_protected_hashes_rejects_symlink_to_outside_file(
    tmp_path: Path,
) -> None:
    protected_repo = tmp_path / "protected-repo"
    protected_repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside", encoding="utf-8")
    disguised = protected_repo / "disguised.py"
    try:
        disguised.symlink_to(outside)
    except (NotImplementedError, OSError) as error:
        raise SkipTest("file symlinks are unavailable in this environment") from error

    try:
        collect_protected_hashes(protected_repo, ("*.py",))
    except ValueError as error:
        assert str(error) == PROTECTED_PATH_ERROR
    else:
        raise AssertionError("protected hash collection accepted an outside symlink")


def test_write_manifest_rejects_output_inside_protected_repo(tmp_path: Path) -> None:
    protected_repo = tmp_path / "protected-repo"
    protected_repo.mkdir()
    manifest = protected_repo / "evidence.json"

    _assert_manifest_boundary_rejected(protected_repo, manifest)

    assert not manifest.exists()


def test_write_manifest_rejects_protected_repo_as_output(tmp_path: Path) -> None:
    protected_repo = tmp_path / "protected-repo"
    protected_repo.mkdir()

    _assert_manifest_boundary_rejected(protected_repo, protected_repo)


def test_write_manifest_contains_metadata_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source_body = "protected source body"
    source = repo / "main.py"
    source.write_text(source_body, encoding="utf-8")
    manifest = tmp_path / "evidence" / "protected.json"

    write_manifest(repo, manifest)

    serialized = manifest.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert set(payload) == {"schema_version", "repo", "files"}
    assert payload["schema_version"] == 1
    assert payload["repo"] == str(repo.resolve())
    assert payload["files"] == [
        {
            "path": "main.py",
            "bytes": source.stat().st_size,
            "sha256": sha256(source.read_bytes()).hexdigest(),
        }
    ]
    assert set(payload["files"][0]) == {"path", "bytes", "sha256"}
    assert source_body not in serialized


def test_verify_manifest_detects_changes_and_schema_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "main.py"
    source.write_text("original", encoding="utf-8")
    manifest = tmp_path / "protected.json"
    write_manifest(repo, manifest)

    assert verify_manifest(repo, manifest)

    source.write_text("changed", encoding="utf-8")
    assert not verify_manifest(repo, manifest)

    source.write_text("original", encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert not verify_manifest(repo, manifest)


def test_verify_manifest_returns_false_for_missing_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    assert not verify_manifest(repo, tmp_path / "missing.json")


def test_verify_manifest_returns_false_for_unreadable_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    unreadable = tmp_path / "manifest.json"
    unreadable.mkdir()

    assert not verify_manifest(repo, unreadable)


def test_verify_manifest_returns_false_for_non_utf8_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"\xff")

    assert not verify_manifest(repo, manifest)


def test_verify_manifest_returns_false_for_damaged_json(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{not-json", encoding="utf-8")

    assert not verify_manifest(repo, manifest)


def test_cli_writes_and_verifies_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "main.py"
    source.write_text("checkpoint", encoding="utf-8")
    manifest = tmp_path / "protected.json"
    base_command = [
        sys.executable,
        "-m",
        "crm_experiment.evidence",
        "--repo",
        str(repo),
        "--manifest",
        str(manifest),
    ]

    written = subprocess.run(base_command, capture_output=True, text=True, check=False)
    verified = subprocess.run(
        [*base_command, "--verify"],
        capture_output=True,
        text=True,
        check=False,
    )
    source.write_text("mutated", encoding="utf-8")
    mismatch = subprocess.run(
        [*base_command, "--verify"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert written.returncode == 0, written.stderr
    assert manifest.is_file()
    assert verified.returncode == 0, verified.stderr
    assert mismatch.returncode == 1
