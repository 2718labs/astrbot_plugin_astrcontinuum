"""Pure loss-aware Capsule recomposition transaction."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns

from .canonical import canonical_json, semantic_hash, sha256_text, utf8_bytes
from .contracts import (
    AtomStatus,
    Candidate,
    CapsuleState,
    KernelSelection,
    LossReport,
    MatrixBundle,
    OutcomeState,
    RecompositionRequest,
    RecompositionResult,
    Selection,
    SemanticAtom,
)
from .kernel import derive_kernel_ceiling, select_kernel
from .matrix import atomize, build_matrix, compose_candidates
from .optimizer import optimize

_STAGE_NAMES = ("matrix", "optimizer", "gate")


@dataclass(frozen=True, slots=True)
class _GateResult:
    valid: bool
    reasons: tuple[str, ...]


def recompose_capsule(request: RecompositionRequest) -> RecompositionResult:
    """Recompose a bounded Capsule or return a bounded degraded transition."""
    kernel_ceiling = derive_kernel_ceiling(request.kernel_schema)
    if request.byte_budget < kernel_ceiling:
        if request.base_state is None:
            raise ValueError("initial budget is below continuity floor")
        return _blocked_result(request)

    atoms = atomize(request.base_state, request.delta)
    kernel = select_kernel(atoms, request.kernel_schema)
    if not kernel.valid:
        if request.base_state is None:
            raise ValueError("initial state cannot satisfy continuity kernel")
        return _blocked_result(request)

    kernel_ids = {atom.atom_id for atom in kernel.atoms}
    body_atoms = tuple(atom for atom in atoms if atom.atom_id not in kernel_ids)
    residual_budget = request.byte_budget - _serialized_kernel_bytes(kernel.atoms)

    matrix_started = perf_counter_ns()
    candidates = compose_candidates(body_atoms)
    matrix = build_matrix(body_atoms, candidates, budget=max(0, residual_budget))
    matrix_ns = perf_counter_ns() - matrix_started

    optimizer_started = perf_counter_ns()
    selection = optimize(matrix, request.loss_policy)
    optimizer_ns = perf_counter_ns() - optimizer_started

    candidate_state = _build_candidate_state(
        request,
        atoms,
        kernel,
        candidates,
        selection,
    )
    gate_started = perf_counter_ns()
    gate = _validate_candidate(
        request,
        atoms,
        candidate_state,
        selection,
        candidates,
    )
    gate_ns = perf_counter_ns() - gate_started

    if not gate.valid:
        candidate_state = _build_kernel_only_state(request, atoms, kernel)
        outcome = OutcomeState.KERNEL_ONLY
    else:
        outcome = (
            OutcomeState.NORMAL if candidate_state.body else OutcomeState.KERNEL_ONLY
        )
    return _build_result(
        request,
        atoms,
        candidate_state,
        outcome,
        matrix,
        candidates,
        selection,
        (matrix_ns, optimizer_ns, gate_ns),
    )


def _serialized_kernel_bytes(atoms: tuple[SemanticAtom, ...]) -> int:
    return utf8_bytes(canonical_json(atoms))


def _serialized_state_bytes(state: CapsuleState) -> int:
    return utf8_bytes(canonical_json(state))


def _generation(request: RecompositionRequest) -> int:
    if request.base_state is None:
        return 1
    return request.base_state.generation + 1


def _high_water(
    request: RecompositionRequest,
    atoms: tuple[SemanticAtom, ...],
) -> int:
    previous = 0 if request.base_state is None else request.base_state.high_water
    return max((previous, *(atom.as_of for atom in atoms)))


def _selected_candidates(
    candidates: tuple[Candidate, ...],
    selection: Selection,
) -> tuple[Candidate, ...]:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    try:
        return tuple(by_id[candidate_id] for candidate_id in selection.candidate_ids)
    except KeyError as error:
        raise ValueError(
            f"selection references unknown candidate: {error.args[0]}"
        ) from error


def _candidate_to_atom(
    candidate: Candidate,
    atom_by_id: dict[str, SemanticAtom],
) -> SemanticAtom:
    sources = tuple(atom_by_id[atom_id] for atom_id in candidate.covers)
    if len(sources) == 1:
        return sources[0]

    covered_atom_ids = tuple(
        sorted(
            {
                covered_id
                for source in sources
                for covered_id in (source.covered_atom_ids or (source.atom_id,))
            }
        )
    )
    return SemanticAtom(
        atom_id=candidate.candidate_id,
        semantic_keys=candidate.semantic_keys,
        role=candidate.role,
        text=candidate.text,
        status=AtomStatus.ACTIVE,
        revision=max(source.revision for source in sources),
        as_of=max(source.as_of for source in sources),
        provenance=tuple(
            sorted({item for source in sources for item in source.provenance})
        ),
        exact=candidate.exact,
        depends_on=candidate.depends_on,
        weight=sum(source.weight for source in sources),
        core_required=candidate.core_required,
        merge_depth=1,
        covered_atom_ids=covered_atom_ids,
    )


def _build_candidate_state(
    request: RecompositionRequest,
    atoms: tuple[SemanticAtom, ...],
    kernel: KernelSelection,
    candidates: tuple[Candidate, ...],
    selection: Selection,
) -> CapsuleState:
    atom_by_id = {atom.atom_id: atom for atom in atoms}
    body = tuple(
        sorted(
            (
                _candidate_to_atom(candidate, atom_by_id)
                for candidate in _selected_candidates(candidates, selection)
            ),
            key=lambda atom: (atom.role.value, atom.atom_id),
        )
    )
    return CapsuleState(
        generation=_generation(request),
        high_water=_high_water(request, atoms),
        accepted_budget=request.byte_budget,
        kernel=kernel.atoms,
        body=body,
        weight_version=request.weight_version,
    )


def _build_kernel_only_state(
    request: RecompositionRequest,
    atoms: tuple[SemanticAtom, ...],
    kernel: KernelSelection,
) -> CapsuleState:
    return CapsuleState(
        generation=_generation(request),
        high_water=_high_water(request, atoms),
        accepted_budget=request.byte_budget,
        kernel=kernel.atoms,
        body=(),
        weight_version=request.weight_version,
    )


def _available_ids(atoms: tuple[SemanticAtom, ...]) -> set[str]:
    return {item for atom in atoms for item in (atom.atom_id, *atom.covered_atom_ids)}


def _has_current_conflict(atoms: tuple[SemanticAtom, ...]) -> bool:
    active = tuple(atom for atom in atoms if atom.status is AtomStatus.ACTIVE)
    for offset, left in enumerate(active):
        for right in active[offset + 1 :]:
            if left.revision == right.revision:
                continue
            if not set(left.semantic_keys).isdisjoint(right.semantic_keys):
                return True
    return False


def _validate_candidate(
    request: RecompositionRequest,
    atoms: tuple[SemanticAtom, ...],
    state: CapsuleState,
    selection: Selection,
    candidates: tuple[Candidate, ...],
) -> _GateResult:
    reasons: list[str] = []
    if _serialized_state_bytes(state) > request.byte_budget:
        reasons.append("budget_exceeded")

    core_ids = {
        atom.atom_id
        for atom in atoms
        if atom.core_required and atom.status is AtomStatus.ACTIVE
    }
    kernel_ids = {atom.atom_id for atom in state.kernel}
    if not core_ids <= kernel_ids:
        reasons.append("core_coverage_incomplete")

    selected = _selected_candidates(candidates, selection)
    if sum(candidate.error_risk for candidate in selected) > (
        request.loss_policy.risk_ceiling
    ):
        reasons.append("risk_ceiling_exceeded")

    input_ids = _available_ids(atoms)
    retained = state.kernel + state.body
    if any(not set(atom.covered_atom_ids) <= input_ids for atom in retained):
        reasons.append("uncovered_retained_block")

    if _has_current_conflict(retained):
        reasons.append("current_conflict")

    retained_ids = _available_ids(retained)
    if any(
        dependency not in retained_ids
        for atom in retained
        for dependency in atom.depends_on
    ):
        reasons.append("dependency_missing")

    selected_coverage = [
        atom_id for candidate in selected for atom_id in candidate.covers
    ]
    if len(selected_coverage) != len(set(selected_coverage)):
        reasons.append("duplicate_coverage")
    current_ids = {atom.atom_id for atom in atoms}
    represented = kernel_ids | set(selected_coverage)
    if not represented <= current_ids:
        reasons.append("unknown_coverage")
    released = current_ids - represented
    if represented | released != current_ids:
        reasons.append("partition_incomplete")

    return _GateResult(valid=not reasons, reasons=tuple(sorted(set(reasons))))


def _input_universe(
    request: RecompositionRequest,
    *,
    include_delta: bool,
) -> dict[str, SemanticAtom]:
    inputs: tuple[SemanticAtom, ...] = ()
    if request.base_state is not None:
        inputs += request.base_state.kernel + request.base_state.body
    if include_delta:
        inputs += request.delta
    return {atom.atom_id: atom for atom in inputs}


def _build_loss_report(
    request: RecompositionRequest,
    atoms: tuple[SemanticAtom, ...],
    state: CapsuleState,
    outcome: OutcomeState,
    candidates: tuple[Candidate, ...],
    selection: Selection,
) -> LossReport:
    current_by_id = {atom.atom_id: atom for atom in atoms}
    kernel_ids = {
        atom.atom_id for atom in state.kernel if atom.atom_id in current_by_id
    }
    selected = ()
    if outcome is OutcomeState.NORMAL and state.body:
        selected = _selected_candidates(candidates, selection)

    retained_ids = set(kernel_ids)
    merged_ids: set[str] = set()
    for candidate in selected:
        if len(candidate.covers) == 1:
            retained_ids.update(candidate.covers)
        else:
            merged_ids.update(candidate.covers)

    universe = _input_universe(request, include_delta=True)
    released_ids = set(universe) - retained_ids - merged_ids
    current_released = set(current_by_id) - retained_ids - merged_ids
    error_risk = sum(candidate.error_risk for candidate in selected)
    expected_core = {
        atom.atom_id
        for atom in atoms
        if atom.core_required and atom.status is AtomStatus.ACTIVE
    }

    return LossReport(
        retained_atom_ids=tuple(sorted(retained_ids)),
        merged_atom_ids=tuple(sorted(merged_ids)),
        released_atom_ids=tuple(sorted(released_ids)),
        omission_weight=sum(current_by_id[item].weight for item in current_released),
        error_risk=error_risk,
        continuity_break=not expected_core <= kernel_ids,
        stale_current=_has_current_conflict(state.kernel + state.body),
    )


def _build_result(
    request: RecompositionRequest,
    atoms: tuple[SemanticAtom, ...],
    state: CapsuleState,
    outcome: OutcomeState,
    matrix: MatrixBundle,
    candidates: tuple[Candidate, ...],
    selection: Selection,
    durations: tuple[int, int, int],
) -> RecompositionResult:
    return RecompositionResult(
        state=state,
        outcome=outcome,
        loss=_build_loss_report(
            request,
            atoms,
            state,
            outcome,
            candidates,
            selection,
        ),
        matrix_hash=sha256_text(canonical_json(matrix)),
        semantic_hash=semantic_hash(state),
        strict_eligible=False,
        consumed_delta=True,
        stage_ns=tuple(zip(_STAGE_NAMES, durations, strict=True)),
    )


def _blocked_result(request: RecompositionRequest) -> RecompositionResult:
    base = request.base_state
    if base is None:
        raise ValueError("blocked transition requires a base state")

    state = CapsuleState(
        generation=base.generation + 1,
        high_water=base.high_water,
        accepted_budget=base.accepted_budget,
        kernel=base.kernel,
        body=(),
        weight_version=base.weight_version,
    )
    retained_ids = {atom.atom_id for atom in base.kernel}
    released_ids = {atom.atom_id for atom in base.body}
    loss = LossReport(
        retained_atom_ids=tuple(sorted(retained_ids)),
        merged_atom_ids=(),
        released_atom_ids=tuple(sorted(released_ids)),
        omission_weight=sum(atom.weight for atom in base.body),
        error_risk=0.0,
        continuity_break=False,
        stale_current=_has_current_conflict(base.kernel),
    )
    return RecompositionResult(
        state=state,
        outcome=OutcomeState.ADMISSION_BLOCKED,
        loss=loss,
        matrix_hash=sha256_text(canonical_json({"admission_blocked": True})),
        semantic_hash=semantic_hash(state),
        strict_eligible=False,
        consumed_delta=False,
        stage_ns=tuple((name, 0) for name in _STAGE_NAMES),
    )
