"""Pure preregistered stress data for the V21-007 multi-round evaluator."""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Final

from crm_experiment.contracts_v21 import (
    CONTROL_FOLD_PROTOCOL_V21,
    CapsuleRoleV21,
    CapsuleStateV21,
    ControlFoldBoundsV21,
    ControlFrameV21,
    DictionaryEntryV21,
    ExactRecordV21,
    LossLedgerV21,
    SparseWeightPolicyV21,
    canonical_json_v21,
    capacity_policy_hash_v21,
    dictionary_commitment_v21,
    dictionary_identity_v21,
    loss_ledger_root_v21,
    namespace_identity_v21,
    record_identity_v21,
    reencoding_policy_hash_v21,
    source_commitment_v21,
)
from crm_experiment.evidenced_planner_v21 import plan_evidenced_reencoding_v21
from crm_experiment.evidenced_reencoder_v21 import EvidencedReencodingStatusV21
from crm_experiment.evidenced_state_v21 import (
    ContributionLocatorV21,
    ContributionSupportV21,
    EvidencedCapsuleStateV21,
    evidenced_state_hash_v21,
)
from crm_experiment.multiround_v21 import (
    EvidencedMultiroundResultV21,
    MultiroundExitReasonV21,
    MultiroundStatusV21,
    MultiroundTraceV21,
    RoundInputV21,
    evaluate_evidenced_multiround_v21,
    validate_evidenced_multiround_result_v21,
)
from crm_experiment.reencoding_contracts_v21 import (
    AdvanceChainHeadV21,
    EvidencedReencodingPolicyV21,
    FoldContributionEvidenceV21,
    SolverModeV21,
    SourceEnvelopeV21,
    SourceGraphNodeV21,
    SourceRecordV21,
    contribution_root_v21,
    evidenced_reencoding_policy_root_v21,
    hard_graph_root_v21,
    seed_advance_chain_head_v21,
    source_envelope_root_v21,
)

PREREGISTERED_STRESS_PROTOCOL_V21: Final = "crm-v21-008-preregistered-stress-s3/v1"
PREREGISTERED_STRESS_SCHEMA_V21: Final = 1
STRESS_DATA_ROOT_DOMAIN_V21: Final = "crm-v21-008-stress-data-root-s3/v1"
WORKLOAD_ROOT_DOMAIN_V21: Final = "crm-v21-008-workload-root-s3/v1"
PPM_SCALE_V21: Final = 1_000_000
STRESS_ROUND_COUNT_V21: Final = 12

_BATCH_SIZES_V21: Final = (1, 2, 3, 4) * 3
_BODY_BYTES_PER_RECORD_V21: Final = (3_072,) * 4 + (4_096,) * 4 + (5_120,) * 4
_CORE_BODY_BYTES_V21: Final = 256
_CORE_NAMESPACE_V21: Final = namespace_identity_v21("crm-v21-008-core")
_INCOMING_NAMESPACE_V21: Final = namespace_identity_v21("crm-v21-008-incoming")
_STATE_HASH_DOMAIN_V21: Final = "crm-v21-evidenced-state-s3/v1"
_ENVELOPE_ROOT_DOMAIN_V21: Final = "crm-v21-source-envelope-s3/v1"
_POLICY_ROOT_DOMAIN_V21: Final = "crm-v21-evidenced-policy-s3/v1"
_HEAD_ROOT_DOMAIN_V21: Final = "crm-v21-advance-head-s3/v1"
_MATRIX_ROOT_DOMAIN_V21: Final = "crm-v21-evidenced-matrix-s3/v1"
_TRANSITION_ROOT_DOMAIN_V21: Final = "crm-v21-evidenced-transition-s3/v1"
_RUN_ROOT_DOMAIN_V21: Final = "crm-v21-multiround-run-s3/v1"


@dataclass(frozen=True, slots=True)
class PreregisteredStressWorkloadV21:
    """Fixed body-free declaration of the sole V21-008 workload arm."""

    round_count: int
    batch_sizes: tuple[int, ...]
    body_bytes_per_record: tuple[int, ...]
    core_source_count: int
    max_source_rows: int
    hard_edge_count: int
    solver_mode: str
    max_new_segments: int
    max_plan_evaluations: int
    segment_loss_units: int


@dataclass(frozen=True, slots=True)
class PreregisteredStressSetupV21:
    """Concrete setup inputs; planner receipts are deliberately not retained."""

    initial_state: EvidencedCapsuleStateV21
    initial_head: AdvanceChainHeadV21
    rounds: tuple[RoundInputV21, ...]


@dataclass(frozen=True, slots=True)
class PreregisteredStressRecordV21:
    """One compact, body-free accepted-round measurement record."""

    round_index: int
    batch_size: int
    body_bytes_per_record: int
    incoming_count: int
    incoming_body_bytes: int
    before_control_bytes: int
    before_source_body_bytes: int
    before_resident_bytes: int
    control_bytes: int
    source_body_bytes: int
    resident_bytes: int
    control_delta_bytes: int
    source_body_delta_bytes: int
    resident_growth_bytes: int
    avoided_incremental_bytes: int
    retention_ppm: int
    reduction_ppm: int
    release_ppm: int
    loss_delta: int
    cumulative_loss: int
    loss_per_avoided_ppm: int
    before_state_hash: str
    after_state_hash: str
    source_envelope_root: str
    policy_root: str
    before_advance_head_root: str
    after_advance_head_root: str
    matrix_root: str
    transition_root: str
    before_generation: int
    after_generation: int
    before_high_water: int
    after_high_water: int
    release_count: int


