# F2 首个正式请求：流校验阻断（2026-10-28，日本时间）

**SDK 已接通并收到 Responses 终止事件，但首条正式请求未通过冻结流校验，F2 按
用户指定的停止条件暂停。** 没有发送第 2 条或后续请求，没有新增能力探测。

## 调用与账目

F1-R2 / Preflight / SDK 修复证据及两次旧探测的封存校验通过；正式 48 个槽位此前均
NOT_SENT。按冻结顺序发送第 1 条：`6ebe216667dc7303487d-legacy`，审核对照，
《嬗变之尘》与《异梦溶媒》。用户授权的 jizhi / `deepseek-v4.1-flash`、
OpenAI SDK + httpx、Responses stream、`max_output_tokens=30000`，同一请求中无
自动重试、渠道回退或额外参数。应用 `trust_env=False`、显式无代理；观察的 TCP
对端不是已核实的公网出口。

| 记录 | 结果 |
|---|---|
| 正式基础实验 | 已尝试 1/48；剩余 47 NOT_SENT |
| 首条流式机械校验 | FAIL，`ambiguous_output`；保留原始证据，不重发 |
| 定向复核 | 0 |
| 旧能力探测 | 首次 403；第二次 UNKNOWN，历史状态不变 |
| 本次新增探测 | 0 |
| 本次正式请求返回 usage | 输入 635、输出 734、总计 1369；两种事件记录一致，数值相加一致 |
| 实际费用 | UNKNOWN，未取得可核验账单；旧 UNKNOWN 探测费用也未知 |

上述 usage 是完成事件中**实际观察到的返回值**，并不覆盖流身份校验的失败；
原始执行终端中的 `usage: UNKNOWN` 未被修改。调用账本须保留这一区别。
输出 Token 是否含推理/缓存的分账细节仍未核实。30K 参数被服务器接收并产生响应，
不代表验证了实际生成 30K 正文的能力。

## 原始证据与异常

HTTP 200、`response.completed`、status=completed，终止事件中的 model 为
`deepseek-v4.1-flash`。原始响应体与 SDK 事件各记录 742 个事件，事件类型序列一致；
最终可见 message 文本与对应 delta、text.done 和 part.done 一致。
这说明已到达 Responses 服务并得到可观察输出；**不能据此称整个流通过校验**。

终止 `output` 包含 reasoning 和 message 两项；冻结的 G1/C1 严格检查器要求一个
message，因而报 `ambiguous_output`。此外 reasoning 的 `response.output_item.done`
ID `item_d63c88d445f80f6b4494660c` 与完成响应中 reasoning ID
`item_d487d58e2b5a685123aa82ed` 不一致。message 项 ID 保持一致。需要对服务商
的流事件合同进行核查，不能简单丢弃 reasoning 项、放宽校验后回填 PASS。

原始请求体确认实际携带冻结 `instructions`（1187字符，要求只输出严格 JSON）及
双方源文本；模型可见回答却表示未收到具体问题并输出 Markdown 表格。对该回答执行
公共 JSON 解析为 `JSON_PARSE_FAILURE`，旧版原生 Schema 因无法得到 JSON 对象而
未能检查。**请求体确实包含 instructions，但现有证据不能判定它在服务端何处丢失、
被忽略，还是模型没有遵循；不能将该现象擅自归因为 Prompt、模型或供应商中的某一方。**

响应体原始字节、SDK解析事件、请求正文、请求/响应元数据和原终止记录分别位于
`data/retrieval/p05-navi-f2-experiment-20261027-r1/attempts/` 下按请求指纹命名的目录。
`offline-observation.json` 记录离线核对；它不覆盖原始终止判断。此前首次准备因
Windows 换行归一化导致快照字符串 hash 检查失败，发生在任何发送之前；重新核对
原始封存文件后纠正该本地检查，不构成模型请求重发。

## 结论与停止点

通信层已收到来自目标端点的真实终止事件，但 F2 的流完整性与 Prompt 执行条件尚未
满足。剩余 47 个槽位和两臂相同配置保留；本次槽位不得以新ID或修改参数重发。
没有足够数据比较新旧 Prompt，没有新版退步/改进样本，也不具备冻结新版的依据。

下一步应先离线诊断 Responses reasoning 事件身份约束、供应商 `instructions` 处理
和新旧 Prompt 公平性；必要时向服务商提供本次 Ray ID `a5128f001d0c1dab-NRT`、
请求/响应 hash 和非敏感结构信息。任何规范/协议调整或后续真实请求需遵循用户的
后续明确要求；不自动放宽规则，不把一次内容失败从分母删除。
无正式发布或人工认可，Phase04 和并行 Embedding 未改变。
