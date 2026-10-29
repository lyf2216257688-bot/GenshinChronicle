# Navi v0.3 运行准备与故障恢复审计

2026-10-28：**离线工程准备完成；未授权、未执行真实调用。**
运行协议为`p05-navi-v03-runner-1`，语义候选仍为冻结的`p05-navi-b-v0.3-rc1` /
`story-admission-v1`。Prompt、剧情准入规则、v0.2完整本地Schema和上下文校验均未改。
本轮是调用与恢复审计，不是新的语义Prompt优化。

## 已确认的问题与补齐范围

原`navi_prompt_candidate.py`确实只有未发送请求构造与内容验证。F2 SDK入口会调用旧版
请求渲染器；F2正式执行器和诊断器还绑定旧Prompt、样本和全局路径，不能直接作为v0.3入口。
F2后续曾有“内容失败后进入下一槽”的授权，本轮没有沿用：v0.3单请求运行遇内容失败即停止。

新增独立模块[`navi_v03_runner.py`](../../../src/genshin_corpus/retrieval/navi_v03_runner.py)，
只复用成熟的底层原始字节留存、解码、cause-chain诊断、发送前trace判据、SSE解析及
消息/流核验函数；没有调用任何F2发送入口、没有猴子补丁替换旧runner的全局请求。
继续使用现有SDK/httpx/jsonschema，不增加依赖、Agent或通用调度框架。

准备阶段还发现一个实际身份问题：如果存档时排序view键，再重载并嵌入Prompt，模型可见
JSON文本及请求指纹会变化。新存档函数保留原键序；不修改冻结候选的渲染器或Prompt。
每次重试除重新验证版本/计划/来源/请求指纹外，也在请求钩子中核对首个尝试的实际正文
字节哈希，不能以JSON对象语义相同掩盖请求正文变化。

末尾复核还用一个未知SSE事件构造反例，确认旧的纯检查器会忽略它。新入口另加有限的
message/reasoning事件白名单和事件绑定检查；未知事件直接失败，不改写旧检查器或历史
成绩。该反例现已纳入同一组针对性测试，没有增加新Prompt或通用协议框架。

## 身份、证据与存档

`prepare`一次只冻结一个请求，不创建客户端或读取凭据：

- 核对原候选冻结清单的身份哈希，以及Prompt、候选代码、旧Schema、旧绑定校验与输入检查器的文件哈希。
- 复用F1的score-free `model-inputs/rc1`**来源view**和审核原句注册表；由v0.3构造器重新生成新请求，绝不读取旧F2 Prompt去发送。
- 复制本次来源Canonical快照，逐段验证文本/字符范围、Canonical文件哈希、record/source、完整RU重建及alias端点。材料缺失或结构冲突直接失败。
- 固定候选/运行器版本、路由、模型、输出上限、格式、来源输入、wire指纹以及执行代码哈希；计划和文件清单均create-only。
- 计划始终`send_authorized=false`。默认格式仍是json_object，json_schema仅显式选择，不自动探测、切换或降级。

原候选freeze还包含当时current-phase等文档快照。这些历史快照不随本轮文档维护重写；
运行准备校验的是其中被冻结的**语义引擎文件**和freeze身份，新的执行文件另行绑定进计划。
旧freeze本身及其数据文件完整保留，不把后来更新的当前状态文档要求成历史原值。

真实`run`须另获用户授权，同时提供执行开关及精确plan文件SHA-256确认；凭据仅在这之后
读取，路由限定为既有jizhi/deepseek-v4.1-flash Responses，无模型或端点回退。
每次SDK/底层传输自动重试均为0，环境代理与自动跳转关闭。

发送前先持久化指纹唯一的跨运行reservation、运行配置、独立attempt目录和请求记录；
请求钩子在发送前保存实际正文。响应钩子先以`RawTee`留存传输体字节（保留Content-Encoding），
再由SDK解码SSE；SDK事件另存且逐条flush/fsync。HTTP错误与部分响应同样保留。
异常记录只存类型/OS代码，凭据、Authorization头和任意异常原文不进入审计。

## 严格验证与有限恢复

完整可信响应必须同时满足终态/模型/响应身份、唯一完成事件、有效usage及上限、raw/SDK
事件精确一致、message与reasoning身份、item快照与次序、事件位置及序号（若提供）、
delta/done/terminal文本一致、完整SSE帧，以及v0.3 JSON/模式Schema/完整旧合同/上下文绑定。
reasoning ID冲突不能因message可读而通过；不剥围栏、不删除重复键、不补截断正文。
程序仍负责每命题处置及Pair汇总，内容通过也只产生INACTIVE研究候选。

每个请求总计最多3次尝试，只有以下证据**全部成立**才能在同一进程内继续：

1. 存档完整、精确文件集合与哈希吻合、请求钩子只执行一次，正文与冻结wire相同。
2. trace明确记录TCP连接或TLS握手失败，且没有任何请求头、请求体、响应头或响应体事件。
3. 没有HTTP响应、原始响应文件或SDK事件；reservation、版本、计划、路由及重试配置全部一致。
4. 下一次发送前再次检查计划/代码/来源/请求身份及实际正文哈希，且未超过3次。

