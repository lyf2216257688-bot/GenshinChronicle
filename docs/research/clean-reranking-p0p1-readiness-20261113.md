# Clean-based Reranking P0–P1 核查与 P2 手动运行交接

2026-11-13。**历史准备检查点：P0–P1 有限工程核查通过，当时P2等待操作者启动。**
之后操作者已完成70题运行；当前结果与暂停点见
[P2对照报告](clean-reranking-p2-comparison-20261113.md)。下文保留当时交接事实与命令，勿重复启动已完成的任务。
当前授权由 [current-phase](../current-phase.md) 管理；技术合同仍是
[Clean Reranking V1](../phases/clean-reranking-v1.md)。本工作单元只补齐准备与有限验证，
不代跑70题，不推进重排 P3/P4，不选择模型，不提交/推送 Git，不切换 Serving。

## P0–P1 完成情况

| 阶段 | 实际核查及交付 | 结论 |
| --- | --- | --- |
| P0：合同与固定输入 | 输入封存回执、70份题目文件、P4来源绑定、Chunk身份、完整 retrieval_text、两模型长度审计与当前计划哈希 | PASS，70题、每模型6,963条；没有重新召回或修改文本 |
| P1：本地适配器与环境 | 两模型权重、tokenizer、自定义代码文件哈希与原计划一致；Python3.14.5、Torch2.11.0+cu128、Transformers4.57.3及其余绑定依赖一致 | PASS，离线、FP16、CUDA、eager、batch=1 |
| P1：既有真实 Smoke | 对原短/长工程输入的4组真实结果再次独立 Validate，复核原始回执及CUDA分配记录 | PASS，原Smoke继续有效，无需重跑 |
| P1：实际最长配对 | 发现原工程问题最长1,129 token，补测冻结Q041原问题与原候选的1,150-token完整配对，两模型各一次 | PASS，未截断、未改问题、未回读Parent；不计入70题评分 |
| P1：失败与恢复 | 补充超长输入显式失败、模型加载OOM传播、题间中断后已完成回执不变且Resume仅处理剩余题目的夹具 | 相关40项测试通过；OOM为故障注入，未故意耗尽真实显存 |
| P2：运行与报告入口 | 原Run/Resume/Validate/Report入口可用，自动报告提供S0/BGE/GTE Top5/10/20、完整排名引用、耗时与资源原始记录 | 已准备；完整70题评分与质量审阅均未执行 |

本次只改变测试和当前文档，没有修改冻结的模型评分实现、运行配置或输入；原计划和Smoke绑定仍匹配。
完整运行目录没有已完成评分组及最终complete.json。结构处理和MMR不参与P2评分。

输入长度均为完整问题、候选文本和特殊token的实际配对长度：两模型均25–1,150 token，
每模型合计2,467,419 token。2048只是本批公平比较预算，不是后续永久上限。
输入含4,916次micro成员关系、5,266次macro成员关系；同一候选可属于两者，不能相加当作候选总数。

## 有限最长配对 CUDA 结果

两模型处理同一原问题和同一完整候选，均输出有限标量logit。
以下为本次单条、冷模型首次评分的工程观测，不能用于判断相关性优劣或推算完整70题吞吐。
加载时间仅为模型加载/转GPU区间，不含整个工作进程启动、依赖导入和文件校验。

| 观测 | BGE | GTE |
| --- | ---: | ---: |
| 完整配对token | 1,150 | 1,150 |
| 模型加载秒数 | 4.040 | 0.782 |
| 该次评分秒数（不含加载） | 0.666 | 0.419 |
| 评分阶段GPU峰值allocated（GiB） | 1.201 | 0.675 |
| 评分阶段GPU峰值reserved（GiB） | 1.227 | 0.699 |
| 进程峰值working set（GiB） | 2.706 | 1.558 |

CUDA allocated/reserved为PyTorch分配器峰值，不是系统GPU总占用；进程峰值由Windows报告，
包含启动以来已观测的峰值。系统可用RAM为离散采样，不宣称连续峰值或最低可运行门槛。
4GiB可用RAM、2GiB空闲显存仍是准备建议，当前running RAM floor保持null；
异常/OOM必须失败并保留已有题目回执，不自动切CPU、缩文本或减少候选。

