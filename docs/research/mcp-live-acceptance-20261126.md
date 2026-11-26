# GC 实时 MCP 验收

2026-11-26。使用现机的旧扁平语料与封存的增量 Release，固定 S0 和 `bge_structural`。实测通过本地进程落盘，复用已安装的 Dense、词法、重排和 Generation 环境。[实时合同与配置](../phases/mcp-live-v1.md)

## 工程验收

- 旧版真实首查问须弥教令院学派，取得 100 个排序候选和 4 个包内引用。随后自由补查因论派、知论派及其别名，复用来源 Reader。两轮分别耗时 127.1 秒和 76.8 秒，来源 Reader 冷准备 46.8 秒，续轮在该阶段使用已验证 Reader。首查与补查文本不同，分段结果用于判断缓存开销。[首次本地运行](../../data/generation/mcp-live-v1/local-flat-baseline-01/result.json)
- 语料线程用 `vcr-1126` 的已封存工件完成旧版读取、新版绑定、跨版引用拒绝及回滚演练。新增、修改、复用均以实际来源核对，六个移除内容 ID 均从新版清单和索引行排除。复用样本的当前 Raw 是 2026-11-24，有效 Raw 是 2026-08-24。实时服务复用同一候选 Release 摘要，并补查菜单和列表变化的两项来源绑定和原文。五类来源变化及六项移除完成工程见证。Extended 的逐题召回和语义范围仍按原记录保留。[绑定验收](storage-and-release-readiness-20261126.md) · [机器结果](../../data/retrieval/release-binding-20261126-r1/result.json) · [两项变更回读](../../data/generation/mcp-live-v1/release-change-readback-20261126/result.json)
- 实时专项夹具验证自由补查、父查询与句柄归属、空结果、来源篡改、写事务取消及失败重试、原请求重放、切换时旧研究继续按原绑定回读，以及跨研究证据拒绝。冷发现测试约束零网络与零派生进程。54 项相关测试通过，其中 1 项在含 tokenizer 的已有运行环境中单独执行。既有冻结回放保持原代码及资产绑定。[实时专项](../../tests/generation/test_mcp_live.py)

## 安装宿主

DSH `0.2.2-rc.5` 使用安装包自带的 MCP 桥接器和工具注册表。新版首查和自由补查分别耗时 124.7 秒、106.4 秒，各 100 个排序候选。首包 34,570 字符，最终包 24,688 字符，两包各三页完整取回。最终包的 `E01` 包含角色故事的旧称，`E04` 包含伊涅芙对来源的第一人称自述。回读保存完整父级，旧包和不存在的引用被拒绝。宿主取消后没有已提交结果。重连成功原样恢复一个已提交的组包操作。[DSH 实机记录](../../data/generation/mcp-live-v1/dsh-release-live-03/dsh-client.json)

Codex `0.171.0-alpha.13.2` 的隔离 app-server 发现 11 个工具。旧版和新版真实首查分别耗时 121.0 秒、100.5 秒。新版首包 40,505 字符，完整父级 261,085 字符，分别用四页和 22 页重组并校验摘要。跨版句柄与包地址被拒绝。随后把新研究的启动参数拨回旧版，两版研究仍能按各自版本重新回读，已提交的新版首包原样恢复。两个宿主记录的新版实现绑定相同。[Codex 实机记录](../../data/generation/mcp-live-v1/codex-release-live-02/codex-client.json)

## 性能与恢复

- 旧扁平首查的主要开销为来源 Reader 准备 46.8 秒、Dense worker 准备 30.7 秒、重排 worker 准备 14.4 秒。三类 worker 在各自阶段退出，本机 GPU 锁覆盖实际计算。当前服务只保留一个语料的来源 Reader，低内存时可释放后重新绑定。新版首轮在召回阶段结束后才加载主进程来源 Reader。
- 新版词法 worker 逐路打开和关闭 Reader，复用原倒排。同题核对微、宏各 50 条，行号、顺序和分数逐条相等。对照实现的已存 RSS 测点为 4.54 GiB，逐路实现两个采样为 2.67 GiB 和 2.59 GiB，释放后为 0.71 GiB。[同题对照](../../data/generation/mcp-live-v1/local-resource-probe-20261126-r2/result.json)
- 同一 Dense worker 内两次执行同一查询，结果完全一致，准备 30.46 秒、冷请求 8.88 秒、暖请求 5.87 秒。同一 BGE worker 的两次评分及各行 Token 数完全一致，准备 15.28 秒、冷请求 7.51 秒、暖请求 4.68 秒，其中冷模型载入 1.81 秒。采样机器显存约 4 GiB，两者各自运行时 CUDA 保留量峰值约 1.10 和 1.30 GiB，BGE 的进程峰值工作集约 4.03 GiB。两进程均确认退出。实际使用保留来源 Reader 复用，Dense 与 BGE 按阶段释放。这是有限的冷暖与资源对照。[分段及资源记录](../../data/generation/mcp-live-v1/local-resource-probe-20261126-r2/result.json)
- 独立 stdio 复测在真实词法 worker 已启动后取消补查。约 7.0 秒后恢复查询报告未提交和 `TimeoutError`，原研究仍只有已经提交的两组查询。[运行中取消记录](../../data/generation/mcp-live-v1/local-cancel-worker-20261126/result.json)
- 初次 DSH 真实查询触发既有 RAM 保护。复测记录了可用内存、每个阶段的资源和实际重跑结果。Codex 默认沙箱中的 SQLite 迁移失败，改用隔离状态目录下的正常宿主进程。Codex 的子进程环境需透传 Windows 架构变量，重排 worker 继续按原合同验证解释器。验收的模型端点是本机占位地址。

## Agentic RAG

P3 的历史压力题离线覆盖、证据压缩增益及四道真实配对题预注册单独保存在评测报告。宿主原生工具能否交付证据、宿主实际送给模型的内容和答案的语义正确性各自记录。真实配对等待许可登记。[离线对照与预注册](mcp-agentic-rag-evaluation-20261126.md)
