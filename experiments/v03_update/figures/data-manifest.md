# Figure data manifest / 图数据清单

| Output | Data file | Generator | Scope boundary | Status |
| --- | --- | --- | --- | --- |
| Gate A wiring verification table | `docs/evidence/v03-update-gate-a/aggregate.csv` plus reviewed redacted receipts | committed CSV and Markdown table; no performance figure is admitted | synthetic structural/ledger/CAS validation only; not semantic quality | PASS: V03-WIRE-001, 36/36 structural units; aggregate SHA-256 08E956A9527AFAA0523A00588D331E293AA54CE5005AD1FC9E3F37C1284D518B |
| Gate B paired E2E outcome figure | approved paired aggregate CSV over update depth/context scale | deterministic data-to-SVG exporter, with paired uncertainty | only after same-model, same-budget, same-scorer comparison | BLOCKED |

No raster generation model is used. Gate A remains a table because its fixed
all-pass contract is not an outcome curve. A Gate B figure must be derived only
from committed paired CSV aggregates; its caption must state the denominator,
data source hash, confidence/paired analysis, and what it does **not**
establish.

不使用生图模型。Gate A 保持为表格，因为固定的全通过契约不是结果曲线。Gate B 实验图
只能从已提交的配对 CSV 聚合数据确定性导出；图注必须写出分母、数据来源哈希、置信／
配对分析，以及该图**不能**证明什么。