@dataclass(frozen=True, slots=True)
class PreregisteredStressDataV21:
    """The validated V21-008 output; it contains no setup payload or receipt."""

    schema_version: int
    protocol_id: str
    workload: PreregisteredStressWorkloadV21
    workload_root: str
    evaluator_run_root: str
    replay_match: bool
    terminal_status: str
    terminal_exit_reason: str
    terminal_failed_round_index: int | None
    terminal_reason: str | None
    records: tuple[PreregisteredStressRecordV21, ...]
    data_root: str


def _workload_v21() -> PreregisteredStressWorkloadV21:
    return PreregisteredStressWorkloadV21(
        round_count=STRESS_ROUND_COUNT_V21,
        batch_sizes=_BATCH_SIZES_V21,
        body_bytes_per_record=_BODY_BYTES_PER_RECORD_V21,
        core_source_count=1,
        max_source_rows=5,
        hard_edge_count=0,
        solver_mode=SolverModeV21.DETERMINISTIC_GREEDY.value,
        max_new_segments=1,
        max_plan_evaluations=16,
        segment_loss_units=1,
    )


def _ascii_body_v21(label: str, byte_count: int) -> str:
    prefix = f"{label}|"
    if len(prefix.encode("ascii")) > byte_count:
        raise ValueError("stress source label exceeds its frozen body size")
    return prefix + "x" * (byte_count - len(prefix))


def _exact_record_v21(
    *,
    namespace: str,
    label: str,
    incarnation: int,
    as_of: int,
    body_bytes: int,
    core_required: bool,
) -> ExactRecordV21:
    record_id = record_identity_v21(namespace, label)
    body = _ascii_body_v21(label, body_bytes)
    return ExactRecordV21(
        record_id=record_id,
        namespace=namespace,
        incarnation=incarnation,
        as_of=as_of,
        commitment=source_commitment_v21(
            record_id=record_id,
            namespace=namespace,
            incarnation=incarnation,
            as_of=as_of,
            body=body,
            core_required=core_required,
            active=True,
        ),
        body=body,
        core_required=core_required,
        active=True,
        hard_depends_on=(),
    )


def _source_v21(record: ExactRecordV21) -> SourceRecordV21:
    return SourceRecordV21(
        record_id=record.record_id,
        namespace=record.namespace,
        incarnation=record.incarnation,
        as_of=record.as_of,
        commitment=record.commitment,
        body=record.body,
        core_required=record.core_required,
        active=record.active,
        hard_depends_on=record.hard_depends_on,
    )


def _coverage_vector_v21(round_number: int, slot: int) -> tuple[int, ...]:
    return tuple(
        1 if (round_number + slot + column) % 2 == 0 else 0 for column in range(4)
    )


def _bridge_vector_v21(round_number: int, slot: int) -> tuple[int, ...]:
    return tuple((round_number + slot + column) % 2 for column in range(3))


def _contribution_v21(
    *,
    source: SourceRecordV21,
    key: str,
    role: CapsuleRoleV21,
    round_number: int,
    slot: int,
) -> FoldContributionEvidenceV21:
    return FoldContributionEvidenceV21(
        record_id=source.record_id,
        source_commitment=source.commitment,
        contribution_keys=(key,),
        role=role,
        coverage_vector=_coverage_vector_v21(round_number, slot),
        bridge_vector=_bridge_vector_v21(round_number, slot),
        representative_candidates=(),
    )


def _incoming_key_v21(round_number: int, slot: int) -> str:
    return dictionary_identity_v21(
        _INCOMING_NAMESPACE_V21,
        f"incoming-key-r{round_number:02d}-s{slot:02d}",
    )


def _incoming_record_v21(
    *, round_number: int, slot: int, body_bytes: int
) -> ExactRecordV21:
    return _exact_record_v21(
        namespace=_INCOMING_NAMESPACE_V21,
        label=f"incoming-r{round_number:02d}-s{slot:02d}",
        incarnation=round_number,
        as_of=round_number * 100 + slot,
        body_bytes=body_bytes,
        core_required=False,
    )


def _initial_state_v21() -> tuple[EvidencedCapsuleStateV21, ExactRecordV21, str]:
    bounds = ControlFoldBoundsV21(
        max_exact_kernel_records=1,
        max_exact_kernel_bytes=4_096,
        max_hot_records=4,
        max_hot_bytes=32_768,
        max_hot_frontier_entries=0,
        max_hot_frontier_bytes=2,
        max_dictionary_entries=62,
        max_dictionary_bytes=65_536,
        max_segments=12,
        max_representatives_per_segment=0,
        coverage_vector_size=4,
        bridge_vector_size=3,
        max_barriers=12,
        max_sparse_overrides=0,
        max_direct_parent_ids=0,
        max_parent_id_bytes=0,
        loss_ledger_bytes=4_096,
    )
    frame_policy_hash = reencoding_policy_hash_v21(
        "crm-v21-008-preregistered-stress-policy"
    )
    frame = ControlFrameV21(
        protocol_id=CONTROL_FOLD_PROTOCOL_V21,
        schema_version=3,
        generation=0,
        high_water=0,
        accepted_budget=1_000_000,
        reencoding_policy_hash=frame_policy_hash,
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )
    core = _exact_record_v21(
        namespace=_CORE_NAMESPACE_V21,
        label="core",
        incarnation=0,
        as_of=0,
        body_bytes=_CORE_BODY_BYTES_V21,
        core_required=True,
    )
    core_key = dictionary_identity_v21(_CORE_NAMESPACE_V21, "core-key")
    keys = (core_key,) + tuple(
        _incoming_key_v21(round_number, slot)
        for round_number, batch_size in enumerate(_BATCH_SIZES_V21, start=1)
        for slot in range(1, batch_size + 1)
    )
    dictionary = tuple(
        sorted(
            (
                DictionaryEntryV21(
                    namespace=(
                        _CORE_NAMESPACE_V21
                        if key == core_key
                        else _INCOMING_NAMESPACE_V21
                    ),
                    key=key,
                    commitment=dictionary_commitment_v21(
                        (
                            _CORE_NAMESPACE_V21
                            if key == core_key
                            else _INCOMING_NAMESPACE_V21
                        ),
                        key,
                    ),
                )
                for key in keys
            ),
            key=lambda entry: entry.key,
        )
    )
    state = CapsuleStateV21(
        frame=frame,
        bounds=bounds,
        exact_kernel=(core,),
        hot_cache=(),
        hot_frontier=(),
        dictionary=dictionary,
        capsule_segments=(),
        fold_barriers=(),
        sparse_weight_policy=SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=LossLedgerV21(
            role_counts=(),
            cumulative_loss_root=loss_ledger_root_v21((), 0),
            last_fold_generation=0,
        ),
    )
    return (
        EvidencedCapsuleStateV21(
            state=state,
            contribution_index=(
                ContributionLocatorV21(
                    contribution_key=core_key,
                    support=ContributionSupportV21.EXACT,
                    resident_id=core.record_id,
                    support_commitment=core.commitment,
                ),
            ),
        ),
        core,
        core_key,
    )


