"""Tests for materialization and freeze-before-reveal execution."""

from __future__ import annotations

import inspect
import json
from dataclasses import fields, replace
from pathlib import Path
from typing import cast

import pytest

import crm_experiment.runner as runner
from crm_experiment.baselines import ARM_ORDER, LegacyCapsuleBaseline
from crm_experiment.canonical import canonical_json, utf8_bytes
from crm_experiment.contracts import (
    AtomRole,
    AtomStatus,
    QuerySpec,
    SemanticAtom,
)
from crm_experiment.kernel import default_kernel_schema, derive_kernel_ceiling
from crm_experiment.matrix import atomize
from crm_experiment.protocol import load_protocol_config
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


@pytest.fixture
def omission_run_output(
    smoke_config: Path,
    materialized: Path,
    tmp_path: Path,
) -> Path:
    runtime = materialized / "runtime" / "protocol.json"
    payload = json.loads(runtime.read_text(encoding="utf-8"))
    first_events = payload["streams"][0]["generations"][0]["events"]
    context = next(event for event in first_events if event["role"] == "context")
    context["text"] = "x" * 20_000
    damaged = tmp_path / "omission-runtime.json"
    damaged.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    root = tmp_path / "omission-run"
    run_protocol(
        smoke_config,
        damaged,
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


def _checkpoint_rows(output: Path) -> list[dict[str, object]]:
    return [
        row
        for row in _jsonl(output / "learning-events.jsonl")
        if row["kind"] == "checkpoint"
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
    gold_rows = json.loads(gold_text)["gold"]
    assert all(
        {"role", "is_exact_anchor", "is_topic_return"} <= row.keys()
        for row in gold_rows
    )
    assert all(
        row["is_exact_anchor"] is (row["role"] == "exact_anchor") for row in gold_rows
    )
    assert all(
        row["is_topic_return"] is (row["role"] == "context") for row in gold_rows
    )
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


def test_checkpoint_sidecar_has_truthful_content_free_metric_provenance(
    run_output: Path,
) -> None:
    rows = _checkpoint_rows(run_output)
    required = {
        "checkpoint_id",
        "history_bytes",
        "step_input_bytes",
        "delta_bytes",
        "kernel_bytes",
        "kernel_payload_bytes",
        "body_bytes",
        "state_overhead_bytes",
        "semantic_weight_denominator",
        "weighted_retained_weight",
        "weighted_omitted_weight",
        "error_risk",
        "internal_continuity_break",
        "internal_stale_current",
        "net_released_bytes",
        "release_denominator_count",
        "coverage_intersection_count",
        "coverage_union_count",
        "kernel_coverage_count",
        "kernel_coverage_denominator",
        "kernel_coverage_id_digests",
        "requested_budget_bytes",
        "accepted_budget_bytes",
        "budget_overshoot_bytes",
        "transition_committed",
        "consumed_delta",
        "rejected_delta_bytes",
        "committed_base_state_reduction_bytes",
        "source_manifest_hash",
        "id_digest_domain",
    }

    assert rows
    assert len(rows) == 1 * 2 * 2 * len(ARM_ORDER)
    assert all(required <= row.keys() for row in rows)
    assert all(row["transition_committed"] is True for row in rows)
    assert all(row["consumed_delta"] is True for row in rows)
    assert all(row["rejected_delta_bytes"] == 0 for row in rows)
    assert all(
        cast(int, row["history_bytes"]) >= cast(int, row["delta_bytes"]) for row in rows
    )
    assert all(
        cast(int, row["step_input_bytes"]) >= cast(int, row["delta_bytes"])
        for row in rows
    )
    assert all(
        cast(int, row["kernel_payload_bytes"]) + cast(int, row["state_overhead_bytes"])
        == cast(int, row["kernel_bytes"])
        for row in rows
    )
    assert all(
        cast(int, row["kernel_bytes"]) + cast(int, row["body_bytes"])
        == cast(int, row["persistent_bytes"])
        for row in rows
    )
    assert all(
        cast(float, row["weighted_retained_weight"])
        + cast(float, row["weighted_omitted_weight"])
        == pytest.approx(cast(float, row["semantic_weight_denominator"]))
        for row in rows
    )
    assert all(
        cast(int, row["net_released_bytes"])
        == cast(int, row["step_input_bytes"]) - cast(int, row["persistent_bytes"])
        for row in rows
    )
    assert any(cast(int, row["net_released_bytes"]) < 0 for row in rows)
    assert all(
        0
        <= cast(int, row["released_count"])
        <= cast(int, row["release_denominator_count"])
        for row in rows
    )
    assert all(
        cast(int, row["coverage_intersection_count"])
        <= cast(int, row["coverage_union_count"])
        for row in rows
    )
    assert all(
        cast(int, row["kernel_coverage_count"])
        <= cast(int, row["kernel_coverage_denominator"])
        for row in rows
    )
    assert all(
        cast(int, row["budget_overshoot_bytes"])
        == max(
            0,
            cast(int, row["persistent_bytes"])
            - cast(int, row["accepted_budget_bytes"]),
        )
        for row in rows
    )
    assert all(
        cast(int, row["requested_budget_bytes"])
        == round(
            cast(float, row["budget_multiplier"])
            * derive_kernel_ceiling(default_kernel_schema())
        )
        for row in rows
    )
    assert all(
        "continuity_break" not in row and "stale_current" not in row for row in rows
    )

    digests = [
        digest
        for row in rows
        for digest in cast(list[str], row["kernel_coverage_id_digests"])
    ]
    assert digests
    assert all(len(digest) == 64 for digest in digests)
    assert all(
        all(character in "0123456789abcdef" for character in digest)
        for digest in digests
    )
    assert all("s00-" not in digest for digest in digests)
    assert all(len(cast(str, row["source_manifest_hash"])) == 64 for row in rows)
    assert all(row["id_digest_domain"] == "crm-source-id/v1" for row in rows)


def test_query_records_join_one_normalized_checkpoint_sidecar(
    run_output: Path,
) -> None:
    records = _jsonl(run_output / "records.jsonl")
    checkpoints = _checkpoint_rows(run_output)
    by_id = {cast(str, row["checkpoint_id"]): row for row in checkpoints}
    counts: dict[str, int] = {}
    checkpoint_only = {
        "persistent_bytes",
        "kernel_bytes",
        "body_bytes",
        "semantic_weight_denominator",
        "weighted_retained_weight",
        "weighted_omitted_weight",
        "coverage_intersection_count",
        "coverage_union_count",
        "kernel_coverage_id_digests",
        "stage_ns",
        "total_ns",
        "peak_workspace_bytes",
    }

    assert len(by_id) == len(checkpoints)
    for record in records:
        checkpoint_id = cast(str, record["checkpoint_id"])
        counts[checkpoint_id] = counts.get(checkpoint_id, 0) + 1
        assert checkpoint_id in by_id
        assert checkpoint_only.isdisjoint(record)
    assert set(counts.values()) == {6}


def test_source_id_digest_is_domain_separated_by_runtime_manifest() -> None:
    assert hasattr(runner, "_source_id_digest")
    first = runner._source_id_digest("atom-1", "a" * 64)
    second = runner._source_id_digest("atom-1", "b" * 64)

    assert first != second
    assert len(first) == len(second) == 64
    assert "atom-1" not in first


def test_every_arm_reports_real_nonzero_omission_under_pressure(
    omission_run_output: Path,
) -> None:
    rows = _checkpoint_rows(omission_run_output)
    first_checkpoint = [
        row
        for row in rows
        if row["generation"] == 1 and row["budget_multiplier"] == 1.0
    ]

    assert {cast(str, row["arm"]) for row in first_checkpoint} == set(ARM_ORDER)
    assert all(
        cast(float, row["weighted_omitted_weight"]) > 0.0 for row in first_checkpoint
    )


def test_unavailable_error_risk_is_null_and_crm_ablation_risk_is_auditable(
    run_output: Path,
) -> None:
    rows = _checkpoint_rows(run_output)
    unavailable = {"legacy_capsule", "recursive_summary"}
    crm_family = set(ARM_ORDER) - unavailable

    assert all(row["error_risk"] is None for row in rows if row["arm"] in unavailable)
    assert all(
        isinstance(row["error_risk"], (int, float))
        for row in rows
        if row["arm"] in crm_family and not row["error_code"]
    )
    checkpoint_rows = [
        row
        for row in _jsonl(run_output / "learning-events.jsonl")
        if row["kind"] == "checkpoint"
        and row["arm"] in {"crm_no_projection", "crm_no_kernel"}
    ]
    assert checkpoint_rows
    assert all(row["matrix_hash"] for row in checkpoint_rows)
    assert all(
        set(cast(dict[str, int], row["stage_ns"])) == {"matrix", "optimizer", "gate"}
        for row in checkpoint_rows
    )


def test_failed_transition_rejects_delta_and_nulls_step_release_metrics(
    smoke_config: Path,
    base_state,
    delta_atom: SemanticAtom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_advance(*args, **kwargs):
        del args, kwargs
        raise ValueError("forced advancement failure")

    monkeypatch.setattr(LegacyCapsuleBaseline, "advance", fail_advance)
    config = load_protocol_config(smoke_config)
    delta = (delta_atom,)
    delta_bytes = utf8_bytes(canonical_json(delta))
    advance = runner._advance_arm(
        "legacy_capsule",
        base_state,
        delta,
        8 * derive_kernel_ceiling(default_kernel_schema()),
        config,
    )
    ledger: dict[str, runner._MeasurementAtom] = {}
    runner._update_measurement_ledger(ledger, delta)
    metrics = runner._measure_transition(
        base_state,
        runner._delta_coverage(delta),
        advance,
        runner._measurement_universe(ledger),
        runner._persistent_bytes(base_state) + delta_bytes,
        delta_bytes,
        "a" * 64,
    )

    assert advance.transition_committed is False
    assert advance.consumed_delta is False
    assert metrics.step_input_bytes is None
    assert metrics.net_released_bytes is None
    assert metrics.released_count is None
    assert metrics.release_denominator_count is None
    assert metrics.rejected_delta_bytes == delta_bytes
    assert metrics.committed_base_state_reduction_bytes is None


def test_admission_block_commits_base_reduction_without_releasing_rejected_delta(
    smoke_config: Path,
    base_state,
    delta_atom: SemanticAtom,
) -> None:
    config = load_protocol_config(smoke_config)
    delta = (delta_atom,)
    delta_bytes = utf8_bytes(canonical_json(delta))
    advance = runner._advance_arm(
        "crm",
        base_state,
        delta,
        derive_kernel_ceiling(default_kernel_schema()) - 1,
        config,
    )
    ledger: dict[str, runner._MeasurementAtom] = {}
    runner._update_measurement_ledger(ledger, delta)
    metrics = runner._measure_transition(
        base_state,
        runner._delta_coverage(delta),
        advance,
        runner._measurement_universe(ledger),
        runner._persistent_bytes(base_state) + delta_bytes,
        delta_bytes,
        "a" * 64,
    )

    assert advance.outcome == "admission_blocked"
    assert advance.transition_committed is True
    assert advance.consumed_delta is False
    assert metrics.step_input_bytes is None
    assert metrics.net_released_bytes is None
    assert metrics.released_count is None
    assert metrics.release_denominator_count is None
    assert metrics.rejected_delta_bytes == delta_bytes
    assert metrics.committed_base_state_reduction_bytes == max(
        0,
        runner._persistent_bytes(base_state) - advance.persistent_bytes,
    )


def test_consumed_transition_does_not_claim_base_only_byte_reduction(
    smoke_config: Path,
    base_state,
    delta_atom: SemanticAtom,
) -> None:
    config = load_protocol_config(smoke_config)
    delta = (delta_atom,)
    delta_bytes = utf8_bytes(canonical_json(delta))
    advance = runner._advance_arm(
        "crm",
        base_state,
        delta,
        8 * derive_kernel_ceiling(default_kernel_schema()),
        config,
    )
    ledger: dict[str, runner._MeasurementAtom] = {}
    runner._update_measurement_ledger(ledger, delta)
    metrics = runner._measure_transition(
        base_state,
        runner._delta_coverage(delta),
        advance,
        runner._measurement_universe(ledger),
        runner._persistent_bytes(base_state) + delta_bytes,
        delta_bytes,
        "a" * 64,
    )

    assert advance.transition_committed is True
    assert advance.consumed_delta is True
    assert metrics.rejected_delta_bytes == 0
    assert metrics.committed_base_state_reduction_bytes is None


def test_measurement_ledger_is_content_free_latest_active_telemetry(
    delta_atom: SemanticAtom,
) -> None:
    old = replace(delta_atom, atom_id="decision-v1", revision=1, as_of=1)
    latest = replace(delta_atom, atom_id="decision-v2", revision=2, as_of=2)
    assert hasattr(runner, "_MeasurementAtom")
    assert hasattr(runner, "_update_measurement_ledger")
    assert hasattr(runner, "_measurement_universe")
    ledger: dict[str, runner._MeasurementAtom] = {}

    runner._update_measurement_ledger(ledger, (latest, old))
    universe = runner._measurement_universe(ledger)

    assert {field.name for field in fields(runner._MeasurementAtom)} == {
        "atom_id",
        "semantic_keys",
        "role",
        "status",
        "revision",
        "as_of",
        "weight",
        "core_required",
    }
    assert tuple(atom.atom_id for atom in universe) == ("decision-v2",)
    assert all(not isinstance(value, SemanticAtom) for value in ledger.values())
    assert "text" not in repr(ledger)
    assert "provenance" not in repr(ledger)

    retracted = replace(
        latest,
        atom_id="decision-v3",
        status=AtomStatus.RETRACTED,
        revision=3,
        as_of=3,
    )
    runner._update_measurement_ledger(ledger, (retracted,))
    assert runner._measurement_universe(ledger) == ()


def test_measurement_resolver_matches_atomize_revision_then_id(
    delta_atom: SemanticAtom,
) -> None:
    higher_revision_older_time = replace(
        delta_atom,
        atom_id="decision-z",
        revision=3,
        as_of=1,
    )
    lower_revision_newer_time = replace(
        delta_atom,
        atom_id="decision-a",
        revision=2,
        as_of=99,
    )
    same_revision_lower_id = replace(
        delta_atom,
        atom_id="decision-y",
        revision=3,
        as_of=100,
    )
    delta = (
        lower_revision_newer_time,
        same_revision_lower_id,
        higher_revision_older_time,
    )
    ledger: dict[str, runner._MeasurementAtom] = {}

    runner._update_measurement_ledger(ledger, delta)
    universe_ids = tuple(atom.atom_id for atom in runner._measurement_universe(ledger))
    atomized_ids = tuple(atom.atom_id for atom in atomize(None, delta))

    assert universe_ids == ("decision-z",)
    assert atomized_ids == universe_ids


def test_measurement_resolver_handles_multi_key_partial_supersession_and_retraction(
    delta_atom: SemanticAtom,
) -> None:
    shared = replace(
        delta_atom,
        atom_id="shared-v1",
        semantic_keys=("alpha", "beta"),
        revision=1,
    )
    alpha_v2 = replace(
        delta_atom,
        atom_id="alpha-v2",
        semantic_keys=("alpha",),
        revision=2,
    )
    alpha_retracted = replace(
        delta_atom,
        atom_id="alpha-v3",
        semantic_keys=("alpha",),
        revision=3,
        status=AtomStatus.RETRACTED,
    )
    ledger: dict[str, runner._MeasurementAtom] = {}

    runner._update_measurement_ledger(ledger, (shared, alpha_v2))
    assert tuple(atom.atom_id for atom in runner._measurement_universe(ledger)) == (
        "alpha-v2",
        "shared-v1",
    )

    runner._update_measurement_ledger(ledger, (alpha_retracted,))
    assert tuple(atom.atom_id for atom in runner._measurement_universe(ledger)) == (
        "shared-v1",
    )


def test_measurement_ledger_never_enters_advance_or_projection_decisions() -> None:
    for function in (runner._advance_arm, runner._project_arm):
        parameters = inspect.signature(function).parameters
        assert "ledger" not in parameters
        assert "measurement" not in parameters


def test_query_rows_name_only_the_frozen_state_source(run_output: Path) -> None:
    rows = _jsonl(run_output / "records.jsonl")
    checkpoints = {
        cast(str, row["checkpoint_id"]): row for row in _checkpoint_rows(run_output)
    }

    assert rows
    assert all(
        row["query_source"]
        == ("frozen_summary" if row["arm"] == "recursive_summary" else "frozen_capsule")
        for row in rows
    )
    assert all(
        row["query_source_state_hash"]
        == checkpoints[cast(str, row["checkpoint_id"])]["state_hash"]
        for row in rows
    )
    assert all(row["fallback_reads"] == 0 for row in rows)
    assert all(row["query_role"] for row in rows)
    assert all(row["projection_budget_bytes"] == 512 for row in rows)
    assert all(
        cast(dict[str, int], row["query_read_trace"])["frozen_summary_reads"]
        == (1 if row["arm"] == "recursive_summary" else 0)
        for row in rows
    )
    assert all(
        cast(dict[str, int], row["query_read_trace"])["frozen_capsule_reads"]
        == (0 if row["arm"] == "recursive_summary" else 1)
        for row in rows
    )


def test_projection_exception_retains_actual_legal_read_trace(
    smoke_config: Path,
    materialized: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_projection(*args, **kwargs):
        del args, kwargs
        raise ValueError("forced projection failure")

    monkeypatch.setattr(runner, "project_query", fail_projection)
    output = tmp_path / "projection-error"
    records_path = run_protocol(
        smoke_config,
        materialized / "runtime" / "protocol.json",
        materialized / "public-queries" / "queries.json",
        output,
    )
    rows = [
        row for row in _jsonl(records_path) if row["error_code"] == "PROJECTION_FAILED"
    ]

    assert rows
    assert all(row["query_source"] == "frozen_capsule" for row in rows)
    assert all(
        cast(dict[str, int], row["query_read_trace"])["frozen_capsule_reads"] == 1
        for row in rows
    )
    assert all(row["fallback_reads"] == 0 for row in rows)


def test_forbidden_query_read_is_traced_and_fails_query(
    base_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_projection(arm, view, query, byte_budget):
        del arm, query, byte_budget
        view.attempt_forbidden_read("old_capsule")

    monkeypatch.setattr(runner, "_project_arm", forbidden_projection)
    execution = runner._execute_projection(
        "crm",
        base_state,
        "state-hash",
        QuerySpec("query", AtomRole.ROOT_GOAL, "goal"),
        512,
        advance_error="",
    )

    assert execution.projection.supported is False
    assert execution.error_code == "FORBIDDEN_QUERY_READ"
    assert execution.query_source == "not_read"
    assert execution.fallback_reads == 1
    assert execution.read_trace["old_capsule_reads"] == 1


def test_caught_forbidden_query_read_still_fails_closed(
    base_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def caught_forbidden_projection(arm, view, query, byte_budget):
        del arm
        try:
            view.attempt_forbidden_read("old_capsule")
        except runner.ForbiddenQueryRead:
            pass
        return runner.project_query(view.read_capsule(), query, byte_budget)

    monkeypatch.setattr(runner, "_project_arm", caught_forbidden_projection)
    execution = runner._execute_projection(
        "crm",
        base_state,
        "state-hash",
        QuerySpec("query", AtomRole.ROOT_GOAL, "goal"),
        512,
        advance_error="",
    )

    assert execution.projection.supported is False
    assert execution.error_code == "FORBIDDEN_QUERY_READ"
    assert execution.query_source == "frozen_capsule"
    assert execution.fallback_reads == 1
    assert execution.read_trace["old_capsule_reads"] == 1


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
        "atom_id",
        "provenance",
    }

    assert rows
    assert all(forbidden.isdisjoint(row) for row in rows)
    raw = (run_output / "learning-events.jsonl").read_text(encoding="utf-8")
    assert "goal for stream" not in raw
    assert '"atom_id"' not in raw


def test_zero_delta_rows_expose_the_complete_round_hash_chain(
    run_output: Path,
) -> None:
    rows = [
        row
        for row in _jsonl(run_output / "learning-events.jsonl")
        if row["kind"] == "zero_delta"
    ]

    assert rows
    assert all(
        {
            "initial_hash",
            "round_hashes",
            "final_hash",
            "first_unstable_round",
        }
        <= row.keys()
        for row in rows
    )
    assert all(
        len(cast(list[str], row["round_hashes"])) == cast(int, row["rounds"])
        for row in rows
    )
    assert all(
        row["final_hash"] == cast(list[str], row["round_hashes"])[-1] for row in rows
    )
    assert all(
        row["stable"]
        is all(
            digest == row["initial_hash"]
            for digest in cast(list[str], row["round_hashes"])
        )
        for row in rows
    )
    assert all(
        row["first_unstable_round"] is None for row in rows if row["stable"] is True
    )


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
    first_ids = [row["checkpoint_id"] for row in _jsonl(first / "records.jsonl")]
    second_ids = [row["checkpoint_id"] for row in _jsonl(second / "records.jsonl")]
    assert first_ids == second_ids


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
    assert all(
        row["query_source"] == "not_read"
        for row in rows
        if row.get("error_code") == "QUERY_MISSING"
    )
    assert all(
        row["query_source_state_hash"] is None
        for row in rows
        if row.get("error_code") == "QUERY_MISSING"
    )
    assert all(
        sum(cast(dict[str, int], row["query_read_trace"]).values()) == 0
        for row in rows
        if row.get("error_code") == "QUERY_MISSING"
    )
