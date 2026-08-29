# P04-W5 — Benchmark Reliability & Coverage Expansion

本工作单元只扩展 benchmark annotation reliability 与 diagnostic coverage；不改变
Canonical、r02 representation、Analyzer A/B 或 scorer，也不选择 Retrieval 技术路线。
所有下列全量观察依赖 Canonical
`phase03-batch5b-p01eb-full-20260824` 与 r02
`p04-w2-phase03-batch5b-lexical-r02`。

## Freeze、覆盖与校验

`benchmark-v0.3.json` 在首次读取 W5 rank 前冻结，SHA-256 为
`e6adee5dd7b235af5306e4e1fc6d5a2387789c1021921cffd8e4d8c635890647`。
它原样保留 v0.2 的 12 个 anchor，新增 9 个 query，合计 21 queries、25 个
evidence locations、11 个 Canonical records。按互斥的 W5 目标 query group 计数为：
structured 5、dialogue 5、semantic paraphrase 5、natural wrong-role HN 4、control 2；
因此 5 + 5 + 5 + 4 + 2 = 21，而不是只对前三个 positive group 求和。

新增 primary gold 未增加阿蕾奇诺，且 `unique_positive_locations_required=true`
禁止 v0.3 内最深 positive location 复用。Canonical locator 严格解析及 r02
required-arm source coverage 共解析 25/25 locations；HN 也严格解析、与同题
positive 的 Record/Section/Context/Unit scope 不同，并被 sufficient evidence
sets 排除。

新增 typed eligibility（`phase04-benchmark-evidence-eligibility-0.1`）不从
component/template 名称推断 evidence type：structured 必须为
`structured_observation` Unit、带 decoded pointer 且被 r02 `structured_path_value`
显式覆盖；dialogue 必须为 `dialogue_graph`、带 node selector（branch 另需 edge）
且被 `dialogue_graph_local` 显式覆盖；semantic paraphrase 必须为 `rich_text` Unit
且被 `naked_leaf` 覆盖。它正是 W3/W4 rich-text 被误标为 structured 时所缺少的
fail-closed 条件。

## Candidate funnel（冻结前、未看 rank）

这是一次候选发现与人工证据审计，不是内容角色分类器。全量结构可用量为：
81,730 个带 scalar decoded path 的 structured Unit（另有 2,342 个 structured Unit
无 scalar path）、5,002 个 dialogue graph / 5,087 groups / 62,727 edges（其中
3,858 groups 同时具 edge 与两端文本）、242,965 个非空 rich-text leaf（90,835 个
长度至少 20）。这些数量不是可直接入题池。

在不读取 A/B rank 的人工审计漏斗中，新增候选为：structured 4（2 入选；2 因
无法在不增加题量下提升 record/family 多样性而不采用）、dialogue edge-local 5
（2 入选；3 因同 record/同一局部图重复而不采用）、rich-text narrative 4（2
入选；2 因只是长文本、非自然改写而拒绝）、same-entity/topic HN 3（1 入选；2
因不能形成自然 lexical-confusion 而拒绝）、record-level control 2（2 入选）。
自动化的 Raw pair/doc 数没有被当作 HN pool；只有逐条确认的自然同实体/主题混淆
才计入 HN。floors 全部达到，故没有 SHORTFALL。

## 固定 A/B 矩阵

只运行一次全量 4 arms × A/B matrix，结果在忽略文件
`data/retrieval/p04-w2-phase03-batch5b-lexical-r02/metadata/w5-lexical-matrix-v0.3.json`。
Analyzer A 为 `phase04-cjk-unigram-ascii-token-0.1`，B 为
`phase04-cjk-unigram-bigram-ascii-token-0.1`；scorer 保持
`phase04-bm25-source-address-tiebreak-0.2`。

