# Evidence and experiment summary

English | [简体中文](EVIDENCE.zh-CN.md)

> **Scope boundary.** This page records isolated research evidence and its
> provenance boundary. It is not a claim that v0.3.0 has passed a semantic
> model evaluation, a Provider benchmark, a production-performance benchmark,
> or a release-readiness gate.

> **Gate A classification.** `V03-WIRE-001` is a deterministic integration
> verification (12 frozen scenarios × 3 trials), published below as a table
> and redacted receipts. Its all-pass structural result is not presented as a
> model-performance figure or a v0.3-versus-Summary result.

## How to read this evidence

The repository publishes bounded aggregate extracts only. It deliberately does
not publish raw conversations, blinded prompts or responses, or nonessential
intermediate artifacts. The [evidence manifest](evidence/README.md) identifies
each extract, its source identity, and its committed-file checksum.

Three bounded evidence records are included:

1. **Frozen R2 synthetic evaluation.** Twelve scenarios × three trials give
   36 fixed-denominator units. It separately reports structural validity,
   answer delivery, and claim-v2 end-to-end outcomes. It does not represent
   real users, real semantic models, or production latency.
2. **CRM V21-008 deterministic stress record.** Twelve replay-matching rounds
   cover 30 input records and 122,880 input bytes. It measures byte and loss
   accounting on a pre-registered deterministic CRM path. It does not prove
   that the CRM path is wired into the v0.3 worker or ready to release.
3. **V03-WIRE-001 Gate A integration verification.** Twelve frozen scenarios × three trials
   exercise the injected/fenced `CompactionWorker` update path. It records
   structural publication, an exact pointer `1 → 2`, durable ledger records,
   and required core-edge retention. It does not measure semantic answer
   quality, Provider behavior, production behavior, or a v0.3-versus-Summary
   outcome.

## Pre-v0.3 historical R2 baseline

<p align="center">
  <img src="assets/evidence-r2-outcomes-rmb.svg" width="860"
       alt="Figure R2: two-panel dot plot of fixed-denominator frozen R2 aggregate outcomes, n=36; research-only and not a v0.3 comparison">
</p>

<p align="center"><em>
Frozen R2 synthetic evaluation (12 scenarios × 3 trials, n=36). Values are
committed aggregates shown as count/36 (%); no uncertainty interval or
hypothesis test is available from the published aggregate. This historical
record is not a v0.3 evaluation or release-to-release comparison.
</em></p>

The frozen research source is identified as results.json with SHA-256:

E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215.

This is a pre-v0.3 historical baseline, not an evaluation of the v0.3
update/reorganization mechanism.

| Arm | Structural valid | Answer delivered | claim-v2 end-to-end | Interpretation boundary |
| --- | ---: | ---: | ---: | --- |
| Full capsule | 27 / 36 | 27 / 36 | 21 / 36 | Frozen synthetic record |
| Projection | 36 / 36 | 36 / 36 | 25 / 36 | Frozen synthetic record |
| Summary | 36 / 36 | 36 / 36 | 27 / 36 | Offline comparison only; never a runtime fallback |
| Oracle floor | 36 / 36 | n/a | n/a | Registered encoding floor, not a quality or information-theoretic optimum |

The committed aggregate is
[docs/evidence/frozen-r2-outcomes.csv](evidence/frozen-r2-outcomes.csv).
Its SHA-256 is
415B52EF538B2F7B76CA6815C45BC2C2F6580CC50BD9C16798D83296E8918B7F.
The Summary arm remains a historical offline comparison; AstrContinuum does
not use it as a main path or degraded runtime fallback.

**Interpretation guard.** These arms are not a release-to-release performance
ranking. This aggregate cannot establish that Full capsule is better than
Summary, or that the v0.3 update/reorganization mechanism was enabled.

### Reproducible figure export

The released SVG is generated from the committed aggregate, not maintained as
hand-drawn artwork. After an approved experiment updates the CSV, rebuild the
figure with:

```bash
uv run python scripts/render_r2_evidence_figure.py
```

The release test renders to a temporary path and requires the result to match
the committed SVG exactly.

## V0.3 Gate A integration verification: opt-in worker wiring

`V03-WIRE-001` is a reviewed, provider-free structural integration check. It runs the
frozen fixture at
[experiments/v03_update_scenarios.json](../experiments/v03_update_scenarios.json)
through the real injected/fenced `CompactionWorker`: initial publication,
capture of one update, candidate reorganization, permanent validation,
Snapshot/CAS publication, and immutable ledger persistence. The fixture
SHA-256 is
36BA7B902C57903B5B1ABCCE21378812C21F54267F018B8706E57A4BE3A648D9;
each of its 12 scenarios is run three times (`n = 36`). Receipts contain only
hashes, counters, pointer versions, and stable status codes—never source event
text.

