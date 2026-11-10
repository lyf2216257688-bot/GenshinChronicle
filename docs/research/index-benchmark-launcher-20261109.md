# 新 RAG 全量索引实验启动器实施记录

2026-11-09。工程合同见 [启动器合同](../phases/index-benchmark-v1.md)。
本轮用户授权实现独立运行的全量实验程序，并进行有限离线验证。

## 调查与工程取舍

读取现有 AGENTS、current-phase、索引输入合同与实施记录、实际实现和模型准备报告。
交接03/04及技术总账的宏微共享、BM25F/Dense原文通道和分支职责继续适用。
现场既有未提交修改保留，本次沿用工作单元1的输入格式与三套本地环境。

模型准备报告实际位于 `.local/rag-index-prep-20261109/preparation-report.md`，
Qwen 复用原 embedding-runtime，BGE/Jina 复用 candidate-runtime，BM25F复用bm25f-runtime。
Tokenizer、权重、模型配置、BGE pooling 配置和 Jina 模型卡均实际读取。
固定版本官方模型卡作为编码合同的独立核对依据，链接由阶段合同维护。

Dense 使用有界 FP32 分片及精确余弦搜索，宏微共享每个 Chunk 的一次文档编码。
选择该物理表示便于本机串行执行、逐分片恢复及机械比较。
Whoosh3 两路独立统计，六字段各保留三分析器。实验选用等字段权重、默认Jieba词典，
并显式绑定参数。精确扫描性能、最终模型、专名词典和字段权重仍须后续查询质量对照。
本轮没有安装或升级依赖。

## 实现入口

- `scripts/retrieval/run-index-benchmark.ps1` 为独立 PowerShell 7 启动脚本。
- `config/index-benchmark-v1.json` 为三模型、环境、资源上限与BM25F实验参数。
- `indexing/benchmark.py` 串行监督阶段，准备输入、保存实验绑定和日志、发布实验报告。
- `benchmark_worker.py` 在指定环境执行独立阶段，离线连接阻断，拥有阶段kernel lock。
- `benchmark_dense.py` 负责模型合同、完整长度审计、资源预检、可恢复向量及精确搜索。
- `benchmark_lexical.py` 负责Jieba适配、Whoosh3构建、generation恢复、校验与独立检索。
- `benchmark_io.py` 复用原子写、回执校验、OS锁与睡眠抑制，提供有界实验IO及资源记录。

`.gitignore` 原先整体忽略 scripts。本次仅开放指定新启动器的跟踪路径，现有忽略规则的
其他脚本范围继续由其原规则负责。原Phase04源代码与运行配置保持原执行合同。

## 验证证据

验证产物保存于 `data/retrieval/index-benchmark-launcher-20261109-review/`。
finite-input 从已验收的 sample-final-a 输入中选择资料509653与640的全部候选，
共7条唯一Chunk、6个micro成员、5个macro成员。来源绑定沿用正式V2.1及Clean Manifest。
该选择由本轮工程验证确定，目的是限制实际本地模型编码规模。

正式有限运行采用 `finite-config.json`，仅将每分片和每次Whoosh提交缩小为2条。
模型、版本、精度、前缀、长度上限、分析器和评分参数沿用正式配置。
`finite-accepted/` 为最终验证目录，先完成Run，再验证Resume和其他入口。
每模型有4个已提交分片，完整7条向量只编码一次用于两路索引。
Resume 前后的47个向量、行映射、分片回执、Dense Manifest和Whoosh文件逐字节一致。

| 验证项 | 结果 |
| --- | --- |
| 原有Clean、Chunk、输入和恢复回归 | 145项通过 |
| 启动器及新编码合同回归 | 36项测试ID在环境矩阵内全部覆盖 |
| 环境矩阵 | 原embedding环境147项通过，含两个Torch pooling/前缀检查；BM25F环境36项中34通过，两个Torch检查在原环境执行 |
| 自动化覆盖合计 | 181项通过 |
| PowerShell | PS7语法解析、独立进程入口通过 |
| 四模式实际运行 | Preflight、Run、Resume、Validate在最终有限目录全部PASS，Validate带中文Query参数通过 |
| 仓库外启动 | 从交接目录以绝对脚本路径启动独立pwsh，Validate全部PASS |
| 三模型实际Tokenizer | 7条各自完整扫描，无超限，包含实际文档前缀和特殊Token |
| 三模型实际小型Dense | 各7条1024维FP32/L2向量，6个micro及5个macro成员，四分片完整校验 |
| Whoosh3实际小型索引 | 六字段乘三通道，两路独立构建与全部存储来源对齐通过 |
| 独立检索 | 各模型单独编码相同问题，四套索引均分别执行micro/macro检索 |
| 已完成部分复用 | 实际Resume通过，47个已提交产物哈希一致 |
| 中断窗口 | 注入停止于完整向量分片后，保留无回执目录并继续；Whoosh提交后恢复通过 |
| 进程生命周期 | 实际Windows kernel lock、Job Object退出与监督进程硬退出后代终止检查通过 |
| 失败处理 | 注入超限、OOM减批、batch=1 OOM、磁盘不足、配置漂移、Token审计/向量/Whoosh损坏均按合同失败 |

有限样本长度最大值分别为Qwen531、BGE540、Jina534 Token。
Run预检的CUDA分配峰值分别约1198、1140、1203 MiB，包含模型及小样本激活。
这些值来自当前小样本，不能作为全量最长输入或最终批次吞吐的保证。
OOM恢复由注入异常验证，实际小样本未触发CUDA OOM。
第一次pooling回归夹具缺少dimensions字段，修正夹具后重新执行完整矩阵并通过。
早期日志和有限检查点保留，最终采信入口为带accepted名称的回归日志与finite-accepted目录。
汇总证据为 [validation-accepted.json](../../data/retrieval/index-benchmark-launcher-20261109-review/validation-accepted.json)，
绑定最终源码、启动脚本、配置、测试日志、四模式报告、模型版本、查询结果及恢复前后哈希。

## 授权边界与后续执行

本轮实际执行为代码/文档修改、离线回归、7条正式来源Chunk的本地模型编码及小型索引。
默认全量目录 `data/retrieval/index-benchmark-v1-20261109/` 在本轮验证时尚未创建。
三模型的119,557条全量编码由用户之后在独立系统终端运行Run启动。
完整共享输入导出尚未在本轮执行，全库Tokenizer审计、最长输入显存可行性、
全量Whoosh性能及检索质量仍需查看之后的实际报告。
完整统一输入导出中断后使用新attempt重导，Dense按分片恢复，Whoosh按已提交generation恢复。
精确Dense搜索顺序读取分片并校验哈希，后续对照应同时记录检索耗时。
实验使用包内Jieba词典、六字段等权及固定三分析器权重，最终工作点仍须质量比较。

复现命令、默认输出及Questions格式由阶段合同维护。实验完成后查看complete.json、
latest-report.json、各索引validation.json及queries中的结果。
长时间运行要求保留独立PowerShell终端，关闭Codex之后脚本的运行依赖仍在本机。
源数据、旧索引和已准备的模型均由其原目录继续保存，既有工作区修改得到保留。
本次实际执行没有付费API、Git Commit/Push或旧Phase04链路迁移。
