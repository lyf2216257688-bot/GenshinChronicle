# GC Clean-based Generation 实施 Plan

日期：2026-11-14（Asia/Tokyo）

文档性质：已确认产品/架构决策的实施交接计划；不是执行授权或实现完成声明。

预期执行者：新建 Codex 6.1-Sol 会话。
核查基线：main，4757232（feat(reranking): adopt BGE structural ordering and finalize handoff）。

## 0. 使用方法、权威与授权

本 Plan 自包含本轮商议结果。新会话不需要重新询问已经确定的产品定位、框架、模型、会话范围及研究行为；应先核对现场是否漂移，再按阶段实施。本文以仓库根为相对路径基准，不依赖作者机器的绝对路径。

- 当前工程状态、实施授权、默认行为与下一步的唯一权威仍是 [current-phase](../current-phase.md)。本文记录未来方案，不把计划写成已实现。
- 当前已完成 Clean-based Reranking P4；Generation 尚未集成。本轮仅授权制定、审计和保存 Plan 及增加文档入口，没有授权实施。
- 新会话必须先取得明确的实施范围。用户说“按本 Plan 实施”时，仍须尊重本文的独立付费、采用、删除及范围扩展门禁；不得将过去实验授权当作长期授权。
- 实施授权可以覆盖代码、夹具测试、Mock、只读历史回放和有限本地接线检查；付费 API、长时间/全量运行、默认入口切换、删除数据、提交或推送分别需要相应明确授权。
- 不运行已收尾重排实验的 Run/Resume/Analyze，不重新导出覆盖既有结果，不重建索引，不修改 Raw/Clean/Chunk 或冻结证据，不恢复已退役的 Python 3.12 环境。
- 不扩大到其他生成模型、其他供应商、在线托管、多用户系统、全局记忆、知识图谱、全库语义抽取或自动联网研究。
- 本 Plan 是设计与交接事实的主要记录。未来获批后，持久技术合同由 docs/phases/clean-generation-v1.md 承接；current-phase 只保留简洁状态/授权与路由。不得同时维护多份互相竞争的当前状态。

### 0.1 状态用语

全文区分：已实现资产、当前采用选择、历史有限验证、已确认设计、工程默认、待实证能力、待用户设置的运行参数。Mock PASS、HTTP 200、引用编号合法、单例成功，均不等于模型语义合格或产品可采用。

本文的工程默认用于让执行者不必重新设计架构；它们不追认未运行的参数或渠道能力。需要实证的条件通过阶段门禁处理；需要用户资源授权的数值保持未设置，程序必须拒绝因此产生的真实调用。

## 1. 已确认产品决策：不得重新作出选择

| 编号 | 本轮确认的决定 |
| --- | --- |
| D01 | GC 是开源个人本地应用；用户自行配置远程 LLM API，不建设公共服务器、账号体系或商业服务基础设施。 |
| D02 | 最终回答质量优先；简单问题尽量快速，复杂问题允许较多阅读、推理与调用；按复杂度分配资源，并有硬上限。 |
| D03 | 支持深度剧情分析、动机/因果讨论、跨资料联读、竞争假说和反证；相关性及先后关系不能直接变为因果。 |
| D04 | 自动适配回答详略、结构和引用密度；用户明确要求优先，不强制简答/深研模式，不做剧透控制。 |
| D05 | 允许一般知识辅助联想、比较及提出假说；具体剧情事实需可核验依据，库外未经核验的解释不得伪装成有出处的事实。本轮没有自动联网查证工具。 |
| D06 | 证据不足时先针对影响结论的缺口补检；仍不足可给有限结论、合理假说和未解事项，不轻易拒答或无限搜索。 |
| D07 | 按需逐级扩大原文阅读范围；复杂问题可直接读完整自然资料，超长资料可分段阅读。读过的全部内容不必都放入最终上下文。 |
| D08 | LLM 可提出补检、回读、原文选段建议；程序校验来源/范围/预算并确定性组包。召回发现候选，重排负责排序，生成牵头 Assembly。 |
| D09 | 当前默认 S0；先看检索反馈再改写、扩展或考虑其他组合。Sparse/S1/S2 资产保留，优先研究 S2 的补充价值，不预设题型路由有效。 |
| D10 | 查询改写、拆解、Step-Back、多跳和反证检查是按需动作，不是每题必走流程。保留原问题；每项行动对应具体缺口。 |
| D11 | 主问题所需查证可自主推进；有趣但非必要的旁支通常提示或留作当前会话未解问题，不自动扩张成另一项完整研究。 |
| D12 | 子问题局部查证，原问题统筹综合；保留必要的中间证据，不跨查询直接比较 BGE logits，不把多次命中算成独立印证。 |
| D13 | 停止原因分为充分回答、有限结论、无明显新增收益、预算耗尽、用户取消或故障；预算耗尽不等于研究充分。复杂关键假说主动检查合理反证。 |
| D14 | LangGraph 负责状态与编排；GC 保留检索、证据、校验及 model_calls；LangChain 组件按收益使用，不重写既有算法。 |
| D15 | 一个研究控制器，按需使用决策、阅读、综合、检查等任务角色；初始共用一个模型，不要求每题四次调用，不建设多 Agent 团队。 |
| D16 | 唯一研究组合：TokenMetro / deepseek-v4.1-flash-1017。不对照其他型号或渠道，不自动切换。 |
| D17 | 按任务动态启用思考；复杂或难判任务偏向开启，简单明确任务优先关闭。具体档位以该精确路由验证结果为准。 |
| D18 | 原生工具调用优先，经完整工具循环验证后采用；JSON 行动协议是明确备选，不提前建设两套，不静默切换协议重发。 |
| D19 | 中途失败尽量保留此前合格证据和成果；关键失败缩小结论范围，来源核验失败隔离相关材料，流式半截文本不冒充最终答案。 |
| D20 | 同一对话保存历史、研究状态与证据记录，关闭应用后可继续；新对话独立开始，不继承其他对话的结论或假说。 |
| D21 | 对话变长时自动整理上下文，完整历史本地保留；保护用户要求/纠正、叙事限定和未解问题，保留原消息与原文回查定位；研究要点可查看纠正。 |
| D22 | 展示真实研究进度、流式草稿和可展开引用；最终检查完成前保持生成中/检查中。默认不展示长篇模型推理或控制协议。 |
| D23 | 验收分工程/来源、回答语义、连续研究三个层次；少量代表案例与完整对话服务具体决策，不建设比产品更重的评测系统。 |
| D24 | V1/V2/V3 只是能力建设参考；必要补检可以提前进入首版，不机械分割。全局长期研究记忆已被明确移出本轮范围。 |

