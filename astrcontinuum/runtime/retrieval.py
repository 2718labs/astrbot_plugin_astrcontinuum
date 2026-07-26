from __future__ import annotations

from collections.abc import Iterable

from ..domain import AnchorStatus, CapsuleLevel, ContextCapsuleEnvelope, SemanticStatus
from ..storage import RequestView
from .types import (
    RUNTIME_SLOT_PRIORITY,
    CandidateBlock,
    CandidateKind,
    RetrievalConfig,
    RuntimeSlot,
)

_SLOT_RANK = {slot: rank for rank, slot in enumerate(RUNTIME_SLOT_PRIORITY)}


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _normalized_query(query: str, config: RetrievalConfig) -> str:
    bounded = query[: config.max_query_characters]
    return " ".join(bounded.casefold().split())


def _lexical_fraction(query: str, text: str, config: RetrievalConfig) -> float:
    if not query:
        return 0.0
    candidate = text[: config.max_lexical_characters_per_candidate].casefold()
    if query in candidate:
        return 1.0
    terms = tuple(dict.fromkeys(query.split()))
    if not terms:
        return 0.0
    return sum(term in candidate for term in terms) / len(terms)


def _score(
    text: str,
    *,
    base_weight: float,
    query: str,
    config: RetrievalConfig,
) -> float:
    return base_weight + config.lexical_weight * _lexical_fraction(query, text, config)


def _reason(text: str, *, query: str, config: RetrievalConfig) -> str:
    if _lexical_fraction(query, text, config) > 0:
        return "LEXICAL_MATCH"
    return "STRUCTURED_FALLBACK"


def _decision_text(
    capsule: ContextCapsuleEnvelope,
    decision_index: int,
    config: RetrievalConfig,
) -> str:
    decision = capsule.decisions[decision_index]
    alternatives = decision.alternatives[: config.max_items_per_field]
    supersedes = decision.supersedes[: config.max_items_per_field]
    dependencies = capsule.dependencies[: config.max_items_per_field]
    dependency_text = " | ".join(
        f"{item.dependency_id}:{item.kind}->{item.target_id}" for item in dependencies
    )
    return "\n".join(
        (
            f"Decision: {decision.text}",
            f"Rationale: {decision.rationale}",
            f"Alternatives: {' | '.join(alternatives)}",
            f"Supersedes: {' | '.join(supersedes)}",
            f"Rejected because: {decision.rejected_because}",
            f"Dependencies: {dependency_text}",
        )
    )


def _task_text(capsule: ContextCapsuleEnvelope, config: RetrievalConfig) -> str:
    sections: list[str] = []
    structured = (
        ("Progress", capsule.progress),
        ("Open loops", capsule.open_loops),
        ("Preferences", capsule.preferences),
        ("Emotional context", capsule.emotional_context),
    )
    for label, values in structured:
        texts = tuple(item.text for item in values[: config.max_items_per_field])
        if texts:
            sections.append(f"{label}: {' | '.join(texts)}")
    return "\n".join(sections)


def _raw_event_text(event: object) -> str:
    event_type = getattr(getattr(event, "event_type", None), "value", "UNKNOWN_EVENT")
    role = getattr(getattr(event, "role", None), "value", "UNKNOWN_ROLE")
    event_id = getattr(event, "event_id", "unknown-event")
    content = getattr(event, "content", "")
    return f"[{event_type}/{role} {event_id}]\n{content}"


def _candidate_sort_key(candidate: CandidateBlock) -> tuple[int, float, int, str]:
    slot_rank = _SLOT_RANK[candidate.slot]
    if candidate.slot == RuntimeSlot.RAW_DELTA:
        return (slot_rank, 0.0, candidate.event_sequence or 0, candidate.block_id)
    return (slot_rank, -candidate.score, 0, candidate.block_id)


