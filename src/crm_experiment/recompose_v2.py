"""Atomic source selection, canonical encoding, and acceptance for CRM v2."""

from __future__ import annotations

import math
from dataclasses import replace

from crm_experiment.canonical import canonical_json, utf8_bytes
from crm_experiment.codec_v2 import (
    CodecDecodeErrorV2,
    decode_frozen_state_v2,
    direct_view_v2,
)
from crm_experiment.contracts import AtomStatus
from crm_experiment.contracts_v2 import (
    ActiveRecordV2,
    CandidatePlanV2,
    CapsuleStateV2,
    DirectBlockV2,
    EncodedPlanV2,
    GateReportV2,
    LogicalResolutionV2,
    OptimizerSelectionV2,
    OptimizerWorkV2,
    PackedContextBlockV2,
    PackedEntryV2,
    PlanEvaluationV2,
    RecompositionMetricsV2,
    RecompositionOutcomeV2,
    RecompositionRequestV2,
    RecompositionResultV2,
    SourceMatrixV2,
    canonical_affixes_v2,
    recomposition_policy_hash_v2,
)
from crm_experiment.kernel_v2 import select_kernel_v2
from crm_experiment.logical_v2 import resolve_latest_v2
from crm_experiment.matrix_v2 import build_source_matrix_v2
from crm_experiment.optimizer_v2 import (
    OptimizerAdmissionErrorV2,
    OptimizerWorkLimitV2,
    optimize_sources_v2,
)
from crm_experiment.resident_v2 import (
    logical_semantic_hash_v2,
    resident_bytes_v2,
    resident_hash_v2,
)


class InitialAdmissionErrorV2(RuntimeError):
    """The first frozen state could not pass mandatory admission and gate checks."""


def _invalid_report(
    reasons: tuple[str, ...],
    accepted_budget: int,
    *,
    expected_logical_hash: str = "unknown",
    candidate_resident_bytes: int | None = None,
    direct_equivalent_bytes: int | None = None,
    actual_logical_hash: str | None = None,
) -> GateReportV2:
    canonical_reasons = tuple(sorted(set(reasons)))
    return GateReportV2(
        valid=False,
        reasons=canonical_reasons,
        candidate_resident_bytes=candidate_resident_bytes,
        direct_equivalent_bytes=direct_equivalent_bytes,
        accepted_budget=accepted_budget,
        budget_margin=(
            accepted_budget - candidate_resident_bytes
            if candidate_resident_bytes is not None
            else None
        ),
        expected_logical_hash=expected_logical_hash,
        actual_logical_hash=actual_logical_hash,
    )


def _rollback_or_raise(
    base_state: CapsuleStateV2 | None,
    report: GateReportV2,
) -> RecompositionResultV2:
    if base_state is None:
        raise InitialAdmissionErrorV2(",".join(report.reasons))
    return RecompositionResultV2(
        state=base_state,
        outcome=RecompositionOutcomeV2.ROLLED_BACK,
        committed=False,
        consumed_delta=False,
        gate=report,
        selection=None,
        metrics=None,
        encoded_plan=None,
    )


def _direct_state_v2(
    resolution: LogicalResolutionV2,
    matrix: SourceMatrixV2,
    plan: CandidatePlanV2,
    kernel_records: tuple[ActiveRecordV2, ...],
    request: RecompositionRequestV2,
    *,
    generation: int,
) -> CapsuleStateV2:
    row_by_source = {row.source_id: row for row in matrix.rows}
    selected = set(plan.retained_source_ids)
    if not selected.issubset(matrix.selectable_source_ids):
        raise ValueError("plan attempts to revive an unavailable payload")
    kernel_ids = {record.atom.source_id for record in kernel_records}
    if kernel_ids != set(matrix.mandatory_source_ids):
        raise ValueError("selected kernel does not match mandatory source rows")
    if not kernel_ids.issubset(selected):
        raise ValueError("logical plan omits a mandatory kernel source")
    body_records = tuple(
        row_by_source[source_id].record for source_id in sorted(selected - kernel_ids)
    )
    if any(record is None for record in body_records):
        raise ValueError("plan attempts to materialize a released payload")
    typed_body_records = tuple(
        record for record in body_records if isinstance(record, ActiveRecordV2)
    )
    if len(typed_body_records) != len(body_records):
        raise ValueError("plan body contains an invalid source record")
    return CapsuleStateV2(
        schema_version=2,
        generation=generation,
        high_water=resolution.high_water,
        accepted_budget=request.requested_budget,
        key_registry_limit=request.key_registry_limit,
        max_semantic_key_bytes=request.max_semantic_key_bytes,
        frontier=resolution.frontier,
        receipts=resolution.receipts,
        weight_policy=request.next_weight_policy,
        kernel=tuple(
            sorted(
                kernel_records,
                key=lambda record: (record.atom.role.value, record.atom.source_id),
            )
        ),
        body=tuple(DirectBlockV2(record) for record in typed_body_records),
        packing_policy=request.next_packing_policy,
        recomposition_policy_hash=recomposition_policy_hash_v2(
            request.kernel_schema,
            request.candidate_policy,
        ),
    )


