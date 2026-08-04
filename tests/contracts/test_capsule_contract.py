from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_capsule_schema() -> dict[str, Any]:
    return json.loads((ROOT / "specs" / "capsule.schema.json").read_text("utf-8"))


def test_capsule_is_closed_and_uses_full_session_key() -> None:
    schema = load_capsule_schema()

    assert schema["additionalProperties"] is False
    assert "session_key" in schema["required"]
    assert "session_id" not in schema["properties"]
    assert schema["properties"]["session_key"] == {"$ref": "#/$defs/sessionKey"}
    assert schema["$defs"]["sessionKey"]["additionalProperties"] is False


def test_capsule_materializes_master_prompt_semantics() -> None:
    schema = load_capsule_schema()
    required = set(schema["required"])
    semantic_fields = {
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
    }

    assert semantic_fields <= required
    assert schema["properties"]["narrative_summary"]["minLength"] == 1
    assert schema["properties"]["source_event_ids"]["minItems"] == 1


def test_all_capsule_objects_are_closed() -> None:
    schema = load_capsule_schema()
    object_defs = {
        name: value for name, value in schema["$defs"].items() if value.get("type") == "object"
    }

    assert object_defs
    assert all(value.get("additionalProperties") is False for value in object_defs.values())


def test_capsule_has_version_time_and_quality() -> None:
    schema = load_capsule_schema()
    required = set(schema["required"])

    assert {"schema_version", "created_at", "quality"} <= required
    quality = schema["$defs"]["quality"]
    assert set(quality["required"]) == {
        "mechanical_passed",
        "source_coverage",
        "anchor_recall",
        "unsupported_critical_claims",
        "coverage_gap",
    }
