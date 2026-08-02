"""Pure, transient schema-3 evidence contracts for V21 reencoding.

This module deliberately contains only nominal rows, local validators and
domain-separated commitments.  It neither resolves a candidate against state
nor plans, authorizes, mutates, persists, or performs I/O.

The transient-source body limits are frozen here: each UTF-8 body is at most
65,536 bytes, one envelope's aggregate source-body bytes are at most 8,388,608
bytes (8 MiB), and its full pre-root canonical envelope is at most 33,554,432
bytes (32 MiB).  The canonical ceiling retains four times the raw-byte
headroom so escaped JSON is still bounded by a reachable, tested limit.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Final, cast

from crm_experiment.contracts_v21 import (
    CapsuleRepresentativeV21,
    CapsuleRoleV21,
    CapsuleStateV21,
    HardDependencyV21,
    canonical_json_v21,
    representative_commitment_v21,
    source_commitment_v21,
    validate_capsule_state_v21,
)
from crm_experiment.evidenced_state_v21 import (
    EvidencedCapsuleStateV21,
    EvidencedStateMismatchV21,
    evidenced_state_hash_v21,
    validate_evidenced_capsule_state_v21,
)

MAX_EVIDENCED_SOURCE_RECORDS_V21: Final = 256
MAX_EVIDENCED_HARD_EDGES_V21: Final = 1024
MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21: Final = 64
MAX_EVIDENCED_REPRESENTATIVE_CANDIDATES_V21: Final = 64
MAX_EVIDENCED_ROOT_ROWS_V21: Final = 256
MAX_EVIDENCED_PLAN_EVALUATIONS_V21: Final = 65_536
MAX_EVIDENCED_LOSS_UNITS_V21: Final = 1_000_000
MAX_EVIDENCED_NEW_SEGMENTS_V21: Final = 64
MAX_EVIDENCED_COVERAGE_VECTOR_V21: Final = 64
MAX_EVIDENCED_BRIDGE_VECTOR_V21: Final = 64
MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21: Final = 64 * 1024
MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21: Final = 8_388_608
MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21: Final = 33_554_432

CANONICAL_SEGMENT_HASH_DOMAIN_V21: Final = "crm-v21-canonical-segment-s3/v1"
HARD_GRAPH_ROOT_DOMAIN_V21: Final = "crm-v21-hard-graph-s3/v1"
CONTRIBUTION_ROOT_DOMAIN_V21: Final = "crm-v21-fold-contributions-s3/v1"
SOURCE_ENVELOPE_ROOT_DOMAIN_V21: Final = "crm-v21-source-envelope-s3/v1"
EVIDENCED_POLICY_ROOT_DOMAIN_V21: Final = "crm-v21-evidenced-policy-s3/v1"
RECORD_DECISION_ROOT_DOMAIN_V21: Final = "crm-v21-record-decisions-s3/v1"
SEGMENT_DECISION_ROOT_DOMAIN_V21: Final = "crm-v21-segment-decisions-s3/v1"
FIRST_FOLD_AUTHORIZATION_ROOT_DOMAIN_V21: Final = "crm-v21-first-fold-auth-s3/v1"
BARRIER_ADVANCE_ROOT_DOMAIN_V21: Final = "crm-v21-barrier-advance-s3/v1"
LEDGER_ADVANCE_ROOT_DOMAIN_V21: Final = "crm-v21-ledger-advance-s3/v1"
BARRIER_ADVANCE_SEED_ROOT_DOMAIN_V21: Final = "crm-v21-barrier-advance-seed-s3/v1"
LEDGER_ADVANCE_SEED_ROOT_DOMAIN_V21: Final = "crm-v21-ledger-advance-seed-s3/v1"
ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21: Final = "crm-v21-advance-head-s3/v1"
EVIDENCED_TRANSITION_ROOT_DOMAIN_V21: Final = "crm-v21-evidenced-transition-s3/v1"
MATRIX_ROOT_DOMAIN_V21: Final = "crm-v21-evidenced-matrix-s3/v1"
EVIDENCED_STATE_HASH_DOMAIN_V21: Final = "crm-v21-evidenced-state-s3/v1"

_NAMESPACE_ID_DOMAIN: Final = "v21-namespace-id-s3"
_RECORD_ID_DOMAIN: Final = "v21-record-id-s3"
_SEGMENT_ID_DOMAIN: Final = "v21-segment-id-s3"
_DICTIONARY_ID_DOMAIN: Final = "v21-dictionary-id-s3"
_REPRESENTATIVE_ID_DOMAIN: Final = "v21-representative-id-s3"
_SOURCE_COMMITMENT_DOMAIN: Final = "v21-source-commitment-s3"
_REPRESENTATIVE_COMMITMENT_DOMAIN: Final = "v21-representative-commitment-s3"
_REENCODING_POLICY_DOMAIN: Final = "v21-reencoding-policy-s3"
_DURABLE_BARRIER_ROOT_DOMAIN: Final = "v21-barrier-root-s3"
_DURABLE_LEDGER_ROOT_DOMAIN: Final = "v21-ledger-root-s3"


def _domain_hash_v21(domain: str, value: object) -> str:
    digest = sha256(
        canonical_json_v21({"domain": domain, "value": value}).encode("utf-8")
    ).hexdigest()
    return f"{domain}:{digest}"


def _require_text_v21(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_domain_digest_v21(value: object, domain: str, label: str) -> str:
    text = _require_text_v21(value, label)
    if re.fullmatch(rf"{re.escape(domain)}:[0-9a-f]{{64}}", text) is None:
        raise ValueError(f"{label} must be a {domain} domain digest")
    return text


def _require_nonnegative_int_v21(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative plain integer")
    return value


def _require_bool_v21(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a bool")
    return cast(bool, value)


def _require_tuple_v21(value: object, label: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{label} must be a nominal tuple")
    return cast(tuple[object, ...], value)


def _require_canonical_digest_tuple_v21(
    value: object,
    *,
    domain: str,
    label: str,
    maximum: int,
) -> tuple[str, ...]:
    items = _require_tuple_v21(value, label)
    if len(items) > maximum:
        raise ValueError(f"{label} cap exceeded")
    digests = tuple(
        _require_domain_digest_v21(item, domain, f"{label} entry") for item in items
    )
    if digests != tuple(sorted(digests)):
        raise ValueError(f"{label} must be canonically sorted")
    if len(set(digests)) != len(digests):
        raise ValueError(f"{label} must be unique")
    return digests


def _require_binary_vector_v21(
    value: object, *, label: str, maximum: int
) -> tuple[int, ...]:
    items = _require_tuple_v21(value, label)
    if len(items) > maximum:
        raise ValueError(f"{label} cap exceeded")
    vector: list[int] = []
    for bit in items:
        if type(bit) is not int or bit not in (0, 1):
            raise ValueError(f"{label} must contain binary plain integers")
        vector.append(cast(int, bit))
    return tuple(vector)


def _require_role_v21(value: object, label: str) -> CapsuleRoleV21:
    if type(value) is not CapsuleRoleV21:
        raise ValueError(f"{label} must be a closed V21 role")
    return cast(CapsuleRoleV21, value)


def _validate_role_counts_v21(
    value: object, *, label: str, maximum_count: int
) -> tuple[tuple[CapsuleRoleV21, int], ...]:
    items = _require_tuple_v21(value, label)
    pairs: list[tuple[CapsuleRoleV21, int]] = []
    for item in items:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError(f"{label} must contain (role, count) pairs")
        pair = cast(tuple[object, object], item)
        role = _require_role_v21(pair[0], f"{label} role")
        count = _require_nonnegative_int_v21(pair[1], f"{label} count")
        if count > maximum_count:
            raise ValueError(f"{label} count cap exceeded")
        pairs.append((role, count))
    canonical = tuple(sorted(pairs, key=lambda pair: pair[0].value))
    if tuple(pairs) != canonical:
        raise ValueError(f"{label} must be canonically sorted")
    if len({role for role, _ in pairs}) != len(pairs):
        raise ValueError(f"{label} roles must be unique")
    return tuple(pairs)


class ReencodingOutcomeV21(StrEnum):
    EXACT = "EXACT"
    SEGMENT = "SEGMENT"
    DROP = "DROP"


class SegmentDispositionV21(StrEnum):
    RETAIN = "RETAIN"
    COMPACT = "COMPACT"
    DROP = "DROP"


class SolverModeV21(StrEnum):
    EXACT_SMALL = "EXACT_SMALL"
    DETERMINISTIC_GREEDY = "DETERMINISTIC_GREEDY"


@dataclass(frozen=True, slots=True)
class SourceRecordV21:
    """Closed transient source facade; it deliberately does not claim closure."""

    record_id: str
    namespace: str
    incarnation: int
    as_of: int
    commitment: str
    body: str
    core_required: bool
    active: bool
    hard_depends_on: tuple[HardDependencyV21, ...]

    def __post_init__(self) -> None:
        validate_source_record_v21(self)


def validate_source_record_v21(record: SourceRecordV21) -> None:
    """Validate one facade record without asserting any base-state closure."""
    if type(record) is not SourceRecordV21:
        raise TypeError("source records require nominal SourceRecordV21")
    record_id = _require_domain_digest_v21(
        record.record_id, _RECORD_ID_DOMAIN, "source record_id"
    )
    namespace = _require_domain_digest_v21(
        record.namespace, _NAMESPACE_ID_DOMAIN, "source namespace"
    )
    incarnation = _require_nonnegative_int_v21(record.incarnation, "source incarnation")
    as_of = _require_nonnegative_int_v21(record.as_of, "source as_of")
    body = _require_text_v21(record.body, "source body")
    if len(body.encode("utf-8")) > MAX_EVIDENCED_SOURCE_BODY_BYTES_PER_RECORD_V21:
        raise ValueError("source body byte cap exceeded")
    core_required = _require_bool_v21(record.core_required, "source core_required")
    active = _require_bool_v21(record.active, "source active")
    commitment = _require_domain_digest_v21(
        record.commitment, _SOURCE_COMMITMENT_DOMAIN, "source commitment"
    )
    if commitment != source_commitment_v21(
        record_id=record_id,
        namespace=namespace,
        incarnation=incarnation,
        as_of=as_of,
        body=body,
        core_required=core_required,
        active=active,
    ):
        raise ValueError("source commitment must bind body and identity metadata")
    dependencies = _require_tuple_v21(record.hard_depends_on, "source hard_depends_on")
    if len(dependencies) > MAX_EVIDENCED_HARD_EDGES_V21:
        raise ValueError("source hard_depends_on cap exceeded")
    validated: list[HardDependencyV21] = []
    for dependency in dependencies:
        if type(dependency) is not HardDependencyV21:
            raise ValueError(
                "source hard dependency requires nominal HardDependencyV21"
            )
        exact = cast(HardDependencyV21, dependency)
        _require_domain_digest_v21(
            exact.target_id, _RECORD_ID_DOMAIN, "source hard dependency target_id"
        )
        _require_domain_digest_v21(
            exact.target_commitment,
            _SOURCE_COMMITMENT_DOMAIN,
            "source hard dependency target_commitment",
        )
        validated.append(exact)
    target_ids = tuple(item.target_id for item in validated)
    if target_ids != tuple(sorted(target_ids)):
        raise ValueError("source hard dependencies must be canonically sorted")
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("source hard dependency target ids must be unique")


@dataclass(frozen=True, slots=True)
class SourceGraphNodeV21:
    record_id: str
    source_commitment: str

    def __post_init__(self) -> None:
        _validate_graph_node_v21(self)


def _validate_graph_node_v21(node: SourceGraphNodeV21) -> None:
    if type(node) is not SourceGraphNodeV21:
        raise TypeError("graph nodes require nominal SourceGraphNodeV21")
    _require_domain_digest_v21(
        node.record_id, _RECORD_ID_DOMAIN, "graph node record_id"
    )
    _require_domain_digest_v21(
        node.source_commitment,
        _SOURCE_COMMITMENT_DOMAIN,
        "graph node source_commitment",
    )


@dataclass(frozen=True, slots=True)
class HardGraphEdgeV21:
    source_id: str
    source_commitment: str
    target_id: str
    target_commitment: str

    def __post_init__(self) -> None:
        _validate_hard_graph_edge_v21(self)


def _validate_hard_graph_edge_v21(edge: HardGraphEdgeV21) -> None:
    if type(edge) is not HardGraphEdgeV21:
        raise TypeError("hard edges require nominal HardGraphEdgeV21")
    _require_domain_digest_v21(edge.source_id, _RECORD_ID_DOMAIN, "hard edge source_id")
    _require_domain_digest_v21(
        edge.source_commitment,
        _SOURCE_COMMITMENT_DOMAIN,
        "hard edge source_commitment",
    )
    _require_domain_digest_v21(edge.target_id, _RECORD_ID_DOMAIN, "hard edge target_id")
    _require_domain_digest_v21(
        edge.target_commitment,
        _SOURCE_COMMITMENT_DOMAIN,
        "hard edge target_commitment",
    )


def _validate_graph_nodes_v21(value: object) -> tuple[SourceGraphNodeV21, ...]:
    items = _require_tuple_v21(value, "graph nodes")
    if len(items) > MAX_EVIDENCED_SOURCE_RECORDS_V21:
        raise ValueError("graph nodes cap exceeded")
    nodes: list[SourceGraphNodeV21] = []
    for item in items:
        if type(item) is not SourceGraphNodeV21:
            raise ValueError("graph nodes require nominal SourceGraphNodeV21")
        node = cast(SourceGraphNodeV21, item)
        _validate_graph_node_v21(node)
        nodes.append(node)
    record_ids = tuple(node.record_id for node in nodes)
    if record_ids != tuple(sorted(record_ids)):
        raise ValueError("graph nodes must be canonically sorted")
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("graph node record ids must be unique")
    return tuple(nodes)


def _validate_hard_edges_v21(value: object) -> tuple[HardGraphEdgeV21, ...]:
    items = _require_tuple_v21(value, "hard edges")
    if len(items) > MAX_EVIDENCED_HARD_EDGES_V21:
        raise ValueError("hard edges cap exceeded")
    edges: list[HardGraphEdgeV21] = []
    for item in items:
        if type(item) is not HardGraphEdgeV21:
            raise ValueError("hard edges require nominal HardGraphEdgeV21")
        edge = cast(HardGraphEdgeV21, item)
        _validate_hard_graph_edge_v21(edge)
        edges.append(edge)
    identities = tuple((edge.source_id, edge.target_id) for edge in edges)
    if identities != tuple(sorted(identities)):
        raise ValueError("hard edges must be canonically sorted")
    if len(set(identities)) != len(identities):
        raise ValueError("hard edge identities must be unique")
    return tuple(edges)


def hard_graph_root_v21(
    *,
    graph_nodes: tuple[SourceGraphNodeV21, ...],
    hard_edges: tuple[HardGraphEdgeV21, ...],
) -> str:
    """Commit a complete, ordered hard graph independently of source bodies."""
    nodes = _validate_graph_nodes_v21(graph_nodes)
    edges = _validate_hard_edges_v21(hard_edges)
    node_by_id = {node.record_id: node for node in nodes}
    for edge in edges:
        source = node_by_id.get(edge.source_id)
        target = node_by_id.get(edge.target_id)
        if source is None or target is None:
            raise ValueError("hard edge endpoints must occur in graph nodes")
        if source.source_commitment != edge.source_commitment:
            raise ValueError("hard edge source commitment must match graph node")
        if target.source_commitment != edge.target_commitment:
            raise ValueError("hard edge target commitment must match graph node")
    return _domain_hash_v21(
        HARD_GRAPH_ROOT_DOMAIN_V21,
        {"graph_nodes": nodes, "hard_edges": edges},
    )


def _validate_representatives_v21(
    value: object,
) -> tuple[CapsuleRepresentativeV21, ...]:
    items = _require_tuple_v21(value, "representative candidates")
    if len(items) > MAX_EVIDENCED_REPRESENTATIVE_CANDIDATES_V21:
        raise ValueError("representative candidates cap exceeded")
    representatives: list[CapsuleRepresentativeV21] = []
    for item in items:
        if type(item) is not CapsuleRepresentativeV21:
            raise ValueError("representative candidates require nominal V21 rows")
        candidate = cast(CapsuleRepresentativeV21, item)
        feature_id = _require_domain_digest_v21(
            candidate.feature_id,
            _REPRESENTATIVE_ID_DOMAIN,
            "representative candidate feature_id",
        )
        feature_commitment = _require_domain_digest_v21(
            candidate.feature_commitment,
            _REPRESENTATIVE_COMMITMENT_DOMAIN,
            "representative candidate commitment",
        )
        if feature_commitment != representative_commitment_v21(feature_id):
            raise ValueError("representative candidate commitment must bind feature_id")
        if isinstance(candidate.weight, bool) or not isinstance(
            candidate.weight, (int, float)
        ):
            raise ValueError("representative candidate weight must be finite")
        if not isfinite(float(candidate.weight)):
            raise ValueError("representative candidate weight must be finite")
        representatives.append(candidate)
    feature_ids = tuple(candidate.feature_id for candidate in representatives)
    if feature_ids != tuple(sorted(feature_ids)):
        raise ValueError("representative candidates must be canonically sorted")
    if len(set(feature_ids)) != len(feature_ids):
        raise ValueError("representative candidate feature ids must be unique")
    return tuple(representatives)


@dataclass(frozen=True, slots=True)
class FoldContributionEvidenceV21:
    record_id: str
    source_commitment: str
    contribution_keys: tuple[str, ...]
    role: CapsuleRoleV21
    coverage_vector: tuple[int, ...]
    bridge_vector: tuple[int, ...]
    representative_candidates: tuple[CapsuleRepresentativeV21, ...]

    def __post_init__(self) -> None:
        validate_fold_contribution_evidence_v21(self)


def validate_fold_contribution_evidence_v21(row: FoldContributionEvidenceV21) -> None:
    if type(row) is not FoldContributionEvidenceV21:
        raise TypeError("contribution rows require nominal FoldContributionEvidenceV21")
    _require_domain_digest_v21(
        row.record_id, _RECORD_ID_DOMAIN, "contribution record_id"
    )
    _require_domain_digest_v21(
        row.source_commitment,
        _SOURCE_COMMITMENT_DOMAIN,
        "contribution source_commitment",
    )
    _require_canonical_digest_tuple_v21(
        row.contribution_keys,
        domain=_DICTIONARY_ID_DOMAIN,
        label="contribution keys",
        maximum=MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21,
    )
    _require_role_v21(row.role, "contribution role")
    _require_binary_vector_v21(
        row.coverage_vector,
        label="contribution coverage_vector",
        maximum=MAX_EVIDENCED_COVERAGE_VECTOR_V21,
    )
    _require_binary_vector_v21(
        row.bridge_vector,
        label="contribution bridge_vector",
        maximum=MAX_EVIDENCED_BRIDGE_VECTOR_V21,
    )
    _validate_representatives_v21(row.representative_candidates)


def _validate_contribution_rows_v21(
    value: object,
) -> tuple[FoldContributionEvidenceV21, ...]:
    items = _require_tuple_v21(value, "contribution evidence")
    if len(items) > MAX_EVIDENCED_SOURCE_RECORDS_V21:
        raise ValueError("contribution evidence cap exceeded")
    rows: list[FoldContributionEvidenceV21] = []
    for item in items:
        if type(item) is not FoldContributionEvidenceV21:
            raise ValueError("contribution evidence requires nominal rows")
        row = cast(FoldContributionEvidenceV21, item)
        validate_fold_contribution_evidence_v21(row)
        rows.append(row)
    record_ids = tuple(row.record_id for row in rows)
    if record_ids != tuple(sorted(record_ids)):
        raise ValueError("contribution evidence must be canonically sorted")
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("contribution evidence record ids must be unique")
    return tuple(rows)


def contribution_root_v21(
    contribution_evidence: tuple[FoldContributionEvidenceV21, ...],
) -> str:
    """Commit complete canonical contribution rows in their own domain."""
    rows = _validate_contribution_rows_v21(contribution_evidence)
    return _domain_hash_v21(CONTRIBUTION_ROOT_DOMAIN_V21, {"rows": rows})


def _validate_source_records_v21(value: object) -> tuple[SourceRecordV21, ...]:
    items = _require_tuple_v21(value, "source_records")
    if len(items) > MAX_EVIDENCED_SOURCE_RECORDS_V21:
        raise ValueError("source_records cap exceeded")
    records: list[SourceRecordV21] = []
    for item in items:
        if type(item) is not SourceRecordV21:
            raise ValueError("source_records require nominal SourceRecordV21")
        record = cast(SourceRecordV21, item)
        validate_source_record_v21(record)
        if not record.active:
            raise ValueError("source_records cannot contain inactive records")
        records.append(record)
    total_hard_edges = sum(len(record.hard_depends_on) for record in records)
    if total_hard_edges > MAX_EVIDENCED_HARD_EDGES_V21:
        raise ValueError("source_records aggregate hard-edge cap exceeded")
    total_body_bytes = sum(len(record.body.encode("utf-8")) for record in records)
    if total_body_bytes > MAX_EVIDENCED_SOURCE_BODY_BYTES_TOTAL_V21:
        raise ValueError("source_records aggregate body-byte cap exceeded")
    record_ids = tuple(record.record_id for record in records)
    if record_ids != tuple(sorted(record_ids)):
        raise ValueError("source_records must be canonically sorted")
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("source_records record ids must be unique")
    return tuple(records)


def _validate_incoming_record_ids_v21(
    value: object, source_records: tuple[SourceRecordV21, ...]
) -> tuple[str, ...]:
    incoming = _require_canonical_digest_tuple_v21(
        value,
        domain=_RECORD_ID_DOMAIN,
        label="incoming_record_ids",
        maximum=MAX_EVIDENCED_SOURCE_RECORDS_V21,
    )
    source_ids = {record.record_id for record in source_records}
    if not set(incoming).issubset(source_ids):
        raise ValueError("incoming_record_ids must be a source_records subset")
    return incoming


def _expected_graph_v21(
    source_records: tuple[SourceRecordV21, ...],
) -> tuple[tuple[SourceGraphNodeV21, ...], tuple[HardGraphEdgeV21, ...]]:
    nodes = tuple(
        SourceGraphNodeV21(record.record_id, record.commitment)
        for record in source_records
    )
    edges = tuple(
        HardGraphEdgeV21(
            source_id=record.record_id,
            source_commitment=record.commitment,
            target_id=dependency.target_id,
            target_commitment=dependency.target_commitment,
        )
        for record in source_records
        for dependency in record.hard_depends_on
    )
    return nodes, edges


def source_envelope_root_v21(
    *,
    base_state_hash: str,
    source_records: tuple[SourceRecordV21, ...],
    incoming_record_ids: tuple[str, ...],
    graph_nodes: tuple[SourceGraphNodeV21, ...],
    hard_edges: tuple[HardGraphEdgeV21, ...],
    contribution_evidence: tuple[FoldContributionEvidenceV21, ...],
    hard_graph_root: str,
    contribution_root: str,
) -> str:
    """Validate a complete pre-root envelope before emitting its compact root."""
    (
        base_hash,
        records,
        incoming,
        _nodes,
        _edges,
        _rows,
        hard_root,
        contributions,
    ) = _normalize_source_envelope_payload_v21(
        base_state_hash=base_state_hash,
        source_records=source_records,
        incoming_record_ids=incoming_record_ids,
        graph_nodes=graph_nodes,
        hard_edges=hard_edges,
        contribution_evidence=contribution_evidence,
        hard_graph_root=hard_graph_root,
        contribution_root=contribution_root,
    )
    return _domain_hash_v21(
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_records": records,
            "incoming_record_ids": incoming,
            "hard_graph_root": hard_root,
            "contribution_root": contributions,
        },
    )


def _normalize_source_envelope_payload_v21(
    *,
    base_state_hash: str,
    source_records: tuple[SourceRecordV21, ...],
    incoming_record_ids: tuple[str, ...],
    graph_nodes: tuple[SourceGraphNodeV21, ...],
    hard_edges: tuple[HardGraphEdgeV21, ...],
    contribution_evidence: tuple[FoldContributionEvidenceV21, ...],
    hard_graph_root: str,
    contribution_root: str,
) -> tuple[
    str,
    tuple[SourceRecordV21, ...],
    tuple[str, ...],
    tuple[SourceGraphNodeV21, ...],
    tuple[HardGraphEdgeV21, ...],
    tuple[FoldContributionEvidenceV21, ...],
    str,
    str,
]:
    """Normalize every full-envelope field before a source root can exist."""
    base_hash = _require_domain_digest_v21(
        base_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "envelope base_state_hash"
    )
    hard_root = _require_domain_digest_v21(
        hard_graph_root, HARD_GRAPH_ROOT_DOMAIN_V21, "envelope hard_graph_root"
    )
    contributions = _require_domain_digest_v21(
        contribution_root, CONTRIBUTION_ROOT_DOMAIN_V21, "envelope contribution_root"
    )
    records = _validate_source_records_v21(source_records)
    incoming = _validate_incoming_record_ids_v21(incoming_record_ids, records)
    nodes = _validate_graph_nodes_v21(graph_nodes)
    edges = _validate_hard_edges_v21(hard_edges)
    rows = _validate_contribution_rows_v21(contribution_evidence)
    expected_nodes, expected_edges = _expected_graph_v21(records)
    if nodes != expected_nodes:
        raise ValueError("graph nodes must exactly equal source_records")
    if edges != expected_edges:
        raise ValueError("hard edges must exactly flatten source_records")
    record_by_id = {record.record_id: record for record in records}
    if tuple(row.record_id for row in rows) != tuple(record_by_id):
        raise ValueError("contribution rows must exactly cover source_records")
    for row in rows:
        if row.source_commitment != record_by_id[row.record_id].commitment:
            raise ValueError("contribution commitment must match its source record")
    if (
        _canonical_envelope_bytes_v21(
            base_state_hash=base_hash,
            source_records=records,
            incoming_record_ids=incoming,
            graph_nodes=nodes,
            hard_edges=edges,
            contribution_evidence=rows,
            hard_graph_root=hard_root,
            contribution_root=contributions,
        )
        > MAX_EVIDENCED_CANONICAL_ENVELOPE_BYTES_V21
    ):
        raise ValueError("canonical envelope byte cap exceeded")
    expected_hard_root = hard_graph_root_v21(graph_nodes=nodes, hard_edges=edges)
    if hard_root != expected_hard_root:
        raise ValueError("envelope hard_graph_root does not bind graph rows")
    expected_contribution_root = contribution_root_v21(rows)
    if contributions != expected_contribution_root:
        raise ValueError("envelope contribution_root does not bind contribution rows")
    return (
        base_hash,
        records,
        incoming,
        nodes,
        edges,
        rows,
        hard_root,
        contributions,
    )


@dataclass(frozen=True, slots=True)
class SourceEnvelopeV21:
    base_state_hash: str
    source_records: tuple[SourceRecordV21, ...]
    incoming_record_ids: tuple[str, ...]
    graph_nodes: tuple[SourceGraphNodeV21, ...]
    hard_edges: tuple[HardGraphEdgeV21, ...]
    contribution_evidence: tuple[FoldContributionEvidenceV21, ...]
    hard_graph_root: str
    contribution_root: str
    envelope_root: str

    def __post_init__(self) -> None:
        validate_source_envelope_v21(self)


def _canonical_envelope_bytes_v21(
    *,
    base_state_hash: str,
    source_records: tuple[SourceRecordV21, ...],
    incoming_record_ids: tuple[str, ...],
    graph_nodes: tuple[SourceGraphNodeV21, ...],
    hard_edges: tuple[HardGraphEdgeV21, ...],
    contribution_evidence: tuple[FoldContributionEvidenceV21, ...],
    hard_graph_root: str,
    contribution_root: str,
) -> int:
    """Measure the complete envelope immediately before its claimed root exists."""
    return len(
        canonical_json_v21(
            {
                "base_state_hash": base_state_hash,
                "source_records": source_records,
                "incoming_record_ids": incoming_record_ids,
                "graph_nodes": graph_nodes,
                "hard_edges": hard_edges,
                "contribution_evidence": contribution_evidence,
                "hard_graph_root": hard_graph_root,
                "contribution_root": contribution_root,
            }
        ).encode("utf-8")
    )


def validate_source_envelope_v21(envelope: SourceEnvelopeV21) -> None:
    """Require the graph and evidence rows to exactly cover the source universe."""
    if type(envelope) is not SourceEnvelopeV21:
        raise TypeError("source envelopes require nominal SourceEnvelopeV21")
    claimed_envelope_root = _require_domain_digest_v21(
        envelope.envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "envelope envelope_root",
    )
    expected_envelope_root = source_envelope_root_v21(
        base_state_hash=envelope.base_state_hash,
        source_records=envelope.source_records,
        incoming_record_ids=envelope.incoming_record_ids,
        graph_nodes=envelope.graph_nodes,
        hard_edges=envelope.hard_edges,
        contribution_evidence=envelope.contribution_evidence,
        hard_graph_root=envelope.hard_graph_root,
        contribution_root=envelope.contribution_root,
    )
    if claimed_envelope_root != expected_envelope_root:
        raise ValueError("envelope_root does not bind the frozen envelope payload")


@dataclass(frozen=True, slots=True)
class EvidencedReencodingPolicyV21:
    frame_policy_hash: str
    max_matrix_rows: int
    max_hard_edges: int
    max_decisions: int
    max_plan_evaluations: int
    max_new_segments: int
    max_loss_units: int
    per_role_loss_caps: tuple[tuple[CapsuleRoleV21, int], ...]
    segment_loss_units: int
    drop_loss_units: int
    compaction_loss_units: int
    solver_mode: SolverModeV21

    def __post_init__(self) -> None:
        validate_evidenced_reencoding_policy_v21(self)


def validate_evidenced_reencoding_policy_v21(
    policy: EvidencedReencodingPolicyV21,
) -> None:
    if type(policy) is not EvidencedReencodingPolicyV21:
        raise TypeError(
            "evidenced policies require nominal EvidencedReencodingPolicyV21"
        )
    _require_domain_digest_v21(
        policy.frame_policy_hash,
        _REENCODING_POLICY_DOMAIN,
        "evidenced policy frame_policy_hash",
    )
    limits = (
        ("max_matrix_rows", policy.max_matrix_rows, MAX_EVIDENCED_SOURCE_RECORDS_V21),
        ("max_hard_edges", policy.max_hard_edges, MAX_EVIDENCED_HARD_EDGES_V21),
        ("max_decisions", policy.max_decisions, MAX_EVIDENCED_ROOT_ROWS_V21),
        (
            "max_plan_evaluations",
            policy.max_plan_evaluations,
            MAX_EVIDENCED_PLAN_EVALUATIONS_V21,
        ),
        ("max_new_segments", policy.max_new_segments, MAX_EVIDENCED_NEW_SEGMENTS_V21),
        ("max_loss_units", policy.max_loss_units, MAX_EVIDENCED_LOSS_UNITS_V21),
        (
            "segment_loss_units",
            policy.segment_loss_units,
            MAX_EVIDENCED_LOSS_UNITS_V21,
        ),
        ("drop_loss_units", policy.drop_loss_units, MAX_EVIDENCED_LOSS_UNITS_V21),
        (
            "compaction_loss_units",
            policy.compaction_loss_units,
            MAX_EVIDENCED_LOSS_UNITS_V21,
        ),
    )
    for label, value, maximum in limits:
        checked = _require_nonnegative_int_v21(value, f"evidenced policy {label}")
        if checked > maximum:
            raise ValueError(f"evidenced policy {label} cap exceeded")
    _validate_role_counts_v21(
        policy.per_role_loss_caps,
        label="evidenced policy per_role_loss_caps",
        maximum_count=MAX_EVIDENCED_LOSS_UNITS_V21,
    )
    if type(policy.solver_mode) is not SolverModeV21:
        raise ValueError("evidenced policy solver_mode must be closed")


def evidenced_reencoding_policy_root_v21(policy: EvidencedReencodingPolicyV21) -> str:
    """Commit every frozen policy bound and its closed solver mode."""
    validate_evidenced_reencoding_policy_v21(policy)
    return _domain_hash_v21(EVIDENCED_POLICY_ROOT_DOMAIN_V21, {"policy": policy})


@dataclass(frozen=True, slots=True)
class RecordDecisionV21:
    record_id: str
    source_commitment: str
    outcome: ReencodingOutcomeV21
    child_segment_id: str | None
    contribution_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_record_decision_v21(self)


def validate_record_decision_v21(decision: RecordDecisionV21) -> None:
    if type(decision) is not RecordDecisionV21:
        raise TypeError("record decisions require nominal RecordDecisionV21")
    _require_domain_digest_v21(
        decision.record_id, _RECORD_ID_DOMAIN, "record decision id"
    )
    _require_domain_digest_v21(
        decision.source_commitment,
        _SOURCE_COMMITMENT_DOMAIN,
        "record decision source_commitment",
    )
    if type(decision.outcome) is not ReencodingOutcomeV21:
        raise ValueError("record decision outcome must be closed")
    if decision.outcome is ReencodingOutcomeV21.SEGMENT:
        _require_domain_digest_v21(
            decision.child_segment_id,
            _SEGMENT_ID_DOMAIN,
            "record decision child_segment_id",
        )
    elif decision.child_segment_id is not None:
        raise ValueError("EXACT and DROP record decisions cannot name a child segment")
    _require_canonical_digest_tuple_v21(
        decision.contribution_keys,
        domain=_DICTIONARY_ID_DOMAIN,
        label="record decision contribution_keys",
        maximum=MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21,
    )


def _validate_record_decision_rows_v21(
    value: object,
) -> tuple[RecordDecisionV21, ...]:
    items = _require_tuple_v21(value, "record decision rows")
    if len(items) > MAX_EVIDENCED_ROOT_ROWS_V21:
        raise ValueError("record decision rows cap exceeded")
    rows: list[RecordDecisionV21] = []
    for item in items:
        if type(item) is not RecordDecisionV21:
            raise ValueError("record decision rows require nominal rows")
        row = cast(RecordDecisionV21, item)
        validate_record_decision_v21(row)
        rows.append(row)
    identities = tuple((row.record_id, row.source_commitment) for row in rows)
    if identities != tuple(sorted(identities)):
        raise ValueError("record decision rows must be canonically sorted")
    if len(set(identities)) != len(identities):
        raise ValueError("record decision identities must be unique")
    return tuple(rows)


def record_decision_root_v21(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    rows: tuple[RecordDecisionV21, ...],
) -> str:
    """Commit record decision rows with their external evidence context."""
    base_hash = _require_domain_digest_v21(
        base_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "record decisions base_state_hash",
    )
    envelope_root = _require_domain_digest_v21(
        source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "record decisions source_envelope_root",
    )
    checked_policy_root = _require_domain_digest_v21(
        policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "record decisions policy_root"
    )
    checked_rows = _validate_record_decision_rows_v21(rows)
    return _domain_hash_v21(
        RECORD_DECISION_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_envelope_root": envelope_root,
            "policy_root": checked_policy_root,
            "rows": checked_rows,
        },
    )


@dataclass(frozen=True, slots=True)
class SegmentDecisionV21:
    segment_id: str
    canonical_segment_hash: str
    disposition: SegmentDispositionV21
    child_segment_id: str | None
    contribution_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_segment_decision_v21(self)


def validate_segment_decision_v21(decision: SegmentDecisionV21) -> None:
    if type(decision) is not SegmentDecisionV21:
        raise TypeError("segment decisions require nominal SegmentDecisionV21")
    segment_id = _require_domain_digest_v21(
        decision.segment_id, _SEGMENT_ID_DOMAIN, "segment decision segment_id"
    )
    _require_domain_digest_v21(
        decision.canonical_segment_hash,
        CANONICAL_SEGMENT_HASH_DOMAIN_V21,
        "segment decision canonical_segment_hash",
    )
    if type(decision.disposition) is not SegmentDispositionV21:
        raise ValueError("segment decision disposition must be closed")
    if decision.disposition is SegmentDispositionV21.COMPACT:
        child = _require_domain_digest_v21(
            decision.child_segment_id,
            _SEGMENT_ID_DOMAIN,
            "segment decision child_segment_id",
        )
        if child == segment_id:
            raise ValueError("COMPACT segment decision cannot name itself as child")
    elif decision.child_segment_id is not None:
        raise ValueError(
            "RETAIN and DROP segment decisions cannot name a child segment"
        )
    _require_canonical_digest_tuple_v21(
        decision.contribution_keys,
        domain=_DICTIONARY_ID_DOMAIN,
        label="segment decision contribution_keys",
        maximum=MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21,
    )


def _validate_segment_decision_rows_v21(
    value: object,
) -> tuple[SegmentDecisionV21, ...]:
    items = _require_tuple_v21(value, "segment decision rows")
    if len(items) > MAX_EVIDENCED_ROOT_ROWS_V21:
        raise ValueError("segment decision rows cap exceeded")
    rows: list[SegmentDecisionV21] = []
    for item in items:
        if type(item) is not SegmentDecisionV21:
            raise ValueError("segment decision rows require nominal rows")
        row = cast(SegmentDecisionV21, item)
        validate_segment_decision_v21(row)
        rows.append(row)
    segment_ids = tuple(row.segment_id for row in rows)
    if segment_ids != tuple(sorted(segment_ids)):
        raise ValueError("segment decision rows must be canonically sorted")
    if len(set(segment_ids)) != len(segment_ids):
        raise ValueError("segment decision segment ids must be unique")
    return tuple(rows)


def segment_decision_root_v21(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    rows: tuple[SegmentDecisionV21, ...],
) -> str:
    """Commit segment decisions and complete canonical segment-hash references."""
    base_hash = _require_domain_digest_v21(
        base_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "segment decisions base_state_hash",
    )
    envelope_root = _require_domain_digest_v21(
        source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "segment decisions source_envelope_root",
    )
    checked_policy_root = _require_domain_digest_v21(
        policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "segment decisions policy_root"
    )
    checked_rows = _validate_segment_decision_rows_v21(rows)
    return _domain_hash_v21(
        SEGMENT_DECISION_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_envelope_root": envelope_root,
            "policy_root": checked_policy_root,
            "rows": checked_rows,
        },
    )


def canonical_segment_hash_v21(state: CapsuleStateV21, segment_id: str) -> str:
    """Hash exactly one current, publicly validated state's full segment bytes."""
    validate_capsule_state_v21(state)
    checked_id = _require_domain_digest_v21(
        segment_id, _SEGMENT_ID_DOMAIN, "canonical segment segment_id"
    )
    for segment in state.capsule_segments:
        if segment.segment_id == checked_id:
            return _domain_hash_v21(
                CANONICAL_SEGMENT_HASH_DOMAIN_V21,
                {"canonical_segment_bytes": canonical_json_v21(segment)},
            )
    raise ValueError("canonical segment must name a current validated segment")


