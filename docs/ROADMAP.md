# 路线图

## Internal Alpha

领域模型、内存 EventStore、Snapshot + Delta、非阻塞 Scheduler、确定性 Assembler、基础测试。

## v0.3.0 Technical Preview（当前）

本里程碑聚焦持久化发布安全：fenced SQLite 事务、不可变 Capsule/Snapshot、schema migration v2、供显式发布调用按 Snapshot 有序保存的重组账本，以及对非摘要 `released` 记录的永久质量闸门。账本只有在 Snapshot 已提交后才可读；CAS 冲突或崩溃不能留下孤儿账本行。标准 `CompactionWorker` 尚未接入重组器，正常后台压缩仍发布空记录集。

它不等同于公开 v1.0，也不代表完整 AstrBot 兼容、Provider 矩阵、性能基准或面向最终用户的发布工件已完成。

本预览的评测方案、冻结研究摘要和当前／目标工作流分别见
[docs/EVALUATION.md](EVALUATION.md)、[docs/EVIDENCE.md](EVIDENCE.md)
与 [docs/WORKFLOW.md](WORKFLOW.md)。其中的隔离合成／确定性记录不构成
生产语义质量、Provider 性能或公开发布就绪声明。

## Public v1.0

Multi-resolution Context Tree、Query-Aware Reconstruction、Source Trace、Web Inspector、Sylanne Adapter、百万 Token demo、故障并发测试和发布文档。

## v1.x

混合检索、Provider adapters、Tool artifact store、更精确 tokenizer、多语言评测、用户可编辑 Context Policy。

## v2

学习型选择器、专用 Context Codec、跨会话项目上下文、多 Agent Context Bus。
