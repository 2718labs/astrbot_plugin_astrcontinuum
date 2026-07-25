# AstrBot 接入计划

实际编码前必须核对目标 AstrBot 版本的真实对象和签名。

## on_llm_request

只允许：

- 获取 session key
- 读取 committed snapshot
- 读取 snapshot 后的 Delta
- 本地检索
- Token 预算装配
- 重构 req.contexts 或临时动态注入
- 记录 Assembly Trace

禁止：LLM 摘要、等待压缩 job、长网络请求、全库扫描和迁移。

## on_llm_response / on_agent_done

- 写入完整 assistant event
- 关闭 turn
- 非阻塞通知 scheduler
- 立即返回

## 工具钩子

- 保存 tool name、args、result metadata
- 大结果外置
- 上下文保留预览、摘要和读取引用
- 路径、哈希、关键参数进入 Exact Anchor

## custom_compressor

只作为最终保险丝，不构成主产品架构。

## 会话隔离

会话键必须区分平台、bot instance、私聊/群聊、group_id、user_id、persona 和 conversation id。