单次记录中的`PRE_SEND_FAILURE_CANDIDATE`只是待核验证据，不等于NOT_SENT。
独立存档检查通过后，重试决定才记录pre_send_proven=true；耗尽则最终NOT_SENT /
NOT_ISSUED。trace缺失或冲突保持UNKNOWN；本地处理异常也不自动重发。
尝试间固定等待0.5秒，各有独立目录、result哈希和重试决定。

已发送/UNKNOWN、任何HTTP响应后故障、HTTP 3xx/403/429/5xx、流不完整、截断、usage异常、
JSON/Schema/绑定失败均禁止自动重发，保留终态与usage观察并停止。命令行实际执行失败
返回非零退出码，不把错误当作下一条请求的放行信号。未验证usage不假装为0或账单。

默认持久账本为`data/retrieval/p05-navi-v03-request-ledger/`，以wire指纹占位；改输出目录或
请求名称不能绕过同一账本中的重复发送保护。已有reservation（包括崩溃后未完成者）
一律阻止重新运行；没有自动resume、自动清理占位或覆盖旧文件。Python调用者也必须复用
同一持久账本。删除/更换账本会破坏此保证，不能用它当恢复操作。

## 已实际完成的离线验证

**97项测试通过，0失败/错误/跳过**：新增19项针对性测试，合并原候选、v0.2完整合同/
Schema、F2 Preflight、底层SDK及message verifier回归。新测试均使用小型合成Canonical/RU
和MockTransport，不依赖全量Raw。覆盖：

| 范围 | 结果 |
|---|---|
| 生成/审核 × json_object/json_schema | SDK实际序列化与冻结v0.3请求一致；raw存档、内容验证与只读重放通过 |
| 发送前TLS失败两次后成功；TCP失败耗尽 | 指纹和实际body字节相同，前者3次内成功，后者3次后停止 |
| 无trace、请求/响应事件后失败 | UNKNOWN且只尝试一次 |
| HTTP 302/403/429/500，即使带TLS失败trace | 保留错误字节、不跳转、不重发 |
| 断流、缺终态、残缺帧、明确输出截断、failed终态 | 全部停止；保留原始响应和可观察usage |
| reasoning身份冲突、未知事件、ghost item、item快照/次序/序号/模型/响应ID/内容位置不一致 | 全流不通过，不以可读message放行 |
| usage布尔值、加总错误、超输出上限、reasoning超额 | 拒绝，保留观察值 |
| 重复键、围栏、非法生成角色、模型越权填写汇总、缺字段、跨端证据、审核原句改写 | 内容失败不重发 |
| 混合A与U | 程序保留独立处置，Pair有候选不覆盖U |
| 重命名运行、同wire新计划、崩溃占位、计划/请求/正文/源证据/历史存档变化 | 阻止重复发送或停止；不覆盖历史 |

另外对两对真实冻结F1/F2来源（两剑生成、赌徒—学士审核）执行了**4次持久Mock运行**，
覆盖两种格式，随后从存档只读重放全部通过。socket连接拦截器记录**0次真实连接**。
最终结果为`offline-verification-final.json`；未知事件加固之前的4份持久Mock及旧计划原样
保留为早期检查点，不计作当前执行代码的证明。因此本轮持久Mock共8次，全部无真实连接
或服务商请求；最终代码的4份使用独立测试账本，不能外推为允许重建真实调用账本。
响应来自原有agent编写回归，不是服务商生成或人类金标准；不能作为语义质量提升证明。
两份Schema通过保守结构子集检查及SDK传递检查，完整本地校验仍在。

原有**4,758份受保护历史文件及文件集合不变**，包括v0.2/F2和上一轮v0.3冻结证据。
候选Prompt/准入/校验实现及并行Embedding代码配置未改变。新证据根为
`data/retrieval/p05-navi-v03-run-readiness-20261028-r1/`，含计划、原始Mock响应/事件、
持久Mock账本、验证结果、测试记录、历史完整性与本轮产物清单；不写回旧根。

## 使用与当前结论

从仓库根执行的PowerShell 7离线示例：

```powershell
$env:PYTHONPATH = 'src'
python -m genshin_corpus.retrieval.navi_v03_runner prepare --source-root data/retrieval/p05-navi-f1-20261026-r2 --pair-id 88d6d29f5017cfd5a644 --mode generation --format json_object --plan data/retrieval/p05-navi-v03-example-plan
python -m genshin_corpus.retrieval.navi_v03_runner run --plan data/retrieval/p05-navi-v03-example-plan --out data/retrieval/p05-navi-v03-example-run
```

第二条默认只报告DRY_RUN，不读取凭据、不创建运行目录、不发请求。
另行授权真实执行时才使用`--execute-authorized`及`--ack-plan-sha256`，确认确切计划，
不得把本报告或离线通过当作该授权。`audit --run <目录>`仅做只读完整性及成功内容重放。

**具备开展有限真实试验的离线工程条件；当前仍未授权或执行。**
真实网络/代理连通性、所选请求形态的线上接受与流完整性、服务商严格Schema执行、模型
语义效果和实际账单仍为UNKNOWN。后续可在独立有限授权下测这些事实；它们不要求继续
重构Prompt或扩建测试框架。json_object不保证Schema；官方
[Structured Outputs说明](https://developers.openai.com/api/docs/guides/structured-outputs)
也不能认证第三方代理。MockTransport通过只证明本地构造、SDK传递和核验路径。

本轮收费模型/API请求0、线上探测0、Embedding调用0，无Navi发布、生产切换、commit或push。
Prompt优化循环继续停止。
