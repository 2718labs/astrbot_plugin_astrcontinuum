# Verification Contract

## Test Order

Every behavior-changing production edit requires:

1. a minimal test for one behavior;
2. a real run showing the intended missing-behavior failure;
3. the minimal implementation;
4. a focused green run;
5. affected old tests;
6. the relevant integration or real-host path.

Tests that error because of syntax, fixture, import, or dependency failure do not count as
RED evidence.

## Required Evidence Layers

- closed wire and permanent-validator unit tests;
- real SQLite multi-connection tests;
- crash injection at every atomic boundary;
- real AstrBot 4.24.2, 4.24.5, and 4.26.7 load/Hook/save tests;
- standalone and Sylanne-enhanced end-to-end tests;
- privacy scans of AC-owned database, files, logs, hashes, and evidence;
- million-token, delayed compiler, killed worker, supersession, and anchor scenarios;
- DevKit plugin, Python, release, lint, type, and full pytest checks.

## Strict Index Gate

A strict task follows:

```text
input project_index_sync
strict task registration
lease-scoped input project_index_query
worktree_checkpoint_create
writes and tests
project_index_sync bind_as output
lease-scoped output project_index_query
verification artifact registration with output snapshot
workflow completion under the same fencing epoch
```

Miss escape is forbidden for input/output verification. `INDEX_PARTIAL` is acceptable only
when required paths are present and remaining gaps are documented extractor limitations.

## Completion Language

No task is called complete without fresh command output. No public `v1.0.0` is claimed or
written into release metadata until the full acceptance matrix passes.
