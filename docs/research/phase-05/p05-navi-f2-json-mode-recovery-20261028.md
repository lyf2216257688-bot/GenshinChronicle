# Phase05 F2｜Responses user 消息 + JSON Object 双臂恢复验证

日期：2026-10-28。状态：**单对资料的旧/新版 Prompt 都已真实机械 PASS；F2 正式48槽仍未执行。**

## 解决了什么

原方案的两次旧版 Prompt 在 `input` 字符串、`text.format` 缺省时出现 HTTP 200 的空 completed：只有 `response.created` / `response.completed` 两个事件，没有正文也没有 usage。记录分别为：

- `p05-navi-f2-legacy-user-smoke-20261028-r2`，上限30000：空响应。
- `p05-navi-f2-legacy-user-smoke-20261028-r3`，上限16000：同样空响应。**说明只降低输出上限不能消除问题。**
- `r1` TLS 握手失败，属于 `NOT_SENT`，未进入模型。

之后改变两项兼容包装（保留原冻结 Prompt 与输入资料本身）：

```json
{
  "model": "deepseek-v4.1-flash",
  "stream": true,
  "max_output_tokens": 16000,
  "input": [{"role": "user", "content": "<冻结Prompt>\n\n<冻结资料>"}],
  "text": {"format": {"type": "json_object"}}
}
```

同一冻结资料对 `6c2d16a80dac01dc3f8c`（艾拉·马斯克 ↔ 丘丘人诗歌选），旧版和 rc1 两臂各发一次，不更改各自 Prompt 正文、F1 双端证据及本地校验契约。

| 实测项目 | 旧版 legacy | 新版 rc1 |
|---|---|---|
| 请求/结果 | 1次，HTTP200 completed | 1次，HTTP200 completed |
| JSON与原生合同 | 机械 PASS | v0.2 Schema+证据ID机械 PASS |
| raw SSE vs SDK、整流结构 | PASS | PASS |
| reasoning ID 冲突 | 0 | 0 |
| 输入 Token | 2401 | 5028 |
| 输出 Token | 8390 | 15589 |
| 推理 Token | 7821 | 14249 |
| 当前结果语义审查 | 未做人工验收 | 未做人工验收 |
| 原始记录 | `data/retrieval/p05-navi-f2-legacy-json-mode-20261028-r1/` | `data/retrieval/p05-navi-f2-rc1-json-mode-20261028-r1/` |

**注意：** 这两项参数一起改变，不能从单次成功证明是其中哪一个单独解决了空响应，更不能声称极智内部缺陷已修复。它只证明了一种可工作的请求形态。同一资料对各成功一次，尚不代表全面稳定。

## 为正式 F2 做好的准备

新增纯离线渲染器 `render_user_json_mode_wire`，对两臂采用完全相同的模型、stream、input 结构、JSON Object 参数和 16000 cap，只保留各自冻结的 Prompt 和证据输入差异。

通过 F1/F2 原封存检查，生成新的隔离清单：

`data/retrieval/p05-navi-f2-json-mode-prepared-20261028-r1/prepared-manifest.json`

该清单核对24对资料、48槽位（生成16+16、复核8+8）、双端证据哈希、原始候选哈希和独立请求指纹；状态 **PREPARED_NOT_AUTHORIZED**，`STOP.json` 明确无模型调用、禁止自动恢复。

**重要风险：** rc1 的输出 15589/16000，余量只有411 Token。下一版如正式运行，应先确认更稳妥的输出上限并对截断请求做可审计的分类；不要直接把16k视为全样本适用。更高的上限在此新包装下仍未经实测。

这次没有更改既有的 `navi_f2_execute.py` 批量运行器，也没有改变旧 F2 的首条失败记录和47条未发送状态；不能直接启动旧批量运行器并误以为它会使用新包装。

## 文件及回归

- `src/genshin_corpus/retrieval/navi_f2_content_recovery.py`：新增统一 JSON Object 请求渲染函数，保留原消息验证规则。
- `src/genshin_corpus/retrieval/navi_f2_legacy_json_mode_probe.py`：旧版单次已执行诊断。
- `src/genshin_corpus/retrieval/navi_f2_rc1_json_mode_probe.py`：rc1 单次已执行诊断。
- `src/genshin_corpus/retrieval/navi_f2_json_mode_preflight.py`：仅离线生成48槽的新配置，不发送请求。
- `tests/retrieval/test_navi_f2_json_mode_preflight.py`：真实两臂记录、48槽指纹、风险和原F2状态回归。
- 此轮相关离线测试 **39/39 PASS**，`git diff --check` 通过。

不发布 Navi，不更改 Phase04/Embedding/KG，不自动重试 UNKNOWN，不提交或推送 Git。实际账单费用 UNKNOWN。

**实务结论：** F2 旧/新版 Prompt 已找到共同可用的 Responses 输入包装；下一阶段重点是输出额度余量与正式执行器接入，而非继续大量重复短格式测试。
