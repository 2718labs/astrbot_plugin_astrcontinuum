# ADR-001：压缩永不进入回复关键路径

状态：Accepted

用户请求必须使用最近 committed snapshot、未压缩 Delta 和本地检索立即装配。任何 LLM 压缩只在后台执行。

后果：系统必须允许快照落后；Delta 始终可用；需要 emergency assembly、后台队列和故障恢复。
