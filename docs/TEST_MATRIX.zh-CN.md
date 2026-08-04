# v0.3.0 测试矩阵

[English](TEST_MATRIX.md) | 简体中文

> 本页列出规范性验收所需的测试；表中要求本身不表示它们已经通过。版本、宿主探针和
> 发布完成度必须以当前可复现的测试证据为准。

## 测试层级

- 单元测试：纯身份、验证、状态迁移和装配行为。
- 集成测试：真实 SQLite 事务、约束、fencing 和并发连接。
- 崩溃恢复：事务／状态边界的进程丢失，以及启动时重新排队。
- AstrBot smoke：在受支持 AstrBot 版本上验证的宿主 hook 与适配器能力。
- 发布测试：依赖／版本／文档／归档契约、隔离验证器和确定性包复现。

每个规范性测试都必须断言持久行和状态，而不能只断言返回值或日志。

## 运行时不变量

| 不变量 | 必需测试 | 层级 |
| --- | --- | --- |
| INV-001 | 对 `on_llm_request` 做插桩；断言它采集 user event 并读取已提交状态，不等待 compiler、auditor、scheduler 完成或远端模型。被阻塞的 worker 不得阻塞该 hook。 | 单元 + AstrBot smoke |
| INV-002 | 预置 active 覆盖 `40`，追加 `41..44`，在 `H=44` 读取；断言恰有一个已提交的 active Snapshot 加有序 `41..44`。再读取一个没有 pointer 的新 session：断言逻辑 `EMPTY_BASE`、`C=0`、事件 `1..H`，且没有伪造 Snapshot 行。并发追加／发布必须得到全在之前或全在之后的视图。 | 集成 |
| INV-003 | 在每个 candidate Capsule、Snapshot、成员关系和账本插入阶段之后、pointer/job 完成之前注入失败；断言回滚后没有 candidate 内容，pointer 未变。对于 CAS 冲突，断言回滚到 savepoint 会移除所有新的 Capsule、成员关系、账本和 Snapshot 行，而受 fence 约束的外层事务持久化 `SUPERSEDED` 并保留 candidate id。账本完整性失败则回滚整个发布事务。读取器绝不观察到 candidate。 | 集成 + 崩溃恢复 |
| INV-004 | 强制 `EMERGENCY_ASSEMBLY`；断言 prompt 材料缩小，而 Journal 行数、sequence、active pointer 和覆盖不变。 | 单元 + 集成 |
| INV-005 | 对规范元组顺序、null `group_id`/`persona_id`、规范 JSON 和 SHA-256 稳定性做 golden test。断言任一分量改变都会改变查找身份。 | 单元 |
| INV-006 | 投递重复／并发的 `on_llm_request` 和 `on_agent_done` callback，以及 `on_llm_response`；断言只有一条 user 行、一条 assistant 行和零条 response-hook Journal 行。 | 集成 + AstrBot smoke |
| INV-007 | 提供 persona id 与外部 Sylanne memory 文本；断言只保存 persona 元数据，memory payload 不出现在任何 Journal 内容中。 | 单元 + 集成 |
| INV-008 | 从 active Snapshot 加连续 Delta 编译；拒绝缺失中间事件、首尾 sequence 错误、过时 base、丢失 exact anchor、丢失既有语义和超出 target 的覆盖。 | 单元 + 集成 |
| INV-009 | 参数化 `strict_audit`；断言结构、身份、覆盖、exact-anchor 和非空检查始终执行，`false` 时只跳过可选模型 audit。 | 单元 |
| INV-010 | 运行竞争 claim 与过期 worker 发布；断言 epoch 单调并拒绝 stale 写入。在 active 工作期间提高 intent；只要 intent 超过胜出的 active 覆盖，就要在 `COMMITTED` 和 `SUPERSEDED` 两种结果后断言存在后续 `PENDING` 工作。 | 集成 + 崩溃恢复 |
| INV-011 | 冻结一个 request profile；计算该 profile 中的 host input、candidate、原子 tool-call/result 对和最终 projection。在每一阶段注入构造／计数失败；断言丢弃全部主路径结果，并以 `utf8-byte-v1` 重放整个请求，绝不混用单位。 | 单元 + 集成 |
| INV-012 | 解析 `MANUAL`、匹配的公开 AstrBot Provider metadata、缺失 metadata、无效 metadata 和 request/provider model 不匹配。断言只输出 `AUTO_ASTRBOT`、`AUTO_SAFE_FALLBACK=128000` 或 `MANUAL`，且不发送 Provider request。 | 单元 + AstrBot smoke |
| INV-013 | 对 Event、Capsule 和 Snapshot，断言加密规范 sidecar identity、逻辑 CAS、artifact 原子发布、有界 backfill、崩溃恢复和不可变兼容字节计数。缺少规范 metric 时必须延期 compaction，不得借用 live count。 | 集成 + 崩溃恢复 |
| INV-014 | 只加载带 size/SHA-256 pin 的 bundled tokenizer asset，网络和可变 cache 均关闭。断言 unknown-model reference policy 和整数基点 multiplier 行为。 | 单元 + 发布 |

