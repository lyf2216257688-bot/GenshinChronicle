# 数据目录

本页登记用到的本地数据根和身份绑定。Git 保存文档、`raw/.gitkeep` 和 `tests/` 中的少量测试夹具。完整语料、索引和运行结果属于 Git 忽略的工作区资产。

## 来源与正式基线

| 数据根（相对本目录） | 负责的内容 |
| --- | --- |
| `raw/mihoyo_obc/zh-cn/` | 官方 API 原始观测、发现响应、失败记录及运行清单。完整快照为 `p01eb_full_20260824/`，source/locale 由配置选择。 |
| `clean/mihoyo_obc/zh-cn/clean-v1-full-20261106/` | Clean V1 结构记录和 Raw 定位，`metadata/manifest.json` 为正式下游绑定。 |
| `parsed/mihoyo_obc/`、`canonical/mihoyo_obc/` | 历史 Phase02/03 证据及血缘，保留独立合同和可回读基线。 |
| `retrieval/clean-chunking-full-v2-1-20261108/` | V2.1 manifest 及 flat exports，包含实际候选、自然父级、对白图和准入记录。 |
| `retrieval/index-benchmark-v1-20261109/` | 其中的 `dense/bge-m3/`、`bm25f/` 和两者共同绑定的 `input-attempts/` 是在用资产。以[选择器](../config/index-selected-v1.json)为准。 |
| `retrieval/bge-m3-sparse-v1-20261112-ready/`、`retrieval/python314-compatibility-20261113-r3/` | 分别为显式 S2 配套索引和用于校验运行时与既有索引兼容性的证明。 |
| `retrieval/rerank-v1-handoff-20261113/` | P4 完整有序候选交接。排序选择器同时绑定冻结的 70Q 输入、分析和评审，定位见[选择器](../config/reranking-selected-v1.json)与 [P4 报告](../docs/research/clean-reranking-p4-handoff-20261113.md)。 |
| `generation/clean-v1-local/` | 可配置的本地会话默认根。显式查询、Mock 测试以及有限研究使用各自的新根。 |
| `generation/personal-chat-v1/` | 独立个人聊天软件的默认数据根。中央 `sessions.sqlite` 保存长期会话、草稿及不可变结果与反馈的引用。`live-settings.json` 保存官方地址与 Windows 当前账号加密的凭据。`batches/<id>/` 独立保存许可、批次关闭记录、预算、请求账本与执行检查点。 |
| `generation/g7-70q-baseline-20261117/` | 完整 G7 基线及审计。保留 `questions/` 原始轨迹、`records/` 逐题结果、`audit/` 独立判断、账目和 UNKNOWN 尝试。 |

在用 Dense/BM25F 的 manifest 共同指向同一输入包，输入包再绑定 Chunk 与 Clean 的绝对路径。移动这些路径会使工作台的验证或源文回读失败。模型、解释器、wheel 和迁移凭据在 `.local/`，入口见[工作台运行前提](../docs/rag-workbench.md#运行前提)。

## 可追溯性与历史数据

- Raw 是不可变证据。派生物通过各自 manifest、哈希和 locator 绑定上游。在用或已封存产物以原数据根、schema 和绑定验证，复现检查使用新输出根。
- 会话 SQLite、LangGraph checkpoint、事件、证据回执和 Packet 分工见 [Generation 合同](../docs/phases/clean-generation-v1.md)。模型操作、预算、失败和 UNKNOWN 与原 campaign 绑定，留存结论与运行原始记录分开。
- 原 RU/词法基线、Phase04 的 70Q 比较、Phase05 导航、Chunk/召回/重排试验及 G0–G7 证据随对应报告查询。数据根上的日期和实验名称不代表采用状态，见[研究索引](../docs/research/README.md)。
- 既有历史档案的定位与早期数据目录说明见[历史数据登记](../docs/research/README.md#历史数据登记)。本轮治理保留当前历史产物及其原路径。
