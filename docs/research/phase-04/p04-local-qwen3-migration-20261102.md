# Phase04 本地 Qwen3 原文 RU 全库迁移准备（2026-11-02）

本轮完成程序、离线检查与有上限的真实试跑；**未启动全库向量化，未切换检索默认值，未调用任何付费 API**。
当前授权和默认状态归 [`current-phase.md`](../../current-phase.md)；本文件负责运行方法、恢复边界与本轮测量。

全库输出根目录的 Preflight 已通过，成功分片0、尚无 vectors/serving manifest。
280项不同离线测试通过：Generation51、后端/UI/模型配置/重排99、RU/Assembly/检索/旧Qwen120、
新分批构建10。测试包括中断恢复、文件损坏、未提交重算、配置漂移、OS并发锁、OOM减批、
NaN阻断、超长拒绝、索引身份一致、local/online隔离和DeepSeek默认选择；均不调用真实API。

## 今晚启动

从 Windows 的独立 PowerShell 7 终端运行，接通电源，关闭其他 GPU 推理/大型游戏。
以下代码块可直接复制。`Run` 自带预检查，不需要分别执行两次：

```powershell
Set-Location 'D:\GenshinChronicle'
pwsh -NoProfile -File .\scripts\retrieval\build-local-embedding.ps1 -Mode Run
```

只检查环境、输入、长度、磁盘及已有成果，不生成向量：

```powershell
Set-Location 'D:\GenshinChronicle'
pwsh -NoProfile -File .\scripts\retrieval\build-local-embedding.ps1 -Mode Preflight
```

中断、断电、进程退出后，从同一个目录恢复：

```powershell
Set-Location 'D:\GenshinChronicle'
pwsh -NoProfile -File .\scripts\retrieval\build-local-embedding.ps1 -Mode Resume
```

`Run` 和 `Resume` 均识别、校验并复用同配置的成功分片。重复启动不会覆盖已提交分片；
同一输出根目录由内核文件锁保护，第二个写入者会明确失败。完成后再次运行仅校验，
不重新推理。不要删除 `build.lock` 来绕过另一个进程。锁文件留在磁盘正常；进程死亡会自动释放内核锁。

启动脚本固定使用已经安装的 `.local/embedding-runtime/Scripts/python.exe`，不要求激活环境、
下载模型或升级依赖。Python 模块的全库模式另有 `--operator-start-full` 门槛；
脚本仅在手动选择 `Run`/`Resume` 时传入该参数。默认脚本模式为 `Preflight`。

## 运行生命周期、进度与日志

支持的入口是上述**独立系统终端的前台运行**。由操作员手动启动后不依赖 Codex，
可以关闭 Codex；**请保留启动 PowerShell 窗口**。关闭该终端会中断任务，用 Resume 继续。
本轮宿主内的后台脱离探针没有保持工作进程，因此不提供未经验证的 `-Detach` 承诺或启动命令。

Python 运行期间通过 `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` 请求抑制自动空闲休眠，
退出时释放请求。它不更改系统电源计划、不防止手动睡眠/关机、合盖策略、断电或系统重启。
显示器仍可自动关闭。不要主动让机器睡眠；这些情况发生后应重新手动 Resume。
没有安装开机自启、计划任务或长期监控服务。

输出根目录固定为项目下：

```text
data/retrieval/p04-local-qwen3-1024-20261102-v1/
```

终端显示已保存数量、总数、百分比、复用/新写入、累计活跃时间、实测速率与 ETA。
长分片编码期间每约15秒记录未提交进度；只有成功保存分片后，完成数量才增加。
ETA 根据实际活跃耗时计算，包含已记录的加载/扫描/恢复开销，初始和恢复阶段会偏保守；
同时保留纯编码速率/ETA，均不保证结束时刻。进程被强制杀死前尚未落盘的末段耗时可能未计入。

另开一个 PowerShell 窗口查看实时日志：

```powershell
Set-Location 'D:\GenshinChronicle'
Get-Content .\data\retrieval\p04-local-qwen3-1024-20261102-v1\events.jsonl -Tail 5 -Wait
```

`progress.json` 是最近成功检查点及 RAM/VRAM 统计；`events.jsonl` 持续追加进度、OOM 缩批、失败和完成事件；
`last-error.json` 记录可捕获的失败/中断与堆栈。强制断电/杀进程无法保证写出新的错误记录，
应以最后有效分片和 Resume 校验为准。预检查在正式执行前发生的失败会打印到终端；修正资源/路径后重试。

## 输入、完整原文与身份

复用正式 RU：
`data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json`。
其 SHA256 为 `dc6bbd30cc6fbc83fa38132085fb8550f20322082467fe24aadcb4239ca78673`，
RU build 为 `49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998`。
程序流式核对压缩 RU、skip ledger、clear failure ledger 的文件摘要和数量；
最终再次流式核对原文行与来源，不重投影、不改动 Canonical/RU。

