# 为 AstrContinuum 贡献

[English](./CONTRIBUTING.md) | 简体中文

> 本文件是 [English 原文](./CONTRIBUTING.md) 的简体中文镜像。代码符号、字段名、状态和不变量标识符在两种语言中均须保持完全一致；如有歧义，以英文原文为准。

感谢你帮助改进 AstrContinuum。该插件会改变会话状态被捕获、组装、临时投影、恢复并最终压缩的方式。因此，看似局部的改动也可能影响持久化历史或 AstrBot 的原生请求图。在修改运行时行为前，请阅读[架构文档](./docs/ARCHITECTURE.zh-CN.md)和相关 ADR。

## 提交 Issue 之前

- 搜索已有的 Issue 和讨论。
- 从日志中移除消息内容、令牌、账户标识符、数据库行以及其他私密数据。
- 对于缺陷，请记录 AstrContinuum 的版本/提交、AstrBot 版本、Python 版本、操作系统、适配器/平台以及最小复现。
- 对安全敏感问题，请使用[私密漏洞报告](./SECURITY.zh-CN.md)。

## 开发环境

AstrContinuum 支持 Python `>=3.10`；CI 会在 Linux 上验证 Python 3.10 至 3.13，并在 Windows 上验证 Python 3.12。

```bash
git clone https://github.com/2718labs/astrbot_plugin_astrcontinuum
cd astrbot_plugin_astrcontinuum
uv sync --frozen --extra dev
```

运行完整的本地质量门：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy astrcontinuum main.py
uv run pytest -q
```

如果修改 `main.py`、元数据、配置、Hook 装饰器、请求投影或 AstrBot 适配器代码，还必须通过真实的 AstrBot `PluginManager` 加载插件。

## 分支、提交和 Pull Request

- 从最新的 `main` 分支创建分支。
- 每个 Pull Request 只处理一个连贯目标。
- 优先使用 `fix:`、`feat:`、`docs:`、`test:` 或 `chore:` 等 Conventional Commit 标题。
- 行为变化时，在同一个 Pull Request 中更新测试和文档。
- 完成 Pull Request 检查清单，并附上精确的验证命令及结果。
- 不要提交生成的数据库、日志、Provider 载荷、缓存、本地配置或机密信息。

## 架构变更证据

如果 Pull Request 改变任何持久化 schema、事件权威来源、会话身份、Hook 所有权/优先级、投影/恢复行为、预算语义、Snapshot 覆盖范围、压缩状态迁移、租约/fencing 行为或发布事务，即属于架构变更。

这类 Pull Request 必须包含：

1. **受影响的不变量：**指出[测试矩阵](./docs/TEST_MATRIX.zh-CN.md)中的 `INV-*` 条目，并说明每一项是否保持不变。
2. **持久化兼容性：**提供向前迁移、回滚/恢复行为，以及对既有 Journal/Snapshot/job 行的处理方式。
3. **失败语义：**说明每个异常边界和进程丢失边界的结果。
4. **并发证据：**测试过期租约、重复回调、竞争发布或该变更影响的其他竞态。
5. **AstrBot 证据：**指出实际测试的宿主版本和适配器。
6. **文档：**按适用范围更新架构、数据流、schema、ADR、配置和 changelog 材料。

若行为会产生持久化数据库结果，任何测试都不得只以日志或返回值证明正确性。必须断言行、指针、覆盖范围和状态迁移。

## 不可协商的运行时规则

- 实时请求 Hook 绝不等待压缩、Provider 支持的审计、长时间网络调用或全库维护。
- Journal 只能追加，且只记录来自源 Hook 的权威映射。
- 读取者看到的是一个已提交的 Snapshot 加上连续的后覆盖 Delta。
- 候选内容在原子发布成功前不可见。
- 投影只追加 AstrContinuum 自己拥有的临时对象。
- 恢复会保留 AstrBot 的原生对象精确身份和 Provider 添加的 Delta。
- 缺少私有 AstrBot 能力时应以 fail-open 方式降级，绝不伪造成功。
- 日志只包含代码和有界元数据，不包含会话内容或对象表示。

## 依赖变更

运行时依赖必须同时写入 `requirements.txt`（AstrBot 安装）和 `pyproject.toml`（本地/项目安装）。仅用于开发的工具应放在 `dev` extra 中。重新生成 `uv.lock`，说明引入依赖的理由，并谨慎维持版本边界。Tokenizer 变更必须保持共享的 `tiktoken>=0.12,<0.14` 契约，以及 `THIRD_PARTY_NOTICES.md` 中固定的离线资产大小/SHA-256 记录；不接受运行时下载或可变的 tokenizer 缓存。

## 文档

默认落地页和主要技术文档以英文为先。变更 README 或架构声明时，必须在同一个 Pull Request 中更新相连的简体中文镜像。代码符号、字段名、状态和不变量标识符在两种语言中必须保持完全一致。

## 发布契约

`v0.3.0` 具有确定性的 AstrBot 安装包契约。作出发布决定前：

- 运行冻结依赖的质量门和专用发布测试；
- 在受跟踪树和解包后的归档中运行随附的 2718lab 校验器；
- 在声明下限 `4.24.2` 与最新已验证样本 `4.26.7` 上探测公共 AstrBot
  Provider/ProviderRequest 路径；
- 使用 `scripts/build_plugin_archive.py` 构建，并使用 `scripts/verify_plugin_archive.py` 验证；
- 确认归档包含唯一的 `astrbot_plugin_astrcontinuum/` 顶层目录、仅包含 allowlist 内的文件、包含固定的 tokenizer 资产，并且大小小于 16 MiB。

AstrBot 市场分发是独立的维护者流程。CI 的归档任务不会提交至市场、创建标签或发布远程 Release。
