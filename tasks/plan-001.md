# PLAN-001 Write CRM Experiment Implementation Plan

Owner: coordinator-plan-writer
Depends on: spec-001

## Goal

为已批准的实验规格创建完整、可执行、TDD 驱动的实现计划；本任务不实现算法。

## Context

- 设计契约：`D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- 只读仓库：`G:\AstrContinuum`
- 实现根：`D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3`

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\plans\2026-07-30-capsule-recomposition-matrix-implementation.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\plan-001.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`

## Steps

1. 映射独立实验包的文件边界与只读生产边界。
2. 锁定跨任务类型、函数签名和依赖 wave。
3. 为每个实现单元编写 failing test、最小实现、验证与提交步骤。
4. 覆盖 CRM、连续性内核、投影、协议、基线、评分、图表和最终证据。
5. 执行规格覆盖、占位符和类型一致性自审。
6. 向用户提供两种执行方式。

## Acceptance

- 实现计划含 writing-plans 要求的标题与执行交接。
- 每项任务有精确文件、命令、预期结果和代码契约。
- 无未解决占位符。
- 所有实现写入 D 盘实验根。
- 不允许生产写入、本地模型、模型 API、真实聊天、Summary 回退，主方法不得输出空 Capsule；仅离线无内核消融可记录空状态失败。

## Verification Evidence

- 2718lab work-package validator：通过。
- writing-plans 占位符扫描：通过。
- 规格覆盖检查：通过。
- 公共函数签名与旧 API 残留检查：通过。
- 自审修正了检查点存储风险：每代先冻结哈希、再揭示查询、随即释放旧代，不保存历代原始 Capsule。
- 自审补齐三项消融、后学习内容无关日志和主方法/消融空状态隔离。
- 最终计划 SHA-256：`4458162c71a42e5b3b4596a8f5ca09a49d337bb2d29fa45065562bf0c06a8ba4`。

## Return

返回计划绝对路径、验证摘要、计划哈希和执行方式选择。
