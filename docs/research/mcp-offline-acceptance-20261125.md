# GC 专业证据 MCP 离线验收

2026-11-25，路线 B 的 P0 与 P1 离线部分。取 G7 的一题单查和一题有已登记补查的研究记录，固定 `p01eb_full_20260824`、S0、`bge_structural`、`gc-g5-official-4`。本次服务、直接对照和两个宿主使用同一目录与代码绑定 `7ea934a0d1df8ba21113f55df545ecbcddd8d4078fcf52020cdfef0b805fb934`。采用状态见[当前阶段](../current-phase.md)，输入路径和 SHA-256 见[冻结目录](../../config/mcp-offline-v1.json)。

## 直接调用与 MCP

| 题目与动作 | 已核对的工程结果 |
| --- | --- |
| Q001，教令院学派 | 首查 100 个候选的顺序、来源与句柄逐项一致。以历史上下文和预算重建首包，完整对象与封存 G5 首包相等，3 个引用均可回查。 |
| Q068，伊涅芙前身的称号 | 首查 100 个候选、完整首包和 4 个引用通过相同对照。沿原问题的证据句柄执行 `query-1`，补查的 100 个候选逐项一致。 |
| Q068，扩大回读并选证 | 用现有 Generation 的 `EvidenceReader` 与 `assemble` 在 stdio 外直接执行同组动作。逐字段比对补查后 Packet、首个补查候选的完整父级收条，以及指定该 fragment 后的最终 Packet。原文位置、源血缘、预算、全部候选的准入处置和引用表均一致。 |
| 恢复 | 关闭服务并新建 stdio 连接后，两题的初始操作都能按原研究和请求 ID 取回已提交值。重复提交返回原值，已被取代的 Packet 拒绝用于当前引用。 |

首包使用原 G5 上下文，后续对照的两侧使用同一试点研究上下文。已封存的排名提供经过校验的输入行，核读器按定位读取原 Chunk、Parent、图和 Clean。本次沿 CleanRef 核对了原文与 Raw 血缘，未重新读取 Raw 原始字节。新的检索、重排打分及生成调用均为 0。[直接对照与 stdio 验收脚本](../../scripts/retrieval/verify_mcp_offline.py) · [机器结果及完整摘要](../../data/generation/mcp-offline-v1/p01-parity/acceptance.json)

## 安装宿主的实测

| 入口 | 已观察到的行为 | 覆盖边界 |
| --- | --- | --- |
| DeepSeek Harness `0.2.2-rc.5`，Electron `44.7.0`，Node `24.21.0` | 直接载入桌面安装包内的 Cordis、工具注册表与 MCP 客户端插件。实际注册 10 个带命名空间的工具。Q068 首包的 31,152 个 Unicode 字符分 3 页回查，拼接结果与一次交付的原文完全相同。补查后新包的 E01 可绑定，旧包 E01 被工具执行器拒绝。 | 核对了宿主工具的原生文本投影和结构化结果。一个单独请求在发出取消后由宿主报告错误；重连查得无已提交结果并记录 `TimeoutError`。对另一个已提交请求，重建插件和 stdio 后可取回原结果。桌面聊天视图、模型上下文压缩和真实推理留待后续。 |
| Codex CLI `0.171.0-alpha.13.2`，app-server | 隔离配置、临时状态库和临时线程发现 10 个工具。`mcpServer/tool/call` 取回完整 Q001 首包，JSON 文本与结构化结果相等，E01 绑定到 Packet、fragment、receipt，E999 返回错误。 | 未启动模型回合。此次观测覆盖 app-server 直调；Codex 聊天视图、模型侧长度限制及 Codex 专属取消行为待测。 |
| MCP TypeScript SDK `1.32.2` | 使用已安装的 SDK 在 stdio 上完成发现、Q068 首查和补查，文本与结构化内容相等。 | 通用客户端独立互操作检查。 |

