# 阶段 0：并发状态机

[English](CONCURRENCY_STATE_MACHINE.md) | 简体中文

## 正确性边界

SQLite 事务、唯一性约束、active-pointer CAS 和单调递增的 `lease_epoch` 构成并发正确性
边界。正确性不得依赖内存队列或锁。所有 worker 写入都必须同时受 `lease_owner` 与
`lease_epoch` fencing 约束。

## Job 状态迁移

| 起始状态 | 事件或守卫条件 | 目标状态 | 原子动作 |
| --- | --- | --- | --- |
| `PENDING` | 符合条件的 claim | `LEASED` | `TX_CLAIM_JOB` 递增 epoch 并建立 lease |
| `RETRY_WAIT` | `next_retry_at <= now` | `LEASED` | `TX_CLAIM_JOB` 递增 epoch 并建立 lease |
| `LEASED` | 受 fence 约束的 worker 启动 | `COMPILING` | 按 owner/epoch 的条件更新 |
| `COMPILING` | 机械检查通过；启用 audit | `AUDITING` | 按 owner/epoch 的条件更新 |
| `COMPILING` | 机械检查通过；关闭 audit | `READY_TO_COMMIT` | 持久化预分配的 candidate id |
| `AUDITING` | audit 接受 | `READY_TO_COMMIT` | 持久化预分配的 candidate id |
| working | 可重试异常 | `RETRY_WAIT` | `TX_FAIL_JOB` 持久化错误和退避，并清除 lease |
| working | 致命或已耗尽异常 | `FAILED` | `TX_FAIL_JOB` 持久化错误，并清除 lease |
| `READY_TO_COMMIT` | fence 及 bootstrap-create／existing-update CAS 成功 | `COMMITTED` | `TX_PUBLISH_SNAPSHOT` 提交 candidate Capsule、Snapshot、有序成员关系、显式提供的重组账本行、严格前进的 pointer 和 job |
| `READY_TO_COMMIT` | fence 有效；Capsule／Snapshot／成员关系插入发生完整性冲突，或 pointer CAS 冲突 | `SUPERSEDED` | 回滚全部新 candidate Capsule、Snapshot、成员关系和账本行；保留 candidate id；持久化终止冲突及任何更高 intent 的后续工作 |
| `PENDING`/`RETRY_WAIT` | 管理性取消 | `CANCELLED` | 条件式终止状态更新 |
| 已过期 working | 恢复扫描 | `PENDING` | `TX_RECOVER_EXPIRED_LEASES` 清除 owner／过期时间，保留 epoch；对于 `READY_TO_COMMIT` 还要清除 candidate id |

未列出的迁移必须在不改变持久状态的情况下失败。`strict_audit=false` 只去除 `AUDITING`
分支；机械检查仍然是强制项。

## 重复和并发触发

`TX_RAISE_COMPACTION_INTENT(session, trigger_high_water)` 必须计算
`intent_target_high_water_mark = max(existing, trigger_high_water)`。对 active job，它不得
改写当前尝试已冻结的 `target_high_water_mark`。重复触发应幂等，较晚的触发不得丢失。

两个发布结果都会将 intent 与胜出的 active `covered_event_end` 比较。对于 `COMMITTED`，
胜者是新 Snapshot；对于 `SUPERSEDED`，胜者是并发发布者。如果 intent 更高，外层事务
必须依据胜者的 `snapshot_id` 和 `pointer_version` 留下或创建后续 `PENDING` 工作。两种
结果都不得丢失通知。

## 竞争 claim 与过期 worker

只有一个 claimant 可以条件式地把符合条件的行变更为 `LEASED`。每次成功 claim 都会把
`lease_epoch` 增至至少 `1`；该 token 永不减少或重置，恢复后也一样。每个 working 状态
都要求 `lease_epoch >= 1`。续租和所有状态迁移都要求 owner、epoch 匹配，且在适用时 lease
未过期。

过期 worker 可以继续在内存中计算，但其下一次数据库写入必须影响零行。它不得发布、
不得为新 owner 记录失败、不得续新 lease，也不得清除新状态。从过期
`READY_TO_COMMIT` 恢复时，必须清除该 worker 局部的 `candidate_snapshot_id`；新的 claim
之后，新 epoch 必须重新编译和验证 candidate。只有新 epoch 具有权威性。

## 并发发布