@dataclass(frozen=True, slots=True)
class FirstFoldSourceV21:
    record_id: str
    source_commitment: str
    contribution_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_first_fold_source_v21(self)


def _validate_first_fold_source_v21(source: FirstFoldSourceV21) -> None:
    if type(source) is not FirstFoldSourceV21:
        raise TypeError("first-fold sources require nominal FirstFoldSourceV21")
    _require_domain_digest_v21(
        source.record_id, _RECORD_ID_DOMAIN, "first-fold record_id"
    )
    _require_domain_digest_v21(
        source.source_commitment,
        _SOURCE_COMMITMENT_DOMAIN,
        "first-fold source_commitment",
    )
    _require_canonical_digest_tuple_v21(
        source.contribution_keys,
        domain=_DICTIONARY_ID_DOMAIN,
        label="first-fold contribution_keys",
        maximum=MAX_EVIDENCED_CONTRIBUTION_KEYS_PER_ROW_V21,
    )


def _validate_first_fold_sources_v21(value: object) -> tuple[FirstFoldSourceV21, ...]:
    items = _require_tuple_v21(value, "first-fold sources")
    if len(items) > MAX_EVIDENCED_ROOT_ROWS_V21:
        raise ValueError("first-fold sources cap exceeded")
    sources: list[FirstFoldSourceV21] = []
    for item in items:
        if type(item) is not FirstFoldSourceV21:
            raise ValueError("first-fold sources require nominal rows")
        source = cast(FirstFoldSourceV21, item)
        _validate_first_fold_source_v21(source)
        sources.append(source)
    record_ids = tuple(source.record_id for source in sources)
    if record_ids != tuple(sorted(record_ids)):
        raise ValueError("first-fold sources must be canonically sorted")
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("first-fold source record ids must be unique")
    return tuple(sources)


