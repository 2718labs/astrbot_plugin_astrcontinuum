"""Read-only total-resident reachability preflight for Protocol v2."""

from __future__ import annotations

import argparse
import math
from collections.abc import Iterable
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Self

from crm_experiment.canonical import canonical_json, sha256_text
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    KernelSchemaV2,
    KeyWinnerV2,
    PackingPolicyV2,
    SourceReceiptV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.kernel_v2 import default_kernel_schema_v2, select_kernel_v2
from crm_experiment.protocol_v2 import (
    ProtocolSourceV2,
    ProtocolV2Bundle,
    ProtocolV2Config,
    ProtocolV2Stream,
    generate_protocol_v2,
    load_protocol_v2_config,
    preflight_protocol_v2,
)

_POLICY_HASH_DOMAIN = "crm-protocol-v2-runner-policy/v1"
_REPORT_HASH_DOMAIN = "crm-protocol-v2-runner-preflight/v1"
_POLICY_ID = "crm-v2-kernel-only-lower-bound-v1"
_WEIGHT_POLICY_VERSION = "protocol-v2-uniform-v1"
_LOWER_BOUND_KEY_REGISTRY_LIMIT = 512
_LOWER_BOUND_MAX_SEMANTIC_KEY_BYTES = 128
_FROZEN_PROTOCOL_V2_ANCHOR_DOMAIN = "crm-protocol-v2-runner-anchor/v1"


@dataclass(frozen=True, slots=True)
class _FrozenProtocolV2Anchor:
    """Immutable sealed input identity for this one Protocol v2 diagnostic."""

    domain: str
    protocol_id: str
    schema_version: int
    config_hash: str
    bundle_input_hash: str
    budget_bytes: tuple[int, ...]
    lower_bound_frame_budget: int


# Generated from config/protocol-v2.json and its canonical V2 bundle on
# 2026-08-01. Changing any value requires a successor diagnostic with a new
# protocol_id, not same-ID drift.
_FROZEN_PROTOCOL_V2_ANCHOR = _FrozenProtocolV2Anchor(
    domain=_FROZEN_PROTOCOL_V2_ANCHOR_DOMAIN,
    protocol_id="crm-capsule-stress-v2",
    schema_version=2,
    config_hash="03a071a5b5aa6f724b061272673256da506368d22db299f42750124cf5a84d73",
    bundle_input_hash="15afbb85414a8038e0818c2b870c30827c6c71f5f4f5fe5776507235638fb807",
    budget_bytes=(36_864, 18_432, 9_216, 6_912, 4_608),
    lower_bound_frame_budget=36_864,
)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _validate_frozen_protocol_v2_anchor(
    *,
    subject: str,
    protocol_id: str,
    schema_version: int,
    config_hash: str,
    bundle_input_hash: str,
    lower_bound_frame_budget: int,
) -> None:
    """Reject same-ID input drift even when an attacker rehashes a policy."""
    anchor = _FROZEN_PROTOCOL_V2_ANCHOR
    if anchor.lower_bound_frame_budget != anchor.budget_bytes[0]:
        raise RuntimeError("runner v2 frozen anchor literal frame is inconsistent")
    if (
        protocol_id,
        schema_version,
        config_hash,
        bundle_input_hash,
        lower_bound_frame_budget,
    ) != (
        anchor.protocol_id,
        anchor.schema_version,
        anchor.config_hash,
        anchor.bundle_input_hash,
        anchor.lower_bound_frame_budget,
    ):
        raise ValueError(f"{subject} does not match immutable Protocol v2 anchor")


def _policy_projection(policy: RunnerPolicyV2) -> dict[str, object]:
    return {
        field.name: getattr(policy, field.name)
        for field in fields(policy)
        if field.name != "policy_hash"
    }


def _policy_hash(policy: RunnerPolicyV2) -> str:
    return sha256_text(
        canonical_json(
            {"domain": _POLICY_HASH_DOMAIN, "policy": _policy_projection(policy)}
        )
    )


