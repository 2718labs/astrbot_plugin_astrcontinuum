# Codex Master Prompt — AstrContinuum

你现在是本仓库的首席架构师与实现工程师。完整阅读本文件以及 `docs/`、`specs/`、`tests/` 中全部内容后再工作。

**禁止把任务降级为聊天摘要插件。**

---

## 1. 任务

为 AstrBot 开发独立插件：

```text
astrbot_plugin_astrcontinuum
```

产品名：

```text
AstrContinuum
```

使命：

> 在模型上下文窗口有限（典型约 200k Token）的情况下，为持续增长的聊天提供近似无限、不中断、可恢复、可追溯的上下文连续性。

它是：

```text
Non-blocking Infinite Context Runtime
```

不是：

```text
Summarizer
```

---

## 2. 用户体验目标

必须同时成立：

1. 用户回复路径绝不等待 LLM 压缩；
2. 原始历史不因压缩被删除；
3. 当前目标、约束、决定和未完成事项持续存在；
4. 旧细节在需要时可以恢复；
5. 压缩失败、超时、进程崩溃时聊天仍然可用；
6. 用户可以查看当前上下文组成；
7. 压缩结论可以追溯到原始消息；
8. 可以回滚旧快照；
9. 无 Sylanne 时独立运行；
10. 有 Sylanne 时利用其长期记忆信号，但不得复制或侵入其记忆系统。

---

## 3. 系统边界

### AstrContinuum 负责

- 当前 LLM 工作上下文
- 历史分层压缩
- Token 预算
- 稳定快照
- 未压缩 Delta
- Episode 分段
- 当前任务状态
- 查询感知历史恢复
- 精确锚点
- 压缩损失审计
- 上下文可观测性

### Sylanne 负责

- 长期用户记忆
- 人格与关系连续性
- 生活模拟与内部状态
- 自身 L1/L2/L3 记忆生命周期
- 自身召回策略

### 可选集成协议

```python
class ExternalMemoryProvider(Protocol):
    async def importance_hints(self, session_id: str, texts: list[str]) -> list[float]: ...
    async def retrieve(self, session_id: str, query: str, budget_tokens: int) -> list[Evidence]: ...
```

AstrContinuum 不得硬依赖 Sylanne。

---

## 4. 不可妥协的不变量

### INV-001：压缩不阻塞回复

禁止：

```python
await compactor.compact(...)
reply = await provider.chat(...)
```

主路径只能读取最近已提交快照并装配上下文。

### INV-002：原始事件不可变

已提交事件不得被快照覆盖或静默删除。

### INV-003：只读取 committed snapshot

候选快照审计并原子提交前，不得进入正式请求。

### INV-004：Delta 无覆盖缺口

任意请求时：

```text
coverage(latest_snapshot) ∪ uncompacted_delta
```

必须覆盖快照边界之后的全部会话事件。

### INV-005：失败保持可用

编译、审计、索引、存储或适配器失败时：

```text
old committed snapshot + raw delta + recent raw
```

仍能构造上下文。

### INV-006：精确锚点不可被释义替代

名称、数字、日期、路径、URL、代码、公式、Prompt、强约束、未完成承诺必须保留来源与精确文本。

### INV-007：关键结论必须有来源

目标、约束、决定、实体和开放事项必须映射到原始 event_id。

### INV-008：旧决定可被覆盖

必须维护：

```text
active / superseded / retracted / uncertain
```

### INV-009：Token 预算是硬约束

Assembler 输出不得超过目标预算或硬上限。

### INV-010：动态上下文不污染稳定 System Prompt

每轮变化的快照、检索内容和状态应通过请求 contexts 或临时内容注入。

---

## 5. 三条通道

```text
User Message ─────▶ Live Lane / Context Assembler ─────▶ Main LLM
                           │
                           ▼
                  Immutable Event Journal
                           │ non-blocking enqueue
                           ▼
                  Compaction Lane
                  Segment → Compile → Audit
                           │ atomic commit
                           ▼
                  Versioned Snapshot Store
```

