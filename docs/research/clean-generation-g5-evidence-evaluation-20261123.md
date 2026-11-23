# Clean Generation 第二批证据利用复验

2026-11-23。问题依据来自业务审计 F03、F04、F05、F09，现有回答与旧运行状态沿用原始记录。本文记录复现口径，运行选择及采用状态见项目当前阶段。 [业务审计](../audits/2026-11-23-business-logic-audit.md) · [G7 原质量审计](clean-generation-g7-70q-quality-audit-20261117.md) · [G6 对照](clean-generation-g6-implementation-live-20261117.md) · [当前阶段](../current-phase.md)

## 封存资料的离线复验

在相同排序、上下文和 Packet 预算下复算。按 G7 总索引定位执行目录和轮次，核验排名、来源记录和阅读回执的哈希。先使用原 Packet 的 `read_receipt_ids` 顺序和原选择重建旧包，再启用新准入规则。Q050、Q068 模拟控制器从既有排名点选指定候选，原题调用、模型输出和旧包均保留原样。G6 的 S2 对照复用了封存排名。 [离线记录](../../data/generation/g5-evidence-quality-offline-20261123/report.json) · [复现脚本](../../scripts/retrieval/evaluate_g5_evidence.py)

| 检查 | 前 | 本次复验 |
| --- | --- | --- |
| 70 题首条请求目录 | 每题 6 个可点名句柄，后台登记 83–100 个 | 将新目录投影叠加到旧请求并核对字节预算，首屏 54–86 个，中位 67.5。分页能按原排名触达全部登记候选。 |
| Q013、Q020、Q046 已读正文 | 锻造、婚姻和卡莉露身份原文均被选段排除 | 三段在相同预算下分别入包。旧 E 编号保留在旧包。 |
| Q050、Q068 排名较后的正文 | Q050 第 16、26 名的两名大调律师和 Q068 第 43 名的角色语音均未读 | 脚本点选并回读这三个候选，三段全部入包。Q050 的完整四人名单仍需逐人核验。 |
| G6 Q046 | S0 保留卡莉露自述，S2 排除了自述和赠礼两段 | 在各自原有排名下，S0 仍保有自述，S2 的两段均入包。 |
| 结构化父级定位 | Q050 缺 100/120 个正文位置，Q070 缺 309/315 个 | 同一观测、块、值指针和字符区间均可回查，两份缺口降至 0。 |
| 首轮直接作答的父级展开 | G7 的 120 个引用地址中，有 4 题的 11 个地址未保存父级 | 对原引用来源重新读取，11 个地址都能获得哈希绑定一致的自然父级。 |
| 已有文本的引用格式 | 55 份已交付答复中 7 份记录 `PASS` 但没有绑定引文 | 对原文和原 Packet 单独重放新版标记解析，七份均取得绑定，共恢复 24 个机械地址。 |

