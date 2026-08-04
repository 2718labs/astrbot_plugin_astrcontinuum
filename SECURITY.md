# Security Policy

English | [简体中文](./SECURITY.zh-CN.md)

AstrContinuum stores conversation content and participates in provider-request assembly. Treat
database disclosure, cross-session leakage, projection persistence, identity collisions,
unsafe migrations, path traversal, unbounded tool metadata, and secret-bearing logs as
security-sensitive.

## Supported versions

| Version | Support level |
| --- | --- |
| Repository `main` / `v0.3.0` | Security fixes and responsible reports accepted |
| Older snapshots or unmaintained forks | Not supported |

AstrBot-market distribution is a separate maintainer process; security reports are accepted for
the repository version regardless of that distribution process.

## Report a vulnerability

Please use the repository's
[private security advisory form](https://github.com/2718labs/astrbot_plugin_astrcontinuum/security/advisories/new).
Do not open a public issue for an unpatched vulnerability.

Include, when possible:

- affected commit/version and AstrBot version;
- platform, adapter, Python, and operating-system details;
- minimal reproduction without real conversation content or credentials;
- expected and observed security boundary;
- impact on confidentiality, integrity, availability, or cross-session isolation;
- whether the issue is already being exploited or publicly known.

Do not attach real database files, access tokens, provider keys, user identifiers, or private
messages. Use synthetic fixtures and redact paths and logs.

Maintainers will acknowledge a usable report, coordinate reproduction and a fix, and agree on
disclosure timing through the private advisory. Exact response times are not guaranteed.

## Security boundaries

- The local AstrBot operator controls filesystem and plugin installation access.
- Conversation-derived SQLite values and canonical token metrics use authenticated
  AES-256-GCM envelopes; key files and environment-injected keys remain operator secrets.
- Complete user and assistant text can be stored in the Journal.
- Offline tokenizer assets are size- and SHA-256-pinned. Runtime counting must not access the
  tokenizer network or a mutable download cache.
- External memory payloads are not trusted as Journal sources.
- Private AstrBot projection APIs are capability-probed and fail open if unavailable.
- Logs contain redacted codes and bounded metadata only; model identities, tokenizer asset
  paths, message text, object representations, and keys are excluded.
