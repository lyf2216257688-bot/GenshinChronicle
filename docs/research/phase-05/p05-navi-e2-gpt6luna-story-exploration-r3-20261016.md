# P05-Navi-E2 `gpt-6-luna` 白夜国自主剧情探索实验报告（Run 3）

- **日期**：2026-10-16（Asia/Shanghai）
- **运行根目录**：`data/retrieval/p05-navi-e2-gpt6luna-20261016-r1`
- **运行身份（Run Identity）**：`7bca0ea7d50355024c85da3456dd46920c182fe5f2d311897c5171713814d689`
- **模型与路由**：`gpt-6-luna` / `tokenmetro`（Base URL: `https://tokenmetro.com/v1`, 路径: `/responses`, 凭据槽: `TOKENMETRO_API_KEY`）
- **协议表面**：OpenAI Responses API (Streaming SSE, `response.completed`)
- **Git 起始基线**：`main@6755faa`，未执行任何 `git commit` 或 `git push`

---

## 1. 结论与五项核心审查指标

本轮实验历史结果为 **PARTIAL / E3 NOT READY**，保持冻结。2026-10-16 后续只读核验确认故障归因为本地跨消息聚合缺陷，见第 7 节；本次修复不重做本文历史语义评级，也不将离线解析通过算作 E2 成功。

本次实验依据用户授权，在全新的独立运行目录中执行了一次完整的 Phase05 Navi E2 真实剧情自主探索。沿用白夜国研究案例与初始锚点（绘真证言 `3429:s7:u3`），让 `gpt-6-luna` 在现有安全预算内完全自主决定检索词、阅读文献、记录线索与停止时机。

在自主路径与线索质量方面，模型展现了严谨的文献追溯与证据辨析能力：在 Round 00 中，模型自主推导出 3 组针对性检索词，成功命中包括《白夜国地理水文考》第三卷原文、小说《常世国龙蛇传》、珊瑚宫东征与大蛇牺牲事件、白夜国大日御舆与深海弃民等 14 个核心 RU 候选；在 Round 01 中，模型自主决策阅读其中 4 个关键 RU，并严格保持了对角色证言、史料、民间故事、百科整理的审慎限定。

Round 01 返回 HTTP 200、`response.completed`，含两个 reasoning、一个 `item.phase=commentary` 消息和一个 `item.phase=final_answer` 消息；两条消息各自的流式文本均与 terminal 完全一致。commentary 含异常字符，final_answer 是合法 JSON。历史客户端却将两条消息的文本拼接后执行严格 JSON 校验，因而设置 `decision_complete = false` 并触发 `ExplorationError("decision does not conform to exploration mode schema")`，会话安全停止并完整保存证据。原先仅归因为上游输出格式异常的结论被此只读核验修正；严格校验本身仍有效。实验未达成模型自主 `stop`，无请求重试或降级。

| 审查指标 | 评级 | 事实与审查证据 |
|---|---|---|
| **1. 是否自主决定路径** | **PASS** | 除指定初始锚点（`3429:s7:u3`）外，所有检索词（涵盖《白夜国水文考》、蛇神到来、海面迁居、海渊生活、民间故事等）均由模型自主推导；Round 01 规划读取的 4 个 RU 目标完全由模型对检索候选自主筛选。 |
| **2. 是否发现有价值的新真实来源** | **PASS** | 检索命中并锁定了多个全新高价值官方文献与事件载体：<br>1. `3470`《白夜国馆藏》中《白夜国地理水文考》第三卷正文（RU `ffdaf...`）及图书描述（RU `834db...`, `520ef...`）；<br>2. `3470`《白夜国馆藏》中《常世国龙蛇传》小说条目（RU `b87a...`，取材自海祇岛民间故事）；<br>3. `507928`《珊瑚宫》重要事件（RU `54d64...`，记录大御神得到天空岛警示后东征与决战决意）；<br>4. `508883`《白夜国》重要事件（RU `bff4...`，大蛇逃往暗之外海长出珊瑚救助深海弃民；RU `fbd8...`，大日御舆建成）；<br>5. `3227`《珊瑚宫民间信仰初勘》（RU `e9f3...`）；<br>6. `3431`《伊达的挑战状》（RU `e6a8...`）。 |
| **3. 来源判断与叙事限定是否可靠** | **PASS** | 表现极为审慎：<br>1. 明确指出绘真言论是“角色对话，点名失书但不足以证实任何历史联系”；<br>2. 明确指示“不将绘真对话或书目标题当作历史佐证”；<br>3. 明确区分“证言/史料/小说/民间故事/传说/推测”不同文类；<br>4. 明确指出“搜索摘要仅是线索，不可当全文，需检查完整措辞和限定后再决定是否追查”；<br>5. 全程严格保持 `unverified_hypothesis_not_fact` 状态。 |
| **4. 线索（Clue）与工作记忆的事实根据** | **PASS** | 工作记忆详尽记录已读的种子 RU `d9fed651...` 内容，所有陈述 100% 对应已交付的 Canonical 原文，未虚构任何未读书籍细节。 |
| **5. 是否在预算内自主停止闭环** | **PARTIAL** | 系统安全截断 PASS；模型未主动发出 `stop` 动作，而在第 2 次模型派发解析阶段因本地跨消息拼接污染正式决策输入而触发 fail-closed 停止（状态 `partial`）。未形成模型自主 stop 闭环。 |

