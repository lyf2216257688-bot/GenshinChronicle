# Card Metro 切换与单次真实验证

2026-10-29；主仓 `main@ee0569e`，保留既有未提交工作。用户明确停止当前Card使用jizhi，
授权Metro首例及成功后的五来源续跑；合计仍不得超过原12次发送。没有新jizhi请求、
Link/辅助核验、Embedding、全库生产、commit或push。冻结v0.3未改。

**工程切换已完成；Metro首例返回合格JSON内容，但协议和输出上限验收失败，按授权停止。
其余四份未发送，五来源实验尚未完成，不能启动全库生产。**

## 渠道与模型身份

- 复用已有 `TOKENMETRO_API_KEY` / `TOKENMETRO_BASE_URL`，不打印或保存Key。
- 对配置中的 `https://tokenmetro.com/v1/models` 作一次只读查询，HTTP200，模型目录列出
  同名 `deepseek-v4.1-flash`，声明 `openai-response` 支持；返回模型亦为同名，没有更换
  能力档位或猜测别名。该目录只能证明渠道声明的逻辑ID，不证明其内部上游部署。
- 沿用配置所选 **Responses**（`/v1/responses`），JSON-object，输出上限**8192**。
  真实请求body与旧jizhi首例**逐字段完全相同**，Card Prompt、Schema和359字来源均未改。
- Card-only新准备默认Metro，旧无Metro身份的准备不能直接执行新live Card。
  请求身份绑定渠道、接口、地址和实际wire；专用持久账本
  `data/retrieval/p05-card-metro-request-ledger/`，不覆盖或清空旧账本。新渠道实验由用户明确
  授权，不是靠改运行目录/随机指纹绕过失败保护。没有自动回退、重试或切换Chat试探。
  历史E2、shared SDK、G1和v0.3路由生命周期均不因此重开或改变。

## 响应与用量修复

`response_records.py` 独立保存以下结果，新请求及新建重放使用同一检查：

1. `provider_reported_usage`：原样保存提供商终态usage，另记算术合法性、观察依据及全部
   终态用量观察；不把报告数字当账单。协议或JSON失败也保留可观察计数。
2. `stream_validation`：保留原完整raw/SDK严格校验，并列出终态矛盾、对象ID漂移、
   原始/SDK不一致、输出截断、输出超限及其他协议错误。未放宽任何旧检查。
3. `content_validation`：最终消息单独作严格JSON、Schema、阅读回执、引文、来源合同
   校验，区分NON_JSON、SCHEMA_INVALID及来源/内容合同错误。reasoning不替代final。

`reasoning.txt`、`final.txt`分别保存（无响应的预发送失败不伪造文本），原始body、raw SSE、
SDK事件继续保存。失败增加简短原因与错误码，Key脱敏；内容PASS且协议PASS才创建
`value.json`并标CONTENT_VERIFIED。Metro Card任一收费失败阻止后续发送。

已做63项相关离线回归，包括新Metro MockTransport、重放/防重复、独立用量、截断、ID
漂移、Schema/来源错误、推理/最终内容分离及既有Card恢复。最后补充“多个终态用量观察
均保留”的记录字段后，新Metro六项测试再次PASS。没有全项目重复审计。首次测试因
系统临时目录不可写，改用仓库内临时测试目录；日志保留，不涉及真实请求。

## 唯一一次 Metro 真实结果

| 项目 | 观测 |
|---|---|
| 来源 | 2287 破魔之弓，359字，两条绑定片段 |
| HTTP / 终态 | 200 / completed |
| 模型 | 请求及返回均deepseek-v4.1-flash |
| 输出模式 / 请求cap | json_object / 8192 |
| reasoning / final | 22839 / 8070字符，内容不同、分别保存 |
| JSON/Schema/来源/精确引文 | PASS |
| 协议/输出预算 | FAIL：reasoning对象ID漂移，报告输出超过请求cap |
| 输入 / 输出 / 总Token | 2462 / 13665 / **16127**（提供商报告） |
| 请求耗时 | 92.841秒 |
| 实际费用 | UNKNOWN |

协议失败不是JSON错误：最终JSON确实完整且通过本地内容合同，但整份响应仍不允许
入库。它以明确的 `unaccepted-content.json` 保留独立只读提取结果，原job无 `value.json`。
没有为提高通过率忽略reasoning ID，也没有把cap改为实际输出量来追认成功。
本轮只对这份Metro原件作必要核对，不继续调查jizhi。

### 内容质量：仅Agent对源核对，不是人工验收

生成内容有13条原文观察、3条解释、3条假说、4条问题、5条查询。

- 文档概述连贯描述船上逐客、回忆与比较、保护誓言、放下弓及施术意图，没有数值配装。
- 观察明确写“巫女作条件式设想：若教射术者未随将军出征……”；没有把出征归给左卫门。
  “左卫门举止像他当年离开”保留为比较/回忆，而非身份同一。
- 未断言巫女属于海祇。赤穗百目鬼与左卫门、教射术者与“他”的对应放在假说中，
  与原文观察分开；法术后果与老猫结局保留未知。
- 仍有弱假说：紫电之鸢因雷神一方而垂死，原文没有确立其死因；虽标成假说，不能当事实。
  一条查询仍偏向战弓工艺；同篇巫女与器物归属的疑问可能偏保守，增加阅读负担。

这些是单篇内容的积极信号及残余问题，不能证明其他资料、长篇文档归纳或整体检索质量。
原请求仍整体FAIL，全部INACTIVE / NOT_REVIEWED，人工通过数0。

## 原五来源实验和剩余边界

累计发送：旧jizhi1次 + Metro1次 = **2/12**，剩余10次未使用，没有重试。
四份3076/3866/4275/3429仍未发送，长篇归纳未实际验证。
累计提供商报告输入4412、输出21854、总26266 Token；旧jizhi严格usage仍UNKNOWN，
观察数与旧原件均不改写，因此该总数不是完整验收或账单金额。

当前可继续离线优化Card和准备方案，但全库条件仍不具备：Metro协议身份/输出上限
尚未满足严格合同，五来源未完成，存储余量和独立备份问题仍在。按照本轮授权，遇异常
立即停止；不再自动换接口、调大cap或重发。后续若讨论Metro Chat路径，应明确作为
另一个受控运行条件，而非本轮自动盲试。
原先全库Token情景使用旧Card历史计数，不能视为新Metro Card的预算保证；本例长推理
及13665输出Token提示需要在取得合格的小样本后重新估算，不能直接照旧预算开全库。

证据根 `data/retrieval/p05-card-metro-live-20261029-r1/`；模型目录查询根
`data/retrieval/p05-card-metro-discovery-20261029-r1/`。保存后审计PASS，238份保护历史/冻结
文件未变。真实运行源码快照保留；运行后仅增加了“全部终态用量观察”字段，其六项离线
测试通过，Prompt/Schema/wire未变，审计如实标记当前代码与真实运行快照存在此差异。
同盘原件不等于独立备份。

接口语义参考：[OpenAI Responses流事件](https://developers.openai.com/api/docs/guides/streaming-responses)、
[JSON输出模式](https://developers.openai.com/api/docs/guides/structured-outputs)。这些文档不证明
Metro行为，渠道结论只依据本轮目录和真实raw/SDK证据。
