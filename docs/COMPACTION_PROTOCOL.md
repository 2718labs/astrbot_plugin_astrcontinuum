# 非阻塞压缩协议

> 本页是可读的协议摘要。`docs/DATA_FLOW.md`、`docs/CONCURRENCY_STATE_MACHINE.md` 与 `docs/DATABASE_SCHEMA.md` 是规范性细节；v0.3.0 的账本规则由 `docs/ADR-008-REORGANIZATION-LEDGER.md` 补充。

## Job 状态机

```text
PENDING → LEASED → COMPILING → AUDITING? → READY_TO_COMMIT → COMMITTED
                            └──────────────────────────────→ RETRY_WAIT / FAILED
READY_TO_COMMIT ── same-prefix / pointer CAS conflict ────→ SUPERSEDED
```

`strict_audit=false` 只省略可选的 `AUDITING` 分支；机械校验、fencing 与永久质量闸门始终生效。未成功的候选不会移动 active pointer；CAS 成功后 pointer 切换到新的 committed Snapshot，而旧行仍保持 immutable、可回溯。

## 合并与恢复

- 每会话只有一个非终态意图链；重复触发取更高的 `intent_target_high_water_mark`，不会改写已冻结尝试的 target。
- 新事件继续写入 Delta。成功或 `SUPERSEDED` 后，只要意图高于获胜 Snapshot 覆盖范围，就在同一事务中保留或创建 `PENDING` follow-up。
- 过期工作由恢复流程回到 `PENDING`，保留递增的 `lease_epoch`；从过期 `READY_TO_COMMIT` 恢复时清除 worker-local candidate id，下一位持有者必须重新编译。

## 原子发布

发布先校验 owner/epoch fencing、base、coverage、成员关系与永久质量条件，再在一个 savepoint 内按以下顺序写入：

1. 新 immutable Capsules；
2. `COMMITTED` Snapshot；
3. 有序 `snapshot_capsules` 成员关系；
4. 若显式提供记录，则写入 v0.3.0 的有序重组账本；
5. active-pointer compare-and-swap 与 Job 终态。

标准 `CompactionWorker` 当前调用发布 API 时不提供重组记录，因此正常后台压缩的第四步为空；重组器接线尚未纳入 v0.3.0。当前实现把 Capsule、Snapshot、成员关系插入时的完整性碰撞，以及 pointer CAS 失败，归入 `SUPERSEDED` 分支，并回滚 savepoint 中的全部候选写入。账本写入本身的完整性错误不属于这个既有分类器：它必须传播并回滚整个事务，不能伪装为并发获败。任一失败都不会移动 active pointer。
