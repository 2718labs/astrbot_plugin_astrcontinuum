# Sylanne 适配边界

[English](SYLANNE_ADAPTER.md) | 简体中文

> 本页定义任何 Sylanne 适配器必须遵守的边界；单独阅读本页并不构成某项适配能力已经接线、
> 兼容或经过运行时验证的证据。

Sylanne 已拥有分层记忆、召回、状态注入和关系系统。AstrContinuum 不复制这些能力。

```text
Sylanne：长期意义、关系、人格、生活记忆
AstrContinuum：当前调用工作集、上下文压缩、状态恢复、Token 预算
```

适配器只读能力：

- 获取长期记忆召回；
- 获取重要性提示；
- 获取隐私级别；
- 获取已格式化的状态片段；
- 获取来源与置信度。

必须避免：

- 重复注入相同记忆；
- 暴露 `internal` 或 `private` 内容；
- 将 Sylanne 记忆复制到 AstrContinuum 的永久事实库；
- 依赖 Sylanne 私有存储结构。

Sylanne 不存在或不兼容时，静默降级为 standalone，主流程不受影响。
