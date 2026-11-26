# GC 专业证据 MCP 离线合同 V1

采用方向见 [ADR-0002](../decisions/adr-0002-host-neutral-evidence-engine.md)，当前工作和后续授权见[当前阶段](../current-phase.md)。本合同的运行入口重放已登记的排序结果，用原语料重新核读和组包。支持范围由 `config/mcp-offline-v1.json` 中的题目及补查目录确定。

## P0 对照与绑定

- 普通问题取 G7 Q001 的首次原始查询，复杂问题取 Q068 首查和已有的 `query-1`。两题使用历史 `gc-g5-official-4`、S0、`bge_structural` 和当时的首包上下文与预算。金标准是封存的排名和首包传递结果。问答语义质量另评。
- 固定目录记录选择器、生成配置、统一输入清单、各题排名及原首包来源工件的 SHA-256。首次使用时核验 Dense/BM25F 的清单和输入包、Chunk/Clean 清单及排名的查询与选择器身份。两种候选粒度保持正式排序。冻结的候选源行来自经过哈希校验的完整排名。回读时按该行的范围与哈希读取真实 Chunk、Parent、图和 Clean，沿既有 CleanRef 验证逐字原文并保留 Raw 血缘。此路径没有重新编码、扫描整个输入表或复验 Raw 原字节。
- 初始组包按原顺序回读前六项，对整个 Packet 做相等断言，包含输入组摘要、证据、未入包处置、引用、上下文和预算。后续动作使用独立 MCP 研究上下文、原首包预算和已有确定性组包器。每组保持内部顺序。模型的实际上下文上限、宿主指令和宿主裁剪应在宿主评测中单独记录。
- 每项研究固定目录摘要与读回、组包、协议实现身份。证据句柄和 Packet 地址绑定研究及实现。引用以 Packet 和包内 E 编号定位，核对结果可以查到阅读句柄、来源条目、Clean 观测、receipt 和 fragment。宿主提交的答案只供格式与绑定核对。研究不会产生 Canonical 事实。

## stdio 工作流

传输支持 MCP `2025-11-25` 与 `2025-06-18` 的初始化、工具发现、调用、ping 和协作取消。发现只返回工具描述。`tools/call` 同时返回结构化结果与等义 UTF-8 JSON 文本。宿主只需支持文本工具结果。字符串分页的偏移量指向完整 JSON 投影中的 Unicode 字符位置；`next_offset` 为空时该投影已传完。分页中间的 JSON 或目录标题不能独立用作引文。

| 研究动作 | 调用与约束 |
| --- | --- |
| 普通提问 | `list_cases` 给出可用题目。宿主用新 `study`、`request_id` 和 `case` 调用 `open_study`，直接取得首包证据、包内引用及 `packet_ref`。首包按原预算交付。`get_packet` 可按指定引用或分段回查实际正文。 |
| 发现缺口 | `browse_candidates` 按查询和偏移分页。目录区别来源标题、已读范围及当前 Packet 对该候选的处置。`read_source` 只接收本研究已登记的句柄，可读 `chunk`、`parent`、`natural_parent` 或已存在的 `graph`。保留条件、分支和显示顺序语义。长文本由 `get_read` 用保存的阅读地址继续，不重复消耗阅读额度。 |
| 继续研究 | `available_queries` 列出已封存的补查、原检索文本与前序证据。`follow_query` 核对所属研究、已执行父查询、回合及证据句柄，另记宿主的具体缺口。自由问题或目录外补查无法执行。`assemble_packet` 可用已读 fragment ID 明确选证，重新核对收条并按组顺序发布新包。 |
| 核对与恢复 | `check_answer` 对当前 `packet_ref` 和答案中的显式 `[E01]` 类标记检查绑定。无标记得到 `MISSING`，外来或过期地址返回错误。相同 `study/request_id` 与参数可重取已提交操作的原结果。`request_status` 区分已提交、查无提交及已记录的失败尝试。 |

研究 ID 和操作 ID 由调用方在自己的审计根内分配。根是本机可信操作边界；同根的进程共享结果。SQLite 在同一事务中保存完整来源回执、Packet、研究进度和返回值。失败回滚，已受理的证据操作所遇到的来源错误或取消另记失败尝试。中断可能早于失败记账；未发现已提交结果只说明本地数据库的观测。对已提交的同 ID 不同参数拒绝重用。

每研究最多 32 个不同阅读收条、96 次已提交状态操作。每连接串行执行，竞争调用显式报告忙。单次操作有 60 秒协作截止时间，取消在来源读取或提交边界生效。已提交后响应丢失可沿原 ID 恢复。原文读取及 SQLite 单次操作可能延迟取消。查询结果的完整投影与审计文件只在本机保存；工具结果能否完整进入宿主模型仍由宿主控制。

## 启动、验证和停止

在仓库根用 PowerShell 7 和现有 Generation 环境运行。需要工作台中已有的 `jsonschema` 依赖。对冻结语料验收需[数据目录](../../data/README.md)登记的旧基线、选择器和 G7 排名及首包文件。只跑小夹具不需要完整语料、索引权重或凭据。

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$python = './.local/runtime-py314/generation/Scripts/python.exe'
& $python -X utf8 -B -m unittest tests.generation.test_mcp_offline -v
& $python -X utf8 -B scripts/retrieval/verify_mcp_offline.py --audit-name review-01
```

验收命令要求使用全新的名称，将报告保存到 `data/generation/mcp-offline-v1/<audit-name>/acceptance.json`。宿主配置 stdio 时，`command` 指向该解释器的绝对路径，`args` 依次传入 `-X`、`utf8`、`-B`、`-m`、`genshin_corpus.mcp_offline`、`--repo`、仓库绝对路径、`--audit-name`、本宿主名称，`PYTHONPATH` 指向仓库的 `src`。给不同宿主分配独立审计名称。协议协商阶段不需要打开证据数据库。结束或禁用这个 stdio 进程即可退出试点。复查历史操作需用原版本、原资产和原审计根。

## 已安装宿主复核

已安装宿主的离线验收命令如下。从仓库根运行，给每次验收指定新名称。DSH 验收载入所选 Windows 程序附带的工具注册表与 MCP 插件。Codex 验收启动临时 app-server，使用独立状态目录及指向本机的占位模型地址。两条路径直接调用工具。具体版本与结论见[实机验收](../research/mcp-offline-acceptance-20261125.md)。

```powershell
$python = (Resolve-Path ./.local/runtime-py314/generation/Scripts/python.exe).Path
& $python -X utf8 -B scripts/retrieval/verify_mcp_codex.py --audit-name codex-review-01
$dsh = Read-Host '输入已安装的 DeepSeek Harness.exe 的完整路径'
$savedNodeMode = $env:ELECTRON_RUN_AS_NODE
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    & $dsh --expose-internals ./scripts/retrieval/verify_mcp_dsh.cjs $python dsh-review-01 | Out-Host
} finally {
    $env:ELECTRON_RUN_AS_NODE = $savedNodeMode
}
```

## 后续闸门

DSH 与 Codex 分别登记宿主版本、协议协商、文本回传、截断、取消及重连。协议客户端通过、宿主直调通过、真实模型看到了证据、回答得到了证据支持是四种独立结论。任意问题的真查询和新版 Release 多轮接线由[实时合同](mcp-live-v1.md)管理。正式问答与宿主 Agent 的有限配对按正确性、关键证据覆盖、响应速度、成本顺序裁决，UNKNOWN 与未交付按全题统计。真实调用、发布切换及资源窗口由[当前阶段](../current-phase.md)登记。
