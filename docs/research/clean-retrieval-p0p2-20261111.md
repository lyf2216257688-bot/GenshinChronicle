# Clean-based Retrieval P0–P2 实施与有限验证

2026-11-11。按用户批准的修订Plan实施；合同见
[Retrieval V1](../phases/clean-retrieval-v1.md)，当前授权由
[current-phase](../current-phase.md)负责。

## 结果与范围

已实现独立S0召回入口、可替换F0/F1融合、保守结构整理、来源回读和SciPy Sparse有限试验。
不改写原始资料、模型或正式索引，不修改旧Serving选择，不进行全量Sparse或70题实验，
不调用收费API，不进行Git Commit/Push。P3仍需单独明确授权。

选中的119,557条BGE-M3 Dense与Whoosh3 BM25F共用原输入包。
新候选接口保持Chunk/Clean身份，与旧RU/Canonical路径分离。
宏微分别104,879/78,658个成员，其中63,980个共享Chunk；没有引入父子级联过滤。
完整Parent和最终Packet继续由生成分支负责。

## 实现与实际取舍

- `retrieval/clean_based/`提供QuerySpec、候选融合、绑定读取、隔离进程和单题CLI。
  原问题直通；未实现LLM查询规划、Step-Back、多跳Agent或自动补检。
- 初始S0为各路Top50、候选最多100、RRF常数60；F1先粒度内融合，再取最佳粒度贡献。
  F0和跨粒度求和F1均可替换。这些数值不是永久约束，也不构成质量胜出结论。
- 相同Chunk只输出一次，保留各路命中；只有全部正文范围及逐片限定、来源语境被一个
  更早候选覆盖时才延后。部分重叠、不同源出现、条件/分支或未知元数据继续保留。
  输出上限之外也有显式处置和原始命中，未按整篇文章限额。
- Dense同一次编码和分片扫描产生宏微两路结果。来源和索引在准备时完整校验，
  会话期间检查文件身份/大小/时间，原文按行哈希回读；不持久化基于mtime的信任。
- 真实预检发现宏微Whoosh reader同时常驻时进程RSS约3.46GiB，导致模型加载前RAM保护
  拒绝查询。改为缓存分析器及校验状态，逐粒度打开和释放reader，不强行常驻两套reader。
- 正式Manifest序列化会排序权重对象键，而分析器名称序列保持原顺序。适配器按该显式序列
  还原执行配置，避免误判为不同表示。未更改正式Manifest或原索引函数。

## Sparse 组件复用与验证限制

现有SciPy可直接使用CSR、标准NPZ持久化及精确点积，因此没有自建完整倒排引擎，
没有新增服务或升级已有环境。现有BM25F postings没有模型学习权重，不能直接承担Sparse。
CSR试验最多128条，默认32条；该入口和驻留策略不能直接用于全量构建/Serving。

先从PyPI下载并在`.local/clean-retrieval-p0p2-20261111/reference/`隔离安装
FlagEmbedding 1.4.2，无依赖安装；实际导入因缺少datasets失败。
没有为此引入无关训练依赖。采用原Transformers主干加固定Sparse head的薄适配器。
验证时从固定wheel中提取并原样执行官方`_sparse_embedding`方法，比较同一hidden states。
这是**官方Sparse核数值一致性**，不是完整FlagEmbedding编码流水线的一致性认证。

固定参考wheel SHA-256为
`35e33a08e8ed5e299eabbe3bc23518eb66a424dd29ee08fb3802bf9aef9e9bf2`；
官方核源码SHA-256为
`ef9f37f511cf9b7ceaf89a2bd569276ca0058cc8dc401545b9b4b688c9801f4f`。
本地Sparse head SHA-256为
`45c93804d2142b8f6d7ec6914ae23a1eee9c6a1d27d83d908a20d2afb3595ad9`。

文档使用原retrieval_text；Sparse按ReLU、指定特殊token排除、同token最大权重及共有token
点积生成，不做IDF/L2/量化/正权重剪枝。无匹配时不以零分文档填充TopK。

## 有限真实结果与证据入口

统一证据根为`data/retrieval/clean-retrieval-p0p2-20261111-review/`。

### S0 原搜索一致性及有限融合

`s0-query-r4/`对独立工程问题“塔利雅的新颂歌灵感来自哪里？”执行正式索引查询，
Dense和BM25F两路结果均与原独立搜索函数逐项一致。原词法函数的缓存准备被重定向至
新输出目录，正式索引根未写入Jieba缓存或查询结果。

200条命中对应106个唯一Chunk，12个候选可延后，最终返回100条。
该次准备106.43秒、查询65.59秒，包含首次模型加载和额外原搜索一致性检查；
不是温态延迟，也不代表端到端RAG性能。

