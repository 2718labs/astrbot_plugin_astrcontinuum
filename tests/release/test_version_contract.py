from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PACKAGE_VERSION = "0.3.0"
EXPECTED_RELEASE_VERSION = f"v{EXPECTED_PACKAGE_VERSION}"
README_PATHS = (ROOT / "README.md", ROOT / "README.zh-CN.md")
DOCUMENTATION_PATHS = (
    *README_PATHS,
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "docs/CONFIGURATION.md",
    ROOT / "docs/CONFIGURATION.zh-CN.md",
    ROOT / "docs/ARCHITECTURE.md",
    ROOT / "docs/ARCHITECTURE.zh-CN.md",
    ROOT / "docs/ASTRBOT_INTEGRATION.md",
    ROOT / "docs/ASTRBOT_INTEGRATION.zh-CN.md",
    ROOT / "docs/DATABASE_SCHEMA.md",
    ROOT / "docs/DATA_FLOW.md",
    ROOT / "docs/TEST_MATRIX.md",
    ROOT / "docs/ROADMAP.md",
    ROOT / "docs/ROADMAP.zh-CN.md",
)


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        return tomllib.load(source)


def _register_version() -> str:
    module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    plugin_class = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "AstrContinuumPlugin"
    )
    register = next(
        decorator
        for decorator in plugin_class.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "register"
    )
    assert len(register.args) >= 4
    version = ast.literal_eval(register.args[3])
    assert isinstance(version, str)
    return version


def _root_lock_package() -> dict[str, object]:
    packages = _read_toml(ROOT / "uv.lock")["package"]
    assert isinstance(packages, list)
    return next(
        package
        for package in packages
        if isinstance(package, dict) and package.get("name") == "astrbot-plugin-astrcontinuum"
    )


