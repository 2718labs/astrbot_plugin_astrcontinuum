"""Phase-separated materialization and freeze-before-reveal execution."""

from __future__ import annotations

import hashlib
import json
import tracemalloc
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter_ns
from typing import Any

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


@dataclass(frozen=True, slots=True)
class _AdvanceResult:
    state: CapsuleState | str | None
    state_hash: str
    persistent_bytes: int
    outcome: str
    released_count: int
    matrix_hash: str
    stage_ns: tuple[tuple[str, int], ...]
    total_ns: int
    peak_workspace_bytes: int
    error_code: str


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


def _capsule_ids(state: CapsuleState | None) -> set[str]:
    if state is None:
        return set()
    return {atom.atom_id for atom in state.kernel + state.body}


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
            state = NoProjectionAblation().advance(
                previous if isinstance(previous, CapsuleState) else None,
                delta,
                budget,
                default_kernel_schema(),
                config.weights,
                config.weight_version,
            )
        elif arm == "crm_no_kernel":
            state = NoKernelAblation().advance(
                previous if isinstance(previous, CapsuleState) else None,
                delta,
                budget,
                default_kernel_schema(),
                config.weights,
                config.weight_version,
            )
        else:
            raise ValueError(f"unknown arm: {arm}")
        _, peak = tracemalloc.get_traced_memory()
        total_ns = perf_counter_ns() - started
        if isinstance(state, CapsuleState):
            old_ids = _capsule_ids(
                previous if isinstance(previous, CapsuleState) else None
            )
            new_ids = _capsule_ids(state)
            released_count = (
                len(result.loss.released_atom_ids)
                if result is not None
                else len(old_ids - new_ids)
            )
            state_digest = semantic_hash(state)
            persistent_bytes = utf8_bytes(canonical_json(state))
            outcome = (
                result.outcome.value
                if result is not None
                else (
                    OutcomeState.NORMAL.value
                    if state.body
                    else OutcomeState.KERNEL_ONLY.value
                )
            )
        else:
            old_ids = _summary_ids(previous if isinstance(previous, str) else "")
            new_ids = _summary_ids(state or "")
            released_count = len(old_ids - new_ids)
            state_digest = sha256_text(state or "")
            persistent_bytes = utf8_bytes(state or "")
            outcome = (
                OutcomeState.NORMAL.value if state else OutcomeState.KERNEL_ONLY.value
            )
        return _AdvanceResult(
            state=state,
            state_hash=state_digest,
            persistent_bytes=persistent_bytes,
            outcome=outcome,
            released_count=released_count,
            matrix_hash="" if result is None else result.matrix_hash,
            stage_ns=() if result is None else result.stage_ns,
            total_ns=total_ns,
            peak_workspace_bytes=peak,
            error_code="",
        )
    except (TypeError, ValueError, KeyError):
        _, peak = tracemalloc.get_traced_memory()
        total_ns = perf_counter_ns() - started
        if isinstance(previous, CapsuleState):
            digest = semantic_hash(previous)
            persistent_bytes = utf8_bytes(canonical_json(previous))
        elif isinstance(previous, str):
            digest = sha256_text(previous)
            persistent_bytes = utf8_bytes(previous)
        else:
            digest = sha256_text("")
            persistent_bytes = 0
        return _AdvanceResult(
            state=previous,
            state_hash=digest,
            persistent_bytes=persistent_bytes,
            outcome="failure",
            released_count=0,
            matrix_hash="",
            stage_ns=(),
            total_ns=total_ns,
            peak_workspace_bytes=peak,
            error_code="ADVANCE_FAILED",
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
    state: CapsuleState | str | None,
    query: QuerySpec,
    byte_budget: int,
) -> ProjectionResult:
    if arm == "recursive_summary" and isinstance(state, str):
        return RecursiveSummaryBaseline().project(state, query, byte_budget)
    if arm == "crm_no_projection" and isinstance(state, CapsuleState):
        return NoProjectionAblation().project(state, query.query_id, byte_budget)
    if isinstance(state, CapsuleState):
        return project_query(state, query, byte_budget)
    return _unknown(query.query_id)


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
    stream_id: str,
    generation: int,
    multiplier: float,
    arm: str,
    advance: _AdvanceResult,
    weight_version: str,
) -> dict[str, object]:
    return {
        "kind": "checkpoint",
        "stream_id": stream_id,
        "generation": generation,
        "budget_multiplier": multiplier,
        "arm": arm,
        "weight_version": weight_version,
        "matrix_hash": advance.matrix_hash,
        "role_counts": _role_counts(advance.state),
        "selected_count": sum(_role_counts(advance.state).values()),
        "released_count": advance.released_count,
        "persistent_bytes": advance.persistent_bytes,
        "peak_workspace_bytes": advance.peak_workspace_bytes,
        "stage_ns": dict(advance.stage_ns),
        "total_ns": advance.total_ns,
        "outcome": advance.outcome,
        "error_code": advance.error_code,
    }


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
            stable = True
            for _ in range(config.zero_delta_rounds):
                advanced = _advance_arm(arm, probe, (), budget, config)
                probe = advanced.state
                stable = stable and advanced.state_hash == initial_hash
            rows.append(
                {
                    "kind": "zero_delta",
                    "stream_id": stream_id,
                    "budget_multiplier": multiplier,
                    "arm": arm,
                    "rounds": config.zero_delta_rounds,
                    "stable": stable,
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
    streams = _load_runtime(Path(runtime_path))
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
            for multiplier in config.budget_multipliers:
                budget = round(multiplier * kernel_ceiling)
                advances: dict[str, _AdvanceResult] = {}
                for arm in ARM_ORDER:
                    key = (multiplier, arm)
                    advance = _advance_arm(
                        arm,
                        states[key],
                        delta,
                        budget,
                        config,
                    )
                    states[key] = advance.state
                    advances[arm] = advance
                    manifest_rows.append(
                        {
                            "stream_id": stream_id,
                            "generation": generation,
                            "budget_multiplier": multiplier,
                            "budget_bytes": budget,
                            "arm": arm,
                            "state_hash": advance.state_hash,
                            "persistent_bytes": advance.persistent_bytes,
                            "outcome": advance.outcome,
                            "released_count": advance.released_count,
                            "error_code": advance.error_code,
                        }
                    )
                    learning.append(
                        _learning_event(
                            stream_id,
                            generation,
                            multiplier,
                            arm,
                            advance,
                            config.weight_version,
                        )
                    )

                _write_json(
                    manifest_path,
                    {"schema_version": 1, "checkpoints": manifest_rows},
                )
                revealed = _load_public_queries(Path(query_path))
                query_outcomes: dict[str, list[ProjectionResult]] = {
                    arm: [] for arm in ARM_ORDER
                }
                for query_id in _expected_query_ids(stream_id, generation):
                    query = revealed.get(query_id)
                    for arm in ARM_ORDER:
                        advance = advances[arm]
                        if query is None:
                            projection = _unknown(query_id)
                            error_code = "QUERY_MISSING"
                        elif advance.error_code:
                            projection = _unknown(query_id)
                            error_code = advance.error_code
                        else:
                            try:
                                projection = _project_arm(
                                    arm,
                                    advance.state,
                                    query,
                                    config.query_injection_bytes,
                                )
                                error_code = ""
                            except (TypeError, ValueError, KeyError):
                                projection = _unknown(query_id)
                                error_code = "PROJECTION_FAILED"
                        records.append(
                            {
                                "stream_id": stream_id,
                                "generation": generation,
                                "budget_multiplier": multiplier,
                                "budget_bytes": budget,
                                "arm": arm,
                                "query_id": query_id,
                                "answer_text": projection.text,
                                "selected_atom_ids": projection.selected_atom_ids,
                                "selected_covered_ids": projection.selected_covered_ids,
                                "projection_bytes": projection.byte_cost,
                                "supported": projection.supported,
                                "state_hash": advance.state_hash,
                                "persistent_bytes": advance.persistent_bytes,
                                "outcome": advance.outcome,
                                "released_count": advance.released_count,
                                "stage_ns": dict(advance.stage_ns),
                                "total_ns": advance.total_ns,
                                "peak_workspace_bytes": advance.peak_workspace_bytes,
                                "error_code": error_code,
                            }
                        )
                        query_outcomes[arm].append(projection)
                for arm in ARM_ORDER:
                    projections = query_outcomes[arm]
                    learning.append(
                        {
                            "kind": "query_outcome",
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
                                bool(row.get("error_code"))
                                for row in records[-len(ARM_ORDER) * 6 :]
                                if row["arm"] == arm
                            ),
                            "weight_version": config.weight_version,
                        }
                    )
                del revealed
                del query_outcomes
            generation_row["events"] = ()
            del delta
        learning.extend(_zero_delta_events(stream_id, states, config))
        learning.extend(_squeeze_events(stream_id, states, config))

    _write_jsonl(records_path, records)
    _write_jsonl(learning_path, learning)
    if not manifest_path.exists():
        _write_json(manifest_path, {"schema_version": 1, "checkpoints": []})
    return records_path
