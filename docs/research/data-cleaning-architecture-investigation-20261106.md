# GenshinChronicle 数据清洗架构只读调查报告

> 调查日期：2026-11-06  
> 范围：仅读取仓库文档、实现、现有 Raw/Parsed/Canonical/RU 样本及已生成的语料统计；本报告是技术研究，不是实施授权。  
> 当前状态：仓库当前阶段仍以 docs/current-phase.md 所述 Phase 04 Basic RAG V1 为准；本文不改变现行生产链路，不修改技术决策总账。

## 结论摘要

1. **Raw → Clean 作为唯一长期清洗产物是可行的，但“一步”应指一个持久化资料层，不应指单体、不可审计的解析函数。** 内部仍应采用可测试、可版本化的源结构解析、富文本处理、标签关联、文本视图生成等小步骤；最终只发布 Clean 记录与审计清单。
2. **Clean 不应是单一纯文本字段。** OBC 资料同时包含文章、结构化属性、表格、嵌套 HTML、内部条目引用、媒体资源和对话图。推荐以有序结构块保存完整内容，并提供确定性文本视图；文本视图是便利投影，结构块和 Raw 定位才是内容依据。
3. **类别标签必须从 Raw 的多个发现层合并。** 详情页本身不一定含分类值：样本《影域的遗留》的详情页 filter_values 为空，但 Raw 频道列表的扩展字段包含“任务类型/世界任务”“任务区域/至冬”“版本号/7.0”。因此 Clean 输入不能只读 detail 响应，还要读取对应 Raw run 的频道树、列表与成员关系，并将每个标签绑定到其来源。
4. **现有解析能力很有价值。** 应保留 OBC 页面解包、组件 JSON 解码、HTML 文本/树/链接/媒体解析、对话图解析、顺序与 RawRef、未知结构回退、哈希与清单审计等能力。可以简化的是 Parsed/Canonical 的持久化包装和同一内容的多份展开表示，而不是这些解析能力本身。
5. **不能把 OBC 平台来源等同于游戏内官方文本，也不能从组件名推断作者或真实性。** 当前分类规则只把 interactive_dialogue 标成临时 content_role=dialogue；provenance 仍为 unknown。频道路径、源筛选项、组件类型、出处/作者性质应分属不同标签命名空间。
6. **HTML、链接和媒体 URL 不是一概可删除的“噪声”。** CSS 的颜色、对齐等通常可从 Clean 正文视图去除；标题层级、列表、表格行列、可见文字、关联条目 ID、图片替代文字及音频条目的标题/文本/URL 关系要保留。现有 Raw 没有下载图片、音频、视频二进制；Clean 可以保留可回溯资源引用，但不能声称已提取资源本身的知识。
7. 现有 1200 字符 RU 分片属于下游实现。Clean 应保存未截断的原文内容、字段与关系，不应沿用该分片长度、删除策略或索引表示。

## 1. 调查范围与证据边界

本次先读了 current-phase、Phase 01–03 契约、架构总览、数据目录说明、Phase 01 频道/API 研究、Phase 03 代表样本验收和 Phase 04 全语料结构统计，并检查了 parser、canonical projector、RU builder 的实现。

真实语料观察来自本地已完成快照：

- Raw：data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/
- Parsed：data/parsed/mihoyo_obc/zh-cn/phase02-batch4-p01eb-full-20260824/
- Canonical：data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/
- RU：data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/
- 全语料统计：data/retrieval/p04-w1-phase03-batch5b-corpus-profile.json

样本实际读取了 Raw detail、Raw channel map/list、Parsed 记录和 Canonical 记录，集中检查 content_id 501157（阿蕾奇诺）与 509653（影域的遗留），包括角色故事、关联词条、配音表格和互动对话。全库层面的结构计数取自已有的 P04-W1 只读 profile；本轮没有再遍历或重建全库，也没有抓取网络数据、调用模型、运行测试或改写现存文件。

