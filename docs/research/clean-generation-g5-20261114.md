# G5 主动研究实施与有限验证

2026-11-14（Asia/Tokyo），起点 `main@98e8c85`，初始工作区干净。
当前状态以 [current-phase](../current-phase.md) 为准；持久接口在
[Generation 合同](../phases/clean-generation-v1.md)，设计遵循
[Astra Plan](clean-generation-implementation-plan-20261114.md) D01–D24。

## 实施

`rag/clean_research.py` 增加单控制器 LangGraph 循环：先看原问题 S0/BGE
结果，再按缺口使用原生 search/read_source/select/finish。支持口语/指代改写、
子问题、Step-Back、多跳的来源句柄依赖和反证查询；这些是模型按需选择的动作，
不是每题固定四步，也没有多 Agent 团队。原问题始终保留，search/rerank 文本分开，
各查询排序组独立，最终 Assembly 不比较跨查询 logits。

复用 G2 LocalPipeline/EvidenceReader/Assembly、G3 原生工具/SSE/ChatSessions、
共享 model_calls 和 G4 会话、上下文、纠正、Job 执行器及 Streamlit。入口仍默认 Mock；
只有明确设置 `engine=gc-clean-research-1` 的新会话选择 G5。没有新依赖、模型选型、
上游重建或默认入口切换。原 G4 固定链和已经关闭的批次保留。

每个请求准备与 checkpoint 先于发送；完整工具终态/参数验证先于执行；
工具回执、研究快照先于工具历史/checkpoint。恢复沿原 operation/预算根对账，
已封存调用和工具结果复用；UNKNOWN 不重发。复杂研究结束后用新的操作和最终
Packet 综合，避免中间 E 编号误用。简单直接事实可一次请求结束。

研究状态保留子问题、假说、反证、未解事项和停止原因。近期同 chat 研究状态可回查，
用户纠正使继承模型前提标为需重审；新 chat 不继承。原文事实、模型分析和用户断言
的身份分开。引用检查不被当作语义支持检查。

## 有限资源与停止条件

用户授权本轮必要实施、离线测试和总计最多 10 次新付费请求，委托 Codex 制定有限
工作点。固定 TokenMetro / `deepseek-v4.1-flash-1017`，thinking disabled；
不以普通工具能力通过追认动态 thinking。

| 边界 | 本轮工作点 |
| --- | --- |
| 全批 / 每 turn 请求 | 10 / 4，所有角色、失败和综合均计入 |
| 全批 / 每 turn 保守 Token | 670,720 / 268,288 |
| 单请求输入 / 输出 / context | 65,024 UTF-8 字节 / 2,048 Token / 67,072 |
| 请求 / 研究 turn / worker 超时 | 120 / 1,800 / 1,800 秒 |
| 研究轮 / 检索 / 工具 / 读取 | 最多 3 / 4（含初检）/ 8 / 32 |
| 自动读取 | 每组前 6 个候选；其余保留 not_read 处置，可按句柄回读 |
| 重试 | max_attempts=1，format_retries=unknown_retries=0 |
| 授权准入窗口 | 准备后最多 2 小时，不自动续期 |

最后一个请求额度优先用于综合。重复查询不重跑，连续两次搜索/阅读无新增已核验
receipt 身份则停止 no_gain。充分、有限结论、无增益、预算、取消、deadline 和故障
分开；预算终止不等于证据充分。取消/过期后没有收费收尾摘要。UNKNOWN、协议/用量/
身份异常和来源失败阻止后续 live 准入。保守字节预留、供应商 Token 和账单金额分开。

## 本地 RAM 门禁与有限验证记录

首个准备根 `data/generation/g5-live-20261114-a/` 的第一次检索在 Dense 编码前
被原有 RAM 门禁拒绝。失败发生时尚无生成 operation、预约或付费请求；独立对账确认
**0 operation / 0 reservation / 0 API**。未降低门禁，未改上游输入、索引或模型。
失败输出与 worker 日志完整保留，原代码保存并封存于 `live/code-snapshot/`；
`live/closed.json` 已关闭此零调用准备，不能与后续准备叠加使用。

当时本地可用 RAM 约 2GB；操作者随后明确回复“已清理，继续”，可用 RAM 回升到
4,211,761,152 字节。新准备根 `g5-live-20261114-b/` 明确关联旧根零调用账务，
总授权仍是原始 10 请求，不叠加、不扩额。第一次 Dense 阶段已通过门禁。