## 状态与失败迁移

| 迁移或失败 | 必需断言 | 层级 |
| --- | --- | --- |
| `PENDING -> LEASED` | 恰好一个 claimant 胜出；设置 owner/expiry，epoch 与 attempt 递增，target 冻结。 | 集成 |
| 到期 `RETRY_WAIT -> LEASED` | 到期前 claim 失败；到期 claim 以更高 epoch 成功。 | 单元 + 集成 |
| `LEASED -> COMPILING` | owner/epoch 匹配时成功；不匹配或 lease 已过期时改变零行。 | 集成 |
| `COMPILING -> AUDITING` | 仅在启用 semantic audit 且机械检查通过时发生。 | 单元 |
| `COMPILING -> READY_TO_COMMIT` | audit 关闭时，candidate id 必须存在，且每项机械检查都已通过。 | 单元 + 集成 |
| `AUDITING -> READY_TO_COMMIT` | 被接受的 audit 记录 outcome 和 candidate id；被拒绝的 audit 不得进入 ready 状态。 | 单元 + 集成 |
| working -> `RETRY_WAIT` | 可重试 compiler/auditor/callback/worker 异常持久化 stage/code/脱敏 message、backoff，并清除 lease。Scheduler 继续处理另一个 job。 | 集成 |
| working -> `FAILED` | 致命或耗尽异常持久化终止错误并清除 lease；scheduler 继续存活。 | 集成 |
| 规范 metric 不可用 -> `RETRY_WAIT` | 保留 attempt budget，持久化稳定且不含正文的 code，保留／重建 backfill intent，绝不在 metric 不完整时发布。 | 集成 + 崩溃恢复 |
| `READY_TO_COMMIT -> COMMITTED` | Snapshot 插入、pointer CAS、job 提交和清除 lease 为原子动作；存在 committed timestamp 和 candidate id。Snapshot audit 为 mechanical true、semantic `NOT_RUN` 或 `PASSED`，且没有失败。 | 集成 + 崩溃恢复 |
| `READY_TO_COMMIT -> SUPERSEDED` | 覆盖同一前缀的 Snapshot 唯一性、竞争 bootstrap 创建和已有 pointer 更新冲突。每种情况都只通过 savepoint／等价机制回滚失败 Snapshot 的插入；外层事务持久化 `SUPERSEDED`、保留 candidate id、清除 lease，且胜者不变。若 intent 超过胜者覆盖，断言 `PENDING` 后续工作使用胜者的 base/version。 | 集成 |
| `PENDING`/`RETRY_WAIT -> CANCELLED` | 显式管理操作成功；对 working/terminal 状态的取消被拒绝。 | 单元 + 集成 |
| 过期 working -> `PENDING` | 启动恢复清除 owner/expiry、保留 epoch，且不修改 Snapshot 或 Journal 行。对于过期 `READY_TO_COMMIT`，还会清除 candidate id；下一次 claim 重新编译和验证新的 candidate。 | 崩溃恢复 |
| 禁止的迁移 | 每个未列出的状态对都被拒绝，且不产生持久变更。 | 单元 + 集成 |

## 数据库与流转契约

event-enum 测试 fixture 必须恰好接受 `USER_MESSAGE/USER/ON_LLM_REQUEST`、
`ASSISTANT_MESSAGE/ASSISTANT/ON_AGENT_DONE`、`TOOL_CALL/TOOL/ON_USING_LLM_TOOL` 和
`TOOL_RESULT/TOOL/ON_LLM_TOOL_RESPOND`。