---

## 2. 实验配置与初始锚点

### 2.1 运行配置与安全预算
- **运行根目录**：`data/retrieval/p05-navi-e2-gpt6luna-20261016-r1`
- **运行唯一标识**：`7bca0ea7d50355024c85da3456dd46920c182fe5f2d311897c5171713814d689`
- **预算硬上限**：最多 6 轮决策、8 次搜索、12 次阅读、8 个页面、20,000 Canonical 正文字符、100,000 工具响应字符、8,192 单轮 output tokens、100,000 总 tokens。
- **初始锚点**：`3429:s7:u3`，真实 RU ID `d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8`（《龙蛇藏归辑录》剧情对话，绘真遗失五本书籍证言）。

---

## 3. 逐轮执行轨迹与模型决策详情

### Round 00（动作 000–003）
1. **动作 000（Seed Read）**：
   - 读取种子 RU `d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8`。
   - 交付正文字符 1,089 字，工具响应字符 13,535 字。
   - 原文关键内容：绘真谈及未追回的四本书籍《常世国龙蛇传》、《白夜国水文考》、《光昼影底集》、宫地实验记录，以及失窃的第五本书《日月前事》（嫌疑人安贞被关押在大日御舆悬空牢，地走官吓唬将永远遗弃）。
2. **Provider 调用 1**：
   - 消耗：5,052 in / 362 out（含 28 reasoning）/ 5,414 total tokens；HTTP 200，耗时 12.5s，finish=`stop`。
   - 决策内容：3 次检索。
   - 工作记忆：准确总结绘真对话要素，指出其转述地走官威胁只是吓唬人，明确“目前这段不足以证实任何历史联系”。
   - 理由（Rationale）：“先按绘真提及的失书标题及任务主题搜索原文，之后依据返回的正式RU来源再读；不将她的对话或标题当作历史佐证。”
3. **动作 001（Search 1）**：
   - 查询：`《白夜国水文考》`（top_k=5）。
   - 命中 5 条结果：包含《白夜国馆藏》地理水文考第三卷（RU `ffdaf...`）、图书描述（RU `520ef...`, `834db...`）、《伊达的挑战状》（RU `e6a8...`）。
4. **动作 002（Search 2）**：
   - 查询：`蛇神 到来 海面 迁居 渊下宫 历史`（top_k=5）。
   - 命中 5 条结果：包含《佚落迁忘之岛》蛇肠之路（RU `45d2...`）、《珊瑚宫民间信仰初勘》（RU `e9f3...`）、《珊瑚宫》重要事件（RU `54d64...`，记录大蛇受天空岛警示后东征牺牲；RU `f220...`，记录封锁渊下宫与五伤岩）。
5. **动作 003（Search 3）**：
   - 查询：`白夜国 海渊生活 蛇神 民间故事`（top_k=5）。
   - 命中 5 条结果：包含《白夜国》重要事件（RU `bff4...`，暗之外海大蛇长出珊瑚救助深海弃民；RU `fbd8...`，大日御舆建成与昼夜之替）、《白夜国馆藏》常世国龙蛇传小说条目（RU `b87a...`，取材自海祇民间故事）。

### Round 01（历史跨消息聚合失败与安全截断）
1. **Provider 调用 2**：
   - 系统将包含种子阅读原文与 3 组检索候选摘要的上下文组装为标准 Responses 输入（输入文本约 16 KB）。
   - 派发至 `https://tokenmetro.com/v1/responses`。
   - 消耗：3,451 in / 827 out（含 28 reasoning）/ 4,278 total tokens；HTTP 200，耗时 13.1s，finish=`stop`。