@dataclass(frozen=True, slots=True)
class RunnerPolicyV2:
    """Frozen smallest native frame for a kernel-only control-state lower bound."""

    policy_id: str
    protocol_id: str
    schema_version: int
    config_hash: str
    bundle_input_hash: str
    stream_ids: tuple[str, ...]
    kernel_schema: KernelSchemaV2
    weight_policy_version: str
    uniform_weight: float
    lower_bound_frame_budget: int
    key_registry_limit: int
    max_semantic_key_bytes: int
    packing_policy: PackingPolicyV2
    recomposition_policy_hash: str
    policy_hash: str

    @classmethod
    def create(
        cls,
        config: ProtocolV2Config,
        bundle: ProtocolV2Bundle,
        *,
        stream_ids: tuple[str, ...] | None = None,
        lower_bound_frame_budget: int | None = None,
    ) -> Self:
        """Bind the lower-bound framing to one frozen Protocol v2 input bundle."""
        if bundle.protocol_id != config.protocol_id:
            raise ValueError("runner v2 protocol_id mismatch")
        if bundle.schema_version != config.schema_version:
            raise ValueError("runner v2 schema_version mismatch")
        if bundle.config_hash != config.config_hash:
            raise ValueError("runner v2 config hash mismatch")
        available_stream_ids = tuple(stream.stream_id for stream in bundle.streams)
        selected_stream_ids = available_stream_ids if stream_ids is None else stream_ids
        provisional = cls(
            policy_id=_POLICY_ID,
            protocol_id=config.protocol_id,
            schema_version=config.schema_version,
            config_hash=config.config_hash,
            bundle_input_hash=bundle.input_hash,
            stream_ids=selected_stream_ids,
            kernel_schema=default_kernel_schema_v2(),
            weight_policy_version=_WEIGHT_POLICY_VERSION,
            uniform_weight=1.0,
            lower_bound_frame_budget=(
                config.budget_bytes[0]
                if lower_bound_frame_budget is None
                else lower_bound_frame_budget
            ),
            key_registry_limit=_LOWER_BOUND_KEY_REGISTRY_LIMIT,
            max_semantic_key_bytes=_LOWER_BOUND_MAX_SEMANTIC_KEY_BYTES,
            packing_policy=PackingPolicyV2.disabled(),
            recomposition_policy_hash="",
            policy_hash="",
        )
        if not set(provisional.stream_ids).issubset(available_stream_ids):
            raise ValueError("runner v2 policy selects an unknown stream")
        if provisional.lower_bound_frame_budget != config.budget_bytes[0]:
            raise ValueError("runner v2 lower_bound_frame_budget must equal 8K")
        return replace(provisional, policy_hash=_policy_hash(provisional))

    def __post_init__(self) -> None:
        if self.policy_id != _POLICY_ID:
            raise ValueError("runner v2 lower-bound policy_id is frozen")
        if self.protocol_id != "crm-capsule-stress-v2":
            raise ValueError("runner v2 protocol_id must be crm-capsule-stress-v2")
        if self.schema_version != 2:
            raise ValueError("runner v2 schema_version must be 2")
        if not _is_sha256(self.config_hash):
            raise ValueError("runner v2 config_hash must be lowercase SHA-256")
        if not _is_sha256(self.bundle_input_hash):
            raise ValueError("runner v2 bundle_input_hash must be lowercase SHA-256")
        _validate_frozen_protocol_v2_anchor(
            subject="runner v2 policy",
            protocol_id=self.protocol_id,
            schema_version=self.schema_version,
            config_hash=self.config_hash,
            bundle_input_hash=self.bundle_input_hash,
            lower_bound_frame_budget=self.lower_bound_frame_budget,
        )
        if not self.stream_ids or self.stream_ids != tuple(
            sorted(set(self.stream_ids))
        ):
            raise ValueError(
                "runner v2 stream_ids must be nonempty, unique, and sorted"
            )
        if self.kernel_schema != default_kernel_schema_v2():
            raise ValueError(
                "runner v2 lower bound must use the default v2 kernel schema"
            )
        if self.weight_policy_version != _WEIGHT_POLICY_VERSION:
            raise ValueError("runner v2 lower bound weight policy version is frozen")
        if not math.isfinite(self.uniform_weight) or self.uniform_weight != 1.0:
            raise ValueError("runner v2 lower bound must use uniform unit weights")
        if self.lower_bound_frame_budget <= 0:
            raise ValueError("runner v2 lower_bound_frame_budget must be positive")
        if self.key_registry_limit != _LOWER_BOUND_KEY_REGISTRY_LIMIT:
            raise ValueError("runner v2 key_registry_limit is frozen")
        if self.max_semantic_key_bytes != _LOWER_BOUND_MAX_SEMANTIC_KEY_BYTES:
            raise ValueError("runner v2 max_semantic_key_bytes is frozen")
        if self.packing_policy != PackingPolicyV2.disabled():
            raise ValueError("runner v2 lower bound must disable physical packing")
        if self.recomposition_policy_hash != "":
            raise ValueError(
                "runner v2 lower bound must not claim a recomposition transition policy"
            )
        if self.policy_hash and self.policy_hash != _policy_hash(self):
            raise ValueError("runner v2 policy_hash does not bind policy fields")


