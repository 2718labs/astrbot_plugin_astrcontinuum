# 证据与实验摘要

[English](EVIDENCE.md) | 简体中文

> **证据边界。** 本页记录隔离研究证据及其溯源边界；它不表示 v0.3.0 已通过
> 真实语义模型评测、Provider 基准、生产性能基准或发布就绪门槛。

> **Gate A 分类。** `V03-WIRE-001` 是确定性的集成验证（12 个冻结场景 × 3 次
> 试验），以下以表格和脱敏回执公开。它的全通过结构结果不会被包装成模型性能图，
> 也不是 v0.3 对比 Summary 的结论。

## 如何阅读这些证据

仓库只提交有边界的聚合摘录，刻意不包含原始对话、盲测提示词或回答，以及非必要
中间产物。[证据清单](evidence/README.zh-CN.md)列出每份摘录的来源身份和已提交
文件校验和。

这里包含三条有边界的证据记录：

1. **冻结 R2 合成评测。** 12 个场景 × 3 次试验，共 36 个固定分母单元，分别报告
   结构有效、回答交付和 claim-v2 端到端结果。它不代表真实用户、真实语义模型或
   生产延迟。
2. **CRM V21-008 确定性压力记录。** 12 个 replay-matching 轮次覆盖 30 条输入
   记录与 122,880 输入字节，量化预注册确定性 CRM 路径的字节和损失记账；它不证明
   CRM 已接入 v0.3 worker 或已具备发布条件。
3. **V03-WIRE-001 Gate A 集成验证。** 12 个冻结场景 × 3 次试验经过注入式、围栏化的
   `CompactionWorker` 更新路径，记录结构发布、精确 `1 → 2` 指针、永久账本记录和
   必需核心边保留；它不测量语义回答质量、Provider 行为、生产行为，也不产生
   v0.3 对比 Summary 的结论。

## v0.3 前的历史 R2 基线

<p align="center">
  <img src="assets/evidence-r2-outcomes-rmb.svg" width="860"
       alt="图 R2：冻结 R2 聚合结果的双面板点图，固定分母 n=36；仅限研究，不是 v0.3 对比">
</p>

<p align="center"><em>
冻结 R2 合成评测（12 个场景 × 3 次试验，n=36）。数值为已提交的聚合计数，以
计数/36（%）表示；公开聚合数据不提供不确定性区间或假设检验。该历史记录不是
v0.3 评测或版本间比较。
</em></p>

冻结研究来源标识为 results.json，SHA-256：

E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215。

它是 v0.3 前的历史基线，不是对 v0.3 更新／重组机制的评测。

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

**解读约束。** 这些试验臂不是版本间性能排名。本聚合不能证明 Full capsule 优于
Summary，也不能证明已启用 v0.3 更新／重组机制。

### 可复现实验图导出

发布 SVG 由已提交的聚合数据自动生成，并非手工维护的绘图作品。经批准的实验更新
CSV 后，执行以下命令即可重建实验图：

```bash
uv run python scripts/render_r2_evidence_figure.py
```

发布测试会先渲染到临时路径，并要求结果与提交的 SVG 完全一致。

## v0.3 Gate A 集成验证：opt-in worker 接线

`V03-WIRE-001` 是经过审阅的、无 Provider 的结构性集成检查。它把冻结 fixture
[experiments/v03_update_scenarios.json](../experiments/v03_update_scenarios.json)
送入真实的注入式、围栏化 `CompactionWorker`：初始发布、捕获一次更新、候选重组、
永久校验、Snapshot/CAS 发布以及不可变账本持久化。fixture SHA-256 为
36BA7B902C57903B5B1ABCCE21378812C21F54267F018B8706E57A4BE3A648D9；其中 12 个
场景各执行三次（`n = 36`）。回执只保存哈希、计数、指针版本和稳定状态码，绝不保存
源事件正文。