### 1.1 覆盖旧讨论的最新修正

较早交接总账第14节的“自动跨会话研究记忆”已被本轮用户收窄为同一对话上下文与本地恢复。不要建设跨会话主题档案、用户画像、记忆向量库、自动全局知识积累，也不要把 V3 长期记忆列为必做阶段。较早的“模型与渠道比较”也已被精确 TokenMetro/1017 单一路由决定取代。

## 2. 仓库现实与复用地图

### 2.1 已实现与仍缺的能力

| 对象 | 现状/可复用内容 | 不能据此宣称 |
| --- | --- | --- |
| Clean V1 | 16,437 条构建；Raw lineage、字段/块/表格/对白/来源关系及 readers | 全库独立语义忠实性已逐条验收 |
| Chunk V2.1 | 119,557 唯一候选、宏微成员关系、214,870 自然 Parent、5,087 对白组；460 个所选来源无候选 | 检索不到即原文不存在；宏微就是父子树 |
| 索引 | 已选 Whoosh3 BM25F 与 BGE-M3 1024 Dense；完整 Sparse 资产保留 | 新 RAG Serving 已部署 |
| Retrieval | S0 原始单轮运行器、F1_max、精确搜索与候选审计 | 改写、Step-Back、多轮或 S1/S2 动态路由已支持 |
| Reranking | BGE-v2-m3 + structural、MMR off；完整70题/6,963候选 handoff | handoff 自己会评分，或新问题实时链已接通 |
| Parent/原文读取 | InputReader.read_chunk/read_parent/read_graph/read_clean/read_natural_parent，带身份/hash核验 | 已有最终阅读范围、选段、预算及 Clean Packet 策略 |
| Phase04 | GenerationRequest/Result 思路、Packet 投影、引用编号检查、combined Assembly、CLI/UI | 旧 Canonical/RU schema 可直接当 Clean schema；旧 Qwen 本地链可运行 |
| model_calls | raw-first attempts、预算、UNKNOWN、指纹锁、封存与有限恢复 | 已解决 LangGraph checkpoint/会话预算/流式工具的全部集成 |
| Phase05研究 | 有界补检、search/read/clue/stop、假说工作状态、读取账本、原文选段经验 | Navi/Card已验收；已有成熟跨会话记忆或自主研究产品 |

### 2.2 优先读取的代码入口

所有下列代码均为真实已有入口；后文带“新增”的名称是计划，不是现有能力。

- Clean/Chunk：src/genshin_corpus/clean/reader.py、chunking/reader.py；索引源读取 indexing/inputs.py。
- 召回：src/genshin_corpus/retrieval/clean_based/runtime.py、worker.py、core.py、indexes.py、source.py。
- 重排：同目录 rerank_model.py、rerank_core.py、rerank_handoff.py；配置 config/reranking-selected-v1.json。
- 索引原语：indexing/benchmark_dense.py 的 Encoder，clean_based/indexes.py 的 DenseSession/LexicalSession；Sparse 后续复用 indexing/sparse_full.py 的 StreamingSparseSession。
- 共享调用：src/genshin_corpus/model_calls.py、evidence_io.py；旧适配器 generation/deepseek.py；流式/raw-first模式参考 retrieval/navi_f2_sdk_transport.py 与 retrieval/navi_offline/metro_chat.py。
- 旧生成与编排：generation/generation.py、rag/backend.py、rag/adaptive.py、rag/navi_e2_experiment.py。
- UI：ui/streamlit_app.py、ui/runtime.py。当前只有同步单问题和 session_state.last_result，并非持久化聊天。
- 回归入口：tests/test_model_calls.py、tests/retrieval/test_clean_based.py、test_clean_reranking.py、test_rerank_handoff.py、tests/generation/、tests/ui/。

旧代码的来源身份、schema、默认重试、错误语义、HTML展示处理必须逐项判断后迁移；不整套搬入旧backend，不机械继承 Top20、12,000字符、1,024输出或 disabled-thinking。

### 2.3 两个必须先解决的兼容约束

1. runtime_compatibility.compatibility_code() 将 core.py、rerank_core.py、indexes.py、source.py、部分 indexing 模块及 evidence_io.py 等文件纳入既有 Python 迁移证据。rerank_job、handoff另有冻结代码绑定。直接修改旧 QuerySpec 或这些核心文件会导致兼容核验失败；不得更新旧hash伪装连续有效。
2. PreparedRetriever 的 Dense worker 在首轮后可常驻 GPU；LocalReranker 又加载 BGE。仅按顺序 await 两者不足以释放显存。新研究运行时必须控制整个应用的 GPU 生命周期，而不是只控制单条调用顺序。

## 3. 目标架构与工程默认

### 3.1 组件与职责

- 编排放在 rag 的新增 Clean 研究模块中；不增加第七个知识层。LangGraph StateGraph 调用现有或新增版本适配器。
- Clean Evidence Assembly、新模型消息/工具协议适配放在 generation 的新增 Clean 模块中；旧 generation/deepseek.py 及历史行为保留。
- 查询/重排 V2 适配放在 retrieval/clean_based 的新增模块中；不修改冻结数学核来解除历史门禁。
- 采用本地 SQLite 保存会话元数据与 LangGraph checkpoint；不可变请求、工具回执、Packet、流事件及结果保存为文件并由数据库引用。不同存储的权威见第5节。
- 首版 UI 复用 Streamlit 技术栈，新增显式 opt-in 的 Clean chat 入口；保留旧入口及其选择器，不能把旧不可用链当新链 fallback。无外部数据库、任务队列服务或 LangSmith 云端依赖；不默认上传会话/语料遥测。
- 新编排环境独立使用常规 GIL CPython 3.14.5，不向 Dense/Sparse、BM25F、Reranker 已锁定环境混装框架。Python/依赖版本不兼容时阻断并报告，不静默降级或重建上游环境。
- 在 G1 选择支持所需官方 API 的稳定 LangGraph/SQLite checkpoint 版本并锁定可复现依赖；先兼容性夹具，再安装入正式新环境。不使用“最新版”作为不固定的运行依赖。

### 3.2 运行与数据根

新增运行数据默认放 data/generation/clean-v1-local/，路径可配置。其下分会话数据库、checkpoint、不可变操作/证据、调用账本和资源锁；具体文件名由合同列明，不能依赖 Streamlit 内存状态恢复。生成数据、密钥、完整本地会话与模型文件不进入 Git；新增目录时更新 data README 与忽略规则。