def default_runner_policy_v2(
    config: ProtocolV2Config,
    bundle: ProtocolV2Bundle,
    *,
    stream_ids: tuple[str, ...] | None = None,
    lower_bound_frame_budget: int | None = None,
) -> RunnerPolicyV2:
    """Return the frozen deterministic lower-bound policy for the given inputs."""
    return RunnerPolicyV2.create(
        config,
        bundle,
        stream_ids=stream_ids,
        lower_bound_frame_budget=lower_bound_frame_budget,
    )


@dataclass(frozen=True, slots=True)
class RunnerLowerBoundRowV2:
    """One read-only full-control-plane kernel lower-bound observation."""

    stream_id: str
    generation: int
    budget_label: str
    budget_bytes: int
    kernel_only_lower_bound_bytes: int
    budget_frame_kernel_only_bytes: int
    core_only_control_bytes: int
    frontier_count: int
    receipt_count: int
    active_weight_count: int
    kernel_source_count: int
    shadow_full_retention_hash: str
    shadow_kernel_only_hash: str
    lower_bound_frame_reasons: tuple[str, ...]
    budget_frame_reasons: tuple[str, ...]
    failed: bool

    def __post_init__(self) -> None:
        if not self.stream_id or self.generation <= 0 or not self.budget_label:
            raise ValueError("runner v2 row identity must be present")
        if any(
            value <= 0
            for value in (
                self.budget_bytes,
                self.kernel_only_lower_bound_bytes,
                self.budget_frame_kernel_only_bytes,
                self.core_only_control_bytes,
            )
        ):
            raise ValueError("runner v2 row bytes must be positive")
        if any(
            value <= 0
            for value in (
                self.frontier_count,
                self.receipt_count,
                self.active_weight_count,
                self.kernel_source_count,
            )
        ):
            raise ValueError("runner v2 row control counts must be positive")
        if self.frontier_count != self.receipt_count:
            raise ValueError("runner v2 receipts must exactly cover the frontier")
        if self.frontier_count != self.active_weight_count:
            raise ValueError("runner v2 weights must exactly cover active sources")
        if not _is_sha256(self.shadow_full_retention_hash):
            raise ValueError("runner v2 full-retention hash must be lowercase SHA-256")
        if not _is_sha256(self.shadow_kernel_only_hash):
            raise ValueError("runner v2 kernel-only hash must be lowercase SHA-256")
        for reasons in (
            self.lower_bound_frame_reasons,
            self.budget_frame_reasons,
        ):
            if reasons != tuple(sorted(set(reasons))):
                raise ValueError("runner v2 row reasons must be canonical")
        if self.failed != bool(self.budget_frame_reasons):
            raise ValueError("runner v2 failure flag must match budget-frame reasons")


