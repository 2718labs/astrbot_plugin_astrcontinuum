# Capsule Recomposition Matrix Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, experiment-only Capsule Recomposition Matrix pipeline that preserves a bounded continuity kernel, releases old generations, supports frozen query projection, and evaluates twelve generations across five byte budgets against Legacy Capsule and Recursive Summary.

**Architecture:** The experiment is a standalone Python package under `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3`. Immutable contracts feed a continuity-kernel selector, candidate/matrix builder, deterministic optimizer, loss-aware recomposition gate, frozen query projector, sealed synthetic protocol, fixed-denominator scorer, and paper-style reporting pipeline. `G:\AstrContinuum` is read-only and is used only for a production-validation diagnostic and regression tests.

**Tech Stack:** Python 3.13.14, uv/uv_build, dataclasses, standard library, NumPy 2.4.6+, Matplotlib 3.10.9+, pytest 9.0.3+, Ruff, Pyright, optional read-only imports from AstrContinuum. Exact dependency resolution is frozen in `uv.lock`.

---

## Execution invariants

Define these PowerShell variables at the start of every execution turn:

```powershell
$TaskRoot = 'D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3'
$RepoRoot = 'G:\AstrContinuum'
$TaskTemp = Join-Path $TaskRoot '.tmp'
New-Item -ItemType Directory -Path $TaskTemp -Force | Out-Null
$env:TEMP = $TaskTemp
$env:TMP = $TaskTemp
$env:TMPDIR = $TaskTemp
$env:UV_CACHE_DIR = Join-Path $TaskRoot '.uv-cache'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $TaskRoot '.venv'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$TaskRoot\src;$RepoRoot"
Set-Location $TaskRoot
```

Use `uv run` for the standalone experiment and figure generation. Use
`G:\AstrContinuum\.venv\Scripts\python.exe` only for repository regressions.
The normative tooling details are in `contracts/python-toolchain.md`.

Do not modify these protected production paths:

- `G:\AstrContinuum\astrcontinuum\domain\capsules.py`
- `G:\AstrContinuum\astrcontinuum\domain\validation.py`
- `G:\AstrContinuum\astrcontinuum\compaction\compiler.py`
- `G:\AstrContinuum\astrcontinuum\runtime\`
- `G:\AstrContinuum\astrcontinuum\storage\`
- `G:\AstrContinuum\astrcontinuum\adapters\`
- `G:\AstrContinuum\main.py`
- `G:\AstrContinuum\pyproject.toml`
- `G:\AstrContinuum\experiments\`
- `G:\AstrContinuum\tests\`

No source module may import `ollama`, `openai`, `requests`, `httpx`, or a network
client. Summary is an offline arm only.

## Locked file map

Create these focused files:

```text
D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\
  pyproject.toml
  uv.lock
  .python-version
  .pre-commit-config.yaml
  README.md
  .gitignore
  config\
    protocol-v1.json
  src\crm_experiment\
    __init__.py
    contracts.py
    canonical.py
    evidence.py
    kernel.py
    matrix.py
    optimizer.py
    recompose.py
    projection.py
    scoring.py
    baselines.py
    protocol.py
    runner.py
    reporting.py
    production_diag.py
    cli.py
  tests\
    test_contracts.py
    conftest.py
    test_evidence.py
    test_kernel.py
    test_matrix.py
    test_optimizer.py
    test_recompose.py
    test_projection.py
    test_scoring.py
    test_protocol.py
    test_runner.py
    test_reporting.py
    test_production_diag.py
  data\
    runtime\
    public-queries\
    sealed-gold\
  results\
  figures\
  evidence\
```

Responsibilities are strict:

- `contracts.py`: immutable types and enums only.
- `canonical.py`: canonical JSON, UTF-8 byte accounting, and semantic hashes.
- `evidence.py`: protected-source and artifact hash manifests.
- `kernel.py`: KernelSchemaV1 capacity derivation and protected selection.
- `matrix.py`: deterministic atomization, candidates, and \(F,A,R,H,D,b,e\).
- `optimizer.py`: dominated-candidate pruning, exact-small and greedy-large selection.
- `recompose.py`: loss-aware gate, outcomes, release inventory, pure recomposition.
- `projection.py`: query-specific projection from one frozen Capsule only.
- `scoring.py`: claim-v2, continuity/stale metrics, paired cluster bootstrap.
- `baselines.py`: Legacy Capsule and Recursive Summary offline arms.
- `protocol.py`: deterministic streams, sealed queries/gold, preregistered manifest.
- `runner.py`: state build, hash freeze, query reveal, pressure tests, records.
- `reporting.py`: aggregation outputs, five-panel academic figure, bounded report.
- `production_diag.py`: read-only production-validator diagnostic; never publishes.
- `cli.py`: explicit phase-separated commands.

## Locked public API

Later tasks must use these exact names:

```python
derive_kernel_ceiling(schema: KernelSchema) -> int
select_kernel(atoms: tuple[SemanticAtom, ...], schema: KernelSchema) -> KernelSelection
atomize(base: CapsuleState | None, delta: tuple[SemanticAtom, ...]) -> tuple[SemanticAtom, ...]
compose_candidates(atoms: tuple[SemanticAtom, ...]) -> tuple[Candidate, ...]
build_matrix(atoms: tuple[SemanticAtom, ...], candidates: tuple[Candidate, ...], budget: int) -> MatrixBundle
optimize(matrix: MatrixBundle, policy: LossPolicy) -> Selection
recompose_capsule(request: RecompositionRequest) -> RecompositionResult
project_query(state: CapsuleState, query: QuerySpec, byte_budget: int) -> ProjectionResult
score_projection(result: ProjectionResult, gold: QueryGold) -> ClaimV2Result
generate_protocol(config: ProtocolConfig) -> ProtocolBundle
run_protocol(config_path: Path, runtime_path: Path, query_path: Path, output_root: Path) -> Path
aggregate_run(records_path: Path, gold_path: Path, output_root: Path) -> Path
```

## Dependency waves

- Wave 1: Tasks 1–2, executed serially.
- Wave 2: Tasks 3–4, executed serially because `contracts.py` and matrix semantics are shared.
- Wave 3: Tasks 5–6, executed serially.
- Wave 4: Tasks 7–8, executed serially.
- Wave 5: Tasks 9–10, executed serially.
- Use one implementation writer at a time. Review after each task before dispatching the next.

### Task 1: Standalone project and protected-source checkpoint

**Files:**

- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\pyproject.toml`
- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\uv.lock`
- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.python-version`
- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.pre-commit-config.yaml`
- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\README.md`
- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\.gitignore`
- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\__init__.py`
- Create: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\src\crm_experiment\evidence.py`
- Test: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tests\test_evidence.py`
- Generate: `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\evidence\protected-source-hashes.json`

- [ ] **Step 1: Initialize an isolated Git history**

```powershell
git init
git switch -c codex/crm-experiment
```

Expected: a new repository rooted at `$TaskRoot`; `G:\AstrContinuum` remains a
separate dirty worktree and is not staged.

- [ ] **Step 2: Add the project configuration**

Calibrate the local `uv_build` requirement by running `uv init --build-backend uv`
inside `$TaskRoot\.tmp\uv-probe`, then create `pyproject.toml` with the emitted
backend requirement and this 2718lab-compliant configuration:

```toml
[project]
name = "crm-experiment"
version = "0.1.0"
description = "Deterministic Capsule Recomposition Matrix experiment."
readme = "README.md"
requires-python = ">=3.13"
dependencies = ["numpy>=2.4.6", "matplotlib>=3.10.9"]

[dependency-groups]
dev = ["pre-commit>=4.0", "pyright>=1.1", "pytest>=9.0.3", "ruff>=0.8"]

[build-system]
requires = ["uv_build>=0.11.28,<0.12.0"]
build-backend = "uv_build"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-p no:cacheprovider"

[tool.ruff]
line-length = 88
target-version = "py313"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "UP"]

[tool.pyright]
typeCheckingMode = "standard"
include = ["src", "tests"]
pythonVersion = "3.13"
```

