# 演示脚本

[English](DEMO_SCRIPT.md) | 简体中文

> 本页列出应由受控脚本复现的演示场景，而不是当前 `v0.3.0` 已交付或已验证能力的清单。
> 每项场景都需要与其对应的运行时、探针和证据门槛，不能用此脚本替代生产链证据。

## 百万 Token 穿越

在早期埋入仓库名、用户禁忌、被否决的决定和未完成任务。跨越多个窗口后，正确恢复这些
信息，并展示 Source Trace 和 Token Map。

## 30 秒压缩不阻塞

将压缩 Provider mock 为延迟 30 秒。在此期间连续发送消息，Bot 仍正常回复。Inspector
显示已提交的 V7、运行中的 V8 和持续增长的 Delta，随后无感切换。

## 杀死 Worker

在候选生成后、提交前抛出异常。旧 Snapshot 仍为 active；新请求使用旧 Snapshot 加
Delta，重启后执行恢复。

## 决定反转

同步压缩 → 否决 → 影子压缩。最终同步方案为 `superseded`，影子压缩为 `active`。

## Sylanne 共存

打开与关闭 Sylanne 时对比 Assembly Trace，检查没有重复注入或隐私越界；关闭 Sylanne 后，
主流程仍应正常工作。