新根首题成功完成真实 S0/BGE 和原文组包，但首次真实模型请求只收到 PING 保活，
随后超时，自动关闭该批。**实际 1 请求 / 1 attempt，0 重试，1 UNKNOWN**；剩余
9 请求关闭，不作为以后继续派发的许可。未发送第二题，没有重发第一题或换根绕过 UNKNOWN。

| 项目 | 实际观察 |
| --- | --- |
| 问题 | 《尘世之锁》的信物/挑战、正式契约及“后来解开锁所以缔约”假说 |
| 本地结果 | 100 个有序候选，6 个原文读取，3 个入包，0 来源失败 |
| 处置 | included 3、budget_excluded 3、not_selected/not_read 94，全数留档 |
| 模型请求 | 32,101 字节，thinking disabled，原生 G5 schema，SSE |
| HTTP / 原始返回 | 200；64 字节，即 8 段 `: PING` 注释，无答案/工具/usage/终态 |
| HTTP 交换耗时 | 121.49 秒，客户端超时，不能据此断言服务端没有处理或计费 |
| 完整 turn | 256.09 秒，结果 partial，通用停止标签 model_fault；原调用状态 UNKNOWN |
| Token | 供应商用量 UNKNOWN；known_tokens=0 只表示没有已知用量，实际值不是零 |
| 预算 | 67,072 保守 Token 整份占额；无额外预约 |
| 费用 | 金额与币种 UNKNOWN；没有可核验账单，未猜价格或调用未知账单接口 |

**真实研究效果没有验证成功。** 模型没有返回完整控制决定，因此实际工具动作 0、
改写/拆题/Step-Back/补检/反证/连续研究均未获得 live 语义观察。不能把首个超时解释成
原生工具不支持，也不能确定是排队或其它服务处理问题；现有响应没有足够诊断事实。
工程/来源通过和真实研究语义未验证分开报告。

来源复核：E01/E02 是同一 OBC `content_id=1222` 的部分重叠范围，直接交付少女所说
“盟约的信物/挑战”、叙述的“没有正式的契约”及“许多年都没能解开”的原文。
E02 保留后两句限定，不能将“许多年没能”写成“从未尝试/永远打不开/后来已经解开”。
E01/E02 不是两份独立佐证；E03（503998）是茜特菈莉钥匙/手帕，和当前关键结论无直接
关系。相关原文确已入包，但没有模型答案可作语义支持复核，亦未宣称 OBC 收录即官方事实。

完整封存请求、attempt/call、预算和原始 SSE 已审计。移除凭据并禁止 socket 后，
原终态 turn 重放新增请求 0、账务不变；17 字节分块重放同一 SSE 正确拒绝
`STREAM_INCOMPLETE`，没有把 PING 当答案。真实部分历史经 Streamlit AppTest 显示并
刷新两次，来源/研究状态可查看，新增提交 0、新增请求 0、外部连接 0；不是像素截图验收。
审计位于 [report.json](../../data/generation/g5-live-20261114-b/audit/report.json)，
交付原文位于 [cited-sources.json](../../data/generation/g5-live-20261114-b/audit/cited-sources.json)
（此处是已交付 Packet 原文，不是最终答案引用，因没有最终答案）。

## 离线验证与边界

已有 82 项 G5/共享调用/证据/协议检查和 57 项 G5/G4/Live/UI 回归通过；
模型接口一致性 7 项通过。收尾 G5 19 项与 UI 5 项复核通过，覆盖取消前不启动检索、
重开后继承研究状态及纠正使假说需重审；按测试身份去重合计 134 项通过，
并非 134 次真实问答。
工具回执后的崩溃恢复不重复检索/模型调用；已封存响应的 checkpoint 缺口恢复零派发；
UNKNOWN 占额且不重发；跨 chat 共用 live 预算；无凭据/来源损坏不预约；
改写/反证查询保持血缘和组序；重复检索停机；原文回读、选段、引用与纠正隔离均有小夹具。
所有测试禁止真实 socket/GPU/全量 Raw 依赖，不用 Mock 冒充真实研究效果。

单独 UI 重跑遇到取消测试的时序竞态：合成响应先于 AppTest 点击结束，测试把
已封存成功误期望为取消。将该测试的合成流明确放慢以覆盖在途取消，不改变产品取消语义。