def _block_source_ids(block: DirectBlockV2 | PackedContextBlockV2) -> tuple[str, ...]:
    if isinstance(block, DirectBlockV2):
        return (block.record.atom.source_id,)
    return block.source_ids


def _packed_pair_v2(
    left_block: DirectBlockV2,
    right_block: DirectBlockV2,
    max_decoded_block_bytes: int,
) -> PackedContextBlockV2 | None:
    records = (left_block.record, right_block.record)
    texts = tuple(record.atom.text for record in records)
    if sum(utf8_bytes(text) for text in texts) > max_decoded_block_bytes:
        return None
    common_prefix, common_suffix = canonical_affixes_v2(texts)
    if not common_prefix and not common_suffix:
        return None
    suffix_length = len(common_suffix)
    packed = PackedContextBlockV2(
        common_prefix=common_prefix,
        common_suffix=common_suffix,
        entries=tuple(
            PackedEntryV2(
                source_id=record.atom.source_id,
                active_keys=record.active_keys,
                text_middle=record.atom.text[
                    len(common_prefix) : (
                        len(record.atom.text) - suffix_length if suffix_length else None
                    )
                ],
                provenance=record.atom.provenance,
            )
            for record in records
        ),
    )
    direct_item_bytes = (
        utf8_bytes(canonical_json(left_block))
        + utf8_bytes(canonical_json(right_block))
        + 1
    )
    if utf8_bytes(canonical_json(packed)) >= direct_item_bytes:
        return None
    return packed


def encode_plan_v2(
    resolution: LogicalResolutionV2,
    matrix: SourceMatrixV2,
    plan: CandidatePlanV2,
    kernel_records: tuple[ActiveRecordV2, ...],
    request: RecompositionRequestV2,
    *,
    generation: int,
) -> EncodedPlanV2:
    """Use one canonical encoder for optimizer costs and final frozen output."""
    direct = _direct_state_v2(
        resolution,
        matrix,
        plan,
        kernel_records,
        request,
        generation=generation,
    )
    blocks = list(direct.body)
    selected = set(plan.retained_source_ids)
    codec_attempts = 0
    emitted = 0
    fallbacks = 0
    for proposal in matrix.pack_proposals:
        if not {
            proposal.left_source_id,
            proposal.right_source_id,
        }.issubset(selected):
            continue
        codec_attempts += 1
        block_index_by_direct_id = {
            block.record.atom.source_id: index
            for index, block in enumerate(blocks)
            if isinstance(block, DirectBlockV2)
        }
        left_index = block_index_by_direct_id.get(proposal.left_source_id)
        right_index = block_index_by_direct_id.get(proposal.right_source_id)
        if left_index is None or right_index is None or right_index != left_index + 1:
            fallbacks += 1
            continue
        left_block = blocks[left_index]
        right_block = blocks[right_index]
        if not isinstance(left_block, DirectBlockV2) or not isinstance(
            right_block, DirectBlockV2
        ):
            fallbacks += 1
            continue
        packed = _packed_pair_v2(
            left_block,
            right_block,
            direct.packing_policy.max_decoded_block_bytes,
        )
        if packed is None:
            fallbacks += 1
            continue
        blocks[left_index : right_index + 1] = [packed]
        emitted += 1

    direct_bytes = resident_bytes_v2(direct)
    current = replace(direct, body=tuple(blocks)) if emitted else direct
    full_state_evaluations = 2 if emitted else 1
    if decode_frozen_state_v2(current) != decode_frozen_state_v2(direct):
        raise ValueError("incremental pack layout failed decoder roundtrip")
    if logical_semantic_hash_v2(current) != logical_semantic_hash_v2(direct):
        raise ValueError("incremental pack layout changed logical hash")
    resident_bytes = resident_bytes_v2(current)
    if emitted and resident_bytes >= direct_bytes:
        raise ValueError("incremental pack layout lacks strict aggregate savings")
    evaluation = PlanEvaluationV2(
        plan=plan,
        resident_bytes=resident_bytes,
        direct_equivalent_bytes=direct_bytes,
        packing_savings_bytes=direct_bytes - resident_bytes,
        emitted_pack_count=emitted,
        valid=True,
        reasons=(),
    )
    return EncodedPlanV2(
        state=current,
        direct_state=direct,
        evaluation=evaluation,
        codec_attempts=codec_attempts,
        direct_fallback_count=fallbacks,
        full_state_evaluations=full_state_evaluations,
    )


