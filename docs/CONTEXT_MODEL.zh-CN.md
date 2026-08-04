# 上下文模型

[English](CONTEXT_MODEL.md) | 简体中文

## Event

不可变的原始事实来源。事件可以是消息、工具调用、工具结果、系统变更、手动锚点、
`reset` 和外部记忆提示。

## Atomic Claim

具有单一语义、来源和状态，可独立更新与检索的记录。

状态：

```text
active / superseded / retracted / uncertain
```

## Decision

除 Claim 字段外，还记录 `rationale`、`alternatives`、`supersedes` 和
`rejected_because`。

## Exact Anchor

任何不能承受释义的信息。

## Capsule

Capsule 是封闭、不可变的结构化信封，具有下列四个分辨率级别之一：

- `micro`
- `episode`
- `task`
- `global`

每个 Capsule 都嵌入完整的七分量 `SessionKey`，记录闭区间事件覆盖、聚合的
`source_event_ids`、Schema 版本、创建时间、token 成本和机械质量指标。语义状态以独立的
有类型数组保存：目标、约束、决定、进展、未闭合事项、偏好、实体、情感上下文、精确锚点
和依赖关系。活动记录保留来源事件 id。

`narrative_summary` 仅用于导航；它不能凌驾于结构化语义数组及其溯源之上。

## Snapshot

由已审计 Capsule、活动状态、索引元数据和覆盖边界组成的不可变版本。Snapshot 通过按
`ordinal` 排序的 `snapshot_capsules` 关系引用 Capsule；成员关系与 Capsule 必须和
Snapshot 使用同一 `SessionKey`。

Snapshot 不是原始历史的替代品。

候选 Capsule、成员关系与 Snapshot 只在发布 savepoint 内出现。只有由 active pointer
指向的 `COMMITTED` Snapshot 可读；CAS 失败会回滚全部新候选内容。

## Delta

从已提交 Snapshot 的 `covered_event_seq + 1` 到最新事件，保持原始形态。

## Context Tree

父节点概括子节点，但不删除子节点。查询可按相关性选择不同分辨率。

## 依赖闭包

选择一个决定时，必须同时选择理解它所需的定义、主体、原因和约束。装配不是简单的 top-k，
而是受依赖关系约束的预算优化。

## 外部记忆边界

Sylanne 可以提供本轮临时的重要性或检索信号，但其私有记忆不是 Capsule 的来源，也不会
进入 AstrContinuum 的 Capsule、Snapshot、Journal 或稳定哈希。