| 契约 | 必需测试 | 层级 |
| --- | --- | --- |
| Event enum 映射 | 只接受四个 `event_type/role/source_hook` 三元组；拒绝所有交叉配对，也拒绝将 `ON_LLM_RESPONSE` 作为 Journal 来源。 | 单元 + 集成 |
| 精确 wire 字段 | 对 Event、Snapshot 和 Job，断言必填字段名与其 Schema 精确匹配，并在 closed-object 验证中拒绝未知属性。 | 单元／Schema |
| Capsule 封闭信封 | 验证完整七分量 SessionKey、精确的必填顶层字段和每个嵌套对象的未知属性拒绝。持久化并重新加载一个 Capsule；断言结构化语义数组、版本／时间、质量、聚合 source id 和嵌套溯源精确 round-trip。 | 单元／Schema + 集成 |
| Capsule 成员关系完整性 | 插入有序 `snapshot_capsules` 行，断言持久 ordinal 顺序、主键和重复 Capsule 拒绝。拒绝跨 session 的 Capsule 成员关系、悬空 Capsule／source-event 引用、覆盖外 source id 以及与 Capsule 内容不一致的 exact-anchor 成员关系。 | 集成 |
| 重组账本完整性 | 发布规范记录，并只经由已提交 Snapshot 读取其不可变 ordinal 顺序。拒绝格式错误／重复的源身份、非布尔或负 token 值，以及没有 `retained` 的 `required=true`；强制主键、源身份唯一性、FK 和更新／删除不可变性。缺失或未提交 Snapshot 的读取被拒绝。 | 单元 + 集成 |
| Candidate Capsule 回滚 | 在每个 candidate 插入阶段注入进程丢失：新 Capsule 行、已提交形态的 Snapshot 行、每个有序成员关系阶段和账本插入。恢复后断言没有部分 candidate 行或 pointer 改变。另行强制同一前缀与 pointer CAS 失败；断言在受 fence 约束的 job 提交 `SUPERSEDED` 之前，每个新的 candidate 行（含账本行）都已回滚，而 stale fence 不提交任何内容。账本完整性错误必须回滚整个发布，而不是归类为 `SUPERSEDED`。 | 集成 + 崩溃恢复 |
| 仅 narrative 回归 | 使用 `narrative_summary` 为空的 compiler fixture，断言 closed-Schema 拒绝不抹除结构化输入；随后持久化一个非空但有意无用的导航 summary 的 Capsule，断言 goals、constraints、decisions、progress、open loops、preferences、entities、emotional context、anchors、dependencies 和 source id 不变。 | 单元／Schema + 集成 |
| 条件式 wire 状态 | 断言每个 Job 字段必填，且可空值为显式 null。base 为 null 当且仅当 pointer version 为 `0`；非空 base 要求版本 `>=1`。candidate id 只在 `READY_TO_COMMIT/COMMITTED/SUPERSEDED` 时非空。错误 stage/code/message 要么全为 null，要么全为非空字符串；拒绝任意部分元组。`RETRY_WAIT/FAILED` 要求非空形式，其他状态可保留前一次完整的脱敏元组。retry time 只在 `RETRY_WAIT` 时非空；committed time 只在 `COMMITTED` 时非空。working 状态要求 owner/expiry 与 epoch `>=1`。Snapshot 只接受 `committed_at=null` 的 `CANDIDATE` 或 `committed_at` 为非空 RFC 3339 的 `COMMITTED`。 | 单元／Schema |
| SQLite round-trip | 持久化并重新加载每个 envelope；断言嵌入 `session_key` 和 wire `state/capsule_ids/exact_anchor_ids/audit_outcome` 精确 round-trip，而 `session_key_hash/lifecycle_state/*_json` 保持物理专用。`capsule_ids` 只从按 ordinal 排序的 `snapshot_capsules` 重建。 | 集成 |
| Bootstrap request | 没有 `active_snapshots` 行时，断言 `EMPTY_BASE`/`C=0` 和 Delta `1..H`；断言 assembly 不创建 Snapshot 行或 pointer。 | 集成 |
| Active pointer CAS | 对 null base/version `0`，断言发布会条件式创建 version `1` 的缺失 pointer；两个 bootstrap／同前缀 publisher 中，一个为 `COMMITTED`，一个为 `SUPERSEDED`，无论失败者在 Snapshot 唯一性还是 pointer 创建处冲突。对于已有 base/version `>=1`，断言条件更新递增 version 和覆盖；相等或更低覆盖被拒绝。 | 集成 |
| Candidate projection | 验证 worker 局部 `state=CANDIDATE` 和 candidate Capsule，且不将其暴露；发布在一个 savepoint 内插入不可变 Capsule、有序成员关系、有序账本行和仅 `state=COMMITTED` 的 Snapshot 行。过期 ready 工作会在重新编译前清除 candidate id。 | 集成 + 崩溃恢复 |
| 永久跨字段检查 | 除非 `covered_event_end = source_high_water_mark = target_high_water_mark`、target 严格大于 active／逻辑覆盖，且 `COMMITTED` audit outcome 机械成功且没有 failure code，否则拒绝发布。非 `narrative_summary` 的 `released` 账本记录必须添加 `QUALITY_COVERAGE_GAP`；在 `strict_audit` 为 true 和 false 时均运行。 | 单元 + 集成 |
| User 幂等性 | 重复 `ON_LLM_REQUEST` key 返回已有 event，不消耗另一个 sequence。 | 集成 |
| Assistant 幂等性 | 重复 `ON_AGENT_DONE` key 返回已有 event，不消耗另一个 sequence。 | 集成 |
| Tool 幂等性 | 重复 `ON_USING_LLM_TOOL` 与 `ON_LLM_TOOL_RESPOND` key 各自生成一个正确映射的 event。 | 集成 + AstrBot smoke |
| 每 session 排序 | 并发 user、assistant 和 tool 插入产生唯一、连续 sequence；不同 session 保持独立身份。 | 集成 |
| 只追加 Journal | 更新／删除路径不存在或被拒绝；compaction 和 emergency assembly 保留所有行。 | 集成 |
| Active 唯一性 | 约束防止每个 session 有两个 active pointer，也防止 pointer 指向缺失／未提交 Snapshot。 | 集成 |
| 触发合并 | 重复／较低触发不降低 intent；较高并发触发获胜，但不改变当前已冻结 target。 | 集成 |
| 后续调度 | 在 intent 为 `44` 时提交 target `43`；断言覆盖为 `43` 且持久后续 target 为 `44`。随后强制与覆盖 `42`、intent `44` 的胜者发生 CAS 冲突；断言失败 job 为 `SUPERSEDED`，后续 target `44` 使用胜者的 Snapshot/version。 | 集成 + 崩溃恢复 |
| 读取高水位 | 在已捕获 `H` 之上追加；断言其不在当前视图中、在下一视图中出现。 | 集成 |
| 依赖职责 | 静态检查断言宿主运行时依赖在根 `requirements.txt`，仅本地工具在 `pyproject.toml`。 | 单元／静态 |
| Token metric 加密 | 以 `(artifact_kind, artifact_id, profile_id)` 认证记录键；拒绝调换／损坏 envelope 和逻辑 count 冲突，且不暴露 artifact 文本。 | 集成 + 安全 |
| 采集 metric 原子性 | 在 Event 插入后和 metric/intent 处理后注入失败；断言 event 加 metric-or-intent 一起提交，或两者均不提交。 | 集成 + 崩溃恢复 |
| 发布 metric 原子性 | 要求每个 candidate Capsule 和 Snapshot 各有一个规范 metric；注入失败与 CAS race，断言没有失败 candidate 的 metric 留在发布 savepoint 外。 | 集成 + 崩溃恢复 |
| Backfill 原子性 | 读取不超过配置上限的稳定批次，重新检查 session 归属，原子写入 metric／删除 intent。断言每个注入的崩溃边界后都可以重试。 | 集成 + 崩溃恢复 |
| 离线 asset 契约 | 断言精确的 `cl100k_base`/`o200k_base` 大小和 SHA-256、Git 二进制属性、无网络／cache 写入，以及 `tiktoken>=0.12,<0.14` 在项目 manifests 中一致。 | 单元 + 发布 |
| 发布归档 | 构建两次并比较字节；断言只有一个顶层插件目录、已排序的固定时间戳、严格 allowlist、已 pin asset、无 traversal／敏感／cache 路径、版本一致且大小低于 16 MiB。 | 发布 |

