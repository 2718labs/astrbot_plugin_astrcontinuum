# Changelog

English | [简体中文](#简体中文)

All notable changes to AstrContinuum are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v0.1.0 — 2026-07-27

Repository-stage technical preview. This version is available from the source repository for
architecture review and controlled compatibility testing. It has not been submitted to the
AstrBot plugin market, tagged as a stable release, or published as a GitHub Release.

### Added

- Append-only SQLite Journal for authoritative user, assistant, tool-call, and tool-result
  events.
- Canonical seven-component session identity with deterministic hashing and per-session
  contiguous event sequences.
- Committed Snapshot plus contiguous Delta read model with a logical `EMPTY_BASE`.
- Deterministic retrieval and normal/emergency budget assembly.
- Reversible, provider-only context projection with exact native-object identity restoration.
- Durable compaction-intent, lease, fencing, retry, audit, and atomic-publication primitives.
- Immutable Capsule and Snapshot contracts, closed schemas, permanent mechanical validation,
  and optional semantic-audit interfaces.
- AstrBot hook bridge, administrator-only `/context_status` command, WebUI configuration schema, and fail-open
  compatibility probes.
- Unit, integration, concurrency, crash-atomicity, recovery, and real AstrBot lifecycle tests.
- English-first README and architecture documentation with complete Simplified Chinese mirrors.

### Changed

- Public metadata now identifies the `2718labs` organization repository and version `v0.1.0`.
- WebUI configuration exposes only settings that are connected to the current plugin lifecycle.
- Budget documentation now states explicitly that `v0.1.0` counts UTF-8 bytes rather than
  provider tokenizer tokens.
- Repository governance is based on the DBJD-CR AstrBot hello-world template and adapted for
  AstrContinuum's persistence and architecture risks.

### Fixed

- Temporary AstrContinuum-owned provider messages are excluded from AstrBot persistence and
  removed before finalization without replacing the host's native message objects.
- Duplicate authoritative callbacks converge through deterministic idempotency keys and
  database uniqueness constraints.
- Losing Snapshot publications roll back candidate content while preserving the winning active
  pointer and durable job outcome.

### Known limitations

- The background compaction worker is implemented as core primitives but is not started by the
  `Star` lifecycle. Compaction intent can remain pending.
- Provider-backed semantic auditing is not connected to an AstrBot provider.
- The budget counter is conservative UTF-8 byte length, not an exact tokenizer.
- No rollback/time-travel administration UI is exposed.
- No platform adapter is claimed in `metadata.yaml` until adapter-specific evidence exists.
- This technical preview must not be treated as the sole production mechanism for permanent
  long-context compaction.

## 简体中文

本文件记录 AstrContinuum 的重要变更，格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

### v0.1.0 — 2026-07-27

这是仓库阶段技术预览版，仅用于架构评审和受控兼容性测试。当前没有提交 AstrBot
插件市场，也不作为稳定标签或 GitHub Release 发布。

#### 新增

- 权威用户、助手、工具调用和工具结果事件的只追加 SQLite Journal。
- 七元会话身份、稳定哈希和会话内连续事件序号。
- 已提交 Snapshot 加连续 Delta 的读取模型，以及逻辑 `EMPTY_BASE`。
- 确定性检索、普通预算装配与紧急装配。
- 仅作用于 Provider 请求的临时上下文投影，以及按对象身份精确恢复。
- 压缩意图、租约、fencing、重试、审计与原子发布核心。
- 不可变 Capsule/Snapshot 契约、闭合 Schema、永久机械校验和可选语义审计接口。
- AstrBot Hook 桥接、仅管理员可用的 `/context_status`、WebUI 配置及 fail-open 兼容探测。
- 单元、集成、并发、崩溃原子性、恢复及真实 AstrBot 生命周期测试。
- 英文优先的 README 与架构文档，以及可点击跳转的完整简体中文镜像。

#### 变更

- 公开元数据更新为 `2718labs` 组织仓库与 `v0.1.0`。
- WebUI 仅暴露已经接入当前插件生命周期的配置项。
- 明确说明当前预算单位是 UTF-8 字节，而不是模型 tokenizer Token。
- 参考 DBJD-CR AstrBot hello-world 模板建立仓库治理，并针对持久化与架构风险加严。

#### 已知限制

- 后台压缩 worker 的核心能力已经实现，但 `Star` 生命周期尚未启动 worker。
- Provider 语义审计尚未接入。
- 预算计数仍是保守的 UTF-8 字节长度。
- 尚未暴露回滚/时间旅行管理界面。
- 在取得适配器专项证据前，`metadata.yaml` 不宣称支持具体平台。
- 当前技术预览不能作为生产环境唯一的永久长上下文压缩机制。
