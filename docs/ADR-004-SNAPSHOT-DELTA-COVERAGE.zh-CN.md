# ADR-004：Snapshot、Delta 与覆盖语义

- 状态：Phase 0 已接受
- 范围：请求装配和压缩输入
- 更新：v0.3.0 的发布写入顺序、账本可见性和冲突分类由 ADR-008 局部修订；本 ADR 的其余
  覆盖语义继续有效。

## 定义

针对一个 `session_key_hash`：

- `H` 是 `TX_READ_REQUEST_VIEW` 事务的高水位：最大的已提交 `journal_events.sequence`；没有
  事件时为 `0`。
- `S` 是通过 `active_snapshots` 选择的已提交活动 Snapshot；首次 Snapshot 发布前为逻辑
  `EMPTY_BASE`。
- `EMPTY_BASE` 不是 Snapshot Schema 信封或数据库行，没有 `snapshot_id` 或活动指针，只在
  引导装配时定义覆盖终点 `0`。
- 已提交 Snapshot 的 `C` 为 `S.covered_event_end`；`EMPTY_BASE` 的 `C` 为 `0`。
- 请求 Delta `Dread` 是满足 `C < sequence <= H` 的有序已提交 Journal 事件集合。
- 压缩目标 `T` 是该次已认领 Job 不可变的 `target_high_water_mark`。
- 压缩 Delta `Djob` 是满足 `C < sequence <= T` 的连续有序集合。

Snapshot 覆盖连续 Journal 前缀 `[1, covered_event_end]`。覆盖保证存储和可恢复性，不能解释为
每个已覆盖原始 token 都会出现在每一个 Prompt 的渲染结果中。

## 请求视图

`TX_READ_REQUEST_VIEW` 必须在同一读事务中读取活动指针、其 Snapshot 和 `H`。有活动指针时，
返回视图必须只含该一个已提交活动 Snapshot 和 `Dread`；无活动指针时，必须返回 `EMPTY_BASE`
和事件 `1..H`。不得伪造持久化 Snapshot、包含候选 Snapshot、包含高于 `H` 的事件，或包含已被
`S` 覆盖的事件。

`EMERGENCY_ASSEMBLY` 可以为满足 Prompt 预算而裁剪、摘要或省略 Prompt 材料，但不得删除或修改
`journal_events`、改变 `active_snapshots`、更新 `covered_event_end`，或把省略材料描述为新覆盖。

## 候选构造

压缩候选必须消耗完整的基础输入（前一个已提交 Snapshot `S`，或引导时 `EMPTY_BASE`）以及
连续 `Djob` 中的每个事件。worker 必须机械校验：

- `T > C`，且 `T` 不超过该 Job 观察到的已提交 Journal 高水位；
- 首个 Delta 序号为 `C + 1`，最后一个为 `T`，相邻序号差恰为一；
- `base_snapshot_id` 等于编译器输入所用活动 Snapshot 且 `base_pointer_version >= 1`；只有
  `EMPTY_BASE` 引导时可为 null 且 `base_pointer_version=0`；
- 候选 `covered_event_end` 与 `source_high_water_mark` 都等于 `T`；
- 先前活动语义和必需精确锚点均存在；
- 输出结构有效、身份一致、机械有效且非空。

丢失先前活动语义、跳过 Journal 事件、覆盖超过 `T`、使用非活动基础或报告非连续前缀的候选，
都不得发布；即使 `strict_audit=false`，这些机械检查也必须执行。

## 发布

wire Snapshot `state` 只能是 `CANDIDATE` 或 `COMMITTED`。候选内容在 Job 为 `READY_TO_COMMIT`
时仍是 worker 局部内容；它可作为 `state=CANDIDATE` 通过 Schema 校验，但不会插入只存
`COMMITTED` 的 `snapshots` 表。`candidate_snapshot_id` 是预分配不透明 id，不代表 reader 可见。
`TX_PUBLISH_SNAPSHOT` 必须在一个外层事务中：校验 lease owner 与 fencing `lease_epoch`；打开
savepoint 并插入不可变 `COMMITTED` Snapshot；用严格大于既有覆盖的条件 CAS `active_snapshots`；
成功时将 Job 设为 `COMMITTED` 并提交；遇到同前缀唯一性或指针 CAS 冲突时仅回滚 savepoint，保留
`candidate_snapshot_id`、将 Job 设为 `SUPERSEDED` 并清除活动 lease；任一分支提交前，如 durable
intent 大于获胜 Snapshot 覆盖，则原子保留或创建后续 `PENDING` 工作。

reader 不得通过 `active_snapshots` 观察到尚未提交成功的插入行。每一次成功发布的指针覆盖必须
严格增长。相同前缀、引导指针竞争或更新冲突是预期的隔离发布冲突，不能成为未处理 worker
异常、不能回滚 durable `SUPERSEDED` 转换、不能覆盖获胜指针、也不能丢弃更高的 durable intent。

`COMMITTED` Snapshot 必须满足 `audit_outcome.mechanical_passed=true`、`semantic_status` 为
`NOT_RUN` 或 `PASSED`，且 `failure_codes` 为空。永久校验器必须在发布时强制
`covered_event_end = source_high_water_mark = compaction_jobs.target_high_water_mark`；标准 JSON
Schema 无法表达这种跨记录相等性。

在 `T` 之后追加的事件仍是下一次请求和压缩的 Journal Delta。活动 Job 期间发生的 trigger 必须
持久化抬升 `intent_target_high_water_mark`；`COMMITTED` 和 `SUPERSEDED` 完成路径在 intent 大于
获胜活动覆盖终点时都必须保留或创建后续 `PENDING` 工作。

## 示例

若活动覆盖为 `C=40`、读取高水位为 `H=44`、Job 目标为 `T=43`，请求视图为 Snapshot `[1,40]`
加事件 `41..44`；候选消耗 Snapshot 加事件 `41..43`；发布后事件 `44` 仍未覆盖。包含 `41,43`
的 Delta 必须在发布前失败。