| Gate A integrity check | Result | Interpretation boundary |
| --- | ---: | --- |
| Baseline publication committed | 36 / 36 | Initial Snapshot exists before the update. |
| Reorganized update publication committed | 36 / 36 | The fenced opt-in candidate was validated and published. |
| Active pointer exactly `1 → 2` | 36 / 36 | One staged update advanced the active Snapshot exactly once. |
| Durable reorganization ledger | 36 / 36 | The update Snapshot has non-empty, hash-round-trippable records. |
| Required exact-anchor + dependency records retained | 36 / 36 | Core ledger edges survive the permanent gate. |
| Non-summary released records | 0 | A safety invariant, not a quality score. |

The committed aggregate is
[aggregate.csv](evidence/v03-update-gate-a/aggregate.csv) (SHA-256
08E956A9527AFAA0523A00588D331E293AA54CE5005AD1FC9E3F37C1284D518B),
with per-unit redacted receipts in
[receipt.csv](evidence/v03-update-gate-a/receipt.csv) (SHA-256
D4382A3778CCEC510EE510716482D67C4BBEFAC1D5630DF2A2488E1B0354EA1D) and
[receipt.json](evidence/v03-update-gate-a/receipt.json) (SHA-256
CBD31AEA6F6062F6C9980FA03B0A0EBCB9C82034C6A1E7E7725D9B068256DF1E).
The aggregate result SHA-256 is
89032BBF0D23D4B09978F93F774FD5392DF90C440EE02BF0D852A85BC80A10D3.

### Reproducible Gate A execution

Use a fresh task-local workspace and an empty output directory; the runner
refuses to overwrite a prior receipt or reuse the same run workspace:

```powershell
uv run python experiments/v03_update_benchmark.py `
  --output-dir D:\bun\tmp\codex\AstrContinuum-v03-experiment\receipt `
  --workspace D:\bun\tmp\codex\AstrContinuum-v03-experiment\workspace
```

Gate A intentionally has no performance figure: its fixed deterministic
all-pass outcome is a release-contract verification, not a scientific outcome
curve. A future E2E figure is admitted only after the paired Gate B dataset
includes the same model, scorer, staged histories, update-depth axis, and
paired accuracy/staleness/context-cost/latency outcomes for both arms.

**Interpretation guard.** Gate A establishes only an opt-in, injected, fenced
worker wiring path. The ordinary Provider-bound runtime and current AstrBot
composition leave `reorganization_token_budget` unset and therefore remain
empty-ledger publications. Gate A is not an AstrBot Provider integration,
semantic-quality result, performance result, production-readiness result, or
a comparison against Summary. The pre-registered E2E `v03_reorganized_worker`
versus freshly refreshed `summary_refresh` result remains **`BLOCKED`** pending
approved Provider receipts and the frozen scorer.

## CRM V21-008 deterministic stress record

The isolated source record is identified by immutable revision
af835babbd1ca07619251f83c4e0201264975a49 and source SHA-256:

0C8BDC214588AA53F06DA830181D7EB9FF5D164063E8EC8FB74F75B7D0651760.

It reports replay_match=true and COMPLETED: 12 rounds, 30 input records, and
122,880 input bytes. Per-round reduction_ppm ranges from 116,499 to 347,594
(mean 247,519.17). Round 12 reports 33,974 resident bytes, 347,594 reduction
ppm, 652,405 retention ppm, and cumulative loss 30.

The committed aggregate is
[docs/evidence/crm-v21-008-stress-rounds.csv](evidence/crm-v21-008-stress-rounds.csv).
Its SHA-256 is
645943F5738320295580CAA62BC228694267251EF8AA01BFA13ECC5C2FFF2A01.
These values only describe the frozen deterministic accounting record. They do
not establish production-session quality, user utility, model generalization,
Provider behavior, or v0.3 release status.

## What v0.3.0 can and cannot claim

v0.3.0 can claim its tested persistence and publication contracts: fenced
SQLite publication, immutable Capsules and Snapshots, a permanent mechanical
quality gate, and an immutable reorganization ledger when records are
explicitly supplied to publication. A successful CAS does not leave orphaned
ledger rows; a ledger-integrity failure rolls back the transaction and does not
move the active pointer. `V03-WIRE-001` additionally establishes the narrow,
opt-in injected/fenced Gate A worker path described above: deterministic
reorganization, publication, exact pointer movement, and durable ledger
records under its frozen synthetic fixture.

The ordinary Provider-bound runtime and current AstrBot composition do not set
`reorganization_token_budget`, so their background publications still supply
an empty reorganization-record tuple. Only an injected compiler backend with
an explicit budget may call `reorganize_capsules()` in the fenced Gate A path.
That does not make automatic CRM reorganization a general AstrBot runtime
capability.

v0.3.0 cannot claim:

- a real semantic-model or Provider evaluation;
- ordinary Provider-bound/AstrBot runtime reorganization or a general CRM
  worker capability;
- superiority to a freshly refreshed Summary arm;
- a bypass around the permanent gate for non-summary released records;
- public v1.0 status, a million-token performance result, or a complete
  compatibility matrix.

See [Workflow](WORKFLOW.md) for the separation between the shipped publication
chain and the CRM research target, and [Evaluation](EVALUATION.md) for
scenarios, metrics, and future release thresholds.
