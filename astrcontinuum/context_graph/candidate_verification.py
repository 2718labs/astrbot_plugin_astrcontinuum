"""Background candidate verification before publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from ..compaction.types import AuditedCandidate
from ..domain import EventEnvelope
from ..runtime.retrieval import required_candidates_complete, select_candidates
from ..runtime.types import CandidateBlock, CandidateKind, RetrievalConfig
from ..storage import RequestView
from .build import build_context_graph
from .closure import close_dependency_closure, validate_dependency_closure
from .engine import SparseContextEngine
from .types import (
    ContextEngineMode,
    ContextGraph,
    ContextGraphConfig,
    EngineOutcome,
    EngineResult,
    QueryActivation,
)


class CandidateVerificationErrorCode(str, Enum):
    """The single content-free durable rejection code."""

    REJECTED = "CANDIDATE_GRAPH_VERIFICATION_FAILED"


class CandidateVerificationError(ValueError):
    __slots__ = ("_code",)

    def __init__(self, code: CandidateVerificationErrorCode) -> None:
        if not isinstance(code, CandidateVerificationErrorCode):
            raise TypeError("code must be a CandidateVerificationErrorCode")
        self._code = code
        super().__init__(code.value)

    @property
    def code(self) -> CandidateVerificationErrorCode:
        return self._code


@dataclass(frozen=True, slots=True)
class CandidateVerificationReport:
    """Bounded verification metrics containing no candidate content."""

    passed: bool
    candidate_count: int
    relation_count: int
    constraint_count: int
    probe_count: int
    probe_ids: tuple[str, ...]


class VerificationEngine(Protocol):
    @property
    def config(self) -> ContextGraphConfig: ...

    def solve(
        self,
        graph: ContextGraph,
        activation: QueryActivation,
        *,
        mode: ContextEngineMode = ContextEngineMode.ACTIVE,
    ) -> EngineResult: ...


_PROBES: tuple[tuple[str, frozenset[CandidateKind]], ...] = (
    (
        "required",
        frozenset(
            {
                CandidateKind.GOAL,
                CandidateKind.CONSTRAINT,
                CandidateKind.EXACT_ANCHOR,
                CandidateKind.TASK_CONTEXT,
            }
        ),
    ),
    (
        "evidence",
        frozenset(
            {
                CandidateKind.DECISION,
                CandidateKind.DEPENDENCY,
                CandidateKind.ENTITY,
            }
        ),
    ),
    ("recent", frozenset({CandidateKind.RAW_EVENT})),
)
_PROVENANCE_FIELDS = (
    "goals",
    "constraints",
    "decisions",
    "progress",
    "open_loops",
    "preferences",
    "entities",
    "emotional_context",
    "exact_anchors",
    "dependencies",
)


def _source_events(candidate: AuditedCandidate) -> tuple[EventEnvelope, ...]:
    events: list[EventEnvelope] = []
    seen: set[str] = set()
    for segment in candidate.segments:
        for event in segment.events:
            event_id = event.event_id
            if event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)
    return tuple(events)


def _known_provenance(candidate: AuditedCandidate) -> frozenset[str]:
    return frozenset(
        (
            *(event.event_id for segment in candidate.segments for event in segment.events),
            *(
                event_id
                for membership in candidate.memberships
                for event_id in membership.capsule.source_event_ids
            ),
        )
    )


def _membership_provenance_valid(candidate: AuditedCandidate) -> bool:
    for membership in candidate.memberships:
        capsule = membership.capsule
        capsule_sources = set(capsule.source_event_ids)
        for field_name in _PROVENANCE_FIELDS:
            if any(
                not set(item.source_event_ids).issubset(capsule_sources)
                for item in getattr(capsule, field_name)
            ):
                return False
    return True


def build_candidate_blocks(
    candidate: AuditedCandidate,
    *,
    retrieval_config: RetrievalConfig | None = None,
) -> tuple[CandidateBlock, ...]:
    """Convert audited structured memberships into bounded extractive blocks."""

    if not isinstance(candidate, AuditedCandidate):
        raise TypeError("candidate must be an AuditedCandidate")
    config = RetrievalConfig() if retrieval_config is None else retrieval_config
    if not isinstance(config, RetrievalConfig):
        raise TypeError("retrieval_config must be a RetrievalConfig")
    snapshot = candidate.snapshot
    memberships = tuple(candidate.memberships)
    if tuple(item.capsule_id for item in memberships) != snapshot.capsule_ids:
        raise ValueError("candidate membership order does not match snapshot")
    if any(item.capsule.session_key != snapshot.session_key for item in memberships):
        raise ValueError("candidate membership session mismatch")

    events = _source_events(candidate)
    view = RequestView(
        session_key=snapshot.session_key,
        snapshot=snapshot,
        memberships=memberships,
        pointer_version=0,
        covered_event_end=snapshot.covered_event_end,
        high_water_mark=snapshot.source_high_water_mark,
        delta=events,
    )
    blocks = select_candidates(view, "", config)
    filtered = tuple(
        block
        for block in blocks
        if block.kind
        not in {
            CandidateKind.EPISODE_BACKGROUND,
            CandidateKind.GLOBAL_BACKGROUND,
        }
    )
    if not required_candidates_complete(view, filtered):
        return tuple(replace(block, required_selection_complete=False) for block in filtered)
    return filtered


def _activation(
    graph: ContextGraph,
    *,
    probe_id: str,
    probe_kinds: frozenset[CandidateKind],
    provenance: tuple[str, ...],
    retain_all: bool,
) -> QueryActivation:
    coordinate_ids = tuple(item.coordinate_id for item in graph.coordinates)
    scores = tuple(
        (
            coordinate.coordinate_id,
            4.0 if coordinate.candidate.kind in probe_kinds else 0.0,
        )
        for coordinate in graph.coordinates
    )
    retained = (
        coordinate_ids
        if retain_all
        else tuple(
            coordinate.coordinate_id
            for coordinate in graph.coordinates
            if coordinate.candidate.required or coordinate.candidate.kind in probe_kinds
        )
    )
    return QueryActivation(
        query_id=f"candidate-probe:{probe_id}",
        scores=scores,
        retained_coordinate_ids=retained,
        provenance_event_ids=provenance,
    )


def _result_verified(
    result: EngineResult,
    *,
    required_ids: frozenset[str],
    provenance: frozenset[str],
) -> bool:
    certificate = result.certificate
    selected_ids = frozenset(item.block_id for item in result.selected_blocks)
    return (
        result.outcome is EngineOutcome.ACTIVE
        and certificate is not None
        and certificate.passed
        and certificate.required_blocks_passed
        and certificate.provenance_passed
        and required_ids.issubset(selected_ids)
        and all(
            set(block.source_event_ids).issubset(provenance) for block in result.selected_blocks
        )
    )


def _probe_equivalent(
    reduced: EngineResult,
    full: EngineResult,
    *,
    tolerance: float,
) -> bool:
    if tuple(item.block_id for item in reduced.selected_blocks) != tuple(
        item.block_id for item in full.selected_blocks
    ):
        return False
    reduced_scores = dict(reduced.activation_scores)
    full_scores = dict(full.activation_scores)
    return reduced_scores.keys() == full_scores.keys() and all(
        abs(reduced_scores[coordinate_id] - full_scores[coordinate_id]) <= tolerance
        for coordinate_id in reduced_scores
    )


def _verify_candidate(
    candidate: AuditedCandidate,
    *,
    config: ContextGraphConfig,
    engine: VerificationEngine,
) -> CandidateVerificationReport:
    blocks = build_candidate_blocks(candidate)
    if not blocks or len(blocks) > config.max_coordinates:
        raise ValueError("candidate block capacity invalid")
    if any(not block.required_selection_complete for block in blocks):
        raise ValueError("required candidate selection incomplete")
    if not _membership_provenance_valid(candidate):
        raise ValueError("candidate membership provenance invalid")
    known_provenance = _known_provenance(candidate)
    if any(not set(block.source_event_ids).issubset(known_provenance) for block in blocks):
        raise ValueError("candidate provenance invalid")
    provenance = tuple(
        dict.fromkeys(event_id for block in blocks for event_id in block.source_event_ids)
    )
    view = RequestView(
        session_key=candidate.snapshot.session_key,
        snapshot=candidate.snapshot,
        memberships=candidate.memberships,
        pointer_version=0,
        covered_event_end=candidate.snapshot.covered_event_end,
        high_water_mark=candidate.snapshot.source_high_water_mark,
        delta=(),
    )
    built = build_context_graph(view, blocks, "", config=config)
    required_ids = frozenset(block.block_id for block in blocks if block.required)
    tolerance = max(
        config.direct_backward_error_limit,
        config.iterative_backward_error_limit,
    )

    for probe_id, probe_kinds in _PROBES:
        reduced_activation = _activation(
            built.graph,
            probe_id=probe_id,
            probe_kinds=probe_kinds,
            provenance=provenance,
            retain_all=False,
        )
        full_activation = replace(
            reduced_activation,
            retained_coordinate_ids=tuple(
                coordinate.coordinate_id for coordinate in built.graph.coordinates
            ),
        )
        reduced = engine.solve(
            built.graph,
            reduced_activation,
            mode=ContextEngineMode.ACTIVE,
        )
        full = engine.solve(
            built.graph,
            full_activation,
            mode=ContextEngineMode.ACTIVE,
        )
        if not _result_verified(
            reduced,
            required_ids=required_ids,
            provenance=frozenset(provenance),
        ) or not _result_verified(
            full,
            required_ids=required_ids,
            provenance=frozenset(provenance),
        ):
            raise ValueError("candidate numerical verification failed")
        if not _probe_equivalent(reduced, full, tolerance=tolerance):
            raise ValueError("candidate probe equivalence failed")
        closure = close_dependency_closure(
            reduced.selected_blocks,
            blocks,
            max_blocks=config.max_coordinates,
        )
        if not validate_dependency_closure(
            closure.blocks,
            blocks,
            max_blocks=config.max_coordinates,
        ) or any(not set(block.source_event_ids).issubset(provenance) for block in closure.blocks):
            raise ValueError("candidate dependency closure failed")

    return CandidateVerificationReport(
        passed=True,
        candidate_count=len(blocks),
        relation_count=len(built.graph.relations),
        constraint_count=len(built.graph.constraints),
        probe_count=len(_PROBES),
        probe_ids=tuple(probe_id for probe_id, _ in _PROBES),
    )


def verify_candidate(
    candidate: AuditedCandidate,
    *,
    config: ContextGraphConfig | None = None,
    engine: VerificationEngine | None = None,
) -> CandidateVerificationReport:
    """Reject any candidate that cannot pass all fixed mechanical probes."""

    graph_config = ContextGraphConfig() if config is None else config
    verifier = SparseContextEngine(graph_config) if engine is None else engine
    try:
        if not isinstance(graph_config, ContextGraphConfig):
            raise TypeError("config must be a ContextGraphConfig")
        return _verify_candidate(
            candidate,
            config=graph_config,
            engine=verifier,
        )
    except CandidateVerificationError:
        raise
    except Exception:  # noqa: BLE001 - content and backend details must be redacted
        raise CandidateVerificationError(CandidateVerificationErrorCode.REJECTED) from None