def _policy_v21(frame_policy_hash: str) -> EvidencedReencodingPolicyV21:
    return EvidencedReencodingPolicyV21(
        frame_policy_hash=frame_policy_hash,
        max_matrix_rows=5,
        max_hard_edges=0,
        max_decisions=16,
        max_plan_evaluations=16,
        max_new_segments=1,
        max_loss_units=4,
        per_role_loss_caps=(
            (CapsuleRoleV21.CONTEXT, 30),
            (CapsuleRoleV21.ROOT_GOAL, 0),
        ),
        segment_loss_units=1,
        drop_loss_units=2,
        compaction_loss_units=2,
        solver_mode=SolverModeV21.DETERMINISTIC_GREEDY,
    )


def _envelope_v21(
    *,
    base_hash: str,
    core: ExactRecordV21,
    core_key: str,
    incoming: tuple[ExactRecordV21, ...],
    round_number: int,
) -> SourceEnvelopeV21:
    sources = tuple(
        sorted(
            (_source_v21(core), *(_source_v21(record) for record in incoming)),
            key=lambda record: record.record_id,
        )
    )
    key_by_record_id = {
        record.record_id: _incoming_key_v21(round_number, slot)
        for slot, record in enumerate(incoming, start=1)
    }
    contributions = tuple(
        sorted(
            (
                _contribution_v21(
                    source=source,
                    key=(
                        core_key
                        if source.record_id == core.record_id
                        else key_by_record_id[source.record_id]
                    ),
                    role=(
                        CapsuleRoleV21.ROOT_GOAL
                        if source.record_id == core.record_id
                        else CapsuleRoleV21.CONTEXT
                    ),
                    round_number=round_number,
                    slot=(0 if source.record_id == core.record_id else 1),
                )
                for source in sources
            ),
            key=lambda evidence: evidence.record_id,
        )
    )
    graph_nodes = tuple(
        SourceGraphNodeV21(
            record_id=source.record_id,
            source_commitment=source.commitment,
        )
        for source in sources
    )
    hard_root = hard_graph_root_v21(graph_nodes=graph_nodes, hard_edges=())
    contribution_root = contribution_root_v21(contributions)
    incoming_ids = tuple(sorted(record.record_id for record in incoming))
    envelope_root = source_envelope_root_v21(
        base_state_hash=base_hash,
        source_records=sources,
        incoming_record_ids=incoming_ids,
        graph_nodes=graph_nodes,
        hard_edges=(),
        contribution_evidence=contributions,
        hard_graph_root=hard_root,
        contribution_root=contribution_root,
    )
    return SourceEnvelopeV21(
        base_state_hash=base_hash,
        source_records=sources,
        incoming_record_ids=incoming_ids,
        graph_nodes=graph_nodes,
        hard_edges=(),
        contribution_evidence=contributions,
        hard_graph_root=hard_root,
        contribution_root=contribution_root,
        envelope_root=envelope_root,
    )


def build_preregistered_stress_setup_v21() -> PreregisteredStressSetupV21:
    """Materialize the fixed chained inputs without retaining planner results."""
    initial_state, core, core_key = _initial_state_v21()
    initial_hash = evidenced_state_hash_v21(initial_state)
    initial_head = seed_advance_chain_head_v21(
        initial_state,
        expected_state_hash=initial_hash,
    )
    policy = _policy_v21(initial_state.state.frame.reencoding_policy_hash)
    policy_root = evidenced_reencoding_policy_root_v21(policy)
    state = initial_state
    head = initial_head
    rounds: list[RoundInputV21] = []
    for round_index, (batch_size, body_bytes) in enumerate(
        zip(_BATCH_SIZES_V21, _BODY_BYTES_PER_RECORD_V21, strict=True)
    ):
        round_number = round_index + 1
        incoming = tuple(
            _incoming_record_v21(
                round_number=round_number,
                slot=slot,
                body_bytes=body_bytes,
            )
            for slot in range(1, batch_size + 1)
        )
        base_hash = evidenced_state_hash_v21(state)
        envelope = _envelope_v21(
            base_hash=base_hash,
            core=core,
            core_key=core_key,
            incoming=incoming,
            round_number=round_number,
        )
        round_input = RoundInputV21(
            round_index=round_index,
            source_envelope=envelope,
            policy=policy,
            expected_base_state_hash=base_hash,
            expected_source_envelope_root=envelope.envelope_root,
            expected_policy_root=policy_root,
            expected_advance_head_root=head.head_root,
        )
        planned = plan_evidenced_reencoding_v21(
            state,
            head,
            envelope,
            policy,
            expected_base_state_hash=round_input.expected_base_state_hash,
            expected_source_envelope_root=round_input.expected_source_envelope_root,
            expected_policy_root=round_input.expected_policy_root,
            expected_advance_head_root=round_input.expected_advance_head_root,
        )
        if planned.verification.status is not EvidencedReencodingStatusV21.VERIFIED:
            raise ValueError("V21-006 setup did not return a verified successor")
        rounds.append(round_input)
        state = planned.verification.state
        head = planned.verification.advance_head
    return PreregisteredStressSetupV21(
        initial_state=initial_state,
        initial_head=initial_head,
        rounds=tuple(rounds),
    )