Create `.gitignore` with:

```gitignore
.tmp/
.uv-cache/
.venv/
.pytest_cache/
.ruff_cache/
__pycache__/
*.pyc
pytest-of-pidan/
data/runtime/
data/public-queries/
data/sealed-gold/
results/
figures/*.png
figures/*.svg
evidence/verification*.txt
```

Create `.python-version` with `3.13`, configure pre-commit for Ruff check, Ruff
format, and Pyright from the 2718lab template, and add `uv sync` plus
`uv run pre-commit install` setup instructions to `README.md`.

Create `src/crm_experiment/__init__.py` with:

```python
"""Deterministic Capsule Recomposition Matrix experiment."""
```

- [ ] **Step 3: Write the failing protected-hash test**

```python
from pathlib import Path

from crm_experiment.evidence import collect_protected_hashes


def test_collect_protected_hashes_is_sorted_and_content_addressed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "b.py").write_text("b", encoding="utf-8")
    (root / "a.py").write_text("a", encoding="utf-8")

    rows = collect_protected_hashes(root, ("*.py",))

    assert [row["path"] for row in rows] == ["a.py", "b.py"]
    assert all(len(row["sha256"]) == 64 for row in rows)
```

- [ ] **Step 4: Run the test and verify the import fails**

```powershell
uv run pytest tests/test_evidence.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `crm_experiment.evidence`.

- [ ] **Step 5: Implement protected hashing**

Create `src/crm_experiment/evidence.py` with:

```python
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable


