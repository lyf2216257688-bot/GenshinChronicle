# Phase05 F2｜user-input 统一调用方案准备与旧 Prompt 实测（2026-10-28）

## 当前结论

**F2 两臂新调用形态已完成离线准备；旧版 Prompt 实际连线验证尚未成功发送。**
本轮不重建旧 G1 内容基线、不运行 F2 正式48槽、不生产/发布 Navi。

## 旧版 Prompt 实际尝试

- 资料：冻结生成样本 `6c2d16a80dac01dc3f8c`，A《艾拉·马斯克【丘丘人语研究学者】》、B《丘丘人诗歌选》，与此前已成功的 rc1 实测同一资料对。
- 调用形态：Responses `stream=true`、极智 `deepseek-v4.1-flash`，旧版冻结 Prompt 与冻结资料通过 `input=prompt+"\\n\\n"+payload` 合并发送，不使用顶层 `instructions` 或服务端 JSON Schema。
- `max_output_tokens=30000`，目标是下一版两臂统一 cap；该上限**没有获得这次实际成功验证**。
- 只进行一次独立尝试，结果 **`NOT_SENT`**：`connection.connect_tcp.complete` 后 `connection.start_tls.failed`，底层 `ssl.SSLEOFError`，HTTP status 为 null。**请求未发送、没有模型 JSON、模型 usage UNKNOWN；不能算旧 Prompt 内容失败或成功。**
- 没有重试；原样保留请求、trace、状态；诊断位置：`data/retrieval/p05-navi-f2-legacy-user-smoke-20261028-r1/`。

## 新 F2 统一方案（只准备，不执行）

冻结并生成 `data/retrieval/p05-navi-f2-user-input-prepared-20261028-r1/prepared-manifest.json`，包含以下检查：

1. 原 F1 和原 F2 preflight 完整性验证，保留 24 对资料、48 个实验槽位，维持原有顺序及两臂/生成与复核比例（16+16生成、8+8复核）。
2. 每对旧 Prompt / rc1 共用同一源资料哈希、生成或复核模式与原始候选哈希；每条请求有独立指纹。
3. 两臂使用相同字段：`model`、`stream=true`、`max_output_tokens=30000`、`input`。唯一按臂变化的是冻结 Prompt 和按该臂准备的冻结输入包装；没有 `instructions`、`text.format` 或其他新增生成参数。
4. 旧 Prompt 输出由冻结原生校验器判定；rc1 输出由冻结 v0.2 JSON Schema 加证据 ID 绑定校验。都只标记程序机械检查结果，不代表人工语义认可。
5. reasoning ID 若不一致，按 `navi_f2_content_recovery.py` 保存完整异常，独立核验 message；没有消息完整性、用量或协议可解释条件则拒绝标记通过。
6. 阶段状态 **`PREPARED_NOT_AUTHORIZED`**，正式模型调用数为0，`STOP.json` 禁止自动续跑。与历史 `p05-navi-f2-experiment-20261027-r1` 完全隔离，不修改其首条失败和47条未发送状态。

## 软件与验证

- `src/genshin_corpus/retrieval/navi_f2_legacy_user_smoke.py`：独立旧版单次诊断入口，目录存在即拒绝再次发送。
- `src/genshin_corpus/retrieval/navi_f2_content_recovery.py`：新旧两臂通用、纯离线请求渲染与消息验证适配器。
- `src/genshin_corpus/retrieval/navi_f2_user_preflight.py`：两臂统一配置、48槽指纹、资料公平性检查与准备清单（没有模型调用路径）。
- `tests/retrieval/test_navi_f2_user_preflight.py`：48槽、24对资料、旧失败保留和旧 Prompt TLS `NOT_SENT` 回归检查。
- 本轮相关测试 **34/34 PASS**，`git diff --check` 无异常；未提交 Git，不触及 Phase04 和 Embedding 工作。

## 仍需完成

**唯一当前外部阻碍：旧版 Prompt 的真实响应尚未取得。** 网络/TLS 恢复后，另立单次诊断尝试（不覆盖本次记录），验证旧版输出经其原生校验器 PASS。若通过，再决定是否开放新版本 F2 执行程序；本轮准备清单本身不等于可运行的48槽正式批处理。

旧版 Prompt 若在 user-input 路线生成内容失败，应针对实际失败做最小修正；不要预设是提示词失败或协议能力不同，也不要把机器校验等同于剧情关系成立。

## 追加：网络可达后独立旧版诊断（r2）

在非模型 HEAD 检查能完成 TLS 后，另外建立 `p05-navi-f2-legacy-user-smoke-20261028-r2/`，只发了一次旧版 user-input 请求，未覆盖 r1：

- HTTPS 请求已发送，HTTP 200、Content-Type 为 SSE。
- 原始流及 SDK **只有两项**：`response.created` 和 `response.completed`；最终包的 `status=completed`，但只有一条**无文本** message，**没有 usage**，也没有正常 output item/text delta 事件。
- 原始诊断记录状态保持 `UNKNOWN`、usage `UNKNOWN`；另存 `offline-observation.json`，离线分类为 `PROVIDER_ENVELOPE_INVALID_NO_CONTENT_NO_USAGE`。
- 这不是可用的旧版 Prompt 验证结果，更不能因为 HTTP 200 判成功。实际费用未知，不重发本次请求。

因此，**目前只有 rc1 的两次真实 Navi 内容机械 PASS；旧版 Prompt 的同包装输出尚未成功取得。** 继续阻碍是服务端响应结构的稳定性，而非两臂调用参数不一致。新 F2 的48槽只是预备并冻结，无正式调用。
