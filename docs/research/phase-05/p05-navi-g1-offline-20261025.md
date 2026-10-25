# Phase05 G1: lightweight offline navigation generation

Status: **bounded offline loop COMPLETE with recorded failures; full production NOT READY**.
Current authorization is owned by `docs/current-phase.md`.

## Scope and frozen operating point

Implement only: structure-aware source Cards -> cached semantic queries ->
original-text Hybrid -> B dual-source propositions and self-check -> graded,
inactive Navi Links. No profile index, role-separated matching, multi-hop,
cost estimation, KG, online service, production changes or Git commit/push.

Historical R1/R1-QA sources are read-only inputs. The 128 main ordinary cards
are converted without paid regeneration; raw model quotations are never used
as evidence. Legacy refs bind occurrences, not proof that a detail is correct.
The R1 five-detail ceiling is a historical limitation, not the G1 schema.
All 6,507 QA source-pair dispositions are retained, including unreviewed rows.
The 49 ordinary-query unique held-out pairs and 13 prior model-useful outcomes
are tracked in full as historical discovery preservation diagnostics.

Independent G1 seeds are selected by source-only deterministic stratification
from the QA source pool, excluding every R1 card source and every source in
the old ten-case calibration packet. Their candidate targets also exclude
these sources before the 500-RU arm budget. This is an enriched source-disjoint
validation cohort, not random population sampling, independent human gold,
or an independent replication of R1. Selection never reads QA verdicts.

Select up to two sources per observed book/weapon/artifact/character/quest/NPC/
analysis channel, requiring 400–24,000 selected source characters. Empty
strata are recorded. Four complex old cards, chosen by occurrence count, get
new versions to probe structure/coverage. Their old versions remain intact.
Real Canonical section/unit order is retained; long texts split at exact
sentence boundaries and are read in <=6,500-character batches. Batch cards
remain ordered parts of one Canonical-managed card, with unioned queries and
reading receipts; there is no unrecorded extra model synthesis.

The narrative selector retains R1's module policy but includes its previously
excluded short rich text. Every Canonical unit, including unsupported carriers
and omitted gameplay modules, is inventoried. Complete selected-scope delivery
never means complete page semantic coverage. Queries are generated only from
the source batch, with no target lists. All queries are cached; at most four
source-order evenly spaced queries per independent source execute in this
bounded cohort. Query omission is explicit. B reads original verified snippets
from both sources, at most 18,000 characters per side with explicit omissions.
Selected candidate positions are 1, 2, 3 and 10, frozen before B sees them.

## Grade and authority

| Grade | Meaning |
|---|---|
| A | Useful explicit textual connection, separately qualified source roles; no objective-history certification |
| B | Useful anchored explanatory/literary reading, or secondary interpretation navigation; not established history |
| C | Tentative, speculative, insufficient increment/anchors, or unresolved identity/cause risk |
| D | Generic similarity, contradiction, gameplay-only or no meaningful increment |
| U | Missing binding, invalid/unreviewed output, or evidence insufficient to judge |

Program grading is a conservative projection of model verdict/claim level/
source roles. Legacy source authorship is unverified and cannot automatically
receive A. Secondary analysis cannot become an independent game-history
witness. Model grade and program grade are retained separately. Every output
is `INACTIVE`, human acceptance `NOT_REVIEWED`; mechanical PASS, model review,
and human acceptance are distinct.

B generates a proposition and self-check in one call. A deterministic risk/
outcome sample receives a separate call to the same model, with full delivered
sources and the proposed claim; both judgments are retained. This is a targeted
verification, not a second full expensive audit or an independent judge.
New cards receive source-visible model quality checks for material omissions,
unsupported interpretations and query specificity. Model checks cannot
establish human precision.

## Reproduction and preservation

Entrypoint: `python -m genshin_corpus.rag.navi_g1_experiment STAGE --run RUN`.
Stages: `prepare`, `reuse`, `cards`, `retrieve`, `candidates`, `links`, `qualify`,
`review`, `history-audit`, `replay-unique`, `finish`.
`prepare` additionally requires an explicit `--source-root` for the
read-only corpus/history checkout. No machine-specific path is the default.

