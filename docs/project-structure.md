# Project Structure

本页说明目录和包的职责。[当前阶段](current-phase.md)维护采用状态，[工作台](rag-workbench.md)维护运行入口，[数据目录](../data/README.md)维护磁盘路径和资产绑定。

## 仓库根

| 路径 | 内容 |
| --- | --- |
| `README.md`、`AGENTS.md` | 项目入口和仓库工程规则。 |
| `src/genshin_corpus/` | Python 包，运行时通过 `PYTHONPATH=src` 导入。 |
| `Start-GC-Chat.cmd`、`frontend/` | Windows 聊天启动器，Vue 3 / TypeScript 页面、产品契约快照、前端操作与浏览器测试。编译页面随产品包交付。 |
| `config/` | 版本化运行选择器、显式实验配置和安全样例。实验计划的绑定以其报告和 manifest 为准。 |
| `requirements-*` | 各隔离运行时的依赖或锁。历史依赖文件照原实验保留。 |
| `scripts/retrieval/` | 版本化的构建、迁移、对照及交接 PowerShell 入口，以及有限研究 helper。 |
| `tests/` | 夹具测试、离线回放和本地 UI 测试。小型净化数据位于 `tests/fixtures/` 和各子套件。 |
| `data/` | Raw 证据、派生产物、运行历史及封存回执。 |
| `docs/` | 层架构、阶段合同、技术决策、运营指南和日期研究。依据[文档导航](current-phase.md#文档导航)选择事实主文档。 |
| `.local/`、`logs/`、`tmp*/` | Git 忽略的本地环境、私有输入、运行日志与测试暂存。历史日志或一次性复核脚本可能是唯一证据。 |

## 源码包

路径均相对 `src/genshin_corpus/`。

| 路径 | 职责 |
| --- | --- |
| `collector/` | 来源发现、采集、Raw 存储、恢复与运行清单。 |
| `clean/` | 直接 Raw-to-Clean 提取、持久化、版本化 locator 和回读接口。 |
| `chunking/` | Clean 准入、微/宏投影、图与条件字段、自然父级回读。 |
| `indexing/` | 统一输入、analyzer、表示绑定及独立离线构建与验证。 |
| `retrieval/clean_based/` | Clean 候选检索与融合、用独立 worker 打分、排序与完整 handoff；V2 运行时由工作台接入。 |
| `generation/` | Evidence Packet 组装、provider-neutral 生成合同、协议检查、结果及引用绑定。 |
| `rag/`、`ui/` | 工作台与保留的 Legacy 编排、持久化和本地 Streamlit 页面。UI 通过后端进入工作流程。 |
| `local_chat/` | 个人聊天软件的 Starlette 接口、会话与批次存储适配、本机凭据、后台执行入口和 `static/` 页面。使用原有 RAG、证据校验和模型调用账本。 |
| `model_calls.py`、`model_call_sessions.py`、`evidence_io.py` | 共享请求计数与恢复、会话侧操作桥接、create-only JSON/哈希/封存。 |
| `build_runtime.py`、`runtime_compatibility.py`、`runtime_migration.py` | 并行构建运行时和解释器迁移/索引兼容性校验。 |
| `parser/`、`canonical/`、`retrieval/` 下的原 RU 实现 | 保留 Legacy Parsed/Canonical/RU、词法与旧 Assembly 接口，参阅 [Phase04 合同](phases/phase-04-retrieval-evidence-assembly.md)。 |
| `rag/navi_*`、`retrieval/navi_*`、`retrieval/navi_offline/` | 暂停的 Phase05 工具、候选生成、实验及回放。实际范围见 [Phase05 合同](phases/phase-05-source-bound-semantic-navigation.md)与[历史索引](research/README.md#phase05-语义导航)。 |

运行选择器、版本名、绝对路径及封存哈希是数据接口的一部分。样本、缓存和冻结结果应按依赖区分。治理前详细的历史模块清单可用 `git show 9041148:docs/project-structure.md` 查阅。
