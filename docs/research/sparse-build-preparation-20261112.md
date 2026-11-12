# P3 Sparse 构建准备与手动运行说明

2026-11-12。工程合同见 [Sparse build V1](../phases/sparse-build-v1.md)。
本轮不启动全量编码；用户独立启动，结束后另行委托 Codex 验收，不要求 Codex 常驻监控。

## 实际资产和入口

- 输入：`data/retrieval/index-benchmark-v1-20261109/input-attempts/export-1794216082672799100-b96c140c/bundle/manifest.json`。
  input_bundle_id=`07aa27f71c6187f489d57f8255281e20847f3bb3de3ec1c0053324eab2d6fb95`。
  119,557 Chunk，micro 104,879、macro 78,658；共享63,980条只编码一次。
- 模型：`.local/rag-index-prep-20261109/models/bge-m3`，revision
  `5617a9f61b028005a4858fdac845db406aefb181`；复用原 FP16 CUDA 编码器和固定 `sparse_linear.pt`。
  不统一 Python 环境、不升级依赖。
- 运行环境：`.local/rag-index-prep-20261109/candidate-runtime/Scripts/python.exe`。
- 默认输出：`data/retrieval/bge-m3-sparse-v1-20261112-ready`。
  预检报告位于此根的 `preflight-attempts/`；有限编码只在其中的 `index/`。
  全量片在根级 `shards/`，完成后发布根级 `manifest.json`；预检不会建立这两项。
- 配置：`config/sparse-build-v1.json`；启动器：`scripts/retrieval/run-sparse-build.ps1`。

## PowerShell 7 操作

在独立的系统 PowerShell 7 窗口中运行。窗口可以从开始菜单打开，或在已有终端运行
`Start-Process pwsh -ArgumentList '-NoExit','-NoProfile'` 打开用户可见的独立窗口。
以下命令在该窗口中执行；Codex 可以关闭。终端需要保持打开。

```powershell
Set-Location 'D:\GenshinChronicle'
$sparseOutput = 'data/retrieval/bge-m3-sparse-v1-20261112-ready'

# 1. 预检：扫描和校验全量输入，但只编码32条结构样本
& .\scripts\retrieval\run-sparse-build.ps1 -Mode Preflight -Output $sparseOutput

# 2. 只有上一步成功，才手动执行此行开始全量
& .\scripts\retrieval\run-sparse-build.ps1 -Mode Run -Output $sparseOutput
```

应逐条执行并确认预检成功；不要忽略报错继续 Run。Run 会自行检查预检回执、代码和模型绑定。
可只在独立终端执行 Run；此前 Preflight 不必与 Run 属于同一 PowerShell 会话。
默认batch=1，每片256条，token预算2048；不用占用BM25F环境或加载Dense索引矩阵。

另开一个 PowerShell 7 窗口查看，不需要 Codex 轮询：

```powershell
Set-Location 'D:\GenshinChronicle'
& .\scripts\retrieval\run-sparse-build.ps1 -Mode Status
Get-Content -LiteralPath '.\data\retrieval\bge-m3-sparse-v1-20261112-ready\events.jsonl' -Tail 8 -Wait
```

`Get-Content -Wait` 的 Ctrl+C 只停止该查看窗口。构建窗口 Ctrl+C 才停止构建。
未提交片正在编码时，progress.json 保持上一个完整片的行数；events.jsonl 有片开始及批心跳。
长输入审计和来源准备阶段可能数十秒没有逐批日志，仍属于预检/准备，不是全量编码。

```powershell
# 3. 中断/关终端/资源释放后：在新的独立窗口执行
Set-Location 'D:\GenshinChronicle'
& .\scripts\retrieval\run-sparse-build.ps1 -Mode Resume

# 曾显式用较大批、发生OOM时可降低；默认本来就是1
& .\scripts\retrieval\run-sparse-build.ps1 -Mode Resume -MaxBatch 1

# 4. 构建成功后独立校验，不会再次编码全库
& .\scripts\retrieval\run-sparse-build.ps1 -Mode Validate
& .\scripts\retrieval\run-sparse-build.ps1 -Mode Status
```

这些行是独立操作示例，不要同时启动两个 Resume。
若使用自定义 Output，上述所有命令均显式添加相同 `-Output`。
不要编辑代码、模型或分片布局后强行沿用旧计划；批大小调整有显式参数，分片布局变更使用新目录。
不要删除锁、半成品、回执或现有正式索引来恢复。提交片损坏应保留现场并另行调查。

## 结果、资源和限制

开发期资源预检已实际触发保护：2026-11-12 首次预检可用 RAM 约0.6GiB，
低于2GiB启动门槛，未加载模型/编码任何Chunk。错误写入默认输出的 `runs/` 与 `events.jsonl`。
该失败未降低资源门槛。资源恢复后，原目录的一轮32条预检全部通过；反向审计
补上构建结束前模型主权重/head重验后，在 `-ready` 最终目录重新完成预检。
旧目录 `bge-m3-sparse-v1-20261112/` 保留开发证据，其旧计划不应直接 Run。

最终证据指针为 `data/retrieval/bge-m3-sparse-v1-20261112-ready/preflight.json`。
它指向 `preflight-attempts/probe-1794451024714926600-411f0d7a/report.json`；
同目录的 `source-cases.md` 展示32条实际原文，`source-readback.json` 保存完整定位。

