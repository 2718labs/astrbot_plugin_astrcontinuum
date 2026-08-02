# PROTOCOL-V2 Preregistered Multiround Capsule Stress Protocol

Owner: primary-main-protocol-v2
Depends on: arch-003

## Goal

Build an offline, deterministic Protocol v2 that measures multi-round Capsule
recomposition, physical packing, selective release, continuity, and bounded
work under growing context. This task creates protocol code and evidence only;
it never uses a model, API, network, real conversation, or production write.

## Context

- `tasks/arch-003.md`: completed atomic v2 source optimizer and gate.
- `src/crm_experiment/contracts_v2.py`, `logical_v2.py`, `recompose_v2.py`,
  `projection_v2.py`: frozen v2 state and public recomposition interfaces.
- Physical packing savings and source release are distinct denominators. A
  released source must not be reported as a packed byte saving.
- `G:\AstrContinuum` is production and read-only. All generated inputs and
  outputs belong under this experiment's `data/v2` and `results/v2`.

## Write Scope

- `config/protocol-v2.json`
- `src/crm_experiment/protocol_v2.py`
- `src/crm_experiment/runner_v2.py`
- `src/crm_experiment/cli.py`
- `tests/test_protocol_v2.py`
- `tests/test_runner_v2.py`
- `tasks/protocol-v2.md`
- `data/v2`
- `results/v2`
- `index.md` (only after checking no active writer owns it)

## Frozen Study Shape

- 24 streams, 12 generations, and 6 sealed queries per generation.
- Budgets are `{8K, 4K, 2K, 1.5K, K}`, where `K` is derived only from the
  exact v2 continuity floor; configuration records the derived numeric value.
- Six arms: CRM-v2 packing, CRM-v2 direct-only, legacy Capsule, recursive
  Summary offline baseline, CRM-v2 no-projection, and CRM-v2 no-kernel.
- Each generation updates five core roles and adds increasing noncore load in
  compressible immutable, incompressible immutable, and mutable/ineligible
  negative-control strata. Generations 5--12 query a source exactly four
  generations old; warmup queries are not labelled topic-return.
- Generator calibration alone targets source-active bytes in `[7.70, 7.90] KiB`
  (`1 KiB = 1024 bytes`, hence physical acceptance `[7885, 8089]` bytes). This
  calibration unit is distinct from the budget symbol `K`, which is derived
  only from the exact continuity-floor byte field.
  Arm outcomes, latency, omission, weight, order, and policy never tune it.

## Required Contracts

- Per round, report four distinct physical byte quantities using the same
  frame/policy/budget: `F` = shadow full-retention direct state, `D` = subject
  retained-plan direct equivalent, `B` = committed resident state, and `K` =
  same-frontier kernel-only state. They must satisfy `F >= D >= B >= K` and
  `F - B == (F - D) + (D - B)`. `F -> B` is total resident reduction;
  `D -> B` is conditional packing savings; source-release counts and canonical
  payload bytes are separate, non-resident denominators.
- The subject and shadow lineages are strictly isolated. Shadow may retain the
  full corpus and construct `F`/`K` or an independent logical target, but it
  can never provide released payload to subject recomposition or projection.
- Aggregate multi-round physical ratios as fixed-denominator micro-averages;
  report final and worst rounds. Rollback, unexpected NOOP, work-limit, stale,
  or budget failure remains in the output and is a hard protocol failure, never
  silently dropped from a favorable denominator.
- Generator-only preflight checks counts, exact bytes, monotonic load, strata,
  fixed lengths, kernel admission, query/gold separation, and input hashes.
- Every artifact carries `protocol_id`, `schema_version`, and config hash.
  V1 and V2 identifiers are rejected if mixed by any runner or output reader.
- Report packing as `direct_equivalent_bytes -> resident_bytes` and strict
  packing savings. Report source release only alongside weighted omission and
  available-source denominators.
- Preserve canonical state continuity: budget, codec, decoder, gate, stale,
  forbidden-fallback, and zero-delta failures are hard failures, never scores.
- First full run freezes parameters. A later implementation correction requires
  a new protocol identifier; do not overwrite or backfill old evidence.