一个本地执行器拥有 graph 生命周期；UI只提交、订阅事件、取消、查看。首版全应用一次执行一个研究 turn，其余明确排队；每个会话有跨进程执行锁，同一data root有应用所有权锁，防止第二个CLI/页面重复执行。无需为个人使用建设并发服务器。纯阅读界面可以同时打开多个会话。

浏览器刷新/重连不重发用户提交。关闭应用后停止新派发并尽力收尾在途请求；下次打开只恢复展示，未完成研究由用户点击“继续”后在原预算与授权仍有效的前提下恢复，不自动启动后台付费研究。

### 3.3 接口合同：新增版本，不伪装旧输入

| 接口/对象（新增设计） | 最低必要内容与规则 |
| --- | --- |
| ResearchQuery / QuerySpecV2 | 会话、turn、query身份；原始用户问题、当前子问题、search_text、rerank_text；kind、round、父query/证据引用、触发缺口与组合。原始问题不得被改写覆盖。 |
| CandidateSetV2 | 复用完整Chunk/source/命中/粒度/排序与处置字段；绑定V2查询、索引空间与代码版本；允许明确empty结果。 |
| RankedCandidateSetV2 | 完整保留候选与原召回记录；绑定实际rerank_text、评分输入digest、模型环境、每项分数/token审计与结构处理。不同查询不产生伪全局分数。 |
| ReadReceipt | 来源快照、CleanRef/Parent/graph定位、准确原文、说话者/条件/未知性质、读取范围及hash；区分检索片段、完整回读和未读范围。 |
| EvidenceSelection / Packet | 模型只能选择程序交付的原文片段ID；程序提取原文与引用定位。记录输入候选/请求/策略hash、每项处置、预算及最终可见内容；保持provider-neutral。 |
| ChatRequest / ChatResult | 版本化完整消息、工具schema、能力profile、动态thinking及输出预算；结果分别表示最终文本、工具调用、拒绝/中止/不完整，不能把tool_calls当最终答案。 |
| ResearchState | 当前问题/子问题、假说及反证、未解事项、证据/操作引用、研究动作与停止原因、预算引用、最新有效摘要和用户纠正；不保存为跨会话知识。 |
| TurnResult / ProgressEvent | 区分草稿、最终回答、部分交付、失败、取消及等待授权；携带引用/验证状态、停止原因、操作引用和单调事件序号。 |

工具只开放受控的检索、回读、选段建议和结束研究操作；不开放任意路径、代码执行、配置修改、供应商/模型更换或网页抓取。read只能使用本次会话经检索/合法回读建立的来源句柄及其有证据的父层范围，不能靠猜content_id绕过候选发现。工具结果中的剧情文本/社区文章当数据处理，不能当新的系统指令执行。

### 3.4 V2 上游适配的具体方案

- 新增query_v2、runtime_v2/worker_v2、rerank_v2等适配职责；保留旧QuerySpec original-only验证。禁止把子查询字段清空后冒充旧用户原问题来绕过合同。
- 复用 indexes.selected、Encoder、DenseSession、LexicalSession、InputReader、core.fuse_candidates、covered_by及rerank_core.ranked_candidates。融合函数可接收经V2验证的dataclass；输出显式转换为V2，不让旧version字符串承载新语义。
- 原 LocalReranker.score 读取 bundle.query.question。适配器为实际 rerank_text 建立独立、可审计的评分输入，保存其与V2请求的映射及digest；验证评分的完整候选覆盖后再导出V2结果。不得把子查询检索文本与原问题评分偷偷混用。
- 默认每个子问题用明确的子问题语义作为 rerank_text；search_text 可以是更短的检索表达。原始单轮使用原问题。跨轮保留各自顺序，不直接混合logits；本轮不新增全局重排或学习式融合。
- 初始继承既有S0工作点：depth=50、candidate_limit=100、F1_max、BM25F/Dense等权、结构处理开启、MMR关闭。新在线配置引用选定模型与策略来源，但不依赖70Q输出目录作为每题运行前提。
- BGE初始沿用已验证2048配对token工作点、FP16 CUDA/eager/batch1，无截断。新超长问题或候选显式失败；扩大预算需有限资源验证，不以原生8192能力冒充4GiB显存验收。
- 零候选在评分前返回empty并跳过GPU；非空不等于有充分证据，BGE logit不作为正确概率。部分评分失败不能包装为完整RankedCandidateSet，也不自动回退召回顺序/GTE/CPU。
- 保留CPU来源读取与校验缓存；GPU切换使用应用级lease和跨进程锁。Dense/Sparse与Reranker不同时常驻；切换前关闭并确认旧worker退出，复用Windows Job Object清理子进程树。先接受可量测的冷启动成本，不以并发工具掩盖资源冲突。

## 4. Evidence Assembly 与研究控制

### 4.1 读取、选段与组包

读取顺序由问题需要决定：命中片段及必要限定→相邻结构范围→自然Parent→同一合法来源内的完整资料。主题分析可直接请求完整资料。对白图的展示顺序不解释为剧情时间；未知分支条件不推断为已发生事件。

程序对回读原文建立带位置映射的片段ID。LLM只返回这些ID及必要的选段目的，程序生成引用正文；不让模型复制引文、计算字符偏移或发明来源ID。部分重叠、多来源独立佐证、同篇不同事件保留；完全覆盖去重必须满足同观测/语境/条件的证明，继承别名/出现关系。

长文阅读笔记仅用于控制器的研究理解，始终保留原文定位；最终事实引用回到原文。临时笔记不进入正式索引，不恢复旧Card/KG任务。模型选择的桥梁关系是待核验假说，不能因有助于论证就免于审查。

确定性Assembly消费“按子问题分组的完整排序候选＋经过验证的选段/回读请求＋明确预算策略”。组内保持上游顺序；跨组按已记录的研究问题顺序组织，不制造全局相关性排名。组包可按必要性决定准入并记录理由，但不执行新检索或语义重排。发现缺口回到研究控制器，由上游工具处理。

每个候选/选段记录included、covered_duplicate、not_selected、budget_excluded、invalid_source、context_expansion等可区分处置。截断只在可回溯的结构边界执行；必要限定无法一起容纳时不交付失真的半段，记录排除及受影响问题。固定输入、请求、策略应得到相同Packet内容与引用映射。

Packet预算须计入实际模型消息：系统指令、问题、必要会话上下文、工具schema/协议历史、证据、输出/思考预留。不能只统计正文字符或引用旧12,000字符预算。精确tokenizer未证实时使用明确标注的保守估计，不宣称原厂精确token或费用保证。

