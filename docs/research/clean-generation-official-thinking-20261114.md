# DeepSeek 官方 thinking：G3/G5 有限能力验证

2026-11-14（Asia/Tokyo）。依据 [Astra Generation Plan](clean-generation-implementation-plan-20261114.md)
D01–D24、[Generation 合同](../phases/clean-generation-v1.md)、
[G3 原始停止报告](clean-generation-g3-20261114.md)、
[G3 工具/SSE 报告](clean-generation-g3-tools-live-20261114.md)与
[G5 最新报告](clean-generation-g5-20261114.md)。当前授权和采用状态仍由
[current-phase](../current-phase.md)唯一管理。

**结果：官方 `deepseek-flash` 的 thinking 非流式文本、thinking SSE 原生双工具及其续答，
在本批小型合成样例中均为 `OBSERVED_LIMITED_PASS`。三份响应都有非空
`reasoning_content` 和独立 `reasoning_tokens`。完整回传推理及工具结果后，续答正确。
这不追认 Metro thinking，不代表真实 G5 主动研究、动态切换策略或最终问答质量通过。**

## 授权、工作点与执行边界

用户明确授权最多三次新付费请求，使用官方 API / `deepseek-flash`，由 Codex 设置预算、
超时与停止条件；不自动重试 UNKNOWN、不扩额、不改默认渠道、不修改历史账务、不提交 Git。
这是 D16 的本批显式诊断例外，不是产品渠道采用或自动 fallback。

| 项目 | 本批固定值 |
| --- | --- |
| endpoint / model | `https://api.deepseek.com/chat/completions` / `deepseek-flash` |
| thinking / effort | 顶层 `thinking: {type: enabled}`；不额外设置 effort，未验证 effort 强度切换 |
| 总请求 / attempt | 最多三次；每操作一次；UNKNOWN、格式及 transport retry 均零 |
| 单请求输入 / 输出 / context | 32,768 UTF-8 字节 / 16,384 Token / 65,536 保守工作点 |
| 总 Token 准入 | 147,456 保守 Token；不是实际计费或厂商 tokenizer |
| 超时 / 准入期限 | 每请求 240 秒；整批准入 20 分钟 |
| 停止 | UNKNOWN、HTTP/协议/身份/用量错误、截断、缺少可见 thinking、错误答案/工具集、到期或代码变化 |
| 工具 | 已有 `read_probe_evidence`，仅合成 amber=23、tide=41；无任意文件、网络或真实语料访问 |

