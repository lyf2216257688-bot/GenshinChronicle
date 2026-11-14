# Clean Generation G0–G2：有限工程验收与交接

日期：2026-11-14（Asia/Tokyo）。依据[已确认 Plan](clean-generation-implementation-plan-20261114.md)，
承接全部 D01–D24；合同见[Generation V1](../phases/clean-generation-v1.md)。
当前状态与授权仅由[current-phase](../current-phase.md)维护。

本报告记录Git收尾之前的工程验收检查点。交付 G0–G2 的工程骨架与有限真实本地接线；没有远程生成回答，
没有付费请求、G4–G7 实施、默认切换、删除既有资产、Git 提交或推送。
**工程/来源 PASS 不等于问答语义或连续研究验收。**

## 阶段交付与条件

| 阶段 | 实际交付、检查 | 遗留与下一阶段条件 |
| --- | --- | --- |
| G0 | HEAD `4757232` 与 Plan 一致；保留初始未提交 Plan/current-phase；新阶段合同映射24项决策，新增禁用 live 的配置样例；430个既有文件及关键 manifest/seal 的保护清单 | 未改变选择器、冻结核或历史证据；工程授权满足 G1 前提 |
| G1 | 隔离3.14.5常规GIL编排环境；LangGraph/SQLite checkpoint；幂等提交、稳定操作、文件/SQLite恢复、campaign/turn预算、取消/派发顺序、事件、队列与应用/session/GPU锁；Mock CLI | 22项新夹具通过，旧共享调用20项回归通过；满足 G2 离线依赖。仅Mock骨架，非持续聊天产品；不含live发送器 |
| G2 | 新Query/Candidate/Ranked V2；S0/F1_max、BGE structural/MMR off；Job-owned进程轮换；源句柄、CleanRef/Raw链、完整Parent/图回读、结构片段ID、确定性Packet/引用及全量处置；独立CLI | 15项新夹具、57项上游/模型文档回归和2项Job测试通过；3个handoff样例＋1个新问题通过有限本地检查。进入真实G3前仍须独立能力与付费授权；G4–G7未获授权 |

共 **116项相关测试通过**：新测试37、共享调用20、上游/模型接口57、Job生命周期2。
夹具依赖小型checked-in资料，不依赖全量Raw。没有重跑Reranking实验或全量70Q。

## G0 基线与保护

`.local/generation-g0-20261114/`保存初始状态、diff、两份未提交文档原字节副本，
以及`protected.json`/`protected-final.json`。430个既有文件hash全部未变，
迁移证书的`compatibility_code()`也完全一致。包括既有源码/配置/测试/启动器、
Plan、索引/统一输入/Clean/Chunk清单、迁移seal和P4 handoff seal。
这不是重新逐字节验收全量Raw或全部历史目录；实际使用的来源继续通过既有reader核验。
外部交接目录未修改。

## G1 持久化与预算

正式新环境：`.local/runtime-py314/generation/`，与Dense、词法和Reranker隔离。
先在`.local/generation-framework-check/`安装并运行兼容夹具，再将同一wheel集合安装到正式环境。
[requirements-py314-generation.lock](../../requirements-py314-generation.lock)固定41项依赖及SHA256：
LangGraph 1.2.22、checkpoint 4.3.2、SQLite checkpoint 3.1.3；`pip check`通过。
LangSmith为框架传递依赖；没有使用云服务、Store或遥测，tracing显式关闭。
框架存在一条第三方`asyncio.iscoroutinefunction`弃用警告；未影响本轮运行。

图使用`durability="sync"`；准备checkpoint先于effect节点；无框架retry policy。
共享`model_calls.py`和`evidence_io.py`原字节保留。会话桥接在每次attempt预约内
检查持久turn/campaign额度；UNKNOWN占额。取消和预约共用短锁，事件序号记录准入先后。
封存调用重放前补齐/核验逐attempt budget outcome及call ledger outcome，不复制聚合用量。
缺失/截断/错hash和代码不兼容安全阻断；旧成果可只读保留，不自动迁移或重发。

故障覆盖prepared/checkpoint、claim、call记录/plan发布间隙、reserve、首字节、
attempt seal、budget outcome、call seal、ledger outcome和checkpoint提交间隙；
pending_retry只对账，继续仍需原策略/预算。工具回执事件可复用而不重复先前步骤。
应用锁、FIFO排队、跨进程竞争、取消准入前后、已知终态保留、超限后停止后续准入均有夹具。
正常Mock执行也进入`offline_only`，不是仅依靠“没有配置密钥”。

最终Mock根`data/generation/g1-mock-20261114-c/`的两次独立CLI进程调用复用同一预约；
操作身份同时绑定共享调用、桥接及SQLite状态代码。实现过程的较早Mock根保留为
冻结检查点，不自动迁移。
运行日志、依赖核对和CLI回执位于`.local/generation-g0-20261114/`。