def first_fold_authorization_root_v21(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    namespace: str,
    incarnation: int,
    generation: int,
    child_segment_id: str,
    sources: tuple[FirstFoldSourceV21, ...],
) -> str:
    """Commit the single-use rootless-child authorization payload."""
    base_hash = _require_domain_digest_v21(
        base_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "first-fold base_state_hash"
    )
    envelope_root = _require_domain_digest_v21(
        source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "first-fold source_envelope_root",
    )
    checked_policy_root = _require_domain_digest_v21(
        policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "first-fold policy_root"
    )
    checked_namespace = _require_domain_digest_v21(
        namespace, _NAMESPACE_ID_DOMAIN, "first-fold namespace"
    )
    checked_incarnation = _require_nonnegative_int_v21(
        incarnation, "first-fold incarnation"
    )
    checked_generation = _require_nonnegative_int_v21(
        generation, "first-fold generation"
    )
    checked_child = _require_domain_digest_v21(
        child_segment_id, _SEGMENT_ID_DOMAIN, "first-fold child_segment_id"
    )
    checked_sources = _validate_first_fold_sources_v21(sources)
    return _domain_hash_v21(
        FIRST_FOLD_AUTHORIZATION_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_envelope_root": envelope_root,
            "policy_root": checked_policy_root,
            "namespace": checked_namespace,
            "incarnation": checked_incarnation,
            "generation": checked_generation,
            "child_segment_id": checked_child,
            "sources": checked_sources,
        },
    )