worker 会记录预期的 `base_snapshot_id` 和 `base_pointer_version`。
`TX_PUBLISH_SNAPSHOT` 会验证 fence 以及永久性的身份、来源、覆盖和 audit 条件，包括严格的
覆盖前进、规范账本记录及非 `narrative_summary` 的 `released` 质量下限，然后在外层发布
事务中打开内层 savepoint。在 pointer CAS 之前，同一 savepoint 必须按以下顺序插入新的
不可变 `capsules`、已提交 Snapshot、有序 `snapshot_capsules` 成员关系和任何显式提供的
有序 `snapshot_reorganization_records`。已有的不可变 base Capsule 可以被引用，不能重写。
对于 bootstrap 的 null/`0`，CAS 会条件式创建版本为 `1` 的缺失 pointer。对于已有的非空
base 且版本 `>=1`，CAS 会条件式更新匹配行并递增其版本。Capsule 插入、Snapshot 插入、
成员关系、账本插入、pointer 改变和 job 的 `COMMITTED` 迁移必须作为同一个成功分支提交。

若另一个 worker 赢得相同覆盖前缀，失败者可能在到达 pointer CAS 前于 Snapshot 插入时触发
`UNIQUE(session_key_hash, covered_event_end)`；bootstrap 创建或已有 pointer 更新也可能在
CAS 处失败。以上情况，以及当前 candidate-conflict classifier 处理的 Capsule／Snapshot／
成员关系插入完整性冲突，都进入 `SUPERSEDED` 路径，而非以未处理的 worker 异常退出。

worker 必须回滚到内层 savepoint，以移除每一个新的 candidate Capsule、成员关系、账本和
未发布 Snapshot 行，然后保留 `candidate_snapshot_id`，将受 fence 约束的 job 迁移为
`SUPERSEDED` 并清除 lease。提交外层事务前，必须读取胜者；若持久 intent 高于胜者覆盖，
必须使用胜者的 base/version 创建或保留 `PENDING` 后续工作。不得重试旧 candidate，因为
它使用了过时的 base。过时 owner/epoch 必须拒绝整次迁移，且不得持久化 `SUPERSEDED`。
插入账本行时发生完整性错误不属于发布冲突；回滚整个外层事务后必须传播该错误。

## 事件并发

采集事务通过 `sessions.next_event_sequence` 串行化每个 session 的序号分配。
`UNIQUE(session_key_hash, source_hook, idempotency_key)` 让重复的 hook 投递返回同一个事件，
`UNIQUE(session_key_hash, sequence)` 则防止排序冲突。

唯一有效的 event/role/hook 三元组为 `USER_MESSAGE/USER/ON_LLM_REQUEST`、
`ASSISTANT_MESSAGE/ASSISTANT/ON_AGENT_DONE`、`TOOL_CALL/TOOL/ON_USING_LLM_TOOL` 和
`TOOL_RESULT/TOOL/ON_LLM_TOOL_RESPOND`。`ON_LLM_RESPONSE` 必须保持纯观测。多个插件
runner 必须通过数据库约束收敛，不能依赖进程归属。

## 读取隔离

`TX_READ_REQUEST_VIEW` 在一个读取事务中固定 active pointer 和 Journal 高水位 `H`。其正常
视图是 pointer 指向的已提交 Snapshot 加上满足 `covered_event_end < sequence <= H` 的事件。
第一次发布前，它观察不到 active pointer，并使用逻辑 `EMPTY_BASE`、`C=0` 和事件 `1..H`；
`EMPTY_BASE` 不是 Snapshot 行。高于 `H` 的并发追加等待下一个视图；并发发布必须被完整地
观察为发布前或发布后，绝不能形成混合的 Snapshot/Delta 边界。

worker 局部 candidate 数据在 wire 层的 `state=CANDIDATE`，对读取器不可见。
`TX_PUBLISH_SNAPSHOT` 只在发布 savepoint 内插入已验证的 Capsule、`state=COMMITTED` 的
Snapshot 行、有序成员关系和显式提供的有序账本行。读取器只能经由 `active_snapshots` 解析
Snapshot，并通过已提交的 `snapshot_capsules` 加载 Capsule id；重组账本读取器只接受已提交
Snapshot。部分或已回滚的 candidate 不可见。

## 调度器隔离与恢复

调度器必须在每次迭代中捕获 compiler、auditor、callback 和 worker 异常，通过
`TX_FAIL_JOB` 持久化它们，并继续 claim 无关工作。启动时和周期性恢复必须将过期的非终态
lease 重新排队，且不得修改已提交 Snapshot 或 Journal 行。
