# P05-W2 TokenMetro DeepSeek / GLM 同条件语义对比（2026-09-26）

## 结论边界

本轮是 TokenMetro 单线路、同条件、有限样本的语义质量观察，不是生产模型选择或 Phase05 semantic acceptance。当前四次请求均使用新的 immutable root：

`data/retrieval/p05-w2-tokenmetro-deepseek-glm-comparison-20260926-r1`

没有调用 Jizhi，没有修改 Prompt v2、schema、source、Block A，也没有 full build。ordinal16 和 ordinal20 来自冻结 preflight `data/retrieval/p05-w2-live-preflight-20260923-r2`，输入中均没有 `钓鱼`。

固定条件：

- Prompt v2 `25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e`
- authoritative output schema `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`
- ordinal16 input identity `4e0010b1045281441313afc682ba17ad9292c8bb3a01adeaf02d5dacec206461`
- ordinal20 input identity `a41fba88f092bf10991cb502c8b4c8e04cd18746ac1c02f8e1abb0c3982d3f97`
- official OpenAI Python SDK 3.19.2, `https://tokenmetro.com/v1`, `stream=true`, `max_tokens=500000`, `max_retries=0`
- request body 除 `model` 外相同；每个 model/ordinal 只发 1 次；没有自动 retry

## 运行结果

| Model | Ordinal | HTTP / finish | 本地结果 | latency | 首 chunk / 首可见内容 | input / completion | reasoning | visible chars | reported credit |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek | 16 | 200 / stop | accepted_for_local_contract | 29.872 s | 2.554 s / 27.872 s | 1,341 / 6,931 | 6,228 | 1,782 | 0.42 |
| DeepSeek | 20 | 200 / stop | accepted_for_local_contract | 68.808 s | 2.225 s / 62.857 s | 3,515 / 16,971 | 14,592 | 6,118 | 1.02 |
| GLM | 16 | 200 / stop | accepted_for_local_contract | 327.792 s | 2.570 s / 315.796 s | 1,236 / 14,414 | 13,348 | 2,809 | 0.59 |
| GLM | 20 | 200 / 未完成 | transport/stream failure | 71.974 s | 3.123 s / 无 | UNKNOWN | 9,654 chars 已收 | 0 | UNKNOWN |

GLM ordinal20 的错误是 `APIError: Upstream response stream ended before completion`：HTTP 已为 200，但只有 reasoning stream，未收到可见 JSON、finish reason 或 usage。它不是语义错误，也不能当作 GLM ordinal20 的语义结果；本轮没有重试。四次请求的 artifact hash/byte count、wire body、共同 messages、配置 secret absence 均通过核对。

`reported credit` 没有 currency 或 billable 字段，不能换算可靠成本。ordinal20 在旧的 16K ceiling 下曾出现 DeepSeek `finish_reason=length`；本轮共同使用 500K 后 DeepSeek 完成，说明较宽上限对 DeepSeek ordinal20 是必要的。GLM ordinal20 的本次中断不是 `length`，不能归因于上限；历史同 operating point 曾完成过一次，当前 route stability 仍需单独保留为 UNKNOWN。

## Ordinal 16：向导笔记

源文明确包含四个要点：巨大剧院位于至冬堡一侧；其下剧团为至冬最高演出水准；科洛列夫茨基剧院是歌手和舞蹈家的圣地；在该剧院舞台演出是许多艺术工作者的终身梦想。

### DeepSeek

做对：

- 保留剧团最高演出水准、艺术工作者演出梦想、巨大剧院与至冬堡的位置、剧团从属于巨大剧院、剧院是歌手和舞蹈家圣地。
- 没有把四个陈述合并成一个大事件，也没有添加因果、否定或不确定性。

小问题：

- “艺术工作者的梦想”作为无 subject 的 fact，检索仍可见，但比显式 `艺术工作者 -> 梦想在剧院演出` 的关系弱。

会影响导航的实质风险：

- 将“巨大剧院”和“科洛列夫茨基剧院”保留成两个未连接的 mention。源文很可能是在先描述后给出专名；这种保守处理会把本应汇聚到同一剧院的关系拆成两个图节点，降低从地点或剧团反查专名剧院的成功率。它不是已证实的错误等价，而是一个需要人工裁决的 coreference/navigation 风险。