### 4.2 图的逻辑流程

接收并持久化turn→构建当前上下文→确定直接回答或研究需要→请求准备/预算门禁→模型决策→验证工具请求→召回/重排或回读→核验与保存回执→更新缺口/假说→继续或停止→Assembly→最终综合→引用/必要语义检查→提交最终或部分结果。

每个远程步骤走第5节的共享调用桥接。简单问题不强制规划、检查或多轮调用；可以直接使用S0证据综合。复杂问题允许拆解、Step-Back、多查询和多跳，但每个动作都记录针对哪个缺口；不强制每题运行全部方法。

不同查询命中相同Chunk合并身份和命中历史；保留各子问题的排序及该证据的作用。主问题决定哪些局部结论有用，不能将多个不同条件片段拼成不存在的事件。反证检查针对影响核心结论的因果/动机/身份假说，不要求为已明确事实凑出反方。

停止由模型提出语义理由，程序同时执行可计算上限：请求、工具、搜索/读取、研究轮数、输入/输出预算与deadline。重复查询/回读无新增证据时提示已覆盖并计入动作预算，防止循环。不得把草稿长度、模型自信或多来源命中数当充分性证明。

## 5. LangGraph、模型账本与恢复合同

### 5.1 单一权威与身份

- SQLite/checkpoint拥有会话和流程进度；GC文件账本拥有派发、attempt、预算占用、原始响应及可复用结果的事实。两者不存在跨存储原子事务，必须通过恢复对账连接。
- 身份链为session_id→turn_id→logical_operation_id→attempt_id。用户提交时先持久化submit_token/turn；准备操作时先持久化稳定operation、完整请求计划、schema/prompt/策略/预算/能力profile身份，再允许网络效果。
- 单独保留请求体fingerprint。业务操作身份与请求内容都绑定；合法新turn与恢复旧操作不能混淆。不添加随机盐、换预算根或换UUID绕过UNKNOWN。
- LangGraph使用同步持久化边界：准备节点完成checkpoint后才进入发送节点。重放只解析同一operation/call root，不能在发送节点重新生成身份。
- 远程节点和供应商SDK关闭框架层/传输层自动重试；格式修复等会新增模型请求的行为也必须经过model_calls和同一预算。LangChain ToolStrategy等自动纠错默认关闭，不能形成隐藏循环。

### 5.2 共享调用桥接的工程落点

优先新增共享会话桥接模块（例如genshin_corpus.model_call_sessions），调用原model_calls.run/Policy/Batch；它负责稳定操作映射、会话/turn额度、取消及只读对账，不自建第二套发送器或retry/UNKNOWN政策。evidence_io及旧model_calls默认保持原样，以免破坏旧调用code_identity与迁移证据。

新计划必须绑定campaign、预算根/配置身份、session/turn/op及请求体；恢复时不允许换根。新会话campaign的所有预约通过统一的campaign锁，锁内检查turn上限再委托Batch.reserve检查并预留总额度；fingerprint对应的操作/turn映射在预约前持久化。所有新入口只能走此路径，避免两个独立Batch各自成功造成超额。不能把Batch.dispatch_limit/window_start当持久的turn上限。

预算桥接实现与Batch兼容的接口，reserve内执行上述检查，不能只在调用run前检查一次而遗漏后续attempt。固定嵌套锁顺序为operation/fingerprint→campaign→Batch内部预算锁；取消准入控制锁只用于短临界区，不带锁等待网络或反向获取这些锁。reconcile不得反向持锁调用run。操作fingerprint绑定稳定业务身份及冻结请求，另存纯body fingerprint；不同turn的合法操作与同一操作恢复明确区分。

同一预算预约只计一次；UNKNOWN按保守额度占用。finish/reconcile遇到已有相同结果应核验后复用，不重复写create-only文件。封存call的replay早退前，同时检查budget outcome及ledger outcome：前者逐项对照原attempt seal、usage和reservation，后者对照call seal；缺失时补齐相应派生回执，零网络，不用call聚合用量冒充每个attempt用量，不改原request、attempt或seal。对无法证明完整的截断文件，安全阻断并诊断，不承诺任意损坏都能自动修复。

如适配过程中证实必须改共享核心，先提交具体冲突与最小版本化迁移方案；不得直接改code hash、放宽校验或令旧UNKNOWN重发。旧封存调用只读显示可以用版本识别的读取器；不兼容旧代码的未完成调用不自动用新代码恢复。

### 5.3 必测恢复状态表

| 持久事实 | 恢复动作 |
| --- | --- |
| 相同prepared operation，没有派发预约 | 在原授权/预算仍有效时允许首次发送 |
| 已预约/可能发送，终态未封存 | UNKNOWN，保留预算，不自动重发 |
| attempt已封存且逻辑操作已成功或终止、call尚未收尾 | 共享调用层/对账补齐结果与账务，零网络 |
| attempt已封存但仍存在待执行的已授权重试 | 先对账并标为pending_retry；对账本身不发送，只有用户继续且原策略/授权/预算有效时才经共享调用层重新准入，不能把已封存失败attempt误当逻辑操作完成 |
| call已封存、SQLite节点未提交 | 验证seal并复用结果，补checkpoint，零网络 |
| 工具回执已封存、tool消息未写回 | 复用回执补齐tool_call_id关联，不重复上一轮模型调用 |
| SQLite声称完成但目标文件丢失或hash不符 | 阻断受影响操作，不能用数据库文本再造证据 |
| schema/代码版本不兼容 | 只读展示旧成果，明确迁移/恢复门禁，不自动调用 |

没有服务端幂等证据时，不承诺网络exactly-once；承诺本地恢复不会盲目重复已预留的不确定操作。

### 5.4 工具、流式与取消

