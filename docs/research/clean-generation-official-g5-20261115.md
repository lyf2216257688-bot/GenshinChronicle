# DeepSeek 官方正式切换与 G5 真实主动研究

2026-11-15（Asia/Tokyo）。承接 [Astra Plan](clean-generation-implementation-plan-20261114.md)、
[Generation 合同](../phases/clean-generation-v1.md)、[G5 先前报告](clean-generation-g5-20261114.md)、
[官方 thinking 验证](clean-generation-official-thinking-20261114.md)及
[G4 固定问答](clean-generation-g4-live-20261114.md)。当前状态唯一权威为
[current-phase](../current-phase.md)。

**正式渠道切换已实施；真实“决策→工具→反馈→继续研究→引用综合”闭环已跑通，
但 G5 完整语义/连续研究验收仍 OPEN。** 主案例因上下文预算提前综合，交付有引用的有限答案；
同 chat 追问执行了原文回读，后因额外工具参数被严格拒绝，未交付最终回答。
本批共7次新官方请求，0重试、0 UNKNOWN、0超限；余下1次随批次关闭，不继续消耗。

## 授权与正式渠道

用户明确授权渠道从 TokenMetro 正式切为官方 API / `deepseek-flash`，必要实施/离线测试及
最多8次新付费请求；按角色调整 thinking 和输出预算，优先真实 G5 验证。无 G6/G7、
上游重建、历史删除、账务改写、Git 提交或推送。
这覆盖 Plan D16 的旧 Metro-only 选择，其他层边界与恢复要求保留；未重写原 Plan 的历史决定。

| 所有者 / 入口 | 实际变化 |
| --- | --- |
| `config/clean-generation-v1.json` | 正式官方 route/model、动态 thinking、角色输出上限；不是永久付费许可 |
| `generation/deepseek.py`、原 V1 provider factory | 默认 base `https://api.deepseek.com`、model `deepseek-flash`、key `DEEPSEEK_API_KEY`；显式 Metro/Bailian 能力保留 |
| `clean_research_live`、`clean_live` 新 prepare | 默认官方；分别复用 G5 研究或 G4 固定链，仍须有限许可 |
| Chat manifest / raw-first transport / Decoder | endpoint/model 随具体 manifest 绑定，响应身份按该请求校验，避免重解释历史 Metro 字节 |
| 现有 G4 UI / Job executor | LIVE 读取新批次设置；保持 Mock 启动默认、显式 LIVE、已有持久化/引用/恢复路径 |
| 旧 G3 常量与低层请求默认 | 保留给冻结的 Metro 探针；正式应用 prepare/入口显式选择官方，不自动 fallback |

官方凭据仅通过隐藏输入进入本次进程环境，子执行器继承后按需读取；结束即从父进程环境移除。
未写仓库、配置、日志或请求归档；不把旧 Metro key 当作官方 key 的 fallback。
原 V1 provider 的可选 base 选择现为 `DEEPSEEK_BASE_URL`，默认官方；显式旧 Metro 需要
相应 base、key 和 model。旧 `TOKENMETRO_BASE_URL` 不再选择生成默认渠道。
没有将暂停的 Phase05 历史 Card/SDK 路由一起迁移。

## 角色、预算和停止合同

| 角色 | thinking | completion 上限 |
| --- | --- | ---: |
| 简单最终回答 | disabled | 4,096 |
| 普通研究控制 | disabled | 8,192 |
| 复杂分析控制 | enabled | 16,384 |
| 复杂最终综合 | enabled | 32,768 |

推理已在 completion 内，一次预留/记账，不在 output 之外重复累计。
新增有限 `research_plan` 让模型组织子问题并按需申请 analysis；不要求每题完整规划。
普通工具协议单元先完整封存，再启动独立分析任务，不给原 assistant 伪造空/摘要 reasoning。
进入 thinking 的原生工具循环后，推理、参数和准确关联的 tool 结果原样保留；最终综合为
独立 Packet 任务。没有引入另一个调用器、代理团队、重试器、外部服务或依赖。

本批硬限：8请求、每操作1 attempt、0格式/UNKNOWN/transport retry；context196,608，
campaign1,572,864 / turn1,179,648 保守 Token；单turn最多6请求、6次S0、16工具、48读取，
研究1小时、请求300秒、Job worker1800秒、准入2小时。保守估算为 UTF-8 字节，非供应商 tokenizer。
USD2 的列表价风险界限使用当次官方 peak output USD1.2/M 乘整个保守 allowance，
上界约 USD1.8874；不能保证供应商计费、汇率、价格变动或违约超限时的实际扣费。
UNKNOWN、协议/来源/引文错误关闭批次；context/no-gain/budget 可作有限交付，不能冒充充分。

