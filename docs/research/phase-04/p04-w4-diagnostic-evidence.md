# P04-W4 — Diagnostic Evidence Expansion + Retrieval-Family Decision Gate

本次为测量性诊断，不改变 v0、v0.1、四个 r02 表示、分析器或 scorer。

## Benchmark and integrity

新增 `benchmark-v0.2.json`，独立于 v0/v0.1，共 12 queries / 15 evidence locations / 8 Canonical records；严格解析通过。删除与 paraphrase 条目重复、且不具备 structured_path_value coverage 的 `w4-structured-moon-child-profile`。现包含 structured 3、dialogue option/branch 3、semantic paraphrase 3、wrong-role hard-negative 3，覆盖阿蕾奇诺、胡桃、谢苗、月童资料、可读物、武器、NPC 与任务对话等多个实体/内容族。所有 gold/HN 均为生产 Canonical locator；未使用 gameplay/build 作为 primary gold。

固定依赖：Canonical `phase03-batch5b-p01eb-full-20260824`，r02 `p04-w2-phase03-batch5b-lexical-r02`；四臂文档数量与 SHA 未变。矩阵结果写入忽略路径 `data/retrieval/p04-w2-phase03-batch5b-lexical-r02/metadata/w4-lexical-matrix-v0.2.json`。

## Fixed 8-cell summary

| arm | A R@10 / MRR / sufficient@10 | B R@10 / MRR / sufficient@10 |
|---|---:|---:|
| naked_leaf | 0 / 0.0100 / 0 | 0 / 0.0095 / 0 |
| contextualized_leaf | 0.0833 / 0.0503 / 0.0833 | 0.0833 / 0.0491 / 0.0833 |
| structured_path_value | 0.0833 / 0.0123 / 0.0833 | 0.0833 / 0.0510 / 0.0833 |
| dialogue_graph_local | 0.0833 / 0.0452 / 0.0833 | 0.1667 / 0.1253 / 0.1667 |

这些是 12-query 诊断观察，不是 winner score。

## Per-query A→B movement

- structured：birthday `989→16`（structured arm）；谢苗 `8→2`；阿蕾奇诺 voice `47→20`。leaf/record side hits 不计为 structured success。
- dialogue：existing branch `419→2`；芭芭拉 branch `2→1`；午睡 follow-up `25→323`（graph-local），显示方向不一致。
- paraphrase：胡桃 `20→36`（naked）、`19→37`（context）；月童历史 `21→13`（naked）、`2→2`（context）；可读物 `59→149`（naked）、`28→20`（context）。存在改善与退化并存。

## Wrong-role observations

三条 wrong-role 均未在任一 arm/analyzer 进入 top-10；仅报告 positive/HN ranks，不执行 down-rank。阿蕾奇诺 build：naked `12331→8929` vs HN `425→111`，context `1741→910` vs HN `359→380`；关系项 naked `48938→49794` vs HN `277→75`，context `165→575` vs HN `136→29`；武器故事 naked `179→496` vs HN `83673→82132`，context `120→102` vs HN `29→26`。HN 未跨实体/内容族进入 top-10。

## Decision gate

选择 **D — mixed / UNKNOWN**。structured 与 dialogue 在部分样本上改善，但 dialogue 存在明显退化，paraphrase 方向混合；HN 未进入 top-10。因此不启动 analyzer follow-up、Dense pilot 或 routing ablation。