Phase 01 已完成的 OBC 快照报告 96/96 频道列表响应、32,916 条列表记录、16,437 个唯一 content_id、16,437 个详情成功、0 个最终未解决失败。这些是该快照的覆盖结果，不代表未来 API 版本或上游语料的永久常数。

## 2. 现有 Raw → Parsed → Canonical → RU 体系

| 层 | 当前责任与实际形态 | 对 Clean 研究的意义 |
|---|---|---|
| Raw | 保存 map、频道 listing、详情响应、元数据、清单、校验值、频道成员关系及失败记录；详情原样含 API envelope 与 data.page | 仍是唯一原始证据。分类和详情必须通过 Raw 的 listing/map 关联；Clean 不得覆盖 Raw |
| Parsed | OBC 专用解包；按 page → module → component → unit 保存顺序、来源字段、组件 JSON 解码、HTML 解析、对话图、未知值、RawRef、版本和诊断 | 大部分内容提取能力可以复用；持久化结构适合作为实现参考，不必自动成为新 Clean 的外部合同 |
| Canonical | Record → Section → Unit，并以 ComponentContext、Observation、LineageLink 保留页/模块/组件上下文和 Parsed → Raw 来源 | 证明结构可以被整理为可追溯的跨来源记录；但层级较多，仍复制大量 Parsed 表示 |
| RU | 从 Canonical 生成 535,802 个检索单元；当前构建显式跳过 32,955 项，清单报告 0 个失败。富文本与结构字段分别产生文本投影 | RU 是检索派生物，不是清洗基准。分片、索引过滤和 1200 字符预算不应倒灌进 Clean |

Phase 03 profile 对 16,437 条 Canonical 记录计数为：84,072 个 section/component context、362,420 个 unit，其中 273,346 个 rich_text、84,072 个 structured_observation、5,002 个 dialogue_graph。对话图含 5,087 组、60,682 个节点和 62,727 条边。profile 记录了大量多父节点、孤立节点等图诊断；这些是源图不规则的证据，不能以“修成树”或线性台词串的方式消除。

另有 8,096 种 section 名称（其中空名称也很常见），说明章节/组件标题开放且多变，不适合把全站强制套进固定的“任务/角色/材料”单一模板。profile 的高频组件包括 collapse_panel、map_desc、material_base_info、rich_base_info、interactive_dialogue、good_desc、npc_base_info、multi_table 等，覆盖叙事正文、属性与表格、地图说明及交互文本等不同载体。

富文本 profile 的 normalized_text 长度中位数为 8 个字符、P90 为 116、P99 为 787，最大值为 22,015；有 149,510 次重复文本出现记录和 18,939 个重复文本组。短片段往往需要文档名、章节、字段或相邻关系才可理解；文本相同也不意味着出处相同，不应在 Clean 去重。

## 3. 真实样本与具体发现

### 3.1 阿蕾奇诺（501157）：一页同时包含属性、故事、关系和媒体

Raw detail 为 361,139 字节；当前样本 Parsed 为 3,555,206 字节，Canonical 为 2,078,892 字节。该页有 29 个模块，标题包括基础信息、角色突破、推荐装备、天赋、命之座、特殊料理、角色故事、宣发时间轴、关联词条、角色关系网、生日邮件等。单页不是一种统一内容类型。

Raw 频道树给出“图鉴 → 角色”；Raw listing 还提供“地区/至冬”“星级/五星”“神之眼所属/愚人众”“元素/火”“武器/长柄武器”等筛选值。详情页中的基础信息是带字段名的 JSON 结构；Clean 应保存键和值的关系与原始顺序，而不是只输出孤立数值。

同一页的角色故事是由 p、span 等标签分段的连续叙事。配音展示是表格型结构，每行同时带语言页签、语音条目名、台词正文、音频文件名与 audio_url。若仅保留台词，会丢掉台词对应的语音资源；若把音频 URL 拼进正文，又会制造检索噪声。应把正文和媒体引用分别保存，并以同一行/来源定位关联。

