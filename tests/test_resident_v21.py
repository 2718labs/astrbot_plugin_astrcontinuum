"""Resident byte accounting for the independent schema-3 control plane."""

from __future__ import annotations

from dataclasses import replace

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
    LossClassV21,
    LossLedgerV21,
    SparseWeightPolicyV21,
    capacity_policy_hash_v21,
    dictionary_commitment_v21,
    dictionary_identity_v21,
    fold_barrier_root_v21,
    fold_policy_hash_v21,
    folded_commitment_root_v21,
    loss_ledger_root_v21,
    namespace_identity_v21,
    record_identity_v21,
    reencoding_policy_hash_v21,
    representative_commitment_v21,
    representative_identity_v21,
    resident_layout_v21,
    segment_identity_v21,
    segment_input_commitment_root_v21,
    source_commitment_v21,
)
from crm_experiment.resident_v21 import (
    resident_breakdown_v21,
    resident_bytes_v21,
    resident_hash_v21,
)

_PROJECT_NAMESPACE = namespace_identity_v21("project")
_OLD_PROJECT_NAMESPACE = namespace_identity_v21("old-project")
_CURRENT_KEY = dictionary_identity_v21(_PROJECT_NAMESPACE, "current-focus")
_HOT_KEY = dictionary_identity_v21(_PROJECT_NAMESPACE, "hot-key")


def _bounds() -> ControlFoldBoundsV21:
    return ControlFoldBoundsV21(
        max_exact_kernel_records=8,
        max_exact_kernel_bytes=2_048,
        max_hot_records=6,
        max_hot_bytes=1_024,
        max_hot_frontier_entries=6,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=12,
        max_dictionary_bytes=2_048,
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
        accepted_budget=8_192,
        reencoding_policy_hash=reencoding_policy_hash_v21("resident-policy-v21"),
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )


def _record(record_label: str, *, core_required: bool = False) -> ExactRecordV21:
    record_id = record_identity_v21(_PROJECT_NAMESPACE, record_label)
    body = f"source body for {record_label} 🙂"
    return ExactRecordV21(
        record_id=record_id,
        namespace=_PROJECT_NAMESPACE,
        incarnation=1,
        as_of=12,
        commitment=source_commitment_v21(
            record_id=record_id,
            namespace=_PROJECT_NAMESPACE,
            incarnation=1,
            as_of=12,
            body=body,
            core_required=core_required,
            active=True,
        ),
        body=body,
        core_required=core_required,
        active=True,
        hard_depends_on=(),
    )


def _record_with_body(record: ExactRecordV21, body: str) -> ExactRecordV21:
    return replace(
        record,
        body=body,
        commitment=source_commitment_v21(
            record_id=record.record_id,
            namespace=record.namespace,
            incarnation=record.incarnation,
            as_of=record.as_of,
            body=body,
            core_required=record.core_required,
            active=record.active,
        ),
    )


