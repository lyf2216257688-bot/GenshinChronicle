# Phase05 Navi 语料结构与叙事关联适用性调查

**日期：2026-10-10（Asia/Shanghai）｜本机、provider-free、network-free｜研究结论待用户审查**

## 1. 结论先行与授权边界

本次调查支持的不是“把 Navi 固定成任务树”，而是：**以真实来源中的完整可读材料为入口，按一个有界研究问题独立探索，再核实跨来源联系。** 任务、人物、地区和文献都能提供不同的可靠起点；重大事件通常没有现成统一字段，必须依靠阅读和搜索建立候选。混合叙事单元可作为试验容器，但不是已批准的新 schema、Agent 或生产 Prompt。

核心发现：

1. 任务类型可以从现有 Canonical **保留的 Raw metadata** 中复算，而不必重抓或重建。1,051 个任务页中有 50 个魔神任务标签、126 个传说任务标签、715 个世界任务标签、316 个限时任务标签；162 页多标签，故不能直接相加。日常委托主要在另一个频道，249/293 页带每日委托标签。
2. 非任务材料不是小众噪声：书籍、地图文献、角色故事、武器/圣遗物、道具描述确实提供互补叙事；同一物件页又可能同时包含玩法联系。**结构引用是可用导航线索，不是确定因果边。**
3. 16,437 个 Canonical record / 84,072 section / 362,420 unit 和 535,802 RU 均真实存在。全库 unit provenance 以及全部 RU provenance 仍为 `unknown`；`dialogue` role 只说明来源组件结构，不是“已验证官方游戏原文”。
4. 现有 Canonical 保存了菜单、filter、template layout、富文本链接、原始组件、对话节点/边和原文定位；RU 只携带其中一部分。**保存存在不等于检索直接可消费**，source-bound back-read 仍不可省略。
5. 最大统计风险不是少了某个分类，而是重复/混合载体：rich text、decoded 结构和 dialogue graph 可重复表达同一原文；角色语音又是普通 `content` 标量。把全页或 RU 字符相加会严重高估，单数 rich-text 又会漏计语音。

### 1.1 开始前 authority 与 Git

读取了 `D:/GenshinChronicle/docs/current-phase.md`、Phase05 specification、Phase02/03/04 相关 contracts、architecture、现有代码与历史资料。起始 `main` 工作区干净，HEAD 为 `bbe25d64b1a63c1a33afe0b3a4954cf44d65612e`，比本机 `origin/main` 引用 ahead 6（未 fetch，非远端即时状态）。

本机 current-phase 仍记录 historical-200 小规模语义质量审核准备为当前 work unit；P05-W1 是 mechanical PASS，real-story-slice acceptance OPEN。用户本次新授权的是独立只读研究，不是替换旧质量结论或批准 compact-navigation/live/backcheck 执行。因此本次继续数据调查，**不更改 current-phase、生产/default、既有 acceptance 或历史 run**。关于主动多轮探索的共识按研究方向比较；若未来要执行，仍需与 source-binding、显式 semantic acceptance、官方 RU/Evidence Packet 权威边界衔接。

## 2. 方法、快照、分母与复核

### 2.1 复用而非重做 Raw census

找到 2026-08-25 Raw census 的恢复文本，位于 `D:/GenshinChronicle_文档交接/01_GenshinChronicle_Full_History.md` 的约 69815 行开始，标题为《GenshinChronicle — 米游社观测枢（OBC）数据调查报告》。它报告 16,437 detail、30 component、78 组合、192 template、53 最深频道组和约 156 代表样本；历史报告也明确 OBC 页面混合游戏文本、Wiki 整理、攻略和考据。

没有在本机 tracked docs 中找到独立的原始 census 脚本/统计 artifact；恢复文本是**历史调查的可读证据，不是本轮重新验收**。复用其结论与分组，不重复 156 样本宽泛精读，也不重新遍历 Raw 全库 profiling。只对当前派生层补足本次问题的统计，并核读相关 Raw。

### 2.2 本轮实际执行

- 按 accepted Canonical manifest 路径读取全部 16,437 record，**逐个核对 SHA-256**；没有由 record_id 猜文件名。
- 全量统计保留 metadata 中的 `ext.fe_ext` 两层 JSON、频道 membership、sections、rich-text、结构化 role、对话结构；不输出整库文本。
- 对 RU gzip 校验 manifest hash，并流式扫描全部 **535,802 行**，统计 carrier、长度、record coverage 与 provenance。
- 提取页面引用 carrier：rich-text `entry_references`、`links.href`、decoded `ep_id` 及 `/content/{id}` 路径。统计去重的是 source–target 对，不合并证据 occurrence、不推断语义。
- 有界核读 4 组跨来源案例与结构控制。附件保留 **27 个 content_id、44 个 section、67 个 rich-text unit、77 个 RU fragment locator**；27 份 Canonical、Parsed、Raw detail 的 hash 全部核对，所有选中 rich-text 的 Parsed 值与 Raw `component.data` 解码后的精确 markup 相等。
- 没有调用 provider、检索服务、网络、Dense/reranker 或 Generation；案例检索是本地 metadata/title、保留 link 和原文关键词扫描。下文“模型可以怎样检索”是建议，不是已执行的模型探索。

### 2.3 严禁混用的数量口径

| 口径 | 含义 | 本次用途/限制 |
|---|---|---|
| page / content_id | 此快照唯一 detail；不是故事、任务节点或人物实体 | 历史与当前 metadata 分组计数 |
| Canonical record | 一个已投影观察 | 16,437；不能跨快照语义合并 |
| section / unit | source module 与投影 unit | 84,072 / 362,420；不等于句子或事实 |
| RU | source-occurrence fragment | 535,802；同一原文可能有结构与文本多种投影 |
| P05 source segment | U1 projection 的 source-bound 输入粒度 | 不等于 RU 或完整场景 |
| normalized 字符 | Python `len(normalized_text)`，含换行、按 Unicode codepoint | 载体规模，不是 tokenizer token |
| 叙事候选 proxy | 指定 component/section 中非空 rich-text occurrence | 非官方来源验收、非 recall、非语义真值 |

