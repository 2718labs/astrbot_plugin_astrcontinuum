# 证据与实验摘要

[English](EVIDENCE.md) | 简体中文

> **证据边界。** 本页记录隔离研究证据及其溯源边界；它不表示 v0.3.0 已通过
> 真实语义模型评测、Provider 基准、生产性能基准或发布就绪门槛。

<p align="center">
  <img src="assets/evidence-r2-outcomes-rmb.svg" width="720"
       alt="冻结 R2 合成结果；仅限研究，不构成 v0.3 生产就绪证据 / Frozen R2 synthetic outcomes; research-only and not evidence of v0.3 production readiness">
</p>

<p align="center"><em>
冻结 R2 合成评测（12 个场景 × 3 次试验，n=36）。仅限研究，不构成 v0.3
生产性能或发布就绪声明。
<br>
Frozen R2 synthetic evaluation (12 scenarios × 3 trials, n=36). Research-only;
not a v0.3 production-performance or release-readiness claim.
</em></p>

## 如何阅读这些证据

仓库只提交有边界的聚合摘录，刻意不包含原始对话、盲测提示词或回答，以及非必要
中间产物。[证据清单](evidence/README.zh-CN.md)列出每份摘录的来源身份和已提交
文件校验和。

这里包含两条相互独立的研究记录：

1. **冻结 R2 合成评测。** 12 个场景 × 3 次试验，共 36 个固定分母单元，分别报告
   结构有效、回答交付和 claim-v2 端到端结果。它不代表真实用户、真实语义模型或
   生产延迟。
2. **CRM V21-008 确定性压力记录。** 12 个 replay-matching 轮次覆盖 30 条输入
   记录与 122,880 输入字节，量化预注册确定性 CRM 路径的字节和损失记账；它不证明
   CRM 已接入 v0.3 worker 或已具备发布条件。

## 冻结 R2 合成评测

冻结研究来源标识为 results.json，SHA-256：

E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215。

| 试验臂 | 结构有效 | 回答交付 | claim-v2 端到端 | 解读边界 |
| --- | ---: | ---: | ---: | --- |
| Full capsule | 27 / 36 | 27 / 36 | 21 / 36 | 冻结合成记录 |
| Projection | 36 / 36 | 36 / 36 | 25 / 36 | 冻结合成记录 |
| Summary | 36 / 36 | 36 / 36 | 27 / 36 | 仅离线对照，绝不是运行时回退 |
| Oracle floor | 36 / 36 | n/a | n/a | 已登记编码下限，不是质量或信息论最优值 |

已提交的聚合摘录为
[docs/evidence/frozen-r2-outcomes.csv](evidence/frozen-r2-outcomes.csv)，
SHA-256：
415B52EF538B2F7B76CA6815C45BC2C2F6580CC50BD9C16798D83296E8918B7F。
Summary 臂只保留为历史离线比较；AstrContinuum 不会把它作为主路径或降级运行时回退。

## CRM V21-008 确定性压力记录

隔离来源记录由不可变修订
af835babbd1ca07619251f83c4e0201264975a49 和来源 SHA-256 标识：

0C8BDC214588AA53F06DA830181D7EB9FF5D164063E8EC8FB74F75B7D0651760。

记录报告 replay_match=true 与 COMPLETED：12 轮、30 条输入记录、122,880
输入字节。逐轮 reduction_ppm 为 116,499–347,594，均值 247,519.17。第 12
轮为 33,974 resident bytes、347,594 reduction ppm、652,405 retention ppm、累计
loss 30。

已提交的聚合摘录为
[docs/evidence/crm-v21-008-stress-rounds.csv](evidence/crm-v21-008-stress-rounds.csv)，
SHA-256：
645943F5738320295580CAA62BC228694267251EF8AA01BFA13ECC5C2FFF2A01。
这些数字只描述冻结确定性记账记录，不说明生产会话质量、用户效用、模型泛化、
Provider 行为或 v0.3 发布状态。

## v0.3.0 可声称与不可声称的范围

v0.3.0 可声称已测试的持久化与发布契约：带 fencing 的 SQLite 发布、不可变 Capsule
和 Snapshot、永久机械质量闸门，以及在发布显式传入记录时使用的不可变重组账本。
成功 CAS 不会留下孤儿账本行；账本完整性错误会回滚事务，且不会移动 active pointer。

标准 CompactionWorker 不调用 reorganize_capsules()，普通后台发布因此传入空的重组
记录元组。账本接口已经存在，但自动 CRM 重组并不是 v0.3 的运行时能力。

v0.3.0 不能声称：

- 已完成真实语义模型或 Provider 评测；
- CRM 重组 worker 已自动接线；
- 非摘要 released 记录可以绕过永久质量闸门；
- 已达到公开 v1.0、百万 Token 性能结果或完整兼容矩阵。

已交付发布链与 CRM 研究目标链的分界见[工作流](WORKFLOW.zh-CN.md)；场景、指标和
未来发布阈值见[评测方案](EVALUATION.zh-CN.md)。
