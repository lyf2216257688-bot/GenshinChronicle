# Metro reasoning.none 单变量真实验证

2026-10-29。用户确认最多两次：D1仅增加reasoning.effort=none，D1通过才执行D2（仅改
stream=false）。同一破魔359字来源、Metro DeepSeek、Card Prompt/Schema、json_object、
stream=true和8192输出cap。没有Qwen、Schema模式、五来源续跑、Embedding、Link、
commit/push。本次授权不是对旧失败的自动重试，而是明确的新参数实验。

**D1失败，因此D2未发送。** 渠道接受了带参数的请求，但未表现出关闭可见思考的效果。
不能把HTTP200或最终Card JSON合格称为none参数已验证可用。

| 项目 | D1实测 |
|---|---|
| 实际请求 | 与原Metro首例只相差新增 `reasoning:{effort:none}` |
| HTTP / 终态 | 200 / completed |
| 返回reasoning | 27533字符，未关闭 |
| 最终文本 | 8263字符，独立于reasoning保存 |
| JSON/Schema/精确引文/来源 | PASS |
| 流协议 | FAIL：reasoning对象ID漂移 |
| 输出预算 | FAIL：提供商报告14781，超过请求8192 |
| 提供商报告Token | 输入1958，输出14781，总16739；未提供reasoning_tokens细分 |
| 耗时 / 费用 | 99.332秒 / 实际费用UNKNOWN |
| 实际发送 / 重试 | 1 / 0 |

已核对实际request.body与计划一致，none参数确实发送，其他wire字段逐项不变；raw与SDK
事件一致，持久预留/outcome及封口job匹配。故不能归因于本地漏发参数或把reasoning
误选成最终文本。只能确定**当前Metro模型/凭据/Responses路径下，本次none没有达到
预期可观察行为**；不足以断言原厂参数不存在，也无法单凭档案区分渠道忽略、转换问题
或上游实现差异。本轮不再增加探针辨别这些内部原因。

与原Metro默认推理请求相比，reasoning字符22839→27533，报告输出13665→14781。
没有出现预期削减，但单次输出有采样差异，不能据此证明none会系统性增加消耗。
原响应仍只读保留，不覆盖此前失败或将其重新判为成功。

最终JSON有17条来源观察，仍正确把出征放在教射术者的条件设想中，左卫门保留为年龄/
举止比较对象。这里只说明局部内容核对，既不是人工验收，也不抵消协议/cap失败。
保留 `unaccepted-content.json` 供阅读，原job没有 `value.json`，没有正式入库Card。
全部INACTIVE / NOT_REVIEWED。

原五来源预算累计实际发送 **3/12**（jizhi1、Metro默认1、本次D1 1），尚余9次；D2没有
预留或发送。本轮条件未满足，所以剩余额度不触发任何自动调用。累计提供商报告输入
6370、输出36635、总43005 Token；旧jizhi严格usage仍UNKNOWN，该累计是观察数，不是
完整流验收或真实账单金额。

下一步需另行明确是否改变D2的执行前提或选择其他已调查方案；本轮不自动改为low、
非流式、Chat或Qwen，也不调高输出cap。参数/协议问题解决前仍不宜全库生成。

证据根：`data/retrieval/p05-metro-none-live-20261029-r1/`，含独立授权、封口来源/代码
快照、实际body、raw SSE、SDK事件、分离文本、用量、来源核对、账本关联和审计。
239份保护文件未变；D1持久账本条目保留。忽略目录独立备份仍未确认。