2. **保存的输出流事件核验**：
   - 响应数据流（共 796 个事件）中，服务商共返回了 4 个 output items：
     - Item 0: `reasoning`
     - Item 1: `message`，`item.phase=commentary`（共 391 个 text delta，796 字符，末尾包含异常符文 `ос}}\n \n המש`）
     - Item 2: `reasoning`
     - Item 3: `message`，`item.phase=final_answer`（共 387 个 text delta，784 字符，完整合法 JSON）
   - 两段 message 均试图表达以下动作规划：
     - Read 1: `ffdaf5bfbc41b755658f3b14db7c109075066eadd484e506860db4415138c3c7`（水文考第三卷，max_chars=5000）
     - Read 2: `b87a370ae5e4fe6c67572c97a44ad2d45eb013720ecb903f7277f9c8b2a76480`（常世国龙蛇传小说条目，max_chars=4000）
     - Read 3: `54d64c4e3330b0c54f92850becb9c7fc3db01dad0f709b77d04b6bb1e16bae97`（珊瑚宫重要事件，max_chars=4000）
     - Read 4: `bff4d246165f8d5dc7f7ee8b7dd7c8d46e48f7b0bc93b6c96d71b7eda92ac281`（白夜国重要事件，max_chars=4000）
   - 工作记忆规划：“待核查来源：馆藏《白夜国地理水文考》第三卷 RU ffdaf...；小说条目 RU b87a...（据描述取材海祇民间故事）；珊瑚宫重要事件 RU 54d64...；白夜国事件 RU bff4...。搜索摘要仅是线索，不可当全文。任务需区分资料性质、叙述者与推断，不能断言历史联系。”
   - 理由：“优先读取被绘真点名的水文考，并读取对照小说及两条事件资料；需检查完整措辞和限定后再决定是否追查。”
3. **本地安全截断处理**：
   - 历史客户端流处理器收集全部 output text deltas，未区分消息身份，跨消息拼接得到 1,580 字符文本；
   - 本地校验函数 `_is_valid_decision_content` 执行严格的单 JSON 对象解析（禁止 substring 自动截取或容错修复），由于拼接文本包含两个重复的 JSON 块及中间的乱码分隔符，语法解析失败；
   - 系统设置 `decision_complete = false`，捕获 `ExplorationError("decision does not conform to exploration mode schema")`；
   - 系统执行 fail-closed 安全停止，未执行无界重试，未切换模型或路由，会话状态置为 `partial`，完整持久化所有证据。

---

## 4. 资源消耗与审计核算

| 指标项 | 预算上限 | 实际消耗 | 状态 |
|---|---|---|---|
| **模型决策轮次（Model Rounds）** | 6 | 1 轮完整接受 | 有界受控 |
| **Provider 发起请求数（Issued Calls）** | 6 | 2 | 有界受控 |
| **请求成功率** | - | 2 响应到达 / 1 聚合解析失败 | 2 RESOLVED / 0 UNKNOWN |
| **应用层重试（Retries）** | 3 / call | 0 | 无重试 |
| **跨模型/旧协议 Fallback** | 0 | 0 | 严格禁止 |
| **检索次数（Searches）** | 8 | 3 | 有界受控 |
| **阅读次数（Reads）** | 12 | 1（Seed Read） | 有界受控 |
| **已读独立页面（Pages）** | 8 | 1（`5b400499...`，即 3429） | 有界受控 |
| **交付 Canonical 正文字符** | 20,000 | 3,089 | 远低于预算（15.4%） |
| **工具响应字符（Tool Response Chars）** | 100,000 | 29,313 | 有界受控 |
| **实际已知总 Tokens** | 100,000 | **9,692**（8,503 in / 1,189 out / 56 reasoning） | 远低于预算（9.7%） |
| **实际已知成本 / 币种** | - | **UNKNOWN / UNKNOWN** | 响应不含计费信息，未访问账单 |

---

## 5. 历史运行完成时的离线合规审计与全库回归

1. **定向离线审计脚本（`offline-audit.py`）**：
   - 检查总数：**70 / 70 checks PASS**（见 [`offline-audit-summary.json`](../../../data/retrieval/p05-navi-e2-gpt6luna-20261016-r1/offline-audit-summary.json)）。
   - 覆盖项：哈希链校验、2 轮 terminal/attempt/request 一致性、2 份 compressed/uncompressed stream 校验、初始请求 0 oracle 泄漏、敏感凭据扫描 0 泄露。
2. **仓库全量单元测试（`python -m unittest`）**：
   - **920 / 920 tests PASS**（耗时 179.6s，无回归故障）。

---

## 6. 研究发现与语义审查总结

1. **自主探索决策质量**：
   `gpt-6-luna` 在单案例离线剧情探索任务中表现出极高的自主检索意图生成水平。从绘真关于五本遗失图书的只言片语中，模型并未死记硬背或幻觉臆造，而是精准提取出《白夜国水文考》作为检索核心，同时构造了“蛇神到来/迁居”与“海渊生活/民间故事”两个互补维度的检索式，一次性覆盖了水文考、小说、珊瑚宫与白夜国官方事件等关键文献。
