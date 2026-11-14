# Clean Generation G3：离线交付与有限 Live 停止记录

日期：2026-11-14。实施基线 `8be6872`，沿用[Plan](clean-generation-implementation-plan-20261114.md)
全部 D01–D24；技术合同见[Generation V1](../phases/clean-generation-v1.md)，
当前授权与下一步由[current-phase](../current-phase.md)维护。

本报告保留原批次停止检查点。此后用户要求将独立能力拆开验证，见新的
[工具优先 Live 提案](clean-generation-g3-tools-live-plan-20261114.md)及随后完成的
[工具/SSE Live报告](clean-generation-g3-tools-live-20261114.md)；旧结果与已关闭授权不变。

用户先要求推进 G3，随后明确：“我授权付费测试，你可以适当自主决定。”
据此完成新适配器与离线检查，再执行有限付费探针。
**G3 离线工程已交付；Live 能力验证在 thinking 门禁停止，G3 尚未完整通过。**
没有进入 G4–G7，没有问答语义/连续研究验收或默认切换。

## 实际交付

- `generation/clean_chat.py`：版本化请求与能力 profile、显式 thinking 开关、
  原生 Chat tools、非流式 JSON 与 SSE 增量解码，身份/终态/usage/上限校验。
  结果区分文本、工具、拒绝、截断、不确定与取消；模型推理、草稿和原始 assistant
  消息单独保留，进度事件只输出草稿且标记非最终。
- `generation/clean_chat_transport.py`：raw-first urllib 单次尝试，逐块 fsync 后解析；
  继承原 `SessionCalls`，唯一派发路径仍为 `model_calls.run` 与持久 turn/campaign
  预约。无 SDK/传输隐藏重试、代理、重定向或服务端幂等假设。旧适配器/共享核心原字节保留。
  Live 需独立的精确 preparation、代码/root/预算/profile 绑定与到期许可。
- 本地完整工具参数校验：闭合对象、嵌套类型、枚举、数值/长度约束和有界数组；
  未支持的 schema 关键字直接拒绝。工具流按 index/ID 累积，完整终态及全组参数通过后
  才允许执行。原始 reasoning/arguments 和工具关联完整回传；工具回执可幂等补齐。
  这不是服务端 strict schema 执行证明，也没有新增 JSON 行动备选协议。
- `generation/clean_probe.py`：独立有限能力工作台，封存预览→单独批准→逐阶段探针，
  保存 raw/账务/工具回执/能力报告。仅合成值，不是持续聊天或自主研究控制器。
- `tests/generation/test_clean_chat.py`：20项离线测试，包含实际停止情形的回归。
  未新增依赖或改变 ML/编排环境。

## 测试与工程验收

最终 **94项相关测试通过**：20项新 Chat/探针测试，37项已有 G1/G2 测试，
10项旧 DeepSeek 回归，7项模型接口文档回归，20项共享调用回归。
共享调用回归在既有全局环境运行，其余在隔离 Generation 环境运行。
日志：`.local/generation-g3-20261114/`。第三方 LangSmith 依赖仍有已知弃用警告，
不影响结果，也没有启用 tracing。

新测试覆盖：逐字节 UTF-8/SSE 分片、多个工具及跨块参数、原始字节先落盘再发布草稿、
thinking/答案分离、模型/响应ID变化、重复/未知工具、无 usage、错误账术、超限、
拒绝/length、缺 DONE/终态、429/524/超时/断流、半截调用不执行、首次字节崩溃恢复、
取消/预算、缺失或过期 Live 许可、未授权注入与消息范围、封存后零网络 replay、
工具回执重放、完整8请求 Mock，以及失败后停止而不消耗剩余额度。

这证明有限协议与工程行为，不证明模型在剧情研究中的理解、引用支撑或反证能力。

## 有限付费工作点与实际结果

精确路由：`https://tokenmetro.com/v1/chat/completions`，
模型 `deepseek-v4.1-flash-1017`，既有 `TOKENMETRO_API_KEY` 槽。
凭据仅在进程内使用，不进入请求预览、日志或仓库。

