"""Tests for the frozen protocol and offline baseline isolation."""

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from crm_experiment.baselines import (
    ARM_ORDER,
    LegacyCapsuleBaseline,
    NoKernelAblation,
    NoProjectionAblation,
    RecursiveSummaryBaseline,
)
from crm_experiment.canonical import canonical_json, semantic_hash, utf8_bytes
from crm_experiment.contracts import (
    AtomRole,
    LossPolicy,
    QuerySpec,
    RecompositionRequest,
)
from crm_experiment.kernel import (
    default_kernel_schema,
    derive_kernel_ceiling,
    select_kernel,
)
from crm_experiment.protocol import (
    generate_protocol,
    load_protocol_config,
    no_kernel_overflow_fixture,
)
from crm_experiment.recompose import recompose_capsule

CONFIG_PATH = Path("config/protocol-v1.json")


def test_protocol_has_frozen_denominator_and_query_order() -> None:
    config = load_protocol_config(CONFIG_PATH)
    bundle = generate_protocol(config)

    assert len(bundle.streams) == 24
    assert all(len(stream.generations) == 12 for stream in bundle.streams)
    assert (
        sum(
            len(generation.queries)
            for stream in bundle.streams
            for generation in stream.generations
        )
        == 1728
    )
    expected_roles = (
        AtomRole.ROOT_GOAL,
        AtomRole.DECISION,
        AtomRole.HARD_CONSTRAINT,
        AtomRole.EXACT_ANCHOR,
        AtomRole.CONTEXT,
        AtomRole.OPEN_LOOP,
    )
    assert all(
        tuple(query.role for query in generation.queries) == expected_roles
        for stream in bundle.streams
        for generation in stream.generations
    )


def test_protocol_generation_is_byte_identical_and_sealed() -> None:
    config = load_protocol_config(CONFIG_PATH)
    first = generate_protocol(config)
    second = generate_protocol(config)

    assert canonical_json(first) == canonical_json(second)
    assert len(first.public_queries) == 1728
    assert len(first.sealed_gold) == 1728
    assert all(
        not hasattr(query, "required_atom_ids") for query in first.public_queries
    )
    assert all(gold.required_atom_ids for gold in first.sealed_gold)
    assert any(gold.forbidden_atom_ids for gold in first.sealed_gold)
    assert all(
        generation.events
        for stream in first.streams
        for generation in stream.generations
    )


def test_protocol_config_matches_preregistered_values() -> None:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config = load_protocol_config(CONFIG_PATH)

    assert raw["stream_count"] == 24
    assert raw["generation_count"] == 12
    assert raw["queries_per_generation"] == 6
    assert config.budget_multipliers == (8.0, 4.0, 2.0, 1.5, 1.0)
    assert config.main_budget_multiplier == 2.0
    assert config.query_injection_bytes == 512
    assert config.weights == LossPolicy(
        gamma=50.0,
        rho=0.1,
        risk_ceiling=0.05,
        exact_threshold=12,
    )


def test_gold_critical_roles_fit_kernel_schema() -> None:
    config = load_protocol_config(CONFIG_PATH)
    bundle = generate_protocol(config)
    schema = default_kernel_schema()
    capacities = {slot.role: slot.max_items for slot in schema.slots}

    for gold in bundle.sealed_gold:
        if not gold.core_query:
            continue
        query = next(
            item for item in bundle.public_queries if item.query_id == gold.query_id
        )
        assert capacities[query.role] >= 1


def test_baseline_advance_apis_never_accept_query_or_gold() -> None:
    recompose_parameters = inspect.signature(recompose_capsule).parameters
    assert "query" not in recompose_parameters
    assert "gold" not in recompose_parameters
    for baseline in (
        LegacyCapsuleBaseline,
        RecursiveSummaryBaseline,
        NoProjectionAblation,
        NoKernelAblation,
    ):
        parameters = inspect.signature(baseline.advance).parameters
        assert "query" not in parameters
        assert "gold" not in parameters


def test_baselines_share_arm_order_and_budgeted_utf8_state(
    base_state,
    delta_atom,
) -> None:
    schema = default_kernel_schema()
    policy = LossPolicy(gamma=50.0, rho=0.1, risk_ceiling=0.05, exact_threshold=12)
    budget = 8 * derive_kernel_ceiling(schema)

    legacy = LegacyCapsuleBaseline().advance(
        base_state,
        (delta_atom,),
        budget,
        schema,
        policy,
        "theta0",
    )
    no_projection = NoProjectionAblation().advance(
        base_state,
        (delta_atom,),
        budget,
        schema,
        policy,
        "theta0",
    )
    no_kernel = NoKernelAblation().advance(
        base_state,
        (delta_atom,),
        budget,
        schema,
        policy,
        "theta0",
    )
    summary = RecursiveSummaryBaseline().advance(
        "",
        (delta_atom,),
        byte_budget=budget,
    )

    assert ARM_ORDER == (
        "crm",
        "legacy_capsule",
        "recursive_summary",
        "crm_greedy",
        "crm_no_projection",
        "crm_no_kernel",
    )
    assert utf8_bytes(canonical_json(legacy)) <= budget
    assert utf8_bytes(canonical_json(no_projection)) <= budget
    assert utf8_bytes(canonical_json(no_kernel)) <= budget
    assert utf8_bytes(summary) <= budget
    assert legacy.kernel
    assert no_projection.kernel