- Latency, peak workspace, synthetic corpus compression, deterministic replay,
  and weighted omission are descriptive engineering results only; they do not
  establish general compression, semantic preservation, or model-answer claims.

## TDD Sequence

1. RED: config/generator invariants and deterministic input/hash separation.
2. GREEN: minimal Protocol v2 configuration, generator, and preflight.
3. RED: total-resident reachability preflight rejects the frozen schedule when
   a complete same-frontier kernel-only control state exceeds its budget.
4. GREEN: a deterministic, read-only runner preflight reports exact per-round
   lower bounds and stops before any arm, query projection, smoke, or result
   artifact when the schedule is unreachable.
5. Only after reachability passes, RED: multi-round runner records correct
   denominators, four-generation returns, release/continuity boundaries, and
   V1/V2 isolation.
6. GREEN: minimal runner and CLI surface, then a one-stream smoke.
7. Run the frozen full local experiment only after all preceding gates pass.

## Phase B Gate — total-resident reachability (2026-08-01)

Phase A generator-only calibration is not a resident-state admission proof.
Before any arm can run, `runner_v2` must compute a canonical lower bound for
every selected stream/generation/budget from a complete V2 kernel-only control
state: frontier, receipts, active weight policy, frame fields, and the chosen
policy fields all count. It must never use raw source-text bytes or the static
schema admission floor as a proxy.

The initial policy is deliberately the smallest native V2 lower-bound frame;
it may not claim a full-arm `F/D/B/K` result. If even that lower bound exceeds
a budget, the report must preserve the first failure and return `passed=False`;
it must not start subject recomposition, projection, smoke, full execution,
or write `data/v2`, `results/v2`, or `index.md`.

Read-only architecture review established the current frozen schedule is
unreachable; the implemented canonical lower-bound preflight below supersedes
its provisional five-core estimate. The frozen policy's exact five-core-only
control state is 6,287 B while configured `K` is 4,608 B; for stream-00 the
lower bound grows from 8,440 B at generation 1 to 120,494 B at generation 12
(before a packing-policy immutable-ID list). Thus the literal
8K/4K/2K/1.5K/K schedule is a **P0
diagnostic failure**, not a candidate compression result. The Phase B deliverable
is the independently reproducible failed preflight and its canonical evidence;
it does not alter the frozen config or protocol id. A corrected, runnable study
requires either a new V2.1 protocol with re-derived levels or a new compacted
V2 control-plane schema; that decision is outside this frozen V2 run.

Phase B code scope is limited to `src/crm_experiment/runner_v2.py` and
`tests/test_runner_v2.py`. It exposes only:

```text
preflight_runner_v2(config, bundle, policy) -> RunnerPreflightReportV2
python -m crm_experiment.runner_v2 --preflight --config config/protocol-v2.json
```

The deterministic report contains protocol/schema/config/bundle/policy hashes,
exact lower-bound bytes and failure rows, but no wall-clock field. Its tests
must prove the current config fails at the documented generations, that a
tampered budget/floor cannot be accepted, that lower-bound accounting includes
the control plane, and that no subject or shadow payload is exposed to a
future projection path.

## Acceptance

For the currently frozen V2 diagnostic, Phase B stops at the deterministic
failed reachability receipt. `--smoke` and a full arm run are deliberately
unavailable until a new V2.1 schedule or compacted control-plane schema passes
this gate; they are not current acceptance commands.

```powershell
uv run pytest tests/test_protocol_v2.py tests/test_runner_v2.py -q
uv run python -m crm_experiment.runner_v2 --preflight --config config/protocol-v2.json
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
```

After a separately frozen V2.1 input has a passing reachability receipt, its
new runner may add a one-stream smoke and then a full run as later gates; it
must not reuse or overwrite this failed V2 diagnostic.

Run the bundled DevKit validator, protected production-source verifier, V1/V2
isolation audit, and register only path-safe, content-free evidence before the
lease CAS completion.

## Return

List changed files, RED/GREEN command outputs, preflight/smoke/full evidence,
V1 hash comparison, unambiguous metric definitions, and any blocker.

## Phase A — generator/preflight evidence (2026-08-01)

Scope is limited to Protocol v2 configuration, deterministic input generation,
and generator-only preflight. No runner, CLI, arm execution, model, network,
or output artifact was added in this phase.

### RED

