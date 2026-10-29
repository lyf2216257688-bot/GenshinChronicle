# Phase05 Navi 离线知识生成环境

日期：2026-10-29。实现：`p05-navi-offline-1`；新增请求适配器：
`p05-navi-typed-source-runner-1`；Card：`p05-navi-card-1`；
索引：`p05-navi-typed-index-1`。当前授权以 `docs/current-phase.md` 为准。

本工作单元在主仓 `main@c8975a3` 上开始，工作区最初干净、相对本地远端引用领先17个提交。
未联网核实远端，也未 commit/push。本次建设完整工程链路，不执行全库生产。

## 工程实现与复用边界

`python -m genshin_corpus.retrieval.navi_offline` 提供准备、运行、恢复、审计、审核导出和
审核追加记录。实现位于 `src/genshin_corpus/retrieval/navi_offline/`，不改变生产选择器、
Evidence Packet、线上 RAG 或 KG。Grill Me 的仓库审查及只读复核识别了分层 Card 缺口、
旧小索引的全量内存路径、重复 RU 扫描和旧 runner 的富文本限制，本实现据此增加外围适配。

- 来源准备对 RU 及配套账本做流式哈希校验，复用既有 Canonical input/build identity；
  一次扫描完整 RU 文件，SQLite 仅作可重建的资料定位目录，无向量数据库或外部服务。
  对选中资料复用 `_validate_record` 完整重投影，逐 row 比较全部身份、文本、选择器与 lineage。
  资料内容缓存最多两份；RU 索引选取按来源和实际类型轮转，不全量保留 RU 对象。
- 富文本引证包含精确 Canonical 字符范围；结构化标量及对话图引证使用已验证 RU 文本的
  `[ru_start,ru_end)`，保留原始选择器、节点、角色和分支信息。不伪造 `normalized_text`。
  所有 Canonical 单元和上游跳过项保留清点；可用不等于已读，展示顺序不等于剧情顺序。
- 小资料直接形成文档 Card；超过预算按真实 section 生成阅读批次和章节 Card，再作明确
  的子 Card 聚合。开放问题产生可为空的线索层。Card 分离原文观察、解释、假说、问题和
  来源内查询；模型不提供引文。直接交付、继承范围、未读 RU 和部分区间分别保存。
  继承证据不计作直接阅读，任何摘要均不宣称全篇语义覆盖。
- Card 合同是新增接口；冻结 v0.3 只负责 Link 生成与定向审核。富文本兼容路径真正调用
  未修改的旧 runner；其他来源和 Card 使用独立版本适配器，复用现有 RawTee、SSE解析、
  全流检查和 usage 校验。Link 使用未修改的 Prompt、Schema、跨字段合同和剧情准入政策。
- 每个非空生成命题都有独立审核任务。生成原句、角色和哈希不可覆盖；审核阻塞也保留
  生成结果。小说、传说、证词和二手解读不能被程序升级为独立历史见证。

## 接口与操作点

Card envelope 包含 `card_id/source_id/record_id/canonical_sha256/level/parent_id/child_ids`、
`parts/evidence/coverage`、五种语义数组及状态。五种数组为 `source_observations`、
`interpretations`、`hypotheses`、`open_questions`、`queries`；每项均绑定交付 evidence ID。
`cards.SCHEMA` 是模型输出合同，来源引证和状态由程序管理。兼容导入入口会验证版本、
来源和精确范围；旧 G1 Card 不能仅凭相同资料 ID 自动获得新范围或新字段。

两类索引有各自 manifest、typed row mapping 和内容身份。RU 编码原文；Card 编码带标签的
稳定语义投影，排除评分、人工状态和发布状态。向量仍来自原固定本地 Qwen 合同。
每片最多4,096条，batch=2，FP32/L2、1024维；向量 mmap，分块精确查询并按 owner 最大
分数合并窗口。索引身份包含投影、来源、模型/设备合同及实际分片摘要。恢复逐片验证，
完成分片不重编码，未完成片另建尝试；查询期间检测文件变动。

