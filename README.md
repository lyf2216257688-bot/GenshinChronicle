# GenshinChronicle

本项目构建可追溯的《原神》官方文本研究语料和本地 RAG 工作台。六阶段主线从不可变的 Raw 证据开始，经 Clean、Chunking、Indexing、Retrieval、Reranking 到 Generation。

长期方向是可由不同 Agent 调用的专业知识引擎。独立应用与正式问答基线继续提供服务。MCP 提供显式启用的实时检索和冻结回放入口。[架构决定](docs/decisions/adr-0002-host-neutral-evidence-engine.md) · [当前验收状态](docs/current-phase.md)

[当前状态](docs/current-phase.md)记录当前采用项、授权和下一步。[RAG 工作台](docs/rag-workbench.md)列出六阶段实现、配置、数据绑定、离线入口及运行环境。

## 从哪里开始

| 任务 | 入口 |
| --- | --- |
| 开始本机聊天、找回上次的讨论 | 双击 [Start-GC-Chat.cmd](Start-GC-Chat.cmd)，见[聊天使用说明](docs/local-chat.md) |
| 本地问题到可核读的 Evidence Packet | [S0 本地查询](docs/rag-workbench.md#离线运行) |
| 通过 MCP 核对冻结题的排名、原文和引用 | [离线试点合同与启动方法](docs/phases/mcp-offline-v1.md) |
| 通过 MCP 提问、自由补查、回读和核对引用 | [实时合同与宿主配置](docs/phases/mcp-live-v1.md) |
| 检查结果、证据和历史评估 | [已交付成果与实验索引](docs/research/README.md) |
| 修改或扩展一个阶段 | [工作规则](AGENTS.md)、[架构](docs/architecture-overview.md)、[阶段合同](docs/current-phase.md#阶段合同) |
| 查找代码、测试和本地资产 | [项目结构](docs/project-structure.md)、[数据目录](data/README.md) |
| 查询模型接口或凭据来源 | [模型接口目录](docs/model-interfaces.md) |

Clone 取得源码、配置、合同和小型测试夹具。完整语料、索引、模型与 Python 运行环境需要本地资产，见[运行前提](docs/rag-workbench.md#运行前提)。访问凭据由本地运行环境提供，未脱敏的浏览器捕获保存在 Git 忽略的 `.local/`。

Legacy Phase04 的 Qwen3 本地 Serving 已退役。其原 RU、Assembly、模型接口和历史结果仍按[旧链合同](docs/phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract)保存。暂停的 NaviRAG、Card、Link 和 KG 工作从 [Phase05 研究索引](docs/research/README.md#phase05-语义导航)查阅。
