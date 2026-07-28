# Changelog

English | [简体中文](#简体中文)

All notable changes to AstrContinuum are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v0.2.1 — 2026-07-28

Request budgeting now uses immutable, request-local tokenizer profiles while preserving every
existing byte-count compatibility value and identity. The deterministic plugin archive is a
verified release artifact; AstrBot-market distribution is a separate maintainer process.

### Added

- Bundled, digest-pinned `cl100k_base` and `o200k_base` assets for offline ordinary-text BPE,
  together with third-party notices and a request-level UTF-8 byte fallback.
- Three independent token lanes: canonical persisted metrics, the live request profile, and the
  background compaction profile. Concurrent requests never share mutable tokenizer state.
- Encrypted canonical-token sidecars, atomic writes for new artifacts, and bounded, restartable
  backfill for older encrypted artifacts.
- Automatic AstrBot context-window resolution, content-free status fields, real-version probes,
  and deterministic archive build and verification scripts.

### Changed

- Final provider projections are recounted as a complete request with one immutable profile.
  Required blocks, dependency closure, and tool-call/tool-result pairs remain atomic.
- `tiktoken>=0.12,<0.14` is now a release contract shared by package metadata and the AstrBot
  requirements file. Runtime tokenization does not use network or mutable tokenizer caches.
- CI covers Python 3.10 through 3.13, includes Windows Python 3.12, and runs a dedicated isolated
  release-contract job.
- Encryption-key management now defaults to automatic local key creation and reuse. It protects
  a separately disclosed database file; use an external environment secret or file when the key
  must be isolated from the complete AstrBot data volume.

### Fixed

- A tokenizer, asset, model-map, or count failure restarts that request with one BYTE fallback
  profile instead of mixing units.
- Missing canonical metrics delay only background compaction; they do not block native AstrBot
  request handling or fall back to immutable compatibility byte fields.

## v0.2.0 — 2026-07-27

Architectural refactor of the encrypted, pressure-triggered context runtime. The authoritative
Journal and reversible AstrBot boundary remain intact, while request-time selection, verification,
recovery, background publication, and observability are rebuilt as separate fail-open planes.
No remote release or plugin-market submission is implied by this entry.

### Added

- A bounded request-local sparse context engine with `active`, `shadow`, and `off` modes.
  Active may use graph-selected context only after verification; Shadow records content-free
  evidence while sending deterministic fallback; Off skips graph computation.
- Required NumPy numerical baseline and an optional SciPy sparse accelerator without vendored
  third-party binaries.
- Authenticated AES-256-GCM envelopes for conversation-derived durable values, explicit locked
  startup, plaintext migration, atomic rotation, post-maintenance scrub, and an offline key-file
  helper that never prints raw key material.
- Administrator-only `/context_inspect` evidence for the current conversation, alongside expanded
  `/context_status` mode, outcome, storage, worker, and aggregate health.
- Content-free engine evidence covering candidate and selected counts, reduction, bounded residual
  bands, recovery passes, and required/provenance coverage.

### Changed

- The context runtime is now separated into authoritative storage, deterministic fallback,
  request-local verified graph selection, and background candidate-verification planes.
- Public package, plugin metadata, configuration, and documentation now identify `v0.2.0`.
- Context work remains pressure-triggered rather than turn-count-triggered. Background compilation
  accepts only closed structured fields backed by exact source spans.
- The WebUI adds one understandable `context_engine_mode` choice and keeps solver tolerances,
  relation weights, matrix limits, and recovery bounds internal.
- Key provisioning separates host administrators from chat administrators. Environment injection
  remains the default; external files are supported for manual deployments; local convenience
  mode is explicitly reported as degraded.

### Fixed

- Graph, solver, certificate, dependency-closure, or budget-pack failure cannot replace the
  deterministic fallback and is reported as `DEGRADED_RAW`.
- Temporary provider context remains excluded from AstrBot persistence and the authoritative
  Journal remains append-only.
- Rotation requires the authentic previous key and changes protected rows plus the verifier
  atomically; failure locks storage without a partial rewrite.

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
- Context-pressure policy that queues Checkpoints before switching to a bounded Provider View;
  no fixed-turn trigger.
- Reversible, provider-only context projection with exact native-object identity restoration.
- Durable compaction-intent, lease, fencing, retry, audit, and atomic-publication primitives.
- A tracked background worker with frozen-target reads, lease renewal, redacted bounded retries,
  non-blocking wake-up, and lifecycle cancellation.
- An AstrBot-backed extractive compiler that accepts only event ids and verbatim source spans,
  while code owns deterministic identities, rendering, validation, and publication.
- Immutable Capsule and Snapshot contracts, closed schemas, permanent mechanical validation,
  and optional semantic-audit interfaces.
- AstrBot hook bridge, administrator-only `/context_status` command with real runtime counts,
  user-readable WebUI configuration, and fail-open compatibility probes.
- Unit, integration, concurrency, crash-atomicity, recovery, and real AstrBot lifecycle tests.
- English-first README and architecture documentation with complete Simplified Chinese mirrors.

### Changed

- Public metadata now identifies the `2718labs` organization repository and version `v0.1.0`.
- WebUI configuration explains pressure thresholds and can follow the conversation model or
  explicitly select a smaller compaction provider.
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

### v0.2.1 — 2026-07-28

请求预算改为使用不可变、请求局部的 tokenizer profile，同时保持既有字节计数兼容值与
对象标识不变。确定性插件归档是已验证的发布产物；AstrBot 市场分发是独立维护者流程。

#### 新增

- 内置并固定摘要的 `cl100k_base`、`o200k_base` 资产，用于离线普通文本 BPE；同时加入
  第三方声明与请求级 UTF-8 字节回退。
- 三条彼此独立的 token 轨道：持久化规范指标、实时请求 profile、后台归约 profile。
  并发请求之间不共享可变 tokenizer 状态。
- 加密规范 token sidecar、新 artifact 的原子写入，以及对旧加密 artifact 有界、可恢复
  的渐进补齐。
- AstrBot 窗口自动解析、无内容状态字段、真实版本探针，以及确定性归档构建与验证脚本。

#### 变更

- Provider 投影在注入前按一个不可变 profile 对完整请求重新计数；必选块、依赖闭包以及
  工具调用/结果对保持原子。
- `tiktoken>=0.12,<0.14` 成为包元数据与 AstrBot requirements 共享的发布契约；运行时
  tokenizer 不访问网络，也不使用可变 tokenizer 缓存。
- CI 覆盖 Python 3.10 至 3.13，明确包含 Windows Python 3.12，并增加隔离发布契约任务。
- 加密密钥管理默认自动创建并复用本地密钥，可保护单独泄漏的数据库文件；若密钥必须与
  整个 AstrBot data 卷隔离，可改用外部环境密钥或外部文件。

#### 修复

- tokenizer、资产、模型映射或计数失败时，整个请求改用同一个 BYTE 回退 profile 重跑，
  不混合不同单位。
- 规范指标缺失只会延迟后台归约，不阻塞原生 AstrBot 请求，也不会回读不可变兼容字节字段。

### v0.2.0 — 2026-07-27

这是对加密、压力触发上下文运行时的一次架构重构。权威 Journal 与可逆 AstrBot 边界
保持不变，请求时选择、验证、恢复、后台发布和可观测性被重建为相互分离的 fail-open
平面。本条目不表示已经远程发布或提交插件市场。

#### 新增

- 有界、请求局部的稀疏上下文引擎，支持 `active`、`shadow`、`off`。主动模式只有在
  完整校验通过后才使用图选择结果；影子模式记录无内容证据但发送确定性回退结果；
  关闭模式跳过图计算。
- NumPy 必需数值基线，以及不随插件打包第三方二进制的可选 SciPy 稀疏加速器。
- 对话派生持久数据的 AES-256-GCM 认证加密、显式锁定启动、明文迁移、原子换钥、
  维护后清理，以及不会打印原始密钥的离线密钥文件工具。
- 管理员 `/context_inspect` 当前会话证据，并扩展 `/context_status` 的模式、结果、
  存储、后台 worker 与汇总健康信息。
- 候选数、选择数、归约比例、有界残差等级、恢复次数、必需块和来源覆盖等无内容指标。

#### 变更

- 上下文运行时拆分为权威存储、确定性回退、请求内验证图选择与后台候选验证平面。
- 包版本、插件元数据、配置与文档统一为 `v0.2.0`。
- 上下文工作仍按窗口压力触发，不按固定轮数触发；后台编译只接受带逐字来源片段的
  闭合结构字段。
- WebUI 只新增一个易懂的 `context_engine_mode`，不暴露求解容差、关系权重、矩阵上限
  或恢复边界。
- 密钥由宿主机管理员配置，聊天管理员只能看无内容状态。环境变量仍是默认来源，
  手动部署可用外部文件，本机便捷模式会明确显示为较弱保护。

#### 修复

- 图、求解器、证书、依赖闭包或预算装配失败都不能替换确定性回退结果，并报告
  `DEGRADED_RAW`。
- 临时 Provider 上下文仍不会写入 AstrBot 历史，权威 Journal 仍保持只追加。
- 换钥必须提供能认证旧数据库的旧密钥，并原子更新受保护行与校验器；失败时锁定存储，
  不会留下部分换钥状态。

### v0.1.0 — 2026-07-27

这是仓库阶段技术预览版，仅用于架构评审和受控兼容性测试。当前没有提交 AstrBot
插件市场，也不作为稳定标签或 GitHub Release 发布。

#### 新增

- 权威用户、助手、工具调用和工具结果事件的只追加 SQLite Journal。
- 七元会话身份、稳定哈希和会话内连续事件序号。
- 已提交 Snapshot 加连续 Delta 的读取模型，以及逻辑 `EMPTY_BASE`。
- 确定性检索、普通预算装配与紧急装配。
- 依据上下文压力提前排队 Checkpoint、再切换有界 Provider View，不使用固定轮数触发。
- 仅作用于 Provider 请求的临时上下文投影，以及按对象身份精确恢复。
- 压缩意图、租约、fencing、重试、审计与原子发布核心。
- 自动后台 worker，包括冻结目标读取、lease 续租、脱敏有界重试、非阻塞唤醒和生命
  周期取消。
- AstrBot 驱动的精确引文式编译器：模型只返回事件编号与逐字片段，标识、渲染、校验
  和发布由程序负责。
- 不可变 Capsule/Snapshot 契约、闭合 Schema、永久机械校验和可选语义审计接口。
- AstrBot Hook 桥接、带真实运行计数的管理员 `/context_status`、易读 WebUI 配置及
  fail-open 兼容探测。
- 单元、集成、并发、崩溃原子性、恢复及真实 AstrBot 生命周期测试。
- 英文优先的 README 与架构文档，以及可点击跳转的完整简体中文镜像。

#### 变更

- 公开元数据更新为 `2718labs` 组织仓库与 `v0.1.0`。
- WebUI 解释压力阈值，并支持跟随当前对话模型或显式选择小型归约模型。
- 明确说明当前预算单位是 UTF-8 字节，而不是模型 tokenizer Token。
- 参考 DBJD-CR AstrBot hello-world 模板建立仓库治理，并针对持久化与架构风险加严。

#### 已知限制

- Provider 语义审计尚未接入。
- 预算计数仍是保守的 UTF-8 字节长度。
- 尚未暴露回滚/时间旅行管理界面。
- 在取得适配器专项证据前，`metadata.yaml` 不宣称支持具体平台。
- 当前技术预览不能作为生产环境唯一的永久长上下文压缩机制。