## 性能调查

G4 四个封存本地 run 的检索/重排阶段为 63.00–96.50 秒，单次 Dense 搜索
约 7.03–11.35 秒，BM25F 约 5.45–16.51 秒；这些不含主进程 InputReader
初始化。G4 HTTP 交换仅 8.56–15.12 秒，不能把总延迟归于远程 LLM。

独立 CPU-only 测量完整 InputReader 初始化为 **33.36 秒**，验证 119,557 行、
绑定 10 个文件；进程 RSS 从 38,060,032 增至 98,951,168 字节，峰值
124,989,440 字节。没有构造 Encoder、加载 GPU 或派发 API。
InputReader 会完整验证输入/route/来源文件，并逐行构建随机读取偏移与行 hash。
G5 在同 turn 的补检间复用已核验 CPU reader，仍在每次访问检查文件身份；不重复构造
整套主进程 reader。新增时间回执区分 reader 初始化、各 worker ready/request/确认退出，
不改冻结的算法核和 GPU 生命周期。Dense/BGE 仍由 Job 管理并串行 lease、确认退出。
不以跳过 hash、截断评分、并发驻留 GPU 或降低 RAM 门禁提速。

本次实际本地 pipeline 为 **128.25 秒**：reader/初始化 41.13 秒；Dense worker ready
27.50 秒、请求 7.90 秒；BM25F ready 3.33 秒、请求 12.29 秒；BGE ready 18.86 秒、
请求 8.80 秒；退出确认合计约 2.08 秒，其余为来源核对/融合/输出。加原文读取后
search/rank/read 共 130.69 秒。当前明显成本是完整初始化和模型冷启动；模型本次还有
独立的 121.49 秒渠道等待，不能用 G4 的历史 9–15 秒假定本次远程耗时。
复用 reader 的收益是消除同 turn 补检的重复构造；因本批没有发生实际补检，
没有声称已测得补检加速率或整个产品提速倍数。

`pip check`、本工作单文档相对链接及 diff whitespace 检查通过。既有保护清单
645 文件中 644 个字节不变，唯一变化是按 owner 维护的 `docs/model-interfaces.md`；
受保护可执行代码和选定配置没有变化。没有修改旧证书或 hash 来绕过校验。

## 保留限制

- 初始 controller 的证据预算为 30,000 保守字节工作点，给工具历史留空间；最终综合使用完整
  67,072 工作点。全候选/处置和已读原文均落盘，入包数量不代表充分性。
- 每组自动读 6 个、有限反馈、整自然资料边界会限制可见覆盖。超长 Parent 可落盘但可能
  被工具反馈预算排除；目前没有细粒度结构分页。不会将未展示部分说成模型已读。
- 最近同 chat 状态是带定位的模型研究记录，不是事实记忆；长对话没有新增 LLM 摘要。
- 动态 thinking、服务端幂等、实际断流可靠性、泛化质量和 G7 产品采用没有被追认。
- 没有账单证据时实际金额/币种为 UNKNOWN，不能写成零或按猜测价格折算。

没有实施 G6/G7、改默认入口、删除既有成果、修改冻结上游核、提交或推送 Git。

## 使用与后续边界

查看本批部分交付历史（不恢复许可）：

```powershell
$env:PYTHONPATH = 'src'
$env:GC_CLEAN_CHAT_ROOT = 'data/generation/g5-live-20261114-b'
$env:GC_CLEAN_CHAT_MODE = 'LIVE'
& ./.local/runtime-py314/generation/Scripts/python.exe -m streamlit run `
  src/genshin_corpus/ui/clean_chat_app.py --server.address 127.0.0.1 `
  --server.headless true --browser.gatherUsageStats false
```

在首轮停止检查点，后续要求先核对渠道与 UNKNOWN 账务，再制定单独有限验证范围；
该检查点禁止重开剩余九次或自动重发。随后用户明确授权的唯一重试见下节，并未授权
自动延长超时或切换 JSON/非流式协议。代码与离线检查的交付不等于
G5 真实能力验收完成，不进入 G6/G7 或正式入口采用。

## 用户报告 Metro 故障后的显式单次重试

用户随后说明“刚才metro故障了”，并明确指示“重试一下”。故障原因是用户提供的信息，
本地回执只能证明前次超时，不能独立证明服务端故障原因或已无扣费。
本次严格限定补发 1 次，仍计入原始总计 10 请求；不重开旧 campaign、不修改旧 UNKNOWN、
不换请求 fingerprint/turn/预算根，不重跑已完成的 S0/BGE。