`fusion-single-query.json/.md`复用这一个问题的相同原始命中：
F1最佳粒度但不整理相对F0有2进2出；加结构整理后有3进3出。
跨粒度求和对照亦保留。报告展示前列及进入/退出候选的实际原文。
抽查未把这些变化判作已证实的关键证据损失，亦不能据此声明F1胜出。
问题主体存在歧义：相关对白中塔利雅正在向芭芭拉提问，前列又有另一首歌曲的故事；
词面相近噪声和同源局部重复仍存在，应交由后续重排和更广泛对照处理。

`s0-query-r1/`为分析器顺序问题，r2为常驻reader触发RAM保护，r3为开发期参考函数
参数错误；均保留失败记录，不作为完成证据。它们没有修改正式资产。

`s0-session-final/`进一步验证同一会话连续执行两个独立工程查询，全部通过；
首题候选与r4原搜索一致性结果相同。该会话准备63.30秒，首题18.35秒，第二题14.92秒，
不含额外原搜索重算。第二题为“至冬先驱报”，200条命中对应107个唯一Chunk、7个延后、
100条输出。精确分片扫描、映射解析和Whoosh打开仍有明显成本，没有承诺交互级延迟。

### Sparse 32条结构样本

`sparse-trial-final/`按最长输入、共享/专属成员、对白及条件确定性选样，非随机相关性样本。
32个Chunk含21个micro、22个macro成员；包含正式长度审计的最长1,135-token输入。

| 检查 | 实际结果 |
| --- | --- |
| Sparse记录/空表示 | 32条，0条空表示，5,056个非零token权重 |
| 官方核数值对照 | 36次检查、累计39个输入，最大绝对误差0 |
| 精确查询 | 3个独立工程查询，宏微结果与暴力点积排序及分数一致 |
| FP16 padding/批处理 | 批量与逐条权重最大差0.00024414，低于本次0.005检查容差 |
| 恢复 | 在首个完整分片后注入停止，恢复保留3个已提交文件的原始字节 |
| 编码阶段 | 5.27秒，包含首次模型加载；不能按32条直接外推全量耗时 |
| 总试验阶段 | 87.66秒，另含来源校验、准备、查询及检查 |
| CUDA分配峰值 | 约1.14GiB，含额外padding检查；不是整机显存使用量 |
| 编码进度采样最低可用RAM | 约1.71GiB；不是连续系统内存峰值测量 |
| 试验Sparse目录 | 87,285字节，包含CSR、映射、计划及清单；不是全量大小预测 |

`validation.json`记录数值、资源和恢复；`source-cases.md`提供实际检索原文。
自我审计将官方参考来源信息移出向量空间配置；实际查询必须使用编码器自己的space_id，
并显式核对其与索引一致。完成这一修正后重新执行上述完整有限试验。
`sparse-trial-r1/`是此前开发检查点，不作为最终空间绑定验收入口。
所有Sparse结果仍属于bounded-trial，不是S1/S2全库检索结果或语义质量验收。

### 回归与资产保存

原181项相关回归加27项新回归，共208个测试ID；模型接口文档一致性再覆盖7项，
环境矩阵合计215个测试ID通过。主要证据：

- `regressions-candidate.log`：205项中的195通过，10项Whoosh依赖检查在词法环境覆盖。
- `regressions-bm25f.log`：46项中的44通过，两个Torch检查由候选环境覆盖。
- `regressions-final-boundaries.log`：32通过，补充3项运行边界及7项文档检查。
- `regressions-final-sparse-docs.log`：最终Sparse校验调整和文档检查15通过。

默认Python首轮日志因缺少tokenizers出现4个错误；已由现有候选环境覆盖，
没有升级默认Python或绕过依赖检查。

`asset-preservation-final.json`重新核对2,805个Dense分片/回执文件、182个词法文件、
两个选中Manifest、共用输入和所用上游文件，以及BGE主权重和Sparse head，全部通过。
这属于资产保存/机械校验，不是重新审定全库Raw语义保真。

## 使用与后续边界

在仓库根目录的PowerShell 7中，单题候选输出使用全新目录：

```powershell
$env:PYTHONPATH = (Resolve-Path src)
python -B -m genshin_corpus.retrieval.clean_based query --question '你的问题' --output 'data/retrieval/clean-query-new'
```

`--verify-reference`额外执行原搜索对照，适用于有限工程检查，日常查询不默认承担该开销。
程序化使用`PreparedRetriever`可复用同一校验会话及工作进程。
所有worker均使用固定本地环境和offline-only保护；输出不允许落入正式源/模型/索引根。

后续P3须另建适合全量的流式构建/查询入口及全量验收，继续复用既有Dense和BM25F；
不将本次有限CSR驻留策略直接扩大到全库。需测全量nnz、构建耗时、磁盘及内存，
必要时先调整SciPy布局，仍无证据要求自建检索服务。
之后的P4仅按批准方向做70题自动对照和原文差异抽查，不设正式盲审或零损失采用门槛。
S0/S1/S2最终由用户决定；本次工程通过不授权任何Serving切换。
