# G3 Live：原生工具优先的最小付费提案

2026-11-14（Asia/Tokyo）。沿用[Plan](clean-generation-implementation-plan-20261114.md)
D01–D24、[Generation 合同](../phases/clean-generation-v1.md)及现有共享调用层。
**准备检查点状态：离线准备完成，等待用户批准；新模型请求0，未生成真实授权文件。**
此后用户明确批准执行，实际结果见[工具/SSE Live报告](clean-generation-g3-tools-live-20261114.md)。
本文保留准备时方案、状态与批准条件，当前结果不由下面的历史“未实测”覆盖。
本提案不是 Live 通过、最终问答验收、产品采用或后续批量研究的声明。
当前授权与下一步的唯一权威仍是[current-phase](../current-phase.md)。

## 现场核对与本轮范围

起点 `main@0337963`，工作区干净；G0–G2 的 `8be6872` 与 G3 的 `0337963`
均已存在。已完整阅读 Plan、AGENTS、现行 Generation 合同、current-phase、
[原 G3 报告](clean-generation-g3-20261114.md)及
[参数核查](clean-generation-g3-parameter-check-20261114.md)，并核对现有适配器、
探针、会话桥接、预算/事件逻辑与离线测试。641个原有 tracked 文件已建保护 hash 清单。

用户明确调整验证顺序：thinking 缺失单独保留，不能阻塞独立的工具与 SSE 测试。
用户说明 G4 已由另一独立工作树推进；本轮不核验其实现、不改 G4 代码、不合并工作树。
不切换默认入口、不自动提交或推送、不更换型号/渠道/API surface、不建设 JSON 行动备选。
旧批次仍是2次请求、212个供应商报告 token、0 retry、0 UNKNOWN；实际人民币 UNKNOWN。
不重复那两项已完成的普通文本测试，不复用旧许可或剩余额度，不改历史结果。

## 固定测试顺序与样例

固定 endpoint：`https://tokenmetro.com/v1/chat/completions`；
固定 model：`deepseek-v4.1-flash-1017`；凭据槽：`TOKENMETRO_API_KEY`。
直接 HTTP Chat JSON；所有请求顶层 `thinking: {type: disabled}`。
不加 effort、不换后缀，不发送新的 thinking-enabled 诊断。

| 顺序 | 配置与目的 | 最多新请求 |
| --- | --- | --- |
| N01 | 非流式原生工具循环：模型用 `tool_choice=auto`，同一回复分别调用 `read_probe_evidence` 读取 amber/tide；程序校验完整调用组、串行执行并封存回执；原样回传 assistant 与关联 tool 消息；模型继续输出整数64。 | 2 |
| N02 | 关闭 tools 的 SSE 中文文本：要求原样输出“星海记录：流式校验完成。”；检查增量 UTF-8、草稿事件、stop、usage及 `[DONE]`。与旧非流式算术样例不同。 | 1 |
| N03 | 与 N01 相同的原生工具循环，改为全程 SSE；检查 tool index/ID/arguments 拼接、tool_calls 终态、全部校验后执行、tool 回传、最终文本及用量。 | 2 |

合成工具只允许两个枚举句柄；本批程序端固定 amber=23、tide=41，两个值不提前写入
模型用户消息。最终64应来自本次工具回执。工具不读取真实 GC 语料、文件系统或网络，
不执行任意代码。同一批调用的完整 JSON/schema 全部通过后才串行执行；不执行半截参数。
若模型一次只调用一个，保留该原生工具观察，但本提案的“双工具完整循环”不计通过；
不自动增加轮次补测。工具回传保留封存 assistant、参数及准确 tool_call_id，不伪造调用。

N01和N03各只准一次工具回传后的模型续答。第二次仍调用工具、拒答、答案错误或未使用
预期双工具，均记录局部未通过；不增加第三次请求修复。N02只准一次。
不借新 turn、campaign、指纹或数据根重测失败项。

## 请求、Token、时间与费用上限

| 项目 | 本批硬上限/处理 |
| --- | --- |
| 新请求总数 | 最多5次；每逻辑操作1 attempt；无 SDK/传输/框架/格式/UNKNOWN 重试。 |
| campaign 预约 | 5 requests /46,080 conservative tokens；每请求预留8,192+1,024=9,216。 |
| 单 turn | 最多2 requests /18,432 conservative tokens；每项样例独立 turn。 |
| 单请求输入 | 整个实际 JSON 请求体≤8,192 UTF-8字节，包含系统/问题/tools/完整协议历史。 |
| 单请求输出 | `max_tokens=1024`；供应商报告超限即失败并停止后续。 |
| 上下文校验 | 32,768 token 工作点；输入估计按UTF-8字节保守计，不声称原厂精确 tokenizer。 |
| 超时/授权 | 每次 socket timeout120秒并有协作式 elapsed 检查；新批准后20分钟内准入。到期不再准入；已准入远端执行不能承诺被撤销。 |
| 币值 | 实际费用 UNKNOWN。请求数和保守预约是可执行上限，不承诺人民币硬限或服务端严格执行max_tokens。 |

所有失败、在途不确定与取消都占原账本；UNKNOWN保留预约，不计零费用，不自动重发。
不调用额外模型生成错误总结。只有完整封存的调用才能零网络重放/对账。
批准时才生成绑定代码/精确 root/profile/limits/messages/preparation 的到期许可，
不是修改仓库示例里的 live 默认设置。批次报告落盘即关闭本批，不自动复用未消耗额度。

## 停止条件与独立能力判定

