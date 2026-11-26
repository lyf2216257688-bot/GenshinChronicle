# 2026-11-26 整版 Quick 兼容恢复

原执行为 `incremental-execution-20261125-v2`，输出在 `data/retrieval/vc-20261124`。2026-11-26 07:41 JST，两个构建臂及 Clean、Chunk 验证均已结束，`compare-vectors` 在打开数值载荷前因 `input_bundle_id` 不一致停止。原执行封条、各臂产物、回执和失败日志保留在原位置。[原进度](../../data/retrieval/vc-20261124/progress.jsonl) · [错误日志](../../data/retrieval/vc-20261124/attempts/compare-vectors/67bde8c18d/stderr.log)

## 复核与恢复范围

- 两臂各有 17,329 份资料、127,880 个唯一 Chunk。实际行文件哈希相同。逐份复算 34,658 个 Clean 文件和两臂全部非空 Chunk 范围哈希。219 份仅历史增量直引记录的同位置孤立节点诊断排序不同。规范序列化后逐字节与全建相等，内存重算的 Clean、Chunk 和输入身份相等。
- 孤立节点诊断最初从集合迭代发出。封存写入器已稳定排序，历史 Clean 引用跳过了序列化边界。修复在新快照上明确登记序列化合同，校验直引。发现许可的差异时绑定原有效 Raw 重派生并核对预期摘要。原始发现、源文、来源顺序和 Raw 定位照原绑定保留。
- 两臂各自计算 124,885 个 Dense 键、124,885 个 Sparse 键和 128 个 BM25F 桶。向量文件实际字节逐块一致。词法桶配置与成员逐桶一致，文件分别验签。恢复阶段验证源绑定和实物，不启动文档编码器或 BM25F 写入器。每臂从自己的物理载荷发布新的索引和 release。Quick 保留独立原始比较器、全量来源和范围验证，以及六题 × S0/S2 × 双臂。词法统计、评分和完整查询验收由正式恢复完成。
- 常规双臂构建在两臂 Clean 完成后先核对逻辑身份，再进行分片。两臂 Chunk 和统一输入完成后再次比较逐资料绑定、逻辑身份与完整行映射，之后才派发物理索引任务。恢复入口使用同样的统一输入前置检查。

## 正式恢复入口

新执行封条 `incremental-execution-20261126-recovery-v1`，输出 `data/retrieval/vcr-1126`。重新核验旧封条 320 个文件、新封条 321 个文件和新封条的 45 个外部输入。题单、三套运行环境、GPU 与驱动身份保持原绑定。PowerShell 7 从仓库根运行以下命令。当前派发状态见[阶段页](../current-phase.md)。

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

只读 PowerShell 预检核对身份和输入后，测得磁盘 **27.40 GiB**、内存 **2.15 GiB**、空闲显存 **2,461 MiB**，仅内存门槛阻断，输出根未创建。以实际启动时的预检为准。

## 中断、验收与资产保留

- 通过 `progress.jsonl` 查看阶段、心跳和资源，通过 `attempts/` 查看原始终端输出。主动中断在启动终端按 Ctrl+C，等待阶段子进程退出。正式根已登记 `run.json` 时，在同一套参数下把 `-Mode Run` 改为 `-Mode Resume`。已完阶段按回执核验，未完成的 Clean 记录从已封存检查点继续，未完成的一次性工作重做。预检拦截且正式根尚未创建时继续使用 `Run`。
- 完成后先核对新根的 `quick-complete.json`、两臂 `clean/normalization.json`、索引清单及 `asset_dependencies` 指向的带哈希依赖清单。每臂编码为 0 键、新建 0 桶，两臂索引文件归属独立。`-Mode Validate -Suite Quick` 复核已完成结果并单独登记复验回执。Extended 与发布状态按[当前阶段](../current-phase.md)安排。
- 恢复根是跨根发布。继续保留原 `vc-20261124` 两臂目录和审计、历史 Clean/Chunk 数据根及当前与有效 Raw 归档。依赖清单记录两臂各自的绝对路径、原构建来源和校验摘要。Chunk pack 按所列新 Chunk 清单的逐范围绑定验证。后续增量可以沿用这些载荷所在的原路径。清理前从所有保留的 release、回滚点和历史引用查明传递依赖。回滚使用原 release 及原会话绑定，恢复描述符之间即使共享逻辑输入 ID，也按各自清单路径和文件哈希解析证据。

## 离线验证

Clean 回归 46 项通过。独立小语料验证了旧身份早停、诊断重派生、重切相同、中断后复用完成的 Clean、零索引写入、拒绝跨臂领用，并用正常数值与词法对照器复验。原文件和清单哈希保持一致。正式 Quick 的构建、检索与重排留给操作者派发。[恢复回归](../../tests/indexing/test_snapshot_recovery.py) · [新执行封条](../../data/retrieval/incremental-execution-20261126-recovery-v1/complete.json)
