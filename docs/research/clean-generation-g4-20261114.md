# Clean Generation G4：离线连续对话与本地界面

日期：2026-11-14（Asia/Tokyo）。正式依据为[Generation Plan](clean-generation-implementation-plan-20261114.md)
的 D01–D24、G4 与第5/6/9节；持久合同见[Generation V1](../phases/clean-generation-v1.md)。
当前授权、状态和下一步仅由[current-phase](../current-phase.md)维护。

本文保留初次交付检查点。此后主工作树完成关闭thinking的工具/SSE有限Live验证；
本工作树又进行了小规模修复。最新判定、测试与G3/G4汇合条件见
[最终审计](clean-generation-g4-final-audit-20261114.md)，下文旧能力门禁不覆盖该新进展。

本轮授权仅为独立工作树中的 G4 离线工程。初始工作区干净，HEAD 与本地 main
均为 `03379637f7bc56f953a1023d92199ca98e8f8376`，包含最新 G3；其父提交为 G0–G2
的 `8be6872`。已读取 AGENTS、current-phase、合同、Plan、G0–G2/G3报告及免费参数核查；
补充架构商议聊天只用于理解沿革，未重新选型。

**交付是 Mock 工程闭环，不是剧情问答语义、主动研究或真实渠道验收。**
没有模型/付费 API 请求、真实检索/重排推理、G5–G7 实施、默认入口切换、上游数据修改、
自动合并、Git提交或推送。没有改写原 Plan、G3专属适配器、model_calls、会话调用桥接或旧 Store。

## 实际交付

| 需求 | 实现与边界 |
| --- | --- |
| 同chat连续对话、关闭恢复 | `clean_conversation.py`扩展本地SQLite，完整消息、要点修订、上下文、来源和结果以不可变文件/hash关联；新chat使用独立campaign与历史 |
| 幂等提交 | SQLite保存编辑草稿及submit token；刷新/双击复用原turn；修改文本才分配下一个token，明确重复相同文本可建立合法新turn；提交元数据也不可改变 |
| 提交/流程恢复 | create-only提交意图先于G1 turn，修复turn/job/message发布间隙；同步LangGraph分开context/source/prepare/answer/check/finish；恢复沿用原op和预算 |
| 上下文自动整理 | `clean_context.py`按配置余量建立确定性摘录索引；近期原始交流、当前要求/纠正/要点和完整工具协议单元受保护；完整本地历史不覆盖 |
| 用户纠正与研究状态 | 显式类型包括要求、用户剧情断言、假说、未解问题、意图纠正、剧情纠正；纠正失效目标前提及依赖闭包；无目标纠正保守失效该类旧前提 |
| 固定链 | `clean_conversation_graph.py`复用V2融合/结构排序、EvidenceReader/Assembly、G3 ChatSessions和唯一共享调用路径；没有自动改写、补检或研究工具循环 |
| 来源与引用 | 实际Clean/Chunk/输入reader、原文/条件/结构/Raw定位、自然Parent回执；地址为turn + Packet + E编号；实际Chat envelope和输出预留也受限 |
| Streamlit界面 | `ui/clean_chat_app.py`独立opt-in入口，支持会话选择、新chat、追问/纠正、真实已落盘进度、非最终草稿、最终/部分/中断状态、引用/Parent/定位展开、停止/继续 |
| 本地执行 | `clean_executor.py`管理本地排队和唯一执行所有权；每次只运行一个Job-owned `clean_turn_worker.py`子进程；UI不执行模型传输 |
| CLI与Mock | `clean_conversation_cli.py`提供new/list/show/submit/continue/cancel/demo；`clean_mock.py`复用小型checked-in源夹具和真实派生原语，命中、分数、回答及usage均为明确合成值 |

消息与研究要点使用 `gc-clean-conversation-1`；上下文使用 `gc-clean-context-1`；
图/结果使用 `gc-clean-conversation-graph-1`。新增表与数据目录的完整职责由合同及
[data README](../../data/README.md)维护；没有LangGraph全局Store、跨chat记忆索引或云服务。

## 上下文、纠正与来源边界

