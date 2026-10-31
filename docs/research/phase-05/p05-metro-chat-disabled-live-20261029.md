# Metro Chat Completions关闭思考：单次真实Card验证

2026-10-29。用户明确停止Responses参数试错，授权直接验证Chat Completions关闭思考。
只发送《破魔之弓》一次，无重试、无其他资料续跑，无模型切换。

## 实际结果

**可见思考已消失，但请求整体失败，零可入库Card。**

| 项目 | 实际观察 |
|---|---|
| 渠道与模型 | Metro / 请求及返回均deepseek-v4.1-flash |
| 端点 | https://tokenmetro.com/v1/chat/completions |
| 参数 | stream=false、max_tokens=8192、response_format=json_object、thinking.type=disabled |
| HTTP / finish_reason | 200 / stop |
| reasoning | message仅content、role；无reasoning字段；无reasoning_tokens明细 |
| 最终内容 | 完整可解析JSON，reasoning未混入正文 |
| 耗时 | 23.252秒 |
| 提供商用量 | prompt1910、completion3450、total5419 |
| 用量验收 | FAIL：1910+3450=5360，和提供商total相差59；不擅自修正 |
| 输出上限 | 报告completion3450低于8192，无观察到超限 |
| Schema | FAIL：interpretations 3项、hypotheses 3项、open_questions 5项应为对象，实际为字符串 |
| 原文引证诊断 | 10条观察引文均在指定交付片段内唯一匹配；不抵消Schema失败 |
| 实际收费 | UNKNOWN，Token并非账单 |
| 状态 | PROTOCOL_OR_CONTENT_INVALID；INACTIVE / NOT_REVIEWED；无value.json |

关闭思考仅能确认“本次没有可见reasoning，也未报告reasoning计数”；无法证明提供商内部
完全没有推理Token或成本。官方Chat提供thinking.type=disabled，但本次不能认证Metro所有
其他参数、模型或未来请求均可靠。[DeepSeek官方接口](https://api-docs.deepseek.com/api/create-chat-completion/)

## Card内容观察（未验收原件）

- 比前期错误有所改善：教射术者随将军出征仍置于条件回忆句；未声称清籁巫女属于海祇。
- 保留放下战弓、动用影向山法术、解开结界的意图，并区分意图和已发生结果。
- 仍有具体问题：overview出现无原文依据的“转为敌对阵营造船”；对“左卫门……和他当年”
  的指代仍可能误读，且假说再次把教射术者与欲保护的人相联系。虽然标为假说，仍需人工核对。
- action字段出现“清代籁”的误字。不能把引用可匹配等同于叙述、行动归属正确。
- 这只是同一来源一个阅读批次，不能证明完整分层归纳或全库Card质量。

## 工程与证据

新增显式opt-in `metro_chat.py`，不改原Card默认wire、Prompt、Schema、冻结v0.3或Responses
校验。SDK和HTTP均零重试；原始响应先于SDK解析落盘，实际HTTP body核对关闭思考参数，
沿用Metro持久账本，指纹包含Chat端点身份。已完成任务只读复用，pending/重复指纹拒绝。
非流式的stream_validation为NOT_APPLICABLE，协议验收独立记录，不伪造SSE一致性结果。

运行后仅修正新适配器的诊断展示：用量加和异常不再误标成输出超限，并新增显式思考观察。
真实运行原件、执行代码快照、result、账本均未改写；新只读结论保存在offline-diagnosis.json。
因此当前代码与运行快照存在这两处诊断差异，wire/Prompt/Schema完全未变，无额外调用。

14项针对性测试PASS（Chat4、原Metro与参数草案10），真实socket拦截，覆盖原始归档、
实际请求体、用量矛盾、超限、正文合同、reasoning分离、防重发和完成结果只读复用。
240个保护文件哈希不变。没有Embedding、Link、全库生成、commit或push。

完整证据：`data/retrieval/p05-metro-chat-disabled-live-20261029-r1/`，含prepared来源与代码、
jobs/chat-disabled原始body/HTTP/SDK/trace/reasoning/final/result、audit、offline-diagnosis和校验和。
该目录被Git忽略，同盘归档不等于独立备份。

父实验累计4/12实际发送，余8次额度未使用；历史各失败均保留。额度不代表自动继续许可。
本轮停止；不回退Responses、不自动改Schema/Prompt、不重复采样。
后续若继续，需用户另行决定一次有明确边界的格式约束验证；用量差异仍须保持UNKNOWN，
不能为继续生成而忽略或改写提供商计数。本次尚不具备可靠批量Card生产条件。
