from crm_experiment.contracts import LossPolicy, MatrixBundle
from crm_experiment.optimizer import optimize


def test_reference_problem_prefers_low_risk_c2_c3(reference_matrix) -> None:
    selection = optimize(
        reference_matrix,
        LossPolicy(gamma=50.0, rho=0.0, risk_ceiling=0.05, exact_threshold=12),
    )

    assert selection.candidate_ids == ("c2", "c3")
    assert selection.byte_cost == 15
    assert selection.objective == 1.15
    assert selection.solver == "exact"


def test_optimizer_rejects_conflict_pairs() -> None:
    matrix = MatrixBundle(
        atom_ids=("old", "new"),
        candidate_ids=("old-current", "new-current"),
        features=((0.0,) * 6,) * 2,
        coverage=((1, 0), (0, 1)),
        redundancy=((1.0, 0.0), (0.0, 1.0)),
        conflicts=((0, 1), (1, 0)),
        dependencies=((0, 0), (0, 0)),
        costs=(1, 1),
        risks=(0.0, 0.0),
        weights=(5.0, 6.0),
        budget=2,
    )

    selection = optimize(
        matrix,
        LossPolicy(gamma=1.0, rho=0.0, risk_ceiling=1.0, exact_threshold=12),
    )

    assert not {"old-current", "new-current"} <= set(selection.candidate_ids)
    assert selection.candidate_ids == ("new-current",)


def test_optimizer_requires_dependency_closure() -> None:
    matrix = MatrixBundle(
        atom_ids=("parent", "child"),
        candidate_ids=("parent-candidate", "child-candidate"),
        features=((0.0,) * 6,) * 2,
        coverage=((1, 0), (0, 1)),
        redundancy=((1.0, 0.0), (0.0, 1.0)),
        conflicts=((0, 0), (0, 0)),
        dependencies=((0, 0), (1, 0)),
        costs=(1, 1),
        risks=(0.0, 0.0),
        weights=(1.0, 20.0),
        budget=1,
    )

    selection = optimize(
        matrix,
        LossPolicy(gamma=1.0, rho=0.0, risk_ceiling=1.0, exact_threshold=12),
    )

    assert "child-candidate" not in selection.candidate_ids


def test_optimizer_rejects_candidates_above_risk_ceiling() -> None:
    matrix = MatrixBundle(
        atom_ids=("a1",),
        candidate_ids=("risky",),
        features=((0.0,) * 6,),
        coverage=((1,),),
        redundancy=((1.0,),),
        conflicts=((0,),),
        dependencies=((0,),),
        costs=(1,),
        risks=(0.2,),
        weights=(10.0,),
        budget=1,
    )

    selection = optimize(
        matrix,
        LossPolicy(gamma=1.0, rho=0.0, risk_ceiling=0.1, exact_threshold=12),
    )

    assert selection.candidate_ids == ()
    assert selection.objective == 10.0


def test_greedy_branch_is_deterministic_and_respects_budget() -> None:
    matrix = MatrixBundle(
        atom_ids=("a1", "a2"),
        candidate_ids=("c1", "c2", "c3"),
        features=((0.0,) * 6,) * 2,
        coverage=((1, 0, 1), (0, 1, 1)),
        redundancy=(
            (1.0, 0.0, 0.5),
            (0.0, 1.0, 0.5),
            (0.5, 0.5, 1.0),
        ),
        conflicts=((0, 0, 0), (0, 0, 0), (0, 0, 0)),
        dependencies=((0, 0), (0, 0)),
        costs=(1, 1, 3),
        risks=(0.0, 0.0, 0.0),
        weights=(4.0, 3.0),
        budget=2,
    )
    policy = LossPolicy(
        gamma=1.0,
        rho=0.0,
        risk_ceiling=1.0,
        exact_threshold=2,
    )

    outputs = {optimize(matrix, policy) for _ in range(20)}

    assert len(outputs) == 1
    selection = outputs.pop()
    assert selection.candidate_ids == ("c1", "c2")
    assert selection.byte_cost == 2
    assert selection.solver == "greedy"


def test_optimizer_is_stable_across_repeated_calls(
    reference_matrix,
    default_policy,
) -> None:
    outputs = {optimize(reference_matrix, default_policy) for _ in range(20)}

    assert len(outputs) == 1
