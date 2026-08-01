"""Phase-separated materialization and freeze-before-reveal execution."""

from __future__ import annotations

import hashlib
import json
import tracemalloc
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter_ns
from typing import Any, NoReturn

from crm_experiment.baselines import (
    ARM_ORDER,
    LegacyCapsuleBaseline,
    NoKernelAblation,
    NoProjectionAblation,
    RecursiveSummaryBaseline,
)
from crm_experiment.canonical import (
    canonical_json,
    canonical_value,
    semantic_hash,
    sha256_text,
    utf8_bytes,
)
from crm_experiment.contracts import (
    AtomRole,
    AtomStatus,
    CapsuleState,
    OutcomeState,
    ProjectionResult,
    QuerySpec,
    RecompositionRequest,
    RecompositionResult,
    SemanticAtom,
)
from crm_experiment.kernel import default_kernel_schema, derive_kernel_ceiling
from crm_experiment.projection import UNKNOWN, project_query
from crm_experiment.protocol import (
    ProtocolConfig,
    generate_protocol,
    load_protocol_config,
)
from crm_experiment.recompose import recompose_capsule

_SOURCE_ID_DIGEST_DOMAIN = "crm-source-id/v1"
_SOURCE_SET_DIGEST_DOMAIN = "crm-source-set/v1"
_CHECKPOINT_ID_DOMAIN = "crm-checkpoint/v1"


class ForbiddenQueryRead(RuntimeError):
    """Raised when projection attempts to read outside its frozen state view."""


@dataclass(slots=True)
class QueryReadTrace:
    frozen_capsule_reads: int = 0
    frozen_summary_reads: int = 0
    old_capsule_reads: int = 0
    delta_reads: int = 0
    canonical_truth_reads: int = 0
    sealed_gold_reads: int = 0
    other_arm_reads: int = 0

    @property
    def fallback_reads(self) -> int:
        return (
            self.old_capsule_reads
            + self.delta_reads
            + self.canonical_truth_reads
            + self.sealed_gold_reads
            + self.other_arm_reads
        )

    @property
    def query_source(self) -> str:
        if self.frozen_capsule_reads and not self.frozen_summary_reads:
            return "frozen_capsule"
        if self.frozen_summary_reads and not self.frozen_capsule_reads:
            return "frozen_summary"
        return "not_read"

    def as_dict(self) -> dict[str, int]:
        return {
            "frozen_capsule_reads": self.frozen_capsule_reads,
            "frozen_summary_reads": self.frozen_summary_reads,
            "old_capsule_reads": self.old_capsule_reads,
            "delta_reads": self.delta_reads,
            "canonical_truth_reads": self.canonical_truth_reads,
            "sealed_gold_reads": self.sealed_gold_reads,
            "other_arm_reads": self.other_arm_reads,
        }


@dataclass(frozen=True, slots=True)
class FrozenStateView:
    """The only state surface available to query projection."""

    _state: CapsuleState | str | None
    state_hash: str
    trace: QueryReadTrace

    def read_capsule(self) -> CapsuleState:
        if not isinstance(self._state, CapsuleState):
            raise TypeError("frozen state is not a capsule")
        self.trace.frozen_capsule_reads += 1
        return self._state

    def read_summary(self) -> str:
        if not isinstance(self._state, str):
            raise TypeError("frozen state is not a summary")
        self.trace.frozen_summary_reads += 1
        return self._state

    def attempt_forbidden_read(self, source: str) -> NoReturn:
        fields = {
            "old_capsule": "old_capsule_reads",
            "delta": "delta_reads",
            "canonical_truth": "canonical_truth_reads",
            "sealed_gold": "sealed_gold_reads",
            "other_arm": "other_arm_reads",
        }
        field = fields.get(source, "other_arm_reads")
        setattr(self.trace, field, getattr(self.trace, field) + 1)
        raise ForbiddenQueryRead(source)


@dataclass(frozen=True, slots=True)
class _QueryExecution:
    projection: ProjectionResult
    error_code: str
    query_source: str
    query_source_state_hash: str | None
    fallback_reads: int
    read_trace: dict[str, int]


@dataclass(frozen=True, slots=True)
class _MeasurementAtom:
    atom_id: str
    semantic_keys: tuple[str, ...]
    role: AtomRole
    status: AtomStatus
    revision: int
    as_of: int
    weight: float
    core_required: bool


@dataclass(frozen=True, slots=True)
class _AdvanceResult:
    state: CapsuleState | str | None
    state_hash: str
    requested_budget_bytes: int
    accepted_budget_bytes: int
    budget_overshoot_bytes: int
    persistent_bytes: int
    kernel_payload_bytes: int
    state_overhead_bytes: int
    kernel_bytes: int
    body_bytes: int
    outcome: str
    error_risk: float | None
    matrix_hash: str
    stage_ns: tuple[tuple[str, int], ...]
    total_ns: int
    peak_workspace_bytes: int
    error_code: str
    transition_committed: bool
    consumed_delta: bool


