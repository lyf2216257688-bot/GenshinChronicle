# 2026-11-26 整版 Quick 兼容恢复

收尾后的路线调整取消了本轮 Extended。恢复根已保存 Q001–Q003 和 Q004 中断记录，后文命令和预算为当时的方案档案。当前验收范围及派发授权见[当前阶段](../current-phase.md)。

原执行为 `incremental-execution-20261125-v2`，输出在 `data/retrieval/vc-20261124`。2026-11-26 07:41 JST，两个构建臂及 Clean、Chunk 验证均已结束，`compare-vectors` 在打开数值载荷前因 `input_bundle_id` 不一致停止。原执行封条、各臂产物、回执和失败日志保留在原位置。[原进度](../../data/retrieval/vc-20261124/progress.jsonl) · [错误日志](../../data/retrieval/vc-20261124/attempts/compare-vectors/67bde8c18d/stderr.log)

## 复核与恢复范围

- 两臂各有 17,329 份资料、127,880 个唯一 Chunk。实际行文件哈希相同。逐份复算 34,658 个 Clean 文件和两臂全部非空 Chunk 范围哈希。219 份仅历史增量直引记录的同位置孤立节点诊断排序不同。规范序列化后逐字节与全建相等，内存重算的 Clean、Chunk 和输入身份相等。
- 孤立节点诊断最初从集合迭代发出。封存写入器已稳定排序，历史 Clean 引用跳过了序列化边界。修复在新快照上明确登记序列化合同，校验直引。发现许可的差异时绑定原有效 Raw 重派生并核对预期摘要。原始发现、源文、来源顺序和 Raw 定位照原绑定保留。
- 两臂各自计算 124,885 个 Dense 键、124,885 个 Sparse 键和 128 个 BM25F 桶。向量文件实际字节逐块一致。词法桶配置与成员逐桶一致，文件分别验签。恢复阶段验证源绑定和实物，不启动文档编码器或 BM25F 写入器。每臂从自己的物理载荷发布新的索引和 release。Quick 保留独立原始比较器、全量来源和范围验证，以及六题 × S0/S2 × 双臂。词法统计、评分和完整查询验收由正式恢复完成。
- 常规双臂构建在两臂 Clean 完成后先核对逻辑身份，再进行分片。两臂 Chunk 和统一输入完成后再次比较逐资料绑定、逻辑身份与完整行映射，之后才派发物理索引任务。恢复入口使用同样的统一输入前置检查。

## Quick 恢复启动记录

执行封条为 `incremental-execution-20261126-recovery-v1`，输出为 `data/retrieval/vcr-1126`。启动前核验旧封条 320 个文件、新封条 321 个文件和新封条的 45 个外部输入。题单、三套运行环境、GPU 与驱动身份保持原绑定。以下 PowerShell 7 命令保留作 Quick 首次恢复的运行记录。针对已完成的输出根使用本页末尾的 Extended 命令。

```powershell
$recoveryOptions = @{
    Suite = 'Quick'
    Execution = 'data/retrieval/incremental-execution-20261126-recovery-v1'
    Output = 'data/retrieval/vcr-1126'
    SealSha256 = 'd924b83840e62ddd71fba297238e376b10b9d2de3c65d52f6a0ad9d1b0355051'
}
& .\scripts\retrieval\run-incremental-corpus.ps1 @recoveryOptions -Mode Preflight
# Preflight 通过后由操作者启动
& .\scripts\retrieval\run-incremental-corpus.ps1 @recoveryOptions -Mode Run
```

| 项目 | 恢复预算 |
| --- | --- |
| 磁盘 | 启动至少 **10 GiB**，运行保底 **6 GiB**。两臂手头同类 Clean、Chunk 和输入清单及行表合计约 413 MiB，219 份 Clean 约 78 MiB，待验证重切范围约 195 MiB。另为查询、日志、依赖清单和中断残留保留空间。|
| 内存与显存 | 启动至少 **6 GiB 可用内存**及 **2,048 MiB 空闲显存**。运行内存保底 0.75 GiB。每个查询 GPU 阶段再次检查显存门槛。 |
| 时间 | 先预留 **3–5 小时**。原两次独立 Clean/Raw 定位验证合计约 1 小时 51 分钟。恢复还需补建与重切、完整物理对照、24 次本地查询及依赖复核。文档向量和词法桶的构建时间省去，整版恢复耗时待实跑。 |

