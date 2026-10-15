# P05-Navi-E2 单案例真实模型自主探索

前文是 2026-10-12 r1/r2 的冻结历史记录，历史“当前/下一步”均限定于该检查点；2026-10-15 四模型协议验证见文末独立后续记录。当前授权以 `docs/current-phase.md` 为准。

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


## 2026-10-15 Four-model Responses protocol validation

本节记录第一次 r1 协议检查点。其 2/4 状态及当时下一步是历史事实，后续新授权的 Jizhi GLM / DeepSeek 结果见下节；当前状态以 `docs/current-phase.md` 为准。

本工作单元从干净的 `main@7e230d868d289f351c4c5a0050e08c6f6168e3d4` 开始。实现和离线验收完成；四组真实通道各发出一次请求，结果 **2 PASS / 2 BLOCKED**。要求“四模型均成功”的第一阶段 **NOT COMPLETE**。外部阻断已经记录，不把 HTTP 200、Mock PASS 或本次合成动作成功写成正式 Navi E2 自主研究 PASS。没有 commit/push，没有启动正式剧情研究或 E3；正式 E2 仍为 PARTIAL。

### 接入与不可变运行约束

`E2SDK` 继续使用现有 E2 prompt、batch / single-action 校验、E1 工具、source binding、memory、预算和 checkpoint。新增显式逻辑模型/路由映射，没有默认模型；缺失、不合法或不匹配的选择在派发前拒绝。路由身份记录 logical model、wire model、provider、Base URL、`/responses` 路径、credential slot 名称和 API surface，不包含凭据。TokenMetro 与独立 TokenMetro Qwen 即使共享 hostname，身份和凭据槽仍相互独立。

| 逻辑模型 | route | 已确认 Base URL | runtime credential / URL slots |
|---|---|---|---|
| `gpt-6-luna` | `tokenmetro` | `https://tokenmetro.com/v1` | `TOKENMETRO_API_KEY` / `TOKENMETRO_BASE_URL` |
| `glm-5.3-flash` | `tokenmetro` | `https://tokenmetro.com/v1` | `TOKENMETRO_API_KEY` / `TOKENMETRO_BASE_URL` |
| `qwen3.8-flash` | `tokenmetro_qwen` | `https://tokenmetro.com/v1` | `NAVI_QWEN_API_KEY` / `NAVI_QWEN_BASE_URL` |
| `deepseek-v4.1-flash` | `jizhi` | `https://jizhiapi.site/v1` | `JIZHI_API_KEY` / `JIZHI_BASE_URL` |

GPT/GLM 使用本机既有 TokenMetro 环境配置，DeepSeek 使用本机既有 Jizhi 环境配置。新 Qwen 凭据仅经内存/标准输入进入子进程运行时环境，没有写入本地配置文件、源码、Git、日志或实验文件；既有通道的密钥没有复制到 Qwen 槽。Base URL 在派发前按已确认地址校验。wire ID 先作为明确候选发送，只有成功终态返回身份一致才记为 verified；没有 `/models` 请求、猜别名或试其他地址。

SDK `max_retries=0`。应用层最多 3 attempts，只对已有证据支持的 error-only、生成前 TokenMetro `429 / RateLimitExceeded.EndpointTPMExceeded` 有界重试，且必须同一路由/模型/请求。每次 issued、raw error/stream、terminal、usage、retry 决策独立保留；重试消耗 call ceiling 和 token reservation。普通 429、403、5xx、断流、超时、执行不确定、无 usage、空正文或动作失败不会自动重试。原先的 `TypeError` 兼容分支已改为派发前检查 callable signature，避免派发后异常触发第二次调用。新路径无跨模型、跨供应商或旧协议 fallback。历史 SDK live entry points 和 TokenMetro→Jizhi 自动 fallback 已关闭，MockTransport / fixture opener、请求重建、hash 和只读 replay 保留；旧 SDK benchmark 的 live 入口已退役。Phase04 百炼生产链未修改。

