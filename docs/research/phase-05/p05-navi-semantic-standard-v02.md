# Navi语义审核标准v0.2：G1-S1实验工作规范

状态：**2026-10-26用户确认的v0.2语义工作规范；设计原则已定稿，模型效果、人工语义验收及发布未获验证或授权**。
用于后续命题级生成/审核的规范准备，不改写G1或G1-S1历史数据与统计。
[S1报告](p05-navi-g1-s1-offline-20261026.md)保留当时的agent判断及待定状态；
本次原则落实与新样本准备见[定稿报告](p05-navi-v02-finalization-20261026.md)。
[Prompt/Schema候选接口](p05-navi-b-prompts-v02.md)未接入模型runner，未经过新模型实验。

## 1. 审核单位与权威

审核单位首先是**具体命题**，不是一对来源“总体看起来相关”。同一来源对可以包含
不同强度、不同用途的命题。记录原始命题，再拆分其引用、设定、解释和事实断言；
不得用一个正确的引用关系为整个历史假说背书。

Pair仅为来源与候选的组织容器。Proposition独立接受语义审核、内容资格判断与导航准入。
错误原命题不自动否决另一个安全改写或新发现；任何单项A/B也不能覆盖其余命题。

- 每次实验保留预先冻结的完整来源对与原命题分母，失败、缺字段、U和待定结果均保留。
- agent审核权威固定为`AGENT_REVIEW_NOT_HUMAN_GOLD`；模型审核另记模型身份，二者都不是人工金标准。
- `human_acceptance=NOT_REVIEWED`、`navigation_status=INACTIVE`。
- 发布建议与语义等级独立；`ELIGIBLE_PENDING_RELEASE`也不表示已经发布或用户认可。
- S1历史回放没有项目LLM/Embedding调用，48主对与2补充对分开；本次定稿亦不调用项目API。
- 用户确认设计原则不等于认可历史全部Link；命题拆分不自动生成或发布正式Link。

## 2. 顺序：先机械，后语义，最后比较历史

1. **机械范围**：核对冻结的pair/命题ID集合、快照、Canonical文件哈希、真实section/unit与
   字符范围、snippet哈希、RU完整重建、交付alias和端点。记录截断及未交付范围。
2. **固定原命题**：保留原文表达与出处，确认它断言了什么；区分“文献提出H”与“H成立”。
3. **拆分**：将引用、人物/事件对应、机制补充、文学解读、身份/因果断言分别判断。
4. **支持程度**：逐子命题填写下表，给出双端ID和具体理由。
5. **信息增量**：至少写明一个方向，说明读另一文后新增的可检查理解；无则明确无。
6. **来源角色**：按本次实际文字定位角色，记录说话者、传闻、小说、编者和考据限制。
7. **等级与资格**：分别提出建议；不能因目标A/B比例、旧评分或标题命中而提级。
8. **解除评分遮蔽**：第一遍冻结后，比较原模型、程序保守建议及定向复核，编码变化原因。

S1第一遍仅隐藏评分字段。既有聊天记忆、用户指定重点案例、候选中的评价措辞和来源角色
标签仍可能造成确认偏差；不能称为严格独立盲审。看到需要补证的命题，使用已授权的
离线原文定向检查，记录实际阅读范围，不能借训练记忆补完。

## 3. 三个独立轴

### 3.1 命题证据支持

| 代码 | 含义 | 不能替代的判断 |
|---|---|---|
| `EXPLICIT` | 双端实际文本明确表达对应，或一端明确引用另一端且被引内容可核验 | 不自动证明被引用假说成立，也不自动有阅读增量 |
| `GROUNDED_INTERPRETATION` | 文本支持具体、有限定的解释，但没有明说关联或历史同一 | 不是作者意图证明或人物/事件同一 |
| `WEAK` | 联系可提出，但特异性、证据或增量不足 | 不等于已经证伪 |
| `INCORRECT_OR_UNSUPPORTED_KEY_ASSERTION` | 关键错误/错误归属，或将无依据身份、因果、历史推论作确定事实 | 必须指出具体错误及证据；仅缺材料无法判断时用U，仅文学关系弱时用C |
| `UNDETERMINED` | 对这一必要命题缺少关键材料、覆盖或可恢复执行内容 | 不可将全页覆盖=false机械当作U |

“证明文献说过某话”与“证明该话是游戏世界真实历史”是不同命题。
也不应反过来因为作品是虚构的，就否认游戏文本中明确的改编、事件或设定联系。

