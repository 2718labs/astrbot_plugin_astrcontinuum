"""Fail-open live sparse-context selection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Protocol

from ..domain import EventType
from ..runtime.budget import assemble
from ..runtime.retrieval import required_candidates_complete
from ..runtime.types import (
    AssemblyResult,
    BudgetConfig,
    CandidateBlock,
    TokenCounter,
)
from ..storage import RequestView
from .build import BuiltContextGraph, build_context_graph
from .closure import (
    ClosureError,
    close_dependency_closure,
    validate_dependency_closure,
)
from .engine import SparseContextEngine
from .types import (
    ContextEngineMode,
    ContextGraph,
    ContextGraphConfig,
    EngineOutcome,
    EngineResult,
    ErrorCertificate,
    QueryActivation,
)


class ResidualBand(str, Enum):
    UNAVAILABLE = "UNAVAILABLE"
    VERIFIED = "VERIFIED"
    MARGINAL = "MARGINAL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class LiveContextTrace:
    """Bounded, content-free diagnostics for the most recent live selection."""

    mode: ContextEngineMode
    outcome: EngineOutcome
    error_code: str | None
    candidate_count: int
    selected_count: int
    relation_count: int
    constraint_count: int
    retained_count: int
    reduced_count: int
    selected_budget_units: int
    required_passed: bool
    provenance_passed: bool
    residual_band: ResidualBand
    adaptive_retry_count: int


@dataclass(frozen=True, slots=True)
class LiveSelectionResult:
    assembly: AssemblyResult = field(repr=False)
    trace: LiveContextTrace


class ContextEngineSolver(Protocol):
    def solve(
        self,
        graph: ContextGraph,
        activation: QueryActivation,
        *,
        mode: ContextEngineMode,
    ) -> EngineResult: ...


def _residual_band(certificate: ErrorCertificate | None) -> ResidualBand:
    if certificate is None:
        return ResidualBand.UNAVAILABLE
    maximum = max(
        certificate.stationarity_error,
        certificate.constraint_error,
        certificate.reconstruction_error,
    )
    if certificate.passed and maximum <= 1.0e-10:
        return ResidualBand.VERIFIED
    if certificate.passed and maximum <= 1.0e-8:
        return ResidualBand.MARGINAL
    return ResidualBand.FAILED


def _trace(
    *,
    mode: ContextEngineMode,
    outcome: EngineOutcome,
    fallback: AssemblyResult,
    candidates: tuple[CandidateBlock, ...],
    built: BuiltContextGraph | None = None,
    result: EngineResult | None = None,
    final: AssemblyResult | None = None,
    error_code: str | None = None,
    retry_count: int = 0,
) -> LiveContextTrace:
    selected = final or fallback
    certificate = result.certificate if result is not None else None
    retained_count = len(built.activation.retained_coordinate_ids) if built is not None else 0
    return LiveContextTrace(
        mode=mode,
        outcome=outcome,
        error_code=error_code,
        candidate_count=len(candidates),
        selected_count=len(selected.selected_blocks),
        relation_count=len(built.graph.relations) if built is not None else 0,
        constraint_count=len(built.graph.constraints) if built is not None else 0,
        retained_count=retained_count,
        reduced_count=max(0, len(candidates) - retained_count),
        selected_budget_units=selected.trace.ac_selected_cost,
        required_passed=(certificate.required_blocks_passed if certificate is not None else False),
        provenance_passed=(certificate.provenance_passed if certificate is not None else False),
        residual_band=_residual_band(certificate),
        adaptive_retry_count=retry_count,
    )


def _should_retry(result: EngineResult, activation: QueryActivation, count: int) -> bool:
    return (
        count == 0
        and result.outcome is EngineOutcome.DEGRADED_RAW
        and result.error_code is not None
        and (result.error_code.startswith("REDUCTION_") or result.error_code.startswith("SOLVE_"))
        and bool(activation.retained_coordinate_ids)
    )


def _verified_scores(
    result: EngineResult,
    built: BuiltContextGraph,
) -> dict[str, float]:
    return dict(result.activation_scores or built.activation.scores)


def _score_for_pack(
    blocks: tuple[CandidateBlock, ...],
    *,
    scores: dict[str, float],
    counter: TokenCounter,
) -> tuple[CandidateBlock, ...]:
    packed: list[CandidateBlock] = []
    for block in blocks:
        if block.required:
            packed.append(block)
            continue
        cost = counter.count_text(block.text)
        if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
            raise TypeError("token counter returned an invalid cost")
        packed.append(
            replace(
                block,
                score=max(0.0, scores.get(block.block_id, 0.0)) / max(1, cost),
                reason="verified_activation_per_token",
            )
        )
    return tuple(packed)


def select_live_context(
    view: RequestView,
    candidates: tuple[CandidateBlock, ...],
    *,
    current_input: str,
    opaque_token_cost: int,
    fixed_required_cost: int,
    counter: TokenCounter,
    budget_config: BudgetConfig,
    mode: ContextEngineMode,
    engine: ContextEngineSolver,
) -> LiveSelectionResult:
    """Compute deterministic fallback first and replace it only after every gate."""

    materialized = tuple(candidates)
    fallback = assemble(
        view,
        materialized,
        current_input=current_input,
        opaque_token_cost=opaque_token_cost,
        fixed_required_cost=fixed_required_cost,
        counter=counter,
        config=budget_config,
    )
    satisfied_event_ids: tuple[str, ...] = ()
    if view.delta:
        latest = view.delta[-1]
        if latest.event_type is EventType.USER_MESSAGE and latest.content == current_input:
            satisfied_event_ids = (latest.event_id,)
    if any(
        not candidate.required_selection_complete for candidate in materialized
    ) or not required_candidates_complete(
        view,
        materialized,
        satisfied_event_ids=satisfied_event_ids,
    ):
        return LiveSelectionResult(
            fallback,
            _trace(
                mode=mode,
                outcome=EngineOutcome.DEGRADED_RAW,
                error_code="LIVE_REQUIRED_CANDIDATE_TRUNCATED",
                fallback=fallback,
                candidates=materialized,
            ),
        )
    if mode is ContextEngineMode.OFF:
        return LiveSelectionResult(
            fallback,
            _trace(
                mode=mode,
                outcome=EngineOutcome.OFF,
                fallback=fallback,
                candidates=materialized,
            ),
        )

    config = getattr(engine, "config", None)
    if not isinstance(config, ContextGraphConfig):
        config = ContextGraphConfig()
    try:
        built = build_context_graph(
            view,
            materialized,
            current_input,
            config=config,
        )
    except Exception:  # noqa: BLE001 - fail-open boundary must redact build details
        return LiveSelectionResult(
            fallback,
            _trace(
                mode=mode,
                outcome=EngineOutcome.DEGRADED_RAW,
                error_code="LIVE_BUILD_FAILURE",
                fallback=fallback,
                candidates=materialized,
            ),
        )

    retry_count = 0
    activation = built.activation
    try:
        result = engine.solve(built.graph, activation, mode=mode)
        if _should_retry(result, activation, retry_count):
            retained = tuple(coordinate.coordinate_id for coordinate in built.graph.coordinates)
            if set(activation.retained_coordinate_ids) < set(retained):
                activation = replace(
                    activation,
                    retained_coordinate_ids=retained,
                )
                retry_count = 1
                result = engine.solve(built.graph, activation, mode=mode)
    except Exception:  # noqa: BLE001 - fail-open boundary must redact engine details
        return LiveSelectionResult(
            fallback,
            _trace(
                mode=mode,
                outcome=EngineOutcome.DEGRADED_RAW,
                error_code="LIVE_ENGINE_FAILURE",
                fallback=fallback,
                candidates=materialized,
                built=built,
                retry_count=retry_count,
            ),
        )

    if mode is ContextEngineMode.SHADOW:
        return LiveSelectionResult(
            fallback,
            _trace(
                mode=mode,
                outcome=(
                    EngineOutcome.SHADOW
                    if result.outcome is EngineOutcome.SHADOW
                    else EngineOutcome.DEGRADED_RAW
                ),
                error_code=result.error_code,
                fallback=fallback,
                candidates=materialized,
                built=built,
                result=result,
                retry_count=retry_count,
            ),
        )

    certificate = result.certificate
    if (
        result.outcome is not EngineOutcome.ACTIVE
        or certificate is None
        or not certificate.passed
        or not certificate.required_blocks_passed
        or not certificate.provenance_passed
    ):
        return LiveSelectionResult(
            fallback,
            _trace(
                mode=mode,
                outcome=EngineOutcome.DEGRADED_RAW,
                error_code=result.error_code or "LIVE_ENGINE_REJECTED",
                fallback=fallback,
                candidates=materialized,
                built=built,
                result=result,
                retry_count=retry_count,
            ),
        )

    try:
        closure = close_dependency_closure(
            result.selected_blocks,
            materialized,
            max_blocks=config.max_coordinates,
        )
        if any(
            not set(block.source_event_ids).issubset(built.activation.provenance_event_ids)
            for block in closure.blocks
        ):
            raise ClosureError("LIVE_PROVENANCE_GATE_FAILED")
        scored = _score_for_pack(
            closure.blocks,
            scores=_verified_scores(result, built),
            counter=counter,
        )
        final = assemble(
            view,
            scored,
            current_input=current_input,
            opaque_token_cost=opaque_token_cost,
            fixed_required_cost=fixed_required_cost,
            counter=counter,
            config=budget_config,
        )
        final_ids = {item.block_id for item in final.selected_blocks}
        closure_ids = {item.block_id for item in closure.blocks}
        required_ids = {item.block_id for item in materialized if item.required}
        if (
            not required_ids.issubset(final_ids)
            or not closure_ids.issubset(final_ids)
            or not validate_dependency_closure(
                final.selected_blocks,
                materialized,
                max_blocks=config.max_coordinates,
            )
        ):
            raise ClosureError("LIVE_FINAL_PACK_GATE_FAILED")
    except Exception:  # noqa: BLE001 - fail-open boundary must redact validation details
        return LiveSelectionResult(
            fallback,
            _trace(
                mode=mode,
                outcome=EngineOutcome.DEGRADED_RAW,
                error_code="LIVE_FINAL_VALIDATION_FAILED",
                fallback=fallback,
                candidates=materialized,
                built=built,
                result=result,
                retry_count=retry_count,
            ),
        )

    return LiveSelectionResult(
        final,
        _trace(
            mode=mode,
            outcome=EngineOutcome.ACTIVE,
            fallback=fallback,
            candidates=materialized,
            built=built,
            result=result,
            final=final,
            retry_count=retry_count,
        ),
    )


def assemble_live_context(
    view: RequestView,
    candidates: tuple[CandidateBlock, ...],
    *,
    current_input: str,
    opaque_token_cost: int,
    fixed_required_cost: int,
    counter: TokenCounter,
    budget_config: BudgetConfig,
    mode: ContextEngineMode,
) -> tuple[AssemblyResult, LiveContextTrace]:
    """Frozen composition API used by the AstrBot request bridge."""

    selected = select_live_context(
        view,
        candidates,
        current_input=current_input,
        opaque_token_cost=opaque_token_cost,
        fixed_required_cost=fixed_required_cost,
        counter=counter,
        budget_config=budget_config,
        mode=mode,
        engine=SparseContextEngine(),
    )
    return selected.assembly, selected.trace
