# 非阻塞压缩协议

## 状态机

```text
IDLE → QUEUED → RUNNING → CANDIDATE → COMMITTING → COMMITTED
```

失败：

```text
RUNNING/CANDIDATE/COMMITTING → FAILED
```

旧 committed snapshot 始终保持 active。

## Job 字段

- session_id
- base_snapshot_version
- start_event_seq
- end_event_seq
- lease_owner
- lease_expiry
- attempt
- provider_id
- status

## 合并策略

- 每会话只允许一个运行中任务
- 新事件继续写入 Delta
- 多次通知合并为“压到最新”的意图
- 当前任务结束后如仍超阈值，立即安排下一轮
- 队列不为每条消息创建独立任务

## 触发条件

- Delta Token 超阈值
- Delta 轮次超阈值
- Episode 结束
- 出现重要决定或强约束
- 会话空闲
- 警戒水位
- 手动请求

## 原子提交

事务内：

1. 校验 base version
2. 校验 coverage 连续
3. 写 immutable snapshot
4. compare-and-swap active pointer
5. 标记 job committed
6. commit

任一步失败，active pointer 不变。

## 重启恢复

- 过期 RUNNING lease 标记 abandoned
- 未提交候选不可使用
- active pointer 仍指向最后 committed snapshot
- 根据事件序号重建 Delta
- 恢复 worker