### GLM

做对：

- 四个源事实均保留；把科洛列夫茨基剧院作为关系中心，显式保留位置、剧团隶属、圣地、艺术工作者梦想四条可导航关系。
- 提取了“至冬”和“艺术工作者” mention，关系粒度更适合后续 sparse KG 导航。

小问题或风险：

- 将“巨大剧院”与“科洛列夫茨基剧院”直接汇聚，源文没有单独的“即”字样。若要求所有等价关系都有明确证据，这属于未经显式声明的 coreference 推断；但本样本中没有其他明显事实错误。

### 样本判断

在 ordinal16 上，GLM 的关系可导航性优于 DeepSeek；DeepSeek 的优势是更保守，不主动合并两个表面 mention。这个差异是实际语义/图结构差异，不是 JSON 或 source-binding 差异。

## Ordinal 20：残破的航海日志

源文的关键链条是：前段盗宝团为寻秘宝招募人手并计划伪装进镇；括注说明中间页面不可读、后续笔迹似乎不同；后段声称带走魔王宝藏并计划回北方村子招兵买马；随后出现“似乎三天”、海雾无陆地；第九或第十天、丢弃物资、食物不足；最后发现所谓宝藏只是石头，日志戛然而止。

### DeepSeek（本轮有输出）

做对：

- 分开提取招募、航行顺利、带走宝藏、回北方村子并招兵买马、三天后海雾无陆地、丢弃物资/食物不足、宝藏变石头七个事件。
- 保留“似乎”带来的 fog 不确定性、日志中段涂画/后续笔迹不同、最终否定/反转；静态地点情报和伪装关系的 source attribution 基本清楚。
- 没有把宝藏变石头写成未经来源支持的因果链，也没有制造事件间的时间边或同名对象合并。

小问题：

- “丢弃物资、食物不足”整体被标为 tentative；源文不确定的是第九/第十天，丢弃和食物不足本身是后段作者的明确陈述，qualifier 范围偏宽。
- 没有输出“避免分赃不均而留记录”和“吞金宝地”的假设性旁支；这两项对核心导航较次要。

会影响导航的实质问题：

- 完全漏掉后段作者要让“跟着伊黎耶的那帮疯子”看到谁有资格成为枫丹未来主人的目标，连同伊黎耶、枫丹 mention 一起缺失。这是一个有明确人物/地点/目标归属的事件漏提，会直接削弱按政治目标或角色关系回找 RU 的能力。
- 将后段解释为“后续不同笔迹的记录者”时，mention 有 tentative，但多个事件 attribution 直接写成该记录者；源文只明确“笔迹似乎完全不同”，未证明一定是不同作者。对“谁做了什么”的导航属于中等归属风险。

粒度判断：

- 相比段落摘要，DeepSeek 保留了 fog、物资、反转三个后段转折，粒度方向正确；但漏掉政治目标使后段覆盖不完整。

### GLM（本轮无语义输出）

本轮只收到 reasoning stream，未收到可解析的 visible JSON；因此 ordinal20 的 GLM 做对/做错/漏掉、实体归属、事件粒度和导航 usefulness 全部为 `UNKNOWN`。唯一可确认的是运行稳定性问题：在同一输入和 500K ceiling 下，stream 在 71.974 s 后以 provider/SDK incomplete stream 终止；不能把它解释成语义质量差。

### 历史 GLM 参考（不是本轮配对结果）

旧 immutable root `data/retrieval/p05-w2-b-v2-sdk-glm-ordinal20-capability-500k-20260924-r1` 在同一 Prompt v2、source、schema、stream 和 500K operating point 下曾完成。该历史输出覆盖了政治计划、海雾、九/十天不确定性、物资短缺、笔迹不确定性、假设性“吞金宝地”和任务链接，覆盖面比本轮 DeepSeek 更宽；但它把“航行顺利 + 伪装”合成一个事件，把回村招兵与政治目标合成一个事件，并产生近似重复的“疯子跟随伊黎耶”关系。它只能作为历史行为参考，不能补齐本轮 GLM ordinal20 的缺失 cell，也不能单独支持模型胜出。

