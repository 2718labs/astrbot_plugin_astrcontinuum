from dataclasses import replace

import pytest

from crm_experiment.canonical import semantic_hash
from crm_experiment.contracts import OutcomeState, RecompositionRequest
from crm_experiment.kernel import derive_kernel_ceiling
from crm_experiment.recompose import recompose_capsule


def test_normal_recomposition_consumes_delta_and_releases_old_body(
    normal_request,
) -> None:
    result = recompose_capsule(normal_request)

    assert result.outcome is OutcomeState.NORMAL
    assert result.consumed_delta is True
    assert result.loss.continuity_break is False
    assert result.state.generation == normal_request.base_state.generation + 1
    assert "decision-v1" in result.loss.released_atom_ids
    assert "decision-v2" in result.loss.retained_atom_ids
    assert all(atom.atom_id != "decision-v1" for atom in result.state.kernel)


def test_budget_at_k_produces_kernel_only(kernel_only_request) -> None:
    result = recompose_capsule(kernel_only_request)

    assert result.outcome is OutcomeState.KERNEL_ONLY
    assert result.state.body == ()
    assert result.state.kernel
    assert result.loss.released_atom_ids == ("oversized-context",)


def test_budget_below_k_rejects_change_and_delta(blocked_request) -> None:
    result = recompose_capsule(blocked_request)

    assert result.outcome is OutcomeState.ADMISSION_BLOCKED
    assert result.state.kernel == blocked_request.base_state.kernel
    assert result.state.body == ()
    assert result.state.accepted_budget == blocked_request.base_state.accepted_budget
    assert result.state.high_water == blocked_request.base_state.high_water
    assert result.state.weight_version == blocked_request.base_state.weight_version
    assert result.consumed_delta is False
    assert "decision-v2" not in result.loss.released_atom_ids


def test_initial_budget_below_k_is_rejected(normal_request) -> None:
    request = replace(
        normal_request,
        base_state=None,
        byte_budget=derive_kernel_ceiling(normal_request.kernel_schema) - 1,
    )

    with pytest.raises(ValueError, match="initial budget is below continuity floor"):
        recompose_capsule(request)


def test_zero_delta_is_a_semantic_fixed_point(normal_request) -> None:
    first = recompose_capsule(normal_request)
    second_request = RecompositionRequest(
        base_state=first.state,
        delta=(),
        byte_budget=first.state.accepted_budget,
        kernel_schema=normal_request.kernel_schema,
        loss_policy=normal_request.loss_policy,
        weight_version=normal_request.weight_version,
    )

    second = recompose_capsule(second_request)

    assert semantic_hash(second.state) == semantic_hash(first.state)


def test_result_has_all_three_timing_stages(normal_request) -> None:
    result = recompose_capsule(normal_request)

    assert tuple(name for name, _ in result.stage_ns) == (
        "matrix",
        "optimizer",
        "gate",
    )
    assert all(duration >= 0 for _, duration in result.stage_ns)


def test_release_partition_is_complete(normal_request) -> None:
    result = recompose_capsule(normal_request)
    universe = {
        atom.atom_id
        for atom in (
            normal_request.base_state.kernel
            + normal_request.base_state.body
            + normal_request.delta
        )
    }
    partition = (
        set(result.loss.retained_atom_ids)
        | set(result.loss.merged_atom_ids)
        | set(result.loss.released_atom_ids)
    )

    assert partition == universe
    assert not (set(result.loss.retained_atom_ids) & set(result.loss.released_atom_ids))