| arm | A R@10 / MRR / sufficient@10 | B R@10 / MRR / sufficient@10 |
|---|---:|---:|
| naked_leaf | 0.1429 / 0.1147 / 0.1429 | 0.1429 / 0.1496 / 0.1429 |
| contextualized_leaf | 0.1905 / 0.1747 / 0.1905 | 0.1905 / 0.1738 / 0.1905 |
| structured_path_value | 0.1905 / 0.1083 / 0.1905 | 0.2381 / 0.1456 / 0.2381 |
| dialogue_graph_local | 0.1905 / 0.1043 / 0.1905 | 0.2381 / 0.1516 / 0.2381 |

All / v0.2-anchor / W5-new 必须分开看，不能把新增 controls 误作重复现象：

| arm / analyzer | all R@10 / MRR | anchor R@10 / MRR | W5-new R@10 / MRR |
|---|---:|---:|---:|
| naked A | 0.1429 / 0.1147 | 0 / 0.0100 | 0.3333 / 0.2544 |
| naked B | 0.1429 / 0.1496 | 0 / 0.0095 | 0.3333 / 0.3366 |
| contextualized A | 0.1905 / 0.1747 | 0.0833 / 0.0503 | 0.3333 / 0.3406 |
| contextualized B | 0.1905 / 0.1738 | 0.0833 / 0.0491 | 0.3333 / 0.3401 |
| structured A | 0.1905 / 0.1083 | 0.0833 / 0.0123 | 0.3333 / 0.2363 |
| structured B | 0.2381 / 0.1456 | 0.0833 / 0.0510 | 0.4444 / 0.2716 |
| dialogue A | 0.1905 / 0.1043 | 0.0833 / 0.0452 | 0.3333 / 0.1830 |
| dialogue B | 0.2381 / 0.1516 | 0.1667 / 0.1253 | 0.3333 / 0.1868 |

### Repeated per-query observations

- Structured arm A→B ranks：birthday `989→16`、谢苗 `8→2`、阿蕾奇诺语音
  `47→20`、奥黛塔所属 `37→9`、奥黛塔初次见面语音 `10→3`。全体 5/5 改善，B
  将 3/5 置于 top-10；但 W5-new 仅 2/2，且两条均属奥黛塔，不能表述为 new-only
  的跨实体重复验证。这仍只是 lexical follow-up 的可审计信号，不是 winner。
- Dialogue graph-local：既有 `419→2`、`2→1`、`25→323`；W5 新增尼可 `1→1`、
  法尔伽 `2→2`。新样本正确覆盖但未出现 B 的重复改善，且既有一例反向大幅退化。
- Paraphrase naked A→B：`20→36`、`21→13`、`59→149`、奥黛塔 `45→83`、
  法尔伽 `1→1`；context 相应为 `19→37`、`2→2`、`28→20`、`29→24`、`1→1`。
  方向混合，且 rich-text eligibility 只证明 leaf occurrence 可审计，不把“长文本”
  当作 paraphrase 充分条件。
- 四条 HN 在任一 arm/analyzer 均未进入 top-10。naked positive/HN A→B 分别为：
  阿蕾奇诺故事 `12331/425→8929/111`，关系 `48938/277→49794/75`，武器
  `179/83673→496/82132`，奥黛塔 `59/无命中→59/无命中`；context 奥黛塔为
  `32/3377→52/784`。这仍不足以授权 routing/down-rank。

## 结论与 UNKNOWN

W5 的可靠性 gate 通过：证据类型和 required r02 source coverage 在创建/验证时
fail-closed，anchor 与新增结果分离。观察到 B 对 structured 的五个已审计例子均
改善，但 dialogue/paraphrase 呈混合或反向模式；HN 没有跨多家族进入 top-10。
因此本工作单元**不授权** analyzer follow-up、representation redesign、Dense
pilot、routing/down-rank 或任何其他 Retrieval technology follow-up。后续是否、
以及以何种独立问题继续，仍由 technical lead 依据 W1–W5 evidence 另行设计授权。