历史 18,928,835 字符是 Raw 去 HTML、排除 URL/样式/纯数字、页内去重的启发式值；**不与本次 occurrence 总量作增减比较**。本轮全库 normalized rich-text 总量 16,108,371 字符，另有 plain scalar 和结构 carrier，不代表“官方叙事正文 1610 万字”。

### 2.4 输入身份

- Canonical manifest SHA-256：`be2ce30d7cb759a3598b8ac90776abaa01f6db46d6f56603360fcb1e3a66b1e9`
- Parsed manifest SHA-256：`b52483d76f92102a5cb8ece3340de8aa0630c0a0e1f6dbed04bd28f60922b29d`
- RU manifest SHA-256：`dc6bbd30cc6fbc83fa38132085fb8550f20322082467fe24aadcb4239ca78673`
- RU gzip SHA-256：`b042e707c6478614df2fefc336826eddae33ca26a75069bb243ac88b9baea0e4`
- RU build identity：`49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998`

原件：`D:/GenshinChronicle/data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/metadata/manifest.json`；RU root：`D:/GenshinChronicle/data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru`。完整方法与计数见统计附件；本轮另在全新 ignored 输出根复算主要数字，类别计数/长度、任务标签、语音、RU 和引用统计全部一致（PASS）；案例附件给出实际文件路径、hash、Parsed/Raw pointer、完整 RU ID、gzip JSONL 一基行号和片段 selector。

## 3. 实际类型、数量及可读文本分布

### 3.1 当前频道 membership 与历史唯一归组不同

下表全部是当前 Canonical 保留的**频道 membership**，类别可重叠。历史 118 角色 / 2,292 NPC / 264 活动 / 27 组织是最深频道唯一归组；本次分别为 134 / 2,293 / 265 / 37，不是新增数据或重抓结果。例如角色/组织可能同时属于“空月之歌”主题；不能把 membership 变化解释为新增角色。`55`“冒险家协会”主要装载委托，不应误当成 293 个组织详情。

候选 proxy 定义：任务/委托/活动取 `interactive_dialogue` rich-text **加** section 名为“剧情对话”的 `collapse_panel`；NPC/地图交互取前者；角色取故事/详细/神之眼/逸闻/对话 sections；书籍取非空 `collapse_panel`；武器取相关故事；圣遗物取 `/story`、`/desc`；背包取 `material_base_info` 中 attr.key=`描述` 以及阅读/笔记/抄录/解读 sections。组织不强行制定全页官方正文 proxy。所有选择都是调查入口，可能混有解锁说明、编辑备注和非官方整理。

| 频道 | 页数 | 全页 rich 字符 | 候选非空页 | 候选字符 | 候选页 P50 / P90 / Max | 全页 RU |
|---|---:|---:|---:|---:|---:|---:|
| 43 任务 | 1,051 | 7,006,355 | 1002 | 6,743,547 | 1,973 / 17,257 / 246,938 | 104,904 |
| 55 冒险家协会 | 293 | 635,151 | 217 | 410,919 | 963 / 3,312 / 10,624 | 21,681 |
| 25 角色 | 134 | 1,489,946 | 132 | 493,480 | 3,426 / 6,488 / 8,116 | 87,598 |
| 261 角色逸闻 | 63 | 274,208 | 63 | 204,302 | 2,608 / 5,885 / 10,995 | 9,031 |
| 20 NPC&商店 | 2,293 | 1,535,288 | 2187 | 1,268,920 | 336 / 1,190 / 13,962 | 78,891 |
| 68 书籍 | 93 | 281,928 | 93 | 262,609 | 2,274 / 5,895 / 17,341 | 2,026 |
| 251 地图文本 | 706 | 390,193 | 705 | 385,800 | 185 / 1,514 / 19,454 | 6,286 |
| 5 武器 | 246 | 324,425 | 246 | 138,857 | 489 / 1,060 / 2,109 | 9,676 |
| 218 圣遗物 | 63 | 148,607 | 63 | 112,114 | 1,391 / 3,126 / 7,481 | 2,464 |
| 13 背包 | 2,015 | 508,882 | 2014 | 204,708 | 53 / 153 / 6,265 | 37,527 |
| 105 活动 | 265 | 505,459 | 28 | 62,832 | 0 / 160 / 35,106 | 12,704 |
| 255 组织 | 37 | 156,476 | — | 未定义 | — | 3,840 |
| 62 观测·考据 | 202 | 758,769 | — | 未定义 | — | 5,173 |

P50/P90 的分母是各频道**所有 member 页面，包含零候选页**，按 nearest-rank 计算；不是非空页中位数。不同频道的列不可直接相加。武器故事短、地图文本长尾、任务页极长，说明一个固定“整页上下文”不适合所有来源。

补充：敌人 415 页 / 205,270 rich 字符，洞天 2,328 / 378,285，观景点 264 / 14,151，区域 71 / 38,128，教程 730 / 71,048。它们也可能有世界观描述，但不能仅因含文本就纳入高价值剧情；全部统计见附件。

### 3.2 任务与委托：可以复算，但没有互斥类型真值

`record_metadata.source_metadata.ext.fe_ext` 是 JSON 字符串，其 `c_43.filter.text` 再含 JSON 列表。全量解析成功，无 filter decode failure。1,051 任务页的类型组合：

| 类型组合（精确标签） | 页面数 |
|---|---:|
| 仅世界任务 | 567 |
| 仅限时任务 | 155 |
| 仅传说任务 | 111 |
| 仅魔神任务 | 50 |
| 世界 + 限时 | 147 |
| 传说 + 限时 | 14 |
| 世界 + 传说 | 1 |
| 仅其他任务 | 3 |
| 没有任务类型标签 | 3 |
| **总计** | **1,051** |

