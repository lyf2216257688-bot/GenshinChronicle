# Clean-based 统一索引输入 V1

2026-11-09。工作单元1根据实际 Chunk V2.1 设计共用输入与表示绑定。
当前状态和授权由 [current-phase](../current-phase.md) 维护。

## 1. 输入与身份

输入为完整 `gc-chunking-pilot-2` Manifest、balanced 平面 Chunk、Parent、Graph、
report 和其绑定的完整 `gc-clean-1` Manifest。要求准入版本
`gc-story-admission-pilot-4`。读取前验证所用文件 SHA-256、Clean 清单绑定及观测身份。
Raw、Clean、Chunk 原文件继续拥有证据。索引输入是可重建投影。

唯一文档键沿用 `chunk_id`。`granularities` 是 micro / macro 成员关系，
一个候选可以同时进入两路。两路使用独立展示序号，共享一个输入行及来源。
原文件先生成 micro，再复用共享候选并追加 macro 专属候选，因此物理行序不保证
macro 展示序连续。路由保存实际序号，读取者按父层和所选粒度解释展示顺序。
不同源出现即使同文也保持独立，宏微重叠继续由原覆盖关系表达。
Parent 是完整源容器的回读入口，其准入后 units 不能替代完整 Parent。

## 2. 统一行与字段

行版本为 `gc-index-input-1`，字段投影版本为 `gc-index-fields-1`。
`input_snapshot_id` 绑定上述版本、Chunk Manifest 哈希、Clean Manifest 哈希和所用产物哈希。
实现代码哈希作为独立构建审计字段保存。input_bundle_id 再绑定实际投影字节，
代码注释变化不会改变来源身份，投影变化会改变输入包身份。文件读取前后重新核对
来源及执行实现，避免运行中变更。
原 Chunk 的代码、Tokenizer、选择、准入和构建 binding 通过 Manifest 继续追溯。

| 行字段 | 来自实际产物及用途 |
| --- | --- |
| chunk_id、parent_id | 原身份，所有表示及行映射共用 |
| source_item、observation_id、clean_record | Clean 清单中的源、locale、内容键、观测及文件哈希 |
| granularities、display_order_by_granularity | 两路逻辑成员关系，顺序仅表示展示 |
| lexical_fields | 下表的多值文本字段，空值保留为空数组 |
| dense_text、dense_text_sha256 | 原样复制 retrieval_text，UTF-8 摘要绑定实际向量输入 |
| clean_refs、alias_clean_refs、context_sources | 原样保留范围及来源语境，无跨出现去重 |
| conditions | 按 part 序号保留 condition_status 与完整 condition_sources |
| source_context | 完整 Chunk context，包括标签、栏目、来源性质、任务关联及标题证据 |
| structure | section/component、局部/文章范围、重叠候选、display_only 语义 |
| chunk_locator、parent_locator、graph_locator | 原 JSONL 相对文件名、字节偏移、长度和行哈希 |

| BM25F 字段 | 确定性投影 |
| --- | --- |
| title | context.label_evidence.document_title.text |
| headings | Parent.labels 中标题以后的非空标签，以及实际 part.article_title.text |
| field_labels | parts.field_labels 的非空原标签，包含源英文标签，无实体/别名推断 |
| body | 完整 body_text，保留引文主体 |
| context | 已交付 context_sources 中 role=来源语境 的原文 |
| conditions | parts.condition_sources 的原文，包含已在 body 内出现的条件 |

文章标题同时可以已经在正文中，这是明确的跨字段角色重叠。字段内重复结构标签及同一
条件来源只投影一次，原始出现、basis 和完整列表仍保存在结构侧及原 Chunk。
不从 context_text 的显示分隔符反向猜测层级，不从机器 field_keys、ID、哈希、
图 JSON 或整份 Parent 自动增加检索正文。context_text 原文可由 chunk_locator 回读。
未知来源性质与未解释条件继续原样传递。

