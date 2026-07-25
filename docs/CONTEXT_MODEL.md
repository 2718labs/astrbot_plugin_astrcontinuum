# 上下文模型

## Event

不可变原始事实来源。类型包括消息、工具调用、工具结果、系统变更、手动锚点、reset 和外部记忆提示。

## Atomic Claim

单一语义、有来源、有状态、可独立更新和检索。

状态：

```text
active / superseded / retracted / uncertain
```

## Decision

除 Claim 字段外，记录 rationale、alternatives、supersedes 和 rejected_because。

## Exact Anchor

任何不能承受释义的信息。

## Capsule

- micro
- episode
- task
- global

## Snapshot

一组已审计 Capsule、活动状态、索引元数据和覆盖边界的不可变版本。

Snapshot 不是原始历史替代品。

## Delta

从 committed snapshot 的 `covered_event_seq + 1` 到最新事件，保持原始形态。

## Context Tree

父节点概括子节点但不删除子节点。查询可按相关性选择不同分辨率。

## 依赖闭包

选择一个决定时，必须同时选择理解它所需的定义、主体、原因和约束。装配不是简单 top-k，而是带依赖的预算优化。
