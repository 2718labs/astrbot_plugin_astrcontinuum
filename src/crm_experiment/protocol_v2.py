"""Deterministic, generator-only inputs and preflight for Protocol v2."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast

from crm_experiment.canonical import canonical_json, sha256_text, utf8_bytes
from crm_experiment.contracts import AtomRole, AtomStatus, QuerySpec
from crm_experiment.contracts_v2 import LogicalAtomV2
from crm_experiment.kernel_v2 import default_kernel_schema_v2

_CONFIG_HASH_DOMAIN = "crm-protocol-v2-config/v1"
_SUBJECT_INPUT_HASH_DOMAIN = "crm-protocol-v2-subject-input/v1"
_SHADOW_INPUT_HASH_DOMAIN = "crm-protocol-v2-shadow-input/v1"
_SHADOW_FULL_RETENTION_HASH_DOMAIN = "crm-protocol-v2-shadow-full-retention/v1"
_SHADOW_KERNEL_ONLY_HASH_DOMAIN = "crm-protocol-v2-shadow-kernel-only/v1"
_BUNDLE_INPUT_HASH_DOMAIN = "crm-protocol-v2-bundle-input/v1"
_EXPECTED_BUDGET_LABELS = ("8K", "4K", "2K", "1.5K", "K")
_EXPECTED_BUDGET_MULTIPLIERS = (8.0, 4.0, 2.0, 1.5, 1.0)
_EXPECTED_CORE_ROLES = (
    AtomRole.ROOT_GOAL,
    AtomRole.CURRENT_FOCUS,
    AtomRole.OPEN_LOOP,
    AtomRole.HARD_CONSTRAINT,
    AtomRole.DECISION,
)


class SourceStratumV2(StrEnum):
    """Frozen generator strata; only immutable strata are packing candidates."""

    CORE = "core"
    COMPRESSIBLE_IMMUTABLE_CONTEXT = "compressible_immutable_context"
    INCOMPRESSIBLE_IMMUTABLE_CONTEXT = "incompressible_immutable_context"
    MUTABLE_INELIGIBLE_CONTEXT = "mutable_ineligible_context"


_IMMUTABLE_CONTEXT_STRATA = (
    SourceStratumV2.COMPRESSIBLE_IMMUTABLE_CONTEXT,
    SourceStratumV2.INCOMPRESSIBLE_IMMUTABLE_CONTEXT,
)
_EXPECTED_STRATUM_KEYS = frozenset(stratum.value for stratum in SourceStratumV2)


@dataclass(frozen=True, slots=True)
class ProtocolV2Config:
    """Frozen generator inputs; K is always ``continuity_floor_bytes``."""

    protocol_id: str
    schema_version: int
    stream_count: int
    generation_count: int
    sealed_queries_per_generation: int
    continuity_floor_bytes: int
    budget_labels: tuple[str, ...]
    budget_multipliers: tuple[float, ...]
    kib_denominator_bytes: int
    terminal_active_source_kib_bounds: tuple[float, float]
    terminal_active_source_byte_bounds: tuple[int, int]
    core_roles: tuple[AtomRole, ...]
    stratum_text_bytes: Mapping[str, int]
    immutable_context_initial_items: int
    immutable_context_increment_per_generation: int
    mutable_ineligible_items_per_generation: int
    query_return_lag_generations: int
    seed_start: int
    config_hash: str

    @property
    def budget_bytes(self) -> tuple[int, ...]:
        """Return {8K, 4K, 2K, 1.5K, K} from the configured continuity floor."""
        return tuple(
            int(multiplier * self.continuity_floor_bytes)
            for multiplier in self.budget_multipliers
        )


_CONFIG_JSON_KEYS = frozenset(
    field.name for field in fields(ProtocolV2Config) if field.name != "config_hash"
)


@dataclass(frozen=True, slots=True)
class ProtocolSourceV2:
    """A v2 source plus fixed generator stratum metadata for future runners."""

    atom: LogicalAtomV2
    stratum: SourceStratumV2
    packing_eligible: bool


@dataclass(frozen=True, slots=True)
class SubjectRoundV2:
    """Inputs visible to the future subject lineage, never its shadow corpus."""

    stream_id: str
    generation: int
    updates: tuple[ProtocolSourceV2, ...]
    queries: tuple[QuerySpec, ...]
    subject_input_hash: str


@dataclass(frozen=True, slots=True)
class ShadowRoundV2:
    """Detached full-retention and kernel-only corpus lineage for later baselines."""

    stream_id: str
    generation: int
    full_retention_sources: tuple[ProtocolSourceV2, ...]
    kernel_only_sources: tuple[ProtocolSourceV2, ...]
    active_source_bytes: int
    full_retention_hash: str
    kernel_only_hash: str
    shadow_input_hash: str


@dataclass(frozen=True, slots=True)
class ProtocolV2QueryGold:
    """Sealed answer source bindings, deliberately separate from ``QuerySpec``."""

    query_id: str
    required_source_ids: tuple[str, ...]
    required_text: tuple[str, ...]
    is_topic_return: bool
    returned_generation: int | None


@dataclass(frozen=True, slots=True)
class ProtocolV2Stream:
    """One paired subject/shadow lineage sharing deterministic source updates."""

    stream_id: str
    subject_rounds: tuple[SubjectRoundV2, ...]
    shadow_rounds: tuple[ShadowRoundV2, ...]


@dataclass(frozen=True, slots=True)
class ProtocolV2Bundle:
    """Complete generated inputs, public queries, and sealed source-only gold."""

    protocol_id: str
    schema_version: int
    config_hash: str
    streams: tuple[ProtocolV2Stream, ...]
    public_queries: tuple[QuerySpec, ...]
    sealed_gold: tuple[ProtocolV2QueryGold, ...]
    input_hash: str


@dataclass(frozen=True, slots=True)
class ProtocolV2PreflightReport:
    """Generator/config-only verification receipt; it contains no arm outcomes."""

    protocol_id: str
    schema_version: int
    config_hash: str
    input_hash: str
    terminal_active_source_bytes: tuple[int, ...]
    terminal_active_source_kib: tuple[float, ...]
    checks: tuple[str, ...]
    passed: bool


def _required_int(raw: Mapping[str, object], name: str) -> int:
    value = raw.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _require_known_keys(
    raw: Mapping[str, object], allowed_keys: frozenset[str], object_name: str
) -> None:
    unknown_keys = sorted(set(raw).difference(allowed_keys))
    if unknown_keys:
        raise ValueError(
            f"{object_name} contains unknown keys: {', '.join(unknown_keys)}"
        )


def _required_string(raw: Mapping[str, object], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _required_string_tuple(raw: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = raw.get(name)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{name} must be a nonempty string array")
    return tuple(value)


def _required_number_tuple(
    raw: Mapping[str, object], name: str, expected_length: int
) -> tuple[float, ...]:
    value = raw.get(name)
    if not isinstance(value, list) or len(value) != expected_length:
        raise ValueError(f"{name} must be an array of length {expected_length}")
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
    ):
        raise ValueError(f"{name} must contain numbers")
    return tuple(float(item) for item in value)


def _required_int_tuple(
    raw: Mapping[str, object], name: str, expected_length: int
) -> tuple[int, ...]:
    value = raw.get(name)
    if not isinstance(value, list) or len(value) != expected_length:
        raise ValueError(f"{name} must be an array of length {expected_length}")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError(f"{name} must contain integers")
    return tuple(cast(int, item) for item in value)


def _required_stratum_lengths(raw: Mapping[str, object]) -> Mapping[str, int]:
    value = raw.get("stratum_text_bytes")
    if not isinstance(value, dict):
        raise ValueError("stratum_text_bytes must be an object")
    typed_value = cast(dict[str, object], value)
    _require_known_keys(
        typed_value,
        _EXPECTED_STRATUM_KEYS,
        "stratum_text_bytes",
    )
    converted: dict[str, int] = {}
    for name, length in typed_value.items():
        if (
            not isinstance(name, str)
            or isinstance(length, bool)
            or not isinstance(length, int)
        ):
            raise ValueError("stratum_text_bytes must map string names to integers")
        converted[name] = length
    return MappingProxyType(converted)


def _config_projection(config: ProtocolV2Config) -> dict[str, object]:
    return {
        field.name: getattr(config, field.name)
        for field in fields(config)
        if field.name != "config_hash"
    }


def _config_hash(config: ProtocolV2Config) -> str:
    return sha256_text(
        canonical_json(
            {"domain": _CONFIG_HASH_DOMAIN, "config": _config_projection(config)}
        )
    )


def _full_retention_hash(sources: tuple[ProtocolSourceV2, ...]) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": _SHADOW_FULL_RETENTION_HASH_DOMAIN,
                "sources": sources,
            }
        )
    )


def _kernel_only_hash(sources: tuple[ProtocolSourceV2, ...]) -> str:
    return sha256_text(
        canonical_json(
            {
                "domain": _SHADOW_KERNEL_ONLY_HASH_DOMAIN,
                "sources": sources,
            }
        )
    )


def _decimal_bound(value: float, denominator: int, rounding: str) -> int:
    return int((Decimal(str(value)) * Decimal(denominator)).to_integral_value(rounding))


def _validate_config(config: ProtocolV2Config) -> None:
    if config.config_hash != _config_hash(config):
        raise ValueError("config hash mismatch")
    if config.protocol_id != "crm-capsule-stress-v2":
        raise ValueError("protocol_id must be crm-capsule-stress-v2")
    if config.schema_version != 2:
        raise ValueError("schema_version must be 2")
    if (
        config.stream_count,
        config.generation_count,
        config.sealed_queries_per_generation,
    ) != (
        24,
        12,
        6,
    ):
        raise ValueError(
            "Protocol v2 shape must be 24 streams x 12 generations x 6 queries"
        )
    schema = default_kernel_schema_v2()
    if config.continuity_floor_bytes != schema.continuity_floor_bytes:
        raise ValueError("continuity_floor_bytes must equal the exact v2 kernel floor")
    if config.budget_labels != _EXPECTED_BUDGET_LABELS:
        raise ValueError("budget_labels must be {8K,4K,2K,1.5K,K}")
    if config.budget_multipliers != _EXPECTED_BUDGET_MULTIPLIERS:
        raise ValueError("budget_multipliers must be {8,4,2,1.5,1}")
    if config.budget_bytes != (36864, 18432, 9216, 6912, 4608):
        raise ValueError(
            "budget bytes must be derived only from continuity_floor_bytes"
        )
    if config.kib_denominator_bytes != 1024:
        raise ValueError("KiB denominator must be exactly 1024 bytes")
    if config.terminal_active_source_kib_bounds != (7.7, 7.9):
        raise ValueError("terminal active source KiB bounds must be [7.70, 7.90]")
    expected_byte_bounds = (
        _decimal_bound(
            config.terminal_active_source_kib_bounds[0],
            config.kib_denominator_bytes,
            ROUND_CEILING,
        ),
        _decimal_bound(
            config.terminal_active_source_kib_bounds[1],
            config.kib_denominator_bytes,
            ROUND_FLOOR,
        ),
    )
    if config.terminal_active_source_byte_bounds != expected_byte_bounds:
        raise ValueError(
            "physical byte bounds must derive from the configured KiB bounds"
        )
    if config.core_roles != _EXPECTED_CORE_ROLES:
        raise ValueError("Protocol v2 must update exactly the five frozen core roles")
    slots_by_role = {slot.role: slot for slot in schema.slots}
    if not set(config.core_roles).issubset(slots_by_role):
        raise ValueError("core roles must fit the frozen v2 kernel schema")
    expected_strata = {item.value for item in SourceStratumV2}
    if set(config.stratum_text_bytes) != expected_strata:
        raise ValueError("stratum_text_bytes must define every frozen stratum")
    if any(length <= 0 for length in config.stratum_text_bytes.values()):
        raise ValueError("stratum text lengths must be positive")
    if any(
        config.stratum_text_bytes[SourceStratumV2.CORE.value]
        > slots_by_role[role].max_text_bytes
        for role in config.core_roles
    ):
        raise ValueError("core source text length does not fit its kernel slot")
    if (
        config.immutable_context_initial_items,
        config.immutable_context_increment_per_generation,
        config.mutable_ineligible_items_per_generation,
        config.query_return_lag_generations,
    ) != (1, 1, 1, 4):
        raise ValueError(
            "Protocol v2 has a frozen growth and four-generation return schedule"
        )


def load_protocol_v2_config(path: Path) -> ProtocolV2Config:
    """Load and validate the frozen generator configuration without a runner."""
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise ValueError("protocol v2 config must be a JSON object with string keys")
    raw = cast(dict[str, object], parsed)
    _require_known_keys(raw, _CONFIG_JSON_KEYS, "protocol v2 config")
    core_role_values = _required_string_tuple(raw, "core_roles")
    try:
        core_roles = tuple(AtomRole(value) for value in core_role_values)
    except ValueError as error:
        raise ValueError("core_roles must contain known AtomRole values") from error
    provisional_config = ProtocolV2Config(
        protocol_id=_required_string(raw, "protocol_id"),
        schema_version=_required_int(raw, "schema_version"),
        stream_count=_required_int(raw, "stream_count"),
        generation_count=_required_int(raw, "generation_count"),
        sealed_queries_per_generation=_required_int(
            raw, "sealed_queries_per_generation"
        ),
        continuity_floor_bytes=_required_int(raw, "continuity_floor_bytes"),
        budget_labels=_required_string_tuple(raw, "budget_labels"),
        budget_multipliers=_required_number_tuple(raw, "budget_multipliers", 5),
        kib_denominator_bytes=_required_int(raw, "kib_denominator_bytes"),
        terminal_active_source_kib_bounds=cast(
            tuple[float, float],
            _required_number_tuple(raw, "terminal_active_source_kib_bounds", 2),
        ),
        terminal_active_source_byte_bounds=cast(
            tuple[int, int],
            _required_int_tuple(raw, "terminal_active_source_byte_bounds", 2),
        ),
        core_roles=core_roles,
        stratum_text_bytes=_required_stratum_lengths(raw),
        immutable_context_initial_items=_required_int(
            raw, "immutable_context_initial_items"
        ),
        immutable_context_increment_per_generation=_required_int(
            raw, "immutable_context_increment_per_generation"
        ),
        mutable_ineligible_items_per_generation=_required_int(
            raw, "mutable_ineligible_items_per_generation"
        ),
        query_return_lag_generations=_required_int(raw, "query_return_lag_generations"),
        seed_start=_required_int(raw, "seed_start"),
        config_hash="",
    )
    config = replace(
        provisional_config,
        config_hash=_config_hash(provisional_config),
    )
    _validate_config(config)
    return config


def _fixed_ascii_text(prefix: str, identity: str, suffix: str, byte_length: int) -> str:
    """Build an ASCII payload with an exactly prescribed UTF-8 byte length."""
    text = f"{prefix}{identity}{suffix}"
    if len(text) > byte_length:
        raise ValueError("fixed source label exceeds its configured byte length")
    return text + "." * (byte_length - len(text))


def _incompressible_text(identity: str, byte_length: int) -> str:
    """Use a deterministic digest stream with no shared format affixes."""
    chunks: list[str] = []
    counter = 0
    while len("".join(chunks)) < byte_length:
        chunks.append(
            sha256_text(f"crm-protocol-v2-incompressible|{identity}|{counter}")
        )
        counter += 1
    return "".join(chunks)[:byte_length]


def _source(
    *,
    config: ProtocolV2Config,
    stream_id: str,
    generation: int,
    role: AtomRole,
    semantic_key: str,
    revision: int,
    stratum: SourceStratumV2,
    text: str,
    packing_eligible: bool,
) -> ProtocolSourceV2:
    provenance = tuple(
        sorted(
            (
                f"protocol:{config.protocol_id}",
                f"stream:{stream_id}",
                f"stratum:{stratum.value}",
            )
        )
    )
    atom = LogicalAtomV2.create(
        source_label=f"{stream_id}:{generation}:{semantic_key}",
        semantic_keys=(semantic_key,),
        role=role,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=revision,
        as_of=generation,
        provenance=provenance,
        exact=False,
        depends_on=(),
        core_required=stratum is SourceStratumV2.CORE,
    )
    return ProtocolSourceV2(
        atom=atom,
        stratum=stratum,
        packing_eligible=packing_eligible,
    )


def _core_source(
    config: ProtocolV2Config,
    stream_id: str,
    generation: int,
    role: AtomRole,
) -> ProtocolSourceV2:
    semantic_key = f"{stream_id}/core/{role.value}"
    text = _fixed_ascii_text(
        "CORE|",
        f"{stream_id}|g{generation:02d}|{role.value}",
        "|ACTIVE",
        config.stratum_text_bytes[SourceStratumV2.CORE.value],
    )
    return _source(
        config=config,
        stream_id=stream_id,
        generation=generation,
        role=role,
        semantic_key=semantic_key,
        revision=generation,
        stratum=SourceStratumV2.CORE,
        text=text,
        packing_eligible=True,
    )


def _compressible_context_source(
    config: ProtocolV2Config,
    stream_id: str,
    generation: int,
    item_index: int,
) -> ProtocolSourceV2:
    semantic_key = (
        f"{stream_id}/context/compressible/g{generation:02d}/i{item_index:02d}"
    )
    text = _fixed_ascii_text(
        "CCTX|",
        f"{stream_id}|g{generation:02d}|i{item_index:02d}",
        "|IMMUTABLE",
        config.stratum_text_bytes[SourceStratumV2.COMPRESSIBLE_IMMUTABLE_CONTEXT.value],
    )
    return _source(
        config=config,
        stream_id=stream_id,
        generation=generation,
        role=AtomRole.CONTEXT,
        semantic_key=semantic_key,
        revision=1,
        stratum=SourceStratumV2.COMPRESSIBLE_IMMUTABLE_CONTEXT,
        text=text,
        packing_eligible=True,
    )


def _incompressible_context_source(
    config: ProtocolV2Config,
    stream_id: str,
    generation: int,
    item_index: int,
) -> ProtocolSourceV2:
    semantic_key = (
        f"{stream_id}/context/incompressible/g{generation:02d}/i{item_index:02d}"
    )
    text = _incompressible_text(
        f"{config.seed_start}|{stream_id}|{generation}|{item_index}",
        config.stratum_text_bytes[
            SourceStratumV2.INCOMPRESSIBLE_IMMUTABLE_CONTEXT.value
        ],
    )
    return _source(
        config=config,
        stream_id=stream_id,
        generation=generation,
        role=AtomRole.CONTEXT,
        semantic_key=semantic_key,
        revision=1,
        stratum=SourceStratumV2.INCOMPRESSIBLE_IMMUTABLE_CONTEXT,
        text=text,
        packing_eligible=True,
    )


def _mutable_context_source(
    config: ProtocolV2Config,
    stream_id: str,
    generation: int,
) -> ProtocolSourceV2:
    semantic_key = f"{stream_id}/context/mutable"
    text = _fixed_ascii_text(
        "MCTX|",
        f"{stream_id}|g{generation:02d}",
        "|INELIGIBLE",
        config.stratum_text_bytes[SourceStratumV2.MUTABLE_INELIGIBLE_CONTEXT.value],
    )
    return _source(
        config=config,
        stream_id=stream_id,
        generation=generation,
        role=AtomRole.CONTEXT,
        semantic_key=semantic_key,
        revision=generation,
        stratum=SourceStratumV2.MUTABLE_INELIGIBLE_CONTEXT,
        text=text,
        packing_eligible=False,
    )


def _source_sort_key(source: ProtocolSourceV2) -> str:
    return source.atom.source_id


def _subject_input_hash(
    config: ProtocolV2Config,
    stream_id: str,
    generation: int,
    updates: tuple[ProtocolSourceV2, ...],
    queries: tuple[QuerySpec, ...],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "config_hash": config.config_hash,
                "domain": _SUBJECT_INPUT_HASH_DOMAIN,
                "generation": generation,
                "protocol_id": config.protocol_id,
                "queries": queries,
                "schema_version": config.schema_version,
                "stream_id": stream_id,
                "updates": updates,
            }
        )
    )


def _shadow_input_hash(config: ProtocolV2Config, shadow_round: ShadowRoundV2) -> str:
    return sha256_text(
        canonical_json(
            {
                "config_hash": config.config_hash,
                "domain": _SHADOW_INPUT_HASH_DOMAIN,
                "full_retention_hash": shadow_round.full_retention_hash,
                "generation": shadow_round.generation,
                "kernel_only_hash": shadow_round.kernel_only_hash,
                "protocol_id": config.protocol_id,
                "schema_version": config.schema_version,
                "stream_id": shadow_round.stream_id,
            }
        )
    )


def _bundle_input_hash(
    protocol_id: str,
    schema_version: int,
    config_hash: str,
    streams: tuple[ProtocolV2Stream, ...],
    public_queries: tuple[QuerySpec, ...],
    sealed_gold: tuple[ProtocolV2QueryGold, ...],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "config_hash": config_hash,
                "domain": _BUNDLE_INPUT_HASH_DOMAIN,
                "protocol_id": protocol_id,
                "public_queries": public_queries,
                "schema_version": schema_version,
                "sealed_gold": sealed_gold,
                "streams": streams,
            }
        )
    )


def generate_protocol_v2(config: ProtocolV2Config) -> ProtocolV2Bundle:
    """Generate paired subject and shadow inputs without runner or arm behavior."""
    _validate_config(config)
    streams: list[ProtocolV2Stream] = []
    public_queries: list[QuerySpec] = []
    sealed_gold: list[ProtocolV2QueryGold] = []

    for stream_index in range(config.stream_count):
        stream_id = f"stream-{stream_index:02d}"
        active_by_key: dict[str, ProtocolSourceV2] = {}
        returnable_context_by_generation: dict[int, ProtocolSourceV2] = {}
        subject_rounds: list[SubjectRoundV2] = []
        shadow_rounds: list[ShadowRoundV2] = []

        for generation in range(1, config.generation_count + 1):
            core_updates = tuple(
                _core_source(config, stream_id, generation, role)
                for role in config.core_roles
            )
            immutable_item_count = (
                config.immutable_context_initial_items
                + (generation - 1) * config.immutable_context_increment_per_generation
            )
            compressible_updates = tuple(
                _compressible_context_source(config, stream_id, generation, item_index)
                for item_index in range(1, immutable_item_count + 1)
            )
            incompressible_updates = tuple(
                _incompressible_context_source(
                    config, stream_id, generation, item_index
                )
                for item_index in range(1, immutable_item_count + 1)
            )
            mutable_updates = tuple(
                _mutable_context_source(config, stream_id, generation)
                for _ in range(config.mutable_ineligible_items_per_generation)
            )
            updates = (
                core_updates
                + compressible_updates
                + incompressible_updates
                + mutable_updates
            )
            for source in updates:
                active_by_key[source.atom.semantic_keys[0]] = source
            returnable_context_by_generation[generation] = compressible_updates[0]

            core_by_role = {source.atom.role: source for source in core_updates}
            query_items: list[tuple[QuerySpec, ProtocolSourceV2, bool, int | None]] = []
            for role in config.core_roles:
                source = core_by_role[role]
                query_items.append(
                    (
                        QuerySpec(
                            query_id=f"v2:{stream_id}:g{generation:02d}:{role.value}",
                            role=role,
                            semantic_key=source.atom.semantic_keys[0],
                        ),
                        source,
                        False,
                        None,
                    )
                )
            if generation <= config.query_return_lag_generations:
                context_source = compressible_updates[0]
                returned_generation: int | None = None
                is_topic_return = False
            else:
                returned_generation = generation - config.query_return_lag_generations
                context_source = returnable_context_by_generation[returned_generation]
                is_topic_return = True
            query_items.append(
                (
                    QuerySpec(
                        query_id=f"v2:{stream_id}:g{generation:02d}:context",
                        role=AtomRole.CONTEXT,
                        semantic_key=context_source.atom.semantic_keys[0],
                    ),
                    context_source,
                    is_topic_return,
                    returned_generation,
                )
            )
            queries = tuple(item[0] for item in query_items)
            if len(queries) != config.sealed_queries_per_generation:
                raise ValueError(
                    "generated query count does not match the sealed contract"
                )
            subject_round = SubjectRoundV2(
                stream_id=stream_id,
                generation=generation,
                updates=updates,
                queries=queries,
                subject_input_hash=_subject_input_hash(
                    config, stream_id, generation, updates, queries
                ),
            )

            full_retention_sources = tuple(
                sorted(active_by_key.values(), key=_source_sort_key)
            )
            kernel_only_sources = tuple(
                sorted(
                    (
                        active_by_key[f"{stream_id}/core/{role.value}"]
                        for role in config.core_roles
                    ),
                    key=lambda source: source.atom.role.value,
                )
            )
            full_retention_hash = _full_retention_hash(full_retention_sources)
            kernel_only_hash = _kernel_only_hash(kernel_only_sources)
            provisional_shadow = ShadowRoundV2(
                stream_id=stream_id,
                generation=generation,
                full_retention_sources=full_retention_sources,
                kernel_only_sources=kernel_only_sources,
                active_source_bytes=sum(
                    utf8_bytes(source.atom.text) for source in full_retention_sources
                ),
                full_retention_hash=full_retention_hash,
                kernel_only_hash=kernel_only_hash,
                shadow_input_hash="",
            )
            shadow_round = ShadowRoundV2(
                stream_id=provisional_shadow.stream_id,
                generation=provisional_shadow.generation,
                full_retention_sources=provisional_shadow.full_retention_sources,
                kernel_only_sources=provisional_shadow.kernel_only_sources,
                active_source_bytes=provisional_shadow.active_source_bytes,
                full_retention_hash=provisional_shadow.full_retention_hash,
                kernel_only_hash=provisional_shadow.kernel_only_hash,
                shadow_input_hash=_shadow_input_hash(config, provisional_shadow),
            )
            subject_rounds.append(subject_round)
            shadow_rounds.append(shadow_round)
            public_queries.extend(queries)
            sealed_gold.extend(
                ProtocolV2QueryGold(
                    query_id=query.query_id,
                    required_source_ids=(source.atom.source_id,),
                    required_text=(source.atom.text,),
                    is_topic_return=is_topic_return,
                    returned_generation=returned_generation,
                )
                for query, source, is_topic_return, returned_generation in query_items
            )

        streams.append(
            ProtocolV2Stream(
                stream_id=stream_id,
                subject_rounds=tuple(subject_rounds),
                shadow_rounds=tuple(shadow_rounds),
            )
        )

    frozen_streams = tuple(streams)
    frozen_queries = tuple(public_queries)
    frozen_gold = tuple(sealed_gold)
    return ProtocolV2Bundle(
        protocol_id=config.protocol_id,
        schema_version=config.schema_version,
        config_hash=config.config_hash,
        streams=frozen_streams,
        public_queries=frozen_queries,
        sealed_gold=frozen_gold,
        input_hash=_bundle_input_hash(
            config.protocol_id,
            config.schema_version,
            config.config_hash,
            frozen_streams,
            frozen_queries,
            frozen_gold,
        ),
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"Protocol v2 preflight failed: {message}")


def _validate_hashes(config: ProtocolV2Config, bundle: ProtocolV2Bundle) -> None:
    _require(bundle.config_hash == config.config_hash, "config hash mismatch")
    _require(
        bundle.input_hash
        == _bundle_input_hash(
            bundle.protocol_id,
            bundle.schema_version,
            bundle.config_hash,
            bundle.streams,
            bundle.public_queries,
            bundle.sealed_gold,
        ),
        "bundle input hash mismatch",
    )
    for stream in bundle.streams:
        for subject_round, shadow_round in zip(
            stream.subject_rounds, stream.shadow_rounds, strict=True
        ):
            _require(
                subject_round.subject_input_hash
                == _subject_input_hash(
                    config,
                    subject_round.stream_id,
                    subject_round.generation,
                    subject_round.updates,
                    subject_round.queries,
                ),
                "subject input hash mismatch",
            )
            _require(
                shadow_round.full_retention_hash
                == _full_retention_hash(shadow_round.full_retention_sources),
                "full-retention hash mismatch",
            )
            _require(
                shadow_round.kernel_only_hash
                == _kernel_only_hash(shadow_round.kernel_only_sources),
                "kernel-only hash mismatch",
            )
            provisional_shadow = ShadowRoundV2(
                stream_id=shadow_round.stream_id,
                generation=shadow_round.generation,
                full_retention_sources=shadow_round.full_retention_sources,
                kernel_only_sources=shadow_round.kernel_only_sources,
                active_source_bytes=shadow_round.active_source_bytes,
                full_retention_hash=shadow_round.full_retention_hash,
                kernel_only_hash=shadow_round.kernel_only_hash,
                shadow_input_hash="",
            )
            _require(
                shadow_round.shadow_input_hash
                == _shadow_input_hash(config, provisional_shadow),
                "shadow input hash mismatch",
            )


def _validate_query_gold_shape(gold: ProtocolV2QueryGold) -> None:
    _require(
        len(gold.required_source_ids) == 1 and len(gold.required_text) == 1,
        "query gold must bind one source id and one text",
    )


def _required_gold_source(
    sources_by_id: Mapping[str, ProtocolSourceV2],
    gold: ProtocolV2QueryGold,
) -> ProtocolSourceV2:
    source = sources_by_id.get(gold.required_source_ids[0])
    _require(source is not None, "query gold source is not real")
    if source is None:
        raise ValueError("Protocol v2 preflight failed: query gold source is not real")
    return source


def _validate_query_source_binding(
    query: QuerySpec,
    gold: ProtocolV2QueryGold,
    source: ProtocolSourceV2,
    *,
    expected_generation: int,
) -> None:
    _require(
        gold.required_source_ids == (source.atom.source_id,),
        "query gold source id does not match source",
    )
    _require(
        gold.required_text == (source.atom.text,),
        "query gold text does not match source",
    )
    _require(
        source.atom.role is query.role,
        "query role does not match source",
    )
    _require(
        source.atom.semantic_keys == (query.semantic_key,),
        "query semantic key does not match source",
    )
    _require(
        source.atom.as_of == expected_generation,
        "query source is not from expected generation",
    )


def _flatten_round_queries(
    streams: tuple[ProtocolV2Stream, ...],
) -> tuple[QuerySpec, ...]:
    """Return the frozen stream, round, then query sequence for public inputs."""
    return tuple(
        query
        for stream in streams
        for subject_round in stream.subject_rounds
        for query in subject_round.queries
    )


def preflight_protocol_v2(config: ProtocolV2Config) -> ProtocolV2PreflightReport:
    """Assert all frozen generator checks without arm, model, or runner inputs."""
    _validate_config(config)
    first_bundle = generate_protocol_v2(config)
    second_bundle = generate_protocol_v2(config)
    _require(
        canonical_json(first_bundle) == canonical_json(second_bundle),
        "generator output is non-deterministic",
    )
    _require(
        first_bundle.input_hash == second_bundle.input_hash,
        "generator input hash is non-deterministic",
    )
    bundle = first_bundle
    _require(bundle.protocol_id == config.protocol_id, "protocol id mismatch")
    _require(bundle.schema_version == config.schema_version, "schema version mismatch")
    _require(len(bundle.streams) == config.stream_count, "stream count mismatch")
    _require(
        len(bundle.public_queries)
        == config.stream_count
        * config.generation_count
        * config.sealed_queries_per_generation,
        "public query count mismatch",
    )
    _require(
        len(bundle.sealed_gold) == len(bundle.public_queries),
        "sealed gold count mismatch",
    )
    _require(
        bundle.public_queries == _flatten_round_queries(bundle.streams),
        "public queries do not exactly match subject rounds",
    )

    schema = default_kernel_schema_v2()
    slot_by_role = {slot.role: slot for slot in schema.slots}
    gold_by_query_id = {gold.query_id: gold for gold in bundle.sealed_gold}
    query_ids = {query.query_id for query in bundle.public_queries}
    _require(
        len(query_ids) == len(bundle.public_queries), "public query ids are not unique"
    )
    _require(set(gold_by_query_id) == query_ids, "query/gold ids do not match")
    _require(
        all(
            not hasattr(query, "required_source_ids") for query in bundle.public_queries
        ),
        "public queries contain sealed source bindings",
    )

    terminal_bytes: list[int] = []
    for stream in bundle.streams:
        _require(
            len(stream.subject_rounds) == config.generation_count,
            "subject round count mismatch",
        )
        _require(
            len(stream.shadow_rounds) == config.generation_count,
            "shadow round count mismatch",
        )
        shadow_by_generation = {
            shadow_round.generation: shadow_round
            for shadow_round in stream.shadow_rounds
        }
        expected_by_key: dict[str, ProtocolSourceV2] = {}
        immutable_frontier_keys: dict[SourceStratumV2, set[str]] = {
            stratum: set() for stratum in _IMMUTABLE_CONTEXT_STRATA
        }
        immutable_frontier_source_counts = {
            stratum: 0 for stratum in _IMMUTABLE_CONTEXT_STRATA
        }
        active_counts: list[int] = []
        active_bytes: list[int] = []
        for subject_round, shadow_round in zip(
            stream.subject_rounds, stream.shadow_rounds, strict=True
        ):
            _require(
                subject_round.stream_id == stream.stream_id,
                "subject stream id mismatch",
            )
            _require(
                shadow_round.stream_id == stream.stream_id, "shadow stream id mismatch"
            )
            _require(
                subject_round.generation == shadow_round.generation,
                "subject/shadow generation mismatch",
            )
            _require(
                len(subject_round.queries) == config.sealed_queries_per_generation,
                "per-round query count mismatch",
            )
            core_updates = [
                source
                for source in subject_round.updates
                if source.stratum is SourceStratumV2.CORE
            ]
            _require(
                {source.atom.role for source in core_updates} == set(config.core_roles),
                "five core updates are incomplete",
            )
            _require(
                len(core_updates) == len(config.core_roles),
                "core update count mismatch",
            )
            expected_immutable_items = (
                config.immutable_context_initial_items
                + (subject_round.generation - 1)
                * config.immutable_context_increment_per_generation
            )
            _require(
                sum(
                    source.stratum is SourceStratumV2.COMPRESSIBLE_IMMUTABLE_CONTEXT
                    for source in subject_round.updates
                )
                == expected_immutable_items,
                "compressible immutable growth mismatch",
            )
            _require(
                sum(
                    source.stratum is SourceStratumV2.INCOMPRESSIBLE_IMMUTABLE_CONTEXT
                    for source in subject_round.updates
                )
                == expected_immutable_items,
                "incompressible immutable growth mismatch",
            )
            _require(
                sum(
                    source.stratum is SourceStratumV2.MUTABLE_INELIGIBLE_CONTEXT
                    for source in subject_round.updates
                )
                == config.mutable_ineligible_items_per_generation,
                "mutable ineligible growth mismatch",
            )
            _require(
                all(
                    utf8_bytes(source.atom.text)
                    == config.stratum_text_bytes[source.stratum.value]
                    for source in subject_round.updates
                ),
                "fixed stratum length mismatch",
            )
            _require(
                all(
                    not source.packing_eligible
                    for source in subject_round.updates
                    if source.stratum is SourceStratumV2.MUTABLE_INELIGIBLE_CONTEXT
                ),
                "mutable context became packing-eligible",
            )
            expected_stream_token = f"stream:{stream.stream_id}"
            round_semantic_keys: list[str] = []
            for source in subject_round.updates:
                _require(
                    expected_stream_token in source.atom.provenance,
                    "subject update provenance does not match stream",
                )
                _require(
                    len(source.atom.semantic_keys) == 1,
                    "subject update semantic key cardinality mismatch",
                )
                _require(
                    source.atom.as_of == subject_round.generation,
                    "subject update is not from its generation",
                )
                round_semantic_keys.append(source.atom.semantic_keys[0])
            _require(
                len(set(round_semantic_keys)) == len(round_semantic_keys),
                "subject round update semantic keys are not unique",
            )
            for stratum in _IMMUTABLE_CONTEXT_STRATA:
                immutable_round_keys = {
                    source.atom.semantic_keys[0]
                    for source in subject_round.updates
                    if source.stratum is stratum
                }
                new_immutable_keys = (
                    immutable_round_keys - immutable_frontier_keys[stratum]
                )
                _require(
                    len(new_immutable_keys) == expected_immutable_items,
                    "immutable stratum did not add the expected number of new semantic keys",
                )
                immutable_frontier_keys[stratum].update(immutable_round_keys)
                immutable_frontier_source_counts[stratum] += expected_immutable_items
                _require(
                    len(immutable_frontier_keys[stratum])
                    == immutable_frontier_source_counts[stratum],
                    "immutable stratum frontier source cardinality mismatch",
                )
            for source in subject_round.updates:
                expected_by_key[source.atom.semantic_keys[0]] = source

            expected_full_retention_sources = tuple(
                sorted(expected_by_key.values(), key=_source_sort_key)
            )
            _require(
                shadow_round.full_retention_sources == expected_full_retention_sources,
                "shadow full-retention corpus does not match subject replay",
            )
            for stratum in _IMMUTABLE_CONTEXT_STRATA:
                immutable_frontier_sources = tuple(
                    source
                    for source in shadow_round.full_retention_sources
                    if source.stratum is stratum
                )
                immutable_frontier_source_keys = {
                    source.atom.semantic_keys[0]
                    for source in immutable_frontier_sources
                }
                _require(
                    len(immutable_frontier_sources)
                    == immutable_frontier_source_counts[stratum]
                    and len(immutable_frontier_source_keys)
                    == len(immutable_frontier_sources),
                    "immutable stratum frontier source cardinality mismatch",
                )
                _require(
                    immutable_frontier_source_keys == immutable_frontier_keys[stratum],
                    "immutable stratum frontier does not match subject replay",
                )
            expected_core_by_role = {
                source.atom.role: source
                for source in expected_by_key.values()
                if source.stratum is SourceStratumV2.CORE
            }
            _require(
                set(expected_core_by_role) == set(config.core_roles),
                "subject replay core roles are incomplete",
            )
            expected_kernel_only_sources = tuple(
                sorted(
                    (expected_core_by_role[role] for role in config.core_roles),
                    key=lambda source: source.atom.role.value,
                )
            )
            _require(
                shadow_round.kernel_only_sources == expected_kernel_only_sources,
                "shadow kernel-only corpus does not match subject replay",
            )
            _require(
                set(shadow_round.kernel_only_sources).issubset(
                    set(shadow_round.full_retention_sources)
                ),
                "kernel-only corpus is not a full-corpus subset",
            )
            _require(
                {source.atom.role for source in shadow_round.kernel_only_sources}
                == set(config.core_roles),
                "kernel-only corpus does not contain every core role",
            )
            _require(
                all(
                    utf8_bytes(source.atom.text)
                    <= slot_by_role[source.atom.role].max_text_bytes
                    for source in shadow_round.kernel_only_sources
                ),
                "kernel-only source does not fit its slot",
            )
            active_counts.append(len(shadow_round.full_retention_sources))
            active_bytes.append(shadow_round.active_source_bytes)
            _require(
                shadow_round.active_source_bytes
                == sum(
                    utf8_bytes(source.atom.text)
                    for source in shadow_round.full_retention_sources
                ),
                "active source byte denominator mismatch",
            )

            sources_by_id = {
                source.atom.source_id: source
                for source in shadow_round.full_retention_sources
            }
            core_queries = [
                query
                for query in subject_round.queries
                if query.role is not AtomRole.CONTEXT
            ]
            _require(
                {query.role for query in core_queries} == set(config.core_roles),
                "core query roles are incomplete",
            )
            _require(
                len(core_queries) == len(config.core_roles),
                "core query count mismatch",
            )
            for core_query in core_queries:
                core_gold = gold_by_query_id[core_query.query_id]
                _require(
                    not core_gold.is_topic_return,
                    "core query gold must not be a topic return",
                )
                _require(
                    core_gold.returned_generation is None,
                    "core query gold must not declare a returned generation",
                )
                _validate_query_gold_shape(core_gold)
                core_source = _required_gold_source(sources_by_id, core_gold)
                _validate_query_source_binding(
                    core_query,
                    core_gold,
                    core_source,
                    expected_generation=subject_round.generation,
                )

            context_queries = [
                query
                for query in subject_round.queries
                if query.role is AtomRole.CONTEXT
            ]
            _require(len(context_queries) == 1, "context query count mismatch")
            context_query = context_queries[0]
            context_gold = gold_by_query_id[context_query.query_id]
            _validate_query_gold_shape(context_gold)
            if subject_round.generation <= config.query_return_lag_generations:
                _require(
                    not context_gold.is_topic_return
                    and context_gold.returned_generation is None,
                    "warmup context query was marked as a topic return",
                )
                current_source = _required_gold_source(sources_by_id, context_gold)
                _validate_query_source_binding(
                    context_query,
                    context_gold,
                    current_source,
                    expected_generation=subject_round.generation,
                )
                _require(
                    current_source.stratum
                    is SourceStratumV2.COMPRESSIBLE_IMMUTABLE_CONTEXT,
                    "warmup context source is not compressible immutable context",
                )
            else:
                returned_generation = (
                    subject_round.generation - config.query_return_lag_generations
                )
                _require(
                    context_gold.is_topic_return
                    and context_gold.returned_generation == returned_generation,
                    "topic-return lag mismatch",
                )
                returned_sources = {
                    source.atom.source_id: source
                    for source in shadow_by_generation[
                        returned_generation
                    ].full_retention_sources
                }
                returned_source = _required_gold_source(returned_sources, context_gold)
                _validate_query_source_binding(
                    context_query,
                    context_gold,
                    returned_source,
                    expected_generation=returned_generation,
                )
                _require(
                    returned_source.stratum
                    is SourceStratumV2.COMPRESSIBLE_IMMUTABLE_CONTEXT,
                    "topic-return source is not compressible immutable context",
                )

        _require(
            all(
                previous < current
                for previous, current in zip(active_counts, active_counts[1:])
            ),
            "active source count is not strictly increasing",
        )
        _require(
            all(
                previous < current
                for previous, current in zip(active_bytes, active_bytes[1:])
            ),
            "active source bytes are not strictly increasing",
        )
        _require(
            config.terminal_active_source_byte_bounds[0]
            <= active_bytes[-1]
            <= config.terminal_active_source_byte_bounds[1],
            "terminal active source bytes are outside the calibrated physical range",
        )
        terminal_bytes.append(active_bytes[-1])

    _validate_hashes(config, bundle)
    checks = (
        "active_bytes",
        "active_monotonicity",
        "counts",
        "fixed_strata",
        "input_hashes",
        "kernel_fit",
        "query_gold_separation",
        "topic_return",
    )
    return ProtocolV2PreflightReport(
        protocol_id=config.protocol_id,
        schema_version=config.schema_version,
        config_hash=config.config_hash,
        input_hash=bundle.input_hash,
        terminal_active_source_bytes=tuple(terminal_bytes),
        terminal_active_source_kib=tuple(
            byte_count / config.kib_denominator_bytes for byte_count in terminal_bytes
        ),
        checks=checks,
        passed=True,
    )