def _segment(
    *, loss_class: LossClassV21 = LossClassV21.BOUNDED_FOLD
) -> CapsuleSegmentV21:
    segment_id = segment_identity_v21(_PROJECT_NAMESPACE, "segment-1")
    fold_policy_hash = fold_policy_hash_v21("fold-policy-v21")
    representatives = (
        CapsuleRepresentativeV21(
            feature_id=representative_identity_v21(segment_id, "feature-segment-1"),
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
    role_counts = ((CapsuleRoleV21.CONTEXT, 1),)
    coverage_vector = (1, 0, 0, 0)
    bridge_vector = (0, 0, 0)
    folded_root = folded_commitment_root_v21(
        segment_id=segment_id,
        namespace=_PROJECT_NAMESPACE,
        generation_start=1,
        generation_end=1,
        source_count=1,
        role_counts=role_counts,
        representatives=representatives,
        coverage_vector=coverage_vector,
        bridge_vector=bridge_vector,
        loss_class=loss_class,
        budget_used=64,
        fold_policy_hash=fold_policy_hash,
    )
    return CapsuleSegmentV21(
        segment_id=segment_id,
        parent_ids=(),
        input_commitment_root=segment_input_commitment_root_v21(
            (),
            folded_commitment_root=folded_root,
            generation_start=1,
            generation_end=1,
            fold_policy_hash=fold_policy_hash,
        ),
        folded_commitment_root=folded_root,
        namespace=_PROJECT_NAMESPACE,
        generation_start=1,
        generation_end=1,
        source_count=1,
        role_counts=role_counts,
        representatives=representatives,
        coverage_vector=coverage_vector,
        bridge_vector=bridge_vector,
        loss_class=loss_class,
        budget_used=64,
        fold_policy_hash=fold_policy_hash,
    )


def _state() -> CapsuleStateV21:
    bounds = _bounds()
    return CapsuleStateV21(
        frame=_frame(bounds),
        bounds=bounds,
        exact_kernel=(_record("core", core_required=True),),
        hot_cache=(_record("hot"),),
        hot_frontier=(_HOT_KEY,),
        dictionary=(
            DictionaryEntryV21(
                namespace=_PROJECT_NAMESPACE,
                key=_CURRENT_KEY,
                commitment=dictionary_commitment_v21(_PROJECT_NAMESPACE, _CURRENT_KEY),
            ),
        ),
        capsule_segments=(_segment(),),
        fold_barriers=(
            FoldBarrierV21(
                namespace=_OLD_PROJECT_NAMESPACE,
                incarnation=1,
                folded_through_high_water=8,
                cumulative_root=fold_barrier_root_v21(_OLD_PROJECT_NAMESPACE, 1, 8),
            ),
        ),
        sparse_weight_policy=SparseWeightPolicyV21(
            default_weight=1.0,
            overrides=((_CURRENT_KEY, 0.5),),
        ),
        loss_ledger=LossLedgerV21(
            role_counts=(
                (CapsuleRoleV21.CONTEXT, 1),
                (CapsuleRoleV21.ROOT_GOAL, 1),
            ),
            cumulative_loss_root=loss_ledger_root_v21(
                (
                    (CapsuleRoleV21.CONTEXT, 1),
                    (CapsuleRoleV21.ROOT_GOAL, 1),
                ),
                1,
            ),
            last_fold_generation=1,
        ),
    )


def test_resident_breakdown_counts_all_control_plane_fields_and_source_bodies() -> None:
    state = _state()
    breakdown = resident_breakdown_v21(state)

    assert breakdown.resident_bytes == resident_bytes_v21(state)
    assert (
        breakdown.resident_bytes
        == breakdown.control_bytes + breakdown.source_body_bytes
    )
    assert breakdown.frame_bytes > 0
    assert breakdown.dictionary_bytes > 0
    assert breakdown.barrier_bytes > 0
    assert breakdown.sparse_weight_bytes > 0
    assert breakdown.segment_bytes > 0
    assert breakdown.loss_ledger_bytes > 0
    assert breakdown.source_body_bytes == sum(
        len(record.body.encode("utf-8"))
        for record in state.exact_kernel + state.hot_cache
    )


def test_each_control_field_changes_its_resident_accounting_and_hash_domain() -> None:
    state = _state()
    before = resident_breakdown_v21(state)
    before_hash = resident_hash_v21(state)

    changed_frame = replace(
        state,
        frame=replace(
            state.frame,
            reencoding_policy_hash=reencoding_policy_hash_v21(
                "resident-policy-v21-longer"
            ),
        ),
    )
    new_key = dictionary_identity_v21(_PROJECT_NAMESPACE, "new-key")
    changed_dictionary = replace(
        state,
        dictionary=state.dictionary
        + (
            DictionaryEntryV21(
                namespace=_PROJECT_NAMESPACE,
                key=new_key,
                commitment=dictionary_commitment_v21(_PROJECT_NAMESPACE, new_key),
            ),
        ),
    )
    new_barrier_namespace = namespace_identity_v21("new-barrier-project")
    changed_barriers = replace(
        state,
        fold_barriers=state.fold_barriers
        + (
            FoldBarrierV21(
                namespace=new_barrier_namespace,
                incarnation=2,
                folded_through_high_water=12,
                cumulative_root=fold_barrier_root_v21(new_barrier_namespace, 2, 12),
            ),
        ),
    )
    changed_weights = replace(
        state,
        sparse_weight_policy=SparseWeightPolicyV21(
            default_weight=1.0,
            overrides=tuple(sorted(((_CURRENT_KEY, 0.5), (new_key, 0.25)))),
        ),
    )
    changed_segments = replace(state, capsule_segments=())
    changed_ledger_counts = (
        (CapsuleRoleV21.CONTEXT, 1),
        (CapsuleRoleV21.ROOT_GOAL, 2),
    )
    changed_ledger = replace(
        state,
        loss_ledger=LossLedgerV21(
            role_counts=changed_ledger_counts,
            cumulative_loss_root=loss_ledger_root_v21(changed_ledger_counts, 1),
            last_fold_generation=1,
        ),
    )

    changes = (
        (changed_frame, "frame_hash"),
        (changed_dictionary, "dictionary_hash"),
        (changed_barriers, "barrier_hash"),
        (changed_weights, "sparse_weight_hash"),
        (changed_segments, "segment_hash"),
        (changed_ledger, "loss_ledger_hash"),
    )
    for changed, field_name in changes:
        changed_breakdown = resident_breakdown_v21(changed)
        assert getattr(changed_breakdown, field_name) != getattr(before, field_name)
        assert resident_hash_v21(changed) != before_hash


def test_source_body_bytes_are_reported_separately_from_control_bytes() -> None:
    state = _state()
    changed = replace(
        state,
        exact_kernel=(
            _record_with_body(state.exact_kernel[0], "longer source body 🙂🙂🙂"),
        ),
    )

    before = resident_breakdown_v21(state)
    after = resident_breakdown_v21(changed)

    assert after.source_body_bytes > before.source_body_bytes
    assert after.resident_bytes > before.resident_bytes
    assert resident_hash_v21(changed) != resident_hash_v21(state)


def test_escaped_source_bytes_are_not_misclassified_as_control_bytes() -> None:
    state = _state()
    escaped_body = 'quoted "value" with slash \\ and newline\n'
    changed = replace(
        state,
        exact_kernel=(_record_with_body(state.exact_kernel[0], escaped_body),),
    )

    breakdown = resident_breakdown_v21(changed)
    layout = resident_layout_v21(changed)

    assert breakdown.source_body_bytes == sum(
        len(record.body.encode("utf-8"))
        for record in changed.exact_kernel + changed.hot_cache
    )
    assert breakdown.canonical_source_body_bytes > breakdown.source_body_bytes
    assert breakdown.control_bytes == layout.control_bytes
    assert breakdown.source_body_bytes == layout.source_body_bytes
    assert breakdown.resident_bytes == layout.resident_bytes
    assert (
        breakdown.resident_bytes
        == breakdown.control_bytes + breakdown.source_body_bytes
    )


def test_breakdown_is_content_sensitive_when_control_values_have_equal_bytes() -> None:
    state = _state()
    before = resident_breakdown_v21(state)

    equal_width_changes = (
        replace(
            state,
            frame=replace(
                state.frame,
                reencoding_policy_hash=reencoding_policy_hash_v21(
                    "resident-policy-v22"
                ),
            ),
        ),
        replace(
            state,
            dictionary=(
                DictionaryEntryV21(
                    namespace=_PROJECT_NAMESPACE,
                    key=dictionary_identity_v21(_PROJECT_NAMESPACE, "current-topic"),
                    commitment=dictionary_commitment_v21(
                        _PROJECT_NAMESPACE,
                        dictionary_identity_v21(_PROJECT_NAMESPACE, "current-topic"),
                    ),
                ),
            ),
        ),
        replace(
            state,
            fold_barriers=(
                FoldBarrierV21(
                    namespace=namespace_identity_v21("other-old-project"),
                    incarnation=1,
                    folded_through_high_water=8,
                    cumulative_root=fold_barrier_root_v21(
                        namespace_identity_v21("other-old-project"), 1, 8
                    ),
                ),
            ),
        ),
        replace(
            state,
            sparse_weight_policy=replace(
                state.sparse_weight_policy,
                overrides=((_CURRENT_KEY, 0.6),),
            ),
        ),
        replace(
            state,
            capsule_segments=(_segment(loss_class=LossClassV21.BOUNDED_HOLD),),
        ),
        replace(
            state,
            loss_ledger=LossLedgerV21(
                role_counts=(
                    (CapsuleRoleV21.CONTEXT, 2),
                    (CapsuleRoleV21.ROOT_GOAL, 1),
                ),
                cumulative_loss_root=loss_ledger_root_v21(
                    (
                        (CapsuleRoleV21.CONTEXT, 2),
                        (CapsuleRoleV21.ROOT_GOAL, 1),
                    ),
                    1,
                ),
                last_fold_generation=1,
            ),
        ),
    )
    for changed in equal_width_changes:
        assert resident_breakdown_v21(changed) != before
        assert resident_hash_v21(changed) != resident_hash_v21(state)