def gate_candidate_v2(
    encoded: EncodedPlanV2,
    released_source_ids: tuple[str, ...],
    accepted_budget: int,
) -> GateReportV2:
    """Validate every semantic, physical, cost, and budget boundary before commit."""
    reasons: list[str] = []
    expected_hash = "unknown"
    actual_hash: str | None = None
    actual_bytes: int | None = None
    direct_bytes: int | None = None
    try:
        encoded.__post_init__()
        encoded.direct_state.__post_init__()
        expected_hash = logical_semantic_hash_v2(encoded.direct_state)
        encoded.state.__post_init__()
        actual_hash = logical_semantic_hash_v2(encoded.state)
        actual_bytes = resident_bytes_v2(encoded.state)
        direct_bytes = resident_bytes_v2(encoded.direct_state)
        resident_hash_v2(encoded.state)

        if encoded.state.accepted_budget != accepted_budget:
            reasons.append("accepted_budget_mismatch")
        if encoded.evaluation.resident_bytes != actual_bytes:
            reasons.append("predicted_actual_mismatch")
        if encoded.evaluation.direct_equivalent_bytes != direct_bytes:
            reasons.append("direct_equivalent_mismatch")
        if actual_bytes > accepted_budget:
            reasons.append("budget_exceeded")
        if expected_hash != actual_hash:
            reasons.append("logical_hash_mismatch")

        frame_fields = (
            "schema_version",
            "generation",
            "high_water",
            "accepted_budget",
            "key_registry_limit",
            "max_semantic_key_bytes",
            "frontier",
            "receipts",
            "weight_policy",
            "kernel",
            "packing_policy",
            "recomposition_policy_hash",
        )
        if any(
            getattr(encoded.state, field_name)
            != getattr(encoded.direct_state, field_name)
            for field_name in frame_fields
        ):
            reasons.append("direct_frame_mismatch")
        if any(
            not isinstance(block, DirectBlockV2) for block in encoded.direct_state.body
        ):
            reasons.append("direct_equivalent_contains_pack")

        expected_records = decode_frozen_state_v2(encoded.direct_state)
        actual_records = decode_frozen_state_v2(encoded.state)
        if expected_records != actual_records:
            reasons.append("decoder_roundtrip_mismatch")
        expected_ids = tuple(record.atom.source_id for record in expected_records)
        if tuple(sorted(expected_ids)) != encoded.evaluation.plan.retained_source_ids:
            reasons.append("plan_payload_mismatch")

        active_source_ids = {
            winner.source_id
            for winner in encoded.state.frontier
            if winner.status is AtomStatus.ACTIVE
        }
        retained_ids = set(encoded.evaluation.plan.retained_source_ids)
        released_ids = set(released_source_ids)
        if (
            retained_ids & released_ids
            or retained_ids | released_ids != active_source_ids
        ):
            reasons.append("source_partition_mismatch")
        if released_source_ids != tuple(sorted(set(released_source_ids))):
            reasons.append("released_sources_noncanonical")

        expected_kernel_ids = {
            winner.source_id
            for winner in encoded.state.frontier
            if winner.status is AtomStatus.ACTIVE and winner.core_required
        }
        actual_kernel_ids = {record.atom.source_id for record in encoded.state.kernel}
        if actual_kernel_ids != expected_kernel_ids:
            reasons.append("kernel_coverage_mismatch")
        for record in encoded.state.kernel:
            if not set(record.atom.depends_on).issubset(actual_kernel_ids):
                reasons.append("kernel_dependency_mismatch")

        receipt_by_source = {
            receipt.source_id: receipt for receipt in encoded.state.receipts
        }
        for source_id in active_source_ids:
            if not set(receipt_by_source[source_id].depends_on).issubset(
                active_source_ids
            ):
                reasons.append("active_dependency_mismatch")

        packed_indexes = tuple(
            index
            for index, block in enumerate(encoded.state.body)
            if isinstance(block, PackedContextBlockV2)
        )
        if len(packed_indexes) != encoded.evaluation.emitted_pack_count:
            reasons.append("emitted_pack_count_mismatch")
        receipt_by_source = {
            receipt.source_id: receipt for receipt in encoded.state.receipts
        }
        for block_index in packed_indexes:
            block = encoded.state.body[block_index]
            if not isinstance(block, PackedContextBlockV2):
                reasons.append("packed_block_type_mismatch")
                continue
            decoded = tuple(
                entry.decode(
                    receipt_by_source[entry.source_id],
                    block.common_prefix,
                    block.common_suffix,
                )
                for entry in block.entries
            )
            expanded_item_bytes = (
                sum(
                    utf8_bytes(canonical_json(DirectBlockV2(record)))
                    for record in decoded
                )
                + len(decoded)
                - 1
            )
            if expanded_item_bytes <= utf8_bytes(canonical_json(block)):
                reasons.append("pack_without_strict_savings")
        if packed_indexes:
            if direct_bytes <= actual_bytes:
                reasons.append("aggregate_pack_without_strict_savings")
        elif direct_bytes != actual_bytes:
            reasons.append("unpacked_direct_mismatch")
    except (AttributeError, KeyError, TypeError, ValueError, CodecDecodeErrorV2):
        reasons.append("candidate_validation_error")

    if reasons:
        return _invalid_report(
            tuple(reasons),
            accepted_budget,
            expected_logical_hash=expected_hash,
            candidate_resident_bytes=actual_bytes,
            direct_equivalent_bytes=direct_bytes,
            actual_logical_hash=actual_hash,
        )
    return GateReportV2(
        valid=True,
        reasons=(),
        candidate_resident_bytes=actual_bytes,
        direct_equivalent_bytes=direct_bytes,
        accepted_budget=accepted_budget,
        budget_margin=(
            accepted_budget - actual_bytes if actual_bytes is not None else None
        ),
        expected_logical_hash=expected_hash,
        actual_logical_hash=actual_hash,
    )


