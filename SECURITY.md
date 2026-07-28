# Security Policy

English | [简体中文](#简体中文)

AstrContinuum stores conversation content and participates in provider-request assembly. Treat
database disclosure, cross-session leakage, projection persistence, identity collisions,
unsafe migrations, path traversal, unbounded tool metadata, and secret-bearing logs as
security-sensitive.

## Supported versions

| Version | Support level |
| --- | --- |
| Repository `main` / `v0.2.1` | Security fixes and responsible reports accepted |
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

## 简体中文

AstrContinuum 会保存会话内容并参与 Provider 请求装配。数据库泄露、跨会话串读、临时投影被
错误持久化、身份碰撞、不安全迁移、路径穿越、无限工具元数据和含密钥日志均属于安全问题。

当前支持仓库 `main` / `v0.2.1`；旧快照和无人维护的 fork 不在支持范围内。AstrBot 市场
分发是独立维护者流程，不影响此处的安全报告范围。

请使用
[GitHub 私密安全公告表单](https://github.com/2718labs/astrbot_plugin_astrcontinuum/security/advisories/new)
报告漏洞，不要在补丁完成前公开 Issue。请使用合成数据，绝不要上传真实数据库、访问令牌、
Provider 密钥、用户身份或私聊内容。

Journal 可能包含完整用户/助手文本；会话派生值与规范 token 指标使用 AES-256-GCM 认证
信封，部署者仍必须保护 AstrBot 数据目录、备份、宿主权限和外部密钥。离线 tokenizer
资产按大小与 SHA-256 固定，运行时不得访问 tokenizer 网络或可变下载缓存；日志不得包含
消息正文、模型身份、资产路径、对象表示或密钥。