@dataclass(frozen=True, slots=True)
class FirstFoldAuthorizationV21:
    base_state_hash: str
    source_envelope_root: str
    policy_root: str
    namespace: str
    incarnation: int
    generation: int
    child_segment_id: str
    sources: tuple[FirstFoldSourceV21, ...]
    authorization_root: str

    def __post_init__(self) -> None:
        validate_first_fold_authorization_v21(self)


def validate_first_fold_authorization_v21(
    authorization: FirstFoldAuthorizationV21,
) -> None:
    if type(authorization) is not FirstFoldAuthorizationV21:
        raise TypeError(
            "first-fold authorization requires nominal FirstFoldAuthorizationV21"
        )
    claimed_root = _require_domain_digest_v21(
        authorization.authorization_root,
        FIRST_FOLD_AUTHORIZATION_ROOT_DOMAIN_V21,
        "first-fold authorization_root",
    )
    expected_root = first_fold_authorization_root_v21(
        base_state_hash=authorization.base_state_hash,
        source_envelope_root=authorization.source_envelope_root,
        policy_root=authorization.policy_root,
        namespace=authorization.namespace,
        incarnation=authorization.incarnation,
        generation=authorization.generation,
        child_segment_id=authorization.child_segment_id,
        sources=authorization.sources,
    )
    if claimed_root != expected_root:
        raise ValueError("authorization_root does not bind first-fold fields")