新研究预算仍为 100,000 total / 8,192 output ceiling；本次探测每次仅配置 2,048 output ceiling，未消费正式 E2 轮数或历史预算。历史 r1-r4 的 Chat Completions 请求、hash、冻结配置、父子约束及旧 DeepSeek UNKNOWN 请求完全保留。新增 runner 恢复会检查每个 nested attempt、聚合 terminal、wire 与 stream hash，不能用新模型/路由接续冻结父运行。

### 真实请求、三层门禁与用量

证据根：`data/retrieval/p05-navi-e2-responses-four-model-20261015-r1/`。每模型目录为 `<logical-model>/probe-01/`。四组均 POST 到各自 Base URL 下的 `/responses`；只有两个成功通道验证了该路径的完整 Responses 能力。

输入是同一份小型合成图书管理员情景，要求输出有序 `search/read/clue/stop` batch，使用固定 synthetic RU 标识。返回先经过现有 E2 batch validator，再验证 read/clue 引用绑定、memory hypothesis 和动作顺序。没有执行任何 search/read/clue 工具，没有加载 Canonical、启动连续搜索或执行剧情研究。这验证动作协议和合成引用一致性，不能替代真实 source binding 或研究语义验收。

| 模型 / wire model sent | 返回 model | route / 请求数 | 鉴权门禁 | Responses / E2 动作门禁 | terminal / 结果 | input / output / total tokens |
|---|---|---|---|---|---|---|
| `gpt-6-luna` | `gpt-6-luna` | `tokenmetro` / 1 | PASS | PASS / PASS | `response.completed`, status=`completed`; **PASS** | 570 / 200 / **770** |
| `glm-5.3-flash` | UNKNOWN | `tokenmetro` / 1 | UNKNOWN | FAIL / FAIL（未达到成功门禁） | HTTP 503 `model_not_found`; 无 Responses terminal; **BLOCKED / EXECUTION UNKNOWN** | UNKNOWN |
| `qwen3.8-flash` | `qwen3.8-flash` | `tokenmetro_qwen` / 1 | PASS | PASS / PASS | `response.completed`, status=`completed`; **PASS** | 585 / 712 / **1,297** |
| `deepseek-v4.1-flash` | UNKNOWN | `jizhi` / 1 | UNKNOWN | FAIL / FAIL（未达到成功门禁） | HTTP 524 origin timeout; 无 Responses terminal; **BLOCKED / EXECUTION UNKNOWN** | UNKNOWN |

GLM 错误明确报告当前 distributor 分组没有该模型的可用 channel，但没有服务商返回模型身份或 Responses 终态。这个结果不能证明模型全局不存在，也不能证明账号鉴权或该通道的 Responses 支持已经通过。没有改名、换密钥、换组、换供应商或重发。Jizhi 返回 Cloudflare HTTP 524 错误载体，称 origin 未在代理窗口内返回完整响应；这是服务端载体的报告，不是对具体生成执行或上游内部故障的独立确认。即使载体写有 retryable=true，也不覆盖本工作单元的 UNKNOWN 禁止重发规则。两通道均停止；未向未经确认地址发送凭据。

GPT 报告 reasoning_tokens=36，包含在 output=200 中，没有提供 reasoning 文本，因此 reasoning 文本为 NOT_PROVIDED。Qwen 提供 `response.reasoning_summary_text.delta`，与可见决策正文分别保存；reasoning summary 不冒充完整私有推理链，未报告 reasoning token 分项，因此该分项 UNKNOWN。两个成功响应的 input+output=total 校验通过。累计 **4/12 issued（每模型 1/3）**，0 自动重试、0 fallback、0 Chat Completions；已知 **2,067 tokens + 2 次 UNKNOWN**。实际总量、费用及币种均 UNKNOWN，未访问账单，不用估价替代实际费用。

### 可复核产物与离线验收

