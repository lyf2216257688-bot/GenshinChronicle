# Clean-based Reranking P4：候选接口与阶段收尾

2026-11-13。**P4交付与有限工程验收完成，Clean-based重排阶段收尾。**
用户正式采用 **BAAI/bge-reranker-v2-m3＋结构感知处理，默认不启用MMR**。
选定配置、完整候选接口、显式回退、70题交接产物及独立校验已落实。
当前状态由[current-phase](../current-phase.md)负责，接口合同见[Reranking V1](../phases/clean-reranking-v1.md)。

## 选定方案及实现

正式选择写入[config/reranking-selected-v1.json](../../config/reranking-selected-v1.json)，
与冻结的`config/clean-reranking-v1.json`实验配置分开：

| 选择 | 行为 |
| --- | --- |
| 默认`bge_structural` | 同一BGE logit相关性排序，再按既有保守覆盖证明延后副本；MMR关闭 |
| 显式回退`bge_relevance` | 同一BGE评分，仅按相关性排序，关闭结构与MMR |
| 错误处理 | 显式失败；不自动换Profile、GTE、CPU、输入长度或候选数量 |

结构规则复用P3已审查实现，不新增Parent配额、事件聚类、召回融合或来源折叠。
MMR研究代码和原始轨迹保留，选定接口不提供启用MMR的开关；没有继续参数搜索。
选择依据继续是[P3报告](clean-reranking-p3-comparison-20261113.md)记录的重复减少与有限增量比较，
不将此裁决解释为全部问题正确或端到端质量验收。

新增[rerank_handoff.py](../../src/genshin_corpus/retrieval/clean_based/rerank_handoff.py)
及[PowerShell入口](../../scripts/retrieval/run-rerank-handoff.ps1)。
没有修改冻结的`rerank_core/model/worker/job`、召回实现或比较配置，
P2/P3计划绑定、评分、审查和Python迁移兼容记录继续有效。

## CandidateSet → RankedCandidateSet

`rank_candidate_set(candidate_set, score_result, selection, *, pair_budget, profile=None)`
只消费候选及已绑定的BGE评分结果，不执行模型推理。默认使用选定结构策略，
回退必须显式指定`profile="bge_relevance"`。

输入要求：完整`gc-clean-candidates-1`、原始S0/f1_max召回结构条件、同一输入包身份、
完整Chunk/source/原始命中、精确输入digest及选定BGE模型/环境绑定。
每个候选必须恰有一个有限logit和一个未截断的token审计，顺序、身份、文本/hash均须一致。
空候选、已重排输入、错绑评分、缺失/重复分数、超长、坏审计和缺失关键来源定位均显式失败。

输出为完整`gc-clean-ranked-candidates-1`：

| 输出位置 | 信息及含义 |
| --- | --- |
| `query`、`input_bundle_id`、原始Profile及其他输入顶层字段 | 原查询、输入身份、召回及原处置信息原样保留 |
| `candidates[*]`原字段 | Chunk/input_row、完整Chunk正文/语境、来源及Clean/Parent定位、条件/未知状态、原始命中、融合与召回名次原样保留 |
| `candidates[*].reranking` | 原候选顺序`recall_rank`、原始`logit`、相关性名次、`covered_by`、源字符交集`overlaps`、`final_rank`；`mmr=null` |
| `policy`、`policy_id` | 可复核的排序参数及身份；`mmr_lambda=1.0`表示本接口未应用MMR |
| `reranking_run` | 选择身份及Profile、原输入版本/digest、评分结果digest、完整评分模型/3.14.5环境身份、token审计与原评分遥测、显式长度预算、排序代码hash及实际Python版本 |

保留的原`counts`、`dispositions`、`deferred_by`、`overlaps`仍表示上游召回阶段，
新排序记录位于`reranking`中，不能混为一套名次。
`logit`不是正确率或跨问题统一概率；`covered_by`表示结构延后依据，不是永久删除指令。
交集记录不声明语义等价，也不保证列出所有两两交集。
接口没有TopK裁剪参数，交付全部已评分候选，报告TopK仅作视图。

`pair_budget`必须显式给出。已交付数据使用2048，实际最长1150 token；
2048没有成为永久上限。接口允许在固定BGE原生8192配对能力内使用更长显式预算，
但不证明更长文本已经在4GiB显存上运行验证。新超长输入仍须按原Plan先做有限Smoke，
不可静默截断、遗漏或自动改用CPU。

## 交接产物与有限验收

已生成的完整交接根：`data/retrieval/rerank-v1-handoff-20261113/`。

- `selection.json`：选定配置快照。
- `manifest.json`：全部题目文件/hash、输入和评分绑定、原独立校验、P3分析/审查引用、排序代码hash及计数。
- `ranked/Q001.json`～`ranked/Q070.json`：完整自包含RankedCandidateSet。
- `complete.json`：最终文件集和hash封存。

