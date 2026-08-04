# ADR-006：AstrBot Hook 所有权与 Adapter 边界

- 状态：已接受；`v0.3.0` 维护更新
- 真实 PluginManager 探针：历史 v4.24.0、v4.24.2 与 v4.26.7
- 已索引源码观察：v4.26.7（`fed29848ca0b3912ab6a8200a10cd0f2cb080f85`）

## 唯一写入者

`on_llm_request` 是当前用户输入写入 `journal_events` 的唯一权威写入者。它必须派生规范
SessionKey，执行幂等 `TX_CAPTURE_USER_EVENT`，并读取已提交请求状态。它不得等待 compiler、
auditor、scheduler 完成或远程模型调用。该路径上的压缩通知只能是有界持久化意图更新，不能是
同步压缩。

`on_agent_done` 是已完成助手输出的唯一权威写入者。只有当 agent completion 已具权威性后，它才
能执行幂等 `TX_CAPTURE_ASSISTANT_EVENT`。

`on_llm_response` 仅用于观察。它可以收集指标或脱敏诊断，但不得向 `journal_events` 追加用户或
助手内容，也不得推进覆盖。

工具生命周期事实有独立唯一写入者：`on_using_llm_tool` 写入 `TOOL_CALL/TOOL/ON_USING_LLM_TOOL`，
`on_llm_tool_respond` 写入 `TOOL_RESULT/TOOL/ON_LLM_TOOL_RESPOND`。用户和助手映射分别为
`USER_MESSAGE/USER/ON_LLM_REQUEST` 和 `ASSISTANT_MESSAGE/ASSISTANT/ON_AGENT_DONE`。没有回调可写入
其他 Hook 的映射组合；`ON_LLM_RESPONSE` 不是允许的 Journal `source_hook`。外部 Sylanne memory
文本不得复制进 Journal；`persona_id` 只是 SessionKey 元数据。

## 身份与幂等

每个 Hook 都必须使用相同的规范 SessionKey 元组，顺序为：`platform_instance_id`、
`message_type`、`session_id`、`group_id` 或 null、`user_id`、`conversation_id`、`persona_id` 或 null。
其规范序列化必须通过 SHA-256 生成 `session_key_hash`。

每次权威写入必须携带由稳定宿主投递或完成身份导出的确定性 `idempotency_key`，不能由进程局部
时间生成。数据库 `(session_key_hash, source_hook, idempotency_key)` 唯一约束必须让重复回调返回
已有事件而不分配新序号。并发插件 runner 必须依赖该数据库约束和事务隔离；内存锁不是正确性边界。

`(session_key_hash, sequence)` 唯一约束和 capture 事务的序号分配必须为每个 session 生成单调顺序。
重复 runner 不得创建两个活动 Snapshot，也不得绕过 Job fencing。

## 请求路径行为

在幂等捕获用户事件后，`on_llm_request` 必须通过 `TX_READ_REQUEST_VIEW` 装配视图：恰好一个已提交
活动 Snapshot，加上序号高于其覆盖终点且不高于事务高水位的 Journal 事件。`EMERGENCY_ASSEMBLY`
可以减少注入 Prompt 材料，但不得删除 Journal 行或推进 Snapshot 覆盖。

可选增强逻辑中的 Hook 错误必须对宿主请求 fail-open：请求在无增强上下文时继续，并记录脱敏错误。
fail-open 不得伪造成功 Journal 写入或修改覆盖。

## 隔离的 `TextPart` 例外

当前已核验的临时请求上下文能力需要此内部导入：

```python
from astrbot.core.agent.message import TextPart
```

它与 `ProviderRequest.extra_user_content_parts` 一起使用；`TextPart.mark_as_temp()` 将注入上下文
标记为临时。这里不主张存在已核验的 `astrbot.api.*` 等价物。

完整插件归档和能力路径曾在真实 AstrBot `PluginManager` 生命周期中验证：历史 v4.24.0、v4.24.2
和 v4.26.7。当前声明的兼容范围为 `>=4.24.2,<5.0.0`；v4.24.0 观察仅保留为历史探针证据。该历史
探针中的 AstrBot v4.24.0 会发出宿主自身 `StarMetadata.pages` 回退警告，但当时初始化、handler
注册、投影/恢复、持久化 Journal 行为和终止均通过。对 v4.26.7 的观察只是采样版本证据，不能保证
所有中间或未来构建；本 ADR 不得声称未经测试的兼容性。

此例外必须隔离在 AstrBot adapter 模块中。adapter 必须对导入、`extra_user_content_parts` 和
`mark_as_temp` 做版本/能力检查。导入或属性查找失败时，必须 fail-open：跳过增强上下文注入并记录
脱敏兼容错误。核心持久化、编译和调度代码不得导入 `astrbot.core.*` 或依赖 `TextPart`。

这个例外是经版本检查的技术债。一旦验证公开等价物，必须在不改变 Journal、Snapshot 或 Job
契约的前提下在 adapter 后替换。该决定不授权虚构公开 tokenizer API 或 Sylanne API。

## 证据

当前官方源码索引证据是 AstrBot workspace snapshot
`sha256:4892d3ed5e94ee1fb5366c0320085b3cbb394f8b376af29d7a1953e6c6a88d44`，位于
`fed29848ca0b3912ab6a8200a10cd0f2cb080f85`；Hook 查询轨迹
`sha256:95e4128299b85b4a16ba74ee72a944ad664d3f809b77cd1d1ca311d6d56675aa`；以及 ProviderRequest
查询轨迹 `sha256:932c9d5ea8ec30eb73a722b1b7bc47f673f15c57ad9fdb3b68e3d716dbec3397`。这些轨迹使该决定可对
已索引源码复核；不得把它们当成其他 AstrBot 版本的保证。

## 依赖放置

AstrBot 加载插件时需要的包属于根目录 `requirements.txt`。`pyproject.toml` 只用于本地开发和测试
工具。成功的本地测试环境不得当作 AstrBot 宿主安装具备全部运行依赖的证明。