[官方模型文档](https://api-docs.deepseek.com/quick_start/pricing/)当次将该 alias 对应为
DeepSeek-V4.1-Flash-1017。[thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)
明确支持开关，并要求带工具的历史完整回传 reasoning；本批以实际回执验证相应子集。
没有 SDK `extra_body` 再嵌套层、强制工具选择、JSON 行动协议或未经核实的型号后缀。

仅新增 Git-ignored 的诊断脚本与派生运行根，复用现有 `model_calls.run/Policy/Batch/ledger`、
`clean_chat_transport.attempt`、`clean_chat.Decoder`、G3 题目/schema、`tool_reply` 及流重放检查。
官方 endpoint/model 只在隔离诊断进程内绑定；生产常量、共享核心、G5/UI、依赖及默认入口未改。
新授权使用独立有限批次，旧 Metro UNKNOWN、其 allowance、原显式重试和旧官方诊断均保留关闭。
Key 经隐藏输入仅在内存中使用，不写源文件、归档、报告或日志。

## 三次真实回执

| 请求 | 实际动作 / 终态 | 输入 | 输出（含推理） | 其中推理 | 非推理输出 | 耗时 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| T01 | 非流式；同 G3 P02 算术题；`2197` / stop | 68 | 49 | 46 | 3 | 1.343 秒 |
| T02 | SSE；原生读取 amber、tide 各一次 / tool_calls | 377 | 107 | 34 | 73 | 2.674 秒 |
| T03 | SSE；原 assistant＋reasoning＋两条 tool 回执；`64` / stop | 563 | 11 | 9 | 2 | 2.594 秒 |
| 合计 | 三次 HTTP200；三次 attempt；零重试/UNKNOWN/超限 | **1,008** | **167** | **89** | **78** | **6.611 秒** |

所有响应 model 均为精确 `deepseek-flash`。三次非空 reasoning 分别为 132、171、35 个字符，
只在本地 audit/原始 assistant 保存；没有将推理内容当作证据、进度文案或最终答案。
三份原始 usage 均包含 `completion_tokens_details.reasoning_tokens`，数值分别 46、34、9。
input＋output＝total 验证通过；**推理已包含在输出 167 内，不能再额外加一次 89。**

三次输出均小于 16,384，未出现 length 或预算溢出。此处证明这三次遵守上限，未进行靠近
16,384 的长推理压力测试，不能宣称所有任务都保证不超限。离线将响应副本的准入上限改为
实际 output−1 时，现有 validator 均拒绝；未修改原响应、原 wire 或服务端额度。
1,175 总 Token 是本批供应商报告用量；6.611 秒仅为小型合成题的请求耗时之和，
不含真实 S0/BGE，不能作为 G5 全链延迟或 thinking 质量收益基准。

## 原生工具与 reasoning 回传

1. T02 仅在完整 SSE 终态、usage、身份和全组严格 JSON/schema 验证后执行工具。
   模型确实生成两个调用；没有由程序伪造或替换模型调用。
2. 复用 `tool_reply` 串行形成两份 immutable receipt，分别含实际 call ID、句柄、值与
   `SYNTHETIC_PROTOCOL_FIXTURE_NOT_CORPUS` 权威标记。
3. T03 messages 的前两项与 T02 相同，第三项逐值等于 T02 封存的完整 assistant，含完整
   `reasoning_content`、tool_calls、原始 arguments；第四、五项对应两个真实 tool ID/回执。
   未重新解释、截断、摘要或改写 reasoning，未添加 user/system 提示纠正结果。
4. T03 再次返回非空 reasoning、独立推理用量和正确最终 `64`，无额外工具调用。

因此，这一个 **thinking＋SSE 原生双工具＋原样历史回传＋续答** 循环已获直接有限实证。
没有省略 reasoning 的付费负向对照，也没有验证跨多个用户轮次、开关混用或多跳检索。
这不是实际 GC 检索/read_source 工具、G5 证据综合或引文语义验收。

## 渠道对照与能支持的结论

| 历史/当前样例 | thinking 请求 | 原始 reasoning / 推理用量 | 能支持的判断 |
| --- | --- | --- | --- |
| Metro G3 P02，原算术题 | enabled；非流式；4096 cap | assistant 仅 role/content；无推理明细；68+50 Token | 当时没有观察到开关生效，不能断言内部完全没推理 |
| 官方 T01，同算术题 | enabled；非流式；16384 cap | 非空 reasoning；46 推理 Token；68+49 Token | 官方该工作点可观察且有明细，原 GC parser 可正确保留 |
| Metro G3 N03 工具循环 | disabled；SSE；1024 cap | 关闭模式循环通过，无推理明细 | 普通工具能力不能推导 thinking 能力 |
| 官方先前 G5 诊断 | disabled；SSE；2048 cap | 未见 reasoning；推理 Token 未报告；输出818 | 关闭模式观察与本批开启模式不同，题目不同，非严格开关 A/B |
| 官方 T02/T03 | enabled；SSE；16384 cap | 两次均有 reasoning/推理明细，工具循环通过 | 官方支持这一个开启模式循环 |

独立审计直接复核 Metro P02 的原始 request/response：与 T01 的 messages、thinking 与
非流式设置相同，wire 仅 model alias 和 max_tokens 不同。T01 实际只输出49 Token，
远低于旧4096 cap；没有“必须扩大到16384才能看到这49 Token”的证据。较大的 cap 仍是
对照的一个改变，且不是同时、确定性请求，不能把它包装成严格因果试验。

**目前证据更支持精确渠道的参数/字段处理存在差异，而非 DeepSeek 模型本身不支持 thinking。**
原始 Metro 响应就没有 reasoning，官方原始响应有，且同一个 GC decoder 能保存/回传；
因此没有客户端必然丢字段的迹象。但无法仅凭本批区分 Metro 是否过滤返回、覆盖开关、
转协议、选用不同后端或存在瞬时配置问题。没有新发 Metro 请求、获取其内部配置或核实上游权重。
此前 Metro G5 的 PING 超时、2306>2048 事件仍各自是历史故障，不由本批小题修复或解释清楚。

## 对 GC 后续动态 thinking 的建议

- **按渠道和工作点保存能力证据。** 官方该 alias 的非流式 thinking、SSE thinking 工具循环可记
  有限通过；Metro 维持 thinking NOT_QUALIFIED。不得用官方证据自动解锁 Metro，或后台切线。
- **保留当前默认关闭 thinking。** 若后续明确采用官方做受控 G5 验证，可优先将普通查询增强、
  简单句柄读取维持关闭；将冲突证据比较、反证/因果辨析和复杂最终综合作为选择开启的候选。
  这是任务分配建议，本批没有质量/成本基准证明收益，未实现自动决策器或修改配置。
- **为开启模式单独预留输出和完整历史。** 下一步如获准真实研究，可从本批16384输出工作点
  制定有限预算；按 input＋全部 completion 记账，其中推理不得二次累加。不能直接将现有
  G5 的2048工作点改个开关就视为合格，也不能按最终答案短就缩减推理 reserve。
- **继续保留 reasoning 与 tool 单元的原样持久化。** 包含工具的后续请求应回传完整历史
  reasoning；摘要/context 压缩不得破坏协议单元。推理仍不成为事实或引用，默认不在 UI 展示。
- **严格区分“可开启”与“值得开启”。** 尚需在独立获准的真实 G5 问题上检查更好的证据、
  更少无效研究及有限成本；effort 强度、跨轮模式切换、长上下文、多跳和长输出仍未验证。
  不为消除这些限制而自动追加调用、放宽 cap、重发 UNKNOWN 或宣布完整 G3/G5 已验收。

本批三次已用完并关闭；上述下一步建议不是新调用授权或正式采用决定。

## 审计、恢复与费用

付费前已有协议/工具探针28项检查通过；诊断专用三个离线场景通过：正常三次循环及原样
回传、首请求 UNKNOWN 后仅一次派发、缺少可见 thinking 后仅一次派发；封存后重放不再派发。
收尾模型接口文档7项检查通过。这些检查不等于模型语义/真实研究验收。

真实回执的独立审计禁止 socket、不给凭据，逐项通过：

- 三个共享 call/attempt、准备/报告及工具 receipt 的 seal/hash、精确 endpoint/model、
  原始发送体等于 manifest、各一次 attempt，Batch 恰三个预约，零未知用量。
- 两份 SSE 逐字节重放一致；各自半途截断、移除 DONE、移除 usage 的六个副本均拒绝。
  非流式原件解码一致，三份超限副本均拒绝。
- 三个共享调用在发送器禁止条件下直接封存重放，零新请求；原 reasoning 和回执往返准确。
- 已绑定的旧 Metro/官方证据 hash/seal 不变，原批次不重开；本批派生根内凭据模式扫描零命中。

本批供应商报告 **1,008 input＋167 output＝1,175 Token**，其中 input cache-hit384、
cache-miss624。按当次官方周末美元列表价估算：
`(384×0.003＋624×0.15＋167×0.6)/1,000,000＝USD 0.000194952`。
这是列表价计算，非实际账单；优惠/赠送余额/币种折算及实际账户扣费仍 UNKNOWN。
没有为查询账单另发私有接口请求，不合并或重写历史账务。

运行根：`data/generation/g3-official-thinking-20261114/`。其中：

- `preparation/`：精确预算、授权范围、预览、代码 hash/脚本快照与历史 reference seals；
  `authorization.json` 是有限准入，不含凭据。
- `calls/T01-text-on`、`T02-tools-on`、`T03-tools-continue-on`：共享原始调用账本和 raw bytes。
- `tool-receipts/`、`continuation.json`：准确关联的工具结果、完整续答 manifest。
- `observations/`、`report/result.json`、`audit/verification.json`：字段/用量/对照及零网络审计；
  `capabilities.json` 将有限能力绑定精确 profile/call seals，不是产品调用许可。
- `closed.json`：三次配额关闭，无剩余 standing permission。

无默认渠道切换、历史账务修改、无关功能实施、Git 提交或推送。