- Campaign `authorization.json` 固定四组映射与 3/model、12/overall 上限；派发门禁扫描 issued，包括失败/未完成请求，UNKNOWN 阻止同通道后续派发，其他通道可独立验证。
- 每模型 `config.json` 保存非敏感 provider/request 配置与 run identity；`rounds/00/attempts/01/` 保存 request、wire、issued、允许的 header metadata、`stream.jsonl.gz` 或 `raw-error.bin`、独立 terminal；`rounds/00/terminal.json` 聚合 attempt identities；`action-validation.json` 与 `report.json` 保存实际结果。失败通道未伪造流或终态。
- `protocol-integrity-review.py` 为只读复核入口，直接打印结果，不写文件或联网。冻结结果 `protocol-integrity-review.json`：**163 checks PASS**；涵盖 run/request/wire/terminal/stream、返回 model/usage、动作 replay、预算与无工具执行，以及 `historical-before.json` 中 r1-r4 **110 个文件 hash/bytes 全部保持不变**。
- 最终 offline suites：E2、E1、SDK、semantic batch、历史 live runner fixture、RAG W1/W2、Generation/measure，**289/289 PASS**；命令与退出码为 `offline-test-command-final.json`，日志 `offline-tests-closeout.log`。所有测试均为 provider-free。早期 `offline-tests.log` / `offline-tests-final.log` 保留为中间检查点，不能替代最终日志。最终适配器对四份真实冻结响应的 MockTransport 离线重放也全部一致（`offline-final-adapter-replay.json`，0 provider calls），包括终态 usage、正文一致性和失败通道的 UNKNOWN。
- 最终实现额外补齐了 raw-error hash、流事件 wall-clock deadline、terminal-only visible/reasoning summary、仅采信 terminal usage、流正文与终态正文一致性及不可变 provider config 防篡改。这些收尾通过离线测试；没有为元数据/审计改动重发已完成或 UNKNOWN 的真实请求。真实证据保持原字节，不补写历史 terminal 字段。
- `credential-audit.json`、`validation.json` 与 `artifact-manifest.json` 分别保存凭据扫描、最终编译/diff/source-hash 验证及本 campaign 产物清单；凭据扫描也检查压缩流展开后的内容，不输出密钥或密钥指纹。

本工作单元的实现、必要测试、四通道实际尝试和文档收尾已完成；要求四模型均成功的第一阶段仍因 GLM/Jizhi 的外部阻断未完成。没有剩余自动派发任务，不把尚有预算理解为重试授权。下一步只保留审查与外部路由问题处理；进一步独立探测需要新的明确授权并遵守 UNKNOWN 不重发。正式 E2 PARTIAL、无 active Navi view/KG/semantic acceptance、E3 NOT AUTHORIZED 均不变。


## 2026-10-15 Jizhi GLM and DeepSeek follow-up

用户后续明确报告 DeepSeek 上游故障并要求再测，同时提供新的独立 GLM 凭据及确认地址 `https://jizhiapi.site/v1`。据此只新增 **DeepSeek 1 次 + GLM 1 次**独立合成 Responses 协议请求；没有恢复或重发 r1 的 UNKNOWN 请求，没有增加正式研究授权。新 root 为 `data/retrieval/p05-navi-e2-responses-followup-20261015-r2/`。其 `authorization.json` 固定原 campaign root 和 manifest SHA-256、每模型新增 1 次 / 总新增 2 次上限，同时将原 4 次 issued 纳入 3/model、12/overall 累计约束。探测 input 明确带有新的诊断授权身份，run/request identity 与旧请求不同；历史 UNKNOWN 仍未解决。

GLM 当前映射改为 **`glm-5.3-flash` / `jizhi_glm`**，使用 `NAVI_GLM_API_KEY` / `NAVI_GLM_BASE_URL` 独立槽。新凭据经不回显的交互输入进入进程环境，仅发送到已确认的 Jizhi `/v1/responses`，不写配置文件或实验文件；未使用 DeepSeek 的 Jizhi 密钥。旧 `glm-5.3-flash` / `tokenmetro` 已从可执行 E2 映射移除，冻结 r1 中该历史配置未改写。其余三组路由保持原配置。本次没有使用任何旧协议或模型/供应商 fallback。