Requests and raw Responses streams use existing audited transport with no
uncertain-execution retry or route/model fallback. Up to three positively
proven pre-header reconnects are inherited; UNKNOWN blocks reissue under any
job name. Existing Qwen query embeddings use the established query seam.
Budgets are request/token safeguards, not fee estimates: 180 model calls,
12 embedding attempts, 16 million accounted/reserved tokens, three UNKNOWNs.
Worker count is three; no unattended full-corpus scheduler is created.

Historical input hashes, Canonical/RU reconstruction, exact snippet offsets,
reading receipts, queries, candidates, requests, terminal records, failures,
and generated inventory remain under the new run. No old file is overwritten.

## 实际结果与复用数量

最终根目录：`data/retrieval/p05-navi-g1-20261025-r2/`。
`r1/`仅保留准备阶段的字段路径错误记录，模型请求为0；未覆盖或删除。
完整字段规范见 [G1 fields 0.1](p05-navi-g1-fields-0.1.md)。

| 产物 | 复用 | 新生成 / 新处置 | 说明 |
|---|---:|---:|---|
| 普通语义资料卡 | **128份** | — | 原始响应及旧版均保留；转换不调用模型，不把旧模型引文当原文 |
| 独立新资料卡 | — | **13份尝试：12 PASS、1 FAIL** | 来源不在128卡及10例校准材料中 |
| 旧资料结构补充版本 | — | **4份，均机械PASS** | 506828、6260、3470、517；按结构复杂度选取，原卡不变 |
| 新卡版本合计 | — | **17份 / 21个生成批次** | 16份机械PASS；长资料按真实章节有序聚合批次，不额外压缩细节 |
| 旧QA来源对处置 | **6,507对** | — | 6,496个有效模型判断及11个未审处置均迁移；不视为G1新验证 |
| 独立新候选 | — | **48对** | 12个有效新起点，每个固定取第1、2、3、10名 |

13个独立资料为：两个铳枪手、侠客记、风信之锋、「渔获」、渡过烈火的贤人、
幸运儿、菈乌玛、兹白、龙蛇藏归辑录、直至世界尽头、维护机关·白金典藏型，
以及两篇蒙德/须弥二手考据。NPC类别只有一份满足选择条件；没有为凑数补样。
新起点与候选两端均排除原128卡及校准材料来源；**48对与QA的6,507对重叠为0**。
这仍是从既有QA来源池选择的分层小研究，不能称为随机总体或独立人工金标准。

17份卡共缓存256条查询，其中机械有效卡238条；独立有效12卡缓存134条，
本轮执行48条，另外86条在查询计划中明确保留为未执行。4份结构补充卡的
104条查询未作为独立发现验证。失败卡的18条部分查询不进入检索。
模型卡片细节条数随材料变化，本轮6至50条，没有沿用每篇最多5条的旧限制。

### 普通卡查询的独有发现能力

`ordinary-unique-replay.json` 实际执行20个旧普通卡查询，直接复用原查询文字、
缓存向量及只读原文索引，不调用模型或Embedding、不建立资料卡索引：

- 历史49个独有来源对 **49/49重新发现**；其中13个旧模型useful **13/13重新发现**。
- 19/20个起点的Top20顺序逐项相同；1563的第20名变化，其余19名相同。
  该微小差异原因未定位，不能声称所有排名按位复现；未影响49个目标的复现。
- 13个旧useful迁移为B10/C3，三个C均保留身份/因果风险，而非被静默丢弃。
  针对这三个C另做定向模型复核，保留不同解释，不用其作为独立验证金标准。
- 此结果证明**复用路径保留已观察到的发现能力**，不证明新生成查询具有同样召回率，
  也不证明13个关系已获人工认可。

### 独立新Link与争议处置

B首轮48次响应均完成，但17次只返回基础QA字段，遗漏新增来源角色等字段。
原始失败保持U。单独冻结完整JSON补充规范后，只对这17个已完成、基础双端
证据有效的输出调用模型：16个成功补齐，1个仍失败，未再反复追求完成率。
补充调用不是UNKNOWN重发；候选、原文、首轮响应及新请求均分别保留。

| 口径 | A | B | C | D | U |
|---|---:|---:|---:|---:|---:|
| 补齐字段后的独立新Link建议 | 4 | 17 | 24 | 1 | 2 |
| 定向复核/争议隔离后的新Link | **4** | **14** | **28** | **0** | **2** |
| 全部复用及新Link的最终建议 | **4** | **490** | **4,557** | **1,279** | **225** |

