# ADR-003：契约权威与冲突处置

- 状态：Phase 0 已接受
- 范围：架构与持久化契约

## 决定

AstrContinuum 必须按以下顺序解决需求冲突：

1. 用户 Master Prompt 与明确的后续约束；
2. 支持版本范围内、已核验的 AstrBot 官方源码；
3. 启动包的 `specs/` 与测试；
4. 2718lab DevKit 规则；
5. 其他全部文档。

发生冲突时，必须记录互相竞争的主张、证据、选定权威和兼容性影响；实现和文档不得静默
选择其中一方。

`specs/` 下的 JSON Schema 是持久化和编排信封形状的权威。标准 JSON Schema 无法表达的
跨记录语义——包括目标和覆盖字段的算术关系、事务边界、连续覆盖、活动指针 CAS 以及 Hook
所有权——由运行时不变量和已接受 ADR 负责，必须由永久机械校验器执行；共享 SQLite 行的字段
还应由数据库约束保护。Markdown 只是投影；2718lab 工作流 SQLite 记录才是调度权威。

## 永久规则

以下检查无论配置如何都必须启用：

- 连续覆盖和来源高水位校验；
- 结构 Schema 校验；
- SessionKey 身份校验；
- 精确锚点保留；
- 编译器输出非空；
- 原子发布和活动指针 CAS。

`strict_audit=false` 只能关闭可选的模型语义审查；不得关闭上述任何永久规则，也不得允许机械
校验失败的候选发布。

外部 Sylanne 记忆文本不得写入 `journal_events`。`persona_id` 仅是身份元数据；本项目不得
虚构 Sylanne API。

## 依赖权威

AstrBot 宿主安装依赖必须在仓库根目录 `requirements.txt` 中声明；本地 lint、类型检查和测试
工具必须在 `pyproject.toml` 中声明。运行插件需要的包不得只出现在 `pyproject.toml`；仅开发
工具不得伪装为 AstrBot 宿主依赖。

AstrBot 集成应优先使用 `astrbot.api.*`。只有同时满足下列条件时，才可例外使用
`astrbot.core.*` 内部符号：没有已核验的公开等价能力；ADR 记录精确导入路径和观察版本；使用
被隔离在一个 adapter 边界；缺失导入或属性有明确 fail-open/fail-closed 策略；该例外被跟踪为
经版本检查的技术债。ADR-006 记录了唯一的 Phase 0 `TextPart` 例外；不得按类比推断公开
tokenizer、响应写入器或 Sylanne memory API。

## 变更控制

凡是变更表、字段、wire state、事务名、SessionKey 分量、Hook 所有者或覆盖含义的后续修改，
都必须在一次经审阅的变更中更新相关 Schema、ADR、数据流、数据库文档、并发状态机和测试矩阵。
重命名稳定 wire value 属于兼容性变更，而非文字清理。

Phase 0 文档不得授权运行时实现、迁移、发布，或修改 `main.py`、`astrcontinuum/**/*.py`。

## 验证

文档或 Schema 写入只有绑定输入索引查询轨迹、写前 checkpoint、输出索引查询轨迹和验证产物后
才算完成。即使 Markdown 检查通过，若文字与更高权威冲突，审阅者也必须拒绝。
