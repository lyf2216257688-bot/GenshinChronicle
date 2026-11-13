# P4 召回实验准备与手动运行说明

> 历史准备检查点（2026-11-12）：下文“尚未运行”“Codex在此停止”等描述均属于当时的准备工作单元。
> 操作者后来已完成70题及独立Validate，2026-11-13又完成已保存结果的轻量审查；见
> [质量审查](recall-p4-70q-quality-review-20261113.md)。当前状态与下一步只由[current-phase](../current-phase.md)管理。

2026-11-12。[合同](../phases/recall-experiment-v1.md)拥有执行/数据协议；
[current-phase](../current-phase.md)拥有授权。工程入口已准备，可由用户开始手动预检和短测。
**真实单题 Smoke 尚未运行，不能声称全量实验运行环境已经验收通过。**

## 完成与限制

已实现 S0/S1/S2 的同命中对照、宏微重合/独有/Dense覆盖、来源范围诊断、F0/F1控制、
断点续跑、独立重放校验、成本记录和实际原文报告。无新增依赖，不改旧 S0 runtime、
selected配置、索引/模型、Raw/Clean/Chunk、Serving或生成/重排分支。

实际 Preflight 在系统可用内存约1.086GiB时被2GiB启动保护拦下；尚未打开模型/执行
检索，没有正式实验计划、work检查点或complete.json。该次失败日志保留：
`data/retrieval/p4-preflight-console-r1.log`及默认输出根的 runs/preflight-*/。
随后用户说明桌面软件占内存，并指定自己在PowerShell启动；Codex不再尝试真实预检/推理。
本轮没有完整70题、真实工程问题检索、索引重建、付费调用、Serving切换或Commit/Push。

## 实际输入与实现取舍

- 问题：`data/retrieval/index-benchmark-v1-70q-raw-20261110/question-set.jsonl`，
  Q001–Q070，SHA256 `a66e270058bb3b56f1a59252634a0e36188e669ed9d12311b284a4c7c3455df2`。
  旧 `.local/p04-rag-m2/` 副本已不存在，因此绑定保留的实际文件，不重造问题/答案。
- Dense/BM25F：仍由 `config/index-selected-v1.json` 选择，正式manifest哈希不变。
- Sparse：`data/retrieval/bge-m3-sparse-v1-20261112-ready/`，manifest SHA256
  `c6897d8b1fc7289fcd26bf0c5d98cb35ab7f5ad8abfb31b0a0b8da8a599f937b`；
  119,557条、468片、10,015,176 nnz、0空向量；独立Validate报告与此manifest匹配。
- 共享输入manifest：`data/retrieval/index-benchmark-v1-20261109/input-attempts/export-1794216082672799100-b96c140c/bundle/manifest.json`。
  input_bundle_id为`07aa27f71c6187f489d57f8255281e20847f3bb3de3ec1c0053324eab2d6fb95`；
  104,879 micro、78,658 macro，双路共享63,980条，不重复算独立Chunk。
- 模型继续使用 `.local/rag-index-prep-20261109/models/bge-m3` 固定主干/head/Tokenizer。
  神经/协调程序为既有candidate-runtime；BM25F为既有bm25f-runtime；不统一/升级环境。
- 三路每题各捕获一次原始双粒度Top50，再计算12配置：主S0/S1/S2 F1_max，
  每组合的F0、F1_rrf与关闭整理。每配置输出上限100，查看10/30/100。
  这些是可更换工作点，不是永久参数或胜出结论。
- S2词法总权重仍为一半；它会有更大的原始候选池和额外Sparse扫描成本，
  相同输出上限不意味着相同计算预算。相关的Dense/Sparse信号也不视作独立证据证明。
- 为节约常驻内存，神经与词法进程串行，Dense/Sparse共用模型但仍各做一次前向，
  不为少一次前向改写已验收算法。预检Tokenizer检查进程会先退出，协调进程不常驻Torch。

## 小型验证

54项不同测试通过：28项新增P4测试 + 17项原Clean召回测试 + 2项索引会话测试 +
7项模型接口一致性测试。BM25F Python3.14环境完整通过54项；实际协调Python3.12
另通过全部28项P4测试。未加载真实模型或查询真实全库。

- 逐题/组中断恢复不重算已提交组，原文件字节保持不变；半写尝试保留；损坏已提交组失败。
- 真子进程强制结束后，锁最终释放且完整组可复用。补测发现Windows venv转发进程退出
  与实际解释器释放内核锁存在毫秒级延迟，测试采用最多5秒等待验证最终释放；不删锁。
- 同Chunk跨粒度/通道只出现一次，保留命中；不设同Parent配额，不误合并不同条件。
- 词法独有继续核对Dense；空命中不补零；来源字符重合不标成语义等价。
- 全部原始候选来源核对、原文显示、12配置重放、报告产物/原文页检查；原文CRLF/CR
  与Unicode按UTF-8原字节保存和复制，不受Windows文本换行转换影响。
- 错表示/问题/映射、缺失字段、Dense深度不足、排名/分数异常、资产/代码漂移拒绝。
- Preflight不会派发查询，Smoke不能使用正式输出根；70题缺失匹配Smoke验收时拒绝启动。
- PowerShell解析及实际Status入口通过；只读核对实际保留题目与三个manifest/独立校验指针。

日志：`data/retrieval/p4-preparation-tests-final-r4.log`（54项）；
`p4-preparation-tests-candidate-final-r4.log`（28项）。更早日志保留，不覆盖失败证据。
静态资产绑定及最终代码/文件清单见`data/retrieval/p4-preparation-acceptance-final.json`。

