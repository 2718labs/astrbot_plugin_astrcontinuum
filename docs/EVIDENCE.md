# 证据与实验摘要

> 这是一份证据边界页，不是 v0.3 的语义评测通过声明。当前 v0.3
> Technical Preview 的生产工作树只包含评测方案和发布安全契约；它没有
> 获准的真实语义模型／Provider 结果。

![冻结 R2 合成评测结果](assets/evidence-r2-outcomes-rmb.svg)

## 如何读这份证据

本页收录两类隔离研究记录：

1. **冻结 R2 合成评测**：12 个场景 × 3 次试验，共 36 个固定分母单元。
   它区分结构可用、回答交付和 claim-v2 端到端通过，不能替代真实用户、
   真实语义模型或生产性能验证。
2. **CRM V21-008 确定性压力记录**：12 轮、30 条输入记录、122,880
   输入字节的 replay-matching 记录。它量化预注册确定性 CRM 路径上的
   字节与损失记账，不能作为当前 v0.3 工作流已接线或可发布的证据。

两种数据均为本地冻结证据的**摘要摘录**；完整来源、哈希和本仓库中提交的
CSV 见 [docs/evidence/README.md](evidence/README.md)。

## 冻结 R2 合成评测

来源为本地冻结的 data/results.json，SHA-256：

E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215。

| Arm | 结构有效 | 回答交付 | claim-v2 端到端 | 解读边界 |
| --- | ---: | ---: | ---: | --- |
| Full capsule | 27 / 36 | 27 / 36 | 21 / 36 | 冻结合成记录 |
| Projection | 36 / 36 | 36 / 36 | 25 / 36 | 冻结合成记录 |
| Summary | 36 / 36 | 36 / 36 | 27 / 36 | 仅离线对照，不能成为运行时或回退路径 |
| Oracle floor | 36 / 36 | n/a | n/a | 已登记编码下限，不是质量或信息论最优值 |

CSV：[docs/evidence/frozen-r2-outcomes.csv](evidence/frozen-r2-outcomes.csv)。
该设计中的 Summary 只保留为历史离线比较臂；AstrContinuum 的运行时不以
Summary 作为主路径或降级回退。

## CRM V21-008 确定性压力记录

来源为隔离的 codex/crm-experiment 工作树提交
af835babbd1ca07619251f83c4e0201264975a49 中的
evidence/v21-008-stress-data.json，SHA-256：

0C8BDC214588AA53F06DA830181D7EB9FF5D164063E8EC8FB74F75B7D0651760。

记录报告 replay_match=true 与 COMPLETED：12 轮总计 30 条输入记录、
122,880 输入字节。逐轮 reduction_ppm 为 116,499–347,594，均值
247,519.17；第 12 轮为 33,974 resident bytes、347,594 reduction ppm、
652,405 retention ppm、累计 loss 30。

CSV：[docs/evidence/crm-v21-008-stress-rounds.csv](evidence/crm-v21-008-stress-rounds.csv)。
这些数值只说明该冻结确定性压力记录的记账结果；它们不说明生产会话质量、
用户效用、模型泛化、Provider 行为或 v0.3 发布状态。

## 当前 v0.3 的可声称范围

当前可声称的是持久化发布安全契约：fenced SQLite 发布、不可变
Capsule/Snapshot、显式传入时的重组账本、永久质量闸门，以及 CAS/崩溃
不留孤儿账本行。标准 CompactionWorker 尚未调用
reorganize_capsules()，所以正常后台发布的重组记录元组为空。

当前不能声称：

- 已完成真实语义模型或 Provider 评测；
- CRM 重组路径已接入生产 worker；
- 非摘要 released 记录可以绕过永久质量闸门；
- v0.3 已达到公开 v1.0、性能基准或全面兼容矩阵。

生产链和研究目标链的精确关系见 [docs/WORKFLOW.md](WORKFLOW.md)；
方案、场景和未来发布阈值见 [docs/EVALUATION.md](EVALUATION.md)。
