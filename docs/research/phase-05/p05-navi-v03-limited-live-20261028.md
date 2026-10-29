# Navi v0.3 有限真实模型验收

**日期：2026-10-28。** 本轮按用户明确授权，使用冻结的 `p05-navi-b-v0.3-rc1`、`navi_v03_runner.py`、极智渠道 `deepseek-v4.1-flash`、Responses API、`json_object`，每次输出上限30,000 Token。三组均使用F1-R2冻结的 `model-inputs/rc1` 来源包；审核组连同冻结登记的原始候选句一并绑定。未切换模型/渠道，未改Prompt、候选代码、Schema或历史材料。

## 运行与机械验收

共收到 **3个HTTP 200完成响应**，总尝试5次。其中两次TLS握手失败均由持久归档证明发生在发送请求头/正文之前；运行器以相同请求正文哈希和身份各重试一次。没有对已发送、UNKNOWN、HTTP响应后故障或内容失败重发。三份运行档案只读重放均 `archive_integrity=PASS`，终态、raw/SDK流一致性、完整SSE、usage、JSON解析、模式Schema、完整rc1合同及来源绑定均通过。`json_object`线上返回这三份响应并通过本地检查，不证明服务商强制执行 `json_schema`。

| 用例 | 完成请求 | 尝试 | usage（输入/输出/合计） | JSON/Schema/合同 | 原始响应 |
|---|---:|---:|---:|---|---|
| 飞天御剑—飞天大御剑（生成） | 1 | 2（1次已证实未发送） | 2,514 / 1,752 / 4,266 | PASS | [response.body.raw](../../../data/retrieval/p05-navi-v03-limited-live-20261028-r1/runs/01-swords/attempts/attempt-002/response.body.raw) |
| 匣里龙吟—海螺的回响（生成） | 1 | 2（1次已证实未发送） | 3,875 / 1,753 / 5,628 | PASS | [response.body.raw](../../../data/retrieval/p05-navi-v03-limited-live-20261028-r1/runs/02-shell/attempts/attempt-002/response.body.raw) |
| 哈玛瓦兰战记—普尔希娜论文集（审核） | 1 | 1 | 3,445 / 2,602 / 6,047 | PASS | [response.body.raw](../../../data/retrieval/p05-navi-v03-limited-live-20261028-r1/runs/03-haramwaran-review/attempts/attempt-001/response.body.raw) |
| **合计** | **3** | **5** | **9,834 / 6,107 / 15,941** | **3/3 PASS** | 原始SSE与SDK事件均归档 |

各行的 `candidate.json`、逐次请求正文、SDK事件、网络trace、retry decision、response ID、usage与文件SHA-256均留在同一运行目录；`evaluation.json`汇总计划哈希、wire指纹、响应哈希和语义结果。服务商实际账单/费用未核验，为UNKNOWN。

## 逐组语义核对

1. **飞天御剑—飞天大御剑：符合目标。** 模型指出B001的“二度飞天”直接承接A001首次尝试，并用A004与B004的失败、转变与结局补充叙事。该具体文本连续性被保留为 `CONCRETE_STORY` / `ELIGIBLE_PENDING_RELEASE`，仅是传说文本内的连续性，不被说成确定历史。较宽的“两段属于同一人物、同一时间线”命题另标为争议并要求审核，没有由它取代直接引用证据。
2. **匣里龙吟—海螺的回响：符合目标。** 模型识别刀/刀鞘、约束/暴力等文学对照，同时说明两个具体故事的物件、人物与事件不同；三条命题均为C并 `EXCLUDED`，没有建立共同剧情导航关系。
3. **哈玛瓦兰战记—普尔希娜论文集：主要符合目标，改写仍需审核。** 模型拒绝原句的作者与角色颠倒（D/REJECT/EXCLUDED）：A004将复述者写为普尔希娜，A006/A008将旅行学者指向哈玛瓦兰；B001的论文集标题不能证明二者身份。另保留了只陈述两端均出现“普尔希娜”及各自著述语境的收窄句，明确不推断同一人或事件联系，状态 `B / REWRITE / REVIEW_REQUIRED`。由于目前只有同名和著述线索、具体剧情桥梁仍未证实，这句不进入正式导航，不能视作语义准入通过。

## 结论与边界

**建议：可以推进小规模 Navi 建设，但仅把候选保持为INACTIVE并逐条人工语义审核。** 三个定向例子分别通过了预期的连续性保留、文学类比排除和错误身份断言拒绝；这里是对三份实际输出的个案结果，不是广泛准确率或自动发布授权。第一组的二度飞天证据尤其直接；第三组的安全改写只可作为人工复核候选。

真实服务端强制Schema的能力仍为 **UNKNOWN**（本轮只用 `json_object`）；本地完整合同验证在3/3通过。实际费用/账单为 **UNKNOWN**。三例不足以证明跨题材语义稳定性、召回率或无幻觉率。三份结果的 `human_acceptance=NOT_REVIEWED`、`navigation_status=INACTIVE`；未发布Navi、未触发额外请求、未改F2历史、并行Embedding、Prompt或生产默认，也未commit/push。

机器可读汇总：[evaluation.json](../../../data/retrieval/p05-navi-v03-limited-live-20261028-r1/evaluation.json)。