## AstrBot 兼容性 smoke 矩阵

| 情况 | 必需断言 |
| --- | --- |
| v4.24.2 声明下限的发布探针 | 发布决策前必须运行：`scripts/probe_astrbot.py` 加载 Star，观察恰好八个 hook，使用真实公开的 Provider/ProviderRequest/get_using_provider 对象，解析 model/window，使用 bundled asset 离线计数，且发送零次 LLM request。宿主自身的 `StarMetadata.pages` fallback warning 不是 AstrContinuum 失败。 |
| v4.24.0 历史 probe（低于当前声明下限） | `scripts/probe_astrbot.py` 加载 Star，观察恰好八个 hook，使用真实公开的 Provider/ProviderRequest/get_using_provider 对象，解析 model/window，使用 bundled asset 离线计数，且发送零次 LLM request。宿主自身的 `StarMetadata.pages` fallback warning 不是 AstrContinuum 失败。该历史样本不能取代当前声明下限的验证。 |
| v4.26.7 最新已验证样本 | 同一 probe 通过。这是截至该样本版本的证据，不是最高兼容上限，也不能证明所有中间／未来 build 都兼容。 |
| 缺少 `TextPart` import | Adapter 记录脱敏兼容错误，宿主 request 在没有 enhanced context 时继续。不得伪造 Journal 成功或覆盖变化。 |
| 缺少 `extra_user_content_parts` 或 `mark_as_temp` | 同样 fail-open；核心持久化和调度保持可用。 |
| `on_llm_response` 观测 | 可以产出 metric，但 Journal 行数和覆盖不变。 |

## 完成门槛

完成要求通过冻结的 Ruff/format/mypy/full-pytest gate、Python 3.10–3.13 和 Windows 3.12
CI 矩阵、release-contract 测试、两个 AstrBot probe、跟踪树与解包归档上的 vendored 2718lab
validator，以及确定性的归档验证报告。单独通过 Markdown/static 检查，绝不能覆盖失败的不变量、
迁移、SQLite 并发、崩溃恢复、宿主 probe 或打包门槛。
