# Clean与双粒度Chunk并行构建优化

本文记录V1并行构建检查点。当前分片准入和表示见[V2合同](../phases/chunking-v2.md)，
V2全量执行授权及当前状态由[current-phase](../current-phase.md)负责。

2026-11-07。两条流程默认12个独立Worker进程，按资料动态分配任务。
本轮只处理16条真实资料，原始Raw、既有Clean及历史试点保持原样。
balanced参数、准入规则、三项质量修正和读取接口沿用现有合同。

## 实现

- 共用有界进程调度器，同时在途任务最多为Worker数的两倍。Worker意外退出明确报错。
  协调进程持有运行锁，逐资料锁保护异常退出后的恢复，Worker之间分别写入。
- Clean按记录原子保存文件及完成凭据，协调进程约每2秒刷新进度，最终按原选择顺序汇总。
  恢复时核对绑定输入和文件SHA-256，复用已验证结果。未登记文件继续确定性重建比较。
- Chunk按资料保存独立分片文件及最后发布的完成凭据，恢复时跳过已完成资料的适配、分词和范围检查。
  最终流式合并原有JSONL并发布完成manifest，汇总中断可从完成凭据重新汇总。
- Token计数采用有界记录内缓存，固定BGE问文对复用正文编码长度和特殊Token数。
  超长段落从后向前查找最后可用句末，保留原边界选择。完整父层Token数继续精确计算。
  适配、父层回读及来源检查共用块索引。每个Worker的Tokenizer线程限制为1。

## 实际耗时与资源

使用[16条选择](../../data/retrieval/build-performance-20261107/selection.json)，
包含前次12条质量案例，以及命定将焚的虹光、长篇对话及角色资料等4条。
覆盖172个组件、12组对话，产生706个自然父层和2,256条balanced候选。
最长自然父层104,195个Qwen Token。503380单条贡献1,603条候选，存在明显单资料长尾。

本机16逻辑CPU、约16GB物理内存，使用既有Python 3.12.10和Tokenizers 0.22.2环境。
各轮顺序执行，计时包含启动、校验、写盘和汇总。进程树每0.2秒采样CPU及内存，
CPU平均值表示约占用几个逻辑核，内存取进程树专用内存之和。
这是有限样本单次实测，系统文件缓存和后台负载未经冷启动控制，不能据此估算全量工期。

| 流程 | 原实现单线程 | 新实现1路 | 新实现12路 | 12路吞吐 | 12路平均 / 峰值CPU核 | 12路峰值内存 |
| --- | --- | --- | --- | --- | --- | --- |
| Raw → Clean | 9.87秒 | 8.87秒 | 4.86秒 | 3.30条/秒 | 3.95 / 11.50 | 798MiB |
| Clean → Chunk | 47.28秒 | 17.12秒 | 12.75秒 | 1.25条/秒，177条候选/秒 | 3.69 / 12.05 | 3,852MiB |

相对原单线程，两项12路实测分别约2.03倍和3.71倍。
串行峰值专用内存分别由211/488MiB变为213/502MiB。
12路Chunk期间系统最低可用内存约0.78GiB，资源较紧张。
已限制分词线程、缓存和在途任务，仍保留`--workers 6`或`--workers 4`供低内存时使用。
多个大条目同时处理时内存可能高于本次峰值。

最终代码绑定的运行名为`clean-stable-1/12`及`chunk-stable-1/12`。
完整原始采样分别见[Clean 12路](../../data/retrieval/build-performance-20261107/clean-stable-12.metrics.json)
与[Chunk 12路](../../data/retrieval/build-performance-20261107/chunk-stable-12.metrics.json)。
同目录的`after`、`final`和`executor`是开发检查点。

## 一致性与恢复

- 79项回归通过，包含原40项Clean、26项分片及13项构建回归，覆盖12进程、Worker异常退出、
  中断、汇总恢复、输入变化、哈希损坏、单写入者及历史完成目录只读。
- 新Clean的串行、12路和强制中断续跑结果，16条记录字节完全一致。
  与原实现及现有全量Clean逐字段比较，正文、结构、定位和诊断集合一致。
  其中14条与原实现字节一致，4690和503380仅有孤立节点诊断排列差异。
- 旧解析器从无序集合生成孤立节点诊断，跨进程本来就存在排列不确定性。
  本轮仅固定构建序列化中同一来源的这类诊断顺序，保留其他数组顺序及全部诊断。
  既有完成记录保持原字节，未登记旧文件恢复容许这一排列差异后仍保留原字节。