```text
uv run pytest tests/test_protocol_v2.py -q
E   ModuleNotFoundError: No module named 'crm_experiment.protocol_v2'
1 error in 0.48s
```

The failure was expected: the test specified the absent Protocol v2 public API.

### GREEN

```text
uv run pytest tests/test_protocol_v2.py -q
.....                                                                    [100%]
5 passed in 26.76s

uv run ruff check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
All checks passed!

uv run ruff format --check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
2 files already formatted

uv run pyright src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
0 errors, 0 warnings, 0 informations
```

### Phase A P1 re-review hardening (2026-08-01)

Only generator/preflight and frozen-config boundaries changed. No runner, CLI,
arm, model, network, result, or index behavior was added.

RED before each minimal change:

```text
round/public exact query sequence: 1 failed, 37 deselected in 11.17s
core gold topic-return metadata: 2 failed, 38 deselected in 14.90s
immutable duplicate/reused frontier key: 2 failed, 40 deselected in 19.42s
unknown frozen config keys: 2 failed, 42 deselected in 0.37s
```

GREEN after the corresponding changes:

```text
round/public exact query sequence: 1 passed, 37 deselected in 7.90s
core gold topic-return metadata: 2 passed, 38 deselected in 13.75s
immutable duplicate/reused frontier key: 2 passed, 40 deselected in 13.36s
unknown frozen config keys: 2 passed, 42 deselected in 0.11s
all new re-review checks: 7 passed, 37 deselected in 24.29s

uv run pytest tests/test_protocol_v2.py -q
44 passed in 164.55s (0:02:44)

uv run ruff check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
All checks passed!

uv run ruff format --check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
2 files already formatted

uv run pyright src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
0 errors, 0 warnings, 0 informations
```

Preflight now exactly binds `public_queries` to the flattened subject-round
sequence, prohibits topic-return metadata on core gold, rejects duplicate round
keys before replay, checks immutable new-key/frontier source cardinality, and
rejects unknown top-level and `stratum_text_bytes` JSON keys. The config JSON
allowlist derives from the same dataclass fields as the config-hash projection,
excluding only the loader-computed `config_hash`.

### Phase A P1 follow-up — config, all-core bindings, and same-origin replay (2026-08-01)

This follow-up remains generator/preflight-only. It adds no runner, CLI, arm,
model, network, result, or index behavior.

RED was observed for each newly sealed boundary before its implementation:

```text
uv run pytest tests/test_protocol_v2.py -q -k "stale_semantic"
2 failed, 13 deselected in 15.02s
E   Failed: DID NOT RAISE ValueError

uv run pytest tests/test_protocol_v2.py -q -k "every_core_query_gold_binding_mismatch and root_goal and source_id"
1 failed, 34 deselected in 10.38s
E   Failed: DID NOT RAISE ValueError

uv run pytest tests/test_protocol_v2.py -q -k "shadow_lineage_not_derived"
2 failed, 35 deselected in 17.21s
E   Failed: DID NOT RAISE ValueError
```

GREEN evidence:

```text
uv run pytest tests/test_protocol_v2.py -q -k "stale_semantic"
2 passed, 13 deselected in 0.19s

uv run pytest tests/test_protocol_v2.py -q -k "every_core_query_gold_binding_mismatch"
20 passed, 15 deselected in 133.62s (0:02:13)

uv run pytest tests/test_protocol_v2.py -q -k "shadow_lineage_not_derived"
2 passed, 35 deselected in 13.93s
```

The config hash is now derived from every semantic config field except the
stored hash itself, and both public entrypoints reject a stale hash. Every core
role now uses the same sealed binding checks as context queries: one source id
and text, a real current source, exact text, role, semantic key, and expected
generation. Preflight also replays each stream's subject updates by semantic
key, requires its own stream provenance and generation, and exactly compares
the resulting full-retention and kernel-only frontiers with the shadow rounds.

### Phase A P2 — focused test efficiency (2026-08-01)

The tests now share a module-scoped immutable pristine config/bundle fixture.
Mutation tests derive self-consistent replacements from it, while the direct
preflight test still invokes the public double-generation determinism check.
The deterministic generator test separately checks its input hash before the
full canonical bundle comparison; no binding, replay, or determinism assertion
was removed.

