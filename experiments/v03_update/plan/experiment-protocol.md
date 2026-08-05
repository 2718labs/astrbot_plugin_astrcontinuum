# V0.3 staged-update validation protocol / v0.3 分阶段更新验证协议

**Status / 状态：** pre-registered; Gate A implemented and executed with frozen
synthetic inputs (`V03-WIRE-001`). Gate B is `BLOCKED` pending approved Provider
execution receipts.

## 1. Scope and historical boundary / 范围与历史边界

Frozen R2 stays intact as a pre-v0.3 historical record. Its `Full capsule`,
`Projection`, and `Summary` arms do not evaluate the v0.3 update/reorganization
path and must not be used to infer v0.3 performance.

冻结 R2 保留为 v0.3 前的历史记录。其中的 `Full capsule`、`Projection` 与
`Summary` 臂没有评测 v0.3 更新／重组路径，不能据此推断 v0.3 性能。

This protocol separates two claims:

1. **Gate A — wiring and durable publication.** The opt-in injected/fenced
   worker invokes reorganization, rebuilds the candidate, clears permanent
   validation, advances the active pointer, and persists a round-trippable
   ledger.
2. **Gate B — E2E current-state comparison.** A v0.3 worker is compared with a
   freshly refreshed Summary on the same staged history and answer contract.

The default provider-bound runtime is not changed by Gate A. A Gate A pass
proves only the experimental opt-in worker path; it does not make a production
readiness, Provider, semantic-quality, or Summary-superiority claim.

## 2. Inputs and fixed denominator / 输入与固定分母

Gate A uses `experiments/v03_update_scenarios.json`: 12 frozen synthetic
`initial_event → update_event` scenarios, each executed exactly three times
for `n = 36`. Scenario text is not emitted into receipts. The runner stores
only its SHA-256, stable status codes, counters, pointer versions, and ledger
hashes.

Gate A 使用 `experiments/v03_update_scenarios.json` 中的 12 个冻结合成
`初始事件 → 更新事件` 场景，每个场景恰好执行三次，固定分母为 `n = 36`。回执
不输出场景正文，只记录 SHA-256、稳定状态码、计数、指针版本和账本哈希。

The registered fixture SHA-256 is
`36BA7B902C57903B5B1ABCCE21378812C21F54267F018B8706E57A4BE3A648D9`.
The runner rejects byte-level fixture drift, a non-three trial count, a
non-10,000 token budget, an existing receipt output, or reuse of the same run
workspace. It is intentionally a frozen deterministic wiring contract, not a
fixture search or tuning loop.

已登记 fixture SHA-256 为
`36BA7B902C57903B5B1ABCCE21378812C21F54267F018B8706E57A4BE3A648D9`。
运行器会拒绝字节级 fixture 漂移、非三次试验数、非 10,000 的 token budget、已有
回执输出或复用同一次运行 workspace。它刻意是冻结的确定性接线契约，而非场景搜索或
调参循环。

Gate B must use the same 12 scenario families, the same three pre-registered
seeds, the same full staged history, and the same UTF-8-byte context ceiling
for both arms. A failed or deferred unit remains in the denominator.

## 3. Arms, fairness, and metrics / 试验臂、公平性与指标

| Gate | Arm | Comparator | Primary endpoint | Status |
| --- | --- | --- | --- | --- |
| A | `opt_in_fenced_compaction_worker` | baseline Snapshot before update | committed update with durable ledger and pointer `1 → 2` | implemented synthetic gate |
| B | `v03_reorganized_worker` | `summary_refresh` from the same full history plus Delta | `current_state_qa_pass` | BLOCKED |

For Gate A, all 36 units must satisfy all of the following:

- baseline and update publication are `COMMITTED`;
- the active pointer advances exactly once from 1 to 2;
- the update Snapshot has a non-empty, hash-round-trippable ledger;
- no non-`narrative_summary` record is `released`;
- required exact-anchor and dependency records remain retained; and
- no source conversation text occurs in committed receipts.

For Gate B, both arms must use the same answer model identifier/digest,
temperature, seed, output cap, answer template, frozen gold scorer, source
hashes, and byte ceiling. Its primary endpoint is a paired
`current_state_qa_pass`: answer delivered, all required current facts correct,
no stale prior fact, and no unsupported critical claim. The pre-registered
success rule is at least six paired net wins (16.7 percentage points) for
v0.3, two-sided exact McNemar `p <= 0.05`, and all 36 structural/ledger/budget
gates passed.

Gate B cannot be reported until raw Provider receipts are retained in the
approved task-local evidence root and only redacted hashes/counts are admitted
to the repository.

## 4. Failure policy / 失败策略

- Core edges over budget, malformed ledger rows, non-summary releases, and
  ledger/CAS faults fail closed; the pointer must not move and no orphan ledger
  rows may remain.
- `BLOCKED_NOT_WIRED`, missing Provider receipts, unavailable scorer, or an
  untrusted model identity are evidence states, not zero-score substitutions.
- Synthetic Gate A results may establish only deterministic worker wiring and
  storage behavior. They never establish end-user utility, semantic quality,
  Provider behavior, production latency, or release readiness.

## 5. Execution records / 执行记录

The runner command is documented in [the directory README](../README.md).
Receipts are written to an explicit task-local workspace, never to a user
production database. The committed evidence layer may contain only aggregate
CSV/JSON and hashes after review.
