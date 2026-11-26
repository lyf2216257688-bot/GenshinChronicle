# GC 专业证据 MCP 实时合同 V1

以 Windows 本机 stdio 提供显式启用的 GC 研究工具。宿主决定问题怎样分解、是否继续查证以及怎样作答。GC 固定检索和重排工作点，核对来源、交付证据、记录组包取舍并校验引用绑定。[当前状态](../current-phase.md) · [离线回放合同](mcp-offline-v1.md)

## 版本与工具

服务启动时须选择 `--release 20260824` 或 `--release 20261124`。前者用现用扁平索引，后者读 Quick 封存的增量构建 Release。两版都复用 S0、`f1_max` 和 `bge_structural`，查询范围为登记语料。新研究固定 Release、选择器、输入和实现身份。关闭后以同一审计根、实现及可回读的原资产重启，可以继续原研究。回滚时把新研究的启动参数拨回 `20260824`，同根原有研究继续各用各的绑定。

| 操作 | 用法 |
| --- | --- |
| 检查版本 | `get_scope` 返回语料键、新研究使用的版本与研究上限。 |
| 首查 | `open_study` 接受新 `study`、新 `request_id` 和原问题 `question`。保留完整原问题，以六个已排序候选的 Chunk 尝试组首包。空结果保留空证据。 |
| 浏览 | `browse_candidates` 指定 `study` 和查询 ID。首轮 ID 为 `original`。续页用 `next_offset`。行内预览、条件提示和标题用于定位。 |
| 阅读 | `read_source` 指定已发放的候选 `handle`、`request_id` 与 `chunk`、`parent`、`natural_parent` 或 `graph`。用 `get_read` 和 `read_ref` 继续取回完整正文。 |
| 补查 | `follow_query` 指定已执行的 `parent_query`、具体缺口 `gap` 和自由查询 `search_text`。可提供父查询的 `evidence_handles`、子问题、重排文本与补查类型。补查查询 ID 使用该操作的 `request_id`。各轮保留完整顺序。 |
| 重新组包 | `assemble_packet` 可给出已读 `fragment_ids` 并查看这些片段的实际准入处置。默认组包先保留原规则准入的片段，再用结构化压缩释放的空间纳入更多已核读的 Chunk。候选顺序由 Reranking 确定。过长范围整段记为预算排除。 |
| 核对引文 | `get_packet` 用 `packet_ref` 可打开该研究已交付的当前或历史包，也可指定单个 `citation`。`check_answer` 用当前包与答案内 `[E01]` 等标记做绑定检查，返回实际阅读收条和来源身份。 |
| 恢复 | `get_study` 返回问题、查询、读回和包地址。`request_status` 使用原操作 ID 取回已提交的原结果。未提交的失败可在同一版本用原参数重试。全量正文、排序、收条及失败记录写入独立审计根。 |

按字符分页的 `projection` 包含总长度、投影摘要及 `next_offset`。从第零页取到末页才得到完整 JSON。引用以 `packet_ref` 和包内编号共同定位。源码回读复验当前所用的 Chunk、Clean 和来源定位。旧扁平链核对原文与 Raw 血缘，新版另外校验当前和有效 Raw 字节，复用记录可显示两个不同的 Raw 版本。语义支持留给逐项原文评审。

每研究最多五次查询、32 份不同阅读收条、96 次已提交操作。首查及补查的服务端时限为 540 秒，建议把宿主工具时限配置为 600 秒。取消或断线在校验、worker 等待和提交边界生效。长文件解析完成后才到达下一检查点。提交后断线由原请求 ID 恢复。查询使用现有 GPU 锁，操作竞争会报告忙。每进程可复用当前语料的来源 Reader。Dense 和重排分进程依次加载并退出。新版词法 worker 逐路关闭 Reader。从 `queries/<attempt>/run.json` 可核对分段耗时、复用状态及进程退出。

## 启用和实机验证

在仓库根运行 PowerShell 7。根据现机路径确定解释器与仓库目录。

```powershell
$repo = (Resolve-Path .).Path
$python = (Resolve-Path ./.local/runtime-py314/generation/Scripts/python.exe).Path
$env:PYTHONPATH = Join-Path $repo 'src'
& $python -X utf8 -B -m genshin_corpus.mcp_live --repo $repo --audit-name standalone --release 20260824
```

服务由宿主以 stdio 启动。以上直接命令用于检查启动参数，运行后等待 MCP 消息。两个宿主可用不同的 `--audit-name`。为历史研究保留原名称。退出时停止对应宿主进程或撤下配置，之后仍可用原绑定复查审计。

**Codex** 可在目标配置中加入以下段落，使用实际的 `<repo>`、`<python>`，Windows TOML 路径可用单引号或正斜杠。[Codex MCP 配置说明](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

```toml
[mcp_servers.gc_live]
command = '<python>'
args = ['-X', 'utf8', '-B', '-m', 'genshin_corpus.mcp_live', '--repo', '<repo>', '--audit-name', 'codex', '--release', '20260824']
cwd = '<repo>'
startup_timeout_sec = 20
tool_timeout_sec = 600
env_vars = ['PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432']

[mcp_servers.gc_live.env]
PYTHONPATH = '<repo>/src'
```

**DSH** 在目标 Cordis 配置作用域中注册下列插件实例。参数与隔离验收使用的桥接器一致。[DSH MCP 配置说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/mcp.md)

```yaml
- id: gc-live
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    transport: stdio
    serverName: gc_live
    command: '<python>'
    cwd: '<repo>'
    args: ['-X', 'utf8', '-B', '-m', 'genshin_corpus.mcp_live', '--repo', '<repo>', '--audit-name', 'dsh', '--release', '20260824']
    env:
      PYTHONPATH: '<repo>/src'
    startupTimeoutMs: 20000
    toolCallTimeoutMs: 600000
```

隔离实机脚本直接调宿主工具。分别用新的审计名称执行，等待一侧完成后再启动另一侧。新版选择器只作用于本次 MCP 研究。

```powershell
& $python -X utf8 -B scripts/retrieval/verify_mcp_live_codex.py --audit-name codex-live-review
$dsh = Read-Host '输入已安装的 DeepSeek Harness.exe 的完整路径'
$savedNodeMode = $env:ELECTRON_RUN_AS_NODE
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    & $dsh --expose-internals ./scripts/retrieval/verify_mcp_dsh.cjs $python dsh-live-review 20261124 | Out-Host
} finally {
    $env:ELECTRON_RUN_AS_NODE = $savedNodeMode
}
```
