# 研究、实验与历史索引

日期文档保存当时的输入、方法、运行结果、独立评审和限制。采用状态及可执行入口分别见[当前阶段](../current-phase.md)和[工作台](../rag-workbench.md)。合同变更记录在 [phases](../current-phase.md#阶段合同)，长期决策由 [ADR](../decisions/README.md) 与适用合同维护。

## Clean 到索引

| 工作 | 证据与复现 |
| --- | --- |
| 源 API 与 Raw | [OBC API 发现](phase-01/mihoyo-obc-api-discovery.md)、[频道树](phase-01/channel-tree-notes.md)、[Raw 结构清查](raw-data-structure-inventory-20261106.md) |
| Clean V1 | [清洗架构调查](data-cleaning-architecture-investigation-20261106.md)、[实现与试点](clean-v1-implementation-20261106.md) |
| Chunk V2.1 | [有限修复与全量结果](clean-chunking-v2-1-20261108.md)、[构建性能与恢复](build-performance-20261107.md) |
| 早期 Chunk | [结构调查](clean-chunking-full-survey-20261106.md)、[初次试点](clean-chunking-pilot-20261107.md)、[三规则修订](clean-chunking-refinement-20261107.md)、[V2 对照](clean-chunking-v2-20261107.md)、[全量构建恢复](chunking-v2-recovery-20261107.md) |
| 索引 | [统一输入导出](indexing-input-v1-20261109.md)、[独立构建及搜索](index-benchmark-launcher-20261109.md)、[Python 3.14 兼容性与迁移](python314-migration-20261113.md) |

## 召回与重排

| 工作 | 证据与复现 |
| --- | --- |
| Retrieval | [P0–P2 实现](clean-retrieval-p0p2-20261111.md)、[P3 Sparse 准备](sparse-build-preparation-20261112.md)、[P4 运行准备](recall-experiment-preparation-20261112.md)、[S0/S1/S2 70Q 评审](recall-p4-70q-quality-review-20261113.md) |
| Reranking | [准备](clean-reranking-preparation-20261113.md)、[P0–P1 运行与最长配对](clean-reranking-p0p1-readiness-20261113.md)、[P2 BGE/GTE](clean-reranking-p2-comparison-20261113.md)、[P3 结构与 MMR](clean-reranking-p3-comparison-20261113.md)、[P4 采用与完整交接](clean-reranking-p4-handoff-20261113.md) |

## Generation

| 检查点 | 证据与复现 |
| --- | --- |
| 技术决定 | [初始实施方案](clean-generation-implementation-plan-20261114.md)保存当时的 24 项决定、约束和验收策略。[Generation 合同](../phases/clean-generation-v1.md#decisions-and-boundaries)记录采用版本及 D16 后续更新。 |
| G0–G2 | [本地状态机、回读和 Packet](clean-generation-g0g2-20261114.md) |
| G3 | [协议与能力限度](clean-generation-g3-20261114.md)、[参数核对](clean-generation-g3-parameter-check-20261114.md)、[tools-first 原计划](clean-generation-g3-tools-live-plan-20261114.md)、[tools/SSE 实测](clean-generation-g3-tools-live-20261114.md) |
| G4 | [Mock 连续对话](clean-generation-g4-20261114.md)、[有限真实 QA](clean-generation-g4-live-20261114.md)、[最终审计](clean-generation-g4-final-audit-20261114.md)、[G3/G4 集成记录](clean-generation-g34-git-close-20261114.md) |
| G5 | [原研究实现与受限运行](clean-generation-g5-20261114.md)、[官方 thinking 试验](clean-generation-official-thinking-20261114.md)、[官方路由与能力](clean-generation-official-g5-20261115.md)、[最终验收检查点](clean-generation-g5-final-acceptance-20261115.md)、[证据价值停止与有限结项](clean-generation-g5-stopping-acceptance-20261115.md) |
| G6 | [暂停交接](clean-generation-g6-pause-20261116.md)、[S2 工程与有限 Live](clean-generation-g6-implementation-live-20261117.md)、[保持 S0 的 Sparse 补充离线研究](clean-generation-g6-s0-sparse-offline-study-20261117.md)。按各原根追溯 UNKNOWN、显式恢复及有限预算。 |
| G7 | [70Q 质量审计](clean-generation-g7-70q-quality-audit-20261117.md)。70 题分桶、逐题判断、问答案据、代表题损失定位与账目分开保存。 |
| G5 第二批 | [证据利用复验](clean-generation-g5-evidence-evaluation-20261123.md)与[续轮传输、证据成本与全链路排序审计](clean-generation-g5-targeted-audit-20261123.md)。封存排名与回执回放、定位与引用对比、有限配对、故障复现、请求开销和排序干预。 |

## Legacy Phase02–04

- [Parsed](../phases/phase-02-parsed-schema.md) 与 [Canonical](../phases/phase-03-canonical-schema.md) 保留确定性抽取、血缘和版本合同。[Batch 5A 验收](phase-03/batch-5a-representative-acceptance.md)记录代表性结构检查。
- [Phase04 检查点索引](phase-04/p04-rag-historical-checkpoint-register.md)与[历史档案](phase-04/p04-rag-historical-checkpoint-archive.md)串联旧 RU、检索、Evidence Assembly 与 RAG 开发。[原端到端修订](../phases/phase-04-first-end-to-end-rag-amendment-v1.md)保留当时的交付约定。
- [旧本地 Qwen3 迁移](phase-04/p04-local-qwen3-migration-20261102.md)、[70Q Embedding 对照](phase-04/p04-embedding-70q-20261103.md)、[Assembly 离线比较](phase-04/p04-assembly-retention-70q-20261104.md)和[有限 DeepSeek QA](phase-04/p04-deepseek-live3-20261102.md)描述各自冻结的历史配置与评价。

## Phase05 语义导航

以下成果属于[暂停的 Phase05 合同](../phases/phase-05-source-bound-semantic-navigation.md)，其报告包含本地证据根、工作点和原实验状态。

| 主题 | 入口 |
| --- | --- |
| 项目早期的证据与决策 | [早期历史档案](phase-05/p05-unique-historical-archive-20261012.md)、[语料与叙事适用性](phase-05/p05-navi-corpus-structure-narrative-suitability-20261010.md)、[W2 语义评审](phase-05/p05-w2-semantic-quality-review-v01-20261008.md) |
| E1/E2 与连续导航 | [E1 本地工具](phase-05/p05-navi-e1-tools-wiring-20261011.md)、[E2 有限探索](phase-05/p05-navi-e2-autonomous-exploration-20261012.md)、[白夜国对照](phase-05/p05-byakuyakoku-navi-comparison-report-20261019.md)、[候选 V 连续交付](phase-05/p05-navi-v-release-continuity-20261020.md) |
| R1、G1、F1、F2 | [R1 召回](phase-05/p05-navi-r1-discovery-20261025.md)、[R1-QA](phase-05/p05-navi-r1-qa-20261025.md)、[G1](phase-05/p05-navi-g1-s1-offline-20261026.md)、[F1 样本冻结](phase-05/p05-navi-f1-r2-freeze-20261026.md)、[F2 批次执行](phase-05/p05-navi-f2-48-execution-20261028.md) |
| 协议与离线环境 | [v0.3 有限运行](phase-05/p05-navi-v03-limited-live-20261028.md)、[离线 Card/Link](phase-05/p05-navi-offline-environment-20261029.md)、[共享调用集成](phase-05/p05-model-call-unification-20261030.md)、[Pinned Qwen3 历史部署](phase-05/p05-local-qwen3-embedding-20261025.md) |
| Card 结果 | [Fixed-66 修复与限度](phase-05/p05-card-live66-repair-20261101.md)、[样本审计](phase-05/p05-card-semantic-sample-audit-20261102.md)、[后续离线改进](phase-05/p05-card-semantic-improvement-20261102.md)、[已关闭的有限对照](phase-05/p05-card-semantic-live6-20261102.md) |

## 历史数据登记

历史运行根保留原路径。报告中的旧命令、额度和默认配置均按当时检查点解读。治理前的阶段记录、数据目录、操作示例、架构及目录说明可按固定 Git 版本查阅：

```powershell
git show 9041148:docs/current-phase.md
git show 9041148:data/README.md
git show 9041148:README.md
git show 9041148:docs/architecture-overview.md
git show 9041148:docs/project-structure.md
git show 9041148:docs/roadmap.md
```

早于本轮的归档事实由 [2026-11-04 存储清理记录](phase-04/p04-storage-cleanup-closure-20261104.md)及 [V2.1 全量构建追记](clean-chunking-v2-1-20261108.md#7-全量构建及归档追记2026-11-09)负责。本轮沿用其历史记载，未访问或新增外置档案。已验证的 V2.1 扁平导出可原位读取。

2026-11-21 查验时，下列历史附件根在当前工作区缺失，历史报告中的原引用仍可用于定位。具体归档位置待独立核实。

- 早期 Chunk：`clean-chunking-survey-20261106`、`clean-chunking-pilot-20261107-r7` 及其 `-review`、`clean-chunking-refinement-20261107-{before,after-r3,review}`、`clean-chunking-v2-20261107-review`、`clean-chunking-full-v2-20261107-recovered-r1`、`build-performance-20261107`。
- Phase05：`p05-navi-g1-20261025-r2`、`p05-navi-real-generation-20261029-r1`。

以上均相对 `data/retrieval/`。本地资产根与依赖的当前登记见[数据目录](../../data/README.md)。