| 验证 | 实际结果 |
| --- | --- |
| 全量默认导出 | 70题 / 6,963条；与已审阅P3结构结果的全部名次及调整记录一致 |
| 完整性与来源字段 | 独立重新读取，逐题重建并比较全部字段，全部候选、原文和原始召回信息保留 |
| 结构延后 | 498条；仍在完整候选集，未删除 |
| 显式回退 | 同70题逐题对照纯BGE保存结果通过；不重复导出整套正文、不重算评分 |
| 相关小夹具 | 新接口13项＋既有排序/恢复20项，共33项通过 |
| 启动器 | PowerShell 7解析及实际Export/Validate通过 |
| 实际消费者读取 | Q050读取100条；微观row65275为93，覆盖宏观row65285为11，原句与覆盖链接保留；所有`mmr`为空 |
| 本轮模型推理 / 新向量 | 0 / 0 |

独立Validate回执：
`data/retrieval/rerank-v1-handoff-20261113-checks/validation-1794575184466876200-af687564/report.json`。
其独立文件封存位于同目录`complete.json`，没有向已封存交接根追加可变指针。
校验包括更改文件拒绝、重新封存但内容/来源被改仍拒绝、错绑输入/环境拒绝、
不完整输出不能读取、回退不能冒充默认，以及输出已存在时不覆盖。

关键身份：

| 对象 | 标识 |
| --- | --- |
| 选定配置逻辑digest | `3c3eb3f49da3fe8cafcaeafa8ca01fd80c54d5f1ce38b1ba5ceb0068f506614f` |
| P4交接complete SHA256 | `401ad3b597e60e44377a849dca97345523dbbffb4f59c2345c6e4d3318050472` |
| 原计划绑定 | `6360c2c0b1a896dbb336b26e32e5bce7e35c05e1f29f9f79dce324545137fb8c` |
| 原P2运行complete SHA256 | `4e2c274140cc4dc304c8a43b6748f5a88cb33911eac619c1c8e1e019ec2b9842` |

评分身份为Windows AMD64常规GIL Python3.14.5、Torch2.11.0+cu128、
Transformers4.57.3、tokenizers0.22.2、NumPy2.5.3、safetensors0.8.0，
CUDA FP16/eager/batch1，与P2一致。排序读取实测Python3.14.5。
没有安装新依赖；未恢复Python3.12或历史模型任务。

## 可直接使用的接口示例

在仓库根、现有3.14环境并设置`PYTHONPATH=src`后，读取已校验交接：

```python
from genshin_corpus.retrieval.clean_based.rerank_handoff import read_handoff

ranked = read_handoff("data/retrieval/rerank-v1-handoff-20261113", "Q020")
for candidate in ranked["candidates"]:
    order = candidate["reranking"]
    print(order["final_rank"], order["logit"], candidate["chunk_id"])
    # candidate["source"] / candidate["chunk"] carry the original evidence identity.
```

给后续已有评分的CandidateSet应用同一接口：

```python
from genshin_corpus.evidence_io import read
from genshin_corpus.retrieval.clean_based.rerank_handoff import rank_candidate_set

selection = read("config/reranking-selected-v1.json")
# candidate_set and score_result come from the validated retrieval/scoring adapters.
ranked = rank_candidate_set(candidate_set, score_result, selection, pair_budget=2048)
fallback = rank_candidate_set(candidate_set, score_result, selection,
                              pair_budget=2048, profile="bge_relevance")
```

这段调用不会评分；新查询的真实推理须由已有评分适配器遵守原长度、离线、OOM和单模型约束。
本P4只交付接口与保存评分产物，没有自动连接新的在线检索—重排—生成服务。

PowerShell 7复核已交付数据（已由Codex执行通过，无需再次运行）：

```powershell
./scripts/retrieval/run-rerank-handoff.ps1 -Mode Validate
```

如未来明确需要重新导出，必须指定一个**不存在的新输出根**，保留已有交接：

```powershell
./scripts/retrieval/run-rerank-handoff.ps1 -Mode Export `
    -Output data/retrieval/rerank-handoff-new
# Explicit relevance-only export, also to a new root:
./scripts/retrieval/run-rerank-handoff.ps1 -Mode Export `
    -Profile bge_relevance -Output data/retrieval/rerank-handoff-relevance-new
```

Export只用于固定已验证70题材料，不会重跑Run/Resume/Analyze或模型。
`read_handoff`为简单有限离线读取器，每次验证完整封存文件集；未实现Serving缓存或吞吐优化。
未来生成分支仍须对实际使用的源/Parent定位执行hash校验，不能仅凭候选存在而当作原文回读通过。

## 收尾边界

P0～P4及环境迁移的本阶段交付已完成；模型和排序选择分别由用户裁决，
工程校验与有限质量审查保持区别。源数据、索引、原评分和历史实验回执均保持原样。
P2/P3报告与封存审查中的未采用/待裁决字段保留为当时事实，不机械改写。

生成分支接下来可在另行授权范围内消费完整排序候选。原文/自然Parent回读、最终证据选用、
上下文预算、去重和Packet构建继续归Evidence Assembly；重排不接管这些职责。
本轮未执行Generation、付费调用、Serving部署、Git提交或推送；不新增第七分支。
