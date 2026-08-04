# ADR-007：v1 Capsule 持久化

状态：已接受

> 更新：v0.3.0 的发布写入顺序、账本可见性和冲突分类由 ADR-008 局部修订；本 ADR 的 Capsule
> 持久化决策保持有效。

## 背景

起始 Schema 将大部分语义表达为通用 claim，并且只用 `session_id` 标识 Capsule。这一形状与
Master Prompt、封闭的七分量 SessionKey 契约，以及 Snapshot 必须是可追溯来源的结构化状态而非
散文摘要的要求相冲突。

发布还需要一个回滚边界。输掉的发布者不得留下可被 reader 误认为已提交状态的孤儿 Capsule 或
membership 行。

## 决定

Master Prompt 的语义字段具体化为独立的 `goals`、`constraints`、`decisions`、`progress`、
`open_loops`、`preferences`、`entities`、`emotional_context`、`exact_anchors` 和 `dependencies`
数组。每个活动 claim、决定、实体、锚点和依赖都带来源 event id。

每个 Capsule 按以下规范顺序嵌入完整 SessionKey：`platform_instance_id`、`message_type`、
`session_id`、`group_id`、`user_id`、`conversation_id`、`persona_id`。

Capsule 是不可变、闭合的结构化记录。`narrative_summary` 仅用于导航，绝不替代结构字段或其来源。

Snapshot 通过有序 `snapshot_capsules` membership 引用 Capsule。membership 顺序是权威；反规范化
id 列表不得成为第二数据源。

新的候选 `capsules`、对应 `snapshot_capsules` membership 和已提交 Snapshot 必须在活动指针 CAS
之前插入同一个 savepoint。若同前缀唯一性或指针 CAS 失败，发布必须回滚每个新候选 Capsule、
membership 和 Snapshot 行，然后将受 fencing 保护的输方 Job 提交为 `SUPERSEDED`。过期 fencing
token 必须拒绝整个操作，且不得记录 `SUPERSEDED`。

Capsule 和 Snapshot membership 必须使用相同的 SessionKey 身份。所有引用 source event 必须存在于
该 session；插入前 exact-anchor membership 必须与 Capsule 内容一致。

Sylanne memory 不是 Capsule 来源，也不会由 AstrContinuum 持久化。

## 后果

JSON Schema 是权威 wire envelope。SQLite 存储规范 Capsule JSON 以及索引身份和覆盖列，而有序
membership 被规范化为 `snapshot_capsules`。reader 仍只解析活动已提交 Snapshot；候选内容不可见，
并且能在发布 savepoint 中完整移除。
