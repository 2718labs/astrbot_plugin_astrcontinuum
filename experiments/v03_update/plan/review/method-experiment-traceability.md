# Method–experiment traceability / 方法—实验可追溯矩阵

| Contribution / 贡献 | Method module / 方法模块 | Experiment / 实验 | Table or figure / 表图 | Allowed claim / 可声称结论 | Evidence status / 证据状态 |
| --- | --- | --- | --- | --- | --- |
| Deterministic capsule reorganization | `reorganize_capsules()` | Gate A synthetic staged update | Gate A aggregate table | The opt-in fenced path deterministically rebuilds a candidate within budget | PASS: V03-WIRE-001, 36/36 committed synthetic units |
| Durable update publication | fenced worker, permanent validator, `publish_snapshot`, CAS, ledger | Gate A baseline then update | Gate A aggregate table and JSON receipt | A committed update records an immutable ledger and advances the active pointer | PASS: 36/36 exact `1 → 2` and non-empty ledger records |
| Loss-aware safety | permanent quality floor | Gate A negative controls | failure ledger table | Non-summary released records fail closed and do not advance the pointer | existing contract tests; negative benchmark receipt pending |
| Current-state quality improvement | v0.3 worker plus answer/scorer integration | Gate B paired E2E vs refreshed Summary | E2E paired-outcome table/figure | v0.3 is superior to Summary only if the pre-registered paired rule passes | BLOCKED: no Provider receipts or scorer |

No contribution in this matrix permits a claim that the historical R2 Full
capsule already used the v0.3 path. 不得把历史 R2 的 Full capsule 写成已使用
v0.3 路径。
