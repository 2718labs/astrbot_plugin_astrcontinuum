"""Tests for bounded query projections from frozen Capsule state."""

from __future__ import annotations

import inspect

from crm_experiment.canonical import utf8_bytes
from crm_experiment.contracts import (
    AtomRole,
    AtomStatus,
    CapsuleState,
    ProjectionResult,
    QuerySpec,
    SemanticAtom,
)
from crm_experiment.projection import UNKNOWN, project_query


def _atom(
    atom_id: str,
    *,
    role: AtomRole = AtomRole.ROOT_GOAL,
    semantic_key: str = "goal",
    text: str = "goal",
    revision: int = 1,
    covered_ids: tuple[str, ...] | None = None,
) -> SemanticAtom:
    return SemanticAtom(
        atom_id=atom_id,
        semantic_keys=(semantic_key,),
        role=role,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=revision,
        as_of=revision,
        provenance=(f"source-{atom_id}",),
        exact=False,
        depends_on=(),
        weight=1.0,
        core_required=False,
        merge_depth=0,
        covered_atom_ids=covered_ids if covered_ids is not None else (atom_id,),
    )


def _state(
    *,
    kernel: tuple[SemanticAtom, ...] = (),
    body: tuple[SemanticAtom, ...] = (),
) -> CapsuleState:
    return CapsuleState(
        generation=3,
        high_water=42,
        accepted_budget=1024,
        kernel=kernel,
        body=body,
        weight_version="theta-v1",
    )


def test_project_query_exposes_only_frozen_state_inputs() -> None:
    assert tuple(inspect.signature(project_query).parameters) == (
        "state",
        "query",
        "byte_budget",
    )


def test_project_query_selects_matching_atoms_by_revision_then_stable_id() -> None:
    state = _state(
        kernel=(
            _atom(
                "goal-v2",
                text="second revision",
                revision=2,
                covered_ids=("coverage-z", "coverage-shared"),
            ),
            _atom("focus-v9", role=AtomRole.CURRENT_FOCUS, revision=9),
        ),
        body=(
            _atom(
                "goal-b-v3",
                text="third revision B",
                revision=3,
                covered_ids=("coverage-b", "coverage-shared"),
            ),
            _atom(
                "goal-a-v3",
                text="third revision A",
                revision=3,
                covered_ids=("coverage-a",),
            ),
        ),
    )

    result = project_query(
        state,
        QuerySpec("goal-query", AtomRole.ROOT_GOAL, "goal"),
        byte_budget=256,
    )

    assert result == ProjectionResult(
        query_id="goal-query",
        text="third revision A\nthird revision B\nsecond revision",
        selected_atom_ids=("goal-a-v3", "goal-b-v3", "goal-v2"),
        selected_covered_ids=(
            "coverage-a",
            "coverage-b",
            "coverage-shared",
            "coverage-z",
        ),
        byte_cost=utf8_bytes("third revision A\nthird revision B\nsecond revision"),
        supported=True,
    )


def test_project_query_counts_utf8_bytes_and_newline_at_exact_boundary() -> None:
    state = _state(
        kernel=(
            _atom("goal-new", text="甲", revision=2),
            _atom("goal-old", text="乙", revision=1),
        )
    )

    result = project_query(
        state,
        QuerySpec("goal-query", AtomRole.ROOT_GOAL, "goal"),
        byte_budget=7,
    )

    assert result.text == "甲\n乙"
    assert result.selected_atom_ids == ("goal-new", "goal-old")
    assert result.byte_cost == 7


def test_project_query_returns_explicit_unknown_when_no_atom_matches() -> None:
    state = _state(kernel=(_atom("focus", role=AtomRole.CURRENT_FOCUS),))

    result = project_query(
        state,
        QuerySpec("goal-query", AtomRole.ROOT_GOAL, "goal"),
        byte_budget=256,
    )

    assert result == ProjectionResult(
        query_id="goal-query",
        text=UNKNOWN,
        selected_atom_ids=(),
        selected_covered_ids=(),
        byte_cost=utf8_bytes(UNKNOWN),
        supported=False,
    )


def test_project_query_returns_explicit_unknown_when_no_match_fits_budget() -> None:
    state = _state(kernel=(_atom("goal", text="甲", revision=1),))

    result = project_query(
        state,
        QuerySpec("goal-query", AtomRole.ROOT_GOAL, "goal"),
        byte_budget=2,
    )

    assert result.supported is False
    assert result.text == UNKNOWN
    assert result.selected_atom_ids == ()