@dataclass(frozen=True, slots=True)
class ReleasedSourceV21:
    record_id: str
    source_commitment: str

    def __post_init__(self) -> None:
        _validate_released_source_v21(self)


def _validate_released_source_v21(source: ReleasedSourceV21) -> None:
    if type(source) is not ReleasedSourceV21:
        raise TypeError("released sources require nominal ReleasedSourceV21")
    _require_domain_digest_v21(
        source.record_id, _RECORD_ID_DOMAIN, "released record_id"
    )
    _require_domain_digest_v21(
        source.source_commitment,
        _SOURCE_COMMITMENT_DOMAIN,
        "released source_commitment",
    )


def _validate_released_sources_v21(value: object) -> tuple[ReleasedSourceV21, ...]:
    items = _require_tuple_v21(value, "released sources")
    if len(items) > MAX_EVIDENCED_ROOT_ROWS_V21:
        raise ValueError("released sources cap exceeded")
    sources: list[ReleasedSourceV21] = []
    for item in items:
        if type(item) is not ReleasedSourceV21:
            raise ValueError("released sources require nominal rows")
        source = cast(ReleasedSourceV21, item)
        _validate_released_source_v21(source)
        sources.append(source)
    record_ids = tuple(source.record_id for source in sources)
    if record_ids != tuple(sorted(record_ids)):
        raise ValueError("released sources must be canonically sorted")
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("released source record ids must be unique")
    return tuple(sources)


class AdvanceCursorKindV21(StrEnum):
    """The only permitted transient roots for an advance-chain cursor."""

    SEED = "SEED"
    ADVANCE = "ADVANCE"


def _require_advance_cursor_kind_v21(value: object, label: str) -> AdvanceCursorKindV21:
    if type(value) is not AdvanceCursorKindV21:
        raise ValueError(f"{label} must be a closed advance cursor kind")
    return cast(AdvanceCursorKindV21, value)


def _validate_barrier_durable_fields_v21(
    durable_root: object,
    high_water: object,
    *,
    label: str,
) -> tuple[str | None, int | None]:
    if (durable_root is None) != (high_water is None):
        raise ValueError(f"{label} durable_root and high_water must co-occur")
    if durable_root is None:
        return None, None
    return (
        _require_domain_digest_v21(
            durable_root, _DURABLE_BARRIER_ROOT_DOMAIN, f"{label} durable_root"
        ),
        _require_nonnegative_int_v21(high_water, f"{label} high_water"),
    )


@dataclass(frozen=True, slots=True)
class BarrierAdvanceCursorV21:
    namespace: str
    incarnation: int
    durable_root: str | None
    high_water: int | None
    kind: AdvanceCursorKindV21
    transient_root: str

    def __post_init__(self) -> None:
        _validate_barrier_advance_cursor_v21(self)


def _validate_barrier_advance_cursor_v21(
    cursor: BarrierAdvanceCursorV21,
) -> BarrierAdvanceCursorV21:
    if type(cursor) is not BarrierAdvanceCursorV21:
        raise TypeError("barrier cursors require nominal BarrierAdvanceCursorV21")
    _require_domain_digest_v21(
        cursor.namespace, _NAMESPACE_ID_DOMAIN, "barrier cursor namespace"
    )
    _require_nonnegative_int_v21(cursor.incarnation, "barrier cursor incarnation")
    _validate_barrier_durable_fields_v21(
        cursor.durable_root,
        cursor.high_water,
        label="barrier cursor",
    )
    kind = _require_advance_cursor_kind_v21(cursor.kind, "barrier cursor kind")
    transient_domain = (
        BARRIER_ADVANCE_SEED_ROOT_DOMAIN_V21
        if kind is AdvanceCursorKindV21.SEED
        else BARRIER_ADVANCE_ROOT_DOMAIN_V21
    )
    _require_domain_digest_v21(
        cursor.transient_root,
        transient_domain,
        "barrier cursor transient_root",
    )
    return cursor


def _validate_barrier_advance_cursors_v21(
    value: object,
) -> tuple[BarrierAdvanceCursorV21, ...]:
    items = _require_tuple_v21(value, "barrier cursors")
    if len(items) > MAX_EVIDENCED_ROOT_ROWS_V21:
        raise ValueError("barrier cursors cap exceeded")
    cursors: list[BarrierAdvanceCursorV21] = []
    for item in items:
        if type(item) is not BarrierAdvanceCursorV21:
            raise ValueError("barrier cursors require nominal rows")
        cursors.append(
            _validate_barrier_advance_cursor_v21(cast(BarrierAdvanceCursorV21, item))
        )
    identities = tuple((cursor.namespace, cursor.incarnation) for cursor in cursors)
    if identities != tuple(sorted(identities)):
        raise ValueError("barrier cursors must be canonically sorted")
    if len(set(identities)) != len(identities):
        raise ValueError("barrier cursor identities must be unique")
    return tuple(cursors)


@dataclass(frozen=True, slots=True)
class LedgerAdvanceCursorV21:
    durable_root: str
    role_counts: tuple[tuple[CapsuleRoleV21, int], ...]
    generation: int
    kind: AdvanceCursorKindV21
    transient_root: str

    def __post_init__(self) -> None:
        _validate_ledger_advance_cursor_v21(self)


def _validate_ledger_advance_cursor_v21(
    cursor: LedgerAdvanceCursorV21,
) -> LedgerAdvanceCursorV21:
    if type(cursor) is not LedgerAdvanceCursorV21:
        raise TypeError("ledger cursors require nominal LedgerAdvanceCursorV21")
    _require_domain_digest_v21(
        cursor.durable_root, _DURABLE_LEDGER_ROOT_DOMAIN, "ledger cursor durable_root"
    )
    _validate_role_counts_v21(
        cursor.role_counts,
        label="ledger cursor role_counts",
        maximum_count=MAX_EVIDENCED_LOSS_UNITS_V21,
    )
    _require_nonnegative_int_v21(cursor.generation, "ledger cursor generation")
    kind = _require_advance_cursor_kind_v21(cursor.kind, "ledger cursor kind")
    transient_domain = (
        LEDGER_ADVANCE_SEED_ROOT_DOMAIN_V21
        if kind is AdvanceCursorKindV21.SEED
        else LEDGER_ADVANCE_ROOT_DOMAIN_V21
    )
    _require_domain_digest_v21(
        cursor.transient_root,
        transient_domain,
        "ledger cursor transient_root",
    )
    return cursor


