# Phase05 Navi v0.2｜第二份冻结资料的 Responses user-input 实测

**日期：2026-10-28。状态：本次诊断机械 PASS；不是 F2 正式槽位，不代表模型语义通过人工认可。**

## 执行范围与输入

- 本次只发送 **1** 次真实模型请求；没有自动重试，没有发送 F2 剩余 47 条正式任务。
- 模型：极智 `deepseek-v4.1-flash`；Responses API；`stream=true`。
- 使用预先冻结的 v0.2 rc1 生成样本 `6c2d16a80dac01dc3f8c-rc1`：A《艾拉·马斯克【丘丘人语研究学者】》；B《丘丘人诗歌选》。
- Prompt 与原文合并为单个 `input`，不再发送顶层 `instructions`，不向服务端发送 `text.format` Schema。
- `max_output_tokens=16000`；本地仍按 v0.2 Schema 和双端证据 ID 验证。
- 实施使用独立诊断入口 `src/genshin_corpus/retrieval/navi_v02_user_smoke.py`，复用历史的 SDK/httpx/raw SSE 留存链，不修改正式 F2 运行器。

## 可直接核验的结果

| 项目 | 结果 |
|---|---|
| HTTP / 响应 | 200 / completed |
| 原始 SSE 与 SDK 对齐 | PASS |
| 完整输出的 message 与整流结构 | PASS / PASS |
| Reasoning ID | **本次一致**，无冲突 |
| JSON / v0.2 rc1 Schema | PASS / PASS |
| 双端证据 alias 绑定 | PASS |
| 输出与费用 | 输入 5,047；输出 15,902（其中推理 14,320）；总 20,949 Token；实际账单 UNKNOWN |
| 输出余量 | **98 Token**（16,000 - 15,902），极接近上限 |
| 语义与发布 | `NOT_VALIDATED`；`NOT_REVIEWED`；`INACTIVE` |

实际生成了 3 条命题：2 条 C 级（作品/卷册对应；部分语言材料对照），1 条 B 级受限解释（艾拉·马斯克与雅各布·马斯克同姓、涉及丘丘语研究）。这仅是模型建议：该 B 级命题未能证明两者亲属、师承或身份相同，不能由机械 PASS 自动进入 Navi。

## 对故障定位的意义

1. 与此前另一份《讨龙英杰谭》↔《亡国的美奈姬》内容机械 PASS（但 reasoning ID 冲突）相比，这份新材料 **内容机械 PASS 且完整 SSE PASS**。
2. 因而当前实现的 user-input 包装 **至少能处理两组不同资料**；reasoning ID 冲突并非每次必现。
3. 这不能证明上游顶层 `instructions` 的失效根因，也不能保证后续请求没有 reasoning ID 冲突或 Schema 失败。
4. 16000 Token 本次仅剩98，说明模型很可能把大量输出额度用于推理；`max_output_tokens=16000` 不能作为任意 F2 样本的安全上限。
5. 离线适配器 `navi_f2_content_recovery.py` 能为两臂渲染相同的传输字段，分开记录内容验证和协议异常，并标记接近输出上限的请求，但**尚未接入正式 F2 48 槽执行器**。今后若采用此包装，应建立独立公平实验版本，不修改原 F2 首条失败或分母，也不能自动重发未知状态请求。

## 保存位置

- 原始请求、raw SSE、SDK 事件、终态、用量：`data/retrieval/p05-navi-v02-user-smoke-20261028-r1/`。
- 可重复的单次诊断入口：`src/genshin_corpus/retrieval/navi_v02_user_smoke.py`。已有目录存在即拒绝重跑。
- 离线兼容适配层：`src/genshin_corpus/retrieval/navi_f2_content_recovery.py`。
- 相关测试：`tests/retrieval/test_navi_f2_content_recovery.py` 与 `tests/retrieval/test_navi_v02_user_smoke.py`。
- 本轮相关离线测试 **29 PASS**，没有额外模型请求；不触碰 Phase04、并行 Embedding、KG、旧 G1 和旧 F2 产物；未提交或推送 Git。

**结论：离线 Navi v0.2 生成的 Responses user-input 兼容方案获得第二份真实样本支持；可作为 F2 下一版调用基础，但不等于原 F2 已恢复或全库生产已获准。**