def select_candidates(
    view: RequestView,
    query: str,
    config: RetrievalConfig,
) -> tuple[CandidateBlock, ...]:
    """Select a bounded deterministic fallback from one immutable request view."""

    if not isinstance(query, str):
        raise TypeError("query must be a string")
    normalized_query = _normalized_query(query, config)
    capsules = tuple(membership.capsule for membership in view.memberships[: config.max_capsules])
    selected: list[CandidateBlock] = []

    def add(candidate: CandidateBlock) -> bool:
        if len(selected) >= config.max_candidates:
            return False
        selected.append(candidate)
        return True

    for capsule in capsules:
        for claim in capsule.goals[: config.max_items_per_field]:
            if claim.status != SemanticStatus.ACTIVE:
                continue
            if not add(
                CandidateBlock(
                    block_id=f"goal:{capsule.capsule_id}:{claim.claim_id}",
                    slot=RuntimeSlot.ACTIVE_GOAL,
                    kind=CandidateKind.GOAL,
                    text=claim.text,
                    source_event_ids=claim.source_event_ids,
                    score=_score(
                        claim.text,
                        base_weight=config.goal_weight,
                        query=normalized_query,
                        config=config,
                    ),
                    reason="ACTIVE_REQUIRED",
                    required=True,
                    capsule_id=capsule.capsule_id,
                    event_sequence=None,
                )
            ):
                break
        for claim in capsule.constraints[: config.max_items_per_field]:
            if claim.status != SemanticStatus.ACTIVE:
                continue
            if not add(
                CandidateBlock(
                    block_id=f"constraint:{capsule.capsule_id}:{claim.claim_id}",
                    slot=RuntimeSlot.HARD_CONSTRAINT,
                    kind=CandidateKind.CONSTRAINT,
                    text=claim.text,
                    source_event_ids=claim.source_event_ids,
                    score=_score(
                        claim.text,
                        base_weight=config.constraint_weight,
                        query=normalized_query,
                        config=config,
                    ),
                    reason="ACTIVE_REQUIRED",
                    required=True,
                    capsule_id=capsule.capsule_id,
                    event_sequence=None,
                )
            ):
                break

    for item in view.delta[-config.max_delta_events :] if config.max_delta_events else ():
        raw_text = _raw_event_text(item)
        if not add(
            CandidateBlock(
                block_id=f"event:{item.event_id}",
                slot=RuntimeSlot.RAW_DELTA,
                kind=CandidateKind.RAW_EVENT,
                text=raw_text,
                source_event_ids=(item.event_id,),
                score=_score(
                    raw_text,
                    base_weight=config.raw_delta_weight,
                    query=normalized_query,
                    config=config,
                ),
                reason="UNCOMPACTED_DELTA",
                required=True,
                capsule_id=None,
                event_sequence=item.sequence,
            )
        ):
            break

    for capsule in capsules:
        for anchor in capsule.exact_anchors[: config.max_items_per_field]:
            if anchor.status != AnchorStatus.ACTIVE:
                continue
            if not add(
                CandidateBlock(
                    block_id=f"anchor:{capsule.capsule_id}:{anchor.anchor_id}",
                    slot=RuntimeSlot.EXACT_ANCHOR,
                    kind=CandidateKind.EXACT_ANCHOR,
                    text=anchor.exact_text,
                    source_event_ids=anchor.source_event_ids,
                    score=_score(
                        anchor.exact_text,
                        base_weight=config.exact_anchor_weight * anchor.importance,
                        query=normalized_query,
                        config=config,
                    ),
                    reason="ACTIVE_EXACT_ANCHOR",
                    required=True,
                    capsule_id=capsule.capsule_id,
                    event_sequence=None,
                )
            ):
                break

    for capsule in capsules:
        if capsule.level != CapsuleLevel.TASK:
            continue
        text = _task_text(capsule, config)
        if not text:
            continue
        if not add(
            CandidateBlock(
                block_id=f"task:{capsule.capsule_id}",
                slot=RuntimeSlot.ACTIVE_TASK,
                kind=CandidateKind.TASK_CONTEXT,
                text=text,
                source_event_ids=capsule.source_event_ids,
                score=_score(
                    text,
                    base_weight=config.task_weight,
                    query=normalized_query,
                    config=config,
                ),
                reason=_reason(text, query=normalized_query, config=config),
                required=False,
                capsule_id=capsule.capsule_id,
                event_sequence=None,
            )
        ):
            break

    for capsule in capsules:
        dependencies = capsule.dependencies[: config.max_items_per_field]
        for index, decision in enumerate(capsule.decisions[: config.max_items_per_field]):
            text = _decision_text(capsule, index, config)
            source_ids = _unique(
                (
                    *decision.source_event_ids,
                    *(source for item in dependencies for source in item.source_event_ids),
                )
            )
            if not add(
                CandidateBlock(
                    block_id=f"decision:{capsule.capsule_id}:{decision.decision_id}",
                    slot=RuntimeSlot.RELEVANT_EVIDENCE,
                    kind=CandidateKind.DECISION,
                    text=text,
                    source_event_ids=source_ids,
                    score=_score(
                        text,
                        base_weight=config.decision_weight,
                        query=normalized_query,
                        config=config,
                    ),
                    reason=_reason(text, query=normalized_query, config=config),
                    required=False,
                    capsule_id=capsule.capsule_id,
                    event_sequence=None,
                )
            ):
                break
        for entity in capsule.entities[: config.max_items_per_field]:
            text = "\n".join(
                (
                    f"Entity: {entity.canonical_name}",
                    f"Kind: {entity.kind}",
                    f"Aliases: {' | '.join(entity.aliases[: config.max_items_per_field])}",
                )
            )
            if not add(
                CandidateBlock(
                    block_id=f"entity:{capsule.capsule_id}:{entity.entity_id}",
                    slot=RuntimeSlot.RELEVANT_EVIDENCE,
                    kind=CandidateKind.ENTITY,
                    text=text,
                    source_event_ids=entity.source_event_ids,
                    score=_score(
                        text,
                        base_weight=config.entity_weight,
                        query=normalized_query,
                        config=config,
                    ),
                    reason=_reason(text, query=normalized_query, config=config),
                    required=False,
                    capsule_id=capsule.capsule_id,
                    event_sequence=None,
                )
            ):
                break
        for dependency in dependencies:
            text = (
                f"Dependency: {dependency.dependency_id}\n"
                f"Kind: {dependency.kind}\n"
                f"Target: {dependency.target_id}"
            )
            if not add(
                CandidateBlock(
                    block_id=(f"dependency:{capsule.capsule_id}:{dependency.dependency_id}"),
                    slot=RuntimeSlot.RELEVANT_EVIDENCE,
                    kind=CandidateKind.DEPENDENCY,
                    text=text,
                    source_event_ids=dependency.source_event_ids,
                    score=_score(
                        text,
                        base_weight=config.dependency_weight,
                        query=normalized_query,
                        config=config,
                    ),
                    reason=_reason(text, query=normalized_query, config=config),
                    required=False,
                    capsule_id=capsule.capsule_id,
                    event_sequence=None,
                )
            ):
                break

    for capsule in capsules:
        if capsule.level not in (CapsuleLevel.EPISODE, CapsuleLevel.GLOBAL):
            continue
        slot = (
            RuntimeSlot.EPISODE_BACKGROUND
            if capsule.level == CapsuleLevel.EPISODE
            else RuntimeSlot.GLOBAL_BACKGROUND
        )
        kind = (
            CandidateKind.EPISODE_BACKGROUND
            if capsule.level == CapsuleLevel.EPISODE
            else CandidateKind.GLOBAL_BACKGROUND
        )
        if not add(
            CandidateBlock(
                block_id=f"background:{capsule.capsule_id}",
                slot=slot,
                kind=kind,
                text=capsule.narrative_summary,
                source_event_ids=capsule.source_event_ids,
                score=_score(
                    capsule.narrative_summary,
                    base_weight=config.background_weight,
                    query=normalized_query,
                    config=config,
                ),
                reason=_reason(
                    capsule.narrative_summary,
                    query=normalized_query,
                    config=config,
                ),
                required=False,
                capsule_id=capsule.capsule_id,
                event_sequence=None,
            )
        ):
            break

    return tuple(sorted(selected, key=_candidate_sort_key))
