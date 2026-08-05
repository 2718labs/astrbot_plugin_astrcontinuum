# 非阻塞压缩协议

[English](./COMPACTION_PROTOCOL.md) | 简体中文

本文描述 **v0.3.0** Technical Preview。它保留已启用的 **v0.2.1** 压缩通道，并在 Repository
发布边界新增持久、可选的重组账本。常规 Provider 绑定／默认 AstrBot 路径不设置
**reorganization_token_budget**，因此发布空账本。独立的注入式 compiler backend 可在围栏合成
Gate A 验证通道中显式设置预算、重组候选并持久化记录；它不构成 Provider 或生产接入声明。

## 1. 安全目标

压缩可以失败、重试、竞争落败或随进程退出，但不能阻塞在线请求，也不能让候选内容提前可见。
只有新候选通过永久校验并赢得原子发布后，活动 Snapshot 才能变化；Journal 原始事件永不因
压缩而删除。

## 2. 持久状态机

~~~mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> LEASED: 领取
    RETRY_WAIT --> LEASED: 到期重试
    LEASED --> COMPILING
    COMPILING --> AUDITING: 启用语义审计
    COMPILING --> READY_TO_COMMIT: 机械校验通过
    AUDITING --> READY_TO_COMMIT: 审计通过
    LEASED --> RETRY_WAIT: 可重试失败
    COMPILING --> RETRY_WAIT: 可重试失败
    AUDITING --> RETRY_WAIT: 可重试失败
    READY_TO_COMMIT --> RETRY_WAIT: 可重试失败
    LEASED --> FAILED: 致命或次数耗尽
    COMPILING --> FAILED: 致命或次数耗尽
    AUDITING --> FAILED: 致命或次数耗尽
    READY_TO_COMMIT --> FAILED: 致命或次数耗尽
    READY_TO_COMMIT --> COMMITTED: 发布获胜
    READY_TO_COMMIT --> SUPERSEDED: 合格冲突或 CAS 竞争落败
    PENDING --> CANCELLED: 管理取消
    RETRY_WAIT --> CANCELLED: 管理取消
~~~

过期工作态恢复为 **PENDING**。恢复会清除租约，但不能降低 fencing epoch、修改 Journal
行或改变活动指针；过期的 **READY_TO_COMMIT** Job 还会丢弃 candidate identifier，使下一
worker 必须重新编译。

## 3. 意图、领取与 fencing

每个会话最多保留一个可执行 Job。提高意图不能降低目标高水位；重复或更低通知会被合并。
worker 领取时冻结：

- 已提交 base Snapshot 与指针版本；
- 目标高水位；
- 租约 owner 与 expiry；
- 单调增长的 fencing epoch；以及
- 自增 attempt。

每次工作态更新都把 owner 与 epoch 写进谓词。过期或陈旧 worker 必须影响零条持久记录。
进程内锁只是优化，数据库 fence 才是权威。

冻结的编译区间为：

~~~text
active/logical coverage + 1 .. target high-water
~~~

缺口、重叠、陈旧 base 或超出冻结 target 的覆盖都必须拒绝。若 **COMMITTED** 或
**SUPERSEDED** 后仍有更新意图，后续 **PENDING** 从获胜活动指针继续。

## 4. 候选流水线

worker 读取一个已提交 base 与连续 Delta，然后：

1. 至多处理一批有界、稳定排序的缺失 **canonical-o200k-v1** 指标；
2. 读取预期 active Snapshot、截止冻结 target 的连续 Delta，以及所需的规范 Event 指标；
3. 切分带角色标签的有界证据；
4. 只让所选 Provider 返回事件 id 与精确来源片段；
5. 拒绝缺失确认、额外字段、未知 id 和非逐字文本；
6. 编译不可变结构化 Capsule，并确定性渲染；
7. 校验身份、覆盖、来源、依赖、精确锚点、封闭 envelope 与规范指标完整性；
8. 可选执行语义损失审计；
9. 构造恰好覆盖冻结 target 的候选 Snapshot；以及
10. 所有强制校验通过后才进入 **READY_TO_COMMIT**。

