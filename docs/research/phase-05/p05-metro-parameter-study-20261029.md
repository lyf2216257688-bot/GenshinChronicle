# Metro Card 参数调查与离线草案

本报告为收费验证前的调查检查点。后续单独授权的[D1真实结果](p05-metro-none-live-20261029.md)
已确认none实际发出却仍有可见reasoning及ID/cap失败；不能继续把该组合当作仅待测的
正向候选。D2条件未满足、未发送；本报告的其他参数调查与旧原件保留。

2026-10-29。只读公开资料、现有源码/档案及离线Mock测试；**新增收费请求0**，未恢复
五来源实验，未切换正式Card模型或更改Card Prompt/Schema/v0.3，未commit/push。

**建议下一次先保持DeepSeek和现有接口，仅增加 `reasoning.effort=none`。**
`low`不是关闭思考。之后分步验证非流式和Schema模式；Qwen保留为成本/格式候选，
不能依据官方能力声明就自动替换已经做过内容检查的DeepSeek。

## 1. Metro 页面实际说明了什么

本次重新读取公共 `/api/pricing`，两模型都在国模分组，并声明
`/v1/chat/completions`、`/v1/responses`。定价页API示例来自它实际加载的
`static/js/async/77952.a9b15d0abd.js`（随版本可能变化，原件及哈希已保存）。
当前环境没有通用浏览器表面，**没有声称已渲染/点击模型详情弹窗**；调查读取的是页面
本身使用的示例生成器、接口映射和公开数据，而不是凭常识编造调用示例。

- Responses Python示例只传 `model` 和 `input`，再取 `response.output_text`。
- Chat Python示例只传 `model`、`messages`，再取 `choices[0].message.content`。
- 示例不显式设置stream，其形态是SDK默认非流式调用；这只是公开示例，不是实测。
- 该组件没有 `enable_thinking`、`thinking_budget`、`reasoning.effort`、`json_schema`
  专属示例。通用参数表的 `response_format` 描述提到JSON/schema输出，但按模型名规则
  套用通用chat表，**不是模型、接口、参数组合的逐项兼容承诺**。
- 因而“Metro有端点”与“Metro透传并执行高级参数”必须分开。

