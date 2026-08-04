# AstrContinuum v1 Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the v1 Capsule and persistence contracts so later runtime code cannot collapse AstrContinuum into a narrative summarizer.

**Architecture:** Wave 1 is contract-only. It resolves the Master Prompt versus starter-Schema conflict, gives every Capsule a full SessionKey and structured semantic fields, and makes Capsules immutable members of atomically published Snapshots. Production Hooks, SQLite code, and release metadata remain unchanged.

**Tech Stack:** Python 3.10+, pytest, JSON Schema Draft 2020-12, Markdown ADRs, 2718lab strict-index workflow.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `tests/contracts/test_capsule_contract.py` | Executable checks for closed Capsule identity, structure, provenance, and quality fields |
| `tests/contracts/test_persistence_contract.py` | Executable checks that prose freezes Capsule tables and atomic publication |
| `specs/capsule.schema.json` | Authoritative closed v1 Capsule wire envelope |
| `docs/ADR-007-V1-CAPSULE-PERSISTENCE.md` | Conflict decision and immutable Capsule/Snapshot membership contract |
| `docs/CONTEXT_MODEL.md` | Human-readable v1 Capsule field and resolution model |
| `docs/DATABASE_SCHEMA.md` | `capsules`, `snapshot_capsules`, constraints, and publish savepoint |
| `docs/DATA_FLOW.md` | Compiler and publish flow including Capsule insertion and rollback |
| `docs/CONCURRENCY_STATE_MACHINE.md` | CAS conflict behavior including candidate Capsule rollback |
| `docs/TEST_MATRIX.md` | Required contract, FK, crash, and anti-summary tests |

## Task 1: Establish the Capsule Contract RED

**Files:**

- Create: `tests/contracts/test_capsule_contract.py`
- Read: `CODEX_MASTER_PROMPT.md`
- Read: `specs/capsule.schema.json`

- [ ] **Step 1: Write the failing contract test**

Create `tests/contracts/test_capsule_contract.py` with these exact assertions:

```python
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
        name: value
        for name, value in schema["$defs"].items()
        if value.get("type") == "object"
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
```

- [ ] **Step 2: Run the focused test and observe the correct RED**

Run:

```powershell
$env:TEMP='<task-temp-root>\temp'
$env:TMP=$env:TEMP
$env:TMPDIR=$env:TEMP
uv run --extra dev pytest tests/contracts/test_capsule_contract.py -q
```

Expected: failures specifically report the missing top-level `additionalProperties`,
`session_key`, structured semantic fields, or quality/version fields. A dependency or
syntax error is not an acceptable RED.

- [ ] **Step 3: Record the RED output as task evidence**

Store the complete output under:

```text
<task-temp-root>\evidence\V1-101\capsule-contract-red.txt
```

Do not edit the expected assertions after observing failures unless the test itself is
demonstrably inconsistent with the approved design contract.

- [ ] **Step 4: Commit only the RED test**

```powershell
git add tests/contracts/test_capsule_contract.py
git commit -m "test: define v1 capsule contract"
```

## Task 2: Replace the Capsule Wire Schema

**Files:**

- Modify: `specs/capsule.schema.json`
- Test: `tests/contracts/test_capsule_contract.py`

- [ ] **Step 1: Replace the top-level identity and required field set**

