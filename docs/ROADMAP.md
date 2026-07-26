# Roadmap

English | [简体中文](./ROADMAP.zh-CN.md)

The roadmap is directional, not a compatibility promise. Maturity claims belong in the README,
architecture document, changelog, and verification evidence for a concrete commit.

## v0.1.x — repository stabilization

- Accumulate real AstrBot and adapter compatibility evidence.
- Wire the background compaction worker into `Star` startup and termination with bounded
  cancellation.
- Select production compiler/auditor providers without blocking live hooks.
- Add provider-aware token counters while preserving conservative fail-safe budgeting.
- Add operator telemetry for queue depth, coverage, retry, lease, and degradation codes.
- Exercise upgrade, backup/restore, crash, and long-running WAL behavior on real deployments.
- Keep releases repository-only until the stabilization gate is explicitly accepted.

## v0.2 — controlled compaction preview

- Enable automatic job claiming behind an explicit configuration gate.
- Provide bounded administration for status, retry, cancellation, and safe rollback.
- Add adapter-specific evidence and declare only platforms actually verified.
- Expand multi-version probe automation without publishing provider secrets.
- Add a safe context inspector that separates provenance from private message content.

## v1.0 — production contract

- Stable migrations and documented upgrade/rollback policy.
- Demonstrated non-blocking behavior, crash atomicity, and cross-session isolation under load.
- Multi-resolution context reconstruction with source trace.
- Provider-aware budgeting and reproducible long-context evaluation.
- Operator-facing backup, retention, privacy, and recovery controls.
- AstrBot market submission only after repository, security, documentation, and compatibility
  gates pass.

## Later exploration

- Hybrid retrieval and tool-artifact storage.
- User-editable context policies.
- Cross-session project context with explicit authority boundaries.
- Multi-agent context exchange.
- Learned selectors or specialized context codecs, only when deterministic fallbacks remain.
