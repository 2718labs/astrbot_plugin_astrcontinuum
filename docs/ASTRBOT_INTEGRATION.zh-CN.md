# AstrBot 接入契约

[English](./ASTRBOT_INTEGRATION.md) | 简体中文

本文规定仓库版本 `v0.2.1` 中 AstrContinuum 与 AstrBot 的真实边界。它不是“计划使用的
API 清单”。凡是修改 Hook 职责、优先级、消息投影、请求身份或插件生命周期，都必须同步
更新本文，并通过真实 `PluginManager` 兼容探针。

## 1. 组合入口

`main.py` 只注册一个 `Star` 子类，负责：

- 通过 `StarTools` 获取插件数据目录；
- 幂等执行 SQLite 迁移并创建 Repository；
- 创建 AstrBot Hook 桥接；
- 在事件对象中保存仅当前请求可见的状态；
- 通过公开 Provider 元数据解析窗口，并冻结一个请求局部 tokenizer profile；
- 执行有界上下文装配与临时投影；
- 验证恢复、写入助手事件并持久化压缩意图；
- 维护当前 Provider 亲和性并适配 AstrBot 精确引文生成；
- 启动、唤醒并在插件终止时关闭受跟踪 worker。

领域、运行时、压缩和存储包不导入 AstrBot。宿主私有对象统一隔离在
`astrcontinuum.adapters.astrbot` 后面。

## 2. 已验证宿主范围

公开元数据声明 `>=4.24.0,<5.0.0`。提交态插件归档已通过官方 AstrBot 分发包验证：

| AstrBot | Python | 验证范围 |
| --- | --- | --- |
| `4.24.0` | `3.12.13` | Star 加载、八个 Hook、真实 Provider/ProviderRequest 元数据、离线计数、零次 LLM 调用 |
| `4.26.7` | `3.12.13` | 同一发布探针 |

AstrBot `4.24.0` 会对缺失的 `StarMetadata.pages` 打印宿主自身回退警告，但不影响本插件
加载或行为。通过一个抽样版本只代表证据覆盖，不能推导所有后续 `4.x` 都已经验证。

## 3. 权威事件 Hook

只有以下四个 Hook/Event/Role 组合可以追加 Journal：

| Hook | Event type | Role |
| --- | --- | --- |
| `on_llm_request` | `USER_MESSAGE` | `USER` |
| `on_agent_done` finalizer | `ASSISTANT_MESSAGE` | `ASSISTANT` |
| `on_using_llm_tool` | `TOOL_CALL` | `TOOL` |
| `on_llm_tool_respond` | `TOOL_RESULT` | `TOOL` |

`on_llm_response` 只能观察，不能写助手事件，否则会与唯一写入者 `on_agent_done` 重复。
确定性幂等键配合 SQLite 唯一约束，保证重复/并发回调只产生一行，也不会多消耗序号。
工具回调仍分别构成权威事件，但请求装配把工具调用与对应结果作为一个不可拆分的选择单元。

## 4. Hook 顺序与职责

| Handler | 优先级 | 职责 | 禁止事项 |
| --- | ---: | --- | --- |
| `on_llm_request` | `2000` | 写用户事件、冻结 `H`、读取 Snapshot+Delta、记住当前 Provider | 远程审计/编译、等待 worker、全库扫描、迁移 |
| `on_agent_begin_guard` | `2000` | 在投影前记录 request/message 的精确身份 | 替换或复制宿主历史 |
| `on_agent_begin_project` | `-100` | 评估压力；必要时用有界临时 Provider View 替换原生历史 | 修改原生消息或持久历史 |
| `on_agent_done_restore` | `2000` | 删除自有投影并保留 Provider 新增 Delta | 按值重建宿主历史 |
| `on_agent_done_finalize` | `900` | 验证恢复、写助手事件，并在有压力时持久化/唤醒归约意图 | 在线发布候选 Snapshot |
| `on_using_llm_tool` | `0` | 保存有界工具调用元数据 | 保存任意对象表示 |
| `on_llm_tool_respond` | `0` | 保存有界工具结果元数据 | 保存无限结果 |
| `on_llm_response` | `0` | 可选的无内容观测 | 任何 Journal 写入 |

两组 `on_agent_begin` / `on_agent_done` 故意使用不同优先级。修改它们会改变保护的是哪一组
对象、恢复发生在 AstrBot 和其他插件的哪个阶段，因此属于架构变更。

## 5. 请求级状态