Focused timing for the same all-core binding matrix improved from `133.62s` to
`71.59s`:

```text
uv run pytest tests/test_protocol_v2.py -q -k "every_core_query_gold_binding_mismatch"
20 passed, 17 deselected in 71.59s (0:01:11)
```

Final Phase A follow-up checks:

```text
uv run pytest tests/test_protocol_v2.py -q
37 passed in 136.75s (0:02:16)

uv run ruff check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
All checks passed!

uv run ruff format --check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
2 files already formatted

uv run pyright src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
0 errors, 0 warnings, 0 informations
```

Generator-only preflight was invoked directly (not through a runner) and
returned `crm-capsule-stress-v2`, terminal active source bytes `8028`, and
terminal active source load `7.83984375 KiB` using the explicit
`1024 bytes/KiB` denominator. The configured physical acceptance interval is
`[7885, 8089]` bytes, derived from the declared `[7.70, 7.90] KiB` bounds.

### Phase A P1 — determinism and sealed-binding hardening (2026-08-01)

RED was observed before the corresponding changes:

```text
uv run pytest tests/test_protocol_v2.py -q -k "hashes_are_domain or non_deterministic"
2 failed, 5 deselected in 9.72s

uv run pytest tests/test_protocol_v2.py -q -k "binding_mismatches" -x
E   Failed: DID NOT RAISE ValueError
1 failed, 1 passed, 7 deselected in 15.21s
```

The first RED exposed bare shadow corpus hashes and a preflight that generated
only once. The second RED used self-consistent, double-generated altered
bundles and showed that a changed context gold text was not rejected.

GREEN evidence:

```text
uv run pytest tests/test_protocol_v2.py -q -k "hashes_are_domain or non_deterministic"
2 passed, 5 deselected in 16.60s

uv run pytest tests/test_protocol_v2.py -q -k "binding_mismatches"
6 passed, 7 deselected in 41.18s

uv run pytest tests/test_protocol_v2.py -q
13 passed in 81.07s (0:01:21)

uv run ruff check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
All checks passed!

uv run ruff format --check src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
2 files already formatted

uv run pyright src/crm_experiment/protocol_v2.py tests/test_protocol_v2.py
0 errors, 0 warnings, 0 informations
```

### Phase B — total-resident reachability evidence (2026-08-01)

This is a read-only P0 diagnostic, not an arm result and not an `F/D/B/K`
measurement. `runner_v2` uses only detached shadow sources to construct a
complete same-frontier kernel-only control state. It does not import or invoke
recomposition, projection, subject transitions, smoke, scoring, or result
writers.

The frozen policy is
`crm-v2-kernel-only-lower-bound-v1`: default V2 kernel schema, uniform unit
weights with version `protocol-v2-uniform-v1`, disabled packing, no candidate
or transition policy hash, `key_registry_limit=512`,
`max_semantic_key_bytes=128`, and a fixed 8K (36,864 B) lower-bound frame.
The report carries the policy id/hash and these frame fields. Its
`kernel_only_lower_bound_bytes` uses that fixed frame; its
`budget_frame_kernel_only_bytes` rebuilds the same control state with each
literal budget before deciding infeasibility. They are deliberately distinct
because `accepted_budget` is itself resident frame data.

RED:

```text
uv run pytest tests/test_runner_v2.py -q
E   ModuleNotFoundError: No module named 'crm_experiment.runner_v2'
1 error in 0.38s

uv run pytest tests/test_runner_v2.py -q -k preserves_all_frozen_failures
E   AttributeError: 'RunnerFirstFailureV2' object has no attribute 'stream_id'
1 failed, 3 deselected in 20.99s
```

GREEN and static checks:

```text
uv run pytest tests/test_runner_v2.py -q
4 passed in 38.40s

uv run pytest tests/test_protocol_v2.py tests/test_runner_v2.py -q
48 passed in 195.45s (0:03:15)

uv run ruff check src/crm_experiment/runner_v2.py tests/test_runner_v2.py
All checks passed!

uv run ruff format --check src/crm_experiment/runner_v2.py tests/test_runner_v2.py
2 files already formatted

uv run pyright src/crm_experiment/runner_v2.py tests/test_runner_v2.py
0 errors, 0 warnings, 0 informations
```