def barrier_advance_seed_root_v21(
    *,
    base_state_hash: str,
    prior_advance_head_root: str | None,
    namespace: str,
    incarnation: int,
    durable_root: str | None,
    high_water: int | None,
) -> str:
    """Commit the explicit durable anchor for one barrier seed cursor."""
    base_hash = _require_domain_digest_v21(
        base_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "barrier seed base_state_hash",
    )
    prior_head = (
        None
        if prior_advance_head_root is None
        else _require_domain_digest_v21(
            prior_advance_head_root,
            ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
            "barrier seed prior_advance_head_root",
        )
    )
    checked_namespace = _require_domain_digest_v21(
        namespace, _NAMESPACE_ID_DOMAIN, "barrier seed namespace"
    )
    checked_incarnation = _require_nonnegative_int_v21(
        incarnation, "barrier seed incarnation"
    )
    checked_durable_root, checked_high_water = _validate_barrier_durable_fields_v21(
        durable_root,
        high_water,
        label="barrier seed",
    )
    return _domain_hash_v21(
        BARRIER_ADVANCE_SEED_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "prior_advance_head_root": prior_head,
            "namespace": checked_namespace,
            "incarnation": checked_incarnation,
            "durable_root": checked_durable_root,
            "high_water": checked_high_water,
        },
    )


def ledger_advance_seed_root_v21(
    *,
    base_state_hash: str,
    durable_root: str,
    role_counts: tuple[tuple[CapsuleRoleV21, int], ...],
    last_fold_generation: int,
) -> str:
    """Commit the explicit durable anchor for the ledger seed cursor."""
    base_hash = _require_domain_digest_v21(
        base_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "ledger seed base_state_hash",
    )
    checked_durable_root = _require_domain_digest_v21(
        durable_root, _DURABLE_LEDGER_ROOT_DOMAIN, "ledger seed durable_root"
    )
    checked_counts = _validate_role_counts_v21(
        role_counts,
        label="ledger seed role_counts",
        maximum_count=MAX_EVIDENCED_LOSS_UNITS_V21,
    )
    checked_generation = _require_nonnegative_int_v21(
        last_fold_generation, "ledger seed last_fold_generation"
    )
    return _domain_hash_v21(
        LEDGER_ADVANCE_SEED_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "durable_root": checked_durable_root,
            "role_counts": checked_counts,
            "last_fold_generation": checked_generation,
        },
    )


@dataclass(frozen=True, slots=True)
class AdvanceChainHeadV21:
    state_hash: str
    generation: int
    barrier_cursors: tuple[BarrierAdvanceCursorV21, ...]
    ledger_cursor: LedgerAdvanceCursorV21
    previous_head_root: str | None
    head_root: str

    def __post_init__(self) -> None:
        validate_advance_chain_head_v21(self)


def advance_chain_head_root_v21(
    *,
    state_hash: str,
    generation: int,
    barrier_cursors: tuple[BarrierAdvanceCursorV21, ...],
    ledger_cursor: LedgerAdvanceCursorV21,
    previous_head_root: str | None,
) -> str:
    """Commit the complete latest advance-chain state without a history list."""
    checked_state_hash = _require_domain_digest_v21(
        state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "advance head state_hash"
    )
    checked_generation = _require_nonnegative_int_v21(
        generation, "advance head generation"
    )
    checked_cursors = _validate_barrier_advance_cursors_v21(barrier_cursors)
    checked_ledger_cursor = _validate_ledger_advance_cursor_v21(ledger_cursor)
    checked_previous = (
        None
        if previous_head_root is None
        else _require_domain_digest_v21(
            previous_head_root,
            ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
            "advance head previous_head_root",
        )
    )
    return _domain_hash_v21(
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        {
            "state_hash": checked_state_hash,
            "generation": checked_generation,
            "barrier_cursors": checked_cursors,
            "ledger_cursor": checked_ledger_cursor,
            "previous_head_root": checked_previous,
        },
    )


def _validate_initial_advance_head_seeds_v21(head: AdvanceChainHeadV21) -> None:
    if head.previous_head_root is not None:
        return
    for cursor in head.barrier_cursors:
        if cursor.kind is not AdvanceCursorKindV21.SEED:
            raise ValueError("initial advance head requires barrier seed cursors")
        if cursor.durable_root is None or cursor.high_water is None:
            raise ValueError("initial barrier seed cursors require durable fields")
        expected_seed = barrier_advance_seed_root_v21(
            base_state_hash=head.state_hash,
            prior_advance_head_root=None,
            namespace=cursor.namespace,
            incarnation=cursor.incarnation,
            durable_root=cursor.durable_root,
            high_water=cursor.high_water,
        )
        if cursor.transient_root != expected_seed:
            raise ValueError("initial barrier cursor seed does not bind its fields")
    if head.ledger_cursor.kind is not AdvanceCursorKindV21.SEED:
        raise ValueError("initial advance head requires a ledger seed cursor")
    expected_ledger_seed = ledger_advance_seed_root_v21(
        base_state_hash=head.state_hash,
        durable_root=head.ledger_cursor.durable_root,
        role_counts=head.ledger_cursor.role_counts,
        last_fold_generation=head.ledger_cursor.generation,
    )
    if head.ledger_cursor.transient_root != expected_ledger_seed:
        raise ValueError("initial ledger cursor seed does not bind its fields")


def validate_advance_chain_head_v21(head: AdvanceChainHeadV21) -> None:
    if type(head) is not AdvanceChainHeadV21:
        raise TypeError("advance heads require nominal AdvanceChainHeadV21")
    claimed_root = _require_domain_digest_v21(
        head.head_root, ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21, "advance head head_root"
    )
    expected_root = advance_chain_head_root_v21(
        state_hash=head.state_hash,
        generation=head.generation,
        barrier_cursors=head.barrier_cursors,
        ledger_cursor=head.ledger_cursor,
        previous_head_root=head.previous_head_root,
    )
    if claimed_root != expected_root:
        raise ValueError("advance head root does not bind head fields")
    _validate_initial_advance_head_seeds_v21(head)


def seed_advance_chain_head_v21(
    base: EvidencedCapsuleStateV21,
    *,
    expected_state_hash: str,
) -> AdvanceChainHeadV21:
    """Derive the one trusted genesis head from a verified state wrapper."""
    validate_evidenced_capsule_state_v21(base)
    expected_hash = _require_domain_digest_v21(
        expected_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "expected_state_hash",
    )
    actual_hash = evidenced_state_hash_v21(base)
    if not hmac.compare_digest(actual_hash, expected_hash):
        raise EvidencedStateMismatchV21(
            "expected_state_hash does not name the evidenced wrapper"
        )
    barrier_cursors = tuple(
        BarrierAdvanceCursorV21(
            namespace=barrier.namespace,
            incarnation=barrier.incarnation,
            durable_root=barrier.cumulative_root,
            high_water=barrier.folded_through_high_water,
            kind=AdvanceCursorKindV21.SEED,
            transient_root=barrier_advance_seed_root_v21(
                base_state_hash=actual_hash,
                prior_advance_head_root=None,
                namespace=barrier.namespace,
                incarnation=barrier.incarnation,
                durable_root=barrier.cumulative_root,
                high_water=barrier.folded_through_high_water,
            ),
        )
        for barrier in sorted(
            base.state.fold_barriers,
            key=lambda row: (row.namespace, row.incarnation),
        )
    )
    ledger = base.state.loss_ledger
    ledger_cursor = LedgerAdvanceCursorV21(
        durable_root=ledger.cumulative_loss_root,
        role_counts=ledger.role_counts,
        generation=ledger.last_fold_generation,
        kind=AdvanceCursorKindV21.SEED,
        transient_root=ledger_advance_seed_root_v21(
            base_state_hash=actual_hash,
            durable_root=ledger.cumulative_loss_root,
            role_counts=ledger.role_counts,
            last_fold_generation=ledger.last_fold_generation,
        ),
    )
    generation = base.state.frame.generation
    return AdvanceChainHeadV21(
        state_hash=actual_hash,
        generation=generation,
        barrier_cursors=barrier_cursors,
        ledger_cursor=ledger_cursor,
        previous_head_root=None,
        head_root=advance_chain_head_root_v21(
            state_hash=actual_hash,
            generation=generation,
            barrier_cursors=barrier_cursors,
            ledger_cursor=ledger_cursor,
            previous_head_root=None,
        ),
    )