本次依据与产物：

- `data/retrieval/rerank-p0p1-review-20261113/`：封存的核查报告、边界计划、原配对样本、两模型实际结果和worker日志。
- `data/retrieval/rerank-v1-input-20261113/`：冻结输入，未修改。
- `data/retrieval/rerank-v1-70q-20261113/`：原Preflight计划和全量长度审计，等待Run。
- `data/retrieval/rerank-v1-smoke-20261113/`：既有4组真实Smoke与本次独立复核。
- `.local/runtime-py314/rerank-p0p1-tests-20261113.log`：40项相关夹具测试日志。
- `.local/runtime-py314/review-rerank-p0p1-20261113.py`：本次有限核查/边界Smoke执行记录脚本，哈希已记录在边界计划中。

输入complete SHA256：`23dedd4e63f928cf6ee953436cb7e92cbe0594c3015ea6bdd224211d786d273a`。
70题plan binding：`6360c2c0b1a896dbb336b26e32e5bce7e35c05e1f29f9f79dce324545137fb8c`。

## 操作者的 PowerShell 7 命令

正常运行，成功后自动执行独立校验与机械报告生成。三步遇错即停止，勿同时启动两个实例：

```powershell
Set-Location D:\GenshinChronicle
& {
    $ErrorActionPreference = 'Stop'
    ./scripts/retrieval/run-clean-reranking.ps1 -Mode Run
    ./scripts/retrieval/run-clean-reranking.ps1 -Mode Validate
    ./scripts/retrieval/run-clean-reranking.ps1 -Mode Report
}
```

内部依次运行BGE、释放其进程、再运行GTE，每模型完整评分6,963条。冷启动与评分耗时分别保留；
逐题回执及原始telemetry保留tokenization、forward、写出耗时、GPU峰值、进程内存及系统可用内存采样。
失败时保留所有产物，不删除锁文件或评分回执，也不重跑Prepare/Preflight/Smoke。

仅在Run中断后，关闭原失败进程，再使用Resume：

```powershell
Set-Location D:\GenshinChronicle
& {
    $ErrorActionPreference = 'Stop'
    ./scripts/retrieval/run-clean-reranking.ps1 -Mode Resume
    ./scripts/retrieval/run-clean-reranking.ps1 -Mode Validate
    ./scripts/retrieval/run-clean-reranking.ps1 -Mode Report
}
```

如果Run已经完成而只有Validate或Report失败，仅重跑失败的后续步骤，不重新推理。
最终机械报告位于运行目录下`reports/comparison-*`；命令会输出实际目录。
Codex不启动、保活或轮询操作者的70题任务；操作者通知完成后，再读取已有产物。

## P2 完成口径与暂停点

完整P2需要两模型各70题/6,963条、140个题目—模型组的独立校验通过，随后只读逐题复核。
自动Report仍标记`NOT_REVIEWED`，不能把命令完成或工程PASS写成质量验收。

复核沿用原Plan：相对S0及BGE/GTE之间记录提升、持平、退化、不确定，列明关键证据前移/后移、
进入/退出Top10/20及可核实替代线索；比较人物关系、事件先后、否定、条件及宏微长短文本。
每个实质判断保留原文、来源和排名；既有片段最佳排名只是诊断，不当作整题正确率或完整相关性金标准。
没有确认的证据缺失、题目预设或来源性质继续标记不确定，不为凑结论补做召回。

对照报告汇总实际加载、tokenization、forward、写出及逐题耗时中位数/P95，
区分首题冷模型、后续稳态和发生恢复的进程；保留GPU allocated/reserved、进程内存和RAM采样边界。
报告提出模型选择建议，但**基础模型由操作者裁决**。报告提交后暂停；不执行Analyze，
不推进结构/MMR消融、候选交接P3/P4，不修改Serving或自动提交Git。
