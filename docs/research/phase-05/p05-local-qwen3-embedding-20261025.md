# 本地 Qwen3-Embedding-0.6B 安装与验证（2026-10-25）

状态：**部署验证 PASS**。实际GPU/CPU离线加载、中文向量、正式RU小索引检索、
BM25/Hybrid及分时BGE重排已运行成功。此状态不表示全库质量或生产采纳通过。
当前授权及生产默认以
[`docs/current-phase.md`](../../current-phase.md)为准。

## 范围、身份和持久路径

本次在主项目目录执行，不创建工作树；不调用付费Embedding/Generation服务，
不修改Canonical、正式RU身份、Navi知识、旧索引、G1成果或生产选择器。
不执行全库535,802条RU重嵌入，不提交、推送或合并Git。

以下路径均相对项目根目录；本机实际根目录为操作员指定的主仓库，
不依赖Codex工作树或用户目录缓存。

| 资源 | 路径 |
|---|---|
| 官方模型 | `.local/models/qwen3-embedding-0.6b/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/` |
| 官方文件证据 | 模型目录内 `official-api-metadata.json`、`installation-manifest.json` |
| Python运行环境 | `.local/embedding-runtime/`，基于项目已有 `.local/python312-base/` |
| 依赖缓存 | `.local/embedding-dependency-cache/` |
| 安装证据、下载脚本、断点 | `.local/embedding-tests/install-20261025/` |
| 本次验证 | `.local/embedding-tests/qwen3-0.6b-20261025-r1/` |
| 新Dense索引 | 验证目录的 `dense/metadata/manifest.json` 和 `dense/artifacts/` |
| 本地适配器 | `src/genshin_corpus/retrieval/local_qwen3_embedding.py` |
| 验证/查询入口 | `src/genshin_corpus/retrieval/local_qwen3_validation.py` |
| 环境版本锁 | `requirements-local-qwen3-embedding.txt` |

