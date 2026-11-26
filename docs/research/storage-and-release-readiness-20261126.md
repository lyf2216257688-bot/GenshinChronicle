# 2026-11-26 存储清理与语料绑定演练

## 实际清理

| 项目 | 执行与核对 |
| --- | --- |
| A1 缓存 | 根据封存清单逐文件核对后清理 712 个文件，包括 64 个 npm 辅助缓存、483 个临时 Codex schema 文件和 165 个 Python 字节码文件。清单核计释放 18,775,344 字节分配空间，执行前后 D: 空闲空间增加 18,800,640 字节。[清单](../../data/retrieval/storage-cleanup-20261126/a1-dry-run.json) · [执行结果](../../data/retrieval/storage-cleanup-20261126/a1-apply/result.json) |
| B1 GTE | 研究目录的独立权重副本改为原权重的硬链接。校验原路径、文件 SHA-256、文件身份及完整研究封条。原四个硬链接保留，现为五个。操作前后 D: 空闲空间增加 611,938,304 字节。[执行结果](../../data/retrieval/storage-cleanup-20261126/b1-gte/result.json) |
| A2 Quick 中断尝试 | 第五题 S0 增量侧中断尝试的 `candidates.json`、`scoring-input.json` 与 `jieba.cache` 和续跑结果逐项同哈希。改为同字节硬链接，两个尝试的路径、独有日志及 Quick 回执继续保留。操作前后 D: 空闲空间增加 22,409,216 字节。[执行结果](../../data/retrieval/storage-cleanup-20261126/a2-quick05/result.json) |
| A2 前端开发依赖 | 使用锁文件和本机缓存，在隔离目录离线重装。3,469 个文件的相对路径与 SHA-256 全部相同。清理 `frontend/node_modules` 和本次验证暂存，保留 npm 内容缓存、锁文件和已登记的应用静态资源。仅删除正式开发依赖目录的直接测量为增加 106,020,864 字节空闲空间。[离线证明](../../data/retrieval/storage-cleanup-20261126/a2-npm-restore.json) · [执行结果](../../data/retrieval/storage-cleanup-20261126/a2-npm-apply.json) |
| A2 G0 试验虚拟环境 | 由 G0 留存 wheel 和 41 项哈希锁在独立目录离线重装。逐字节核对 1,506 项运行包文件，`pip check` 和 39 项证据与会话测试通过。归档原虚拟环境的本机启动入口与安装元数据，随后清理原临时环境及重装证据的暂存目录。只移除原虚拟环境的直接测量为增加 66,666,496 字节空闲空间。[重装证明](../../data/retrieval/storage-cleanup-20261126/a2-framework-restore.json) · [原元数据](../../data/retrieval/storage-cleanup-20261126/a2-framework-original-metadata.zip) · [执行结果](../../data/retrieval/storage-cleanup-20261126/a2-framework-apply.json) |

两个整版构建根、两版 Raw、旧 Clean/Chunk、在用索引、回滚点和历史研究继续原位保留。清理后重新核验 36 份 Quick 回执、120 项输出及资产依赖清单，并复验 GTE 封条和活动选择器。[总复核](../../data/retrieval/storage-cleanup-20261126/post-check.json) · [A1 清理后清单](../../data/retrieval/storage-cleanup-20261126/a1-post.json)

上表各项直接测量合计增加 825,835,520 字节（787.58 MiB）空闲空间，临时验证目录的归还另计。旧冒烟和架构审计暂存中仍有未独立归属的固定回执、诊断和版本快照，原位保留。研究冷归档及 CUDA wheel 迁出待确认独立目标并验收恢复。两套已封存的 Torch 环境分别维护。

前端开发如需恢复依赖，可从 `frontend/` 使用 `npm ci --offline --ignore-scripts --no-audit --no-fund --cache ..\.local\npm-cache`。运行已登记的本地聊天包由其静态目录提供资源。安装缓存仍保留多项独立的离线安装材料，例如 `pip-cache` 的 transformers、scipy 与 `pip-cache-bm25f` 的 jieba、Whoosh3，继续作为恢复输入。

### 可复用的缓存维护入口

库存报告仅登记每个顶级项的逻辑大小和跳过的重解析点。DryRun 将已登记的再生缓存逐项写出路径、文件身份、摘要及依据文件分配大小计算的预计回收量。应用严格限定同一清单和同一目录，以新日志目录记录每项开始和完成。Git 跟踪文件、共享硬链接及重解析点受保护。清单变化时重新生成 DryRun。此入口范围是 A1 缓存，历史目录的传递依赖仍由 Release、研究清单和资产审计负责。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$python = '.\.local\runtime-py314\generation\Scripts\python.exe'
& $python -B scripts/retrieval/storage_cleanup.py Inventory --output data/retrieval/storage-inventory-next.json
& $python -B scripts/retrieval/storage_cleanup.py DryRun --output data/retrieval/storage-cache-next.json
```

核对清单并获对应范围批准后，以 `ApplyCaches --plan <清单> --plan-sha256 <清单摘要> --output <新日志目录>` 应用。已验证错误摘要、文件漂移和共享文件保护，及已授权小夹具中的实际清理。复用入口和参数见[脚本](../../scripts/retrieval/storage_cleanup.py)。

## 显式 Release 与回滚演练

在独立审计根内模拟从旧选择器绑定候选再退回旧选择器。复用封存 S0 与 S2 结果，检查运行描述符及实际通道，并重新回读证据。旧版冻结 Q001 首包三条引用可读，候选 S0 首包三条引用与历史验收记录一致。S2 首条证据按原行及 Raw 绑定直接回读。定位变化的 1048 通过当前新版和实际引用旧版的双观测校验，1190 的详情变更和新增 509698 均回读到新版，六个移除 ID 均退出当前 Clean 和索引行。

独立选择器回滚后，重启旧证据读取器可取得同一 Packet，新会话的固定候选引用仍可回读。错误摘要、缺失描述符及混用两臂物理清单均被拒绝。演练使用历史真实查询回执，新查询、GPU 推理和远端调用均为 0。活动索引选择器持续保持原 SHA-256。产品默认接线、正式发布和宿主工具实查分别按[当前阶段](../current-phase.md)登记。[验收脚本](../../scripts/retrieval/verify_release_binding.py) · [初次验收](../../data/retrieval/release-binding-20261126-r1/result.json) · [S0 回执补验](../../data/retrieval/release-binding-20261126-r2/result.json)
