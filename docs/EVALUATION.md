# 评测方案与证据边界

> 本页定义场景、指标与发布阈值。它本身不是实验结果；当前 v0.3
> Technical Preview 没有获准的真实语义模型／Provider 评测结果。已冻结的
> 隔离研究摘要、数据清单和图见 [docs/EVIDENCE.md](EVIDENCE.md)，当前
> 生产链与 CRM 研究目标链见 [docs/WORKFLOW.md](WORKFLOW.md)。

## 三类保证

- Storage-lossless：原始事件可精确重建
- Source-verifiable：关键状态可回溯来源
- Behaviorally near-lossless：压缩后行为尽量接近完整历史

## 基准场景

- Needle：早期埋入名称、数字、禁忌和约束
- Decision Reversal：A → 否定 A → B → C
- Open Loop：长时间后继续未完成任务
- Topic Return：跨多个话题后返回
- Pronoun：刚才那个、第二套、还是原来的
- Tool Overflow：超长工具结果外置后恢复关键内容
- Concurrency：压缩延迟期间连续请求
- Crash：编译、审计和提交阶段故障注入
- Sylanne Coexistence：重复注入和 privacy 检查

## 指标

- critical_anchor_recall
- constraint_recall
- active_decision_accuracy
- open_loop_recall
- exact_entity_accuracy
- stale_fact_pollution
- unsupported_claim_rate
- source_coverage
- compression_ratio
- assembly_latency_p95
- main_path_compaction_wait_count
- recovery_success

## 发布门槛

- main_path_compaction_wait_count = 0
- critical_anchor_recall = 100%
- coverage gap = 0
- unsupported critical claims = 0
- crash scenarios remain usable
- long-history token reduction >= 75%

## 证据与发布声明边界

上述阈值是未来评测／发布门槛，不是本页声称已经达成的成绩。当前
CompactionWorker 未接入 reorganize_capsules()，正常后台发布仍传入空的
重组记录元组；任何 CRM、合成或离线 Summary 记录都不能替代这条生产链的
端到端证据。

可审阅的冻结研究摘要在 [docs/EVIDENCE.md](EVIDENCE.md)：它明确区分
合成 R2 观测、确定性 CRM 压力记账与当前 v0.3 可声称范围。所有数字均
标注来源哈希和非生产边界。
