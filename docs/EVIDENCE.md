# Evidence and experiment summary

English | [简体中文](EVIDENCE.zh-CN.md)

> **Scope boundary.** This page records isolated research evidence and its
> provenance boundary. It is not a claim that v0.3.0 has passed a semantic
> model evaluation, a Provider benchmark, a production-performance benchmark,
> or a release-readiness gate.

<p align="center">
  <img src="assets/evidence-r2-outcomes-rmb.svg" width="720"
       alt="Frozen R2 synthetic outcomes; research-only and not evidence of v0.3 production readiness / 冻结 R2 合成结果；仅限研究，不构成 v0.3 生产就绪证据">
</p>

<p align="center"><em>
Frozen R2 synthetic evaluation (12 scenarios × 3 trials, n=36). Research-only;
not a v0.3 production-performance or release-readiness claim.
<br>
冻结 R2 合成评测（12 个场景 × 3 次试验，n=36）。仅限研究，不构成 v0.3
生产性能或发布就绪声明。
</em></p>

## How to read this evidence

The repository publishes bounded aggregate extracts only. It deliberately does
not publish raw conversations, blinded prompts or responses, or nonessential
intermediate artifacts. The [evidence manifest](evidence/README.md) identifies
each extract, its source identity, and its committed-file checksum.

Two independent research records are included:

1. **Frozen R2 synthetic evaluation.** Twelve scenarios × three trials give
   36 fixed-denominator units. It separately reports structural validity,
   answer delivery, and claim-v2 end-to-end outcomes. It does not represent
   real users, real semantic models, or production latency.
2. **CRM V21-008 deterministic stress record.** Twelve replay-matching rounds
   cover 30 input records and 122,880 input bytes. It measures byte and loss
   accounting on a pre-registered deterministic CRM path. It does not prove
   that the CRM path is wired into the v0.3 worker or ready to release.

## Frozen R2 synthetic evaluation

The frozen research source is identified as results.json with SHA-256:

E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215.

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
move the active pointer.

The standard CompactionWorker does not call reorganize_capsules(); ordinary
background publication therefore supplies an empty reorganization-record tuple.
The ledger interface exists, but automatic CRM reorganization is not a v0.3
runtime capability.

v0.3.0 cannot claim:

- a real semantic-model or Provider evaluation;
- an automatically wired CRM reorganization worker;
- a bypass around the permanent gate for non-summary released records;
- public v1.0 status, a million-token performance result, or a complete
  compatibility matrix.

See [Workflow](WORKFLOW.md) for the separation between the shipped publication
chain and the CRM research target, and [Evaluation](EVALUATION.md) for
scenarios, metrics, and future release thresholds.