- 首个待验证API surface为现有TokenMetro Chat Completions路径；固定模型deepseek-v4.1-flash-1017、既有凭据槽。不得猜别名、把旧flash的能力当1017实证或转去官方API。
- 新适配器识别assistant普通文本、tool_calls、tool结果及拒绝/截断；工具参数按call id/index累积，完整终态和JSON/schema核验后才执行，不执行半截流中的对象。
- 原始HTTP字节先归档再解析；reasoning、tool arguments、answer分别处理。消息关联、终态、usage与模型身份各自验证。工具执行串行，接受多工具返回但逐项受预算和取消门禁约束。
- UI事件带session/turn/op和序号，可重放去重；流出的答案是草稿，最终提交与机械/语义检查状态分开。推理文本不当证据，不默认展示。
- 持久化取消意图；取消提交与dispatch admission使用同一控制锁/单调序列形成明确先后关系。取消先提交，则不允许新的派发准入；已先准入的操作属于在途，尽力在尚未发送时停止，但不能承诺远端不执行。在准备、预算预约、首次发送、重试等待、工具和图边界协作检查；发送后断流/取消仍可能远端执行，UNKNOWN占额保留。
- 若取消时完整终态已经封存，保留已知结果和用量；不能覆盖为未发送。UI断线不等于取消；应用退出停止新派发，重开不自动收费恢复。
- 取消提交或没有剩余预算时，不再准入额外模型请求生成收尾摘要；用确定性模板交付已有合格内容、证据及未完成范围。旧model_calls的重试等待通过可取消sleep桥接，新的send_once在发送边界再次检查；正常本地取消不冒充渠道故障或成为重试理由。已预约但未发送只有真实trace证明未发送时才按共享规则记账，不能凭UI取消标签释放UNKNOWN额度。

## 6. 会话、上下文与回答检查

- 保留完整本地消息历史和用户修订；摘要是派生状态，不覆盖原始消息。新chat不加载旧chat研究摘要、假说或答案。共享不可变语料读取缓存不构成共享聊天记忆。
- 上下文由近期原始交流、当前研究状态、必要原文Packet和协议要求的完整工具消息单元组成；早期交流可整理为带消息/证据引用的摘要。
- 摘要记录用户要求、当前问题、明确纠正、已支持判断、假说/反证及未解事项。新纠正使依赖旧前提的研究判断标记需重审；旧助手输出不升格为事实。
- 用户对意图、范围和表达要求的纠正直接采用；用户提供的剧情断言或引文保留“用户提供、尚待核验”身份，须与原文核对后才能成为已支持判断。发生冲突时说明差异，不因来自用户或旧回答就覆盖来源事实。
- 根据配置的上下文余量触发整理，整理调用也占同一预算。先保护用户当前要求和完整协议单元；无法容纳时报告限制，不静默截断工具参数或关键条件。
- 工具循环所需reasoning/历史回传按该路由已验证规则保留。不能为压缩任意删除字段；必要时结束完整工具循环，再以新请求用明确研究状态和原文继续，而不是伪造未发生的工具消息。
- 最终生成仅接收问题、必要对话背景、明确标为待分析的假说与核验原文Packet；不直接注入旧Phase05导航审计轨迹或未验证Card。
- 引用标记由Packet映射到准确原文、资料标题及必要语境；支持展开Parent和已知原始来源。编号应绑定turn/Packet身份，避免后续回答E01错误指向先前Packet。
- 程序验证引用存在、属于交付材料、可回读；复杂关键论证按需进行语义检查，验证支撑关系、叙事归属、条件/否定和反证。程序PASS不宣称语义PASS，同模型检查也不冒充独立评审。
- 有限修订仍走同一预算和原始版本记录；不无限自我纠错。最终未通过时给出部分交付或明确失败，草稿保留但不标为已验证最终答案。

## 7. 参数、能力与授权门禁

### 7.1 已选工程默认

| 项目 | 默认 |
| --- | --- |
| 本轮生成路由 | TokenMetro / deepseek-v4.1-flash-1017；无跨路由回退 |
| 工具协议 | 优先验证Chat原生tools；JSON备选需明确决定与独立profile |
| 本地检索/重排 | S0/F1_max现有工作点；BGE structural；MMR off；配对预算2048 |
| 执行并发 | 全应用单研究turn、工具串行、GPU单模型lease；其他提交排队 |
| 存储/UI | 本地SQLite checkpoint/会话＋不可变文件；新增opt-in Streamlit chat |
| 网络默认 | live_enabled=false；Mock/离线回放显式标记，不冒充真实模型输出 |
| 初版重试 | 共享Policy中unknown_retries=0、format_retries=0；初始渠道探针max_attempts=1。其他重试必须另有验证与有界授权 |
| 来源失败 | 隔离失败材料；无自动改绑、模糊引文修复或跳过hash核验 |

### 7.2 用户明确延期的数值

真实调用前必须由用户确认或通过经授权的有限测量提出后采用：每会话campaign与每turn的请求/token上限，搜索/读取/研究轮数上限，wall-clock期限，单次输出上限，实际可用上下文限制，thinking档位，以及费用控制口径。所有角色调用、压缩、检查、修订、失败和重试均计入同一预算体系。

缺少任何必要有限上限时，真实调用在发送前拒绝；不要由实施者随手填高值。允许使用明显标为fixture的极小数值测试控制逻辑，不能将其变成产品默认。

初次研究分配较小额度，复杂度/证据缺口可以在用户已批准的硬上限内增加分配；不能由模型提高硬上限。预留最终回答所需额度，避免研究过程耗尽全部预算。若仍无预算，则确定性部分交付。

预算预约、供应商报告用量与真实账单分开。请求max_tokens是客户端请求值，不保证服务端执行；历史有超限报告，超限保留原始事实并阻止后续超额派发。价格/计费模型未验证时不承诺人民币硬限能被精确保证，不把UNKNOWN计为零。会话扩额或新campaign必须有明确关联和新授权，不改旧预算配置清零用量。

### 7.3 渠道能力门禁

每个能力profile绑定endpoint、精确模型、协议、thinking、stream、schema/prompt版本和证据。先离线Mock，再按独立付费授权验证：非思考普通回答、思考回答、原生工具选择与多轮回传、流式终态/usage、错误与恢复。先验证最小非流式工具循环，再加流式变量；不一次改变所有参数。

历史strict function成功使用较早flash且为强制Card输出；历史Responses JSON Schema失败也有具体路由/时间边界。两者都不能直接替代1017新工作点验证。官方DeepSeek支持声明不等于TokenMetro透传/执行证明。

真实能力不满足时：暂停依赖该能力的真实阶段，交付可用离线部分与差距。JSON备选、换API surface、调整模式或上限均要有明确方案/授权；不自动换模型、供应商或借新指纹重发UNKNOWN。

## 8. 分阶段实施与验收

依赖主线：G0 → G1 → G2；G3离线适配可在G1之后与G2并行开发；G4依赖G1/G2/G3离线接口；G5依赖G4。G7完整S0产品验收依赖G4/G5离线闭环，真实远程与语义验收还依赖G3-Live通过及该批运行授权。G6可选，不阻塞G7的S0验收。未获付费授权不阻止已授权的离线工程继续。

### G0：实施授权、合同与冻结基线