def _evaluate_v21(setup: PreregisteredStressSetupV21) -> EvidencedMultiroundResultV21:
    initial_hash = evidenced_state_hash_v21(setup.initial_state)
    result = evaluate_evidenced_multiround_v21(
        setup.initial_state,
        setup.initial_head,
        setup.rounds,
        expected_initial_state_hash=initial_hash,
        expected_initial_advance_head_root=setup.initial_head.head_root,
    )
    validate_evidenced_multiround_result_v21(result)
    return result


def _record_from_trace_v21(
    *,
    trace: MultiroundTraceV21,
    round_number: int,
    batch_size: int,
    body_bytes: int,
    cumulative_loss: int,
) -> PreregisteredStressRecordV21:
    incoming_body_bytes = batch_size * body_bytes
    unfolded = trace.before_resident_bytes + incoming_body_bytes
    avoided_incremental_bytes = unfolded - trace.after_resident_bytes
    if incoming_body_bytes <= 0 or unfolded <= 0 or avoided_incremental_bytes <= 0:
        raise ValueError("stress metric denominator is not positive")
    if trace.release_count <= 0:
        raise ValueError("stress release denominator is not positive")
    return PreregisteredStressRecordV21(
        round_index=round_number,
        batch_size=batch_size,
        body_bytes_per_record=body_bytes,
        incoming_count=batch_size,
        incoming_body_bytes=incoming_body_bytes,
        before_control_bytes=trace.before_control_bytes,
        before_source_body_bytes=trace.before_source_body_bytes,
        before_resident_bytes=trace.before_resident_bytes,
        control_bytes=trace.after_control_bytes,
        source_body_bytes=trace.after_source_body_bytes,
        resident_bytes=trace.after_resident_bytes,
        control_delta_bytes=trace.after_control_bytes - trace.before_control_bytes,
        source_body_delta_bytes=(
            trace.after_source_body_bytes - trace.before_source_body_bytes
        ),
        resident_growth_bytes=trace.after_resident_bytes - trace.before_resident_bytes,
        avoided_incremental_bytes=avoided_incremental_bytes,
        retention_ppm=trace.after_resident_bytes * PPM_SCALE_V21 // unfolded,
        reduction_ppm=avoided_incremental_bytes * PPM_SCALE_V21 // unfolded,
        release_ppm=trace.release_count * PPM_SCALE_V21 // batch_size,
        loss_delta=trace.loss_delta,
        cumulative_loss=cumulative_loss,
        loss_per_avoided_ppm=trace.loss_delta
        * PPM_SCALE_V21
        // avoided_incremental_bytes,
        before_state_hash=trace.before_state_hash,
        after_state_hash=trace.after_state_hash,
        source_envelope_root=trace.source_envelope_root,
        policy_root=trace.policy_root,
        before_advance_head_root=trace.before_advance_head_root,
        after_advance_head_root=trace.after_advance_head_root,
        matrix_root=trace.matrix_root,
        transition_root=trace.transition_root,
        before_generation=trace.before_generation,
        after_generation=trace.after_generation,
        before_high_water=trace.before_high_water,
        after_high_water=trace.after_high_water,
        release_count=trace.release_count,
    )


def _workload_payload_v21(
    workload: PreregisteredStressWorkloadV21,
) -> dict[str, object]:
    return {
        "round_count": workload.round_count,
        "batch_sizes": workload.batch_sizes,
        "body_bytes_per_record": workload.body_bytes_per_record,
        "core_source_count": workload.core_source_count,
        "max_source_rows": workload.max_source_rows,
        "hard_edge_count": workload.hard_edge_count,
        "solver_mode": workload.solver_mode,
        "max_new_segments": workload.max_new_segments,
        "max_plan_evaluations": workload.max_plan_evaluations,
        "segment_loss_units": workload.segment_loss_units,
    }


