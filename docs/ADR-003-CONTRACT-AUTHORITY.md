# ADR-003: Contract Authority and Conflict Resolution

- Status: Accepted for Phase 0
- Scope: architecture and persistence contracts

## Decision

AstrContinuum MUST resolve requirements in this order:

1. the user Master Prompt and explicit follow-up constraints;
2. verified AstrBot official source for the supported version range;
3. startup-package `specs/` and tests;
4. 2718lab DevKit rules;
5. all other documentation.

A conflict MUST be recorded as an explicit decision with the competing claims, evidence, selected authority, and compatibility effect. Implementations and documents MUST NOT silently choose one claim.

The JSON Schemas under `specs/` are authoritative for persistence and orchestration envelope shape. The runtime invariants and accepted ADRs are authoritative for cross-record semantics that standard JSON Schema cannot express, including arithmetic relations between target and coverage fields, transaction boundaries, contiguous coverage, active-pointer compare-and-swap (CAS), and hook ownership. Those relations MUST be enforced by permanent mechanical validators and, where the fields share a SQLite row, DB constraints. Filesystem Markdown is a projection; the 2718lab workflow SQLite record is the scheduling authority.

## Permanent Rules

The following checks MUST remain enabled regardless of configuration:

- contiguous coverage and source high-water validation;
- structural schema validation;
- SessionKey identity validation;
- exact-anchor preservation;
- non-empty compiler output;
- atomic publish and active-pointer CAS.

`strict_audit=false` MAY disable only optional model-based semantic review. It MUST NOT disable any permanent rule above or permit a candidate to be published after a mechanical check fails.

External Sylanne memory text MUST NOT be imported into `journal_events`. `persona_id` is identity metadata only. This project MUST NOT invent a Sylanne API.

## Dependency Authority

AstrBot host installation dependencies MUST be declared in the repository-root `requirements.txt`. Local lint, type-check, and test tooling MUST be declared in `pyproject.toml`. A package needed by the running plugin MUST NOT be present only in `pyproject.toml`; development-only tooling MUST NOT be presented as an AstrBot host requirement.

AstrBot integrations MUST prefer `astrbot.api.*`. An exception for an internal `astrbot.core.*` symbol MUST satisfy all of these conditions:

- the required capability has no verified public equivalent;
- the exact import path and verified version observations are recorded in an ADR;
- the use is isolated behind one adapter boundary;
- missing imports or attributes have an explicit fail-open or fail-closed policy;
- the exception is tracked as version-checked technical debt.

ADR-006 records the sole Phase 0 exception for `TextPart`. No public tokenizer, response writer, or Sylanne memory API is inferred by analogy.

## Change Control

A later change that alters a table, field, wire state, transaction name, SessionKey component, hook owner, or coverage meaning MUST update the relevant Schema, ADR, data-flow document, database document, concurrency state machine, and test matrix in one reviewed change. Renaming a stable wire value is a compatibility change, not editorial cleanup.

Phase 0 documentation MUST NOT be used to authorize runtime implementation, migration, publication, or modification of `main.py` or `astrcontinuum/**/*.py`.

## Verification

A documentation or Schema write is complete only when it is bound to an input index query trace, a pre-write checkpoint, an output index query trace, and a verification artifact. Reviewers MUST reject a change whose prose contradicts a higher authority even when its Markdown checks pass.
