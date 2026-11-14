# G3 Live：关闭 thinking 的原生工具与 SSE 有限验证

2026-11-14（Asia/Tokyo）。起点 `main@0337963`，沿用[Plan](clean-generation-implementation-plan-20261114.md)
D01–D24及[Generation 合同](../phases/clean-generation-v1.md)。用户在审阅
[最小付费方案](clean-generation-g3-tools-live-plan-20261114.md)后明确回复“我批准执行”。
当前状态/授权的唯一权威仍是[current-phase](../current-phase.md)。

**结果：普通非流式原生双工具循环、SSE文本、SSE原生双工具循环，三个精确工作点均为
`OBSERVED_LIMITED_PASS`。实际5次请求、5个attempt、0重试、0 UNKNOWN，供应商报告2,103 Token。
thinking工具循环与完整主动研究能力仍未证实；没有最终问答质量验收或默认采用。**

## 本批授权与实际范围

- 固定 TokenMetro `https://tokenmetro.com/v1/chat/completions`，精确模型
  `deepseek-v4.1-flash-1017`，直接HTTP Chat，所有请求 `thinking.type=disabled`。
- 新准备根 `data/generation/g3-ts-20261114-b/`；新授权绑定封存预览、代码、预算、root、
  profile与20分钟准入期限。不复用旧8请求许可，不重做原先两次普通文本测试。
- 最多5次请求、每操作1 attempt，UNKNOWN/格式重试均0；campaign46,080保守token、
  turn2请求/18,432；单次输入≤8,192 UTF-8字节、输出≤1,024、context32,768、timeout120秒。
- 真实工具只读两个合成句柄，程序值 amber=23、tide=41，不访问语料/任意文件或网络。
  关闭thinking的工具选择使用 `tool_choice=auto`，不是强制结构输出。
- 新批准只覆盖这份能力探针。本批5/5已消耗且报告已封存，授权关闭，不自动扩额或继续。
  没有真实GC问答、G4代码修改、工作树合并、默认入口切换、提交或推送。

## 实际循环、流式与用量

| 请求 | 模式/实际结果 | 供应商input/output/total | 单次耗时 |
| --- | --- | --- | --- |
| N01-0 | 非流式、关闭thinking；原生调用 `read_probe_evidence(amber)` 与 `(tide)`，`finish_reason=tool_calls` | 377 /110 /487 | 4.83秒 |
| N01-1 | 程序执行两个工具并回传；模型继续输出 `64`，`finish_reason=stop` | 530 /2 /532 | 2.34秒 |
| N02-0 | SSE中文文本，完整输出“星海记录：流式校验完成。”，stop/usage/DONE齐全 | 73 /24 /97 | 2.93秒 |
| N03-0 | SSE双工具调用，参数片段完整累积后才校验并执行，`finish_reason=tool_calls` | 377 /73 /450 | 2.17秒 |
| N03-1 | 精确回传原assistant和两个tool消息；SSE输出 `64`，stop/usage/DONE齐全 | 530 /7 /537 | 2.04秒 |
| 合计 | 5次HTTP200，精确model；每操作恰好1 attempt | **1,887 /216 /2,103** | 单次耗时合计约14.30秒 |

耗时仅为这五次请求观察，不是整条GC问答延迟或服务可靠性指标。
N01两次共1,019 Token，N02共97，N03两次共987。
每次实际请求字节数依序为808、1,477、415、847、1,526，均在整消息上限内。
输出报告也均未越过1,024上限；这不消除旧Phase04曾出现的渠道超限历史。

两个工具循环均完整保留：模型原生assistant/tool_calls → 全组严格JSON/schema校验 →
串行执行 → 4份合成工具回执（每循环2份）→ 原样assistant和准确tool_call_id回传 → 模型最终续答。
没有伪造模型调用、执行半截参数、修改模型参数或切换JSON行动协议。

SSE原始证据：

| 响应 | JSON chunk数 | 内容增量 | 工具delta片段 / 非空arguments片段 | 终态 |
| --- | --- | --- | --- | --- |
| N02-0 | 11 | 9 | 0 /0 | stop、usage、DONE |
| N03-0 | 19 | 0 | 17 /15 | tool_calls、usage、DONE |
| N03-1 | 3 | 1 | 0 /0 | stop、usage、DONE |

三份流都通过逐字节UTF-8重放。流式草稿事件保持 `final=false`；只有完整响应解析完成后
才形成 final_text/tool_calls 结果。工具循环成功不意味着长任务、复杂schema、重复服务稳定性
或服务器端strict约束已获证明；本批只有每个工作点一个合成样例。

## 异常、恢复与独立复核

对三份真实SSE响应的内存副本分别做半途截断、删除DONE和删除usage，共9项故障检查，
全部拒绝产生最终答案或可执行工具。原始响应不变、没有额外付费中断请求。
现有离线fixtures覆盖真正read异常、timeout、429/524、取消、UNKNOWN占额与崩溃/回执恢复。
本批没有遇到真实服务断流，所以不能报告服务断流率、实际断流计费或远端取消保证。

