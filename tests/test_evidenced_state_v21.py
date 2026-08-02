"""Current-support evidence guards for the schema-3 control plane."""

from __future__ import annotations

from collections.abc import Callable
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
    LossClassV21,
    LossLedgerV21,
    QueryLabelV21,
    SparseWeightPolicyV21,
    body_elided_state_v21,
    canonical_bytes_v21,
    capacity_policy_hash_v21,
    dictionary_commitment_v21,
    dictionary_identity_v21,
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
from crm_experiment.evidenced_state_v21 import (
    ContributionLocatorV21,
    ContributionRequirementV21,
    ContributionSupportV21,
    EvidencedCapsuleStateV21,
    EvidencedStateMismatchV21,
    evidenced_resident_layout_v21,
    evidenced_state_hash_v21,
    query_evidenced_v21,
    validate_contribution_requirement_v21,
    validate_evidenced_capsule_state_v21,
)

_PROJECT_NAMESPACE = namespace_identity_v21("evidenced-project")


def _bounds() -> ControlFoldBoundsV21:
    return ControlFoldBoundsV21(
        max_exact_kernel_records=8,
        max_exact_kernel_bytes=4_096,
        max_hot_records=8,
        max_hot_bytes=4_096,
        max_hot_frontier_entries=8,
        max_hot_frontier_bytes=512,
        max_dictionary_entries=8,
        max_dictionary_bytes=2_048,
        max_segments=4,
        max_representatives_per_segment=3,
        coverage_vector_size=4,
        bridge_vector_size=3,
        max_barriers=4,
        max_sparse_overrides=4,
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
        accepted_budget=16_384,
        reencoding_policy_hash=reencoding_policy_hash_v21("evidenced-policy"),
        capacity_policy_hash=capacity_policy_hash_v21(bounds),
    )


def _record(
    label: str,
    *,
    body: str | None = None,
    core_required: bool = False,
) -> ExactRecordV21:
    record_id = record_identity_v21(_PROJECT_NAMESPACE, label)
    selected_body = body if body is not None else f"body for {label}"
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
            body=selected_body,
            core_required=core_required,
            active=True,
        ),
        body=selected_body,
        core_required=core_required,
        active=True,
        hard_depends_on=(),
    )


def _dictionary_entry(label: str) -> DictionaryEntryV21:
    key = dictionary_identity_v21(_PROJECT_NAMESPACE, label)
    return DictionaryEntryV21(
        namespace=_PROJECT_NAMESPACE,
        key=key,
        commitment=dictionary_commitment_v21(_PROJECT_NAMESPACE, key),
    )


