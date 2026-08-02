"""Protocol v2 runner reachability preflight contracts."""

from __future__ import annotations

import sys
from dataclasses import fields, replace
from hashlib import sha256
from pathlib import Path

import pytest

from crm_experiment.canonical import canonical_json
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    KeyWinnerV2,
    SourceReceiptV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.kernel_v2 import select_kernel_v2
from crm_experiment.protocol_v2 import (
    ProtocolSourceV2,
    ProtocolV2Bundle,
    ProtocolV2Config,
    generate_protocol_v2,
    load_protocol_v2_config,
)
from crm_experiment.runner_v2 import (
    RunnerPolicyV2,
    RunnerPreflightReportV2,
    _policy_hash,
    _report_hash,
    default_runner_policy_v2,
    preflight_runner_v2,
)

_ROOT = Path(__file__).resolve().parents[1]


def _tracked_artifact_snapshot() -> tuple[tuple[str, int, int, str], ...]:
    """Fingerprint forbidden output paths without creating them."""
    snapshots: list[tuple[str, int, int, str]] = []
    for relative in (Path("data/v2"), Path("results/v2"), Path("index.md")):
        path = _ROOT / relative
        if not path.exists():
            snapshots.append((relative.as_posix(), -1, -1, "missing"))
            continue
        if path.is_file():
            stat = path.stat()
            snapshots.append(
                (
                    relative.as_posix(),
                    stat.st_size,
                    stat.st_mtime_ns,
                    sha256(path.read_bytes()).hexdigest(),
                )
            )
            continue
        for child in sorted(path.rglob("*")):
            if child.is_file():
                stat = child.stat()
                snapshots.append(
                    (
                        child.relative_to(_ROOT).as_posix(),
                        stat.st_size,
                        stat.st_mtime_ns,
                        sha256(child.read_bytes()).hexdigest(),
                    )
                )
    return tuple(snapshots)


@pytest.fixture(scope="module", autouse=True)
def _no_result_artifact_writes() -> object:
    before = _tracked_artifact_snapshot()
    yield
    assert _tracked_artifact_snapshot() == before


@pytest.fixture(scope="module")
def frozen_inputs() -> tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2]:
    config = load_protocol_v2_config(_ROOT / "config/protocol-v2.json")
    bundle = generate_protocol_v2(config)
    policy = default_runner_policy_v2(config, bundle, stream_ids=("stream-00",))
    return config, bundle, policy


@pytest.fixture(scope="module")
def reachability_report(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
) -> RunnerPreflightReportV2:
    config, bundle, policy = frozen_inputs
    return preflight_runner_v2(config, bundle, policy)


def _control_inputs(
    sources: tuple[ProtocolSourceV2, ...],
) -> tuple[
    tuple[ActiveRecordV2, ...],
    tuple[KeyWinnerV2, ...],
    tuple[SourceReceiptV2, ...],
]:
    records = tuple(
        sorted(
            (
                ActiveRecordV2(source.atom, tuple(sorted(source.atom.semantic_keys)))
                for source in sources
            ),
            key=lambda record: record.atom.source_id,
        )
    )
    frontier = tuple(
        sorted(
            (
                KeyWinnerV2.from_atom(source.atom, semantic_key)
                for source in sources
                for semantic_key in source.atom.semantic_keys
            ),
            key=lambda winner: winner.semantic_key,
        )
    )
    receipts = tuple(
        sorted(
            (SourceReceiptV2.from_atom(source.atom) for source in sources),
            key=lambda receipt: receipt.source_id,
        )
    )
    return records, frontier, receipts


def _independent_kernel_bytes(
    sources: tuple[ProtocolSourceV2, ...],
    *,
    generation: int,
    requested_budget: int,
    policy: RunnerPolicyV2,
) -> int:
    records, frontier, receipts = _control_inputs(sources)
    weights = WeightPolicyV2.create(
        version=policy.weight_policy_version,
        source_weights=tuple(
            sorted(
                (
                    SourceWeightV2(record.atom.source_id, policy.uniform_weight)
                    for record in records
                ),
                key=lambda source_weight: source_weight.source_id,
            )
        ),
    )
    selection = select_kernel_v2(
        records,
        frontier,
        receipts,
        weights,
        policy.kernel_schema,
        generation=generation,
        high_water=generation,
        requested_budget=requested_budget,
        key_registry_limit=policy.key_registry_limit,
        max_semantic_key_bytes=policy.max_semantic_key_bytes,
        recomposition_policy_hash=policy.recomposition_policy_hash,
    )
    assert selection.resident_bytes is not None
    return selection.resident_bytes