| 模型 | 路由 / 实际 wire & returned model | HTTP / Responses terminal | 鉴权 / Responses / E2 动作 | 新增请求数 | input / output / total |
|---|---|---|---|---:|---|
| GLM | `jizhi_glm` / `glm-5.3-flash` | 200 / `response.completed`, `status=completed` | PASS / PASS / PASS | 1 | 610 / 197 / **807** |
| DeepSeek | `jizhi` / `deepseek-v4.1-flash` | 200 / `response.completed`, `status=completed` | PASS / PASS / PASS | 1 | 620 / 234 / **854** |

两组均真实 POST 到 `https://jizhiapi.site/v1/responses`，服务商返回 model 与 wire 配置一致。相同短合成输入下，返回的 batch JSON 通过现有 E2 决策校验及四动作顺序、read/clue fixture 引用绑定和 memory hypothesis 校验。stream、正文、terminal 与 usage 均完整保存，input+output=total 校验通过。服务商未提供 reasoning 文本或 reasoning token 分项：分别记 NOT_PROVIDED / UNKNOWN，不能推断服务端未推理。SDK 和应用层本次均为最多 1 attempt；没有重试、隐式换路由或 Chat Completions，没有工具执行、Canonical 回读或白夜国研究。

本次新增 **2 次请求 / 1,661 tokens**。合并原 r1：GPT 1/3、Qwen 1/3、GLM 2/3、DeepSeek 2/3，累计 **6/12 issued**，**3,728 已知 tokens + 2 次历史 UNKNOWN**；费用、币种和包含旧请求的实际总 token 仍 UNKNOWN。新成功不抹去原 GLM 503、DeepSeek 524 证据，也不把旧 UNKNOWN 改为已完成。

结合 r1 GPT/Qwen 成功证据，**四逻辑模型 Responses/action 协议第一阶段 4/4 PASS / COMPLETE**（GLM 当前采用新 Jizhi 独立路由）。这不是四模型研究质量对比、正式 E2 自主研究 PASS 或 E3 准入。正式 E2 保持 PARTIAL，E3 NOT AUTHORIZED。所有请求已停止，没有 commit/push。

### Follow-up validation and review

- `offline-tests.log` / `offline-test-command.json`：**292/292 provider-free tests PASS**，覆盖 E2/Responses、新 GLM 独立凭据拒绝、旧 GLM 路由拒绝、新授权边界/累计计数/先前文件篡改拒绝、E1、SDK、batch、历史 fixture、RAG W1/W2、Generation/measure。没有使用真密钥做测试 fixture。
- `integrity-review.py` 为只读复核入口（打印结果，不写文件或联网）；结果 `integrity-review.json` **222 checks PASS**。重新校验原 campaign manifest 所列 **72 文件**和历史 E2 r1-r4 **110 文件**完全不变；验证新 authorization/config/request/wire/issued/terminal/SSE hash、返回身份、usage、动作 replay 及累计预算。
- 原 r1 审计脚本保持冻结；它导入当时的模型映射，不应将当前 GLM 映射套入其历史 route 判断。新 follow-up 审计按持久化历史配置和冻结 manifest 校验原证据，不修改旧脚本或旧结果。
- 每模型 `config.json`、`rounds/00/attempts/01/`、聚合 terminal、`action-validation.json` 和 `report.json` 保留完整独立证据。`glm-credential-audit.json` 记录新凭据在本轮证据中的扫描（含 gzip 展开）PASS，无密钥或指纹输出。
- `validation.json` 和 `artifact-manifest.json` 记录最终源码/文档 hashes、compile/diff 检查、运行结果和产物清单。前一轮源码快照保持原样，本轮保存独立新快照。

此次明确授权只支持新的独立诊断，不创建自动越过 UNKNOWN 的通用恢复机制，也不允许重置预算。后续 provider 请求或正式 E2 剧情研究需要另行明确授权。
