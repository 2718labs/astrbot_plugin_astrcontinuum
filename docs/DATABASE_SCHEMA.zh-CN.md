# v0.3.0 数据库 Schema

[English](DATABASE_SCHEMA.md) | 简体中文

## 约定

持久化目标为 SQLite。opaque id 是非空文本；时间戳是 UTC RFC 3339 文本；布尔值为整数
`0` 或 `1`；JSON 列保存插入前已验证的规范 JSON。Journal sequence 和 Snapshot 覆盖索引
从 `1` 开始。bootstrap `EMPTY_BASE` 的逻辑覆盖为 `0`，但它不是 Snapshot Schema 信封、
数据库行或 active pointer。

必须启用外键。写事务与约束是正确性边界；进程局部锁只能作为可选优化。wire-envelope
枚举值使用稳定的大写值。仅存储的 v0.3.0 重组账本使用单独记录的小写值 `retained`、
`approximate` 和 `released`。wire envelope 是封闭对象。SQLite 规范化必须精确 round-trip
每个必填 wire 字段，且不得把物理查找列或 JSON 存储列暴露成额外 wire 属性。

当前安全格式以经认证的 `acenc:v1:` AES-256-GCM 信封保存会话派生文本、结构化信封、
兼容计数和规范 token 指标。加密不改变逻辑 wire 值或兼容字节计数。物理
`*_envelope` 列和加密 JSON wrapper 是存储细节，不得成为额外 wire 属性。

标准 JSON Schema 无法比较任意记录之间的字段。永久机械验证器必须在发布前强制
`covered_event_end = source_high_water_mark = compaction_jobs.target_high_water_mark`；
同一行的算术关系也应尽可能通过 SQLite `CHECK` 约束实现。

## `sessions`

| 列 | 规则 |
| --- | --- |
| `session_key_hash` | TEXT PRIMARY KEY；规范 `SessionKey` 序列化的 SHA-256 |
| `canonical_session_key_json` | TEXT NOT NULL UNIQUE；精确的有序身份对象 |
| `platform_instance_id` | TEXT NOT NULL，非空 |
| `message_type` | TEXT NOT NULL，非空 |
| `session_id` | TEXT NOT NULL，非空 |
| `group_id` | TEXT NULL |
| `user_id` | TEXT NOT NULL，非空 |
| `conversation_id` | TEXT NOT NULL，非空 |
| `persona_id` | TEXT NULL；仅身份元数据 |
| `next_event_sequence` | INTEGER NOT NULL CHECK `>= 1` |
| `created_at`、`updated_at` | TEXT NOT NULL，UTC RFC 3339 |

规范元组顺序必须是 `platform_instance_id`、`message_type`、`session_id`、`group_id`、
`user_id`、`conversation_id`、`persona_id`。规范 JSON 与反规范化字段必须一致。不得存储
persona memory 正文。Event、Snapshot 和 Job projection 必须从
`canonical_session_key_json` 重建必需的 wire `session_key`；`session_key_hash` 是额外的
物理查找键，不能取代 `session_key`，也不得与其并列出现在封闭 wire envelope 中。

## `journal_events`

| 列 | 规则 |
| --- | --- |
| `event_id` | TEXT PRIMARY KEY |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `sequence` | INTEGER NOT NULL CHECK `>= 1` |
| `event_type` | TEXT NOT NULL：`USER_MESSAGE`、`ASSISTANT_MESSAGE`、`TOOL_CALL` 或 `TOOL_RESULT` |
| `role` | TEXT NOT NULL：`USER`、`ASSISTANT` 或 `TOOL` |
| `content` | TEXT NOT NULL |
| `source_hook` | TEXT NOT NULL：`ON_LLM_REQUEST`、`ON_AGENT_DONE`、`ON_USING_LLM_TOOL` 或 `ON_LLM_TOOL_RESPOND` |
| `idempotency_key` | TEXT NOT NULL，非空 |
| `token_count_envelope` | TEXT NOT NULL；保存非负逻辑兼容字节计数的 `acenc:v1:` 信封 |
| `created_at` | TEXT NOT NULL，UTC RFC 3339 |

数据库或 repository 验证必须恰好强制以下三元组：

- `USER_MESSAGE / USER / ON_LLM_REQUEST`；
- `ASSISTANT_MESSAGE / ASSISTANT / ON_AGENT_DONE`；
- `TOOL_CALL / TOOL / ON_USING_LLM_TOOL`；
- `TOOL_RESULT / TOOL / ON_LLM_TOOL_RESPOND`。

