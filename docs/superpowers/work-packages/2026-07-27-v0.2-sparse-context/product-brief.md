# AstrContinuum v0.2 Sparse Context Engine

## Goal

Deliver v0.2.0 with a verified sparse dependency-graph engine that uses exact structured
history, explicit constraints, query activation, state reduction, and adaptive recovery to
assemble bounded model context without rewriting AstrBot conversation history.

## Scope

Included:

- finish the existing encrypted storage and lifecycle closure;
- build one shared graph engine for background candidate validation and live request selection;
- use a small model only for closed extractive structure backed by exact source spans;
- provide Active, Shadow, and Off modes with deterministic fallback;
- expose content-free effectiveness metrics;
- run security, correctness, performance, packaging, and release verification;
- publish the provided byte-identical `logo.png`;
- prepare a feature-branch PR without pushing before user review.

Excluded:

- changing AstrBot's native conversation persistence;
- free-form or narrative conversation summaries;
- unbounded external indexes or vector databases;
- persistent plaintext or process-global graph caches;
- destructive administration commands;
- remote push, PR creation, tag, release, or market submission before explicit user approval.

## Direction

The Journal remains authoritative. Background compilation produces exact-source Capsules and
must pass the same graph validation used by the live path. The live path builds one request-local
graph from Snapshot, Delta, and the current query; solves a bounded constrained activation
problem; checks an error certificate; and restores omitted source blocks when required.

NumPy provides the required native numerical backend. SciPy may accelerate sparse systems when
already installed, while a tiny pure-Python backend provides a portable reference and failure
fallback. No third-party binary is included in the plugin archive.

Public names use computer-science and numerical-computing terminology only.

## Risk Gate

Stop for user approval before any remote write, PR creation, merge, tag, release, market
submission, destructive database operation, or disclosure of private source material.

## Done

v0.2.0 is done when the complete request path works in Active mode, injected failures fall back
safely, hard constraints and provenance have zero omissions in the declared fixtures, full
quality gates pass, the whitelist archive is below 16 MiB, all commits use Ayleovelle's configured
identity, and the final diff/package are presented for review.
