# Current Phase

本页是当前阶段、运行选择、采用状态、授权和下一步的主入口。阶段合同定义接口和约束，研究文档记录具体日期的验证与结论。

## 当前工作与下一步

2026-11-23 第二批实现了 G5 证据利用策略 `gc-g5-official-5` 和 Windows 本地聊天接口修订号 4。新版 G5 可从已排序候选按页选读，按可核验位置保存父级正文，优先入包已选和主动回读的关键片段，并区分引用绑定、缺失和失败。查证面板可以分别核对引用和父级上下文。离线复验覆盖 G6/G7 固定排名、选读和组包。新版通过显式研究许可试用，工作台和本地聊天的新真实批次默认仍用 `gc-g5-official-4`。[Generation 合同](phases/clean-generation-v1.md#evidence-quality-profile) · [产品合同](phases/local-chat-v1.md) · [第二批复验记录](research/clean-generation-g5-evidence-evaluation-20261123.md)

四题真实配对的八侧均有运行记录，首轮候选顺序逐题一致，五侧交付正式回答。Q068 是唯一两侧均交付的题，试验侧支持的必要结论由 0/2 增至 1/2。试验侧另外三题的第二个请求均为远端结果未知。新登记批次占用 25 次，加上先前凭据被拒绝的 1 次，共 26/48 次。四题均可配对复核的采用门槛未达。历史 G6/G7 保持原记录。[配对结果与限制](research/clean-generation-g5-evidence-evaluation-20261123.md#配对实测与独立样本)

续轮专项审计已按封存请求定位到连接建立或请求发送阶段，底层网络原因仍待诊断。已补充传输诊断、修复截断正文判定、改进候选预览和证据压缩，并保护首包原已准入片段。当前 `gc-g5-official-5` 通过工程验证，继续作为显式试验。此前试验许可、记录及 UNKNOWN 占额保留原绑定。[续轮和成本审计](research/clean-generation-g5-targeted-audit-20261123.md)

全链路复核确认 Q068 的关键语音在 S0 第 6，经 BGE 相关性降至第 48，结构处理后回到第 43。局部离线试验把该语音送到第 6 并验证首包收录，代价是压后另一条故事证据。当前优先用独立样本验证定向排序的收益和代价，再决定是否进行排序真实配对。现用 S0、`bge_structural` 和正式索引继续采用。若开展证据策略的真实配对，需单独登记有限批次，固定正式上游顺序。[排序对照](research/clean-generation-g5-targeted-audit-20261123.md#排序干预对照)

第一批可靠性修复覆盖提交、调度、中断恢复、会话状态、接口错误、定向纠正和长会话上下文。历史工程验收复现了跨日续聊、220 轮历史后的追问和旧版数据根回读。仍兼容且有效的批次可以显式继续，不兼容的旧批次按原绑定只读，同一会话可开启新批次。[产品测试](../tests/local_chat/test_product.py) · [浏览器场景](../frontend/tests/chat.browser.spec.ts)

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
