"""Tests for materialization and freeze-before-reveal execution."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from crm_experiment.baselines import ARM_ORDER
from crm_experiment.runner import materialize_protocol, run_protocol


@pytest.fixture
def smoke_config(tmp_path: Path) -> Path:
    config = {
        "schema_version": 1,
        "stream_count": 1,
        "generation_count": 2,
        "queries_per_generation": 6,
        "seed_start": 271800,
        "budget_multipliers": [2.0, 1.0],
        "main_budget_multiplier": 2.0,
        "main_generation": 2,
        "query_injection_bytes": 512,
        "zero_delta_rounds": 2,
        "bootstrap_replicates": 100,
        "bootstrap_seed": 2718,
        "noninferiority_margin_pp": -5.0,
        "weights": {
            "gamma": 50.0,
            "rho": 0.1,
            "risk_ceiling": 0.05,
            "exact_threshold": 12,
            "version": "theta0",
        },
    }
    path = tmp_path / "smoke-config.json"
    path.write_text(
        json.dumps(config, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def materialized(smoke_config: Path, tmp_path: Path) -> Path:
    root = tmp_path / "inputs"
    materialize_protocol(smoke_config, root)
    return root


@pytest.fixture
def run_output(smoke_config: Path, materialized: Path, tmp_path: Path) -> Path:
    root = tmp_path / "run"
    run_protocol(
        smoke_config,
        materialized / "runtime" / "protocol.json",
        materialized / "public-queries" / "queries.json",
        root,
    )
    return root


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_materialization_separates_runtime_queries_and_gold(
    materialized: Path,
) -> None:
    runtime = materialized / "runtime" / "protocol.json"
    queries = materialized / "public-queries" / "queries.json"
    gold = materialized / "sealed-gold" / "gold.json"
    manifest = materialized / "input-manifest.json"

    assert runtime.is_file() and queries.is_file() and gold.is_file()
    runtime_text = runtime.read_text(encoding="utf-8")
    query_text = queries.read_text(encoding="utf-8")
    gold_text = gold.read_text(encoding="utf-8")
    assert '"events"' in runtime_text
    assert '"queries"' not in runtime_text
    assert "required_atom_ids" not in query_text
    assert "required_atom_ids" in gold_text
    manifest_rows = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    assert len(manifest_rows) == 3
    assert all(row["sha256"] and row["bytes"] > 0 for row in manifest_rows)


def test_run_protocol_signature_has_no_gold_argument() -> None:
    parameters = inspect.signature(run_protocol).parameters
    assert tuple(parameters) == (
        "config_path",
        "runtime_path",
        "query_path",
        "output_root",
    )
    assert "gold" not in parameters


def test_checkpoint_manifest_is_content_free(run_output: Path) -> None:
    manifest = run_output / "state-hash-manifest.json"
    raw = manifest.read_bytes()

    assert manifest.is_file()
    assert b'"kernel"' not in raw
    assert b'"body"' not in raw
    assert b'"text"' not in raw
    assert b'"summary"' not in raw.lower()


def test_runtime_records_keep_fixed_denominator_without_released_text(
    run_output: Path,
) -> None:
    records_path = run_output / "records.jsonl"
    records = _jsonl(records_path)

    assert len(records) == 1 * 2 * 2 * len(ARM_ORDER) * 6
    assert "released_raw_text" not in records_path.read_text(encoding="utf-8")
    assert all("query_id" in row for row in records)


def test_learning_events_are_content_free(run_output: Path) -> None:
    rows = _jsonl(run_output / "learning-events.jsonl")
    forbidden = {
        "text",
        "kernel",
        "body",
        "summary",
        "raw_delta",
        "released_text",
        "query_text",
    }

    assert rows
    assert all(forbidden.isdisjoint(row) for row in rows)


def test_state_hash_manifest_is_byte_identical_across_output_roots(
    smoke_config: Path,
    materialized: Path,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    runtime = materialized / "runtime" / "protocol.json"
    queries = materialized / "public-queries" / "queries.json"

    run_protocol(smoke_config, runtime, queries, first)
    run_protocol(smoke_config, runtime, queries, second)

    assert (first / "state-hash-manifest.json").read_bytes() == (
        second / "state-hash-manifest.json"
    ).read_bytes()


def test_missing_public_query_yields_failure_rows_without_denominator_loss(
    smoke_config: Path,
    materialized: Path,
    tmp_path: Path,
) -> None:
    query_path = materialized / "public-queries" / "queries.json"
    payload = json.loads(query_path.read_text(encoding="utf-8"))
    payload["queries"] = payload["queries"][1:]
    damaged = tmp_path / "damaged-queries.json"
    damaged.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    output = tmp_path / "damaged-run"
    records = run_protocol(
        smoke_config,
        materialized / "runtime" / "protocol.json",
        damaged,
        output,
    )
    rows = _jsonl(records)

    assert len(rows) == 1 * 2 * 2 * len(ARM_ORDER) * 6
    assert any(row.get("error_code") == "QUERY_MISSING" for row in rows)