@dataclass(frozen=True, slots=True)
class RunnerFirstFailureV2:
    """The retained first infeasible generation for one frozen budget level."""

    stream_id: str
    budget_label: str
    budget_bytes: int
    generation: int
    budget_frame_kernel_only_bytes: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.stream_id
            or not self.budget_label
            or self.budget_bytes <= 0
            or self.generation <= 0
            or self.budget_frame_kernel_only_bytes <= self.budget_bytes
        ):
            raise ValueError("runner v2 first-failure fields are inconsistent")
        if not self.reasons or self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("runner v2 first-failure reasons must be canonical")


def _report_projection(report: RunnerPreflightReportV2) -> dict[str, object]:
    return {
        field.name: getattr(report, field.name)
        for field in fields(report)
        if field.name != "result_hash"
    }


def _report_hash(report: RunnerPreflightReportV2) -> str:
    return sha256_text(
        canonical_json(
            {"domain": _REPORT_HASH_DOMAIN, "report": _report_projection(report)}
        )
    )


@dataclass(frozen=True, slots=True)
class RunnerPreflightReportV2:
    """Canonical receipt for a read-only schedule reachability diagnosis."""

    policy_id: str
    protocol_id: str
    schema_version: int
    config_hash: str
    bundle_input_hash: str
    policy_hash: str
    stream_ids: tuple[str, ...]
    kernel_schema: KernelSchemaV2
    lower_bound_frame_budget: int
    key_registry_limit: int
    max_semantic_key_bytes: int
    weight_policy_version: str
    uniform_weight: float
    packing_policy: PackingPolicyV2
    recomposition_policy_hash: str
    rows: tuple[RunnerLowerBoundRowV2, ...]
    first_failures: tuple[RunnerFirstFailureV2, ...]
    passed: bool
    result_hash: str

    def __post_init__(self) -> None:
        if self.policy_id != _POLICY_ID:
            raise ValueError("runner v2 report policy_id is invalid")
        if self.protocol_id != "crm-capsule-stress-v2" or self.schema_version != 2:
            raise ValueError("runner v2 report identity is not Protocol v2")
        for value, label in (
            (self.config_hash, "config_hash"),
            (self.bundle_input_hash, "bundle_input_hash"),
            (self.policy_hash, "policy_hash"),
        ):
            if not _is_sha256(value):
                raise ValueError(f"runner v2 report {label} must be lowercase SHA-256")
        _validate_frozen_protocol_v2_anchor(
            subject="runner v2 report",
            protocol_id=self.protocol_id,
            schema_version=self.schema_version,
            config_hash=self.config_hash,
            bundle_input_hash=self.bundle_input_hash,
            lower_bound_frame_budget=self.lower_bound_frame_budget,
        )
        try:
            expected_policy_hash = _policy_hash(_policy_from_report(self))
        except ValueError as error:
            raise ValueError(
                "runner v2 report policy_hash does not bind policy fields"
            ) from error
        if self.policy_hash != expected_policy_hash:
            raise ValueError("runner v2 report policy_hash does not bind policy fields")
        if self.lower_bound_frame_budget <= 0:
            raise ValueError("runner v2 report frame budget must be positive")
        if self.key_registry_limit != _LOWER_BOUND_KEY_REGISTRY_LIMIT:
            raise ValueError("runner v2 report key registry limit is invalid")
        if self.max_semantic_key_bytes != _LOWER_BOUND_MAX_SEMANTIC_KEY_BYTES:
            raise ValueError("runner v2 report semantic key bound is invalid")
        if (
            self.weight_policy_version != _WEIGHT_POLICY_VERSION
            or self.uniform_weight != 1.0
        ):
            raise ValueError("runner v2 report weight framing is invalid")
        if self.packing_policy != PackingPolicyV2.disabled():
            raise ValueError("runner v2 report packing framing is invalid")
        if self.recomposition_policy_hash != "":
            raise ValueError("runner v2 report must not claim a transition policy")
        if not self.rows:
            raise ValueError("runner v2 report rows must not be empty")
        if any(not isinstance(row, RunnerLowerBoundRowV2) for row in self.rows):
            raise ValueError("runner v2 report rows must be lower-bound rows")
        for row in self.rows:
            row.__post_init__()
        if self.rows != tuple(sorted(self.rows, key=_row_sort_key)):
            raise ValueError("runner v2 report rows must be canonically sorted")
        if any(
            not isinstance(item, RunnerFirstFailureV2) for item in self.first_failures
        ):
            raise ValueError("runner v2 report first failures must be typed")
        for item in self.first_failures:
            item.__post_init__()
        if self.first_failures != tuple(
            sorted(
                self.first_failures,
                key=lambda item: (
                    -item.budget_bytes,
                    item.budget_label,
                    item.stream_id,
                ),
            )
        ):
            raise ValueError("runner v2 first failures must be canonically sorted")
        expected_first_failures = _first_failures(self.rows)
        if self.first_failures != expected_first_failures:
            raise ValueError(
                "runner v2 first failures must derive from all failed rows"
            )
        if self.passed != (not any(row.failed for row in self.rows)):
            raise ValueError("runner v2 passed flag must preserve every failure row")
        if self.result_hash and self.result_hash != _report_hash(self):
            raise ValueError("runner v2 result_hash does not bind report fields")