本轮扫描全部 **535,802** 条原文，用固定官方 Tokenizer、特殊 Token 计入长度、无截断：

| 指标 | 结果 |
|---|---:|
| 总 Token | 97,644,971 |
| P50 / P95 / P99 | 44 / 650 / 844 |
| 最长 | 1,189 |
| 超过现有 2,048 上限 | 0 |

因此本次直接编码完整 `retrieval_visible_text`，无需创建窗口或改变一 RU 一向量关系。
全库 Token 审计写入 `length-audit.json`，绑定 RU manifest 和编码空间；重复运行可复用匹配审计。
每个实际编码批仍重新计数，超限即记录并停止；编码后还检查截断标志和实际 Token 数。
将来出现超限的新快照需要另定窗口方案，本程序不会静默缩短原文。

编码复用既有 `local_qwen3_embedding.py`：Qwen/Qwen3-Embedding-0.6B，固定 revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，CUDA FP16、1024维 FP32/L2、SDPA、最后有效 Token 池化。
CPU FP32 是另一空间，不自动回退。模型所有安装文件摘要与固定依赖版本在加载时复核。
Query 保持原指令模板；模型、文件、精度、设备、Tokenizer、长度、池化、指令和运行时绑定为同一空间身份。

## 构建、恢复与验收机制

- RU 和向量按最多1,024条组成分片；分片内部按长度稳定分组，最多8条/微批、填充后最多4,096 Token。
  输出散射回原顺序。4,096 是微批预算，单条合同仍为2,048，不能据此截断原文。
- 仅 CUDA OOM 会将当前未完成微批减半重算；单条也 OOM 则停止。无 CPU、线上或量化后备。
  普通错误明确失败，不无限重试。每个成功微批的实际分组写入收据。
- 分片向量/行映射先原子写入并同步，`receipt.json` 最后提交。已提交文件的 SHA256、行数、
  FP32/1024、有限值和 L2 会在恢复时校验，并与原 RU 文本/来源哈希逐行比对。
  损坏的已提交分片停止，不悄悄覆盖；仅当前未提交分片可能重新计算。
- `build-plan.json` 固定源 manifest/hash/build、模型编码合同、批次策略和目标范围。
  源/配置不一致拒绝继续。不要在运行中编辑编码器或修改配置；新的编码合同应使用新版本目录。
- 全部成功后由分片组装 `vectors.f32.npy` 与压缩行映射，使用 mmap/小块处理，
  不一次载入全库矩阵；原始 RU 也始终流式读取。已提交分片保留用于恢复。
- 独立验收检查完整覆盖、原顺序、原 RU ID、文本摘要、来源与整行身份、维度、NaN/Inf、L2、
  文件大小/摘要和索引身份；再次核对源文件未漂移。通过后才原子发布 `metadata/manifest.json`。
  未完成/失败分片无法被 Dense loader 当成完整索引，smoke scope 也不能进入正式后端。
- 全库构建不会自动修改 RAG 配置、覆盖2048维历史索引，或删除任何历史资产。

完成时终端打印 `COMPLETE`、覆盖数、路径与验收状态；详细结果为 `report.json`、`validation.json`。
可再次独立验收，不运行模型：

```powershell
Set-Location 'D:\GenshinChronicle'
pwsh -NoProfile -File .\scripts\retrieval\build-local-embedding.ps1 -Mode Validate
Get-Content .\data\retrieval\p04-local-qwen3-1024-20261102-v1\report.json
Get-Content .\data\retrieval\p04-local-qwen3-1024-20261102-v1\validation.json
```

要求 `status=complete`、`coverage_rows=535802`、validation `status=PASS`，且存在完整 manifest。
校验不等于检索质量验收；仍需比较问题集。

## 本轮真实测试与资源估算

全量扫描只运行 Tokenizer，没有全库模型推理。真实推理分别有 CLI 硬上限512、1,024，均已完成。
选取源行均匀间隔样本并加入最长的16条，不使用问答标签；第二组使用今晚默认的1,024条分片策略。

| 测量 | 512 RU（128条/分片） | 1,024 RU（默认单分片） |
|---|---:|---:|
| 编码 Token | 105,955 | 204,209 |
| 成功分片纯编码时间 | 24.14秒 | 37.69秒 |
| 纯编码吞吐 | 21.21 RU/s | 27.17 RU/s |
| 峰值进程工作集 | 约2.84 GiB | 约2.83 GiB |
| Torch 峰值分配 / 保留 | 约1.25 / 1.40 GiB（恢复进程） | 约1.40 / 1.76 GiB |
| 完成验收 | PASS | PASS |

512组先在256条处显式暂停，再强制终止自己的恢复进程（第三个收据提交后），最后恢复完成；
三个成功分片均复用，最后只计算剩余128条。OS 锁随进程死亡释放，未留下无法恢复的 PID 门槛。
1,024组单次完整步骤96.27秒（含加载、遍历分散的原文、保存和最终验收）；不能把该总时长按分片数
直接相乘，因为小样本每次均遍历同一个全库压缩输入，完整构建只顺序扫描。
512组还包含首次完整 Token 扫描和多次重启，因此它的总时长更不能代表全库单次吞吐。

