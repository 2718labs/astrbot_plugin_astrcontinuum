# Crypto and Storage Contract

## Key and Envelope

- Raw keys are exactly 32 uniformly random bytes.
- External text is canonical unpadded base64url and decodes to exactly 32 bytes.
- `key_id = sha256(raw_key).hexdigest()[:16]`.
- Envelope: `acenc:v1:<key-id>:<canonical-base64url-12-byte-nonce>:<canonical-base64url-ciphertext-and-16-byte-tag>`.
- AAD is the sequence `astrcontinuum`, `envelope-v1`, table, column, primary-key, each encoded
  as a four-byte unsigned big-endian length followed by UTF-8 bytes.
- Maximum plaintext and envelope sizes are checked before allocation-heavy decode/decrypt.

## Public Python Interface

```python
class SecurityErrorCode(str, Enum):
    STORAGE_KEY_MISSING = "STORAGE_KEY_MISSING"
    STORAGE_KEY_INVALID = "STORAGE_KEY_INVALID"
    STORAGE_KEY_FILE_UNSAFE = "STORAGE_KEY_FILE_UNSAFE"
    STORAGE_LOCAL_KEY_CREATE_FAILED = "STORAGE_LOCAL_KEY_CREATE_FAILED"
    STORAGE_KEY_MISMATCH = "STORAGE_KEY_MISMATCH"
    STORAGE_PREVIOUS_KEY_REQUIRED = "STORAGE_PREVIOUS_KEY_REQUIRED"
    STORAGE_ENVELOPE_INVALID = "STORAGE_ENVELOPE_INVALID"
    STORAGE_AUTHENTICATION_FAILED = "STORAGE_AUTHENTICATION_FAILED"
    STORAGE_LEGACY_VALIDATION_FAILED = "STORAGE_LEGACY_VALIDATION_FAILED"
    STORAGE_MIGRATION_FAILED = "STORAGE_MIGRATION_FAILED"
    STORAGE_REKEY_FAILED = "STORAGE_REKEY_FAILED"
    STORAGE_SCRUB_FAILED = "STORAGE_SCRUB_FAILED"
```

```python
class SecureCodec:
    key_id: str

    def encrypt_text(self, table: str, column: str, record_key: str, plaintext: str) -> str: ...
    def decrypt_text(self, table: str, column: str, record_key: str, envelope: str) -> str: ...
    def encrypt_object_json(
        self, table: str, column: str, record_key: str, canonical_json: str
    ) -> str: ...
    def decrypt_object_json(
        self, table: str, column: str, record_key: str, sentinel_json: str
    ) -> str: ...
    def encrypt_array_json(
        self, table: str, column: str, record_key: str, canonical_json: str
    ) -> str: ...
    def decrypt_array_json(
        self, table: str, column: str, record_key: str, sentinel_json: str
    ) -> str: ...
```

The object sentinel is exactly `{"$astrcontinuum_encrypted":"<envelope>"}` and the array
sentinel is exactly `["$astrcontinuum_encrypted","<envelope>"]`. Parsers reject extra keys,
wrong lengths, non-string envelope values, and non-canonical JSON shapes.

## Error and Secret Boundary

`StorageSecurityError` exposes only one stable `SecurityErrorCode`. Its string and repr do
not include raw keys, key text, paths, ciphertext, nonce, tags, plaintext, SQLite values, or
chained third-party exception messages.

`KeyMaterial.raw_key` is excluded from repr and equality output. Runtime logs and chat
commands may expose only `key_id`, source mode, degradation state, stage, and stable code.

## Key Sources

`resolve_key_material(config, data_dir, environ=None)` returns active and optional previous
`KeyMaterial`, selected source, and `local_degraded`.

- `environment`: active `ASTRCONTINUUM_MASTER_KEY`; optional
  `ASTRCONTINUUM_PREVIOUS_KEY`.
- `file`: bounded regular files resolved outside `data_dir`; optional final newline only.
- `local`: exclusive-create `data_dir/astrcontinuum.key`, never overwrite or fall back.
- Active and previous keys must be distinct.

## Performance Boundary

Live repository calls perform only canonical serialization plus one AESGCM call per protected
scalar. Key resolution, schema transformation, rekey, checkpoint truncation, secure-delete
scrub, and `VACUUM` are forbidden after repository construction and worker startup.
