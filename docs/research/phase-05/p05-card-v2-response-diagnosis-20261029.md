# 首次 Card v2 响应的只读诊断

2026-10-29。没有新模型请求、重试、Prompt改写或解析器放宽。原运行、失败结果和账本
均不修改。独立分析根：`data/retrieval/p05-card-v2-response-diagnosis-20261029-r1/`。

**已证实服务端/渠道返回的Responses封装异常，排除本地解析造成“把推理当答案”。**
仍不能从客户端档案区分中转实现与其上游服务各自的责任，也不能证明Prompt复杂度
对本次预算耗尽完全没有影响。

## 直接证据

从原始 `response.body.raw` 独立按SSE帧和标准库JSON解析；处理一个尾部 `[DONE]`
标记，不依赖现有SSE解析器。所得事件与既有解析器、SDK保存的事件逐对象完全一致。

1. **推理与最终消息重复**：reasoning summary和message/output_text各20949字符，
   内容逐字相同，SHA256均为
   `6f5df3b49e7d0eb8d6ce124fcdf65d19a084f1d9c0de86cba0f7b54872d34e79`。
   唯一一条 `response.output_text.delta` 也直接包含同一段完整文字。
   复制在收到的原始字节里已经存在，不是本地跨消息拼接或SDK选择错字段。
2. **终态矛盾**：事件类型 `response.completed`，内部 `response.status=incomplete`，
   理由 `max_output_tokens`。不能以外层completed证明完整生成。
3. **reasoning身份改变**：added/done为 `item_4aca67fd6041776f8e0ae7cd`，
   terminal变成 `item_1ce115ea79e754f75dca185b`；message身份则一致。
   Prompt内容不能解释这种协议对象身份漂移。

原始响应SHA256：`88157bedcea611948a843ee9d9eaaa293b22b915b3c7109974b59eb86a328e8c`。
详细对象、独立解析结果与拒绝调用栈见 `findings.json`。

## Prompt 与请求

实际发送body与准备wire及当前Card wire完全相同，Prompt和Schema逐字存在；
`text.format.type=json_object`、模型、8192上限、359字来源片段均正确。
没有证据显示程序漏发指令/Schema或额外添加了推理模式参数。

输出内容是规划如何制作Card的分析文字，未形成最终JSON。材料很短但输出很长，且
上报8189输出Token接近8192上限，提示推理展开/输出预算是可能的生成失败诱因。
Card v2的原子观察、引文和多字段要求可能增加生成负担；这属于推断，单次档案不能
隔离它与渠道默认推理/上游行为。不能据此断言Prompt本身错误，也不能断言单纯增大cap
就能修复消息复制、身份漂移和终态矛盾。

只读对照两份历史成功Card：相同请求顶层字段、同一模型、8192 cap及json_object模式，
分别正常返回4874/1957字符JSON，终态completed，只有message项，没有reasoning项。
因此当前wire形式并非普遍不能产JSON；此对照不是同输入、同Prompt的因果实验。
历史job为首轮真实实验 `live-run-r2/jobs/01d12554…` 与 `03b86bb2…`。

## 程序行为

- 最早拒绝发生在 `navi_f2_instruction_diagnostic.compare` 的终态状态校验，早于JSON或
  Card Schema校验；程序拒绝不完整响应是正确行为。
- 单独用标准库 `json.loads` 解析全部可见输出，同样在第一个字符失败。没有因只读了
  reasoning而漏掉另一个合法final，也没有在拼接时丢掉Card JSON。
- 本地仍有次要可观测性缺口：result只保存异常类型，精确原因需要重放调用栈；完整完成
  检查失败时usage仍是UNKNOWN，尽管独立终态观察可保留1950+8189=10139的提供商上报。
  这影响诊断/预算状态表达，不是原始响应变成分析文字的原因；本次不改旧结果或解锁派发。

优先排查服务端/渠道的Responses转换与推理预算行为，再决定是否需要缩减Card输出负担。
不应先改剧情语义标准，或通过放宽本地解析把分析草稿认成Card。本轮零新增收费，
四份未发送资料继续受现有门禁保护。
