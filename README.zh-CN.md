# AstrContinuum / 星续

[English](./README.md) | 简体中文

[![版本](https://img.shields.io/badge/version-v0.3.0-A44742)](./CHANGELOG.md)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.24.2%2C%3C5.0.0-1F6B5B)](https://github.com/AstrBotDevs/AstrBot)
[![许可证](https://img.shields.io/badge/license-AGPL--3.0--or--later-263238)](./LICENSE)
[![CI](https://github.com/2718labs/astrbot_plugin_astrcontinuum/actions/workflows/ci.yml/badge.svg)](https://github.com/2718labs/astrbot_plugin_astrcontinuum/actions/workflows/ci.yml)

**面向 AstrBot 的非阻塞、持久化长上下文运行时。** AstrContinuum 将权威会话事件写入 SQLite Journal，从已提交 Snapshot 加连续 Delta 构造请求上下文，仅向 Provider 请求临时投影自身上下文，并在 AstrBot 持久化完成回合前按对象身份恢复原生对象。

> **v0.3.0 技术预览。** 本版本在持久化发布边界中加入不可变的重组账本。标准 `CompactionWorker` 目前不会调用重组引擎，因此常规运行发布的是空账本。CRM/重组接线、面向用户的回滚和大范围性能认证仍属于后续工作。

## 当前可用边界

| 范围 | 当前事实 |
| --- | --- |
| 持久化权威 | 追加式 Journal、不可变 Capsule/Snapshot、SQLite 事务、fencing 与 CAS 发布。 |
| 请求链 | Snapshot 加 Delta 的读取、确定性受限组装，以及可精确恢复的 Provider 专属临时投影。 |
| 压缩 | 持久化意图和后台 worker 生命周期；发布前执行精确来源候选校验。 |
| Token 与存储基线 | 离线 Token profile、上下文窗口回退、加密持久化值和有界指标回填。 |
| v0.3 账本 | 仅当显式发布器提供记录时才写入的、仅存储型不可变审计账本。 |

持久化核心与 AstrBot 组合根刻意分离。核心中“已实现”不等于它已经作为 AstrBot 命令或后台运行能力对外暴露。

## 隔离实验数据

下图是**隔离、冻结的 R2 合成评测**，不是生产基准、语义质量结论，也不能证明 v0.3 已接线重组功能。它被保留在文档中，是为了让发布内容拥有可追溯的可视化参考，同时不掩盖其边界。

<img src="./docs/assets/evidence-r2-outcomes-rmb.svg" width="860" alt="图 R2：冻结 R2 聚合结果的双面板点图，固定分母 n=36；仅限研究，不是 v0.3 生产能力证据。">

<sub>图 R2｜冻结合成聚合结果；数值为计数/36（%），公开聚合数据不提供不确定性区间或假设检验。</sub>

实验来源、哈希、修订、排除项及配套流程见 [Evidence](./docs/EVIDENCE.md) / [证据说明](./docs/EVIDENCE.zh-CN.md) 与 [Workflow](./docs/WORKFLOW.md) / [工作流](./docs/WORKFLOW.zh-CN.md)。

## 安装

将仓库克隆到 AstrBot 的插件目录，然后重启 AstrBot 或从 WebUI 重新加载插件。

```bash
cd AstrBot/data/plugins
git clone https://github.com/2718labs/astrbot_plugin_astrcontinuum
```

声明的兼容范围为 **AstrBot `>=4.24.2,<5.0.0`**。本地 AstrBot 实加载仍是发布门槛，因为静态测试无法证明动态 Hook 注册或 Provider 消息行为。

## 运维速览

- `/context_status` 提供不含会话内容的管理员健康与聚合状态。
- `/context_inspect` 提供当前会话的不含内容证据。
- 暂无公开命令暴露压缩、重组、回滚、密钥管理或破坏性数据库管理。
- 会话派生的持久化值已加密；外部密钥必须位于插件数据目录和版本控制之外。

配置、隐私、恢复与失败行为请以[配置参考](./docs/CONFIGURATION.zh-CN.md)、[AstrBot 接入契约](./docs/ASTRBOT_INTEGRATION.zh-CN.md)和[数据库模式](./docs/DATABASE_SCHEMA.zh-CN.md)为准，而不是只依赖本概览。

## 文档

| 主题 | English | 简体中文 |
| --- | --- | --- |
| 架构与运行时边界 | [Architecture](./docs/ARCHITECTURE.md) | [架构](./docs/ARCHITECTURE.zh-CN.md) |
| 配置与密钥管理边界 | [Configuration](./docs/CONFIGURATION.md) | [配置参考](./docs/CONFIGURATION.zh-CN.md) |
| AstrBot Hook 所有权与运维 | [AstrBot integration](./docs/ASTRBOT_INTEGRATION.md) | [AstrBot 集成](./docs/ASTRBOT_INTEGRATION.zh-CN.md) |
| 压缩与 v0.3 发布边界 | [Compaction protocol](./docs/COMPACTION_PROTOCOL.md) | [压缩协议](./docs/COMPACTION_PROTOCOL.zh-CN.md) |
| 数据流转 | [Data flow](./docs/DATA_FLOW.md) | [数据流](./docs/DATA_FLOW.zh-CN.md) |
| 证据边界 | [Evidence](./docs/EVIDENCE.md) | [证据说明](./docs/EVIDENCE.zh-CN.md) |
| 发布工作流 | [Workflow](./docs/WORKFLOW.md) | [工作流](./docs/WORKFLOW.zh-CN.md) |
| 交付边界与下一道门 | [Roadmap](./docs/ROADMAP.md) | [路线图](./docs/ROADMAP.zh-CN.md) |
| 持久化表与事务 | [Database schema](./docs/DATABASE_SCHEMA.md) | [数据库模式](./docs/DATABASE_SCHEMA.zh-CN.md) |
| 验证范围 | [Test matrix](./docs/TEST_MATRIX.md) | [测试矩阵](./docs/TEST_MATRIX.zh-CN.md) |

## 开发与验证

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy astrcontinuum main.py
uv run pytest -q
```

不要把本地 Atlas 索引、缓存、任务包或机器相关产物加入发布提交。贡献与验证要求见 [CONTRIBUTING.zh-CN.md](./CONTRIBUTING.zh-CN.md)，安全问题请按 [SECURITY.zh-CN.md](./SECURITY.zh-CN.md) 提交。

## 许可证

Copyright © 2026 Ayleovelle.

本项目采用 [GNU Affero General Public License v3.0 or later](./LICENSE)。
