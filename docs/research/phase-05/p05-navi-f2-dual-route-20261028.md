# Phase05 F2｜Responses 双路线诊断（2026-10-28）

**结论：两路线都未证明具备可靠的 F2 运行条件。** 流式完整 Navi 响应通过本地 Schema 和证据 ID 绑定，但其 reasoning 身份冲突仍未解决；非流式 Navi 响应明确因输出达到 12,000 Token 上限而不完整。简单用例仅证明两路线各成功一次，不能证明复杂任务稳定或 Schema 被服务商严格执行。没有启动正式 F2。

## G1 与 F2 已知差异

|项目|历史 G1|当前 F2 / 本诊断|
|---|---|---|
|SDK、端点、模型|OpenAI SDK + httpx，极智 Responses、同名模型|同 SDK/端点/模型；本诊断保留独立存档|
|指令位置|顶层 instructions；历史有成功 JSON|原 F2 也是顶层 instructions，但首条与短诊断未遵循；本诊断合并完整指令与输入到 user 文本|
|流式及上限|stream=true、8192；119份历史流机械重放通过，非语义成功率|原 F2 stream=true、30000；本诊断分别 stream=true/false，简单2048、Navi 12000，每组内完全一致|
|格式约束|原生提示词要求 JSON；无本次独立 text.format Schema|本诊断额外加入 Responses `text.format` strict json_schema；原 F2 冻结请求未更改|
|流数据|历史单 message 或符合历史校验的归档|本轮流同时含 reasoning、message；原始流及 SDK 一致但 reasoning ID 冲突|

历史与现在的服务商映射、模型权重及出口并无完整可比凭据，不能由此断定服务端变更或忽略指令。SDK 支持结构化参数不等于极智完整遵循 Schema。用户消息变更了模型可见输入；不能把结果当成原冻结 F2 条件。

## 四次实测

|请求|终态及 usage（输入/输出）|JSON / Schema|内容与事件|
|---|---|---|---|
|A 简单流式|HTTP 200 completed，100/214|目标 JSON 和简单 Schema PASS|raw SSE 与 SDK 事件相同；reasoning ID 冲突|
|B 简单非流式|HTTP 200 completed，100/175|目标 JSON 和简单 Schema PASS|原始 JSON 与 SDK 响应一致；不适用 SSE 身份比较|
|A Navi 流式|HTTP 200 completed，5865/10989|完整 JSON、本地完整 rc1 Schema PASS，双端 alias 绑定 PASS|1 条 C 级解释性命题，reasoning ID 冲突；两输出项 added/done/terminal 顺序可核对|
|B Navi 非流式|HTTP 200 incomplete：max_output_tokens，5865/12000|未形成可解析 JSON；Schema 未通过|原始响应与 SDK 一致；可见 message 仍是 44,201 字符未完文本，不能当正式命题|

每对 A/B 的实际请求正文经比较，除了 `stream`，模型、input、Schema 及生成参数相同；Navi 输入来自冻结 F2 rc1 生成样本 `8978e15f60f91e0e1db1-rc1`。简单 Schema 和完整 rc1 Schema 均获 HTTP 200，但成功结果没有反例，因此无法证明上游严格执行每项 Schema 约束。单条成功不证明总体稳定性。输出 token 包含什么推理/内容分账以实际 usage 字段为准，不能据此断定关闭推理或定价。

A Navi 引用的 A001/A004 提到书文笔差和作家自嘲，B004/B007 出现对套路、NPC 和设定集的叙述；它提出文学平行，说明增量有限、不推断身份因果，建议 C/排除。这是有对应原文锚点的初步 Agent 观察，是否充分有价值、是否漏掉其他联系仍需独立语义复核；不能用格式或 alias PASS 证明内容质量。没有人工金标准。

流式两例均收完整终止事件、raw 与 SDK 对象相同，message ID、文本一致；reasoning added/done ID 与终止项 ID 冲突。事件 added 在 done 之前且均在 terminal 之前，这仅是关键事件次序检查，非全部事件协议的形式证明。不得通过移除 reasoning 或重写 ID 宣称流全量 PASS。非流式无 SSE，因此不能声称修复了流式协议。

## 原始记录与离线纠正

B Navi 自动记录最初写 UNKNOWN：原代码将任何非 completed 状态抛错，遗漏了明确的 incomplete 终态。原始结果保持不变；`offline-observation.json` 从原始响应与 SDK 双份一致的响应明确核定 `INCOMPLETE/max_output_tokens` 与 usage，是真实完成状态的补充说明，不是重发或覆盖。新增纯函数 `navi_f2_response_observation.py` 区分 completed、incomplete、failed 与未知，并保留已知 usage；未改动正式 F2 适配器。它的3项模拟测试及既有15项流/SDK测试共18项 PASS。

4次请求、0重试、0正式 F2 槽位；已知输入11930、输出23378、总35308 Token，账户实际账单 UNKNOWN。无本次未确认终态，原历史第二探测仍 UNKNOWN。发起 B Navi 后所有请求已用完，未继续重试。按输出上限提前截断是明确结果，不应将其记为一次 JSON 或语义成功。

## 下一步和公平性

推荐将非流式作为值得后续探索的候选路线：它保留完整原始 HTTP 响应，避免依赖 SSE 身份匹配，但本次复杂用例未完成，故目前不能选它作为已验证解决方案。需要在另行授权的有界研究中核查输出上限、推理输出及复杂任务的完整 JSON 和语义；不能仅靠提高上限宣称可靠。流式仍需获得可靠的 ID 解释或修复，不应降低完整性门槛。若采用新路线，先新建实验版本：两臂同一模型、路由、user 包装、格式约束、输出限额和冻结材料；保留原 F2 首条失败、分母与历史 UNKNOWN，不混用结果，正式实验须另获授权。

新增诊断程序 `navi_f2_dual_route_diagnostic.py` 只用于本次独立试验，沿用 SDK/httpx 的原始字节留存，无重试。将来若采用非流式，需要单独审核与测试原始响应优先归档、状态和 usage、拒绝/截断处理及重复计费保护，而不是直接将诊断脚本投入正式生产。

原始请求、原始响应、SDK事件、状态及封存哈希在 `data/retrieval/p05-navi-f2-dual-route-20261028-r1/`；Preflight及相关历史封存通过完整性复核。没有改动冻结样本、生产Navi、Phase04、并行Embedding或Git历史。