## 第一条真实研究：尘世之锁

口语问题询问“她给他那把锁算不算已经立契约”“后来解开所以正式缔约”，并要求核对
说话者、结尾、琉璃百合和人物身份，寻找独立来源，区分原文/推断/未知。

实际顺序：

1. 原问题 S0/BGE 后，关闭 thinking 的模型主动建立四个子问题，申请 analysis，
   并检索独立文本确认“大袖少女”与琉璃百合的身份线索。
2. 完整工具反馈交付后，独立 thinking 分析读取新 Packet/反馈，指出不能用书中的猜测
   或武器故事自身给身份自证；继续执行两次自主检索。
3. 其中一个子问题以 `query-1` 为 parent，引用新发现的证据 handle，构成真实多跳；
   另一个以 original 为 parent，专查“解开／正式缔约”的支持或反证。
4. 原生工具累计反馈逼近序列化 context 硬限，系统按合同转为复杂最终综合。
   最终有引用答案交付，但状态为 `partial/context_budget`，没有模型充分性确认。

| 项目 | 实际观察 |
| --- | --- |
| 请求 / 用量 | 3请求；83,457 Token |
| 检索 | 原始1＋子问题2＋反证1＝4组，各保留100候选/独立组内排序；不跨查询混分 |
| 读取 / 工具 | 19个读取动作（含最终 Parent）；4个模型工具动作＝plan1＋search3；不是19份独立来源 |
| 最终 Packet | 6片段、实际引用5个E号；400候选处置：included6 / covered_duplicate11 / budget_excluded14 / not_selected369 |
| 来源错误 | 0；来源/条件/否定/Raw lineage 均保留 |
| 耗时 | 425.75秒；HTTP合计87.70秒 |

有限来源复核结论：

- 核心原文支持少女在同一句中说“盟约的信物”“我对你的挑战”，以及结尾“许多年没能解开”。
  回答没有把少女的词句分配给不同说话者，也没有把长期未解直接改成永远不可能解开。
- 读到了社区武器考据（content6849）；模型明确将其视为个人解释/重复引文，未当作独立情节。
  另引用任务材料（1250）及《侠客记·留尘》（605）的琉璃百合/归离原线索。
  书中“有人猜测”被保留，未坐实少女姓名或与归终等同。
- E04/E05来自同一书籍观测，原文内部还有重复显示内容；回答明确说明不能算两份独立证据。
  Packet6片段也不代表6个独立见证。OBC来源及hash PASS不等于全部内容已获官方事实认证。
- 核心事实与引用定位有支撑；但开头将“后来解开并缔约”称为“假的”偏强。更严谨应为
  “当前原文无支持，所述结尾仍是多年未解；更晚的未写情节未知”。后文虽有限定，也不能
  消除开头措辞风险。因此不将这一答案评为无保留语义 PASS。
- “没有正式契约”的句子靠近“他想起……”的回忆语境；文本未显式标“他心想”，
  不能把人物内心归属或全知旁白身份说成已经确定。本轮追问正用于复核这一边界。

## 同 chat 追问：完整原文与措辞复核

追问保留同一session/原campaign，收窄到完整武器故事、叙述归属和“假的”是否过强。
以 `correction_intent` 记录任务范围，上一轮解释仍是待复核材料。

模型选择 simple、保持关闭 thinking，先计划，再连续执行三次 `read_source(natural_parent)`。
三份完整回读都落盘且在反馈预算内实际交付，未只保存后声称模型已读。前两次实际为
社区文本解读（6871）和武器原型考据（6849），最后才回读到武器资料（1222）。
这里暴露了当前E号与目录handle未显式配对的接线问题，导致多余回读与请求；不是上游索引被改动。

第4次返回 `select`，同时含合法 `fragment_ids` 数组和多余单数 `fragment_id`。
严格 closed-object validator 以 `TOOL_ARGUMENT_KEYS` 拒绝整项，未执行选择、未静默丢字段，
未把本应失败的响应改成成功，未自动重试/补综合；campaign关闭。该turn无最终答案。
该额外字段确实来自官方模型原始响应，不是本次Metro转发问题。切换渠道不能替代模型
工具参数校验或保证自动研究可靠性。
追问共4请求、111,638 Token、137.95秒，读取动作9、模型动作4（plan1＋read3）。
同chat引用/来源与完整失败记录仍可展开；这个追问不是连续研究成功案例。