@dataclass(frozen=True, slots=True)
class _TransitionMetrics:
    step_input_bytes: int | None
    semantic_weight_denominator: float
    weighted_retained_weight: float
    weighted_omitted_weight: float
    released_count: int | None
    release_denominator_count: int | None
    net_released_bytes: int | None
    rejected_delta_bytes: int
    committed_base_state_reduction_bytes: int | None
    coverage_intersection_count: int
    coverage_union_count: int
    previous_coverage_digest: str
    new_coverage_digest: str
    kernel_coverage_count: int
    kernel_coverage_denominator: int
    kernel_coverage_id_digests: tuple[str, ...]
    internal_continuity_break: bool
    internal_stale_current: bool


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{canonical_json(row)}\n" for row in rows)
    path.write_text(content, encoding="utf-8")


def _file_digest(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def materialize_protocol(config_path: str | Path, output_root: str | Path) -> Path:
    """Write separate runtime, public-query, and sealed-gold inputs."""
    config = load_protocol_config(config_path)
    bundle = generate_protocol(config)
    root = Path(output_root)
    runtime_path = root / "runtime" / "protocol.json"
    query_path = root / "public-queries" / "queries.json"
    gold_path = root / "sealed-gold" / "gold.json"

    runtime_payload = {
        "schema_version": config.schema_version,
        "streams": [
            {
                "stream_id": stream.stream_id,
                "generations": [
                    {
                        "generation": generation.generation,
                        "events": canonical_value(generation.events),
                    }
                    for generation in stream.generations
                ],
            }
            for stream in bundle.streams
        ],
    }
    _write_json(runtime_path, runtime_payload)
    _write_json(
        query_path,
        {
            "schema_version": config.schema_version,
            "queries": canonical_value(bundle.public_queries),
        },
    )
    _write_json(
        gold_path,
        {
            "schema_version": config.schema_version,
            "gold": canonical_value(bundle.sealed_gold),
        },
    )
    manifest_path = root / "input-manifest.json"
    rows = []
    for path in (runtime_path, query_path, gold_path):
        digest = _file_digest(path)
        digest["path"] = path.relative_to(root).as_posix()
        rows.append(digest)
    _write_json(manifest_path, {"schema_version": 1, "files": rows})
    return manifest_path


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object: {path}")
    return raw


def _atom_from_raw(raw: dict[str, Any]) -> SemanticAtom:
    return SemanticAtom(
        atom_id=str(raw["atom_id"]),
        semantic_keys=tuple(str(item) for item in raw["semantic_keys"]),
        role=AtomRole(str(raw["role"])),
        text=str(raw["text"]),
        status=AtomStatus(str(raw["status"])),
        revision=int(raw["revision"]),
        as_of=int(raw["as_of"]),
        provenance=tuple(str(item) for item in raw["provenance"]),
        exact=bool(raw["exact"]),
        depends_on=tuple(str(item) for item in raw["depends_on"]),
        weight=float(raw["weight"]),
        core_required=bool(raw["core_required"]),
        merge_depth=int(raw["merge_depth"]),
        covered_atom_ids=tuple(str(item) for item in raw["covered_atom_ids"]),
    )


def _load_runtime(path: Path) -> tuple[dict[str, object], ...]:
    payload = _load_json(path)
    streams_raw = payload.get("streams")
    if not isinstance(streams_raw, list):
        raise ValueError("runtime streams must be a list")
    streams: list[dict[str, object]] = []
    for stream_raw in streams_raw:
        if not isinstance(stream_raw, dict):
            raise ValueError("runtime stream must be an object")
        generations_raw = stream_raw.get("generations")
        if not isinstance(generations_raw, list):
            raise ValueError("runtime generations must be a list")
        generations: list[dict[str, object]] = []
        for generation_raw in generations_raw:
            if not isinstance(generation_raw, dict):
                raise ValueError("runtime generation must be an object")
            events_raw = generation_raw.get("events")
            if not isinstance(events_raw, list):
                raise ValueError("runtime events must be a list")
            events = tuple(
                _atom_from_raw(item) for item in events_raw if isinstance(item, dict)
            )
            if len(events) != len(events_raw):
                raise ValueError("runtime event must be an object")
            generations.append(
                {
                    "generation": int(generation_raw["generation"]),
                    "events": events,
                }
            )
        streams.append(
            {
                "stream_id": str(stream_raw["stream_id"]),
                "generations": tuple(generations),
            }
        )
    return tuple(streams)


def _query_from_raw(raw: dict[str, Any]) -> QuerySpec:
    return QuerySpec(
        query_id=str(raw["query_id"]),
        role=AtomRole(str(raw["role"])),
        semantic_key=str(raw["semantic_key"]),
    )


def _load_public_queries(path: Path) -> dict[str, QuerySpec]:
    payload = _load_json(path)
    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list):
        raise ValueError("public queries must be a list")
    queries: dict[str, QuerySpec] = {}
    for raw in raw_queries:
        if not isinstance(raw, dict):
            raise ValueError("public query must be an object")
        query = _query_from_raw(raw)
        if query.query_id in queries:
            raise ValueError(f"duplicate query id: {query.query_id}")
        queries[query.query_id] = query
    return queries


