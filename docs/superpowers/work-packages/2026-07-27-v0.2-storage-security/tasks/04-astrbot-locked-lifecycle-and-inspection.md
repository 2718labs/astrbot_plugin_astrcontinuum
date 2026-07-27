# S4 AstrBot Locked Lifecycle and Inspection

Owner: primary-codex
Depends on: S3

## Goal

Authenticate and activate encrypted storage before constructing any live Repository, latch
all startup or runtime authentication failures into a request-safe `LOCKED` lifecycle state,
and give administrators content-free proof that AstrContinuum is active for the current
AstrBot conversation.

## Context

- `../contracts/crypto-storage.md`
- `docs/superpowers/specs/2026-07-27-v0.2-storage-security-design.md` sections 6, 10, 11, and 13
- 2718lab AstrBot API reference sections 2, 4, 10, and 14
- S3 commit `7178226`

## Write Scope

- `main.py`
- `_conf_schema.json`
- `astrcontinuum/adapters/astrbot.py`
- `astrcontinuum/adapters/__init__.py`
- `astrcontinuum/compaction/worker.py`
- AstrBot adapter, lifecycle, worker, integration, and configuration tests

## Steps

- [x] **Step 1: Write locked-lifecycle and operator-visibility tests**

Cover missing/default environment keys, explicit local mode, authenticated startup, one-shot
lock latching, retry only after terminate/reload, partial worker-start cleanup, runtime
authentication failure, `/context_status`, `/context_inspect`, and strict secret-free output.

RED evidence: collection failed because `extract_host_session_key` did not exist; the existing
production constructor also lacked the mandatory Repository codec.

- [x] **Step 2: Share the documented AstrBot SessionKey boundary**

Add one adapter function that builds the exact seven-field `SessionKey` from documented
`AstrMessageEvent.message_obj`, public event getters, and the current Conversation. Both the
LLM Hook and inspection command use this function, so identity semantics cannot drift.

- [x] **Step 3: Activate storage before Repository construction**

Resolve the configured key source before creating the SQLite factory, run atomic security
activation, require `ACTIVE`, then construct the Repository with the authenticated codec.
Construct and probe every fallible runtime component before starting the worker, and publish
component references only after startup succeeds.

- [x] **Step 4: Latch request-safe locked state**

Missing, wrong, unsafe, malformed, migration, rekey, scrub, and unexpected startup failures
leave bridge, worker, provider registry, and projection capability unavailable. Initialization
is marked complete so live requests do not retry; native AstrBot processing continues. Only a
terminate/reload permits another startup attempt.

- [x] **Step 5: Make authentication failure globally fatal**

Hooks catch `StorageSecurityError` before generic failures, remove the live bridge, stop the
worker, and publish the stable security code. The worker never persists authentication
failure as a retryable compaction error; it invokes a fatal callback and exits. Request
preflight authenticates the existing current-session view before the next Journal write.

- [x] **Step 6: Add safe status and current-session inspection**

`/context_status` reports protection, envelope format, key source, non-secret key id,
maintenance state, security code, worker state, and global counts only while storage is
available. `/context_inspect` uses the public Conversation manager with a double-CID race
check, then reports only Snapshot suffix/version, coverage, HWM, Delta range, typed counts,
Capsule slots, pending state, and retry code.

- [x] **Step 7: Add configuration UX**

The WebUI selects `environment`, external `file`, or explicit degraded `local` mode. It
accepts external file paths but never key text. Hints name the environment variables, explain
locked behavior, and warn administrators not to paste keys into WebUI, chat, logs, or process
arguments.

- [x] **Step 8: Verify GREEN**

Focused S4 tests: `30 passed`.

Complete suite: `448 passed, 1 skipped`.

Ruff: passed. Mypy: no issues in 44 source files. Strict JSON parse: passed.

## Acceptance

- Default missing key creates no SQLite database and leaves every live Hook fail-open.
- Storage activation completes before Repository/worker construction.
- Runtime authentication failure is non-retryable and globally locks the plugin boundary.
- Locked status never queries Repository counts.
- Inspection never creates or switches an AstrBot conversation and detects concurrent CID
  changes.
- Status, logs, and inspection contain no message text, raw identity, Provider id, key path,
  key material, ciphertext, exception text, or object representation.

## Return

Report lifecycle/security evidence and commit SHA, update `../index.md`, then release S5 for
version, documentation, Logo, archive, and PR preparation.