The module command completed with exit 0 and a deterministic failed receipt:
`protocol_id=crm-capsule-stress-v2`, `passed=false`, 1,440 rows,
`result_hash=3d97339bdc92d456d160933a71e9b38f5b74c5bab4ec726116bded8d54dfd9d1`.
For stream-00, fixed-frame lower-bound bytes were
`[8440, 11348, 15710, 21526, 28796, 37520, 47698, 59330, 72416, 87006,
103022, 120494]`; the five-core-only control state was 6,287 B. First literal
budget-frame failures were K/1.5K at generation 1 (8,439 B), 2K at generation
2 (11,347 B), 4K at generation 4 (21,526 B), and 8K at generation 6
(37,520 B). No `data/v2` or `results/v2` path was created.

### Phase B P1 — receipt-policy binding repair (2026-08-01)

The public preflight receipt now mirrors the two previously omitted policy
projection fields, `stream_ids` and `kernel_schema`. Its dataclass rebuilds a
`RunnerPolicyV2` with the full report policy projection, uses the shared
`_policy_hash` calculation, and compares the expected hash before accepting
its self-hash. This binds every identity and lower-bound framing field to the
policy receipt without exposing source payload. The runner remains
shadow-only, read-only, and does not import recomposition or projection.

The adversarial RED constructed an otherwise canonical report with a new
`result_hash`, but retained a valid-shape `policy_hash` while altering each
bound field:

```text
uv run pytest tests/test_runner_v2.py -q -k self_rehashed
3 failed, 4 deselected in 25.36s
E   Failed: DID NOT RAISE ValueError
```

After the repair, all three self-rehashed forgeries (config hash, bundle input
hash, and lower-bound frame budget), each also carrying an arbitrary
valid-shape `policy_hash`, are rejected by the report dataclass:

```text
uv run pytest tests/test_runner_v2.py -q -k self_rehashed
3 passed, 4 deselected in 24.94s

uv run pytest tests/test_runner_v2.py -q
7 passed in 51.85s

uv run pytest tests/test_protocol_v2.py tests/test_runner_v2.py -q
51 passed in 231.08s (0:03:51)

uv run ruff check src/crm_experiment/runner_v2.py tests/test_runner_v2.py
All checks passed!

uv run ruff format --check src/crm_experiment/runner_v2.py tests/test_runner_v2.py
2 files already formatted

uv run pyright src/crm_experiment/runner_v2.py tests/test_runner_v2.py
0 errors, 0 warnings, 0 informations
```

The report's canonical public projection gained those two bound fields, so its
receipt hash intentionally changed from
`3d97339bdc92d456d160933a71e9b38f5b74c5bab4ec726116bded8d54dfd9d1` to
`dbb6c57e979f366a78940a973d34546bc905a8f332c3a55d661448248b72b224`.
This is a versioned evidence-schema change, not a metric change: the policy
hash remains `930c6823c21ea71c775cf796704d2fbbd41359cfbf932c1903663237383b07f5`,
the 1,440 rows, fixed 8K curve, five-core-only 6,287 B state, and first
failure observations are unchanged. A fresh module preflight completed with
exit 0 and `passed=false`; no `data/v2`, `results/v2`, or `index.md` artifact
was created or modified.

### Phase B P1 — immutable frozen-input anchor (2026-08-01)

Policy-hash self-consistency alone is not an immutable input identity: an
attacker could construct a new `RunnerPolicyV2`, recalculate its matching
policy hash, and then self-hash a direct report. Before changing code, the
trusted sealed config and its canonical generated bundle were independently
read to derive the private anchor values:

```text
domain=crm-protocol-v2-runner-anchor/v1
protocol_id=crm-capsule-stress-v2
schema_version=2
config_hash=03a071a5b5aa6f724b061272673256da506368d22db299f42750124cf5a84d73
bundle_input_hash=15afbb85414a8038e0818c2b870c30827c6c71f5f4f5fe5776507235638fb807
budget_bytes=(36864, 18432, 9216, 6912, 4608)
lower_bound_frame_budget=36864
```