def _measurement_rank(atom: _MeasurementAtom) -> tuple[int, str]:
    return atom.revision, atom.atom_id


def _update_measurement_ledger(
    ledger: dict[str, _MeasurementAtom],
    delta: tuple[SemanticAtom, ...],
) -> None:
    """Update content-free latest-per-key telemetry from one already-seen delta."""
    for atom in delta:
        telemetry = _MeasurementAtom(
            atom_id=atom.atom_id,
            semantic_keys=tuple(sorted(atom.semantic_keys)),
            role=atom.role,
            status=atom.status,
            revision=atom.revision,
            as_of=atom.as_of,
            weight=atom.weight,
            core_required=atom.core_required,
        )
        for semantic_key in telemetry.semantic_keys:
            previous = ledger.get(semantic_key)
            if previous is None or _measurement_rank(telemetry) > _measurement_rank(
                previous
            ):
                ledger[semantic_key] = telemetry


def _measurement_universe(
    ledger: dict[str, _MeasurementAtom],
) -> tuple[_MeasurementAtom, ...]:
    """Return each latest active source atom once, independent of arm state."""
    active_by_id = {
        atom.atom_id: atom
        for atom in ledger.values()
        if atom.status is AtomStatus.ACTIVE
    }
    return tuple(active_by_id[atom_id] for atom_id in sorted(active_by_id))


def _expanded_atom_coverage(atom: SemanticAtom) -> set[str]:
    return set(atom.covered_atom_ids or (atom.atom_id,))


def _capsule_coverage(state: CapsuleState | None) -> set[str]:
    if state is None:
        return set()
    return {
        source_id
        for atom in state.kernel + state.body
        for source_id in _expanded_atom_coverage(atom)
    }


def _kernel_coverage(state: CapsuleState | str | None) -> set[str]:
    if not isinstance(state, CapsuleState):
        return set()
    return {
        source_id
        for atom in state.kernel
        for source_id in _expanded_atom_coverage(atom)
    }


def _persistent_bytes(state: CapsuleState | str | None) -> int:
    if isinstance(state, CapsuleState):
        return utf8_bytes(canonical_json(state))
    if isinstance(state, str):
        return utf8_bytes(state)
    return 0


def _state_coverage(state: CapsuleState | str | None) -> set[str]:
    if isinstance(state, CapsuleState):
        return _capsule_coverage(state)
    if isinstance(state, str):
        return _summary_ids(state)
    return set()


def _delta_coverage(delta: tuple[SemanticAtom, ...]) -> set[str]:
    return {source_id for atom in delta for source_id in _expanded_atom_coverage(atom)}


def _source_id_digest(source_id: str, source_manifest_hash: str) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": _SOURCE_ID_DIGEST_DOMAIN,
                "source_manifest_hash": source_manifest_hash,
                "source_id": source_id,
            }
        )
    )


def _coverage_digest(source_ids: set[str], source_manifest_hash: str) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": _SOURCE_SET_DIGEST_DOMAIN,
                "source_manifest_hash": source_manifest_hash,
                "source_id_digests": tuple(
                    _source_id_digest(source_id, source_manifest_hash)
                    for source_id in sorted(source_ids)
                ),
            }
        )
    )


def _checkpoint_id(
    stream_id: str,
    generation: int,
    multiplier: float,
    arm: str,
    source_manifest_hash: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": _CHECKPOINT_ID_DOMAIN,
                "source_manifest_hash": source_manifest_hash,
                "stream_id": stream_id,
                "generation": generation,
                "budget_multiplier": multiplier,
                "arm": arm,
            }
        )
    )


def _state_byte_partitions(
    state: CapsuleState | str | None,
) -> tuple[int, int, int, int, int]:
    """Return persistent, kernel payload, frame overhead, kernel, and body bytes."""
    if isinstance(state, CapsuleState):
        persistent = utf8_bytes(canonical_json(state))
        resident_frame = utf8_bytes(canonical_json(replace(state, body=())))
        kernel_payload = utf8_bytes(canonical_json(state.kernel))
        state_overhead = resident_frame - kernel_payload
        body = persistent - resident_frame
        return persistent, kernel_payload, state_overhead, resident_frame, body
    if isinstance(state, str):
        persistent = utf8_bytes(state)
        return persistent, 0, 0, 0, persistent
    return 0, 0, 0, 0, 0