DSH 实测使用随程序附带的 `@deepseek-ai/dsh-mcp-client`，编译入口 SHA-256 为 `879b2364e28563626697dd5e3dcf64dcae61dd215dc8f21b283cb7616a672bde`。Codex 使用临时配置、本机占位模型地址及单独的 MCP 审计根。两次验收都没有改个人宿主配置。更换宿主版本应重做发现、长正文、错误引用和恢复验证。[DSH 脚本](../../scripts/retrieval/verify_mcp_dsh.cjs) · [DSH 结果](../../data/generation/mcp-offline-v1/dsh-p01-acceptance/dsh-client.json) · [Codex 脚本](../../scripts/retrieval/verify_mcp_codex.py) · [Codex 结果](../../data/generation/mcp-offline-v1/codex-p01-acceptance/codex-client.json) · [SDK 结果](../../data/generation/mcp-offline-v1/sdk-verified/sdk-client.json)

## 回归、运行资产和退出

- 离线夹具覆盖两种协议版本、空排名、正文分页、条件与父级结构、旧包和跨研究句柄、错误补查祖先、来源与审计内容篡改、并发争用、取消回滚和原请求重放。冷启动发现另以独立解释器拒绝派生子进程和网络连接，并检查发现结束时 `torch`、`transformers`、`jsonschema` 与证据适配器均未载入。9 项 MCP 专项全部通过。[专项测试](../../tests/generation/test_mcp_offline.py)
- 扩展回归覆盖证据组包、研究目录、G5 研究和本地聊天产品。该次运行含最初 8 项 MCP 专项，共 99 项，98 项通过，1 项按原设置跳过。跳过项要求显式开启本机 G3 封存路由测试。上述 9 项专项复验含后来补入的冷启动检查。worker 内存不足与退出在既有夹具中模拟，真实 GPU 资源释放尚待资源窗口验证。[组包与 worker 测试](../../tests/generation/test_clean_evidence.py) · [聊天产品测试](../../tests/local_chat/test_product.py)
- 部署单元是宿主启动的本机 stdio 子进程、Generation 解释器与已有的 `jsonschema`、冻结目录指定的索引清单和可按原路径回读的证据文件。现有清单包含绝对路径，复制 Python 包到另一台机器还需迁移并验证运行资产。当前每个服务进程独立复用已验证的清单、读取器和已登记排名。模型内存与多宿主索引共享的成本尚无本次测量。[离线合同与启动命令](../phases/mcp-offline-v1.md) · [工作台运行前提](../rag-workbench.md#运行前提)
- 停止该 stdio 进程即可退出试点，历史审计根可保留并按原版本复查。正式问答、索引及 Quick 使用各自原入口。本次单独只读核对 Quick 执行封条及其中 320 个文件，通过；未运行 Quick 或 Extended。[Quick 执行封存](incremental-launcher-readiness-20261125.md#执行封存)

## 留给下一阶段的问题

主机工具能交付证据，模型是否读到限定语、能否主动补齐关键证据，以及最终回答是否正确，仍需独立评测。后续有限真查询须记录冷启动、召回、重排、回读、组包各段时延与内存；先取得同一绑定下有效的排序和引用，再评价速度及常驻收益。新版语料接线的工程验收范围已在路线调整中修订，见[当前阶段](../current-phase.md)与[版本化合同](../phases/versioned-corpus-incremental.md)。

真实配对可沿用 G5 `-4`，固定同一语料、首查、排序和证据预算，允许宿主自行决定补查与停止。将调试用的 Q001、Q068 与最终盲评题分开。盲评预先登记必要结论、合格出处、反证及可接受的不确定性，逐题追踪“已排序、已回读、已入包、宿主投影、最终引用”。模型或可见工具不同的组单独报告。全题保留未交付和 UNKNOWN，再报告双侧可评价题的结论。先判断回答正确性和关键证据覆盖，再考虑时延和费用。真实调用量、费用上限及发布切换另行审议。[既有正式质量审计](clean-generation-g7-70q-quality-audit-20261117.md) · [既有配对限制](clean-generation-g5-evidence-evaluation-20261123.md)