def test_reachability_preflight_preserves_all_frozen_failures(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
    reachability_report: RunnerPreflightReportV2,
) -> None:
    config, bundle, policy = frozen_inputs
    report = reachability_report

    assert report.policy_id == policy.policy_id
    assert report.protocol_id == config.protocol_id
    assert report.schema_version == 2
    assert report.config_hash == config.config_hash
    assert report.bundle_input_hash == bundle.input_hash
    assert report.policy_hash == policy.policy_hash
    assert report.lower_bound_frame_budget == config.budget_bytes[0]
    assert report.passed is False
    assert len(report.rows) == config.generation_count * len(config.budget_bytes)

    first_failure_generation = {
        failure.budget_label: failure.generation for failure in report.first_failures
    }
    first_failure_stream = {
        failure.budget_label: failure.stream_id for failure in report.first_failures
    }
    assert first_failure_generation == {"K": 1, "1.5K": 1, "2K": 2, "4K": 4, "8K": 6}
    assert set(first_failure_stream.values()) == {"stream-00"}

    stream = bundle.streams[0]
    expected_fixed_curve = tuple(
        _independent_kernel_bytes(
            shadow.full_retention_sources,
            generation=shadow.generation,
            requested_budget=policy.lower_bound_frame_budget,
            policy=policy,
        )
        for shadow in stream.shadow_rounds
    )
    reported_fixed_curve = tuple(
        row.kernel_only_lower_bound_bytes
        for row in report.rows
        if row.budget_label == "8K"
    )
    assert reported_fixed_curve == expected_fixed_curve


def test_reachability_control_plane_and_payload_boundary_are_explicit(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
    reachability_report: RunnerPreflightReportV2,
) -> None:
    config, bundle, policy = frozen_inputs
    report = reachability_report
    first_shadow = bundle.streams[0].shadow_rounds[0]

    independent_core_only = _independent_kernel_bytes(
        first_shadow.kernel_only_sources,
        generation=first_shadow.generation,
        requested_budget=policy.lower_bound_frame_budget,
        policy=policy,
    )
    first_row = report.rows[0]
    assert first_row.core_only_control_bytes == independent_core_only
    assert first_row.core_only_control_bytes > config.continuity_floor_bytes
    assert first_row.kernel_only_lower_bound_bytes > first_row.core_only_control_bytes
    assert report.rows[-1].kernel_only_lower_bound_bytes > (
        first_row.kernel_only_lower_bound_bytes
    )

    serialized_report = canonical_json(report)
    for shadow_round in bundle.streams[0].shadow_rounds:
        for source in shadow_round.full_retention_sources:
            assert source.atom.text not in serialized_report
    assert "subject_round" not in serialized_report
    assert "projection" not in serialized_report


def test_reachability_report_is_deterministic_and_frame_bound(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
    reachability_report: RunnerPreflightReportV2,
) -> None:
    config, bundle, policy = frozen_inputs
    first = reachability_report
    second = preflight_runner_v2(config, bundle, policy)

    assert canonical_json(first) == canonical_json(second)
    assert first.result_hash == second.result_hash
    assert first.policy_hash == policy.policy_hash
    assert first.stream_ids == policy.stream_ids
    assert first.kernel_schema == policy.kernel_schema
    assert first.lower_bound_frame_budget == policy.lower_bound_frame_budget
    assert first.key_registry_limit == policy.key_registry_limit
    assert first.max_semantic_key_bytes == policy.max_semantic_key_bytes
    assert first.weight_policy_version == policy.weight_policy_version
    assert first.recomposition_policy_hash == policy.recomposition_policy_hash


def test_reachability_rejects_tampered_identity_or_budget(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
) -> None:
    config, bundle, policy = frozen_inputs

    with pytest.raises(ValueError, match="config hash mismatch"):
        preflight_runner_v2(
            replace(config, continuity_floor_bytes=config.continuity_floor_bytes + 1),
            bundle,
            policy,
        )
    with pytest.raises(ValueError, match="schema_version"):
        default_runner_policy_v2(config, replace(bundle, schema_version=1))
    with pytest.raises(ValueError, match="immutable Protocol v2 anchor"):
        default_runner_policy_v2(
            config,
            bundle,
            stream_ids=("stream-00",),
            lower_bound_frame_budget=config.continuity_floor_bytes,
        )


def _unchecked_report_for_hash(
    report: RunnerPreflightReportV2, **changes: object
) -> RunnerPreflightReportV2:
    """Model untrusted receipt bytes solely to calculate the claimed result hash."""
    forged = object.__new__(RunnerPreflightReportV2)
    for field in fields(RunnerPreflightReportV2):
        object.__setattr__(
            forged,
            field.name,
            ""
            if field.name == "result_hash"
            else changes.get(field.name, getattr(report, field.name)),
        )
    return forged