- Chunk的正文、父层、图关系、准入账及Markdown样例与原实现SHA-256完全一致。
  2,256条候选的文本、ID、范围、别名、双粒度覆盖和重叠关系均随这些文件保持一致。
  report除运行时间外内容一致，既有Clean清单及所选源文件哈希保持一致。
- 两条流程分别在至少一条完成凭据落盘后强制终止测试进程树，再用12路续跑至complete。
  已完成凭据的字节及修改时间保持不变，最终产物与未中断运行一致。
  汇总中断恢复、输入变化拒绝及损坏拒绝另由小fixture回归覆盖。

核对结果见[一致性记录](../../data/retrieval/build-performance-20261107/comparison.json)、
[强制中断记录](../../data/retrieval/build-performance-20261107/interruption-result-stable.json)
和[回归日志](../../data/retrieval/build-performance-20261107/tests-final.log)。
工程变化不改变既有全库Raw保真验收和后续检索质量实验的范围。

## 可复制的PowerShell 7命令

在项目根目录使用既有本地环境。以下全量命令供用户手动执行，本轮未启动。
Clean使用新run ID，Chunk读取当前已完成Clean。

```powershell
Set-Location D:\GenshinChronicle
$env:PYTHONPATH = 'src'
$env:PYTHONIOENCODING = 'utf-8'
$py = '.\.local\embedding-runtime\Scripts\python.exe'

# Clean全量构建。中断后原样重跑同一命令即可续跑。
& $py -m genshin_corpus.clean build `
  --raw-run-root data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824 `
  --clean-run-id clean-v1-parallel-20261107 `
  --workers 12

# Chunk全量构建。首次使用新目录。
$chunkArgs = @(
  '-m', 'genshin_corpus.chunking',
  '--clean-manifest', 'data/clean/mihoyo_obc/zh-cn/clean-v1-full-20261106/metadata/manifest.json',
  '--selection', 'config/chunking-full-20261107-balanced.json',
  '--qwen-tokenizer', '.local/models/qwen3-embedding-0.6b/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/tokenizer.json',
  '--bge-tokenizer', '.local/models/bge-reranker-v2-m3/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e/tokenizer.json',
  '--output', 'data/retrieval/clean-chunking-full-parallel-20261107',
  '--workers', '12'
)
& $py @chunkArgs

# Chunk中断续跑。重新打开终端时先恢复上面的变量定义。
& $py @chunkArgs --resume
```

上述已有全量选择文件核对为16,437个唯一ID，与当前Clean全集一致，使用原balanced参数。
未来读取新Clean时相应修改`--clean-manifest`，并核对选择集合。
Chunk恢复绑定代码、配置、输入及Tokenizer，绑定变化使用新目录。
旧版无逐条完成凭据的中断Chunk目录也使用新目录。

### 完成状态

```powershell
$cleanManifest = 'data/clean/mihoyo_obc/zh-cn/clean-v1-parallel-20261107/metadata/manifest.json'
Get-Content $cleanManifest -Raw | ConvertFrom-Json |
  Select-Object status, input_detail_count, accounted_detail_count, counts

$chunkRoot = 'data/retrieval/clean-chunking-full-parallel-20261107'
Get-Content "$chunkRoot/build-state.json" -Raw | ConvertFrom-Json |
  Select-Object status, completed_records, total_records, workers, error
if (Test-Path "$chunkRoot/manifest.json") {
  Get-Content "$chunkRoot/manifest.json" -Raw | ConvertFrom-Json |
    Select-Object status, build_binding
}
```

Clean完成要求`status=complete`、计数相等且无`blocked_integrity`。
`complete_with_errors`需检查对应记录。Chunk完成要求最终manifest为`complete`，
同时进度计数等于总数。约2秒的进度刷新间隔可能使中断时计数落后于完成凭据，
续跑会重新核对凭据。完成后重跑Clean原命令或Chunk续跑命令会校验绑定和产物哈希。

## 保留事项

源资料内仍按原算法串行组织，单条超长资料可形成长尾。
每条记录完成后才有恢复凭据，中断中的当前资料需要重做。
Chunk逐条结果与最终JSONL同时保留，增加磁盘占用，支持恢复和重新汇总。
Python 3.12的异常清理使用兼容分支终止执行器拥有的子进程，异常退出回归已覆盖。
全库并发资源峰值、磁盘持续吞吐及长时间运行仍需用户之后实际构建观察。