PROTECTED_PATTERNS = (
    "astrcontinuum/domain/capsules.py",
    "astrcontinuum/domain/validation.py",
    "astrcontinuum/compaction/compiler.py",
    "astrcontinuum/runtime/*.py",
    "astrcontinuum/storage/*.py",
    "astrcontinuum/adapters/*.py",
    "main.py",
    "pyproject.toml",
    "experiments/*.py",
    "experiments/*.json",
    "tests/**/*.py",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_protected_hashes(root: Path, patterns: Iterable[str]) -> list[dict[str, object]]:
    selected: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                selected[path.relative_to(root).as_posix()] = path
    return [
        {
            "path": relative,
            "bytes": selected[relative].stat().st_size,
            "sha256": sha256_file(selected[relative]),
        }
        for relative in sorted(selected)
    ]


def write_manifest(repo: Path, output: Path) -> None:
    payload = {
        "schema_version": 1,
        "repo": str(repo.resolve()),
        "files": collect_protected_hashes(repo, PROTECTED_PATTERNS),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_manifest(repo: Path, manifest: Path) -> bool:
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    current = collect_protected_hashes(repo, PROTECTED_PATTERNS)
    return recorded["files"] == current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        return 0 if verify_manifest(args.repo, args.manifest) else 1
    write_manifest(args.repo, args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run the unit test and freeze the repository checkpoint**

```powershell
uv lock --check
uv run pytest tests/test_evidence.py -q
uv run python -m crm_experiment.evidence --repo $RepoRoot --manifest "$TaskRoot\evidence\protected-source-hashes.json"
```

Expected: `1 passed`; the manifest exists and contains only hashes and sizes.

- [ ] **Step 7: Commit**

```powershell
git add .gitignore .python-version .pre-commit-config.yaml README.md pyproject.toml uv.lock src/crm_experiment/__init__.py src/crm_experiment/evidence.py tests/test_evidence.py evidence/protected-source-hashes.json product-brief.md index.md contracts tasks plans
git commit -m "chore: scaffold isolated CRM experiment"
```

### Task 2: Immutable contracts and canonical byte semantics

**Files:**

- Create: `src/crm_experiment/contracts.py`
- Create: `src/crm_experiment/canonical.py`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Write failing contract tests**

```python
from crm_experiment.canonical import semantic_hash, utf8_bytes
from crm_experiment.contracts import (
    AtomRole,
    AtomStatus,
    CapsuleState,
    SemanticAtom,
)


def atom(text: str = "目标") -> SemanticAtom:
    return SemanticAtom(
        atom_id="a1",
        semantic_keys=("goal",),
        role=AtomRole.ROOT_GOAL,
        text=text,
        status=AtomStatus.ACTIVE,
        revision=1,
        as_of=1,
        provenance=("e1",),
        exact=False,
        depends_on=(),
        weight=5.0,
        core_required=True,
        merge_depth=0,
        covered_atom_ids=("a1",),
    )


def test_utf8_bytes_counts_encoded_bytes() -> None:
    assert utf8_bytes("目标") == 6


def test_semantic_hash_ignores_generation_metadata() -> None:
    first = CapsuleState(1, 1, 8192, (atom(),), (), "theta0")
    second = CapsuleState(2, 9, 8192, (atom(),), (), "theta0")
    assert semantic_hash(first) == semantic_hash(second)
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
uv run pytest tests/test_contracts.py -q
```

Expected: FAIL because `contracts` and `canonical` do not exist.

- [ ] **Step 3: Implement the immutable contracts**

Create `contracts.py` with these exact public fields:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AtomRole(StrEnum):
    ROOT_GOAL = "root_goal"
    CURRENT_FOCUS = "current_focus"
    OPEN_LOOP = "open_loop"
    HARD_CONSTRAINT = "hard_constraint"
    DECISION = "decision"
    EXACT_ANCHOR = "exact_anchor"
    CONTEXT = "context"


class AtomStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class OutcomeState(StrEnum):
    NORMAL = "normal"
    KERNEL_ONLY = "kernel_only"
    ADMISSION_BLOCKED = "admission_blocked"


@dataclass(frozen=True, slots=True)
class SemanticAtom:
    atom_id: str
    semantic_keys: tuple[str, ...]
    role: AtomRole
    text: str
    status: AtomStatus
    revision: int
    as_of: int
    provenance: tuple[str, ...]
    exact: bool
    depends_on: tuple[str, ...]
    weight: float
    core_required: bool
    merge_depth: int
    covered_atom_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    role: AtomRole
    text: str
    covers: tuple[str, ...]
    semantic_keys: tuple[str, ...]
    byte_cost: int
    error_risk: float
    depends_on: tuple[str, ...]
    exact: bool
    core_required: bool
    merge_depth: int


@dataclass(frozen=True, slots=True)
class CapsuleState:
    generation: int
    high_water: int
    accepted_budget: int
    kernel: tuple[SemanticAtom, ...]
    body: tuple[SemanticAtom, ...]
    weight_version: str


@dataclass(frozen=True, slots=True)
class KernelSlot:
    role: AtomRole
    max_items: int
    max_text_bytes: int


@dataclass(frozen=True, slots=True)
class KernelSchema:
    slots: tuple[KernelSlot, ...]
    metadata_reserve_bytes: int


@dataclass(frozen=True, slots=True)
class KernelSelection:
    atoms: tuple[SemanticAtom, ...]
    byte_ceiling: int
    valid: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatrixBundle:
    atom_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    features: tuple[tuple[float, ...], ...]
    coverage: tuple[tuple[int, ...], ...]
    redundancy: tuple[tuple[float, ...], ...]
    conflicts: tuple[tuple[int, ...], ...]
    dependencies: tuple[tuple[int, ...], ...]
    costs: tuple[int, ...]
    risks: tuple[float, ...]
    weights: tuple[float, ...]
    budget: int


@dataclass(frozen=True, slots=True)
class LossPolicy:
    gamma: float
    rho: float
    risk_ceiling: float
    exact_threshold: int


@dataclass(frozen=True, slots=True)
class Selection:
    candidate_ids: tuple[str, ...]
    covered_atom_ids: tuple[str, ...]
    objective: float
    byte_cost: int
    solver: str


@dataclass(frozen=True, slots=True)
class LossReport:
    retained_atom_ids: tuple[str, ...]
    merged_atom_ids: tuple[str, ...]
    released_atom_ids: tuple[str, ...]
    omission_weight: float
    error_risk: float
    continuity_break: bool
    stale_current: bool


@dataclass(frozen=True, slots=True)
class RecompositionRequest:
    base_state: CapsuleState | None
    delta: tuple[SemanticAtom, ...]
    byte_budget: int
    kernel_schema: KernelSchema
    loss_policy: LossPolicy
    weight_version: str


@dataclass(frozen=True, slots=True)
class RecompositionResult:
    state: CapsuleState
    outcome: OutcomeState
    loss: LossReport
    matrix_hash: str
    semantic_hash: str
    strict_eligible: bool
    consumed_delta: bool
    stage_ns: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class QuerySpec:
    query_id: str
    role: AtomRole
    semantic_key: str


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    query_id: str
    text: str
    selected_atom_ids: tuple[str, ...]
    selected_covered_ids: tuple[str, ...]
    byte_cost: int
    supported: bool


@dataclass(frozen=True, slots=True)
class QueryGold:
    query_id: str
    required_atom_ids: tuple[str, ...]
    forbidden_atom_ids: tuple[str, ...]
    required_text: tuple[str, ...]
    forbidden_text: tuple[str, ...]
    core_query: bool


@dataclass(frozen=True, slots=True)
class ClaimV2Result:
    query_id: str
    passed: bool
    omission: bool
    stale_current: bool
    missing: tuple[str, ...]
    contradictions: tuple[str, ...]
```

- [ ] **Step 4: Implement canonical serialization**

Create `canonical.py` with:

```python
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from .contracts import CapsuleState


def utf8_bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def canonical_value(value: Any) -> Any:
    if is_dataclass(value):
        return canonical_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def semantic_hash(state: CapsuleState) -> str:
    payload = {
        "kernel": state.kernel,
        "body": state.body,
        "weight_version": state.weight_version,
    }
    return sha256_text(canonical_json(payload))
```

- [ ] **Step 5: Run tests, lint, and type check**

```powershell
uv run pytest tests/test_contracts.py -q
uv run ruff check src/crm_experiment/contracts.py src/crm_experiment/canonical.py tests/test_contracts.py
uv run pyright src/crm_experiment/contracts.py src/crm_experiment/canonical.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```powershell
git add src/crm_experiment/contracts.py src/crm_experiment/canonical.py tests/test_contracts.py
git commit -m "feat: define deterministic CRM contracts"
```

### Task 3: Continuity kernel and matrix construction

**Files:**

- Create: `src/crm_experiment/kernel.py`
- Create: `src/crm_experiment/matrix.py`
- Create: `tests/conftest.py`
- Test: `tests/test_kernel.py`
- Test: `tests/test_matrix.py`

- [ ] **Step 1: Write failing kernel tests**

```python
from crm_experiment.contracts import AtomRole, KernelSchema, KernelSlot
from crm_experiment.kernel import default_kernel_schema, derive_kernel_ceiling


def test_default_kernel_ceiling_is_frozen() -> None:
    schema = default_kernel_schema()
    assert derive_kernel_ceiling(schema) == 4608


def test_custom_kernel_ceiling_uses_utf8_escape_reserve() -> None:
    schema = KernelSchema(
        slots=(KernelSlot(AtomRole.ROOT_GOAL, 1, 100),),
        metadata_reserve_bytes=200,
    )
    assert derive_kernel_ceiling(schema) == 400
```

- [ ] **Step 2: Run the kernel tests and verify failure**

```powershell
uv run pytest tests/test_kernel.py -q
```

Expected: FAIL because `kernel.py` does not exist.

- [ ] **Step 3: Implement the frozen KernelSchemaV1**

```python
from __future__ import annotations

from collections import defaultdict

from .canonical import utf8_bytes
from .contracts import AtomRole, AtomStatus, KernelSchema, KernelSelection, KernelSlot, SemanticAtom


def default_kernel_schema() -> KernelSchema:
    return KernelSchema(
        slots=(
            KernelSlot(AtomRole.ROOT_GOAL, 1, 192),
            KernelSlot(AtomRole.CURRENT_FOCUS, 1, 192),
            KernelSlot(AtomRole.OPEN_LOOP, 1, 192),
            KernelSlot(AtomRole.HARD_CONSTRAINT, 4, 128),
            KernelSlot(AtomRole.DECISION, 2, 160),
            KernelSlot(AtomRole.EXACT_ANCHOR, 2, 192),
        ),
        metadata_reserve_bytes=1024,
    )


def derive_kernel_ceiling(schema: KernelSchema) -> int:
    text_bytes = sum(slot.max_items * slot.max_text_bytes for slot in schema.slots)
    return schema.metadata_reserve_bytes + 2 * text_bytes


def _priority(atom: SemanticAtom) -> tuple[int, int, int, str]:
    return (
        0 if atom.status is AtomStatus.ACTIVE else 1,
        0 if atom.core_required else 1,
        -atom.revision,
        atom.atom_id,
    )


def select_kernel(
    atoms: tuple[SemanticAtom, ...],
    schema: KernelSchema,
) -> KernelSelection:
    by_role: dict[AtomRole, list[SemanticAtom]] = defaultdict(list)
    for atom in atoms:
        if atom.core_required and atom.status is AtomStatus.ACTIVE:
            by_role[atom.role].append(atom)
    selected: list[SemanticAtom] = []
    reasons: list[str] = []
    for slot in schema.slots:
        eligible = sorted(by_role[slot.role], key=_priority)
        if len(eligible) > slot.max_items:
            reasons.append(f"slot_overflow:{slot.role.value}")
        for atom in eligible[: slot.max_items]:
            if utf8_bytes(atom.text) > slot.max_text_bytes:
                reasons.append(f"text_overflow:{atom.atom_id}")
            selected.append(atom)
    expected = {
        atom.atom_id
        for atom in atoms
        if atom.core_required and atom.status is AtomStatus.ACTIVE
    }
    actual = {atom.atom_id for atom in selected}
    if expected != actual:
        reasons.append("core_coverage_incomplete")
    ordered = tuple(sorted(selected, key=lambda atom: (atom.role.value, atom.atom_id)))
    return KernelSelection(
        atoms=ordered,
        byte_ceiling=derive_kernel_ceiling(schema),
        valid=not reasons,
        reasons=tuple(sorted(set(reasons))),
    )
```

- [ ] **Step 4: Write failing matrix tests**

```python
from crm_experiment.matrix import atomize, build_matrix, compose_candidates


def test_atomize_replaces_all_blocks_touching_an_updated_key(base_state, delta_atom) -> None:
    atoms = atomize(base_state, (delta_atom,))
    assert [atom.revision for atom in atoms if "decision" in atom.semantic_keys] == [2]


def test_exact_atoms_are_never_merge_candidates(two_exact_atoms) -> None:
    candidates = compose_candidates(two_exact_atoms)
    assert all(len(candidate.covers) == 1 for candidate in candidates)


def test_matrix_dimensions_match_atoms_and_candidates(two_body_atoms) -> None:
    candidates = compose_candidates(two_body_atoms)
    matrix = build_matrix(two_body_atoms, candidates, budget=1024)
    assert len(matrix.coverage) == len(two_body_atoms)
    assert all(len(row) == len(candidates) for row in matrix.coverage)
```

Put shared fixtures in `tests/conftest.py`; fixtures must construct real
`SemanticAtom`, `CapsuleState`, and delta objects with no model calls.

- [ ] **Step 5: Implement atomization, candidates, and matrices**

`matrix.py` must implement these rules:

```python
def atomize(
    base: CapsuleState | None,
    delta: tuple[SemanticAtom, ...],
) -> tuple[SemanticAtom, ...]:
    base_atoms = () if base is None else base.kernel + base.body
    updated_keys = {key for atom in delta for key in atom.semantic_keys}
    retained = [
        atom
        for atom in base_atoms
        if updated_keys.isdisjoint(atom.semantic_keys)
    ]
    latest: dict[str, SemanticAtom] = {}
    for atom in sorted(delta, key=lambda item: (item.as_of, item.revision, item.atom_id)):
        for key in atom.semantic_keys:
            previous = latest.get(key)
            if previous is None or (atom.revision, atom.atom_id) > (
                previous.revision,
                previous.atom_id,
            ):
                latest[key] = atom
    unique_delta = {atom.atom_id: atom for atom in latest.values()}
    return tuple(sorted(retained + list(unique_delta.values()), key=lambda atom: atom.atom_id))
```

`compose_candidates` must create:

- one direct candidate per active atom;
- one pairwise merge candidate only when both atoms are non-exact, non-core,
  active, share a role, have merge depth 0, and have no dependency conflict;
- merged text as `"<left text>; <right text>"`;
- stable IDs from canonical candidate content;
- exact serialized UTF-8 byte cost;
- zero risk for direct candidates and `0.001` for verified merge candidates.

`build_matrix` must return immutable tuple matrices. Feature columns are fixed as:

```python
(
    role_priority,
    active_indicator,
    normalized_revision,
    exact_indicator,
    dependency_count,
    core_indicator,
)
```

Coverage is based on `candidate.covers`; dependency rows use atom IDs; conflicts
are 1 when candidates cover different active revisions of the same semantic key;
redundancy is Jaccard overlap of coverage sets.

- [ ] **Step 6: Run focused tests**

```powershell
uv run pytest tests/test_kernel.py tests/test_matrix.py -q
uv run ruff check src/crm_experiment/kernel.py src/crm_experiment/matrix.py tests/test_kernel.py tests/test_matrix.py
```

Expected: all tests pass; default \(K=4608\).

- [ ] **Step 7: Commit**

```powershell
git add src/crm_experiment/kernel.py src/crm_experiment/matrix.py tests/conftest.py tests/test_kernel.py tests/test_matrix.py
git commit -m "feat: add continuity kernel and CRM matrices"
```

### Task 4: Deterministic optimizer

**Files:**

- Create: `src/crm_experiment/optimizer.py`
- Test: `tests/test_optimizer.py`

- [ ] **Step 1: Write the five-atom reference test**

```python
from crm_experiment.contracts import LossPolicy
from crm_experiment.optimizer import optimize


def test_reference_problem_prefers_low_risk_c2_c3(reference_matrix) -> None:
    selection = optimize(
        reference_matrix,
        LossPolicy(gamma=50.0, rho=0.0, risk_ceiling=0.05, exact_threshold=12),
    )
    assert selection.candidate_ids == ("c2", "c3")
    assert selection.byte_cost == 15
    assert selection.objective == 1.15
```

The fixture encodes weights `(4, 5, 6, 3, 1)`, costs `(12, 7, 8, 7)`,
risks `(.02, .002, .001, .40)`, and the dependency `a4 -> a3`.

Add this exact fixture to `tests/conftest.py`:

```python
@pytest.fixture
def reference_matrix() -> MatrixBundle:
    return MatrixBundle(
        atom_ids=("a1", "a2", "a3", "a4", "a5"),
        candidate_ids=("c1", "c2", "c3", "c4"),
        features=((0.0,) * 6,) * 5,
        coverage=(
            (1, 1, 0, 0),
            (1, 1, 0, 0),
            (1, 0, 1, 0),
            (0, 0, 1, 1),
            (0, 0, 0, 1),
        ),
        redundancy=((0.0,) * 4,) * 4,
        conflicts=((0,) * 4,) * 4,
        dependencies=(
            (0, 0, 0, 0, 0),
            (0, 0, 0, 0, 0),
            (0, 0, 0, 0, 0),
            (0, 0, 1, 0, 0),
            (0, 0, 0, 0, 0),
        ),
        costs=(12, 7, 8, 7),
        risks=(0.02, 0.002, 0.001, 0.40),
        weights=(4.0, 5.0, 6.0, 3.0, 1.0),
        budget=15,
    )
```

- [ ] **Step 2: Run the test and verify failure**

```powershell
uv run pytest tests/test_optimizer.py -q
```

Expected: FAIL because `optimizer.py` does not exist.

- [ ] **Step 3: Implement exact-small and greedy-large selection**

Implement:

```python
def optimize(matrix: MatrixBundle, policy: LossPolicy) -> Selection:
    candidate_count = len(matrix.candidate_ids)
    if candidate_count <= policy.exact_threshold:
        return _exact_selection(matrix, policy)
    return _greedy_selection(matrix, policy)


def _evaluate(
    matrix: MatrixBundle,
    policy: LossPolicy,
    selected: tuple[int, ...],
) -> tuple[float, tuple[int, ...]] | None:
    if sum(matrix.costs[index] for index in selected) > matrix.budget:
        return None
    risk = sum(matrix.risks[index] for index in selected)
    if risk > policy.risk_ceiling:
        return None
    for offset, left in enumerate(selected):
        for right in selected[offset + 1 :]:
            if matrix.conflicts[left][right]:
                return None
    covered = tuple(
        atom_index
        for atom_index, row in enumerate(matrix.coverage)
        if any(row[candidate_index] for candidate_index in selected)
    )
    covered_set = set(covered)
    for atom_index in covered:
        required = {
            dependency_index
            for dependency_index, flag in enumerate(matrix.dependencies[atom_index])
            if flag
        }
        if not required <= covered_set:
            return None
    omission = sum(
        weight
        for atom_index, weight in enumerate(matrix.weights)
        if atom_index not in covered_set
    )
    redundancy = sum(
        matrix.redundancy[left][right]
        for offset, left in enumerate(selected)
        for right in selected[offset + 1 :]
    )
    return omission + policy.gamma * risk + policy.rho * redundancy, covered


def _selection(
    matrix: MatrixBundle,
    selected: tuple[int, ...],
    objective: float,
    covered: tuple[int, ...],
    solver: str,
) -> Selection:
    return Selection(
        candidate_ids=tuple(matrix.candidate_ids[index] for index in selected),
        covered_atom_ids=tuple(matrix.atom_ids[index] for index in covered),
        objective=round(objective, 12),
        byte_cost=sum(matrix.costs[index] for index in selected),
        solver=solver,
    )


def _exact_selection(matrix: MatrixBundle, policy: LossPolicy) -> Selection:
    best: tuple[float, tuple[str, ...], tuple[int, ...], tuple[int, ...]] | None = None
    for mask in range(1 << len(matrix.candidate_ids)):
        selected = tuple(
            index
            for index in range(len(matrix.candidate_ids))
            if mask & (1 << index)
        )
        evaluated = _evaluate(matrix, policy, selected)
        if evaluated is None:
            continue
        objective, covered = evaluated
        ids = tuple(matrix.candidate_ids[index] for index in selected)
        key = (objective, ids, selected, covered)
        if best is None or key[:2] < best[:2]:
            best = key
    if best is None:
        raise ValueError("no feasible candidate selection")
    return _selection(matrix, best[2], best[0], best[3], "exact")


def _greedy_selection(matrix: MatrixBundle, policy: LossPolicy) -> Selection:
    selected: tuple[int, ...] = ()
    current = _evaluate(matrix, policy, selected)
    if current is None:
        raise ValueError("empty selection must be feasible")
    while True:
        choices: list[tuple[float, str, int, float, tuple[int, ...]]] = []
        for index, candidate_id in enumerate(matrix.candidate_ids):
            if index in selected:
                continue
            trial = tuple(sorted(selected + (index,)))
            evaluated = _evaluate(matrix, policy, trial)
            if evaluated is None:
                continue
            objective, covered = evaluated
            improvement = current[0] - objective
            if improvement > 0:
                choices.append(
                    (
                        -(improvement / max(1, matrix.costs[index])),
                        candidate_id,
                        index,
                        objective,
                        covered,
                    )
                )
        if not choices:
            break
        _, _, index, objective, covered = min(choices)
        selected = tuple(sorted(selected + (index,)))
        current = (objective, covered)
    return _selection(matrix, selected, current[0], current[1], "greedy")
```

Both solvers must:

- reject subsets over the byte budget stored in the matrix selection context;
- reject total risk above `risk_ceiling`;
- reject any pair with `conflicts[i][j] == 1`;
- close dependencies before accepting coverage;
- compute `omission + gamma*risk + rho*redundancy`;
- sort equal solutions by candidate ID tuple;
- round only the returned objective to 12 decimal places;
- never use random order or wall-clock time.

`MatrixBundle.budget` is defined in Task 2 and is the persistent residual budget
available to body candidates. The exact solver enumerates bit masks only up to
`exact_threshold`. The greedy solver ranks feasible marginal objective decrease
per byte and breaks ties by candidate ID. It stops when no candidate strictly
improves the objective.

- [ ] **Step 4: Add determinism and constraint tests**

```python
def test_optimizer_rejects_conflict_pairs(conflicting_matrix) -> None:
    selection = optimize(conflicting_matrix, default_policy())
    assert not {"old-current", "new-current"} <= set(selection.candidate_ids)


def test_optimizer_is_stable_across_repeated_calls(reference_matrix) -> None:
    outputs = {optimize(reference_matrix, default_policy()) for _ in range(20)}
    assert len(outputs) == 1
```

- [ ] **Step 5: Run focused tests and lint**

```powershell
uv run pytest tests/test_optimizer.py tests/test_matrix.py -q
uv run ruff check src/crm_experiment/optimizer.py tests/test_optimizer.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/crm_experiment/contracts.py src/crm_experiment/matrix.py src/crm_experiment/optimizer.py tests/test_optimizer.py tests/test_matrix.py
git commit -m "feat: implement deterministic CRM optimizer"
```

### Task 5: Loss-aware recomposition and safe release

**Files:**

- Create: `src/crm_experiment/recompose.py`
- Create: `src/crm_experiment/production_diag.py`
- Test: `tests/test_recompose.py`
- Test: `tests/test_production_diag.py`

- [ ] **Step 1: Write failing state-transition tests**

```python
from crm_experiment.canonical import semantic_hash
from crm_experiment.contracts import OutcomeState
from crm_experiment.recompose import recompose_capsule


def test_normal_recomposition_consumes_delta_and_releases_old_body(normal_request) -> None:
    result = recompose_capsule(normal_request)
    assert result.outcome is OutcomeState.NORMAL
    assert result.consumed_delta is True
    assert result.loss.continuity_break is False
    assert result.state.generation == normal_request.base_state.generation + 1


def test_budget_at_k_produces_kernel_only(kernel_only_request) -> None:
    result = recompose_capsule(kernel_only_request)
    assert result.outcome is OutcomeState.KERNEL_ONLY
    assert result.state.body == ()
    assert result.state.kernel


def test_budget_below_k_rejects_change_and_delta(blocked_request) -> None:
    result = recompose_capsule(blocked_request)
    assert result.outcome is OutcomeState.ADMISSION_BLOCKED
    assert result.state.kernel == blocked_request.base_state.kernel
    assert result.state.body == ()
    assert result.state.accepted_budget == blocked_request.base_state.accepted_budget
    assert result.consumed_delta is False


def test_zero_delta_is_a_semantic_fixed_point(normal_request) -> None:
    first = recompose_capsule(normal_request)
    second_request = normal_request.__class__(
        base_state=first.state,
        delta=(),
        byte_budget=first.state.accepted_budget,
        kernel_schema=normal_request.kernel_schema,
        loss_policy=normal_request.loss_policy,
        weight_version=normal_request.weight_version,
    )
    second = recompose_capsule(second_request)
    assert semantic_hash(second.state) == semantic_hash(first.state)
```

- [ ] **Step 2: Run the tests and verify failure**

```powershell
uv run pytest tests/test_recompose.py -q
```

Expected: FAIL because `recompose.py` does not exist.

- [ ] **Step 3: Implement the pure recomposition transaction**

`recompose_capsule` must execute this exact order:

```python
def recompose_capsule(request: RecompositionRequest) -> RecompositionResult:
    kernel_ceiling = derive_kernel_ceiling(request.kernel_schema)
    if request.byte_budget < kernel_ceiling:
        if request.base_state is None:
            raise ValueError("initial budget is below continuity floor")
        return blocked_result(request)

    atoms = atomize(request.base_state, request.delta)
    kernel = select_kernel(atoms, request.kernel_schema)
    if not kernel.valid:
        if request.base_state is None:
            raise ValueError("initial state cannot satisfy continuity kernel")
        return blocked_result(request)

    kernel_ids = {atom.atom_id for atom in kernel.atoms}
    body_atoms = tuple(atom for atom in atoms if atom.atom_id not in kernel_ids)
    residual_budget = request.byte_budget - serialized_kernel_bytes(kernel.atoms)
    candidates = compose_candidates(body_atoms)
    matrix = build_matrix(body_atoms, candidates, budget=max(0, residual_budget))
    selection = optimize(matrix, request.loss_policy)
    candidate_state = build_candidate_state(request, kernel, candidates, selection)
    gate = validate_candidate(request, atoms, candidate_state, selection)
    if not gate.valid:
        candidate_state = build_kernel_only_state(request, kernel)
        outcome = OutcomeState.KERNEL_ONLY
    else:
        outcome = OutcomeState.NORMAL if candidate_state.body else OutcomeState.KERNEL_ONLY
    return build_result(request, atoms, candidate_state, outcome, matrix)
```

All helper functions in the snippet are private functions in `recompose.py`.
They must be implemented in the same task. `validate_candidate` checks:

- actual canonical UTF-8 bytes do not exceed `byte_budget`;
- every applicable core-required input atom is in `state.kernel`;
- no selected candidate exceeds the aggregate risk ceiling;
- every retained block is covered by input atoms;
- no current/current conflict survives;
- all retained dependencies exist;
- every input atom appears in retained, merged, or released sets.

`blocked_result(request)` creates a new generation containing only
`request.base_state.kernel`, keeps the previous accepted budget and high-water
mark, releases the old body, rejects the incoming delta, and sets
`consumed_delta=False`. It never returns the full old Capsule. This makes failure
bounded without creating an empty context.

`build_candidate_state` converts selected candidates to `SemanticAtom` blocks.
Merged blocks have `merge_depth=1`. `compose_candidates` must not merge atoms
whose `merge_depth > 0`, which makes zero-delta recomposition idempotent.

Measure matrix construction, optimization, and gate phases with
`time.perf_counter_ns()` and store the three named durations in
`RecompositionResult.stage_ns`. Do not include durations in semantic hashes or
the frozen state manifest.

- [ ] **Step 4: Implement the production diagnostic**

`production_diag.py` exposes:

```python
@dataclass(frozen=True, slots=True)
class ProductionDiagnostic:
    attempted: bool
    strict_eligible: bool
    codes: tuple[str, ...]


def diagnose_production_eligibility(result: RecompositionResult) -> ProductionDiagnostic:
    if result.loss.released_atom_ids:
        return ProductionDiagnostic(False, False, ("EXPERIMENT_LOSSY",))
    return ProductionDiagnostic(False, False, ("NO_FORMAL_ENVELOPE",))
```

The initial experiment never publishes and never reports `strict_eligible=True`.
A later lossless formal-envelope adapter requires a separate approved task.

Update `src/crm_experiment/__init__.py` only in this task:

```python
"""Deterministic Capsule Recomposition Matrix experiment."""

from .recompose import recompose_capsule

__all__ = ["recompose_capsule"]
```

- [ ] **Step 5: Run transition and diagnostic tests**

```powershell
uv run pytest tests/test_recompose.py tests/test_production_diag.py -q
```

Expected: all tests pass, including `NORMAL`, `KERNEL_ONLY`,
`ADMISSION_BLOCKED`, release inventory, and zero-delta fixed point.

- [ ] **Step 6: Commit**

```powershell
git add src/crm_experiment/__init__.py src/crm_experiment/recompose.py src/crm_experiment/production_diag.py tests/test_recompose.py tests/test_production_diag.py
git commit -m "feat: add loss-aware Capsule recomposition"
```

### Task 6: Frozen query projection and claim-v2 scoring

**Files:**

- Create: `src/crm_experiment/projection.py`
- Create: `src/crm_experiment/scoring.py`
- Test: `tests/test_projection.py`
- Test: `tests/test_scoring.py`

- [ ] **Step 1: Write failing projection tests**

```python
from crm_experiment.projection import project_query


def test_projection_reads_only_matching_frozen_atoms(frozen_state, goal_query) -> None:
    result = project_query(frozen_state, goal_query, byte_budget=256)
    assert result.supported is True
    assert result.selected_atom_ids == ("goal-block",)


def test_projection_returns_explicit_unknown_when_missing(frozen_state, missing_query) -> None:
    result = project_query(frozen_state, missing_query, byte_budget=256)
    assert result.supported is False
    assert result.text == "[CONTEXT_INSUFFICIENT]"
```

- [ ] **Step 2: Run the tests and verify failure**

```powershell
uv run pytest tests/test_projection.py -q
```

Expected: FAIL because `projection.py` does not exist.

- [ ] **Step 3: Implement bounded projection**

```python
from __future__ import annotations

from .canonical import utf8_bytes
from .contracts import CapsuleState, ProjectionResult, QuerySpec


UNKNOWN = "[CONTEXT_INSUFFICIENT]"


def project_query(
    state: CapsuleState,
    query: QuerySpec,
    byte_budget: int,
) -> ProjectionResult:
    matching = [
        atom
        for atom in state.kernel + state.body
        if atom.role is query.role and query.semantic_key in atom.semantic_keys
    ]
    matching.sort(key=lambda atom: (-atom.revision, atom.atom_id))
    selected = []
    lines = []
    used = 0
    for atom in matching:
        line = atom.text
        cost = utf8_bytes(line) + (1 if lines else 0)
        if used + cost <= byte_budget:
            selected.append(atom)
            lines.append(line)
            used += cost
    if not selected:
        return ProjectionResult(query.query_id, UNKNOWN, (), (), utf8_bytes(UNKNOWN), False)
    covered = sorted({item for atom in selected for item in atom.covered_atom_ids})
    return ProjectionResult(
        query_id=query.query_id,
        text="\n".join(lines),
        selected_atom_ids=tuple(atom.atom_id for atom in selected),
        selected_covered_ids=tuple(covered),
        byte_cost=used,
        supported=True,
    )
```

No overload accepting old state, delta, truth, or Summary is permitted.

- [ ] **Step 4: Implement claim-v2 scoring**

`score_projection` must use both coverage IDs and normalized text:

```python
def score_projection(result: ProjectionResult, gold: QueryGold) -> ClaimV2Result:
    selected = set(result.selected_covered_ids)
    missing_ids = sorted(set(gold.required_atom_ids) - selected)
    stale_ids = sorted(set(gold.forbidden_atom_ids) & selected)
    normalized = normalize_text(result.text)
    missing_text = sorted(
        token for token in gold.required_text if normalize_text(token) not in normalized
    )
    forbidden_text = sorted(
        token for token in gold.forbidden_text if normalize_text(token) in normalized
    )
    missing = tuple(missing_ids + missing_text)
    contradictions = tuple(stale_ids + forbidden_text)
    omission = not result.supported or bool(missing)
    stale = bool(contradictions)
    return ClaimV2Result(
        query_id=gold.query_id,
        passed=not omission and not stale,
        omission=omission,
        stale_current=stale,
        missing=missing,
        contradictions=contradictions,
    )
```

`normalize_text` performs NFKC, casefolding, and whitespace collapse. Add tests
for Chinese text, exact anchors, explicit unknown, and forbidden old revisions.

- [ ] **Step 5: Implement paired cluster bootstrap**

Add:

```python
def paired_cluster_lower_bound(
    paired_by_cluster: dict[str, tuple[float, float]],
    replicates: int,
    seed: int,
    alpha: float = 0.05,
) -> float:
    cluster_ids = tuple(sorted(paired_by_cluster))
    rng = random.Random(seed)
    deltas = []
    for _ in range(replicates):
        sample = [rng.choice(cluster_ids) for _ in cluster_ids]
        delta = sum(
            paired_by_cluster[key][0] - paired_by_cluster[key][1]
            for key in sample
        ) / len(sample)
        deltas.append(delta)
    deltas.sort()
    index = max(0, math.floor(alpha * replicates) - 1)
    return deltas[index]
```

- [ ] **Step 6: Run focused tests**

```powershell
uv run pytest tests/test_projection.py tests/test_scoring.py -q
uv run ruff check src/crm_experiment/projection.py src/crm_experiment/scoring.py tests/test_projection.py tests/test_scoring.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/crm_experiment/projection.py src/crm_experiment/scoring.py tests/test_projection.py tests/test_scoring.py
git commit -m "feat: add frozen projection and claim-v2 scoring"
```

### Task 7: Preregistered protocol and offline baselines

**Files:**

- Create: `config/protocol-v1.json`
- Create: `src/crm_experiment/protocol.py`
- Create: `src/crm_experiment/baselines.py`
- Test: `tests/test_protocol.py`

- [ ] **Step 1: Freeze the protocol configuration**

Create `config/protocol-v1.json`:

```json
{
  "schema_version": 1,
  "stream_count": 24,
  "generation_count": 12,
  "queries_per_generation": 6,
  "seed_start": 271800,
  "budget_multipliers": [8.0, 4.0, 2.0, 1.5, 1.0],
  "main_budget_multiplier": 2.0,
  "main_generation": 12,
  "query_injection_bytes": 512,
  "zero_delta_rounds": 16,
  "bootstrap_replicates": 10000,
  "bootstrap_seed": 2718,
  "noninferiority_margin_pp": -5.0,
  "weights": {
    "gamma": 50.0,
    "rho": 0.1,
    "risk_ceiling": 0.05,
    "exact_threshold": 12,
    "version": "theta0"
  }
}
```

- [ ] **Step 2: Write failing protocol tests**

```python
from crm_experiment.protocol import generate_protocol, load_protocol_config


def test_protocol_has_fixed_denominator() -> None:
    config = load_protocol_config("config/protocol-v1.json")
    bundle = generate_protocol(config)
    assert len(bundle.streams) == 24
    assert all(len(stream.generations) == 12 for stream in bundle.streams)
    assert sum(len(generation.queries) for stream in bundle.streams for generation in stream.generations) == 1728


def test_protocol_generation_is_byte_identical() -> None:
    first = generate_protocol(load_protocol_config("config/protocol-v1.json"))
    second = generate_protocol(load_protocol_config("config/protocol-v1.json"))
    assert canonical_json(first) == canonical_json(second)
```

- [ ] **Step 3: Run tests and verify failure**

```powershell
uv run pytest tests/test_protocol.py -q
```

Expected: FAIL because protocol models and generator do not exist.

- [ ] **Step 4: Implement deterministic stream generation**

Add protocol-only frozen dataclasses in `protocol.py`:

```python
ProtocolConfig
GenerationCase
StreamCase
ProtocolBundle
```

`generate_protocol` uses seeds `271800..271823`. Every generation emits:

- one current-goal or focus update;
- one active constraint or decision update;
- one open-loop update;
- one exact anchor in generation 1 and an anchor revision in every even generation;
- two topic-return/context atoms;
- one stale predecessor in sealed gold only.

Every generation exposes exactly six query specs in this fixed order:

```python
(
    AtomRole.ROOT_GOAL,
    AtomRole.DECISION,
    AtomRole.HARD_CONSTRAINT,
    AtomRole.EXACT_ANCHOR,
    AtomRole.CONTEXT,
    AtomRole.OPEN_LOOP,
)
```

Runtime events, public queries, and sealed gold are separate members of
`ProtocolBundle`. The state-building API accepts runtime events only.

- [ ] **Step 5: Implement fair offline baselines**

`baselines.py` defines:

```python
class LegacyCapsuleBaseline
class RecursiveSummaryBaseline
class NoProjectionAblation
class NoKernelAblation
```

Legacy Capsule selects direct candidates by scalar `weight / byte_cost` with a
stable ID tie-break and no CRM relation terms.

Recursive Summary stores newline-delimited records:

```text
<role>|<semantic-key>|<atom-id>|r<revision>|<text>
```

It receives only its previous text and current delta, replaces lines with the
same semantic key, then packs lines under the identical persistent byte budget.
It never receives canonical truth, Capsule state, or hidden gold. Query-time line
selection uses the same 512-byte injection ceiling. Role, key, atom ID, revision,
delimiters, and text all count toward the Summary byte budget.

Register these budgeted arms in fixed order:

```python
ARM_ORDER = (
    "crm",
    "legacy_capsule",
    "recursive_summary",
    "crm_greedy",
    "crm_no_projection",
    "crm_no_kernel",
)
```

- `crm_greedy` uses the same CRM request with `exact_threshold=-1`, which forces
  the deterministic greedy path without changing weights or hard constraints.
- `crm_no_projection` reuses the frozen CRM state but injects a stable prefix of
  serialized blocks under 512 bytes without inspecting the query.
- `crm_no_kernel` uses an empty kernel schema and clears `core_required` only in
  the offline ablation copy. It may emit an empty state and can never be treated
  as a publishable or main-method result.
- The canonical upper bound is scored separately, has no byte-budget claim, and
  is absent from `ARM_ORDER`.

- [ ] **Step 6: Add isolation tests**

Tests must prove:

- `recompose_capsule` and all baseline `advance` methods contain no query or gold parameter;
- query files contain query IDs/specs but no required answers;
- sealed gold contains required and forbidden atom IDs;
- all gold-critical kernel roles fit KernelSchemaV1;
- the no-kernel overflow fixture is excluded from the main 24 clusters;
- every budgeted arm obeys the same UTF-8 budget;
- only the named `crm_no_kernel` arm may produce an empty state.

- [ ] **Step 7: Run focused tests and commit**

```powershell
uv run pytest tests/test_protocol.py -q
git add config/protocol-v1.json src/crm_experiment/protocol.py src/crm_experiment/baselines.py tests/test_protocol.py
git commit -m "feat: add sealed multiround protocol and baselines"
```

### Task 8: Freeze-before-reveal runner with generational release

**Files:**

- Create: `src/crm_experiment/runner.py`
- Create: `src/crm_experiment/cli.py`
- Test: `tests/test_runner.py`
- Generate: `data/runtime/protocol.json`
- Generate: `data/public-queries/queries.json`
- Generate: `data/sealed-gold/gold.json`
- Generate: `results/learning-events.jsonl`

- [ ] **Step 1: Write the failing ordering and release tests**

```python
from pathlib import Path

from crm_experiment.runner import run_protocol


def test_checkpoint_hash_is_frozen_before_queries_are_opened(
    smoke_config: Path,
    runtime_protocol: Path,
    public_queries: Path,
    tmp_path: Path,
) -> None:
    records = run_protocol(
        smoke_config,
        runtime_protocol,
        public_queries,
        tmp_path / "run",
    )
    manifest = tmp_path / "run" / "state-hash-manifest.json"
    assert records.is_file()
    assert manifest.is_file()
    assert b'"kernel"' not in manifest.read_bytes()
    assert b'"body"' not in manifest.read_bytes()


def test_runtime_records_do_not_retain_old_capsule_text(
    smoke_config: Path,
    runtime_protocol: Path,
    public_queries: Path,
    tmp_path: Path,
) -> None:
    records = run_protocol(
        smoke_config,
        runtime_protocol,
        public_queries,
        tmp_path / "run",
    )
    text = records.read_text(encoding="utf-8")
    assert "released_raw_text" not in text


def test_learning_events_are_content_free(run_output: Path) -> None:
    rows = [
        json.loads(line)
        for line in (run_output / "learning-events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    forbidden = {"text", "kernel", "body", "raw_delta", "released_text"}
    assert rows
    assert all(forbidden.isdisjoint(row) for row in rows)
```

- [ ] **Step 2: Run test and verify failure**

```powershell
uv run pytest tests/test_runner.py -q
```

Expected: FAIL because `runner.py` does not exist.

- [ ] **Step 3: Implement per-checkpoint freeze, reveal, query, and release**

`cli.py` must expose:

```text
materialize --config <path> --output <root>
run --config <path> --runtime <path> --queries <path> --output <root>
aggregate --records <path> --gold <path> --output <root>
render --results <path> --output <root>
```

`run_protocol` iterates stream, generation, budget, and arm in sorted order.
At each checkpoint it must:

1. load only runtime delta;
2. advance every arm without opening the query file;
3. compute and append content-free state semantic hashes, byte counts, outcome,
   release counts, and timing metrics;
4. only after every arm hash is frozen, open the six public query specs for that
   checkpoint;
5. project and write query records;
6. discard the previous generation objects and retain only the new current state
   needed for the next delta.

The manifest stores no kernel, body, Summary text, or released raw content.
Records may store bounded projected answer text and selected coverage IDs for
offline scoring, but the runtime state machine cannot read records back.
Sealed gold is not an argument to `run_protocol` and is never opened.

Write a separate content-free `learning-events.jsonl` containing only weight
version, matrix dimensions/hash, role-count vectors, selected/released counts,
byte totals, churn, later-use counts, omission/error flags, and outcome. It must
contain no atom text, raw delta, old Capsule, query text, or released content.

Any missing or invalid checkpoint yields six explicit failure records rather
than reducing the denominator. Each of the six budgeted arms therefore has
exactly 1728 rows at each budget.

Run 16 zero-delta recompressions for CRM, Legacy Capsule, and Recursive Summary
at each budget. CRM stability is a hard gate; baseline drift is reported. Also
run:

- the final-state budget squeeze \(8K\to4K\to2K\to1.5K\to K\);
- peak memory with `tracemalloc`;
- matrix, optimizer, gate, and total latency from `RecompositionResult.stage_ns`
  plus runner timing.

- [ ] **Step 4: Add deterministic rerun tests**

```python
def test_smoke_state_hashes_are_byte_identical(
    smoke_config,
    runtime_protocol,
    public_queries,
    tmp_path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    run_protocol(smoke_config, runtime_protocol, public_queries, first)
    run_protocol(smoke_config, runtime_protocol, public_queries, second)
    first_manifest = first / "state-hash-manifest.json"
    second_manifest = second / "state-hash-manifest.json"
    assert first_manifest.read_bytes() == second_manifest.read_bytes()
```

Normalize output paths and performance timing out of the state-hash manifest so
separate roots remain byte-identical.

- [ ] **Step 5: Materialize the frozen protocol**

```powershell
uv run python -m crm_experiment.cli materialize --config config/protocol-v1.json --output data
```

Expected:

- `data/runtime/protocol.json`;
- `data/public-queries/queries.json`;
- `data/sealed-gold/gold.json`;
- an input manifest with SHA-256 and byte size for all three.

- [ ] **Step 6: Run focused tests and commit**

```powershell
uv run pytest tests/test_runner.py tests/test_protocol.py -q
git add src/crm_experiment/runner.py src/crm_experiment/cli.py tests/test_runner.py config/protocol-v1.json
git commit -m "feat: add phase-separated CRM experiment runner"
```

Do not commit materialized runtime, query, gold, or result files.

### Task 9: Aggregation, hard gates, and academic figures

**Files:**

- Create: `src/crm_experiment/reporting.py`
- Test: `tests/test_reporting.py`
- Generate: `results/records.jsonl`
- Generate: `results/metrics.json`
- Generate: `results/metrics.csv`
- Generate: `results/report.md`
- Generate: `figures/fig1_crm_rate_distortion.png`
- Generate: `figures/fig1_crm_rate_distortion.svg`
- Generate: `figures/data-manifest.md`

- [ ] **Step 1: Write failing aggregation tests**

```python
from crm_experiment.reporting import evaluate_hard_gates


def test_any_stale_current_fails_the_architecture() -> None:
    metrics = {
        "continuity_breaks": 0,
        "stale_current": 1,
        "budget_overshoots": 0,
        "fallback_reads": 0,
        "core_coverage": 1.0,
        "anchor_exact": 1.0,
        "zero_delta_stability": 1.0,
    }
    result = evaluate_hard_gates(metrics)
    assert result["passed"] is False
    assert "stale_current" in result["failures"]
```

`evaluate_hard_gates` consumes only the main `crm` arm. Failures in ablation or
baseline arms remain reported metrics and cannot either fail or rescue the main
architecture gate.

- [ ] **Step 2: Implement aggregation**

`aggregate_run` must produce:

- cumulative history compression ratio;
- step compression ratio;
- release rate;
- \(K/E\) byte split;
- projection bytes;
- peak workspace bytes;
- build/solve/gate/total latency;
- weighted retention and omission;
- continuity break and stale-current counts;
- core and exact-anchor recall;
- query-level claim-v2;
- topic-return accuracy;
- kernel-only and admission-blocked rates;
- zero-delta semantic stability;
- paired cluster-bootstrap differences.

The independent unit is stream ID. Query rows remain a fixed denominator.
Unknown is omission; a stale assertion is both query failure and hard safety
failure.

At \(B=2K\), generation 12, compute the one-sided 95% paired-cluster lower
bound for CRM minus Recursive Summary. The gate passes only if it is at least
`-0.05`.

- [ ] **Step 3: Implement the five-panel paper figure**

Use these constants:

```python
INK = "#263238"
GREEN = "#1F6B5B"
RED = "#A44742"
WARM_GRAY = "#786F63"
LIGHT_GRAY = "#DDD6CC"
MARKERS = {"crm": "o", "legacy_capsule": "s", "recursive_summary": "^"}
```

Panels:

1. rate-distortion across five budgets;
2. continuity and stale-current across twelve generations;
3. stacked kernel/body/released bytes;
4. budget-by-generation query heatmap;
5. latency and peak-memory efficiency.

Export SVG and a 450-DPI PNG. Use geometric markers, no illustration, no
currency character, and label every denominator. Small vector glyphs for
kernel, merge, and release may appear in the legend; they must remain geometric
paper symbols rather than characters or cartoons.

- [ ] **Step 4: Add figure contract tests**

Tests must assert:

- both files exist after a smoke render;
- SVG contains the three palette colors;
- `chr(0x00A5)` is absent from SVG text;
- PNG dimensions are at least 3000 by 2400;
- every plotted metric comes from `results/metrics.json`;
- mock or synthetic values never enter the final result path.

- [ ] **Step 5: Run the full registered experiment**

```powershell
uv run python -m crm_experiment.cli run --config config/protocol-v1.json --runtime data/runtime/protocol.json --queries data/public-queries/queries.json --output results
uv run python -m crm_experiment.cli aggregate --records results/records.jsonl --gold data/sealed-gold/gold.json --output results
uv run python -m crm_experiment.cli render --results results/metrics.json --output figures
```

If any hard gate fails, the report must say the architecture failed the current
protocol. Do not rewrite it as partial success.

- [ ] **Step 6: Run reporting tests and commit source**

```powershell
uv run pytest tests/test_reporting.py tests/test_scoring.py -q
git add src/crm_experiment/reporting.py tests/test_reporting.py
git commit -m "feat: add CRM metrics and academic reporting"
```

Do not commit generated result data or figures until the user separately asks
for publication packaging.

### Task 10: Final verification and evidence handoff

**Files:**

- Generate: `evidence/final-artifact-hashes.json`
- Generate: `evidence/verification.txt`
- Modify only if verification exposes a scoped defect: files under `src/crm_experiment\` and `tests\`

- [ ] **Step 1: Run the full standalone suite**

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
```

Expected: all commands exit 0.

- [ ] **Step 2: Prove there is no model or network path**

```powershell
$forbidden = Get-ChildItem -LiteralPath "$TaskRoot\src\crm_experiment" -Recurse -File -Filter '*.py' |
    Select-String -Pattern 'ollama','openai','requests','httpx','urllib.request','socket' -CaseSensitive:$false
if ($forbidden) { throw 'Forbidden model or network dependency found' }
```

Expected: no matches.

- [ ] **Step 3: Re-run protected repository regressions**

```powershell
Set-Location $RepoRoot
$env:TEMP = "$TaskRoot\.tmp\repo-regression"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
& 'G:\AstrContinuum\.venv\Scripts\python.exe' -m pytest -p no:cacheprovider -q tests/experiments tests/test_permanent_validator.py tests/test_retrieval.py tests/test_budget.py
```

Expected: all affected existing tests pass. Record skips separately.

- [ ] **Step 4: Verify protected source hashes**

```powershell
Set-Location $TaskRoot
uv run python -m crm_experiment.evidence --repo $RepoRoot --manifest "$TaskRoot\evidence\protected-source-hashes.json" --verify
```

Expected: exit 0. If it fails, inspect and report drift; never restore or revert
the user's dirty worktree.

- [ ] **Step 5: Hash final artifacts**

Extend `evidence.py` with `write_artifact_manifest(root, paths, output)`. Register:

- protocol config and frozen input manifests;
- state manifest and records;
- content-free learning events;
- metrics JSON/CSV;
- report;
- figure source, PNG, and SVG;
- complete test output.

Write only relative paths, byte sizes, and SHA-256 values.

- [ ] **Step 6: Final consistency assertions**

Programmatically assert:

- every arm/budget has exactly 1728 query rows;
- every main-method generation has one frozen semantic hash;
- all persistent and injection byte ceilings are respected;
- all 16 zero-delta semantic hashes equal their starting hash;
- Summary is absent from CRM runtime dependencies;
- `strict_eligible` is false for every lossy result;
- no empty Capsule is emitted by `crm` or any runtime-eligible arm; only the
  explicitly named offline `crm_no_kernel` ablation may be empty;
- hard-gate outcome in `metrics.json` equals the prose conclusion in `report.md`.

- [ ] **Step 7: Commit verification code and final source state**

```powershell
git add src/crm_experiment tests config/protocol-v1.json evidence/protected-source-hashes.json
git commit -m "test: verify CRM experiment end to end"
```

Generated evidence remains available in the task root even when not committed.

## Plan self-review checklist

- Spec coverage: Tasks 2–6 implement CRM, matrices, deterministic selection,
  continuity kernel, safe release, frozen projection, and production isolation.
- Protocol coverage: Tasks 7–9 implement 24 streams, 12 generations, 6 queries,
  five normalized budgets, 16 zero-delta rounds, fixed denominators, baselines,
  efficiency, statistics, and publication figures.
- Safety coverage: Tasks 1, 5, 7, 8, and 10 prevent production writes, empty
  context, Summary fallback, model/API use, query leakage, and silent failures.
- Post-learning: Phase one records weight version and content-free outcomes.
  Training is intentionally excluded; it requires a later approved plan.
- Type consistency: the locked public API and dataclass fields are used
  unchanged in all tasks.
- Scope: every created source file is under the approved D-drive experiment
  root; repository files are read-only.
