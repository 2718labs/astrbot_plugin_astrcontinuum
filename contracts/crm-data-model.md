# CRM Immutable Data Model

## Rules

- Every record below is a `@dataclass(frozen=True, slots=True)` in `crm_experiment.contracts`.
- Every collection field is a tuple. Callers establish deterministic order before construction.
- Enums inherit from `StrEnum` and use the exact values below.
- Public field names, order, and types are frozen for Tasks 2–10.
- No record contains a Summary, raw released history, model output, or storage handle.

## Enums

```python
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
```

## Records

```python
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

## Canonical semantics

- `utf8_bytes(text)` is `len(text.encode("utf-8"))`.
- `canonical_value` recursively converts dataclasses, enums, mappings with string keys, tuples, and lists into JSON-compatible values.
- `canonical_json` uses UTF-8-preserving JSON, sorted keys, and separators `(",", ":")`.
- `sha256_text` hashes UTF-8 bytes and returns lowercase hexadecimal.
- `semantic_hash(CapsuleState)` includes only `kernel`, `body`, and `weight_version`. It deliberately excludes generation, high-water mark, and accepted budget, so zero-delta recomposition stability is content-based.
- Unsupported mapping keys or value types must fail explicitly rather than be stringified ambiguously.

## Locked public functions

```python
utf8_bytes(value: str) -> int
canonical_value(value: object) -> object
canonical_json(value: object) -> str
sha256_text(value: str) -> str
semantic_hash(state: CapsuleState) -> str
```