关联词条的 HTML 同时有可见名称、data-entry-id、data-entry-name、data-entry-link、图片 URL 和 href。可见名称应保留为文本；content_id/href 应保留为关系引用；图片 URL 应作为关联资源信息存放。将整段 HTML 标签删除后只留名称，会丢失可解析的条目目标。

### 3.2 影域的遗留（509653）：任务标签在 listing，不在 detail

Raw detail 为 18,470 字节，含 8 个模块，包括任务概述、任务过程、任务奖励、地图说明、攻略方法和剧情对话。detail 的 filter_values 是空对象；同一 Raw run 的 43 号频道 listing 却为该内容提供“任务类型/世界任务”“任务区域/至冬”“版本号/7.0”。频道树中的父路径为“图鉴 → 任务”。

这说明：

- 可可靠保留的内容分类包括频道路径和 listing 中实际出现的筛选值；
- 标签需携带 Raw listing 文件、频道 ID、列表项及字段路径，而不能假装来自 detail；
- 某个 detail 的空筛选值不等于该内容没有类别；如果 Raw listing 没有对应值，也不能自行猜测。

互动对话有 1 个 group、5 个节点和 5 条边，节点中包括玩家选项与角色对话；其中一个节点有多条入边，解析器报告 DIALOGUE_MULTIPLE_PARENT。speaker 字段为 null，HTML 中出现的角色名不能自动当成 speaker 字段写入。若 Clean 把它变成单一线性文本，会丢失选项、根节点、分支和多父关系；建议把图作为结构块保存，并提供每节点的原文文本视图。

该样本 Raw→Parsed→Canonical 大小分别为 18,470、191,542、114,912 字节。此处的扩张显示富文本树、text segments、原 markup、generic decoded component 和 dialogue graph 可能重复表示相同底层内容。它们为审计和复用提供了价值，但可以在新 Clean 中选择一个主结构表示，再由其派生便于阅读的文本视图。

### 3.3 来源标签和“玩家考据”的边界

实际 listing 可提供稳定、可回指的 source-filter，例如“任务类型/世界任务”。map 可提供层级频道名；详情页可提供页名、描述、别名、page_type、版本、状态、模块名和组件 ID。它们的证据强度和语义不同，建议分别标为频道路径、筛选标签、页面字段、结构标签。

在 profile 中也看到“考据与原摄”这样的来源可见 section 名称，但 section 名不能单独证明作者身份或“玩家考据”性质。样本 listing 中 article_user_name 为空；当前分类规则没有批准来源/作者性质标签，provenance 为 unknown。除非 Raw 明确提供并且后续定义接受该字段，否则应保留原始标签/作者字段但不升级成“玩家原创”“玩家考据”或“官方游戏文本”。

## 4. 现有能力：保留、简化与替换

### 应保留并复用

- RawRef 的 source、locale、run_id、artifact_path/hash、content_id、JSON Pointer、嵌套指针和 source-value hash；另保留 listing/map 证据引用。当前 Canonical profile 记录 547,001 个 RawRef 连接，其中 direct_raw 与 inherited_parent_raw 分开统计，说明“有 RawRef”不等于每个值都有精确字段级来源，Clean 也应表达定位范围。
- OBC adapter 对 data.page、modules、组件顺序、模板布局和组件 JSON 字符串的校验/解码；不识别的组件 ID 不代表其数据没有价值。
- rich_text.py 中的树、文本片段、规范化文本、链接、图片和 data-entry 引用提取；对话图 parser 保留 group/root/node/edge 顺序与异常。它们是有用的确定性代码，不必保留现有中间文件格式。
- 未知值保留、异常码、schema/parser/rule 版本、源内容指纹和完成/失败计数。Raw/Parsed/Canonical 的原子写入、不可冲突覆盖和 manifest 依赖检查也可作为工程模式复用。
- 频道树与 Raw listing 的关联：Raw 采集已经保留 map/listing 响应，需将 membership/filter 视为 Clean 的来源元数据，而不是重新抓取或只看 Parsed detail。

### 可简化的持久化模型

