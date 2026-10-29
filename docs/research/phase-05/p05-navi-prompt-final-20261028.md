# Phase05 Navi 最后一轮 Prompt 候选冻结

2026-10-28，候选 **`p05-navi-b-v0.3-rc1` / `story-admission-v1`**。
本轮离线实现与验证完成，停止默认 Prompt 优化循环。可作为后续另行授权小规模生成的
冻结候选；**不是已验证提高模型效果的版本，也不是 Navi 发布或生产采用**。

## 业务定位与历史边界

用户确认 Navi 用于发现制作组分散于物品、书籍、任务、传说等文本中的隐藏剧情联系，
组织人物、事件和世界观。保留 v0.2 的严格证据、命题拆分、来源角色、原句不可覆盖、
语义等级与准入分离原则；本版前瞻性增加：单纯意象、隐喻、文学结构或宏观主题相似，
不足以进入正式剧情 Navi。没有互引或共同专名也不能单独排除有具体双端剧情证据的联系。
隐藏解释仍为受限解释，不能冒充确定史实或已证实的制作组意图。

旧 v0.2 的文学 B、S1判断及 F2 48槽成绩均不重标。旧完整命题级语义评审仍未完成；
本任务不将其扩成新的24对研究。F2原结果仍是legacy 23/24机械通过、rc1 6/24；
rc1另有7 JSON、8 Schema、3生成角色合同失败，不能直接当语义胜负。

## 接口调查与实现选择

安装环境为OpenAI SDK 3.19.2、httpx 0.28.1、jsonschema 4.26.0；没有安装新依赖。
现有[双路线记录](p05-navi-f2-dual-route-20261028.md)已提交过
`text.format=json_schema,strict=true`，有HTTP 200和单例本地Schema通过，也有复杂响应
不完整；这没有证明代理严格执行Schema。实际F2的json_object仍出现语法和Schema失败。

