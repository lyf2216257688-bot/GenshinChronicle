# 数据目录

本页登记用到的本地数据根和身份绑定。Git 保存文档、`raw/.gitkeep` 和 `tests/` 中的少量测试夹具。完整语料、索引和运行结果属于 Git 忽略的工作区资产。

## 来源与正式基线

| 数据根（相对本目录） | 负责的内容 |
| --- | --- |
| `raw/mihoyo_obc/zh-cn/` | 官方 API 原始观测、发现响应、失败记录及运行清单。完整观测包括 `p01eb_full_20260824/` 和 `p01eb_full_20261124/`。source/locale 由配置选择，采用状态见[当前阶段](../docs/current-phase.md)。 |
| `parsed/mihoyo_obc/`、`canonical/mihoyo_obc/` | 历史 Phase02/03 证据及血缘，保留独立合同和可回读基线。 |
| `retrieval/vcr-1126/full/`、`retrieval/vc-20261124/full/` | 正式 Release、输入行表、Clean、Chunk pack、Dense/Sparse 数值块及 BM25F 桶。两个物理目录共同组成同一构建臂。以[活动发布目录](../config/mcp-live-v1.json)为准。 |
| `retrieval/incremental-raw-*-20261125.json`、`retrieval/incremental-impact-sample-20261125/` | 版本化 Raw 核验、正式差分、旧分片复用量和真实变更样本的投影及编码实测。逐版本独立数据根与发布描述符的结构见[增量合同](../docs/phases/versioned-corpus-incremental.md)。 |
| `retrieval/storage-adoption-20261126/` | 专项清理的验收、逐文件执行回执及历史归档。旧扁平语料及重复增量臂按历史身份查询，关键绑定和研究结果集中留档。 |
| `retrieval/release-binding-20261126-r1/`、`retrieval/release-binding-20261126-r2/`、`retrieval/storage-cleanup-20261126/` | 隔离绑定与回滚、前置清单和清理回执。原研究工件路径和摘要可继续回读。[验收记录](../docs/research/storage-and-release-readiness-20261126.md) |
| `retrieval/rerank-v1-handoff-20261113/` | P4 完整有序候选交接。排序选择器同时绑定冻结的 70Q 输入、分析和评审，定位见[选择器](../config/reranking-selected-v1.json)与 [P4 报告](../docs/research/clean-reranking-p4-handoff-20261113.md)。 |
| `generation/clean-v1-local/` | 可配置的本地会话默认根。显式查询、Mock 测试以及有限研究使用各自的新根。 |
| `generation/personal-chat-v1/` | 独立个人聊天软件的默认数据根。中央 `sessions.sqlite` 保存长期会话、草稿及不可变结果与反馈的引用。`live-settings.json` 保存官方地址与 Windows 当前账号加密的凭据。`batches/<id>/` 独立保存许可、批次关闭记录、预算、请求账本与执行检查点。 |
| `generation/g7-70q-baseline-20261117/` | G7 题库、逐题结果、独立判断、排序、原始请求账目和 UNKNOWN 轨迹。临时候选投影、打分输入和分词缓存按清理记录精简。 |
| `generation/mcp-offline-v1/<audit-name>/` | 冻结试点的 SQLite 和宿主验收记录。已交付原文与 Packet 可通过专项归档按原绑定查询。 |
| `generation/mcp-live-v1/<audit-name>/` | 实时研究的独立审计根。SQLite 按原绑定保存已提交的查询、收条和 Packet。可继续活动 Release 的研究。已归档绑定的历史交付可从专项归档查询。 |

在用 Dense/Sparse 与 BM25F 的清单绑定同一输入包，输入包再绑定 Chunk、Clean 和 Raw。绝对路径和摘要共同参与验证。模型、解释器及安装来源记录在 `.local/`，入口见[工作台运行前提](../docs/rag-workbench.md#运行前提)。

## 可追溯性与历史数据

- Raw 是不可变证据。派生物通过各自 manifest、哈希和 locator 绑定上游。在用或已封存产物以原数据根、schema 和绑定验证，复现检查使用新输出根。
- 会话 SQLite、LangGraph checkpoint、事件、证据回执和 Packet 分工见 [Generation 合同](../docs/phases/clean-generation-v1.md)。模型操作、预算、失败和 UNKNOWN 与原 campaign 绑定，留存结论与运行原始记录分开。
- 原 RU/词法基线、Phase04 的 70Q 比较、Phase05 导航、Chunk/召回/重排试验及 G0–G7 证据随对应报告查询。数据根上的日期和实验名称不代表采用状态，见[研究索引](../docs/research/README.md)。
- 既有历史档案的定位与早期数据目录说明见[历史数据登记](../docs/research/README.md#历史数据登记)。清理影响以专项执行回执为准。
