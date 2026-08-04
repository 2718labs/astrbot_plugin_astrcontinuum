# S2 Atomic Storage Activation

Owner: primary-codex
Depends on: S1

## Goal

Activate the secure physical schema before repository construction, atomically transform every
legacy protected value, authenticate the database verifier, rotate keys without partial rows,
and scrub ordinary SQLite plaintext remnants before reporting `ACTIVE`.

## Context

- `../contracts/crypto-storage.md`
- `docs/superpowers/specs/2026-07-27-v0.2-storage-security-design.md` sections 4 and 6–8
- Project-index snapshot
  `sha256:f0e3cb61ad9356d6cd1d790424a4b9108d0ee74e2388ae94db9c8c11d3d0a468`
- Project-index trace
  `sha256:fda5fa64444dcb1eb0d669633bccaff1bccb35b4bfed3102b3a990a3643b20ed`

## Write Scope

- `astrcontinuum/storage/security.py`
- `astrcontinuum/storage/sqlite.py`
- `astrcontinuum/storage/__init__.py`
- `astrcontinuum/__init__.py`
- `tests/storage/test_storage_security.py`
- `tests/storage/test_sqlite_connection_policy.py`

## Steps

- [x] **Step 1: Write fresh and legacy activation tests**

Create `tests/storage/test_storage_security.py`. Build a v0.1 database through
`SQLiteMigrator` and the plaintext repository, then assert:

- fresh activation creates the secure schema, singleton verifier, and `ACTIVE` result;
- every protected scalar is an `acenc:v1` envelope or exact closed JSON sentinel;
- decrypted values are wire-identical to their v0.1 values;
- row counts, foreign keys, indexes, immutable triggers, migration ledger, and
  `user_version` remain valid;
- a unique synthetic plaintext marker is absent from the database, WAL, and SHM after scrub.

- [x] **Step 2: Verify activation RED**

Run:

```powershell
uv run --frozen --extra dev pytest tests/storage/test_storage_security.py -q
```

Expected: import or attribute failure for the missing activation API.

- [x] **Step 3: Add the secure schema and activation state**

Add closed `StorageMaintenanceState`, `StorageSecurityActivation`, and
`activate_storage_security(factory, keys, fault_injector=None)`. Keep migration ledger v1
unchanged. Rebuild only physical tables whose legacy constraints cannot accept encrypted
representations, preserve all relationships, and create exactly one `storage_security` row
with a verifier bound to the singleton record.

- [x] **Step 4: Implement legacy preflight and atomic transform**

Before changing rows, validate the complete migration ledger, `user_version`, foreign keys,
SessionKey identity/hash agreement, Capsule and Snapshot canonical JSON, membership
relationships, and sentinel absence. Under one bounded exclusive transaction:

- encrypt every protected field with table/column/primary-key AAD;
- preserve SQL `NULL` identity values;
- preserve every plaintext wire value only in local variables;
- re-read, decrypt, and compare every transformed value;
- compare table row counts and run `foreign_key_check`;
- insert the verifier and durable `NEEDS_SCRUB` state;
- restore immutable triggers before commit.

Any injected precommit failure must roll back schema, metadata, and rows so the v0.1
repository can still read the original database.

- [x] **Step 5: Verify legacy migration GREEN**

Run the Step 2 command. Expected: fresh and populated legacy activation tests pass.

- [x] **Step 6: Write verifier, rotation, and crash tests**

Add cases for matching active key, wrong active key, absent/wrong previous key, successful
rotation, precommit rotation failure, process interruption after transform commit, malformed
legacy JSON/sentinels, and scrub failure. Assert errors expose only stable codes and never
contain keys, paths, plaintext, verifier, ciphertext, or SQLite values.

- [x] **Step 7: Verify rotation/crash RED**

Run the Step 2 command. Expected: failures identify missing verifier routing, rekey, resume,
or scrub behavior.

- [x] **Step 8: Implement verifier routing and atomic rekey**

When the configured active id matches durable metadata, authenticate the fixed verifier
without reading conversation rows. When it differs, require the supplied previous key to
match and authenticate, then re-encrypt and verify every protected value inside one exclusive
transaction. Change all rows, verifier, active key id, and `NEEDS_SCRUB` atomically. Preserve
the prior key and ciphertext on any precommit failure.

- [x] **Step 9: Implement idempotent startup scrub**

For durable `NEEDS_SCRUB`, run only during startup:

```text
PRAGMA wal_checkpoint(TRUNCATE)
PRAGMA secure_delete = ON
VACUUM
PRAGMA wal_checkpoint(TRUNCATE)
```

Record `ACTIVE` only after all four operations succeed. An interruption keeps
`NEEDS_SCRUB`, and the next activation repeats the scrub before returning a codec.

- [x] **Step 10: Verify S2 GREEN and regressions**

Run:

```powershell
uv run --frozen --extra dev pytest tests/storage/test_storage_security.py -q
uv lock --check
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev mypy astrcontinuum main.py
uv run --frozen --extra dev pytest -q
```

- [x] **Step 11: Commit S2**

Commit only the S2 write scope and work-package updates with:

```text
feat: add atomic encrypted storage activation
```

Verify both author and committer are
`Ayleovelle <273111507+Ayleovelle@users.noreply.github.com>`.

## Acceptance

- No precommit failure leaves a partial schema, metadata row, or transformed value.
- No activation or rekey returns before authenticated verification and scrub complete.
- Missing or wrong key material never causes an AstrContinuum database write.
- Rotation changes all protected ciphertext and preserves exact decrypted wire values.
- The secure schema retains durable correctness, CAS-supporting metadata, and immutable rows.
- No generated key or synthetic plaintext marker is tracked or packaged.

## Return

Report RED/GREEN evidence, protected-column coverage, crash stages, scrub evidence, full
regression, commit SHA and identity, then update `../index.md` and create only the ready S3
task card.