所以标签 occurrence 是世界 715、限时 316、传说 126、魔神 50、其他 3；不能默认“限时”与叙事类型互斥。页面常聚合一章/幕、多个子任务或整个系列，50 页绝不是 50 个独立游戏任务。实际如 `509534`《第七章 第二幕「死魂灵的夜曲」》包含 40 sections，template layout 内有 repeated module groups、group name“ 一/二/三…”和 `parent_group_id`；章/幕来自标题文本，不是已统一的章幕语义字段。

同一标签内候选剧情正文长度差异巨大：

| 标签 | 页数（允许重叠） | 候选字符 | 页 P50 / P90 / Max |
|---|---:|---:|---:|
| 魔神任务 | 50 | 1,685,661 | 32,885 / 53,486 / 246,938 |
| 传说任务 | 126 | 1,710,520 | 10,537 / 28,455 / 56,371 |
| 世界任务 | 715 | 2,721,598 | 1,648 / 8,824 / 65,980 |
| 限时任务 | 316 | 1,011,803 | 1,230 / 8,942 / 38,502 |
| 其他任务 | 3 | 32,714 | 2,991 / 27,954 / 27,954 |

`c_55` 在 293 个冒险家协会页中给出每日委托 249、突发事件 31、声望任务 6、冒险之证 4、冒险等阶奖励详情 1，2 页缺该标签；这不是按标题猜测。委托类不宜一概排除：其候选剧情页中位数 963 字符、最大 10,624；但这些字符同时覆盖该频道其他子类型。

任务页还有 **477 个**名为“剧情对话”的 `collapse_panel` section，其中 305 非空、1,503,373 字符；只按 `interactive_dialogue` 收集会漏掉这部分。这里保留的是线性富文本，不具备同等可用的显式 dialogue graph。

### 3.3 书籍体裁与任务道具

93 书籍页的 `c_68` 标签包括小说故事 36、学术作品 13、诗歌哲学 13、童话寓言 11、民俗传说 9、书信日志 6、手册指南 3；4 页缺类型，某页可多体裁。共 294 个非空 collapse-panel sections，**不等于已清洗的 294 卷**：里面可以是卷册、合集分篇或页面编辑内容。应使用真实 section 名、内容和来源上下文，不能用 array index 编造卷序。

2,015 背包页中 967 带 `道具类型/任务道具`。这些任务道具页的 material-base 描述共 55,715 字符，页面 P50=39、P90=99、最大=1,480；短文本也能给出来源、叙述或关键专名。全部背包 material-base 描述 2,220 个非空 rich occurrence、138,943 字符；加阅读/笔记/解读等候选后为 204,708。不得将获取方式、培养素材用途和武器使用表中的链接当成故事关联。

### 3.4 角色故事、普通语音标量与重复

134 个角色 member 页中，132 个有非空故事/详细/神之眼等候选 sections。故事类候选 rich 文本 493,480 字符。**这不包含绝大部分配音正文**：`role_voice.list[i].table[j].content` 是普通字符串，保存在 decoded 中，因此没有 rich_text unit。

该频道内 132 个 role_voice 组件、486 个 audio tabs，所有 tab 的非空 content occurrence 34,075 / 1,405,194 字符；只取 tab_name=`汉语` 为 8,522 / 351,588 字符。其他音频 tab 往往同样承载中文内容，历史 census 已提示这个问题；本次不声称去重后的跨语言文本完整性。**可以按 tab/条目回读，但不能把四个音频语言当四套本地化语料。**

全库 rich-text unit 长度：273,346 个（包括空），P50=8、P90=116、P99=787、Max=22,015。历史 P04 profile 记录 149,510 repeated rich occurrence；等文本只是重复出现，不是可合并 evidence identity。跨 unit、角色音频 tab、结构化和 graph 表达的重复要保留原文来源，而不是计成四倍有效证据。

## 4. 四层实际结构：保留、投影损失与可消费性

代码依据：`D:/GenshinChronicle/src/genshin_corpus/parser/obc/adapter.py`（metadata、layout、decoded/raw）；`D:/GenshinChronicle/src/genshin_corpus/parser/rich_text.py`（markup/tree/link/media/entry）；`D:/GenshinChronicle/src/genshin_corpus/parser/dialogue.py`（原始节点、边和诊断）；`D:/GenshinChronicle/src/genshin_corpus/canonical/projector.py`（module→section、metadata lineage）；`D:/GenshinChronicle/src/genshin_corpus/retrieval/retrieval_units.py`（fragment、structured scalar、node-local edges）。这里谈的是当前代码与快照，不是长期冻结的新类型体系。