The top-level Schema must be a closed object with this exact required set:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "capsule_id",
    "schema_version",
    "level",
    "session_key",
    "covered_event_start",
    "covered_event_end",
    "source_event_ids",
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
    "narrative_summary",
    "token_cost",
    "quality",
    "created_at"
  ]
}
```

Use `session_key: {"$ref": "#/$defs/sessionKey"}`. Remove the old top-level
`session_id`. Every id is a non-empty string; every array that represents a set uses
`uniqueItems: true`; `source_event_ids` has `minItems: 1`.

- [ ] **Step 2: Define typed semantic records without narrative authority**

Use these closed definitions:

```json
{
  "claim": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "claim_id",
      "text",
      "status",
      "confidence",
      "source_event_ids"
    ]
  },
  "decision": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "decision_id",
      "text",
      "status",
      "confidence",
      "source_event_ids",
      "rationale",
      "alternatives",
      "supersedes",
      "rejected_because"
    ]
  },
  "entity": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "entity_id",
      "kind",
      "canonical_name",
      "aliases",
      "source_event_ids"
    ]
  },
  "anchor": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "anchor_id",
      "anchor_type",
      "exact_text",
      "source_event_ids",
      "status",
      "importance"
    ]
  },
  "dependency": {
    "type": "object",
    "additionalProperties": false,
    "required": ["dependency_id", "kind", "target_id", "source_event_ids"]
  }
}
```

Claims and decisions use exactly `active`, `superseded`, `retracted`, or `uncertain`.
Anchors use `active`, `superseded`, or `retracted`. `alternatives` and
`rejected_because` are required arrays/strings and may be empty; omission is invalid.

- [ ] **Step 3: Define full SessionKey and quality records**

The `sessionKey` definition contains exactly these required properties in this order:

```text
platform_instance_id, message_type, session_id, group_id,
user_id, conversation_id, persona_id
```

`group_id` and `persona_id` accept a non-empty string or null. The `quality` definition
is closed and requires:

```json
{
  "mechanical_passed": {"type": "boolean"},
  "source_coverage": {"type": "number", "minimum": 0, "maximum": 1},
  "anchor_recall": {"type": "number", "minimum": 0, "maximum": 1},
  "unsupported_critical_claims": {"type": "integer", "minimum": 0},
  "coverage_gap": {"type": "integer", "minimum": 0}
}
```

- [ ] **Step 4: Parse the Schema and run the focused test GREEN**

Run:

```powershell
uv run --extra dev python -c "import json, pathlib; json.loads(pathlib.Path('specs/capsule.schema.json').read_text('utf-8')); print('capsule schema: valid json')"
uv run --extra dev pytest tests/contracts/test_capsule_contract.py -q
```

Expected:

```text
capsule schema: valid json
4 passed
```

- [ ] **Step 5: Commit the wire contract**

```powershell
git add specs/capsule.schema.json tests/contracts/test_capsule_contract.py
git commit -m "feat: freeze structured capsule schema"
```

## Task 3: Establish the Persistence Contract RED

**Files:**

- Create: `tests/contracts/test_persistence_contract.py`
- Read: `docs/DATABASE_SCHEMA.md`
- Read: `docs/DATA_FLOW.md`
- Read: `docs/CONCURRENCY_STATE_MACHINE.md`
- Read: `docs/TEST_MATRIX.md`

- [ ] **Step 1: Write the failing documentation contract test**

```python
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_doc(name: str) -> str:
    return (ROOT / "docs" / name).read_text("utf-8")


def test_database_materializes_capsules_and_ordered_membership() -> None:
    database = read_doc("DATABASE_SCHEMA.md")

    assert "## `capsules`" in database
    assert "## `snapshot_capsules`" in database
    assert "PRIMARY KEY (`snapshot_id`, `ordinal`)" in database
    assert "UNIQUE (`snapshot_id`, `capsule_id`)" in database


def test_publish_rolls_back_all_candidate_content_on_cas_loss() -> None:
    corpus = "\n".join(
        [
            read_doc("ADR-007-V1-CAPSULE-PERSISTENCE.md"),
            read_doc("DATA_FLOW.md"),
            read_doc("CONCURRENCY_STATE_MACHINE.md"),
        ]
    )

    for required in (
        "capsules",
        "snapshot_capsules",
        "same savepoint",
        "roll back",
        "SUPERSEDED",
    ):
        assert required in corpus


def test_test_matrix_requires_capsule_integrity_and_crash_checks() -> None:
    matrix = read_doc("TEST_MATRIX.md")

    assert "Capsule closed envelope" in matrix
    assert "Capsule membership integrity" in matrix
    assert "Candidate Capsule rollback" in matrix
    assert "Narrative-only regression" in matrix
```

- [ ] **Step 2: Run and observe the correct RED**

```powershell
uv run --extra dev pytest tests/contracts/test_persistence_contract.py -q
```

Expected: failures identify the missing ADR, tables, savepoint language, and test matrix
rows. Record the output as
`<task-temp-root>\evidence\V1-101\persistence-contract-red.txt`.

- [ ] **Step 3: Commit only the RED test**

```powershell
git add tests/contracts/test_persistence_contract.py
git commit -m "test: define capsule persistence contract"
```

## Task 4: Synchronize ADR, Database, Flow, State, and Tests

**Files:**

- Create: `docs/ADR-007-V1-CAPSULE-PERSISTENCE.md`
- Modify: `docs/CONTEXT_MODEL.md`
- Modify: `docs/DATABASE_SCHEMA.md`
- Modify: `docs/DATA_FLOW.md`
- Modify: `docs/CONCURRENCY_STATE_MACHINE.md`
- Modify: `docs/TEST_MATRIX.md`
- Test: `tests/contracts/test_persistence_contract.py`

- [ ] **Step 1: Record the authority decision in ADR-007**

The ADR must state all of these decisions without optional language:

```text
Master Prompt semantic fields are materialized as separate arrays.
Every Capsule embeds the seven-component SessionKey.
Capsules are immutable structured records; narrative_summary is navigation only.
Snapshots refer to Capsules through ordered snapshot_capsules membership.
New candidate Capsules, membership, and Snapshot use the same publish savepoint.
CAS loss removes every candidate content row before SUPERSEDED is committed.
Sylanne memory is not a Capsule source and is never persisted by AstrContinuum.
```

- [ ] **Step 2: Add the two persistence tables**

`DATABASE_SCHEMA.md` must define the following logical SQL contract:

```sql
CREATE TABLE capsules (
    capsule_id TEXT PRIMARY KEY,
    session_key_hash TEXT NOT NULL,
    level TEXT NOT NULL,
    covered_event_start INTEGER NOT NULL CHECK (covered_event_start >= 1),
    covered_event_end INTEGER NOT NULL CHECK (covered_event_end >= covered_event_start),
    canonical_capsule_json TEXT NOT NULL,
    token_cost INTEGER NOT NULL CHECK (token_cost >= 0),
    source_coverage REAL NOT NULL CHECK (source_coverage BETWEEN 0 AND 1),
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash)
);