def _accepted_budget(state: CapsuleState | str | None, requested: int) -> int:
    if isinstance(state, CapsuleState):
        return state.accepted_budget
    return requested


def _measure_transition(
    previous: CapsuleState | str | None,
    delta_source_ids: set[str],
    advance: _AdvanceResult,
    universe: tuple[_MeasurementAtom, ...],
    raw_step_input_bytes: int,
    delta_bytes: int,
    source_manifest_hash: str,
) -> _TransitionMetrics:
    previous_coverage = _state_coverage(previous)
    new_coverage = _state_coverage(advance.state)
    step_sources = previous_coverage | delta_source_ids
    universe_by_id = {atom.atom_id: atom for atom in universe}
    universe_ids = set(universe_by_id)
    retained_ids = universe_ids & new_coverage
    semantic_weight_denominator = sum(atom.weight for atom in universe_by_id.values())
    weighted_retained_weight = sum(
        universe_by_id[source_id].weight for source_id in retained_ids
    )
    weighted_omitted_weight = semantic_weight_denominator - weighted_retained_weight
    required_ids = {
        atom.atom_id for atom in universe_by_id.values() if atom.core_required
    }
    kernel_ids = _kernel_coverage(advance.state)
    step_metrics_available = advance.transition_committed and advance.consumed_delta
    previous_bytes = _persistent_bytes(previous)
    return _TransitionMetrics(
        step_input_bytes=raw_step_input_bytes if step_metrics_available else None,
        semantic_weight_denominator=semantic_weight_denominator,
        weighted_retained_weight=weighted_retained_weight,
        weighted_omitted_weight=weighted_omitted_weight,
        released_count=(
            len(step_sources - new_coverage) if step_metrics_available else None
        ),
        release_denominator_count=(
            len(step_sources) if step_metrics_available else None
        ),
        net_released_bytes=(
            raw_step_input_bytes - advance.persistent_bytes
            if step_metrics_available
            else None
        ),
        rejected_delta_bytes=0 if advance.consumed_delta else delta_bytes,
        committed_base_state_reduction_bytes=(
            max(0, previous_bytes - advance.persistent_bytes)
            if advance.transition_committed and not advance.consumed_delta
            else None
        ),
        coverage_intersection_count=len(previous_coverage & new_coverage),
        coverage_union_count=len(previous_coverage | new_coverage),
        previous_coverage_digest=_coverage_digest(
            previous_coverage,
            source_manifest_hash,
        ),
        new_coverage_digest=_coverage_digest(new_coverage, source_manifest_hash),
        kernel_coverage_count=len(required_ids & kernel_ids),
        kernel_coverage_denominator=len(required_ids),
        kernel_coverage_id_digests=tuple(
            _source_id_digest(source_id, source_manifest_hash)
            for source_id in sorted(kernel_ids)
        ),
        internal_continuity_break=not required_ids <= kernel_ids,
        internal_stale_current=bool(new_coverage - universe_ids),
    )


def _summary_ids(value: str) -> set[str]:
    identifiers: set[str] = set()
    for line in value.splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            identifiers.add(parts[2])
    return identifiers


def _role_counts(state: CapsuleState | str | None) -> dict[str, int]:
    if isinstance(state, CapsuleState):
        return dict(
            sorted(
                Counter(atom.role.value for atom in state.kernel + state.body).items()
            )
        )
    if isinstance(state, str):
        roles = [line.split("|", 1)[0] for line in state.splitlines() if "|" in line]
        return dict(sorted(Counter(roles).items()))
    return {}


def _capsule_advance(
    previous: CapsuleState | None,
    delta: tuple[SemanticAtom, ...],
    budget: int,
    config: ProtocolConfig,
    *,
    greedy: bool = False,
) -> tuple[CapsuleState, RecompositionResult]:
    policy = replace(config.weights, exact_threshold=-1) if greedy else config.weights
    result = recompose_capsule(
        RecompositionRequest(
            base_state=previous,
            delta=delta,
            byte_budget=budget,
            kernel_schema=default_kernel_schema(),
            loss_policy=policy,
            weight_version=config.weight_version,
        )
    )
    return result.state, result


