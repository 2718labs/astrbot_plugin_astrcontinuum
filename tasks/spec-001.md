# SPEC-001 Write Approved CRM Experiment Design

Owner: coordinator-doc-writer
Depends on: none

## Goal

把用户已批准的 Capsule Recomposition Matrix 架构、连续性内核、安全释放和多轮实验协议写成可复核规格；不实现 CRM。

## Context

- 只读参考：`G:\AstrContinuum` 的 Capsule、compiler、validator、retrieval 与 budget 现状。
- 共享契约：`D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`。
- 现有永久校验要求无损；有损 CRM 只能位于实验层。

## Write Scope

- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\product-brief.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\index.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\contracts\crm-experiment-design.md`
- `D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\tasks\spec-001.md`

## Steps

1. 固化目标、范围和生产隔离边界。
2. 定义 CRM 原子、候选、矩阵、目标函数与硬约束。
3. 定义连续性内核、状态机、原子交换与释放规则。
4. 定义查询投影和后学习边界。
5. 定义多轮、极限预算、公平基线、指标和成功条件。
6. 执行工作包校验、占位符扫描和一致性自审。
7. 把规格交给用户复核。

## Acceptance

- 四个工作包文件存在且位于批准的 D 盘临时根。
- 工作包验证器通过。
- 不含未解决的占位符。
- 不把 Summary 设为运行时组件或回退。
- 不允许上下文完全清空。
- 不声称实现、运行或实验结果已经完成。

## Verification Evidence

- 2718lab work-package validator：通过。
- 规格占位符扫描：通过。
- Summary 运行时隔离、连续性内核下限、生产发布隔离和固定实验分母检查：通过。
- 自审修正了无效低预算的语义：低于 \(K\) 的预算变更不生效，当前内核仍受上一有效预算约束。
- 自审明确了实验查询前 delta 已完全并入冻结 Capsule，不暗中增加 recent-delta 查询通道。

## Return

返回规格绝对路径、验证证据、自审修正摘要和用户复核门禁。
