# F2 TokenMetro 跨渠道诊断（2026-10-28）

状态：**BLOCKED：TokenMetro 返回 HTTP 401，没有模型响应。** 只发送一次诊断，
没有重试或执行 F2 正式槽位。

离线核对确认配置为 `https://tokenmetro.com/v1/responses`，独立的
`TOKENMETRO_API_KEY` 凭据槽存在，地址与项目配置相符；密钥值未保存。
历史项目配置将 TokenMetro 和极智均标为 `deepseek-v4.1-flash`；历史 TokenMetro
Responses 测试曾返回相同模型 ID 和 completed，另有历史流式 Responses 运行。
这些是过去的同名模型与接口证据，不证明两个渠道当前实际权重快照相同，也不能
证明当前账户权限仍有效。

发现历史 [ordinal21 报告](p05-w2-ordinal21-route-recovery-20260924.md)把
TokenMetro DeepSeek 短 canary 描述为流式；其原始 `request.json` 实际为
`stream=false`。流式支持另有历史运行记录，不以该 canary 证明流式。历史报告和
原始数据未改写。

本次正式发送的 JSON 正文与极智 D1 **字节完全一致**，包括 `instructions`、
`input`、模型 ID、`stream=true`、8192 上限；只改变服务商地址和密钥。
使用 OpenAI SDK + httpx，显式无应用代理，`trust_env=false`，自动重试0。
服务端返回 `HTTP 401`，错误对象为 `type=upstream_error`、`code=16`、
`message=Forbidden`。没有 Responses 终止事件、返回模型或 usage；实际费用未知。
401 并不证明具体是密钥错误、账户权限不足还是上游授权问题。已停止，不重试。

| 对照 | 极智 D1 | TokenMetro |
|---|---|---|
| 终止状态 | completed | HTTP 401，无模型终止 |
| instructions 目标 JSON | 未遵循 | 无法判断 |
| reasoning ID | added/done 与终止不一致 | 无法判断 |
| 返回 Token | 594 | UNKNOWN |
| 实际账单 | UNKNOWN | UNKNOWN |

因为 TokenMetro 没有返回模型输出，**无法判断两项异常是否存在渠道差异，也不能
据此证明极智接口兼容性问题**。历史同名 ID 不是当前两渠道相同权重版本的证明。
现阶段不建议迁移 F2；需要先解决 TokenMetro 401、确认当前账户对同名模型的
Responses 权限及模型映射，并取得可核验的流式行为。若以后比较或迁移，应重新
冻结渠道、模型身份与参数，对两臂使用相同来源、Prompt、证据范围和相同渠道，
保留已有失败、UNKNOWN 和原有分母；不得将混合渠道结果当作原设计的配对比较。

完整请求、原始响应体及元数据保存在独立目录
`data/retrieval/p05-navi-f2-metro-diagnostic-20261028-r1/`。原 F2 首条失败和
47 条未发送槽位不变；不自动继续实验或发布知识。