def next_advance_chain_head_v21(
    previous_head: AdvanceChainHeadV21,
    *,
    proposed_state_hash: str,
    target_generation: int,
    barrier_cursors: tuple[BarrierAdvanceCursorV21, ...],
    ledger_cursor: LedgerAdvanceCursorV21,
) -> AdvanceChainHeadV21:
    """Construct a complete successor head from the immediately prior head."""
    if type(previous_head) is not AdvanceChainHeadV21:
        raise TypeError("next advance head requires nominal AdvanceChainHeadV21")
    validate_advance_chain_head_v21(previous_head)
    checked_state_hash = _require_domain_digest_v21(
        proposed_state_hash,
        EVIDENCED_STATE_HASH_DOMAIN_V21,
        "next advance head proposed_state_hash",
    )
    checked_generation = _require_nonnegative_int_v21(
        target_generation, "next advance head target_generation"
    )
    if checked_generation < previous_head.generation:
        raise ValueError("next advance head generation must be monotonic")
    checked_cursors = _validate_barrier_advance_cursors_v21(barrier_cursors)
    checked_ledger_cursor = _validate_ledger_advance_cursor_v21(ledger_cursor)
    previous_by_identity = {
        (cursor.namespace, cursor.incarnation): cursor
        for cursor in previous_head.barrier_cursors
    }
    next_by_identity = {
        (cursor.namespace, cursor.incarnation): cursor for cursor in checked_cursors
    }
    missing = set(previous_by_identity) - set(next_by_identity)
    if missing:
        raise ValueError("next advance head must carry every prior barrier cursor")
    for identity, cursor in next_by_identity.items():
        prior_cursor = previous_by_identity.get(identity)
        if prior_cursor is not None:
            if cursor == prior_cursor:
                continue
            if cursor.kind is not AdvanceCursorKindV21.ADVANCE:
                raise ValueError("changed barrier cursors must be advance cursors")
            if cursor.durable_root is None or cursor.high_water is None:
                raise ValueError("advanced barrier cursors require durable fields")
            continue
        if cursor.kind is not AdvanceCursorKindV21.SEED:
            raise ValueError("new barrier cursors require an on-demand seed")
        if cursor.durable_root is not None or cursor.high_water is not None:
            raise ValueError("new barrier seed cursors require null durable fields")
        expected_seed = barrier_advance_seed_root_v21(
            base_state_hash=checked_state_hash,
            prior_advance_head_root=previous_head.head_root,
            namespace=cursor.namespace,
            incarnation=cursor.incarnation,
            durable_root=None,
            high_water=None,
        )
        if cursor.transient_root != expected_seed:
            raise ValueError("new barrier seed cursor does not bind the prior head")
    if (
        checked_ledger_cursor != previous_head.ledger_cursor
        and checked_ledger_cursor.kind is not AdvanceCursorKindV21.ADVANCE
    ):
        raise ValueError("changed ledger cursor must be an advance cursor")
    return AdvanceChainHeadV21(
        state_hash=checked_state_hash,
        generation=checked_generation,
        barrier_cursors=checked_cursors,
        ledger_cursor=checked_ledger_cursor,
        previous_head_root=previous_head.head_root,
        head_root=advance_chain_head_root_v21(
            state_hash=checked_state_hash,
            generation=checked_generation,
            barrier_cursors=checked_cursors,
            ledger_cursor=checked_ledger_cursor,
            previous_head_root=previous_head.head_root,
        ),
    )


def barrier_advance_root_v21(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    record_decision_root: str,
    segment_decision_root: str,
    prior_advance_head_root: str,
    prior_cursor: BarrierAdvanceCursorV21,
    namespace: str,
    incarnation: int,
    previous_high_water: int,
    new_high_water: int,
    generation: int,
    released_sources: tuple[ReleasedSourceV21, ...],
) -> str:
    """Commit a monotonic barrier advance without including its claimed root."""
    base_hash = _require_domain_digest_v21(
        base_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "barrier base_state_hash"
    )
    envelope_root = _require_domain_digest_v21(
        source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "barrier source_envelope_root",
    )
    checked_policy_root = _require_domain_digest_v21(
        policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "barrier policy_root"
    )
    record_root = _require_domain_digest_v21(
        record_decision_root,
        RECORD_DECISION_ROOT_DOMAIN_V21,
        "barrier record_decision_root",
    )
    segment_root = _require_domain_digest_v21(
        segment_decision_root,
        SEGMENT_DECISION_ROOT_DOMAIN_V21,
        "barrier segment_decision_root",
    )
    checked_namespace = _require_domain_digest_v21(
        namespace, _NAMESPACE_ID_DOMAIN, "barrier namespace"
    )
    checked_incarnation = _require_nonnegative_int_v21(
        incarnation, "barrier incarnation"
    )
    prior_head = _require_domain_digest_v21(
        prior_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "barrier prior_advance_head_root",
    )
    checked_cursor = _validate_barrier_advance_cursor_v21(prior_cursor)
    if (
        checked_cursor.namespace != checked_namespace
        or checked_cursor.incarnation != checked_incarnation
    ):
        raise ValueError("barrier prior_cursor identity must match the advance")
    previous_water = _require_nonnegative_int_v21(
        previous_high_water, "barrier previous_high_water"
    )
    if (
        checked_cursor.high_water is not None
        and checked_cursor.high_water != previous_water
    ):
        raise ValueError("barrier previous_high_water must match prior_cursor")
    if checked_cursor.high_water is None and previous_water != 0:
        raise ValueError("null barrier prior_cursor requires zero previous_high_water")
    next_water = _require_nonnegative_int_v21(new_high_water, "barrier new_high_water")
    if next_water < previous_water:
        raise ValueError("barrier new_high_water must be monotonic")
    checked_generation = _require_nonnegative_int_v21(generation, "barrier generation")
    released = _validate_released_sources_v21(released_sources)
    return _domain_hash_v21(
        BARRIER_ADVANCE_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_envelope_root": envelope_root,
            "policy_root": checked_policy_root,
            "record_decision_root": record_root,
            "segment_decision_root": segment_root,
            "prior_advance_head_root": prior_head,
            "prior_cursor": checked_cursor,
            "namespace": checked_namespace,
            "incarnation": checked_incarnation,
            "previous_high_water": previous_water,
            "new_high_water": next_water,
            "generation": checked_generation,
            "released_sources": released,
        },
    )


def barrier_durable_projection_root_v21(
    *,
    previous_durable_root: str | None,
    barrier_advance_root: str,
    namespace: str,
    incarnation: int,
    new_high_water: int,
    generation: int,
) -> str:
    """Project a transient barrier advance into the durable barrier domain."""
    checked_previous = (
        None
        if previous_durable_root is None
        else _require_domain_digest_v21(
            previous_durable_root,
            _DURABLE_BARRIER_ROOT_DOMAIN,
            "barrier projection previous_durable_root",
        )
    )
    checked_advance_root = _require_domain_digest_v21(
        barrier_advance_root,
        BARRIER_ADVANCE_ROOT_DOMAIN_V21,
        "barrier projection barrier_advance_root",
    )
    checked_namespace = _require_domain_digest_v21(
        namespace, _NAMESPACE_ID_DOMAIN, "barrier projection namespace"
    )
    checked_incarnation = _require_nonnegative_int_v21(
        incarnation, "barrier projection incarnation"
    )
    checked_high_water = _require_nonnegative_int_v21(
        new_high_water, "barrier projection new_high_water"
    )
    checked_generation = _require_nonnegative_int_v21(
        generation, "barrier projection generation"
    )
    return _domain_hash_v21(
        "v21-barrier-root-s3",
        {
            "projection": "evidenced-advance-v1",
            "previous_durable_root": checked_previous,
            "barrier_advance_root": checked_advance_root,
            "namespace": checked_namespace,
            "incarnation": checked_incarnation,
            "new_high_water": checked_high_water,
            "generation": checked_generation,
        },
    )


@dataclass(frozen=True, slots=True)
class BarrierAdvanceV21:
    base_state_hash: str
    source_envelope_root: str
    policy_root: str
    record_decision_root: str
    segment_decision_root: str
    prior_advance_head_root: str
    prior_cursor: BarrierAdvanceCursorV21
    namespace: str
    incarnation: int
    previous_high_water: int
    new_high_water: int
    generation: int
    released_sources: tuple[ReleasedSourceV21, ...]
    new_durable_root: str
    new_root: str

    def __post_init__(self) -> None:
        validate_barrier_advance_v21(self)


def validate_barrier_advance_v21(advance: BarrierAdvanceV21) -> None:
    if type(advance) is not BarrierAdvanceV21:
        raise TypeError("barrier advances require nominal BarrierAdvanceV21")
    claimed_root = _require_domain_digest_v21(
        advance.new_root, BARRIER_ADVANCE_ROOT_DOMAIN_V21, "barrier new_root"
    )
    claimed_durable_root = _require_domain_digest_v21(
        advance.new_durable_root,
        _DURABLE_BARRIER_ROOT_DOMAIN,
        "barrier new_durable_root",
    )
    expected_root = barrier_advance_root_v21(
        base_state_hash=advance.base_state_hash,
        source_envelope_root=advance.source_envelope_root,
        policy_root=advance.policy_root,
        record_decision_root=advance.record_decision_root,
        segment_decision_root=advance.segment_decision_root,
        prior_advance_head_root=advance.prior_advance_head_root,
        prior_cursor=advance.prior_cursor,
        namespace=advance.namespace,
        incarnation=advance.incarnation,
        previous_high_water=advance.previous_high_water,
        new_high_water=advance.new_high_water,
        generation=advance.generation,
        released_sources=advance.released_sources,
    )
    if claimed_root != expected_root:
        raise ValueError("barrier new_root does not bind advance fields")
    expected_durable_root = barrier_durable_projection_root_v21(
        previous_durable_root=advance.prior_cursor.durable_root,
        barrier_advance_root=expected_root,
        namespace=advance.namespace,
        incarnation=advance.incarnation,
        new_high_water=advance.new_high_water,
        generation=advance.generation,
    )
    if claimed_durable_root != expected_durable_root:
        raise ValueError("barrier new_durable_root does not bind the projection")


def _validate_barrier_roots_v21(value: object) -> tuple[str, ...]:
    return _require_canonical_digest_tuple_v21(
        value,
        domain=BARRIER_ADVANCE_ROOT_DOMAIN_V21,
        label="barrier_roots",
        maximum=MAX_EVIDENCED_ROOT_ROWS_V21,
    )


