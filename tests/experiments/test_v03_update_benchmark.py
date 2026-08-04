from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from experiments.v03_update_benchmark import (
    E2E_SUMMARY_STATUS,
    EXECUTION_PATH,
    ORDINARY_PROVIDER_RUNTIME_REORGANIZATION_WIRED,
    _aggregate_row,
    _ReceiptRow,
    load_frozen_scenarios,
    run_v03_update_benchmark,
)

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "experiments" / "v03_update_scenarios.json"


def test_v03_update_benchmark_writes_redacted_36_trial_receipts(tmp_path: Path) -> None:
    scenarios = load_frozen_scenarios(SCENARIOS)
    assert len(scenarios) == 12

    output_dir = tmp_path / "receipt"
    workspace = tmp_path / "workspace"
    summary = run_v03_update_benchmark(
        scenarios_path=SCENARIOS,
        output_dir=output_dir,
        workspace=workspace,
        trials=3,
        token_budget=10_000,
    )

    assert summary.total_trials == 36
    assert summary.committed_trials == 36
    assert (output_dir / "receipt.json").is_file()
    assert (output_dir / "receipt.csv").is_file()
    assert (output_dir / "aggregate.csv").is_file()
    assert b"\r\n" not in (output_dir / "receipt.json").read_bytes()
    assert b"\r\n" not in (output_dir / "receipt.csv").read_bytes()
    assert b"\r\n" not in (output_dir / "aggregate.csv").read_bytes()
    assert not (workspace / "astrcontinuum.sqlite3").exists()

    receipt = json.loads((output_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["execution_path"] == EXECUTION_PATH
    assert receipt["ordinary_provider_runtime_reorganization_wired"] is (
        ORDINARY_PROVIDER_RUNTIME_REORGANIZATION_WIRED
    )
    assert receipt["e2e_summary_status"] == E2E_SUMMARY_STATUS == "BLOCKED"
    assert receipt["scenario_count"] == 12
    assert receipt["trials_per_scenario"] == 3
    assert len(receipt["rows"]) == 36
    assert receipt["result_sha256"]
    receipt_text = (output_dir / "receipt.json").read_text(encoding="utf-8")
    assert all(scenario.initial_event not in receipt_text for scenario in scenarios)
    assert all(scenario.update_event not in receipt_text for scenario in scenarios)

    required_row_keys = {
        "scenario_code",
        "trial",
        "status_code",
        "baseline_status_code",
        "execution_path",
        "ordinary_provider_runtime_reorganization_wired",
        "e2e_summary_status",
        "input_sha256",
        "snapshot_sha256",
        "ledger_sha256",
        "pointer_before",
        "pointer_after",
        "ledger_record_count",
        "retained_record_count",
        "approximate_record_count",
        "released_record_count",
        "non_summary_released_record_count",
        "required_record_count",
        "failure_codes",
    }
    assert all(set(row) == required_row_keys for row in receipt["rows"])
    assert all(row["status_code"] == "COMMITTED" for row in receipt["rows"])
    assert all(row["baseline_status_code"] == "COMMITTED" for row in receipt["rows"])
    assert all(row["execution_path"] == EXECUTION_PATH for row in receipt["rows"])
    assert all(
        row["ordinary_provider_runtime_reorganization_wired"] is False for row in receipt["rows"]
    )
    assert all(row["e2e_summary_status"] == "BLOCKED" for row in receipt["rows"])
    assert all(row["pointer_before"] == 1 for row in receipt["rows"])
    assert all(row["pointer_after"] == 2 for row in receipt["rows"])
    assert all(row["ledger_record_count"] > 0 for row in receipt["rows"])
    assert all(row["required_record_count"] >= 2 for row in receipt["rows"])
    assert all(row["non_summary_released_record_count"] == 0 for row in receipt["rows"])
    assert all(not row["failure_codes"] for row in receipt["rows"])

    with (output_dir / "receipt.csv").open(encoding="utf-8", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert len(csv_rows) == 36
    assert set(csv_rows[0]) == required_row_keys
    assert all(row["pointer_after"] == "2" for row in csv_rows)

    with (output_dir / "aggregate.csv").open(encoding="utf-8", newline="") as stream:
        aggregate_rows = list(csv.DictReader(stream))
    assert len(aggregate_rows) == 1
    aggregate = aggregate_rows[0]
    assert aggregate == {
        "experiment_id": "V03-WIRE-001",
        "scope": "synthetic_provider_free_wiring_gate",
        "execution_path": EXECUTION_PATH,
        "denominator": "36",
        "baseline_committed": "36",
        "update_committed": "36",
        "pointer_advanced": "36",
        "durable_ledger": "36",
        "required_core_edges": "36",
        "non_summary_released_record_count": "0",
        "ordinary_provider_runtime_reorganization_wired": "False",
        "e2e_summary_status": "BLOCKED",
        "fixture_sha256": receipt["fixture_sha256"],
        "result_sha256": receipt["result_sha256"],
    }


def test_v03_update_benchmark_rejects_repository_as_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace must not be the repository root"):
        run_v03_update_benchmark(
            scenarios_path=SCENARIOS,
            output_dir=tmp_path / "receipt",
            workspace=ROOT,
            trials=3,
            token_budget=10_000,
        )


def test_v03_update_benchmark_rejects_a_fixture_with_content_drift(tmp_path: Path) -> None:
    payload = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    payload["scenarios"][0]["update_event"] = "changed content must not pass as frozen"
    altered_scenarios = tmp_path / "altered-scenarios.json"
    altered_scenarios.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="SHA-256"):
        run_v03_update_benchmark(
            scenarios_path=altered_scenarios,
            output_dir=tmp_path / "receipt",
            workspace=tmp_path / "workspace",
        )


def test_v03_aggregate_requires_an_exact_single_pointer_advance() -> None:
    valid = _ReceiptRow(
        scenario_code="UPD-01",
        trial=1,
        status_code="COMMITTED",
        baseline_status_code="COMMITTED",
        input_sha256="a" * 64,
        snapshot_sha256="b" * 64,
        ledger_sha256="c" * 64,
        pointer_before=1,
        pointer_after=2,
        ledger_record_count=2,
        retained_record_count=2,
        approximate_record_count=0,
        released_record_count=0,
        non_summary_released_record_count=0,
        required_record_count=2,
        failure_codes=(),
    )
    skipped = replace(valid, pointer_after=3)

    aggregate = _aggregate_row(
        rows=(valid, skipped),
        fixture_sha256="d" * 64,
        result_sha256="e" * 64,
    )

    assert aggregate["pointer_advanced"] == 1


def test_v03_update_benchmark_does_not_overwrite_an_existing_aggregate(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "receipt"
    output_dir.mkdir()
    (output_dir / "aggregate.csv").write_text("protected\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already contains a benchmark receipt"):
        run_v03_update_benchmark(
            scenarios_path=SCENARIOS,
            output_dir=output_dir,
            workspace=tmp_path / "workspace",
        )