def _policy_from_report(report: RunnerPreflightReportV2) -> RunnerPolicyV2:
    """Reconstruct the exact policy projection bound into a public report."""
    return RunnerPolicyV2(
        policy_id=report.policy_id,
        protocol_id=report.protocol_id,
        schema_version=report.schema_version,
        config_hash=report.config_hash,
        bundle_input_hash=report.bundle_input_hash,
        stream_ids=report.stream_ids,
        kernel_schema=report.kernel_schema,
        weight_policy_version=report.weight_policy_version,
        uniform_weight=report.uniform_weight,
        lower_bound_frame_budget=report.lower_bound_frame_budget,
        key_registry_limit=report.key_registry_limit,
        max_semantic_key_bytes=report.max_semantic_key_bytes,
        packing_policy=report.packing_policy,
        recomposition_policy_hash=report.recomposition_policy_hash,
        policy_hash="",
    )


def _row_sort_key(row: RunnerLowerBoundRowV2) -> tuple[str, int, int, str]:
    return row.stream_id, row.generation, -row.budget_bytes, row.budget_label


def _first_failures(
    rows: tuple[RunnerLowerBoundRowV2, ...],
) -> tuple[RunnerFirstFailureV2, ...]:
    first_by_budget: dict[tuple[str, int], RunnerLowerBoundRowV2] = {}
    for row in rows:
        if not row.failed:
            continue
        key = row.budget_label, row.budget_bytes
        previous = first_by_budget.get(key)
        if previous is None or (row.generation, row.stream_id) < (
            previous.generation,
            previous.stream_id,
        ):
            first_by_budget[key] = row
    return tuple(
        sorted(
            (
                RunnerFirstFailureV2(
                    stream_id=row.stream_id,
                    budget_label=row.budget_label,
                    budget_bytes=row.budget_bytes,
                    generation=row.generation,
                    budget_frame_kernel_only_bytes=row.budget_frame_kernel_only_bytes,
                    reasons=row.budget_frame_reasons,
                )
                for row in first_by_budget.values()
            ),
            key=lambda item: (-item.budget_bytes, item.budget_label, item.stream_id),
        )
    )


def _shadow_control_inputs(
    sources: tuple[ProtocolSourceV2, ...],
) -> tuple[
    tuple[ActiveRecordV2, ...],
    tuple[KeyWinnerV2, ...],
    tuple[SourceReceiptV2, ...],
]:
    """Build complete control state from detached shadow sources only."""
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