def _workload_root_v21(
    protocol_id: str,
    workload: PreregisteredStressWorkloadV21,
) -> str:
    digest = sha256(
        canonical_json_v21(
            {
                "domain": WORKLOAD_ROOT_DOMAIN_V21,
                "value": {
                    "protocol_id": protocol_id,
                    "workload": workload,
                },
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"{WORKLOAD_ROOT_DOMAIN_V21}:{digest}"


def _record_payload_v21(record: PreregisteredStressRecordV21) -> dict[str, object]:
    return {
        "round_index": record.round_index,
        "batch_size": record.batch_size,
        "body_bytes_per_record": record.body_bytes_per_record,
        "incoming_count": record.incoming_count,
        "incoming_body_bytes": record.incoming_body_bytes,
        "before_control_bytes": record.before_control_bytes,
        "before_source_body_bytes": record.before_source_body_bytes,
        "before_resident_bytes": record.before_resident_bytes,
        "control_bytes": record.control_bytes,
        "source_body_bytes": record.source_body_bytes,
        "resident_bytes": record.resident_bytes,
        "control_delta_bytes": record.control_delta_bytes,
        "source_body_delta_bytes": record.source_body_delta_bytes,
        "resident_growth_bytes": record.resident_growth_bytes,
        "avoided_incremental_bytes": record.avoided_incremental_bytes,
        "retention_ppm": record.retention_ppm,
        "reduction_ppm": record.reduction_ppm,
        "release_ppm": record.release_ppm,
        "loss_delta": record.loss_delta,
        "cumulative_loss": record.cumulative_loss,
        "loss_per_avoided_ppm": record.loss_per_avoided_ppm,
        "before_state_hash": record.before_state_hash,
        "after_state_hash": record.after_state_hash,
        "source_envelope_root": record.source_envelope_root,
        "policy_root": record.policy_root,
        "before_advance_head_root": record.before_advance_head_root,
        "after_advance_head_root": record.after_advance_head_root,
        "matrix_root": record.matrix_root,
        "transition_root": record.transition_root,
        "before_generation": record.before_generation,
        "after_generation": record.after_generation,
        "before_high_water": record.before_high_water,
        "after_high_water": record.after_high_water,
        "release_count": record.release_count,
    }


def _data_payload_v21(
    data: PreregisteredStressDataV21, *, include_data_root: bool
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": data.schema_version,
        "protocol_id": data.protocol_id,
        "workload": _workload_payload_v21(data.workload),
        "workload_root": data.workload_root,
        "evaluator_run_root": data.evaluator_run_root,
        "replay_match": data.replay_match,
        "terminal": {
            "status": data.terminal_status,
            "exit_reason": data.terminal_exit_reason,
            "failed_round_index": data.terminal_failed_round_index,
            "reason": data.terminal_reason,
        },
        "records": tuple(_record_payload_v21(record) for record in data.records),
    }
    if include_data_root:
        payload["data_root"] = data.data_root
    return payload


def _data_root_v21(data: PreregisteredStressDataV21) -> str:
    digest = sha256(
        canonical_json_v21(
            {
                "domain": STRESS_DATA_ROOT_DOMAIN_V21,
                "value": _data_payload_v21(data, include_data_root=False),
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"{STRESS_DATA_ROOT_DOMAIN_V21}:{digest}"


def _require_int_v21(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be a plain integer")
    return value


def _require_nonnegative_v21(value: object, label: str) -> int:
    checked = _require_int_v21(value, label)
    if checked < 0:
        raise ValueError(f"{label} must be non-negative")
    return checked


def _require_text_v21(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _require_domain_root_v21(value: object, domain: str, label: str) -> str:
    text = _require_text_v21(value, label)
    if re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", text) is None:
        raise ValueError(f"{label} has the wrong root domain")
    return text


def _validate_record_v21(
    record: PreregisteredStressRecordV21,
    *,
    round_number: int,
    batch_size: int,
    body_bytes: int,
    previous: PreregisteredStressRecordV21 | None,
) -> None:
    if type(record) is not PreregisteredStressRecordV21:
        raise ValueError("stress records must be nominal")
    nonnegative = {
        "round_index": record.round_index,
        "batch_size": record.batch_size,
        "body_bytes_per_record": record.body_bytes_per_record,
        "incoming_count": record.incoming_count,
        "incoming_body_bytes": record.incoming_body_bytes,
        "before_control_bytes": record.before_control_bytes,
        "before_source_body_bytes": record.before_source_body_bytes,
        "before_resident_bytes": record.before_resident_bytes,
        "control_bytes": record.control_bytes,
        "source_body_bytes": record.source_body_bytes,
        "resident_bytes": record.resident_bytes,
        "avoided_incremental_bytes": record.avoided_incremental_bytes,
        "retention_ppm": record.retention_ppm,
        "reduction_ppm": record.reduction_ppm,
        "release_ppm": record.release_ppm,
        "loss_delta": record.loss_delta,
        "cumulative_loss": record.cumulative_loss,
        "loss_per_avoided_ppm": record.loss_per_avoided_ppm,
        "before_generation": record.before_generation,
        "after_generation": record.after_generation,
        "before_high_water": record.before_high_water,
        "after_high_water": record.after_high_water,
        "release_count": record.release_count,
    }
    for label, value in nonnegative.items():
        _require_nonnegative_v21(value, label)
    for label, value in {
        "control_delta_bytes": record.control_delta_bytes,
        "source_body_delta_bytes": record.source_body_delta_bytes,
        "resident_growth_bytes": record.resident_growth_bytes,
    }.items():
        _require_int_v21(value, label)
    if (
        record.round_index != round_number
        or record.batch_size != batch_size
        or record.incoming_count != batch_size
        or record.body_bytes_per_record != body_bytes
        or record.incoming_body_bytes != batch_size * body_bytes
    ):
        raise ValueError("stress record does not match the frozen workload")
    if record.release_count != batch_size or record.release_ppm != PPM_SCALE_V21:
        raise ValueError("stress record release metrics do not match its batch")
    expected_cumulative_loss = (
        previous.cumulative_loss if previous is not None else 0
    ) + batch_size
    if (
        record.loss_delta != batch_size
        or record.cumulative_loss != expected_cumulative_loss
    ):
        raise ValueError("stress record loss is not the preregistered loss")
    if (
        record.before_control_bytes + record.before_source_body_bytes
        != record.before_resident_bytes
        or record.control_bytes + record.source_body_bytes != record.resident_bytes
        or record.control_delta_bytes
        != record.control_bytes - record.before_control_bytes
        or record.source_body_delta_bytes
        != record.source_body_bytes - record.before_source_body_bytes
        or record.resident_growth_bytes
        != record.resident_bytes - record.before_resident_bytes
    ):
        raise ValueError("stress C/B/R arithmetic does not close")
    unfolded = record.before_resident_bytes + record.incoming_body_bytes
    if unfolded <= 0:
        raise ValueError("stress unfolded denominator must be positive")
    avoided = unfolded - record.resident_bytes
    if avoided <= 0 or record.avoided_incremental_bytes != avoided:
        raise ValueError("stress avoided incremental bytes are invalid")
    if (
        record.retention_ppm != record.resident_bytes * PPM_SCALE_V21 // unfolded
        or record.reduction_ppm != avoided * PPM_SCALE_V21 // unfolded
        or record.loss_per_avoided_ppm != record.loss_delta * PPM_SCALE_V21 // avoided
    ):
        raise ValueError("stress integer ppm arithmetic does not close")
    _require_domain_root_v21(
        record.before_state_hash, _STATE_HASH_DOMAIN_V21, "before state"
    )
    _require_domain_root_v21(
        record.after_state_hash, _STATE_HASH_DOMAIN_V21, "after state"
    )
    _require_domain_root_v21(
        record.source_envelope_root,
        _ENVELOPE_ROOT_DOMAIN_V21,
        "source envelope",
    )
    _require_domain_root_v21(record.policy_root, _POLICY_ROOT_DOMAIN_V21, "policy")
    _require_domain_root_v21(
        record.before_advance_head_root,
        _HEAD_ROOT_DOMAIN_V21,
        "before advance head",
    )
    _require_domain_root_v21(
        record.after_advance_head_root,
        _HEAD_ROOT_DOMAIN_V21,
        "after advance head",
    )
    _require_domain_root_v21(record.matrix_root, _MATRIX_ROOT_DOMAIN_V21, "matrix")
    _require_domain_root_v21(
        record.transition_root,
        _TRANSITION_ROOT_DOMAIN_V21,
        "transition",
    )
    if record.after_generation != record.before_generation + 1:
        raise ValueError("stress generation does not advance exactly once")
    if record.after_high_water <= record.before_high_water:
        raise ValueError("stress high_water does not advance")
    if record.after_high_water != round_number * 100 + batch_size:
        raise ValueError("stress high_water does not match the frozen source arm")
    if previous is None:
        if record.before_generation != 0 or record.before_high_water != 0:
            raise ValueError("stress first record does not bind the frozen predecessor")
        return
    if (
        record.before_state_hash != previous.after_state_hash
        or record.before_advance_head_root != previous.after_advance_head_root
        or record.before_generation != previous.after_generation
        or record.before_high_water != previous.after_high_water
    ):
        raise ValueError("stress record chain is discontinuous")


def validate_preregistered_stress_data_v21(data: PreregisteredStressDataV21) -> None:
    """Fail closed unless a body-free report is the exact frozen workload."""
    if type(data) is not PreregisteredStressDataV21:
        raise ValueError("stress data must be nominal")
    if data.schema_version != PREREGISTERED_STRESS_SCHEMA_V21:
        raise ValueError("stress data schema version differs")
    if data.protocol_id != PREREGISTERED_STRESS_PROTOCOL_V21:
        raise ValueError("stress data protocol differs")
    workload_root = _require_domain_root_v21(
        data.workload_root,
        WORKLOAD_ROOT_DOMAIN_V21,
        "workload_root",
    )
    if not hmac.compare_digest(
        workload_root,
        _workload_root_v21(data.protocol_id, data.workload),
    ):
        raise ValueError("workload_root does not bind the body-free workload")
    if data.workload != _workload_v21():
        raise ValueError("stress data workload differs")
    _require_domain_root_v21(
        data.evaluator_run_root,
        _RUN_ROOT_DOMAIN_V21,
        "evaluator run root",
    )
    if data.replay_match is not True:
        raise ValueError("stress replay integrity gate did not match")
    if (
        data.terminal_status != MultiroundStatusV21.COMPLETED.value
        or data.terminal_exit_reason != MultiroundExitReasonV21.COMPLETED.value
        or data.terminal_failed_round_index is not None
        or data.terminal_reason is not None
    ):
        raise ValueError("stress terminal result is not a complete V21-007 run")
    if type(data.records) is not tuple or len(data.records) != STRESS_ROUND_COUNT_V21:
        raise ValueError("stress data requires exactly twelve compact records")
    previous: PreregisteredStressRecordV21 | None = None
    for round_number, (record, batch_size, body_bytes) in enumerate(
        zip(data.records, _BATCH_SIZES_V21, _BODY_BYTES_PER_RECORD_V21, strict=True),
        start=1,
    ):
        _validate_record_v21(
            record,
            round_number=round_number,
            batch_size=batch_size,
            body_bytes=body_bytes,
            previous=previous,
        )
        previous = record
    _require_domain_root_v21(data.data_root, STRESS_DATA_ROOT_DOMAIN_V21, "data_root")
    if not hmac.compare_digest(data.data_root, _data_root_v21(data)):
        raise ValueError("data_root does not bind the compact stress data")


def run_preregistered_stress_v21() -> PreregisteredStressDataV21:
    """Run the frozen arm once and use a second run only as a replay gate."""
    setup = build_preregistered_stress_setup_v21()
    result = _evaluate_v21(setup)
    replay = _evaluate_v21(setup)
    if not hmac.compare_digest(canonical_json_v21(result), canonical_json_v21(replay)):
        raise ValueError("V21-007 replay canonical bytes differ")
    if (
        result.status is not MultiroundStatusV21.COMPLETED
        or result.exit_reason is not MultiroundExitReasonV21.COMPLETED
        or result.failed_round_index is not None
        or result.reason is not None
        or len(result.traces) != STRESS_ROUND_COUNT_V21
    ):
        raise ValueError("V21-007 did not accept the full preregistered arm")
    cumulative_loss = 0
    records: list[PreregisteredStressRecordV21] = []
    for round_number, (trace, batch_size, body_bytes) in enumerate(
        zip(result.traces, _BATCH_SIZES_V21, _BODY_BYTES_PER_RECORD_V21, strict=True),
        start=1,
    ):
        cumulative_loss += trace.loss_delta
        records.append(
            _record_from_trace_v21(
                trace=trace,
                round_number=round_number,
                batch_size=batch_size,
                body_bytes=body_bytes,
                cumulative_loss=cumulative_loss,
            )
        )
    workload = _workload_v21()
    provisional = PreregisteredStressDataV21(
        schema_version=PREREGISTERED_STRESS_SCHEMA_V21,
        protocol_id=PREREGISTERED_STRESS_PROTOCOL_V21,
        workload=workload,
        workload_root="",
        evaluator_run_root=result.run_root,
        replay_match=True,
        terminal_status=result.status.value,
        terminal_exit_reason=result.exit_reason.value,
        terminal_failed_round_index=result.failed_round_index,
        terminal_reason=result.reason,
        records=tuple(records),
        data_root="",
    )
    with_workload_root = replace(
        provisional,
        workload_root=_workload_root_v21(provisional.protocol_id, workload),
    )
    data = replace(
        with_workload_root,
        data_root=_data_root_v21(with_workload_root),
    )
    validate_preregistered_stress_data_v21(data)
    return data


def canonical_preregistered_stress_json_v21(data: PreregisteredStressDataV21) -> str:
    """Return the one body-free canonical JSON serialization for V21-008."""
    validate_preregistered_stress_data_v21(data)
    return canonical_json_v21(_data_payload_v21(data, include_data_root=True))


_WORKLOAD_KEYS_V21: Final = frozenset(_workload_payload_v21(_workload_v21()))
_RECORD_KEYS_V21: Final = frozenset(
    _record_payload_v21(
        PreregisteredStressRecordV21(
            round_index=0,
            batch_size=0,
            body_bytes_per_record=0,
            incoming_count=0,
            incoming_body_bytes=0,
            before_control_bytes=0,
            before_source_body_bytes=0,
            before_resident_bytes=0,
            control_bytes=0,
            source_body_bytes=0,
            resident_bytes=0,
            control_delta_bytes=0,
            source_body_delta_bytes=0,
            resident_growth_bytes=0,
            avoided_incremental_bytes=0,
            retention_ppm=0,
            reduction_ppm=0,
            release_ppm=0,
            loss_delta=0,
            cumulative_loss=0,
            loss_per_avoided_ppm=0,
            before_state_hash="x",
            after_state_hash="x",
            source_envelope_root="x",
            policy_root="x",
            before_advance_head_root="x",
            after_advance_head_root="x",
            matrix_root="x",
            transition_root="x",
            before_generation=0,
            after_generation=0,
            before_high_water=0,
            after_high_water=0,
            release_count=0,
        )
    )
)


def _require_mapping_v21(
    value: object, *, keys: frozenset[str], label: str
) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    mapping = value
    if any(type(key) is not str for key in mapping) or set(mapping) != keys:
        raise ValueError(f"{label} has an unexpected schema")
    return mapping


def _require_tuple_ints_v21(value: object, label: str) -> tuple[int, ...]:
    if type(value) is not list:
        raise ValueError(f"{label} must be a JSON array")
    return tuple(_require_int_v21(item, label) for item in value)


def _parse_workload_v21(value: object) -> PreregisteredStressWorkloadV21:
    payload = _require_mapping_v21(
        value,
        keys=_WORKLOAD_KEYS_V21,
        label="stress workload",
    )
    return PreregisteredStressWorkloadV21(
        round_count=_require_int_v21(payload["round_count"], "workload round_count"),
        batch_sizes=_require_tuple_ints_v21(payload["batch_sizes"], "workload batches"),
        body_bytes_per_record=_require_tuple_ints_v21(
            payload["body_bytes_per_record"], "workload body bytes"
        ),
        core_source_count=_require_int_v21(
            payload["core_source_count"], "workload core sources"
        ),
        max_source_rows=_require_int_v21(
            payload["max_source_rows"], "workload max source rows"
        ),
        hard_edge_count=_require_int_v21(
            payload["hard_edge_count"], "workload hard edge count"
        ),
        solver_mode=_require_text_v21(payload["solver_mode"], "workload solver mode"),
        max_new_segments=_require_int_v21(
            payload["max_new_segments"], "workload max new segments"
        ),
        max_plan_evaluations=_require_int_v21(
            payload["max_plan_evaluations"], "workload max plan evaluations"
        ),
        segment_loss_units=_require_int_v21(
            payload["segment_loss_units"], "workload segment loss"
        ),
    )


def _parse_record_v21(value: object) -> PreregisteredStressRecordV21:
    payload = _require_mapping_v21(value, keys=_RECORD_KEYS_V21, label="stress record")
    return PreregisteredStressRecordV21(
        round_index=_require_int_v21(payload["round_index"], "record round_index"),
        batch_size=_require_int_v21(payload["batch_size"], "record batch_size"),
        body_bytes_per_record=_require_int_v21(
            payload["body_bytes_per_record"], "record body bytes"
        ),
        incoming_count=_require_int_v21(
            payload["incoming_count"], "record incoming count"
        ),
        incoming_body_bytes=_require_int_v21(
            payload["incoming_body_bytes"], "record incoming body bytes"
        ),
        before_control_bytes=_require_int_v21(
            payload["before_control_bytes"], "record before control"
        ),
        before_source_body_bytes=_require_int_v21(
            payload["before_source_body_bytes"], "record before source body"
        ),
        before_resident_bytes=_require_int_v21(
            payload["before_resident_bytes"], "record before resident"
        ),
        control_bytes=_require_int_v21(payload["control_bytes"], "record control"),
        source_body_bytes=_require_int_v21(
            payload["source_body_bytes"], "record source body"
        ),
        resident_bytes=_require_int_v21(payload["resident_bytes"], "record resident"),
        control_delta_bytes=_require_int_v21(
            payload["control_delta_bytes"], "record control delta"
        ),
        source_body_delta_bytes=_require_int_v21(
            payload["source_body_delta_bytes"], "record source body delta"
        ),
        resident_growth_bytes=_require_int_v21(
            payload["resident_growth_bytes"], "record resident growth"
        ),
        avoided_incremental_bytes=_require_int_v21(
            payload["avoided_incremental_bytes"], "record avoided bytes"
        ),
        retention_ppm=_require_int_v21(
            payload["retention_ppm"], "record retention ppm"
        ),
        reduction_ppm=_require_int_v21(
            payload["reduction_ppm"], "record reduction ppm"
        ),
        release_ppm=_require_int_v21(payload["release_ppm"], "record release ppm"),
        loss_delta=_require_int_v21(payload["loss_delta"], "record loss delta"),
        cumulative_loss=_require_int_v21(
            payload["cumulative_loss"], "record cumulative loss"
        ),
        loss_per_avoided_ppm=_require_int_v21(
            payload["loss_per_avoided_ppm"], "record loss per avoided ppm"
        ),
        before_state_hash=_require_text_v21(
            payload["before_state_hash"], "record before state"
        ),
        after_state_hash=_require_text_v21(
            payload["after_state_hash"], "record after state"
        ),
        source_envelope_root=_require_text_v21(
            payload["source_envelope_root"], "record envelope"
        ),
        policy_root=_require_text_v21(payload["policy_root"], "record policy"),
        before_advance_head_root=_require_text_v21(
            payload["before_advance_head_root"], "record before head"
        ),
        after_advance_head_root=_require_text_v21(
            payload["after_advance_head_root"], "record after head"
        ),
        matrix_root=_require_text_v21(payload["matrix_root"], "record matrix"),
        transition_root=_require_text_v21(
            payload["transition_root"], "record transition"
        ),
        before_generation=_require_int_v21(
            payload["before_generation"], "record before generation"
        ),
        after_generation=_require_int_v21(
            payload["after_generation"], "record after generation"
        ),
        before_high_water=_require_int_v21(
            payload["before_high_water"], "record before high_water"
        ),
        after_high_water=_require_int_v21(
            payload["after_high_water"], "record after high_water"
        ),
        release_count=_require_int_v21(
            payload["release_count"], "record release count"
        ),
    )


def _json_canonical_view_v21(value: object) -> object:
    """Adapt decoded JSON arrays for the tuple-only shared canonicalizer."""
    if type(value) is list:
        return tuple(_json_canonical_view_v21(item) for item in value)
    if type(value) is dict:
        return {key: _json_canonical_view_v21(item) for key, item in value.items()}
    return value


def parse_preregistered_stress_json_v21(value: str) -> PreregisteredStressDataV21:
    """Parse and validate the exact canonical JSON artifact shape."""
    if type(value) is not str:
        raise ValueError("stress artifact must be text")
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("stress artifact is not JSON") from error
    if canonical_json_v21(_json_canonical_view_v21(raw)) != value:
        raise ValueError("stress artifact JSON is not canonical")
    payload = _require_mapping_v21(
        raw,
        keys=frozenset(
            {
                "schema_version",
                "protocol_id",
                "workload",
                "workload_root",
                "evaluator_run_root",
                "replay_match",
                "terminal",
                "records",
                "data_root",
            }
        ),
        label="stress artifact",
    )
    terminal = _require_mapping_v21(
        payload["terminal"],
        keys=frozenset({"status", "exit_reason", "failed_round_index", "reason"}),
        label="stress terminal",
    )
    raw_records = payload["records"]
    if type(raw_records) is not list:
        raise ValueError("stress artifact records must be a JSON array")
    failed_round = terminal["failed_round_index"]
    if failed_round is not None:
        failed_round = _require_int_v21(failed_round, "terminal failed round")
    reason = terminal["reason"]
    if reason is not None:
        reason = _require_text_v21(reason, "terminal reason")
    replay_match = payload["replay_match"]
    if type(replay_match) is not bool:
        raise ValueError("stress replay_match must be boolean")
    data = PreregisteredStressDataV21(
        schema_version=_require_int_v21(payload["schema_version"], "artifact schema"),
        protocol_id=_require_text_v21(payload["protocol_id"], "artifact protocol"),
        workload=_parse_workload_v21(payload["workload"]),
        workload_root=_require_text_v21(
            payload["workload_root"], "artifact workload_root"
        ),
        evaluator_run_root=_require_text_v21(
            payload["evaluator_run_root"], "artifact evaluator run root"
        ),
        replay_match=replay_match,
        terminal_status=_require_text_v21(terminal["status"], "terminal status"),
        terminal_exit_reason=_require_text_v21(
            terminal["exit_reason"], "terminal exit reason"
        ),
        terminal_failed_round_index=failed_round,
        terminal_reason=reason,
        records=tuple(_parse_record_v21(record) for record in raw_records),
        data_root=_require_text_v21(payload["data_root"], "artifact data_root"),
    )
    validate_preregistered_stress_data_v21(data)
    return data


def main() -> None:
    """Emit only the canonical body-free data JSON for explicit callers."""
    print(canonical_preregistered_stress_json_v21(run_preregistered_stress_v21()))


if __name__ == "__main__":
    main()