- **整批停止**：UNKNOWN、HTTP失败、模型/响应身份不符、协议或 usage 缺失/账术异常、
  输出/预算超限、取消、许可到期、本地 seal/hash/恢复不一致。原始失败保留；不隐藏重试。
- **仅该项未通过**：身份、协议、终态和用量完整合法，但不是预期工具/合成答案。
  不再续发该项，只可执行表中尚未发送的独立项；报告保留失败，不能用其他项覆盖。
- thinking-enabled 不在本批请求里；此前 `NOT_QUALIFIED` 单独保留，既不阻塞本表，
  也不会因关闭thinking工具成功而自动升级。

## SSE 与故障检查的实际边界

真实请求核对 HTTP200、`text/event-stream`、精确 model/response ID、完整 finish/usage、
UTF-8与工具片段、终态和 `[DONE]`，原始每块先 fsync 再解析/发布草稿。
答案草稿始终标记 `final=false`；只有完整结果可供后续最终提交判断。

对本批成功 SSE 原始字节的**内存副本**做逐字节重放、半途截断、删除 DONE 和删除 usage；
缺失终态不得产生最终答案或可执行工具，不改原件、不调用 API。
已有/新增离线 transport fixtures 另覆盖实际读取异常、timeout、429/524、UNKNOWN占额、
取消、崩溃/工具回执恢复和 sealed replay；不主动制造额外付费断流。
这些故障测试不证明供应商真实断流的计费行为或长期可靠性；真实遇到中断则如实记账并停止。
一次成功的 SSE 也不证明所有内容都被渠道及时转发，时延仅作本次观察。

## 免费 thinking 调查

本轮重新读取公开资料时，浏览工具仍无法提取 TokenMetro 动态页；采用不带凭据的公开 GET。
pricing及精确型号页面成功，status/docs各一次连接失败，均保留元数据且不无限重试。
成功页面实际引用的入口 JS 也已归档；HTML与入口 JS 的SHA256均与此前证据一致，
精确型号公开条目也相同。公开[定价目录](https://tokenmetro.com/api/pricing)的标签不证明
此路由的实际请求透传或 reasoning 过滤配置，没有新证据解释此前缺失字段。

重新核对的[官方 thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)
仍列出顶层 thinking 开关与 thinking+tools 的 reasoning 历史要求；
[官方工具示例](https://api-docs.deepseek.com/guides/tool_calls/)包含关闭thinking的完整工具循环。
这是请求形状依据，不是 TokenMetro/1017 的能力证明。
不将无reasoning_content解释为“没有内部推理”，也不将正确算术答案解释为开关有效。
旧报告中准备好的渠道询问未发送；如取得精确路由说明，再单独裁决是否定向付费验证。

## 已交付的离线准备与证据

- 新 `generation/clean_tools_probe.py`，复用原 ChatSessions、model_calls、预算与UNKNOWN恢复，
  不改原适配器、原探针、共享桥接/核心或 G4 模块。支持只准备、明确批准、执行及显式恢复。
- 8项新离线测试覆盖5请求完整循环、严格预算、无授权/过期拒发、局部失败后独立项继续、
  硬失败停批、断流UNKNOWN、合法SSE空格/转义差异与工具回执后崩溃零重复发送。
  加已有相关测试共82项PASS；sockets被阻断，Mock不能升级任何Live能力。
  最后一次受影响的8项新测试与7项文档回归复验为15项PASS，日志另存。
- 准备根：`data/generation/g3-ts-20261114-b/`，仅 `preparation/`，无真实授权/账本/预约。
  初始请求预览：`preparation/previews/N01-tools-off.json`、`N02-stream-text-off.json`、
  `N03-stream-tools-off.json`。续答必须遵循封存计划中的严格回传规则。
- preparation seal SHA256：`ff31d1752e49cf4f65dff26d87e3cd2e6f14e41f231f75d085bb73951c73a1c0`。
- 早期纯离线准备根 `g3-ts-20261114-a/`原样保留；本地SSE复核补强后以b绑定最终代码。
  a未获授权、没有任何派发，不能作为待批执行根。
- `.local/generation-g3-tools-prep-20261114/`保存641文件基线、离线日志和公开核查原件。
  所有生成根均Git-ignored；本批不扩展或覆盖旧运行根。

## Live 完成后的报告与后续集成条件

| 项目 | 准备时状态 / 执行后的合格范围 |
| --- | --- |
| 普通非流式原生工具循环 | 未实测；仅N01两请求与两回执完整通过才记该精确profile的有限观察通过。 |
| SSE文本与SSE原生工具循环 | 未实测；N02、N03分别判定，不相互替代。 |
| thinking模式工具循环 | 未证实；不在本批验证，不由普通工具成功推导。 |
| 完整主动研究能力 | 未评估；合成工具循环不证明查证、研究停止、引用、反证或剧情语义质量。 |
| 实际用量/费用 | 本轮准备新增模型请求0；Live后逐请求报告input/output/total、预约、失败/UNKNOWN、耗时与原始回执。无账单证据时币值仍UNKNOWN。 |
| 真实问答集成 | 现在不足。若所需关闭thinking的协议profile通过，可作为另行批准的有限真实问答集成依据；仍需核对G4接线与真实Packet/引用/语义检查，不自动授权该集成或默认采用。 |

**准备时的下一条件：用户批准此精确5请求/46,080保守token工作点及UNKNOWN风险规则后，才可记录新许可并调用。**
批内出现停止条件不扩额；thinking额外诊断、真实问答批次、主动研究、Git提交/推送或默认采用另行裁决。
