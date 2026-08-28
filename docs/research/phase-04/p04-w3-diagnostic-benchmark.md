# P04-W3 — Diagnostic Benchmark Expansion + Lexical Failure Isolation

本记录是一次只读诊断实验，依赖 Canonical run `phase03-batch5b-p01eb-full-20260824` 与未重建的 r02 Retrieval 表示 `p04-w2-phase03-batch5b-lexical-r02`。它不选择表示、分析器或生产检索技术。

## Benchmark

`benchmark-v0.json` 保持不变（11 queries / 13 locations）。新增 `benchmark-v0.1.json` 为独立诊断集，共 4 queries / 5 evidence locations / 3 Canonical records，覆盖两个实体（阿蕾奇诺、胡桃）与结构化资料、对话分支、角色叙事释义、同实体 wrong-role hard-negative。原先标注为 `structured_table` / `character_voice` 的胡桃条目与叙事 Unit 重复，已移除；因此不再将该 Unit 解释为结构化表格或角色语音证据。所有保留 locations 已通过现有 locator 严格解析。

## Matrix contract

Analyzer A 保持 `phase04-cjk-unigram-ascii-token-0.1`；Analyzer B 为 `phase04-cjk-unigram-bigram-ascii-token-0.1`。BM25 参数、source-address tie-break `phase04-bm25-source-address-tiebreak-0.2`、四个 r02 表示臂与 coverage gold matching 均不变。结果版本为 `phase04-w3-lexical-matrix-0.1`。

## Aggregate observations

| arm | analyzer | R@1 | R@5 | R@10 | MRR | sufficient@10 | HN top-10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| naked_leaf | A | 0 | 0 | 0 | 0.0125 | 0 | 0 |
| naked_leaf | B | 0 | 0 | 0 | 0.0070 | 0 | 0 |
| contextualized_leaf | A | 0 | 0 | 0 | 0.0133 | 0 | 0 |
| contextualized_leaf | B | 0 | 0 | 0 | 0.0070 | 0 | 0 |
| structured_path_value | A | 0 | 0 | 0 | 0.0003 | 0 | 0 |
| structured_path_value | B | 0 | 0 | 0 | 0.0156 | 0 | 0 |
| dialogue_graph_local | A | 0 | 0 | 0 | 0.0006 | 0 | 0 |
| dialogue_graph_local | B | 0 | 0.25 | 0.25 | 0.125 | 0.25 | 0 |

逐题 ranks、slice metrics 与完整元数据保存在被忽略的 `data/retrieval/p04-w2-phase03-batch5b-lexical-r02/metadata/w3-lexical-matrix-v0.1.json`。

关键 diagnostic slices 的 Recall@10（query_count 以实际切片归属为准；`character_narrative=2`，其余列出的 slices 均为 1）为：

| slice | naked A/B | contextualized A/B | structured A/B | dialogue A/B |
| --- | --- | --- | --- | --- |
| structured_attribute_value / structured_table | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| dialogue_option / branch | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 1 |
| semantic_paraphrase | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| wrong_role_contamination | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

## Failure isolation

- 结构化生日 gold：structured A/B 为 989/16，B 有明显局部改善但仍未进 top-10；leaf 臂没有结构化 coverage，不能把同记录命中当作 structured 成功。
- 对话 option/branch gold：graph-local A/B 为 419/2，B 进入 top-10；这不表示已选择 B。
- 胡桃叙事释义：naked A/B 为 20/36，contextualized A/B 为 19/37，均未进 top-10，表现为词法 paraphrase/常见词竞争。
- 同实体 wrong-role：naked positive A/B 为 12331/8929、hard-negative 为 425/111；contextualized positive A/B 为 1741/910、hard-negative 为 359/380。四臂 HN-top10 均为 0；仅报告 ranks，不执行 down-rank 或角色分类。

## Decision gate

选择 **D — mixed / UNKNOWN**。B 对一个结构化 gold 与对话 gold 有局部改善，但新增集仅 4 queries，HN 未跨多个 family 进入 top-k。因此不启动 Dense、routing 或 analyzer follow-up。

## Integrity

- r02 四个 gzip artifact 未重建；counts/SHA 与 W2 manifest 一致。
- Canonical、Raw、Parsed、profiler 与 benchmark-v0 未修改。
- 数字仅是本次 Canonical run、r02 表示依赖和 v0.1 seed 的观察。