原文和 Card 超窗时按实际 tokenizer/EOS 预算作连续字符窗口，全部范围可核算。
窗口不是引证身份。超限查询拒绝，不依赖静默截断。默认双路 Top20，以 `1/(60+rank)`
安排来源对候选；不以相似度裁定剧情。生成/审核只收到重新提取的双方原文、来源限定和
阅读范围，收不到 Card 摘要、发现分数或旧等级。单端正文预算8,000字符；遗漏明确记录。

默认上限：512 RU、64 Card、20来源对、32 Card 请求批次。真实验证使用更小的显式操作点。
上限可配置，超额记为延期，不宣称全库已完成。将来扩大运行需要新的规模与调用授权。

## 恢复、证据和审核

准备产物保存精确源码快照、来源快照及哈希。执行代码或输入变化阻止恢复；只读审计可以
核验历史源码快照，并明确报告是否与当前代码一致，不把历史结果冒称为当前版本执行。
已封口 job 可严格重放；pending/UNKNOWN 不自动重发，不更换真实账本绕过保护。
新适配器无自动重试；旧 runner 保留原有严格预发送证明政策。Mock 使用隔离账本。

raw 响应先于 SDK 解析保存，实际请求体、SDK事件、终态、失败及 usage 观察分别保留。
真实路线仅为既有 jizhi/DeepSeek Responses；SDK与传输重试关闭。未来调用必须另获授权，
同时绑定精确计划摘要与总 token 上限。未知 usage 阻止后续收费派发，其他本地工作可继续。
UTF-8字节预留是保守预算，不冒充 tokenizer 或账单。

`export-review` 保存来源快照、Card、Link、生成/审核原件和原始请求响应，带校验清单。
`record-review` 追加 reviewer、带时区时间、判断和理由，并绑定文件摘要；可通过 `--item-id`
额外绑定单张 Card/单个 Link 内容摘要。审核记录不修改模型原件，不激活 Navi。
同盘导出不是独立备份，Git忽略的运行档案也不会随代码提交而获得备份。

## 使用方法（仓库根目录，PowerShell 7）

```powershell
$env:PYTHONPATH = 'src'
$run = 'data/retrieval/my-navi-bounded-run'
python -m genshin_corpus.retrieval.navi_offline prepare --run $run `
  --sources 128 220 --max-ru 64 --max-cards 16 --max-pairs 1
python -m genshin_corpus.retrieval.navi_offline run --run $run --backend mock --embedding local
python -m genshin_corpus.retrieval.navi_offline resume --run $run --backend mock --embedding local
python -m genshin_corpus.retrieval.navi_offline audit --run $run
python -m genshin_corpus.retrieval.navi_offline export-review --run $run --out "$run-review-export"
```

上例实际运行本地 Embedding；Card/Link 为明确标记的 Mock，不产生真实剧情知识。
`--embedding mock` 仅供机械测试，向量身份为 `MOCK_NOT_QWEN`，不混入本地模型索引。
本地模型在既有 `.local/embedding-runtime/Scripts/python.exe` 中经持久进程管道运行；
控制进程需要已有 OpenAI SDK/httpx/jsonschema/numpy。未向已合格 ML 环境安装 SDK，
未改模型权重或原编码器。模型缺失、固定运行时不匹配或 CUDA 不可用会明确失败；CPU须
独立准备 `--device cpu`，不自动回退。

重放使用 `--backend replay --replay-root <旧运行的jobs目录>`；只接受实际 wire、来源和
请求体一致的完整档案，不修复旧坏 JSON，不将历史输出移植到不同交付范围。
不在本报告提供实际收费启动命令，当前授权未包含收费执行。

```powershell
python -m genshin_corpus.retrieval.navi_offline record-review --run $run `
  --artifact "$run/output/links.json" --item-id '<link_id>' `
  --reviewer '<reviewer>' --timestamp '<带时区ISO时间>' `
  --decision NEEDS_INFORMATION --reason '<具体待查证据>'
```

## 验证与 UNKNOWN