首次只读预检测得磁盘 **27.40 GiB**、内存 **2.15 GiB**、空闲显存 **2,461 MiB**，内存门槛在当时阻止了启动。实际恢复运行结果见下文。

## 中断、验收与资产保留

- 通过 `progress.jsonl` 查看阶段、心跳和资源，通过 `attempts/` 查看原始终端输出。主动中断在启动终端按 Ctrl+C，等待阶段子进程退出。正式根已登记 `run.json` 时，在同一套参数下把 `-Mode Run` 改为 `-Mode Resume`。已完阶段按回执核验，未完成的 Clean 记录从已封存检查点继续，未完成的一次性工作重做。预检拦截且正式根尚未创建时继续使用 `Run`。
- 完成后先核对新根的 `quick-complete.json`、两臂 `clean/normalization.json`、索引清单及 `asset_dependencies` 指向的带哈希依赖清单。每臂编码为 0 键、新建 0 桶，两臂索引文件归属独立。`-Mode Validate -Suite Quick` 复核已完成结果并单独登记复验回执。Extended 与发布状态按[当前阶段](../current-phase.md)安排。
- 恢复根是跨根发布。继续保留原 `vc-20261124` 两臂目录和审计、历史 Clean/Chunk 数据根及当前与有效 Raw 归档。依赖清单记录两臂各自的绝对路径、原构建来源和校验摘要。Chunk pack 按所列新 Chunk 清单的逐范围绑定验证。后续增量可以沿用这些载荷所在的原路径。清理前从所有保留的 release、回滚点和历史引用查明传递依赖。回滚使用原 release 及原会话绑定，恢复描述符之间即使共享逻辑输入 ID，也按各自清单路径和文件哈希解析证据。

## 恢复前的离线验证

Clean 回归 46 项通过。独立小语料验证了旧身份早停、诊断重派生、重切相同、中断后复用完成的 Clean、零索引写入、拒绝跨臂领用，并用正常数值与词法对照器复验。原文件和清单哈希保持一致。[恢复回归](../../tests/indexing/test_snapshot_recovery.py) · [新执行封条](../../data/retrieval/incremental-execution-20261126-recovery-v1/complete.json)

## 恢复版 Quick 收尾

`vcr-1126` 于 2026-11-26 10:05:06 JST 登记，15:09:21 JST 封存 `PASS`，墙钟累计 5 小时 4 分 15 秒，含暂停与等待。系统在增量上游恢复和 BM25F 对照阶段各因可用内存低于 0.75 GiB 暂停一次。另有一次在第五道 Quick 题的 S0 增量侧记录 `KeyboardInterrupt`。后续按同一身份接续，最后一次进程耗时 12 分 50 秒。回执记录远端调用为 0。[正式进度](../../data/retrieval/vcr-1126/progress.jsonl) · [Quick 完成回执](../../data/retrieval/vcr-1126/quick-complete.json)

| 验收项 | 通过结果 |
| --- | --- |
| Clean、Chunk、统一输入 | 两臂各 17,329 份资料、127,880 个唯一 Chunk。全量 Clean/Raw 定位、Chunk 范围及统一输入逻辑身份通过，行文件 SHA-256 一致。 |
| Dense、Sparse | 两臂逐渠道对照 124,885 个活跃键。每臂各复用该臂原建的 124,885 个 Dense 键和 124,885 个 Sparse 键，重新编码为 0。 |
| BM25F | micro 和 macro 的全局统计通过，六题 741,426 项评分一致。每臂复用原臂的 128 桶，新建为 0。 |
| 固定查询 | 六题 × S0/S2 × 双臂，共 24 次本地查询。相同配置下双臂候选、重排顺序和首条证据回读通过。 |
| 独立审计与收尾复核 | ChatGPT 独立审计通过。收尾再次从磁盘计算 36 份阶段回执、120 项输出、资产依赖清单及封条中 321 个文件和 45 个固定输入的 SHA-256。两臂物理索引路径分别落在各自原臂，跨臂借用为 0。 |