def _segment(label: str = "segment") -> CapsuleSegmentV21:
    segment_id = segment_identity_v21(_PROJECT_NAMESPACE, label)
    fold_policy_hash = fold_policy_hash_v21("evidenced-fold-policy")
    representatives = (
        CapsuleRepresentativeV21(
            feature_id=representative_identity_v21(segment_id, f"feature-{label}"),
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
    folded_root = folded_commitment_root_v21(
        segment_id=segment_id,
        namespace=_PROJECT_NAMESPACE,
        generation_start=1,
        generation_end=1,
        source_count=1,
        role_counts=role_counts,
        representatives=representatives,
        coverage_vector=(1, 0, 0, 0),
        bridge_vector=(0, 0, 0),
        loss_class=LossClassV21.BOUNDED_FOLD,
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
        coverage_vector=(1, 0, 0, 0),
        bridge_vector=(0, 0, 0),
        loss_class=LossClassV21.BOUNDED_FOLD,
        budget_used=64,
        fold_policy_hash=fold_policy_hash,
    )


def _state(
    *,
    bounds: ControlFoldBoundsV21 | None = None,
    frame: ControlFrameV21 | None = None,
    exact_kernel: tuple[ExactRecordV21, ...] | None = None,
    hot_cache: tuple[ExactRecordV21, ...] = (),
    dictionary: tuple[DictionaryEntryV21, ...] | None = None,
    capsule_segments: tuple[CapsuleSegmentV21, ...] = (),
) -> CapsuleStateV21:
    selected_bounds = bounds or _bounds()
    return CapsuleStateV21(
        frame=frame or _frame(selected_bounds),
        bounds=selected_bounds,
        exact_kernel=exact_kernel
        if exact_kernel is not None
        else (_record("core", core_required=True),),
        hot_cache=hot_cache,
        hot_frontier=(),
        dictionary=dictionary
        if dictionary is not None
        else (_dictionary_entry("current-focus"),),
        capsule_segments=capsule_segments,
        fold_barriers=(),
        sparse_weight_policy=SparseWeightPolicyV21(default_weight=1.0, overrides=()),
        loss_ledger=LossLedgerV21(
            role_counts=((CapsuleRoleV21.ROOT_GOAL, 1),),
            cumulative_loss_root=loss_ledger_root_v21(
                ((CapsuleRoleV21.ROOT_GOAL, 1),), 0
            ),
            last_fold_generation=0,
        ),
    )


def _exact_locator(state: CapsuleStateV21, key: str) -> ContributionLocatorV21:
    record = state.exact_kernel[0]
    return ContributionLocatorV21(
        contribution_key=key,
        support=ContributionSupportV21.EXACT,
        resident_id=record.record_id,
        support_commitment=record.commitment,
    )


def _segment_locator(segment: CapsuleSegmentV21, key: str) -> ContributionLocatorV21:
    return ContributionLocatorV21(
        contribution_key=key,
        support=ContributionSupportV21.SEGMENT,
        resident_id=segment.segment_id,
        support_commitment=segment.folded_commitment_root,
    )


def _wrapper(
    state: CapsuleStateV21, locators: tuple[ContributionLocatorV21, ...]
) -> EvidencedCapsuleStateV21:
    return EvidencedCapsuleStateV21(
        state=state,
        contribution_index=tuple(
            sorted(locators, key=lambda locator: locator.contribution_key)
        ),
    )


def test_evidenced_wrapper_rejects_non_nominal_state() -> None:
    with pytest.raises(TypeError, match="nominal"):
        wrapper = EvidencedCapsuleStateV21(
            state=cast(CapsuleStateV21, SimpleNamespace()),
            contribution_index=(),
        )
        validate_evidenced_capsule_state_v21(wrapper)


def test_nominal_locator_and_requirement_bind_a_current_exact_record() -> None:
    state = _state()
    key = state.dictionary[0].key
    wrapper = _wrapper(state, (_exact_locator(state, key),))
    requirement = ContributionRequirementV21(contribution_key=key)
    expected_hash = evidenced_state_hash_v21(wrapper)

    assert validate_evidenced_capsule_state_v21(wrapper) is None
    assert validate_contribution_requirement_v21(requirement) is None
    assert (
        query_evidenced_v21(
            wrapper,
            (requirement,),
            expected_state_hash=expected_hash,
        )
        is QueryLabelV21.EXACT
    )


def test_query_requires_an_external_expected_state_hash_keyword() -> None:
    state = _state()
    key = state.dictionary[0].key
    wrapper = _wrapper(state, (_exact_locator(state, key),))
    requirement = ContributionRequirementV21(contribution_key=key)
    query_without_expected = cast(Callable[..., QueryLabelV21], query_evidenced_v21)

    with pytest.raises(TypeError, match="expected_state_hash"):
        query_without_expected(wrapper, (requirement,))


def test_query_exposes_the_contract_wrapper_and_requirements_keywords() -> None:
    state = _state()
    key = state.dictionary[0].key
    wrapper = _wrapper(state, (_exact_locator(state, key),))
    requirement = ContributionRequirementV21(contribution_key=key)
    expected_hash = evidenced_state_hash_v21(wrapper)

    assert (
        query_evidenced_v21(
            wrapper=wrapper,
            requirements=(requirement,),
            expected_state_hash=expected_hash,
        )
        is QueryLabelV21.EXACT
    )


def test_locator_and_requirement_reject_non_nominal_or_foreign_domains() -> None:
    state = _state()
    key = state.dictionary[0].key
    locator = _exact_locator(state, key)

    with pytest.raises(ValueError, match="locator"):
        EvidencedCapsuleStateV21(
            state=state,
            contribution_index=(cast(ContributionLocatorV21, SimpleNamespace()),),
        )
    with pytest.raises(ValueError, match="dictionary"):
        ContributionRequirementV21(contribution_key=state.exact_kernel[0].record_id)
    with pytest.raises(ValueError, match="support"):
        ContributionLocatorV21(
            contribution_key=key,
            support=cast(ContributionSupportV21, "EXACT"),
            resident_id=locator.resident_id,
            support_commitment=locator.support_commitment,
        )


def test_index_rejects_unsorted_duplicate_foreign_and_dangling_locators() -> None:
    first_entry = _dictionary_entry("first")
    second_entry = _dictionary_entry("second")
    dictionary = tuple(sorted((first_entry, second_entry), key=lambda entry: entry.key))
    state = _state(dictionary=dictionary)
    first = _exact_locator(state, first_entry.key)
    second = _exact_locator(state, second_entry.key)
    ordered = tuple(
        sorted((first, second), key=lambda locator: locator.contribution_key)
    )
    missing = _record("missing")
    foreign_entry = _dictionary_entry("foreign")

    with pytest.raises(ValueError, match="sorted"):
        EvidencedCapsuleStateV21(state=state, contribution_index=ordered[::-1])
    with pytest.raises(ValueError, match="unique"):
        EvidencedCapsuleStateV21(state=state, contribution_index=(first, first))
    with pytest.raises(ValueError, match="dictionary"):
        EvidencedCapsuleStateV21(
            state=state,
            contribution_index=(
                ContributionLocatorV21(
                    contribution_key=foreign_entry.key,
                    support=ContributionSupportV21.EXACT,
                    resident_id=state.exact_kernel[0].record_id,
                    support_commitment=state.exact_kernel[0].commitment,
                ),
            ),
        )
    with pytest.raises(ValueError, match="current"):
        EvidencedCapsuleStateV21(
            state=state,
            contribution_index=(
                ContributionLocatorV21(
                    contribution_key=first_entry.key,
                    support=ContributionSupportV21.EXACT,
                    resident_id=missing.record_id,
                    support_commitment=missing.commitment,
                ),
            ),
        )


def test_exact_locator_cannot_be_minted_from_a_segment_or_folded_root() -> None:
    segment = _segment()
    state = _state(capsule_segments=(segment,))

    with pytest.raises(ValueError, match="EXACT"):
        ContributionLocatorV21(
            contribution_key=state.dictionary[0].key,
            support=ContributionSupportV21.EXACT,
            resident_id=segment.segment_id,
            support_commitment=segment.folded_commitment_root,
        )


def test_query_revalidates_tampered_locator_and_requirement() -> None:
    state = _state()
    key = state.dictionary[0].key
    locator = _exact_locator(state, key)
    wrapper = _wrapper(state, (locator,))
    requirement = ContributionRequirementV21(contribution_key=key)
    wrong_record = _record("wrong")
    expected_hash = evidenced_state_hash_v21(wrapper)

    object.__setattr__(locator, "support_commitment", wrong_record.commitment)
    with pytest.raises(ValueError, match="commitment"):
        query_evidenced_v21(
            wrapper,
            (requirement,),
            expected_state_hash=expected_hash,
        )

    fresh_wrapper = _wrapper(state, (_exact_locator(state, key),))
    fresh_expected_hash = evidenced_state_hash_v21(fresh_wrapper)
    object.__setattr__(requirement, "contribution_key", state.exact_kernel[0].record_id)
    with pytest.raises(ValueError, match="dictionary"):
        query_evidenced_v21(
            fresh_wrapper,
            (requirement,),
            expected_state_hash=fresh_expected_hash,
        )


def test_index_shares_existing_dictionary_entry_and_byte_capacity() -> None:
    entry = _dictionary_entry("shared-capacity")
    entry_limited_bounds = replace(_bounds(), max_dictionary_entries=1)
    entry_limited_state = _state(
        bounds=entry_limited_bounds,
        dictionary=(entry,),
    )

    with pytest.raises(ValueError, match="entry capacity"):
        _wrapper(
            entry_limited_state,
            (_exact_locator(entry_limited_state, entry.key),),
        )

    byte_limited_bounds = replace(
        _bounds(), max_dictionary_bytes=canonical_bytes_v21((entry,))
    )
    byte_limited_state = _state(bounds=byte_limited_bounds, dictionary=(entry,))

    with pytest.raises(ValueError, match="byte capacity"):
        _wrapper(
            byte_limited_state,
            (_exact_locator(byte_limited_state, entry.key),),
        )


def test_wrapper_cbr_and_hash_are_deterministic_and_include_the_index() -> None:
    state = _state()
    key = state.dictionary[0].key
    wrapper = _wrapper(state, (_exact_locator(state, key),))
    layout = evidenced_resident_layout_v21(wrapper)
    expected_control = canonical_bytes_v21(
        {
            "state": body_elided_state_v21(state),
            "contribution_index": wrapper.contribution_index,
        }
    )
    expected_source = sum(
        len(record.body.encode("utf-8"))
        for record in state.exact_kernel + state.hot_cache
    )

    assert layout.control_bytes == expected_control
    assert layout.source_body_bytes == expected_source
    assert layout.resident_bytes == expected_control + expected_source
    assert evidenced_resident_layout_v21(wrapper) == layout
    assert evidenced_state_hash_v21(wrapper) == evidenced_state_hash_v21(wrapper)
    assert evidenced_state_hash_v21(_wrapper(state, ())) != evidenced_state_hash_v21(
        wrapper
    )

    changed_state = _state(
        exact_kernel=(_record("core", body="changed body", core_required=True),),
    )
    changed_wrapper = _wrapper(
        changed_state,
        (_exact_locator(changed_state, changed_state.dictionary[0].key),),
    )
    assert evidenced_state_hash_v21(changed_wrapper) != evidenced_state_hash_v21(
        wrapper
    )

    base_state = _state()
    base_budget = resident_layout_v21(base_state).resident_bytes
    budget_limited_state = replace(
        base_state,
        frame=replace(base_state.frame, accepted_budget=base_budget),
    )
    assert resident_layout_v21(budget_limited_state).resident_bytes <= base_budget
    with pytest.raises(ValueError, match="accepted budget"):
        _wrapper(
            budget_limited_state,
            (
                _exact_locator(
                    budget_limited_state, budget_limited_state.dictionary[0].key
                ),
            ),
        )


def test_saved_external_hash_preserves_same_requirement_from_exact_to_miss() -> None:
    exact_state = _state()
    key = exact_state.dictionary[0].key
    requirement = ContributionRequirementV21(contribution_key=key)
    exact_wrapper = _wrapper(exact_state, (_exact_locator(exact_state, key),))

    segment = _segment()
    segment_state = _state(capsule_segments=(segment,))
    segment_wrapper = _wrapper(segment_state, (_segment_locator(segment, key),))

    released_wrapper = _wrapper(_state(), ())
    exact_expected_hash = evidenced_state_hash_v21(exact_wrapper)
    segment_expected_hash = evidenced_state_hash_v21(segment_wrapper)
    released_expected_hash = evidenced_state_hash_v21(released_wrapper)

    assert (
        query_evidenced_v21(
            exact_wrapper,
            (requirement,),
            expected_state_hash=exact_expected_hash,
        )
        is QueryLabelV21.EXACT
    )
    assert (
        query_evidenced_v21(
            segment_wrapper,
            (requirement,),
            expected_state_hash=segment_expected_hash,
        )
        is QueryLabelV21.CAPSULE_APPROX
    )
    assert (
        query_evidenced_v21(
            released_wrapper,
            (requirement,),
            expected_state_hash=released_expected_hash,
        )
        is QueryLabelV21.RELEASED_MISS
    )


def test_query_priority_prefers_miss_over_segment_over_exact() -> None:
    exact_entry = _dictionary_entry("exact")
    segment_entry = _dictionary_entry("segment")
    missing_entry = _dictionary_entry("missing")
    dictionary = tuple(
        sorted((exact_entry, segment_entry, missing_entry), key=lambda entry: entry.key)
    )
    segment = _segment()
    state = _state(dictionary=dictionary, capsule_segments=(segment,))
    wrapper = _wrapper(
        state,
        (
            _exact_locator(state, exact_entry.key),
            _segment_locator(segment, segment_entry.key),
        ),
    )
    exact_requirement = ContributionRequirementV21(exact_entry.key)
    segment_requirement = ContributionRequirementV21(segment_entry.key)
    missing_requirement = ContributionRequirementV21(missing_entry.key)
    expected_hash = evidenced_state_hash_v21(wrapper)

    assert (
        query_evidenced_v21(
            wrapper,
            (exact_requirement, segment_requirement),
            expected_state_hash=expected_hash,
        )
        is QueryLabelV21.CAPSULE_APPROX
    )
    assert (
        query_evidenced_v21(
            wrapper,
            (segment_requirement, missing_requirement, exact_requirement),
            expected_state_hash=expected_hash,
        )
        is QueryLabelV21.RELEASED_MISS
    )


@pytest.mark.parametrize(
    "expected_state_hash",
    (
        "not-a-state-hash",
        "v21-state-s3:" + "0" * 64,
    ),
)
def test_invalid_expected_hash_fails_before_requirement_projection(
    expected_state_hash: str,
) -> None:
    state = _state()
    key = state.dictionary[0].key
    wrapper = _wrapper(state, (_exact_locator(state, key),))
    malformed_requirement = cast(ContributionRequirementV21, SimpleNamespace())

    with pytest.raises(ValueError, match="expected.*state hash"):
        query_evidenced_v21(
            wrapper,
            (malformed_requirement,),
            expected_state_hash=expected_state_hash,
        )


def test_old_hash_rejects_a_coherent_exact_repoint() -> None:
    unrelated = _record("unrelated")
    state = _state(hot_cache=(unrelated,))
    key = state.dictionary[0].key
    requirement = ContributionRequirementV21(key)
    original_wrapper = _wrapper(state, (_exact_locator(state, key),))
    expected_hash = evidenced_state_hash_v21(original_wrapper)
    repointed_wrapper = _wrapper(
        state,
        (
            ContributionLocatorV21(
                contribution_key=key,
                support=ContributionSupportV21.EXACT,
                resident_id=unrelated.record_id,
                support_commitment=unrelated.commitment,
            ),
        ),
    )
    object.__setattr__(requirement, "contribution_key", state.exact_kernel[0].record_id)

    assert validate_evidenced_capsule_state_v21(repointed_wrapper) is None
    with pytest.raises(EvidencedStateMismatchV21):
        query_evidenced_v21(
            repointed_wrapper,
            (requirement,),
            expected_state_hash=expected_hash,
        )


def test_old_hash_rejects_a_coherent_exact_to_segment_repoint() -> None:
    segment = _segment("repointed-segment")
    state = _state(capsule_segments=(segment,))
    key = state.dictionary[0].key
    requirement = ContributionRequirementV21(key)
    original_wrapper = _wrapper(state, (_exact_locator(state, key),))
    expected_hash = evidenced_state_hash_v21(original_wrapper)
    repointed_wrapper = _wrapper(state, (_segment_locator(segment, key),))

    assert validate_evidenced_capsule_state_v21(repointed_wrapper) is None
    with pytest.raises(EvidencedStateMismatchV21):
        query_evidenced_v21(
            repointed_wrapper,
            (requirement,),
            expected_state_hash=expected_hash,
        )
