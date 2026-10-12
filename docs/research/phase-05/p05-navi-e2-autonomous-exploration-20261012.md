# P05-Navi-E2 单案例真实模型自主探索

日期：2026-10-12（Asia/Shanghai）。起始 Git：`main@4ff3887d8972bf5fda351415d915c7f172417657`，工作区干净。E1 PASS/CLOSED，仅复用工具和来源校验，没有重跑 real smoke；未进入 E3。

## 结论与五项验收问题

本轮是 **PARTIAL / E3 NOT READY**。模型确实自主选择了首轮六个搜索词，正常检索发现了真实候选页面；但第二次模型调用耗尽输出 token，只返回 reasoning，没有可执行动作。因此没有新来源回读，没有主动 stop，不能宣称叙事理解或产品收益已验证。

| 问题 | 观察结论 |
|---|---|
| 是否自主决定路径 | 首轮 PASS（限于 search）：问题和起点以外没有给定搜索词或后续来源；六个搜索词来自原始模型决策 JSON。后续 read/clue/stop 未观察到。 |
| 是否发现有价值的新真实来源 | 候选层命中 `3226`、`3470` 等真实页面；**新有效已读来源为 0**，价值和原文资格尚未验收。 |
| 是否保留叙事限定 | 首轮 memory 保留角色证言属性和 hypothesis 状态，但派蒙/绘真的措辞有归属混杂。小说、传说、作者推测及史料不足的处理 UNKNOWN，未读书籍正文。 |
| 是否预算内主动停止 | 系统有界停止 PASS；模型主动 stop 未观察到。 |
| 是否可审计 | 请求、wire、issued、流、terminal usage、决策、工具回执、来源 lineage、父子身份、预算与失败现场均保存；离线审计 PASS。 |

## Provider、实现与运行身份

实际 route 为 TokenMetro，`deepseek-v4.1-flash`，`https://tokenmetro.com/v1/chat/completions`，OpenAI SDK `3.19.2`，E2 专用 adapter `p05-navi-e2-sdk-chat-0.1`。两次响应流均报告 model 为 `deepseek-ai/deepseek-v4.1-flash`。使用当前 `TOKENMETRO_BASE_URL` / `TOKENMETRO_API_KEY`，未更改生产默认；未调用 Jizhi。

- provider config identity：`f93fc18d97f2aa60b5fd095daa44eaacae74bb0f4b82abfff2a61bd001852b41`
- route config identity：`76415d9d19435d5b6e12cc3a94160093cf9c55703ad349effa8123dd6a7b3d97`
- r1 root：`data/retrieval/p05-navi-e2-real-20261012-r1`
- r1 identity：`315c47029fe13766ca712ce7e5c57a249bddad87a44ea86fba98ea6b20b60e21`
- r2 root：`data/retrieval/p05-navi-e2-real-20261012-r2`
- r2 identity：`4e03947f804234d1522d0fae5a39260a204d1c819bf23c76cbb6359e5ebad394`

新增 `rag/navi_e2_experiment.py`，复用 `LocalExplorationTools`、E1 动作校验/来源解析/checkpoint 序列化，以及已有 SDK route、流终止检测、durable stream writer 和 usage normalization helpers。未复用原子提取 runner 的输出校验：它不接受探索决策协议。没有新增 Agent 框架、KG 边、active view、正式 Packet 或 Generation 调用。

r1 第一轮的六组完整搜索回执在下一次发送前触发 conservative UTF-8-byte token reservation 门禁，状态 `budget_exhausted`；第二次请求未发。对模型输入做窄适配，只投影真实 RU、定位、标题、80 字 snippet（截断可见），完整回执保留。r2 **继承同一案例和已消耗预算**，没有重启六轮/八搜索/十二读取预算，没有重做起点读取或六个 search；它只发了累计第 2 次请求。父子完整台账均保留。

## 自主动作、实际来源与 oracle

固定起点：`3429:s7:u3`，RU `d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8`。起点 read 是用户指定的初始化，不能算模型自主 read。完整 source binding/Canonical payload/hash 与 RU 原文保存在 r1 `actions/000.json` 和 session read receipt。

首轮模型动作：

1. search `常世国龙蛇传`
2. search `白夜国水文考`
3. search `光昼影底集`
4. search `日月前事`
5. search `渊下宫 蛇神`
6. search `白夜国 迁居海面`

来源候选涉及 14 个不同页面，30 个返回 occurrence（各搜索 Top5）。包括 `3226` 常世国龙蛇传、`3470` 白夜国馆藏、`3431` 伊达的挑战状、`3508` 绘真、`3629` 社区书籍解读、`217` 匣里日月、`2481` 珊瑚宫记、`3467` 渊下宫、`3502` 安贞及其他地区/NPC 页面；完整 RU/Canonical 定位在 r2 `final-audit-v2.json` 的 `found_sources` 中。实际模型可见回读只有起点，其他均为搜索片段，不能视为已读正文。

