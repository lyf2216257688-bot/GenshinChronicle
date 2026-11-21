# V2数量识别修复与全量产物恢复

> 2026-11-21 资料定位更新：本机缺少本文部分历史附件，原链接保留用于追溯。见[历史数据登记](README.md#历史数据登记)。

2026-11-07。按用户批准的最小修复计划执行，复用16,435份旧成果，补建7370与7233，
在新目录完成汇总及全量产物验收。[当前状态](../current-phase.md)负责授权与采用，
[V2合同](../phases/chunking-v2.md)负责接口。

## 1. 故障与修复

原`structure.py`第136至140行的奖励数量正则，物品名空白与前后`\s*`存在重叠，
外层重复使末项匹配失败时发生灾难性回溯。两份实际Clean的完整适配隔离测试均在3秒后超时，
抓取的调用栈停在该`re.fullmatch()`。

7370的奖励段原长255字符，去除首尾空白后228字符，末项含`配方：「纯洁之水」`的冒号。
7233原长245字符，去除首尾空白后211字符，末项`奇特的零件`缺少数量。
两段均不满足原完整数量语法，修复后继续保留全文。

匹配改为固定状态数的逐字符识别，时间复杂度为O(n)，保持原物品名字符集、1至40字符限制、
空白、乘号、Unicode十进制数字、小数、多项组合和栏目作用域。
准入版本、候选版本及balanced参数沿用原值。
现有汇总逻辑提取为内部函数，普通构建和一次性恢复共用；资料失败异常附带身份并保留原异常链。

其他正则未发现相同的指数回溯结构。对白条件识别存在多项式退化风险，
本次约6,000至9,000字符的构造样本耗时不足0.1秒，保留为后续针对性调查线索。
旧日志只含16,435行完成记录，原状态`error=null`，历史非零退出的具体终止原因仍缺少记录。

## 2. 恢复结果

最终目录为
[`clean-chunking-full-v2-20261107-recovered-r1`](../../data/retrieval/clean-chunking-full-v2-20261107-recovered-r1/)。

| 项目 | 结果 |
| --- | ---: |
| 资料 | 16,437 |
| 原字节复用资料 | 16,435 |
| 补建资料 | 2 |
| balanced候选 | 122,527 |
| 自然父层 | 214,870 |
| 完整源对白图 | 5,087 |

| 补建资料 | 候选 | 记录构建耗时 |
| --- | ---: | ---: |
| 7370《坏蛋们》 | 80 | 0.859秒 |
| 7233《旧世遗音》 | 33 | 0.281秒 |

两份并行补建总耗时2.344秒，包含Tokenizer初始化和汇总。
原122,414条候选加113条补建候选得到最终数量。
微观可检索107,459条，宏观可检索80,693条，其中65,625条同时服务两种粒度。

恢复期间直接读取旧分片，旧目录及原凭据保留。新目录拥有完整平面产物，
索引入口使用新根目录的`manifest.json`及`chunks-balanced.jsonl`，父层、图与准入文件同样位于根目录。
自然父层来源回读继续使用既有Clean源资料。

## 3. 来源与验收

原binding为`3f1abab6186697906536bda76682b1cc8a1f27a35b20184a477fd5d47d0c7ce4`。
补建binding为`69a3c71aed9257fb82b253da5d032b888195713c897a6c0877515cca4a8fdbf9`。
恢复binding为`adcc41701632aa2253a6481953776ea0e9fbf83df2c284e083c4698a0992d61b`。

完整输入与代码身份保存在完成manifest中。顶层代码哈希标明属于恢复协调程序，
每份资料的实际构建binding、凭据哈希和分片哈希保存在`record-origins.jsonl`。
旧实现及原状态在修改前封存于`evidence/original/`。

- 原16,435份凭据、82,175个文件和对应Clean文件均通过哈希及身份校验，汇总后再次复核。
- 结构适配AST核对限制为奖励匹配调用替换，记录构建和校验函数保持一致。
  26,941个相关源值的新匹配结果与旧准入结果一致。
- 112项回归通过，新增9项覆盖数量语法、实际故障文本、混合正文、长拒绝输入、
  binding/来源拒绝、原字节汇总、独立读取及失败异常链。
  原有串行/12路、中断续跑和汇总中断检查继续通过。
- 全量核对65,748个逐资料平面分片哈希、7,823,071条准入记录，以及1,151,346条正文/别名引用。
  两路原文覆盖、展示顺序、条件/语境引用和源图一致性均通过。
- 全部122,527条候选重新计算本地Qwen、BGE文档Token及固定问文对计数。
  微观最大384、宏观最大1,024，BGE文档最大1,135，满足1,792保护线。
- 完成manifest在验收后原子发布，`recovery-state.json`为`complete`，构建进程退出码为0。

机器记录见[完整验收](../../data/retrieval/clean-chunking-full-v2-20261107-recovered-r1/validation.json)、
[兼容性核对](../../data/retrieval/clean-chunking-full-v2-20261107-recovered-r1/evidence/compatibility.json)、
[来源清单](../../data/retrieval/clean-chunking-full-v2-20261107-recovered-r1/record-origins.jsonl)和
[独立读取复核](../../data/retrieval/clean-chunking-full-v2-20261107-recovered-r1/independent-read.json)。
独立复核在禁止打开旧Chunk目录文件的条件下读取新目录，检查完整平面产物及逐资料原字节。
结果为PASS，打开旧目录的尝试次数为0。

## 4. 检查命令与边界

PowerShell 7可直接检查完成状态并独立验证新目录。

```powershell
Set-Location D:\GenshinChronicle
$env:PYTHONPATH = 'src'
$env:PYTHONIOENCODING = 'utf-8'
$recoveryRoot = 'data/retrieval/clean-chunking-full-v2-20261107-recovered-r1'
Get-Content "$recoveryRoot/recovery-state.json" | ConvertFrom-Json
.\.local\embedding-runtime\Scripts\python.exe -m genshin_corpus.chunking.recover `
  --validate-only --output $recoveryRoot
if ($LASTEXITCODE -ne 0) { throw 'Recovered Chunk validation failed' }
```

本次只恢复既定V2产物。两段奖励混合格式按原语义保留，其中仍含机制信息。
检索效果及索引/召回采用由后续阶段验证；长期增量更新另行立项。