整理是可回查的**摘录索引**，不是模型生成的语义摘要。索引记录消息ID、turn、引用定位、
省略范围和完整历史digest；超限时按整条旧索引记录排除，并保留原消息ID审计。
近期完整交流、有效要求/纠正、研究要点及完整协议单元无法容纳时明确阻断，
不截掉工具参数、reasoning字段或关键用户要求。整理无远程调用；未来若增加模型整理，
仍必须经原预算桥接，不能借此豁免费用。

意图/范围纠正直接作为用户要求。剧情纠正、用户补充材料与假说始终标记待核验，
不会因为用户提出、旧助手复述或被整理进摘要就成为已支持事实。旧前提及依赖项标为
`needs_review`，新上下文不将它们重新陈述为当前事实；历史原文仍可查看。
G4没有实现自动语义提取、剧情纠错裁决或对指代理解质量的证明。

最终消息只接收当前问题、必要本chat背景、明确身份的研究要点和已核验Packet。
历史答案属于`historical_assistant_not_fact`，不产生supported judgments。
引用检查只证明编号属于本Packet及来源文件可回查，不证明剧情论证支撑。
错误引用退为部分交付，不发布该答案；源文件缺失/变更则阻断受影响恢复和显示。
Parent展示来自保存的合法源回读，不猜content_id或任意路径；显示顺序仍不是剧情时间。

## 取消、故障与执行器

- 刷新/重连只读记录；执行器启动不重新排入旧任务。未完成任务需要明确“继续”，
  沿用相同turn/op/checkpoint/campaign。队列中的旧轮次也不会因新页面打开而自动启动。
- 用户取消意图持久化，通过既有admission控制阻止后续请求。已准入在途请求如实收尾；
  UNKNOWN继续占额，不创建新指纹重发。完整已封存响应不会被后来取消覆盖为“未发送”。
- 应用关闭以本地shutdown回执停止新派发并协作收尾；必要时Job关闭整个子进程树。
  未完成轮次保留为interrupted，重开只显示。已取消轮次只读，后续追问使用新turn。
- 流断、预算停止、取消及引用失败保留已有合格原文和草稿；草稿不能冒充最终。
  来源/版本问题明确中断，无法从SQLite文本再造丢失证据。没有额外模型收尾调用。

本轮实际解决的两个跨阶段集成约束：

1. G1锁固定`websockets==17.4`，本机Streamlit1.63要求`<17`。保留原锁，
   固定兼容的Streamlit1.67及其24项新增依赖到
   [可选UI锁](../../requirements/requirements-py314-generation-ui.lock)，由其引用原G1锁。
   两份锁在本工作树隔离3.14.5环境安装，`pip check`通过；没有改主工作树或ML环境。
2. G3的`offline_only`临时修改进程全局socket方法；直接在Streamlit进程的后台线程运行
   Mock会与本地socket通信冲突。G4将图执行隔离为本地子进程，复用已有Windows Job owner，
   不削弱离线保护、不修改G3代码，也不引入外部服务。界面测试允许Windows asyncio所需
   loopback socketpair，拒绝外部连接；实际Mock发送仍在子进程的离线保护内。

另将Windows长工作树路径统一为extended absolute spelling，防止共享账本深路径触发MAX_PATH；
该处理只在新增G4 Store中生效。结果预算快照去除仅对单次进程有效的
`new_request_reservations`，保留持久campaign总账，避免重开后不可变结果发生变化。

## 验证与留档

| 检查 | 结果与范围 |
| --- | --- |
| G4后端 | 27项通过：完整对话/隔离、摘要余量和完整历史、纠正依赖、工具协议保护、提交发布间隙、五个graph持久边界、UNKNOWN、预算、来源故障、取消/关闭、代码不兼容、排队/执行器互斥 |
| Streamlit AppTest | 4项通过：发送/引用/Parent、刷新与独立页面重开、纠正/新chat隔离、旧任务手动继续、停止并保留非最终草稿；不是浏览器像素截图验收 |
| G0–G3/旧DeepSeek/模型文档回归 | 隔离Generation环境74项通过，未新增旧SDK依赖 |
| 共享调用核心回归 | 既有全局环境20项通过；该历史测试依赖openai，未为此扩展新Generation环境 |
| 合计 | **125项相关测试通过**，无真实模型请求；不代表问答语义或连续研究质量验收 |
| 依赖 | regular-GIL CPython3.14.5；原框架pins保持；可选UI锁安装及pip check通过 |
| CLI留档与独立进程重开 | 六轮流程共12条历史消息，5个completed与1个partial；CLI退出后另一个独立进程追加第7轮并完成，能够读取此前12条消息；最终14条消息，另一chat为0条，均明确Mock |

