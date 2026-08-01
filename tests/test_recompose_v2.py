"""Atomic frozen-state recomposition and complete CRM v2 gate tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

import crm_experiment.recompose_v2 as recompose_module
from crm_experiment.codec_v2 import decode_frozen_state_v2, direct_view_v2
from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    CandidatePolicyV2,
    DeltaEnvelopeV2,
    DirectBlockV2,
    EncodingModeV2,
    GateReportV2,
    LogicalAtomV2,
    PackedContextBlockV2,
    PackingPolicyV2,
    RecompositionOutcomeV2,
    RecompositionRequestV2,
    SolverModeV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.kernel_v2 import default_kernel_schema_v2, select_kernel_v2
from crm_experiment.logical_v2 import resolve_latest_v2
from crm_experiment.matrix_v2 import build_source_matrix_v2
from crm_experiment.optimizer_v2 import optimize_reference_v2, optimize_sources_v2
from crm_experiment.recompose_v2 import (
    InitialAdmissionErrorV2,
    encode_plan_v2,
    gate_candidate_v2,
    recompose_capsule_v2,
)
from crm_experiment.resident_v2 import resident_bytes_v2, resident_hash_v2


def _atom(
    label: str,
    text: str,
    *,
    key: str,
    role: AtomRole = AtomRole.CONTEXT,
    core: bool = False,
    revision: int = 1,
    as_of: int = 1,
) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=label,
        semantic_keys=(key,),
        role=role,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=revision,
        as_of=as_of,
        provenance=(f"fixture:{label}",),
        exact=False,
        depends_on=(),
        core_required=core,
    )


def _weight_policy(*atoms: LogicalAtomV2, version: str = "theta-1") -> WeightPolicyV2:
    return WeightPolicyV2.create(
        version=version,
        source_weights=tuple(
            SourceWeightV2(atom.source_id, float(index + 1))
            for index, atom in enumerate(sorted(atoms, key=lambda item: item.source_id))
        ),
    )


def _packing_policy(*immutable_atoms: LogicalAtomV2) -> PackingPolicyV2:
    return PackingPolicyV2(
        codec="dmc1-lcp-lcs-v1",
        immutable_source_ids=tuple(sorted(atom.source_id for atom in immutable_atoms)),
        max_records_per_block=8,
        max_decoded_block_bytes=65_536,
    )


def _request(
    envelope: DeltaEnvelopeV2,
    atoms: tuple[LogicalAtomV2, ...],
    immutable_atoms: tuple[LogicalAtomV2, ...],
    *,
    budget: int,
    version: str = "theta-1",
    candidate_policy: CandidatePolicyV2 | None = None,
) -> RecompositionRequestV2:
    return RecompositionRequestV2(
        envelope=envelope,
        requested_budget=budget,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=_weight_policy(*atoms, version=version),
        next_packing_policy=_packing_policy(*immutable_atoms),
        candidate_policy=candidate_policy
        or CandidatePolicyV2(
            solver_mode=SolverModeV2.EXACT_SMALL,
            exact_small_limit=8,
            reference_row_limit=8,
        ),
        key_registry_limit=128,
        max_semantic_key_bytes=128,
    )


def _initial_packable_state():
    core = _atom(
        "goal",
        "preserve experimental continuity",
        key="core:goal",
        role=AtomRole.ROOT_GOAL,
        core=True,
    )
    prefix = "laboratory observation with stable apparatus :: " * 24
    left = _atom("left", f"{prefix}alpha", key="topic:left")
    right = _atom("right", f"{prefix}beta", key="topic:right")
    atoms = (core, left, right)
    envelope = DeltaEnvelopeV2(
        0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))
    )
    request = _request(
        envelope,
        atoms,
        (left, right),
        budget=12_000,
    )
    result = recompose_capsule_v2(None, request)
    return atoms, result


def test_budget_500_is_rejected_by_v2_continuity_admission() -> None:
    core = _atom(
        "goal",
        "continuity",
        key="core:goal",
        role=AtomRole.ROOT_GOAL,
        core=True,
    )
    envelope = DeltaEnvelopeV2(0, 1, (core,))
    request = _request(envelope, (core,), (), budget=500)

    with pytest.raises(
        InitialAdmissionErrorV2, match="admission_below_continuity_floor"
    ):
        recompose_capsule_v2(None, request)


def test_legal_v2_analogue_of_two_13_byte_sources_retains_both() -> None:
    left = _atom("left-13", "a" * 13, key="topic:left")
    right = _atom("right-13", "b" * 13, key="topic:right")
    atoms = (left, right)
    request = _request(
        DeltaEnvelopeV2(0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))),
        atoms,
        (),
        budget=4_608,
    )

    result = recompose_capsule_v2(None, request)

    assert result.state is not None
    assert result.selection is not None
    assert result.outcome is RecompositionOutcomeV2.NORMAL
    assert result.selection.released_source_ids == ()
    assert set(result.selection.plan.retained_source_ids) == {
        left.source_id,
        right.source_id,
    }
    assert resident_bytes_v2(result.state) <= 4_608


def test_calibrated_full_state_oracle_uses_pack_to_avoid_false_kernel_only() -> None:
    core = _atom(
        "goal",
        "preserve experimental continuity",
        key="core:goal",
        role=AtomRole.ROOT_GOAL,
        core=True,
    )
    prefix = "laboratory observation with stable apparatus :: " * 24
    left = _atom("left", f"{prefix}alpha", key="topic:left")
    right = _atom("right", f"{prefix}beta", key="topic:right")
    atoms = (core, left, right)
    envelope = DeltaEnvelopeV2(
        0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))
    )
    dmc1_policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.EXACT_SMALL,
        encoding_mode=EncodingModeV2.DMC1,
        exact_small_limit=8,
        reference_row_limit=8,
    )
    direct_policy = replace(dmc1_policy, encoding_mode=EncodingModeV2.DIRECT_ONLY)

    packed = recompose_capsule_v2(
        None,
        _request(
            envelope,
            atoms,
            (left, right),
            budget=4_608,
            candidate_policy=dmc1_policy,
        ),
    )
    direct = recompose_capsule_v2(
        None,
        _request(
            envelope,
            atoms,
            (left, right),
            budget=4_608,
            candidate_policy=direct_policy,
        ),
    )

    assert packed.state is not None and direct.state is not None
    assert packed.selection is not None and direct.selection is not None
    assert packed.metrics is not None and direct.metrics is not None
    assert packed.selection.released_source_ids == ()
    assert packed.outcome is RecompositionOutcomeV2.NORMAL
    assert packed.metrics.packing_savings_bytes > 0
    assert packed.metrics.direct_equivalent_bytes > 4_608
    assert packed.metrics.persistent_bytes <= 4_608
    assert direct.selection.released_source_ids
    assert direct.outcome is not RecompositionOutcomeV2.KERNEL_ONLY
    assert direct.metrics.released_source_count == 1
    assert direct.metrics.released_control_source_count == 0
    assert direct.metrics.known_released_payload_bytes > 0
    assert packed.state.frontier == direct.state.frontier
    assert packed.state.receipts == direct.state.receipts
    assert packed.state.weight_policy == direct.state.weight_policy
    assert packed.state.packing_policy == direct.state.packing_policy


def test_exact_and_reference_match_with_the_real_canonical_state_oracle() -> None:
    prefix = "shared laboratory apparatus and calibration :: " * 12
    atoms = tuple(
        _atom(
            f"source-{index}",
            f"{prefix}{index}",
            key=f"topic:{index}",
        )
        for index in range(4)
    )
    envelope = DeltaEnvelopeV2(
        0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))
    )
    exact_policy = CandidatePolicyV2(
        solver_mode=SolverModeV2.EXACT_SMALL,
        exact_small_limit=4,
        reference_row_limit=4,
    )
    request = _request(
        envelope,
        atoms,
        atoms,
        budget=5_200,
        candidate_policy=exact_policy,
    )
    resolution = resolve_latest_v2(
        None,
        envelope,
        key_registry_limit=request.key_registry_limit,
        max_semantic_key_bytes=request.max_semantic_key_bytes,
    )
    kernel = select_kernel_v2(
        resolution.active_records,
        resolution.frontier,
        resolution.receipts,
        request.next_weight_policy,
        request.kernel_schema,
        generation=1,
        high_water=1,
        requested_budget=request.requested_budget,
        key_registry_limit=request.key_registry_limit,
        max_semantic_key_bytes=request.max_semantic_key_bytes,
    )
    assert kernel.valid
    matrix = build_source_matrix_v2(
        resolution,
        request.next_weight_policy,
        request.next_packing_policy,
        request.candidate_policy,
    )

    def oracle(plan):
        return encode_plan_v2(
            resolution,
            matrix,
            plan,
            kernel.records,
            request,
            generation=1,
        ).evaluation

    exact = optimize_sources_v2(matrix, exact_policy, request.requested_budget, oracle)
    reference = optimize_reference_v2(
        matrix,
        replace(exact_policy, solver_mode=SolverModeV2.REFERENCE),
        request.requested_budget,
        oracle,
    )

    assert exact.plan == reference.plan
    assert exact.evaluation == reference.evaluation
    assert exact.retained_weight == reference.retained_weight
    assert exact.omitted_weight == reference.omitted_weight
    assert exact.work.oracle_evaluations == reference.work.oracle_evaluations == 16


def test_shared_encoder_commits_lossless_pack_with_exact_metric_split() -> None:
    atoms, result = _initial_packable_state()

    assert result.committed
    assert result.consumed_delta
    assert result.outcome is RecompositionOutcomeV2.NORMAL
    assert result.state is not None
    assert result.selection is not None
    assert result.metrics is not None
    assert result.gate.valid
    assert result.gate.candidate_resident_bytes == resident_bytes_v2(result.state)
    assert result.metrics.persistent_bytes == resident_bytes_v2(result.state)
    assert result.metrics.direct_equivalent_bytes > result.metrics.persistent_bytes
    assert result.metrics.packing_savings_bytes > 0
    assert result.metrics.emitted_pack_count >= 1
    assert result.metrics.encoder_full_state_evaluations == 2
    assert result.metrics.released_source_count == 0
    assert result.metrics.omitted_weight == 0.0
    assert {
        record.atom.source_id for record in decode_frozen_state_v2(result.state)
    } == {atom.source_id for atom in atoms}


def test_multi_pack_encoder_constructs_only_direct_and_final_full_states() -> None:
    prefix = "repeated laboratory prefix for physical packing :: " * 20
    atoms = tuple(
        _atom(
            f"multi-{index}",
            f"{prefix}{index}",
            key=f"topic:multi:{index}",
        )
        for index in range(6)
    )
    result = recompose_capsule_v2(
        None,
        _request(
            DeltaEnvelopeV2(
                0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))
            ),
            atoms,
            atoms,
            budget=20_000,
        ),
    )

    assert result.state is not None
    assert result.metrics is not None
    assert result.encoded_plan is not None
    assert result.metrics.emitted_pack_count >= 2
    assert result.encoded_plan.full_state_evaluations == 2
    assert result.metrics.encoder_full_state_evaluations == 2
    assert result.encoded_plan.evaluation.resident_bytes == resident_bytes_v2(
        result.state
    )
    assert result.encoded_plan.evaluation.packing_savings_bytes == (
        result.encoded_plan.evaluation.direct_equivalent_bytes
        - result.encoded_plan.evaluation.resident_bytes
    )


def test_gate_predicted_actual_mismatch_is_public_and_fail_closed() -> None:
    _, result = _initial_packable_state()
    assert result.state is not None
    assert result.selection is not None
    assert result.encoded_plan is not None
    evaluation = result.encoded_plan.evaluation
    forged_evaluation = replace(
        evaluation,
        resident_bytes=evaluation.resident_bytes - 1,
        direct_equivalent_bytes=evaluation.direct_equivalent_bytes - 1,
    )
    forged = replace(result.encoded_plan, evaluation=forged_evaluation)

    report = gate_candidate_v2(
        forged,
        result.selection.released_source_ids,
        result.state.accepted_budget,
    )

    assert not report.valid
    assert "predicted_actual_mismatch" in report.reasons
    assert report.candidate_resident_bytes == resident_bytes_v2(result.state)


def test_gate_rejects_corrupted_packed_leaf_without_leaking_an_exception() -> None:
    _, result = _initial_packable_state()
    assert result.state is not None
    assert result.selection is not None
    assert result.encoded_plan is not None
    packed_block = next(
        block for block in result.state.body if isinstance(block, PackedContextBlockV2)
    )
    entry = packed_block.entries[0]
    object.__setattr__(entry, "text_middle", f"{entry.text_middle}corrupt")

    report = gate_candidate_v2(
        result.encoded_plan,
        result.selection.released_source_ids,
        result.state.accepted_budget,
    )

    assert not report.valid
    assert "candidate_validation_error" in report.reasons


def test_gate_failure_rolls_back_original_packed_state_without_consuming_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (core, left, right), initial = _initial_packable_state()
    assert initial.state is not None
    original = initial.state
    original_bytes = resident_bytes_v2(original)
    original_hash = resident_hash_v2(original)
    left_v2 = _atom(
        "left-v2",
        "new left observation",
        key="topic:left",
        revision=2,
        as_of=2,
    )
    next_atoms = (core, left_v2, right)
    request = _request(
        DeltaEnvelopeV2(1, 2, (left_v2,)),
        next_atoms,
        (left_v2, right),
        budget=12_000,
        version="theta-2",
    )

    def reject(*_args, **_kwargs) -> GateReportV2:
        return GateReportV2(
            valid=False,
            reasons=("injected_gate_mismatch",),
            candidate_resident_bytes=None,
            direct_equivalent_bytes=None,
            accepted_budget=12_000,
            budget_margin=None,
            expected_logical_hash="unknown",
            actual_logical_hash=None,
        )

    monkeypatch.setattr(recompose_module, "gate_candidate_v2", reject)
    rolled_back = recompose_capsule_v2(original, request)

    assert not rolled_back.committed
    assert not rolled_back.consumed_delta
    assert rolled_back.outcome is RecompositionOutcomeV2.ROLLED_BACK
    assert rolled_back.state is original
    assert rolled_back.state is not None
    assert resident_bytes_v2(rolled_back.state) == original_bytes
    assert resident_hash_v2(rolled_back.state) == original_hash
    assert rolled_back.state.high_water == 1
    assert rolled_back.gate.reasons == ("injected_gate_mismatch",)


def test_packed_state_recomposes_across_rounds_without_losing_unchanged_sibling() -> (
    None
):
    (core, left, right), initial = _initial_packable_state()
    assert initial.state is not None
    assert any(isinstance(block, PackedContextBlockV2) for block in initial.state.body)
    prefix = "laboratory observation with stable apparatus :: " * 24
    left_v2 = _atom(
        "left-v2",
        f"{prefix}gamma",
        key="topic:left",
        revision=2,
        as_of=2,
    )

    updated = recompose_capsule_v2(
        initial.state,
        _request(
            DeltaEnvelopeV2(1, 2, (left_v2,)),
            (core, left_v2, right),
            (left_v2, right),
            budget=12_000,
            version="theta-2",
        ),
    )

    assert updated.committed
    assert updated.consumed_delta
    assert updated.state is not None
    assert updated.state.generation == 2
    assert updated.state.high_water == 2
    assert any(isinstance(block, PackedContextBlockV2) for block in updated.state.body)
    decoded_source_ids = {
        record.atom.source_id for record in decode_frozen_state_v2(updated.state)
    }
    assert decoded_source_ids == {core.source_id, left_v2.source_id, right.source_id}
    assert left.source_id not in decoded_source_ids


def test_stale_immutable_source_policy_rolls_back_without_consuming_update() -> None:
    (core, left, right), initial = _initial_packable_state()
    assert initial.state is not None
    left_v2 = _atom(
        "left-v2",
        "replacement left observation",
        key="topic:left",
        revision=2,
        as_of=2,
    )
    request = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(1, 2, (left_v2,)),
        requested_budget=12_000,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=_weight_policy(core, left_v2, right, version="theta-2"),
        next_packing_policy=initial.state.packing_policy,
        candidate_policy=CandidatePolicyV2(
            solver_mode=SolverModeV2.EXACT_SMALL,
            exact_small_limit=8,
            reference_row_limit=8,
        ),
        key_registry_limit=initial.state.key_registry_limit,
        max_semantic_key_bytes=initial.state.max_semantic_key_bytes,
    )

    rejected = recompose_capsule_v2(initial.state, request)

    assert not rejected.committed
    assert not rejected.consumed_delta
    assert rejected.state is initial.state
    assert rejected.state is not None
    assert rejected.state.high_water == 1
    assert rejected.outcome is RecompositionOutcomeV2.ROLLED_BACK
    assert rejected.gate.reasons == (
        "recomposition_validation_error:packing policy source is outside active universe",
    )
    assert left.source_id in initial.state.packing_policy.immutable_source_ids


def test_zero_delta_same_policy_is_a_byte_stable_no_work_fixed_point() -> None:
    _, initial = _initial_packable_state()
    assert initial.state is not None
    state = initial.state
    request = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(state.high_water, state.high_water, ()),
        requested_budget=state.accepted_budget,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=state.weight_policy,
        next_packing_policy=state.packing_policy,
        candidate_policy=CandidatePolicyV2(
            solver_mode=SolverModeV2.EXACT_SMALL,
            exact_small_limit=8,
            reference_row_limit=8,
        ),
        key_registry_limit=state.key_registry_limit,
        max_semantic_key_bytes=state.max_semantic_key_bytes,
    )

    replay = recompose_capsule_v2(state, request)

    assert replay.committed
    assert replay.consumed_delta
    assert replay.outcome is RecompositionOutcomeV2.NOOP
    assert replay.state is state
    assert replay.state is not None
    assert resident_bytes_v2(replay.state) == resident_bytes_v2(state)
    assert resident_hash_v2(replay.state) == resident_hash_v2(state)
    assert replay.state.generation == state.generation
    assert replay.selection is None
    assert replay.metrics is not None
    assert replay.metrics.optimizer_work.oracle_evaluations == 0


def test_zero_delta_stricter_kernel_schema_revalidates_and_rolls_back() -> None:
    _, initial = _initial_packable_state()
    assert initial.state is not None
    state = initial.state
    schema = default_kernel_schema_v2()
    stricter_schema = replace(
        schema,
        slots=(replace(schema.slots[0], max_text_bytes=1), *schema.slots[1:]),
    )
    request = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(state.high_water, state.high_water, ()),
        requested_budget=state.accepted_budget,
        kernel_schema=stricter_schema,
        next_weight_policy=state.weight_policy,
        next_packing_policy=state.packing_policy,
        candidate_policy=CandidatePolicyV2(
            solver_mode=SolverModeV2.EXACT_SMALL,
            exact_small_limit=8,
            reference_row_limit=8,
        ),
        key_registry_limit=state.key_registry_limit,
        max_semantic_key_bytes=state.max_semantic_key_bytes,
    )

    rejected = recompose_capsule_v2(state, request)

    assert not rejected.committed
    assert not rejected.consumed_delta
    assert rejected.state is state
    assert any(reason.startswith("text_overflow:") for reason in rejected.gate.reasons)


def test_zero_delta_direct_only_reencodes_instead_of_reusing_packed_state() -> None:
    _, initial = _initial_packable_state()
    assert initial.state is not None
    state = initial.state
    request = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(state.high_water, state.high_water, ()),
        requested_budget=state.accepted_budget,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=state.weight_policy,
        next_packing_policy=state.packing_policy,
        candidate_policy=CandidatePolicyV2(
            solver_mode=SolverModeV2.EXACT_SMALL,
            encoding_mode=EncodingModeV2.DIRECT_ONLY,
            exact_small_limit=8,
            reference_row_limit=8,
        ),
        key_registry_limit=state.key_registry_limit,
        max_semantic_key_bytes=state.max_semantic_key_bytes,
    )

    reencoded = recompose_capsule_v2(state, request)

    assert reencoded.committed
    assert reencoded.consumed_delta
    assert reencoded.outcome is RecompositionOutcomeV2.NORMAL
    assert reencoded.state is not None
    assert reencoded.state is not state
    assert reencoded.state.generation == state.generation + 1
    assert all(isinstance(block, DirectBlockV2) for block in reencoded.state.body)


def test_zero_delta_rejects_mutated_nested_historical_atom_without_consuming() -> None:
    _, initial = _initial_packable_state()
    assert initial.state is not None
    state = initial.state
    historical = _atom(
        "historical",
        "valid historical payload",
        key="historical:unrelated",
        as_of=1,
    )
    request = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(state.high_water, state.high_water, (historical,)),
        requested_budget=state.accepted_budget,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=state.weight_policy,
        next_packing_policy=state.packing_policy,
        candidate_policy=CandidatePolicyV2(),
        key_registry_limit=state.key_registry_limit,
        max_semantic_key_bytes=state.max_semantic_key_bytes,
    )
    object.__setattr__(historical, "text", "forged historical payload")

    rejected = recompose_capsule_v2(state, request)

    assert not rejected.committed
    assert not rejected.consumed_delta
    assert rejected.state is state
    assert rejected.outcome is RecompositionOutcomeV2.ROLLED_BACK
    assert rejected.gate.reasons[0].startswith("request_validation_error:")


def test_zero_delta_rejects_mutated_nested_kernel_slot_without_consuming() -> None:
    _, initial = _initial_packable_state()
    assert initial.state is not None
    state = initial.state
    request = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(state.high_water, state.high_water, ()),
        requested_budget=state.accepted_budget,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=state.weight_policy,
        next_packing_policy=state.packing_policy,
        candidate_policy=CandidatePolicyV2(),
        key_registry_limit=state.key_registry_limit,
        max_semantic_key_bytes=state.max_semantic_key_bytes,
    )
    object.__setattr__(request.kernel_schema.slots[0], "max_items", 0)

    rejected = recompose_capsule_v2(state, request)

    assert not rejected.committed
    assert not rejected.consumed_delta
    assert rejected.state is state
    assert rejected.outcome is RecompositionOutcomeV2.ROLLED_BACK
    assert rejected.gate.reasons[0].startswith("request_validation_error:")


def test_released_payload_is_not_revived_when_a_later_budget_expands() -> None:
    core = _atom(
        "goal",
        "preserve continuity",
        key="core:goal",
        role=AtomRole.ROOT_GOAL,
        core=True,
    )
    body = _atom("large", "context payload " * 300, key="topic:large")
    atoms = (core, body)
    initial = recompose_capsule_v2(
        None,
        _request(
            DeltaEnvelopeV2(
                0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))
            ),
            atoms,
            (body,),
            budget=12_000,
        ),
    )
    assert initial.state is not None
    assert any(
        not record.atom.core_required
        for record in decode_frozen_state_v2(initial.state)
    )

    squeezed = recompose_capsule_v2(
        initial.state,
        _request(
            DeltaEnvelopeV2(1, 1, ()),
            atoms,
            (body,),
            budget=4_608,
        ),
    )
    assert squeezed.state is not None
    assert squeezed.outcome is RecompositionOutcomeV2.KERNEL_ONLY
    assert tuple(
        record.atom.source_id for record in decode_frozen_state_v2(squeezed.state)
    ) == (core.source_id,)

    expanded = recompose_capsule_v2(
        squeezed.state,
        _request(
            DeltaEnvelopeV2(1, 1, ()),
            atoms,
            (body,),
            budget=12_000,
        ),
    )
    assert expanded.state is not None
    assert expanded.outcome is RecompositionOutcomeV2.KERNEL_ONLY
    assert tuple(
        record.atom.source_id for record in decode_frozen_state_v2(expanded.state)
    ) == (core.source_id,)
    assert body.source_id in {receipt.source_id for receipt in expanded.state.receipts}


def test_kernel_admission_failure_on_existing_state_rolls_back_atomically() -> None:
    _, initial = _initial_packable_state()
    assert initial.state is not None
    state = initial.state
    request = RecompositionRequestV2(
        envelope=DeltaEnvelopeV2(state.high_water, state.high_water, ()),
        requested_budget=500,
        kernel_schema=default_kernel_schema_v2(),
        next_weight_policy=state.weight_policy,
        next_packing_policy=state.packing_policy,
        candidate_policy=CandidatePolicyV2(),
        key_registry_limit=state.key_registry_limit,
        max_semantic_key_bytes=state.max_semantic_key_bytes,
    )

    result = recompose_capsule_v2(state, request)

    assert not result.committed
    assert not result.consumed_delta
    assert result.state is state
    assert result.outcome is RecompositionOutcomeV2.ROLLED_BACK
    assert "admission_below_continuity_floor" in result.gate.reasons


def test_real_packing_policy_frame_can_fail_after_kernel_trial_passes() -> None:
    core = _atom(
        "goal",
        "continuity",
        key="core:goal",
        role=AtomRole.ROOT_GOAL,
        core=True,
    )
    bodies = tuple(
        _atom(f"body-{index}", f"payload-{index}", key=f"topic:{index}")
        for index in range(6)
    )
    atoms = (core, *bodies)
    initial = recompose_capsule_v2(
        None,
        _request(
            DeltaEnvelopeV2(
                0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))
            ),
            atoms,
            bodies,
            budget=20_000,
        ),
    )
    assert initial.state is not None
    working = direct_view_v2(initial.state)
    replay = DeltaEnvelopeV2(1, 1, ())
    resolution = resolve_latest_v2(working, replay)
    trial = select_kernel_v2(
        resolution.active_records,
        resolution.frontier,
        resolution.receipts,
        initial.state.weight_policy,
        default_kernel_schema_v2(),
        generation=2,
        high_water=1,
        requested_budget=5_600,
        key_registry_limit=initial.state.key_registry_limit,
        max_semantic_key_bytes=initial.state.max_semantic_key_bytes,
    )
    assert trial.valid

    squeezed = recompose_capsule_v2(
        initial.state,
        RecompositionRequestV2(
            envelope=replay,
            requested_budget=5_600,
            kernel_schema=default_kernel_schema_v2(),
            next_weight_policy=initial.state.weight_policy,
            next_packing_policy=initial.state.packing_policy,
            candidate_policy=CandidatePolicyV2(),
            key_registry_limit=initial.state.key_registry_limit,
            max_semantic_key_bytes=initial.state.max_semantic_key_bytes,
        ),
    )

    assert not squeezed.committed
    assert not squeezed.consumed_delta
    assert squeezed.state is initial.state
    assert squeezed.gate.reasons == ("optimizer_admission_failed",)
