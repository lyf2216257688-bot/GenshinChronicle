# GenshinChronicle 代码架构审计

审计日期为 2026-11-23。源码基线为 `main` 的 `a3af06793d5593ca13bf6ea87a4c4c2ef925501f`（`Add chat deletion and runtime version checks`）。报告面向后续工程取舍，与同目录的业务逻辑审计独立。项目的采用状态和授权以当前阶段及各阶段合同为准。 [当前阶段](../current-phase.md) · [架构](../architecture-overview.md) · [本地聊天 V1 合同](../phases/local-chat-v1.md)

## 1. 审计结论

GC 的核心分层应保留。主链把 Raw 证据、可检索表示、候选、排序、Evidence Packet 和答案分开，关键产物通常有版本、哈希、完成标记和原文回读。Windows 聊天通过同源产品 API 复用 Generation 引擎，其会话、批次和反馈有独立的数据根。保存的答案及引用仍可回到具体轮次和证据包。 [架构职责](../architecture-overview.md#primary-evidence-and-the-clean-based-path) · [索引输入合同](../phases/indexing-input-v1.md) · [产品引用读模型](../../src/genshin_corpus/local_chat/presentation.py#L163)

**优先修复本地聊天的三个故障窗口。** 已提交轮次可能因 SQL 提交后的收尾失败而阻塞队列。G5 在本地检索产物只写了一部分时，显式继续会再次命中未封存目录，初始检索的这类故障还能关闭共享批次。前端重命名的迟到响应能把当前会话标题切回旧会话，编辑器和草稿仍归新会话。三者均有离线复证。Raw 采集另有违反不可变约束的续跑路径，应在重新启用采集前修复。具体影响、验证范围及建议见下表和第 4 节。

| 编号 | 优先级 | 已确认的缺陷 | 影响范围 |
| --- | --- | --- | --- |
| D01 | P1 | Raw 续跑可替换同一定位上的响应字节，且同一 run 可跨来源配置误用缓存 | 保留采集入口，重启采集前 |
| D02 | P2 | Collector 的部分失败清单仍对应 CLI 成功退出码 | 保留采集入口及脚本调用者 |
| D03 | P1 | 未封存本地查询没有可用的显式恢复路径 | 本地聊天 LIVE 的 G5；G4 LIVE 也有相同目录判断 |
| D04 | P1 | 已提交轮次未进入内存调度时会卡住后续队列 | 本地聊天 MOCK 与 LIVE |
| D05 | P1 | 重命名响应可串写前端的当前会话 | 本地聊天界面 |
| D06 | P2 | SQLite 错误可绕过约定的 API 错误格式 | 本地聊天界面及 API 调用者 |
| D07 | P2 | 真问答许可的代码绑定漏掉实际使用的上游回读实现 | 本地聊天 LIVE，代码升级后 |

P1 表示可能损害证据可追溯性、阻塞工作或让界面误示会话身份，按入口的使用时点处理。P2 表示有明确触发条件的契约缺口。第 5 节是维护和扩展风险，第 6 节的优化须由使用需求或测量结果触发。复证使用合成数据。当前已封存 Raw、历史问答和用户会话是否曾触发这些故障，未作历史追查。

## 2. 范围与方法

- 对 Git 跟踪的 Python 源码做文件清点和 AST 导入分析。沿各包的公开入口、核心合同、数据读写、进程职责和测试核查。前端覆盖 API 客户端、生成类型、会话状态、组件操作、静态构建、Windows 启动器和浏览器场景。
- 按采用状态审阅四类运行路径。Clean 六阶段是采用的本地研究链，Windows 聊天是独立产品入口，Phase02–04 的 Parsed/Canonical/RU 和原 Streamlit 按历史合同保留，Phase05 的 NaviRAG、Card、Link 与 KG 留供回读和复现。判断依赖走向和失败传播时，以调用入口能到达的代码为准。 [当前阶段](../current-phase.md) · [工作台运行入口](../rag-workbench.md) · [历史研究索引](../research/README.md)
- 将结论分为**已确认缺陷**、**潜在风险**和**可选优化**。缺陷以可达代码路径与合同差异为依据，重点场景增加离线复证。AST 涵盖所有跟踪的 Python 模块，人工深读按入口、边界和高风险实现展开。历史模块采用同样的结构盘点及代表性路径抽查。
- 采用磁盘上现有的 Windows CPython 3.14.5 和 Node 24。用小夹具、临时数据根、内存传输和测试服务验证。全库 Raw/索引重建、GPU 真查询、模型回答质量和计费均不在本轮验证范围。2026-11-17 的 G7 语义审计继续适用其原样本和评价口径。 [G7 质量审计](../research/clean-generation-g7-70q-quality-audit-20261117.md)

## 3. 代码组织、数据流与依赖

### 3.1 各层的流向与检查结果

| 模块或入口 | 输入、输出及职责 | 审计判断 |
| --- | --- | --- |
| Raw Collector | 官方频道树、列表、详情 → Raw 字节、请求元数据、成员关系、失败记录和 run manifest。经已核实的 `content_id` 去重，locale 可配置。 | 采集接口可替换传输，离线测试可覆盖续跑。相同路径的证据与可更新清单共用替换型写入，且运行身份绑定不足，见 D01、D02。 [采集合同](../phases/phase-01-raw-collection.md) · [Collector](../../src/genshin_corpus/collector/collector.py#L103) |
| Clean | `RawRun` 验证 Raw 清单和响应哈希后生成逐记录结构、原文及 locator。`OriginResolver` 和独立验证器核对来源值、结构和未知项。 | 主链确实从 Raw 直接生成。少量 HTML、布局和对白解析复用旧 Parser 的实现，存在维护耦合，见 R02。 [Clean 构建](../../src/genshin_corpus/clean/pipeline.py#L42) · [定位回读](../../src/genshin_corpus/clean/reader.py#L107) · [独立校验](../../src/genshin_corpus/clean/validation.py#L73) |
| Chunking V2.1 | Clean 记录 → 准入结果、微宏候选、自然父级、图、分片回执与扁平导出。定位、条件、显示顺序与检索投影分别保存。 | 并行按记录完成，校验源文件及构建身份，扁平清单最后发布。文件名沿用 `pilot`，激活版本依清单 schema、准入策略和选择器识别。 [合同](../phases/chunking-v2.md) · [构建与续跑](../../src/genshin_corpus/chunking/pilot.py#L348) |
| Indexing | Chunk/Clean 已验证导出 → 同一 `input_bundle_id` 下的输入行、微宏映射和 BM25F/Dense/Sparse 表示。独立读回验证行、文档哈希、模型空间和分片回执。 | 统一输入维护候选与原文的关系。构建工具与 Retrieval/旧模型脚本有反向依赖，运行期读入成本见 R02、R03、R05。 [输入打包](../../src/genshin_corpus/indexing/inputs.py#L219) · [表示身份](../../src/genshin_corpus/indexing/schema.py#L89) · [Sparse 完成检查](../../src/genshin_corpus/indexing/sparse_full.py#L191) |
| Retrieval | S0 串接 BM25F 和 Dense，冻结的 S2 额外接 Sparse。工作进程执行检索，校验返回的查询、候选和表示身份，并保留原始命中及融合轨迹。 | V1 独立召回与 V2 工作台是不同入口。默认链选择与研究配置应明确标注，内部 worker 传输与资源管理可在有具体维护需求时收敛。 [召回合同](../phases/clean-retrieval-v1.md) · [V2 编排](../../src/genshin_corpus/retrieval/clean_based/runtime_v2.py#L124) |
| Reranking | 完整候选集和已绑定评分 → 有序候选。当前使用 BGE v2 m3 的 `bge_structural`，保留原始候选与结构调整记录，另有显式 relevance-only 版本。 | 评分、排序和 P4 冻结交接有独立验证，Generation 仍须回读。物理目录共用 `retrieval/clean_based`，现有交接合同足以表明排序职责。 [排序合同](../phases/clean-reranking-v1.md#selected-candidate-interface-and-p4-handoff) · [排序接口](../../src/genshin_corpus/retrieval/clean_based/rerank_handoff.py#L46) |
| Generation / Workbench | 排序 → 已验证的读回回执及 Evidence Packet → G4 固定链或 G5 有界查证 → 模型调用账本 → 已发布回答、引用及审计文件。 | 证据组装核对定位与内容，预算取舍保留 disposition；模型只能按允许的工具及最终证据进行生成。旧答复和模型查证内容沿派生链存放。G4、G5 的恢复及编排共享方式需要明确边界，见 D03、D07、R03、R04。 [证据回读](../../src/genshin_corpus/generation/clean_evidence.py#L39) · [组装](../../src/genshin_corpus/generation/clean_evidence.py#L154) · [G5 合同](../phases/clean-generation-v1.md#g5-bounded-active-research-contract) |
| Windows 本地聊天 / 前端 | 启动器 → 回环 Starlette → Vue/TypeScript 静态页面与 `/api/v1` → 产品目录、G4 MOCK/G5 LIVE 子进程、按轮绑定的批次和证据。 | API 路由与类型由同一契约生成。正文、引用与进度的呈现边界明确。SQL、文件、队列和浏览器状态在异常及交错响应下存在不一致，见 D04–D06。 [启动器](../../Start-GC-Chat.cmd) · [API](../../src/genshin_corpus/local_chat/api.py#L193) · [前端状态](../../frontend/src/chat/state.ts#L22) |
| Legacy Parsed / Canonical / RU 与原 Streamlit | Raw → Parsed 的源结构及 RawRef → Canonical 保留来源/投影血缘 → RU 与旧词法/证据组装 → 历史后端。原 Streamlit 与 Legacy CLI 使用同一运行选项。 | 历史读回有独立合同。Canonical 的解码需了解 Parsed 内部类型，跨阶段兼容依靠原版本与回放。旧 `DEFAULT_EMBEDDING_PROFILE` 仍存在于保留路径，Clean 默认选择由另一组配置决定。 [Parsed 管线](../../src/genshin_corpus/parser/obc/pipeline.py) · [Canonical 解码](../../src/genshin_corpus/canonical/pipeline.py#L214) · [旧后端](../../src/genshin_corpus/rag/backend.py#L399) · [Legacy 选择器](../../src/genshin_corpus/rag/config.py#L17) |
| Phase05 / 研究工具 | 历史 RU/Canonical → 层级候选、Card/Link、导航轨迹及独立回放。各版本各自绑定前置材料、协议及有限许可。 | 保留历史入口和检查点有复现价值。顶层包导入范围较大，部分历史测试依赖未跟踪产物，见 R01、R06。现有工作暂停，代码中出现执行开关与本轮授权无关。 [Phase05 合同](../phases/phase-05-source-bound-semantic-navigation.md) · [离线命令](../../src/genshin_corpus/retrieval/navi_offline/__main__.py) · [历史请求桥接](../../src/genshin_corpus/retrieval/navi_offline/requests.py#L389) |

### 3.2 结构盘点

基线共 734 个跟踪文件。`src/genshin_corpus` 有 276 个 Python 文件、93,462 个物理行（含空行和注释），`tests` 有 157 个 Python 文件，其中 143 个文件名为 `test_*.py`。前端有 15 个手写 TS、Vue 与 CSS 源文件，另有生成类型和提交的静态构建产物。`retrieval` 及两级子包共 148 个 Python 文件、56,484 行，其中 `clean_based` 为 23 个文件、3,485 行。`rag` 为 38 个文件、17,877 行。体量主要集中在同时承载开发、运行和历史复现的两个包。文件长度和体量仅用于确定审阅范围。

相对导入解析到模块，函数内导入也计入依赖。按一级包聚合，并单列 `parser.obc`、`retrieval.clean_based`、`retrieval.navi_offline`。此口径下有 **9 组多模块强连通分量**，其中 Clean 编排的一组含 8 个模块。仅保留模块层面的静态导入语句后，未发现多模块环。另有 **20 处跨上述包组的下划线命名导入**。这些数字用于定位双向依赖和变更面。动态导入、进程启动路径和文件协议另外按入口核查。

有实质影响的反向依赖包括 Indexing 与 Clean Retrieval 互相借用输入读写和索引选择，实现协议的 Generation 运输层使用 `rag.clean_store.put`，会话存储为历史读取反向调用图层的 `verify_sources`。现有局部导入规避了直接的加载顺序问题，责任重叠仍会放大修改和复验范围。 [Indexing 的 Sparse 读取](../../src/genshin_corpus/indexing/sparse_full.py#L238) · [Retrieval 的索引输入依赖](../../src/genshin_corpus/retrieval/clean_based/source.py#L8) · [生成运输层](../../src/genshin_corpus/generation/clean_chat_transport.py#L10) · [工作台会话存储](../../src/genshin_corpus/rag/clean_conversation.py#L117)

### 3.3 状态与恢复的归属

| 状态 | 持久化位置和读取边界 | 合同要求 |
| --- | --- | --- |
| 构建与导出 | 各阶段 run manifest、按记录或分片的回执、源哈希 | 已完成范围可核验和复用。部分产物的存在不构成完成。 [Clean 构建](../../src/genshin_corpus/clean/pipeline.py#L265) · [Sparse 续跑](../../src/genshin_corpus/indexing/sparse_full.py#L105) |
| 产品会话与执行 | `sessions.sqlite` 的轮次、草稿、作业、事件索引；当前执行器的 `pending/active` | 提交令牌确定轮次，草稿 revision 处理并发。进程重开后旧工作等待显式继续。数据库与调度之间的窗口见 D04。 [产品提交](../../src/genshin_corpus/local_chat/store.py#L144) · [执行器](../../src/genshin_corpus/local_chat/worker.py#L20) |
| 研究、尝试与账目 | 批次目录内的 `checkpoints.sqlite`、稳定 operation、Raw 尝试、请求额度和完成回执 | 按原检查点、原许可和原操作回放。无法确认的远端结果占用原额度。状态展示的推断应与原账本一致。 [G5 检查点](../../src/genshin_corpus/rag/clean_research.py#L656) · [会话调用桥接](../../src/genshin_corpus/model_call_sessions.py#L97) · [共享模型账本](../../src/genshin_corpus/model_calls.py#L268) |
| 结果与引用 | 不可变的结果、Packet 和消息文件按轮次发布。SQLite 引用带路径与哈希。 | 先写结果，再发布可见消息。答案、部分回答、流式草稿和故障各有独立表示。引用以轮次、Packet 和 E 编号共同定位。 [发布顺序](../../src/genshin_corpus/rag/clean_conversation_graph.py#L117) · [产品引用](../../src/genshin_corpus/local_chat/presentation.py#L163) |
| 浏览器状态 | REST 初始化会话与草稿，SSE 按持久化游标读取进度，终态再取轮次详情。浏览器存储保留会话指针和暂存草稿。 | 显示层按会话身份和轮次合并状态。访问进度和重开页面只读取已存在的工作。 [API 进度流](../../src/genshin_corpus/local_chat/api.py#L165) · [浏览器进度合并](../../frontend/src/chat/state.ts#L81) |

## 4. 已确认的缺陷

### D01 · P1 · Raw 续跑复用证据路径

`RunStore` 按 `source / locale / run_id / kind / key` 定位响应。当元数据缺失、格式不合格或校验失败时，`valid_response()` 返回无可复用记录。Collector 随即按同一键取新响应，`write_response()` 以 `os.replace` 写回同一个 Raw 路径。`Collector.__init__` 同时用新的部分清单替换原清单，恢复入口没有核对既存 run 的完整请求范围。现有测试还把损坏后重新获取作为通过条件。 [Collector 构造与恢复](../../src/genshin_corpus/collector/collector.py#L42) · [响应存储](../../src/genshin_corpus/collector/storage.py#L62) · [续跑测试](../../tests/collector/test_collector.py#L118) · [Raw 约束](../../AGENTS.md#hard-constraints)

**离线复证。** 用已有 Raw 夹具建临时 run，移除一份详情的元数据，替身传输返回仅多一个换行的响应。同一 locator 的正文与哈希发生变化。另将 `base_url` 从合成 A 切换到合成 B，使用原 run id，观察到零次替身请求且清单仍 complete。这两条路径分别破坏原证据定位和运行范围的可辨识性。

**建议与验收。** 区分未完成 attempt 与已接收 Raw 观察。让响应字节只追加写入，用独立回执指向可用 attempt。已完成 run 的清单和请求范围须核验后只读复用。局部失败可在新 attempt 或新 run 记录原由，已存在的响应继续可定位。同一运行的根、来源端点、locale 与请求合同不匹配时明确拒绝混用。补旁车缺失、哈希冲突和同根双启动的夹具测试，证明旧字节和旧定位仍可复核。按采集阶段单独排期。

### D02 · P2 · CLI 丢弃采集完成状态

`Collector._finalize()` 能通过 manifest 表明 `partial`，但 CLI 调用 `run()` 后固定返回 `0`。在合成 transport 抛出 HTTP 503 错误的测试中，得到 `partial` 清单和退出码 `0`。只看进程返回值的脚本可能推进下一阶段，Clean 虽然会再次检查清单，出错位置和通知已与采集实际结果脱节。 [采集完成判定](../../src/genshin_corpus/collector/collector.py#L337) · [CLI](../../src/genshin_corpus/collector/__main__.py#L27) · [Clean 入口校验](../../src/genshin_corpus/clean/pipeline.py#L42)

**建议与验收。** 将完成状态映射成文档化的退出码和可机读摘要。分别覆盖主动只做发现、按合同执行有限样本、运行失败和完整交付的返回值。区分完成了本次选择范围与已构成可供全量下游使用的完整输入。

### D03 · P1 · 本地查询中断会堵住同轮继续

LIVE 的 `ResearchPipeline.run()` 以路径存在作为重放分支，直接读取 `complete.json` 校验目录。V2 在启动 worker 前创建输出目录，完成封存发生在整次查询结束后。若在两者之间关停，继续该轮会落在同一路径，缺失完成标记时反复抛错。G5 初始查询向上传播 `FileNotFoundError` 时，有处理分支把它归为 `integrity_fault` 并封闭整个 LIVE 批次，其他会话可受影响。G4 的 `LivePipeline` 也按目录是否存在进入重放，原合同明确保留部分目录并阻止同路径自动重算。 [G5 查询落盘](../../src/genshin_corpus/rag/clean_research_live.py#L227) · [V2 目录分配](../../src/genshin_corpus/retrieval/clean_based/runtime_v2.py#L116) · [G5 初始查询](../../src/genshin_corpus/rag/clean_research.py#L383) · [G5 关批分支](../../src/genshin_corpus/rag/clean_research.py#L698) · [G4 合同](../phases/clean-generation-v1.md#L510)

**离线复证。** 用一个尚无完成标记的临时查询目录对应 G5 和 G4 各自的预期路径。两条路径连续两次均因缺少 `complete.json` 失败，未启动 worker。G5 关批后果来自源码控制流核查。

**建议与验收。** 将已封存查询、可恢复的未提交本地 attempt、已提交但校验失败三种情形映射为明确结果。保留旧 attempt，用户显式继续且原许可满足条件时，在新 attempt 目录完成相同逻辑查询。对已发布记录的篡改继续阻断。验证 worker 创建目录后退出、写完排序后尚未封存，以及已封存的重放。每种情况下核对原轮次、原账本和已发模型请求数，禁止以新模型操作替代 UNKNOWN。

### D04 · P1 · SQL 入库与执行队列之间缺少恢复衔接

`submit_draft()` 先将轮次、提交和 `queued` 作业一并提交到 SQLite，再补用户消息与 `g4-submitted` 事件。API 仅对 `fresh=True` 的返回调用 `schedule()`。若补写失败，原提交令牌能找回该轮，调度动作却被跳过。调度器只挑内存 `pending` 中匹配数据库最早非取消作业的轮次。留在数据库首位的孤立 `queued` 作业因此可以挡住其他会话。重启恢复逻辑能补消息和事件，并将旧作业挂起。 [提交与补写](../../src/genshin_corpus/local_chat/store.py#L144) · [API 调度](../../src/genshin_corpus/local_chat/api.py#L106) · [调度资格](../../src/genshin_corpus/rag/clean_executor.py#L62) · [执行器启动修复](../../src/genshin_corpus/local_chat/worker.py#L20)

**离线复证。** 通过 Starlette 测试客户端在 SQL 提交之后注入一次 `OSError`。首次提交返回 409。同令牌重发返回 200 和同一轮，状态 `queued`、`watching=false`。第二个会话可以提交，但其轮次排队且没有 worker 调用。单独对首轮点击继续的 API 可接受请求。复证替换了 worker 启动，因此没有实际模型请求。

**建议与验收。** 让一处持久化作业状态承担领取资格。可采用同事务作业记录加受控领取，或为已提交但未入队的状态提供幂等补写及明确停车。区分当前进程内的提交收尾修复与重启后的旧任务。补 SQL 提交后、事件写完后、入队前及同令牌重发的故障注入，要求后续轮次继续推进、原轮可见、只有明确授权的轮次会派发。

### D05 · P1 · 重命名的迟到响应造成会话错配

`open()` 对加载结果使用代际检查。`rename()` 等待 API 后直接赋值 `current`，没核对发出请求时的会话 ID 和代际。重命名期间可切换侧栏会话。若 A 的重命名响应在 B 已加载后返回，会话头部显示 A，而 B 的消息、URL 和 `DraftEditor` 仍在使用中。用户此时可能误判草稿或发送的归属。 [会话切换](../../frontend/src/chat/state.ts#L177) · [重命名](../../frontend/src/chat/state.ts#L336) · [界面操作入口](../../frontend/src/App.vue#L111) · [保存草稿目标](../../frontend/src/chat/state.ts#L143)

**离线复证。** 用 Vue 状态组合函数和可控 Promise。先重命名 A，切到 B，再释放 A 的响应。读取到 `current=A`、URL/历史/编辑器为 B，随后草稿保存实际调用 B。

**建议与验收。** 重命名用返回的会话 ID 更新列表项，只在会话仍为当前操作目标时更新标题和面板。让会话级异步操作统一校验目标与代际，覆盖重命名、反馈跳转、刷新，以及交错的 HTTP 与进度流终态。测试断言 URL、标题、消息、编辑器和实际命令目标一致。

### D06 · P2 · 异常响应跨越了 JSON 契约

OpenAPI 对声明的接口给出 JSON `Error` 作为默认错误。路由注册处理了 `Problem`、`ValueError`、`OSError` 和 `KeyError`，SQLite 的 `OperationalError` 会走 Starlette 的非 JSON 错误路径。前端在检查状态码前调用 `response.json()`，于是解析异常可以遮住原状态和可用恢复信息。SSE 的故障和连接失败也有独立路径，客户端需能恢复到持久化视图。 [OpenAPI 生成](../../src/genshin_corpus/local_chat/contract.py#L233) · [异常映射](../../src/genshin_corpus/local_chat/api.py#L270) · [HTTP 客户端](../../frontend/src/api/client.ts#L27) · [SSE 处理](../../frontend/src/chat/state.ts#L81)

**离线复证。** 对 `GET /api/v1/chats` 注入 SQLite `OperationalError`，实际得到 `500 text/plain`。该路由生成的默认错误媒体类型是 `application/json`。

**建议与验收。** 将暂时占用、缺失、校验失败和程序错误映射为稳定错误码及适当的 HTTP 状态，服务日志保留可核查的位置和失败类型。前端兼容空/非 JSON 响应，SSE 失败后可通过 REST 核实当前轮的持久状态。覆盖目录损坏、SQLite 占用、旧端点以及连接中断后的界面提示。

### D07 · P2 · LIVE 许可的代码身份覆盖不完整

本地聊天合同要求执行适配、路由配置或上游代码变更后将旧真实批次置为只读。实际身份是若干固定文件清单的组合。`EvidenceReader.read()` 在 `natural_parent/graph` 路径通过 `InputReader.read_natural_parent()` 调用 `chunking/reader.py`，自然父级内容可以参与查证和证据组装。这个文件不在产品 adapter、G4/G5 或 V2 上游的代码摘要清单中。改变它的执行行为不会单独触发产品批次的代码身份失配。已有的文件哈希、回读及每次请求前校验仍各自发挥作用。该结论来自实际引用链与摘要清单的比对，未更改任何实现，也未开启 LIVE 批次。 [产品合同](../phases/local-chat-v1.md#接口与恢复约定) · [批次身份检查](../../src/genshin_corpus/local_chat/batches.py#L69) · [G5 身份](../../src/genshin_corpus/rag/clean_research.py#L82) · [上游文件清单](../../src/genshin_corpus/retrieval/clean_based/runtime_v2.py#L23) · [自然父级读取](../../src/genshin_corpus/retrieval/clean_based/source.py#L153) · [证据使用](../../src/genshin_corpus/generation/clean_evidence.py#L89)

**建议与验收。** 由工作点维护一份版本化的执行身份合同，明确哪些文件、运行时和资产影响查证、排序、证据与请求行为，让许可签发和派发校验共用。以自然父级读取为首个变更用例，验证旧批次禁止派发，新批次经显式许可运行，已保存历史保持只读可核验。身份切换宜为新许可版本，保留冻结回执原有算法。

## 5. 潜在风险与维护债务

### R01 · 活动主链加载了宽泛的历史导出

`retrieval/__init__.py` 在导入 Clean 子包时也加载 Legacy RU、组装、Qwen 和 Phase05 语义相关模块。`rag/__init__.py` 同样汇集研究和历史接口。两个包合计约占 Python 源码物理行的八成，其中包括活跃实现。变更历史模块的依赖或顶层初始化时，可能影响新入口启动。建议先固定公开 import 兼容性和当前入口的加载边界。若修改确实被耦合拖累，再渐进式缩小顶层加载，旧入口保留兼容转发。不要以目录搬迁作为阶段验收目标。 [Retrieval 导出](../../src/genshin_corpus/retrieval/__init__.py#L9) · [RAG 导出](../../src/genshin_corpus/rag/__init__.py#L3) · [保留接口](../architecture-overview.md#preserved-interfaces)

### R02 · 通用基础设施依附在具体阶段和模型

Clean 直接使用 Parser 的私有 `_TreeParser`、`_layout_observations`。Clean Retrieval 使用 Indexing 的私有 `_read_locator`，Indexing 的 Sparse 代码又从 Retrieval 取 `FileBinding`、`InputReader`。进程、锁、离线保护及电源管理的复用跨过 `benchmark_io.py` 和已经退役的 `local_qwen3_build.py`。这些复用减少了重复代码，调用者也继承了被依赖模块的变更范围。建议先将已有的字节定位、回执验证和进程生命周期划定公开接口，按实际变更需求提取同语义实现。可更新清单与构建进度的原子替换，以及证据的只追加写入要保留各自的写入语义。以小夹具校验定位、Windows 子进程退出和锁释放，避免用统一 helper 改变冻结产物的字节合同。 [Clean 标记读取](../../src/genshin_corpus/clean/markup.py#L8) · [Clean OBC 适配](../../src/genshin_corpus/clean/obc.py#L9) · [索引定位](../../src/genshin_corpus/indexing/inputs.py#L29) · [工作进程基础设施](../../src/genshin_corpus/indexing/benchmark_io.py#L17) · [证据写入](../../src/genshin_corpus/evidence_io.py#L38)

### R03 · 多个运行包装对同类失败有不同语义

Retrieval V1 工人使用阻塞 `readline()`，V2 通过有时限的读取和 Windows Job 管理。G4、G5 各自管理本地查询目录与回执，Streamlit 执行器和产品执行器分别组装子进程命令、终止流程和日志。V2 的工人超时、G5 单轮时限、产品子进程外部时限属于不同层。长耗时情况下，停止动作要等管线到可取消边界才可能完成。建议先统一受支持路径的失败类别、截止时间传递和取消信号，再提取重复的进程监督及查询封存代码。保持 V1/P4 已定输出及 Standalone CLI 的合同，只有明确还在维护的路径才纳入共用实现。 [V1 Worker](../../src/genshin_corpus/retrieval/clean_based/runtime.py#L35) · [V2 JsonWorker](../../src/genshin_corpus/retrieval/clean_based/runtime_v2.py#L49) · [工作台子进程](../../src/genshin_corpus/rag/clean_executor.py#L87) · [产品子进程](../../src/genshin_corpus/local_chat/worker.py#L42)

### R04 · 产品状态投影耦合引擎表、文件和账本内部结构

`ProductStore` 继承 `ConversationStore` 并直接访问引擎表。`ExecutionView` 用 `__getattr__` 透传其余操作，把批次根与共享会话目录组合给引擎。`turn_view` 汇总 SQL 作业、内存调度、已发布结果及 `uncertain()` 的文件扫描，分别维护产品、引擎和账本视角。`LedgerReader` 通过继承并填充内部字段获得只读核算。产品数据根有 marker 和记录版本，当前 DDL 以 `CREATE TABLE IF NOT EXISTS` 扩充，重大表结构变更还缺显式迁移入口。建议首先在修 D04 和 D06 时定义提交、读取视图、账本只读状态三个窄接口，统一读出作业、答复与远端请求状态。对将来的结构变更再加可验证的 schema 迁移和旧目录打开用例。保留历次原始请求、结算和拒绝重发的凭据。 [产品 DDL](../../src/genshin_corpus/local_chat/store.py#L43) · [底层 DDL 与事务](../../src/genshin_corpus/rag/clean_store.py#L23) · [批次执行视图与核算](../../src/genshin_corpus/local_chat/batches.py#L23) · [产品轮次投影](../../src/genshin_corpus/local_chat/presentation.py#L93) · [共享恢复](../../src/genshin_corpus/model_call_sessions.py#L97)

### R05 · 冷启动 I/O 与长会话读放大

对在用索引的 `inputs.jsonl` 只查看文件元数据，测得 1,217,534,292 字节。`InputReader` 绑定时散列输入文件，再通过 `iter_inputs()` 复验完整输入，并为字节游标逐行解析、计算行哈希。G5 的 CPU reader 在一轮内复用，当前产品按轮启动独立进程，每次查证查询串行启动检索与重排 worker。这种架构对应已知的模型资源边界。另有两处随个人使用积累放大的路径。G5 为上下文获取历史并核对先前研究记录后才按近期截取，产品历史列表虽分页，侧栏聚合、反馈列表和部分轮次进度重建仍可扫描较多记录。尚无本轮的时延、内存或磁盘增长测量，不能据此断定性能已经超标。 [输入游标构建](../../src/genshin_corpus/retrieval/clean_based/source.py#L59) · [索引输入复核](../../src/genshin_corpus/indexing/inputs.py#L253) · [轮内 reader 复用](../../src/genshin_corpus/rag/clean_research_live.py#L213) · [研究上下文](../../src/genshin_corpus/rag/clean_research.py#L361) · [侧栏查询](../../src/genshin_corpus/local_chat/store.py#L77) · [反馈列表](../../src/genshin_corpus/local_chat/presentation.py#L244)

建议先量化冷轮、同轮二次检索、长历史与失效切换的开销。若冷轮验证占主导，可先验证版本化的偏移及行哈希旁车，绑定完整输入哈希和行计数。若长会话有明显下降，优先限定历史查询和反馈列表的读取量。继续对候选实际读取执行原文与定位校验。

### R06 · 测试和运行环境的入口与边界不够明确

工程已有不少小夹具、故障注入、原始事件回放、契约及浏览器测试。仓库未见跟踪的 CI 工作流或统一测试入口来区分当前可移植套件、按运行环境拆分的 ML 检查以及归档回放。部分 Phase05 测试直接读取未跟踪的研究产物。共享账本的测试模块顶层还导入了历史 Navi 适配器，Generation 环境会因缺少该 SDK 而无法加载同模块的纯账本测试。个别测试在找到历史数据后还会在历史运行根生成派生文件。新检出直接发现所有测试的结果会混杂缺资产、环境差异和实际回归。建议首先提供离线分层入口和最小夹具。把确需封存档案的用例显式标记为回放，并在临时副本落盘。常规合并应验证 OpenAPI、TypeScript 和随包页面的一致性。 [测试布局](../project-structure.md#仓库根) · [旧研究输入依赖](../../tests/retrieval/test_semantic_live_runner.py#L139) · [硬编码评估产物](../../tests/retrieval/test_semantic_prompt_v2.py#L302) · [归档派生写入调用](../../tests/retrieval/test_byakuyakoku_continuity_experiment.py#L288) · [前端开发命令](../local-chat.md#开发与验收)

### R07 · 运行资产、升级与恢复依赖本机路径和手工同步

隔离 Python 解释器、索引选择器、兼容性证书和已验证协议回执是实际运行依赖。Legacy Canonical 在本轮长路径副本有 6 处临时文件写入错误，缩短隔离根后同套件通过。产品诊断做轻量存在性与部分哈希检查，工作台和模型许可在使用时做完整校验。还原到同一路径是当前备份和继续执行的条件之一。前端 `API_REVISION` 与打包静态文件也需在交付时同步。目前 Node 类型与 bundle 均核对一致。随着安装位置变化及升级增多，建议提供只读的统一运行预检，输出解释器、配置及资产标识、具体缺项和校验阶段。区分历史只读迁移与继续执行所需的许可重建。保持冻结索引和旧回执的路径约定。 [启动器路径](../../Start-GC-Chat.cmd#L7) · [资产诊断](../../src/genshin_corpus/local_chat/diagnostics.py#L7) · [兼容性验证](../../src/genshin_corpus/runtime_compatibility.py) · [版本探测](../../src/genshin_corpus/local_chat/__main__.py#L16) · [备份说明](../local-chat.md#本机数据)

## 6. 可选优化的采用条件

| 优化 | 采用条件与验证 |
| --- | --- |
| 为生产路径建立更窄的包导出 | 当前入口的启动或升级频繁受到历史代码牵连时，先固定 `python -m` 入口及历史 import 的兼容测试。抽离经证实反复使用的纯函数和运行时接口。 |
| 加速可验证的读回 | 实测冷启动受全量 I/O 限制时，尝试绑定清单的偏移/行校验旁车或受控 reader 复用。比较运行占用和证据哈希等价性。 |
| 收窄会话层接口与持久化升级 | 先让已有入口修复提交与恢复。下一次确有 schema 变更需求时，验证已有数据打开、备份恢复、阶段化升级及失败原子性。 |
| SSE 状态合并与诊断 | 并发操作增多或实测重连不稳定时，按轮次维护事件游标和刷新版本，统一错误与恢复提示。可通过现有 composable 和 REST 接口迭代。 |

现有规模下，SQLite、本机处理和串行 GPU worker 提供了清晰的可复现边界。应优先验证小改动能否修复故障和改善成本。索引更换、图框架更换及外部服务属于需独立证据和阶段授权的决策。

## 7. 建议修复顺序及验收

1. **聊天可靠性。** 先修 D04、D05，再处理 D03 与 D06。交付标准是中断发生在哪一步都能查到同一轮的真实状态，其他轮次可推进，界面显示的会话与执行目标一致。恢复、停止、关闭批次和接口故障各有离线故障注入。
2. **采集重启和 LIVE 升级的前置条件。** 在再次运行采集前解决 D01、D02。在下一次真实批次兼容性变更前解决 D07。验收包括原始字节和 locator 稳定、同 run 参数校验、旧批次只读及重放零派发。
3. **测试可复现化与小范围解耦。** 固化当前离线回归、归档回放和模型运行时测试入口。先用契约锁定已有结果及异常语义，再在修改点附近收敛读回、执行和只读核算。每次变更对照上游血缘、候选顺序、Packet dispositions 和新旧快照读取。
4. **按观测改善性能。** 分别记录数据绑定、模型启动、推理、读取及前端刷新耗时。只有量化成本后才安排缓存、分页或进程复用，用破坏绑定的夹具验证性能措施仍能发现篡改。

## 8. 验证记录与限制

- 已通过 4 项 Node 前端操作测试和 6 项 Edge 离线浏览器测试。Edge 使用项目启动器与独立数据根，覆盖续聊、进度、重启及显式继续、引文、凭据配置、版本检测和删除。默认沙箱中的 Playwright 工作进程报 EPIPE，随后在普通 Windows 进程环境重跑全部通过。 [操作测试](../../frontend/tests/actions.test.ts) · [浏览器测试](../../frontend/tests/chat.browser.spec.ts)
- 前端类型检查及临时目录构建通过。比较生成文件清单与 SHA-256，随源码交付的 HTML 和静态资源逐字节匹配。运行时 OpenAPI 与跟踪快照结构一致，重新生成的 TypeScript 类型逐字节匹配。 [构建配置](../../frontend/vite.config.ts) · [契约生成](../../src/genshin_corpus/local_chat/contract.py#L212)
- 短探针覆盖 Raw 续跑与 CLI、G4/G5 未封存查询、聊天提交后调度窗口、前端延迟重命名和 SQLite 错误。均在隔离或替身条件下复证。D07 由源码、调用路径和固定绑定范围确认。

### Python 回归

| 执行口径 | 结果 |
| --- | --- |
| 只含跟踪文件的长路径隔离副本，Generation 环境全量发现 | 发现并运行 1,805 项，报告 17 个 failure、329 个 error 和 22 跳过。日志确认存在未跟踪的 Phase05 前置数据、`openai` 等依赖缺失，以及隔离路径长度和 Windows 子进程限制。该结果记录测试入口的可移植性，不能作为全仓功能通过的依据。 |
| 原工作区 Generation 环境，选定主链与产品套件 | 512 项中 462 通过、21 跳过、28 个 error、1 个 failure。11 项产品测试全部通过，包括使用内存传输的官方 G5 接线及拒绝外部套接字的用例。异常发生在混合用例的 `scipy`、`tokenizers`、`openai` 依赖和从标准输入启动的 Windows 多进程测试中。相应覆盖在下两行通过匹配解释器和常规 `python -m unittest` 方式复验。 |
| Dense/Sparse 解释器 | 69 项通过，覆盖并行 Clean/Chunk 构建、恢复回执、Dense/Sparse 小型索引、工作进程终止和续跑。 |
| BM25F 解释器 | 31 项通过，覆盖 BM25F、索引读回及共享 `model_calls` 封存、预算和恢复。 |
| 短路径的纯跟踪文件副本 | Legacy Parsed、Canonical、RU、旧 Generation 和 UI 运行时共 172 项通过。长路径副本中 6 处 Canonical 临时文件写入错误在短路径下消失。Phase05 另有 33 项独立夹具通过，涵盖协议 schema、消息有效性、Card 语义与证据定位。 |

各次执行的测试集合有交集。历史整套回放仍受未跟踪档案和运行环境约束。检查点级语义及真实接口仍按其原合同单独验收。 [并行构建测试](../../tests/test_build_runtime.py) · [Sparse 测试](../../tests/indexing/test_sparse_full.py) · [共享调用测试](../../tests/test_model_calls.py) · [官方 G5 内存接线测试](../../tests/local_chat/test_product.py#L299)

验证以跟踪代码和项目现有合同为准。全库真实性、实际响应质量、满载资源峰值、真实提供方的传输行为与账务需要各自验收。本轮只新增架构审计报告，测试使用临时数据和替身，没有外部模型请求或采集请求。
