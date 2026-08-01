"""Deterministic preregistered runtime, query, and gold protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from crm_experiment.contracts import (
    AtomRole,
    AtomStatus,
    LossPolicy,
    QueryGold,
    QuerySpec,
    SemanticAtom,
)

_QUERY_ROLES = (
    AtomRole.ROOT_GOAL,
    AtomRole.DECISION,
    AtomRole.HARD_CONSTRAINT,
    AtomRole.EXACT_ANCHOR,
    AtomRole.CONTEXT,
    AtomRole.OPEN_LOOP,
)


@dataclass(frozen=True, slots=True)
class ProtocolConfig:
    schema_version: int
    stream_count: int
    generation_count: int
    queries_per_generation: int
    seed_start: int
    budget_multipliers: tuple[float, ...]
    main_budget_multiplier: float
    main_generation: int
    query_injection_bytes: int
    zero_delta_rounds: int
    bootstrap_replicates: int
    bootstrap_seed: int
    noninferiority_margin_pp: float
    weights: LossPolicy
    weight_version: str


@dataclass(frozen=True, slots=True)
class GenerationCase:
    generation: int
    events: tuple[SemanticAtom, ...]
    queries: tuple[QuerySpec, ...]


@dataclass(frozen=True, slots=True)
class StreamCase:
    stream_id: str
    generations: tuple[GenerationCase, ...]


@dataclass(frozen=True, slots=True)
class ProtocolBundle:
    streams: tuple[StreamCase, ...]
    public_queries: tuple[QuerySpec, ...]
    sealed_gold: tuple[QueryGold, ...]


def _as_int(raw: object, name: str, *, positive: bool = False) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{name} must be an integer")
    if positive and raw <= 0:
        raise ValueError(f"{name} must be positive")
    return raw


def _as_float(raw: object, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be numeric")
    return float(raw)


def load_protocol_config(path: str | Path) -> ProtocolConfig:
    """Load and validate the frozen JSON protocol configuration."""
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("protocol config must be a JSON object")

    schema_version = _as_int(raw.get("schema_version"), "schema_version")
    if schema_version != 1:
        raise ValueError("unsupported protocol schema_version")
    stream_count = _as_int(raw.get("stream_count"), "stream_count", positive=True)
    generation_count = _as_int(
        raw.get("generation_count"), "generation_count", positive=True
    )
    queries_per_generation = _as_int(
        raw.get("queries_per_generation"),
        "queries_per_generation",
        positive=True,
    )
    seed_start = _as_int(raw.get("seed_start"), "seed_start")
    multipliers_raw = raw.get("budget_multipliers")
    if not isinstance(multipliers_raw, list) or not multipliers_raw:
        raise ValueError("budget_multipliers must be a non-empty list")
    budget_multipliers = tuple(
        _as_float(value, "budget_multipliers item") for value in multipliers_raw
    )
    if any(value <= 0 for value in budget_multipliers):
        raise ValueError("budget_multipliers must be positive")

    weights_raw = raw.get("weights")
    if not isinstance(weights_raw, dict):
        raise ValueError("weights must be a JSON object")
    weight_version = weights_raw.get("version")
    if not isinstance(weight_version, str) or not weight_version:
        raise ValueError("weights.version must be a non-empty string")
    weights = LossPolicy(
        gamma=_as_float(weights_raw.get("gamma"), "weights.gamma"),
        rho=_as_float(weights_raw.get("rho"), "weights.rho"),
        risk_ceiling=_as_float(weights_raw.get("risk_ceiling"), "weights.risk_ceiling"),
        exact_threshold=_as_int(
            weights_raw.get("exact_threshold"), "weights.exact_threshold"
        ),
    )
    if weights.risk_ceiling < 0 or weights.exact_threshold < 0:
        raise ValueError("weights risk ceiling and exact threshold must be nonnegative")

    config = ProtocolConfig(
        schema_version=schema_version,
        stream_count=stream_count,
        generation_count=generation_count,
        queries_per_generation=queries_per_generation,
        seed_start=seed_start,
        budget_multipliers=budget_multipliers,
        main_budget_multiplier=_as_float(
            raw.get("main_budget_multiplier"), "main_budget_multiplier"
        ),
        main_generation=_as_int(raw.get("main_generation"), "main_generation"),
        query_injection_bytes=_as_int(
            raw.get("query_injection_bytes"),
            "query_injection_bytes",
            positive=True,
        ),
        zero_delta_rounds=_as_int(
            raw.get("zero_delta_rounds"), "zero_delta_rounds", positive=True
        ),
        bootstrap_replicates=_as_int(
            raw.get("bootstrap_replicates"),
            "bootstrap_replicates",
            positive=True,
        ),
        bootstrap_seed=_as_int(raw.get("bootstrap_seed"), "bootstrap_seed"),
        noninferiority_margin_pp=_as_float(
            raw.get("noninferiority_margin_pp"), "noninferiority_margin_pp"
        ),
        weights=weights,
        weight_version=weight_version,
    )
    if config.main_budget_multiplier not in config.budget_multipliers:
        raise ValueError("main_budget_multiplier must be one of budget_multipliers")
    if not 1 <= config.main_generation <= config.generation_count:
        raise ValueError("main_generation must be within generation_count")
    if config.queries_per_generation != len(_QUERY_ROLES):
        raise ValueError("queries_per_generation must equal the frozen six-role order")
    return config


def _atom(
    stream_index: int,
    seed: int,
    generation: int,
    role: AtomRole,
    semantic_key: str,
    revision: int,
    text: str,
    *,
    exact: bool = False,
    core_required: bool = False,
) -> SemanticAtom:
    atom_id = f"s{stream_index:02d}-g{generation:02d}-{semantic_key}-r{revision}"
    return SemanticAtom(
        atom_id=atom_id,
        semantic_keys=(semantic_key,),
        role=role,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=revision,
        as_of=generation,
        provenance=(f"seed-{seed}",),
        exact=exact,
        depends_on=(),
        weight=1.0 if core_required else 0.5,
        core_required=core_required,
        merge_depth=0,
        covered_atom_ids=(atom_id,),
    )


def _query_and_gold(
    stream_index: int,
    generation: int,
    current: dict[str, SemanticAtom],
    history: dict[str, list[SemanticAtom]],
) -> tuple[tuple[QuerySpec, ...], tuple[QueryGold, ...]]:
    query_specs: list[QuerySpec] = []
    gold: list[QueryGold] = []
    for position, role in enumerate(_QUERY_ROLES):
        semantic_key = {
            AtomRole.ROOT_GOAL: "goal",
            AtomRole.DECISION: "decision",
            AtomRole.HARD_CONSTRAINT: "constraint",
            AtomRole.EXACT_ANCHOR: "anchor",
            AtomRole.CONTEXT: "topic-0",
            AtomRole.OPEN_LOOP: "open-loop",
        }[role]
        query_id = f"s{stream_index:02d}-g{generation:02d}-q{position:02d}"
        query_specs.append(QuerySpec(query_id, role, semantic_key))
        active = current[semantic_key]
        previous = history[semantic_key][-2:-1]
        gold.append(
            QueryGold(
                query_id=query_id,
                required_atom_ids=(active.atom_id,),
                forbidden_atom_ids=tuple(item.atom_id for item in previous),
                required_text=(active.text,),
                forbidden_text=tuple(item.text for item in previous),
                core_query=role is not AtomRole.CONTEXT,
            )
        )
    return tuple(query_specs), tuple(gold)


def generate_protocol(config: ProtocolConfig) -> ProtocolBundle:
    """Generate deterministic runtime events with separate public query/gold data."""
    streams: list[StreamCase] = []
    public_queries: list[QuerySpec] = []
    sealed_gold: list[QueryGold] = []
    for stream_index in range(config.stream_count):
        seed = config.seed_start + stream_index
        current: dict[str, SemanticAtom] = {}
        history: dict[str, list[SemanticAtom]] = {
            key: []
            for key in (
                "goal",
                "decision",
                "constraint",
                "anchor",
                "topic-0",
                "topic-1",
                "open-loop",
            )
        }
        generations: list[GenerationCase] = []
        for generation in range(1, config.generation_count + 1):
            events: list[SemanticAtom] = []
            specifications = (
                (
                    AtomRole.ROOT_GOAL,
                    "goal",
                    f"goal for stream {stream_index} generation {generation}",
                ),
                (
                    AtomRole.DECISION,
                    "decision",
                    f"decision at stream {stream_index} generation {generation}",
                ),
                (
                    AtomRole.OPEN_LOOP,
                    "open-loop",
                    f"open loop at stream {stream_index} generation {generation}",
                ),
                (
                    AtomRole.CONTEXT,
                    "topic-0",
                    f"topic return A at stream {stream_index} generation {generation}",
                ),
                (
                    AtomRole.CONTEXT,
                    "topic-1",
                    f"topic return B at stream {stream_index} generation {generation}",
                ),
            )
            if generation == 1 or generation % 3 == 0:
                specifications += (
                    (
                        AtomRole.HARD_CONSTRAINT,
                        "constraint",
                        f"hard constraint for stream {stream_index} generation {generation}",
                    ),
                )
            if generation == 1 or generation % 2 == 0:
                specifications += (
                    (
                        AtomRole.EXACT_ANCHOR,
                        "anchor",
                        f"exact anchor S{stream_index:02d}-A-r{generation}",
                    ),
                )
            for role, key, text in specifications:
                revision = len(history[key]) + 1
                atom = _atom(
                    stream_index,
                    seed,
                    generation,
                    role,
                    key,
                    revision,
                    text,
                    exact=role is AtomRole.EXACT_ANCHOR,
                    core_required=role is not AtomRole.CONTEXT,
                )
                events.append(atom)
                current[key] = atom
                history[key].append(atom)
            events.sort(key=lambda atom: atom.atom_id)
            queries, gold = _query_and_gold(
                stream_index,
                generation,
                current,
                history,
            )
            generations.append(GenerationCase(generation, tuple(events), queries))
            public_queries.extend(queries)
            sealed_gold.extend(gold)
        streams.append(
            StreamCase(
                stream_id=f"stream-{stream_index:02d}",
                generations=tuple(generations),
            )
        )
    return ProtocolBundle(tuple(streams), tuple(public_queries), tuple(sealed_gold))


def no_kernel_overflow_fixture() -> StreamCase:
    """Return the isolated overflow case excluded from all main clusters."""
    events = tuple(
        _atom(
            stream_index=99,
            seed=271899,
            generation=1,
            role=AtomRole.HARD_CONSTRAINT,
            semantic_key=f"overflow-constraint-{index}",
            revision=1,
            text=f"overflow hard constraint {index}",
            core_required=True,
        )
        for index in range(5)
    )
    return StreamCase(
        stream_id="overflow-no-kernel",
        generations=(GenerationCase(1, events, ()),),
    )
