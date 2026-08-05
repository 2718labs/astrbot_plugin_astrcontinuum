# v0.3.0 Technical Preview 数据流

[English](./DATA_FLOW.md) | 简体中文

本数据流契约把已验证的 **v0.2.1** 运行时与 **v0.3.0** 重组账本叠加说明。它严格区分
“已持久化”与“已接线”：常规 Provider 绑定／默认 AstrBot 后台压缩使用 token 指标通道并发布空
重组账本；可选的注入式 compiler backend 则可在显式预算下完成围栏合成 Gate A 重组后提供记录。

## 规范记录

所有流都携带 wire 字段 **session_key**。SQLite 仅把 **session_key_hash**（规范
SessionKey 序列化的 SHA-256）用作查询键；将它与
**sessions.canonical_session_key_json** 关联后，必须无损重建嵌入式 **session_key**。
持久数据保存在 **sessions**、**journal_events**、**capsules**、**snapshots**、有序
**snapshot_capsules**、可选有序 **snapshot_reorganization_records**、
**active_snapshots**、**compaction_jobs**、加密的 **token_metrics** 与不含正文的
**token_metric_backfill_intents** 中。本文的事务名称是稳定架构名，必须与
**DATABASE_SCHEMA.md** 和 **CONCURRENCY_STATE_MACHINE.md** 一致。

Journal 的 wire 组合必须限制为：

| **event_type** | **role** | 唯一 **source_hook** |
| --- | --- | --- |
| **USER_MESSAGE** | **USER** | **ON_LLM_REQUEST** |
| **ASSISTANT_MESSAGE** | **ASSISTANT** | **ON_AGENT_DONE** |
| **TOOL_CALL** | **TOOL** | **ON_USING_LLM_TOOL** |
| **TOOL_RESULT** | **TOOL** | **ON_LLM_TOOL_RESPOND** |

**ON_LLM_RESPONSE** 只用于观测，不能作为 Journal **source_hook**。

## 用户请求流

1. **on_llm_request** 推导七元 SessionKey 与其 **session_key_hash**。
2. 适配器通过公开的 **Context.get_using_provider()** 和 Provider 元数据读取活动模型与
   上下文限制；它解析 **AUTO_ASTRBOT**、**MANUAL** 或
   **AUTO_SAFE_FALLBACK=128000**，随后冻结一个不可变的请求 tokenizer/预算 profile。
3. **TX_CAPTURE_USER_EVENT** 插入一条 **journal_events** 行，字段为
   **event_type=USER_MESSAGE**、**role=USER**、**source_hook=ON_LLM_REQUEST**；对于相同的
   幂等元组则返回既有行。序号分配与插入必须原子完成；同一事务还会写入规范 Event 指标
   或匹配的补齐意图。
4. **TX_READ_REQUEST_VIEW** 打开一个读事务，记录已提交 Journal 高水位 **H**，并通过
   **active_snapshots** 选择活动 Snapshot。没有 pointer 时，使用逻辑
   **EMPTY_BASE**（**C=0**）并选择事件 **1..H**；**EMPTY_BASE** 不是 Schema
   envelope 或数据库 Snapshot。
5. 一个请求局部计数器在冻结 profile 下计算每个宿主组件与候选。工具调用和对应结果不可拆分。
   assembler 合并已提交 Snapshot 与有序 Delta；**EMERGENCY_ASSEMBLY** 可以减少 prompt
   材料，但不能修改持久记录或覆盖。
6. 最终完整 Provider projection 在同一 profile 下重新计数。任何 tokenizer 构造或计数
   失败都会丢弃全部部分主结果，并从源重新以 **utf8-byte-v1** 回放整个请求；BPE 与 BYTE
   单位绝不在同一请求混用。
7. AstrBot 适配器可以向 **ProviderRequest.extra_user_content_parts** 临时追加
   **TextPart**。内部能力缺失时必须 fail-open：跳过增强并记录脱敏错误。
8. 需要压缩时，**TX_RAISE_COMPACTION_INTENT** 持久化提高
   **intent_target_high_water_mark**。请求 Hook 不得等待编译、审计、调度器完成或远程模型。