| 信息 | Raw | Parsed | Canonical | RU/检索可用性 |
|---|---|---|---|---|
| 频道、filter、alias、page metadata | page menus、ext/filter、alias_name；listing membership 在 inventory | channel_memberships + 完整 source_metadata；不统一解释嵌套 filter | record_metadata 保存；membership lineage 属 parsed_dependency，不捏造 detail Raw 指针 | source_identity/title 可定位；RU 不自动携带全量 filter/menus/alias。筛选需回读 record metadata |
| 章幕/任务层级 | 标题、模块名、template_layout.tab/module_group/parent_group_id、module origin/repeated/is_submodule | layout observations 与原 template 保留 | section module 信息、source_template_layout 保留；不是统一任务 DAG | section ordinal/name 只是 page source order；模板 group 需回读；章幕标题仍是文本证据 |
| 前后置/相关任务 | attr key、页面内 link、相关任务 section 与攻略正文 | 保留键/值和富文本链接 | decoded + rich links/entry refs + lineage 保留 | structured RU 可检索属性名和值，rich RU 可检索显示文本；**无全局 typed predecessor/successor 关系** |
| 对话顺序/选项 | child_ids、contents、root_id、option、icon，常在列表中有多组 | DialogueGraph 保留 source_id/order/edge/option/raw_fields/source_path | 5,002 graph / 5,087 groups / 60,682 nodes / 62,727 edges | node fragment + incident observed_edges；RU 不是完整场景。option 与 dialogue 可合成文本，不足以识别互斥/实际发生 |
| 说话者 | 样本多嵌于“名字：台词”；option/HTML 可有未知来源细节 | 当前 parser 不从冒号推断 speaker | 全库 60,682 node 的 speaker 全为空 | speaker metadata 全空不表示无人说话；须读原文判断，不能机械拆冒号就建 entity |
| 否定/推测/条件/叙述立场 | 在原文及分支中 | 字面保留，不作逻辑标签 | 无全量语义 acceptance | 1200 字符 fragment 可断开限定词/上下文；KG 需局部完整场景/branch 回读 |
| 书籍卷册/作者/出处/内部引用 | 名称、材料描述、collapse 正文/卷名、链接；格式异构 | 原 attr、markup/tree/links 保存 | 294 非空书页 sections；书名/卷名是原始值，非统一 book entity | 可按 section/关键词检索；统一作者/史料可靠度不存在，未统计为完整书目 |
| 地图文本位置 | 地区 filter、点位名、map_desc.list.image/tab_name、交互正文 | 图片 URL/标签和正文保留 | map_desc decoded 与 node/paragraph 来源保留 | 文本 RU 可命中“位置1”等；**截图中的地图坐标并未被 OCR 成结构字段**，单读 RU 不一定知道位置 |
| 武器/圣遗物/物品/角色故事 | related-story、story/desc、attr、plain content、获取/养成/推荐表 | 泛型 decoded/rich 保留，多数非专用 handler | `UNSUPPORTED_COMPONENT` 是专用处理未提升，不是内容被删除 | structured/rich 多重 carrier，需按具体字段选阅读上下文 |
| 页面引用 | data-entry-id/name/link、href、ep_id、structured link | rich references/links 与 decoded 保存 | 明确 source–target 线索可定位 | rich RU 一般不保存完整 links/entry refs；structured RU 有字符串路径，但不等于已 materialize 跨页 graph |
| 来源角色/原文身份 | 平台提供，但作者/是否游戏原文不能仅凭域名成立 | component/record provenance 默认 unknown | 362,420 unit 全 provenance unknown；302,297 role unknown，60,123 role dialogue | 535,802 RU provenance 全 unknown；398,392 role unknown、137,410 classified（仅 carrier role） |

### 4.1 前后置字段不是全库缺失；但页面 ID 不等于子任务 ID

本轮对 1,051 个任务页进一步按 `rich_base_info.decoded.list[i].key` 精确计数，**1,051 页全部具有“前置任务”和“后续任务”字段**。前置字段 occurrence=1,666，其中 1,662 非空、369 字面值为“无”、905 含非空 entry ID；后续 occurrence=1,667，其中 1,661 非空、543 字面值为“无”、626 含 entry ID。按 source–target 去重分别得到 691 / 401 对。

其中前置有 76 个 page-level self-pair，后续有 72 个；不应据此宣布逻辑循环或擅自清理。任务页可包含多个子任务，例如 `2636` 在后续字段指向本页内不同显示名称；页面标识粗于任务节点。还可存在 `data-entry-id` 非空但 href=`无` 的 NPC 名，以及纯文字/no-ID 条目。**可沿带 role 的来源链接选初始资料，但正式任务 DAG 仍需要子模块定位与来源解释。**

`2942` 的“前置任务”明确指向 `2631`《清籁逐雷记系列任务》，`3429` 明确指向 `3427`《白夜国晨昏记系列任务》。这类字段是本次证据支持的任务入口；它们不同于基于标题或页序猜测的关系。所有数值的分母、exact-label 和 self-pair 口径在统计附件 `task_predecessor_successor_fields`，不把含字段等同于语义正确覆盖。

**不是数据已丢弃，而是信息密度从整页到 RU 降低。** 一个 RU 足以定位某个句段，却未必携带相邻否定、章节标题、原文类型、整张分支图或页面链接。应区别“上游可恢复”和“当前检索单元直接能用”。

对话诊断：3,598/5,087 groups 含诊断；unit/context 计数包含 multiple-parent 10,037、orphan 7,759、root-not-found 298、parent-missing 146。它们不是四类互斥坏图数，也不意味着全部对话无效。保留 source ID/edge 和显式不确定性，比补造树结构更安全。

## 5. 跨类型引用统计及四级关系边界

共有 **9,729 个来源页**含可识别引用，去重后 **70,013 个有向 source–target 页面对**：69,591 对 target 存在当前快照，422 对指向 48 个不在快照的 target ID；另有 198 self-reference 对。以去掉 189/190/80/231/263 根频道后两端非空 membership 集合不相交为“跨频道”，得到 61,321 对。只统计路径/字段，不宣称其语义正确。

引用 carrier occurrence：decoded ep_id 15,816、decoded link 217,830、rich entry 92,375、rich href 109,921。后两者/前两者可重复保存同一 link，**不能相加当独立关系数**。养成、材料、推荐、玩法引用很多，证明有跨页面通道，不证明有 70,013 条剧情联系。

| 级别 | 可以说什么 | 不可以说什么 |
|---|---|---|
| 1 确定结构关系 | 页面 A 的具体字段 link 指向 B；group 包含这些 source nodes/edges | 该 link 证明人物身份、因果、时间或游戏内事件 |
| 2 原文直接支持 | 某段人物故事明确引用某本书；某段任务对白明确列出书名；原文自己说明“传说/推测” | 抹掉叙述主体/体裁后宣称世界事实 |
| 3 候选线索 | 同专名、同地点、类似物件意象、事件主题值得检索/对照 | 直接把同名合并、相似描述等同、把多篇拼成确定因果 |
| 4 无依据推断 | UNKNOWN，留待核实或停止 | 依标题排序、编号、模型剧情记忆补人物/时间/原因 |

## 6. 真实跨类型案例核读

