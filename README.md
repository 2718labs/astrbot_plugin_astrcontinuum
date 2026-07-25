# AstrContinuum / 星续

**Non-blocking Infinite Context Runtime for AstrBot**

> 上下文会被压缩，对话不会断裂。

AstrContinuum 面向持续聊天与长期 Agent 任务。它将无限增长的历史编译为稳定状态、多分辨率胶囊、精确锚点、未压缩增量和可回溯证据，再按当前请求动态装配进有限窗口。

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
4. `docs/COMPACTION_PROTOCOL.md`
5. `docs/EVALUATION.md`

## 边界

Sylanne 管理“长期什么仍然有意义”。

AstrContinuum 管理“这一轮模型具体看到什么”。