正式请求上下文：

```text
fixed_prefix
+ latest_committed_snapshot
+ active_task_state
+ query_relevant_reconstruction
+ uncompacted_delta
+ recent_raw
+ current_user_message
```

---

## 6. Shadow Compaction

假设：

```text
V20 covers event 1..500
job covers event 501..560
user continues and creates 561..570
```

压缩期间主请求使用：

```text
V20 + raw 501..570
```

V21 完成后：

```text
V21 covers 1..560
remaining delta = 561..570
```

要求：

- 每会话最多一个运行中的 worker
- 新事件进入 Delta，不等待 job
- 重复通知合并
- 候选使用乐观并发提交
- 过期候选不得覆盖新快照
- 压缩 Provider 可独立于主 Provider
- 主聊天调度优先级最高

---

## 7. 多分辨率上下文树

历史不能只有一个摘要。

```text
L0 Raw Events
  ↓
L1 Micro Capsules       约 8–16 轮或一个局部语义单元
  ↓
L2 Episode Capsules     一个完整话题或阶段
  ↓
L3 Task Capsules        目标、约束、决定、进度、开放事项
  ↓
L4 Global State         当前继续交互所需高密度状态
```

每个节点保存：覆盖范围、来源、实体、时间、Token 成本、依赖、精确锚点、质量报告和版本。

查询时选择不同分辨率：

- 高相关：原文
- 中相关：Micro
- 背景相关：Episode
- 仅需状态：Task/Global
- 无关：不进入窗口，只保留索引

---

## 8. Context Capsule

禁止只输出散文摘要。至少实现：

```python
class Claim:
    claim_id: str
    kind: str
    text: str
    status: Literal["active", "superseded", "retracted", "uncertain"]
    confidence: float
    source_event_ids: list[str]

class ContextCapsule:
    capsule_id: str
    level: Literal["micro", "episode", "task", "global"]
    session_id: str
    covered_event_start: int
    covered_event_end: int
    goals: list[Claim]
    constraints: list[Claim]
    decisions: list[Decision]
    progress: list[Claim]
    open_loops: list[Claim]
    preferences: list[Claim]
    entities: list[EntityRef]
    emotional_context: list[Claim]
    exact_anchors: list[ExactAnchor]
    dependencies: list[str]
    narrative_summary: str
    token_cost: int
    source_coverage: float
```

散文摘要只能作为导航字段。

---

## 9. Exact Anchors

自动原样保留：

- 专有名词、人名、仓库名、路径、URL
- 日期、数字、参数
- 代码、公式、Prompt
- 用户说“记住”“不要忘”“以后必须”“别再……”
- 当前活动强约束
- 未完成承诺
- 用户手动 Pin 的消息

提供：

```text
/context pin
/context unpin
/context anchors
```

---

## 10. Query-Aware Reconstruction

不能只做向量 top-k。混合评分：

```text
semantic relevance
+ lexical overlap
+ active task dependency
+ entity match
+ temporal reference
+ unresolved loop match
+ explicit reference signal
- redundancy
```

识别：

- 刚才那个
- 之前第二个方案
- 我们否掉的那个
- 上次没做完的
- 你还记得
- 继续
- 还是按原来的

检索到决定时，同时恢复其定义、依赖、原因和否决理由。

---

## 11. Token Budget

默认：

```text
model_context_limit: 200000
target_input_budget: 130000
hard_input_ceiling: 150000
reserved_output_and_tools: 50000
```

优先级：

1. 当前输入
2. 固定安全与人格规则
3. 活动目标和强约束
4. 未压缩 Delta
5. 精确锚点
6. 最近原文
7. 查询相关历史
8. 一般背景

警戒时不等待压缩，进入 emergency assembly：

```text
fixed rules
+ active state
+ critical constraints
+ exact anchors
+ recent raw
+ current input
```

---

## 12. Loss Auditor

