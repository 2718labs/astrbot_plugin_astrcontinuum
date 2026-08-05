# Roadmap

English | [简体中文](./ROADMAP.zh-CN.md)

The roadmap is directional, not a compatibility promise. Maturity claims belong in the README,
architecture document, changelog, and verification evidence for a concrete commit.

## v0.3.0 Technical Preview — current integration baseline

This milestone overlays the verified `v0.2.1` runtime baseline with a narrowly scoped `v0.3.0`
storage-safety contract. The retained runtime baseline includes:

- authenticated at-rest encryption, key creation, rotation, recovery, and transactional upgrade;
- pinned offline `cl100k_base` and `o200k_base` assets with request-level BYTE fallback;
- separate canonical, live-request, and compaction token lanes with encrypted metric sidecars and
  bounded backfill;
- public AstrBot Provider context-window resolution with a conservative automatic fallback;
- content-free status and inspection evidence;
- a deterministic, allowlisted `<16 MiB` archive contract and real AstrBot `4.24.0` and `4.26.7`
  probes; and
- the wired worker lifecycle: claim, renewal, retry, cancellation, and atomic Snapshot
  publication.

`v0.3.0` adds migration v3 and immutable ordered reorganization-ledger storage at the explicit
repository-publication boundary. The permanent quality floor rejects every non-
`narrative_summary` `released` ledger record. The ordinary Provider-bound/default AstrBot path
remains **storage-safety-only**: it leaves `reorganization_token_budget` unset and publishes an
empty ledger. A separate opt-in injected compiler backend exercises candidate reorganization and
ledger persistence in fenced synthetic Gate A validation. That narrow path does not assert
Provider integration, semantic quality, Provider performance, full AstrBot compatibility, or
public end-user release readiness.

The Technical Preview's evaluation plan, frozen evidence summary, and current/target workflow are
documented in [Evaluation](./EVALUATION.md), [Evidence](./EVIDENCE.md), and
[Workflow](./WORKFLOW.md). Their isolated synthetic and deterministic records are not production
semantic-quality, Provider-performance, or public-release-readiness evidence.

## v0.3.x — wiring and operational evidence

- Extend reorganization from the fenced synthetic Gate A worker to an ordinary Provider-bound
  runtime only with explicit source-item accounting, permanent-quality validation, focused
  regression evidence, and approved integration review.
- Exercise that ordinary runtime, lease renewal, bounded cancellation, encrypted upgrade,
  backup/restore, crash recovery, and long-running WAL behavior on real deployments.
- Evaluate an optional semantic-audit Provider without blocking live hooks.
- Provide bounded administration for retry, cancellation, and safe rollback.
- Add adapter-specific evidence and declare only platforms actually verified.
- Expand multi-version and long-context probe automation without publishing Provider secrets or
  message content.

## v1.0 — production contract

- Stable migrations and documented upgrade and rollback policy.
- Demonstrated non-blocking behavior, crash atomicity, and cross-session isolation under load.
- Multi-resolution context reconstruction with source trace.
- Reproducible multi-provider long-context evaluation with explicit tokenizer profiles.
- Operator-facing backup, retention, privacy, and recovery controls.
- AstrBot-market submission only after repository, security, documentation, and compatibility
  gates pass.

## Later exploration

- Hybrid retrieval and tool-artifact storage.
- User-editable context policies.
- Cross-session project context with explicit authority boundaries.
- Multi-agent context exchange.
- Learned selectors or specialized context codecs, only when deterministic fallbacks remain.