第二组外部 `nvidia-smi` 采样整卡峰值 **3,585 MiB**、最低空闲 **378 MiB**，包含桌面/其他程序；
与 Torch 进程分配口径不同。4GB RTX3050 Laptop 显存余量有限，应避免同时运行其他 GPU 工作。
未实测整夜热稳定性；OOM 减半路径已用离线故障注入验证，真实小试没有触发 OOM。

另从1,024组取两个原微批复算（上限16、实际9条，含最长1,189 Token），相同设备/配置/批分组
最大绝对差为0。离线小夹具还验证中断/不停机两条构建路径生成相同索引身份；
不宣称跨设备或不同批分组逐位一致。

512小索引经现有 Dense mmap 和 field-aware BM25/RRF Hybrid Top20 加载，重复候选一致。
隔离本地 Query 编码含进程/模型加载约14.31秒，退出后在原 BGE 环境对两个候选重排约27.91秒（含BGE冷加载）。
这是小索引与分时模型生命周期检查，不是 Top500 全库端到端延迟或答案质量结论。
网络连接在本地程序与测试中被阻断；没有创建线上 Embedding 请求。

基于两组实测吞吐，535,802条纯编码约 **5～7小时**；加上 IO、校验、负载和温度差异，
先为完整流程留 **6～10小时**，实际可能更久，进度由实测速率更新。
全库平均约182 Token/RU，试验约199～207，因此没有用短句的峰值吞吐直接外推。

单份向量矩阵 **2,194,644,992 bytes（约2.04 GiB）**，分片与最终矩阵同时保留约4.09 GiB；
按实际压缩行映射和日志外推，新增常驻数据约4.5～5 GiB，建议至少8 GiB空闲用于暂存/恢复。
预检查保守要求约7.09 GiB空闲（两份向量加3 GiB余量），不把旧模型/索引算成新增空间。
准备时 D盘约31 GiB空闲。RAM实测约2.8～3.0 GiB进程工作集，建议给系统留出至少4 GiB余量；
预检查最低门槛为可用RAM1.5 GiB、空闲VRAM1.5 GiB，实际GPU推理仍可能因竞争失败并保留进度。

证据根：`data/retrieval/p04-local-qwen3-preparation-20261102/`、
`p04-local-qwen3-smoke-20261102-r1/`、`p04-local-qwen3-smoke-20261102-r2/`。
小索引是独立导出，不能替换全库，历史909条试验保持不变。

## 完成后比较、切换与手动回退

> 历史准备记录。2026-11-03 用户选择已完成的本地索引作为默认。
> 当前选择、独立 70 题评审及回退见 [当前阶段](../../current-phase.md) 和
> [70 题对照](p04-embedding-70q-20261103.md)。

当前 **检索默认仍为线上2048维**。全库完成并校验后，才在独立启动会话中选择本地成对配置：

```powershell
Set-Location 'D:\GenshinChronicle'
$env:GENSHIN_EMBEDDING_PROFILE = 'local_qwen3_1024'
$env:GENSHIN_LOCAL_DENSE_MANIFEST = (Resolve-Path .\data\retrieval\p04-local-qwen3-1024-20261102-v1\metadata\manifest.json).Path
```

随后按现有本地UI启动方式运行问题集。`rag/config.py` 的配置及缓存键同时绑定索引和查询路径。
本地查询必须使用相同模型/精度/运行时/指令身份，禁止裸向量或1024/2048混用；缺资源即失败。
Embedding 在独立运行时的短生命周期进程中执行，退出后再运行 BGE，不将两套 Transformers 混装。
已有 BM25、Hybrid、BGE、Assembly、Evidence Packet 和原文引用策略保持原样。

使用相同的70Q/剧情材料中的代表问题，先比较新旧召回、证据和阶段耗时，再决定正式启用；
评审参考答案仍不进入运行请求。切换前的范围只授权离线或本地 evidence-only 测量，
线上对照/Generation付费测试须有新的有限授权。

明确回退到旧索引和线上查询对：

```powershell
$env:GENSHIN_EMBEDDING_PROFILE = 'online_qwen_2048'
Remove-Item Env:GENSHIN_LOCAL_DENSE_MANIFEST -ErrorAction SilentlyContinue
```

这只是配置操作，不发出 API；实际运行旧配置仍可能调用线上查询 Embedding，不能据此视为已获付费许可。
重启UI会话可确保释放旧 prepared state；缓存键也会区分两种配置。

用户已选择 **DeepSeek 为V1默认问答**：未设置 `GENSHIN_GENERATION_BACKEND` 也选择DeepSeek，
`bailian` 是显式可选回退。离线测试覆盖默认值和不自动跨模型回退。
已知输出 Token 超限问题原样保留，本轮未处理或重新付费测试；仍需配置新的有限调用预算/授权后才能生成新答案。
