# AstrContinuum 任务清单

[English](./TASKS.md) | 简体中文

> **规划边界。** 本页是面向后续工作的任务清单，不是完成报告。当前版本是 `v0.3.0`
> Technical Preview；发布声明以[路线图](docs/ROADMAP.zh-CN.md)、[测试矩阵](docs/TEST_MATRIX.zh-CN.md)
> 和当前可复现证据为准。

## Phase 0 — 侦察

- [ ] 在每个受支持的宿主探针上核对 AstrBot 插件 API、Hook 顺序与锁语义。
- [ ] 对照当前源码核对上下文持久化、Provider 与 tokenizer 接口。
- [ ] 在任何持久化接入前核对 Sylanne 稳定公开 API。
- [ ] 为实质契约变更记录 ADR、风险与差距报告。

## Phase 1 — 基础一致性

- [ ] SQLite Event Journal、WAL 与迁移演进。
- [ ] Snapshot Store 与原子 active pointer。
- [ ] Job lease 与崩溃恢复。
- [ ] Session key 规范化。

## Phase 2 — 主请求链路

- [ ] 具备离线、可复现契约的模型感知 token 计数器。
- [ ] Snapshot + Delta 装配。
- [ ] Turn boundary 保留。
- [ ] Emergency assembly。
- [ ] Assembly trace 与有边界的性能基准。

## Phase 3 — 压缩

- [ ] Episode segmenter。
- [ ] Exact-anchor extractor。
- [ ] Schema-constrained compiler。
- [ ] Capsule merger 与 decision-conflict resolver。
- [ ] Dependency graph。
- [ ] Loss auditor。

## Phase 4 — 检索与恢复

- [ ] Hybrid retrieval。
- [ ] Query-reference classifier。
- [ ] Resolution selector。
- [ ] Evidence source trace。
- [ ] Tool-artifact retrieval。

## Phase 5 — 接入与发布

- [ ] AstrBot 宿主探针覆盖。
- [ ] Custom-compressor 的 fail-safe 证据。
- [ ] 仅在具备稳定公开 API 证据后接线 Sylanne adapter。
- [ ] 具有有界运维权限的 Commands / Web Inspector。
- [ ] 仅在批准可复现协议后开展百万 Token 基准。
- [ ] 具有记录测试边界的非阻塞演示。
- [ ] 崩溃与决策反转演示。
