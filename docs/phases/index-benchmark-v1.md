# 全量索引实验启动器 V1

2026-11-09。索引输入由 [输入合同](indexing-input-v1.md) 管理，当前授权和验收范围由
[current-phase](../current-phase.md) 管理。本工作单元交付独立启动器和有限离线验证。

## 入口与环境

在独立 PowerShell 7 终端中运行以下一条命令，工作目录可以位于仓库外。

```powershell
& 'D:\GenshinChronicle\scripts\retrieval\run-index-benchmark.ps1' -Mode Run
```

终端及其 Python 子进程拥有任务，关闭 Codex 后可继续运行。终端需要保持打开。
Windows 空闲睡眠抑制在监督进程运行期间生效，系统关机或强制睡眠之后使用 Resume。
启动器通过脚本位置解析仓库，无需 Codex 工具、在线会话或调度器。

四种模式使用同一 Output 和 Config。

| 模式 | 行为 |
| --- | --- |
| Preflight | 创建或复用统一输入，逐模型全量无截断 Tokenizer 扫描，最长实际输入小批量 CUDA 预检，词法环境预检 |
| Run | 自动执行 Preflight，串行构建三套 Dense，再构建 BM25F，逐套校验并用相同功能问题独立检索 |
| Resume | 核对原配置、执行代码和来源绑定，复用已提交分片或 Whoosh generation，继续未完成部分 |
| Validate | 流式核对已完成输入与四套索引，按 Query 或 Questions 参数执行独立检索 |

默认输出为 `data/retrieval/index-benchmark-v1-20261109/`。
`-Output` 可指定新的实验目录，`-Config` 可指定完整实验配置。
模型、权重或分析器变化使用新实验目录，既有索引保留独立配置和身份。
`-InputManifest` 复用完成的统一输入包，有限样本另须 `-AllowSubset`。
样本运行的最终报告和完成标记明确保存 scope=subset。
Resume 与 Validate 沿用首次启动的输入和 AllowSubset 选项。

```powershell
& 'D:\GenshinChronicle\scripts\retrieval\run-index-benchmark.ps1' -Mode Preflight
& 'D:\GenshinChronicle\scripts\retrieval\run-index-benchmark.ps1' -Mode Resume
& 'D:\GenshinChronicle\scripts\retrieval\run-index-benchmark.ps1' -Mode Validate -Query '坎瑞亚与深渊有什么关系？' -TopK 20
```

Questions 为 UTF-8 JSONL，每行具有唯一 question_id 和非空 question。
同一个问题文件交给各个独立索引和宏微入口，结果保留问题、表示身份、分数及源映射。
采用逐模型独立编码与检索，CPU/GPU 资源按进程退出释放。

## 固定资源及编码约定

`config/index-benchmark-v1.json` 引用已有本地环境与准备完成的固定版本模型。
本地准备报告位于 `.local/rag-index-prep-20261109/preparation-report.md`。
运行时使用 offline 环境变量和 socket 连接阻断，模型读取使用 local_files_only。
标准库监督进程串行等待每个子进程退出。各阶段使用各自既有 Python 环境。

| 模型 | 版本 | 文档 / 查询 | Pooling / 精度 / 最大长度 |
| --- | --- | --- | --- |
| Qwen3-Embedding-0.6B | 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3 | 原 dense_text / 既有 Instruct 与 Query 前缀 | 末非 padding token，FP16，32768 |
| BGE-M3 Dense | 5617a9f61b028005a4858fdac845db406aefb181 | 文档及查询均无前缀 | CLS token，FP16，8192 |
| Jina v5 Text Small Retrieval | 6856e76bb72982e58de0620458a4e8b3614da340 | Document 前缀 / Query 前缀 | 末非 padding token，BF16，32768 |

编码使用 AutoModel，Jina 沿用准备阶段已验证的 Transformers 5.1.0 直接加载路径。
BGE 的 CLS pooling 与本地 Sentence-Transformers 配置核对。
Qwen/Jina 使用左 padding，按 attention_mask 找到末个实际 token。
所有文档向量保存为 1024 维 FP32，并执行 L2 归一化。
模型权重 SHA-256、Tokenizer/配置文件摘要、完整 revision、Python/包版本、精度、
pooling、padding、special token、前缀、长度上限和查询编码合同进入向量空间绑定。
同维数模型之间保持独立身份。Qwen 本轮使用原生 32768 上限，属于新实验表示配置。

官方固定版本依据如下。

