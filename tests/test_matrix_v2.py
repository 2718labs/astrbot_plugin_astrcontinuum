"""Sparse logical-source candidate construction for CRM v2."""

from __future__ import annotations

from dataclasses import replace

from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    CandidatePolicyV2,
    DeltaEnvelopeV2,
    EncodingModeV2,
    LogicalAtomV2,
    LogicalResolutionV2,
    PackingPolicyV2,
    SourceWeightV2,
    WeightPolicyV2,
)
from crm_experiment.logical_v2 import resolve_latest_v2
from crm_experiment.matrix_v2 import build_source_matrix_v2


def _atom(
    index: int,
    *,
    role: AtomRole = AtomRole.CONTEXT,
    exact: bool = False,
    core: bool = False,
    depends_on: tuple[str, ...] = (),
) -> LogicalAtomV2:
    return LogicalAtomV2.create(
        source_label=f"source-{index}",
        semantic_keys=(f"topic:{index:03d}",),
        role=role,
        text=f"laboratory shared prefix :: observation {index:03d}",
        status=AtomStatus.ACTIVE,
        revision=1,
        as_of=1,
        provenance=(f"fixture:{index:03d}",),
        exact=exact,
        depends_on=depends_on,
        core_required=core,
    )


def _resolution(*atoms: LogicalAtomV2) -> LogicalResolutionV2:
    return resolve_latest_v2(
        None,
        DeltaEnvelopeV2(0, 1, tuple(sorted(atoms, key=lambda atom: atom.source_id))),
        key_registry_limit=256,
        max_semantic_key_bytes=128,
    )


def _weights(resolution: LogicalResolutionV2) -> WeightPolicyV2:
    active_ids = sorted(
        {
            winner.source_id
            for winner in resolution.frontier
            if winner.status is AtomStatus.ACTIVE
        }
    )
    return WeightPolicyV2.create(
        version="matrix-fixture-v1",
        source_weights=tuple(
            SourceWeightV2(source_id, 1.0) for source_id in active_ids
        ),
    )


def _packing(*atoms: LogicalAtomV2) -> PackingPolicyV2:
    return PackingPolicyV2(
        codec="dmc1-lcp-lcs-v1",
        immutable_source_ids=tuple(sorted(atom.source_id for atom in atoms)),
        max_records_per_block=8,
        max_decoded_block_bytes=65_536,
    )


def test_n72_uses_linear_bounded_pack_proposals_without_dense_matrices() -> None:
    atoms = (
        _atom(0, role=AtomRole.ROOT_GOAL, core=True),
        *tuple(_atom(index) for index in range(1, 72)),
    )
    resolution = _resolution(*atoms)
    policy = CandidatePolicyV2(max_pack_neighbors=3)

    matrix = build_source_matrix_v2(
        resolution,
        _weights(resolution),
        _packing(*atoms),
        policy,
    )

    assert len(matrix.rows) == 72
    assert matrix.active_source_ids == tuple(sorted(atom.source_id for atom in atoms))
    assert matrix.selectable_source_ids == matrix.active_source_ids
    assert len(matrix.pack_proposals) == 207
    assert matrix.pack_proposal_count == 207
    assert matrix.total_candidate_count == 279
    assert all(
        proposal.left_source_id < proposal.right_source_id
        for proposal in matrix.pack_proposals
    )
    assert not hasattr(matrix, "redundancy_matrix")
    assert not hasattr(matrix, "conflict_matrix")