## 完成与工具流

**on_agent_done** 为权威完成的助手输出执行 **TX_CAPTURE_ASSISTANT_EVENT**。
**on_using_llm_tool** 与 **on_llm_tool_respond** 对 **TOOL_CALL**、**TOOL_RESULT** 使用相同的
幂等追加原语。每个 source hook 只能写入其映射的 event/role 组合。每个新 Event 会在同一
事务中提交其规范指标或可重试的补齐意图；回调成功不能留下未追踪的指标缺口。

**on_llm_response** 可以收集指标或脱敏诊断，但不得追加 Journal 行。编译器、调度器或回调
失败不得追溯移除已经采集的事件。外部 Sylanne 记忆正文永不进入这些流。

## 持久化压缩流

1. **TX_RAISE_COMPACTION_INTENT** 为会话保存最大请求 target，不能降低已存在 target。
2. **TX_CLAIM_JOB** 将符合条件的 **PENDING** 或到期 **RETRY_WAIT** 工作移至
   **LEASED**，增加 **lease_epoch**，设置租约字段并冻结本次 attempt 的
   **target_high_water_mark**。
3. 编译前，worker 至多处理一批有界、稳定排序的缺失 **canonical-o200k-v1** 指标。每个
   指标与其匹配 intent 的删除原子提交。
4. 带 fence 的 worker 将 **LEASED -> COMPILING**，读取预期 active Snapshot、截止
   **target_high_water_mark** 的连续原始 Delta 与完整规范 Event 指标集合。规范指标缺失或
   不可用会延期 Job，但不消耗一次普通失败 attempt。
5. 编译器消费两类输入与规范计数，产出封闭 Snapshot 和封闭结构化 Capsule。机械结构、身份、
   source-event、覆盖、精确锚点、同会话 membership 和非空输出校验始终执行。Sylanne
   记忆不能作为 Capsule 来源。
6. **v0.3.0** 增加一个可选的 Repository 发布参数，用于接收规范重组记录。常规 Provider
   绑定／默认 AstrBot 路径不设置 **reorganization_token_budget**，因此普通后台工作发布空账本。
   可选的注入式 compiler backend 可设置该预算，在审计前调用 **reorganize_capsules()**，并在
   围栏合成 Gate A 验证中提供规范记录。该路径不表示已安装插件完成 Provider 接入、语义质量评测
   或生产就绪。
7. 启用可选语义审计时，worker 进入 **COMPILING -> AUDITING**；否则直接进入
   **READY_TO_COMMIT**。**strict_audit=false** 只影响这一步可选审计。
8. 处于 **READY_TO_COMMIT** 时，**candidate_snapshot_id** 指向 worker 局部、状态为
   **CANDIDATE** 的不可变 wire envelope；worker 还持有封闭候选 Capsule envelope 与有序
   membership。它们已通过 Schema 校验，但对读者不可见。
9. **TX_PUBLISH_SNAPSHOT** 校验 fence 以及永久 target/coverage 关系。可发布 envelope 必须
   具有 **audit_outcome.mechanical_passed=true**、语义状态 **NOT_RUN** 或 **PASSED**、
   无 failure code、合法同会话 Capsule/source membership，并为每个新 Capsule 和 Snapshot
   恰有一个规范指标。显式提供重组记录时，记录必须规范；每个非
   **narrative_summary** 的 **released** 记录都会加入 **QUALITY_COVERAGE_GAP**，独立于
   可选语义审计而拒绝发布。
10. 在一个外层事务中，发布为每个新候选行打开一个 savepoint。它插入新不可变
    **capsules**、以 **COMMITTED** 形态插入 Snapshot、插入有序
    **snapshot_capsules**、写入显式有序 **snapshot_reorganization_records**、写入规范
    指标，随后执行 active-pointer CAS。bootstrap
    （**base_snapshot_id=null**、**base_pointer_version=0**）会有条件地以版本 **1**
    创建缺失的 **active_snapshots** 行；已有 base 则有条件地更新 Snapshot/version 匹配的
    行并递增版本。两种形式都要求新覆盖严格大于活动覆盖。