不得接受 `ON_LLM_RESPONSE` 作为 `source_hook`。必需的唯一性约束是
`UNIQUE(session_key_hash, sequence)` 和
`UNIQUE(session_key_hash, source_hook, idempotency_key)`。行只追加。采集事务必须原子地
分配 `sessions.next_event_sequence` 并插入；幂等冲突要返回已有行，不得推进 sequence。
Repository projection 会把物理 `session_key_hash` 映回必需的嵌入式 wire `session_key`。

## `capsules`

逻辑表契约如下：

```sql
CREATE TABLE capsules (
    capsule_id TEXT PRIMARY KEY,
    session_key_hash TEXT NOT NULL,
    level TEXT NOT NULL,
    covered_event_start INTEGER NOT NULL CHECK (covered_event_start >= 1),
    covered_event_end INTEGER NOT NULL CHECK (covered_event_end >= covered_event_start),
    canonical_capsule_json TEXT NOT NULL,
    token_cost_envelope TEXT NOT NULL,
    source_coverage REAL NOT NULL CHECK (source_coverage BETWEEN 0 AND 1),
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_key_hash) REFERENCES sessions(session_key_hash)
);
```

`canonical_capsule_json` 在插入前必须通过封闭的 v1 Capsule Schema 验证，并精确 round-trip
完整嵌入式 `SessionKey` 和每一条结构化语义记录。`source_coverage` 是
`quality.source_coverage` 的物理索引 projection，不是额外 wire 字段。

在安全格式中，`canonical_capsule_json` 是加密 JSON wrapper，`token_cost_envelope` 保存经
认证的非负逻辑兼容计数。Capsule 行不可变。任何顶层或嵌套来源事件 id 缺失、属于其他
session 或落在 Capsule 覆盖之外时，永久验证器必须拒绝该 Capsule。Sylanne memory 正文
不是可接受的来源事件。

## `snapshot_capsules`

有序成员关系契约如下：

```sql
CREATE TABLE snapshot_capsules (
    snapshot_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    capsule_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ordinal),
    UNIQUE (snapshot_id, capsule_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (capsule_id) REFERENCES capsules(capsule_id)
);
```

规范关系约束为：

```text
PRIMARY KEY (`snapshot_id`, `ordinal`)
UNIQUE (`snapshot_id`, `capsule_id`)
```

成员关系行不可变，并且是 Snapshot wire `capsule_ids` 顺序的权威来源。插入前，永久验证器
必须证明 Capsule 和 Snapshot 具有相同 `session_key_hash`，每个 Capsule 来源事件都存在于
该 session，并且 Snapshot 的精确锚点成员关系与有序 Capsule 内容一致。必须拒绝跨 session
成员关系和悬空的 Capsule、来源事件或锚点引用。

## `snapshot_reorganization_records`（migration v3）

这张 v0.3.0 表是某个已提交 Snapshot 的源项重组不可变审计账本；它不能替代 Capsule
溯源或 Snapshot 质量。

| 列 | 规则 |
| --- | --- |
| `snapshot_id` | TEXT NOT NULL REFERENCES `snapshots(snapshot_id)` |
| `ordinal` | INTEGER NOT NULL CHECK `>= 0`；保留调用方顺序 |
| `source_capsule_id`、`kind`、`item_id` | TEXT NOT NULL 且非空；三者共同标识 Snapshot 内一个源项 |
| `status` | TEXT NOT NULL：`retained`、`approximate` 或 `released` |
| `before_tokens`、`after_tokens` | INTEGER NOT NULL CHECK `>= 0` |
| `required` | INTEGER NOT NULL CHECK `IN (0, 1)` |

主键为 `PRIMARY KEY(snapshot_id, ordinal)`；源身份通过
`UNIQUE(snapshot_id, source_capsule_id, kind, item_id)` 保持唯一。更新和删除 trigger 令行保持
不可变。savepoint 打开前，repository canonicalization 要求准确的 record/status/boolean 类型、
非空源身份、非负且不是布尔值的 token 计数、唯一的源身份，以及仅当
`status=retained` 时 `required=true`。

