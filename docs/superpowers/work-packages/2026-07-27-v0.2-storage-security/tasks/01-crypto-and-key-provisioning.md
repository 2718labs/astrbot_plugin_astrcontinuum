# S1 Crypto and Key Provisioning

Owner: primary-codex
Depends on: none

## Goal

Provide a closed, bounded AES-256-GCM codec, safe environment/file/local key resolution, and
an offline key-file helper without exposing raw key material.

## Context

- `../contracts/crypto-storage.md`
- `docs/superpowers/specs/2026-07-27-v0.2-storage-security-design.md` sections 3 and 5
- Official `cryptography` AESGCM contract: 256-bit key, fresh 12-byte nonce, 16-byte appended
  tag, identical AAD for decrypt, and `InvalidTag` on key/nonce/AAD/ciphertext mismatch.

## Write Scope

- `astrcontinuum/storage/crypto.py`
- `astrcontinuum/storage/security.py`
- `astrcontinuum/keyctl.py`
- `astrcontinuum/storage/__init__.py`
- `astrcontinuum/__init__.py`
- `tests/storage/test_crypto.py`
- `tests/storage/test_security_keys.py`
- `tests/test_keyctl.py`
- `pyproject.toml`
- `requirements.txt`
- `uv.lock`

## Steps

- [x] **Step 1: Write envelope tests**

Create `tests/storage/test_crypto.py` with a fixed 32-byte key and assertions equivalent to:

```python
codec = SecureCodec(bytes(range(32)))
first = codec.encrypt_text("journal_events", "content", "evt:1", "中文")
second = codec.encrypt_text("journal_events", "content", "evt:1", "中文")
assert first != second
assert codec.decrypt_text("journal_events", "content", "evt:1", first) == "中文"
with pytest.raises(StorageSecurityError) as raised:
    codec.decrypt_text("journal_events", "content", "evt:2", first)
assert raised.value.code is SecurityErrorCode.STORAGE_AUTHENTICATION_FAILED
assert "中文" not in str(raised.value)
```

Cover empty/max-bound text, wrong key/AAD, cross-row and cross-column swaps, modified tag,
unknown version/key id, padded or non-canonical base64url, oversized input, and exact
object/array sentinels.

- [x] **Step 2: Verify envelope RED**

Run:

```powershell
uv run --frozen --extra dev pytest tests/storage/test_crypto.py -q
```

Expected: collection fails because `astrcontinuum.storage.crypto` does not exist.

- [x] **Step 3: Add dependency and minimal codec**

Run `uv add "cryptography>=42"` so `pyproject.toml` and `uv.lock` move together, then add the
same lower bound to `requirements.txt`. Implement the exact contract using
`AESGCM.encrypt/decrypt`, `secrets.token_bytes(12)`, strict split/count checks, canonical
unpadded base64url helpers, length-prefixed AAD, and `InvalidTag` translation without
exception chaining.

- [x] **Step 4: Verify envelope GREEN**

Run the Step 2 command. Expected: all tests in `test_crypto.py` pass.

- [x] **Step 5: Write key-source tests**

Create `tests/storage/test_security_keys.py` covering canonical environment input, missing and
malformed variables, distinct previous key, bounded external files, path/symlink escape,
non-regular files, explicit local creation, no overwrite, and no fallback. Assert repr and
exceptions do not contain raw key text or unsafe paths.

- [x] **Step 6: Verify key-source RED**

Run:

```powershell
uv run --frozen --extra dev pytest tests/storage/test_security_keys.py -q
```

Expected: import or attribute failure for missing resolver types.

- [x] **Step 7: Implement key-source resolution**

Add frozen `KeyMaterial`, `ResolvedKeyMaterial`, and `KeySource` types in `security.py`.
Implement environment/file/local branches with explicit dispatch, bounded regular-file
reads, `Path.resolve()` boundary checks, `os.open(..., O_CREAT | O_EXCL, 0o600)`, best-effort
owner-only chmod, and stable content-free errors. Never silently select another source.

- [x] **Step 8: Verify key-source GREEN**

Run the Step 6 command. Expected: all key-source tests pass.

- [x] **Step 9: Write offline-helper tests**

Create `tests/test_keyctl.py`. Call `keyctl.main([...])` and assert `generate` exclusively
creates a canonical key file, `fingerprint` returns the same non-secret id, a second generate
does not overwrite, stdout/stderr never contain the raw key, and no raw-key CLI option is
accepted.

- [x] **Step 10: Verify helper RED**

Run:

```powershell
uv run --frozen --extra dev pytest tests/test_keyctl.py -q
```

Expected: collection fails because `astrcontinuum.keyctl` does not exist.

- [x] **Step 11: Implement offline helper**

Use `argparse` subcommands `generate --output [--data-dir]` and
`fingerprint --key-file`. Reuse the runtime parser and exclusive writer; print only key id,
destination path, and a permission warning flag. Return nonzero with a stable code on error.

- [x] **Step 12: Run S1 regression and commit**

Run:

```powershell
uv lock --check
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev mypy astrcontinuum main.py
uv run --frozen --extra dev pytest -q
```

Expected: zero failures. Commit only S1 scope with:

```text
feat: add encrypted storage codec and key provisioning
```

Verify both author and committer are
`Ayleovelle <273111507+Ayleovelle@users.noreply.github.com>`.

## Acceptance

- Every test was observed failing for the missing behavior before implementation.
- All crypto failures expose only a stable code.
- No source, output, log, exception, or tracked test artifact contains a generated raw key.
- Full existing regression remains green.

## Return

Report changed files, RED/GREEN commands and outputs, dependency resolution, commit SHA and
identity, then update `../index.md` and create only the now-ready S2 task card.