选择不是随机质量测试，也不建立 KG；是为比较不同入口的可行性。以下 `content_id:sN:uM` 使用 **0-based Canonical section/unit ordinal**。附件 entry key=`content_id:sN`，每个 rich_unit 含完整 RU ID/Raw/Parsed 定位；正文摘录仅用于展示判断依据。关系级别按上表，不从模型记忆补足。

### 6.1 海祇—白夜国：地图碑文→任务→书籍→材料描述

**问题**：海祇/白夜国的制度和蛇神叙述，哪些来自任务/实物文字，哪些只是书中传说或学者推测？

| 真实来源 | 提供的材料 | 能确定的联系与限制 |
|---|---|---|
| `501987:s1` 古老的石碑【海祇岛】 | 位置1–4 的碑文；“神使目有三瞳”“出于南…越于北…盘桓西岗” | 原文位置标识与碑文内容直接存在；不保证碑文是客观全知史书 |
| `501987:s2` 相关任务 | data-entry-id=`2636` 月浴之渊系列任务 | **级别1** 显式 page link；不自动代表所有碑文对应所有子任务 |
| `2636:s2/s7` 月浴之渊系列任务 | “海祇之眼/鳍”流程要求读石碑/解除封印 | **级别2** 任务文本中的石碑使用上下文；同时含“参考攻略”注释，不作为纯官方台词 |
| `3429:s7:u3` 龙蛇藏归辑录 | 绘真对白列出《常世国龙蛇传》《白夜国水文考》《光昼影底集》与实验记录，并谈《日月前事》被盗 | 角色口述支持书的任务关联；对牢狱/时间的说明仍是其叙述，不变成全知编年 |
| `3470:s1/s2/s4` 白夜国馆藏 | 分篇《常世国龙蛇传》《日月前事》《白夜国地理水文考》 | 同页文献集合结构可靠；《日月前事》写“原初的那一位，**或许**是法涅斯”，不能建立无条件等同关系 |
| `3226:s0` 常世国龙蛇传（背包） | 描述明确是“取材自海祗岛民间故事的小说” | 与馆藏同名篇关联值得核读；该小说中“太阳之子/蛇神”不是自动验真的历史 |
| `3227:s1` 珊瑚宫民间信仰初勘 | “远吕羽氏之动机并无确实可靠之史料记载”；明确说后人推测，并提出另一传说版本 | **级别2** 支持作者自述的来源局限和版本分歧，不支持确定东征动机 |
| `2362:s0:u2` 远海夷地的金枝 | material-base 描述谈海民、蛇神折珊瑚、战斗和祟神 | 与书籍同主题是**级别3** 候选交叉材料；旁边武器突破使用表只是玩法 link |

**实际怎样找到**：本地 title/频道定位碑文，follow `501987` 的 `data-entry-id=2636`；以“渊下宫/白夜国/丢失的五本图书”找任务；从对白列出的书名查 `3470`；再搜索“海祇/蛇神/远吕羽氏/珊瑚枝”找 `3227/2362`。没有把所有含“蛇”的材料都连成同一实体。

**独立探索策略**：碑文/任务线与馆藏文献线分别记录；汇总时只确认“任务引用某书”和“某作者提出某版本”。世界历史/神明身份/动机仍按材料立场和限定词保留。结构帮助是 link、section 分篇和 locator；限制是书中时间与现实任务时间不等同，任务流程文字也混有攻略。

### 6.2 菲谢尔：人物经历→书籍设定集→武器叙述→地图“辞典”

**问题**：真实人物菲谢尔如何借用文学人格？能否把同名皇女/奥兹的宇宙故事当作该角色实际经历？

- `382:s15`角色故事1明确写她身为冒险家协会调查员，奥兹提供观察优势；其中年龄/父母引荐有“据说”，不能除去模态。
- `382:s16`角色故事2先称流通《菲谢尔辞典》，随即明说“所谓的《菲谢尔辞典》**并不是成文的书卷**”，指朋友理解她言语。适合训练“后文否定前文表面理解”的回读需求。
- `382:s19`角色故事5直接引用《菲谢尔皇女夜谭》，同时叙述幻想女孩、十四岁与图书馆经历。来源中的真名以黑块保留，**本次不补造真名**。
- `3594:s0:u2`道具介绍说该书是系列的**设定集/第零卷**；`3594:s1`还有“主编加笔”“不用管作者的看法”等书内编辑立场。
- `2127:s3`《幽夜华尔兹》故事谈皇女、黑鸦、常寂国土、世界之兽与三轮皓月。与设定集人物/主题关联可读，但本轮没有依据把这些文学宇宙事件写成提瓦特人物的真实履历。
- `505671:s1`地图文本《菲谢尔辞典·皇女新编版【西风骑士团】》实际出现这类“辞典”材料。它与角色故事2不应粗暴判成数据库矛盾：**不同文本的叙述时间、所指版本/物件未核实**，可能需活动/逸闻补读。
- `505626:s8`组织汇总列菲谢尔于蒙德分会，适合提供 NPC/人物索引；组织编辑汇总不代替角色故事原文。

**实际定位和探索线索**：title/角色故事段落命中书名→精确搜索 `菲谢尔皇女夜谭`；武器可由角色页中装备相关 link 到 `2127`，但“推荐武器”本身只是**级别1玩法关联**。真正文学关联依原文命名/主题再读；以“菲谢尔辞典”找地图文本，读其上下文而不是把同名书卷立即合并。

此例支持人物经历和文献线并行探索，并是防混淆控制：同名叙述角色≠同一客观实体；小说、书内编辑、角色幻想与世界事件必须分开。完整 local scene/后续限定词对 KG 尤其重要。

### 6.3 龙脊雪山：石碑、武器、圣遗物与另一组残旧记事

**问题**：雪葬古国、祭司之女和异邦勇士的线索能怎样互证？“同在雪山”的资料能否直接并入同一古代事件？

