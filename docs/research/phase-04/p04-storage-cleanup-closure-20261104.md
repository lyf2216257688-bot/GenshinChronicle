# Phase04 存储清理收尾报告

日期：2026-11-04（JST）  
范围：仅处理授权的两份 Phase05 目录、轻量核对已有 F 盘镜像，并完成 Phase04 离线生产链路验收。未修改源代码或 Git，未调用模型/API，未重新生成全库向量。

## 结论

| 项目 | 状态 | 结果 |
|---|---|---|
| Card repair-v3 归档及 D 盘原件清理 | **PASS** | 完整路径、数量、字节数及逐文件 SHA-256 校验通过后删除。 |
| Navi R1-QA 归档及 D 盘原件清理 | **PASS** | 完整路径、数量、字节数及逐文件 SHA-256 校验通过后删除。 |
| F 盘旧 Navi 散文件副本 | **BLOCKED** | 保留；其中有 6 个文件不属于归档时的 D 盘源目录快照。 |
| 此前 22 项镜像 | **PARTIAL / observed** | 本轮仅作路径级核对；未独立完成全量哈希核验。 |
| Phase04 离线生产检查 | **PASS** | 生产清单、索引、模型和检索冒烟检查通过；249 项离线回归通过。 |
| 本范围以外的历史存储 | **UNKNOWN** | 未调查、未修改。 |

## 两份新归档

使用系统自带 Windows tar.exe（bsdtar 3.8.9）分别制作 gzip 压缩 tar 包，直接写入 F 盘。采用流式、中等压缩配置，适合大量小文件；未安装 7-Zip。每份归档均为单文件、无分卷，低于 FAT32 的 4 GiB 单文件限制；未在 D 盘生成临时压缩包。

| 原目录 | 原始文件数 / 总字节数 | 目录项数（含根目录） | F 盘归档（各 1 个文件） | 归档大小 / SHA-256 | 文件清单大小 / SHA-256 |
|---|---:|---:|---|---|---|
| p05-card-live66-1017-repair-v3-20261101-r1 | 32,007 / 4,704,814,488 | 6,916 | F:\2_个人存档\GC存档\data\retrieval\p05-card-live66-1017-repair-v3-20261101-r1.tar.gz | 734,979,305 / 5ff727d37187f652defb542814734652f0e4071c6e555902b2bbd90a2a7734d6 | 10,308,812 / a19932a256755013d9e5b6fefad0aa6973f69baa56e939e8300933a58c757df9 |
| p05-navi-r1-qa-20261025-r2-deepseek | 98,129 / 1,172,220,928 | 26,142 | F:\2_个人存档\GC存档\data\retrieval\p05-navi-r1-qa-20261025-r2-deepseek.tar.gz | 380,819,423 / dd5bbb8f76388d70e6dafde753410fe3fa38f9bc5ca902c97418c9fc6899c9fb | 27,153,145 / b418e5bfdd7bc3b67c3a26030274315e929fbf17a32bb5ffa58204682507494a |

每个归档旁均有 .tar.gz.sha256 校验文件和 .files.sha256.jsonl 全量文件清单；清单逐文件记录相对路径、字节数及 SHA-256。压缩耗时：Card 约 10 分 54 秒，Navi 约 4 分 35 秒。

### 完整校验与删除

两份归档分别通过完整性读取测试；所有成员均可流式读取。原目录与归档的相对路径集合、文件数、目录数、文件总字节数逐项一致；归档中每个文件的 SHA-256 均与原文件一致。压缩完成后还重新读取并核对了源目录。抽样恢复并核对了关键状态/清单文件及原始模型响应。

全部检查通过后，仅删除授权的两个 D 盘原目录；目前两条原路径均不存在。未留下 D 盘压缩临时文件，本轮一次性审计临时目录也已清理。

## 磁盘空间

| 磁盘 | 清理前可用空间 | 收尾时可用空间 | 观测变化 |
|---|---:|---:|---:|
| D 盘 | 47,410,761,728 字节（首次删除原件前） | 53,508,186,112 字节（报告写入后） | 区间净增加 **6,097,424,384 字节** |
| D 盘 Card 删除步骤 | 47,410,761,728 字节 | 52,159,725,568 字节 | 增加 4,748,963,840 字节 |
| F 盘 | 34,325,725,184 字节（新归档前） | 33,172,357,120 字节 | 减少 1,153,368,064 字节 |

F 盘为 FAT32，总容量 61,509,107,712 字节。两份压缩包合计 1,115,798,728 字节；清单及校验文件合计 37,462,182 字节。可用空间变化与逻辑文件大小略有差异，来自 FAT 分配单元。F 盘旧 Navi 散文件副本的占用不计入本轮新增归档。未单独捕获 Navi 删除瞬间的 D 盘空间，因此不推算其独立释放量。