## 3. 中文分析器接口

字段与分析器是独立维度。`analyze_fields` 对每个字段的每个值分别分析，
输出按 field / analyzer 分开的词项序列，禁止跨多值边界生成双字。
提供版本化的 Han 单字与相邻双字分析器，连续非 Han 字母/数字作为词，执行 casefold。
标点和空白作为边界，单字、双字表示保持独立以便后续标定权重和长度归一化。

中文分词通过显式注入的 segmenter 接口接入。必须同时绑定分词器实现/版本、
配置及专名词典摘要，缺失接口时明确失败。工作单元1不选定分词库或专名词典。
《原神》专名由可替换词典及单字/双字通道承接，避免把临时词典提升为语料身份。
BM25F 的字段权重、b、k1、分析器组合和词典由后续有限比较确定。
两路的 DF、字段平均长度等统计分别在各自成员集合计算，共享候选在每路只计一次。

## 4. 表示与独立构建

`representation_id` 由 input_bundle_id、粒度及完整表示配置内容寻址。
input_bundle_id 额外绑定选择集合、实际行/路由哈希与数量，有限样本与全量输入不会共用表示身份。
Dense 配置必须显式给出模型、revision、Tokenizer 摘要、维数、pooling、normalization、
文档/查询前缀和最大长度。BM25F 配置绑定字段权重、长度归一化、k1、
分析器版本及分词器/词典绑定。相同维数不构成向量空间兼容。
表示配置必须与未来实际执行器核对，ID 仅建立绑定，不能证明模型输出正确。

每个新模型/配置使用独立目录和 Manifest，显式行映射继续使用 chunk_id。
不同表示不得原地覆盖或隐式修改默认选择。宏微共享候选可以复用同配置的文档编码，
两路查询/融合、候选排序和最终证据组包分别交给召回、重排、生成。

## 5. 离线接口与完成协议

`genshin_corpus.indexing.ChunkInputSource` 验证来源并流式产出统一行。
`read_chunk / read_parent / read_graph / read_clean / read_natural_parent` 从绑定文件回读。
Clean 记录文件按需回读时验证哈希，输入准备阶段校验其清单绑定，不重做全库 Raw 保真验收。
完整 CleanRef 可继续交给 `genshin_corpus.clean.resolve_clean_ref` 解析到原文和 Raw origin。

`python -m genshin_corpus.indexing survey` 扫描所有候选和连接，输出字段/粒度/条件统计。
`prepare` 显式选择 content-id 或 all，将共用行写入 inputs.jsonl，
micro.jsonl / macro.jsonl 仅保存行号、chunk_id 和展示序号。
输出目录必须全新，所有文件写完、输入哈希复查完成后，最后发布 complete Manifest。
prepare_inputs 的可选 progress 回调每1024行及末尾交付计数，供独立实验启动器记录进度。
失败留下无完成清单的目录，重试使用新目录。此最小导出没有断点续跑或增量机制。
`iter_inputs` 验证完整输入包后按粒度读取，两路保持相同源身份。
源/输入迭代器的末尾检查需要完整消费，构建者必须消费完并通过检查后才发布表示完成清单。
`validate --manifest <输入清单> --source-readback` 额外核对输入投影、正文/别名/语境范围、
任务关系及完整自然父层/原对白图。有限样本选择及复核结果由工作单元报告记录。

全量 BM25F/Dense 执行器需要此输入包或同一 source iterator、明确表示配置、
逐行 chunk_id 映射、执行资源及独立构建目录。Dense 需要实际模型的 Tokenizer
无截断预检和文档/查询编码一致性验证。上游 Qwen Token 数不能替代新模型预检。
工作单元1的字段及机械检查不建立召回相关性或最终回答质量结论。

后续 [全量实验启动器](index-benchmark-v1.md) 实现串行本地模型、Whoosh3、
分片恢复、独立检索及模型自身的长度审计，复用本输入合同。