付费前82项相关离线测试通过，其中8项工具优先探针测试；最后受影响的8项新测试与7项
文档回归复验为15项PASS；Live收尾的7项模型接口文档回归也通过。
本批独立零网络审计另逐项核对：

- preparation/report/case/call/attempt/receipt的seal与hash；123份已记录不可变文件复核前后相同。
- 首请求发送字节完全等于封存预览；续答字节完全等于原请求、真实封存assistant与实际工具回执的组合。
- 每次精确endpoint/model、HTTP200、完整终态和usage；3份SSE按逐字节增量解码仍相同。
- 5个预约、每个9,216保守token allowance、每turn最多2次；全部结果与Batch/ledger一致。
- 5个封存调用在模拟许可过期、未提供凭据且socket/发送器均被禁止的条件下重放一致，0新发送。
- 工具结果逐一绑定实际调用ID/原操作，值23与41准确；完整报告重放不启动新研究。

独立复核 `PASS` 说明本批证据/协议/账务完整，不是剧情语义或产品验收。
准备后的适配器、runner、model_calls、会话桥接、原Plan、选定配置和既有测试字节均未改变。

## 费用口径

本批供应商报告 **1,887输入 +216输出 =2,103 Token**，5次已知请求、0重试、0 UNKNOWN。
5份预约allowance合计46,080是保守资源准入，不是实际计费token；对账后已知用量为2,103。
响应含部分输入缓存明细，原样保留；没有推理用量明细或可核实的币值账单。
**实际人民币费用 UNKNOWN**；不把公开目录公式、缓存标签或usage差异冒充账户实际扣费。
没有为查询账单调用未获授权的私有接口或联系渠道。

原先2次/212 Token仍为独立的已关闭批次；两个G3批次合计7次/2,315供应商报告Token，
只作历史累计，不合并campaign或重置任何额度。

## thinking与研究可用性

本批未发送thinking-enabled请求，保持原报告的动态thinking `NOT_QUALIFIED`。
先前开启模式无reasoning_content/明细的结论不变，不能推断服务端没有内部推理。
免费参数核查及本轮准备期复查仍未取得精确TokenMetro/1017路由的透传、过滤或覆盖说明；
公开目录/通用官方文档不能解释它。没有盲目增设effort、换后缀/型号或改API协议。

| 能力 | 本批结论 | 后续使用条件 |
| --- | --- | --- |
| 关闭thinking的非流式原生工具循环 | 有限观察通过 | 仅上述schema/prompt/profile与小型合成循环；实际GC工具仍要验证各自来源与参数合同。 |
| 关闭thinking的SSE文本/工具循环 | 各自有限观察通过 | 客户端完整终态、草稿与异常处理保持；一次成功不保证渠道长期可靠或无缓冲。 |
| thinking模式工具循环 | 未证实 | 精确路由说明与必要的新有限诊断，不能由普通工具循环推导。 |
| 完整主动研究 | 未评估 | 还需受控查证、预算/停止、原文/引用、反证与连续研究验收。 |
| 真实问答集成 | **协议层已有依据推进关闭thinking的有限集成** | 仍需操作者批准真实问答批次；先核对G4接线和实际工具/schema/Packet/引用，单独检查答案语义。 |

因此无需为等待thinking结论继续阻塞独立的关闭thinking集成准备；但不能宣布G3全部能力、
完整研究或问答质量已通过，也不自动给G4工作树增加调用权限。
待用户裁决：是否先按关闭thinking工作点开展少量真实问答；是否取得渠道精确说明后再定向调查thinking。
这两项均不由本批自动执行，也不需要为了追求全部门禁通过而无限验证。

## 证据与交接

真实运行根：`data/generation/g3-ts-20261114-b/`。
独立审计根：`data/generation/g3-ts-audit-20261114-a/`；其中 `capabilities.json`
把三个 `OBSERVED_LIMITED_PASS` profile绑定到call seal、工作点、scope及独立限制。
请求manifest中的 `NOT_LIVE_VALIDATED` 是准备时不可变状态；不能改写，后续观察以审计能力记录为准。
该记录可供另一工作树只读审阅，不是账户许可，不自动合并代码或启用Serving。

- preparation seal：`ff31d1752e49cf4f65dff26d87e3cd2e6f14e41f231f75d085bb73951c73a1c0`。
- probe report seal：`5c3bdbe53467dec5fd568db60ee2b066ae83350e7cc6c87131e861c44a26e3e6`。
- independent audit seal：`ab2d7ca64f43250c907da47dfcd598d507a88b9767c8b34a68341a04a5a0d557`。

各请求/attempt seal、完整用量、原始字节与回执路径见审计 `audit.json` 与原报告。
`.local/generation-g3-tools-prep-20261114/`保留准备/测试/审计脚本与日志，生成资产均Git-ignored。
既有数据、实验、未提交文件及独立G4工作树受保护；本批无提交、推送或默认切换。
