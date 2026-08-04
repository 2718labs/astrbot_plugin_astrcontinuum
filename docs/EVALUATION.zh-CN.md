# 评测方案与证据边界

[English](EVALUATION.md) | 简体中文

> 本页定义场景、指标和发布阈值，并非实验结果。当前 `v0.3` Technical Preview 没有获准的
> 真实语义模型或 Provider 评测结果。已冻结的隔离研究摘要、数据清单和图见
> [EVIDENCE.zh-CN.md](EVIDENCE.zh-CN.md)；当前生产链与 CRM 研究目标链见
> [WORKFLOW.zh-CN.md](WORKFLOW.zh-CN.md)。

## 三类保证

- Storage-lossless：原始事件可以精确重建。
- Source-verifiable：关键状态可以追溯到来源。
- Behaviorally near-lossless：压缩后行为尽量接近完整历史。

## 基准场景

- Needle：早期埋入名称、数字、禁忌和约束。
- Decision Reversal：A → 否定 A → B → C。
- Open Loop：在很久以后继续未完成任务。
- Topic Return：跨多个话题后回到原话题。
- Pronoun：例如“刚才那个”“第二套”“还是原来的”。
- Tool Overflow：超长工具结果外置后恢复关键内容。
- Concurrency：压缩延迟期间仍连续请求。
- Crash：在编译、审计和提交阶段注入故障。
- Sylanne Coexistence：检查重复注入和隐私。

## 指标

- `critical_anchor_recall`
- `constraint_recall`
- `active_decision_accuracy`
- `open_loop_recall`
- `exact_entity_accuracy`
- `stale_fact_pollution`
- `unsupported_claim_rate`
- `source_coverage`
- `compression_ratio`
- `assembly_latency_p95`
- `main_path_compaction_wait_count`
- `recovery_success`

## 发布门槛

- `main_path_compaction_wait_count = 0`
- `critical_anchor_recall = 100%`
- `coverage gap = 0`
- `unsupported critical claims = 0`
- 崩溃场景仍可用。
- 长历史 token 缩减 `>= 75%`。

## 证据与发布声明边界

上述阈值是未来评测与发布门槛，不是本页所声称已经达到的成绩。当前
`CompactionWorker` 未接入 `reorganize_capsules()`；正常后台发布仍传入空的重组记录元组。
任何 CRM、合成或离线 Summary 记录都不能替代这条生产链的端到端证据。

可审阅的冻结研究摘要见 [EVIDENCE.zh-CN.md](EVIDENCE.zh-CN.md)。该页面明确区分合成
R2 观测、确定性 CRM 压力记账和当前 `v0.3` 可声称范围；所有数字都标注来源哈希与非生产
边界。