The module now keeps those values in an immutable private anchor and both
`RunnerPolicyV2` and `RunnerPreflightReportV2` call the same validation helper.
The anchor is deliberately not a runtime mutable-config lookup and contains no
source text or payload. The sealed canonical config hash commits the complete
budget scope, while the helper independently fixes its literal 8K frame.

This separates a legitimate new experiment from forbidden parameter drift: a
changed config, canonical bundle, or literal frame must receive a new protocol
id and separately reviewed anchor; attempting the change under
`crm-capsule-stress-v2` is rejected even with matching policy and report
hashes. This frozen V2 module is deliberately not that successor and rejects
the new id too; it adds no V2.1 behavior or protocol broadening.

Strict RED used all three quality-review attacks through the public policy
dataclass and, separately, a direct report carrying the corresponding matching
policy hash and a new report hash:

```text
uv run pytest tests/test_runner_v2.py -q -k immutable_anchor
6 failed, 7 deselected in 21.89s
E   Failed: DID NOT RAISE ValueError
```

GREEN evidence:

The following full-runner and joint gates are pre-P2 historical evidence; P2
adds one test and its current full-runner gate is recorded below. They are kept
to preserve the original anchor-validation receipt, not as a claim about the
post-P2 test count.

```text
uv run pytest tests/test_runner_v2.py -q -k immutable_anchor
6 passed, 5 deselected in 25.40s

uv run pytest tests/test_runner_v2.py -q -k "self_rehashed or immutable_anchor"
7 passed, 4 deselected in 34.11s

uv run pytest tests/test_runner_v2.py -q
11 passed in 51.18s

uv run pytest tests/test_protocol_v2.py tests/test_runner_v2.py -q
55 passed in 290.85s (0:04:50)

uv run ruff check src/crm_experiment/runner_v2.py tests/test_runner_v2.py
All checks passed!

uv run ruff format --check src/crm_experiment/runner_v2.py tests/test_runner_v2.py
2 files already formatted

uv run pyright src/crm_experiment/runner_v2.py tests/test_runner_v2.py
0 errors, 0 warnings, 0 informations
```

A fresh read-only module preflight again completed with exit 0 and
`passed=false`. The private validation anchor does not alter the public report
projection, so the receipt remains
`dbb6c57e979f366a78940a973d34546bc905a8f332c3a55d661448248b72b224`, its
policy hash remains
`930c6823c21ea71c775cf796704d2fbbd41359cfbf932c1903663237383b07f5`, and
all 1,440 observations remain unchanged. No `data/v2`, `results/v2`, or
`index.md` artifact was created or modified.

### Phase B P2 — direct-report rehash evidence (2026-08-01)

The anchor tests previously used `_self_rehashed_report()` implemented with
`dataclasses.replace`. For an anchor-drifting report, that public construction
rightly rejected before `_report_hash` executed, so it did not prove that an
attacker could first calculate a matching receipt hash. This is a test-evidence
gap only; no runner behavior changed.

The test helper now has a narrowly scoped unchecked raw-report object used
only to calculate canonical `_report_hash` over the altered receipt fields.
It then explicitly invokes the public `RunnerPreflightReportV2` constructor
with the altered identity/frame field, matching policy hash, and calculated
matching result hash. The public constructor still rejects the raw receipt at
the immutable anchor. The helper contains no source payload and the existing
artifact snapshot guard remains active.

RED proved the former helper never reached its report-hash calculation:

```text
uv run pytest tests/test_runner_v2.py -q -k raw_receipt
1 failed, 11 deselected in 24.68s
E       assert 0 == 1
```

GREEN proves one raw forged receipt is hashed before its public rejection, and
the broader self-rehash/anchor attack set remains green:

```text
uv run pytest tests/test_runner_v2.py -q -k raw_receipt
1 passed, 11 deselected in 24.87s

uv run pytest tests/test_runner_v2.py -q -k "self_rehashed or immutable_anchor or raw_receipt"
8 passed, 4 deselected in 24.94s

uv run ruff check tests/test_runner_v2.py
All checks passed!

uv run ruff format --check tests/test_runner_v2.py
1 file already formatted
```

The current post-P2 full runner verification is:

```text
uv run pytest tests/test_runner_v2.py -q
12 passed in 40.11s
```

Fresh quality re-review remains required; this evidence update does not claim
acceptance and does not change any diagnostic output or result artifact.