### 3.2 实际联读信息增量

合格增量可以是：原著与改编差异、具体事件背景、机制解释、同一叙事的不同视角、
有特异性的文学对照、可被原文约束的新问题。必须说明新增理解的内容与限制。

不自动合格：同名人物列表、地图物产、物品掉落、推荐玩法、同源正文重复、泛泛的
火/孤独/牺牲/命运等共同主题。目录和版本关系可以真实，但另有`CATALOG_ONLY`用途。

反事实检查：“如果把另一文换成任意一篇同主题文本，这段解释是否依旧成立？”
若成立而没有损失关键对应，说明特异性可能不足。这个检查是语义问题，不能改写成
固定关键词数量、相似度阈值或必须共享专名的机械门槛。

### 3.3 来源角色与推论边界

记录到证据段落层：游戏剧情/描述、游戏内小说、传说、角色证词、编者整理、玩家
考据、玩法/目录、混合或未知。栏目只提供筛查，不能替代段落归属。

- 二手文章可以准确引用原文，引用这一文本关系也可以达到A；文章的历史解释另审。
- 两篇二手文章重复同一游戏文本，不是两份独立历史证词。
- 小说/传说不能因为文体被一律拒绝；其文本事实、文学对应和世界历史断言分开。
- 角色证词保留说话者立场、传闻、否定和未知，不把NPC自述自动当全知叙述。
- 同一IP的两个文本不是“互为独立现实历史见证”，但可以明确互文或补充设定。
- 源角色不明而影响关键推论时，建议`REVIEW_REQUIRED`或`UNRESOLVED`；不是所有类别未知
  都必须否定其可直接观察的引用关系。

## 4. A/B/C/D/U与发布建议

| 等级 | 本轮定义 |
|---|---|
| A | 具体、明确、可核验的文本联系，且具有实质阅读增量；不要求对全部相关历史背书 |
| B | 有具体双端锚点和可说明的实际阅读增量，但属于解释性、文学性或受限定的联系；局部增量不自动达到正式剧情准入价值 |
| C | 存在相关性，但特异性、证据或阅读增量不足，不能直接进入正式剧情Navi |
| D | 具体命题有关键错误/错误归属，或将无依据的身份、因果、历史推论作确定事实；不能仅因缺互引或解释未证实而判D |
| U | 必要证据、阅读覆盖或执行结果不足，无法可靠判断该具体命题 |

| 独立建议 | 条件 |
|---|---|
| `ELIGIBLE_PENDING_RELEASE` | 当前A/B命题具有正式剧情导航所需价值且满足内容条件，尚未授权发布；不是整对准入 |
| `REVIEW_REQUIRED` | 文学特异性、核心断言或命题拆分有关键规范争议 |
| `CATALOG_ONLY` | 仅目录、物品、同源版本等关系，剧情增量不成立；可保留非剧情用途 |
| `EXCLUDED` | 当前命题不适合导航，包括D、清楚的泛泛主题包装等 |
| `UNRESOLVED` | 对必要命题缺少可恢复证据/执行内容 |

资格不由单个等级机械决定。例如C可以是`CATALOG_ONLY`、`EXCLUDED`或`REVIEW_REQUIRED`；
A/B也可以因核心争议为`REVIEW_REQUIRED`。发布候选最低A/B只是必要条件，不是充分条件。
有趣巧合或低增量反讽即使可记受限B，也可待审、仅目录或排除；必须另述发布价值理由。
D写具体关键错误与双端/已有证据依据，U写必要证据缺口。未读整页或关键词未命中均不能自动U。

## 5. B级文学准入与反向保护

必须有：双端可定位的具体场景/结构；与该两文相关的理解增量；明确替代解释和限制。
统一标签：**`解释性联读 / 非确定史实`**。

不能单凭共同主题或把两份摘要并排后起一个宏大题目判B。也不要求固定共同意象数、
同名人物或明确互引，否则会错误拒绝真实的文学联读。没有互引既不能单独否定B，
也不能否定原文明示的同一事件/机制等A级联系。对具体样本仍有疑问时保留C/`REVIEW_REQUIRED`，不伪装成已证明无价值；
这不是重新开放已经确认的平衡原则。

## 6. 拆分、配对汇总与防止提级污染

