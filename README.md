# AstrContinuum / 星续

**Non-blocking Infinite Context Runtime for AstrBot**

> 上下文会被压缩，对话不会断裂。

AstrContinuum 面向持续聊天与长期 Agent 任务。它将无限增长的历史编译为稳定状态、多分辨率胶囊、精确锚点、未压缩增量和可回溯证据，再按当前请求动态装配进有限窗口。

## v0.3.0 技术预览定位

v0.3.0 是“持久化发布安全与不可变重组账本”的 Technical Preview：它提供受 fencing 保护的 SQLite 发布、不可变 Capsule/Snapshot，以及供显式发布调用写入的重组审计账本；传入的非摘要 `released` 记录始终受永久质量闸门约束。

标准 `CompactionWorker` 当前未产生或传入重组记录，因此正常后台压缩可以发布空账本；把重组器接入该 worker 是后续工作，不是本预览的已交付能力。它不是公开 v1.0，也不声明完整 AstrBot 兼容矩阵、百万 Token 性能验收或全部 Provider 行为。数据迁移版本和 Capsule `schema_version` 是独立契约，不能由包版本推导；升级已有数据库时必须运行迁移计划。

## 核心能力

- Shadow Compaction：后台影子压缩，主聊天从不等待
- Stable Snapshot + Delta：稳定快照与未压缩增量叠加
- Multi-Resolution Context Tree：原文、微胶囊、情节、任务、全局状态
- Exact Anchors：名称、数字、路径、代码、强约束等不可丢失
- Query-Aware Reconstruction：按当前问题恢复必要历史
- Loss Auditor：候选快照通过审计后才提交
- Time Travel：快照查看、差异与回滚
- Standalone + Sylanne Adapter：独立运行，可选利用 Sylanne 长期记忆

## 开发入口

依次阅读：

1. `START_HERE.md`
2. `CODEX_MASTER_PROMPT.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DATA_FLOW.md`
5. `docs/CONCURRENCY_STATE_MACHINE.md`
6. `docs/DATABASE_SCHEMA.md`
7. `docs/ADR-008-REORGANIZATION-LEDGER.md`
8. `docs/EVALUATION.md`

## 边界

Sylanne 管理“长期什么仍然有意义”。

AstrContinuum 管理“这一轮模型具体看到什么”。
