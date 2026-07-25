# ADR-006: AstrBot Hook Ownership and Adapter Boundary

- Status: Accepted for Phase 0
- Verified AstrBot observations: v4.24.5 (`c77cb0f4e2c84fa3cf8d9b4704e49a38841d0b70`) through v4.26.7 (`fed29848ca0b3912ab6a8200a10cd0f2cb080f85`)

## Unique Writers

`on_llm_request` is the sole authoritative writer of current user input to `journal_events`. It MUST derive the canonical SessionKey, execute idempotent `TX_CAPTURE_USER_EVENT`, and read committed request state. It MUST NOT await compiler, auditor, scheduler completion, or a remote model call. Compaction notification from this path MUST be a bounded durable intent update, not synchronous compaction.

`on_agent_done` is the sole authoritative writer of completed assistant output. It MUST execute idempotent `TX_CAPTURE_ASSISTANT_EVENT` only after the agent completion is authoritative.

`on_llm_response` is observational. It MAY collect metrics or redacted diagnostics, but MUST NOT append user or assistant content to `journal_events` and MUST NOT advance coverage.

Tool lifecycle facts have separate unique writers: `on_using_llm_tool` writes `TOOL_CALL/TOOL/ON_USING_LLM_TOOL` and `on_llm_tool_respond` writes `TOOL_RESULT/TOOL/ON_LLM_TOOL_RESPOND`. The user and assistant mappings are `USER_MESSAGE/USER/ON_LLM_REQUEST` and `ASSISTANT_MESSAGE/ASSISTANT/ON_AGENT_DONE`. No callback may write another hook's mapped combination, and `ON_LLM_RESPONSE` is not an allowed Journal `source_hook`. External Sylanne memory text MUST NOT be copied into the Journal; `persona_id` is SessionKey metadata only.

## Identity and Idempotency

Every hook MUST use the same canonical SessionKey tuple, in this order: `platform_instance_id`, `message_type`, `session_id`, `group_id` or null, `user_id`, `conversation_id`, and `persona_id` or null. Its canonical serialization MUST be hashed with SHA-256 to produce `session_key_hash`.

Each authoritative write MUST carry a deterministic `idempotency_key` derived from stable host delivery or completion identity, not process-local time. The database uniqueness constraint on `(session_key_hash, source_hook, idempotency_key)` MUST make repeated callbacks return the existing event without allocating a new sequence. Concurrent plugin runners MUST rely on this database constraint and transaction isolation; an in-memory lock MUST NOT be the correctness boundary.

The `(session_key_hash, sequence)` uniqueness constraint and sequence allocation in the capture transaction MUST produce one monotonic order per session. Duplicate runners MUST NOT create two active Snapshots or bypass job fencing.

## Request-path Behavior

After idempotent user capture, `on_llm_request` MUST assemble a view using `TX_READ_REQUEST_VIEW`: exactly one committed active Snapshot plus Journal events above its coverage end and no greater than the transaction high-water mark. `EMERGENCY_ASSEMBLY` MAY reduce injected prompt material, but MUST NOT delete Journal rows or advance Snapshot coverage.

Hook errors in optional enhancement logic MUST fail open for the host request: the request proceeds without enhanced context and a redacted error is recorded. Fail-open behavior MUST NOT fabricate a successful Journal write or mutate coverage.

## Isolated `TextPart` Exception

The verified capability for temporary request context currently requires this internal import:

```python
from astrbot.core.agent.message import TextPart
```

It is used with `ProviderRequest.extra_user_content_parts`; `TextPart.mark_as_temp()` marks injected context as temporary. No verified `astrbot.api.*` equivalent is asserted.

Evidence differs on the lower bound: an official tag first confirms `mark_as_temp()` in v4.24.1, while the DevKit reference lists v4.24.2. The supported lower bound MUST therefore conservatively remain AstrBot `>=4.24.2` until a real loading matrix validates v4.24.1. The path is observed through v4.26.7; this is an evidence upper bound, not a maximum-version compatibility cap, and the ADR MUST NOT claim compatibility with untested later versions.

This exception MUST be isolated in the AstrBot adapter module. The adapter MUST perform a version/capability check for the import, `extra_user_content_parts`, and `mark_as_temp`. If import or attribute lookup fails, it MUST fail open by skipping enhanced-context injection and recording a redacted compatibility error. Core persistence, compiler, and scheduling code MUST NOT import `astrbot.core.*` or depend on `TextPart`.

The exception is version-checked technical debt. A public equivalent, once verified, MUST replace it behind the adapter without changing Journal, Snapshot, or job contracts. This decision does not authorize invention of a public tokenizer API or Sylanne API.

## Evidence

The current official-source index evidence is AstrBot workspace snapshot `sha256:4892d3ed5e94ee1fb5366c0320085b3cbb394f8b376af29d7a1953e6c6a88d44` at `fed29848ca0b3912ab6a8200a10cd0f2cb080f85`, hook query trace `sha256:95e4128299b85b4a16ba74ee72a944ad664d3f809b77cd1d1ca311d6d56675aa`, and `ProviderRequest` query trace `sha256:932c9d5ea8ec30eb73a722b1b7bc47f673f15c57ad9fdb3b68e3d716dbec3397`. These traces make this decision reproducible for the indexed source; they MUST NOT be treated as a guarantee for other AstrBot versions.

## Dependency Placement

Packages required when AstrBot loads the plugin belong in root `requirements.txt`. `pyproject.toml` is only for local development and test tooling. A successful local test environment MUST NOT be treated as proof that AstrBot host installation has all runtime dependencies.
