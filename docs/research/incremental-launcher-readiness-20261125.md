# 2026-11-25 整版运行准备与验收

运行前先复核 [机器资源与 Quick／Extended 边界](incremental-first-build-runbook-20261125.md)。以下命令从仓库根运行，正式输出为 `data/retrieval/vc-20261124/`。

```powershell
pwsh -NoProfile -File .\scripts\retrieval\run-incremental-corpus.ps1 -Mode Run -Suite Quick -SealSha256 34089f8d3ab8f64ebeb00b777a07fd244baa5c9bc60bddf03a3ea9b11f74cb67
```

在同一条命令上替换 `-Mode` 即可执行预检 `Preflight`、续跑 `Resume` 或完成后复验 `Validate`。继续做历史题、真实变化和 Packet 时指定 `-Mode Validate -Suite Extended`。若夜间一并执行，初次命令直接用 `-Suite Extended`，中断后续跑也使用 Extended。

## 执行封存

- [执行封条](../../data/retrieval/incremental-execution-20261125-v2/complete.json) 固定已审核源码、全部项目配置、启动器、输入清单、分词器、CUDA 设备与驱动及三套解释器身份。包记录包含安装版本和 `RECORD` 哈希，解释器与基础 Python 库有文件哈希。模型权重由对应运行器按原选择清单验签。
- 构建、查询及重排从封存源码导入。封存文件与运行时读取的本机配置持有 Windows 只读共享句柄。封条哈希须与启动命令匹配，续跑核验原运行身份。错误封条和过长的 Windows Clean 输出路径在派发前拒绝。
- [题集注册](../../data/retrieval/incremental-execution-20261125-v2/questions.json) 保留六道快速题并注册 G7 历史 70 题、11 个变化见证。已校验 70 份历史原始召回、排序和原始首包可按封存回执读取。新版 Extended 结果等待整版候选建立。

## 新版 Raw 独立派生

按资料规模、对白图及列表指针是否移动分层，从未变化资料中固定抽取 24 条。新 Clean 和 Chunk 只从新版 Raw 派生。逐条回读新旧的实际 Raw 证据，核对 Clean 文本、值和结构。栏目观测按实际消费的 ID、名称和父关系比较。Chunk 比较保留原文、顺序、条件、准入、边及作用域，对随观测派生的 ID 逐一建立双射，并按实际目标比较重叠边。通过 314 个候选、642 个父级、6 个对白图和 16,241 条准入记录的对照。这个结论覆盖注册样本。[独立派生报告](../../data/retrieval/incremental-new-raw-equivalence-20261125/report.json)

## 启动器实测

冒烟采用独立微型语料和执行封条。资源保护实测含低磁盘、低系统内存、低可用显存的启动阻止。运行中注入磁盘不足后，阶段被停止、子进程退出、暂停事件入日志，阶段完成回执保持缺失。Windows 封存文件写入探针被拒绝，错误 SHA 的正式预检在创建构建目录前停止。[资源保护记录](../../data/retrieval/incremental-smoke-guard-20261125/report.json)

通过实际 PowerShell 启动器测试四个模式。微型 Run 在首个向量阶段强制退出，进程树退出确认。Resume 保留先前 Clean 回执，接续两臂及一题 S0/S2 的双臂查询，约 228 秒完成。Validate 复验物理对照并复用查询回执。双臂首包各自核对原排序输入和引用绑定，正文、准入处置、预算与引用投影一致。运行元数据参与各自的 Packet ID。另有 9 项针对性回归通过。[冒烟报告](../../data/retrieval/incremental-smoke-20261125-r6/report.json) · [阶段日志](../../data/retrieval/vcs-r6/progress.jsonl)

前序冒烟发现 Windows 深层文件名和 Whoosh 短时目录共享锁问题。正式根使用短路径，预检核对最深 Clean 写入路径。词法桶发布在同一构建锁内对 Windows 短时文件冲突做有界重试，已发布目标仍拒绝覆盖。

正式封条包含 320 个文件、12 个外部输入哈希和 3 套解释器身份。实机只读预检通过，D 盘余量约 48.6 GiB、可用内存约 6.56 GiB、GPU 空闲 2,576 MiB。正式输出目录尚未创建。[正式预检日志](../../data/retrieval/incremental-execution-preflight-20261125.log)

用正式封存的 Extended 子程序单独试跑一题。历史侧取 Q001 的封存结果，新侧使用冒烟小语料，成功生成新旧召回、重排、Packet 及新引用校验制品。该试跑用于验证报告链，真实 81 案例的机械和语义结果仍在整版后验收。[单题 Extended 冒烟](../../data/retrieval/incremental-smoke-extended-q001-20261125/case.json)
