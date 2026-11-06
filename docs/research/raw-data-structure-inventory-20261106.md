# GenshinChronicle Raw 数据结构调查报告（事实盘点）

- 调查日期：2026-11-06
- 调查对象：Raw 快照 p01eb_full_20260824、其 Parsed/Canonical 清单与代表记录，以及当前 Parser/Canonical 实现。
- 口径：下文分别标注全量统计、代码实现事实、定向样本观察和未确认事项。报告不包含 Clean 结构或架构方案判断。
- 完整字段路径、字段类型与计数、组件目录、筛选值、栏目树、HTML 标签计数及统计口径见 [机器可读统计附表](raw-data-structure-inventory-20261106.stats.json)。附表中的 [] 表示数组元素；{content_id}、{channel_id}、{node_id}、{opaque_id} 是为避免展开动态键而使用的路径占位符。

## 1. 数据来源与调查覆盖

### 全量扫描范围

Raw 根目录：data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/。

| 对象 | 本次读取范围 | 结果 |
|---|---|---:|
| 详情响应 | responses/details/*.json，排除 .meta.json | 16,437 |
| 栏目列表响应 | responses/listings/*.json，排除 .meta.json | 96 |
| 栏目树响应 | responses/map/*.json，排除 .meta.json | 1 |
| 响应元数据侧车 | 上述三类 .meta.json | 16,534 |
| inventory | metadata/inventory.json | 16,437 条记录 |
| manifest | metadata/manifest.json | 1 份 |
| 失败记录 | failures.jsonl | 242 行 |
| 组件数据 | 全部 84,072 个 data 字符串尝试 JSON 解码；对成功解码值递归统计 | 84,071 成功、1 个空字符串无法解码 |

全量响应文件的顶层均为对象；详情、列表、栏目树响应的根字段均观测为 retcode（整数）、message（字符串）、data（对象）。本快照所有详情响应的 retcode=0、message="OK"；列表响应和栏目树响应亦分别为 96/96 与 1/1 个 retcode=0、message="OK"。

### 文件关系

    data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/
    ├─ metadata/
    │  ├─ manifest.json       # run、栏目抓取状态、路径及响应摘要
    │  └─ inventory.json      # [{content_id, channels}, ...]
    ├─ responses/
    │  ├─ details/            # 详情响应 .json + 对应 .meta.json
    │  ├─ listings/           # 栏目列表响应 .json + 对应 .meta.json
    │  └─ map/                # 栏目树响应 .json + 对应 .meta.json
    └─ failures.jsonl         # 抓取过程失败事件记录

响应文件名是 64 位十六进制字符串；详情文件名本身不直接呈现 content_id。同名 .meta.json 记录 raw_path、metadata_path、sha256、kind、key、context、status、ok 和 url。详情侧车的 context.content_id、key 是字符串；列表侧车的 context.channel_id、key 是字符串；栏目树侧车的 context 为 null、key 为 home-map。本快照所有 16,534 个侧车均为 ok=true、HTTP status=200。

manifest 的 status 为 complete，recognized_channel_count=96，listing_responses_saved=96，listing_records_discovered=32,916，inventory_count=16,437，successful_detail_fetches=16,437，failed_detail_fetches=0。同一文件树中 failures.jsonl 有 242 行，均为 kind=details、status=null，错误文本均为 urlopen error [WinError 10061] ... 目标计算机积极拒绝连接；这是失败事件记录，与 manifest 汇总的最终失败数为 0 同时存在。

## 2. Raw 的 JSON 层级与实体关系

### 详情响应

    response object
    ├─ retcode: integer
    ├─ message: string
    └─ data: object
       └─ page: object
          ├─ page metadata fields
          ├─ menus[]
          ├─ template_layout
          └─ modules[]
             ├─ module metadata fields
             └─ components[]
                ├─ component_id: string
                ├─ data: string（本快照中为 JSON 文本）
                ├─ layout: string
                └─ style: string

详情 data.page 在 16,437 条中都存在。页面 ID page.id 为字符串；详情侧车 context.content_id 与它相同。modules 为数组，长度从 1 到 160；合计 84,072 个模块。每个模块都有 components 数组；本快照 84,072 个模块的 components 数组长度都恰为 1，因此组件总数也为 84,072。模块数组索引保留页面内顺序。

页面、模块、组件的顶层字段出现情况与类型如下。字段目录的嵌套字段、类型计数及分母见附表 entity_top_level_fields 与 field_catalog。

| 实体 | 数量 | 字段数 | 本快照字段覆盖与类型 |
|---|---:|---:|---|
| data.page | 16,437 | 23 | 下列 23 字段均在 16,437/16,437 个页面出现；字段类型全部一致 |
| modules[] | 84,072 | 16 | 下列 16 字段均在 84,072/84,072 个模块出现；字段类型全部一致 |
| components[] | 84,072 | 4 | component_id/data/layout/style 均为字符串，四字段均在 84,072/84,072 个组件出现 |
| 栏目树节点 | 96 | 10 | id/parent_id/depth/entry_limit 为整数；hidden 为布尔；其余为字符串或数组，字段均出现 |
| 列表条目 | 32,916 次出现 | 11 | 11 字段均出现；content_id 为整数，其余字段为字符串 |

data.page 字段：id, name, desc, icon_url, header_img_url, modules, filter_values, menu_id, menu_name, version, langs, template_layout, edit_lock_status, correct_lock_status, menus, template_id, ext, alias_name, lang, beta, page_type, menu_style, status。

modules[] 字段：id, name, desc, components, repeated, is_submodule, origin_module_id, can_delete, is_abstract, is_customize_name, is_hidden, is_poped, is_show_switch, switch, without_border, rich_text_editing。其中 ID、名称、描述和 origin_module_id 为字符串；其他列出的布尔字段为布尔类型。

components[] 字段：component_id, data, layout, style。本快照所有组件的 data 均是字符串；84,071 个字符串可作为 JSON 解码，解码根均为对象。组件外层 layout、style 与解码后 data 内可能出现的 layout_ 是不同路径上的字段。

列表条目字段：content_id, title, ext, icon, bbs_url, article_user_name, article_time, avatar_url, summary, alias_name, corner_mark。列表 ext 是 JSON 字符串；其内部的 filter.text 又是 JSON 数组字符串。

### 列表响应、栏目树与 inventory

- 栏目列表响应根路径为 data.list[]。本快照观测到列表条目位于栏目节点的 list[] 下；部分响应还嵌套 children[]，子栏目节点再含 list[]。正文条目字段与上表一致。
- 栏目树响应位于 data.list[]，共有 96 个节点。节点字段包括 id, name, parent_id, depth, children[], list[], ch_ext, entry_limit, hidden, layout。children[] 表示栏目树子节点；不少叶节点的 list[] 是最多约 20 条的预览列表。完整栏目清单另存于 96 份栏目列表响应及 inventory 中。
- inventory.json 是数组，每项均为 { "channels": [字符串, ...], "content_id": 字符串 }。16,437 个 content_id 对应 32,888 条 channel membership；涉及 58 个不同 channel_id。栏目树有 96 个节点，节点数与 inventory 中实际出现的不同 channel_id 数不同。
- manifest 记录 32,916 次列表条目出现、16,437 个唯一详情 content_id；listing 响应中同一 content_id 出现次数分布为：出现 1 次的 20 个、2 次的 16,386 个、4 次的 31 个。inventory membership 边数与 listing 条目出现数相差 28；这些统计只表示各自来源中的计数，不将条目重复直接等同为不同的栏目归属。

## 3. 字段、模块与组件目录

完整的 415 条规范化 JSON 字段路径目录在附表 field_catalog。每条记录含 scope、路径、观测到的 JSON 类型、字段出现次数、包含该字段的文档数；对于由动态键合并的路径，documents_with_field 以 null 标注无法从合并前计数精确恢复的值。Raw 响应、详情页、组件 data 解码、侧车、manifest、inventory 和失败记录分别使用独立统计 scope。

组件按 Raw 的 component_id 原值统计如下。pages 是至少出现过该组件类型的详情页数，不是频道数。解码数与失败数合计为该类型的组件实例数。

| component_id | 实例 | 页面数 |
|---|---:|---:|
| collapse_panel | 33,974 | 12,543 |
| map_desc | 9,920 | 8,555 |
| material_base_info | 7,435 | 4,703 |
| rich_base_info | 5,022 | 3,830 |
| interactive_dialogue | 5,002 | 4,156 |
| good_desc | 3,322 | 3,266 |
| npc_base_info | 2,489 | 2,489 |
| card_group_info | 2,353 | 2,346 |
| cave_base_info | 2,328 | 2,328 |
| base_info | 2,327 | 1,535 |
| strategy | 2,212 | 2,094 |
| multi_table | 2,009 | 1,464 |
| card_base_info | 909 | 762 |
| card_skill | 860 | 232 |
| equipment_growth_info | 672 | 672 |
| timeline_base_info | 649 | 640 |
| monster_base_info | 645 | 642 |
| business_card | 402 | 402 |
| artifact_list_v2 | 331 | 76 |
| equipment_base_info | 246 | 246 |
| role_base_info | 208 | 208 |
| role_talent | 142 | 134 |
| recommend | 134 | 134 |
| role_ascension | 134 | 134 |
| role_voice | 134 | 134 |
| filter_table | 91 | 66 |
| role_relation | 55 | 55 |
| dress_base_info | 50 | 35 |
| spiral_abyss_target | 13 | 13 |
| copy_base_info | 4 | 1 |

组件目录只记录 ID 和出现数，不把 ID 名称当作已经验证的语义定义。各类型解码后字段和类型计数见附表 component_id_catalog 与 field_catalog。

## 4. 标签、栏目分类与内容标记

### Raw 中出现标签的位置

| 来源 | Raw 路径/字段 | 观测到的值或关系 |
|---|---|---|
| 栏目树 | map 响应 data.list[] 的节点 name/id/parent_id | 本快照含“图鉴”(189)、“角色”(25)、“任务”(43)、“过场动画”(81)、“考据”(209)、“观测·考据”(62)、“玩家贡献榜”(217) 等栏目名称 |
| inventory | metadata/inventory.json 每项 channels[] | 内容与栏目 ID 的关联；16,437 个内容记录、32,888 条 membership、58 个不同栏目 ID |
| 详情页栏目描述 | data.page.menus[] | 节点对象含 id/name/parent_id 等；阿蕾奇诺样本 menus[0] 为 id=25、name=角色、parent_id=189 |
| 栏目配置 | data.page.menus[].ch_ext | JSON 字符串；样本中含展示样式、图标、筛选器选项树等配置 |
| 内容筛选值 | data.page.ext.fe_ext | 对象内的字符串；字符串解码后常见 c_43.filter.text，再对 text 解码为字符串数组 |
| 列表项筛选值 | data.list[].list[].ext 等列表路径 | ext 是 JSON 字符串；内含 c_N.filter.text，text 又是 JSON 字符串数组 |

本快照 16,437 个详情页的 filter_values 均为空对象；任务、角色、物品等筛选信息在样本中位于 ext 等字段，而非 filter_values。完整列表 ext 中统计到 689 个不同的筛选字符串、70 个不同筛选标签。计数按列表条目出现次数汇总，重复 content_id 的行也计入。

### 目标词的直接观测

- “魔神任务”作为筛选字符串出现于 类型/魔神任务（268 次）和 任务类型/魔神任务（100 次）。
- “世界任务”作为筛选字符串出现于 任务类型/世界任务（1,430 次）和 类型/世界任务（102 次）。
- “角色故事”不是本快照筛选字符串中的一个完整标签；详情模块名中可见“角色故事1”至“角色故事5”。全量模块名计数分别为 144、144、144、130、130。content_id=501157 页面含上述故事模块。
- 在 16,534 个 Raw 响应 JSON 的字符串值中未发现完整字符串“玩家考据”。栏目树中存在“考据”“观测·考据”“玩家贡献榜”等不同名称；这些名称本身不表示每条内容的作者或贡献者身份。
- 数据中没有与上述任务、故事或玩家考据标签同义的 Raw 字段被本调查确认。列表 article_user_name 在 32,916 次条目出现中均为空字符串；详情 ext.post_ext.post_user_name 在本次样本中为空。未据此推断作者身份。

示例：详情 content_id=509653 的 data.page.ext.fe_ext 是字符串，内容解码后包含 c_43.filter.text；该 text 解码后的值为“任务类型/世界任务”“任务区域/至冬”“版本号/7.0”。同一条目在 channel_id=43 列表中的 ext 亦存有这些筛选值。原始定位为：
- 详情文件：responses/details/e3077713843d5632894c1704f7ade455c82b5fd5fca186ce03168439be8df84c.json，JSON Pointer /data/page/ext/fe_ext。
- 列表文件：responses/listings/44cb730c420480a0477b505ae68af508fb90f96cf0ec54c6ad16949dd427f13a.json，JSON Pointer /data/list/0/list/1/ext。
- 上述 JSON Pointer 定位到字符串字段；其内部 JSON 字符串再解码后才有 c_43/filter/text 和筛选数组。

## 5. 正文、表格、HTML、链接、图片与对话

### 解码后正文与组件样本

所有 84,072 个组件外层 data 均为字符串；84,071 个可解码为对象。不同 component_id 的解码结构差异明显，以下为实际样本，不将样本结构外推为每个同类组件的必备字段：

| 样本类别 | content_id 与 Raw 文件 | 真实路径与观察 |
|---|---|---|
| 角色页 | 501157 阿蕾奇诺；responses/details/c37f8c2180545b94183b2ffc07eb44b144551a829a15de281d940f5d258342a0.json | 29 个模块；模块索引 5 为 multi_table，索引 14 为 role_voice，索引 15 名称“角色故事1”，索引 25 名称“关联词条” |
| 任务页 | 509653 影域的遗留；responses/details/e3077713843d5632894c1704f7ade455c82b5fd5fca186ce03168439be8df84c.json | 8 个模块；模块索引 7、组件索引 0 为 interactive_dialogue |
| 视频页 | 509109 过场动画-「若我们再次相见」；responses/details/a10095afac5878f961581458d5b5528872fccb01ed5ac380b1597dc68a65cc8a.json | 1 个模块、1 个 collapse_panel；data JSON 解码后 rich_text 内有 video、poster、img URL |
| material_base_info | 502532；responses/details/00051df7eb582979b3d5a63c26efd4a86925ceb9a6d2bdc036dad92d21d0b0f9.json | data 解码对象含 name、img、materials、attr；样本页面名为“不动鸣神，恒常乐土” |
| monster_base_info | 500790；responses/details/00c8078baa6b1c4b442795b319c9ca657b6c401b126e30fc4cf8482d85fca392.json | data 解码对象含 attr、feedback、list；list 中出现 image URL 和 tab_name |
| map_desc | 1039；responses/details/00037f39cf870a1f49129f9c82d935665d352ffd25ea3296208f6f7b16fd654f.json | 模块索引 1、组件索引 0；data 解码对象含 list，每项含 tab_name 与 image URL |
| 卡牌页 | 500188；responses/details/007943b703a61c32e2af4f27dc52d2ee7ac040f8f6e019fbb038daebb59204b6.json | card_base_info 的 data 含 name、image、star、life、energy、attr 等字段 |

### 表格与字段数据

- multi_table 的解码值包含 tables[]；样本表对象含 tab_name、header[] 和 row[][]。表头与单元格都是字符串；单元格中可嵌 HTML、图片、链接及实际叙述文本。阿蕾奇诺样本 Raw 路径为 /data/page/modules/5/components/0/data，嵌入 JSON 路径为 /tables/0。
- filter_table 的解码对象含 table.filter、table.header、table.row 等数组字段。
- role_voice 的一条实际数据位于 content_id=501157 的 /data/page/modules/14/components/0/data，内层 /list/0/table/0，字段包括 name、content、audio_url、isUploading、audio_name。content 是语音文本，audio_url 是 mp3 URL，未见相应音频二进制存于本 Raw 快照。
- content_id=502532 的 material_base_info 示例含 {"name":"不动鸣神，恒常乐土","materials":{"key":"成就集","value":"<p>天地万象</p>"}}。字段值本身可混合纯字符串、数组、HTML 字符串及 URL。
- generic attr[] 常见为包含 key、value[] 的对象数组；key 往往是中文字段名，value 项可为数字的字符串、普通文本或 HTML。相同字段名会在多个 component_id 下以不同嵌套方式出现。
- map_desc 的图片位于解码后 list[].image；其他图片也可出现于 JSON 字段 img/image/icon/avatar 等，或 HTML 的 img src、data-image-url 属性。

### HTML 与样式的全量字符串扫描

以下计数来自对全部 84,072 个组件 data 原始字符串进行标签/属性正则扫描，不是浏览器渲染后的 DOM 统计：

| 观测项 | 计数 |
|---|---:|
| 含 HTML 标签形式字符串的组件 data | 64,700 |
| 含 style 属性的组件 data | 18,395 |
| script 标签 | 0 |
| CSS url(...) 形式 | 0 |
| p 标签文本 | 763,015 |
| span | 547,003 |
| a | 117,222 |
| img | 104,178 |
| td | 45,728 |
| table | 1,157 |
| audio | 5,294 |
| video | 989 |
| iframe | 741 |

HTML 片段包含段落、标题、列表、表格、内联样式、图片、音视频及条目链接。style 属性、data-image-url、data-video-url、poster、src、href、data-entry-id、data-entry-name、data-entry-link 都是实际出现的属性值；这些属性承载图像/视频资源位置、链接和条目关联信息。页面中的 CSS 表现以字符串内 HTML 属性为本次可见证据；本 Raw 目录只含 JSON 响应和侧车，没有随页面响应保存的独立 CSS 文件。

content_id=509109 的 /data/page/modules/0/components/0/data 解码后 rich_text 含 wiki-editor-extension__local-video-wrapper、data-video-url、data-video-poster 与 video src。URL 指向 mp4/png 资源；此快照保留 URL，没有资源二进制。

### 条目与媒体关联

阿蕾奇诺 content_id=501157 的模块索引 25、组件索引 0，其 rich_text 可见 custom-entry-wrapper，包含 data-entry-id=509103、data-entry-name=奥黛塔、data-entry-img 和 data-entry-link；内部 a.href 指向 /ys/obc/content/509103/detail?...。这是正文 HTML 中的条目 ID、显示名、图片 URL 和目标链接并存的样本。奖励 HTML 还观察到 data-entry-link="无" 与 href="%E6%97%A0"；不是所有 href 都是内容详情页路径。

栏目/条目详情、列表条目、HTML 中的条目引用及资源链接是不同层级的关联：列表 content_id 连到详情页 id；data-entry-id 可在样本中与另一详情 id 相同；图片/音频/视频则通过 URL 字符串引用。Raw 目录未包含 URL 指向的媒体文件本体。

### 互动对话结构

content_id=509653 的组件原始路径为 /data/page/modules/7/components/0/data。该字段是 JSON 字符串；解码后对象顶层含 contents、list、root_id、child_ids。其 list[0] 组内含 root_id、child_ids、contents。真实节点片段（省略其他字段和完整对白）：

    {
      "root_id": "ewllh0qqr0dT1711546416025",
      "child_ids": {
        "ewllh0qqr0dT1711546416025": ["sriyhsrfz7T1787134572785"],
        "6mttjd5ntbaT1787134589088": ["xw8gn2cly6oT1787134598748"],
        "m3crhuggnonT1787134589840": ["xw8gn2cly6oT1787134598748"]
      },
      "contents": {
        "ewllh0qqr0dT1711546416025": {
          "option": "",
          "dialogue": "<p><span>你终于将遗迹内的古代造物发掘完毕…</span></p>…",
          "icon": ""
        }
      }
    }

Raw child_ids 的对象键是父节点 ID，值数组列出子节点 ID；上例同一子节点出现在两个父节点的子数组中。contents 对象以节点 ID 为键；节点对象在全量有效载荷中观测到的字段为 option、dialogue、icon。dialogue 可为 HTML 字符串并含说话人称呼文本；节点对象中没有 speaker/speaker_name/actor/character 字段。option 字符串可含选项文字。

在本快照的有效 interactive_dialogue JSON 中，计数为 5,001 个组件、3,950 个 list 组、46,765 个组内节点与 47,263 个子边。1 个 interactive_dialogue 的 data 是空字符串。3,864 个有效组件的顶层 root_id 为空；1,137 个顶层对象含非空 contents 和 child_ids 对象。组层面观测到 211 个 contents 为空的组。所有可读节点 payload 都有 option、dialogue、icon 三个键。Raw 没有单独的 node ordering 字段；顺序体现于 JSON 对象成员次序与 child_ids 值数组顺序。本段计数只描述载荷结构。

## 6. Raw、Parsed 与 Canonical 的既有字段/来源映射

### Parsed 实现与本地记录

代码位置：src/genshin_corpus/parser/obc/adapter.py、src/genshin_corpus/parser/rich_text.py、src/genshin_corpus/parser/dialogue.py、src/genshin_corpus/parser/contracts.py、src/genshin_corpus/parser/models.py 与 src/genshin_corpus/parser/classification.py。

实现行为如下：

- OBC 解析器校验 Raw 文件 SHA-256、UTF-8 JSON envelope、data.page、modules 数组和 page.id/content_id 一致性。
- ParsedDetail 保存 source、locale、run_id、content_id、page_id、name、page_type、source_metadata、source_template_layout、modules、unsupported_modules 与 channel_memberships。source_metadata 包含页面除 modules 和 template_layout 外的字段。
- ParsedModule 保存 source_module_id、module_index、name、repeated、is_submodule、origin_module_id、components、unsupported、layout_observations；其 metadata 带 RawRef、SourcePosition、源指纹和解析指纹。
- ParsedComponent 保存 source_component_id、source_data_encoding、source_layout、source_style、units、unsupported。组件 data 同时以 raw 原字符串和 decoded 值进入通用数据单元。可识别的 markup 字符串各自另有 rich_text 单元；interactive_dialogue 另有 dialogue_graph 单元。
- rich_text 输出包含 raw_markup、树形节点及属性、normalized_text、text_segments、links、图片 media 和 entry_references。解析器从 a.href、img.src/data-image-url 与 data-entry-id/name/link 提取对应信息。
- dialogue_graph 保留 group、node、edge、源 ID、顺序、option/dialogue/icon、RawRef、SourcePosition 与结构诊断；当前 OBC 解析代码未由对话文字推断 speaker。
- 当前分类规则只将 source component_id=interactive_dialogue 映射成 content_role=dialogue。其 provenance 仍为 unknown；其它 component_id 的 provenance/content_role 保持 unknown。此 classification 是 Parsed/Canonical 派生字段，不是 Raw 原字段。
- 非 interactive_dialogue 组件有通用 raw/decoded 与 markup 提取，但代码诊断标记为 UNSUPPORTED_COMPONENT（info；没有针对该组件 ID 的专门 handler）。其 Raw 数据不会因此从通用数据单元中消失。

本地 Parsed 目录中的 phase02-batch4-p01eb-full-20260824 manifest 记录 input/accounted_detail_count=16,437，16,437 条 parsed，0 条 parsed_with_anomalies、preserved_unsupported 或 blocked_integrity。Canonical 目录中的 phase03-batch5b-p01eb-full-20260824 manifest 记录 16,437 条 canonical、0 条 canonical_with_anomalies 或 blocked_integrity。以上为 manifest 状态计数；Canonical 记录仍可能包含组件级 UNSUPPORTED_COMPONENT 诊断码。

### 示例来源链

以 content_id=509653 为例：

1. Raw 详情文件：data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/responses/details/e3077713843d5632894c1704f7ade455c82b5fd5fca186ce03168439be8df84c.json。Parsed 记录的 metadata.raw_refs[0] 指向该文件，Raw JSON Pointer 为 /data/page，包含 artifact_sha256、source、locale、run_id 和 content_id。
2. Parsed 记录：data/parsed/mihoyo_obc/zh-cn/phase02-batch4-p01eb-full-20260824/records/9ab40dfee749de20770797757a8c3b8ab71dc622a831162b6428c9aaf53fa898.json。interactive_dialogue 组件位于 Parsed modules[7].components[0]；dialogue_graph 中节点的 RawRef JSON Pointer 指向 /data/page/modules/7/components/0，embedded_json_pointer 可指向 /list/0/contents/<node_id>/dialogue。RichText 单元亦携带组件内嵌 JSON 的路径。
3. Canonical 记录：data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/records/b00b41d85ef9aeaab80511580ef3f35d9a58c3e9f2aeb3521a838ae143b8f345.json。顶层 observation 指向 Parsed manifest/记录观测；sections[7] 对应 Parsed 模块索引 7；ComponentContext 和 CanonicalUnit 保留组件、单元与解析来源信息。
4. Canonical Raw lineage 有 direct_raw、inherited_parent_raw、parsed_dependency 三种 evidence_scope。CanonicalUnit 的 parsed_json_pointer 指向 Parsed 记录中的模块/组件/单元；raw_refs 可携带 Raw 文件路径、SHA-256、content_id、json_pointer 与 embedded_json_pointer。channel_memberships 的 lineage 指向 parsed-manifest:records/509653/channels，scope 为 parsed_dependency，raw_refs 为空。
5. inventory 中 content_id=509653 的 channels 为 ["189","43"]；ParsedDetail 中也保存该 membership；Canonical record_metadata.channel_memberships 通过 manifest dependency lineage 引用该值，而非直接从详情 JSON 推得。

样本 501157、509109 的 Parsed/Canonical 文件路径、RawRef 和 membership 见 data/parsed/... 与 data/canonical/... 的对应 manifest；本报告对这三条代表记录做了字段级检查，没有全量遍历全部 Parsed/Canonical 记录正文。

### 同一源文本的多种派生表示

对 content_id=509653，Parsed 中 interactive_dialogue 组件含一个 raw/decoded 通用单元、每个检测到的 markup 各一个 rich_text 单元、以及一个 dialogue_graph 单元。该样本有三个 rich_text 单元，分别定位到组内三个节点的 dialogue 字段；dialogue_graph 节点也保存对应的 dialogue 字段及 rich-text 解析结果。Canonical 将这些 Parsed 单元分别映射为 structured_observation、rich_text、dialogue_graph 类型的 CanonicalUnit。这是层间表示并存，不表示 Raw 中存在三份独立原始来源。

列表重复方面，32,916 次条目出现中有 17,895 次 title 与 summary 字符串完全相同。此为两个字段取值相同的观察，不据此判断生成原因。

## 7. 异常与空值观察

| 观察项 | 全量结果或样本事实 |
|---|---|
| 页面固定空值 | 16,437 页的 header_img_url、filter_values、menu_name、langs、alias_name、lang、menu_style 均为空字符串/空数组/空对象；icon_url 有 9 页为空 |
| 模块字段 | 84,072 个模块的 desc 均为空字符串；name 有 19,777 个为空；其余固定字段在模块中均有值 |
| 列表行 | 32,916 行中 bbs_url、article_user_name、avatar_url、alias_name 均为空字符串；icon 有 18 行为空；article_time 字段为字符串 |
| interactive_dialogue | content_id=2283 页面，modules[9]（module.id=4272）/components[0].data 为空字符串，JSON 解码报 JSONDecodeError |
| 对话组 | 全量有效对话载荷中有 211 个组的 contents 为空；顶层 root_id 为空的载荷有 3,864 个 |
| 列表重复 | 16,386 个 content_id 各出现 2 次；31 个各出现 4 次；20 个只出现 1 次 |
| 重复字段值 | 列表 title 与 summary 在 17,895 次行出现中相同 |
| HTML | 多个组件 data 混合 HTML、字段 JSON、样式、资源 URL、表格文本；标签和内联属性计数见附表 |
| 失败日志 | 242 行连接拒绝事件均 status=null；Raw manifest 汇总 failed_detail_fetches=0，详情成功计数=16,437 |

字段“为空”按空字符串、null、空数组、空对象计数；具体分字段计数见附表 empty_value_counts。上表未把空值解释为源错误或内容缺失。

## 8. 未确认与调查边界

- 本调查读取一个 zh-cn Raw 快照；没有对其它 locale、其它抓取 run 或上游 API 版本作结构一致性断言。
- 本次统计能记录 component_id、字段名、类型、取值形态和出现次数；对部分标识符/字段（如 c_N、is_poped、is_customize_name、is_abstract、rich_text_editing 等）的完整业务语义，当前数据结构本身不足以确认，报告不作解释性扩展。
- “玩家考据”未在本快照 Raw JSON 字符串中出现；现有的考据类栏目名不能证明某条记录的作者身份或来源性质。
- 对话结构没有显式 speaker 字段；文本内的冒号称谓不等同于 Raw speaker 属性。
- Raw 只见图片、音频、视频等 URL 或 HTML/JSON 字段，没有取得这些 URL 所指的二进制媒体；媒体内容、可访问性及页面实际渲染效果未验证。
- 字段目录对动态对象键使用占位符；真实 node_id、content_id 等值仍可在相应 Raw 样本中查到，但不会作为数万条独立字段路径重复输出。
- Parsed 与 Canonical 的全量状态依据 manifest；正文细节只检查代码、schema 和 content_id=501157、509653、509109 三条样本。未对所有 Parsed/Canonical 文件做全量字段分布统计。
- 本报告不统计 Retrieval Unit 正文，也不对分片内容选择或策略作判断。

## 附件

- 主报告：docs/research/raw-data-structure-inventory-20261106.md
- 完整统计附表：docs/research/raw-data-structure-inventory-20261106.stats.json
- 既有背景资料：docs/current-phase.md、docs/research/phase-01/、docs/research/data-cleaning-architecture-investigation-20261106.md