## thinking 与查询能力的实际覆盖

| 能力 | 本轮真实结论 |
| --- | --- |
| 口语问题理解、子问题/查询增强 | 观察到四子问题和三条LLM自拟补检文本；不是固定一次检索 |
| 多跳、主动补检、反证 | main的query-2依赖query-1；另有counterevidence查询，实际执行 |
| 原文扩大回读 | 追问三次natural_parent实际执行并反馈；目录误配导致额外回读，收尾修复仅离线 |
| Step-Back | kind/schema/祖先合同及共享执行路径保留；这两题模型未选择，不声称本轮真实通过 |
| 动态 thinking | 主问题由模型申请analysis，真实返回reasoning；聚焦追问保持simple/off |
| thinking原生工具 | 真实analysis生成两项search，执行成功；随后context转独立综合，未继续同一enabled原生历史 |
| enabled工具历史续答 | 既有G3官方合成循环有限通过仍有效；本轮真实研究未新增该完整续答观察 |
| 最终综合 | main启用thinking、32768 cap，输出6491，其中reasoning5064；没有上限压力测试 |
| 连续研究与纠正 | 同chat承接范围并回读，但select失败，未完成最终纠正答案 |

## 与 G4 固定问答的比较

G4历史题问“石锁性质、原文是否说解开”，它的一次固定答案已经准确保留“信物/挑战”
与“没有正式契约”的差别、以及“许多年未解”的范围。G4只做一次S0和一次模型请求，
16,903 Token，模型14.68秒；来源Packet4片段，其中有不必要的须弥石头锁延伸。

G5主问题额外要求人物/独立文本交叉核查，确实新增子问题、多跳、反证和独立书籍，
没有强行认定身份；相对G4增加了研究行为和证据类型。但核心“锁未解”的事实并未优于
G4，付出了更多Token/时间，还出现过强措辞和上下文提前结束。两题范围、渠道、时间均不同，
不是同条件A/B或质量胜出证明；不能把400候选、更多引用或thinking本身当作质量提升。
简单且原始证据充分时，应保留直接回答路径，避免将这类长研究作为每题默认流程。

## 性能、用量与费用

| 请求 | thinking / cap | input | output（其中推理） | HTTP秒 | 结果 |
| --- | --- | ---: | ---: | ---: | --- |
| 主1 | off /8192 | 8,155 | 475（未报告） | 6.86 | plan＋search |
| 主2 | on /16384 | 23,348 | 2,020（1,375） | 18.18 | search×2 |
| 主3 | on /32768 | 42,968 | 6,491（5,064） | 62.65 | 有引用的有限最终答案 |
| 追1 | off /8192 | 14,652 | 349（未报告） | 5.25 | plan＋read |
| 追2 | off /8192 | 26,269 | 140（未报告） | 2.86 | read |
| 追3 | off /8192 | 32,954 | 141（未报告） | 3.29 | read |
| 追4 | off /8192 | 36,963 | 170（未报告） | 3.95 | 非法额外参数，拒绝 |
| 合计 | 7请求/7attempt | **185,309** | **9,786（已报告推理6,439）** | **103.05** | 总195,095 Token |

关闭模式未报告推理明细，不把“未报告”伪装成独立计量的零。开启两次的推理已包含在输出，
未重复加到195,095。cache-hit75,904、cache-miss109,405。按当次官方周末USD目录价：
`(75,904×0.003＋109,405×0.15＋9,786×0.6)/1,000,000＝USD0.022510062`。
**实际账户扣费仍 UNKNOWN**，不是零，也不把估算当账单；没有为查账另发私有API请求。

主问题四个LocalPipeline分别109.60、55.24、59.64、57.73秒，合计282.20秒。
首次reader初始化33.016秒，后面三次同turn复用仅0.0017–0.0020秒；复用真实发生。
GPU worker仍逐查询按Job/lease冷启动并确认退出；Dense约22–25秒、BGE约11秒的就绪成本
是主要剩余瓶颈。未让两个GPU模型并驻、降证据门禁或修改检索/重排算法。
追问的本地原始查询105.48秒，reader32.53秒，跨turn未复用reader。两轮墙钟合计563.70秒。
差额还含source读取、完整性校验、序列化/持久化和进程成本，不将HTTP之外全部归为模型慢。

