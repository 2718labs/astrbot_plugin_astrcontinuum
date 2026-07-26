# Security Policy

English | [简体中文](#简体中文)

AstrContinuum stores conversation content and participates in provider-request assembly. Treat
database disclosure, cross-session leakage, projection persistence, identity collisions,
unsafe migrations, path traversal, unbounded tool metadata, and secret-bearing logs as
security-sensitive.

## Supported versions

| Version | Support level |
| --- | --- |
| Repository `main` / `v0.1.0` preview | Security fixes and responsible reports accepted |
| Older snapshots or unmaintained forks | Not supported |

`v0.1.0` is a repository-stage technical preview, not a stable market release.

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
disclosure timing through the private advisory. Exact response times are not guaranteed for this
technical preview.

## Security boundaries

- The local AstrBot operator controls filesystem and plugin installation access.
- AstrContinuum does not encrypt SQLite data at rest.
- Complete user and assistant text can be stored in the Journal.
- External memory payloads are not trusted as Journal sources.
- Private AstrBot projection APIs are capability-probed and fail open if unavailable.
- Logs should contain redacted codes and bounded metadata only.

## 简体中文

AstrContinuum 会保存会话内容并参与 Provider 请求装配。数据库泄露、跨会话串读、临时投影被
错误持久化、身份碰撞、不安全迁移、路径穿越、无限工具元数据和含密钥日志均属于安全问题。

当前只支持仓库 `main` / `v0.1.0` 技术预览；旧快照和无人维护的 fork 不在支持范围内。

请使用
[GitHub 私密安全公告表单](https://github.com/2718labs/astrbot_plugin_astrcontinuum/security/advisories/new)
报告漏洞，不要在补丁完成前公开 Issue。请使用合成数据，绝不要上传真实数据库、访问令牌、
Provider 密钥、用户身份或私聊内容。

本插件默认不对 SQLite 静态数据加密，Journal 可能包含完整用户/助手文本；部署者必须自行保护
AstrBot 数据目录、备份和宿主权限。