`released` 是审计处置，不是绕过质量下限的许可。非 `narrative_summary` 的 `released` 记录
会在永久验证时添加 `QUALITY_COVERAGE_GAP`，即使 `strict_audit=false` 也阻止发布。账本行
只在其 `COMMITTED` Snapshot 行存在后写入，按 `ordinal` 排序，且只能经由已提交 Snapshot
读取。Repository 发布 API 的记录是可选项：常规 Provider 绑定／默认 AstrBot 路径不提供记录，
所以账本为空的已提交 Snapshot 合法；可选的注入式／围栏 Gate A 路径可在重组后提供规范记录。

## `snapshots`

Snapshot 的 wire-to-SQLite 映射是规范性的：

| Wire 字段 | SQLite 表示 |
| --- | --- |
| `session_key` | 将 `session_key_hash` 连接到 `sessions.canonical_session_key_json`，并重建嵌入对象 |
| `capsule_ids` | 将 `snapshot_capsules` 按 `ordinal` 连接；至少一个唯一且非空 id |
| `exact_anchor_ids` | `exact_anchor_ids_json`：唯一非空 id 的规范 JSON 数组；允许为空 |
| `audit_outcome` | `audit_outcome`：规范 JSON 对象 |
| `state` | `lifecycle_state`；该表只允许 `COMMITTED` |

| 列 | 规则 |
| --- | --- |
| `snapshot_id` | TEXT PRIMARY KEY |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `base_snapshot_id` | TEXT NULL REFERENCES `snapshots(snapshot_id)` |
| `covered_event_end` | INTEGER NOT NULL CHECK `>= 1` |
| `source_high_water_mark` | INTEGER NOT NULL CHECK `>= covered_event_end` |
| `exact_anchor_ids_json` | TEXT NOT NULL，规范 JSON 数组 |
| `rendered_context` | TEXT NOT NULL `acenc:v1:` 信封 |
| `token_cost_envelope` | TEXT NOT NULL；保存非负逻辑兼容计数的 `acenc:v1:` 信封 |
| `audit_outcome` | TEXT NOT NULL；保存逻辑规范 audit 对象的加密 JSON wrapper |
| `lifecycle_state` | TEXT NOT NULL；CHECK 值为 `COMMITTED` |
| `created_at`、`committed_at` | TEXT NOT NULL，UTC RFC 3339 |

wire `state` 枚举仅为 `CANDIDATE` 或 `COMMITTED`。`CANDIDATE` 信封必须具有
`committed_at=null`；`COMMITTED` 信封必须具有非空的 UTC RFC 3339 `committed_at`。插入后行
不可变。worker 局部 `CANDIDATE` 信封是封闭 Schema 对象，但在 Phase 0 不插入；job 上预分配
`candidate_snapshot_id`，`TX_PUBLISH_SNAPSHOT` 直接将最终行插为 `COMMITTED`。有序
`snapshot_capsules` 行是 wire `capsule_ids` 唯一的持久权威；不得存在与它竞争的反规范化 id
数组。每个插入行都必须有 `audit_outcome.mechanical_passed=true`、
`semantic_status` 为 `NOT_RUN` 或 `PASSED`，且 `failure_codes` 为空。对于 compaction 输出，
永久验证器必须确立 `source_high_water_mark = covered_event_end =
compaction_jobs.target_high_water_mark`。`UNIQUE(session_key_hash, covered_event_end)` 防止
为同一前缀提交两个表示；发布期间违反它必须与 pointer CAS 冲突一样进入隔离的
`SUPERSEDED` 路径。

## `active_snapshots`

| 列 | 规则 |
| --- | --- |
| `session_key_hash` | TEXT PRIMARY KEY REFERENCES `sessions` |
| `snapshot_id` | TEXT NOT NULL UNIQUE REFERENCES `snapshots` |
| `pointer_version` | INTEGER NOT NULL CHECK `>= 1` |
| `updated_at` | TEXT NOT NULL，UTC RFC 3339 |

每个 session 最多有一个 active Snapshot。第一次发布前没有行；逻辑 `EMPTY_BASE` 不插入。
`TX_PUBLISH_SNAPSHOT` 必须使用两种 CAS 形式之一：bootstrap（`base_snapshot_id=null` 且
`base_pointer_version=0`）条件式地插入缺失行，`pointer_version=1`；已有 base
（`base_snapshot_id` 非空且 `base_pointer_version>=1`）条件式地更新 `snapshot_id` 与
`pointer_version` 都匹配的行，然后递增版本。两种形式都必须拒绝
`covered_event_end` 未严格大于当前逻辑／active 覆盖的新 Snapshot。Snapshot 只通过此表对
读取器可见。

## `compaction_jobs`

