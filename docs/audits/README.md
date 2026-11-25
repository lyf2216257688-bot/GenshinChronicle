# 工程审计索引

## 2026-11-25 正式 Raw 跨日恢复

独立核验 `p01eb_full_20261124` 的发布和响应回执。新档案接受 17,329 条详情，间隔约 12 小时 44 分后续跑 386 条。本次保存的四次传输超时均有后续成功回执。代理配置、具体超时阶段和暂停根因仍待核验。实时进度、安全暂停、网络诊断和连续失败熔断列入 Collector 后续工程。[真实时间线、覆盖和后续验收](../research/incremental-raw-and-indexes-20261125.md#raw-档案和跨日恢复)

## 2026-11-24 第三批 Raw 采集可靠性

| 原审计条目 | 处置与离线验收 |
| --- | --- |
| 业务 F01 | `checkpoint.json` 报告部分进度，最终清单由已校验的冻结内容一次发布。故障注入覆盖正文、回执、接受引用、库存及最终发布边界。完成后重开零请求、零写入，清单哈希相同。 |
| 业务 F02 | HTTP 响应落盘、业务通过和覆盖资格分别记账。验证有限详情及仅发现成功、栏目匹配缺口、业务错包、详情身份错配、未知分页及发现恢复后的采样上限。Clean 与保留的 Parsed 在创建输出前验证全量资格。 |
| 架构 D01 | 续采核对版本化请求身份，共用独占运行锁。验收覆盖同根双进程、请求配置冲突、多次有效尝试的选择、接受引用损坏、侧车缺失、原始字节保留及迁移后只读回放。 |
| 架构 D02 | CLI 分别返回 `0`、`1`、`2` 表示请求范围完成、部分完成和入口拒绝，运行结果附 JSON 摘要。验证 503 有界失败、403 跨进程阻断、429 冷却未到期无法派发，以及崩溃遗留的派发状态待确认。 |

在 Windows CPython 3.14.5 运行 Collector 43 项、Clean 40 项、Parsed 27 项和 Canonical 35 项回归，合计 145 项通过。正式 `p01eb_full_20260824` 清单 SHA-256 为 `b7b519c94fd5001cc3b7128ea59bd8117d9e3a88b7a26db71431349010f26978`。两份 Clean、三份 Parsed 的原绑定一致。正式 Clean 的 `501157`、`509653` 通过记录哈希及 10,277、846 个 Raw 定位的回读。Collector 的访问限制使用本机同一操作系统用户的持久化状态。线上 403/429 行为保留原来的未知状态，相关验收使用替身传输和受控时钟。[Raw 合同](../phases/phase-01-raw-collection.md#versioned-collector-contract) · [Collector 回归](../../tests/collector/test_collector.py) · [故障注入与下游验证](../../tests/collector/test_reliability.py)

## 2026-11-23 基线与阶段收尾

[业务逻辑审计](2026-11-23-business-logic-audit.md)和[代码架构审计](2026-11-23-code-architecture-audit.md)均以 `a3af067` 为基线，保留当时的现象、复证和建议。下表记录截至 `f023800` 的实现与验证范围。最新采用状态、授权及下一步见[当前阶段](../current-phase.md)。

| 原审计条目 | 截至该检查点的处置与证据 |
| --- | --- |
| 业务 F06、F07；架构 D04、D05、D06 | `2a57c51` 在 Windows 本地聊天实现定向纠正、预算准入、持久队列领取、迟到响应隔离和 API 错误映射。验证入口为[产品接口测试](../../tests/local_chat/test_product.py)、[长会话上下文测试](../../tests/local_chat/test_context.py)和[前端可靠性测试](../../frontend/tests/reliability.test.ts)。 |
| 业务 F08；架构 D03 | `2a57c51` 将兼容 G5 产品批次的本地查询分为尝试与封存发布，显式继续可重做尚未发布的本地计算。已封存结果按原校验重放。旧 G4 LIVE 的未封存恢复仍按原合同处理。见[产品恢复合同](../phases/local-chat-v1.md#接口与恢复约定)、[G5 恢复回归](../../tests/generation/test_clean_research.py)和 [G4 有限运行合同](../phases/clean-generation-v1.md#g4-finite-remote-fixed-chain)。 |
| 业务 F03、F04、F05、F09 | `f023800` 的 `gc-g5-official-5` 覆盖父级定位、已排序候选翻页、引用后的父级补存以及绑定缺失与失败的区分。见[离线复验与真实配对](../research/clean-generation-g5-evidence-evaluation-20261123.md)和[续轮专项审计](../research/clean-generation-g5-targeted-audit-20261123.md)。工程复验、真实交付、逐结论支持与默认采用各有独立门槛。 |
| 业务 F01、F02；架构 D01、D02 | 在该检查点，Raw 续跑的证据不可变性、清单范围及完成语义待处理。原触发条件和恢复边界见[业务审计](2026-11-23-business-logic-audit.md#确定的问题)与[架构审计](2026-11-23-code-architecture-audit.md#4-已确认的缺陷)。 |
| 架构 D07 | `f023800` 将 `chunking/reader.py`、Clean 回读、传输、候选目录和答案绑定代码纳入共用的 G5 代码身份，适用于两种 G5 策略。产品续用和派发均核验此身份。旧 G4 LIVE 使用自身的文件清单，其中仍缺自然父级读取器。见 [G5 代码身份](../../src/genshin_corpus/rag/clean_research.py)、[产品批次](../../src/genshin_corpus/local_chat/batches.py)和 [G4 LIVE](../../src/genshin_corpus/rag/clean_live.py)。 |
| 业务 F10 | G7 的 Q001、Q002 逐题导出仍保留旧 `source_store`。回读以已对账的[总索引](../../data/generation/g7-70q-baseline-20261117/index.jsonl)和[恢复对账记录](../../data/generation/g7-70q-baseline-20261117/recovery-index-reconciliation.json)定位原始来源。 |

架构审计 R01–R07、可选优化和业务审计中的建议沿原报告追溯。`2a57c51`、`f023800` 和对应合同保留两批实现与历史验收；[研究索引](../research/README.md)可反查 G0–G7、Sparse 对照、G5 第二批封存排名、盲评、账本和离线复现。`data/` 下的运行根与回执仍保存在本机，其中 Git 忽略的资产需要随原数据根单独保管。数据归属见[数据目录](../../data/README.md)。