| Gate A 完整性检查 | 结果 | 解读边界 |
| --- | ---: | --- |
| 基线发布已提交 | 36 / 36 | 更新前初始 Snapshot 已存在。 |
| 重组后的更新发布已提交 | 36 / 36 | 围栏化 opt-in 候选经校验后发布。 |
| Active pointer 精确 `1 → 2` | 36 / 36 | 一次阶段式更新只推进一次 active Snapshot。 |
| 永久重组账本 | 36 / 36 | 更新 Snapshot 有非空且可哈希回读的记录。 |
| 必需 exact-anchor + dependency 记录已保留 | 36 / 36 | 核心账本边经过永久闸门后仍存在。 |
| 非摘要 released 记录 | 0 | 安全不变量，不是质量分数。 |

已提交聚合为
[aggregate.csv](evidence/v03-update-gate-a/aggregate.csv)（SHA-256
08E956A9527AFAA0523A00588D331E293AA54CE5005AD1FC9E3F37C1284D518B），
逐单元脱敏回执为
[receipt.csv](evidence/v03-update-gate-a/receipt.csv)（SHA-256
D4382A3778CCEC510EE510716482D67C4BBEFAC1D5630DF2A2488E1B0354EA1D）和
[receipt.json](evidence/v03-update-gate-a/receipt.json)（SHA-256
CBD31AEA6F6062F6C9980FA03B0A0EBCB9C82034C6A1E7E7725D9B068256DF1E）。
聚合结果 SHA-256 为
89032BBF0D23D4B09978F93F774FD5392DF90C440EE02BF0D852A85BC80A10D3。

### 可复现 Gate A 执行

请使用新的任务本地 workspace 和空输出目录；运行器会拒绝覆盖已有回执，也会拒绝
复用同一次运行的 workspace：

```powershell
uv run python experiments/v03_update_benchmark.py `
  --output-dir D:\bun\tmp\codex\AstrContinuum-v03-experiment\receipt `
  --workspace D:\bun\tmp\codex\AstrContinuum-v03-experiment\workspace
```

Gate A 刻意不生成性能图：它的固定确定性全通过结果属于发布契约验证，而不是科学结果
曲线。只有在配对 Gate B 数据集同时包含两臂的同一模型、评分器、阶段历史、更新深度
横轴以及配对的准确率／陈旧率／上下文成本／延迟结果后，才可收录真正的 E2E 实验图。

**解读约束。** Gate A 只证明 opt-in、注入式、围栏化 worker 的接线路径。常规
Provider 绑定运行时及当前 AstrBot 组合配置未设置 `reorganization_token_budget`，
因此仍是空账本发布。Gate A 不是 AstrBot Provider 接入、语义质量结果、性能结果、
生产就绪结果，也不是与 Summary 的比较。预注册的端到端
`v03_reorganized_worker` 对重新生成 `summary_refresh` 仍为 **`BLOCKED`**，等待
获准的 Provider 回执与冻结评分器。

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
`V03-WIRE-001` 还在其冻结合成 fixture 下证明了狭义的 opt-in、注入式、围栏化
Gate A worker 路径：确定性重组、发布、精确指针推进和永久账本记录。

常规 Provider 绑定运行时及当前 AstrBot 组合配置未设置
`reorganization_token_budget`，因此后台发布仍传入空的重组记录元组。只有带显式
预算的注入式 compiler backend 才可在围栏 Gate A 路径中调用 `reorganize_capsules()`。
这并不意味着自动 CRM 重组已成为通用 AstrBot 运行时能力。

v0.3.0 不能声称：

- 已完成真实语义模型或 Provider 评测；
- 常规 Provider 绑定／AstrBot 运行时重组或通用 CRM worker 能力；
- 优于重新生成的 Summary 臂；
- 非摘要 released 记录可以绕过永久质量闸门；
- 已达到公开 v1.0、百万 Token 性能结果或完整兼容矩阵。

已交付发布链与 CRM 研究目标链的分界见[工作流](WORKFLOW.zh-CN.md)；场景、指标和
未来发布阈值见[评测方案](EVALUATION.zh-CN.md)。