- 目标：把本Plan转为明确授权的Generation工作单，消除当前阶段冲突。
- 任务：复核git状态/HEAD、current-phase、选定配置/manifest与历史路径；建立受保护文件hash清单；起草并落实Generation阶段合同、数据根及owner；确认新运行不覆盖旧产物。
- 交付：docs/phases/clean-generation-v1.md、current-phase的精确授权范围、新配置样例/输入清单和阶段检查表；本文作为已确认决策来源链接保留，不复制第二份动态状态。
- 验收：D01–D24全部映射到合同；明确offline与live边界；旧产物/选择器未改；无敏感值进入文档。
- 下一阶段前提：用户已给出工程实施范围。仅打开本Plan不满足此门禁。

### G1：持久化与共享调用桥接的离线骨架

- 目标：先证明图恢复不会重复派发，再增加研究功能。
- 任务：隔离3.14编排环境及依赖锁；SQLite会话/checkpoint、稳定submit/turn/op身份；调用桥接、campaign/turn预算、取消意图、事件落盘/重放、应用/session/GPU锁；Mock图和故障注入。
- 交付：最小可恢复graph、共享桥接与版本化状态、离线恢复/预算报告。
- 验收：第5.3状态表及持久边界故障注入通过；相同提交/重连/图replay不新增派发；UNKNOWN不因重启消失；预算作用域不能换根；无真实网络请求。
- 下一阶段前提：恢复和预算夹具通过。暂未安装/验证框架的事实不能写成已兼容。

### G2：实时S0/BGE接线与Clean Evidence Assembly

- 目标：形成“真实新问题→候选→排序→原文Packet”的可用链。
- 任务：实现第3.4的V2适配、GPU轮换、空结果处理；复用readers；源片段ID/选段验证、结构保真、预算与处置记录、provider-neutral Packet。先用封存70Q handoff离线验证Assembly，再用新查询验证真实接线。
- 交付：独立的新入口与新数据根、Candidate/Ranked V2、Packet及audit；必要的小型fixture；新查询有限本地检查报告。
- 验收：相同原问题fixture与现有S0/排序一致；候选/评分覆盖及来源完整；重复执行Packet一致；Parent/条件/对白/独立佐证正确；GPU退出与资源检查通过；旧hash不变。
- 下一阶段前提：完整本地链通过；只读取70Q handoff不能算实时接线完成。有限本地检查按新工作单授权执行，超出范围交操作者运行。

### G3：TokenMetro 1017 新适配器与能力验证

- 目标：支持明确任务结果类型、动态thinking、原生tools与流式，同时保留共享账本。
- 任务：新增适配器，不修改旧DeepSeek QA类；实现raw-first消息/事件解码、工具schema与完整参数校验、usage/身份/终态判定、思考与答案分离、取消和对账；用Mock/历史只读资料覆盖异常。
- 交付：route capability manifest格式、离线适配器测试、精确请求预览与有限Live提案。
- 离线验收：SSE分片、多工具、错误模型名、断流、无usage、超限、工具中断恢复等场景通过；共享账本调用路径唯一，SDK/框架无隐藏重试。
- G3-Live门禁：用户另行批准精确模型/endpoint/profile、样例、最多请求/尝试、输入输出与总预算、超时和UNKNOWN风险策略后才发请求。先完成约定最小循环，不启动研究批量运行。
- 真实验收：实际raw结果与发送配置相符、工具轮次和usage可核对；机械PASS与语义质量分开。未通过则保留失败与未知，停止依赖能力，不自行扩额/换协议。

### G4：连续对话、固定链问答与本地界面

- 目标：用户可打开同一会话反复追问并关闭恢复。
- 任务：接入会话历史、指代上下文、研究要点、自动压缩与纠正；Streamlit新opt-in chat入口、稳定提交token、后台本地执行器、进度/草稿/最终状态、引用/Parent展开、停止和继续。
- 交付：Mock端到端聊天应用及CLI/测试入口；本地会话格式、恢复与上下文报告；经授权后实际问答样例。
- 验收：同chat重开能继续；新chat不继承旧内容；刷新/双击不重发；纠正后旧摘要不再当事实；引用跨turn不串位；草稿不冒充最终；没有全局记忆Store或远程服务依赖。
- 下一阶段前提：离线完整交互与来源检查通过；真实远程部分必须G3-Live通过且本批授权有效。

### G5：主动研究闭环

- 目标：由同一控制器在有限预算内解决影响结论的证据缺口。
- 任务：原问题/子问题/查询关系；按需改写、Step-Back、多跳、回读/选段建议、竞争假说与反证；按反馈停机；组内排序及跨组证据组织；简单问题直达路径；先前成功轮次的部分交付。
- 交付：受控工具清单、可回放研究trace、停止/部分交付结果、完整研究对话夹具与有限真实验收提案。
- 验收：每个动作能回到具体缺口；中间证据可保留；无跨查询logit混排；无无界旁支/循环；模型不能改硬预算；关键查证失败不伪装充分回答；取消提交后无新派发准入，先准入的在途操作按第5.4节如实收尾。
- 下一阶段前提：离线对话与故障场景通过；真实质量研究独立授权。必要的一次补检可在G4交互验证时提前接入，但沿用本阶段合同。

### G6：可选Sparse/S2增量接入

- 目标：仅在S0闭环已有可解释缺口时评价额外检索表示。
- 任务：以明确工作单启用现有Sparse streaming原语与V2候选接口；保留全部通道/权重/来源记录，S2重新通过评分和Packet；比较新增有效证据及开销，不重新构建索引或搜索融合参数。
- 交付：有限S0/S2差异与真实原文案例、能力开关与采用建议；S1资产/接口方向保留，不强制本轮实现。
- 验收：有具体证据增益而非仅排名差异；没有BM25F权重变化导致的误归因；与原始S0路径可区分；按用户裁决启用，未裁决保持S0。
- 门禁：G5基础成立且用户另行确认该增量工作单。G6不阻塞S0产品交付，不能因“动态路由愿景”自动恢复Sparse调参。

### G7：有限质量验收、文档与交接

