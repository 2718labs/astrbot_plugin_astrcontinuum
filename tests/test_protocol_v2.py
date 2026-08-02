"""Protocol v2 generator-only contract tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import fields, replace
from pathlib import Path

import pytest

import crm_experiment.protocol_v2 as protocol_v2
from crm_experiment.canonical import canonical_json, sha256_text, utf8_bytes
from crm_experiment.contracts import AtomRole, QuerySpec
from crm_experiment.contracts_v2 import LogicalAtomV2
from crm_experiment.kernel_v2 import default_kernel_schema_v2
from crm_experiment.protocol_v2 import (
    ShadowRoundV2,
    SubjectRoundV2,
    generate_protocol_v2,
    load_protocol_v2_config,
    preflight_protocol_v2,
)

CONFIG_PATH = Path("config/protocol-v2.json")


@pytest.fixture(scope="module")
def pristine_protocol_v2_config() -> protocol_v2.ProtocolV2Config:
    return load_protocol_v2_config(CONFIG_PATH)


@pytest.fixture(scope="module")
def pristine_protocol_v2(
    pristine_protocol_v2_config: protocol_v2.ProtocolV2Config,
) -> tuple[protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle]:
    return (
        pristine_protocol_v2_config,
        generate_protocol_v2(pristine_protocol_v2_config),
    )


def _with_recomputed_bundle_hash(
    bundle: protocol_v2.ProtocolV2Bundle,
) -> protocol_v2.ProtocolV2Bundle:
    return replace(
        bundle,
        input_hash=protocol_v2._bundle_input_hash(
            bundle.protocol_id,
            bundle.schema_version,
            bundle.config_hash,
            bundle.streams,
            bundle.public_queries,
            bundle.sealed_gold,
        ),
    )


def _with_replaced_gold(
    bundle: protocol_v2.ProtocolV2Bundle,
    replacement: protocol_v2.ProtocolV2QueryGold,
) -> protocol_v2.ProtocolV2Bundle:
    return _with_recomputed_bundle_hash(
        replace(
            bundle,
            sealed_gold=tuple(
                replacement if gold.query_id == replacement.query_id else gold
                for gold in bundle.sealed_gold
            ),
        )
    )


def _with_replaced_subject_query(
    config: protocol_v2.ProtocolV2Config,
    bundle: protocol_v2.ProtocolV2Bundle,
    stream_index: int,
    generation: int,
    replacement: QuerySpec,
    *,
    update_public_query: bool = True,
) -> protocol_v2.ProtocolV2Bundle:
    stream = bundle.streams[stream_index]
    subject_round = stream.subject_rounds[generation - 1]
    assert subject_round.generation == generation
    queries = tuple(
        replacement if query.query_id == replacement.query_id else query
        for query in subject_round.queries
    )
    changed_round = replace(
        subject_round,
        queries=queries,
        subject_input_hash=protocol_v2._subject_input_hash(
            config,
            subject_round.stream_id,
            subject_round.generation,
            subject_round.updates,
            queries,
        ),
    )
    changed_stream = replace(
        stream,
        subject_rounds=tuple(
            changed_round if round_item.generation == generation else round_item
            for round_item in stream.subject_rounds
        ),
    )
    return _with_recomputed_bundle_hash(
        replace(
            bundle,
            streams=tuple(
                changed_stream if index == stream_index else stream_item
                for index, stream_item in enumerate(bundle.streams)
            ),
            public_queries=(
                tuple(
                    replacement if query.query_id == replacement.query_id else query
                    for query in bundle.public_queries
                )
                if update_public_query
                else bundle.public_queries
            ),
        )
    )


def _with_replaced_subject_update(
    config: protocol_v2.ProtocolV2Config,
    bundle: protocol_v2.ProtocolV2Bundle,
    stream_index: int,
    generation: int,
    target: protocol_v2.ProtocolSourceV2,
    replacement: protocol_v2.ProtocolSourceV2,
) -> protocol_v2.ProtocolV2Bundle:
    stream = bundle.streams[stream_index]
    subject_round = stream.subject_rounds[generation - 1]
    updates = tuple(
        replacement if source == target else source for source in subject_round.updates
    )
    changed_round = replace(
        subject_round,
        updates=updates,
        subject_input_hash=protocol_v2._subject_input_hash(
            config,
            subject_round.stream_id,
            subject_round.generation,
            updates,
            subject_round.queries,
        ),
    )
    changed_stream = replace(
        stream,
        subject_rounds=tuple(
            changed_round if round_item.generation == generation else round_item
            for round_item in stream.subject_rounds
        ),
    )
    return _with_recomputed_bundle_hash(
        replace(
            bundle,
            streams=tuple(
                changed_stream if index == stream_index else stream_item
                for index, stream_item in enumerate(bundle.streams)
            ),
        )
    )


def _with_replaced_shadow_full_retention(
    config: protocol_v2.ProtocolV2Config,
    bundle: protocol_v2.ProtocolV2Bundle,
    stream_index: int,
    generation: int,
    full_retention_sources: tuple[protocol_v2.ProtocolSourceV2, ...],
) -> protocol_v2.ProtocolV2Bundle:
    stream = bundle.streams[stream_index]
    shadow_round = stream.shadow_rounds[generation - 1]
    ordered_sources = tuple(
        sorted(full_retention_sources, key=lambda source: source.atom.source_id)
    )
    provisional_shadow = replace(
        shadow_round,
        full_retention_sources=ordered_sources,
        active_source_bytes=sum(
            utf8_bytes(source.atom.text) for source in ordered_sources
        ),
        full_retention_hash=protocol_v2._full_retention_hash(ordered_sources),
        shadow_input_hash="",
    )
    changed_shadow = replace(
        provisional_shadow,
        shadow_input_hash=protocol_v2._shadow_input_hash(config, provisional_shadow),
    )
    changed_stream = replace(
        stream,
        shadow_rounds=tuple(
            changed_shadow if round_item.generation == generation else round_item
            for round_item in stream.shadow_rounds
        ),
    )
    return _with_recomputed_bundle_hash(
        replace(
            bundle,
            streams=tuple(
                changed_stream if index == stream_index else stream_item
                for index, stream_item in enumerate(bundle.streams)
            ),
        )
    )


def test_protocol_v2_config_freezes_shape_budgets_and_calibration_units(
    pristine_protocol_v2_config: protocol_v2.ProtocolV2Config,
) -> None:
    config = pristine_protocol_v2_config

    assert config.protocol_id == "crm-capsule-stress-v2"
    assert config.schema_version == 2
    assert config.stream_count == 24
    assert config.generation_count == 12
    assert config.sealed_queries_per_generation == 6
    assert (
        config.continuity_floor_bytes
        == default_kernel_schema_v2().continuity_floor_bytes
    )
    assert config.budget_multipliers == (8.0, 4.0, 2.0, 1.5, 1.0)
    assert config.budget_bytes == (36864, 18432, 9216, 6912, 4608)
    assert config.kib_denominator_bytes == 1024
    assert config.terminal_active_source_kib_bounds == (7.70, 7.90)
    assert config.terminal_active_source_byte_bounds == (7885, 8089)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("top_level", "protocol v2 config contains unknown keys: stream_counts"),
        (
            "stratum_text_bytes",
            "stratum_text_bytes contains unknown keys: typo_stratum",
        ),
    ),
)
def test_protocol_v2_loader_rejects_unknown_frozen_config_keys(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if case == "top_level":
        raw["stream_counts"] = raw["stream_count"]
    else:
        raw["stratum_text_bytes"]["typo_stratum"] = 64
    candidate_path = tmp_path / "protocol-v2-unknown-key.json"
    candidate_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_protocol_v2_config(candidate_path)


@pytest.mark.parametrize("entrypoint", (generate_protocol_v2, preflight_protocol_v2))
def test_public_protocol_v2_entrypoints_reject_stale_semantic_config_hash(
    entrypoint: Callable[[protocol_v2.ProtocolV2Config], object],
    pristine_protocol_v2_config: protocol_v2.ProtocolV2Config,
) -> None:
    config = pristine_protocol_v2_config
    stale_config = replace(config, seed_start=config.seed_start + 1)

    with pytest.raises(ValueError, match="config hash mismatch"):
        entrypoint(stale_config)


def test_protocol_v2_generator_is_deterministic_and_separates_subject_shadow_and_gold(
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, first = pristine_protocol_v2
    second = generate_protocol_v2(config)

    assert first.input_hash == second.input_hash
    assert canonical_json(first) == canonical_json(second)
    assert first.protocol_id == config.protocol_id
    assert first.schema_version == config.schema_version
    assert first.config_hash == config.config_hash
    assert len(first.streams) == 24
    assert len(first.public_queries) == 24 * 12 * 6
    assert len(first.sealed_gold) == 24 * 12 * 6
    assert set(SubjectRoundV2.__dataclass_fields__) == {
        "stream_id",
        "generation",
        "updates",
        "queries",
        "subject_input_hash",
    }

    gold_by_query_id = {gold.query_id: gold for gold in first.sealed_gold}
    assert set(gold_by_query_id) == {query.query_id for query in first.public_queries}
    assert all(isinstance(query, QuerySpec) for query in first.public_queries)
    assert all(
        not hasattr(query, "required_source_ids") for query in first.public_queries
    )

    for stream in first.streams:
        assert len(stream.subject_rounds) == 12
        assert len(stream.shadow_rounds) == 12
        for subject_round, shadow_round in zip(
            stream.subject_rounds, stream.shadow_rounds, strict=True
        ):
            assert isinstance(subject_round, SubjectRoundV2)
            assert isinstance(shadow_round, ShadowRoundV2)
            assert subject_round.stream_id == shadow_round.stream_id == stream.stream_id
            assert subject_round.generation == shadow_round.generation
            assert len(subject_round.queries) == 6
            assert shadow_round.full_retention_hash == sha256_text(
                canonical_json(
                    {
                        "domain": "crm-protocol-v2-shadow-full-retention/v1",
                        "sources": shadow_round.full_retention_sources,
                    }
                )
            )
            assert shadow_round.kernel_only_hash == sha256_text(
                canonical_json(
                    {
                        "domain": "crm-protocol-v2-shadow-kernel-only/v1",
                        "sources": shadow_round.kernel_only_sources,
                    }
                )
            )


def test_protocol_v2_hashes_are_domain_separated_at_each_lineage_layer(
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, bundle = pristine_protocol_v2
    subject_round = bundle.streams[0].subject_rounds[0]
    shadow_round = bundle.streams[0].shadow_rounds[0]

    assert config.config_hash == sha256_text(
        canonical_json(
            {
                "domain": "crm-protocol-v2-config/v1",
                "config": {
                    field.name: getattr(config, field.name)
                    for field in fields(config)
                    if field.name != "config_hash"
                },
            }
        )
    )
    assert subject_round.subject_input_hash == sha256_text(
        canonical_json(
            {
                "config_hash": config.config_hash,
                "domain": "crm-protocol-v2-subject-input/v1",
                "generation": subject_round.generation,
                "protocol_id": config.protocol_id,
                "queries": subject_round.queries,
                "schema_version": config.schema_version,
                "stream_id": subject_round.stream_id,
                "updates": subject_round.updates,
            }
        )
    )
    assert shadow_round.full_retention_hash == sha256_text(
        canonical_json(
            {
                "domain": "crm-protocol-v2-shadow-full-retention/v1",
                "sources": shadow_round.full_retention_sources,
            }
        )
    )
    assert shadow_round.kernel_only_hash == sha256_text(
        canonical_json(
            {
                "domain": "crm-protocol-v2-shadow-kernel-only/v1",
                "sources": shadow_round.kernel_only_sources,
            }
        )
    )
    assert shadow_round.shadow_input_hash == sha256_text(
        canonical_json(
            {
                "config_hash": config.config_hash,
                "domain": "crm-protocol-v2-shadow-input/v1",
                "full_retention_hash": shadow_round.full_retention_hash,
                "generation": shadow_round.generation,
                "kernel_only_hash": shadow_round.kernel_only_hash,
                "protocol_id": config.protocol_id,
                "schema_version": config.schema_version,
                "stream_id": shadow_round.stream_id,
            }
        )
    )
    assert bundle.input_hash == sha256_text(
        canonical_json(
            {
                "config_hash": config.config_hash,
                "domain": "crm-protocol-v2-bundle-input/v1",
                "protocol_id": config.protocol_id,
                "public_queries": bundle.public_queries,
                "schema_version": config.schema_version,
                "sealed_gold": bundle.sealed_gold,
                "streams": bundle.streams,
            }
        )
    )
    assert subject_round.subject_input_hash != shadow_round.shadow_input_hash
    assert shadow_round.full_retention_hash != shadow_round.kernel_only_hash


def test_preflight_rejects_two_self_consistent_but_non_deterministic_bundles(
    monkeypatch: pytest.MonkeyPatch,
    pristine_protocol_v2_config: protocol_v2.ProtocolV2Config,
) -> None:
    config = pristine_protocol_v2_config
    original_generator = protocol_v2.generate_protocol_v2
    call_count = 0

    def alternating_generator(current_config):
        nonlocal call_count
        call_count += 1
        bundle = original_generator(current_config)
        if call_count % 2:
            return bundle
        reversed_streams = tuple(reversed(bundle.streams))
        return replace(
            bundle,
            streams=reversed_streams,
            input_hash=protocol_v2._bundle_input_hash(
                bundle.protocol_id,
                bundle.schema_version,
                bundle.config_hash,
                reversed_streams,
                bundle.public_queries,
                bundle.sealed_gold,
            ),
        )

    monkeypatch.setattr(protocol_v2, "generate_protocol_v2", alternating_generator)

    with pytest.raises(ValueError, match="generator output is non-deterministic"):
        protocol_v2.preflight_protocol_v2(config)


def test_preflight_rejects_round_queries_that_do_not_exactly_match_public_queries(
    monkeypatch: pytest.MonkeyPatch,
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, pristine = pristine_protocol_v2
    stream = pristine.streams[0]
    subject_round = stream.subject_rounds[1]
    context_query = next(
        query for query in subject_round.queries if query.role is AtomRole.CONTEXT
    )
    alternate_source = next(
        source
        for source in stream.shadow_rounds[1].full_retention_sources
        if source.atom.as_of == subject_round.generation
        and source.stratum is protocol_v2.SourceStratumV2.COMPRESSIBLE_IMMUTABLE_CONTEXT
        and source.atom.semantic_keys != (context_query.semantic_key,)
    )
    context_gold = next(
        gold for gold in pristine.sealed_gold if gold.query_id == context_query.query_id
    )
    altered = _with_replaced_subject_query(
        config,
        pristine,
        stream_index=0,
        generation=subject_round.generation,
        replacement=replace(
            context_query,
            semantic_key=alternate_source.atom.semantic_keys[0],
        ),
        update_public_query=False,
    )
    altered = _with_replaced_gold(
        altered,
        replace(
            context_gold,
            required_source_ids=(alternate_source.atom.source_id,),
            required_text=(alternate_source.atom.text,),
        ),
    )
    monkeypatch.setattr(protocol_v2, "generate_protocol_v2", lambda _: altered)

    with pytest.raises(
        ValueError,
        match="public queries do not exactly match subject rounds",
    ):
        protocol_v2.preflight_protocol_v2(config)


def test_protocol_v2_generator_preserves_fixed_strata_monotonic_load_and_kernel_fit(
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, bundle = pristine_protocol_v2
    schema = default_kernel_schema_v2()
    max_text_bytes = {slot.role: slot.max_text_bytes for slot in schema.slots}

    for stream in bundle.streams:
        active_counts = []
        active_source_bytes = []
        for subject_round, shadow_round in zip(
            stream.subject_rounds, stream.shadow_rounds, strict=True
        ):
            core_updates = [
                source for source in subject_round.updates if source.stratum == "core"
            ]
            compressible_updates = [
                source
                for source in subject_round.updates
                if source.stratum == "compressible_immutable_context"
            ]
            incompressible_updates = [
                source
                for source in subject_round.updates
                if source.stratum == "incompressible_immutable_context"
            ]
            mutable_updates = [
                source
                for source in subject_round.updates
                if source.stratum == "mutable_ineligible_context"
            ]
            assert {source.atom.role for source in core_updates} == set(
                config.core_roles
            )
            assert len(core_updates) == 5
            assert len(compressible_updates) == subject_round.generation
            assert len(incompressible_updates) == subject_round.generation
            assert len(mutable_updates) == 1
            assert all(
                utf8_bytes(source.atom.text)
                == config.stratum_text_bytes[source.stratum]
                for source in subject_round.updates
            )
            assert all(
                source.packing_eligible
                for source in compressible_updates + incompressible_updates
            )
            assert all(not source.packing_eligible for source in mutable_updates)

            active_counts.append(len(shadow_round.full_retention_sources))
            active_source_bytes.append(shadow_round.active_source_bytes)
            assert set(shadow_round.kernel_only_sources).issubset(
                set(shadow_round.full_retention_sources)
            )
            assert {
                source.atom.role for source in shadow_round.kernel_only_sources
            } == set(config.core_roles)
            assert all(
                utf8_bytes(source.atom.text) <= max_text_bytes[source.atom.role]
                for source in shadow_round.kernel_only_sources
            )

        assert active_counts == sorted(active_counts)
        assert active_source_bytes == sorted(active_source_bytes)
        assert all(
            previous < current
            for previous, current in zip(active_counts, active_counts[1:])
        )
        assert all(
            previous < current
            for previous, current in zip(active_source_bytes, active_source_bytes[1:])
        )
        assert config.terminal_active_source_byte_bounds[0] <= active_source_bytes[-1]
        assert active_source_bytes[-1] <= config.terminal_active_source_byte_bounds[1]


def test_protocol_v2_context_returns_reference_real_four_round_old_source_only_after_warmup(
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, bundle = pristine_protocol_v2
    gold_by_query_id = {gold.query_id: gold for gold in bundle.sealed_gold}

    for stream in bundle.streams:
        shadow_by_generation = {
            shadow_round.generation: shadow_round
            for shadow_round in stream.shadow_rounds
        }
        for subject_round in stream.subject_rounds:
            context_queries = [
                query
                for query in subject_round.queries
                if query.role is AtomRole.CONTEXT
            ]
            assert len(context_queries) == 1
            gold = gold_by_query_id[context_queries[0].query_id]
            if subject_round.generation <= 4:
                assert gold.is_topic_return is False
                assert gold.returned_generation is None
                continue

            assert gold.is_topic_return is True
            assert gold.returned_generation == subject_round.generation - 4
            assert gold.returned_generation is not None
            returned_round = shadow_by_generation[gold.returned_generation]
            returned_source = next(
                source
                for source in returned_round.full_retention_sources
                if source.atom.source_id == gold.required_source_ids[0]
            )
            assert returned_source.atom.as_of == subject_round.generation - 4
            assert returned_source.stratum == "compressible_immutable_context"
            assert gold.required_text == (returned_source.atom.text,)


@pytest.mark.parametrize(
    ("role", "case", "message"),
    tuple(
        (role, case, message)
        for role in (
            AtomRole.ROOT_GOAL,
            AtomRole.CURRENT_FOCUS,
            AtomRole.OPEN_LOOP,
            AtomRole.HARD_CONSTRAINT,
            AtomRole.DECISION,
        )
        for case, message in (
            ("source_id", "query gold source is not real"),
            ("text", "query gold text does not match source"),
            ("semantic_key", "query semantic key does not match source"),
            ("cardinality", "query gold must bind one source id and one text"),
        )
    ),
)
def test_preflight_rejects_every_core_query_gold_binding_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    role: AtomRole,
    case: str,
    message: str,
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, pristine = pristine_protocol_v2
    subject_round = pristine.streams[0].subject_rounds[0]
    query = next(item for item in subject_round.queries if item.role is role)
    gold = next(
        item for item in pristine.sealed_gold if item.query_id == query.query_id
    )

    if case == "source_id":
        altered = _with_replaced_gold(
            pristine,
            replace(gold, required_source_ids=("sha256:" + "f" * 64,)),
        )
    elif case == "text":
        altered = _with_replaced_gold(
            pristine,
            replace(gold, required_text=("wrong core text",)),
        )
    elif case == "semantic_key":
        altered = _with_replaced_subject_query(
            config,
            pristine,
            stream_index=0,
            generation=1,
            replacement=replace(query, semantic_key=f"wrong/{role.value}"),
        )
    else:
        altered = _with_replaced_gold(
            pristine,
            replace(gold, required_text=gold.required_text * 2),
        )

    monkeypatch.setattr(protocol_v2, "generate_protocol_v2", lambda _: altered)

    with pytest.raises(ValueError, match=message):
        protocol_v2.preflight_protocol_v2(config)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("topic_return", "core query gold must not be a topic return"),
        (
            "returned_generation",
            "core query gold must not declare a returned generation",
        ),
    ),
)
def test_preflight_rejects_core_gold_topic_return_metadata(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, pristine = pristine_protocol_v2
    core_query = next(
        query
        for query in pristine.streams[0].subject_rounds[0].queries
        if query.role is AtomRole.ROOT_GOAL
    )
    core_gold = next(
        gold for gold in pristine.sealed_gold if gold.query_id == core_query.query_id
    )
    replacement = (
        replace(core_gold, is_topic_return=True)
        if case == "topic_return"
        else replace(core_gold, returned_generation=1)
    )
    altered = _with_replaced_gold(pristine, replacement)
    monkeypatch.setattr(protocol_v2, "generate_protocol_v2", lambda _: altered)

    with pytest.raises(ValueError, match=message):
        protocol_v2.preflight_protocol_v2(config)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("cross_stream_update", "subject update provenance does not match stream"),
        (
            "wrong_shadow_update",
            "shadow full-retention corpus does not match subject replay",
        ),
    ),
)
def test_preflight_rejects_shadow_lineage_not_derived_from_subject_updates(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, pristine = pristine_protocol_v2
    primary_stream = pristine.streams[0]

    if case == "cross_stream_update":
        target = next(
            source
            for source in primary_stream.subject_rounds[0].updates
            if source.stratum == "incompressible_immutable_context"
        )
        foreign_source = next(
            source
            for source in pristine.streams[1].subject_rounds[0].updates
            if source.stratum == target.stratum
        )
        altered = _with_replaced_subject_update(
            config,
            pristine,
            stream_index=0,
            generation=1,
            target=target,
            replacement=foreign_source,
        )
        full_sources = tuple(
            foreign_source if source == target else source
            for source in primary_stream.shadow_rounds[0].full_retention_sources
        )
        altered = _with_replaced_shadow_full_retention(
            config,
            altered,
            stream_index=0,
            generation=1,
            full_retention_sources=full_sources,
        )
    else:
        shadow_round = primary_stream.shadow_rounds[1]
        target = next(
            source
            for source in shadow_round.full_retention_sources
            if source.atom.as_of == 2
            and source.stratum == "incompressible_immutable_context"
        )
        stale_source = next(
            source
            for source in shadow_round.full_retention_sources
            if source.atom.as_of == 1
            and source.stratum == "incompressible_immutable_context"
        )
        altered = _with_replaced_shadow_full_retention(
            config,
            pristine,
            stream_index=0,
            generation=2,
            full_retention_sources=tuple(
                stale_source if source == target else source
                for source in shadow_round.full_retention_sources
            ),
        )

    monkeypatch.setattr(protocol_v2, "generate_protocol_v2", lambda _: altered)

    with pytest.raises(ValueError, match=message):
        protocol_v2.preflight_protocol_v2(config)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("duplicate_round_key", "subject round update semantic keys are not unique"),
        (
            "reused_frontier_key",
            "immutable stratum did not add the expected number of new semantic keys",
        ),
    ),
)
def test_preflight_rejects_immutable_frontier_key_reuse_after_self_consistent_rehash(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, pristine = pristine_protocol_v2
    stream = pristine.streams[0]
    subject_round = stream.subject_rounds[-1]
    shadow_round = stream.shadow_rounds[-1]
    target = next(
        source
        for source in subject_round.updates
        if source.stratum
        is protocol_v2.SourceStratumV2.INCOMPRESSIBLE_IMMUTABLE_CONTEXT
    )

    if case == "duplicate_round_key":
        replacement = next(
            source
            for source in subject_round.updates
            if source.stratum is target.stratum and source is not target
        )
        full_retention_sources = tuple(
            source
            for source in shadow_round.full_retention_sources
            if source is not target
        )
    else:
        prior_source = next(
            source
            for source in stream.subject_rounds[-2].updates
            if source.stratum is target.stratum
        )
        replacement = replace(
            target,
            atom=LogicalAtomV2.create(
                source_label="adversarial-immutable-frontier-rekey",
                semantic_keys=prior_source.atom.semantic_keys,
                role=target.atom.role,
                text=target.atom.text,
                status=target.atom.status,
                revision=target.atom.revision,
                as_of=target.atom.as_of,
                provenance=target.atom.provenance,
                exact=target.atom.exact,
                depends_on=target.atom.depends_on,
                core_required=target.atom.core_required,
            ),
        )
        full_retention_sources = tuple(
            source
            for source in shadow_round.full_retention_sources
            if source is not target and source is not prior_source
        ) + (replacement,)

    altered = _with_replaced_subject_update(
        config,
        pristine,
        stream_index=0,
        generation=subject_round.generation,
        target=target,
        replacement=replacement,
    )
    altered = _with_replaced_shadow_full_retention(
        config,
        altered,
        stream_index=0,
        generation=shadow_round.generation,
        full_retention_sources=full_retention_sources,
    )
    monkeypatch.setattr(protocol_v2, "generate_protocol_v2", lambda _: altered)

    with pytest.raises(ValueError, match=message):
        protocol_v2.preflight_protocol_v2(config)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("source_id", "query gold source is not real"),
        ("text", "query gold text does not match source"),
        ("query_semantic_key", "query semantic key does not match source"),
        ("source_semantic_key", "query semantic key does not match source"),
        ("tuple_cardinality", "query gold must bind one source id and one text"),
        ("warmup_stale", "query source is not from expected generation"),
    ),
)
def test_preflight_rejects_context_gold_binding_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, pristine = pristine_protocol_v2
    stream = pristine.streams[0]
    topic_round = stream.subject_rounds[4]
    topic_query = next(
        query for query in topic_round.queries if query.role is AtomRole.CONTEXT
    )
    topic_gold = next(
        gold for gold in pristine.sealed_gold if gold.query_id == topic_query.query_id
    )

    if case == "source_id":
        altered = _with_replaced_gold(
            pristine,
            replace(topic_gold, required_source_ids=("sha256:" + "0" * 64,)),
        )
    elif case == "text":
        altered = _with_replaced_gold(
            pristine,
            replace(topic_gold, required_text=("wrong text",)),
        )
    elif case == "query_semantic_key":
        altered = _with_replaced_subject_query(
            config,
            pristine,
            stream_index=0,
            generation=5,
            replacement=replace(topic_query, semantic_key="wrong/context/key"),
        )
    elif case == "source_semantic_key":
        semantic_round = stream.subject_rounds[5]
        semantic_query = next(
            query for query in semantic_round.queries if query.role is AtomRole.CONTEXT
        )
        semantic_gold = next(
            gold
            for gold in pristine.sealed_gold
            if gold.query_id == semantic_query.query_id
        )
        different_source = next(
            source
            for source in stream.shadow_rounds[1].full_retention_sources
            if source.atom.source_id != semantic_gold.required_source_ids[0]
            and source.atom.as_of == semantic_gold.returned_generation
            and source.stratum == "compressible_immutable_context"
        )
        altered = _with_replaced_gold(
            pristine,
            replace(
                semantic_gold,
                required_source_ids=(different_source.atom.source_id,),
                required_text=(different_source.atom.text,),
            ),
        )
    elif case == "tuple_cardinality":
        altered = _with_replaced_gold(
            pristine,
            replace(
                topic_gold,
                required_source_ids=topic_gold.required_source_ids * 2,
            ),
        )
    else:
        warmup_round = stream.subject_rounds[1]
        warmup_query = next(
            query for query in warmup_round.queries if query.role is AtomRole.CONTEXT
        )
        warmup_gold = next(
            gold
            for gold in pristine.sealed_gold
            if gold.query_id == warmup_query.query_id
        )
        prior_source = next(
            source
            for source in stream.shadow_rounds[0].full_retention_sources
            if source.stratum == "compressible_immutable_context"
        )
        altered = _with_replaced_subject_query(
            config,
            pristine,
            stream_index=0,
            generation=2,
            replacement=replace(
                warmup_query,
                semantic_key=prior_source.atom.semantic_keys[0],
            ),
        )
        altered = _with_replaced_gold(
            altered,
            replace(
                warmup_gold,
                required_source_ids=(prior_source.atom.source_id,),
                required_text=(prior_source.atom.text,),
            ),
        )

    monkeypatch.setattr(protocol_v2, "generate_protocol_v2", lambda _: altered)

    with pytest.raises(ValueError, match=message):
        protocol_v2.preflight_protocol_v2(config)


def test_protocol_v2_preflight_is_generator_only_and_reports_physical_input_hashes(
    pristine_protocol_v2: tuple[
        protocol_v2.ProtocolV2Config, protocol_v2.ProtocolV2Bundle
    ],
) -> None:
    config, bundle = pristine_protocol_v2
    report = preflight_protocol_v2(config)

    assert report.passed is True
    assert report.protocol_id == config.protocol_id
    assert report.schema_version == config.schema_version
    assert report.config_hash == config.config_hash
    assert report.input_hash == bundle.input_hash
    assert report.terminal_active_source_bytes == tuple(8028 for _ in range(24))
    assert report.terminal_active_source_kib == tuple(8028 / 1024 for _ in range(24))
    assert set(report.checks) == {
        "active_bytes",
        "active_monotonicity",
        "counts",
        "fixed_strata",
        "input_hashes",
        "kernel_fit",
        "query_gold_separation",
        "topic_return",
    }