缺失或不可用的规范指标会延期 Job，但不消耗一次普通失败 attempt。关闭可选语义审计，
也不能跳过结构、身份、覆盖、精确锚点、非空、规范指标或永久发布校验。

**v0.3.0** 的 Repository API 还可从显式发布者接收有序重组记录，并在发布前将其规范化。
任何 kind 不为 **narrative_summary** 的 **released** 记录都会加入
**QUALITY_COVERAGE_GAP**，即使 **strict_audit=false** 也会阻止发布。常规 Provider 绑定／默认
AstrBot 路径不设置重组预算，因此不走此路径；注入式 compiler backend 可在围栏合成 Gate A 中设置
该预算。该窄通道不能让普通后台工作被表述为具备重组覆盖。

## 5. 原子发布

候选内容在带 fence 的外层事务内通过 savepoint 发布：

1. 重读活动指针与 base version；
2. 插入新不可变 Capsule；
3. 插入 committed-form Snapshot；
4. 插入有序 Snapshot/Capsule membership；
5. 仅在显式发布者提供时写入有序重组账本记录；
6. 写入每个新 Capsule 与 Snapshot 的规范指标，并删除匹配的 backfill intent；以及
7. 对活动指针执行 compare-and-swap，将 Job 标记为 **COMMITTED**、清除租约并提交。

读者只能看到完整旧视图或完整新视图，不能看到半成品候选。账本读取器只接受已提交
Snapshot；候选或回滚行不可见。

现有 **SUPERSEDED** 分类器被有意限制在较窄范围：合格的 Capsule、Snapshot 或 membership
插入冲突，以及活动指针 CAS 落败，会回滚 savepoint。账本写入完整性错误不能改标为
**SUPERSEDED**，必须传播并回滚外层事务。规范指标错误同样不能发布部分候选。任何失败都
不能移动活动指针。

## 6. 失败分类

- **可重试：** SQLite 暂时争用，或有界编译、审计、回调异常；持久化脱敏错误元组，并在
  **RETRY_WAIT** 中退避。
- **指标延期：** 缺失或不可用的规范指标会保留或创建不含正文的补齐工作，并延期编译，
  不会以不兼容单位替代。
- **致命：** 封闭 envelope 无效、身份或覆盖不可能、策略拒绝或 attempt 耗尽；持久化
  **FAILED**。
- **竞争落败：** 另一有效发布者赢得合格唯一性冲突或 active-pointer CAS；这是竞争结果，
  不是数据损坏。
- **崩溃或租约过期：** 启动恢复把工作放回 **PENDING**；已提交数据不变。

诊断只允许阶段、代码和脱敏消息，不能记录会话内容或任意 Provider 对象表示。

## 7. 与在线请求隔离

**on_llm_request** 只读取已提交状态和捕获的高水位 **H**，永不等待 Job。编译期间新事件
继续进入 Delta。紧急装配可以缩短请求视图，但不能修改 Journal、Snapshot、覆盖、Job
状态、规范指标或重组账本。

## 8. 当前启用边界

安装后的预览版已经包含由 **Star** 管理的 claim loop、冻结压缩读取、lease 续租、有界取消、
Provider 选择、离线 tokenizer profile 与请求级 BYTE 回退、加密规范 token sidecar 与有界
补齐、脱敏重试、原子发布以及不含正文的状态/检查遥测。

Provider 驱动的语义审计适配器仍未绑定。常规 Provider 绑定／默认 AstrBot 路径继续发布空账本。
独立的注入式 compiler backend 可在围栏合成 Gate A 验证中以显式预算调用
**reorganize_capsules()**，再穿过同一持久化发布边界。本 Technical Preview 不构成公开 **v1.0**
兼容性、Provider 覆盖、语义质量、性能或面向最终用户发布就绪声明。

参见[数据流](./DATA_FLOW.zh-CN.md)、[并发状态机](./CONCURRENCY_STATE_MACHINE.zh-CN.md)、
[数据库 Schema](./DATABASE_SCHEMA.zh-CN.md)、[重组账本 ADR](./ADR-008-REORGANIZATION-LEDGER.zh-CN.md)
和[测试矩阵](./TEST_MATRIX.zh-CN.md)。