def test_n77_released_payload_rows_remain_auditable_but_not_selectable() -> None:
    atoms = tuple(_atom(index) for index in range(77))
    complete = _resolution(*atoms)
    resident_ids = {atom.source_id for atom in atoms[:70]}
    partial = replace(
        complete,
        active_records=tuple(
            record
            for record in complete.active_records
            if record.atom.source_id in resident_ids
        ),
    )

    matrix = build_source_matrix_v2(
        partial,
        _weights(complete),
        _packing(*atoms),
        CandidatePolicyV2(max_pack_neighbors=3),
    )

    assert len(matrix.rows) == 77
    assert len(matrix.selectable_source_ids) == 70
    assert len(matrix.unavailable_source_ids) == 7
    assert set(matrix.selectable_source_ids) == resident_ids
    assert not set(matrix.unavailable_source_ids) & set(matrix.selectable_source_ids)
    assert all(
        proposal.left_source_id in resident_ids
        and proposal.right_source_id in resident_ids
        for proposal in matrix.pack_proposals
    )
    assert len(matrix.pack_proposals) == 204
    assert matrix.total_candidate_count == 274


def test_direct_only_ablation_changes_layout_proposals_not_logical_inputs() -> None:
    atoms = tuple(_atom(index) for index in range(8))
    resolution = _resolution(*atoms)
    weights = _weights(resolution)
    packing = _packing(*atoms)
    enabled_policy = CandidatePolicyV2(
        max_pack_neighbors=2, encoding_mode=EncodingModeV2.DMC1
    )
    direct_policy = replace(enabled_policy, encoding_mode=EncodingModeV2.DIRECT_ONLY)

    enabled = build_source_matrix_v2(resolution, weights, packing, enabled_policy)
    direct = build_source_matrix_v2(resolution, weights, packing, direct_policy)

    assert enabled.rows == direct.rows
    assert enabled.dependencies == direct.dependencies
    assert enabled.conflicts == direct.conflicts
    assert enabled.pack_proposals
    assert direct.pack_proposals == ()


def test_dependencies_are_sparse_source_edges_and_core_rows_are_mandatory() -> None:
    dependency = _atom(1)
    dependent = _atom(2, depends_on=(dependency.source_id,))
    core = _atom(3, role=AtomRole.ROOT_GOAL, core=True)
    resolution = _resolution(dependency, dependent, core)

    matrix = build_source_matrix_v2(
        resolution,
        _weights(resolution),
        _packing(dependency, dependent),
        CandidatePolicyV2(max_pack_neighbors=2),
    )

    assert tuple((edge.source_id, edge.target_id) for edge in matrix.dependencies) == (
        (dependent.source_id, dependency.source_id),
    )
    assert matrix.conflicts == ()
    row_by_id = {row.source_id: row for row in matrix.rows}
    assert row_by_id[core.source_id].mandatory
    assert row_by_id[core.source_id].available
    assert not row_by_id[dependent.source_id].pack_eligible


def test_pack_proposals_only_reference_policy_eligible_sources() -> None:
    eligible_a = _atom(10)
    eligible_b = _atom(11)
    exact = _atom(12, exact=True)
    mutable = _atom(13)
    resolution = _resolution(eligible_a, eligible_b, exact, mutable)
    packing = _packing(eligible_a, eligible_b, exact)

    matrix = build_source_matrix_v2(
        resolution,
        _weights(resolution),
        packing,
        CandidatePolicyV2(max_pack_neighbors=4),
    )

    eligible_ids = {eligible_a.source_id, eligible_b.source_id}
    assert matrix.pack_proposals
    assert all(
        {proposal.left_source_id, proposal.right_source_id}.issubset(eligible_ids)
        for proposal in matrix.pack_proposals
    )


def test_pack_proposal_global_cap_is_deterministic_and_exact() -> None:
    atoms = tuple(_atom(index) for index in range(20))
    resolution = _resolution(*atoms)
    policy = CandidatePolicyV2(
        max_pack_neighbors=4,
        max_pack_proposals=10,
    )

    first = build_source_matrix_v2(
        resolution, _weights(resolution), _packing(*atoms), policy
    )
    second = build_source_matrix_v2(
        resolution, _weights(resolution), _packing(*reversed(atoms)), policy
    )

    assert first.pack_proposal_count == 10
    assert first.total_candidate_count == 30
    assert first.pack_proposals == second.pack_proposals