批准工作点：最多8次请求，每操作1 attempt，UNKNOWN/格式重试均0；
campaign163,840保守 token，turn2次/40,960 token；
单请求输入≤16,384 UTF-8字节，输出 off≤1,024 / on≤4,096，context32,768，
socket timeout120秒并有协作式 elapsed 检查，整批派发准入期限20分钟。
预约不是供应商 tokenizer 或人民币账单保证；实际费用 `UNKNOWN`。
停止条件包括协议/身份/能力失败、超限、UNKNOWN、取消和到期。

计划顺序：P01关闭thinking普通回答→P02开启thinking普通回答→
P03关闭thinking非流式双工具循环→P04开启thinking非流式双工具循环→
P05开启thinking流式双工具循环。工具只读两个合成句柄 amber=17、tide=29，
不访问真实语料或执行任意代码。后三项各最多两次请求，总计最多8次。

| 样例 | 配置/结果 | 供应商用量 input/output/total | 判定 |
| --- | --- | --- | --- |
| P01 | 非流式，`thinking.type=disabled`；HTTP200，精确模型；17+29→46 | 64 / 30 / 94 | 普通文本与未见推理字段的有限观察通过 |
| P02 | 非流式，`thinking.type=enabled`；HTTP200，精确模型；137×19−58×7→2197 | 68 / 50 / 118 | 基础协议/答案通过，但动态thinking能力未获证实，停止 |
| P03–P05 | 未发送 | 0次请求 | 工具/流式真实能力尚未验证 |

两次原始响应的 assistant 都只有 `role`、`content`，没有 `reasoning_content`；
usage 都没有 reasoning 明细。P02的停止条件为 `THINKING_ENABLE_NOT_OBSERVED`。
**不能由此认定服务端一定没有内部思考，也不能认定动态开关已生效。**
原始 usage 与可见文本长度的差异本身不证明隐藏推理或费用。
当前结果不足以支持需要 reasoning 回传的 thinking+tools 工作点。

实际：**2次真实请求、2个预约、212个供应商报告 token、0次重试、0个 UNKNOWN**。
两次耗时分别约19.08秒、2.77秒，仅该样例观察；非通用延迟结论。
剩余6次没有继续消耗；停止后没有借新指纹重发、改参数、换路由或切 JSON 协议。

## 证据与保护

真实探针：`data/generation/g3-probe-20261114-a/`；
独立零网络复核：`data/generation/g3-audit-20261114-a/`。

- preparation seal：`457efb4089ed599847f7cb85466933d11f9d1cca1f3409ea27d20b30f7ac661a`。
- P01 call seal：`6745b830368543c13a08fedf5dda698b80a386484e2bb143b2cf6b2734cdf581`。
- P02 call seal：`4e0c471a3d238a9e18c491a0fb36ee1dd1b8cc1d777a3f2627fd0dc3e309f180`。

复核逐项验证 preparation/report/call/attempt seal、发送字节等于预览、精确模型、
各1 attempt、usage账术/账本/预算一致，并零网络对账。测试开始时635个 tracked文件
建立保护清单；已有源码/配置/测试/依赖锁和Plan均未改变，改变的已有文件仅为相关文档。
Raw/Clean/Chunk、索引、历史实验和 G0–G2 运行根均未修改。
所有生成根 Git-ignored；本工程验收检查点尚未提交或推送，后续 Git 收尾授权由 current-phase 记录。

## 遗留与下一门禁

必须由用户裁决 thinking 能力缺口的后续处理，再执行任何依赖它的真实阶段。
可先只读核对渠道文档/参数透传，或另行明确同路由的定向诊断工作点；
不能将本次无可见推理直接升级为能力通过。工具与流式仍须独立实证。
本次批准不是后续研究批量调用或 G4–G7 的授权。

后续用户选择的[免费渠道参数核查](clean-generation-g3-parameter-check-20261114.md)
已另行记录：新增模型调用0，未发现可解除门禁或修改客户端参数的精确渠道合同。
此后续说明不改变上述 Live 停止检查点。

协议依据仅作实现参考：[DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/)
要求工具请求保留历史 reasoning；[原生工具调用](https://api-docs.deepseek.com/guides/tool_calls/)
展示 assistant/tool 消息关联；[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)
定义流式 usage/终态。官方声明不等于 TokenMetro/1017 实证；本报告的渠道结论来自上述原始回执。