## G2 来源与实时接线

全部新增适配在`*_v2.py`；旧QuerySpec继续拒绝改写与多轮。
V2分别绑定original_question、subquestion、search_text和rerank_text，并校验祖先、缺口与范围。
评分投影显式标为`gc-clean-scoring-input-2`，保存V2映射和digest，不冒充旧原问题。
原问题fixture的融合、完整排序与V1一致。候选/原命中/来源字段保持完整；
空结果跳过BGE；缺分、错模型/输入、非有限值、超长和OOM显式失败，无自动fallback。
在线适配只引用已选索引/模型配置，不依赖70Q评分目录。

来源回执将准确原文、条件/未知状态、Parent定位、CleanRef和完整Raw origin链保存为文件；
核验Clean/Chunk/Parent字节与身份，不重新宣称全量Raw保真或OBC内容均为官方事实。
模型可见投影与完整来源回执分开。片段以完整Chunk或自然容器作为准入边界，
选段只能使用程序交付ID；不剪掉否定、条件或未知分支。
当前未做自然容器内的细粒度切段；大容器可完整回读，但预算不够则整块排除。
这是后续按具体需要细化的能力限制，不能将整块排除当作研究充分。

### 保存handoff回放

`data/generation/g2-handoff-20261114-a/`消费封存70Q handoff中的3个诊断样例，
不是重新运行70Q，也不是70题质量评估。每题完整100候选均回读成功。
预算仅为本轮明确标记的离线工程工作点：context=16000、output reserve=1000、thinking reserve=0；
按完整消息UTF-8字节保守估算，不是供应商精确token、费用保证或产品默认。

| 样例 | 回读/来源失败 | 入包 | 预算排除 | 已证明覆盖去重 |
| --- | --- | --- | --- | --- |
| Q020 | 100 / 0 | 1 | 99 | 0 |
| Q037 | 100 / 0 | 1 | 99 | 0 |
| Q050 | 100 / 0 | 2 | 96 | 2 |

三题共300候选全部有处置；重复Assembly和最终代码从保存回执重放均逐字段一致。
Q020/Q037的历史问答问题没有因此被重新判为解决；尤其入包少不证明证据足够。

### 一个新问题的真实本地链

问题：“雷内的调查笔记中如何描述世界式与未来？”

输出：`data/generation/g2-new-query-20261114-a/`。

- S0原始深度50/候选100/F1_max；Dense与BM25F各自对原独立搜索核验PASS。
- 100候选完整BGE评分；最长配对1067 token，低于本轮2048显式工作点，无截断。
- BGE冷加载约4.00秒，评分约6.69秒；上游run约103.81秒，不含InputReader初始准备及后续Assembly。
- BGE peak allocated约1.18GiB、peak reserved约1.44GiB；有限单题观察，不是通用显存保证。
- Dense/BM25F/BGE三个实际解释器PID均确认退出；进程轮换无GPU共驻，运行后GPU compute进程为空。
- 100条原文回读、0来源失败；Packet入包3块、预算排除97块；估算消息14701、输出预留1000。
- 远程调用0；没有生成答案，不存在语义PASS或精确端到端问答时延结论。

`data/generation/g2-acceptance-20261114/`另存4题Packet最终重放、引用映射和
Q020/Q037完整自然对白组/图的真实回读（分别146/137个原文定位），全部PASS。
图结构与自然Parent一致，顺序仍为`display_only`；未推断剧情时间、条件成立或因果。

## 显式入口与后续条件

PowerShell 7，在仓库根执行，设置`PYTHONPATH=src`并使用独立Generation环境。
以下查询必须改为一个全新输出名；它执行本地推理，没有远程LLM。

```powershell
$env:PYTHONPATH = 'src'
./.local/runtime-py314/generation/Scripts/python.exe -m genshin_corpus.rag.clean_local query `
  --output data/generation/my-new-local-check `
  --question '新的有限工程问题' `
  --context-tokens 16000 --output-tokens 1000 --thinking-tokens 0
```

不要为本次交付重复运行上述已封存根。G1 Mock入口为
`python -m genshin_corpus.rag.clean_cli mock`；中断研究必须显式`--continue-turn`，
重启不自动派发。当前配置样例的全部live上限保持null。

本轮没有实现G3远程适配器，只有路由/能力profile及live拒发边界准备。
G3-Live需要明确的精确profile、样例、请求/尝试、输入输出/总预算、超时与UNKNOWN风险授权；
原生tools、thinking和流式能力仍未实证。G4–G7由用户另行决定，默认入口不切换。

框架接口依据：[LangGraph概览](https://docs.langchain.com/oss/python/langgraph/overview)、
[持久化/checkpointer](https://docs.langchain.com/oss/python/langgraph/persistence)。
版本号与兼容性由实际锁文件/安装/测试支持，不用官网声明代替本机验收。
