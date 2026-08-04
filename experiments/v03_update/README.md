# v0.3 staged-update validation / v0.3 分阶段更新验证

This directory defines the auditable validation path for the v0.3 update and
reorganization work. It retains the frozen R2 record as a **pre-v0.3 historical
baseline**; it neither overwrites R2 nor treats R2 as a v0.3 result.

本目录定义 v0.3 更新与重组能力的可审计验证路径。冻结 R2 记录仍保留为**v0.3
前的历史基线**；本实验既不覆盖 R2，也不把它当作 v0.3 结果。

The implemented runner is the synthetic wiring gate. It drives the opt-in
fenced worker through baseline publication and an update publication, then
writes redacted receipts to an explicit output directory:

```powershell
uv run python experiments/v03_update_benchmark.py `
  --output-dir D:\bun\tmp\codex\AstrContinuum-v03-experiment\receipt `
  --workspace D:\bun\tmp\codex\AstrContinuum-v03-experiment\workspace
```

The runner is not a semantic-model or Provider benchmark. The E2E comparison
against a freshly refreshed Summary remains `BLOCKED` until approved Provider
receipts and the pre-registered scorer are available.

The admitted Gate A execution is `V03-WIRE-001`: all 36 frozen units committed
their baseline and reorganized update, advanced the pointer exactly `1 → 2`,
and retained a non-empty durable ledger with required core edges. Its public,
redacted [aggregate and receipts](../../docs/evidence/v03-update-gate-a/) are
part of the repository evidence. It is intentionally a verification table, not
a performance figure: an all-pass synthetic contract does not measure model
quality. This result establishes only the opt-in fenced path;
the ordinary Provider-bound runtime remains unwired and the E2E Summary
comparison remains `BLOCKED`.

该运行器不是语义模型或 Provider 基准；在获得已批准的 Provider 回执与预注册评分器
之前，和重新生成 Summary 的端到端比较始终为 `BLOCKED`。

已收录的 Gate A 执行为 `V03-WIRE-001`：36 个冻结单元全部提交基线和重组更新，
指针均精确 `1 → 2`，并保留带必需核心边的非空永久账本。其公开、脱敏的
[聚合与回执](../../docs/evidence/v03-update-gate-a/)均已纳入仓库证据。它刻意以验证表
而非性能图呈现：全通过的合成契约不测量模型质量。该结果只证明 opt-in 围栏路径；
常规 Provider 绑定运行时仍未接线，
端到端 Summary 比较仍为 `BLOCKED`。

See [the protocol](plan/experiment-protocol.md),
[stage gates](plan/stage-gates.md), [table schema](tables/table-schema.md), and
[figure data manifest](figures/data-manifest.md).