def _metrics_v2(
    matrix: SourceMatrixV2,
    selection: OptimizerSelectionV2,
    encoded: EncodedPlanV2,
) -> RecompositionMetricsV2:
    selected = set(selection.plan.retained_source_ids)
    released = set(selection.released_source_ids)
    unavailable = set(matrix.unavailable_source_ids)
    known_released_payload_bytes = sum(
        utf8_bytes(canonical_json(row.record.atom))
        for row in matrix.rows
        if row.record is not None and row.source_id not in selected
    )
    active_weight = selection.retained_weight + selection.omitted_weight
    return RecompositionMetricsV2(
        persistent_bytes=encoded.evaluation.resident_bytes,
        direct_equivalent_bytes=encoded.evaluation.direct_equivalent_bytes,
        packing_savings_bytes=encoded.evaluation.packing_savings_bytes,
        packing_savings_rate=(
            encoded.evaluation.packing_savings_bytes
            / encoded.evaluation.direct_equivalent_bytes
        ),
        active_source_count=len(matrix.active_source_ids),
        available_source_count=len(matrix.selectable_source_ids),
        retained_source_count=len(selection.plan.retained_source_ids),
        released_source_count=len(selection.released_source_ids),
        released_control_source_count=len(released & unavailable),
        known_released_payload_bytes=known_released_payload_bytes,
        active_weight=active_weight,
        retained_weight=selection.retained_weight,
        omitted_weight=selection.omitted_weight,
        weighted_omission_rate=(
            selection.omitted_weight / active_weight if active_weight else 0.0
        ),
        pack_proposal_count=matrix.proposal_count,
        codec_attempts=encoded.codec_attempts,
        emitted_pack_count=encoded.evaluation.emitted_pack_count,
        direct_fallback_count=encoded.direct_fallback_count,
        encoder_full_state_evaluations=encoded.full_state_evaluations,
        optimizer_work=selection.work,
    )


