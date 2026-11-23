# 工程审计索引

## 2026-11-23 基线与阶段收尾

[业务逻辑审计](2026-11-23-business-logic-audit.md)和[代码架构审计](2026-11-23-code-architecture-audit.md)均以 `a3af067` 为基线，保留当时的现象、复证和建议。下表记录截至 `f023800` 的实现与验证范围。最新采用状态、授权及下一步见[当前阶段](../current-phase.md)。

| 原审计条目 | 截至该检查点的处置与证据 |
| --- | --- |
| 业务 F06、F07；架构 D04、D05、D06 | `2a57c51` 在 Windows 本地聊天实现定向纠正、预算准入、持久队列领取、迟到响应隔离和 API 错误映射。验证入口为[产品接口测试](../../tests/local_chat/test_product.py)、[长会话上下文测试](../../tests/local_chat/test_context.py)和[前端可靠性测试](../../frontend/tests/reliability.test.ts)。 |
| 业务 F08；架构 D03 | `2a57c51` 将兼容 G5 产品批次的本地查询分为尝试与封存发布，显式继续可重做尚未发布的本地计算。已封存结果按原校验重放。旧 G4 LIVE 的未封存恢复仍按原合同处理。见[产品恢复合同](../phases/local-chat-v1.md#接口与恢复约定)、[G5 恢复回归](../../tests/generation/test_clean_research.py)和 [G4 有限运行合同](../phases/clean-generation-v1.md#g4-finite-remote-fixed-chain)。 |
| 业务 F03、F04、F05、F09 | `f023800` 的 `gc-g5-official-5` 覆盖父级定位、已排序候选翻页、引用后的父级补存以及绑定缺失与失败的区分。见[离线复验与真实配对](../research/clean-generation-g5-evidence-evaluation-20261123.md)和[续轮专项审计](../research/clean-generation-g5-targeted-audit-20261123.md)。工程复验、真实交付、逐结论支持与默认采用各有独立门槛。 |
| 业务 F01、F02；架构 D01、D02 | Raw 续跑的证据不可变性、清单范围及完成语义仍待处理。原触发条件和恢复边界见[业务审计](2026-11-23-business-logic-audit.md#确定的问题)与[架构审计](2026-11-23-code-architecture-audit.md#4-已确认的缺陷)。 |
| 架构 D07 | `f023800` 将 `chunking/reader.py`、Clean 回读、传输、候选目录和答案绑定代码纳入共用的 G5 代码身份，适用于两种 G5 策略。产品续用和派发均核验此身份。旧 G4 LIVE 使用自身的文件清单，其中仍缺自然父级读取器。见 [G5 代码身份](../../src/genshin_corpus/rag/clean_research.py)、[产品批次](../../src/genshin_corpus/local_chat/batches.py)和 [G4 LIVE](../../src/genshin_corpus/rag/clean_live.py)。 |
| 业务 F10 | G7 的 Q001、Q002 逐题导出仍保留旧 `source_store`。回读以已对账的[总索引](../../data/generation/g7-70q-baseline-20261117/index.jsonl)和[恢复对账记录](../../data/generation/g7-70q-baseline-20261117/recovery-index-reconciliation.json)定位原始来源。 |

架构审计 R01–R07、可选优化和业务审计中的建议沿原报告追溯。`2a57c51`、`f023800` 和对应合同保留两批实现与历史验收；[研究索引](../research/README.md)可反查 G0–G7、Sparse 对照、G5 第二批封存排名、盲评、账本和离线复现。`data/` 下的运行根与回执仍保存在本机，其中 Git 忽略的资产需要随原数据根单独保管。数据归属见[数据目录](../../data/README.md)。