def _uniform_weights(
    records: Iterable[ActiveRecordV2], policy: RunnerPolicyV2
) -> WeightPolicyV2:
    return WeightPolicyV2.create(
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


@dataclass(frozen=True, slots=True)
class _KernelOnlyControl:
    resident_bytes: int
    reasons: tuple[str, ...]
    frontier_count: int
    receipt_count: int
    active_weight_count: int
    kernel_source_ids: tuple[str, ...]


def _kernel_only_control(
    sources: tuple[ProtocolSourceV2, ...],
    *,
    generation: int,
    requested_budget: int,
    policy: RunnerPolicyV2,
) -> _KernelOnlyControl:
    records, frontier, receipts = _shadow_control_inputs(sources)
    weights = _uniform_weights(records, policy)
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
    if selection.resident_bytes is None:
        raise ValueError("runner v2 lower-bound state could not be constructed")
    return _KernelOnlyControl(
        resident_bytes=selection.resident_bytes,
        reasons=selection.reasons,
        frontier_count=len(frontier),
        receipt_count=len(receipts),
        active_weight_count=len(weights.source_weights),
        kernel_source_ids=tuple(
            sorted(record.atom.source_id for record in selection.records)
        ),
    )


def _validate_runner_identities(
    config: ProtocolV2Config,
    bundle: ProtocolV2Bundle,
    policy: RunnerPolicyV2,
) -> tuple[ProtocolV2Stream, ...]:
    policy.__post_init__()
    if not policy.policy_hash:
        raise ValueError("runner v2 policy_hash must bind policy fields")
    if (
        bundle.protocol_id != config.protocol_id
        or policy.protocol_id != config.protocol_id
    ):
        raise ValueError("runner v2 protocol_id mismatch")
    if bundle.schema_version != 2 or policy.schema_version != 2:
        raise ValueError("runner v2 schema_version mismatch")
    if (
        bundle.config_hash != config.config_hash
        or policy.config_hash != config.config_hash
    ):
        raise ValueError("runner v2 config hash mismatch")
    if policy.bundle_input_hash != bundle.input_hash:
        raise ValueError("runner v2 bundle input hash mismatch")
    if policy.lower_bound_frame_budget != config.budget_bytes[0]:
        raise ValueError("runner v2 lower_bound_frame_budget must equal frozen 8K")
    if policy.kernel_schema.continuity_floor_bytes != config.continuity_floor_bytes:
        raise ValueError("runner v2 kernel schema floor mismatch")
    if policy.packing_policy != PackingPolicyV2.disabled():
        raise ValueError("runner v2 lower bound must disable physical packing")
    streams_by_id = {stream.stream_id: stream for stream in bundle.streams}
    if len(streams_by_id) != len(bundle.streams):
        raise ValueError("runner v2 bundle stream ids are not unique")
    try:
        return tuple(streams_by_id[stream_id] for stream_id in policy.stream_ids)
    except KeyError as error:
        raise ValueError("runner v2 policy selects an unknown stream") from error


def _validate_shadow_round(
    *,
    stream_id: str,
    generation: int,
    full_source_ids: tuple[str, ...],
    kernel_source_ids: tuple[str, ...],
    selected_kernel_source_ids: tuple[str, ...],
) -> None:
    if not full_source_ids or not kernel_source_ids:
        raise ValueError("runner v2 shadow corpus must not be empty")
    if not set(kernel_source_ids).issubset(full_source_ids):
        raise ValueError("runner v2 kernel-only shadow sources leave the full frontier")
    if set(kernel_source_ids) != set(selected_kernel_source_ids):
        raise ValueError(
            f"runner v2 kernel selection diverges from shadow at {stream_id}/g{generation}"
        )


def preflight_runner_v2(
    config: ProtocolV2Config,
    bundle: ProtocolV2Bundle,
    policy: RunnerPolicyV2,
) -> RunnerPreflightReportV2:
    """Diagnose complete kernel-only control-state reachability without an arm run."""
    generator_report = preflight_protocol_v2(config)
    if not generator_report.passed:
        raise ValueError("runner v2 requires a passing generator-only preflight")
    selected_streams = _validate_runner_identities(config, bundle, policy)
    canonical_bundle = generate_protocol_v2(config)
    if canonical_json(bundle) != canonical_json(canonical_bundle):
        raise ValueError("runner v2 bundle does not match frozen generator output")
    if generator_report.input_hash != bundle.input_hash:
        raise ValueError("runner v2 generator preflight input hash mismatch")

    rows: list[RunnerLowerBoundRowV2] = []
    for stream in selected_streams:
        for shadow_round in stream.shadow_rounds:
            full_sources = shadow_round.full_retention_sources
            fixed = _kernel_only_control(
                full_sources,
                generation=shadow_round.generation,
                requested_budget=policy.lower_bound_frame_budget,
                policy=policy,
            )
            core_only = _kernel_only_control(
                shadow_round.kernel_only_sources,
                generation=shadow_round.generation,
                requested_budget=policy.lower_bound_frame_budget,
                policy=policy,
            )
            _validate_shadow_round(
                stream_id=stream.stream_id,
                generation=shadow_round.generation,
                full_source_ids=tuple(source.atom.source_id for source in full_sources),
                kernel_source_ids=tuple(
                    source.atom.source_id for source in shadow_round.kernel_only_sources
                ),
                selected_kernel_source_ids=fixed.kernel_source_ids,
            )
            for budget_label, budget_bytes in zip(
                config.budget_labels, config.budget_bytes, strict=True
            ):
                budget_frame = _kernel_only_control(
                    full_sources,
                    generation=shadow_round.generation,
                    requested_budget=budget_bytes,
                    policy=policy,
                )
                rows.append(
                    RunnerLowerBoundRowV2(
                        stream_id=stream.stream_id,
                        generation=shadow_round.generation,
                        budget_label=budget_label,
                        budget_bytes=budget_bytes,
                        kernel_only_lower_bound_bytes=fixed.resident_bytes,
                        budget_frame_kernel_only_bytes=budget_frame.resident_bytes,
                        core_only_control_bytes=core_only.resident_bytes,
                        frontier_count=fixed.frontier_count,
                        receipt_count=fixed.receipt_count,
                        active_weight_count=fixed.active_weight_count,
                        kernel_source_count=len(fixed.kernel_source_ids),
                        shadow_full_retention_hash=shadow_round.full_retention_hash,
                        shadow_kernel_only_hash=shadow_round.kernel_only_hash,
                        lower_bound_frame_reasons=fixed.reasons,
                        budget_frame_reasons=budget_frame.reasons,
                        failed=bool(budget_frame.reasons),
                    )
                )

    frozen_rows = tuple(sorted(rows, key=_row_sort_key))
    provisional = RunnerPreflightReportV2(
        policy_id=policy.policy_id,
        protocol_id=config.protocol_id,
        schema_version=config.schema_version,
        config_hash=config.config_hash,
        bundle_input_hash=bundle.input_hash,
        policy_hash=policy.policy_hash,
        stream_ids=policy.stream_ids,
        kernel_schema=policy.kernel_schema,
        lower_bound_frame_budget=policy.lower_bound_frame_budget,
        key_registry_limit=policy.key_registry_limit,
        max_semantic_key_bytes=policy.max_semantic_key_bytes,
        weight_policy_version=policy.weight_policy_version,
        uniform_weight=policy.uniform_weight,
        packing_policy=policy.packing_policy,
        recomposition_policy_hash=policy.recomposition_policy_hash,
        rows=frozen_rows,
        first_failures=_first_failures(frozen_rows),
        passed=not any(row.failed for row in frozen_rows),
        result_hash="",
    )
    return replace(provisional, result_hash=_report_hash(provisional))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.preflight:
        parser.error("only --preflight is available during the reachability gate")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the read-only preflight; an unreachable schedule is a valid receipt."""
    args = _parse_args(argv)
    config = load_protocol_v2_config(args.config)
    bundle = generate_protocol_v2(config)
    policy = default_runner_policy_v2(config, bundle)
    report = preflight_runner_v2(config, bundle, policy)
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
