# 架构

## 三条通道

### Live Lane

```text
current request
→ load committed snapshot
→ load uncompacted delta
→ retrieve local evidence
→ budget assembly
→ main LLM
```

禁止等待压缩。

### Compaction Lane

```text
new completed events
→ episode segmentation
→ claim/anchor extraction
→ capsule compilation
→ candidate snapshot
→ loss audit
→ atomic commit
```

### Archive Lane

保存不可变原始事件、工具结果引用和来源证据。

## 分层

```text
AstrBot hooks
    ↓
Application services
    ↓
Domain model
    ↓
Ports / protocols
    ↓
SQLite / providers / adapters
```

领域层不得依赖 AstrBot。

## 每轮上下文

```text
C_t =
fixed_prefix
+ global_state
+ active_task
+ relevant_capsules/evidence
+ exact_anchors
+ uncompacted_delta
+ recent_raw
+ current_input
```

## 主要组件

- EventJournal
- EpisodeSegmenter
- AnchorExtractor
- CapsuleCompiler
- CapsuleMerger
- SnapshotStore
- CompactionScheduler
- LossAuditor
- HybridRetriever
- ContextAssembler
- TokenCounter
- Inspector
- ExternalMemoryProvider

## 降级

- NORMAL
- RETRIEVAL_DEGRADED：检索失败，使用 snapshot + delta + recent
- COMPACTION_LAGGING：后台落后，压缩非关键检索但保留 Delta
- EMERGENCY_ASSEMBLY：固定规则、活动状态、关键锚点、最近原文、当前输入
- READ_ONLY_RECOVERY：存储写失败时不提交新快照

任何降级都必须可观测。