按[官方协议说明](https://developers.openai.com/api/docs/guides/structured-outputs)，
json_object只约束JSON形式，json_schema用于结构约束；这一说明不认证第三方代理/模型。
离线MockTransport确认当前SDK能够原样传递两种格式。代理与模型的严格执行能力仍为
**UNKNOWN**，本轮没有新能力探测或项目模型请求。

选择一个小模块实现，不增加Agent、SDK封装、依赖或新生成runner：

- 模型只负责命题、证据支持、剧情桥梁、增量、边界、来源角色和阅读声明。
- 程序计算id、审核action、B固定标签、逐命题处置及Pair汇总；输出记录原文哈希、输入注册表哈希与处置原因。
- 生成Schema没有role/candidate_id；审核Schema保留角色与原句引用，逐条检查原句、角色、哈希及唯一判断。非法生成角色仍拒绝，不自动丢弃。
- 模式Schema使用保守结构子集；本地继续运行**原封不动的rc1完整Schema及绑定合同**，包括跨字段条件、双端证据、阅读核算、D/U依据和原句保留。新剧情桥梁检查是附加约束。
- 两种显式格式都可构造未发送Responses草稿。默认保留已观测json_object；strict草稿是可审查备选，均标记send_authorized=false、strict执行UNKNOWN；没有自动探测、降级或重试。

新准入政策与完整Prompt见[候选合同](p05-navi-b-prompts-v03.md)。纯文学B保留B与分析，
但内容建议为EXCLUDED；具体剧情A/B可建议ELIGIBLE_PENDING_RELEASE，仍需独立语义审核和
另行发布授权。UNKNOWN、争议、目录用途与D分别处理，不从单个正确命题提升整对。

## 六对来源、七个离线用例

以下判断由本agent依据既有交付原文编写，权威为
`AGENT_AUTHORED_CONTRACT_REGRESSION_NOT_MODEL_OUTPUT_OR_HUMAN_GOLD`。
它们验证规则能表达什么以及程序是否正确执行这些声明，**不测新Prompt能否让模型给出
这些声明**。未独立盲评，选例已受用户案例与旧结果影响，不构成泛化评测。

| 来源对 / 依据 | 本轮记录与处置 |
|---|---|
| 飞天御剑—飞天大御剑，`88d6…`，A001/A004、B001/B004 | 首次受挫、征程未结束、二度飞天、由剑术转向剑本身与旅程告终，支持具体传说叙事连续性。A/CONCRETE_STORY，可列待发布候选；不证明历史生平或死因。 |
| 匣里龙吟—海螺的回响，`e302…`，A004、B003/B006 | 昆吾制匣约束宝刀与孩子掌刀/海螺建立秩序有文学对照，但没有共同剧情桥梁。保留受限文学B，正式剧情候选EXCLUDED。不能认定两刀同一或推断传播与作者意图。 |
| 艾伦的困境—艾伦·史密西作者尾声，`86af…`，A001/A003、B001/B007 | 对本轮人工构造的“获救者就是作者”确定身份断言判D/EXCLUDED；另记身份是否同一为U/UNRESOLVED，缺独特经历或明确身份材料。控制断言不是F2模型错误。 |
| 赌徒—学士，`61e8…`，A006/A008/A012、B014 | 原F2待审句的控制/不确定性对读可以保留文学B讨论，但未连接具体剧情，EXCLUDED；不因此改写v0.2历史判断。 |
| 哈玛瓦兰战记—普尔希娜论文集，`9692…`，A003/A004/A008/A012、B001 | 原候选颠倒作者与角色，D/EXCLUDED；另存安全改写C/EXCLUDED，仅定位名称与著述，未凭同名推定作者身份或剧情。正确改写不覆盖原错。 |
| 嬗变之尘—异梦溶媒，`6ebe…`，A002/A003、B002/B003 | 素材变质用途可类比，但无共同起源、人物、事件或具体世界观机制桥梁，C/NON_STORY、EXCLUDED。 |
| 两剑同一来源的额外控制审核 | 明确标注本轮构造的“真实历史生平”“第一次伤势导致死亡”“已证实制作组意图”三句均D/EXCLUDED；安全的传说连续性改写独立保留A候选，不能给三句洗白。 |

7个用例共有12条agent编写命题：2条待发布候选（同一来源对的首次生成和安全改写，
不是两条独立发现）、9条排除、1条待查。全部human_acceptance=NOT_REVIEWED、
navigation_status=INACTIVE、semantic_quality=NOT_VALIDATED。

## 实际验证与证据保全

- **64项测试通过，0失败、0错误、0跳过**：新候选18项，以及原v0.2合同、完整Schema和F2 Preflight回归。测试进程拦截socket连接，实际连接尝试0；SDK测试仅使用MockTransport。
- 12份选定Canonical快照、75条去重片段的文件/文本哈希、字符范围、记录归属与RU完整重建通过；没有将栏目、模型引用或嵌入ID冒充证据身份。
- 8个旧Schema失败逐条确认均含Pair汇总不一致。只另外计算诊断汇总说明责任边界，旧失败不转成功；3个旧生成合同失败确含审核警戒角色，新生成Schema拒绝此字段。
- 7个旧JSON失败仍被严格解析拒绝；不剥围栏、不消重复键、不补残缺结尾、不猜缺失命题。仅合法JSON加完整合同通过才产生新候选结果。
- **4,723份受保护历史文件哈希与文件集合不变**。旧v0.2 Schema/校验代码、F1/F2 runner及并行Embedding实现/配置未改。相关文档仅新增明确的历史范围说明或新候选入口。

可移植小fixture：[`navi_prompt_final_cases.json`](../../../tests/fixtures/retrieval/navi_prompt_final_cases.json)。
执行代码：[`navi_prompt_candidate.py`](../../../src/genshin_corpus/retrieval/navi_prompt_candidate.py)；
测试：[`test_navi_prompt_candidate.py`](../../../tests/retrieval/test_navi_prompt_candidate.py)。
本地生成审计与冻结目录：`data/retrieval/p05-navi-prompt-final-20261028-r1/`，包含
`verification.json`、`source-binding-checks.json`、`historical-failure-ownership.json`、
`tests.json/txt`、`protected-inputs.json`、`final-integrity.json`、`candidate-freeze.json`、
两份完整模式Schema/Prompt、14份未发送草稿和7份用例结果。该目录被Git忽略，历史记录同样保留。

从仓库根重跑可移植测试（PowerShell 7）：

```powershell
$env:PYTHONPATH = 'src'
python -m unittest tests.retrieval.test_navi_prompt_candidate tests.retrieval.test_navi_v02_contract tests.retrieval.test_navi_v02_schema tests.retrieval.test_navi_f2_preflight
```

## 结论与停止

这一版将已观察到的汇总与生成角色职责冲突移出模型任务，并把剧情定位落实为独立、
可审查的前瞻准入规则。离线合同与指定案例的表达/处置通过；不会用放松校验换取通过率。
但是现有证据不足以证明新Prompt改善了模型发现能力、语义准确率或真实JSON成功率，
也不能证明当前代理严格执行Schema。程序无法仅凭非空bridge识别伪剧情、隐性身份/因果
偷换或真实作者意图；这些仍需对原文做语义审核。

收费API调用0、Embedding调用0，没有重跑48槽、自动发布Navi、修改生产默认、commit或push。
本轮到此冻结，不默认启动下一轮Prompt优化。后续如开展小规模生成，应另行明确授权实际
调用，并复用本冻结候选与严格失败保留；这不是本任务自动延续。