模型：[`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)。
固定官方revision：`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`。
官方 `model.safetensors` 大小：1,191,586,416 bytes；官方LFS SHA256：
`0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd`。
Tokenizer及配置逐文件记录SHA256，并与同revision官方LFS摘要或Git blob身份核对。
本地加载会重新校验完整文件清单；不信任模型目录名替代文件验证。

## 安装经过与环境隔离

复用项目现有Python 3.12.10基础运行时；将旧环境的CUDA PyTorch及其必要依赖
复制为独立文件，随后只在新环境安装Transformers 4.57.3、Tokenizers 0.22.2、
psutil等依赖。旧BGE环境保持Transformers 4.39.1；没有向它安装或升级依赖。
新环境固定PyTorch `2.11.0+cu128`，CUDA可用。
完整版本见锁文件及安装目录的 `runtime-freeze.txt`；`pip check`通过。

沙箱内DNS/套接字与CIM查询受限，已授权联网操作使用必要的沙箱升级。
官方下载偶发TLS EOF；首个单连接下载被本轮主动中断，保留partial。
在确认官方206字节范围支持后，使用6个有界连接下载同revision权重。
第一次分段遇到TLS握手失败，保留已完成分段；改用requests复用连接补齐。
每次连接恢复最多3次，HTTP拒绝不重试；没有更换模型、非官方镜像、量化或
禁用TLS验证。下载脚本、日志和中间文件保留供审计。

初查GPU为RTX 3050 Laptop 4GB、驱动617.42；总RAM约15.69GiB，
初次可用RAM约2.61GiB，D盘初次空余约56.45GiB。
补充脱敏进程检查仅发现本轮下载/检查进程，没有发现其他Python推理任务。
WDDM的nvidia-smi进程列表不足以证明无桌面显存占用；实测已有约1.4–1.5GiB占用。
没有终止其他任务。详细时点见 `install-20261025/hardware-preflight.json`。

## 首版编码合同

| 参数 | 固定值 |
|---|---|
| 输出 | 1024维、FP32、L2归一化；非有限/零向量报错 |
| Query | `Instruct: Given a web search query, retrieve relevant passages that answer the query` + 换行 + `Query:` + 原查询 |
| Document | 正式RU的完整 `retrieval_visible_text`，不加指令、不重写 |
| Tokenizer | 官方同revision，left padding，right truncation |
| 特殊Token | 保留官方后处理器；实际会追加 `151643` 结束Token，计入2048上限 |
| 最大长度 | 2048 Token，包含Query指令和特殊Token；不宣称模型原生上限只有2048 |
| 池化 | 最后一个有效Token；固定左填充使其位于最后一列 |
| 推理 | `eval()`、`inference_mode()`、SDPA、`use_cache=False` |
| GPU | 显式CUDA FP16，不自动回退CPU或量化 |
| CPU | 显式CPU FP32，独立向量空间身份及独立索引 |
| 复现 | 相同运行时、设备、相同batch分组重复编码 `atol=1e-6, rtol=0`；不承诺跨设备逐位相同 |

固定随机种子、4个CPU线程、禁用TF32、开启PyTorch确定性算法，并设置
`CUBLAS_WORKSPACE_CONFIG=:4096:8`。每条输入记录截断前/后Token数及截断状态。
首版2048长度是4GB显存环境的明确操作点，不静默缩短模型能力声明。

边界脚本最初误将重复字符数当成总Token数，两次检查失败，日志分别为
`smoke-cuda.log`、`smoke-cuda-repair.log`。直接检查Tokenizer后确认其追加一个
结束Token；修正计数后 `smoke-cuda-eos-boundary.log` 与CPU检查均通过。
这两次失败发生在测试断言，模型加载及中文向量步骤已成功；没有改变官方特殊Token行为。

模型revision、全部文件身份、精度/设备、指令、长度、池化和运行时版本绑定为
`vector_space_identity`；RU顺序、文本SHA256、正式RU build及向量文件摘要进一步
绑定索引身份。Query必须携带相同空间身份，等维度外来模型也拒绝。
已有百炼2048维索引不会由本地入口检索。低层通用裸向量API仍为既有研究接口，
本地入口通过 `EmbeddingBatch` 强制身份核对。

## 验证设计与结果产物

### 模型运行与性能

`smoke-cuda.json`、`smoke-cpu.json`：记录实际加载、中文query/document输出、
向量维数/范数、重复差异、CPU/GPU资源及batch 1/2/4吞吐。
真实Token边界分别验证2047、2048、2049，要求后两者编码前缀相同、超长输入
明确记为截断；Query另计指令Token。验证期间使用本地文件、HF离线模式并阻断
socket连接，任何网络连接尝试使验证失败。

### 真实RU与历史对照

`selection.json`冻结909条正式RU：全部586条R1既有叙事RU，加每条G1旧普通卡查询
缓存Hybrid前20条RU；选择不读取有用/无用标签。扫描并验证正式535,802行RU
快照的文件摘要、行数和身份，但只为909条编码。原文、RU ID和上游文件不变。
`selected-rus.jsonl.gz`保留选中RU的全部来源字段，索引行映射记录正式ID和定位。

`retrieval-validation.json`包含：

- 中文角色关系、事件背景、语义改写、神话、文学意象5组人工部署探针的Top10，
  及同一小索引的BM25/Hybrid RRF组合结果。人工探针没有独立相关性标注。
- 20个R1起点，每个重用原raw首查询和ordinary查询，共40条原查询，
  在相同586条文档上比较本地Qwen3、百炼归档文档向量、R1缓存现期文档向量，
  以及各自相同BM25/RRF。百炼查询向量直接读历史缓存，零新API调用。
- 百炼完整归档矩阵和行映射在比较前校验原manifest摘要；现期586条文档向量
  只按原请求身份读取已有批次缓存，缺失即报错，不调用旧Campaign或自动补嵌入。
- G1的13个模型认为有用的旧候选：小索引覆盖7个目标，其余6个标为范围未覆盖。
  G1全库Hybrid与本轮子集Dense条件不同，其名次只能作诊断，不是同条件胜负。

R1提案和G1模型等级均非独立人工金标准；负例中的神话/人物身份等价关系不能由
向量相似度认定。没有执行新的语义裁决或Navi知识写入。

### 实测硬件表现

| 测量 | CUDA FP16 | CPU FP32 |
|---|---:|---:|
| 本次加载耗时（文件缓存已热） | 6.61秒 | 6.96秒 |
| 2条短Query编码 | 0.056秒 | 0.364秒 |
| 短文batch 1 / 2 / 4，条/秒 | 15.26 / 33.43 / 80.77 | 7.06 / 7.96 / 8.35 |
| 3条约2048 Token边界输入 | 3.03秒 | 31.32秒 |
| 进程峰值工作集（边界测试） | 约2.84GiB | 约3.92GiB |
| Torch峰值显存分配 / 保留 | 1.83 / 1.88GiB | 无GPU模型分配 |

短文仅4条中文句子重复为8条，GPU计时不足一秒，不能外推全库速度。
实际909条RU共496,933字符、343,840编码Token；GPU batch=2纯编码
**73.50秒、12.37 RU/秒**，包括验证/加载/写索引的总步骤94.71秒。
真实索引阶段Torch峰值分配1.33GiB、保留1.41GiB，进程峰值工作集2.86GiB；
nvidia-smi同阶段采样总占用3234MiB、空闲729MiB（包含桌面等占用，口径不同）。
909条全部未截断；人工2049边界输入明确记录2049→2048，含指令Query则
2066/2067/2068→2048。重复结果最大绝对差GPU/CPU各为0，范数范围
`0.99999994–1.0`，各次验证网络连接尝试数均为0。

实际BGE兼容检查使用原环境单独启动，10个新Hybrid候选全部评分并完成原排名融合；
含模型加载/权重校验共10.08秒，Torch峰值分配1.17GiB、保留1.30GiB。
Embedding进程已经退出；没有实测双模型同时常驻。4GB显存且桌面已占约1.5GiB，
RAM可用仅约1–2.6GiB，因此推荐分时加载；CPU可运行，但长文本更慢且工作集更大。

### 检索测量与失败案例

新索引身份：`52a792dc1761150eb8893cee20084a0007357a2d0fa685fadc832352348c0456`。
GPU编码空间身份：`d4a4cf6421c3b688f85e1c09ef0af8c445533e400d120abf427adca54c7729c6`。
40条精确旧查询均实际重新做本地Query编码；以下是在同586条RU上重算的
**文档Hit@20**，不是历史全库命中率。每组dev/holdout各9个正向提案。
百炼“现期”特指R1当时缓存的文档/查询向量，不是本轮新服务调用。

| 查询组 | 本地Dense | 百炼归档Dense | 百炼现期缓存Dense | 本地Hybrid | 百炼归档/现期缓存Hybrid |
|---|---:|---:|---:|---:|---:|
| raw / dev | 6/9 | 6/9 | 7/9 | 7/9 | 6/9、6/9 |
| raw / holdout | 6/9 | 7/9 | 7/9 | 7/9 | 7/9、7/9 |
| ordinary / dev | 7/9 | 7/9 | 7/9 | 7/9 | 7/9、7/9 |
| ordinary / holdout | 6/9 | 7/9 | 8/9 | 7/9 | 7/9、7/9 |

结论限于：本地模型保留若干关键召回，加入BM25后本次留出Hit@20与两套缓存
Hybrid一致；Dense本身未达到现期缓存的留出召回，不能宣称等效替代。
完整逐例名次在 `r1-matched-scope-ranks.csv`，结构化汇总在 `measured-summary.json`。

| 本地Dense掉出Top20的对照案例 | 查询组 | 本地排名 | 百炼现期缓存排名 |
|---|---|---:|---:|
| H04 如雷的盛怒 → 奇特的羽毛 | ordinary | 27 | 11 |
| H05 水仙之梦 → 逐影猎人 | ordinary | 23 | 9 |
| H05 水仙之梦 → 逐影猎人 | raw | 29 | 6 |
| D01 终末嗟叹之诗 → 炽烈的炎之魔女 | raw | 22 | 14 |

G1的13个旧模型有用案例中，本次7个目标页面出现，4个进入本地Dense文档Top20；
3个已出现目标排名266、62、29，另外6个目标没有进入本次范围。
“页面出现”不保证旧提案所需证据片段完整出现；局部文档覆盖、原全库Hybrid与
子集Dense差异均限制结论。这不是13个人工金标准的4/13效果指标。

人工部署探针的实际Top1如下；这些命中展示流程和原文定位，不是新语义事实判定：

| 探针 | Top1来源 | 正式RU ID |
|---|---|---|
| 钟离/若陀契约 | 古闻之章 第二幕「匪石」 | `897304311ef1ef0a8fe542075e4e1742437b22e2280e2926f52dcb8ff71c8eb0` |
| 渊下宫失书背景 | 龙蛇藏归辑录 | `d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8` |
| 歌声安抚巨鸟、后遭献祭的改写 | 如雷的盛怒 | `d7f2ce50af3c3b9898debd79c0f298f618ff81cc71a8c9fa5fd236c25f513401` |
| 三轮明月姐妹 | 竹林月夜 | `3ad2b18de4e771f63571c61673cbd14da88f2536ce7672c262907f3ed9dc2cca` |
| 风中故事/时间种子意象 | 祭礼弓 | `ab251f8fd852cc3acfa92296e5ca7b9f9cdfcf04a85c2ee794fd17789d35ad7e` |

其中祭礼弓原文实际含“风带来故事，时间使之发芽”，有可核对字面线索；
不能把此探针称为已验证的完全无词汇重合推理。改写、隐含联系和神话探针没有
新人工金标，结果不足以证明跨文档解释、身份等同或因果成立。

旧BGE Embedding权重在本地模型目录缺失；现存BGE历史索引使用不同文档范围和
查询，不能冒充本次同条件对照。历史W6的11例R@10 0.7273/MRR 0.4834仅作背景
（[原记录](../phase-04/p04-w6-dense-family-isolation.md)），不能与本次数字直接比较。
本轮没有下载另一个Embedding模型。生产BGE Reranker仍在，单独使用原环境
验证新Hybrid候选、正式RU ID、原重排接口及排名融合兼容性，见
`reranker-compatibility.json`；不验证两模型同时常驻或完整500候选QA质量。

## 从项目目录运行（PowerShell 7）

已有环境/模型可直接离线查询，不需要凭据或联网：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& .\.local\embedding-runtime\Scripts\python.exe -m genshin_corpus.retrieval.local_qwen3_validation query `
  --index .local/embedding-tests/qwen3-0.6b-20261025-r1/dense/metadata/manifest.json `
  --query '钟离与若陀龙王是什么关系？' --top-k 5
```

独立复测使用**新的**输出目录，避免覆盖已验收的结果：

```powershell
$run = '.local/embedding-tests/qwen3-0.6b-manual-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$python = '.\.local\embedding-runtime\Scripts\python.exe'
& $python -m genshin_corpus.retrieval.local_qwen3_validation prepare --run $run
& $python -m genshin_corpus.retrieval.local_qwen3_validation smoke --device cuda --run $run
& $python -m genshin_corpus.retrieval.local_qwen3_validation smoke --device cpu --run $run
& $python -m genshin_corpus.retrieval.local_qwen3_validation retrieve --run $run
# Embedding进程退出后，再使用原BGE环境分时验证：
& .\.local\gte-reranker-runtime\Scripts\python.exe -m genshin_corpus.retrieval.local_qwen3_validation rerank-probe --run $run
```

CPU用户需显式创建 `LocalQwen3Config(model_path, device='cpu', dtype='float32')`
并建立自己的新索引；CPU Query不能直接借用GPU FP16索引。
CLI的 `retrieve --device cpu` 和 `query --device cpu` 同样支持此显式路径；
必须为CPU建索引选择新的run目录。本轮CPU完成运行验证，未另建909条CPU索引。
恢复依赖时在独立Python 3.12.10环境使用版本锁，并单独从PyTorch cu128官方索引
取得匹配Torch wheel；不向原BGE环境安装本锁文件。模型下载证据脚本保存在
`install-20261025/`，已有完整安装无需重下。

## 代码与后续门槛

新增适配器、部署验证CLI、固定依赖锁及7项provider-free守卫测试；更新当前授权、
模型接口目录及目录职责。没有改生产路由/默认配置。
41项相关单元测试（本地适配器、原Dense/Hybrid、模型接口一致性）通过，
这与实际部署验收分别报告。
`preservation-audit.json`验证8个冻结输入文件摘要未变，原BGE环境版本未变；
这是一组命名输入的完整检查，不宣称重新审计全部历史文件。

全库重嵌入前仍需单独授权和设计：正式同条件检索评估、长文本覆盖/分段与RU不变
的处理、批量断点和失败账本、速度/磁盘预算、BGE分时生命周期、完整索引与查询
版本一致性、回滚及人工质量门槛。535,802×1024×4仅裸FP32矩阵约2.04GiB，
尚不含行映射、索引元数据、模型和中间数据。不能由909条的结果证明全库效果。