现有 Parsed/Canonical 为审计分别保存了 source/generic decoded 单元、按每个 markup 值展开的 rich_text 单元、dialogue_graph 单元，并在 Canonical 再包装成 section/context/unit。该设计解决分层阶段的问题，但会重复存储。新 Clean 可用：

- 一条 source-record observation；
- 一个有序 sections 数组；
- 每 section 下有序 blocks；
- 仅在确有来源结构时保留 fields、table rows/cells、dialogue graph、references、media 和 unknown payload；
- 每个标签/文本块/结构值直接携带 Raw 证据地址。

不需要为了“每种 component 都有一个 Canonical Unit”而人为增加空白文本；也不必在 Clean 再保存一份完整原 markup、完整解析树、独立 text_segments 与一份重复的 decoded 原组件。Raw 本身是原始 HTML/JSON 权威来源，Clean 保留可重建引用、哈希、已提取内容和必要结构即可。未能解析的未知内容则应保留 opaque 原值或其无损编码与 RawRef，不能只记“忽略”。

### 不应从 RU 倒灌的规则

现有 RU builder 对 rich_text 使用规范化文本，对结构化对象遍历标量值并以 JSON Pointer 作标签，再按 1200 字符预算合并/切分；对话节点按 option/dialogue 生成文本并附关联边。它适合作为下游投影经验，但这些拆分会改变 Clean 的完整呈现、可能包含对分片特定的顺序/预算，不应变成 Clean 的内容结构或长度约束。Clean 保持完整；分片分支自行决定字段过滤、组合、切块及重复证据处理。

## 5. 推荐的 Clean 数据结构

推荐把 Clean 定义为**基于 Raw observation 的结构化文本记录**：记录的主体是“可读文本 + 保持必要原结构的块”，而不是 Raw 的副本，也不是只有一段 Markdown/纯字符串。

~~~yaml
clean_record:
  schema_version: ...
  observation:
    source: mihoyo_obc
    locale: zh-cn
    raw_run_id: ...
    content_id: ...
    raw_detail_ref: {artifact_path, artifact_sha256, json_pointer}
  document:
    name: ...
    source_fields: ...
    channel_paths: [...]
    memberships: [...]
    tags:
      - namespace: source_filter
        label_path: [任务类型, 世界任务]
        observed_value: 世界任务
        evidence_ref: {raw_listing_ref, item_pointer, field_pointer}
  sections:
    - ordinal: 0
      heading: ...
      source_module_id: ...
      layout_observations: [...]
      blocks:
        - ordinal: 0
          kind: rich_text | field_group | table | list | dialogue_graph |
                entry_reference | media_reference | opaque
          text: ...
          fields_or_rows_or_graph: ...
          source_ref: {artifact_path, artifact_sha256, json_pointer,
                       embedded_json_pointer, source_value_sha256}
          text_spans: [...]
  text_projection:
    renderer_version: ...
    text: ...
  status: complete | complete_with_diagnostics | preserved_unknown | blocked_integrity
  diagnostics: [...]
~~~

这只是便于讨论的结构草案，不是已批准 schema。重要设计点：

- 标签使用命名空间区分 source_channel、source_filter、page_field、source_component、content_role、provenance。标签值附 observed source value、规则版本（如有）与证据地址。未验证的语义分类保持 unknown；不要把提取到的文本等同于分类。
- section/block 顺序是来源位置。保留模块顺序、模板布局位置和 tab/group 名称，但不以模板左右列自行重排叙事顺序；若 Clean 同时提供阅读视图，应标明这是哪个版本的确定性展示规则。
- rich text block 可以保存解码后的正文及段落/标题/列表结构。表格保存 row/column 位置、可见字段名和 cell 内容；只有源数据明确给出表头时才标记表头，不能猜表格语义。数字保持原始值、字符串格式和单位，不做舍入、翻译或相邻数值合并。
- dialogue_graph 保存 root、group、node ID、option、dialogue、真实 speaker 字段（没有就 null）、edge 和源顺序；文本投影可按节点显示，但不能伪造一条单线路径。
- link/reference 保存可见显示名、target content_id、href 与上下文；不强行把关系命名为“人物关系/任务前置”等语义，除非 Raw 的字段或标注本身给出了关系类型。
- image/audio/video 作为 media_reference 保存 URL、资源类型、所在模块/表格行、可用的 title/alt/caption/name；正文仍保留对应描述/台词。Clean 不下载或分析二进制。
- text_projection 可以物化以便下游读取，但必须由 blocks 确定性生成、带 renderer 版本并可重建；不能成为唯一事实来源。标题/章节上下文可以通过独立 context 字段供分片组合，避免把系统补入的前缀伪装成原文。