新审核每个命题记录角色：`ORIGINAL_CORE`、`ORIGINAL_COMPONENT`、`SAFE_REPHRASING`、
`NEW_DISCOVERY`或`COUNTERFACTUAL_GUARD`。安全改写绑定被改写原命题；新发现是审核中
另立的联系，不冒称原模型已经发现；反事实警戒不是原模型实际输出。
历史S1没有NEW_DISCOVERY枚举，其SAFE_REPHRASING中可能有后来独立发现的联系；保留原记录，
不根据新定义猜测或批量改标历史角色。

- S1来源对`grade_v02`只描述当时对原始核心的历史判断；后续不得用它作为Pair正式准入级别。
- 新接口每命题独立grade/release；Pair只有候选、无候选、尚未解决的汇总状态，不拥有覆盖性等级。
- 若原始核心把明确引用和未证实历史断言绑在一起，不能靠A级引用提升整条；保留
  原命题D/U/待审，并单列安全A/B子命题的资格。
- 若原命题只说“文章提出H”，不能因为审核者添加了“H客观成立”的D级反向例，
  就把原命题判D或统计为旧模型错误。
- 目录对应明确但无剧情增量，支持轴可以`EXPLICIT`而等级为C/`CATALOG_ONLY`。
- `reason_codes`可多选，原因频数之和不等于48；等级变化与命题/资格变化分别统计。
- 审核必须为每个输入原命题保留一条原文不变的实际判断。REWRITE另存，不能以改写结果充当原判断。
- `NEW_DISCOVERY`不必依附错误原命题；满足自身条件后可以单独成为候选。警戒命题永不进入候选清单或旧模型错误分母。
- Pair汇总先看非警戒命题：有eligible则CANDIDATES；否则有待审/未解决则UNRESOLVED；其余NO_FORMAL_NAVI。
  此汇总仅表示如何整理审核结果；混合Pair中的错误、U和待审命题仍逐条保留，不发生整对发布。

## 7. v0.1迁移与失败处理

v0.1的D包括泛泛相似、玩法和无增量；v0.2这些通常归C，并按用途决定排除或目录。
v0.1的U包括缺扩展字段等执行格式问题；S1可以用已完成原响应与既有真实原文做
agent审核，保留历史FAIL，但不把缺角色字段自动等同语义不可判断。不可恢复的
必要内容仍U；不修改旧JSON、不生成修复后的新正式Link。

变化原因：`DEFINITION_MIGRATION`、`PROPOSITION_MIXING`、`SOURCE_ROLE`、
`INCREMENT_INSUFFICIENT`、`PRIOR_INCONSISTENCY`、`EVIDENCE_MISSING`、
`NEW_SOURCE_INSPECTION`、`SPECIFICITY_DISPUTE`、`CLAIM_SCOPE_CORRECTION`、
`EXECUTION_SCHEMA_NOT_SEMANTIC`、`NO_CHANGE`、`OTHER`。

字段/哈希/alias错误是机械失败；人物同一、文学价值与来源是否足以支持推论是语义
判断，不能声称机械PASS已经解决。若二者冲突，先保留原始失败与明确的审计限制。

## 8. 定稿范围与验证条件

五项用户原则已确认：命题级审核/准入、平衡文学B、明确C/D/U、等级与发布资格分离、
原始错误不能通过改写洗白。它们不再作为本轮待用户选择项。
具体样本仍可有待审/缺证结果；不能用一次原则确认将历史agent判断追认成人工金标准。

新Prompt/Schema只能声称通过已实际完成的离线结构检查。新样本实验未执行，不能声称提高
模型质量或证明全库适用。人工语义认可、正式发布授权另记且不随定稿变化。
全新验证的选择/对照与停止条件见[验证方案](p05-navi-v02-fresh-validation-plan-20261026.md)。

## 9. S1审核文件完整字段与失败保留

本节专属不可改写的S1历史账本；第6节及候选接口定义后续命题级规则，不倒改本节历史字段。

Schema和Prompt草案描述未来模型输出；本轮agent审核使用独立的以下账本。
机器文件所有JSON为UTF-8，CSV使用UTF-8 BOM以便本地查看，嵌套字段在CSV中保留JSON。