def _advance_arm(
    arm: str,
    previous: CapsuleState | str | None,
    delta: tuple[SemanticAtom, ...],
    budget: int,
    config: ProtocolConfig,
) -> _AdvanceResult:
    started = perf_counter_ns()
    tracemalloc.start()
    result: RecompositionResult | None = None
    state: CapsuleState | str | None = previous
    try:
        if arm == "crm":
            state, result = _capsule_advance(
                previous if isinstance(previous, CapsuleState) else None,
                delta,
                budget,
                config,
            )
        elif arm == "crm_greedy":
            state, result = _capsule_advance(
                previous if isinstance(previous, CapsuleState) else None,
                delta,
                budget,
                config,
                greedy=True,
            )
        elif arm == "legacy_capsule":
            state = LegacyCapsuleBaseline().advance(
                previous if isinstance(previous, CapsuleState) else None,
                delta,
                budget,
                default_kernel_schema(),
                config.weights,
                config.weight_version,
            )
        elif arm == "recursive_summary":
            state = RecursiveSummaryBaseline().advance(
                previous if isinstance(previous, str) else "",
                delta,
                budget,
            )
        elif arm == "crm_no_projection":
            result = NoProjectionAblation().advance_with_diagnostics(
                previous if isinstance(previous, CapsuleState) else None,
                delta,
                budget,
                default_kernel_schema(),
                config.weights,
                config.weight_version,
            )
            state = result.state
        elif arm == "crm_no_kernel":
            result = NoKernelAblation().advance_with_diagnostics(
                previous if isinstance(previous, CapsuleState) else None,
                delta,
                budget,
                default_kernel_schema(),
                config.weights,
                config.weight_version,
            )
            state = result.state
        else:
            raise ValueError(f"unknown arm: {arm}")
        _, peak = tracemalloc.get_traced_memory()
        total_ns = perf_counter_ns() - started
        state_digest = (
            semantic_hash(state)
            if isinstance(state, CapsuleState)
            else sha256_text(state or "")
        )
        (
            persistent_bytes,
            kernel_payload_bytes,
            state_overhead_bytes,
            kernel_bytes,
            body_bytes,
        ) = _state_byte_partitions(state)
        accepted_budget_bytes = _accepted_budget(state, budget)
        outcome = (
            result.outcome.value
            if result is not None
            else (
                OutcomeState.NORMAL.value
                if (
                    (isinstance(state, CapsuleState) and bool(state.body))
                    or (isinstance(state, str) and bool(state))
                )
                else OutcomeState.KERNEL_ONLY.value
            )
        )
        return _AdvanceResult(
            state=state,
            state_hash=state_digest,
            requested_budget_bytes=budget,
            accepted_budget_bytes=accepted_budget_bytes,
            budget_overshoot_bytes=max(0, persistent_bytes - accepted_budget_bytes),
            persistent_bytes=persistent_bytes,
            kernel_payload_bytes=kernel_payload_bytes,
            state_overhead_bytes=state_overhead_bytes,
            kernel_bytes=kernel_bytes,
            body_bytes=body_bytes,
            outcome=outcome,
            error_risk=None if result is None else result.loss.error_risk,
            matrix_hash="" if result is None else result.matrix_hash,
            stage_ns=() if result is None else result.stage_ns,
            total_ns=total_ns,
            peak_workspace_bytes=peak,
            error_code="",
            transition_committed=True,
            consumed_delta=True if result is None else result.consumed_delta,
        )
    except (TypeError, ValueError, KeyError):
        _, peak = tracemalloc.get_traced_memory()
        total_ns = perf_counter_ns() - started
        digest = (
            semantic_hash(previous)
            if isinstance(previous, CapsuleState)
            else sha256_text(previous or "")
        )
        (
            persistent_bytes,
            kernel_payload_bytes,
            state_overhead_bytes,
            kernel_bytes,
            body_bytes,
        ) = _state_byte_partitions(previous)
        accepted_budget_bytes = _accepted_budget(previous, budget)
        return _AdvanceResult(
            state=previous,
            state_hash=digest,
            requested_budget_bytes=budget,
            accepted_budget_bytes=accepted_budget_bytes,
            budget_overshoot_bytes=max(0, persistent_bytes - accepted_budget_bytes),
            persistent_bytes=persistent_bytes,
            kernel_payload_bytes=kernel_payload_bytes,
            state_overhead_bytes=state_overhead_bytes,
            kernel_bytes=kernel_bytes,
            body_bytes=body_bytes,
            outcome="failure",
            error_risk=None,
            matrix_hash="",
            stage_ns=(),
            total_ns=total_ns,
            peak_workspace_bytes=peak,
            error_code="ADVANCE_FAILED",
            transition_committed=False,
            consumed_delta=False,
        )
    finally:
        tracemalloc.stop()


def _unknown(query_id: str) -> ProjectionResult:
    return ProjectionResult(
        query_id=query_id,
        text=UNKNOWN,
        selected_atom_ids=(),
        selected_covered_ids=(),
        byte_cost=utf8_bytes(UNKNOWN),
        supported=False,
    )


