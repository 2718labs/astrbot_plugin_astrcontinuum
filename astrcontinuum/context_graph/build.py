"""Deterministic request-local graph construction."""

from __future__ import annotations

from dataclasses import dataclass

from ..runtime.types import CandidateBlock, CandidateKind
from ..storage import RequestView
from .types import (
    ConstraintRow,
    ContextGraph,
    ContextGraphConfig,
    GraphCoordinate,
    QueryActivation,
    SparseRelation,
)


@dataclass(frozen=True, slots=True)
class BuiltContextGraph:
    """One request-local graph and its immutable activation."""

    graph: ContextGraph
    activation: QueryActivation


def _lexical_units(text: str) -> frozenset[str]:
    return frozenset(character for character in text.casefold() if character.isalnum())


def _lexical_score(query_units: frozenset[str], text: str) -> float:
    if not query_units:
        return 0.0
    candidate_units = _lexical_units(text)
    union = query_units | candidate_units
    return len(query_units & candidate_units) / len(union) if union else 0.0


def _relation_weight(left: CandidateBlock, right: CandidateBlock) -> float:
    weight = 0.0
    if set(left.source_event_ids) & set(right.source_event_ids):
        weight += 1.0
    if left.capsule_id is not None and left.capsule_id == right.capsule_id:
        weight += 0.75
    if (
        left.kind is CandidateKind.RAW_EVENT
        and right.kind is CandidateKind.RAW_EVENT
        and left.event_sequence is not None
        and right.event_sequence is not None
        and abs(left.event_sequence - right.event_sequence) == 1
    ):
        weight += 0.5
    if left.kind is right.kind:
        weight += 0.25
    if left.slot is right.slot:
        weight += 0.125
    return weight


def build_context_graph(
    view: RequestView,
    candidates: tuple[CandidateBlock, ...],
    current_input: str,
    *,
    config: ContextGraphConfig,
) -> BuiltContextGraph:
    """Build a bounded graph only from the supplied immutable request view."""

    if not isinstance(view, RequestView):
        raise TypeError("view must be a RequestView")
    if not isinstance(current_input, str):
        raise TypeError("current_input must be a string")
    if not isinstance(config, ContextGraphConfig):
        raise TypeError("config must be a ContextGraphConfig")
    materialized = tuple(candidates)
    if any(not isinstance(item, CandidateBlock) for item in materialized):
        raise TypeError("candidates must contain CandidateBlock values")
    block_ids = tuple(item.block_id for item in materialized)
    if len(set(block_ids)) != len(block_ids):
        raise ValueError("candidate block ids must be unique")
    if len(materialized) > config.max_coordinates:
        raise ValueError("GRAPH_CAPACITY_EXCEEDED")

    coordinates = tuple(
        GraphCoordinate(coordinate_id=item.block_id, candidate=item) for item in materialized
    )
    constraints = tuple(
        ConstraintRow.fix_one(f"required:{item.block_id}", item.block_id)
        for item in materialized
        if item.required
    )
    if len(constraints) > config.max_constraints:
        raise ValueError("GRAPH_CAPACITY_EXCEEDED")

    relations: list[SparseRelation] = []
    expanded_entries = 0
    for left_index, left in enumerate(materialized):
        for right in materialized[left_index + 1 :]:
            weight = _relation_weight(left, right)
            if weight <= 0.0:
                continue
            if (
                len(relations) >= config.max_relations
                or expanded_entries + 4 > config.max_relation_entries
            ):
                break
            relations.append(
                SparseRelation(
                    relation_id=f"relation:{left.block_id}:{right.block_id}",
                    terms=((left.block_id, 1.0), (right.block_id, -1.0)),
                    weight=weight,
                )
            )
            expanded_entries += 4
        if (
            len(relations) >= config.max_relations
            or expanded_entries + 4 > config.max_relation_entries
        ):
            break

    query_units = _lexical_units(current_input)
    scores = tuple((item.block_id, _lexical_score(query_units, item.text)) for item in materialized)
    retained = tuple(
        item.block_id
        for item, (_, score) in zip(materialized, scores)
        if item.required or score > 0.0
    )
    provenance = tuple(
        dict.fromkeys(event_id for item in materialized for event_id in item.source_event_ids)
    )
    graph = ContextGraph(
        graph_id=(
            f"request:{view.pointer_version}:{view.covered_event_end}:{view.high_water_mark}"
        ),
        coordinates=coordinates,
        relations=tuple(relations),
        constraints=constraints,
    )
    activation = QueryActivation(
        query_id=f"query:{view.pointer_version}:{view.high_water_mark}",
        scores=scores,
        retained_coordinate_ids=retained,
        provenance_event_ids=provenance,
    )
    return BuiltContextGraph(graph=graph, activation=activation)