def test_v030_version_is_consistent_across_release_surfaces() -> None:
    metadata = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    project = _read_toml(ROOT / "pyproject.toml")["project"]
    assert isinstance(project, dict)

    assert _register_version() == EXPECTED_PACKAGE_VERSION
    assert metadata["version"] == EXPECTED_RELEASE_VERSION
    assert project["version"] == EXPECTED_PACKAGE_VERSION
    assert _root_lock_package()["version"] == EXPECTED_PACKAGE_VERSION

    for readme_path in README_PATHS:
        readme = readme_path.read_text(encoding="utf-8")
        assert f"version-{EXPECTED_RELEASE_VERSION}-A44742" in readme

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    english_heading = re.search(r"^## (v\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
    chinese_heading = re.search(r"^### (v\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
    assert english_heading is not None
    assert chinese_heading is not None
    assert english_heading.group(1) == EXPECTED_RELEASE_VERSION
    assert chinese_heading.group(1) == EXPECTED_RELEASE_VERSION


def test_ci_release_archive_contract_matches_public_version() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert f"astrbot_plugin_astrcontinuum-{EXPECTED_RELEASE_VERSION}.zip" in workflow
    assert f"--expected-version {EXPECTED_RELEASE_VERSION}" in workflow


def test_readmes_link_bounded_evidence_without_presenting_gate_a_as_a_result() -> None:
    for readme_path in README_PATHS:
        text = readme_path.read_text(encoding="utf-8")
        assert not any(line.strip() == "$$" for line in text.splitlines())
        assert "docs/assets/v03-update-wiring-gate-a.svg" not in text
        assert "docs/assets/evidence-r2-outcomes-rmb.svg" not in text
        assert "CompactionWorker" in text
        assert (
            "Gate A 受限接线验证" in text
            if readme_path.name.endswith(".zh-CN.md")
            else "Gate A opt-in wiring verification" in text
        )
        assert (
            "技术预览" in text
            if readme_path.name.endswith(".zh-CN.md")
            else "Technical Preview" in text
        )
        assert (
            "v0.3 前的基线" in text
            if readme_path.name.endswith(".zh-CN.md")
            else "pre-v0.3 baseline" in text
        )


def test_r2_evidence_figure_exporter_reproduces_committed_svg(tmp_path: Path) -> None:
    output_path = tmp_path / "evidence-r2-outcomes-rmb.svg"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/render_r2_evidence_figure.py",
            "--input",
            "docs/evidence/frozen-r2-outcomes.csv",
            "--output",
            str(output_path),
        ],
        capture_output=True,
        cwd=ROOT,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    figure_bytes = output_path.read_bytes()
    assert figure_bytes == (ROOT / "docs/assets/evidence-r2-outcomes-rmb.svg").read_bytes()
    assert b"\r\n" not in figure_bytes
    figure = figure_bytes.decode("utf-8")
    assert "Generated by scripts/render_r2_evidence_figure.py" in figure
    with (ROOT / "docs/evidence/frozen-r2-outcomes.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        outcomes = list(csv.DictReader(source))

    assert "Successful units / fixed denominator (%)" in figure
    assert "No uncertainty interval or hypothesis test is available" in figure
    assert "structural-only reference" in figure
    assert "release-to-release performance comparison" in figure
    assert "Pre-v0.3 historical R2 aggregate" in figure
    assert "Source and provenance: docs/evidence/frozen-r2-outcomes.csv;" in figure

    for outcome in outcomes:
        denominator = int(outcome["denominator"])
        for metric in (
            "structural_valid",
            "answer_delivered",
            "claim_v2_end_to_end",
        ):
            if outcome[metric]:
                numerator = int(outcome[metric])
                assert f"{numerator}/{denominator} ({numerator / denominator * 100:.1f}%)" in figure


def test_versioned_evidence_figure_exporter_reproduces_committed_svg(tmp_path: Path) -> None:
    output_path = tmp_path / "evidence-r2-v03-evidence.svg"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/render_versioned_evidence_figure.py",
            "--r2-input",
            "docs/evidence/frozen-r2-outcomes.csv",
            "--gate-a-input",
            "docs/evidence/v03-update-gate-a/aggregate.csv",
            "--output",
            str(output_path),
        ],
        capture_output=True,
        cwd=ROOT,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    figure_bytes = output_path.read_bytes()
    assert figure_bytes == (ROOT / "docs/assets/evidence-r2-v03-evidence.svg").read_bytes()
    assert b"\r\n" not in figure_bytes
    figure = figure_bytes.decode("utf-8")
    assert "Pre-v0.3 / R2 historical evidence" in figure
    assert "v0.3 / Gate A wiring verification" in figure
    assert "NOT A PERFORMANCE COMPARISON" in figure
    assert "No shared outcome axis" in figure
    assert "Structural / ledger only; not an E2E outcome" in figure
    assert "E2E versus refreshed Summary: BLOCKED" in figure
    assert "Ordinary Provider runtime wired: false" in figure
    assert "Non-summary released records: 0" in figure
    assert "docs/evidence/frozen-r2-outcomes.csv" in figure
    assert "docs/evidence/v03-update-gate-a/aggregate.csv" in figure

    with (ROOT / "docs/evidence/frozen-r2-outcomes.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        r2_outcomes = list(csv.DictReader(source))
    for outcome in r2_outcomes:
        denominator = int(outcome["denominator"])
        assert (
            f"{outcome['structural_valid']}/{denominator} ({int(outcome['structural_valid']) / denominator * 100:.1f}%)"
            in figure
        )

    with (ROOT / "docs/evidence/v03-update-gate-a/aggregate.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        gate_a = next(csv.DictReader(source))
    for field in (
        "baseline_committed",
        "update_committed",
        "pointer_advanced",
        "durable_ledger",
        "required_core_edges",
    ):
        count = int(gate_a[field])
        denominator = int(gate_a["denominator"])
        assert f"{count} / {denominator} verified units" in figure


def test_versioned_evidence_figure_rejects_an_e2e_gate_a_claim(tmp_path: Path) -> None:
    gate_a_input = tmp_path / "invalid-gate-a.csv"
    gate_a_input.write_text(
        (ROOT / "docs/evidence/v03-update-gate-a/aggregate.csv")
        .read_text(encoding="utf-8")
        .replace(",BLOCKED,", ",REPORTED,", 1),
        encoding="utf-8",
        newline="\n",
    )
    output_path = tmp_path / "evidence.svg"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/render_versioned_evidence_figure.py",
            "--r2-input",
            "docs/evidence/frozen-r2-outcomes.csv",
            "--gate-a-input",
            str(gate_a_input),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        cwd=ROOT,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "e2e_summary_status must remain BLOCKED" in completed.stderr
    assert not output_path.exists()


def test_versioned_evidence_figure_is_documented_as_separate_versioned_evidence() -> None:
    for evidence_path in (ROOT / "docs/EVIDENCE.md", ROOT / "docs/EVIDENCE.zh-CN.md"):
        evidence = evidence_path.read_text(encoding="utf-8")
        assert "assets/evidence-r2-v03-evidence.svg" in evidence
        assert "BLOCKED" in evidence
        assert "performance comparison" in evidence or "性能比较" in evidence
    for manifest_path in (
        ROOT / "docs/evidence/README.md",
        ROOT / "docs/evidence/README.zh-CN.md",
    ):
        manifest = manifest_path.read_text(encoding="utf-8")
        assert "evidence-r2-v03-evidence.svg" in manifest
        assert "render_versioned_evidence_figure.py" in manifest


def test_committed_v03_gate_a_evidence_is_redacted_and_matches_its_aggregate() -> None:
    evidence_root = ROOT / "docs/evidence/v03-update-gate-a"
    aggregate_path = evidence_root / "aggregate.csv"
    receipt_csv_path = evidence_root / "receipt.csv"
    receipt_json_path = evidence_root / "receipt.json"
    with aggregate_path.open(encoding="utf-8", newline="") as source:
        aggregates = list(csv.DictReader(source))
    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert aggregate["experiment_id"] == "V03-WIRE-001"
    assert aggregate["scope"] == "synthetic_provider_free_wiring_gate"
    assert aggregate["execution_path"] == "opt_in_fenced_compaction_worker"
    assert aggregate["denominator"] == "36"
    for field in (
        "baseline_committed",
        "update_committed",
        "pointer_advanced",
        "durable_ledger",
        "required_core_edges",
    ):
        assert aggregate[field] == "36"
    assert aggregate["non_summary_released_record_count"] == "0"
    assert aggregate["ordinary_provider_runtime_reorganization_wired"] == "False"
    assert aggregate["e2e_summary_status"] == "BLOCKED"

    receipt = json.loads(receipt_json_path.read_text(encoding="utf-8"))
    assert receipt["fixture_sha256"] == aggregate["fixture_sha256"]
    assert receipt["result_sha256"] == aggregate["result_sha256"]
    assert receipt["ordinary_provider_runtime_reorganization_wired"] is False
    assert receipt["e2e_summary_status"] == "BLOCKED"
    assert len(receipt["rows"]) == 36
    assert all(row["status_code"] == "COMMITTED" for row in receipt["rows"])
    assert all(row["pointer_before"] == 1 for row in receipt["rows"])
    assert all(row["pointer_after"] == 2 for row in receipt["rows"])
    assert all(row["required_record_count"] >= 2 for row in receipt["rows"])
    assert all(row["non_summary_released_record_count"] == 0 for row in receipt["rows"])
    assert all(not row["failure_codes"] for row in receipt["rows"])

    with receipt_csv_path.open(encoding="utf-8", newline="") as source:
        receipt_csv_rows = list(csv.DictReader(source))
    assert len(receipt_csv_rows) == 36
    assert all(row["pointer_before"] == "1" for row in receipt_csv_rows)
    assert all(row["pointer_after"] == "2" for row in receipt_csv_rows)
    for path in (aggregate_path, receipt_csv_path, receipt_json_path):
        assert b"\r\n" not in path.read_bytes()

    scenarios = json.loads(
        (ROOT / "experiments/v03_update_scenarios.json").read_text(encoding="utf-8")
    )
    public_receipts = receipt_json_path.read_text(encoding="utf-8")
    assert all(item["initial_event"] not in public_receipts for item in scenarios["scenarios"])
    assert all(item["update_event"] not in public_receipts for item in scenarios["scenarios"])

    aggregate_hash = hashlib.sha256(aggregate_path.read_bytes()).hexdigest().upper()
    receipt_csv_hash = hashlib.sha256(receipt_csv_path.read_bytes()).hexdigest().upper()
    receipt_json_hash = hashlib.sha256(receipt_json_path.read_bytes()).hexdigest().upper()
    for evidence_path in (ROOT / "docs/EVIDENCE.md", ROOT / "docs/EVIDENCE.zh-CN.md"):
        evidence = evidence_path.read_text(encoding="utf-8")
        assert "v03-update-gate-a/aggregate.csv" in evidence
        assert aggregate_hash in evidence
        assert receipt_csv_hash in evidence
        assert receipt_json_hash in evidence
        assert "36 / 36" in evidence
        assert "BLOCKED" in evidence


def test_r2_evidence_figure_exporter_rejects_a_non_frozen_denominator(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "r2-outcomes.csv"
    with (ROOT / "docs/evidence/frozen-r2-outcomes.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames
        outcomes = list(reader)
    assert fieldnames is not None
    for outcome in outcomes:
        outcome["denominator"] = "40"
    with input_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(outcomes)
    output_path = tmp_path / "figure.svg"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/render_r2_evidence_figure.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        cwd=ROOT,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "fixed denominator must be 36" in completed.stderr
    assert not output_path.exists()


def test_r2_evidence_figure_exporter_never_overwrites_its_input(tmp_path: Path) -> None:
    input_path = tmp_path / "r2-outcomes.csv"
    source = (ROOT / "docs/evidence/frozen-r2-outcomes.csv").read_text(encoding="utf-8")
    input_path.write_text(source, encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/render_r2_evidence_figure.py",
            "--input",
            str(input_path),
            "--output",
            str(input_path),
        ],
        capture_output=True,
        cwd=ROOT,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "output path must differ from input path" in completed.stderr
    assert input_path.read_text(encoding="utf-8") == source


def test_r2_evidence_figure_exporter_rejects_malformed_or_unregistered_contract_rows(
    tmp_path: Path,
) -> None:
    source = (ROOT / "docs/evidence/frozen-r2-outcomes.csv").read_text(encoding="utf-8")
    invalid_sources = (
        source.replace(',21,"Frozen synthetic R2; research-only"\n', ",21\n", 1),
        source.replace(
            ',21,"Frozen synthetic R2; research-only"',
            ',21,"Frozen synthetic R2; research-only",extra',
            1,
        ),
        source.replace("Frozen synthetic R2; research-only", "unregistered scope note", 1),
    )

    for index, invalid_source in enumerate(invalid_sources):
        input_path = tmp_path / f"invalid-{index}.csv"
        output_path = tmp_path / f"invalid-{index}.svg"
        input_path.write_text(invalid_source, encoding="utf-8", newline="\n")

        completed = subprocess.run(
            [
                sys.executable,
                "scripts/render_r2_evidence_figure.py",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            capture_output=True,
            cwd=ROOT,
            text=True,
            check=False,
        )

        assert completed.returncode != 0
        assert "frozen R2 CSV contract violation" in completed.stderr
        assert not output_path.exists()


def test_r2_evidence_figure_exporter_labels_the_actual_input_source(tmp_path: Path) -> None:
    input_path = tmp_path / "approved-r2-aggregate.csv"
    output_path = tmp_path / "candidate-figure.svg"
    input_path.write_bytes((ROOT / "docs/evidence/frozen-r2-outcomes.csv").read_bytes())

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/render_r2_evidence_figure.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        cwd=ROOT,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    figure = output_path.read_text(encoding="utf-8")
    assert f"Source and provenance: {input_path.resolve().as_posix()};" in figure


def test_r2_evidence_tables_match_the_committed_aggregate() -> None:
    with (ROOT / "docs/evidence/frozen-r2-outcomes.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        outcomes = list(csv.DictReader(source))

    for evidence_path in (ROOT / "docs/EVIDENCE.md", ROOT / "docs/EVIDENCE.zh-CN.md"):
        evidence = evidence_path.read_text(encoding="utf-8")
        for outcome in outcomes:
            values = [
                f"{outcome['structural_valid']} / {outcome['denominator']}",
                (
                    f"{outcome['answer_delivered']} / {outcome['denominator']}"
                    if outcome["answer_delivered"]
                    else "n/a"
                ),
                (
                    f"{outcome['claim_v2_end_to_end']} / {outcome['denominator']}"
                    if outcome["claim_v2_end_to_end"]
                    else "n/a"
                ),
            ]
            assert f"| {outcome['arm']} | {' | '.join(values)} |" in evidence


def test_readmes_link_public_configuration_evidence_and_workflow() -> None:
    for readme_path in README_PATHS:
        text = readme_path.read_text(encoding="utf-8")
        assert "docs/CONFIGURATION" in text
        assert "docs/EVIDENCE" in text
        assert "docs/WORKFLOW" in text


def test_key_and_automatic_context_configuration_contract_is_documented() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["encryption_key_source"]["default"] == "local"
    assert schema["encryption_key_file"]["type"] == "string"
    assert schema["encryption_key_file"]["condition"] == {"encryption_key_source": "file"}
    assert schema["encryption_previous_key_file"]["type"] == "string"
    assert schema["encryption_previous_key_file"]["condition"] == {"encryption_key_source": "file"}
    assert schema["model_context_limit"]["default"] == 0

    configuration_paths = (
        ROOT / "docs/CONFIGURATION.md",
        ROOT / "docs/CONFIGURATION.zh-CN.md",
    )
    english, chinese = (path.read_text(encoding="utf-8") for path in configuration_paths)
    assert "| `encryption_key_source` | `string` | `local` |" in english
    assert "| `encryption_key_file` | `string` | empty |" in english
    assert "Shown only when `encryption_key_source=file`" in english
    assert "| `model_context_limit` | `int` | `0` |" in english
    assert "| `encryption_key_source` | `string` | `local` |" in chinese
    assert "| `encryption_key_file` | `string` | 空 |" in chinese
    assert "只在 `encryption_key_source=file` 时显示" in chinese
    assert "| `model_context_limit` | `int` | `0` |" in chinese

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    v021 = changelog.split("## v0.2.1", 1)[1].split("## v0.2.0", 1)[0]
    assert "defaults to automatic local key creation and reuse" in v021
    assert "默认自动创建并复用本地密钥" in changelog


def test_release_credits_name_ayleovelle() -> None:
    for path in README_PATHS:
        assert "Copyright © 2026 Ayleovelle" in path.read_text(encoding="utf-8")


def test_release_documentation_has_no_confidential_or_civil_domain_terms() -> None:
    forbidden = re.compile(
        r"confidential paper|civil engineering|土木工程|桥梁工程|隧道工程|私密论文|保密论文",
        re.IGNORECASE,
    )
    for path in DOCUMENTATION_PATHS:
        assert forbidden.search(path.read_text(encoding="utf-8")) is None
