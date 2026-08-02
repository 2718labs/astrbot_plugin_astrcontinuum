# V21-001 Control-Fold Contract

Owner: primary coordinator
Depends on: accepted Protocol V2 reachability diagnostic and user approval of
transparent `CAPSULE_APPROX` / `RELEASED_MISS` for old noncore context.

## Goal

Freeze the smallest shared V2.1 control-fold contract before any schema,
codec, optimizer, or runner code is written.

## Context

- `contracts/crm-v21-control-fold.md` is the only shared V2.1 contract.
- V2 control state reached 8,440 B at generation 1 and 120,494 B at generation
  12 under the fixed lower-bound frame; V2 remains immutable diagnostic data.
- The user permits old noncore content to become explicit approximation or
  miss, while core/hard dependencies remain exact.

## Routing

Exceptional design scope: a bounded Sol-high design review established the
schema direction; coordinator integration owns this contract. Future multi-file
implementation is High (Terra max), with disjoint write scopes and TDD.

## Write Scope

- `contracts/crm-v21-control-fold.md`
- `tasks/v21-001-control-contract.md`

## Acceptance

- Contract defines identity isolation, state vocabulary, outcome labels,
  public reencoding boundary, atomic failure behavior, and no-revival rules.
- It explicitly excludes Summary runtime state and preserves V2 evidence.
- No Python code, config, data/result artifact, or production file changes.

## Return

Report the contract path, exact boundary accepted from the user, and the next
implementation task required to prove schema-3 reachability.