目录可达、读后入包和 E 编号绑定均是离线能力验证。表中的新 Packet 没有重新生成答案。Q060 的身份断言仍是原审计记录中的反例，须按身份桥梁和引文语义独立评阅。Q070 保留了父亲身份和关系的查证缺口。 [G7 代表题和逐题判断](clean-generation-g7-70q-quality-audit-20261117.md#3-代表题错误发生在哪一步)

工程复验通过 201 项相关 Python 回归、20 项共享调用账本测试、40 项 Clean 回读测试、13 项前端单测和 9 项 Edge 场景，前端类型检查及静态页面构建通过。工具反馈在最终综合前是否实际发给模型的记账修正后，复跑 8 项针对性检查。回归覆盖已发布会话的读回、纠正范围、同轮中断恢复、批次绑定、父级展开及引用定位。各测试集合分次执行。 [Generation 测试](../../tests/generation/test_clean_research.py) · [本地聊天测试](../../tests/local_chat/test_product.py) · [浏览器场景](../../frontend/tests/chat.browser.spec.ts)

## 配对实测与独立样本

配对评估使用当前 S0 与 BGE 资产、同一官方模型和相同的每题额度。`gc-g5-official-4` 是第一批可靠性修复之后的旧取证策略，`gc-g5-official-5` 是本批策略。每个问题的两个执行根各有独立的 6 请求上限与调用账本。每题先后顺序交替，盲评标签通过运行时随机分配。历史 G7 结果用于定位根因，配对基线使用当次真实生成结果。 [有限批次和调用合同](../phases/clean-generation-v1.md)

| 题目 | 复核项 |
| --- | --- |
| Q046 | 卡莉露的身份原文，以及迪奥娜与祝福的联系。区分人物推测和文本直接陈述。 |
| Q068 | 「明晨之镜」与分离意志「悖谬」的区别。保留伊涅芙的自述归属。 |
| Q060 | 「诺奇特」自称、渊上化名和诺奇特梨服装是否有有效的身份桥梁。 |
| H02 | 《铛铛铛，大危机！》中的两个任务条件和报案台词。按 Clean `506548` 的 b234、b550、b551 复核。 |

样本定义另外登记了 Q050、合作成就 H01、历史书叙述归属 H03、道具到期与拍卖时间 H04。三个独立样本与 H02 均按固定 Clean 观测的哈希和逐字段字符位置校验。 [已登记题库及原句](../../scripts/retrieval/g5_evidence_cases.json)

首次配对执行遇到官方接口 HTTP 401。Q046 的一个盲评侧占用 1 个请求额度，收到明确拒绝，未交付回答。另一个盲评侧在本地查证中中止，占额为 0。其余题未派发。原运行、盲评记录和未完成检查点保留。更换凭据后，用相同题目和新版代码重新登记，八侧均启动。原先拒绝的 1 次计入 48 次总上限。 [首次暂停状态](../../data/generation/g5-evidence-quality-ab-20261123/execution/progress/0001.json) · [第二次配对注册](../../data/generation/g5-evidence-quality-ab-20261123-r02/registration.json)

盲评者获取 `blind-review/`，该目录含配对答复、各自引用的实际 Packet 内容、来源、Clean 定位及统一评分表。先逐条判断必要结论为直接支持、有据推断、合理保留未知、遗漏、无支持、与原文冲突或无交付，再登记额外的无据实质断言。将按题按侧完成的评阅保存到 `blind-review/ratings/`，以 `seal-ratings --out` 校验题干评价口径、答案哈希和引用地址，创建评阅哈希封存后，通过独立的 `execution/mask-key.json` 揭盲。全部已提交问题作为交付分母，语义统计同时报告无交付，已知 Token 和 UNKNOWN 独立计数。面对单个配对结果，保留能够解释差异的排名、读取、选择、入包和写作链路。该小样本用于观察具体问题与回归。

四对首轮候选的 100 个 Chunk 及顺序逐题一致。共取得 5 份正式回答，旧策略交付 4/4，试验策略交付 1/4。评阅由两个独立的 Codex 评阅者各负责两题，按必要结论逐条记录，再核对答案哈希并揭盲。 [评阅封存](../../data/generation/g5-evidence-quality-ab-20261123-r02/execution/ratings-seal.json) · [Q046、Q068 评阅](../../data/generation/g5-evidence-quality-ab-20261123-r02/blind-review/ratings/selection-q046-q068.json) · [Q060、H02 评阅](../../data/generation/g5-evidence-quality-ab-20261123-r02/blind-review/ratings/eval-q060-h02.json)

| 题目 | 旧策略 `gc-g5-official-4` | 试验策略 `gc-g5-official-5` |
| --- | --- | --- |
| Q046 | 支持 0/2。答出泉水精灵的泛称和相关仪式，漏掉卡莉露身份及其与祝福的证据链。 | 无正式答复。首次响应协议校验成功，第二次发生传输 UNKNOWN。 |
| Q068 | 支持 0/2。回答了「悖谬」，未区分所问源体与分离意志。 | 支持 1/2。用已有正文推断源体称号「明晨之镜」，仍遗漏伊涅芙与分出意志「悖谬」的承接关系。两侧都没有读取首查第 43 名的直接语音。旧策略 5 次请求，试验策略 6 次。 |
| Q060 | 支持 2/2。准确归属笔记中的自称，恰当保留真实身份未知。 | 无正式答复。首次响应协议校验成功，第二次发生传输 UNKNOWN。 |
| H02 | 支持 1/2。正确归属伊涅芙及报案台词中的限定。任务条件原句已经引用，解释将后半句的「体验更佳」扩展到两个条件。 | 无正式答复。首次响应协议校验成功，第二次发生传输 UNKNOWN。 |

第二次运行记录 25 次占额，其中 22 次获得 HTTP 200 并通过内容或工具协议校验，3 次为传输 UNKNOWN。加上首次明确被拒的 1 次，累计 26/48。已知用量合计 491,558 Token，UNKNOWN 的远端执行、用量和费用仍未知。没有因 UNKNOWN 重派操作。 [运行完成记录](../../data/generation/g5-evidence-quality-ab-20261123-r02/execution/run-result.json) · [揭盲后的对照与逐步证据](../../data/generation/g5-evidence-quality-ab-20261123-r02/comparison.json)

三次 UNKNOWN 均发生在试验策略的第二个请求，派发前的请求校验已通过。尝试记录只保留 `URLError`，耗时约 5 秒，无 HTTP 状态。三个请求的实际请求体为 78,552–91,928 字节，同一策略的 Q068 有更大的请求取得 HTTP 200。现有日志无法定位故障发生在连接、传输还是服务端处理。轮次摘要将这三侧记为 `model_fault`，本次统计以共享调用账本的 `UNKNOWN` 为准。 [配对执行档案](../../data/generation/g5-evidence-quality-ab-20261123-r02/execution)

本次试验没有通过默认采用验收，默认继续使用旧策略。离线复验确认了候选可达、关键片段准入、父级定位与格式绑定改善。真实配对仅在 Q068 观察到一项必要结论改善，且控制器仍未利用最直接的已找到原文。余下试验侧没有可评语义，现有样本不足以确认总体回答质量改善或将三次传输故障归因于策略。 [采用状态](../current-phase.md) · [逐步证据对照](../../data/generation/g5-evidence-quality-ab-20261123-r02/comparison.json)

## 复现入口

更换凭据后的重新登记使用 `--prior-reservations 1`，将首次 HTTP 401 已占的 1 次计入确认的 48 次总上限。评测只有在总余量足以覆盖完整的 6 次上限时才开启新的一侧。没有足够余量时保留待评记录，标为未启动。 [第二次配对注册](../../data/generation/g5-evidence-quality-ab-20261123-r02/registration.json)

在仓库根目录使用已有 Generation 解释器。`replay` 只读访问历史输入，在新的 `data/generation/` 子目录写报告，并阻断套接字连接。`readiness` 检查本地资产、协议回执和凭据可用性。`register` 在请求前冻结评测题目、原文定位、代码、资产与总额度，返回注册哈希。

```powershell
$env:PYTHONPATH = 'src'
$gcPython = '.\.local\runtime-py314\generation\Scripts\python.exe'
& $gcPython -X utf8 -B scripts/retrieval/evaluate_g5_evidence.py replay --out data/generation/g5-evidence-quality-replay-recheck
& $gcPython -X utf8 -B scripts/retrieval/evaluate_g5_evidence.py readiness
$gcTrial = 'data/generation/g5-evidence-quality-pair-recheck'
& $gcPython -X utf8 -B scripts/retrieval/evaluate_g5_evidence.py register --out $gcTrial --case Q046 --case Q068 --case Q060 --case H02
```

实际调用由独立的 `run-live` 入口使用已确认的注册哈希和总请求额度开启，沿用现有官方路由、凭据及 `model_calls`。请求占额、单次尝试和 UNKNOWN 保留原状态。同一配对运行的目录只创建一次，按各题各侧独立保存运行和关闭记录。401 或 403 暂停调度。注册代码和资产仍匹配时，显式 `--continue` 跳过已登记结果，按先前许可继续未占额且仍有效的中断轮次及尚未开始的配对侧。代码、资产或评测范围改变后使用新注册。 [本地模型配置](../local-chat.md) · [配对执行脚本](../../scripts/retrieval/evaluate_g5_evidence.py)