CREATE TABLE snapshot_capsules (
    snapshot_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    capsule_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ordinal),
    UNIQUE (snapshot_id, capsule_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (capsule_id) REFERENCES capsules(capsule_id)
);
```

The prose must additionally require same-session validation, source-event existence,
closed-Schema validation before insert, immutable rows, and exact-anchor membership
consistency.

- [ ] **Step 3: Update the publish flow and concurrency conflict branch**

The success path inserts candidate Capsules, membership, and committed Snapshot inside
one savepoint before pointer CAS. The conflict path rolls back to that savepoint, then
persists only the losing job's `SUPERSEDED` evidence and any higher-intent follow-up in
the outer transaction. A stale fence rejects the whole operation and does not mark the
job `SUPERSEDED`.

- [ ] **Step 4: Add exact test matrix rows**

Add rows named:

```text
Capsule closed envelope
Capsule membership integrity
Candidate Capsule rollback
Narrative-only regression
```

The rows must require durable-row assertions, cross-session rejection, dangling-source
rejection, crash injection after each candidate insert stage, and proof that structured
fields survive even when `narrative_summary` is empty or unhelpful in a test fixture.

- [ ] **Step 5: Run documentation contract tests GREEN**

```powershell
uv run --extra dev pytest tests/contracts/test_persistence_contract.py -q
```

Expected: `3 passed`.

- [ ] **Step 6: Commit the synchronized contract**

```powershell
git add docs/ADR-007-V1-CAPSULE-PERSISTENCE.md docs/CONTEXT_MODEL.md docs/DATABASE_SCHEMA.md docs/DATA_FLOW.md docs/CONCURRENCY_STATE_MACHINE.md docs/TEST_MATRIX.md tests/contracts/test_persistence_contract.py
git commit -m "docs: define atomic capsule persistence"
```

## Task 5: Verify Wave 1 Without Expanding Scope

**Files:**

- Verify only; no production code changes

- [ ] **Step 1: Parse every JSON Schema**

```powershell
uv run --extra dev python -c "import json, pathlib; files=sorted(pathlib.Path('specs').glob('*.json')); [json.loads(p.read_text('utf-8')) for p in files]; print(f'{len(files)} schemas: valid json')"
```

Expected: `5 schemas: valid json`.

- [ ] **Step 2: Run the focused contract suite**

```powershell
uv run --extra dev pytest tests/contracts -q
```

Expected: `7 passed`.

- [ ] **Step 3: Run all starter regression tests**

```powershell
uv run --extra dev pytest -q
```

Expected: all existing eight tests plus the seven new contract tests pass.

- [ ] **Step 4: Validate the work package**

```powershell
uv run --extra dev python "<2718lab-devkit>\skills\work-methodology\scripts\validate_work_package.py" docs/work-packages/astrcontinuum-v1
```

Expected: validator exits `0` with no missing section or line-budget error.

- [ ] **Step 5: Complete the strict-index output gate**

With the active `V1-101` lease, execute in order:

```text
project_index_sync(bind_as="output")
project_index_query(allow_miss_escape=false)
workflow_artifact_register(kind="verification", snapshot_id=<output snapshot>)
workflow_complete
```

The query receipt, output snapshot id, validation log hash, and changed-file list are the
completion evidence. An `INDEX_PARTIAL` result is acceptable only when required paths are
present and gaps are limited to known stdlib/dynamic-reference extraction limits.

- [ ] **Step 6: Commit verification projection updates only if they changed tracked files**

```powershell
git status --short
```

Do not change `main.py`, `astrcontinuum/**/*.py`, `metadata.yaml`, or the public version in
this wave.

## Wave 1 Exit Gate

Wave 1 is complete only when:

- both focused tests were observed failing for the intended missing contract before any
  corresponding contract file changed;
- all Capsule objects are closed and use full SessionKey identity;
- every Master Prompt semantic category is persisted structurally with source ids;
- Capsule, membership, and Snapshot candidate rows share one rollback boundary;
- the contract suite, old regression suite, work-package validator, and strict output
  index gate pass with fresh evidence;
- production runtime and release metadata remain untouched.

The next wave may then implement immutable domain envelopes and permanent validators
against this frozen contract.
