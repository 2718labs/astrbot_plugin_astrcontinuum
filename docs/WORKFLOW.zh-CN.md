# v0.3 工作流与研究路径

[English](WORKFLOW.md) | 简体中文

![v0.3 已交付工作流与 CRM 研究目标 / v0.3 shipped workflow and CRM research target](assets/v030-workflow-rmb.svg)

## 图例

- **实线墨绿：** 已由当前 v0.3.0 契约实现并约束的发布链。
- **虚线暗红：** CRM 研究目标链。它有隔离确定性记录，但尚未接入标准
  CompactionWorker，不能视为当前运行能力。
- **暖灰：** 账本、质量与失败边界。它们约束状态迁移，不能被可选语义审计设置绕过。

图采用暗红 #A44742、墨绿 #1F6B5B、暖灰 #DDD6CC 与墨色 #263238 的学术配色；
不包含货币符号、面额或价格意象。

## 当前 v0.3.0：已交付发布链

1. 已捕获的 Journal / Delta 工作先获取带 fencing 的 job claim；请求路径不会等待
   编译、审计或远端模型。
2. 编译器从 frozen high-water mark 出发，生成候选 Capsule 与 Snapshot。
3. 可选语义审计只决定候选是否经过 AUDITING；机械校验、fence 和永久质量闸门
   始终执行。
4. TX_PUBLISH_SNAPSHOT 在同一 savepoint 内写入 Capsule、Snapshot、membership、
   显式提供的账本记录，并执行 active-pointer CAS。
5. 候选 Snapshot 会在 CAS 之前以 COMMITTED 插入；只有 CAS 成功且外层事务提交
   后才变为 active/read-visible。CAS 冲突会回滚候选行并返回 SUPERSEDED；账本
   完整性错误会传播并回滚事务，且不会移动 active pointer。

标准 CompactionWorker 当前**不**调用 reorganize_capsules()。普通后台发布传入空的
重组记录元组。图中的 ledger 是已实现的**显式发布接口**，不是自动重组已经接线的声明。

## CRM：虚线研究目标链

研究路径从旧 Capsule 和新 Delta 开始：

Atomizer → Candidate Composer → Matrix Builder (F/A/R/H/D) → budgeted CRM
Optimizer → loss-aware gate → atomic swap → query projection。

每一阶段都必须保留 ID、状态、时间、极性和来源。受控释放必须通过 loss-aware gate。
目标 swap 只让 query projection 读取新 Capsule，并释放已消费的 Delta / 旧材料。
Summary 只能用于离线比较，不能作为运行时主路径或降级回退。

这条虚线路径需要独立的 claim → compile → audit → publish/CAS worker 接线、永久验证
和发布证据，才能成为生产能力。

## 相关页面

- [证据与溯源](EVIDENCE.zh-CN.md)
- [评测场景、指标与阈值](EVALUATION.zh-CN.md)
- [规范性数据流细节](DATA_FLOW.zh-CN.md)
- [账本发布契约](ADR-008-REORGANIZATION-LEDGER.zh-CN.md)