候选快照提交前至少检查：

- 关键目标、约束、决定、开放事项
- 被覆盖决定状态
- 精确实体和数字
- Exact Anchors 全覆盖
- Claim 来源
- 覆盖范围连续
- Token 目标
- 自相矛盾
- 来源外新事实

硬规则：

```text
critical_anchor_recall == 1.0
unsupported critical claims == 0
coverage gap == 0
```

失败就丢弃候选，继续使用旧快照。

---

## 13. AstrBot 接入

先核对当前版本真实接口，不要凭空假设。

预期：

- `on_llm_request`：快速装配
- `on_llm_response` / `on_agent_done`：记录轮次并非阻塞入队
- 工具钩子：记录调用并外置大结果
- `custom_compressor`：最终保险丝，不是主架构
- 动态上下文优先临时注入，不污染稳定 System Prompt

`on_llm_request` 禁止 LLM 摘要、等待 job、全库扫描和长网络请求。

---

## 14. Sylanne 适配

实现 `adapters/sylanne.py`：

- 检测 Sylanne
- 使用稳定公共接口只读召回
- 获取重要性提示与隐私级别
- 去重双方注入
- 尊重 privacy_level
- 失败静默降级 standalone
- 默认不修改 Sylanne 内部记忆

原则：

```text
Memory decides what remains meaningful across life.
Context Runtime decides what enters this model call.
```

---

## 15. 存储与并发

第一版 SQLite，支持：WAL、append-only events、snapshot versioning、atomic pointer、job lease、crash recovery、migration、导出重建、非阻塞数据库 IO。

---

## 16. 可观测性

提供：

```text
/context status
/context trace
/context snapshots
/context anchors
/context audit
/context rebuild
/context pause
/context resume
```

至少显示：快照版本、覆盖事件、Delta、原始/装配 Token、压缩率、压缩耗时、队列滞后、审计状态、降级模式、各槽预算。

---

## 17. 发布演示

A. 超过 1,000,000 Token 的历史，恢复早期名称、禁忌、决定与待办。

B. 压缩 Provider 延迟 30 秒，聊天仍连续。

C. 杀死 Worker，旧快照 + Delta 继续工作。

D. A → 否定 A → B，最终 A superseded，B active。

E. 与 Sylanne 共存，不重复注入、不违反隐私；关闭 Sylanne 后仍可运行。

---

## 18. 验收指标

硬指标：

```text
主链路等待压缩次数 = 0
压缩失败后的会话可用率 = 100%
精确锚点保留率 = 100%
覆盖缺口 = 0
关键无来源事实 = 0
```

目标：

```text
关键约束召回率 >= 99%
当前任务恢复率 >= 98%
精确实体召回率 >= 99%
过期决定污染率 <= 1%
长期上下文 Token 降幅 >= 75%
Assembler 本地额外延迟 P95 < 100ms
```

正确表述：

```text
storage-lossless
source-verifiable
behaviorally near-lossless
```

---

## 19. 开发纪律

编码前先完成：仓库侦察、AstrBot 接口核对、风险登记、ADR、数据流、数据库 schema、并发状态机、测试矩阵。

编码时：小步提交、完整类型、领域逻辑与 AstrBot 胶水分离、异常可观测、不得吞一致性错误、所有非阻塞承诺必须有并发测试。

---

## 20. 第一轮回复格式

现在不要直接大量写代码。第一轮只输出：

### A. Repository Reconnaissance
### B. Gap Analysis
### C. Architecture Decisions
### D. Implementation Plan
### E. First Patch Scope
### F. Risks

之后再开始首批实现。

---

## 21. 最终定义

AstrContinuum 将无限增长的对话事件流编译为：

```text
稳定状态
+ 活动任务
+ 精确锚点
+ 多分辨率历史
+ 未压缩 Delta
+ 查询相关证据
+ 最近原文
```

并在固定 Token 预算下装配最小充分上下文。

> 上下文会被压缩，对话不会断裂。