## 关闭后的代码修复与验证边界

这些修复不修改七次原请求、预算、结果、失败历史或permit/code hash：

1. 每个current catalog项明确列出receipt、fragment、source identity及当前citation_ids。
   编号不再按目录位置猜配；反馈也带source_item，便于识别读错资料。
2. analysis独立任务只传一次当前Packet正文；此前反馈中的重复正文用已入Packet/明确deferred
   的handle与片段记录代替。完整原feedback和closed native history保留，不截断原文、不冒充已读；
   进入thinking后的原生历史不作这种投影。
3. select说明明确只接受gap与复数fragment_ids；closed schema保持，额外字段仍拒绝，未放宽validator。

三项均有离线回归，**没有新付费验证**；因此不能宣称映射/上下文问题已在真实模型下解决。
将主案例归档的真实历史用于离线投影检查，原本超出上下文准入的后续研究请求，修复后
为150,729序列化字节，可在原180,224 input-byte限内准入；分析边界反馈由38,510降为
10,748字节，原Packet消息与其后thinking原生历史逐值相同。本次该投影没有deferred正文。
这是实际归档输入的机械改进，不是新模型续答或语义改善的实证。
实际采样代码按原permit保存于 `live/code-snapshot/`；收尾版本另有post-live审计记录。

受影响的不同离线检查共89项通过：G5最终24、显式retry5、G4 Live8、V1 provider11、
Chat/工具探针29、UI5、接口文档7。基础协议的检查因transport/路由实现改变而执行，
没有再花付费请求重复合成协议验证。初次回归仅发现两个旧测试仍期待Metro专属缺key错误名，
更新为通用错误后通过；没有为过测试降低准入或schema。

独立无凭据/无网络审计PASS，范围为出处/协议/账务/恢复，不是语义验收：

- 7个call/attempt、精确wire预览、usage算术、role cap、来源文件及当前Packet引用一致。
- 6份合法SSE逐字节/17字节重放一致；缺DONE/usage或截断副本拒绝。失败原件分块重放仍拒绝。
- 原生历史中的assistant与真实tool回执准确匹配；本批存在的普通模式续答原样保留。
- 7个共享调用和2个终态turn在禁止发送/检索条件下重放，0新请求、7预约不变。
- 上游检索/排序核心、选定配置及shared caller/session/evidence IO基线hash未变；
  原Metro UNKNOWN原件inventory和既有官方协议证据未变。
- 实际UI历史两次刷新呈现4条消息、研究/引用/失败原文，0新提交/请求/外部连接。

## 交付、使用与下一条件

运行根：`data/generation/g5-official-live-20261115-a/`。`audit/report.json` 是封存原件审计，
包含逐请求seals/usage、每turn来源原文、查询祖先、反馈、时间及非法参数诊断。
`case-reports/` 保存两轮原始结果；`live-case.json` 保存session/主问题身份；
`live/code-snapshot/` 保留付费时精确代码；`post-live/` 记录收尾回归与UI readback。
所有运行数据Git-ignored，正式配置/实现/文档/小型fixtures测试保留为可审阅源码改动。

查看本批历史不会重开额度：

```powershell
$env:PYTHONPATH = 'src'
$env:GC_CLEAN_CHAT_ROOT = 'data/generation/g5-official-live-20261115-a'
$env:GC_CLEAN_CHAT_MODE = 'LIVE'
& ./.local/runtime-py314/generation/Scripts/python.exe -m streamlit run `
  src/genshin_corpus/ui/clean_chat_app.py --server.address 127.0.0.1 `
  --server.headless true --browser.gatherUsageStats false
```

下一条件是新的有限真实验证授权，优先确认修复后的catalog、受控feedback和最终措辞/来源归属，
完成一次未被context提前结束的充分性判断及同chat纠正答案；不重开本批最后1次、不静默重发
失败select、不重发任何旧UNKNOWN。Step-Back、纯省略指代、长程研究与thinking收益仍需独立评价。
官方渠道采用不意味着G6/G7或完整产品/研究质量采用，未自动提交或推送Git。


后续正式检查点：参见 [G5 最终完善与有限验收](clean-generation-g5-final-acceptance-20261115.md)。
本文7次调用、原失败与关闭额度保持历史原样，后续观察不追改本文结论。
