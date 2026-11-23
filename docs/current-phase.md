# Current Phase

本页是当前阶段、运行选择、采用状态、授权和下一步的主入口。阶段合同定义接口和约束，研究文档记录具体日期的验证与结论。

## 当前工作与下一步

2026-11-23 完成 [Windows 本地聊天 V1](local-chat.md) 第一批可靠性修复，覆盖提交与调度、中断恢复、多会话状态、接口错误、纠正范围和长会话上下文。产品接口修订号为 3。兼容且仍有效的原批次可显式继续；升级后不兼容的旧批次保留为只读，可在同一会话开启新批次。恢复、草稿及上下文行为见[产品合同](phases/local-chat-v1.md)。六阶段算法、默认选择器及已封存 G7 审计沿用既有基线。

本批工程验收通过 165 项 Python 回归、13 项前端单测和 9 个本机 Edge 场景。隔离演练验证了次日批次到期后的同会话续聊、220 轮历史后的追问及旧版数据根升级后的答案、引文和账目回读。验收使用离线夹具和内存模型传输，本批未发起付费请求。真实剧情回答的语义验收仍需单独开启有限批次。[产品测试](../tests/local_chat/test_product.py) · [浏览器场景](../frontend/tests/chat.browser.spec.ts)

Windows 本地聊天入口已采用地脉等高线、星图印记和青金配色的界面。宽窗并排显示会话、对话和按需打开的查证面板，窄窗使用抽屉。新版静态页面随启动器交付，沿用 [V1 产品合同](phases/local-chat-v1.md)。

真实问答的 DeepSeek 官方地址与 API Key 可以在「批次设置」中保存。API Key 使用 Windows 当前账号加密，配置与会话、反馈和批次记录同属个人聊天数据根。页面显示本次启动实际使用的数据目录，付费派发仍需单独开启有限批次。启动器和页面检查本机后台版本。侧栏支持确认删除会话，应用隐藏该会话及关联反馈，保留本机审计材料。[本地聊天使用说明](local-chat.md)

仓库根目录保留 Windows 聊天启动器和项目入口。Python 依赖统一从 `requirements/` 读取，仓库内的测试暂存与旧根目录材料归入 `.local/`。[项目结构](project-structure.md#仓库根)

G7 运行分桶为 41 完成、14 有答案的部分完成、1 失败、14 UNKNOWN。55 个最终答案的单人非盲审计评为 43 正确、8 部分正确、1 不受证据支持的错误身份断言、3 证据不足；其余 15 题无法评价。43 个正确核心答案中有 12 个仍依赖汇编或考据，主要文本尚未独立闭环。审计判断与原始运行状态独立保存，费用和实际扣款保持原审计的不确定性。此结果属于狭义语料判断，真实剧情回答质量的产品验收仍开放。见 [G7 审计](research/clean-generation-g7-70q-quality-audit-20261117.md)。

## 已选定的本地链

| 阶段 | 已完成基线与当前采用状态 |
| --- | --- |
| Clean | 16,437 份 `mihoyo_obc` `zh-cn` 记录由 Raw 直接构建，保留原文、结构、未知项和 Raw 定位。 |
| Chunking | V2.1 balanced 微/宏双粒度，119,557 个唯一候选、214,870 个自然父级、5,087 组图；在用索引用已验证的扁平导出。 |
| Indexing | 固定输入包上的 BGE-M3 1024d Dense 和 BM25F 为选定索引。完整的 BGE-M3 Sparse 供显式 S2 使用。 |
| Retrieval | S0（BM25F + Dense，`f1_max`）是 Clean 本地链默认。S2 是冻结的显式研究选项，S1 和补充式 Sparse 结果保留为实验。 |
| Reranking | BGE reranker v2 m3 + 保守结构处理（`bge_structural`），MMR 关闭。`bge_relevance` 可显式使用同一评分，自动回退关闭。 |
| Generation | 独立工作台支持本地证据组装、持久化对话，原 Streamlit 工作台默认 Mock。个人聊天软件另有 Windows 入口，提供离线演示及显式开启有限批次的真实问答。G5/G6 研究配置选用 DeepSeek 官方 `deepseek-flash`。 |

[六阶段实现、配置和入口](rag-workbench.md) · [数据与索引绑定](../data/README.md) · [阶段验收与实验](research/README.md)

## 保留的独立路径

- Raw、Parsed、Canonical 的历史 Phase01–03 已关闭并保留原合同和来源数据。原 RU、词法索引和 Evidence Assembly 仍用于历史回读与复核。
- Legacy Phase04 的本地 Qwen3 encoder/index 已退役，代码里的旧 selector 和原有备用路线属于历史接口说明。独立的 Clean 工作台没有切换 Legacy backend 或 Streamlit 的默认入口。
- Phase05 NaviRAG、Card、Link、KG 暂停。已封存、失败及 UNKNOWN 证据保留，历史许可按原状态关闭或受原合同约束。

## 文档导航

| 职责 | 主文档 |
| --- | --- |
| 项目入口与工程规则 | [README](../README.md) · [AGENTS](../AGENTS.md) |
| 层职责、边界和证据流 | [架构](architecture-overview.md) |
| 代码、配置、锁和测试布局 | [项目结构](project-structure.md) |
| 长期阶段方向 | [路线图](roadmap.md) |
| 外部模型接口、协议、凭据位置 | [模型接口目录](model-interfaces.md) |
| 代价高、难逆转的技术决策 | [ADR](decisions/README.md) |
| 固定档案、实验结论和复现依据 | [研究索引](research/README.md) |

### 阶段合同

- [Raw 采集](phases/phase-01-raw-collection.md) · [Clean V1](phases/clean-v1.md) · [Chunking V2/V2.1](phases/chunking-v2.md)
- [统一索引输入](phases/indexing-input-v1.md) · [物理索引构建与独立查询](phases/index-benchmark-v1.md) · [Sparse 构建](phases/sparse-build-v1.md)
- [Clean Retrieval](phases/clean-retrieval-v1.md) · [冻结召回比较](phases/recall-experiment-v1.md) · [Clean Reranking](phases/clean-reranking-v1.md) · [Clean Generation](phases/clean-generation-v1.md)
- [个人聊天 V1 产品合同](phases/local-chat-v1.md) · [Windows 使用与开发说明](local-chat.md)
- [Legacy Parsed](phases/phase-02-parsed-schema.md) · [Legacy Canonical](phases/phase-03-canonical-schema.md) · [Legacy Phase04 RAG](phases/phase-04-retrieval-evidence-assembly.md) · [暂停的 Phase05](phases/phase-05-source-bound-semantic-navigation.md)
