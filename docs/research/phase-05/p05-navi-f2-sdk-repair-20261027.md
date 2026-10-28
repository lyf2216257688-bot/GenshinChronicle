# F2 SDK 通信修复与恢复检查（2026-10-27）

**通信适配已完成并通过离线验证；F2 真实对照仍未开始。** 本轮模型调用 0 次，
另有一次无认证的首页 GET 返回 200。该结果不等于认证 Responses POST / 30K
能力已确认，因此未用额外模型探测或首条正式实验请求来试探能力。

## 1. 根因与证据

首次 HTTP 403 / Error 1010、Ray ID `a51115feba34e353` 是已保存的真实响应。
[Cloudflare 官方说明](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/)
将 1010 与客户端特征拒绝关联，并将默认 urllib 标识列为某些部署形态的可能触发因素。
这支持客户端兼容问题的优先排查方向，但不能证明极智采用了其中哪条规则。
用户提供的“同一首页 curl 200、urllib 403”是进一步线索，本轮未重复该组网络试验。

历史 G1 使用 OpenAI SDK + httpx，应用代理由显式 `NAVI_R1_PROXY_URL` 决定，
`trust_env=False`；当时实际出口未在现有记录中得到证明。旧 F2 urllib 依赖默认
代理发现，并使用 `Python-urllib/3.14`。上一轮离线检查发现的 Windows 代理不能
追溯证明两次失败时的实际出口；第二次只留下 URLError 类型，原因和执行状态仍未知。

本轮无密钥首页 GET 使用与 SDK 相同的默认标识 `OpenAI/Python 3.19.2`、httpx 0.28.1，
显式 `proxy=None`、`trust_env=False`、无重定向或重试，返回 HTTP 200。
新 Ray ID 为 `a51276270f3ee365-NRT`，观察到 TCP 对端 `198.18.0.8:443`。
这不是公网出口 IP 的证明；应用层禁用隐式代理也不能证明不存在系统级透明转发。
未调整系统代理、IP、浏览器身份、全局模型或环境设置。

记录：`data/retrieval/p05-navi-f2-sdk-repair-20261027-r1/non-model-access-check.json`。
该 GET 使用 SDK 默认 User-Agent 的普通 httpx 请求，不是模型 Responses 调用，也不是
以 SDK 已解析对象冒充原始网络响应。当前尚无服务商关于账户认证、POST 放行及 30K
限额的确认；首页可访问与 API 配置可执行之间仍存在证据缺口。

## 2. 最小代码修改

- 新增 `src/genshin_corpus/retrieval/navi_f2_sdk_transport.py`，实际模型请求通过
  `OpenAI.responses.create`，固定 jizhi Responses 路径、模型及输出上限 30000。
  复用 C1 原文渲染、SSE 解码和历史完整性检查，以及既有 create-only 写入机制。
  不进入旧 E2SDK 的 8192 上限分支，也不修改历史实现或冻结合同。
- 显式选择应用代理；httpx 客户端和原生传输均关闭环境代理继承，SDK/传输重试均为 0，
  禁止跟随重定向。两臂使用同一调用函数，没有独立的温度、思考或输出参数。
- HTTP 传输流包装器先落盘原始响应体字节，再交给 httpx 内容解码及 SDK 解析。
  `response.body.raw` 保留 Content-Encoding（含 gzip）；它不包含 TLS 包、HTTP
  分块传输边界或未记录的头部。选定 HTTP 头独立保存在 `http.json`。
  `sdk-events.jsonl` 是 SDK 解析结果，`output.json` 是最终文字和 usage，三者明确区分。
- 同时对原始字节重解码事件及 SDK 事件执行 C1 终止、模型、内容和 usage 校验；不修补
  JSON。完整流通过仅为机械状态，之后仍须使用冻结 Preflight 原生 Schema / 绑定检查
  和统一语义评审。本轮没有建立另一套内容评审或实验循环。
- 持久化请求指纹预留，成功请求也不能换 ID 重发；pending、UNKNOWN、拒绝、截断或
  存档 hash 不一致都会阻断同一运行的自动继续。外部未决请求可通过隔离指纹清单拒绝。
  调用方仍负责跨运行隔离清单、冻结调用顺序、48+最多8的授权账目及完整输入复核；
  该模块是单请求适配，不自建或自动启动实验批次。
- 异常保留 SDK/httpx/OS 原因链类型与错误码；不保存可能夹带密钥、请求内容的任意
  异常文本。仅有明确连接失败轨迹且未开始发送请求头才记 NOT_SENT；其他无响应记
  UNKNOWN。所有情形都不自动重试。旧 URLError 不能据此追溯改判。
- 停用 `navi_f2_live_probe.py` 的 urllib 发送入口，防止再发旧探测；原源码已独立快照，
  两次原请求、原始 403、UNKNOWN 和冻结索引均保留。

## 3. 实际验证

| 检查 | 结果与边界 |
|---|---|
| 48 份冻结请求走完整 SDK 模拟传输 | PASS；正文、方向、Prompt、审核原句、模型、路径及统一30K保持一致 |
| 新增10项适配测试 + C1相关7项 | 17 PASS，全部小型离线 fixture |
| 完整 SSE / gzip 原始字节与 SDK 事件分存 | PASS |
| 403 / 超时原因链 / 中途断流 / 缺终止 / 输出截断 | PASS；保留错误并暂停，无自动重试 |
| 请求指纹、重复发送、UNKNOWN隔离、续跑hash检查 | PASS；单运行及显式外部隔离清单范围内 |
| 冻结 Preflight / F1-R2 验证 | PASS |
| 历史数据、C1、两次失败证据 | 3416个内容文件hash核对，无变化（不含这些根自身的seal文件） |
| 不发送模型的首页可达性检查 | 200，仅证明此次首页路径可达 |

逐请求模拟记录、测试日志与检查结果位于独立数据根。`SYNTHETIC_ONLY/` 内的48条
响应和 usage 全是合成 fixture，不能计入真实模型成功率或用量。没有进行 Embedding
推理、其他工作树操作、Git提交/合并/推送或正式知识发布。

## 4. F2状态与遗留条件

两次历史探测均已发送：一次403，一次执行UNKNOWN；真实模型成功响应0条，usage和
费用均未知。基础48次发送0次，定向复核0次。本轮模型调用0次、非模型GET 1次。
没有真实新旧结果，无法判断哪版语义质量更好，不以模拟PASS替代效果比较。

目前已具备SDK通信代码与离线证据；尚不能宣称正式运行条件满足。需要服务商确认
正常SDK的认证Responses POST允许访问及所用模型/分组接受30000输出参数，或提供
等价的现有可核验成功证据。第二次未决请求须保留隔离和费用UNKNOWN；可以由服务商
依据UTC `2026-10-27T11:06:43.393419Z`、正文hash
`4f497a302392465cfe13d8e05eb165a2e61f95b4a56b7804035999100f65b37b`
核查，但本轮不重发它。首次Ray ID仍可用于服务商排查客户端误拦截。

原48次及最多8次对称复核的费用授权持续有效，没有新增金额审批要求。当前停止原因
是上述实际访问/参数证据缺口，不是费用上限；不把额外能力探测包装成正式实验。

SDK接入依据：[OpenAI Python 官方文档](https://developers.openai.com/api/reference/python)。
此文档用于SDK接口核验，不代表OpenAI为极智端点作能力或价格担保。