新增 `rag/clean_research_retry.py` 直接复用共享
`model_calls.run(legacy_origin=原调用根)`，绑定完全相同的 plan/fingerprint/ledger、
wire body、Packet、campaign/turn/Batch。单独 create-only grant 保存明确费用风险授权，
10 分钟准入期、one additional attempt 和原始 seal；共享 Policy 最多 origin＋重试两次、
unknown_retries=1、format_retries=0，不能因此自动出现第三次。
原有共享 caller、会话桥接核心和旧证书不改。

G5 read-only 对账只额外识别 ledger 正式登记且绑定原件 inventory 的 recovery，
逐 attempt 计入同一 Batch。原历史结果保留 partial/UNKNOWN；重试报告另外保存，
不把原先不确定用量改为零。原始 `live/closed.json` 保留关闭。

| 项目 | 单次重试实际结果 |
| --- | --- |
| 新增请求 / attempt | 1 / 1；G5 累计请求 2 |
| wire body | 与原请求逐字节相同，32,101 字节，thinking disabled、SSE、同一原生工具 schema |
| 响应 | HTTP200，完整 tool_calls 终态与 usage |
| 耗时 | 17.057 秒，不含此前已完成的本地链 |
| 供应商报告 Token | 输入 8,058＋输出 2,306＝10,364 |
| 拒绝原因 | `PROVIDER_TOKEN_LIMIT_EXCEEDED`：报告输出 2,306 超过 wire 中的 max_tokens=2,048 |
| 实际执行工具 / 第三次模型请求 | 0 / 0 |
| 累计账务 | 同一 Batch 2 个预约，已知 Token 10,364＋原请求用量 UNKNOWN；总 Token/实际费用仍 UNKNOWN |

模型文字说明要核对完整结尾、锁是否被解开、正式契约是否成立，并返回
`read_source(natural_parent)` 与 `search(kind=counterevidence)`。独立只读诊断显示
两项工具参数通过当前本地 schema，缺口也与主问题相关。这是有限的工具决策提议观察，
**不是工具循环成功**：整体响应已因超限拒绝，没有执行回读/补检，也没有最终答案。
不放宽 validator 接受超限结果，不自动提高输出上限，不静默重发或更换协议。

原请求完整 inventory 在重试前后相同；重试的 call/attempt、原 claim 下的
`recoveries/0001`、同一 Batch 的第二项预约和单独报告均保存。
请求中 max_tokens 与原件相同，未把 provider-reported usage 当作精确厂商 tokenizer。
共享聚合回执的 `automatic_retry=true` 表示包含 origin＋新增 attempt 的历史聚合，
实际本次由用户明确指令触发；没有新增自动循环。

精确运行代码已按 grant SHA 保存到 `explicit_retry/<operation>/code-snapshot/`。
收尾仅修正未来重试草稿事件的顺序编号（相同文本片段不能合并），并使已封存 recovery
支持只读跨代码收尾重放；新派发仍检查完整代码绑定。此后没有新增真实请求。
离线覆盖同根预算/原件不变、UNKNOWN 后不再补发、无凭据/过期/代码变化不预约、
封存后无网络重放、重复流文本事件保留等场景。

最终 G5＋显式重试 24 项检查通过（19 项既有 G5、5 项新增重试）；共享 caller 20 项
和接口一致性 7 项检查通过。共享 caller 测试首次在 Generation 隔离环境因其历史
OpenAI SDK 导入缺失而未能加载，随后使用已经配置该依赖的通用 Python 环境通过；
没有向 Generation/ML 环境混装依赖。真实重试审计在移除凭据、禁止 socket 后通过，
新增请求 0，原历史 partial 结果不变，原件 inventory 不变；同一 Batch 恰为两个
同 fingerprint 的预约，known_tokens=10,364、unknown_usage_requests=1。
17 字节分块重放完整 SSE 仍按原 2,048 上限拒绝，未放宽解析。
真实审计位于 `campaigns/<id>/explicit_retry/<operation>/audit/verification.json`，
重试结果位于同目录的 `report.json`。

## 官方 DeepSeek 单次对照（用户随后明确授权）

