# P05-Navi-E2 最小 continuation 增量证据

日期：2026-10-12（Asia/Shanghai）。Git checkpoint：`main@4ff3887d8972bf5fda351415d915c7f172417657`。本报告只记录 r2 之后的增量；原 r1/r2 报告与运行文件保持历史状态。

## 结论

**PARTIAL / EXECUTION UNKNOWN；E3 gate NOT READY。** 两次新增完整决策都是模型自主选择的 read，已完成真实来源绑定与工具回读。第三次新增请求已经 issued，但没有响应、stream 或 terminal；暂停后核查没有存活的 Python 进程，原因 UNKNOWN。该请求不重发，不再调用 provider。材料资格判断、新 clue 与 autonomous stop 尚未观察到；不能宣称模型保留了全部叙述限定，也不能宣称问答收益。

环境治理同步通知后只进行了离线核查、测试和收尾，新增 provider calls 为 0。没有重跑 r1/r2、重新检索或重读起点，没有进入 E3、创建 active view、调用 Generation、修改生产默认或语料原件，也没有 commit/push。

## 最小适配与继承

- `src/genshin_corpus/rag/navi_e2_experiment.py:41`：显式 child operating point 每轮只要一个短 JSON 动作，不再要求 actions/memory/rationale 长包装；output ceiling 为 4,096，总 token ceiling 仍为 30,000。没有猜测或加入 thinking 参数。实际返回两条短 read 决策，说明该可执行输出路径已被观察到，不能据此保证后续请求一定完成。
- `:50`：从父链 issued/terminal 重算累计 calls、usage，校验配置、checkpoint、request/wire、terminal 及历史 artifact manifest；r2 的失败请求计入六次调用上限，不沿用旧 accepted-only token 数。
- `:85`：搜索投影保留真实 hit 坐标、标题、类型与可见截断标记；read 投影发送来源绑定的 RU 原文和定位，明确省略重复 Canonical payload。完整回执仍保存，不把投影当作完整 Canonical 上下文。
- `:335`：显式 continuation 保留已完成动作、已读来源、待核实记忆、累计预算及父 checkpoint；unresolved issued request 禁止重新发送。新增 regression test 覆盖该停止路径。

继承 r2：2 次 issued calls、13,048 个实际 provider tokens、6 次 search、1 次初始化 read、1 页、3,089 个 Canonical 正文字符、44,623 个完整工具响应字符；r2 checkpoint identity 为 `26181ef2c7abc40027b41a1d1d8db643c1059f26d464ac56b03eac3e1a0c4d87`。六轮上限包括失败请求；search/read/page/char/token 上限均未重置。

r3 在第一次新增 read 后，下一次派发前触发 token projection ceiling。r4 是同一案例的技术 child，只去掉模型输入中的重复 Canonical 载体，继承 r3 状态及预算，不重放工具动作，不更换研究问题或扩大样本。

## Provider 与 run identities

实际配置：TokenMetro / `deepseek-v4.1-flash`，Chat Completions，配置 base URL `https://tokenmetro.com/v1`，OpenAI SDK `3.19.2`，adapter `p05-navi-e2-sdk-chat-0.1`；stream/include_usage=true，SDK max_retries=0，fallback=false。未更改生产默认，不记录 secret。详细参数以各 root 的 `config.json`、`rounds/*/wire-request.json` 为准。

| root（位于 `data/retrieval/`） | run identity | 结果 |
|---|---|---|
| `p05-navi-e2-real-20261012-r3` | `bb585879d86dad77531d6880d22e1d3221e4156d6b5d5846d042af72ad61c925` | 第 3 次累计请求完整，之后派发前预算停止 |
| `p05-navi-e2-real-20261012-r4` | `19fce8383b0046184530b2dbabd869249cb0334da9d2ccfee7d5149347fa35bf` | 第 4 次完整；第 5 次 issued 无 terminal，PARTIAL |

r4 收尾 checkpoint identity：`1622a4b8a2908b2d3486544157512b2039373fea07936c81922a8ab95a7aac65`。原 running checkpoint 保留在 immutable checkpoints；`interruption-closeout.json` 记录停止观察，不伪造 provider terminal。

## 模型新增动作与实际材料

后续来源、RU 和搜索词没有预先注入。下面的来源 ID 是事后审计定位；模型决策使用已存在搜索回执的 hit 坐标。

| 累计请求 | 原始模型动作 | 实际回读 | token usage |
|---|---|---|---|
| #3（r3 `rounds/02`） | `{"action":"read","hit":{"round":1,"index":1},"max_chars":6000,"neighbors":0}` | `3226:s1:u1`，《常世国龙蛇传》，collapse_panel rich_text | input 3,453 / output 427 / total 3,880；reasoning 397 属于 output |
| #4（r4 `rounds/03`） | `{"action":"read","hit":{"round":6,"index":0},"max_chars":6000,"neighbors":0}` | `3470:s3:u2`，“白夜国馆藏”，material_base_info rich_text 描述 | input 1,896 / output 449 / total 2,345；reasoning 419 属于 output |
| #5（r4 `rounds/04`） | 无可审计返回决策 | 没有新增工具动作 | UNKNOWN；派发 reservation 9,367 |

