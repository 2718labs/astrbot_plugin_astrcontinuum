"""Sparse logical-source construction for bounded CRM v2 optimization."""

from __future__ import annotations

from collections import defaultdict

from crm_experiment.contracts import AtomRole, AtomStatus
from crm_experiment.contracts_v2 import (
    CandidatePolicyV2,
    LogicalResolutionV2,
    PackingPolicyV2,
    PackProposalV2,
    SourceMatrixV2,
    SourcePlacementV2,
    SourceRowV2,
    SparseEdgeV2,
    WeightPolicyV2,
)


def build_source_matrix_v2(
    resolution: LogicalResolutionV2,
    weight_policy: WeightPolicyV2,
    packing_policy: PackingPolicyV2,
    candidate_policy: CandidatePolicyV2,
) -> SourceMatrixV2:
    """Build O(sources + edges + sources*neighbors) optimizer inputs."""
    weight_policy.__post_init__()
    packing_policy.__post_init__()
    candidate_policy.__post_init__()

    active_keys_by_source: dict[str, list[str]] = defaultdict(list)
    for winner in resolution.frontier:
        if winner.status is AtomStatus.ACTIVE:
            active_keys_by_source[winner.source_id].append(winner.semantic_key)
    active_source_ids = set(active_keys_by_source)
    receipt_by_source = {receipt.source_id: receipt for receipt in resolution.receipts}
    if not active_source_ids.issubset(receipt_by_source):
        raise ValueError("active source is missing its receipt")

    weight_by_source = {
        source_weight.source_id: source_weight.weight
        for source_weight in weight_policy.source_weights
    }
    if set(weight_by_source) != active_source_ids:
        raise ValueError("weight policy must exactly cover active sources")
    if not set(packing_policy.immutable_source_ids).issubset(active_source_ids):
        raise ValueError("packing policy source is outside active universe")

    record_by_source = {
        record.atom.source_id: record for record in resolution.active_records
    }
    if not set(record_by_source).issubset(active_source_ids):
        raise ValueError("available payload is outside active universe")
    immutable_ids = set(packing_policy.immutable_source_ids)
    rows: list[SourceRowV2] = []
    for source_id in sorted(active_source_ids):
        receipt = receipt_by_source[source_id]
        record = record_by_source.get(source_id)
        available = record is not None
        if (
            available
            and tuple(sorted(active_keys_by_source[source_id])) != record.active_keys
        ):
            raise ValueError("available payload active_keys mismatch the frontier")
        rows.append(
            SourceRowV2(
                source_id=source_id,
                receipt=receipt,
                active_keys=tuple(sorted(active_keys_by_source[source_id])),
                record=record,
                weight=weight_by_source[source_id],
                placement=(
                    SourcePlacementV2.KERNEL
                    if receipt.core_required
                    else (
                        SourcePlacementV2.BODY
                        if available
                        else SourcePlacementV2.RELEASED_CONTROL
                    )
                ),
                pack_eligible=(
                    available
                    and source_id in immutable_ids
                    and receipt.role is AtomRole.CONTEXT
                    and not receipt.exact
                    and not receipt.core_required
                    and not receipt.depends_on
                ),
            )
        )

    dependencies = tuple(
        sorted(
            (
                SparseEdgeV2(row.source_id, dependency)
                for row in rows
                for dependency in row.depends_on
            ),
            key=lambda edge: (edge.source_id, edge.target_id),
        )
    )

    # A well-formed latest frontier has one owner per key. Keep the sparse
    # conflict representation explicit so forged or future inputs cannot force
    # a dense candidate-squared allocation.
    owners_by_key: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for key in row.active_keys:
            owners_by_key[key].append(row.source_id)
    conflict_pairs: set[tuple[str, str]] = set()
    for owners in owners_by_key.values():
        ordered = sorted(set(owners))
        for left_index, left_source_id in enumerate(ordered):
            conflict_pairs.update(
                (left_source_id, right_source_id)
                for right_source_id in ordered[left_index + 1 :]
            )
    conflicts = tuple(
        SparseEdgeV2(left, right) for left, right in sorted(conflict_pairs)
    )

    proposals: list[PackProposalV2] = []
    if candidate_policy.enable_packing:
        eligible_ids = [row.source_id for row in rows if row.pack_eligible]
        for index, left_source_id in enumerate(eligible_ids):
            stop = min(
                len(eligible_ids), index + candidate_policy.max_pack_neighbors + 1
            )
            for right_index in range(index + 1, stop):
                if len(proposals) >= candidate_policy.max_pack_proposals:
                    break
                proposals.append(
                    PackProposalV2(
                        left_source_id=left_source_id,
                        right_source_id=eligible_ids[right_index],
                        neighbor_distance=right_index - index,
                    )
                )
            if len(proposals) >= candidate_policy.max_pack_proposals:
                break

    return SourceMatrixV2(
        rows=tuple(rows),
        dependencies=dependencies,
        conflicts=conflicts,
        pack_proposals=tuple(proposals),
    )