- `501754:s1`《石碑【龙脊雪山】》按位置1–8保存片段，出现“沙尔·芬…”、“天降之柱”、“公主/壁画”、“星银”和异邦人“伊蒙洛…”等。它支持原文碎片，不允许补齐缺失姓名或编排完整年表。
- `1362:s3`《雪葬的星银》叙述祭司之女、星银大剑、异乡勇士、壁画、远行与归来；与石碑的多处细节构成可调查的叙事联系。武器故事未在本段完整实名确认勇士，**不能只靠相似性推出唯一人物身份**。
- `1377:s3`《忍冬之果》明确写沙尔·芬德尼尔、祭司之女、白树、长钉和嫁接失败，后又谈黑龙/风龙与腐殖之血。能记录材料自身的先后叙述，不能仅靠此建立全球绝对年代。
- `1629:s1–s5`《冰风迷途的勇士》提供山国、勇士、绘画少女、银白枝条/未及时归来的不同物件叙述；五件有结构上的同套归属，但不是五份独立史料作者。
- `501755:s1`《残旧的记事【龙脊雪山】》却出现英戈伯特、鲁瑟、普莉希拉、补给/逃离等另一条第一人称线索；本轮没有可靠依据把它们和祭司之女/古国灾变合并。
- `1404:s2`《雪山迷踪》是寻找乔尔父亲的任务流程材料，可提供探索地点/人员检索 seed；不能因为“雪山+失踪”就认定它与古国勇士同事件。

**实际探索路径**：先由地点/碑文 title 找 `501754`；用石碑原词“星银/公主/壁画/白树”在武器和圣遗物字段中搜索 `1362/1377/1629`；另将残旧记事的姓名作为独立线索。每条线都保留“古国文献/物件故事/较近人物任务”的隔离标签。

这个案例反对单纯地区全集无限阅读：地区是可靠候选范围，但至少应拆出不同时间/人物/材料性质的独立研究线程。“地理同域”只能算级别3，不能合并成因果图。

### 6.4 雷鸟与少年：高度相似但需要继续核实的正向候选

- `1632:s1–s5`《如雷的盛怒》写雷之魔鸟、少年歌声、血祭、毁灭部落与未兑现约定；没有在选中原文中明确写“阿瑠”。
- `2942:s12`《雾海纪行系列任务》的流程写阿瑠、雷鸟羽毛/记忆、菅名山及前往清籁岛，同时多次出现“**或许**…共鸣”“或许是雷鸟的记忆”。
- `3121:s0`《奇特的羽毛》的获取说明明确连“清籁逐雷记”与“雾海纪行”，但是这首先是**获取/任务整理联系**，不是单凭此证明圣遗物少年身份。

**结论**：事件主题高度相关，值得以“雷鸟+少年+歌声+祭典”搜索更多对白/NPC/地图文本；本轮保留为级别3候选，不宣称已经证明两处少年就是同一人。这个案例显示重大事件入口有价值，但精确身份/因果需要额外阅读，不应由原子候选或剧情记忆填空。

### 6.5 结构控制与材料体裁控制

`723:s7`《暂行之策》是具备 graph 的世界任务控制；`507825:s1/s2`《古老的字迹【安瓦蒂尼尔湖】》将两处点位文本放在一段/节点内，node 并不等于单场景；`509226:s14`《旅行者·冰》角色故事控制；`509224:s3`《白湖冬羽》故事自己谈剧本参考、歌本与无法判定先后；`509469:s1`《极星舞剧集》为实际第一卷；`509294:s1`《血红之证》生之花中有物件 story/desc。它们都按实际原文定位保留，不能仅凭“武器/圣遗物/书籍”类型给统一事实等级。`509224` 故事直接讨论《黑雪鹄》，`509469` 第一卷正是同名剧本；这是可读的文献关联，不等于其中戏剧人物/隐喻就是客观历史。

### 6.6 正文代表 RU 定位（完整定位在附件）

| content_id : section : unit | 原文条目 | RU ID（首片段） | gzip 行号 |
|---|---|---|---:|
| 501987:1:1 | 古老的石碑【海祇岛】 / 交互文本 | `8d824f7eb2f1c88ba5f4ec83aa6ebe33662ee04c6f3566c5eacacba72224a9d4` | 125245 |
| 501987:2:1 | 古老的石碑【海祇岛】 / 相关任务 | `7a24464d3e8ef1010e420403d31bb6b9119bb2b5c9d035814d5315d582f5eeef` | 125248 |
| 3429:7:3 | 龙蛇藏归辑录 / 剧情对话 | `d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8` | 293939 |
| 3470:2:1 | 白夜国馆藏 / 日月前事 | `02fc38f6e6c54d6561181a33cbc068f8d147033f2f5bb2418e27f5fcfd6acb97` | 310440 |
| 3227:1:1 | 珊瑚宫民间信仰初勘 / 珊瑚宫民间信仰初勘 | `dfef2cb0683af0559485a1c4f1c81e253bd3a4205c4ac633f4e42db3751449c1` | 310506 |
| 2362:0:2 | 远海夷地的金枝 /  | `76bf19ae384832cdc401e75844647e35cbf663867896b6cda35e6d4d4444b4a4` | 182479 |
| 382:16:1 | 菲谢尔 / 角色故事2 | `7535b38180282f55a5ac2664c953e627bce232c512331e0c038f407783cd588d` | 17554 |
| 3594:0:2 | 菲谢尔皇女夜谭 /  | `48a8d4fdd3cd46e124d0c19f8b1a154d4954be4ad80f993cd01e6b906be89492` | 310361 |
| 3594:1:1 | 菲谢尔皇女夜谭 / 菲谢尔皇女夜谭·第零卷 | `0defc05aa48240cac33eccb06bacf44d1656dae3ab6716b676a4215b2017b306` | 310368 |
| 2127:3:1 | 幽夜华尔兹 / 相关故事 | `aa22c668c9f1ca0c58139688410b8665be6fd87ed1151e796f316c2462aa154c` | 105104 |
| 505671:1:1 | 菲谢尔辞典·皇女新编版【西风骑士团】 / 交互文本 | `5c298d41f5193bec5f4eaebee774ea4ed6ba6ca77a5413909a93d2ed3184e8b0` | 122720 |
| 501754:1:1 | 石碑【龙脊雪山】 / 交互文本 | `1a7ccc017f2af9f8d895bdbea8e1c535fdb6f251b20326d60d131119dcebe482` | 125973 |
| 1362:3:1 | 雪葬的星银 / 相关故事 | `cd6f272a047f1c18562518370525df49a05e0e471ee9c4968d6ce1b6e40d45c1` | 104796 |
| 1377:3:1 | 忍冬之果 / 相关故事 | `902e9e1b202fcc6d0af3aadc31139a505b6be4a551d9bd6a7a50b15c02357c40` | 104869 |
| 1629:5:2 | 冰风迷途的勇士 / 理之冠 | `83dd384aa0769c3674bb326c92f5dfa3b3a82c1ab99c610ea59f260dfdfc536b` | 108328 |
| 501755:1:1 | 残旧的记事【龙脊雪山】 / 交互文本 | `98b802dea69e53622f289439b6ff745aaaa9478060e2e5aa9929e18d2475e251` | 125966 |
| 1632:4:2 | 如雷的盛怒 / 空之杯 | `f71d6b5a6df5922a0658f056348ebba0141cdc3c743e2499c09e0a7887289a73` | 108062 |
| 2942:12:1 | 雾海纪行系列任务 / 任务过程 | `921e3c280785d551d58a385550502c7d0c0a9d0fbae0b372f943573562bca317` | 294446 |

