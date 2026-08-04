# AstrContinuum task register

English | [简体中文](./TASKS.zh-CN.md)

> **Planning boundary.** This is a forward-looking task register, not a completion report. The
> current release is the `v0.3.0` Technical Preview; use the [roadmap](docs/ROADMAP.md),
> [test matrix](docs/TEST_MATRIX.md), and current reproducible evidence for release claims.

## Phase 0 — reconnaissance

- [ ] Verify AstrBot plugin APIs, hook order, and lock semantics for each supported host probe.
- [ ] Verify context persistence, Provider, and tokenizer interfaces against current sources.
- [ ] Verify stable public Sylanne APIs before any durable integration.
- [ ] Record ADRs, risks, and gap reports for material contract changes.

## Phase 1 — foundational consistency

- [ ] SQLite Event Journal, WAL, and migration evolution.
- [ ] Snapshot store and atomic active pointer.
- [ ] Job leases and crash recovery.
- [ ] Session-key normalization.

## Phase 2 — primary request path

- [ ] Model-aware token counter with an offline, reproducible contract.
- [ ] Snapshot + Delta assembly.
- [ ] Turn-boundary preservation.
- [ ] Emergency assembly.
- [ ] Assembly trace and a bounded performance benchmark.

## Phase 3 — compaction

- [ ] Episode segmenter.
- [ ] Exact-anchor extractor.
- [ ] Schema-constrained compiler.
- [ ] Capsule merger and decision-conflict resolver.
- [ ] Dependency graph.
- [ ] Loss auditor.

## Phase 4 — retrieval and recovery

- [ ] Hybrid retrieval.
- [ ] Query-reference classifier.
- [ ] Resolution selector.
- [ ] Evidence source trace.
- [ ] Tool-artifact retrieval.

## Phase 5 — integration and release

- [ ] AstrBot host-probe coverage.
- [ ] Custom-compressor fail-safe evidence.
- [ ] Sylanne adapter wiring only after stable public API evidence.
- [ ] Commands / Web Inspector with bounded operator authority.
- [ ] Million-token benchmark only after a reproducible protocol is approved.
- [ ] Non-blocking demonstration with a recorded test boundary.
- [ ] Crash and decision-reversal demonstrations.
