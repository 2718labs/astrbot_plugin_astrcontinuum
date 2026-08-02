"""Schema-3 control-fold contract guards."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from crm_experiment.contracts_v21 import (
    CONTROL_FOLD_PROTOCOL_V21,
    CapsuleRepresentativeV21,
    CapsuleRoleV21,
    CapsuleSegmentV21,
    CapsuleStateV21,
    ControlFoldBoundsV21,
    ControlFrameV21,
    DictionaryEntryV21,
    ExactRecordV21,
    FoldBarrierV21,
    HardDependencyV21,
    LossClassV21,
    LossLedgerV21,
    QueryLabelV21,
    QueryRequirementV21,
    ReencodingCandidateV21,
    ReencodingStatusV21,
    SparseWeightPolicyV21,
    canonical_bytes_v21,
    capacity_policy_hash_v21,
    dictionary_commitment_v21,
    dictionary_identity_v21,
    fold_barrier_root_v21,
    fold_policy_hash_v21,
    folded_commitment_root_v21,
    loss_ledger_root_v21,
    namespace_identity_v21,
    query_label_v21,
    record_identity_v21,
    reencoding_policy_hash_v21,
    representative_commitment_v21,
    representative_identity_v21,
    segment_identity_v21,
    segment_input_commitment_root_v21,
    source_commitment_v21,
    state_hash_v21,
    validate_capsule_state_v21,
    validate_control_frame_v21,
    validate_reencoding_candidate_v21,
)

_PROJECT_NAMESPACE = namespace_identity_v21("project")


def _namespace_id(label_or_identity: str) -> str:
    if label_or_identity.startswith("v21-namespace-id-s3:"):
        return label_or_identity
    return namespace_identity_v21(label_or_identity)


def _bounds() -> ControlFoldBoundsV21:
    return ControlFoldBoundsV21(
        max_exact_kernel_records=8,
        max_exact_kernel_bytes=4_096,
        max_hot_records=6,
        max_hot_bytes=4_096,
        max_hot_frontier_entries=6,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=12,
        max_dictionary_bytes=1_024,
        max_segments=4,
        max_representatives_per_segment=3,
        coverage_vector_size=4,
        bridge_vector_size=3,
        max_barriers=5,
        max_sparse_overrides=6,
        max_direct_parent_ids=2,
        max_parent_id_bytes=160,
        loss_ledger_bytes=256,
    )


def _frame(bounds: ControlFoldBoundsV21) -> ControlFrameV21:
    return ControlFrameV21(
        protocol_id=CONTROL_FOLD_PROTOCOL_V21,
        schema_version=3,
        generation=3,
        high_water=12,
        accepted_budget=4_096,
        reencoding_policy_hash=reencoding_policy_hash_v21("policy-v21-test"),
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )


def test_schema_three_frame_binds_every_growth_limit() -> None:
    bounds = _bounds()
    frame = _frame(bounds)

    assert validate_control_frame_v21(frame, bounds) is None
    assert capacity_policy_hash_v21(bounds).startswith("v21-capacity-s3:")

    with pytest.raises(ValueError, match="max_segments"):
        replace(bounds, max_segments=-1)
    with pytest.raises(ValueError, match="schema"):
        validate_control_frame_v21(replace(frame, schema_version=2), bounds)
    with pytest.raises(ValueError, match="capacity"):
        validate_control_frame_v21(
            replace(frame, capacity_policy_hash="v2-capacity:wrong"), bounds
        )


def _record(
    record_label: str,
    *,
    body: str | None = None,
    core_required: bool = False,
    hard_depends_on: tuple[HardDependencyV21, ...] = (),
    as_of: int = 12,
    namespace: str = "project",
    incarnation: int = 1,
) -> ExactRecordV21:
    namespace_id = _namespace_id(namespace)
    record_id = record_identity_v21(namespace_id, record_label)
    selected_body = body if body is not None else f"exact body for {record_label}"
    return ExactRecordV21(
        record_id=record_id,
        namespace=namespace_id,
        incarnation=incarnation,
        as_of=as_of,
        commitment=source_commitment_v21(
            record_id=record_id,
            namespace=namespace_id,
            incarnation=incarnation,
            as_of=as_of,
            body=selected_body,
            core_required=core_required,
            active=True,
        ),
        body=selected_body,
        core_required=core_required,
        active=True,
        hard_depends_on=hard_depends_on,
    )


def _segment(
    segment_label: str = "segment-1",
    *,
    parent_ids: tuple[str, ...] = (),
    generation_start: int = 1,
    generation_end: int = 1,
    input_commitment_root: str | None = None,
    namespace: str = _PROJECT_NAMESPACE,
    budget_used: int = 64,
) -> CapsuleSegmentV21:
    namespace_id = _namespace_id(namespace)
    segment_id = segment_identity_v21(namespace_id, segment_label)
    fold_policy_hash = fold_policy_hash_v21("fold-policy-v21")
    role_counts = ((CapsuleRoleV21.CONTEXT, 1),)
    representatives = (
        CapsuleRepresentativeV21(
            feature_id=representative_identity_v21(
                segment_id, f"feature-{segment_label}"
            ),
            feature_commitment="",
            weight=1.0,
        ),
    )
    representatives = tuple(
        replace(
            representative,
            feature_commitment=representative_commitment_v21(representative.feature_id),
        )
        for representative in representatives
    )
    coverage_vector = (1, 0, 0, 0)
    bridge_vector = (0, 0, 0)
    folded_root = folded_commitment_root_v21(
        segment_id=segment_id,
        namespace=namespace_id,
        generation_start=generation_start,
        generation_end=generation_end,
        source_count=1,
        role_counts=role_counts,
        representatives=representatives,
        coverage_vector=coverage_vector,
        bridge_vector=bridge_vector,
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=budget_used,
        fold_policy_hash=fold_policy_hash,
    )
    return CapsuleSegmentV21(
        segment_id=segment_id,
        parent_ids=parent_ids,
        input_commitment_root=input_commitment_root
        or segment_input_commitment_root_v21(
            (),
            folded_commitment_root=folded_root,
            generation_start=generation_start,
            generation_end=generation_end,
            fold_policy_hash=fold_policy_hash,
        ),
        folded_commitment_root=folded_root,
        namespace=namespace_id,
        generation_start=generation_start,
        generation_end=generation_end,
        source_count=1,
        role_counts=role_counts,
        representatives=representatives,
        coverage_vector=coverage_vector,
        bridge_vector=bridge_vector,
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=budget_used,
        fold_policy_hash=fold_policy_hash,
    )


def _state(
    *,
    bounds: ControlFoldBoundsV21 | None = None,
    frame: ControlFrameV21 | None = None,
    exact_kernel: tuple[ExactRecordV21, ...] | None = None,
    hot_cache: tuple[ExactRecordV21, ...] = (),
    hot_frontier: tuple[str, ...] = (),
    dictionary: tuple[DictionaryEntryV21, ...] | None = None,
    capsule_segments: tuple[CapsuleSegmentV21, ...] = (),
    fold_barriers: tuple[FoldBarrierV21, ...] = (),
    sparse_weight_policy: SparseWeightPolicyV21 | None = None,
    loss_ledger: LossLedgerV21 | None = None,
) -> CapsuleStateV21:
    selected_bounds = bounds or _bounds()
    return CapsuleStateV21(
        frame=frame or _frame(selected_bounds),
        bounds=selected_bounds,
        exact_kernel=exact_kernel or (_record("core", core_required=True),),
        hot_cache=hot_cache,
        hot_frontier=hot_frontier,
        dictionary=dictionary
        or (
            DictionaryEntryV21(
                namespace=_PROJECT_NAMESPACE,
                key=dictionary_identity_v21(_PROJECT_NAMESPACE, "current-focus"),
                commitment=dictionary_commitment_v21(
                    _PROJECT_NAMESPACE,
                    dictionary_identity_v21(_PROJECT_NAMESPACE, "current-focus"),
                ),
            ),
        ),
        capsule_segments=capsule_segments,
        fold_barriers=fold_barriers,
        sparse_weight_policy=sparse_weight_policy
        or SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=loss_ledger
        or LossLedgerV21(
            role_counts=((CapsuleRoleV21.ROOT_GOAL, 1),),
            cumulative_loss_root=loss_ledger_root_v21(
                ((CapsuleRoleV21.ROOT_GOAL, 1),), 0
            ),
            last_fold_generation=0,
        ),
    )


def _candidate(
    base_state: CapsuleStateV21,
    proposed_state: CapsuleStateV21,
    *,
    incoming_records: tuple[ExactRecordV21, ...] = (),
    base_state_hash: str | None = None,
    requested_budget: int | None = None,
    target_high_water: int | None = None,
) -> ReencodingCandidateV21:
    return ReencodingCandidateV21(
        base_state_hash=base_state_hash or state_hash_v21(base_state),
        proposed_state=proposed_state,
        incoming_records=incoming_records,
        requested_budget=requested_budget or proposed_state.frame.accepted_budget,
        target_high_water=target_high_water
        if target_high_water is not None
        else proposed_state.frame.high_water,
    )


def _cbr_resident_bytes(state: CapsuleStateV21) -> int:
    body_elided = replace(
        state,
        exact_kernel=tuple(replace(record, body="") for record in state.exact_kernel),
        hot_cache=tuple(replace(record, body="") for record in state.hot_cache),
    )
    return canonical_bytes_v21(body_elided) + sum(
        len(record.body.encode("utf-8"))
        for record in state.exact_kernel + state.hot_cache
    )


def test_schema_three_apis_nominally_reject_duck_and_v2_hash_domains() -> None:
    base_state = _state()
    duck_state = SimpleNamespace(frame=base_state.frame, bounds=base_state.bounds)

    with pytest.raises(TypeError, match="nominal"):
        validate_capsule_state_v21(cast(CapsuleStateV21, duck_state))
    with pytest.raises(TypeError, match="nominal"):
        state_hash_v21(cast(CapsuleStateV21, duck_state))

    result = validate_reencoding_candidate_v21(
        base_state,
        _candidate(base_state, base_state, base_state_hash="v2-state:foreign-domain"),
    )

    assert result.status is ReencodingStatusV21.ROLLED_BACK
    assert result.state is base_state
    assert result.consumed_delta is False


def test_cbr_layout_controls_exact_hot_frame_and_candidate_budget_gates() -> None:
    exact = _record("core", core_required=True, body='"')
    exact_bound = canonical_bytes_v21((replace(exact, body=""),)) + 1
    exact_bounds = replace(_bounds(), max_exact_kernel_bytes=exact_bound)
    exact_state = _state(
        bounds=exact_bounds,
        frame=_frame(exact_bounds),
        exact_kernel=(exact,),
    )
    assert validate_capsule_state_v21(exact_state) is None

    hot = _record("hot", body='"')
    hot_bound = canonical_bytes_v21((replace(hot, body=""),)) + 1
    hot_bounds = replace(_bounds(), max_hot_bytes=hot_bound)
    hot_state = _state(bounds=hot_bounds, frame=_frame(hot_bounds), hot_cache=(hot,))
    assert validate_capsule_state_v21(hot_state) is None

    budget_state = _state(exact_kernel=(exact,))
    resident_budget = _cbr_resident_bytes(budget_state)
    budget_state = replace(
        budget_state,
        frame=replace(budget_state.frame, accepted_budget=resident_budget),
    )
    assert _cbr_resident_bytes(budget_state) == resident_budget
    assert validate_capsule_state_v21(budget_state) is None

    candidate = validate_reencoding_candidate_v21(
        _state(exact_kernel=(exact,)),
        _candidate(
            _state(exact_kernel=(exact,)),
            _state(exact_kernel=(exact,)),
            requested_budget=resident_budget,
        ),
    )
    assert candidate.status is ReencodingStatusV21.COMMITTED


def test_candidate_rejects_lower_incarnation_against_residents_and_batch_max() -> None:
    namespace = namespace_identity_v21("incarnation-only")
    base_core = _record(
        "core",
        namespace=namespace,
        incarnation=2,
        core_required=True,
    )
    base_state = _state(exact_kernel=(base_core,))
    older = _record(
        "older",
        namespace=namespace,
        incarnation=1,
        as_of=13,
    )
    next_frame = replace(base_state.frame, generation=4, high_water=13)
    resident_replay = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            replace(base_state, frame=next_frame, hot_cache=(older,)),
            incoming_records=(older,),
            target_high_water=13,
        ),
    )
    assert resident_replay.status is ReencodingStatusV21.STALE_REJECTED
    assert resident_replay.state is base_state

    batch_namespace = namespace_identity_v21("incarnation-batch")
    higher = _record("higher", namespace=batch_namespace, incarnation=2, as_of=13)
    lower = _record("lower", namespace=batch_namespace, incarnation=1, as_of=14)
    batch_base = _state()
    batch_frame = replace(batch_base.frame, generation=4, high_water=14)
    batch_replay = validate_reencoding_candidate_v21(
        batch_base,
        _candidate(
            batch_base,
            replace(batch_base, frame=batch_frame, hot_cache=(higher, lower)),
            incoming_records=(higher, lower),
            target_high_water=14,
        ),
    )
    assert batch_replay.status is ReencodingStatusV21.STALE_REJECTED
    assert batch_replay.state is batch_base


def test_state_rejects_lower_active_resident_incarnation_without_a_barrier() -> None:
    namespace = namespace_identity_v21("state-incarnation-dominance")
    exact_core = _record(
        "core",
        namespace=namespace,
        incarnation=2,
        core_required=True,
    )
    lower_hot = _record("hot", namespace=namespace, incarnation=1)

    with pytest.raises(ValueError, match="incarnation"):
        validate_capsule_state_v21(
            _state(exact_kernel=(exact_core,), hot_cache=(lower_hot,))
        )


def test_state_accepts_history_root_domains_but_candidate_freezes_existing_roots() -> (
    None
):
    barrier = FoldBarrierV21(
        namespace=_PROJECT_NAMESPACE,
        incarnation=1,
        folded_through_high_water=8,
        cumulative_root=fold_barrier_root_v21(_PROJECT_NAMESPACE, 1, 8),
    )
    ledger = LossLedgerV21(
        role_counts=((CapsuleRoleV21.CONTEXT, 1),),
        cumulative_loss_root=loss_ledger_root_v21(((CapsuleRoleV21.CONTEXT, 1),), 1),
        last_fold_generation=1,
    )
    base_state = _state(fold_barriers=(barrier,), loss_ledger=ledger)
    alternate_barrier = replace(
        barrier,
        cumulative_root=f"v21-barrier-root-s3:{'a' * 64}",
    )
    alternate_ledger = replace(
        ledger,
        cumulative_loss_root=f"v21-ledger-root-s3:{'b' * 64}",
    )
    proposed_state = replace(
        base_state,
        fold_barriers=(alternate_barrier,),
        loss_ledger=alternate_ledger,
    )
    assert validate_capsule_state_v21(proposed_state) is None

    rejected = validate_reencoding_candidate_v21(
        base_state,
        _candidate(base_state, proposed_state),
    )
    assert rejected.status is ReencodingStatusV21.ROLLED_BACK
    assert rejected.state is base_state
    assert rejected.consumed_delta is False

    with pytest.raises(ValueError, match="cumulative_root"):
        validate_capsule_state_v21(
            _state(
                fold_barriers=(
                    replace(barrier, cumulative_root="v2-barrier-root:foreign"),
                )
            )
        )


def test_candidate_rejects_barrier_and_ledger_advance_injection_and_deletion() -> None:
    barrier = FoldBarrierV21(
        namespace=_PROJECT_NAMESPACE,
        incarnation=1,
        folded_through_high_water=8,
        cumulative_root=fold_barrier_root_v21(_PROJECT_NAMESPACE, 1, 8),
    )
    ledger = LossLedgerV21(
        role_counts=((CapsuleRoleV21.CONTEXT, 1),),
        cumulative_loss_root=loss_ledger_root_v21(((CapsuleRoleV21.CONTEXT, 1),), 1),
        last_fold_generation=1,
    )
    base_state = _state(fold_barriers=(barrier,), loss_ledger=ledger)
    advanced_barrier = FoldBarrierV21(
        namespace=_PROJECT_NAMESPACE,
        incarnation=1,
        folded_through_high_water=9,
        cumulative_root=f"v21-barrier-root-s3:{'c' * 64}",
    )
    advanced_ledger = LossLedgerV21(
        role_counts=((CapsuleRoleV21.CONTEXT, 2),),
        cumulative_loss_root=f"v21-ledger-root-s3:{'d' * 64}",
        last_fold_generation=2,
    )
    injected_namespace = namespace_identity_v21("injected-barrier")
    injected_barrier = FoldBarrierV21(
        namespace=injected_namespace,
        incarnation=1,
        folded_through_high_water=8,
        cumulative_root=f"v21-barrier-root-s3:{'e' * 64}",
    )

    candidates = (
        replace(
            base_state,
            frame=replace(base_state.frame, generation=4, high_water=13),
            fold_barriers=(advanced_barrier,),
        ),
        replace(
            base_state,
            frame=replace(base_state.frame, generation=4, high_water=13),
            loss_ledger=advanced_ledger,
        ),
        replace(base_state, fold_barriers=(barrier, injected_barrier)),
        replace(base_state, fold_barriers=()),
    )
    for proposed_state in candidates:
        result = validate_reencoding_candidate_v21(
            base_state,
            _candidate(base_state, proposed_state),
        )
        assert result.status is ReencodingStatusV21.ROLLED_BACK
        assert result.state is base_state
        assert result.consumed_delta is False


def test_hard_closure_requires_exact_target_and_matching_commitment() -> None:
    target = _record("target")
    source = _record(
        "core",
        core_required=True,
        hard_depends_on=(
            HardDependencyV21(
                target_id=target.record_id,
                target_commitment=target.commitment,
            ),
        ),
    )
    complete = _state(exact_kernel=(source, target))

    assert validate_capsule_state_v21(complete) is None
    with pytest.raises(ValueError, match="hard dependency"):
        validate_capsule_state_v21(_state(exact_kernel=(source,)))
    with pytest.raises(ValueError, match="commitment"):
        validate_capsule_state_v21(
            _state(exact_kernel=(source, replace(target, commitment="tampered")))
        )


def test_nonrecoverable_control_collections_reject_raw_source_text() -> None:
    valid_segment = _segment()
    assert validate_capsule_state_v21(_state(capsule_segments=(valid_segment,))) is None

    raw_dictionary = cast(tuple[DictionaryEntryV21, ...], ("recoverable source text",))
    with pytest.raises(ValueError, match="dictionary"):
        validate_capsule_state_v21(_state(dictionary=raw_dictionary))

    raw_representatives = cast(
        tuple[CapsuleRepresentativeV21, ...], ("recoverable source text",)
    )
    with pytest.raises(ValueError, match="representative"):
        validate_capsule_state_v21(
            _state(
                capsule_segments=(
                    replace(valid_segment, representatives=raw_representatives),
                )
            )
        )


def test_control_identifiers_cannot_smuggle_recoverable_source_prose() -> None:
    with pytest.raises(ValueError, match="dictionary"):
        validate_capsule_state_v21(
            _state(
                dictionary=(
                    DictionaryEntryV21(
                        namespace="project",
                        key="recoverable source prose",
                        commitment="dictionary-commitment",
                    ),
                ),
            )
        )
    with pytest.raises(ValueError, match="commitment"):
        validate_capsule_state_v21(
            _state(
                exact_kernel=(
                    replace(
                        _record("core", core_required=True),
                        commitment="recoverable source prose",
                    ),
                )
            )
        )
    with pytest.raises(ValueError, match="segment"):
        validate_capsule_state_v21(
            _state(
                capsule_segments=(
                    replace(
                        _segment(),
                        folded_commitment_root="recoverable source prose",
                    ),
                )
            )
        )


def test_v21_domain_digests_bind_bodies_and_reject_foreign_identity_tokens() -> None:
    base_state = _state()
    exact = base_state.exact_kernel[0]
    with pytest.raises(ValueError, match="commitment"):
        validate_capsule_state_v21(
            _state(exact_kernel=(replace(exact, body="mutated source body"),))
        )
    with pytest.raises(ValueError, match="commitment"):
        validate_capsule_state_v21(_state(exact_kernel=(replace(exact, as_of=11),)))
    with pytest.raises(ValueError, match="commitment"):
        validate_capsule_state_v21(
            _state(
                exact_kernel=(
                    replace(exact, commitment="cmVjb3ZlcmFibGUgc291cmNlIHByb3Nl"),
                )
            )
        )
    with pytest.raises(ValueError, match="segment"):
        validate_capsule_state_v21(
            _state(
                capsule_segments=(
                    replace(_segment(), segment_id="v2-segment:0000000000000000"),
                )
            )
        )
    with pytest.raises(ValueError, match="dictionary"):
        validate_capsule_state_v21(
            _state(
                dictionary=(
                    DictionaryEntryV21(
                        namespace="project",
                        key="Zm9vYmFy",
                        commitment="dictionary-current-focus",
                    ),
                )
            )
        )
    with pytest.raises(ValueError, match="policy"):
        validate_control_frame_v21(
            replace(base_state.frame, reencoding_policy_hash="v2-policy:foreign"),
            base_state.bounds,
        )


def test_roles_and_loss_classes_are_closed_against_base64_source_channels() -> None:
    segment = _segment()
    with pytest.raises(ValueError, match="role"):
        validate_capsule_state_v21(
            _state(
                capsule_segments=(
                    replace(
                        segment,
                        role_counts=cast(
                            tuple[tuple[CapsuleRoleV21, int], ...],
                            (("Y29udGV4dCBzb3VyY2U=", 1),),
                        ),
                    ),
                )
            )
        )
    with pytest.raises(ValueError, match="loss class"):
        validate_capsule_state_v21(
            _state(
                capsule_segments=(
                    replace(
                        segment,
                        loss_class=cast(LossClassV21, "Ym91bmRlZCBmb2xk"),
                    ),
                )
            )
        )


def test_query_labels_follow_required_set_priority_and_reject_bad_requests() -> None:
    state = _state(capsule_segments=(_segment(),))
    exact = QueryRequirementV21(record_id=state.exact_kernel[0].record_id)
    approximate = QueryRequirementV21(coverage_index=0)
    missing = QueryRequirementV21(
        record_id=record_identity_v21(_PROJECT_NAMESPACE, "released")
    )

    assert query_label_v21(state, (exact,)) is QueryLabelV21.EXACT
    assert query_label_v21(state, (exact, approximate)) is QueryLabelV21.CAPSULE_APPROX
    assert (
        query_label_v21(state, (exact, approximate, missing))
        is QueryLabelV21.RELEASED_MISS
    )
    with pytest.raises(ValueError, match="required"):
        query_label_v21(state, ())
    with pytest.raises((TypeError, ValueError)):
        query_label_v21(state, cast(tuple[QueryRequirementV21, ...], ("core",)))


def test_query_label_revalidates_a_mutated_requirement_selector() -> None:
    state = _state()
    requirement = QueryRequirementV21(record_id=state.exact_kernel[0].record_id)
    object.__setattr__(requirement, "coverage_index", 0)

    with pytest.raises(ValueError, match="exactly one selector"):
        query_label_v21(state, (requirement,))


def test_stale_barrier_is_checked_per_record_before_high_water_projection() -> None:
    base_state = _state(
        fold_barriers=(
            FoldBarrierV21(
                namespace=_PROJECT_NAMESPACE,
                incarnation=1,
                folded_through_high_water=12,
                cumulative_root=fold_barrier_root_v21(_PROJECT_NAMESPACE, 1, 12),
            ),
        )
    )
    stale_record = _record("stale", as_of=12)

    result = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            base_state,
            incoming_records=(stale_record,),
            target_high_water=999,
        ),
    )

    assert result.status is ReencodingStatusV21.STALE_REJECTED
    assert result.state is base_state
    assert result.consumed_delta is False


def test_incarnation_dominance_rejects_lower_barriers_and_residents() -> None:
    retired_namespace = namespace_identity_v21("retired-project")
    high_barrier = FoldBarrierV21(
        namespace=retired_namespace,
        incarnation=2,
        folded_through_high_water=8,
        cumulative_root=fold_barrier_root_v21(retired_namespace, 2, 8),
    )
    low_resident = _record(
        "retired-low",
        namespace=retired_namespace,
        incarnation=1,
    )
    with pytest.raises(ValueError, match="incarnation"):
        validate_capsule_state_v21(
            _state(fold_barriers=(high_barrier,), hot_cache=(low_resident,))
        )

    base_state = _state(fold_barriers=(high_barrier,))
    incoming = _record(
        "retired-replay",
        namespace=retired_namespace,
        incarnation=1,
        as_of=13,
    )
    next_frame = replace(base_state.frame, generation=4, high_water=13)
    rejected = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            replace(base_state, frame=next_frame, hot_cache=(incoming,)),
            incoming_records=(incoming,),
            target_high_water=13,
        ),
    )
    assert rejected.status is ReencodingStatusV21.STALE_REJECTED
    assert rejected.state is base_state
    assert rejected.consumed_delta is False

    lower_barrier = FoldBarrierV21(
        namespace=retired_namespace,
        incarnation=1,
        folded_through_high_water=9,
        cumulative_root=fold_barrier_root_v21(retired_namespace, 1, 9),
    )
    reintroduced = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            replace(
                base_state,
                frame=next_frame,
                fold_barriers=(high_barrier, lower_barrier),
            ),
            target_high_water=13,
        ),
    )
    assert reintroduced.status is ReencodingStatusV21.ROLLED_BACK
    assert reintroduced.state is base_state
    assert reintroduced.consumed_delta is False


def test_candidate_binds_direct_base_parent_and_consumes_it_atomically() -> None:
    parent = _segment("parent", generation_start=1, generation_end=2)
    base_state = _state(capsule_segments=(parent,))
    next_frame = replace(base_state.frame, generation=4, high_water=13)
    child_template = _segment(
        "child",
        parent_ids=(parent.segment_id,),
        generation_start=4,
        generation_end=4,
        input_commitment_root="placeholder",
    )
    child = replace(
        child_template,
        input_commitment_root=segment_input_commitment_root_v21(
            (parent,),
            folded_commitment_root=child_template.folded_commitment_root,
            generation_start=child_template.generation_start,
            generation_end=child_template.generation_end,
            fold_policy_hash=child_template.fold_policy_hash,
        ),
    )
    proposed_state = _state(frame=next_frame, capsule_segments=(child,))

    accepted = validate_reencoding_candidate_v21(
        base_state, _candidate(base_state, proposed_state)
    )

    assert accepted.status is ReencodingStatusV21.COMMITTED
    assert accepted.state is proposed_state
    assert accepted.consumed_delta is True

    retained_parent = _state(
        frame=next_frame,
        capsule_segments=(parent, child),
    )
    retained = validate_reencoding_candidate_v21(
        base_state, _candidate(base_state, retained_parent)
    )
    assert retained.status is ReencodingStatusV21.ROLLED_BACK
    assert retained.state is base_state

    tampered_parent = _segment(
        "parent", generation_start=1, generation_end=2, budget_used=65
    )
    tampered_base = _state(capsule_segments=(tampered_parent,))
    tampered = validate_reencoding_candidate_v21(
        tampered_base,
        _candidate(tampered_base, proposed_state),
    )
    assert tampered.status is ReencodingStatusV21.ROLLED_BACK
    assert tampered.state is tampered_base


def test_candidate_cannot_construct_a_first_segment_without_a_base_parent() -> None:
    base_state = _state()
    first_leaf = _segment()
    result = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            replace(base_state, capsule_segments=(first_leaf,)),
        ),
    )

    assert result.status is ReencodingStatusV21.ROLLED_BACK
    assert result.state is base_state
    assert result.consumed_delta is False


def test_direct_parent_is_single_use_and_cannot_remain_resident_with_a_child() -> None:
    parent = _segment("parent", generation_start=1, generation_end=2)
    base_state = _state(capsule_segments=(parent,))
    next_frame = replace(base_state.frame, generation=4, high_water=13)

    def child_for(segment_id: str) -> CapsuleSegmentV21:
        template = _segment(
            segment_id,
            parent_ids=(parent.segment_id,),
            generation_start=4,
            generation_end=4,
            input_commitment_root="placeholder",
        )
        return replace(
            template,
            input_commitment_root=segment_input_commitment_root_v21(
                (parent,),
                folded_commitment_root=template.folded_commitment_root,
                generation_start=template.generation_start,
                generation_end=template.generation_end,
                fold_policy_hash=template.fold_policy_hash,
            ),
        )

    first_child = child_for("child-one")
    second_child = child_for("child-two")
    with pytest.raises(ValueError, match="parent"):
        validate_capsule_state_v21(
            _state(
                frame=next_frame,
                capsule_segments=(parent, first_child),
            )
        )

    double_consumed = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            _state(
                frame=next_frame,
                capsule_segments=(first_child, second_child),
            ),
        ),
    )
    assert double_consumed.status is ReencodingStatusV21.ROLLED_BACK
    assert double_consumed.state is base_state
    assert double_consumed.consumed_delta is False


def test_budget_breach_rolls_back_without_consuming_the_delta() -> None:
    base_state = _state()
    result = validate_reencoding_candidate_v21(
        base_state,
        _candidate(base_state, base_state, requested_budget=1),
    )

    assert result.status is ReencodingStatusV21.ROLLED_BACK
    assert result.state is base_state
    assert result.consumed_delta is False


def test_candidate_cannot_invent_a_resident_body_or_commitment() -> None:
    base_state = _state()
    invented = _record("invented")
    candidate_only = validate_reencoding_candidate_v21(
        base_state,
        _candidate(base_state, replace(base_state, hot_cache=(invented,))),
    )

    assert candidate_only.status is ReencodingStatusV21.ROLLED_BACK
    assert candidate_only.state is base_state
    assert candidate_only.consumed_delta is False

    fresh = _record("fresh", as_of=13)
    next_frame = replace(base_state.frame, generation=4, high_water=13)
    authorized = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            replace(base_state, frame=next_frame, hot_cache=(fresh,)),
            incoming_records=(fresh,),
            target_high_water=13,
        ),
    )
    assert authorized.status is ReencodingStatusV21.COMMITTED
    assert authorized.state is not base_state
    assert authorized.consumed_delta is True

    tampered = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            replace(
                base_state,
                frame=next_frame,
                hot_cache=(replace(fresh, commitment="tampered-commitment"),),
            ),
            incoming_records=(fresh,),
            target_high_water=13,
        ),
    )
    assert tampered.status is ReencodingStatusV21.ROLLED_BACK
    assert tampered.state is base_state
    assert tampered.consumed_delta is False


def test_candidate_cannot_consume_an_incoming_record_without_installing_it() -> None:
    base_state = _state()
    incoming = _record("uninstalled", as_of=13)
    proposed_state = replace(
        base_state,
        frame=replace(base_state.frame, generation=4, high_water=13),
    )

    result = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            proposed_state,
            incoming_records=(incoming,),
            target_high_water=13,
        ),
    )

    assert result.status is ReencodingStatusV21.ROLLED_BACK
    assert result.state is base_state
    assert result.consumed_delta is False


def test_candidate_cannot_silently_remove_base_exact_or_hot_residents() -> None:
    base_state = _state()
    exact_removed = validate_reencoding_candidate_v21(
        base_state,
        _candidate(base_state, replace(base_state, exact_kernel=())),
    )

    assert exact_removed.status is ReencodingStatusV21.ROLLED_BACK
    assert exact_removed.state is base_state
    assert exact_removed.consumed_delta is False

    hot = _record("hot")
    base_with_hot = _state(hot_cache=(hot,))
    hot_removed = validate_reencoding_candidate_v21(
        base_with_hot,
        _candidate(base_with_hot, replace(base_with_hot, hot_cache=())),
    )

    assert hot_removed.status is ReencodingStatusV21.ROLLED_BACK
    assert hot_removed.state is base_with_hot
    assert hot_removed.consumed_delta is False

    updated_core = _record(
        "core",
        core_required=True,
        as_of=13,
        body="updated exact body for core",
    )
    next_frame = replace(base_state.frame, generation=4, high_water=13)
    authorized_update = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            replace(base_state, frame=next_frame, exact_kernel=(updated_core,)),
            incoming_records=(updated_core,),
            target_high_water=13,
        ),
    )

    assert authorized_update.status is ReencodingStatusV21.COMMITTED
    assert authorized_update.state.exact_kernel == (updated_core,)
    assert authorized_update.consumed_delta is True


def test_candidate_rejects_old_replay_even_without_an_applicable_barrier() -> None:
    base_state = _state()
    replay = _record("replay", as_of=12)
    proposed_state = replace(
        base_state,
        frame=replace(base_state.frame, generation=4, high_water=13),
        hot_cache=(replay,),
    )

    result = validate_reencoding_candidate_v21(
        base_state,
        _candidate(
            base_state,
            proposed_state,
            incoming_records=(replay,),
            target_high_water=13,
        ),
    )

    assert result.status is ReencodingStatusV21.ROLLED_BACK
    assert result.state is base_state
    assert result.consumed_delta is False


def test_candidate_cannot_regress_a_barrier_or_fixed_loss_ledger() -> None:
    barrier = FoldBarrierV21(
        namespace=_PROJECT_NAMESPACE,
        incarnation=1,
        folded_through_high_water=8,
        cumulative_root=fold_barrier_root_v21(_PROJECT_NAMESPACE, 1, 8),
    )
    ledger = LossLedgerV21(
        role_counts=((CapsuleRoleV21.CONTEXT, 2),),
        cumulative_loss_root=loss_ledger_root_v21(((CapsuleRoleV21.CONTEXT, 2),), 2),
        last_fold_generation=2,
    )
    base_state = _state(fold_barriers=(barrier,), loss_ledger=ledger)
    proposed_state = _state(
        frame=replace(base_state.frame, generation=4, high_water=13),
        fold_barriers=(
            FoldBarrierV21(
                namespace=_PROJECT_NAMESPACE,
                incarnation=1,
                folded_through_high_water=7,
                cumulative_root=fold_barrier_root_v21(_PROJECT_NAMESPACE, 1, 7),
            ),
        ),
        loss_ledger=LossLedgerV21(
            role_counts=((CapsuleRoleV21.CONTEXT, 2),),
            cumulative_loss_root=loss_ledger_root_v21(
                ((CapsuleRoleV21.CONTEXT, 2),), 1
            ),
            last_fold_generation=1,
        ),
    )

    result = validate_reencoding_candidate_v21(
        base_state, _candidate(base_state, proposed_state)
    )

    assert result.status is ReencodingStatusV21.ROLLED_BACK
    assert result.state is base_state
    assert result.consumed_delta is False