## 6. HTML、字段、噪声和 Raw 定位

| 来源内容 | 建议在 Clean 中的处理 |
|---|---|
| p/h1–h6/blockquote/li/br 等结构 | 保留段落、标题级别、引用和列表项边界；文本视图只作明确标注的确定性渲染 |
| table/tr/th/td 与 JSON table/list | 保留行列顺序及单元格内容；不能只靠换行把表格打平。列名不明确时保留坐标，不推断表头 |
| b/strong/em 等可能表达强调的标签 | 通常保留强调类型或文本节点标记；CSS 颜色/对齐/字体等纯展示值不拼进正文 |
| a、data-entry-id/name/link、href | 保留锚文本和目标引用。内部条目 ID 可供以后按需回读；不等于已判定实体关系 |
| img/src、data-image-url、alt/title/caption | URL 和语义性替代文字分开保存；不要把长 URL 塞进正文，也不要因“非文本”就删除关联 |
| script/style、class/style 与 API/template 控制字段 | 可从可读正文视图排除；保留 Raw 地址，未知/嵌入数据型结构需有诊断或 opaque 回退。page version/status、layout、filter_values 等按元数据保留，不自动当正文 |
| 空值、未知模块/组件、非标准节点 | 不制造内容、不静默跳过；将原始值、结构路径、原因与诊断计入 Clean 状态和清单 |

现有 HTML parser 保留 raw_markup、树和属性，忽略 script/style 的 normalized_text 内容，按 block tags 加换行并折叠空白；link/media/entry_reference 另有提取。但该 text normalization 本身不是无损格式：例如 td/th/tr 的换行不会表达完整表格单元格关系，列表标记和 h1 与 h2 级别不会仅靠 normalized_text 保留。Clean 应以保留的结构块为主。

当前 looks_like_markup 仅以同时出现“<”与“>”判断字符串是否为 markup，适合作为轻量启发而非安全的类型证明。Clean 需要对解析异常、未知标签与含尖括号的普通文本保留原字符串/路径，避免把公式或原文误解析后静默损失。

建议复用 RawRef 的字段模型，并在块级、field/cell/node 级尽可能保存定位。对于 Raw 中 JSON 字符串内嵌 JSON/HTML 的数据，需同时保存外层 component data 的 Raw JSON Pointer、解码值中的嵌套 pointer、原值 hash，以及 Clean 文本范围到 HTML 文本节点/解码字段的映射。HTML 实体解码、空白折叠会使 Clean 字符偏移与 Raw 字节偏移不一一对应；不要仅存“原始字节起止”。最小可用定位应包括 Raw run、artifact path/hash、content_id、外层 JSON Pointer、嵌套/HTML path、源字段 hash 和来源顺序；若提供片段级定位，再保存 Clean codepoint range 与对应 source-node span。

Raw 内没有实际图片/视频/音频文件，Phase 01 当前契约只要求保留 JSON 中的资源地址。若未来要求回答图片文字或视频画面信息，那是 Raw 资产覆盖/解析范围的独立决定；本轮 Clean 无法从未抓取的二进制中恢复这些信息。

## 7. Raw → Clean 一步完成是否合理

### 好处

