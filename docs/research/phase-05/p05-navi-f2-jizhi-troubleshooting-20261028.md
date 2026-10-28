# F2 极智 Responses 自主排障（2026-10-28）

**结论：仍 BLOCKED。** 直接把指令放入 user 输入在三次短诊断中得到目标 JSON；但四次真实响应均有 reasoning 项 ID 冲突，不能证明全流可靠，也不能证明复杂 Navi 语义能力。system 输入一次未遵循；不能据此确定服务端具体丢弃字段的位置。未修改 F2 冻结 Prompt、请求、校验器或原始失败，也未启动新的正式实验。

## 已有证据与本次设计

原 F2 请求及 D1/D2 的原始正文包含顶层 instructions；原 SSE 与 SDK 对象一致。G1 历史曾以同类顶层 instructions 成功，不能证明当前供应商映射或模型快照未变化。TokenMetro 的401没有模型输出，不能作行为对照。采用已验证 SDK/httpx、独立目录、显式 trust_env=false、Responses 流式、同模型、2048 输出上限、零重试；前一请求不完整、HTTP拒绝或 usage 无法确认即停止。请求由程序原样落盘；raw 字节、SDK 事件与最终摘要分别保存。四次均为独立诊断，不计入 F2。计划最多五次，第四次后已观察到关键阻断，故未凑满。

|测试|变更|JSON目标|reasoning ID|input/output/total|
|---|---|---|---|---|
|system|原 instructions 放 input 的 system 消息|未遵循|冲突|67/413/480|
|user|同一指令放 user 输入|遵循|冲突|112/52/164|
|json|在 user 基础上增加 text.format=json_object|遵循|冲突|112/24/136|
|none|在 user 基础上增加 reasoning.effort=none|遵循|冲突，仍有 reasoning 输出|112/32/144|

四次均 HTTP 200、response.completed、返回 deepseek-v4.1-flash，usage 三项可加总，原 SSE 与 SDK 解析事件逐项相等；message ID 及文本 delta/done/terminal 一致。reasoning 在 added/done 的 ID 相同，但终止响应内不同。故通信完成与目标 JSON 分别记录，不可称整个流 PASS。目标成功不能证明 JSON 格式参数生效：user 对照本身也成功；`none` 被接受也不能证明推理被关闭，reasoning 输出仍出现。四次合计输入403、输出521、总924 Token；实际账单及计费细分 UNKNOWN；本次无 UNKNOWN 执行、无重试。输出 token 分账中的推理量应以实际 response usage 明细为准，不能推断定价。

## 判断及可行边界

明确观察到：顶层/system 条件下未遵循，而 user 条件下短指令成功；原始流自身已含 reasoning 身份矛盾，不是 SDK 投影引入。可能原因包括渠道处理 system/instructions、模型对角色的遵循或当前上游映射变化；不能确定具体哪一层、不能仅凭 usage 不同断言丢字段。没有证据证明 8192 与30000差异造成异常。

兼容校验应允许多输出项，并以 output_index、类型和身份核查 added/done/terminal；按消息与 content index 校验 delta、done 与终止文本，单独核验 response ID、模型、usage、终止状态和 raw/SDK 一致性。已有独立 `navi_f2_instruction_diagnostic.compare` 及模拟测试覆盖合法 reasoning+message 和冲突，并未修改冻结 F2 的失败判定。这里真实冲突必须继续 FAIL 或显式未解决，不能丢弃 reasoning 或按位置改写身份。仅支持可靠 message 文本不等于整个流身份完整。

若后续需要将 instructions 移入 user、改变 reasoning 或格式参数，则会改变模型可见条件，不能继续声称原冻结 F2 相同。须先建立新运行版本，冻结两臂同一参数、完整请求指纹及分母，保留首条旧失败和先前 UNKNOWN；重新实验须另行获得完整运行授权。当前没有可靠替代，未生成新48请求，也未改生产适配器。短 JSON 不能验证文学、隐含剧情、长期复杂指令或公平语义效果；无人工认可。

## 离线检查

- 既有诊断和 SDK 模拟测试 **15/15 PASS**，包括合法多项、真实冲突、403、截断、超时和防重；这是合成验证。
- Navi v0.2 contract/schema **24/24 PASS**，是既有合成结构回归，并非本次真实 Navi 生成或模型语义测试。
- 历史七个封存根以及 Preflight 封存验证 PASS，清单见本轮 `integrity.json`；本次两个独立目录重新 seal 并验证。未修改历史根；无 F2 新正式槽位。
- 新增独立诊断程序 `navi_f2_jizhi_diagnostic.py`、`navi_f2_jizhi_reasoning_probe.py`；复用现有 SDK 原始流存档、事件比较器，不替代 F2 实验 runner。代码仅供本次受控诊断，不授权再次调用。

## 可直接发给服务商的脱敏问题

> 我们通过标准 OpenAI Python SDK + httpx 调用 `POST /v1/responses`，模型 `deepseek-v4.1-flash`，stream=true。请核查：1）Responses 顶层 instructions 和 input 中 system 角色是否映射到上游，当前模型版本及路由是否变更；2）同一 output_index 的 reasoning 在 output_item.added/done 与 response.completed 中的不同 ID 是否符合贵方合同，若有映射规则请提供可验证规范；3）reasoning.effort=none 和 text.format=json_object 是否被此路由支持并实际执行。没有重试。请关联以下 UTC 与 response ID / Cloudflare Ray：
>
> system 2026-10-28T01:29:35Z，`ea8b3a5c80c2515667b4695b88ba2124` / `a5162bc09feceb7a-NRT`；user 01:29:58Z，`28d7924d780bdea43e6061f988fb2a85` / `a5162c4ebf728e1a-NRT`；json 01:30:03Z，`3d40740aab26c01875445ff196b2c08e` / `a5162c6df8cde37d-NRT`；none 01:30:35Z，`e32facc74ed15ec969b4fb585752aba5` / `a5162d315beb1ca8-NRT`。请核对账单及用量。请求、SSE、事件、ID 与哈希保存在本地封存目录，分享前由持有人审阅；不要发送密钥。

原实验仍 1/48 已尝试且机械失败、47 未发送；旧第二次探测 UNKNOWN 保留。没有更换服务商、触碰并行 Embedding、Phase04 或正式导航数据；未提交 Git。下一步需服务商解释或修正流身份/角色映射，并在适当授权下验证复杂 Navi 样例及决定新版本实验；此前不能宣布具备可靠 F2 条件。
