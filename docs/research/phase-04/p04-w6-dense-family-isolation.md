# P04-W6 — Dense family-isolation pilot

本实验固定既有 `contextualized_leaf` / r02 文档；W6 新增 pinned-model
Dense document embeddings 与 row mapping，随后在 frozen benchmark-v0.3 上
编码 query 并执行 exact-dot evaluation。Canonical、r02、benchmark-v0.3 均未修改。

## Accepted dependencies and provenance

- Canonical run: `phase03-batch5b-p01eb-full-20260824`
- contextualized_leaf documents: 242,965
- model: local `BAAI/bge-small-zh-v1.5`, revision
  `7999e1d3359715c523056ef9478215996d62a620`
- model SHA-256:
  `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`
- vectors SHA-256:
  `43b6143908e2d3341b4bc7f57d270010715b119185355ff4125e6c771cc5a7ed`
- rows SHA-256:
  `6de8e38375c78165ddd9bdcb74d427c46f325100aba2633bf81c8af0a9365f76`
- benchmark-v0.3 SHA-256:
  `e6adee5dd7b235af5306e4e1fc6d5a2387789c1021921cffd8e4d8c635890647`
- Dense evaluation SHA-256:
  `e758096daf385099fd0588e466430033d0a0e52787c9be6e56b3d1987ac0da5f`

The evaluator was `D:\GenshinChronicle\.local\w6_dense_evaluate.py`, run with
`D:\python\python.exe` and `.local\w6-runtime`. It used
`local_files_only=True`, the query-only instruction
`为这个句子生成表示以用于检索相关文章：`，FP32 L2-normalized query vectors,
and existing document vectors only.

## Result

There were 11 eligible and 10 NA queries because contextualized_leaf has no
required structured/dialogue coverage. Dense all-cohort R@10 was 0.7273 and
MRR 0.4834. Clean lexical-miss rescues included readable-mule `28/20 → 3`,
Arlecchino relation `165/575 → 5` (HN 312), and W5-new Odette story-build
`32/52 → 1` (HN 200). Counterevidence included Hutao `19/37 → 42` and Odette
double-life `29/24 → 14`; Arlecchino-build and weapon HNs ranked 10 and 12.

The predefined complementary-signal gate passed. Paraphrase-family repeated
rescue was not established, Dense standalone winner was not established, and
the Retrieval winner remains UNKNOWN. No Hybrid, ANN/vector DB, reranking,
routing/down-rank, larger-model, query-rewrite, or representation follow-up is
authorized by this result.

Ignored artifacts remain under
`data/retrieval/p04-w6-phase03-batch5b-dense-contextualized-bge-small-zh-v1/`:
`vectors.f32.npy`, `rows.jsonl`, `metadata.json`, and `eval/dense-evaluation-v0.3.json`.