- Qwen 的指令式查询、末 token pooling 和上下文合同见固定模型卡。[Qwen 模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/blob/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/README.md)
- BGE-M3 的直接编码方式及 8192 上限见固定模型卡，CLS 配置来自同版本 1_Pooling/config.json。[BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md)
- Jina 的 Query/Document 前缀、末 token pooling 和 32768 上限见固定模型卡，准备记录保留 CC BY-NC 4.0 许可声明。[Jina 模型卡](https://huggingface.co/jinaai/jina-embeddings-v5-text-small-retrieval/blob/6856e76bb72982e58de0620458a4e8b3614da340/README.md)

## 输入、预检与失败处理

统一输入每个 chunk_id 只保存一行，micro/macro 保存路由。
默认从正式 Chunk V2.1 完整平面产物创建 all 选择的输入包。
加载来源时验证完整 Clean/Chunk Manifest 及实际使用文件的哈希。
输入导出复用工作单元1的 create-only 协议。中断导出保留在 input-attempts，
Resume 使用新的 attempt 完整重导。已完成导出通过哈希验证后复用。

每个模型扫描全部唯一 dense_text，计数包含其文档前缀及特殊 token。
lengths.jsonl 逐行保存 input_row、chunk_id、原 dense_text 哈希及未截断长度。
独立 overlength.jsonl 保存所有超限行，完整 histogram 和 maximum 进入回执。
完成的长度扫描可以复用，部分扫描保留后重新扫描。
只要存在超限，该模型预检失败，保留问题和失败日志供后续输入策略决策。

资源预检选择最长的至多四条实际输入，并编码一个固定查询。
默认批大小最多2，批内 padding Token 预算2048。单条完整文本可以超过该批预算，
仍须通过模型长度上限和实际单条显存检查。预检覆盖最长输入，实际构建继续监测资源。
CUDA OOM 写事件，批大小依次减半，仅重试未提交批。
batch=1 仍 OOM 或模型加载失败时停止该模型，回执与失败上下文保留。
编码始终使用 truncation=False，并核对 padding 前后的真实 token 数。
其他模型和 BM25F 独立继续，监督报告以 BLOCKED 表达未全部完成。

## Dense 存储与检索

每个模型拥有独立 dense/model-key 目录、编码合同、预检、审计、分片及完成清单。
每个分片最多128个唯一输入行，共享 Chunk 只编码一次。
vectors.npy 保存 FP32 矩阵，rows.jsonl 保存 input_row、chunk_id、文本哈希、
两路关系及展示序、源身份和 Chunk/Parent/Graph 位置。
按分片内长度排序组成有界批次，编码后散射回原输入行序。
complete.json 分片回执在向量与映射文件 fsync 后原子发布，绑定输入包及编码空间。
中断留下的无回执目录保留，恢复使用新 attempt，已提交分片逐一校验后复用。
最终 Manifest 在完整消费输入并独立校验向量、映射、长度审计和两路覆盖后发布。

向量分片本身构成可搜索的精确余弦索引。查询逐分片 mmap，计算 FP32 点积并维护
有限 TopK 堆，按路由筛选，分数相同时按 input_row 排序。
每次搜索验证分片回执和文件哈希，内存规模受分片大小约束。
采用精确扫描以建立三模型可复核的实验基线，119,557条规模的单次查询需顺序读取
约0.456 GiB向量，性能由实测报告评价。检索输出继续绑定共用输入和原文回读入口。

## Whoosh3 多分析器基线

使用既有 Whoosh3 3.54.0、Jieba 0.42.1 环境。
六字段均保留，每字段拆成 han_unigram、han_bigram、word 三个独立物理通道。
单字/双字复用工作单元1的分析器，Jieba 使用精确模式、HMM=False、包内默认词典，
词典 SHA-256 和配置摘要进入身份绑定。多值字段分别分析，双字保持源值边界。
六字段原文和源映射作为 payload 保存，完整来源/条件继续从共用输入与原 Chunk 回读。

实验基线采用六字段等权1、b=0.75、k1=1.2，三分析器权重依次0.2、0.3、0.5。
这些是启动器的明确可替换实验工作点，最终权重和专名词典仍依质量对照确定。
查询对各通道的唯一字面词项做 OR，通过字段权重乘分析器权重设置 boost。
Whoosh3 BM25F 的实际实现按物理字段分别计算 TF 饱和与 IDF 后加权求和，
该具体评分实现随配置保存，研究对照须据此解释分数。

micro/macro 使用独立 Whoosh 索引，分别计算 DF、平均字段长度及文档数量。
单进程 writer，64 MiB内存池，每1024条路由成员提交一次，采用 merge=False 限制合并峰值。
恢复直接读取已提交 generation 中的 input_row 与 payload 哈希，逐行对齐后补齐缺失行。
这覆盖提交成功、外部进度日志尚未更新时的中断窗口。
完成发布前验证全部输入与两路存储身份，并记录索引文件哈希。

## 运行保护及报告

监督目录和各子进程目录使用仓库已有的 OS kernel lock。
进程退出时锁由操作系统释放，保留的 build.lock 文件可继续使用。
源、配置或执行代码变更要求使用原实现恢复或新输出目录。
启动时按 Chunk文件大小、向量规模和6 GiB余量估算磁盘需求，阶段和分片继续检查余量。
模型加载前要求至少1.5 GiB可用系统内存，模型已加载时保留0.25 GiB运行余量。
监督进程使用 Windows Job Object，在启动被暂停的子进程并绑定进程树后才恢复执行，
虚拟环境解释器的后代也由同一任务持有。监督进程结束时操作系统关闭job并停止后代。
stdout/stderr 进入独立阶段日志，监督进程定期输出心跳，events.jsonl 保存资源与进度。
统一输入导出每1024行及末尾报告计数，源目录校验阶段另输出开始与完成事件。
Windows 内存余量及可用时的进程 RSS、CUDA 分配/保留峰值进入事件。
完整性报告和查询结果独立保存。complete.json 在全部索引验证与功能检索成功后发布，
失败运行保留独立 reports、failures 和已提交分片。

本合同建立机械完整性和独立检索接口。查询质量、人审与最终模型选择由后续对照负责。
