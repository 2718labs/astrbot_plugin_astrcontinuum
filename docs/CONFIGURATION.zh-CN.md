# 配置参考

[English](CONFIGURATION.md) | 简体中文

本页是 `_conf_schema.json` 的公开参考。它说明配置边界，不表示每项可选接入都已在某个
AstrBot 宿主上启用。不得提交密钥、密钥文件位置、Provider 凭据、本地数据库或机器专属配置。

## 安全默认值

| 设置 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `enabled` | `bool` | `true` | 启用 AstrContinuum 的可选请求增强。 |
| `context_engine_mode` | `string` | `active` | `active` 只在完整校验通过后采用图引擎结果；`shadow` 只测量、不替换确定性结果；`off` 只走确定性路径。 |
| `model_context_limit` | `int` | `0` | `0` 从 AstrBot 公开 Provider 元数据读取当前窗口；元数据缺失或不兼容时保守回退到 `128000`。正数为手动上限。 |
| `target_input_budget` | `int` | `130000` | 希望保留的装配输入上限。 |
| `hard_input_ceiling` | `int` | `150000` | 受控 Provider View 的安全上限。 |
| `compaction_start_ratio` | `float` | `0.75` | 可以提高持久化压缩意图的占用比例。 |
| `provider_view_switch_ratio` | `float` | `0.80` | 可以选择临时受控 Provider View 的占用比例。 |

在线 Hook 不等待编译器、语义审计器、后台 worker 或远端模型。修改预算或比例不会允许部分发布，
也不会绕过持久化不变量。

## 密钥管理

| 设置 | 类型 | 默认值 | 边界 |
| --- | --- | --- | --- |
| `encryption_key_source` | `string` | `local` | 可选 `local`、`file` 或 `environment`。`local` 会在 AstrBot 插件数据目录中创建并复用服务器端密钥。 |
| `encryption_key_file` | `string` | 空 | 只在 `encryption_key_source=file` 时显示；它是外部密钥文件路径，绝不是密钥正文。 |
| `encryption_previous_key_file` | `string` | 空 | 只在 `file` 模式的受控换钥期间显示。 |

使用 `environment` 时，应由外部密钥管理系统提供文档所述环境变量；不要把秘密填入 WebUI、聊天、
日志、命令行参数或仓库文件。文件模式换钥时，应按运维流程停止或重载宿主，确认
`/context_status` 显示新密钥已生效且维护完成，再删除旧密钥引用。披露流程见
[安全说明](../SECURITY.zh-CN.md)。

## Provider 与兼容设置

| 设置 | 类型 | 默认值 | 边界 |
| --- | --- | --- | --- |
| `compaction_provider_id` | `string` | 空 | 成功解析时使用显式压缩 Provider；留空则跟随当前对话 Provider。若既没有显式 Provider，也没有公开的当前 Provider 能力，Provider 绑定 worker 通道保持缺席，宿主请求 fail-open。 |
| `thinking_compat_openai_provider_ids` | `list` | `[]` | 高级 allowlist：仅填写已确认兼容 OpenAI Chat Completions 的 Provider ID。当活动模型需要临时兼容副本时使用；绝不改写宿主持久历史。 |

标准 `CompactionWorker` 不调用 CRM/重组引擎。设置 Provider 不会改变 v0.3.0 的边界，也不会把
不可变账本变成自动重组流程。

## 运维检查

修改配置后：

1. 通过 AstrBot 重启或重载插件。
2. 用 `/context_status` 查看不含正文的健康、密钥、模型窗口与 worker 状态。
3. 用 `/context_inspect` 只查看当前会话的不含正文证据。
4. 作出发布决策前，按[测试矩阵](TEST_MATRIX.zh-CN.md)运行完整测试与 AstrBot probe 门槛；
   配置界面不等于兼容性回执。

相关页面：[AstrBot 接入契约](ASTRBOT_INTEGRATION.zh-CN.md)、
[架构](ARCHITECTURE.zh-CN.md)与[安全说明](../SECURITY.zh-CN.md)。