AstrContinuum 在带命名空间的 event extra 中保存私有 `_RequestState`，其中只有当前请求
需要的引用：原始 request、冻结读视图、窗口来源、不可变 tokenizer/预算 profile、投影
guard、投影/恢复结果、压力决策、请求局部 Conversation 副本、助手事件、工具序号与脱敏
故障。它不是持久真源，不能进入 SQLite 或日志；缺失时应有界 fail-open。

`on_llm_request` 只通过公开同步 `Context.get_using_provider(umo=...)` 与 Provider 字段
读取活动模型和正数 `max_context_tokens`。`model_context_limit=0` 在模型一致时使用
`AUTO_ASTRBOT`；元数据缺失、非法或不一致时使用 `AUTO_SAFE_FALLBACK=128000`；正整数
配置是 `MANUAL`。读取这些值不会发起 Provider 调用。

## 6. 会话身份

适配层从宿主提取完整七元 `SessionKey`：

```text
platform_instance_id
message_type
session_id
group_id
user_id
conversation_id
persona_id
```

可空的 `group_id` / `persona_id` 使用规范表示。完整键保留在 wire envelope 中，SHA-256
只用于物理查询。若宿主无法提供必需身份，本请求跳过 AstrContinuum，不能写入含糊会话。

## 7. 投影能力边界

当前投影依赖 AstrBot 的 `Message`、`TextPart`、`extra_user_content_parts` 和
`mark_as_temp` / `_no_save`，使用前必须运行时探测。未达到 Provider View 阈值时完全
不动原生列表；达到阈值后，只为本轮 Provider 调用移除原生历史并加入临时视图。临时
消息无法创建时，空 Provider View 仍会保留 system 对象和当前输入。

恢复必须按对象身份进行：

1. 保存 request 和每个原生 message 的身份；
2. 保留 system/current 对象，移除原生历史，只追加自有临时对象；
3. 允许 AstrBot 替换 list，也允许 Provider/Agent 追加自己的 Delta；
4. 按对象身份定位精确 current-user 边界；
5. 恢复精确原生对象序列，并保留 Provider 合法新增对象；
6. 校验消息身份，不要求 list 容器身份或序列化值相等。

“值相等”不能替代“同一个对象”；即使重建的字典内容一样，也会侵犯宿主所有权并可能破坏
后续持久化。

投影前会按冻结的请求 profile 计算完整源工作集，装配后再按同一 profile 计算最终完整
Provider 投影。tokenizer 构造或任一计数失败时，所有部分结果都会被丢弃，整次请求以
`utf8-byte-v1` 重放；BPE 与 BYTE 绝不混用。随包提供的 `cl100k_base` 和
`o200k_base` 只离线加载，不使用可变下载缓存。

## 8. 故障与日志

兼容或不变量失败只能生成有界 `AdapterFault`（错误码、阶段、计数），不能包含消息内容或
对象表示。在线请求保持可用：

- 身份提取失败：本请求不采集、不投影；
- Provider 窗口元数据失败：使用不含正文的 `AUTO_SAFE_FALLBACK=128000`；
- tokenizer 失败：丢弃部分结果，以 BYTE 重算整次请求；
- 硬压力以下投影能力缺失：持久采集继续，使用原生上下文；
- 硬压力下装配/临时消息失败：使用有界空 Provider View；
- 投影/恢复不变量失败：在安全范围内移除自有增强，然后让宿主继续；
- finalizer 失败：不能伪造助手行或 Snapshot 覆盖。

## 9. 生命周期与当前限制

初始化阶段迁移数据库、构造持久服务与精确引文式编译器，并启动唯一受跟踪 worker。
finalizer 只在上下文压力需要时提高并唤醒持久归约意图；终止阶段先取消和等待 worker，
再清理服务。

剩余接入限制是可选的 Provider 语义审计适配器。逐字来源校验、机械校验、fencing、
原子发布、离线 token profile 与规范指标 sidecar 已经启用。

## 10. 修改核对

每次接入变更都要：

1. 查目标 AstrBot 的真实源码与签名，不凭记忆写 API；
2. 使用私有字段前先更新能力探测；
3. 跑单元和 SQLite 集成测试；
4. 在 AstrBot `4.24.0` 与 `4.26.7` 运行 `scripts/probe_astrbot.py`；
5. 断言八个 handler、公开 Provider/ProviderRequest 元数据路径、离线计数和零次 LLM 请求；
6. 兼容声明至少覆盖下界与最新验证样本；
7. 同步更新中英文本文及测试矩阵。

相关文档：[架构](./ARCHITECTURE.zh-CN.md)、[数据流](./DATA_FLOW.md)、
[测试矩阵](./TEST_MATRIX.md)和 [ADR-006](./ADR-006-ASTRBOT-HOOK-OWNERSHIP.md)。