| 封存文件 | SHA-256 |
| --- | --- |
| [执行封条](../../data/retrieval/incremental-execution-20261126-recovery-v1/complete.json) | `d924b83840e62ddd71fba297238e376b10b9d2de3c65d52f6a0ad9d1b0355051` |
| [Quick 完成回执](../../data/retrieval/vcr-1126/quick-complete.json) | `6bb423876647a9049ffb02a8d4a6fafdbf2044f824d7d0271b92fe1854b44d95` |
| [资产依赖清单](../../data/retrieval/vcr-1126/attempts/asset-dependencies-fe587cd8/e6c0f737c8/output/dependencies.json) | `4b585ade4e102d7f9e9cbf296c75c888c975343aaded54c42e0a8f8873addad3` |

原执行根 `vc-20261124`、原封条、故障日志、新旧 Raw、历史 Clean/Chunk 和恢复根依现有绝对路径一并保留。每臂依赖清单登记 17,329 个 Clean 文件、2,928 个数值载荷文件及封条、384 个词法载荷文件及封条。增量臂登记 8 个 Chunk pack 路径，全建臂登记 4 个，实际范围按 Chunk 清单验证。历史 G7 归档、在用旧版索引、恢复封条和固定的本地运行环境继续作为 Extended 输入。收尾范围为只读核验和文档登记，所有有效资产留存在原路径。

## Extended 验收计划

使用同一[封存题单](../../data/retrieval/incremental-execution-20261126-recovery-v1/questions.json)和[增量臂描述符](../../data/retrieval/vcr-1126/incremental/indexes/release.json)。Quick 已对照新版两臂，Extended 从新版增量臂查 S0，并按以下范围与在用旧版比较。

| 阶段 | 验收范围 |
| --- | --- |
| 接续检查 | 核对 Quick 回执和输出、封条及原跨根依赖。启动器预检固定输入、三套解释器和包清单、GPU 与驱动身份。沿用 Quick 的已完成回执。 |
| G7 历史 70 题 | 旧侧读取已封存的首轮 S0 召回、重排、证据读取和实际首包。新侧逐题执行 S0 的 BM25F/Dense、`bge_structural` 和 `gc-g5-official-4` 首包组装。按通道和粒度登记候选出入、分数与名次，比较重排前六和首包证据。 |
| 11 个变化案例 | 在用旧版和新版增量臂各查一次。覆盖新增 509698、模块变化 1190、菜单变化 2030、列表变化 506076、定位变化但内容未变 1048，以及 6 个移除 ID。逐项记录其在原始召回、重排及首包中的位置。所有移除 ID 均须退出新版库存，且在对应案例的新版原始召回、重排和首包中无残留。 |
| Packet 与人工复核 | 保留两侧首包及新版回读回执，程序核验新版引用、原文回读、两类 Raw 观测的文件绑定及首包预算。机械结果以 `extended-review.json` 的 81 条唯一记录和 `extended-complete.json` 为准。人工逐题复核关键证据出入、原文及相关父级范围、否定、条件及定位，优先查看 11 个变化见证和有证据换出的历史题。语义判定单独登记，证据不足时保留不确定。 |

程序验收涉及 **81 次新版查询和 11 次旧版本地查询**，历史侧其余 70 题从封存件读取。新版每题最多先读前 6 个证据，使用原 G5 策略组包并校验引用。结果中的 `MECHANICAL_PASS` 对应程序检查，语义栏初始为 `NOT_REVIEWED`。新增资料在有效库存中的状态与其是否被固定题召回分别记录。定位变化案例需要同时核对当前和实际引用观测，固定题未命中该资料时沿清单回读。全程在本机执行，付费调用预算为 0，聊天的活动版本继续按[当前阶段](../current-phase.md)记录。

### Extended 资源预算