- 一个长期派生资料层，避免同时维护 Parsed 和 Canonical 两套持久化 schema、版本迁移和跨层 join。
- Clean 可以直接携带来源 Raw 的校验和最短充分定位，减少中间层引用链长度。
- 对复杂页面，避免原 markup、DOM-like tree、segments、generic decoded copy、Canonical 再序列化等多份副本；501157 样本说明这一空间问题值得测量，但不能据两个样本推断总体节省比例。
- 下游分片只读 Clean 的通用文档/结构块接口，少受 OBC 组件展示细节影响。

### 风险与缓解

- 解析、清洗、分类和展示渲染若混成单体，问题将难以定位，版本变化影响范围大。**缓解：**内部保留小型 source adapters、纯解析函数和独立 renderer；以 manifest 记录各规则版本及诊断，但不额外物化 Parsed/Canonical 数据层。
- 直接输出的内容若没有对账，错误会同时污染唯一派生层。**缓解：**每个 Raw detail 必须在 Clean manifest 中有明确 disposition；检查文本节点/结构化标量/媒体/引用的覆盖计数、未知节点清单、hash、源定位以及失败原因。
- 只看 detail 会漏掉 listing filter 与频道路径。**缓解：**Clean run 必须绑定同一 Raw run 的 map、listing、inventory 和 manifest hash；对于每个 membership/filter 保存其具体 Raw listing locator。
- 一次性转为“Clean 文本”可能不可逆地压平表格/对话关系。**缓解：**正文视图由结构块派生；结构是 Clean 的持久化内容，不把 Markdown 渲染结果当唯一记录。
- 源格式演进可能导致已识别组件被误当噪声。**缓解：**未知 component/data type 进入 opaque 保留和显式告警；关键 envelope/hash 冲突阻断对应记录，不能默默部分通过。
- 需要能从 Raw 重放并能对比旧体系。**缓解：**新 Clean 使用自己的 run_id、manifest、逐记录 hash、依赖指纹与原子写入；旧 Parsed/Canonical/RU 保持只读，先做有限样本与差异评审后再单独决定迁移。不要跨 Raw 快照自动合并相同 content_id。

### 备选架构

- **只保留 Raw + Clean 的长期数据层、内部模块化处理：推荐。**符合两层目标，同时保留解析可维护性。
- **Raw → 临时结构对象 → Clean：可接受。**结构对象只在内存/临时诊断文件中存在，帮助测试和故障定位，不把它升级为新的长期数据层。
- **继续持久化 Parsed + Canonical：技术上最保守。**现有审计与复用路径已实现，但会保留用户希望简化的多层物化与重复内容。若未来证明多个消费方依赖各自的结构合同，再考虑保留独立解析层，而不是现在预设。
- **Raw → 纯 Markdown/String：不推荐。**难以无损表达 table、dialogue branch、media/reference、source tags 与细粒度 Raw 定位，且可能把添加的标题/字段前缀混成原文。

## 8. 给后续分片环节的 Clean 接口建议

Clean 只提供完整输入，不替分片决定保留哪些知识、过滤哪些字段或每片多长。建议至少提供：

1. **来源身份：** source、locale、Raw run/manifest/hash、content_id、Clean observation ID；不跨 snapshot 断言语义同一。
2. **可审计标签：** channel path、listing filters、页面字段、component/结构类型，以及每项的 namespace、范围、原始值、证据 locator。标签应能随 chunk 继承但保留其文档级或 block 级作用域。
3. **结构化文本单元：** 有序 section/block；每块具有 kind、heading path、source component/module、原始文字/字段名值、必要的 table 行列或 dialogue 节点/边。Clean 不提供 chunk ID。
4. **上下文而非拼接：** document title、section path、相邻 block/node/reference 作为独立上下文字段。分片器可决定是否把上下文前缀加入检索文本，但必须将前缀和原文 span 区分。
5. **文本来源映射：** 每个可分文本值提供 RawRef 与嵌套 selector；若分片从文本范围切开，允许以 Clean block ID + nested path + codepoint range 指回源文本范围。跨块片段携带多个 source spans。
6. **结构账目：** 每个 media/reference/unknown structure 有可迭代清单；若下游选择跳过，跳过理由由分片/索引 manifest 记录，Clean 不回写删减。
7. **完整未截断值：** 对长文、表格、数字字段、对话节点不预先按 RU 字数截断；投影和上下文策略由分片定义。

