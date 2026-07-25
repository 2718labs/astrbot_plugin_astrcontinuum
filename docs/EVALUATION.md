# 评测方案

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