def _project_arm(
    arm: str,
    view: FrozenStateView,
    query: QuerySpec,
    byte_budget: int,
) -> ProjectionResult:
    if arm == "recursive_summary":
        state = view.read_summary()
        return RecursiveSummaryBaseline().project(state, query, byte_budget)
    state = view.read_capsule()
    if arm == "crm_no_projection":
        return NoProjectionAblation().project(state, query.query_id, byte_budget)
    return project_query(state, query, byte_budget)


def _execute_projection(
    arm: str,
    state: CapsuleState | str | None,
    state_hash: str,
    query: QuerySpec | None,
    byte_budget: int,
    *,
    advance_error: str,
    query_id: str | None = None,
) -> _QueryExecution:
    trace = QueryReadTrace()
    resolved_query_id = query.query_id if query is not None else (query_id or "")
    if query is None:
        projection = _unknown(resolved_query_id)
        error_code = "QUERY_MISSING"
    elif advance_error:
        projection = _unknown(resolved_query_id)
        error_code = advance_error
    else:
        view = FrozenStateView(state, state_hash, trace)
        try:
            projection = _project_arm(arm, view, query, byte_budget)
            error_code = ""
        except ForbiddenQueryRead:
            projection = _unknown(resolved_query_id)
            error_code = "FORBIDDEN_QUERY_READ"
        except (TypeError, ValueError, KeyError):
            projection = _unknown(resolved_query_id)
            error_code = "PROJECTION_FAILED"
    if trace.fallback_reads:
        projection = _unknown(resolved_query_id)
        error_code = "FORBIDDEN_QUERY_READ"
    query_source = trace.query_source
    return _QueryExecution(
        projection=projection,
        error_code=error_code,
        query_source=query_source,
        query_source_state_hash=(
            state_hash if query_source in {"frozen_capsule", "frozen_summary"} else None
        ),
        fallback_reads=trace.fallback_reads,
        read_trace=trace.as_dict(),
    )


def _expected_query_ids(stream_id: str, generation: int) -> tuple[str, ...]:
    try:
        stream_index = int(stream_id.rsplit("-", 1)[1])
    except (IndexError, ValueError) as error:
        raise ValueError(f"invalid stream id: {stream_id}") from error
    return tuple(
        f"s{stream_index:02d}-g{generation:02d}-q{position:02d}"
        for position in range(6)
    )


def _learning_event(
    checkpoint_id: str,
    stream_id: str,
    generation: int,
    multiplier: float,
    arm: str,
    advance: _AdvanceResult,
    transition: _TransitionMetrics,
    weight_version: str,
    history_bytes: int,
    delta_bytes: int,
    source_manifest_hash: str,
) -> dict[str, object]:
    row = {
        "kind": "checkpoint",
        "checkpoint_id": checkpoint_id,
        "stream_id": stream_id,
        "generation": generation,
        "budget_multiplier": multiplier,
        "arm": arm,
        "state_hash": advance.state_hash,
        "source_manifest_hash": source_manifest_hash,
        "id_digest_domain": _SOURCE_ID_DIGEST_DOMAIN,
        "weight_version": weight_version,
        "matrix_hash": advance.matrix_hash,
        "role_counts": _role_counts(advance.state),
        "selected_count": sum(_role_counts(advance.state).values()),
        "released_count": transition.released_count,
        "release_denominator_count": transition.release_denominator_count,
        "requested_budget_bytes": advance.requested_budget_bytes,
        "accepted_budget_bytes": advance.accepted_budget_bytes,
        "budget_overshoot_bytes": advance.budget_overshoot_bytes,
        "persistent_bytes": advance.persistent_bytes,
        "kernel_payload_bytes": advance.kernel_payload_bytes,
        "state_overhead_bytes": advance.state_overhead_bytes,
        "kernel_bytes": advance.kernel_bytes,
        "body_bytes": advance.body_bytes,
        "history_bytes": history_bytes,
        "step_input_bytes": transition.step_input_bytes,
        "delta_bytes": delta_bytes,
        "net_released_bytes": transition.net_released_bytes,
        "transition_committed": advance.transition_committed,
        "consumed_delta": advance.consumed_delta,
        "rejected_delta_bytes": transition.rejected_delta_bytes,
        "committed_base_state_reduction_bytes": (
            transition.committed_base_state_reduction_bytes
        ),
        "semantic_weight_denominator": transition.semantic_weight_denominator,
        "weighted_retained_weight": transition.weighted_retained_weight,
        "weighted_omitted_weight": transition.weighted_omitted_weight,
        "error_risk": advance.error_risk,
        "coverage_intersection_count": transition.coverage_intersection_count,
        "coverage_union_count": transition.coverage_union_count,
        "previous_coverage_digest": transition.previous_coverage_digest,
        "new_coverage_digest": transition.new_coverage_digest,
        "kernel_coverage_count": transition.kernel_coverage_count,
        "kernel_coverage_denominator": transition.kernel_coverage_denominator,
        "kernel_coverage_id_digests": transition.kernel_coverage_id_digests,
        "internal_continuity_break": transition.internal_continuity_break,
        "internal_stale_current": transition.internal_stale_current,
        "peak_workspace_bytes": advance.peak_workspace_bytes,
        "stage_ns": dict(advance.stage_ns),
        "total_ns": advance.total_ns,
        "outcome": advance.outcome,
        "error_code": advance.error_code,
    }
    return row


