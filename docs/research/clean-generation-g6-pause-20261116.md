# G6 暂停断点

2026-11-16（Asia/Tokyo）。用户要求“先暂停，明天再继续”。没有安排自动运行。
当前状态以 [current-phase](../current-phase.md) 为准；本文保存本轮未完成工作断点。

**后续关系：** 用户已于2026-11-17显式继续；本暂停点的4次账本是历史快照。
接入、有限问答与后续 UNKNOWN 处置见 [G6 实施报告](clean-generation-g6-implementation-live-20261117.md)，
不要按本文旧下一步重新派发或重置预算。

## 已完成

- 主仓库基线 `main@ea12935`，开始时工作区干净；本轮改动保留在工作区，未提交或推送。
- V2 查询、固定 S2 配置、StreamingSparseSession/SparseEncoder、BGE 和研究控制器接线已实现。
  S0 默认、冻结核心/索引/融合参数保持不变；没有动态检索路由或上游重建。
-9项新 G6 测试通过；131项实际执行的 Generation 回归（含这9项）、27项共享调用/接口检查、
  5项 UI 检查通过。首次扩大测试调用有两个加载错误：隔离 Generation 环境没有旧 SDK，
  以及接口测试模块名写错；已用既有 SDK 环境/正确模块补测，没有安装依赖。
- 70题仅复用历史结果清点。按“未出现在 Dense/BM25F 固定窗口”的定义，Sparse 独有命中
  2,457题内身份次，其中665进入 S2 的100候选；S2 相对 S0 新增1,250、失去1,213候选身份次。
  这些均不是语义质量指标。
- 8题离线 S0/S2 对照：Q003/Q007/Q028/Q041/Q044/Q045/Q046/Q050。
  共复用1,454个精确历史 BGE 配对，仅补评分146个缺失配对。
- 真正本地 S2 查询 Q046 完成三通道精确检索核对、100候选 BGE 评分与 Assembly，
  全部候选顺序和 BGE 顺序与回放完全一致；所有 GPU worker 确认退出。

## 当前研究发现（尚无完整 Live 配对结论）

- Q046：Sparse 独有 row63311 含“卡莉露：我给予你的赠礼，同样会陪伴着你长大”，
  并有重云关于调酒体质/精灵祝福的原文推想。召回100→BGE4，进入离线诊断 Packet。
  S0 另有 row63315 的卡莉露/芬奇对话；不得把新增赠礼直接联系夸大为 S0 完全没有人物名。
- Q050：Sparse 独有 row16380 的《香韵奏者》明确写“仁善的大调律师优恩尼娅”，
  召回98→BGE27，未读入前六候选诊断，在全回读诊断中 budget_excluded。
  有用候选未到生成端，不能用“新增 Chunk”冒充实际回答改善。
- Q007：一斗“生日6月1日”召回1→14，但 BGE 两边都排1并入包；历史召回退化被下游修复。
- Q028：新增长版 NPC 文本重复已有“阿布拉克设计大日御舆”，扩大身份/上下文而未补新答案。
- 部分 S2 高排位长片段会挤占预算。必须逐条区分事实损失、重复封装替换和旁支片段损失。

离线 initial6/all_read 诊断统一用196,608预算、4,096输出保留；这**不是** G5 控制器的
初始 Packet 策略（真实控制器初始最多60,000保守预算，最终才用完整预算）。
all_read100是独立离线诊断，不改变 G5 的48读取上限。实际 Live Packet 需单独审阅。

## 付费断点与严格保留项

同一 G6 permit：最多20次、每 turn3次、动态角色、零自动重试，2小时到期；当前批次已关闭。

| 案例 | 计入请求 | 状态 |
|---|---:|---|
| Q007/S0 |3 | completed/sufficient；回答荒泷一斗，已保存原答案 |
| Q007/S2 |1 | URLError，UNKNOWN；无响应正文、无 usage、无最终答案 |
| 合计 |4/20 | 16次未使用；UNKNOWN 的潜在消耗/费用仍未知 |

已知三次合计45,874 reported tokens。尚未完成费用汇总；总 Token/实扣不能因 UNKNOWN 当作已知。
Q007/S0 自主补查了派蒙生日前提，再 select+finish，最后综合；不能将这一额外动作归因于 Sparse。

原 UNKNOWN operation：`88af89dfe0e164f4505cfbb9e2cf43d8b55552ee908b9634bfdf71753790d29c`。
其 attempt 保留 request.body.bin、request-metadata.json、空 decoded、trace、result 和 seal；
没有 HTTP 状态或响应，5.014481秒后 URLError。现有记录没有具体网络错误原因，
**不足以证明请求未发送**。没有重发、改判、替换操作、恢复调用或新建额度根。

已有 `rag/clean_research_retry.py` 是保持原操作/指纹/Batch/UNKNOWN 的单次显式恢复入口，
不会重开已关闭 campaign。恢复需要符合其显式授权和重复计费风险要求；剩余额度本身
不解决这个门禁。用户当前只要求暂停，因此本轮没有追加询问或执行恢复。

## 文件和继续顺序

- 工程源码/配置/测试/文档在当前 Git diff 中；基线 hash：`.local/g6-20261116/baseline.json`。
- 离线完整产物：`data/generation/g6-offline-20261116/`（已封存）。
- 原文/Packet 差异：`data/generation/g6-evidence-review-20261116/cases.md` 和 differences.json（已封存）。
- 真实本地链：`data/generation/g6-local-s2-20261116/`（已封存），阶段耗时154.17秒，不含初始 reader 加载。
- Live：`data/generation/g6-live-20261116/`，permit、code-snapshot、四次调用、关闭记录、两个 case-report。
- 工作脚本及日志：`.local/g6-20261116/`。`live.py run` 拒绝已存在的 case，不应直接重复执行。
  `audit.py` 是尚未运行的草稿，目前假定所有调用成功；恢复工作须先适配 UNKNOWN，不能直接宣称 PASS。
- 密钥按用户要求保存到 `.local/deepseek/credential.clixml`，Windows 当前用户 DPAPI 加密。
  `.local/deepseek/load-key.ps1` 可在当前 PowerShell 点加载到 DEEPSEEK_API_KEY；文件已确认被 Git 忽略。

显式继续后：先核对工作区和此断点，处理 UNKNOWN/原授权剩余16次的合法恢复路径；
再完成 Q007/S2、Q046 和 Q050 的有限配对，独立审阅真实 Packet/原答案，核算全部尝试与费用，
最后完成正式 G6 报告与采用建议。工程通过与回答质量仍分别判断，最终采用由用户决定。
