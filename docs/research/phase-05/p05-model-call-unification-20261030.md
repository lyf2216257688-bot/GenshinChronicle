# 统一模型调用、重试与恢复整改

本轮仅完成基础设施和离线验证。**新增收费请求 0；未重发盲测 T01，未生成新 Card、Link
或 Embedding；没有 commit/push。** 原盲测结果、UNKNOWN、匿名评审和请求账本不改写。

## 调查结论

已有可复用底座并不缺原件：`navi_f2_sdk_transport` 提供 raw-first tee、解压和保守的
TCP/TLS 未发送证明；v0.3 runner 有跨运行指纹、逐次尝试与原始流验证；typed adapter 有
来源合同和用量/协议/内容分离；Metro Chat 关闭 SDK/HTTP 自动重试并保存最终文本与 reasoning。
E2/semantic SDK 另有 Retry-After、429 和研究重试政策。

主要问题是这些政策不共享：Card build、盲测及流水线分别把失败/UNKNOWN 当成整批停止
条件，盲测还曾需要实验专用许可才能继续其他四个指纹。旧研究中的重试范围也不同，
不能直接把最宽政策复制为新的默认值。原始证据保存、来源验收和语义标准继续复用。

## 实现

新增 `genshin_corpus.model_calls`，复用并提取原有 IO 到 `evidence_io`；离线包 common
兼容导出不改变 JSON 哈希、文件内容或 seal 规则。没有加入数据库、队列或依赖。

- 一个逻辑请求对应不变指纹和原始 plan。`call.json` 绑定共享代码、请求、渠道、政策
  和可选原件身份。每次尝试独立保存并封存，重试决策和派发 reservation 独立记录。
- SDK/HTTP 层仍零重试；公共层分类超时/断线、429、5xx、非 JSON、截断、合同错误，
  安排有限退避并保留 Retry-After。默认仅肯定证明未发送的 TCP/TLS 失败可重试。
- 524 和普通 429/5xx 不被冒充“未执行”。Metro 服务端去重 **NOT_VERIFIED**。
  若显式授权有限 `unknown_retries` 和重复成本风险，则可再次尝试；其余情况隔离任务。
  通用 seam 支持经路线合同验证的稳定 idempotency header，但 Metro 适配器未启用它。
- 非 JSON 可在另行授权的 `format_retries` 内重新生成；来源/Schema 失败、输出截断
  仍拒绝，不补造字段或改写引文。Card 原有确定性转换与冻结 Link 语义验收不变。
- 请求/重试/CLI 启动、Token 已知小计与 UNKNOWN、实际或未知费用分别保存。
  成功重试不清除之前 UNKNOWN；完整总量仍 UNKNOWN。provider 原生 usage 保留。
- 共享 Batch 在进程锁内预占每次尝试，约束全 campaign、当前执行窗口及 Token 预算。
  未知用量消耗保守 allowance，不能按零预算继续。已知失败用量同样计入。
  allowance 不等于提供商计费或硬 Token 上限；超出后可阻止后续派发。
- 单一来源/内容失败不会默认停其他独立任务；同渠道连续 3 次渠道故障进入 60 秒
  冷却，其他渠道不受影响。退避默认 1 秒递增、最高 30 秒；这些数值均可版本化配置。
- 完成尝试在恢复时复用；中断分片/部分响应保持 UNKNOWN，默认不重发。
  原请求的后续恢复使用新根、精确原 plan、原始文件哈希和原账本身份，追加恢复记录，
  不改指纹、不移除 reservation、不把未完成原件重新封装为历史成功。
- 新 Card/Navi/盲测准备快照同时保存共享模块字节及哈希。代码/政策漂移不会静默续跑。
  已耗尽的原盲测不迁移到新授权，仍可只读核验；本轮未追加它的运行版本或修改原件。

## 接入范围

| 调用任务 | 当前状态 |
|---|---|
| Metro Card Chat | 默认共享调用层；Prompt、Schema、模型和 wire 不变 |
| typed Responses Card、Link 生成、独立审核、辅助诊断 | 默认共享调用层；严格任务验收不变 |
| Navi 离线 pipeline，包括富文本 | 默认使用共享 typed 路径；旧原生 runner 保留为显式兼容路径 |
| Card build / resume | 共享预算、单项隔离、继续其他资料；真实运行须有 prepared 总请求上限 |
| 新 DeepSeek 盲测准备 | 共享预算和独立任务续跑；历史实验专用许可仅保留用于原档案 |
| Antigravity 盲测外层 | 共享启动 reservation、恢复、原件、用量、渠道冷却；内部机制不冒充受控 |
| 原生 v0.3 / F2、旧 semantic SDK、R1 等历史实验、Navi E2 E2SDK | **仍旧实现**，未改冻结历史政策 |
| Phase04 Generation、远程 Embedding 及旧 urllib/SDK 消费者 | **仍旧实现**，生产不变 |

