# S3 Encrypted Repository Boundary

Owner: primary-codex
Depends on: S2

## Goal

Require an authenticated codec for every `SQLiteRepository`, encrypt every protected SQL
parameter immediately before write, decrypt immediately after read, and preserve all existing
domain, idempotency, CAS, fencing, publication, and crash-atomicity behavior.

## Context

- `../contracts/crypto-storage.md`
- `docs/superpowers/specs/2026-07-27-v0.2-storage-security-design.md` sections 4 and 9
- Project-index snapshot
  `sha256:2cfa43c3e7c1a044be288da1af5934c6baefa18261273cd0674033b3997d0814`
- Project-index trace
  `sha256:8ea88ee6f105d303171e569ab5314d3b22bb0cd4c24d102c16b741586cea3b96`

## Write Scope

- `astrcontinuum/storage/repository.py`
- `tests/storage/test_repository_encryption.py`
- `tests/storage/*repository*.py`
- `tests/storage/test_compiler_publication.py`
- `tests/test_compaction_worker.py`
- `tests/test_runtime_read_view.py`
- `tests/storage/security_testkit.py`

## Steps

- [x] **Step 1: Write repository encryption tests**

Assert construction without a `SecureCodec` fails, then activate a fresh database and cover
Session creation, all four Journal mappings, idempotent replay, Capsule publication, Snapshot
publication, request view, compaction view, job reconstruction, and raw SQL representation.
Every protected value must be an envelope/sentinel with the active key id, while returned
domain objects remain byte-for-byte equivalent to caller inputs.

- [x] **Step 2: Verify repository RED**

Run:

```powershell
uv run --frozen --extra dev pytest tests/storage/test_repository_encryption.py -q
```

Expected: constructor/API assertions fail because the repository does not require or use a
codec.

- [x] **Step 3: Require codec and encrypt writes**

Make `codec` a required keyword-only constructor argument with no production default. Encrypt
Session canonical JSON and identity components, Journal content, Capsule canonical JSON, and
Snapshot anchors/rendered context/audit outcome using the exact table, column, and primary-key
AAD contract. Preserve nullable Session identity components as SQL `NULL`.

- [x] **Step 4: Decrypt reads and close errors**

Convert row reconstruction helpers that need the codec into instance methods. Authenticate
and decrypt at row boundaries before Pydantic/domain reconstruction. Never chain a Pydantic,
SQLite, crypto, ciphertext, or decrypted-input exception that could expose protected values.
Avoid repeatedly decrypting the same SessionKey for every event in one bounded view.

- [x] **Step 5: Update deterministic test composition**

Add a test-only fixed key helper that activates storage and constructs the repository
explicitly. Route every existing repository/worker/runtime test through it; tests that
deliberately exercise the legacy plaintext schema seed v0.1 rows directly without exposing a
plaintext production repository.

- [x] **Step 6: Verify all repository invariants GREEN**

Run all storage, compaction-worker, and runtime-view suites. Inspect raw SQLite values and
assert plaintext synthetic markers do not appear in DB/WAL/SHM after startup scrub.

Evidence: `190 passed, 1 skipped`; the focused encrypted-boundary and migration set reports
`19 passed`.

- [x] **Step 7: Run S3 quality gates**

Run lock, Ruff, mypy, focused tests, and the complete suite once S4 has updated the sole
production constructor call in `main.py`.

Evidence after S4 constructor wiring: Ruff passed, mypy passed for all 44 source files, and
the complete suite reported `448 passed, 1 skipped`.

- [x] **Step 8: Commit S3**

Committed the repository boundary and its tests as:

```text
7178226 feat: encrypt repository persistence boundary
```

Both author and committer are
`Ayleovelle <273111507+Ayleovelle@users.noreply.github.com>`.

## Acceptance

- No repository can be constructed without an explicit valid `SecureCodec`.
- No protected plaintext reaches a SQL parameter or survives a successful repository write.
- Every protected read authenticates table, column, and primary-key AAD before domain parsing.
- Existing transaction and immutable-domain behavior is unchanged through encryption.
- Corrupt/tampered protected values fail with content-free public errors.
- Live calls perform only bounded serialization and local AES-GCM work.

## Return

Report RED/GREEN evidence, all protected touchpoints, regression totals, commit SHA and
identity, then update `../index.md` and release S4.