U包括1个有双端原文但模型判证据不足的候选，以及1个字段仍不完整的失败。
最终没有把“判useful”直接写成接受：模型分歧、A/B定向复核失败及具体语义门槛
争议进入C；原始分级和双方判断继续保留。D变C的重复文本例子不是新事实被接受，
而是两次模型对“版本对照价值是否算剧情增量”存在分歧。

16次定向第二调用中，12次字段有效，**5/12发生等级分歧**；另4次为明确格式失败
（一次缺来源角色，三次输出整体反斜杠转义、不是合法JSON）。未修造JSON或
冒充完成审核。这里包含3个历史独有候选和1个看过原文后追加的文学门槛争议例；
不是随机准确率估计，也不是第二模型/人工复核。

## 可直接查看的实际示例

[可读示例及程序提取引文](../../../data/retrieval/p05-navi-g1-20261025-r2/examples.md)
包含完整JSON入口和双端Canonical/RU定位；以下为内容说明，不替代原文引用。

### Navi Card：4855《风信之锋》

[完整卡](../../../data/retrieval/p05-navi-g1-20261025-r2/examples/card-4855.json)
保留普莉希拉、厄伯哈特、猎鹰之死的秘密、拔除钉子榫卯、狼烟、少爷未出现、
贵族讥笑与少女面对结局等具体细节，而不是只写“贵族、背叛、自由”。
实际缓存查询包括“普莉希拉与厄伯哈特少爷在相关故事中的关系与对话”和
“猎鹰是我杀的 秘密 少爷 少女 血污”。每条绑定本卡片段，无未知目标标题输入。

3470《白夜国馆藏》的[新版本](../../../data/retrieval/p05-navi-g1-20261025-r2/examples/card-3470.json)
分两批，保留小说、创世纪叙述、地理文献、龙蜥实验和儿童谜题等不同资料角色，
以及实验/遗传机制与名讳、传说限定。它与旧卡并存，没有重跑全部128卡。

### Navi Link：A，500557《景框内外的虚实》 ↔ 500771《两个铳枪手》

[完整Link](../../../data/retrieval/p05-navi-g1-20261025-r2/examples/link-d2b50fded44d3321dd37.json)
命题是“剧情拍摄以小说为底本，并把兄妹改为年龄相仿的姐妹，产生改编上的再解释”。
剧情端精确片段 `g1:500557:s5:u1:3858-4284` 明示导演认为原著哥哥压低妹妹戏份，
建议改为姐妹；小说端 `g1:500771:s1:u1:0-421` 明示老人、兄妹、六颗子弹与复仇。
B对阅读A增加可检查的原著情节和改编差异，不需要猜人物同一或世界历史因果。
A仅表示明确文本联系，仍为未获人工认可的导航建议。

### Navi Link：B，500771《两个铳枪手》 ↔ 500559《何处盛放的蔷薇》

[完整Link](../../../data/retrieval/p05-navi-g1-20261025-r2/examples/link-604c76e1d8f97cb91e52.json)
关联是小说的复仇结构、虹彩蔷薇意象与剧情案件/拍摄的具体对照。
增量为小说在游戏剧情中的接受、改编和正义讨论；边界保留“情节存在差异，
不能把小说凶手、人物和案件事实直接同一化”。这是解释性联读，非确定史实。

### 争议C：502058《黑曜秘典》 ↔ 1602《渡过烈火的贤人》

[完整Link](../../../data/retrieval/p05-navi-g1-20261025-r2/examples/link-24f3d8d90a7725b0a1fd.json)
两次同模型调用都判B，理由是“火—智慧—远行—孤寂”的组合。来源确有这些词和
相应场景，但是否有足够具体的信息增量，仍可能只是扩写的共同母题。
本轮Codex来源阅读将其标为**门槛争议C**，没有把两次模型一致当成人工认可，
也没有宣称已证明它完全无文学价值。没有推定两名智者同一或因果继承。

### U：605《侠客记》 ↔ 368《碧水原》

[完整Link](../../../data/retrieval/p05-navi-g1-20261025-r2/examples/link-aa68d4a9add59d7a563e.json)
小说涉及归离原、碧水河、琉璃百合及传说女子；另一端主要为地点资源目录。
双端引文准确并不补出女子身份、因果或新增剧情，因此模型判insufficient/U。

