# AstrContinuum 懒人启动包

这是给 Codex 的可执行启动包，不是普通 PRD。

## 最懒用法

1. 把整个目录交给 Codex。
2. 打开 `CODEX_MASTER_PROMPT.md`。
3. 对 Codex 发送：

```text
完整阅读本仓库中的 CODEX_MASTER_PROMPT.md、docs/、specs/ 和 tests/。
先做仓库侦察和差距分析，然后按提示词执行。不要把项目简化成摘要器。
```

4. Codex 首轮必须先输出：AstrBot 接入面、实施计划、风险、首批修改文件和测试策略。
5. 确认没有跑偏后，再让它编码。

## 已固定的产品决策

- 产品名：AstrContinuum / 星续
- 仓库名：`astrbot_plugin_astrcontinuum`
- 独立插件，专门负责上下文，不复制 Sylanne 记忆系统
- 无 Sylanne 时独立运行；有 Sylanne 时通过只读适配器协作
- 原始历史永久保存
- 任何 LLM 压缩都不得进入用户回复关键路径
- 稳定快照 + 未压缩 Delta + 查询相关恢复
- 多分辨率上下文树，而非单一散文摘要
- 精确锚点不得被普通摘要覆盖
- 候选快照必须审计通过后原子提交
- 压缩失败继续使用旧快照，不影响聊天
- 第一公开版必须形成“压缩—验证—切换—恢复—可视化”闭环

## 当前版本：v0.3.0 Technical Preview

v0.3.0 只定位为已验证的存储与发布安全里程碑：SQLite 迁移计划包含不可变重组账本；显式传入重组记录的发布会在 fenced 事务和 savepoint 内依次写入 Capsule、已提交 Snapshot、成员关系和账本，再执行 active-pointer CAS；账本只会随已提交 Snapshot 被读取。

重组记录必须具有唯一来源身份，`required=true` 的记录只能是 `retained`。任一非 `narrative_summary` 的 `released` 记录都会触发永久 `QUALITY_COVERAGE_GAP`，即使 `strict_audit=false` 也不得发布。标准 `CompactionWorker` 目前没有接入重组器、会传入空记录集；本预览并不把非空账本称为标准后台压缩能力。原始历史不会因此删除。

这不是 v1.0 公共发布：不要据此宣称完整宿主兼容、Provider 矩阵、百万 Token 基准或最终 Web Inspector。当前实现边界以 `docs/ADR-008-REORGANIZATION-LEDGER.md`、`docs/DATA_FLOW.md` 和测试为准。

## 出现以下情况立即返工

- 只有 `summarize(messages)`
- 请求前 `await compress()`
- 压缩失败后删除旧消息
- 一个摘要被反复摘要
- 把 Sylanne 记忆复制进新插件
- 把向量数据库当成完整方案
- 没有来源、回滚、审计和并发测试
- 宣称数学意义上的 100% 语义无损

从 `CODEX_MASTER_PROMPT.md` 开始。
