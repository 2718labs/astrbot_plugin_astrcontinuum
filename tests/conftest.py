from __future__ import annotations

import pytest

from crm_experiment.contracts import (
    AtomRole,
    AtomStatus,
    CapsuleState,
    LossPolicy,
    MatrixBundle,
    SemanticAtom,
)


def make_atom(
    atom_id: str,
    *,
    semantic_keys: tuple[str, ...] = ("context",),
    role: AtomRole = AtomRole.CONTEXT,
    text: str = "context",
    revision: int = 1,
    as_of: int = 1,
    exact: bool = False,
    core_required: bool = False,
    depends_on: tuple[str, ...] = (),
    merge_depth: int = 0,
    weight: float = 1.0,
) -> SemanticAtom:
    return SemanticAtom(
        atom_id=atom_id,
        semantic_keys=semantic_keys,
        role=role,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=revision,
        as_of=as_of,
        provenance=(f"source-{atom_id}",),
        exact=exact,
        depends_on=depends_on,
        weight=weight,
        core_required=core_required,
        merge_depth=merge_depth,
        covered_atom_ids=(atom_id,),
    )


@pytest.fixture
def base_state() -> CapsuleState:
    return CapsuleState(
        generation=1,
        high_water=1,
        accepted_budget=1024,
        kernel=(
            make_atom(
                "goal-v1",
                semantic_keys=("goal",),
                role=AtomRole.ROOT_GOAL,
                text="旧目标",
                revision=1,
                core_required=True,
            ),
            make_atom(
                "decision-v1",
                semantic_keys=("decision",),
                role=AtomRole.DECISION,
                text="旧决定",
                revision=1,
                core_required=True,
            ),
        ),
        body=(
            make_atom(
                "background-v1",
                semantic_keys=("background",),
                text="背景",
                revision=1,
            ),
        ),
        weight_version="theta-v1",
    )


@pytest.fixture
def delta_atom() -> SemanticAtom:
    return make_atom(
        "decision-v2",
        semantic_keys=("decision",),
        role=AtomRole.DECISION,
        text="新决定",
        revision=2,
        as_of=2,
        core_required=True,
    )


@pytest.fixture
def two_exact_atoms() -> tuple[SemanticAtom, SemanticAtom]:
    return (
        make_atom("exact-a", text="精确锚点 A", exact=True),
        make_atom("exact-b", text="精确锚点 B", exact=True),
    )


@pytest.fixture
def two_body_atoms() -> tuple[SemanticAtom, SemanticAtom]:
    return (
        make_atom("body-a", text="正文 A", semantic_keys=("a",)),
        make_atom("body-b", text="正文 B", semantic_keys=("b",), as_of=2),
    )


@pytest.fixture
def reference_matrix() -> MatrixBundle:
    return MatrixBundle(
        atom_ids=("a1", "a2", "a3", "a4", "a5"),
        candidate_ids=("c1", "c2", "c3", "c4"),
        features=((0.0,) * 6,) * 5,
        coverage=(
            (1, 1, 0, 0),
            (1, 1, 0, 0),
            (1, 0, 1, 0),
            (0, 0, 1, 1),
            (0, 0, 0, 1),
        ),
        redundancy=((0.0,) * 4,) * 4,
        conflicts=((0,) * 4,) * 4,
        dependencies=(
            (0, 0, 0, 0, 0),
            (0, 0, 0, 0, 0),
            (0, 0, 0, 0, 0),
            (0, 0, 1, 0, 0),
            (0, 0, 0, 0, 0),
        ),
        costs=(12, 7, 8, 7),
        risks=(0.02, 0.002, 0.001, 0.40),
        weights=(4.0, 5.0, 6.0, 3.0, 1.0),
        budget=15,
    )


@pytest.fixture
def default_policy() -> LossPolicy:
    return LossPolicy(gamma=50.0, rho=0.0, risk_ceiling=0.05, exact_threshold=12)