def _noop_result_v2(state: CapsuleStateV2) -> RecompositionResultV2:
    direct = direct_view_v2(state)
    actual_bytes = resident_bytes_v2(state)
    direct_bytes = resident_bytes_v2(direct)
    actual_hash = logical_semantic_hash_v2(state)
    retained_ids = {record.atom.source_id for record in decode_frozen_state_v2(state)}
    active_ids = {
        winner.source_id
        for winner in state.frontier
        if winner.status is AtomStatus.ACTIVE
    }
    weight_by_source = {
        source_weight.source_id: source_weight.weight
        for source_weight in state.weight_policy.source_weights
    }
    retained_weight = math.fsum(
        weight_by_source[source_id] for source_id in sorted(retained_ids)
    )
    omitted_weight = math.fsum(
        weight_by_source[source_id] for source_id in sorted(active_ids - retained_ids)
    )
    active_weight = retained_weight + omitted_weight
    packed_count = sum(isinstance(block, PackedContextBlockV2) for block in state.body)
    gate = GateReportV2(
        valid=True,
        reasons=(),
        candidate_resident_bytes=actual_bytes,
        direct_equivalent_bytes=direct_bytes,
        accepted_budget=state.accepted_budget,
        budget_margin=state.accepted_budget - actual_bytes,
        expected_logical_hash=actual_hash,
        actual_logical_hash=actual_hash,
    )
    metrics = RecompositionMetricsV2(
        persistent_bytes=actual_bytes,
        direct_equivalent_bytes=direct_bytes,
        packing_savings_bytes=direct_bytes - actual_bytes,
        packing_savings_rate=(direct_bytes - actual_bytes) / direct_bytes,
        active_source_count=len(active_ids),
        available_source_count=len(retained_ids),
        retained_source_count=len(retained_ids),
        released_source_count=len(active_ids - retained_ids),
        released_control_source_count=len(active_ids - retained_ids),
        known_released_payload_bytes=0,
        active_weight=active_weight,
        retained_weight=retained_weight,
        omitted_weight=omitted_weight,
        weighted_omission_rate=(
            omitted_weight / active_weight if active_weight else 0.0
        ),
        pack_proposal_count=0,
        codec_attempts=packed_count,
        emitted_pack_count=packed_count,
        direct_fallback_count=0,
        encoder_full_state_evaluations=0,
        optimizer_work=OptimizerWorkV2(0, 0, 0, 0, False),
    )
    return RecompositionResultV2(
        state=state,
        outcome=RecompositionOutcomeV2.NOOP,
        committed=True,
        consumed_delta=True,
        gate=gate,
        selection=None,
        metrics=metrics,
        encoded_plan=None,
    )