11. 成功时，Capsule、Snapshot、membership、可选账本行、规范指标、pointer 和 Job 原子提交。
    候选竞争分类器只把合格的 Capsule、Snapshot 或 membership 插入完整性冲突和 pointer
    冲突映射为 **SUPERSEDED**，回滚 savepoint 中的每项候选写入。账本写入完整性错误与
    规范指标错误则回滚外层事务并传播，不能改标为 **SUPERSEDED**。陈旧 fence 会拒绝整个
    操作。
12. 任一终态分支提交前，只要持久意图超过获胜 active coverage，同一事务必须保留或创建
    后续 **PENDING** 工作，并使用获胜 Snapshot 与 pointer version。

冻结 target 之上的 Journal 事件仍是之后视图和 Job 的 Delta。发布永不删除 Journal 数据。

## 失败与恢复流

编译器、审计器、回调或 worker 异常跨越调度器迭代边界后进入 **TX_FAIL_JOB**，它持久化
**error_stage**、**error_code** 和脱敏 **error_message**，再选择 **RETRY_WAIT** 或
**FAILED**。调度器必须继续处理无关 Job。

在 **TX_PUBLISH_SNAPSHOT** 中，合格的 Capsule、Snapshot 或 membership 插入完整性冲突、
bootstrap-create 竞争或 existing-pointer CAS 冲突，会通过发布 savepoint 回滚每条新候选
Capsule、**snapshot_capsules** membership、可选账本行、规范指标与未发布 Snapshot，并在
带 fence 的外层事务中记录 **SUPERSEDED**。失败 worker 不得覆盖获胜 Snapshot，也不得留下
孤儿候选内容。账本写入完整性错误和规范指标错误不属于该分类器，必须改为回滚整个事务。
若持久意图仍高于获胜覆盖，事务必须为获胜 base 排定后续工作。陈旧 owner/epoch 影响零行，
不能记录 **SUPERSEDED**。

启动时及周期性地，**TX_RECOVER_EXPIRED_LEASES** 将过期的非终态 Job 移到 **PENDING**，
清除 owner/expiry 并保持 **lease_epoch** 单调。对于过期 **READY_TO_COMMIT**，它还会清除
worker 局部 **candidate_snapshot_id**，下一领取者必须重新编译。它不得修改
**journal_events**、**snapshots** 或 **active_snapshots**。

实时通道 tokenizer 失败只会以 BYTE 模式重放当前请求，不会重写持久规范指标。规范计数器
失败会保留或创建不含正文的 backfill intent 并延期压缩，绝不以实时或兼容单位替代。

## 事务清单

| 事务 | 持久职责 |
| --- | --- |
| **TX_CAPTURE_USER_EVENT** | 幂等用户追加、序号分配与规范指标或 intent |
| **TX_CAPTURE_ASSISTANT_EVENT** | 幂等助手追加、序号分配与规范指标或 intent |
| **TX_CAPTURE_TOOL_EVENT** | 幂等工具追加、序号分配与规范指标或 intent |
| **TX_READ_REQUEST_VIEW** | 一致读取 active Snapshot、高水位与未覆盖 Delta |
| **TX_RAISE_COMPACTION_INTENT** | 单调、持久的触发意图合并 |
| **TX_CLAIM_JOB** | 合格选择、fencing 递增与租约获取 |
| **TX_FAIL_JOB** | 持久化错误与重试或终态迁移 |
| **TX_PUBLISH_SNAPSHOT** | 在一个 savepoint 中带 fence 地发布 Capsule、Snapshot、membership、可选账本和规范指标，执行 active-pointer CAS、Job 提交与后续意图 |
| **TX_RECOVER_EXPIRED_LEASES** | 启动和周期性重排队，不修改内容 |
| **TX_BACKFILL_TOKEN_METRICS** | 有界、所有权检查后的指标 CAS 与匹配 intent 删除 |