| 列 | 规则 |
| --- | --- |
| `job_id` | TEXT PRIMARY KEY |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `state` | TEXT NOT NULL；稳定的 Job state enum |
| `target_high_water_mark` | INTEGER NOT NULL CHECK `>= 1`；每个已 claim 尝试冻结 |
| `intent_target_high_water_mark` | INTEGER NOT NULL CHECK `>= target_high_water_mark` |
| `base_snapshot_id` | TEXT NULL REFERENCES `snapshots` |
| `base_pointer_version` | INTEGER NOT NULL CHECK `>= 0`；bootstrap 时为 `0` |
| `candidate_snapshot_id` | TEXT NULL |
| `lease_owner` | TEXT NULL |
| `lease_epoch` | INTEGER NOT NULL CHECK `>= 0`；单调递增 |
| `lease_expires_at` | TEXT NULL，UTC RFC 3339 |
| `attempt_count` | INTEGER NOT NULL CHECK `>= 0` |
| `next_retry_at` | TEXT NULL，UTC RFC 3339 |
| `error_stage`、`error_code`、`error_message` | TEXT NULL；message 必须脱敏 |
| `created_at`、`updated_at`、`committed_at` | TEXT；前两者 NOT NULL，提交时间可空 |

Job wire envelope 要求每个声明字段均存在。条件不提供值的可空字段必须显式设为 `null`；
省略无效。状态条件如下：

- `base_snapshot_id=null` 当且仅当 `base_pointer_version=0`；非空 base 要求
  `base_pointer_version>=1`。
- working 状态 `LEASED`、`COMPILING`、`AUDITING` 与 `READY_TO_COMMIT` 需要非空
  `lease_owner` 和 `lease_expires_at`，并且 `lease_epoch>=1`。其他状态要求 owner 和 expiry
  显式为 null。
- `candidate_snapshot_id` 只在 `READY_TO_COMMIT`、`COMMITTED` 和 `SUPERSEDED` 时非空；
  所有其他状态要求显式为 null。
- `error_stage`、`error_code` 和 `error_message` 是全有或全无的元组：三个值都必须显式为
  null，或全部是带有脱敏 message 的非空字符串。`RETRY_WAIT` 与 `FAILED` 要求非空形式。
  其他状态可使用全 null 形式，或保留前一次尝试的完整脱敏元组；任何部分元组都无效。
- `next_retry_at` 只在 `RETRY_WAIT` 时非空；其他状态必须显式为 null。
- `committed_at` 只在 `COMMITTED` 时非空；其他状态必须显式为 null。

`state` 必须为 `PENDING`、`LEASED`、`COMPILING`、`AUDITING`、`READY_TO_COMMIT`、
`RETRY_WAIT`、`COMMITTED`、`SUPERSEDED`、`FAILED` 或 `CANCELLED` 之一。Job wire envelope
不改变这些完整字段名；`session_key_hash` 会投影回嵌入式 `session_key`。

数据库必须在每个 session 至多保持一条非终态 intent 链，例如以非终态上的 partial unique
index 实现。`TX_RAISE_COMPACTION_INTENT` 必须通过取最大 intent target 来合并重复触发。

## `token_metrics`

规范计数是派生 sidecar，绝不能替代兼容 wire 字段：

| 列 | 规则 |
| --- | --- |
| `artifact_kind` | TEXT NOT NULL：`EVENT`、`CAPSULE` 或 `SNAPSHOT` |
| `artifact_id` | TEXT NOT NULL，非空 |
| `tokenizer_profile_id` | TEXT NOT NULL，非空且不可变的 profile identity |
| `metric_envelope` | TEXT NOT NULL；只含 schema version 与非负 token count 的 `acenc:v1:` 信封 |
| `created_at` | TEXT NOT NULL，UTC RFC 3339 |

主键为 `(artifact_kind, artifact_id, tokenizer_profile_id)`。相同逻辑 count 的重复写入幂等；
该身份的不同 count 为 `TOKEN_METRIC_CONFLICT`。认证记录键绑定全部三个键字段，因此在
artifact 或 profile 之间交换加密 metric envelope 会认证失败。

## `token_metric_backfill_intents`

| 列 | 规则 |
| --- | --- |
| `session_key_hash` | TEXT NOT NULL REFERENCES `sessions` |
| `artifact_kind` | TEXT NOT NULL：`EVENT`、`CAPSULE` 或 `SNAPSHOT` |
| `artifact_id` | TEXT NOT NULL，非空 |
| `tokenizer_profile_id` | TEXT NOT NULL，非空 |
| `created_at` | TEXT NOT NULL，UTC RFC 3339 |

