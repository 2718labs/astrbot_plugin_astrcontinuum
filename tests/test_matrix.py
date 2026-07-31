from crm_experiment.canonical import utf8_bytes
from crm_experiment.matrix import atomize, build_matrix, compose_candidates


def test_atomize_replaces_all_blocks_touching_an_updated_key(
    base_state, delta_atom
) -> None:
    atoms = atomize(base_state, (delta_atom,))

    assert [atom.revision for atom in atoms if "decision" in atom.semantic_keys] == [2]
    assert {atom.atom_id for atom in atoms} == {
        "background-v1",
        "decision-v2",
        "goal-v1",
    }


def test_exact_atoms_are_never_merge_candidates(two_exact_atoms) -> None:
    candidates = compose_candidates(two_exact_atoms)

    assert all(len(candidate.covers) == 1 for candidate in candidates)


def test_matrix_dimensions_match_atoms_and_candidates(two_body_atoms) -> None:
    candidates = compose_candidates(two_body_atoms)
    matrix = build_matrix(two_body_atoms, candidates, budget=1024)

    assert len(matrix.coverage) == len(two_body_atoms)
    assert all(len(row) == len(candidates) for row in matrix.coverage)
    assert len(matrix.features) == len(two_body_atoms)
    assert all(len(row) == 6 for row in matrix.features)


def test_direct_candidate_cost_is_exact_utf8_size(two_body_atoms) -> None:
    candidates = compose_candidates((two_body_atoms[0],))

    assert candidates[0].byte_cost == utf8_bytes(two_body_atoms[0].text)
    assert candidates[0].error_risk == 0.0


def test_non_exact_same_role_atoms_get_one_stable_pair_merge(two_body_atoms) -> None:
    candidates = compose_candidates(two_body_atoms)
    merged = [candidate for candidate in candidates if len(candidate.covers) == 2]

    assert len(merged) == 1
    assert merged[0].text == "正文 A; 正文 B"
    assert merged[0].error_risk == 0.001
    assert merged[0].merge_depth == 1
    assert (
        merged[0].candidate_id
        == compose_candidates(tuple(reversed(two_body_atoms)))[-1].candidate_id
    )
