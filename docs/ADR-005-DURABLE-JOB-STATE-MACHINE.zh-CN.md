# ADR-005：持久化压缩 Job 状态机

- 状态：Phase 0 已接受
- 范围：持久化意图、lease、重试与发布

## 稳定状态

`compaction_jobs.state` 只能使用以下稳定 wire value：

- `PENDING`：有持久化工作但无活动 lease；
- `LEASED`：worker 持有未过期 lease，尚未开始编译；
- `COMPILING`：worker 正在构造并机械检查候选；
- `AUDITING`：正在进行可选模型语义审查；
- `READY_TO_COMMIT`：全部必需检查已通过且已分配 `candidate_snapshot_id`；
- `RETRY_WAIT`：可重试失败已持久化，带有 `next_retry_at` 且无活动 lease；
- `COMMITTED`：发布和 Job 完成原子提交；
- `SUPERSEDED`：活动指针 CAS 输给较新的已提交 Snapshot；失败证据保留输方
  `candidate_snapshot_id`；
- `FAILED`：不可重试失败或重试耗尽已持久化；
- `CANCELLED`：明确管理员决定结束了待处理工作。

终态为 `COMMITTED`、`SUPERSEDED`、`FAILED`、`CANCELLED`；工作态为 `LEASED`、`COMPILING`、
`AUDITING`、`READY_TO_COMMIT`。

## 持久化意图与认领

`TX_RAISE_COMPACTION_INTENT` 必须原子地将 `intent_target_high_water_mark` 设为其当前值与
trigger 高水位的最大值。并发或重复 trigger 不得降低 intent，也不得为同一 session 创建冲突的
活动工作。

`TX_CLAIM_JOB` 必须选择合格的 `PENDING` 工作或到期的 `RETRY_WAIT` 工作并将其转为 `LEASED`。
每次成功认领必须将 `lease_epoch` 单调增加到至少 `1`，设置 `lease_owner`、`lease_expires_at`，
增加 `attempt_count`，并为本次 attempt 冻结 `target_high_water_mark`。每一个工作态都必须有
`lease_epoch >= 1`。worker 对每一次工作态转换、失败记录、lease 续租和发布尝试都必须提供
`lease_owner` 和 `lease_epoch`。

过期或被替代的 worker 不得发布、不得改变较新的 lease、不得清除较新 worker 的错误。即使旧进程
仍在运行，数据库 fencing predicate 仍是权威。

## 允许转换

实现只能允许：

- 通过 `TX_CLAIM_JOB` 执行 `PENDING -> LEASED` 和到期 `RETRY_WAIT -> LEASED`；
- `LEASED -> COMPILING`；
- 可选语义审查开启时 `COMPILING -> AUDITING`；
- 语义审查关闭且机械检查通过时 `COMPILING -> READY_TO_COMMIT`；
- 审查接受后 `AUDITING -> READY_TO_COMMIT`；
- 通过 `TX_FAIL_JOB` 从任一工作态到 `RETRY_WAIT` 或 `FAILED`；
- 成功 `TX_PUBLISH_SNAPSHOT` 后 `READY_TO_COMMIT -> COMMITTED`；
- 发布 CAS 冲突后 `READY_TO_COMMIT -> SUPERSEDED`；
- 由明确管理操作执行 `PENDING` 或 `RETRY_WAIT -> CANCELLED`；
- 通过 `TX_RECOVER_EXPIRED_LEASES` 将过期非终态工作转为 `PENDING`。

其他全部转换都必须拒绝，持久化状态必须保持不变。

## 故障隔离

编译器、审计器、回调和 worker 异常必须在调度器迭代边界捕获，并以 `error_stage`、`error_code`
和脱敏 `error_message` 持久化到受影响 Job。可重试异常必须转到 `RETRY_WAIT`；不可重试或耗尽的
失败必须转到 `FAILED`。一个 Job 失败不得终止调度器循环，也不得阻止认领其他 session。

`strict_audit=false` 只跳过 `AUDITING` 转换，不得跳过 `COMPILING` 中的机械检查。

## 恢复与后续工作

启动时及定期执行的 `TX_RECOVER_EXPIRED_LEASES` 必须将过期非终态 lease 重新排队为 `PENDING`，
清除 `lease_owner` 和 `lease_expires_at`，但保留单调 `lease_epoch`。如果过期状态为
`READY_TO_COMMIT`，还必须清除 `candidate_snapshot_id`，因为候选 payload 是 worker 局部的；下一次
认领必须重新编译和校验。恢复不得修改 `journal_events`、`snapshots` 或 `active_snapshots`。

`TX_PUBLISH_SNAPSHOT` 后，两个终态分支都必须将 `intent_target_high_water_mark` 与获胜活动
Snapshot 覆盖进行比较。`COMMITTED` 使用新发布 Snapshot；`SUPERSEDED` 使用并发获胜 Snapshot。
如果 intent 更高，同一个外层事务必须用获胜的 `base_snapshot_id` 和 `base_pointer_version` 持久化
保留或创建后续 `PENDING` 工作；不得把任何终态视为已满足较晚的 trigger，也不得丢失通知。