def test_no_projection_reuses_the_frozen_crm_state(base_state, delta_atom) -> None:
    schema = default_kernel_schema()
    policy = LossPolicy(gamma=50.0, rho=0.1, risk_ceiling=0.05, exact_threshold=12)
    budget = 8 * derive_kernel_ceiling(schema)
    expected = recompose_capsule(
        RecompositionRequest(
            base_state=base_state,
            delta=(delta_atom,),
            byte_budget=budget,
            kernel_schema=schema,
            loss_policy=policy,
            weight_version="theta0",
        )
    ).state

    actual = NoProjectionAblation().advance(
        base_state,
        (delta_atom,),
        budget,
        schema,
        policy,
        "theta0",
    )

    assert semantic_hash(actual) == semantic_hash(expected)


def test_no_projection_uses_a_query_blind_stable_prefix(base_state) -> None:
    arm = NoProjectionAblation()
    first = arm.project(base_state, query_id="query-a", byte_budget=1024)
    second = arm.project(base_state, query_id="query-b", byte_budget=1024)

    assert tuple(inspect.signature(arm.project).parameters) == (
        "state",
        "query_id",
        "byte_budget",
    )
    assert first.text == second.text
    assert first.selected_atom_ids == second.selected_atom_ids
    assert first.selected_covered_ids == second.selected_covered_ids


def test_no_projection_stops_at_the_first_unfitting_block(base_state) -> None:
    oversized = replace(base_state.kernel[0], text="x" * 2000)
    state = replace(base_state, kernel=(oversized,))

    result = NoProjectionAblation().project(
        state,
        query_id="query",
        byte_budget=512,
    )

    assert result.supported is False
    assert result.selected_atom_ids == ()


@pytest.mark.parametrize("baseline", [LegacyCapsuleBaseline, NoProjectionAblation])
def test_non_kernel_ablations_do_not_emit_empty_state(base_state, baseline) -> None:
    schema = default_kernel_schema()
    policy = LossPolicy(gamma=50.0, rho=0.1, risk_ceiling=0.05, exact_threshold=12)
    state = baseline().advance(
        base_state,
        (),
        8 * derive_kernel_ceiling(schema),
        schema,
        policy,
        "theta0",
    )
    assert state.kernel or state.body


def test_recursive_summary_replaces_same_key_without_query_inputs(delta_atom) -> None:
    summary = RecursiveSummaryBaseline()
    first = summary.advance("", (delta_atom,), byte_budget=512)
    replacement = replace(
        delta_atom,
        atom_id="decision-v3",
        revision=3,
        text="最新决定",
    )
    second = summary.advance(first, (replacement,), byte_budget=512)

    assert "decision-v2" not in second
    assert "decision-v3" in second
    assert len(second.splitlines()) == 1


def test_recursive_summary_retains_unrelated_keys_across_rounds(delta_atom) -> None:
    summary = RecursiveSummaryBaseline()
    context = replace(
        delta_atom,
        atom_id="context-v1",
        semantic_keys=("background",),
        role=AtomRole.CONTEXT,
        text="background",
    )
    first = summary.advance("", (delta_atom, context), byte_budget=512)
    replacement = replace(
        delta_atom,
        atom_id="decision-v3",
        revision=3,
        text="latest decision",
    )

    second = summary.advance(first, (replacement,), byte_budget=512)

    assert "context-v1" in second
    assert "decision-v3" in second


def test_recursive_summary_projects_only_matching_lines(delta_atom) -> None:
    summary = RecursiveSummaryBaseline()
    context = replace(
        delta_atom,
        atom_id="context-v1",
        semantic_keys=("background",),
        role=AtomRole.CONTEXT,
        text="background",
    )
    state = summary.advance("", (delta_atom, context), byte_budget=512)

    result = summary.project(
        state,
        QuerySpec("decision-query", AtomRole.DECISION, "decision"),
        byte_budget=512,
    )

    assert result.supported is True
    assert result.selected_atom_ids == ("decision-v2",)
    assert "background" not in result.text


def test_no_kernel_overflow_fixture_is_not_a_main_cluster() -> None:
    config = load_protocol_config(CONFIG_PATH)
    bundle = generate_protocol(config)
    overflow = no_kernel_overflow_fixture()

    assert overflow.stream_id not in {stream.stream_id for stream in bundle.streams}
    assert len(overflow.generations) == 1
    assert (
        select_kernel(
            overflow.generations[0].events,
            default_kernel_schema(),
        ).valid
        is False
    )