二手来源实际保留为解读导航，例如6700考据 ↔ 4446《王下近侍》；原B、定向复核C，
最终保留C，不把文章中的名字、连贯化叙述或结局当作第二份独立历史证词。

## 失败、误判与质量检查

1. **虚报阅读覆盖被拦截**：501683《直至世界尽头》第一批交付23个片段，模型却
   将元数据中全篇37个ID都写入read_ids。虽然细节refs均属于已交付范围，整份新卡
   仍FAIL、首批不被收为有效卡片内容，未进入独立检索。初次响应与后半批内容保留，
   没有靠“看起来读过”补造阅读记录。后续新run的代码已改为只展示本批ID与全篇
   片段总数，增加离线护栏；本轮失败响应仍保留，未重跑该卡冒充原始成功。
2. **字段规范不稳定**：首轮B有17/48缺扩展字段；完整对象补充规范仍留下1例失败。
   缺字段不能由程序猜成角色/文体，失败U保留。后续新run已统一为一个完整对象
   模板，避免先给基础JSON再追加字段；已有请求与结果不重写。
3. **审核器误把有限覆盖当遗漏**：505973卡的审核器承认交付的11段均覆盖、没有
   过推断，却因未交付角色语音、其他任务等而判insufficient。卡本就只声明选定故事
   范围，这不能证明卡遗漏；原评分和Codex的范围错误标记分别保留。
4. **审核器要求不存在于片段的后续**：3429卡记录绘真询问“你说说这本书讲了什么”。
   原末片段正到此处结束，审核却要求补出此后的回答/认可。该“遗漏”部分没有
   此交付片段依据，不应据此强令生成补完；审核其它低风险建议也未被冒称人工决定。
5. **重复文本判定摇摆**：6964与500115为白金典藏型的高度同源文本，主要多出观赏鱼
   标签与小幅措辞。初判D（无剧情增量），再判B（版本对照价值），最终C争议队列。
   来源可对照并不构成两份独立历史证词。
6. **文学边界仍不稳定**：黑曜秘典/渡火智者两次模型B并未解除泛泛母题风险。
   自动审核确会为薄弱关系补写一段看似细致的辩护，不能以文字长度代替锚点强度。
7. **查询预算含噪声**：新卡有获取地点、物品玩法等查询。均有源依据，但对剧情
   发现未必有用；本轮均匀选4条是明确研究上限，不是成熟的查询优先级策略。

17次新版本卡质量检查给出adequate14、insufficient2、material_omission1，查询
specific16/generic1；**这些是模型原评分**。上面的范围误判说明，不能将14/17
称为已验收的语义充分率。4份结构补充卡在模型层均adequate，但没有独立人工
清点全部关键剧情点，旧128卡也没有被重新宣称语义完整。

## 机械验证、账目与重放

- 1,042个所用Canonical文件重新核对哈希；27,394个精确片段保留在来源映射中。
  验证Canonical真实范围、完整RU重建与交叉片段RU归属，不使用模型引文作为出处。
- 最终检查42,622个Link证据引用投影，检查端点、片段字节哈希与取文一致性；
  机械PASS只证明来源/格式，不证明命题正确。保留每个片段原始章节、unit及字符范围。
- R1清单11,625个文件、QA清单101,347个文件，共**112,972个历史文件哈希一致**。
  `historical-integrity-audit.json`保存逐清单结果。
- 119次模型调用：卡生成21、B生成48、字段补齐17、定向Link复核16、卡质量检查17；
  另5次Qwen query-embedding请求。124个请求终态均RESOLVED，UNKNOWN=0、未结=0、
  请求内容重复=0。三次审核JSON失败等仍属于已执行请求，不从账目剔除。
- 已知用量：输入923,318、输出133,846，共1,057,164 tokens（含查询Embedding账目）；
  无本轮费用估算。最初受网络限制的预热未发送认证或模型正文，不冒充模型请求。
