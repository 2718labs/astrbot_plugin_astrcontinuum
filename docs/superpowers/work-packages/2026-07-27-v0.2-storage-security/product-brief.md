# AstrContinuum v0.2.0 Storage Security

## Goal

Encrypt durable conversation-derived data before SQLite sees it, while preserving the
existing exact-source compaction loop and native AstrBot fail-open behavior.

## Scope

Included: AES-256-GCM field encryption, external/environment/local key sources, offline key
helper, plaintext-v0.1 migration, key rotation, WAL/free-page scrub, locked startup,
content-free status/inspection, versioning, documentation, tests, and a market-safe archive.

Excluded: SQLCipher, chat/WebUI key submission, remote key escrow, destructive admin
commands, protection from a hostile AstrBot process, and recovery without the retained key.

## Direction

The default environment mode locks until a valid external key is present. Every repository
is constructed with an explicit secure codec. Storage migration and rekey occur only during
startup; live hooks perform bounded native-library crypto and keep compaction asynchronous.
Corruption or key mismatch locks the storage boundary rather than becoming empty context.

## Risk Gate

Rewriting encrypted rows and scrubbing WAL/free pages are startup-only and transaction
guarded. Remote push, release creation, tagging, or publishing requires the user's final
diff review and explicit authorization.

## Done

All protected fields round-trip through encryption; legacy migration and rotation are
atomic under injected failures; missing/wrong keys cause no AstrContinuum writes; native
requests continue; status exposes only stable codes; the exact packaged source passes all
quality gates and contains no key, database, WAL, cache, or synthetic plaintext secret.
