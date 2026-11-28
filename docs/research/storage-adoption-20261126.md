# 2026-11-26 正式语料采用与磁盘清理

本次采用 `20261124` 的全建臂。活动描述符为 `data/retrieval/vcr-1126/full/indexes/release.json`，SHA-256 为 `468db257136b31fddb30bfc3cb3ed9e0ec9ee2eb67ccbca6b82436f9076bcd6f`。同版 `vc-20261124/full` 提供物理载荷。两版及其他历史 Raw 原位保留。正式工作点为 S0、`f1_max`、`bge_structural` 和 `gc-g5-official-4`，可显式使用同版 S2。[采用状态](../current-phase.md) · [活动发布目录](../../config/mcp-live-v1.json)

## 工程验收

- 沿用 Quick 双臂对照、36 份回执、120 项输出摘要及来源变化见证。临时屏蔽六个退役派生路径后，核对 1048 的双观测、1190 的修改、509698 的新增和六个移除 ID。新版查询脱离旧扁平索引的模型启动路径，已验证 Dense/Sparse 合同保持兼容。
- DSH 安装桥接器完成 S0 首查、自由补查、引用、分页和重连。Codex 安装宿主发现 11 个工具并核对活动绑定。两宿主的持久 GC 配置均切换至实时入口，下一次连接载入。个人聊天现场检查通过，S2 通过新版查询适配器获得 100 个候选并核对原文。
- 相关 Generation、聊天和 MCP 组合运行共 69 项，其中 68 项通过、1 项跳过。增量数组复用和恢复夹具通过独立数值测试。真实下一版尚未采集和构建。付费模型请求、整版构建及 Extended 运行均为零。[切换回执](../../data/retrieval/storage-adoption-20261126/cutover-accepted.json) · [清理后复核](../../data/retrieval/storage-adoption-20261126/postcheck.json)

## 存储与历史记录

D: 首末可用空间由 **30.92 GiB** 增至 **64.85 GiB**，净增 **33.94 GiB**。逐文件清理账本合计 **34.08 GiB** 分配空间。收尾盘点项目目录实际占用 **34.00 GiB**，路径逻辑合计 **37.57 GiB**。工作区卷的净增还包含验收和归档写入。

- 旧 Clean、旧 Chunk、旧扁平 Dense/BM25F/Sparse，以及新版重复增量臂均已移除。当前证据仍可读取早期有效 Raw。旧研究按原绑定归档，已保存的 Packet、原文与操作结果覆盖 20 个研究数据库、22 项研究。旧 Clean 清单单独保留。G7 继续保留题目、回答、排名、评分和原账目。
- PyTorch wheel 删除前核对本机 SHA-256，并确认原登记分发地址返回同长度的安装件。两套运行环境保留各自解释器和包版本，58 个同字节库文件在 D: 共享 NTFS 文件身份。两套环境的 `pip check` 和 CUDA 计算通过。升级时在独立目录准备新环境，避免就地修改共享库。
- 逐文件清单、SHA-256、分配字节、删除回执与实际卷余量保存在 `data/retrieval/storage-adoption-20261126/`。空间结果按 NTFS 文件身份去重，卷净增还包含验收和归档新增写入。[历史归档](../../data/retrieval/storage-adoption-20261126/history.zip) · [旧 Clean 清单](../../data/retrieval/storage-adoption-20261126/history-clean-metadata.zip) · [实测空间账本](../../data/retrieval/storage-adoption-20261126/final-totals.json)

下一次更新从活动 Release 读取上一版 Raw、Clean、Chunk、Dense/Sparse 和 BM25F 清单，用单臂入口生成新候选。删除上一版之前核对新版持有的继承引用。[增量合同](../phases/versioned-corpus-incremental.md) · [单臂入口](../../scripts/retrieval/update_corpus.py)