同样值得保留的是：旧 DeepSeek ordinal20 输出曾包含政治目标和伪装事件，而本轮 DeepSeek 输出漏掉政治目标但拆出了更完整的 fog/物资转折。这说明相同模型在同一输入上的事件取舍也会波动，两个样本不足以证明稳定模型特性。

## 横向判断

| 维度 | DeepSeek V4.1 Flash | GLM 5.3 Flash |
| --- | --- | --- |
| 关键事件 | 本轮 ordinal20 保留反转、海雾、物资链，但漏掉政治目标 | ordinal16 完整；ordinal20 本轮无输出，历史输出覆盖更宽 |
| 身份/归属 | 倾向保守拆 mention；ordinal20 把“疑似不同笔迹”在事件 attribution 中写得偏确定 | ordinal16 主动把描述性剧院汇聚为专名 hub；带来 coreference 推断风险 |
| 不确定/否定 | 保留 fog tentative、反转 polarity；但 tentative 范围有时过宽 | 历史 ordinal20 对笔迹和假设关系保留较好；本轮 ordinal20 无法验证 |
| 因果与事件拆分 | 本轮未见凭空因果；ordinal20 的 fog/物资/反转拆分较好 | 历史输出有事件过度合并和近似 tautological relation；本轮无法确认是否稳定 |
| RU 回找价值 | 静态地点关系好；政治目标漏提和剧院节点分裂会损失导航召回 | ordinal16 的关系 hub 更利于回找；ordinal20 当前 stream failure 使整体不可用 |
| 运行稳定性 | 两个样本均完整 stop；29.9 s / 68.8 s | ordinal16 完整但 327.8 s；ordinal20 本轮 stream incomplete |

## 结论

现有证据不足以宣布正式胜出。更准确的工作结论是：

1. **GLM 在 ordinal16 显示出更强的关系中心化和导航粒度，但本轮 ordinal20 没有可审阅输出，运行延迟显著更高。**
2. **DeepSeek 在两个本轮样本都完成，能保留明确的静态关系、反转和部分后段事件；但会漏掉高价值目标事件，并可能把不确定笔迹解释成确定的不同作者。**
3. **历史 GLM ordinal20 的覆盖较宽但有过度合并；历史与本轮 DeepSeek 的差异说明输出方差本身已是决定性限制。**
4. 当前证据可以支持“GLM ordinal16 的局部导航表达优于 DeepSeek ordinal16”和“DeepSeek 本轮运行完整性优于 GLM ordinal20”，不能支持全局模型胜负、生产/default 变更、semantic acceptance 或 full build。

本轮后不自动扩大样本、不 retry GLM ordinal20、不改 Prompt/schema/source，不把任何 semantic item 当作 citation authority。最终 citations 仍必须通过 Canonical/Retrieval Unit provenance 和 Evidence Packet；本报告只记录语义导航辅助层的观察。

## Evidence

- 本轮 immutable root：`data/retrieval/p05-w2-tokenmetro-deepseek-glm-comparison-20260926-r1`
- 本轮 manifest/checkpoint/terminal：root 下 `manifest.json`（immutable request/preflight snapshot）、`checkpoint.json`、`terminal_summary.json`，以及 `attempts/{deepseek,glm}/ordinal-{16,20}/`；最终 4 次请求计数以 checkpoint/terminal_summary 为准
- 本轮成功 canonical outputs：各成功 attempt 下的 `canonical_output.json`、`visible_content.txt`、`reasoning_content.txt`、`stream_chunks.jsonl`、`terminal.json`
- GLM ordinal20 failure：`attempts/glm/ordinal-20/sdk_error.json` 和 `terminal.json`
- 历史 GLM ordinal20：`data/retrieval/p05-w2-b-v2-sdk-glm-ordinal20-capability-500k-20260924-r1`
- 历史 DeepSeek ordinal20：`data/retrieval/p05-w2-b-v2-sdk-deepseek-ordinal20-capability-500k-20260924-r1`