本轮不依赖已有 atomic 输出作语义事实，未重放/修复 historical-200。67 个 rich-unit 是核读证据集，不是 67 个独立事件，也不是语义 precision/recall 测试集。

## 7. Navi 输入组织方案的比较

| 初始单元 | 可依赖的已有结构 | 仍需主动搜索 | 防扩张/污染边界 | 证据支持的判断 |
|---|---|---|---|---|
| 任务链/章幕 | c_43 类型/地区/版本标签、明确 related link、template group、section | 任务外文献/物件/NPC；真实前后置与原文归属 | 限章幕/子模块；剧情 vs 攻略/奖励隔离；跨任务链接先读 relation 语境 | 高可行入口，但页不是 task node，不能全库默认任务树 |
| 人物经历 | 角色故事/逸闻 sections、人物页/同名导航、实际角色名 | 非角色页提及、任务/NPC/书籍/物件的同名和别称 | 人物自身履历 vs 文学人格/传说/旁人口述分别保存 | 高可行；菲谢尔例证明全文相似不足以实体合并 |
| 地区历史 | region/filter、地图 point labels、地区导航和地图文本 | 物件/书籍/任务跨区域；非文字地图位置 | 同地点不同年代/人物线分开，不能读完所有同地区页 | 高发现价值；雪山例显示污染风险，需要问题限定 |
| 重大事件 | 暂无统一 accepted 事件 ID；文本/物件线索可起步 | 主体/身份/时间/因果几乎都要阅读 | 不让事件主题变成无限关键词集合；反证/限定词优先 | 不宜作第一层机械组织主轴，宜作后续汇总候选 |
| 文献集合 | 书页 sections、合集名、卷名、地图交互、物件 story/desc | 作者/出处/内部引用、任务现实语境 | 按文献叙述视角/版本/体裁隔离，引用不等于事实 | 高可行，尤其适合世界观；合集内部也要分篇 |
| 混合叙事单元 | 一个 content_id+可读 section/group 为 anchor，围绕明确研究问题 | 按来源线索有界发现跨类型材料 | 先独立探索、后来源级核实；每个候选保留理由/预算/停止 | 最适合小实验容器；不是长期唯一 taxonomy |

### 7.1 KG 与 Navi 不必共用同一阅读粒度

- **KG**：优先完整局部场景/对话 branch、物件 story 或有来源视角的文献段。跨 RU 恢复主体、说话者、对象、时间限定、否定、推测和条件；branch 未确认不写无条件边。Dialogue graph 可供恢复结构，但无法替代语义读取。像“或许是法涅斯”“动机无确实史料”“辞典并不是书卷”是必须保留的否定/模态，不是后处理装饰。
- **Navi**：入口可以是问题+材料 anchor，不要求提前分出全库事件。工作记忆保存已经读过的材料、下一条检索线索、冲突和预算；允许跨类型回看原文，不继承 atomic candidate 的语义正确性。探索会话成功也不能只统计 novel candidate，后续仍须满足现有 contract 的 RU resolution/admission/最终 Packet visibility。

### 7.2 什么应进工作记忆，什么必须回查原文

工作记忆可保存：问题范围、anchor ID/section、已读 sources、关键词/别称**候选**、检索理由、关系证据等级、未核实问题、叙述主体、体裁、否定/条件、冲突、剩余预算/终止原因。没有 source locator 的“剧情记忆”不能作为证据。

始终保持外部可回查：Raw path/hash、JSON pointer 和 embedded pointer、Parsed/Canonical record 身份、section/unit/group/node、RU fragment selector 与完整 ID；原文、列表/表格/分支及来源角色。摘要只引用这些，不覆盖它们；等文本不能跨来源静默合并。

独立线的隔离不是搭建长期记忆平台：本轮仅建议在有界实验中为每条问题线留独立来源/待核实列表，跨线汇总时逐条核实。可把“菲谢尔人物线”和“皇女小说线”、“古国雪山线”和“残旧记事人物线”作为实际防污染例。

## 8. UNKNOWN 与限制

