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

- 产品名：AstrContinuum
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
