# v0.3 工作流与研究路径

![v0.3 工作流与 CRM 研究路径](assets/v030-workflow-rmb.svg)

## 图例

- **实线墨绿**：当前 v0.3 Technical Preview 已实现并可由现有契约约束的
  发布链。
- **虚线暗红**：CRM 的研究目标流程；它有冻结实验记录，但尚未接入标准
  CompactionWorker，不是当前运行能力。
- **暖灰**：账本、质量或失败边界；它们限制状态迁移，不能被可选语义
  审计设置绕过。

配色采用冻结 R2 论文图验证过的暗红 #A44742、墨绿 #1F6B5B、暖灰纸色
#DDD6CC 与墨色 #263238；没有货币符号、面额或价格意象。

## 当前 v0.3：实线发布链

1. 已捕获的 Journal / Delta 触发带 fencing 的 job claim；请求路径不等待
   编译、审计或远端模型。
2. 编译器基于 frozen high-water mark 生成候选 Capsule 与 Snapshot。
3. 可选语义审计仅决定是否经过 AUDITING；机械校验、fence 和永久质量
   闸门始终执行。
4. TX_PUBLISH_SNAPSHOT 在同一 savepoint 内写入 Capsule、Snapshot、
   membership、显式提供的账本记录，并执行 active-pointer CAS。
5. 在同一 savepoint 内先插入 state=COMMITTED 的候选 Snapshot，再执行
   active-pointer CAS；只有 CAS 成功且外层事务提交后，新 Snapshot 才会
   成为 active/read-visible。CAS 冲突会回滚候选并得到 SUPERSEDED 结果。
   账本完整性错误则传播并回滚事务，不能移动 active pointer。

标准 CompactionWorker 当前没有调用 reorganize_capsules()；正常后台发布
会传入空的 reorganization-record tuple。图中的 ledger 是一个已实现的
**显式发布接口**，不是声称自动重组已经接线。

## CRM：虚线研究目标链

研究路径的输入是旧 Capsule 与新 Delta，目标顺序是：

Atomizer → Candidate Composer → Matrix Builder (F/A/R/H/D) →
budgeted CRM Optimizer → loss-aware gate → atomic swap → query projection。

每一步都应保留 ID、状态、时间、极性和来源；受控释放必须通过 loss-aware
gate。目标 swap 只让 query projection 读取新的 Capsule，并释放已消费
Delta / 旧版本。Summary 可以用于离线比较，但不能是运行时主路径或降级
回退。

该虚线路径需要单独的 claim → compile → audit → publish/CAS worker 接线、
永久验证和发布证据，才能从研究目标变成生产能力。

## 相关页面

- 研究摘要与哈希边界：[docs/EVIDENCE.md](EVIDENCE.md)
- 场景、指标、阈值：[docs/EVALUATION.md](EVALUATION.md)
- 规范性事务细节：[docs/DATA_FLOW.md](DATA_FLOW.md)
- 账本发布契约：[docs/ADR-008-REORGANIZATION-LEDGER.md](ADR-008-REORGANIZATION-LEDGER.md)