1. **官方游戏叙事正文的精确总量/来源角色正确率仍 UNKNOWN**。当前 provenance 无统一官方文本验收；结构 proxy 不等于高价值正文。社区考据 202 页可作为带标记的候选线索，不能转为最终官方事实。
2. **现存 filter 标签数量已知，标签语义准确率/覆盖真值 UNKNOWN**。不能把 715 世界标签页当成 715 互斥完整 world-quest 单元，也不能从版本/排列推绝对剧情时间。
3. **全库统一 task DAG、完整前后置、书目作者/卷册规范、地图坐标可消费性 UNKNOWN**；原始 layout/链接存在、任务页前后置字段覆盖已知，但不能靠字段名称冻结语义。
4. **speaker 和 branch 的正确语义解释 UNKNOWN**。speaker slot 全空是当前解析现象，不能声称 Raw 从来没有说话者线索；对话图诊断不等于文本不可信，互斥/条件不能从边数自动推出。
5. **案例联系的完备性与身份统一 UNKNOWN**。本次有界原文阅读只支持列明的联系/立场，没有执行完整叙事考证；未查材料不能视为不存在。石碑/武器/书籍所声称的世界事实仍有独立验真层。
6. **主动探索收益、语义 precision/recall、成本/最佳预算、模型或 Harness 选择 UNKNOWN**。没有模型、付费调用、实时检索/回答实验；本报告不能替代 real-story acceptance。
7. 历史 census 独立 artifact 未在当前 repo 找到；本轮引用恢复文本并清楚标为历史，不宣称重验 156 样本。Raw 全量 hash 未重审：本轮全量检查的是 Canonical，Raw/Parsed hash 检查仅限 27 个案例。
8. 探索 seed 策略可能受标题/频道选择偏差影响；4 案例是说明性案例，不是统计代表总体或性能 acceptance。包含负向/不确定控制，不能因漂亮正例冻结唯一方案。

## 9. 建议的最小下一步（未执行、待另行授权）

优先比较 **任务 anchor、人物 anchor、地区/文献 anchor** 三种 seed strategy，使用上述四组中的 6–8 个真实起点；每个问题包括至少一个非任务来源，另含“同名但不能合并”和“同地区但不同叙事线”的控制。先冻结可核实问题和人工来源线索，再决定是否做 provider-free recorded exploration 或另行授权有限模型探索；**本报告不把旧 compact-navigation gate 宣称为已通过**。

可以先只做材料选择/人工探索账本，不建 Agent：同一预算下记录从起点发现了哪些额外、可回查 RU；跨类型材料是否真正补足问题所需限定；发现错误联系时是否停止或标为候选。必要时通过既有 Navi seam 检查 resolution/admission/Packet 可见性，但属于下一工作单元。

建议试验性上界（不是生产 operating point）：单线最多 2–3 次探索回合，每回合最多 2 个新的明确检索线索、读不超过 5 个新来源 section，累计正文约 20k–30k codepoints；超长章节拆为真实 template group/场景阅读，保留分支。来源没有新证据、预算到达、冲突不可消解或仅剩同名重复即显式停止。先比较不同策略的可发现性，而不是优化全量 atomic facts。

机械与语义评价分开：机械看 locator/hash、候选 accounting、循环/停滞、额外 RU 和 Packet visibility；语义由小样本人工核读判断是否补足问题、是否混淆叙述主体/体裁/否定。只在这种真实产品证据出现后，再决定是否授权模型工具调用、长期记忆、KG 关系精编或 Harness 工程。

## 10. 复算附件与交付状态

- `D:/GenshinChronicle/docs/research/phase-05/p05-navi-corpus-structure-statistics-20261010.json`：当前类型标签、组合、分类长度/RU、role/provenance、引用 carrier 与输入 hash。
- `D:/GenshinChronicle/docs/research/phase-05/p05-navi-representative-cases-20261010.json`：4 组案例 + 控制材料，完整 source/Raw/Parsed/Canonical/RU locator、片段长度和 hash、entry refs、核对结果。
- 一次性本地分析脚本/发现目录留在 Git-ignored `D:/GenshinChronicle/tmp/` 下，以便复算，不属于产品代码或新基础设施。统计 JSON 记录了方法，正文和附件给出可复算字段；重算必须输出到新研究目录，不能覆写 frozen 数据根或历史 run。

本次只新增这份报告和两个研究 JSON 到 Git 可见工作区。未改已有文件、代码、生产配置、Raw/Parsed/Canonical/RU、历史 run 或外部交接文档；未 commit/push/stage。完整调查结论等待用户审查，不开展后续实现。

### 10.1 复算方法与本机临时脚本

所有临时脚本仅用 Python 标准库、无 provider/project-runtime imports。`D:/GenshinChronicle/tmp/p05-navi-recompute-all-20261010.py` 接收一个**不存在的**输出目录，重算 Canonical/RU/类型/载体全部主统计，拒绝复用已有输出目录；`D:/GenshinChronicle/tmp/p05-navi-task-links-20261010.py` 同样写入新目录，重算前置/后续字段；二者脚本 SHA-256 记录在统计附件 `reproduction`。核读 locator 生成脚本为 `D:/GenshinChronicle/tmp/p05-navi-case-evidence-20261010.py`，已执行 hash 和 Raw scalar/Parsed equality 检查。临时脚本不是跨机器产品功能；若后续需可迁移的正式调查 runner，应另行授权，不改现有数据或 parser。

本机复算可使用以下 PowerShell 7 兼容命令（主统计/前后置写新临时目录，不重跑 provider）：

```powershell
Set-Location 'D:/GenshinChronicle'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
python 'D:/GenshinChronicle/tmp/p05-navi-recompute-all-20261010.py' "D:/GenshinChronicle/tmp/p05-navi-recheck-$stamp"
python 'D:/GenshinChronicle/tmp/p05-navi-task-links-20261010.py' "D:/GenshinChronicle/tmp/p05-navi-tasklink-recheck-$stamp"
```

仅核读定位时：在 case JSON 中查 entry=`content_id:sN`，依 `canonical_record_path` 读取 section N / unit ordinal M；再读取 `parsed_record_path` 按 `parsed_json_pointer`；读取 Raw `artifact_path` 按 `json_pointer` 获取组件，解码其 `data` 后应用 `embedded_json_pointer`。RU 用 gzip JSONL 的一基行号或完整 unit_id 定位，并对照其 fragment selector。整个链路无需模型或网络；不凭文本相等替换 source identity。
