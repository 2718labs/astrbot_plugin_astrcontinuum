# V0.3 experiment stage gates / v0.3 实验阶段门

| Gate | Required evidence / 必需证据 | Pass condition / 通过条件 | Current state / 当前状态 |
| --- | --- | --- | --- |
| D0 — protocol locked | protocol, fixed scenarios, seeds, byte ceiling, receipt schema | files reviewed; no mutable arm definition after run | PASS for Gate A; fixture SHA-256 locked as 36BA7B902C57903B5B1ABCCE21378812C21F54267F018B8706E57A4BE3A648D9; Gate B protocol locked but execution blocked |
| D1 — method traceability | method–experiment matrix | every claim maps to an experiment or a BLOCKED boundary | PASS |
| D2 — data contract | table schema, figure manifest, redacted receipt format | schema validates 36 fixed-denominator rows | PASS for Gate A contract |
| D3A — wiring result | 36 Gate A receipts plus aggregate | all baseline/update publications committed; pointer and ledger checks pass | PASS: V03-WIRE-001, 36/36 for every required structural check; no non-summary release |
| D3B — E2E result | raw approved Provider receipts, scorer output, paired aggregate | pre-registered McNemar and safety gates pass | BLOCKED |
| D4 — publication wording | bilingual evidence text and generated table | no Gate A wording claims semantic or Provider quality | PASS: English/Chinese evidence and aggregate/receipts admitted; no Gate A performance figure is presented |
| D5 — independent review | review of runner, data hashes, and result claims | no unadmitted data or scope overclaim | PASS for Gate A: independent harness review verified real worker path, fixture locking, exact pointer accounting, non-overwrite, and redaction; Gate B remains BLOCKED |

`BLOCKED` is not a failure count. It means a required external evidence source
or execution capability has not been admitted. `BLOCKED` 不是失败计数，而是尚未
获得必要外部证据或执行能力。