后续新建或实质改造的模型 API 任务须复用共享组件；该要求已加入 AGENTS。
未宣称所有旧脚本已迁移。接入矩阵由 `docs/model-interfaces.md` 维护。

Antigravity 的一次 CLI 启动可能产生多次模型 HTTP 请求、内部重试、工具和系统上下文。
外层只能可靠预算自己的启动次数；内部请求数/去重/未暴露 reasoning/实际费用继续 UNKNOWN。
CLI 已完整结束但 Card 格式/引文失败，归为内容失败，不因此把模型调用当成网络未完成。

## 实际 HTTP524 案例的离线验证

原件：`data/retrieval/p05-card-blind-prep-20261030-r1/runs/deepseek/T01/request/`。
读取并校验原 plan、原始 POST、HTTP524 错误体、trace、usage UNKNOWN 与原账本。

1. 无风险重试授权：公共恢复器不调用发送回调；原状态和总用量保持 UNKNOWN。
2. 只在独立的本地验证根/账本副本中，授予 **本地回放** 一次尝试；回调重放原 524，
   不接触网络，不制造新的模型答案。只执行一次本地回放，原始 UNKNOWN 保持不变。
3. 再恢复同一个本地验证根：零新增回放/发送。已有 T02 有效结果可独立只读读取。
4. 另外用 MockTransport 证明 524→合法 Card 的有限重试：两次实际序列化 body 相同，
   后一次经过原 Card Schema/来源验收；此前未知用量仍未知。这是协议/调度测试，
   **不是新的真实 Card，也不证明模型语义效果**。

原 524 是否在上游生成/计费，Metro 是否提供可验证 idempotency，仍 UNKNOWN。
未来真实恢复必须另外授权；本轮没有借本地回放许可发起任何收费调用。

Windows 最终回放另外发现恢复目录重复嵌套完整哈希导致长路径超限；新记录改为
`recoveries/0001/` 与短渠道事件文件定位，完整请求指纹、尝试路径与原件哈希仍在记录中校验。失败的本地回放
保留，不覆盖也不重用为成功结果。

## 验证和产物

147 项针对性回归 PASS：公共状态/风险预算、429/5xx、Retry-After、JSON 与来源失败
分离、独立任务、渠道冷却、预算前后中断、封存后恢复、重复提交/损坏/代码漂移、
Metro 实际序列化与 raw-first、完整 Mock Card→Link→审核链，以及冻结 v0.3/F2 和接口目录。
测试中的 Mock/本地回放均不产生真实模型调用。

工程证据根：`data/retrieval/p05-model-call-unification-20261030-r1/`。
`tests-accepted.log` 是最终回归；`actual-v/actual-524-verification.json`
记录真实原件的离线恢复结果；`protected-final.json` 记录开始前 448 个原盲测及 Metro
账本文件的哈希一致性。原件目录与历史匿名结果不覆盖。`git diff --check` PASS。
忽略目录留在本机仍不等于独立备份。

## 使用及边界

```powershell
$env:PYTHONPATH = 'src'
python -m genshin_corpus.model_calls inspect --request data/retrieval/p05-card-blind-prep-20261030-r1/runs/deepseek/T01/request
```

以上只读检查不发送请求。新 Card/盲测 CLI 可用 `--call-policy <JSON文件>`；普通任务
不再自建网络重试。API 参数、Policy 默认值和精确原件恢复接口见 `docs/model-interfaces.md`。
收费执行仍须原有显式授权、准确准备哈希和有限 campaign 预算。

剩余限制：旧消费端尚未迁移；Metro 去重和真实 524 后重试没有线上验证；费用无发票
证据；外部 Agent 的内部调用不能由外层完整控制。公共层刻意采用串行实现，未验证全库
并发吞吐。工程恢复可靠性不等于 Card/Link 的语义质量或人工审核通过。
