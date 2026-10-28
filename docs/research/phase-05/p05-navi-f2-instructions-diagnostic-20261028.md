# F2｜Responses 指令与输出上限对照诊断

**两次诊断均收到完整终止响应，但两次均未输出指定 JSON；两次均存在 reasoning 项
事件 ID 与终止响应 ID 不一致。** 这两项问题应分别处理。诊断完成后停止，未发送
任何 F2 正式槽位，也未更改首条正式请求的失败记录。

## 请求与结果

两次均使用 jizhi / `deepseek-v4.1-flash`、OpenAI SDK + httpx、Responses stream，
`trust_env=False`、显式无应用代理、无自动重试或回退。共同的 `instructions` 和
`input` 与用户指定文本完全一致；实际发送正文逐项对照表明，唯一差异是
`max_output_tokens`：D1 为 8192、D2 为 30000。两次各一次，顺序 D1→D2。

| 检查 | D1：8192 | D2：30000 |
|---|---|---|
| HTTP / 终止 | 200 / `response.completed` | 200 / `response.completed` |
| response ID | `0bb99e726ea98c9329f28a7fccd08027` | `8248175de2971ea2fdcfde0058c344bb` |
| 返回模型 | `deepseek-v4.1-flash` | `deepseek-v4.1-flash` |
| usage 输入 / 输出 / 总计 | 67 / 527 / 594 | 67 / 500 / 567 |
| 原始SSE与SDK事件 | 538 / 538，逐事件对象相同 | 511 / 511，逐事件对象相同 |
| 输出项 | reasoning 1、message 1 | reasoning 1、message 1 |
| reasoning ID | added=done；终止项不同 | added=done；终止项不同 |
| message ID / 文本 | added=done=终止；delta/done/终止文本一致 | 同左 |
| 目标 JSON | 未遵循；返回自然语言及示例 | 未遵循；返回自然语言询问 |

两次实际输出都没有 `{"probe":"INST_OK_4826"}` 对象。终止 usage 中三个整数相加
一致，也与 SDK 对应事件一致。合计输入 **134**、输出 **1027**、总计 **1161**
Token，均为服务端返回量；实际货币费用和推理/缓存计费细节 **UNKNOWN**，没有取得
可核验账单。30K 请求获得终止响应只表明该参数未被显式拒绝，不证明能生成30K正文。

## 流身份的实际证据

- D1 reasoning：added/done `item_2273149536dd625b3472a476`；终止响应
  `item_46f8e9a35532c1e5a9594d70`。
- D2 reasoning：added/done `item_291af9de4d03b656440e9b3d`；终止响应
  `item_bce94e45dac982a49d0f8d8d`。
- 每次 reasoning summary 相关事件均指向相应 added/done ID；两个 message 项的
  added、done、终止 ID 均相同。

因此“收到完整终止事件”和“流身份一致”分别为**是**和**否**。正常的
reasoning + message 结构本身不应判失败；但这里存在具体 ID 冲突，不能仅按位置
替换 ID 或忽略 reasoning 后声称全流 PASS。原始 SSE 与 SDK 解析事件之间没有观察到
差异，不能把这次冲突归咎于 SDK 解析转换。

## 解释与F2校验建议

两个上限均出现相同方向的问题，没有证据显示 8192 在本次对照中比 30000 更能让
instructions 生效。结果支持**优先检查极智当前路由如何传递或处理 `instructions`**；
它不能证明字段在哪里丢失，也不能排除模型不遵循。两次每组各一个请求，不能建立
总体成功率或可靠的输出上限因果结论。此前 G1 一个历史 8192 流复核为单 message
结构且旧校验 PASS，但不证明当前服务仍使用相同实现或模型快照。

未来如修订 F2 检查器，应在独立版本中：按 output_index 接受明确支持的 reasoning
与 message 多项；分别核对每项 added/done/终止的类型和身份；对每个 message
按 item_id、output_index、content_index 核对 delta、done 与终止文本；再检查
response ID、终止状态、模型、usage 和原始/SDK事件。未知类型或真实 ID 冲突须
保留并标记，不得用位置强行合并。仅当服务商合同或其他充分证据解释了特定 ID
映射时，才能审议有据可查的兼容规则；不能把本次诊断或历史 F2 失败改写为 PASS。

**尚不具备安全恢复 F2 的基本技术条件。** 需要先解决或获得充分可核验解释：
当前 `instructions` 未在简单测试中按要求生效，以及 reasoning ID 冲突如何按
服务商协议处理。不能仅凭这两次结果认定 v0.2 Prompt 的语义优劣。

## 审计和隔离

诊断根 `data/retrieval/p05-navi-f2-instructions-diagnostic-20261028-r1/` 包含
两个请求正文、原始HTTP响应体、SDK事件、逐项结果、不可重发预留和完整性检查。
原始响应体为网络内容编码后的字节，不含 TLS 包或 HTTP 传输分帧；SDK事件另存。
两次均无重试。正式 F2 仍为首条已尝试且流失败、47条未发送、0条定向复核；
先前探测 UNKNOWN 状态不变。

5项新增模拟测试通过；保护清单与实际计数见 `verification.json`。
模型输出仅为诊断证据，不是人工金标准或正式知识。用户的两次诊断授权已用完，
本轮停止，不自动恢复正式实验、修改生产链或发布 Link。
