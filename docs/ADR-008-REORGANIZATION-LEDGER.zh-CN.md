# ADR-008：不可变 Snapshot 重组账本

- 状态：v0.3.0 技术预览已接受
- 范围：Snapshot 发布可审计性与持久化损失核算
- 部分取代：ADR-004、ADR-007 中的发布顺序和冲突分类；其覆盖与 Capsule 持久化决定仍然有效。

## 背景

AstrContinuum 可以在 token 预算内重组结构化 Capsule 内容。worker 局部结果必须核算每个来源项的
处置方式，且不能让有损结果绕过永久发布校验器。v0.3.0 前，该记录形状只存在于内存中：已发布
Snapshot 无法持久化展示来源项被保留、近似或释放的情况。

本决定必须保持既有发布边界，同时精确记录当前范围。既有候选冲突分类器会将 Capsule/Snapshot/
membership 插入完整性冲突和 CAS 丢失映射为 `SUPERSEDED`。v0.3.0 必须确保账本插入完整性失败
不进入该分类器。常规 Provider 绑定 worker 与默认 AstrBot 组合配置均不设置
`reorganization_token_budget`，因此不传入重组记录。注入式 compiler backend 可在围栏合成 Gate A
验证通道中显式设置该预算并调用 `reorganize_capsules()`。本 ADR 因而将 repository/发布契约与
普通后台行为分开；Gate A 不构成 Provider 或生产声明。

## 决定

### 持久化形状与规范化

迁移 v3 创建 `snapshot_reorganization_records`，以 `(snapshot_id, ordinal)` 为键，并保证
`(snapshot_id, source_capsule_id, kind, item_id)` 唯一。每一行记录来源身份、
`retained`/`approximate`/`released` 状态、前后 token 数和该项是否必需。通过拒绝 update/delete 的
trigger 保持行只追加。

发布打开 savepoint 前，repository 规范化要求精确的 `ReorganizationRecord` 和
`ReorganizationStatus`、非空身份字符串、非负且非 bool 的 token 数、精确 bool `required`，且不得
有重复来源项身份。只有 `retained` 可以使用 `required=true`。

### 发布与可见性

`TX_PUBLISH_SNAPSHOT` 首先校验活动 lease fence、候选形状、membership、规范账本记录和永久
Snapshot 条件。在一个发布 savepoint 中，物理写入顺序为：

1. 不可变 Capsule；
2. `COMMITTED` Snapshot；
3. 有序 `snapshot_capsules` membership；
4. 显式提供时的有序 `snapshot_reorganization_records` 账本行；
5. 活动指针 CAS、Job 终态转换和后续 intent 保留。

这个顺序有意如此：membership 和账本行都对 Snapshot 使用即时外键。账本 reader 只接受已有
`COMMITTED` Snapshot，并按 ordinal 顺序返回行；worker 局部候选和已回滚写入永远不可见。

### 永久质量下限

`kind` 不是 `narrative_summary` 的 `released` 记录必须无条件向永久校验报告加入
`QUALITY_COVERAGE_GAP`。因此，即使 `strict_audit=false`，它也会拒绝发布；可选语义审查不能放宽
这一底线。`narrative_summary` 的释放会被核算，但其本身不会加入此失败。

### 冲突分类

既有候选冲突分类器将 Capsule/Snapshot/membership 插入完整性冲突和指针 CAS 丢失映射为
`SUPERSEDED`，回滚 savepoint 并移除每一行候选数据，包括账本行。任何账本插入完整性失败刻意
不同：它会在外层事务回滚后传播，使 Job 位于该分类器之外，且不留下孤儿账本数据。因此，
`SUPERSEDED` 结果本身不能证明 Capsule/Snapshot/membership 失败就是 CAS 竞争；技术预览明确保留
这一限制。

## 后果

- 既有数据库必须通过经 checksum 核验的迁移计划到达 schema version 3。
- Schema migration 版本与 Capsule `schema_version` 是数据契约，二者都不是包的 v0.3.0 发布版本。
- v0.3.0 对常规 Provider 绑定／默认 AstrBot 路径仍保持仅存储安全：它传入空记录 tuple。独立的
  注入式／围栏 Gate A 通道可在显式预算下调用 `reorganize_capsules()` 并持久化规范记录。该合成
  验证通道不主张公开 v1.0 兼容性、Provider 覆盖、语义质量、性能基准或终端用户发布就绪。
