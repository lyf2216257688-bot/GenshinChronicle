# Clean-based Retrieval V1 — P0–P2

2026-11-11。用户批准修订 Plan 的 P0–P2：合同、S0 工程基线和 Sparse
小规模验证。当前状态及授权由 [current-phase](../current-phase.md) 负责。
当时的 P3 全量 Sparse、P4 70题实验、正式 Serving、付费 API、Git Commit/Push 均未授权。
2026-11-12 的 P3 准备与用户手动全量入口由 [Sparse build V1](sparse-build-v1.md) 补充；
本合同的128条有限试验入口和边界继续保留。

## 输入与边界

复用选中的 BGE-M3 Dense、Whoosh3 BM25F 和原统一输入包，不重建或改写它们。
候选身份是 input_bundle_id + chunk_id，不是旧 Phase04 RU。宏微是成员集合，
不是父子树。同一 Chunk 只输出一次，各通道和粒度命中继续保留。
召回负责发现和融合；重排负责后续排序；生成负责完整父层回读、预算和 Packet。
来源性质、未知状态、源条件和显示顺序语义继续遵守上游合同。

## 工作点与候选接口

独立配置 `config/clean-retrieval-v1.json` 不改变正式索引选择或旧 Runtime。
QuerySpec 保存 question_id、query_id、原问题、检索文本、kind、round 和前序查询引用。
V1 只执行 original/round=0；未来查询增强复用同一检索原语，不在此实现规划器。
原始命中保存 input_row、chunk_id、channel、granularity、rank、raw_score、
representation_id 和查询身份。CandidateSet 保存来源、文本、原始命中、粒度内名次、
融合名次、最终顺序和结构整理/预算处置；不是 Evidence Packet。

初始深度50、候选上限100、RRF常数60。S0/S1两通道等权；S2初始Dense=.5、
BM25F=.25、Sparse=.25。配置可替换，不是永久约束。
F1 先粒度内 Weighted RRF，再按同一 Chunk 的最佳粒度 reciprocal-rank 贡献合并。
同时提供跨粒度 RRF 和 F0 直接融合供有限对照。相同输入名次/分数的重复命中幂等，
冲突重复记录失败。两层之间不另截断。所有并列按 input_row 确定性排序。

结构整理仅将同观测、同Parent、全部正文范围及逐片限定和交付语境已被一个更早候选
覆盖的候选延后。使用源字符区间而非文本相似度；别名/不同条件或分支不凭同文折叠。
部分重叠保留，不按整篇文章配额。未知/未支持的结构采用保留策略并记录原因。
延后候选可回填；最终上限之外的候选有显式处置，不静默丢弃。

## 只读运行与校验

准备时验证完成清单、输入/成员/表示身份、所用文件哈希和源清单绑定。
会话内复用轻量行定位、分析器和校验状态；文件身份/大小/mtime变化使会话失效。
每次原文回读校验行哈希与身份。缓存只在当前校验会话有效，不持久化信任mtime。
Dense一次编码、一次分片扫描同时维护两个粒度TopK；BM25F独立两路统计不变。
实际资源检查显示双路常驻Whoosh reader占用过大，改为逐粒度打开/释放reader。
词法缓存、日志和结果放在新的输出根，禁止在正式索引目录写queries、锁或Jieba缓存。
隔离进程使用已有Windows Job Object原语，退出释放GPU和文件句柄。
通道失败显式失败，不自动换组合。输入过长失败，不截断。

## Sparse 有限验证

先检查官方FlagEmbedding在隔离参考路径的可用性；不升级既有模型运行环境。
必要时复用现有Transformers主干和固定Sparse head的薄适配器。
官方规则为ReLU线性投影、指定特殊token排除、同token最大权重、共有token加权点积。
文档文本为原dense_text/retrieval_text；不加IDF、L2、量化或正权重剪枝。
Sparse身份独立绑定主干、Tokenizer、head、精度和规则，不能复用Dense space_id。

本阶段最多128条真实Chunk；默认选择32条覆盖最长输入、双路/专属成员、对白和条件。
只使用少量独立工程查询，不导入70题。采用已安装SciPy CSR分片及精确点积；
保存行映射、成员关系、分片回执及Manifest。空向量和无正分匹配显式记账。
官方FlagEmbedding 1.4.2隔离导入缺少datasets等训练侧依赖，因此不扩张既有环境；
薄适配器以固定wheel中的原样Sparse核方法核对同一hidden states的结果。
这属于官方核数值检查，不声称完整FlagEmbedding端到端一致。
对照独立暴力点积、官方核、padding和重复token，验证恢复和损坏拒绝。
只有有限验证通过并检查资源后才提出P3建议，本入口拒绝超过128条的构建。

## 验证及后续评价

测试使用小型fixtures，不依赖全量Raw。有限真实S0查询与原独立搜索核对排名和分数，
记录资源、耗时、来源回读和正式资产保存情况。工程正确性与语义质量分开。
后续P4只用现有70题自动对照和代表性原文案例，不要求正式盲审、完整标注或nDCG。
比较有效覆盖、独有收益、明显退化、重复和成本；不设机械零损失采用门槛。
S0/S1/S2最终由用户选择，实验完成不自动改变Serving。
