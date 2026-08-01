"""Source-bound query projection from one frozen CRM v2 state view."""

from __future__ import annotations

from crm_experiment.canonical import utf8_bytes
from crm_experiment.codec_v2 import CodecDecodeErrorV2, decode_frozen_state_v2
from crm_experiment.contracts import QuerySpec
from crm_experiment.contracts_v2 import (
    FrozenStateViewV2,
    ProjectedSourceV2,
    ProjectionResultV2,
)

UNKNOWN_V2 = "[CONTEXT_INSUFFICIENT]"


def _unknown(query_id: str, reason: str) -> ProjectionResultV2:
    return ProjectionResultV2(
        query_id=query_id,
        records=(),
        text=UNKNOWN_V2,
        byte_cost=utf8_bytes(UNKNOWN_V2),
        supported=False,
        reason=reason,
    )


def project_query_v2(
    view: FrozenStateViewV2,
    query: QuerySpec,
    byte_budget: int,
) -> ProjectionResultV2:
    """Project only decoded matching source records within the injection budget."""
    if byte_budget <= 0:
        raise ValueError("projection byte_budget must be positive")
    try:
        records = decode_frozen_state_v2(view.state)
    except CodecDecodeErrorV2:
        return _unknown(query.query_id, "malformed_frozen_state")

    matching = [
        record
        for record in records
        if record.atom.role is query.role and query.semantic_key in record.active_keys
    ]
    matching.sort(key=lambda record: (-record.atom.revision, record.atom.source_id))
    if not matching:
        return _unknown(query.query_id, "no_matching_source")

    selected: list[ProjectedSourceV2] = []
    lines: list[str] = []
    used = 0
    for record in matching:
        line_bytes = utf8_bytes(record.atom.text) + (1 if lines else 0)
        if used + line_bytes <= byte_budget:
            selected.append(ProjectedSourceV2.from_record(record))
            lines.append(record.atom.text)
            used += line_bytes
    if not selected:
        return _unknown(query.query_id, "injection_budget")

    return ProjectionResultV2(
        query_id=query.query_id,
        records=tuple(selected),
        text="\n".join(lines),
        byte_cost=used,
        supported=True,
        reason="supported",
    )