oracle 仅在结束后对照 `p05-navi-representative-cases-20261010.json`：

- 候选命中 `3226`、`3470`；没有命中 `3227`、`2362`。
- oracle 已读命中：0。
- oracle 外的新有效已读来源：0；《珊瑚宫记》等只是待核实候选。
- 初始请求没有四个 oracle content_id 或其参考 RU；r2 中包含的 oracle RU 均来自真实 search 回执，而非预先注入。

首轮 memory 的“绘真自身称…几千年…为派蒙转述体感”措辞应人工复核：seed 原文这句是派蒙台词，不能把它改成绘真本人陈述。两段均在 seed 内，并非读取范围之外的内容。`clue` 动作数为 0；memory 始终保存为 `unverified_hypothesis_not_fact`，source binding 没有被提升为 semantic acceptance。

## 预算、失败、usage 和费用

| 口径 | 限制 / 实际 |
|---|---|
| 模型请求 / 完整接受的决策轮 | 最多 6 / 实际 2 次请求、1 个完整决策 |
| search | 最多 8 / 实际 6 |
| read | 最多 12 / 实际 1（初始化 seed）；自主新 read 为 0 |
| 已读不同页面 | 最多 8 / 实际 1；候选涉及 14 页不等于已读页面 |
| Canonical 正文字符 | 最多 20,000 / 实际 3,089；按交付 Canonical units.value 的字符串叶子累计，排除 metadata，重复载体不去重 |
| 完整工具响应字符 | 单独上限 100,000 / 实际 44,623，含定位/元数据等，不等同正文或 model tokens |
| 实际 provider total tokens | 最多 30,000 / 实际 **13,048** |
| input / output / reasoning tokens | 10,186 / 2,862 / 2,514（reasoning 是 output 的子集，不再加到 total） |
| provider calls / retry / fallback | 2 / 0 / 0 |
| 实际费用 / 币种 | **UNKNOWN / UNKNOWN**；响应只提供 token usage，未访问账单，不按未经核实价格估算“实际费用” |

第 1 次请求：HTTP 200，finish=stop，input 4,498、output 814、total 5,312，latency 252,419.951 ms。
第 2 次请求：HTTP 200，finish=length，input 5,688、output 2,048、total 7,736，output 全部为 reasoning；visible content 为空。流台账终结并归档，但没有可执行决策。这是输出预算 partial，不是 HTTP 失败或一次成功探索。没有改变 output ceiling 后再试，也没有额外样本、429/5xx retry 或自动 fallback。

冻结的 r2 session 早期记账有两处口径不准：`used.tokens=5312` 只计被接受决策；`provider_audit.issued_calls=1` 只计 child。**保留原文件**，最终累计数字以完整 terminal usage 和 `final-audit-v2.json` 为准。实验入口已在本地修正为计入不完整决策的 usage，并区分流完整与决策完整；未重跑真实请求。最终源代码和 live 冻结 source snapshots 的差异明确保留在验证 evidence 中。

## 验证与限制

- Provider-free focused tests：E2 16 项 + E1 工具 25 项 + SDK 48 项，**89/89 PASS**。覆盖来源身份拒绝、假设 memory、预算停止、恢复身份、父子累积预算、已结束决策不可篡改、stream partial、429 保留、UNKNOWN usage 和不完整输出记账。没有重跑 E1 real smoke。
- 定向 `py_compile` 与 `git diff --check` PASS。完整命令/输出位于 `data/retrieval/p05-navi-e2-validation-20261012-r2/tests.log`。
- 离线审计：checkpoint/config/request/wire/terminal/stream hashes、父子 lineage、首轮 oracle 隔离、来源回读与 token 上限 PASS。额外本地审计只回读同一 seed 一次，不交给模型，不增加探索已读来源。
- `final-audit-v2.json` 仅修正 v1 reviewer 对 seed 说话者的描述，机械 checks/statistics 不变；v1 保留，v2 指明被替代文件的 hash。
- 尚未验证：新来源价值、书籍叙事性质处理、有效 clue、多轮 read 路径、模型主动 stop、复杂中断后的真实 live 恢复、费用、问答收益。fake tests 不是模型语义验收。

建议独立 review 先确认 partial 状态及累计记账，再决定是否另行授权同一案例的新 operating point：需处理 reasoning 占满输出 ceiling 与搜索候选上下文开销，保留原问题/起点及累计 lineage。**当前不满足进入 E3 的条件，本轮停止，不自动调用 provider、commit 或 push。**
