# G3 thinking：TokenMetro 免费参数核查

2026-11-14（Asia/Tokyo）。用户选择先免费核查 TokenMetro 文档与参数透传说明。
本次只读公开 HTTP GET、静态页面组件、官方文档及已有 G3 原始回执；
**新增付费/模型请求0，没有读取或发送凭据，没有登录后台或修改渠道配置。**
没有执行下载的脚本、安装任何页面建议的软件或联系第三方。

结论：GC 的已发送参数位置符合 DeepSeek 官方 Chat 格式，原始响应本身缺少
reasoning；没有发现足以支持修改 GC 请求或解除门禁的渠道合同。
**精确 TokenMetro/1017 路由的动态 thinking 与 reasoning 回传仍未证实。**
这次核查不能确定是渠道过滤/转换、上游行为还是其他原因。
此前付费事实仍为2次请求、212个供应商报告 token，工具及流式未实测。

## 已核实的边界

| 核查项 | 事实与能支持的结论 |
| --- | --- |
| GC 发出的 HTTP body | 两份原始 `request.body.bin` 顶层分别包含 `thinking: {type: disabled/enabled}`，不存在嵌套的 `extra_body`。这是直接 HTTP JSON，不是 SDK 参数字典。 |
| DeepSeek 官方格式 | [thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/)使用同一开关形式；SDK 的 `extra_body` 是把自定义字段加入实际请求体的调用方式，不是要求 HTTP body 再嵌套一层。文档还描述 reasoning 字段以及 tools 历史回传要求。其声明不等于 TokenMetro 逐项兼容证明。 |
| GC 是否丢弃字段 | 两份 `response.body.raw` 的 assistant 都仅有 `role/content`；`decoded.json` 与之相符，没有原始 reasoning 被 GC 解码器删除的证据。不能据此排除渠道在原始 HTTP 响应到达前已作转换。 |
| 当前精确型号 | [公开定价数据](https://tokenmetro.com/api/pricing)明确列出 `deepseek-v4.1-flash-1017`、推理/工具标签和 `openai` 端点，端点映射为 `/v1/chat/completions`。目录中的1M等标签是渠道声明，不扩展已批准的上下文/费用工作点。 |
| 当前模型说明/示例 | [模型页](https://tokenmetro.com/pricing/deepseek-v4.1-flash-1017)所用公开组件依模型名匹配类别后生成通用参数表/示例。1017落入通用 chat 类；并非逐参数验证清单。没有精确路由的 `thinking` 或 `reasoning_content` 透传说明。表中其他类别出现 `reasoning_effort` 也不证明1017应改用它。 |
| 公共接入文档 | [文档页](https://tokenmetro.com/docs/)组件给出通用 Chat 接入及流式/工具等协议说明，没有 DeepSeek thinking 专项合同。[公开状态](https://tokenmetro.com/api/status)的 `docs_link` 为空，表示未配置该站外文档链接，并非本站没有 `/docs/`。 |
| 渠道公告 | 同一公开状态中的2026-11-07公告声称国模分组相关模型已改为官方 API Key 直连；2026-09-20公告曾声称修复 DeepSeek 思考回传。两者均不是本次精确1017请求的上游字节或配置实证。 |
| 参数透传配置 | 当前站点的公开前端包含全局请求透传、thinking 后缀处理与 Chat→Responses 兼容配置界面。前端存在配置功能不表示这些功能对本次路由启用；公共状态不返回实际值。未访问任何需要认证的配置接口。 |

官方文档还列出可选 `reasoning_effort`，不能仅因本次未设置它就认定是故障原因。
目前没有渠道证据支持增设某字段、改型号后缀、切协议或放宽“未观察到开启”的门禁。
同理，缺少 usage 中的 reasoning 明细本身不是“完全没有内部思考”的证明。

## 实际读取与存证

浏览工具不能读取动态模型页正文，转为下载同一站点实际引用的公开静态文件并只读分析；
没有声称已渲染或点击浏览器页面。只跟随 HTML/入口组件提供的路径，不猜测私有端点。
首次公开 pricing GET 遇到连接错误，之后一次有界 GET 成功；这不是模型调用重试。

公开站点当次状态报告版本 `f1db851e`；不把它直接认作某开源仓库的提交或实际上游身份。
以下为当次页面使用的组件，路径和 hash 随网站部署可能变化：

- [入口 bundle](https://tokenmetro.com/static/js/index.d7fe775086.js)：实际路由、公开API调用与配置界面。
- [模型详情公共组件](https://tokenmetro.com/static/js/async/n.88777b6d1f.js)：参数表分类与代码示例。
- [文档组件](https://tokenmetro.com/static/js/async/pc.71f9ace16b.js)：通用接入说明。

完整原件、GET元数据、选定模型/公告、参数分类片段及原请求复核结果存于
`.local/generation-g3-doccheck-20261114/`，Git-ignored。
其 seal SHA256：`503c59eb1e8cb99788368b2515f2113c99ed62fd674d580aef5ef80ede78d6d2`。
旧探针 preparation/report/call seal 校验通过，新适配器与 runner 的源码 hash
仍匹配原付费 preparation；未改变源代码、模型配置、预算或历史数据。

历史[2026-10-29参数调查](phase-05/p05-metro-parameter-study-20261029.md)仅作为沿革线索；
本次重新取证，没有把当时的前端资源路径或未测参数沿用为当前事实。
[G3 Live 停止报告](clean-generation-g3-20261114.md)保留原检查点，不改写其结果。

## 可供渠道确认的问题

下面是准备好的问题清单，尚未发送；不含 API Key、账号或完整会话数据：

> 请确认 `https://tokenmetro.com/v1/chat/completions` 上精确模型
> `deepseek-v4.1-flash-1017` 的当前行为。2026-11-14两次非流式测试分别发送顶层
> `thinking.type=disabled` 和 `enabled`，均HTTP200并返回该精确model字段；
> assistant只含role/content，没有reasoning_content或推理用量明细。
>
> 1. 此路由是否原样接收并传递 `thinking.type`？是否存在字段过滤、覆盖或协议转换？
> 2. 开启时是否应回传 `reasoning_content`？若渠道会隐藏/剥离，请说明规则，以及原生工具
>    循环是否仍要求回传该字段；非流式与流式是否不同？
> 3. 若需要额外参数，请提供该精确模型、该端点的最小请求和响应示例，并说明
>    开关/effort与输出上限的实际处理，不以其他型号或端点示例替代。

下一条件：获得精确路由说明后，由用户裁决是否进行定向有限实测及其工作点。
在该核查检查点，不消耗原探针剩余额度，不把目录标签当作能力通过，也未进入 G4–G7。
后续单独授权的 G4 离线工程见[current-phase](../current-phase.md)与
[G4报告](clean-generation-g4-20261114.md)；它不解除此处的真实渠道门禁。