def recompose_capsule_v2(
    base_state: CapsuleStateV2 | None,
    request: RecompositionRequestV2,
) -> RecompositionResultV2:
    """Commit one generation only after the complete candidate gate passes."""
    try:
        request.__post_init__()
    except (AttributeError, TypeError, ValueError) as error:
        fallback_budget = (
            request.requested_budget
            if isinstance(request.requested_budget, int)
            and request.requested_budget > 0
            else (base_state.accepted_budget if base_state is not None else 1)
        )
        report = _invalid_report(
            (f"request_validation_error:{error}",), fallback_budget
        )
        return _rollback_or_raise(base_state, report)
    if base_state is not None:
        if request.key_registry_limit != base_state.key_registry_limit:
            report = _invalid_report(
                ("key_registry_limit_changed",), request.requested_budget
            )
            return _rollback_or_raise(base_state, report)
        if request.max_semantic_key_bytes != base_state.max_semantic_key_bytes:
            report = _invalid_report(
                ("max_semantic_key_bytes_changed",), request.requested_budget
            )
            return _rollback_or_raise(base_state, report)
        if (
            request.envelope.target_high_water == base_state.high_water
            and request.envelope.base_high_water == base_state.high_water
            and request.requested_budget == base_state.accepted_budget
            and request.next_weight_policy == base_state.weight_policy
            and request.next_packing_policy == base_state.packing_policy
        ):
            try:
                if base_state.recomposition_policy_hash == recomposition_policy_hash_v2(
                    request.kernel_schema,
                    request.candidate_policy,
                ):
                    base_state.__post_init__()
                    base_bytes = resident_bytes_v2(base_state)
                    direct_bytes = resident_bytes_v2(direct_view_v2(base_state))
                    base_hash = logical_semantic_hash_v2(base_state)
                    if base_bytes > base_state.accepted_budget:
                        report = _invalid_report(
                            ("budget_exceeded",),
                            request.requested_budget,
                            expected_logical_hash=base_hash,
                            candidate_resident_bytes=base_bytes,
                            direct_equivalent_bytes=direct_bytes,
                            actual_logical_hash=base_hash,
                        )
                        return _rollback_or_raise(base_state, report)
                    return _noop_result_v2(base_state)
            except (
                AttributeError,
                KeyError,
                TypeError,
                ValueError,
                CodecDecodeErrorV2,
            ):
                report = _invalid_report(
                    ("noop_validation_error",), request.requested_budget
                )
                return _rollback_or_raise(base_state, report)

    original_frozen_state = base_state
    try:
        working_direct_state = (
            direct_view_v2(base_state) if base_state is not None else None
        )
        resolution = resolve_latest_v2(
            working_direct_state,
            request.envelope,
            key_registry_limit=(
                request.key_registry_limit if base_state is None else None
            ),
            max_semantic_key_bytes=(
                request.max_semantic_key_bytes if base_state is None else None
            ),
        )
        generation = 1 if base_state is None else base_state.generation + 1
        policy_hash = recomposition_policy_hash_v2(
            request.kernel_schema,
            request.candidate_policy,
        )
        kernel = select_kernel_v2(
            resolution.active_records,
            resolution.frontier,
            resolution.receipts,
            request.next_weight_policy,
            request.kernel_schema,
            generation=generation,
            high_water=resolution.high_water,
            requested_budget=request.requested_budget,
            key_registry_limit=request.key_registry_limit,
            max_semantic_key_bytes=request.max_semantic_key_bytes,
            recomposition_policy_hash=policy_hash,
        )
        if not kernel.valid:
            return _rollback_or_raise(
                original_frozen_state,
                _invalid_report(kernel.reasons, request.requested_budget),
            )
        matrix = build_source_matrix_v2(
            resolution,
            request.next_weight_policy,
            request.next_packing_policy,
            request.candidate_policy,
        )
        encoded_by_plan: dict[CandidatePlanV2, EncodedPlanV2] = {}

        def oracle(plan: CandidatePlanV2) -> PlanEvaluationV2:
            encoded = encode_plan_v2(
                resolution,
                matrix,
                plan,
                kernel.records,
                request,
                generation=generation,
            )
            encoded_by_plan[plan] = encoded
            return encoded.evaluation

        selection = optimize_sources_v2(
            matrix,
            request.candidate_policy,
            request.requested_budget,
            oracle,
        )
        encoded = encoded_by_plan.get(selection.plan)
        if encoded is None:
            encoded = encode_plan_v2(
                resolution,
                matrix,
                selection.plan,
                kernel.records,
                request,
                generation=generation,
            )
        report = gate_candidate_v2(
            encoded,
            selection.released_source_ids,
            request.requested_budget,
        )
        if not report.valid:
            return _rollback_or_raise(original_frozen_state, report)
        metrics = _metrics_v2(matrix, selection, encoded)
        has_body = any(
            not record.atom.core_required
            for record in decode_frozen_state_v2(encoded.state)
        )
        return RecompositionResultV2(
            state=encoded.state,
            outcome=(
                RecompositionOutcomeV2.NORMAL
                if has_body
                else RecompositionOutcomeV2.KERNEL_ONLY
            ),
            committed=True,
            consumed_delta=True,
            gate=report,
            selection=selection,
            metrics=metrics,
            encoded_plan=encoded,
        )
    except OptimizerWorkLimitV2:
        report = _invalid_report(("optimizer_work_limit",), request.requested_budget)
    except OptimizerAdmissionErrorV2:
        report = _invalid_report(
            ("optimizer_admission_failed",), request.requested_budget
        )
    except CodecDecodeErrorV2:
        report = _invalid_report(("codec_decode_error",), request.requested_budget)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        report = _invalid_report(
            (f"recomposition_validation_error:{error}",),
            request.requested_budget,
        )
    return _rollback_or_raise(original_frozen_state, report)