| 文件/字段 | 规则 |
|---|---|
| `protocol.json` | 主48与补充2分母、权威、零项目调用、遮蔽限制、并行任务边界 |
| `input-manifest.json` | 全部G1输入文件相对路径、字节数、SHA256，不覆盖历史 |
| `pass1-packets.json` | cases与supplementary_cases分开；每条case_id/pair_id、原candidate、来源sides、覆盖与原响应job |
| `sides.A/B` | source_id/title/record_id、Canonical路径与哈希、来源筛查、selected_evidence_ids、delivered_ids、coverage；保持交付方向 |
| `evidence.json` | 沿用G1精确snippet字段：section/unit/range、text、text_sha256、official_unit_ids与record/Canonical哈希；模型不重写引文 |
| `pass1-authored/*` | 原agent手写批次及冻结前修订均保留；不是程序自动分类结果 |
| `pass1-reviews-frozen.json`, `pass1-freeze.json` | 冻结审核对象及其哈希；只能冻结后解除旧评分；不声称严格盲审 |
| 审核标识 | `case_id`, `pair_id`, `sources`：与原分母、A/B标题顺序严格相同 |
| 对级建议 | `grade_v02`, `release_recommendation`：审核原核心，不取子命题最高分；各自枚举见第4节 |
| 解释 | `proposition_decision`, `increment`, `source_limitations`, `reading_scope`：具体成立范围、至少一个方向的增量、段落来源角色/推论边界、实际读到的范围 |
| `reason_codes` | 第7节枚举，可多选；第一遍的判断理由，第二遍历史归因另存，不能混成错误率 |
| `subpropositions[]` | 非空；role/text/support/grade/release/reason/evidence_ids，B须navigation_label，D须critical_error |
| 子命题证据 | evidence_ids.A/B均非空、属于实际交付本端；必要证据缺失仍引用已读片段并在理由说明缺口，不能虚构缺失证据ID |
| 权威与状态 | review_authority固定AGENT_REVIEW_NOT_HUMAN_GOLD、human_acceptance固定NOT_REVIEWED、navigation_status固定INACTIVE、strict_independent_blind_review=false |
| `reviews-v02.json/csv/md` | 冻结第一遍加historical；原candidate与source_endpoints保留于主JSON/CSV，补充案例单列；Markdown逐条展开 |
| `historical` | initial_model_grade、initial_program_grade、post_schema_model_grade、post_schema_program_grade、final_conservative_grade、targeted_model_grade、targeted_program_grade、targeted_failure、targeted_cohort |
| 历史缺值 | 原模型NOT_PROVIDED与复核未执行/失败的null分别保留；不能当成D或模型反对 |
| `grade_changed_from_final` | 仅字母是否改变的机械对照，不能解释为质量提高/降低 |
| `pass2-change-attribution.json` | 全48条历史理由对照、定向命题/边界、主要原因及解释；是agent归因，非观测到的模型心理原因 |
| `comparison-summary.json` | 完整48分母、迁移矩阵、等级/资格/拆分统计；不使用全体子命题最高级代替对级 |
| `reverse-audit.json` | 全48条反向自审与未决边界；同一agent自审，不称第二位独立评审 |
| 补充与纠错 | supplementary-history-comparison、historical-agent-scope-correction独立保存，不修改旧结论或混入48分母 |
| 机械记录 | mechanical-source-checks、prompt-schema-static-checks、final-input-integrity、verification、final-artifact-manifest保留各自检查范围；机械PASS不认证语义 |

冻结后如改变判断，应另建版本或更正账本，保留原版本与变化理由。不能静默修复历史失败、
移走U结果、改人工认可状态，或把子命题重新包装成已生成的正式Link。

## 10. 用户确认的校准示例（不调整历史成绩）

| S1案例 | 后续适用规则 |
|---|---|
| S14 | 原核心D；安全溯源A独立保留，不因原错否决新候选 |
| S35 | 原命题C；城市发展/传奇消逝的新文学命题可B，用NEW_DISCOVERY说明其审核来源 |
| S19 | 具体器物与人生经历对照可B，不要求互引或作者意图 |
| S04 | 主体混淆单列；宽泛文学对应暂C/待审 |
| S34 | 暂C/待审，不因宏观结构相近自动B |
| S30 | 局部反讽可记受限B，正式剧情发布待审 |
| S09 | 无依据地方传说定位D；商品对应C/CATALOG_ONLY |
| S37 | 必要证据未充分交付则U，缺口不能用关键词无命中证明不存在 |

这些案例及全部S1的48+2对已参与校准，只能用于规则说明/回归，不能用于独立证明新Prompt性能。