def _self_rehashed_report(
    report: RunnerPreflightReportV2, **changes: object
) -> RunnerPreflightReportV2:
    report_changes = {"policy_hash": "f" * 64, **changes}
    raw_report = _unchecked_report_for_hash(report, **report_changes)
    return RunnerPreflightReportV2(
        policy_id=raw_report.policy_id,
        protocol_id=raw_report.protocol_id,
        schema_version=raw_report.schema_version,
        config_hash=raw_report.config_hash,
        bundle_input_hash=raw_report.bundle_input_hash,
        policy_hash=raw_report.policy_hash,
        stream_ids=raw_report.stream_ids,
        kernel_schema=raw_report.kernel_schema,
        lower_bound_frame_budget=raw_report.lower_bound_frame_budget,
        key_registry_limit=raw_report.key_registry_limit,
        max_semantic_key_bytes=raw_report.max_semantic_key_bytes,
        weight_policy_version=raw_report.weight_policy_version,
        uniform_weight=raw_report.uniform_weight,
        packing_policy=raw_report.packing_policy,
        recomposition_policy_hash=raw_report.recomposition_policy_hash,
        rows=raw_report.rows,
        first_failures=raw_report.first_failures,
        passed=raw_report.passed,
        result_hash=_report_hash(raw_report),
    )


def test_report_rejects_self_rehashed_arbitrary_policy_hash(
    reachability_report: RunnerPreflightReportV2,
) -> None:
    with pytest.raises(ValueError, match="policy_hash"):
        _self_rehashed_report(reachability_report)


def _frozen_anchor_forged_value(policy: RunnerPolicyV2, field_name: str) -> str | int:
    if field_name == "config_hash":
        return "0" * 64
    if field_name == "bundle_input_hash":
        return "1" * 64
    return policy.lower_bound_frame_budget + 1


def _unchecked_policy_for_hash(
    policy: RunnerPolicyV2, **changes: object
) -> RunnerPolicyV2:
    """Model an untrusted serialized policy solely to calculate its claimed hash."""
    forged = object.__new__(RunnerPolicyV2)
    for field in fields(RunnerPolicyV2):
        object.__setattr__(
            forged,
            field.name,
            ""
            if field.name == "policy_hash"
            else changes.get(field.name, getattr(policy, field.name)),
        )
    return forged


def _self_hashed_policy(policy: RunnerPolicyV2, **changes: object) -> RunnerPolicyV2:
    """Construct the quality-review public policy attack through its dataclass API."""
    provisional = _unchecked_policy_for_hash(policy, **changes)
    return replace(policy, policy_hash=_policy_hash(provisional), **changes)


@pytest.mark.parametrize(
    "field_name",
    ("config_hash", "bundle_input_hash", "lower_bound_frame_budget"),
)
def test_policy_rejects_self_consistent_immutable_anchor_drift(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
    field_name: str,
) -> None:
    _, _, policy = frozen_inputs

    with pytest.raises(ValueError, match="immutable Protocol v2 anchor"):
        _self_hashed_policy(
            policy,
            **{field_name: _frozen_anchor_forged_value(policy, field_name)},
        )


@pytest.mark.parametrize(
    "field_name",
    ("config_hash", "bundle_input_hash", "lower_bound_frame_budget"),
)
def test_report_rejects_self_consistent_immutable_anchor_drift(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
    reachability_report: RunnerPreflightReportV2,
    field_name: str,
) -> None:
    _, _, policy = frozen_inputs
    forged_value = _frozen_anchor_forged_value(policy, field_name)
    matching_policy_hash = _policy_hash(
        _unchecked_policy_for_hash(policy, **{field_name: forged_value})
    )

    with pytest.raises(ValueError, match="immutable Protocol v2 anchor"):
        _self_rehashed_report(
            reachability_report,
            **{field_name: forged_value, "policy_hash": matching_policy_hash},
        )


def test_report_forgery_rehashes_raw_receipt_before_public_rejection(
    frozen_inputs: tuple[ProtocolV2Config, ProtocolV2Bundle, RunnerPolicyV2],
    reachability_report: RunnerPreflightReportV2,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, policy = frozen_inputs
    forged_config_hash = "0" * 64
    matching_policy_hash = _policy_hash(
        _unchecked_policy_for_hash(policy, config_hash=forged_config_hash)
    )
    seen_raw_reports: list[RunnerPreflightReportV2] = []
    original_report_hash = _report_hash

    def record_report_hash(report: RunnerPreflightReportV2) -> str:
        seen_raw_reports.append(report)
        return original_report_hash(report)

    monkeypatch.setattr(sys.modules[__name__], "_report_hash", record_report_hash)

    with pytest.raises(ValueError, match="immutable Protocol v2 anchor"):
        _self_rehashed_report(
            reachability_report,
            config_hash=forged_config_hash,
            policy_hash=matching_policy_hash,
        )

    assert len(seen_raw_reports) == 1
    assert seen_raw_reports[0].config_hash == forged_config_hash
    assert seen_raw_reports[0].policy_hash == matching_policy_hash
