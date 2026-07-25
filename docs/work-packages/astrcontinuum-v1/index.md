# AstrContinuum v1.0.0 Work Index

## Product Direction

- `product-brief.md`
- `../../superpowers/specs/2026-07-26-astrcontinuum-v1-design.md`
- `../../superpowers/plans/2026-07-26-astrcontinuum-v1-wave1.md`

## Shared Contracts

- `contracts/runtime-invariants.md`
- `contracts/data-contracts.md`
- `contracts/astrbot-composition.md`
- `contracts/verification.md`

## Tasks

| Task | State | Depends on | Deliverable |
| --- | --- | --- | --- |
| `V1-001` | done | none | Freeze architecture, contracts, risks, and first patch plan |
| `V1-101` | done | `V1-001` | Repair Capsule Schema and persistence documents with observed RED tests |
| `V1-201` | done | `V1-101` | Immutable domain envelopes and permanent validators |
| `V1-202` | done | `V1-201` | SQLite migrations and connection policy |
| `V1-203` | ready | `V1-202` | Nine repository transactions, concurrency, and recovery |
| `V1-301` | pending | `V1-203` | Standalone read view, retrieval, budget, and assembly trace |
| `V1-302` | pending | `V1-301` | Reversible AstrBot projection and unique Hook writers |
| `V1-401` | pending | `V1-203` | Segmenter, compiler, permanent validator, and optional auditor |
| `V1-402` | pending | `V1-301`, `V1-401` | Query-aware reconstruction and FTS fallback |
| `V1-501` | pending | `V1-302`, `V1-402` | Tool payloads, Inspector commands, lifecycle, migration |
| `V1-601` | pending | `V1-501` | Compatibility, crash, privacy, performance, and million-token acceptance |
| `V1-602` | pending | `V1-601` | Release metadata, package, changelog, tag, and market checks |

Only tasks in the current wave receive detailed cards. Later cards are generated after
their direct contracts and dependencies are green.

## Dispatch

- Current wave: `V1-203`
- Current card: `tasks/V1-203.md`
- Write owner: one repository implementation agent
- Write scope: the exact paths listed in the task card
- Write conflicts: none
- Next gate: observe capture RED before adding repository code, then close each remaining
  transaction family with focused RED/GREEN and real multi-connection evidence

## Operating Rules

- SQLite workflow state is authoritative; this index is a human projection.
- One effective lease owns each write scope.
- Workers read only their card and directly linked contracts.
- Production behavior requires a real failing test before implementation.
- `INDEX_PARTIAL` is acceptable only with no missing required path and only known
  extractor gaps.
- Public release metadata remains unchanged until `V1-601` is green.