正式 RU：

- `3226:s1:u1`：`0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6`。原文包含“相传”“不知经过了多少岁月”“演义”，涉及海渊生活、太阳之子和大蛇到来。限定存在于交付文本，不能把这些叙述自动升级为客观历史。
- `3470:s3:u2`：`834db7c2ae4682bc2c2909a1a0acd56629d67bc4b20f43bba9cf6b8fe660aeac`。描述称该书记录地理、水文，并称海渊封门、海祇民集体移居和时间变化使其失去用途。**此次读到的是图书描述，不是《白夜国水文考》章节正文**。

两份 Canonical 回执均 partial=true，分别 omitted_chars=6,091 / 1,301；RU 原文完整交付，但 Canonical 结构/上下文并非完整交付。定位、Raw lineage、文本 hash、截断范围见 r3 `actions/007.json`、r4 `actions/008.json` 及最终 audit 的 `new_reads`。content_role/provenance 字段仍为 unknown；人工文本观察不等于自动来源资格准入。

事后 oracle：自主已读命中 `3226`、`3470`，没有新的 oracle 外已读来源；不声称 oracle 外新有效来源。新 clue=0，memory 沿用 `unverified_hypothesis_not_fact`，没有事实、KG 边或 semantic acceptance。模型对小说/传说/推测/史料不足的明确资格判断 NOT_OBSERVED；不能用 prompt 要求或源文本中存在限定代替模型表现证据。

## 累计账目与 terminal

| 口径 | r2 继承 | 本次增量 | 累计 / 上限 |
|---|---:|---:|---|
| issued provider calls（占决策轮预算） | 2 | 3 | 5 / 6；完整 terminal=4，accepted decisions=3 |
| search | 6 | 0 | 6 / 8 |
| read（包括指定 seed 初始化） | 1 | 2 | 3 / 12 |
| 不同已读页面 | 1 | 2 | 3 / 8 |
| Canonical 正文字符 | 3,089 | 6,265 | 9,354 / 20,000 |
| 完整工具响应字符 | 44,623 | 12,000 | 56,623 / 100,000 |
| 已知 provider total tokens | 13,048 | 6,225，另一次 UNKNOWN | 已知小计 19,273；实际总量 UNKNOWN |
| retry / fallback | 0 / 0 | 0 / 0 | 0 / 0 |

Canonical 字符按交付 units.value 字符串叶子或截断 Canonical prefix 计算，重复载体不去重；不是独立剧情正文规模。完整工具响应字符与模型 token 分开记录。r4 最后一次 reservation=5,271 input UTF-8 conservative upper bound + 4,096 output ceiling；已知 usage + unresolved reservation = 28,640，低于 30,000 派发门禁。该上界不是已测 token，也不是费用。

#5 issued 时间为 `2026-10-12T02:54:48.954181+00:00`（北京时间 10:54:48），request identity `cefdcf3829f18b4ddc5ab2287a5ea00d2b3b4ec1666a3c33f8134d01c3defe3a`。请求/输入记账已经保存，服务端执行、usage、finish_reason 及中断原因 UNKNOWN；不归因于环境清理。实际费用、币种保持 UNKNOWN，未访问账单，未用价格估算冒充实际费用。

## 验证与审查入口

- 最终当前代码：`python -m unittest tests.retrieval.test_navi_e2_experiment tests.retrieval.test_navi_exploration tests.retrieval.test_semantic_sdk_runner -v`，**96/96 PASS**（E2 23 + E1 工具 25 + SDK 48）。只跑 provider-free focused/affected tests，没有重跑真实实验。
- `python -m py_compile src/genshin_corpus/rag/navi_e2_experiment.py tests/retrieval/test_navi_e2_experiment.py` PASS；`git diff --check` PASS。
- 完整日志、命令、exit codes、源码 SHA-256：`data/retrieval/p05-navi-e2-continuation-validation-20261012-r2/{tests.log,py-compile.log,diff-check.log,validation.json}`。
- r4 `continuation-final-audit.json`：44 项机械核查通过，结果 `PASS_WITH_EXECUTION_UNKNOWN`；检查父子绑定、冻结 r1/r2 清单、request/wire/terminal/stream hash、真实 hit 与 read 对应及 RU text hash。审计不额外探索来源。
- r4 `continuation-offline-audit.py` 保存当时收尾程序；它已执行并冻结结果，包含一次性保存操作，**不要重复执行**。独立只读复核入口为 `continuation-integrity-review.py`，结果 `continuation-integrity-review.json`（151 项 PASS_WITH_EXECUTION_UNKNOWN）；子根文件清单为 `continuation-artifact-manifest.json`（冻结 58 个文件，不包括清单自身及随后生成的复核输出）。原 r1/r2 artifact manifest 未改写。

工具与短动作输出已观察到增量机械成功；qualification → clue → autonomous stop 链仍不完整，最后请求执行未知。下一步仅等待独立 review；任何进一步 live continuation 或 E3 都须另行明确决定。