## PowerShell 7 命令

在独立系统PowerShell 7窗口执行；可以先关闭桌面软件释放内存。终端必须保持打开，
任务不依赖Codex会话。不需要管理员权限。各步骤单独执行、检查成功后再运行下一步。

1. 环境、源文件、索引和70题长度预检，不推理/检索：

```powershell
Set-Location 'D:\GenshinChronicle'
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Preflight
```

2. 独立工程问题短测：1题、三通道、12融合配置，自动完成独立Validate。
输出到单独的`recall-p4-smoke-v1-20261112/`，不会运行70题。

```powershell
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Smoke
```

3. **仅在上述两步成功后**，显式启动完整70题：

```powershell
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Run
```

4. 在另一个窗口只读查看进度；实时日志查看可选：

```powershell
Set-Location 'D:\GenshinChronicle'
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Status
Get-Content -LiteralPath '.\data\retrieval\recall-p4-70q-v1-20261112\events.jsonl' -Tail 10 -Wait
```

日志查看窗口的Ctrl+C只停止查看。不要关闭执行Run的窗口。

5. 中断后等待几秒，再以相同Output续跑：

```powershell
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Resume
```

单题Smoke中断后可以重新执行`-Mode Smoke`，它会复用已提交组。
若已完成Smoke检索但需单独恢复/校验该根，也可执行：

```powershell
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Resume -Output 'data/retrieval/recall-p4-smoke-v1-20261112'
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Validate -Output 'data/retrieval/recall-p4-smoke-v1-20261112'
```

6. 完整70题结束后校验全部来源、排名、结构处置、诊断和报告回执：

```powershell
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Validate
```

Run自动产出首份报告；以下命令仅重新生成报告，不重新检索：

```powershell
& .\scripts\retrieval\run-recall-experiment.ps1 -Mode Report
$p4Root = Join-Path (Get-Location).Path 'data/retrieval/recall-p4-70q-v1-20261112'
$p4Report = Get-Content -LiteralPath (Join-Path $p4Root 'report.json') -Raw | ConvertFrom-Json
Invoke-Item -LiteralPath (Join-Path $p4Root $p4Report.report)
```

默认正式输出`data/retrieval/recall-p4-70q-v1-20261112/`。自定义正式Output时，Preflight、
Run、Resume、Validate、Report、Status均传同一个`-Output`；Smoke仍使用配置的smoke_output。
不要把Smoke放入正式实验根，也不要覆盖已有正式资产目录。

## 资源、恢复与阅读结果

建议启动前至少4GiB系统可用内存，空闲显存至少1.5GiB，D盘至少6GiB空闲。
保护起点是启动2GiB/运行0.75GiB；Torch准备后、加载模型前还需通过启动门槛。
这是已有4GB GPU/16GB机器上的保守保护，不是实测P4峰值或全程资源保证。
本轮尚无真实P4吞吐/峰值/ETA；不能把P3编码速度推算为70题检索速度。

OOM或资源不足会停止，无自动截断、降深度、跳题、换模型或付费调用。释放资源后续跑。
不手动删除锁、半写目录、complete.json或失败记录。完整组哈希损坏先停下检查，不覆盖修复。
代码/配置/题目/输入/索引/模型变化需新Output及预检/Smoke，不在已有实验里混用。

执行顺序：神经70题 → BM25F70题 → 逐题融合 → 报告；神经组同时保存Dense和Sparse。
进度中的比较数量在检索阶段可能为0；长单题期间事件不变不等于卡住。
每次运行独立保存`runs/*/stdout.log`、`stderr.log`、`failure.json/result.json`；
worker准备和错误保存在`workers/*/`，状态及事件在根目录。

报告`report.json`指向最新`reports/report-*/report.md`。它包含词法独有/Dense覆盖和前排
变化汇总，以及逐题JSON、完整名次/覆盖/成本TSV和至多6题原文页。
每题页面至多12条完整示例，不截断单条原文；其他候选都保留在该题documents.json。
样例按通道/候选名次选取，避免只因input_row靠前而选旧资料。

## 自我审计与反向审计

- **重复加分**：共享身份只输出一次；通道/宏微信号分别保留。F0的多粒度奖励是对照算法
  的显式行为，F1_max没有被锁定为正确答案；S2权重不因通道数增加而膨胀。
- **误去重/损失**：仅复用同观测、Parent、限定和语境严格包含的延后策略，不删资料。
  所有越过候选上限的处置保留；关闭整理和替换融合控制项显式暴露挤出变化。
- **覆盖**：同Parent不同有效线索没有配额；正文范围并集只是诊断。词法独有未被Dense
  在既定窗口覆盖，不说明Dense在全库中绝对找不到，也不说明该候选有效。
- **偏差**：历史70题、固定深度/权重、通道相关性、精选案例和缓存/冷启动都有局限。
  自动指标与原文保留，但不自动宣布有效证据、退化或赢家。
- **追溯/恢复**：实际输入、Chunk原文和所有来源行绑定；独立Validate逐候选回读并重放。
  检索、融合、报告分别有回执，故障不会抹掉已完成结果。
- **扩展/范围**：不改原S0 Serving接口，不新增规划器、查询改写、多跳、重排、Evidence
  Assembly或KG；本轮没有实际质量选型，用户保留最终决定权。

交付结论：**工程准备完成，可手动启动Preflight和Smoke；真实Smoke通过前，70题启动
条件尚未满足，Run会检查此门槛。Codex在此停止，不执行或监控用户任务。**