可移植测试基于既有小型 checked-in Canonical fixture，不依赖本地全库。新增21项覆盖
三层 Card、三种来源、错误绑定、索引隔离与精确排序、分片故障、封口前后恢复、审核阻塞、
重放目录、历史代码漂移和零真实 socket。连同 Prompt、runner、本地 Embedding、
Candidate V、E1和Phase04 RU合同，**179项分组回归通过**；随后补充的富文本原 runner
真实兼容调用测试亦通过，合计 **180项不同测试通过**。这不是全仓测试数量或全库语义验收。

最终运行：`data/retrieval/p05-navi-offline-20261029-r1/real-smoke-r3/`。

| 实测项目 | 结果 |
|---|---|
| 真实来源 | 飞天御剑、飞天大御剑、匣里龙吟、海螺的回响、哈玛瓦兰战记、普尔希娜论文集、龙蛇藏归辑录、白夜国馆藏、凯亚，共9份 |
| 完整输入核验 | 扫描535,802 RU；选中资料共有768 RU，逐 row 重投影绑定 |
| 本轮 RU 索引 | 254条：129富文本、121结构化、4对话图；其余514条按预算延期 |
| Card 索引 | 64张：9文档、39章节、16线索；实际318条文档输入=254 RU+64 Card |
| 本地查询编码 | 135条；无远程 Embedding |
| 候选及 Mock | 35来源对候选；20对进入64 Card+20生成+20审核任务，共104个完成档案、0失败 |
| 其他延期 | 134项 Card批次/Card数量/来源对预算处置，单列于deferred.json |
| GPU编码耗时 | 文档40.640秒、约7.82条/秒；查询22.568秒；模型加载46.028秒 |
| 总耗时 | 流水线172.909秒，外层网络保护计时177.028秒；不外推全库耗时 |
| 内存/显存 | Embedding进程峰值工作集2,151,542,784字节；CUDA峰值分配1,312,945,152、保留1,409,286,144字节；不是整机峰值 |
| 网络 | 父进程真实socket连接尝试0；子进程禁止socket连接；收费生成/API调用0 |
| 保存后验证 | 原始流、SDK、内容与来源只读核验PASS；无pending任务；当前代码与运行代码一致 |
| 完成恢复 | 1,089份准备/job/索引/output文件哈希不变；新增模型请求0、文档编码0 |
| 审核导出 | 1,079文件，校验和封口；尚无本次真实人工判断，所有产物INACTIVE/NOT_REVIEWED |

本次20个 Link 是明确的 Mock 待查命题，用于穿过生成和审核合同，**不是20条真实剧情发现**。
真实运行所选 pair 均使用typed-source适配器；原runner的富文本兼容路径由专门的真实SDK+
MockTransport测试验证，不能把这两项执行证据混称为真实提供商验证。

首轮 `real-smoke/` 在首批编码后遇新增Windows监测调用错误，未完成索引，原件与源码
完整保留；修复限于新监测适配，不修改已合格模型实现。`real-smoke-r2/` 仅完成准备，
后续恢复修复使其成为未执行的历史准备快照。最终r3独立运行，没有重发收费模型请求。

保护清单覆盖379份指定历史档案及冻结实现/合同，全部哈希不变；并非声称重新全量审计
了仓内所有历史实验。旧Qwen 535,802行/2048维回退索引的行映射及4,389,290,112字节
向量文件均重新核对manifest声明哈希，通过。未切换或清理任何旧索引。
核验原件：`tests.txt`、`audit.json`、`resume-verification.json`、
`production-fallback-verification.json`、`real-smoke-r3/embedding-metrics-000.json`及
`real-smoke-r3/network-guard.json`，均在本工作单元忽略目录内。

本环境的机械可运行性不等于新增 Card Prompt、typed-source生成路径或全库语义质量通过。
真实生成效果、误关联率、召回、与现有 Dense 的互补增益、严格代理Schema执行、未来实际
收费、全库耗时/磁盘与资源峰值仍需相应证据。完整运行前需要：确认资料范围与操作点、
有限真实模型验收、明确调用/token及规模预算、可用资源和档案独立备份；生产切换、发布、
KG和线上融合仍需分别授权。人工验收保持开放。