## 此前 22 项镜像与旧 Navi 副本

按要求未寻找此前报告，也未重跑 22 项归档。本轮只检查了可见镜像根目录：.local 和 data\retrieval 下共发现 24 个直接子目录，其中 21 个同名 D 盘目录不存在，3 个仍存在且属于保留的 Phase04 生产/回退资产。该目录视图不能与此前 22 项逐条一一对应。根据用户提供的初核结果，此前 22 项的 F 盘镜像路径存在、D 盘原路径不存在；本轮未取得可逐项复核的全量 SHA 清单，因此结论为**未独立完成全量哈希核验**。没有重拷或覆盖这些目录。

旧散文件副本 F:\2_个人存档\GC存档\data\retrieval\p05-navi-r1-qa-20261025-r2-deepseek 仍保留。它有 71,177 个文件、逻辑大小 948,155,111 字节；归档时 D 盘源目录有 98,129 个文件。双方共有路径的文件哈希一致，但它不完全是新归档的子集：发现以下 6 个只存在于旧 F 盘副本、且不属于当时 D 盘源目录快照的文件，故删除该副本会有丢失证据的风险：

- attempts/review-94808d8e5716bf26608c-deepseek/sdk/attempts/01/issued.json
- attempts/review-94808d8e5716bf26608c-deepseek/sdk/attempts/01/request-metadata.json
- attempts/review-94808d8e5716bf26608c-deepseek/sdk/attempts/01/request.json
- attempts/review-94b73185b017d3baa5a2-deepseek/sdk/attempts/01/stream.jsonl.gz
- attempts/review-94b73185b017d3baa5a2-deepseek/sdk/attempts/01/terminal.json
- attempts/review-94b73185b017d3baa5a2-deepseek/sdk/attempts/01/wire-request.json

旧副本还缺少 analysis-code-snapshots、clarifications 两个目录，且 attempts 目录数量少于当前源目录。待上述 6 个 F 盘独有文件完成归属核对后，再决定能否清理旧副本；新归档完整保存了当前 D 盘源目录。

## Phase04 生产数据与离线验收

- Raw、Parsed、Canonical 目录仍存在。RU 源 Manifest 状态为 complete，共 **535,802 条 RU**；Retrieval Unit、skip ledger、failure ledger 文件大小均与 Manifest 一致。
- 新 1024 维全库向量 Manifest 完整，覆盖 535,802 条；行数据/向量文件大小匹配记录，向量矩阵以 float32 [535802, 1024] 成功打开。
- 旧 2048 维全库向量仍保留，Manifest 完整覆盖 535,802 条；文件大小匹配记录，矩阵以 float32 [535802, 2048] 成功打开，继续作为对比基线和回退选项。
- 正式 Field-Aware BM25 Manifest 与产物大小匹配；生产词法检索成功返回候选。
- 原始 70 题集、参考答案及比较材料仍在；相关问卷/评审文件各 70 行。本项只确认文件存在，不代表重新评测或人工验收。
- 本地 Qwen3 Embedding、BGE Reranker 模型及运行环境离线加载成功。Qwen 查询检索全量 1024 维索引并返回 5 个候选；BGE 对候选完成打分并返回有限值。
- 现有离线回归：**249 项通过**，17 个模块，Python unittest，耗时 77.973 秒；启用 Hugging Face/Transformers 离线模式。未调用 DeepSeek、线上 Embedding 或其他模型 API；未重新跑 70 题评测或全库 Embedding。

## 恢复方法

在 F 盘可用的 PowerShell 7 环境中运行以下命令，将归档解压到原 data\retrieval 位置并恢复原目录名：

~~~powershell
$archiveRoot = 'F:\2_个人存档\GC存档\data\retrieval'
$restoreRoot = 'D:\GenshinChronicle\data\retrieval'
tar -xzf (Join-Path $archiveRoot 'p05-card-live66-1017-repair-v3-20261101-r1.tar.gz') -C $restoreRoot
tar -xzf (Join-Path $archiveRoot 'p05-navi-r1-qa-20261025-r2-deepseek.tar.gz') -C $restoreRoot
~~~

解压前用 Get-FileHash -Algorithm SHA256 将压缩包哈希与相邻 .tar.gz.sha256 比较；恢复后按 .files.sha256.jsonl 对逐文件路径、字节数和 SHA-256 做全量核验。

## 未结事项

- **BLOCKED：** 核对旧 Navi 副本中 6 个仅 F 盘存在的文件后，再决定是否删除该副本。
- **PARTIAL / observed：** 此前 22 项只获得路径级证据；未独立完成全量哈希核验。
- **PASS：** 两份授权 D 盘原件均在完整校验后删除；D 盘无本轮压缩临时文件。
- **UNKNOWN：** 其他历史实验资产不在本轮范围内，状态未检查。