2. **证据限定意识的高度保持**：
   模型在工作记忆和理由阐述中，始终保持严格的批判性质疑。它明确指出“绘真对话不足以证实任何历史联系”、“不将书名或NPC言辞当作历史佐证”，并提醒自己“搜索摘要仅是线索，不可当全文”。这种严守证据边界的认知状态与 Phase 05 的设计要求完全契合。
3. **未达 PASS 的根本原因与机制价值**：
   历史运行停留在 `partial` 状态：本地将 commentary 和 final_answer 跨消息拼接，严格 JSON 校验据此拒绝。不能把 commentary 中的异常字符归为正式消息损坏，也不能据此认定多消息本身违反协议。fail-closed 阻止了错误解析后的动作执行；第 7 节的修复通过明确身份和正式消息唯一性校验解决聚合缺陷，不修改历史结果。


## 7. 2026-10-16 多消息处理离线修复（不重放历史运行）

- 基线：`main@580c9e2`；开始时 `docs/current-phase.md` 已修改，本文未跟踪；均保留并仅维护相关事实。未提交或推送 Git。
- 源码核实：`semantic_sdk_runner._responses_event_parts` 返回事件文本但不绑定消息；E2 和两个历史 semantic SDK Responses 消费路径此前直接聚合全部 message deltas。E2 的 terminal 比较也跨消息拼接，无法识别逐消息错配。
- 修复：共用 `_ResponsesDecisionText` 先按 output/item/content 身份逐项核对 terminal、done 快照与增量，再选择唯一 `item.phase=final_answer`；其他 message 必须为 commentary。单条已知身份、已完成的无 phase assistant 消息仍支持，完整 terminal-only 响应也支持。身份不明、多正式消息、未知 phase、拒绝/不支持内容、截断或不一致全部拒绝。JSON 和动作校验保持严格，不截取、不删除字符、不修复，不按第一/最后位置选择。
- 审计：新 terminal 的 `decision_message` 记录正式消息身份、策略及非决策消息 ID；所有原始流事件继续原样保存。commentary 中异常字符仍保留在审计流中。共用 SDK 的本地消息校验失败保持本地拒绝、已完成执行和原 usage，不触发 retry/fallback；Chat Completions 不变，历史 live dispatch 仍禁止。
- Run 3 全量只读核验：Round 00 的 326 个事件选出原有 539 字符正式消息；Round 01 的 796 个事件按消息核验后选出 784 字符正式消息，原严格 batch 决策校验通过。历史 terminal 的 `decision_complete=false`、session PARTIAL、请求/动作和 9,692 tokens 账目均未改变；四个计划 read 未执行。
- 独立离线证据根：`data/retrieval/p05-navi-e2-multimessage-offline-20261016-r1`。`run3-readonly-validation.json` 记录逐消息 delta 数、字数、SHA-256、terminal 一致性及新选择结果；`historical-file-hashes.json` 保存修复前文件哈希。**443 个既有 E2 证据文件（含 Run 3 全部 37 个文件）未变**；两个 Run 3 attempt/aggregate 及 gzip/解压字节数、哈希和行数均核验通过。
- 既有单消息兼容性只读检查：13/13 份保存的 completed Responses 流通过消息选择与一致性检查，包含 GLM、DeepSeek、Qwen 的无 phase 单消息；详见 `historical-selection-compatibility.json`。这不重放任何工具动作，也不改变原先的 schema/动作/语义接受结果。
- 测试 fixture：`tests/fixtures/retrieval/p05-responses-multimessage.json` 是有原始流 SHA-256 的最小投影，只在同一消息同一 content part 内合并相邻 delta，不依赖完整本地语料，不执行历史动作。回归包含正式消息任意位置、legacy 单消息、terminal-only、多个 content part 与交错 delta、身份/phase/done 冲突、多个正式消息、截断、乱码尾缀、重复 JSON、schema/mode 拒绝和两条共用 SDK 路径。
- 本次回归：**931/931 全库测试 PASS**（167.010 秒，0 failure/error/skip）；测试过程显式禁止 socket 网络连接，0 连接尝试。详见独立证据根的 `full-suite.log` 和 `full-suite-summary.json`。全库通过后又补强非字符串 phase/type 的明确拒绝，并对最终代码执行 **191/191 定向测试 PASS**（`focused-final.log` / `focused-final-summary.json`，0 网络连接尝试）。原第 5 节的 920/920 是历史运行审计时点，未回填历史产物。
- 剩余 UNKNOWN：未来真实服务响应和自主 stop 闭环未验证；实际成本/币种仍 UNKNOWN；历史 GLM/DeepSeek 和 Run 2 Round 04 执行 UNKNOWN 未解决。本次只做离线机械修复，不进行语义重评；E2 仍 PARTIAL、E3 NOT READY / NOT AUTHORIZED，后续 E2 运行等待操作者审查及另行授权。