用户提供官方凭据并要求连接官方，判断模型与 Metro 渠道的关系。该指令仅作为 D16 的
单次诊断例外，不将官方渠道接入产品，不切换默认路由。凭据通过隐藏终端输入进入内存，
不写代码、配置、请求归档或报告。没有回显凭据。

官方[模型文档](https://api-docs.deepseek.com/quick_start/pricing/)在本次核对时明确：
`deepseek-flash` 对应 DeepSeek-V4.1-Flash-1017。诊断复用原始封存请求，wire 仅将
model 从 `deepseek-v4.1-flash-1017` 改为 `deepseek-flash`，endpoint 改为
`https://api.deepseek.com/chat/completions`；其余 messages、证据、工具 schema、
thinking disabled、SSE、usage 选项及 max_tokens=2048 完全相同。
这是用户主动要求的另一渠道诊断，不将旧 UNKNOWN 改成未发送，也不是自动 fallback。

复用共享 `model_calls`、原始 raw-first transport 与严格 Decoder，在独立进程内绑定
官方 endpoint/model；未修改仓库协议常量、共享 caller 或生产适配器。有限预算为一次请求、
67,072 保守 Token、120 秒、零重试、零工具执行；本轮只有一次真实派发。

| 对照项目 | Metro 原请求 | Metro 显式重试 | 官方诊断 |
| --- | --- | --- | --- |
| HTTP / 耗时 | 200 / 121.49 秒 | 200 / 17.06 秒 | 200 / 9.96 秒 |
| 完整 SSE 终态 | 无，只有 PING | 有 | 有 |
| 输入 / 输出 Token | UNKNOWN | 8,058 / 2,306 | 8,033 / 818 |
| 报告输出 ≤ 2048 | UNKNOWN | 否，拒绝 | 是 |
| 本地协议与 schema | 未完成 | 整体因超限拒绝 | PASS |
| 执行工具 / 后续请求 | 0 / 0 | 0 / 0 | 0 / 0 |

官方返回两个 `search` 提议，分别检查“后来解开石锁／正式缔约”的其他来源证据，以及
人物指代和“盟约的信物／挑战”的说话者。完整流、响应 model、usage 算术、关闭 thinking
及工具参数 schema 均通过。仅验证提议协议；未执行检索、未验证 parent_query/handles
在 controller 的实际准入、未综合最终答案，因此不能称为完整 G5 研究循环通过。

**结论：证据更倾向 Metro 链路或服务实现的问题。** 同一个记录版本的模型通过官方接口，
对同一输入能在上限内正常完成这一步，因此目前没有证据将前两次故障归为模型普遍不能
完成任务。仍不能由单次、非同时、非确定性采样确定 Metro 是参数转发、模型映射、用量
报告还是瞬时服务问题；也不能独立确认 Metro 后端真实权重或保证官方后续永不超限。
两边输入 Token 差 25，虽 wire 仅 alias 不同，仍可能来自模板或计量差异，不能据此定位。

官方报告 8,851 Token。按当日周末官方美元列表价、8,033 cache-miss input 和 818 output
估算 USD 0.00169575（约 USD 0.0017）；这不是实际账单扣费或优惠余额消耗证据，实际费用
仍为 UNKNOWN。G5 相关累计三次派发，已知 Token 19,215 加原 UNKNOWN；总量／总费用仍未知。
原 Metro campaign 仍为两个预约，原件 inventory 和 UNKNOWN 没有变化。

诊断封存于 `data/generation/g5-official-diagnostic-20261114/`，包括 `comparison.json`、
完整 request/response、共享 call/ledger/budget、精确运行脚本快照及 `closed.json`。
`audit/report.json` 保存对照、费用估算、原件不变和无网络审计：17 字节分块重放 PASS，
没有新增请求，归档凭据模式扫描零命中。该诊断已收尾，未自动追加请求或改变任何默认入口。

随后用户独立授权的[官方 thinking 能力验证](clean-generation-official-thinking-20261114.md)
使用三次新请求，观察到非空 reasoning、推理用量和合成工具循环通过。其独立配额已关闭，
不改变本报告原件/账务，也不补足真实 G5 主动研究、引文或连续研究质量验收。

2026-11-15用户正式采用官方渠道并另授权最多8次新真实研究请求；实施、实际闭环、
停止及剩余问题见[官方 G5 报告](clean-generation-official-g5-20261115.md)。本报告原批次不重开。
