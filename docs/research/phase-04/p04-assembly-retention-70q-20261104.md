# Phase04 Evidence Assembly 保留底稿试验

2026-11-04。用户授权实现经来源证明的完整 RU 去重、结构化 HTML 保守展示压缩及
两者组合，并离线复用既有 70 题材料。运行完整性与原文质量分别记录。
当前默认、采用和授权状态由 [current-phase](../../current-phase.md) 管理。

## 正式采用记录

2026-11-04，用户明确选择现有 `combined` 实现作为后端、CLI、Streamlit 的统一默认。
`GENSHIN_ASSEMBLY_BACKEND=formal` 显式回退到原 Formal Deferred-Footprint-Charge。
直接后端使用 `SingleQuestionBackendConfig(assembly_backend="formal")`。
下文试验结论记录的是采用前检查点，完整包人工盲评和 DeepSeek 生成质量继续待评。
本次采用复用已有补位策略，运行必要离线回归，并创建仅含相关改动的本地提交。
采用回归共 151 项通过，覆盖真实小夹具的后端 combined 默认及 Formal 回退、
CLI 共享环境选择、Streamlit 共享 runtime、无效选择器、Generation 投影与引用。
本次采用没有新增完整 70 题检索运行，收费 API 调用为 0。

## 实现契约

`retrieval.evidence_compaction.assemble_retained_evidence_packet` 是版本化入口，亦用于离线对照，
版本为 `phase04-retained-evidence-1`。三个 selector 为 `aliases`、`html`、`combined`。
backend、CLI、Streamlit 默认使用 combined，formal 保留为显式回退。

- 每次先运行现行 Formal Assembly，冻结已经入包的独立证据及上下文。
- 去重只处理已经可见的单 RU 块。两项必须通过原有 Raw 对白同源证明，正文和
  可见标题、章节、说话人相同，且该组恰有一项 rich_text 和一项 dialogue_node。
  保留原融合排名更高的项，平局沿用原包顺序。被合并的完整 member、来源证明和
  最终代表证据 ID 留在 admission audit。代表项始终实际可见。
- HTML 展示版本为 `phase04-structured-layout-display-1`。仅简化结构化标量内
  成对且可定位的 `p/br/span/strong/em/b/i` 标签，以及只含限定排版属性的标签。
  排版属性限定于 white-space、text-align、min-height、line-height、font-size、
  font-weight、font-family、font-style、margin、margin-top、margin-bottom、padding、
  text-indent、vertical-align、letter-spacing、word-spacing。保留所有字符数据、
  实体、JSON Pointer、字段顺序、颜色属性、链接、媒体、条目名称和未知属性。
  段落与换行标签转换为换行，原 RU 及 member.text 原样保留。
- 长标量使用同一来源的完整连续分片链定位完整标签，然后只改写当前 RU 内的
  标签区间。审计保存标量上下文散列、链上 RU ID、完整标签范围和本 RU 改写范围。
  引用仍解析到原始 RU。若边界、嵌套或标量行归属不明确，保留整个标量原样。
  若任一既有分片会变成空展示，整个标量链保留原样，避免局部回退造成残缺标签。
- 腾出的预算只供原 Top20 中因总预算落选、且通过原完整邻接块 3,000 字符门槛的
  根 RU 使用。按原融合顺序尝试完整根 RU，补位不带新邻居。独立底稿始终保留。
  总预算仍为 12,000 字符，按最终可见正文计费。原文字符计数另存。
- Packet 的 `control_admission_contract` 和 `control_budget` 保存原控制处置。
  新 `admission_contract` 区分底稿保留、别名和补位，并绑定对照 Packet SHA-256。
  Generation 沿用既有可见正文投影及结构引用校验，不接收新增审计信息。

这项试验复用历史 A1-2-2 的来源证明。历史 runner、负面结果和策略维持各自版本。

## 冻结输入与机械结果

采用 `data/retrieval/p04-assembly-70q-20261104-r3/`。
运行身份为 `4a9cda80b7422cb75b514103b65378b696ec1b52777f2f3cfca89b213b5f0514`。

输入来自 `p04-embedding-70q-20261103-r3` 两侧落盘的 140 个融合 Top20，绑定其
manifest、逐题 result/Packet 散列和原 RU manifest。按候选所属记录加载完整 RU
上下文，验证同一原产物，不重新检索、编码或重排。全部 140 份 Formal 控制包
先通过规范 JSON 字节匹配，随后才创建三个 ablation 的 420 份 Packet。
每份候选均重复执行并检查字节一致、原底稿保留、原文/谱系、投影范围重放、预算、
引用目标和可见别名代表，420/420 通过。模型调用为 0。

| 配置 | 方案 | 去重释放字符 | HTML 释放字符 | 补入完整 RU | 有补位的题数 | 最终正文字符 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 本地 1024d | 对照 | 0 | 0 | 0 | 0 | 813,640 |
| 本地 1024d | aliases | 19,328 | 0 | 9 | 7 | 800,711 |
| 本地 1024d | html | 0 | 79,135 | 65 | 42 | 783,455 |
| 本地 1024d | combined | 19,328 | 79,135 | 80 | 48 | 779,537 |
| 保留 2048d | 对照 | 0 | 0 | 0 | 0 | 808,910 |
| 保留 2048d | aliases | 18,094 | 0 | 11 | 9 | 796,407 |
| 保留 2048d | html | 0 | 77,458 | 66 | 45 | 781,806 |
| 保留 2048d | combined | 18,094 | 77,458 | 82 | 49 | 776,038 |