主键与 `token_metrics` 一致。行标识不含正文的缺失工作，不含消息正文或 count。backfill 以
稳定且有界的顺序读取已认证 artifact 文本，重新检查 session 归属，在同一事务内写 metric
并删除其 intent。崩溃后只会保留旧 intent 或已完成 metric，不会留下未跟踪的部分状态。

## 原子事务

| 名称 | 必需的原子效果 |
| --- | --- |
| `TX_CAPTURE_USER_EVENT` | Upsert／验证 session、幂等检查、分配 sequence、追加映射后的 user event |
| `TX_CAPTURE_ASSISTANT_EVENT` | Upsert／验证 session、幂等检查、分配 sequence、追加映射后的 assistant event |
| `TX_CAPTURE_TOOL_EVENT` | Upsert／验证 session、幂等检查、分配 sequence、追加映射后的 tool event |
| `TX_READ_REQUEST_VIEW` | 从一个 read snapshot 中读取 active pointer／Snapshot、Journal 高水位和未覆盖的有序事件 |
| `TX_RAISE_COMPACTION_INTENT` | 创建／合并非终态工作，并单调提升 intent target |
| `TX_CLAIM_JOB` | 选择符合条件的 job、递增 fencing epoch、冻结 target、设置 lease、进入 `LEASED` |
| `TX_FAIL_JOB` | 按 owner/epoch fencing、持久化脱敏错误、进入 `RETRY_WAIT` 或 `FAILED`、清除 lease |
| `TX_PUBLISH_SNAPSHOT` | 执行 fencing 和严格覆盖前进及规范账本记录的机械验证；打开一个 savepoint；插入新的 candidate Capsule、已提交 Snapshot、有序成员关系和有序重组记录；bootstrap CAS-create 或 existing-pointer CAS-update；现有 classifier 将 Capsule／Snapshot／成员关系插入完整性冲突和 pointer 冲突映射为 `SUPERSEDED`，回滚每个新 candidate 行，并将更高 intent 保留为基于胜者 base 的后续工作；账本插入完整性错误则传播 |
| `TX_RECOVER_EXPIRED_LEASES` | 将过期 working job 重新排队并清除 owner／expiry；对于过期 `READY_TO_COMMIT` 还清除 candidate id，使下一次 claim 重新编译 |
| `TX_BACKFILL_TOKEN_METRICS` | 重新检查 artifact/session 归属，以 CAS 写入有界规范 metric 批次，并原子删除匹配 intent |

每个采集事务还会随新 event 提交规范 Event metric 或匹配的 backfill intent 之一。
`TX_PUBLISH_SNAPSHOT` 要求并原子提交每个新 Capsule 和新 Snapshot 恰好一个规范 metric。
metric 绝不能独立于其描述的 artifact 推进 active pointer。

`TX_PUBLISH_SNAPSHOT` 必须在同一个 savepoint 内、active-pointer CAS 之前插入所有新编译的
`capsules`、已提交 Snapshot、有序 `snapshot_capsules` 行和有序
`snapshot_reorganization_records` 行。已有的不可变 base Capsule 可以被引用，但绝不重写。
必需的物理顺序是 Capsule → Snapshot → 成员关系 → 账本 → CAS，因为成员关系和账本行都
对 Snapshot 有立即外键。

如果 savepoint 之前的验证失败，发布会在写入 candidate 内容之前拒绝。现有
candidate-conflict classifier 将 Capsule／Snapshot／成员关系插入完整性冲突、
`UNIQUE(session_key_hash, covered_event_end)`、bootstrap CAS-create 冲突或 existing-pointer
CAS-update 影响零行视为隔离冲突路径。它必须回滚内层 savepoint，以移除每个新的 candidate
Capsule、成员关系、Snapshot 和账本行。外层事务必须保留 `candidate_snapshot_id`，把受
fence 约束的 job 记为 `SUPERSEDED`，并清除其 lease。提交前必须读取胜出的 active Snapshot
和 pointer version；当持久 `intent_target_high_water_mark` 超过胜者覆盖时，必须留下或创建
使用该胜者 base 的 `PENDING` 后续工作。过时 fencing predicate 必须拒绝整个尝试的迁移，
且不得记录 `SUPERSEDED`。写账本本身发生完整性错误不属于预期发布冲突：必须传播并回滚整个
外层事务。