| 检查 | 最终结果 |
| --- | --- |
| 全量输入/成员/已接受长度审计核对 | 119,557条；micro104,879/macro78,658；最大1,135token，0超长；不重新对全库编码 |
| 32条真实结构样本 | micro21/macro22；5,056nnz；0空向量 |
| 对P0–P2相同样本 | 32/32；最大权重绝对误差0；space_id不变 |
| 官方Sparse核 | 8次、累计9条，最大误差0；不等同完整FlagEmbedding流水线一致 |
| padding/特殊token | 最大权重差0.00024414，有限检查容差0.005 |
| 流式查询 | 3个独立工程问题，两路排序和分数均与独立暴力点积一致，未使用70题 |
| 真实样本恢复 | 首片停止后续跑，3个提交文件字节不变，0条已提交行重编码 |
| 初次32条编码 | 4.23秒，含首次模型加载；恢复验证另重编码尚未提交部分；不能外推可靠全量ETA |
| 总预检 | 94.37秒，主要含全量只读核验、模型准备、回读和重复校验 |
| 启动系统可用RAM | 约4.99GiB |
| 最终采样系统可用RAM / 进程RSS | 约3.05GiB / 2.86GiB；不是全程RSS峰值 |
| 加载前空闲显存 | 约3.23GiB |
| CUDA分配/保留峰值 | 约1.14GiB / 1.18GiB；未出现OOM |
| 预检磁盘空闲 | 约68.86GiB |

32条样本平均158nnz，简单外推CSR载荷约144MiB，**不含来源映射、元数据和
失败尝试，也不是全库容量承诺**；保留6GiB磁盘保护和逐片检查。默认batch=1、
shard_rows=256、padded_token_budget=2048可以作为本机的首轮保守参数。

相关环境矩阵共104个不同测试ID通过，其中17项新增流式/作业测试；
包括生产片大小的513条合成流式夹具，以及真实子进程在第二片半写时强杀再续跑。
各环境的依赖跳过由另一现有环境覆盖，没有升级环境。证据在 `data/retrieval/`：
`p3-preparation-tests-r3.log`、`p3-preparation-tests-lexical.log`、
`p3-preparation-tests-final-core-r3.log`、`p3-preparation-tests-final-interfaces.log`。
早期r1暴露Windows只读句柄不能fsync的问题，已改为可写句柄；r2的单元测试
目录准备错误亦已修正，失败日志保留。最终脚本PowerShell解析与真实Preflight/Status通过；
Run/Resume/Validate处理路径由小夹具验证，本轮未对真实全库调用Run/Resume。
`p3-preparation-acceptance.json` 汇总最终绑定、验证与Git状态。

**已达到用户手动启动全量构建的工程条件。** 资源会随其他程序变化，手动运行时
仍须通过同一Preflight/Run门槛；运行期间的全量稳定性和最终完整性由后续结果验收负责。

此前 P0–P2 的32条试验 CUDA分配峰值约1.14GiB；本机RTX3050 Laptop 4GiB。
这只是既有有限证据，不代替本轮运行前检查。启动前建议系统可用RAM至少3GiB、
GPU空闲至少1.5GiB、D盘空闲超过6GiB；关闭占用较大的其他任务，保持接电和系统终端打开。
默认batch=1优先控制显存；单条OOM须释放显存后重试，不截断/丢弃该条。
磁盘保护还会在写片前计入当前CSR载荷。样本体积只能给出粗估，不承诺全量时间或总大小。

## 审计

自我审计：编码空间与P0–P2保持独立Sparse合同；不复用Dense space_id；宏微共享Chunk
不重复编码；全量候选和成员覆盖、空向量都有计数；来源逐行映射可独立回读。
本轮没有融合/去重算法改动，没有以工程正确性替代相关性选型，没有新增服务依赖。

反向审计：模拟强杀时最后一个未提交片允许重算，已有回执片不能重算或改字节；
伪造/损坏已提交回执、源身份/代码/模型变更、缺行、内存/磁盘不足都必须失败；
只有全部存储检查与最后绑定检查通过后才发布Manifest，之后仍须独立Validate。
批配置变化可能带来FP16有限差异，日志明确记录，不承诺跨批逐位一致。
有限验证不覆盖全量长时稳定性、全库查询延迟、全量nnz分布或S0/S1/S2质量。

本轮未执行：119,557条全量编码、70题、付费API、Serving切换、Git Commit/Push、破坏性清理。

## 本轮文件改动与 Git 状态

本轮新增：

- `src/genshin_corpus/indexing/sparse_full.py`：流式存储、回执恢复、独立校验和逐片查询。
- `src/genshin_corpus/indexing/sparse_job.py`：独立作业、来源/模型/代码绑定、预检、日志、资源保护。
- `scripts/retrieval/run-sparse-build.ps1`：真实PowerShell入口。
- `config/sparse-build-v1.json`：隔离运行环境和首轮可调参数。
- `tests/indexing/test_sparse_full.py`：17项小夹具及故障测试。
- `docs/phases/sparse-build-v1.md`：持久技术合同。
- 本运行说明。

本轮更新：`.gitignore`（纳入新脚本）、`README.md`、`data/README.md`、
`docs/current-phase.md`、`docs/architecture-overview.md`、`docs/project-structure.md`、
`docs/roadmap.md`、`docs/model-interfaces.md`，以及
`docs/phases/clean-retrieval-v1.md`（明确P0–P2历史授权与本轮新增P3合同关系）。
没有改动已有P0–P2编码/召回代码、模型、正式索引或上游资料。

交付时 HEAD 仍为 `8d9fdd2`，暂存区为空。`git status --short` 共8个 tracked 修改项和
15个 untracked 条目（其中一个是目录，展开后包含多文件）。这包括此前未提交的P0–P2文件，
不是本轮单独新增的数量；完整快照保存在 `p3-preparation-acceptance.json`。
本轮所有源码/文档保持未提交，没有 Commit/Push。
