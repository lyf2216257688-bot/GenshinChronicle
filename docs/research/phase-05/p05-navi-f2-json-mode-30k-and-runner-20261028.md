# Phase05 F2｜30k 输出额度验证与独立执行器准备（2026-10-28）

**当时状态：30k 新版 Prompt 实际单次机械 PASS；48槽独立执行器已完成离线预检，当时没有运行正式 F2，也没有授权自动批量发送。** 后续用户授权的新批次在第2槽Schema失败后停止，详见[正式执行部分结果](p05-navi-f2-formal-30k-partial-20261028.md)。

## 1. 30,000 Token 真实验证

冻结资料：`6c2d16a80dac01dc3f8c-rc1`，艾拉·马斯克 ↔ 丘丘人诗歌选。沿用此前两臂真实通过的极智 `deepseek-v4.1-flash` / Responses `stream=true` / 单一 `user` 消息对象 / `text.format={"type":"json_object"}`。**唯一改变：`max_output_tokens=16000` → `30000`**。没有修改原始 Prompt、双端资料及语义标准；没有重试。

- HTTP 200，Responses `completed`。
- 原始 SSE / SDK 对照 PASS，消息及整流事件核验 PASS，reasoning ID 冲突为0。
- 完整 JSON、v0.2 rc1 Schema、A/B 双端证据 ID 绑定：程序机械 PASS。
- 用量：输入 5047 Token，输出 15617 Token（其中 reasoning 13846），总计 20664；距离30k上限尚有14383 Token。
- 实际账单费用仍 `UNKNOWN`，语义质量 `NOT_VALIDATED`，人工认可 `NOT_REVIEWED`，Navi 发布 `INACTIVE`。
- 原始请求、响应、SDK事件、终态：`data/retrieval/p05-navi-f2-rc1-json-mode-30k-20261028-r1/`。独立诊断标记 `formal_f2_slots=0`。

该实测证明30k上限**在这一份资料、这一种请求包装中可正常使用**；没有证明全量资料都不会截断，也未测试旧版 Prompt 在30k上限的真实执行（旧版在相同包装、16k上限已单次机械 PASS）。

## 2. 新版独立 F2 准备清单

新位置：`data/retrieval/p05-navi-f2-json-mode-prepared-30k-20261028-r1/prepared-manifest.json`。

- 新版本 `F2-Responses-user-item-json-object-30k-v2`。
- 冻结 24 对资料、48 条请求（旧/新生成各16、旧/新复核各8）。
- 两臂的模型、`stream`、`max_output_tokens=30000`、user消息包装、`text.format=json_object`完全一致。
- 按臂保留原始冻结 Prompt、材料证据哈希、请求指纹及专用校验器。
- `state=PREPARED_NOT_AUTHORIZED`；`formal_model_requests_sent=0`。旧 F2 首槽失败记录与47条未发送全部保留。

## 3. 独立新 F2 执行入口

`src/genshin_corpus/retrieval/navi_f2_json_mode_execute_v2.py`

- 默认只做离线预检、打印48槽范围及配置，不发送模型请求。
- 后续若另获明确批量执行授权，才可在独立新目录 `data/retrieval/p05-navi-f2-json-mode-execution-20261028-r1/` 使用双重显式开关发起，绝不会复用旧 F2 路径。
- 必须整批48槽有明确授权；禁止静默重试、覆盖、续跑既有请求。失败、不完整、消息身份不可靠时立即停下，保留原始请求/响应/用量，禁止将 HTTP200 单独视为成功。
- 新旧两臂分别执行冻结原生校验器或 v0.2 Schema/证据绑定校验。reasoning ID 的独立异常会完整记录，并不冒充整流协议完全正常；只在最终 message 可独立严格核验时保留机械候选。
- 机械 PASS **不等于语义验收或Navi发布**；输出与正式版本、配对证据和旧历史记录分离。

> 注意：本轮仅验证了离线路径和历史响应重放。**新执行器的真实48槽调度路径尚未经过实际端到端批量运行，不能写成“正式 F2 已完成”。**

## 4. 自检结果

- 新代码：`navi_f2_json_mode_30k_preflight.py`、`navi_f2_json_mode_execute_v2.py`。
- 兼容层：`navi_f2_content_recovery.py` 增加完整的 user消息+JSON对象请求核验。
- 新离线测试：`tests/retrieval/test_navi_f2_json_mode_execute_v2.py`。包括48槽指纹核验、真实旧版16k/新版16k/新版30k原始流回放、禁止无授权执行及拒绝部分正式批次。
- 相关回归 **44/44 PASS**；`git diff --check` 无异常。
- 本轮只有 **1 次新付费模型请求**（30k新版验证），0次重试，正式F2发送0条。未动并行Embedding、Phase04或KG；未提交/推送 Git。

## 5. 仍需决策

如果下一轮确定要正式运行新 F2，需另外明确授权48槽批量；运行时如首槽出现不确定状态，应保留UNKNOWN并停下，不直接重试。30k输出额度不是无条件容量保证。实验结果只供后续语义审阅与对照分析，不能自动发布Navi。