- 目标：判断完整产品是否值得采用，并交付可复现本地使用方式。
- 任务：从70Q抽代表证据案例，加少量未参与调试的新题和连续对话。Assembly对照固定同一RankedCandidateSet、回读输入与预算，比较产生的Packet；Generation对照固定同一Packet和模型；固定链与主动链按同一授权硬上限比较并报告实际开销。人工/来源复核关键差异。
- 交付：工程/协议/来源/语义/连续研究五栏分开的报告，已知限制、配置与PowerShell 7启动说明、依赖锁、阶段收尾清单；更新实际受影响的文档owner。
- 验收：第9节完整覆盖；阻断性来源错绑、错误人物/条件归属、关键无依据断言、漏记调用、恢复重复派发均为失败；不存在用模型自评或引用ID PASS替代质量验收的表述。
- 采用门禁：默认入口/Serving切换由用户明确决定。未采用时交付opt-in入口；不自动commit/push，不删除旧资料。

## 9. 测试与验收覆盖

测试使用小型checked-in fixtures，不依赖全量Raw。真实本地检查与付费检查分别有独立入口/授权，缺失本地资产时明确skip或阻断，不下载或重建代替。优先跑受影响测试；无新增问题不反复运行全库/70Q。

| 类别 | 必测场景 | 合格判据 |
| --- | --- | --- |
| 历史兼容 | 保护清单hash、旧V1 original-only拒绝、旧选定profile与迁移证据 | 不修改旧证据、不放宽旧门禁；新V2独立版本 |
| 查询/排序 | 原问题、改写、Step-Back、父查询依赖、search/rerank文本分离、empty、评分缺失/OOM | 全过程身份可追；空结果正常返回；失败不冒充完整排序 |
| 资源 | 排队两会话、两个CLI、取消/timeout/OOM、模型切换 | 无Dense/Reranker同时驻留、孤儿进程或来源reader误释放 |
| 来源/Assembly | 同源别名、部分重叠、跨来源印证、否定/条件/对白分支、长Parent、未知来源、超预算 | 原文/限定/定位一致；处置完整；相同输入可复现 |
| 模型协议 | UTF-8/SSE碎片、工具arguments交错/缺ID/重复ID、未知工具、schema错误、错模型、拒绝、无usage、超限 | 完整校验前不执行工具；错误/未知如实保留 |
| 崩溃恢复 | prepared、claim、预算reserve、dispatch、首字节、terminal、attempt seal、budget outcome、call seal、ledger outcome、checkpoint | 已完成复用零网络；可能发送不盲重发；缺失/损坏安全阻断 |
| 预算 | 跨节点/turn恢复、UNKNOWN占额、扩额/新campaign、压缩/检查/修订调用 | 不重置用量、不换根绕过、所有请求计入；取消提交后不准入新的收费收尾操作 |
| 会话/UI | 双击、刷新、重连、应用重启、新chat、用户纠正、摘要过期、跨turn引用 | 单次提交幂等；历史可恢复；新chat隔离；纠正和引用正确 |
| 研究语义 | 直接事实、缺证补检、动机与替代解释、先后≠因果、角色观点、考据/社区文章/作者未知、同文不同条件、桥梁证据 | 简单题不过度研究；复杂分析有依据、能修正且合理停止；OBC收录或hash核验不等于官方客观事实 |
| 故障交付 | 非关键失败、关键查证失败、来源失败、预算终止、用户取消/派发竞态、末段流断 | 合格既有成果保留；结论范围与失败一致；取消前后准入可判定；草稿不标最终 |

代表历史案例必须保留诊断层次：旧Assembly Q020说明已召回关键关系可能被组包挤出；Q037说明增加背景文字不等于补回直接证据；Q022/Q030有上游范围问题；新BGE研究Q018/Q022有直接证据后移。不得用一个“模型回答不好”掩盖不同故障位置。

对连续研究至少覆盖：提出问题→追问某解释依据→补充材料→否定一个前提→要求重新分析→关闭重开继续。加一组新chat隔离、一个反证未找到但可给有限结论、一个反证成立需撤回旧观点，以及用户剧情纠正与现有原文冲突的案例。

不在本Plan虚构成功率或时延目标。代表集出现上述阻断性缺陷必须修复或明确限定不可采用的范围；语义最终采用由用户看实际结果决定。样本数与付费规模在G7提案时另定，不以重复调试的70Q宣称独立泛化验收。

## 10. 文档入口与历史证据

### 10.1 当前合同与数据

- [当前阶段](../current-phase.md)、[架构](../architecture-overview.md)、[模型接口与共享调用](../model-interfaces.md)。
- [Clean V1](../phases/clean-v1.md)、[Chunk V2](../phases/chunking-v2.md)、[统一索引输入](../phases/indexing-input-v1.md)、[Retrieval V1](../phases/clean-retrieval-v1.md)、[Reranking V1](../phases/clean-reranking-v1.md)。
- [P4完整交接及接口示例](clean-reranking-p4-handoff-20261113.md)、[Python迁移证据](python314-migration-20261113.md)。
- config/index-selected-v1.json、config/clean-retrieval-v1.json、config/reranking-selected-v1.json。旧比较配置不作新运行配置模板直接覆写。
- data/retrieval/rerank-v1-handoff-20261113/：70题已封存排序材料；data/retrieval/python314-compatibility-20261113-r3/：迁移证书。现场应只读核对存在性/hash，不重跑已完成实验。

### 10.2 必须保留的历史教训

- [旧combined Assembly](phase-04/p04-assembly-retention-70q-20261104.md)：去重释放预算与关键证据真正入包分别评价；旧字符预算不是新合同。
- [DeepSeek三例](phase-04/p04-deepseek-live3-20261102.md)：精确1017、TokenMetro、两例合格和一例输出cap失败；有限QA不是深度研究能力证明。
- [Strict Function Calling](phase-05/p05-chat-strict-function-20261031.md)：较早flash型号一次强制Card输出通过，不证明1017自主工具循环或服务器严格schema执行。
- [Card修复](phase-05/p05-card-live66-repair-20261101.md)、[语义失败](phase-05/p05-card-semantic-live6-20261102.md)：原文片段ID/程序提取可复用；机械完成不等于语义正确，旧摘要可能固化错误或过度保守。
- [E2探索](phase-05/p05-navi-e2-autonomous-exploration-20261012.md)、[后续协议修复](phase-05/p05-navi-e2-gpt6luna-story-exploration-r3-20261016.md)：工具回执/预算/恢复经验可复用；历史PARTIAL与冻结结果不可改写为成功。
- [召回70Q有限复核](recall-p4-70q-quality-review-20261113.md)、[BGE/GTE比较](clean-reranking-p2-comparison-20261113.md)、[结构/MMR比较](clean-reranking-p3-comparison-20261113.md)：采用范围与语义局限分开，不重新选模型/开MMR。

外部交接目录的技术总账第12–14节和06长期教训用于理解沿革，不替代current-phase；其中全局记忆和模型未定表述已被本文第1节的最新用户决定取代。本任务不授权改外部交接文件。