- 119份SDK原始流压缩/解压哈希核验通过；新产物未发现配置密钥值泄漏。
- **116项聚焦离线测试通过**：G1护栏18、R1/QA37、模型接口6、既有V55。
  覆盖伪造引文、跨端引用、RU缺片、虚报read_ids、未绑定查询、语义等级/人工
  边界、不可变写入及跨进程UNKNOWN隔离。不宣称全仓测试或语义正确率。
  原114项检查记录保留，新增两项护栏另记于`completion-checks-final.json`。

## 对五个问题的独立评价

1. **资料卡保留关键语义是否足够？** 选定正文中已有可检查的具体人物、行动、
   转折、机制和文学限定，显著超过旧5条细节的密度；足以支持本次有限发现。
   不能据此宣称全页/全部128卡已充分：有一例覆盖失败、结构化与短文/编辑模块
   选择仍有限，跨批理解也仅为有序聚合，未获独立语义充分性认可。
2. **语义查询是否发现具体有价值的新关联？** 有。旧49/13全部复现；独立新候选中
   出现原著与映影改编、龙蛇藏归辑录与安提戈努斯等具体联系，48对无旧QA对重叠。
   补齐后22对模型useful，其中保守争议处置后A4/B14可供优先阅读；这不是人工
   精确率，也没有本轮原文基线增益或最终QA提升的对照结论。
3. **B能否可靠区分明确/解释/泛泛/不足？** 能输出并在若干案例做出合理区分，
   但**尚不可靠到可自动接受**。有双端ID并不能防止泛化辩护、重复版本价值摇摆、
   二手推测和来源遗漏被误读；12个有效定向复核有5个等级变化，小样本非误差率。
4. **生成与自动审核是否必须分开模型调用？** 不宜默认全部分开。B一次完成命题与
   自检，程序做引用/覆盖/字段核验；争议、身份因果、二手、截断及明确联系高等级
   可定向再审。第二次同模型调用本轮确提供改写/降级，也产生范围误判和格式失败，
   不能作为独立验收或可靠性的保证。是否换独立审核模型仍无本轮对照证据。
5. **能否全库正式生产？** **不能**。闭环、原文绑定和审计链已可运行；严格字段稳定性、
   阅读覆盖、文学门槛、审核范围、代表性及最终剧情QA收益仍未过关。所有产物
   `INACTIVE`，人工认可0，Phase04生产链不变。

## 下一步建议（本轮不自动执行）

当前代码已处理三个暴露点：只展示当前批次ID、统一完整对象规范、明确审核器应
区分“选定范围充分”与“整页覆盖”；没有为消除失败记录重跑资料。下一步应有限
确认这些修复的语义效果，明确duplicate/版本价值与剧情信息增量的处置，
并用少量具体文学门槛争议做人工定标。保留旧缓存与已绑定证据，之后再做一个
有来源隔离、含明确/解释/泛泛/不足反例的小规模确认批次及实际剧情QA检查。
不需要重跑128卡、比较索引架构、全量第二审核或直接进入全库生产。

## 交付入口

- `cards-reused.json` / `cards-generated.json`：128份旧卡、17份新版本与显式失败。
- `navigation-knowledge.json`：6,555个最终分级处置，全部inactive；原判断仍在
  `links-reused.json`、`links-generated.json`、`links-qualified.json`中。
- `examples.md` / `examples/`：可读Card/Link、真实引文与完整字段实例。
- `dispute-packet.md`：五个定向门槛/范围争议案例，非盲审金标准。
- `query-execution-plan.json` / `rankings.json` / `candidates.json`：缓存查询、未执行
  查询、原文检索排序、固定候选和独立性检查。
- `ordinary-unique-reuse.json` / `ordinary-unique-replay.json`：49个历史独有候选的
  全量迁移与实际复现；不伪装成独立金标准。
- `targeted-reviews.json` / `card-quality-checks.json` / `agent-review-findings.json`：
  模型审核与Codex来源检查分别记录；没有人工确认。
- `historical-integrity-audit.json` / `request-integrity-audit.json` /
  `completion-checks.json` / `summary.json`：完整性、调用账目、测试与结果。
  交付时的新增机械护栏及最终文件清单另有final/delivery产物，保留原清单。
- `attempts/` / `responses/` / `deliveries/` / `g1-code-snapshots/`：原始请求、流、
  响应、原文交付和各次实现快照。新规范补充另有不可变addendum。

未修改Phase04生产RAG，未覆盖历史产物，未全库生产、上线、建KG、提交或推送Git。