| 资源 | 计划值和依据 |
| --- | --- |
| 时间 | 机械验收预留 **2–4 小时**。Quick 的 12 次 S0 查询每次约 53–77 秒，平均 65 秒。Extended 增加 11 次旧版查询、逐题首包、历史归档读取及最终依赖复核。按同机测量估算，完整 81 题的实际耗时待实跑。人工语义审阅另计。 |
| 磁盘 | 预计新增 **2–3 GiB**，另保留失败尝试，安排时预留 **5 GiB** 增量空间，建议开始前可用至少 **12 GiB**。估算依据是整版 S0 每个阶段约 20–28 MiB，再计入旧侧查询、首包、回执和日志。续跑硬保底 **6 GiB**。 |
| 系统内存 | 安排时释放到 **至少 6 GiB** 可用。既有续跑和运行保护值为 **0.75 GiB**，恢复版 Quick 曾两次触发。 |
| GPU | 沿用封存的 RTX 3050 Laptop 4 GiB 与原驱动。预检和每个新查询阶段要求 **至少 2,048 MiB** 空闲显存。查询编码和重排使用本地 GPU，文档向量继续使用已验收载荷。 |

2026-11-26 在已登记输出根运行只读预检，封条、固定输入和运行时核验通过，当时可用磁盘 **30.48 GiB**、内存 **3.15 GiB**、显存 **2,277 MiB**。该内存符合续跑保护值，计划仍按 6 GiB 可用内存安排。

### Extended 启动与断点恢复

取得本次验收授权并备齐上表资源后，在仓库根的 PowerShell 7 执行下列代码块。首次接续和中断恢复使用同一个 `Resume -Suite Extended` 命令。前置检查核对原 Quick 全部回执和输出，发现缺失或摘要变化立即停止。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $extended = @{
        Suite = 'Extended'
        Execution = 'data/retrieval/incremental-execution-20261126-recovery-v1'
        Output = 'data/retrieval/vcr-1126'
        SealSha256 = 'd924b83840e62ddd71fba297238e376b10b9d2de3c65d52f6a0ad9d1b0355051'
    }
    function Assert-PinnedFile($Path, $Sha256) {
        if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Sha256) {
            throw "SHA-256 differs: $Path"
        }
    }
    $quickPath = Join-Path $extended.Output 'quick-complete.json'
    Assert-PinnedFile $quickPath '6bb423876647a9049ffb02a8d4a6fafdbf2044f824d7d0271b92fe1854b44d95'
    $quick = Get-Content -LiteralPath $quickPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
    foreach ($stage in $quick.receipts.Keys) {
        $receiptPath = Join-Path $extended.Output "receipts/$stage.json"
        Assert-PinnedFile $receiptPath $quick.receipts[$stage]
        $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        foreach ($file in $receipt.outputs.Values) {
            Assert-PinnedFile (Join-Path $extended.Output $file.path) $file.sha256
        }
    }
    Assert-PinnedFile (Join-Path $extended.Output $quick.asset_dependencies.path) $quick.asset_dependencies.sha256
    & .\scripts\retrieval\run-incremental-corpus.ps1 @extended -Mode Preflight
    & .\scripts\retrieval\run-incremental-corpus.ps1 @extended -Mode Resume
}
```

- 在另一终端可用 `Get-Content data/retrieval/vcr-1126/progress.jsonl -Tail 20 -Wait` 查看实时阶段、心跳和资源。逐题回执为 `receipts/review-*.json`。每次尝试在 `attempts/review-<题号>/<尝试ID>/` 保存日志，完成该题时封存 `case.json` 和 `packets.json`。结果路径与 SHA-256 见回执和汇总。
- 出现资源暂停或主动按 Ctrl+C 时等待进程退出，保留锁文件、回执和尝试目录。资源恢复后重新执行同一检查和 `Resume`。已完成题目按回执复用，未完成题目在新尝试目录继续。最终汇总会再次核对各题 Packet 文件哈希，随后重验跨根依赖。哈希异常应保留现场并调查。
- `Run` 用于新运行根的登记。在既有根上使用 `Validate -Suite Extended` 会另外重跑 Quick 的四项全量层验证及两项物理索引对照，按本次实测另计约 2.5 小时。常规查看结果可直接核对 `extended-complete.json`、`extended-review.json` 及其引用的逐题回执。实际进度和采用状态见[当前阶段](../current-phase.md)。