官方框架依据：[LangGraph概览](https://docs.langchain.com/oss/python/langgraph/overview)、[checkpoint与持久化](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[重试/副作用边界](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)、[LangChain结构输出及自动错误处理](https://docs.langchain.com/oss/python/langchain/structured-output)。官方DeepSeek能力仅作为待验证接口参考，不替代TokenMetro实证。

## 11. 新会话执行说明与待决定项

### 11.1 建议给新会话的启动说明

以下文本可随实际工程授权一起交给新建Codex 6.1-Sol会话；它不是本Plan作者已经发出的实施或付费授权：

> 请先读取AGENTS.md、docs/current-phase.md和docs/research/clean-generation-implementation-plan-20261114.md，核对实际HEAD、工作区、选定配置及保护清单。按本次消息明确授权的阶段实施；若本消息未指定实施范围，先报告当前可执行边界。承接D01–D24，不重新选择框架、生成模型/渠道，不恢复全局记忆。默认只做已授权离线工程、fixtures/Mock及有限本地检查。所有真实TokenMetro请求、长期/全量任务、采用切换、数据删除、Git提交推送都必须有对应明确授权。每阶段报告改动、验证证据、未实证能力和下一门禁；不要因Live门禁未开放而停止独立可做的离线工程。不得修改旧hash来绕过校验。

新会话先提交短基线差异报告；若现场与4757232不同，审查相关差异，不reset/revert用户改动。逐阶段小改动，不能一次把全部目录/抽象铺开。遇到渠道不支持/版本冲突/受保护核心必须修改，给出具体证据和最小调整方案，不自行扩围。

每阶段收尾更新真正受影响的owner：current-phase、Generation合同、模型接口、必要的架构/项目结构/data README。搜索受影响tracked文档中未限定的current/next/NOT AUTHORIZED/NOT STARTED/默认模型/目录不存在等陈旧描述；历史实验保留日期和边界，不机械改成新状态。

### 11.2 仍需用户在相应门禁决定的事项

1. 新会话的工程实施阶段授权；本轮仅保存Plan，不含该授权。
2. TokenMetro精确1017的有限付费能力验证额度及风险边界；随后才确定实际运行资源profile。不是重新选择模型/渠道。
3. 真实请求/token/时间/工具轮次/上下文/输出预算及费用口径。缺失时offline继续，Live拒发。
4. 如原生工具或动态thinking无法达到要求，是否采用明确JSON备选或调整任务模式；不能自动实施替代。
5. S0闭环之后是否启动可选G6 Sparse/S2增量工作单；S1并非必做。
6. 基于G7结果是否采用新默认入口，以及是否提交/推送。未批准保持opt-in。

无需用户重新决定：本地单用户定位、LangGraph混合、唯一TokenMetro/1017、单控制器、证据/推论边界、同chat本地恢复/新chat隔离、暂不全局记忆、按需主动研究、失败部分交付、流式草稿及有限评价方向。

## 12. 本轮Plan审计记录

审计性质：需求追踪、源码静态核对、历史证据复查、阶段依赖与测试覆盖审阅。不是实现测试、渠道验证或产品验收。本轮无模型推理、付费请求、上游数据变更、默认切换、Git提交/推送。

初审发现并已纳入计划的关键事项：

| 发现 | 本Plan处理位置 |
| --- | --- |
| 全局研究记忆与用户最新范围冲突 | 1.1、6、G4：收窄同chat持久化；不建设跨chatStore |
| 原QuerySpec/重排接口无法直接接受多轮，改核心会破坏迁移hash | 2.3、3.4、G0/G2：新增V2适配并保护旧核心/证书 |
| Dense常驻与Reranker并存可能导致显存不足 | 3.2、3.4、G1/G2：全应用GPU lease与worker退出确认 |
| 空候选和部分评分失败没有新产品语义 | 3.4、9：empty正常工具结果；失败不伪完整排序 |
| checkpoint replay可能重复调用，文件/SQLite没有原子提交 | 5.1–5.3、G1：稳定操作身份、账本权威与零网络对账 |
| Batch不等于持久turn预算；恢复换预算根可漏记 | 5.2、7.2：campaign绑定和统一预约入口 |
| 旧DeepSeek适配器拒绝thinking/tools，旧UI只有last_result | 2、5.4、G3/G4：新增版本适配和独立本地执行器 |
| 历史工具测试型号/任务不等于1017研究循环 | 7.3、G3：精确能力profile和独立付费门禁 |
| 工具流半截执行、取消后收尾调用、摘要丢工具关联 | 5.4、6、9：完整终态后执行、取消禁止新调用、协议单元保护 |
| 仅用70Q handoff会误称实时集成完成 | G2/G7：离线Assembly与新查询实时链分别验收 |
| 多查询logits混排及模型选段越界可能侵犯上游职责 | 3.3–4：查询评分分离、组内排序、程序源范围验证 |
| Live未授权可能误阻塞全部实施，或Plan被误当付费批准 | 0、8、11：离线依赖与Live门禁分开 |

### 12.1 落盘后复审与修正

完成产品/历史范围、上游接口/资源、调用账本/恢复三个方面的只读复审。复审新增发现已修正：

1. Assembly对照固定排序候选/回读输入/预算，Generation对照才固定Packet，避免控制变量失效。
2. G7明确依赖G4/G5的完整S0闭环；真实验收额外依赖G3-Live及当批授权，G6可选。
3. 将已封存的成功/终止操作与仍待重试attempt分开；pending_retry不在对账时自动发送。
4. 明确取消与派发准入的同步边界、在途请求语义和竞态测试，不承诺撤销远端已发生效果。
5. 封存replay前同时核对budget/ledger outcome；逐attempt核对用量，不把call聚合数写入单次预约。
6. 明确用户事实纠正仍需来源核验，并加入OBC收录不等于官方事实的验收案例。

文档检查：24项决策编号唯一且齐全；G0–G7阶段均存在；21个本地Markdown链接全部指向现存文件；新增Plan及current-phase入口无whitespace错误。改动范围仅本Plan和current-phase的13行规划入口，没有源码、运行配置、数据或外部交接变更。

审计结论：设计/接口/依赖层面的复核通过，未发现尚未处理的阻断性遗漏。此结论不代表框架安装、GPU接线、TokenMetro能力或回答质量已通过运行验收；相关实证与用户门禁仍按第7、8、11节保留。本轮没有运行产品测试或模型，只有只读检查及文档一致性验证。