def _zero_delta_events(
    stream_id: str,
    states: dict[tuple[float, str], CapsuleState | str | None],
    config: ProtocolConfig,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    schema = default_kernel_schema()
    kernel_ceiling = derive_kernel_ceiling(schema)
    for multiplier in config.budget_multipliers:
        budget = round(multiplier * kernel_ceiling)
        for arm in ("crm", "legacy_capsule", "recursive_summary"):
            current = states[(multiplier, arm)]
            if isinstance(current, CapsuleState):
                initial_hash = semantic_hash(current)
            else:
                initial_hash = sha256_text(current or "")
            probe = current
            round_hashes: list[str] = []
            first_unstable_round: int | None = None
            for round_number in range(1, config.zero_delta_rounds + 1):
                advanced = _advance_arm(arm, probe, (), budget, config)
                probe = advanced.state
                round_hashes.append(advanced.state_hash)
                if first_unstable_round is None and advanced.state_hash != initial_hash:
                    first_unstable_round = round_number
            final_hash = round_hashes[-1] if round_hashes else initial_hash
            rows.append(
                {
                    "kind": "zero_delta",
                    "stream_id": stream_id,
                    "budget_multiplier": multiplier,
                    "arm": arm,
                    "rounds": config.zero_delta_rounds,
                    "initial_hash": initial_hash,
                    "round_hashes": tuple(round_hashes),
                    "final_hash": final_hash,
                    "first_unstable_round": first_unstable_round,
                    "stable": first_unstable_round is None,
                    "weight_version": config.weight_version,
                }
            )
    return rows


def _squeeze_events(
    stream_id: str,
    states: dict[tuple[float, str], CapsuleState | str | None],
    config: ProtocolConfig,
) -> list[dict[str, object]]:
    if not config.budget_multipliers:
        return []
    start_multiplier = max(config.budget_multipliers)
    state = states[(start_multiplier, "crm")]
    if not isinstance(state, CapsuleState):
        return []
    rows: list[dict[str, object]] = []
    kernel_ceiling = derive_kernel_ceiling(default_kernel_schema())
    for multiplier in config.budget_multipliers:
        budget = round(multiplier * kernel_ceiling)
        advanced = _advance_arm("crm", state, (), budget, config)
        if isinstance(advanced.state, CapsuleState):
            state = advanced.state
        rows.append(
            {
                "kind": "budget_squeeze",
                "stream_id": stream_id,
                "budget_multiplier": multiplier,
                "state_hash": advanced.state_hash,
                "persistent_bytes": advanced.persistent_bytes,
                "outcome": advanced.outcome,
                "weight_version": config.weight_version,
            }
        )
    return rows


def run_protocol(
    config_path: str | Path,
    runtime_path: str | Path,
    query_path: str | Path,
    output_root: str | Path,
) -> Path:
    """Run every budgeted arm while revealing queries only after state freeze."""
    config = load_protocol_config(config_path)
    runtime_file = Path(runtime_path)
    source_manifest_hash = hashlib.sha256(runtime_file.read_bytes()).hexdigest()
    streams = _load_runtime(runtime_file)
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    records_path = output / "records.jsonl"
    learning_path = output / "learning-events.jsonl"
    manifest_path = output / "state-hash-manifest.json"
    records: list[dict[str, object]] = []
    learning: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []
    kernel_ceiling = derive_kernel_ceiling(default_kernel_schema())

    for stream in sorted(streams, key=lambda item: str(item["stream_id"])):
        stream_id = str(stream["stream_id"])
        history_bytes = 0
        measurement_ledger: dict[str, _MeasurementAtom] = {}
        states: dict[tuple[float, str], CapsuleState | str | None] = {
            (multiplier, arm): "" if arm == "recursive_summary" else None
            for multiplier in config.budget_multipliers
            for arm in ARM_ORDER
        }
        generations = stream["generations"]
        if not isinstance(generations, tuple):
            raise ValueError("runtime generations must be immutable after loading")
        for generation_row in sorted(
            generations, key=lambda item: int(item["generation"])
        ):
            generation = int(generation_row["generation"])
            delta = generation_row["events"]
            if not isinstance(delta, tuple):
                raise ValueError("runtime events must be immutable after loading")
            delta_bytes = utf8_bytes(canonical_json(delta))
            history_bytes += delta_bytes
            _update_measurement_ledger(measurement_ledger, delta)
            measurement_universe = _measurement_universe(measurement_ledger)
            delta_source_ids = _delta_coverage(delta)
            for multiplier in config.budget_multipliers:
                budget = round(multiplier * kernel_ceiling)
                advances: dict[str, _AdvanceResult] = {}
                checkpoint_ids: dict[str, str] = {}
                for arm in ARM_ORDER:
                    key = (multiplier, arm)
                    previous_state = states[key]
                    raw_step_input_bytes = (
                        _persistent_bytes(previous_state) + delta_bytes
                    )
                    advance = _advance_arm(
                        arm,
                        previous_state,
                        delta,
                        budget,
                        config,
                    )
                    transition = _measure_transition(
                        previous_state,
                        delta_source_ids,
                        advance,
                        measurement_universe,
                        raw_step_input_bytes,
                        delta_bytes,
                        source_manifest_hash,
                    )
                    states[key] = advance.state
                    advances[arm] = advance
                    checkpoint_id = _checkpoint_id(
                        stream_id,
                        generation,
                        multiplier,
                        arm,
                        source_manifest_hash,
                    )
                    checkpoint_ids[arm] = checkpoint_id
                    manifest_rows.append(
                        {
                            "checkpoint_id": checkpoint_id,
                            "stream_id": stream_id,
                            "generation": generation,
                            "budget_multiplier": multiplier,
                            "arm": arm,
                            "state_hash": advance.state_hash,
                            "source_manifest_hash": source_manifest_hash,
                        }
                    )
                    learning.append(
                        _learning_event(
                            checkpoint_id,
                            stream_id,
                            generation,
                            multiplier,
                            arm,
                            advance,
                            transition,
                            config.weight_version,
                            history_bytes,
                            delta_bytes,
                            source_manifest_hash,
                        )
                    )

                _write_json(
                    manifest_path,
                    {"schema_version": 1, "checkpoints": manifest_rows},
                )
                revealed = _load_public_queries(Path(query_path))
                query_executions: dict[str, list[_QueryExecution]] = {
                    arm: [] for arm in ARM_ORDER
                }
                for query_id in _expected_query_ids(stream_id, generation):
                    query = revealed.get(query_id)
                    for arm in ARM_ORDER:
                        advance = advances[arm]
                        execution = _execute_projection(
                            arm,
                            advance.state,
                            advance.state_hash,
                            query,
                            config.query_injection_bytes,
                            advance_error=advance.error_code,
                            query_id=query_id,
                        )
                        projection = execution.projection
                        records.append(
                            {
                                "checkpoint_id": checkpoint_ids[arm],
                                "stream_id": stream_id,
                                "generation": generation,
                                "budget_multiplier": multiplier,
                                "arm": arm,
                                "query_id": query_id,
                                "query_role": None
                                if query is None
                                else query.role.value,
                                "projection_budget_bytes": config.query_injection_bytes,
                                "query_source": execution.query_source,
                                "query_source_state_hash": (
                                    execution.query_source_state_hash
                                ),
                                "query_read_trace": execution.read_trace,
                                "fallback_reads": execution.fallback_reads,
                                "answer_text": projection.text,
                                "selected_atom_ids": projection.selected_atom_ids,
                                "selected_covered_ids": projection.selected_covered_ids,
                                "projection_bytes": projection.byte_cost,
                                "supported": projection.supported,
                                "error_code": execution.error_code,
                            }
                        )
                        query_executions[arm].append(execution)
                for arm in ARM_ORDER:
                    executions = query_executions[arm]
                    projections = [execution.projection for execution in executions]
                    learning.append(
                        {
                            "kind": "query_outcome",
                            "checkpoint_id": checkpoint_ids[arm],
                            "stream_id": stream_id,
                            "generation": generation,
                            "budget_multiplier": multiplier,
                            "arm": arm,
                            "supported_count": sum(
                                projection.supported for projection in projections
                            ),
                            "omission_count": sum(
                                not projection.supported for projection in projections
                            ),
                            "selected_count": sum(
                                len(projection.selected_covered_ids)
                                for projection in projections
                            ),
                            "error_count": sum(
                                bool(execution.error_code) for execution in executions
                            ),
                            "fallback_reads": sum(
                                execution.fallback_reads for execution in executions
                            ),
                            "weight_version": config.weight_version,
                        }
                    )
                del revealed
                del query_executions
            generation_row["events"] = ()
            del delta
        learning.extend(_zero_delta_events(stream_id, states, config))
        learning.extend(_squeeze_events(stream_id, states, config))

    _write_jsonl(records_path, records)
    _write_jsonl(learning_path, learning)
    if not manifest_path.exists():
        _write_json(manifest_path, {"schema_version": 1, "checkpoints": []})
    return records_path
