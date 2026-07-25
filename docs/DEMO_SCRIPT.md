# 发布演示

## 百万 Token 穿越

在早期埋入仓库名、用户禁忌、被否决决定和未完成任务。跨越多次窗口后正确恢复并展示 Source Trace 与 Token Map。

## 30 秒压缩不阻塞

Mock 压缩 Provider 延迟 30 秒。期间连续发消息，Bot 正常回复。Inspector 显示 committed V7、running V8、Delta 持续增长，随后无感切换。

## 杀死 Worker

候选生成后、提交前抛异常。旧快照仍 active，新请求使用旧快照 + Delta，重启后恢复。

## 决定反转

同步压缩 → 否决 → 影子压缩。最终同步方案 superseded，影子压缩 active。

## Sylanne 共存

开启前后对比 Assembly Trace，证明无重复注入、无隐私越界；关闭 Sylanne 后仍正常工作。
