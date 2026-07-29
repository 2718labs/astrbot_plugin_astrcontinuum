# Roadmap

English | [简体中文](./ROADMAP.zh-CN.md)

The roadmap is directional, not a compatibility promise. Maturity claims belong in the README,
architecture document, changelog, and verification evidence for a concrete commit.

## v0.2.1 — verified release baseline

The repository now includes:

- authenticated at-rest encryption, key creation/rotation/recovery, and transactional upgrade;
- pinned offline `cl100k_base`/`o200k_base` assets and request-level BYTE fallback;
- separate canonical, live-request, and compaction token lanes with encrypted metric sidecars
  and bounded backfill;
- public AstrBot Provider context-window resolution with a conservative automatic fallback;
- content-free status/inspection evidence;
- a deterministic, allowlisted `<16 MiB` archive contract and real AstrBot `4.24.0`/`4.26.7`
  probes.

The repository and verified release ZIP are installation sources. AstrBot-market distribution is
a separate maintainer process and is not performed by repository CI.

## Next v0.2.x — operational evidence

- Exercise the wired worker, lease renewal, bounded cancellation, encrypted upgrade,
  backup/restore, crash recovery, and long-running WAL behavior on real deployments.
- Evaluate an optional semantic-audit provider without blocking live hooks.
- Provide bounded administration for retry, cancellation, and safe rollback.
- Add adapter-specific evidence and declare only platforms actually verified.
- Expand multi-version and long-context probe automation without publishing provider secrets
  or message content.

## v1.0 — production contract

- Stable migrations and documented upgrade/rollback policy.
- Demonstrated non-blocking behavior, crash atomicity, and cross-session isolation under load.
- Multi-resolution context reconstruction with source trace.
- Reproducible multi-provider long-context evaluation with explicit tokenizer profiles.
- Operator-facing backup, retention, privacy, and recovery controls.
- AstrBot market submission only after repository, security, documentation, and compatibility
  gates pass.

## Later exploration

- Hybrid retrieval and tool-artifact storage.
- User-editable context policies.
- Cross-session project context with explicit authority boundaries.
- Multi-agent context exchange.
- Learned selectors or specialized context codecs, only when deterministic fallbacks remain.
