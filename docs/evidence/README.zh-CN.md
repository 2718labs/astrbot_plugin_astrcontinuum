# 证据数据清单

[English](README.md) | 简体中文

本目录包含公开文档使用的有边界聚合摘录，刻意不包含原始对话、盲测提示词和回答，
以及非必要中间产物。

| 摘录 | 记录数 | 溯源范围 | 不可变来源身份 | 已提交摘录 SHA-256 |
| --- | ---: | --- | --- | --- |
| frozen-r2-outcomes.csv | 4 个试验臂 | 冻结合成 12 场景 × 3 试验聚合；仅限研究 | results.json；来源 SHA-256 E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215 | 415B52EF538B2F7B76CA6815C45BC2C2F6580CC50BD9C16798D83296E8918B7F |
| crm-v21-008-stress-rounds.csv | 12 轮 | 确定性 replay-matching CRM 压力记录；仅限研究 | 修订 af835babbd1ca07619251f83c4e0201264975a49；来源 SHA-256 0C8BDC214588AA53F06DA830181D7EB9FF5D164063E8EC8FB74F75B7D0651760 | 645943F5738320295580CAA62BC228694267251EF8AA01BFA13ECC5C2FFF2A01 |
| v03-update-gate-a/aggregate.csv | 36 个固定分母试验单元 | 合成 opt-in 注入式／围栏化 worker 接线闸门；仅限结构／账本 | V03-WIRE-001；fixture SHA-256 36BA7B902C57903B5B1ABCCE21378812C21F54267F018B8706E57A4BE3A648D9；结果 SHA-256 89032BBF0D23D4B09978F93F774FD5392DF90C440EE02BF0D852A85BC80A10D3 | 08E956A9527AFAA0523A00588D331E293AA54CE5005AD1FC9E3F37C1284D518B |

前两类来源是刻意隔离的历史研究记录。Gate A fixture 和脱敏回执是仓库本地、无
Provider 的结构验证记录。三类数据都只作为有边界的实验文档收录，不能作为语义模型
证据、Provider 基准或 v0.3 发布门槛。已提交摘录可让读者复算本仓库中的图和聚合，
但不能重建未公开的原始研究输入，也不意味着存在 E2E 结果。

Gate A 的逐单元回执与聚合一同提交：`v03-update-gate-a/receipt.csv`（SHA-256
D4382A3778CCEC510EE510716482D67C4BBEFAC1D5630DF2A2488E1B0354EA1D）和
`v03-update-gate-a/receipt.json`（SHA-256
CBD31AEA6F6062F6C9980FA03B0A0EBCB9C82034C6A1E7E7725D9B068256DF1E）。它们只含
哈希、计数、指针版本和稳定状态码。

SVG 视觉语言来自 SHA-256
F50B090888EFEE0A059F9C2593480F0E8FA1879B03E53B12B6E7DFB8C565D2FD
标识的冻结渲染器。它确定了已提交 SVG 所用克制的暗红、墨绿、暖灰配色；这是视觉
语言的溯源，不是新的生产结果。

证据解释边界见[证据摘要](../EVIDENCE.zh-CN.md)。