来源：[Metro定价数据](https://tokenmetro.com/api/pricing)、
[DeepSeek详情页](https://tokenmetro.com/pricing/deepseek-v4.1-flash)、
[实际页面示例生成器](https://tokenmetro.com/static/js/async/77952.a9b15d0abd.js)。

## 2. 三层支持矩阵

“官方”表示原厂API文档声明；“已验证”只指当前Card凭据槽位/请求路径的真实证据，
不把SDK成功序列化、页面示例或历史其他槽位的协议探针当作已验证。

| 参数/行为 | Metro公开声明/示例 | 模型官方支持 | 当前Metro Card真实验证 |
|---|---|---|---|
| DeepSeek Responses `reasoning.effort=low` | 高级参数未明示；仅端点 | 是，启用低强度思考 | 未测 |
| DeepSeek Responses `reasoning.effort=none` | 高级参数未明示；仅端点 | 是，关闭思考 | 未测 |
| DeepSeek `text.format.type=json_schema` | 未见该专属示例；通用Chat表提Schema | 是，包含name/schema | 未测；不能从已有json_object通过推出 |
| DeepSeek Responses `stream=false` | SDK默认非流式基础示例 | 是，返回完整response对象 | 未测 |
| Qwen Chat `enable_thinking` | 未见专属示例 | 是，true/false | 未测 |
| Qwen Chat `thinking_budget` | 未见专属示例 | 是，控制思考预算 | 未测 |
| Qwen Chat `response_format=json_schema` | 通用response_format说明，不是专属验证 | 是，Qwen3.8 Flash在详细支持清单中 | 未测 |

唯一当前Card真实Metro请求仍是DeepSeek + stream=true + json_object + 未指定推理控制：
内容JSON/Schema/来源PASS，reasoning ID及8192输出cap FAIL；不属于任何新增参数的
正向验证。仓库历史Qwen E2探针也不证明本Card槽位的Chat/thinking/schema组合可用。

### 原厂文档中的重要边界

DeepSeek原厂模型目录把 `deepseek-flash` 展示为 **DeepSeek-V4.1-Flash**；Metro使用
`deepseek-v4.1-flash`。本调查不替换Metro ID，也不声称证明其内部上游部署。
原厂Responses接口列出none/low/high/max，默认开启思考；`max_output_tokens`包括思考
和可见答案；json_schema的name/schema及非流式response形式均有定义。原厂这页没有
明确列出format.strict，因此DeepSeek实验草案没有凭空加入strict字段。
[DeepSeek Responses](https://api-docs.deepseek.com/api/create-response/)、
[官方模型身份](https://api-docs.deepseek.com/api/list-models/)。

Qwen官方说明：Python SDK中用 `extra_body` 传自定义参数，但实际HTTP body应当把
`enable_thinking`、`thinking_budget`放顶层，不能把extra_body整个嵌套发给服务端。
关闭思考建议显式false，并省略无意义的thinking_budget；不要把low或budget=0假定为
关闭。启用思考时预算草案可用1024，**不同时传reasoning_effort与thinking_budget**。
`max_completion_tokens`限制思考+答案，`max_tokens`的含义可能只限答案；优先前者。
官方给出默认高思考预算，不显式关闭可能产生额外消耗。
[Qwen Chat参数](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)、
[思考开关与预算](https://www.alibabacloud.com/help/en/model-studio/deep-thinking)。

Qwen3.8 Flash属于官方JSON Schema详细支持清单，Chat格式为
`response_format={type:json_schema,json_schema:{name,strict:true,schema}}`。
该支持有部署/地域边界，文档注明新加坡暂不支持；Metro上游部署未知。Card原Schema含
uniqueItems/minItems等约束，必须原样核对渠道兼容性，不能为得到HTTP200删掉约束，
也不能把strict参数被接受当作模型确实遵循。无论远端模式如何，本地全Schema和来源
校验继续执行。[Qwen结构化输出](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output)。

## 3. 最小验证顺序：每步授权、失败即停

保持同一359字来源、Card Prompt/Schema、8192 cap、既有原始响应和账本规则。
**建议先只授权前两次，不恢复五来源批次。**

1. **D1：只增加reasoning.none。** 保留Responses、stream=true、json_object，对比已经
   保存的默认推理失败例。检查是否接受参数、是否实际仍返回reasoning、报告推理/输出
   Token、ID一致性、cap和来源合同。参数被静默忽略时不能称none已支持；缺少推理
   计数时最多确认“未观察到可见推理”，不能证明内部绝无思考。
2. **D2：D1通过后，只改stream=false。** 控制其余条件；原始完整JSON与SDK对象核对，
   检查终态、模型、最终消息唯一性、独立用量和内容。非流式只减少SSE事件交叉绑定风险，
   不能证明此前ID漂移已在服务器被修复，也不能保证减少推理Token或解决超cap。
3. **D3（后续单独决定）：只改json_schema。** 在D2基础上原样提交Card Schema，核对
   接受及遵循；不改Prompt，不自动回退到宽松Schema。low作为独立对照，不在none失败后
   自动替换；它仍可能保留reasoning/额外消耗。
4. **Qwen候选（另行确认模型实验）：** 首先Chat非流式、enable_thinking=false、
   max_completion_tokens=8192、json_object；通过后只改json_schema。需要深层解释时
   再比较enable_thinking=true + thinking_budget=1024；不能顺带改变temperature等参数。

任何协议异常、非JSON、来源错误、cap异常或发送UNKNOWN均停止，不利用不同参数
指纹偷偷重试旧失败。模型/协议/开关的改变需要明确实验身份和授权；过去2/12发送记录
仍在，剩余10次本轮未使用。要隔离模型语义差异，后续还需同接口/同预算/同资料对照；
不能把“DeepSeek Responses”和“Qwen Chat”一次对比的差异全归因于模型。

## 4. 作为Card生成器的比较

| 方面 | DeepSeek V4.1 Flash | Qwen3.8 Flash |
|---|---|---|
| 当前格式证据 | 本Card JSON/Schema/引文确实通过一次，整体协议失败 | 官方有Schema支持；此Card通道无真实结果，不能评为更可靠 |
| 推理成本 | 默认行为已产生22839字符reasoning；none值得先测 | 原厂默认也会思考，必须显式关闭或控制预算，不能假设天然便宜 |
| 预算控制 | 官方总cap包含推理；当前Metro实测报告超cap，尚未合格 | 官方有总cap与思考子预算；Metro执行力度未知 |
| 剧情语义 | 单例正确保留教射术者与左卫门区别，仍有弱假说 | 尚无同Card比较，中文理解/指代/假说质量均需实测，不能按品牌推定 |
| 使用定位 | 优先验证参数，避免同时换模型和接口 | 可作为结构化/成本候选，不能直接成为正式模型 |

公开pricing表达式显示：DeepSeek非峰/峰的输入系数1/2、输出4/8，Qwen标准输入0.8、
输出2.7；两者国模组倍率均0.5。同Token结构和同组倍率下，Qwen的非缓存输入/输出
系数较低；但缓存读系数Qwen0.1高于DeepSeek0.02/0.04，DeepSeek还有UTC峰谷条件。
这些是公开报价结构，不是实际账户结算、请求费用或新实测。总成本还取决于推理量、
重读、缓存和成功率；本轮没有账单，本轮新增费用0。

## 5. 已完成的离线适配及未完成边界

- 新增独立 `metro_parameter_drafts.py`，**没有发送入口**，不接入当前Card默认wire。
  七个参数草案使用真实旧view生成，保留同一Prompt和完整Schema，明确send_authorized=false。
  实际body指纹包含渠道、接口和参数；SDK额外字段展平可核对。
- SDK+MockTransport验证了所有草案的实际请求体，覆盖Responses none/low、非流式、
  Schema，以及Qwen Chat扩展参数；严格拒绝模型错绑、截断、非JSON、用量超限、
  raw/SDK不一致及“请求关闭思考却返回可见思考”。**10项离线测试PASS**，零真实socket。
  这些不是Metro参数能力证明。
- 非流式只读解析原型分别提取Responses output和Chat choices，保留reasoning与final、
  原始usage与规范化计数，复用原Card校验；明确不作SSE身份一致性声明，不产生正式
  CONTENT_VERIFIED job。来源前置仍要求既有受验证的Canonical/RU registry。
- 当前收费执行器依然只接入已归档的Responses流式路径。非流式/Chat草案未启用收费；
  将来选择它们前，还需接入明确计划、原始JSON归档和同等审计回放，不能直接把草案
  塞入原流式执行循环。无需为此重构NaviRAG。

最推荐的**下一次单请求配置**：Metro原模型、Responses、stream=true、
reasoning={effort:none}、text.format={type:json_object}、max_output_tokens=8192。
后续若D1/D2/D3均证明可用，可考虑非流式+none+完整Schema作为离线Card运行配置；
这仍只是待验证方案，不是现在已经可靠的正式配置。

证据根：`data/retrieval/p05-metro-parameter-study-20261029-r1/`。以 `final-drafts/` 为
最终草案；较早drafts仅保留准备过程。公开数据、页面代码、来源链接、SDK测试及保护
文件核对均保留；没有修改旧运行、旧索引、旧请求账本或默认Card生成模型。