释放数在补位前计量，与最终 Packet 的净字符变化分开。两侧去重各涉及 27 题，
分别合并 41/40 项。比调查阶段仅用 Raw 证明统计的 19,654/18,420 字符更保守，
因为实际入口还要求相同可见标签和无歧义的两项组。HTML 实际释放约占对照正文
9.7%/9.6%。最终提示字符含编号、上下文标签等，本地 combined 从 865,228 降至
832,373，保留侧从 859,642 降至 828,124。字符变化不推定 token、费用或答案质量。

初始 r1 因候选省略行未提供既有 Markdown 渲染器所需的 `unit_ids` 而失败，随后已
补齐并通过导出。r2 在长标量重复散列的性能诊断中主动中止。最终 r3 对每个标量
只计算一次上下文散列，并用有序标签区间定位局部改写。前两轮保留独立状态记录。
127 项相关离线回归通过，包含 16 项新增回归。Windows 沙箱默认临时目录权限
导致的一次测试失败，已通过将测试 TEMP/TMP 指向仓库 tmp 后完整复跑解决。

## 原文变化审阅与采用判断

`review/codex-delta-review.jsonl` 按原题逐项记录三个方案、两个 profile 的新增
来源、原文片段和变化判断。审阅复用了先前的原文判断作为对照背景，重点阅读新增
内容及四个诊断案例。它属于增量原文审阅。完整包的人工盲评仍为 pending，结构引用
校验继续报告 `semantic_faithfulness=not_evaluated`。

- **Q020，本地 aliases 和 combined 有明确收益。** 两组都补入
  `5831f7d537…`，原文直接说明希鲁伊为希琳之子，并说明其父为基斯拉。
  原包关于利露帕尔/奥尔玛兹、亲姐弟成婚和希鲁伊/胡拉姆丁同一身份的证据仍可见。
  aliases 最终正文 11,580，combined 为 11,343 字符。html 释放 491 字符后仍放不下
  该块，最终保留 11,398 字符。空余字符与完整关键 RU 可用性需分别判断。
- **Q037，旧侧缺口仍在。** html/combined 补位先接纳融合第 12 的夜叉考据文章，
  闲云直接说弥怒最擅长设计衣物的第 14/15 项仍被预算挡住。释放空间后依顺位补位
  仍可能优先增加背景资料。本地原包的直接对白被保留。
- **Q022/Q030，原诊断保持。** 指定的关键 RU 分别在本地融合第 23 和 Hybrid
  Top500 之外。本试验固定 Top20，实际补位没有修复这两个指定缺口。
- **新增支撑与噪声并存。** Q048 补入艾伊丝直接称「斯彭西安号」为贝瑟·埃尔顿
  旗舰的对白，Q062 旧侧补入戴因直接说维瑟弗尼尔为其哥哥的对白。Q004/Q021/
  Q034/Q037 等题也增加带有作者推测、文化考据或评论的内容。不能将官方站点承载
  的全部文本都作为官方设定的直接证明。这里保持原内容角色，未发明自动优先级。
- **未据此重判历史不足或待审。** Q003/Q015/Q017/Q033/Q041/Q045/Q050/Q057/
  Q070 的已知缺口仍需对应来源核对。Q004/Q044/Q060/Q061 保留待审，尤其 Q041
  的同条件饭量排序与 Q061 的否定命题无法凭新增字符或未见名字确证。

在正式采用前的试验检查点，Q020 已支持该方向继续评审。三组完整结果同时暴露了补位质量的不确定性，
当时采用条件中的人工验收与整体证据质量判断尚待完成，该检查点使用 Formal 默认。
当时建议继续检查 420 对盲评材料，再由用户决定采用哪组。之后的用户采用决定见本页正式采用记录。
收费 DeepSeek 生成、真实 token 收益与最终答案质量须有独立授权和验证。

## 使用与产物

从项目根目录在 PowerShell 7 执行，每轮使用新目录。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m genshin_corpus.rag.assembly_70q --output data/retrieval/p04-assembly-70q-next
```

runner 禁止 socket 连接。`manifest.json` 绑定输入和代码散列，`source/` 保留本轮
实现快照，`control/` 与三个 selector 目录保存可引用的 JSON/Markdown Packets，
`metrics.jsonl` 保存机械检查和计量，review 目录保存 420 对盲包、解盲键和 pending
评审模板。参考答案仅在全部 Assembly 完成后进入 review 导出。

复制 `review/decisions-template.jsonl` 另存并完成盲评后，可以校验其运行身份、
证据 ID 和判断，再汇总。

```powershell
python -m genshin_corpus.rag.assembly_70q --output data/retrieval/p04-assembly-70q-20261104-r3 --tally-review data/retrieval/p04-assembly-70q-20261104-r3/review/decisions-reviewed.jsonl
```