最终日志与安装记录位于`.local/generation-g4-20261114/`：
`g4-all-final.log`、`regression-final.log`、`shared-core-tests.log`、
`cli-demo-final.log`、`cli-independent-reopen.log`及依赖安装日志。框架仍有已知第三方弃用警告；AppTest有bare-context提示，
不影响上述检查，未启用云tracing。

六轮最终资料在`data/generation/g4-acceptance-20261114-b/`；其中的“未找到反证”、
“撤回”和“用户断言冲突”均是预编排Mock场景，不是模型完成了研究或剧情语义判断。
中间开发/失败根保留，未自动迁移或覆盖。保护清单447个既有文件hash保持一致，
包括G3源码/测试、共享核心、G1/G2、旧配置/依赖锁及原Plan。
本轮未对主工作树执行写入；本地main引用仍为`0337963`。额外读取主工作树
`git status`被沙箱权限拒绝，未独立确认其是否存在其他会话的未提交改动。
没有对冻结上游数据运行写入、重建或重复实验。

## PowerShell 7入口

从本工作树仓库根运行。UI依赖已在本工作树安装；在其他隔离Generation环境使用时，
先安装可选锁，不安装到Dense/BM25F/Reranker环境。

```powershell
$env:PYTHONPATH = 'src'
$py = './.local/runtime-py314/generation/Scripts/python.exe'
& $py -m pip install --require-hashes -r requirements/requirements-py314-generation-ui.lock
$env:GC_CLEAN_CHAT_ROOT = 'data/generation/g4-my-mock'
& $py -m streamlit run src/genshin_corpus/ui/clean_chat_app.py `
  --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

完整离线CLI示例使用新的数据根，不覆盖本次验收根：

```powershell
$env:PYTHONPATH = 'src'
& ./.local/runtime-py314/generation/Scripts/python.exe `
  -m genshin_corpus.rag.clean_conversation_cli --root data/generation/g4-my-cli demo
```

其他入口：`new`、`list`、`show --session ...`；
`submit --session ... --submit-token ... --question ... --run`显式执行新提交；
`continue --turn ...`恢复未完成轮次；`cancel --turn ...`保存取消并进行确定性对账。
命令没有Live选项，不读取API凭据。仓库需保留小型checked-in源夹具以运行Mock。

## 待G3 Live与后续合并条件

**仍待真实验证：**精确TokenMetro/1017的动态thinking、reasoning回传、完整原生工具循环、
流式终态/usage及取消行为。G3原有2次请求/212报告token、thinking未获证实及tools/streaming
未发送的历史结论保持不变。本轮不消耗原探针剩余额度。

真实固定链问答还需G3 Live通过、当批有限授权/预算、真实新问题S0/BGE与会话上下文的
联合验收，以及来源支撑/指代/纠正语义人工复核。当前只证明Mock工程行为，
不把确定性摘要或合成答案当成真实模型能力。主动补检、竞争假说研究闭环和Sparse增量
分别保留G5/G6门禁，完整质量采用保留G7门禁。

离线G4代码可以独立审查，不必等待G3 Live才能合并Mock工程。合并前需：

1. 审阅此独立工作树diff；若main移动，核对新增变化并在合并基线上重跑相关检查。
2. 保持原G3/共享核心/Plan/旧选择器及冻结证据hash，验证可选UI锁不会改变原pins。
3. 仅合并源码、锁、测试和文档；排除完整会话、生成数据、运行环境、日志和凭据。
4. 用户另行授权Git收尾；默认入口与远程能力门禁继续保持。合并离线代码不构成付费、
   G5–G7或采用授权。

实现参考：[LangGraph持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、
[Streamlit fragment](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment)、
[AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)。
依赖兼容性与上述结果以锁文件、本机安装和测试为证，不以官网功能声明代替运行验收。