def loss_ledger_advance_root_v21(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    record_decision_root: str,
    segment_decision_root: str,
    barrier_roots: tuple[str, ...],
    prior_advance_head_root: str,
    prior_cursor: LedgerAdvanceCursorV21,
    previous_role_counts: tuple[tuple[CapsuleRoleV21, int], ...],
    delta_role_counts: tuple[tuple[CapsuleRoleV21, int], ...],
    new_role_counts: tuple[tuple[CapsuleRoleV21, int], ...],
    previous_generation: int,
    new_generation: int,
    loss_units_delta: int,
) -> str:
    """Commit a ledger advance without including its claimed new root."""
    base_hash = _require_domain_digest_v21(
        base_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "ledger base_state_hash"
    )
    envelope_root = _require_domain_digest_v21(
        source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "ledger source_envelope_root",
    )
    checked_policy_root = _require_domain_digest_v21(
        policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "ledger policy_root"
    )
    record_root = _require_domain_digest_v21(
        record_decision_root,
        RECORD_DECISION_ROOT_DOMAIN_V21,
        "ledger record_decision_root",
    )
    segment_root = _require_domain_digest_v21(
        segment_decision_root,
        SEGMENT_DECISION_ROOT_DOMAIN_V21,
        "ledger segment_decision_root",
    )
    barriers = _validate_barrier_roots_v21(barrier_roots)
    prior_head = _require_domain_digest_v21(
        prior_advance_head_root,
        ADVANCE_CHAIN_HEAD_ROOT_DOMAIN_V21,
        "ledger prior_advance_head_root",
    )
    checked_cursor = _validate_ledger_advance_cursor_v21(prior_cursor)
    prior_counts = _validate_role_counts_v21(
        previous_role_counts,
        label="ledger previous_role_counts",
        maximum_count=MAX_EVIDENCED_LOSS_UNITS_V21,
    )
    delta_counts = _validate_role_counts_v21(
        delta_role_counts,
        label="ledger delta_role_counts",
        maximum_count=MAX_EVIDENCED_LOSS_UNITS_V21,
    )
    next_counts = _validate_role_counts_v21(
        new_role_counts,
        label="ledger new_role_counts",
        maximum_count=MAX_EVIDENCED_LOSS_UNITS_V21,
    )
    previous_by_role = dict(prior_counts)
    delta_by_role = dict(delta_counts)
    next_by_role = dict(next_counts)
    for role in set(previous_by_role) | set(delta_by_role) | set(next_by_role):
        if next_by_role.get(role, 0) != previous_by_role.get(
            role, 0
        ) + delta_by_role.get(role, 0):
            raise ValueError("ledger new_role_counts must equal previous plus delta")
    prior_generation = _require_nonnegative_int_v21(
        previous_generation, "ledger previous_generation"
    )
    if checked_cursor.role_counts != prior_counts:
        raise ValueError("ledger previous_role_counts must match prior_cursor")
    if checked_cursor.generation != prior_generation:
        raise ValueError("ledger previous_generation must match prior_cursor")
    next_generation = _require_nonnegative_int_v21(
        new_generation, "ledger new_generation"
    )
    if next_generation < prior_generation:
        raise ValueError("ledger new_generation must be monotonic")
    units = _require_nonnegative_int_v21(loss_units_delta, "ledger loss_units_delta")
    if units > MAX_EVIDENCED_LOSS_UNITS_V21:
        raise ValueError("ledger loss_units_delta cap exceeded")
    return _domain_hash_v21(
        LEDGER_ADVANCE_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_envelope_root": envelope_root,
            "policy_root": checked_policy_root,
            "record_decision_root": record_root,
            "segment_decision_root": segment_root,
            "barrier_roots": barriers,
            "prior_advance_head_root": prior_head,
            "prior_cursor": checked_cursor,
            "previous_role_counts": prior_counts,
            "delta_role_counts": delta_counts,
            "new_role_counts": next_counts,
            "previous_generation": prior_generation,
            "new_generation": next_generation,
            "loss_units_delta": units,
        },
    )


def ledger_durable_projection_root_v21(
    *,
    previous_durable_root: str,
    ledger_advance_root: str,
    new_role_counts: tuple[tuple[CapsuleRoleV21, int], ...],
    new_generation: int,
) -> str:
    """Project a transient ledger advance into the durable ledger domain."""
    checked_previous = _require_domain_digest_v21(
        previous_durable_root,
        _DURABLE_LEDGER_ROOT_DOMAIN,
        "ledger projection previous_durable_root",
    )
    checked_advance_root = _require_domain_digest_v21(
        ledger_advance_root,
        LEDGER_ADVANCE_ROOT_DOMAIN_V21,
        "ledger projection ledger_advance_root",
    )
    checked_counts = _validate_role_counts_v21(
        new_role_counts,
        label="ledger projection new_role_counts",
        maximum_count=MAX_EVIDENCED_LOSS_UNITS_V21,
    )
    checked_generation = _require_nonnegative_int_v21(
        new_generation, "ledger projection new_generation"
    )
    return _domain_hash_v21(
        "v21-ledger-root-s3",
        {
            "projection": "evidenced-advance-v1",
            "previous_durable_root": checked_previous,
            "ledger_advance_root": checked_advance_root,
            "new_role_counts": checked_counts,
            "new_generation": checked_generation,
        },
    )


@dataclass(frozen=True, slots=True)
class LossLedgerAdvanceV21:
    base_state_hash: str
    source_envelope_root: str
    policy_root: str
    record_decision_root: str
    segment_decision_root: str
    barrier_roots: tuple[str, ...]
    prior_advance_head_root: str
    prior_cursor: LedgerAdvanceCursorV21
    previous_role_counts: tuple[tuple[CapsuleRoleV21, int], ...]
    delta_role_counts: tuple[tuple[CapsuleRoleV21, int], ...]
    new_role_counts: tuple[tuple[CapsuleRoleV21, int], ...]
    previous_generation: int
    new_generation: int
    loss_units_delta: int
    new_durable_root: str
    new_root: str

    def __post_init__(self) -> None:
        validate_loss_ledger_advance_v21(self)


def validate_loss_ledger_advance_v21(advance: LossLedgerAdvanceV21) -> None:
    if type(advance) is not LossLedgerAdvanceV21:
        raise TypeError("ledger advances require nominal LossLedgerAdvanceV21")
    claimed_root = _require_domain_digest_v21(
        advance.new_root, LEDGER_ADVANCE_ROOT_DOMAIN_V21, "ledger new_root"
    )
    claimed_durable_root = _require_domain_digest_v21(
        advance.new_durable_root,
        _DURABLE_LEDGER_ROOT_DOMAIN,
        "ledger new_durable_root",
    )
    expected_root = loss_ledger_advance_root_v21(
        base_state_hash=advance.base_state_hash,
        source_envelope_root=advance.source_envelope_root,
        policy_root=advance.policy_root,
        record_decision_root=advance.record_decision_root,
        segment_decision_root=advance.segment_decision_root,
        barrier_roots=advance.barrier_roots,
        prior_advance_head_root=advance.prior_advance_head_root,
        prior_cursor=advance.prior_cursor,
        previous_role_counts=advance.previous_role_counts,
        delta_role_counts=advance.delta_role_counts,
        new_role_counts=advance.new_role_counts,
        previous_generation=advance.previous_generation,
        new_generation=advance.new_generation,
        loss_units_delta=advance.loss_units_delta,
    )
    if claimed_root != expected_root:
        raise ValueError("ledger new_root does not bind advance fields")
    expected_durable_root = ledger_durable_projection_root_v21(
        previous_durable_root=advance.prior_cursor.durable_root,
        ledger_advance_root=expected_root,
        new_role_counts=advance.new_role_counts,
        new_generation=advance.new_generation,
    )
    if claimed_durable_root != expected_durable_root:
        raise ValueError("ledger new_durable_root does not bind the projection")


def evidenced_transition_root_v21(
    *,
    base_state_hash: str,
    source_envelope_root: str,
    policy_root: str,
    matrix_root: str,
    record_decision_root: str,
    segment_decision_root: str,
    authorization_roots: tuple[str, ...],
    barrier_roots: tuple[str, ...],
    ledger_root: str,
    proposed_hash: str,
    target_generation: int,
    target_high_water: int,
) -> str:
    """Commit the complete future transition evidence without executing one."""
    base_hash = _require_domain_digest_v21(
        base_state_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "transition base_state_hash"
    )
    envelope_root = _require_domain_digest_v21(
        source_envelope_root,
        SOURCE_ENVELOPE_ROOT_DOMAIN_V21,
        "transition source_envelope_root",
    )
    checked_policy_root = _require_domain_digest_v21(
        policy_root, EVIDENCED_POLICY_ROOT_DOMAIN_V21, "transition policy_root"
    )
    checked_matrix_root = _require_domain_digest_v21(
        matrix_root, MATRIX_ROOT_DOMAIN_V21, "transition matrix_root"
    )
    record_root = _require_domain_digest_v21(
        record_decision_root,
        RECORD_DECISION_ROOT_DOMAIN_V21,
        "transition record_decision_root",
    )
    segment_root = _require_domain_digest_v21(
        segment_decision_root,
        SEGMENT_DECISION_ROOT_DOMAIN_V21,
        "transition segment_decision_root",
    )
    authorizations = _require_canonical_digest_tuple_v21(
        authorization_roots,
        domain=FIRST_FOLD_AUTHORIZATION_ROOT_DOMAIN_V21,
        label="transition authorization_roots",
        maximum=MAX_EVIDENCED_ROOT_ROWS_V21,
    )
    barriers = _validate_barrier_roots_v21(barrier_roots)
    checked_ledger_root = _require_domain_digest_v21(
        ledger_root, LEDGER_ADVANCE_ROOT_DOMAIN_V21, "transition ledger_root"
    )
    checked_proposed_hash = _require_domain_digest_v21(
        proposed_hash, EVIDENCED_STATE_HASH_DOMAIN_V21, "transition proposed_hash"
    )
    generation = _require_nonnegative_int_v21(
        target_generation, "transition target_generation"
    )
    high_water = _require_nonnegative_int_v21(
        target_high_water, "transition target_high_water"
    )
    return _domain_hash_v21(
        EVIDENCED_TRANSITION_ROOT_DOMAIN_V21,
        {
            "base_state_hash": base_hash,
            "source_envelope_root": envelope_root,
            "policy_root": checked_policy_root,
            "matrix_root": checked_matrix_root,
            "record_decision_root": record_root,
            "segment_decision_root": segment_root,
            "authorization_roots": authorizations,
            "barrier_roots": barriers,
            "ledger_root": checked_ledger_root,
            "proposed_hash": checked_proposed_hash,
            "target_generation": generation,
            "target_high_water": high_water,
        },
    )