建议的 chunk 回引最小形式是 Clean observation + block/nested path + text range，并附来源 RawRef 列表；Clean 的块/字段顺序号只作为本次来源位置，不作为跨 snapshot 永久 ID。这样后续改变分片算法时可以重建 chunk，而不改变 Raw 或 Clean 证据。

## 9. 后续需决定的问题

1. **Clean 自包含范围：**已提取文本与结构之外，是否复制完整 raw_markup/未知 payload？推荐常见已解析 markup 不重复存储，靠 RawRef/hash 回溯；未知/不能安全解析的数据仍以 opaque 值保留。
2. **输出格式：**推荐每个 Clean observation 一个确定性 JSON 文件和不可冲突的 run manifest；文本视图可作为同记录字段或可重建导出。是否另做 JSONL/Markdown 只属接口便利性。
3. **源标签最低集合：**是否将频道路径、全部 listing filter、page 字段、module/tab/group 作为第一版来源标签；建议先保留 Raw 明确给出的全部标签，不预设检索白名单。
4. **provenance 与作者：**何种可核验 Raw 字段足以标“玩家考据”“官方游戏文本”或其他出处？没有规则时应保留 unknown，不从 OBC 域名/标题/正文猜测。
5. **布局阅读顺序：**modules[] 顺序与 template_layout 的 tab/group/position 如何同时暴露；需要决定纯文本视图按哪个顺序展示，不能无标记重排。
6. **资源资产：**Raw 目前只有媒体 URL。是否另行授权采集官方资源并处理图片/视频/音频内容，需在 Raw/资产范围单独决定。
7. **时间与版本：**同一 content_id 在不同 Raw run 是新 observation；是否总是保留多个 Clean snapshot、如何指定当前视图，需另行制定，不应由清洗时静默覆盖决定。

## 10. 总结判断

实际数据支持“Raw → Clean → 分片”作为后续讨论路线，但前提是 Clean 被定义为**有证据地址的结构化全文**。对于已经验证的 OBC 页面，源码有足够信息恢复文本、标题、表格字段、频道分类、条目关系和对话分支；但这些信息分散在 Raw detail、频道树和频道 listing 中。对未知结构、媒体二进制、作者性质及跨快照身份，当前证据不足，Clean 应显式保留未知而不擅自分类。

建议继续沿用现有解析实现中可验证的“保留结构、未知不丢、哈希与 lineage 可审计”能力，将 Parsed/Canonical 的持久化层级压缩为一个新 Clean artifact contract；不要将其压成扁平字符串，也不要把 RU 分片/索引规则并入清洗。实现与生产切换仍须先更新项目授权和方案合同；本报告没有作出该切换。

## 主要依据

- [current-phase.md](../current-phase.md)：当前优先级与生产状态。
- [Phase 01 Raw collection](../phases/phase-01-raw-collection.md) 与 [OBC API discovery](phase-01/mihoyo-obc-api-discovery.md)：Raw 发现、覆盖与不变性。
- [Phase 02 Parsed schema](../phases/phase-02-parsed-schema.md)：源解析、未知保留与标签边界。
- [Phase 03 Canonical schema](../phases/phase-03-canonical-schema.md) 与 [代表样本验收](phase-03/batch-5a-representative-acceptance.md)：Canon 结构与代表语料证据。
- [P04-W1 Canonical corpus profile](phase-04/p04-w1-corpus-profile.md) 及其本地 profile JSON：全语料结构统计。
- 代码：src/genshin_corpus/parser/obc/adapter.py、parser/rich_text.py、parser/dialogue.py、parser/classification.py、canonical/projector.py、retrieval/retrieval_units.py。
- 会话提供的 GenshinChronicle_RAG_全链路技术决策总账（2026-11-06 版本）：清洗与分片职责边界；本报告未修改该文件。

