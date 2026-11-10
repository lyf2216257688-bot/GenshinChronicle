# 新 RAG 索引工作单元1实施记录

2026-11-09。根据用户授权，先调查 Clean V1、Chunk V2.1、交接03/04及技术决策总账，
再设计和实现统一索引输入。[工程合同](../phases/indexing-input-v1.md)负责版本化接口，
[当前阶段](../current-phase.md)负责授权和下一步。
本报告的执行边界和第6节记录工作单元1完成时的检查点。后续独立启动器的实现、
模型编码与有限索引验证由 [启动器报告](index-benchmark-launcher-20261109.md) 记录。

## 1. 现场与实际输入

交接03已记录 V2.1全量构建和分片收尾，仓库 current-phase 的原首节仍停在有限试点。
本次以现存完整 Manifest、report、源绑定和全库扫描为依据，修正该状态并登记本工作单元。
现场原有 AGENTS、架构、项目结构和旧Phase04合同的未提交修改得到保留。
执行期间另有资料归档追记进入工作区，该类修改继续保留其原事实归属。
本报告的验证范围以本节和下文列出的实际检查为准。

| 实际资产 | 数量/合同 |
| --- | --- |
| Clean Manifest | gc-clean-1，complete，16,437条 |
| Chunk Manifest | gc-chunking-pilot-2，complete，balanced，准入策略 gc-story-admission-pilot-4 |
| 唯一 Chunk | 119,557 |
| 同时属于 micro / macro | 63,980 |
| micro专属 / macro专属 | 40,899 / 14,678 |
| micro逻辑成员 / macro逻辑成员 | 104,879 / 78,658 |
| 自然 Parent / 完整 Graph | 214,870 / 5,087 |
| 零候选资料 | 460 |

实际输入是 `data/retrieval/clean-chunking-full-v2-1-20261108/` 的平面产物，
配对 Clean 为 `data/clean/mihoyo_obc/zh-cn/clean-v1-full-20261106/metadata/manifest.json`。
输入读取验证 Chunk/Clean Manifest、Chunk/Parent/Graph/report 四个实际使用文件的 SHA-256、
16,437份清单身份与记录文件绑定。Clean 文件本体在回读时单独验证。
本轮全库扫描的证据入口为
[survey-accepted.json](../../data/retrieval/index-input-v1-20261109-review/survey-accepted.json)。

## 2. 从实际字段得出的设计

原 Chunk 已有 body_text、retrieval_text、context_text、context_sources、parts、
两路粒度和展示序、父层身份、CleanRef、别名、局部/文章范围与覆盖关系。
parts 中还实际出现条件解释状态、直接/继承条件、入边条件、分支/汇合、对白节点、
表格行列、文章标题及可选语境延后标记。
Parent.labels 和 context.label_evidence 提供结构化标题，适合直接投影。

| BM25F字段 | 非空候选数 | 全库字符数 |
| --- | ---: | ---: |
| title | 119,557 | 1,062,331 |
| headings | 107,379 | 629,867 |
| field_labels | 111,161 | 1,284,139 |
| body | 119,557 | 27,448,440 |
| context | 17,276 | 431,906 |
| conditions | 4,599 | 76,159 |

title来自实际文档标题，headings来自Parent标签及文章标题，field_labels来自parts中的原字段标签，
body原样使用body_text，context使用已交付的来源语境，conditions使用带CleanRef的条件字面文本。
field_labels中实际包含 dialogue、option、rich_text 等源英文标签，保留它们的来源性。
后续权重配置允许只启用部分字段，字段存在本身不授予固定正权重。

实际例子如下。

- 509517《至冬先驱报》的 title 为“至冬先驱报”，headings 为“交互文本”和
  “《欢迎来到奥古洛夫镇！》”，context可含“种类一”，body保持文章原文。
- 5095阿米尔的某条候选保留“完成魔神任务 第三章第五幕 虚空鼓动，劫火高扬前”
  条件及其完整来源，body仍包含该选项原文。条件同时出现在body与conditions中属于明确的跨字段角色重叠。
- 507287雅珂达的简介候选拥有title“雅珂达”和body“系风攘影 · 雅珂达”，
  其headings、field_labels、context、conditions可以为空。

Dense 的 dense_text 逐码点等于已有 retrieval_text，同时保存 UTF-8 SHA-256。
这保留了分片阶段已经计入预算的标题、必需条件和可读关系标记。
完整父层中的机制值、延后语境、内部ID、哈希和图JSON通过结构回读访问。
新模型仍须使用自己的Tokenizer做无截断预检，旧Qwen计数仅作为上游审计信息。

## 3. 共用存储与版本取舍

选择标准库JSONL和原文件字节定位，以完成可审计的最小基础。
共用 inputs.jsonl 每个chunk_id只有一行，micro.jsonl / macro.jsonl只有
row_number、chunk_id、display_order，原Chunk、Parent和Graph通过字节偏移、长度和行哈希回读。
字段投影、完整来源语境、条件状态和CleanRef保存在输入行中，完整parts和所有结构由原Chunk承担。
该输入包依赖绑定的原Chunk和Clean文件，迁移时需同时保留这些依赖或显式重定位同哈希清单。

原文件先生成micro候选，再合并共享候选并追加macro专属候选，物理行序可能与macro展示序不同。
实现核对各父层各粒度序号唯一且完整，路由显式保存实际序号。
同文不同出现继续独立存在，共享候选只在每个逻辑入口计一次。
BM25F未来分别计算各入口的DF和平均字段长度，Dense可复用相同配置下共享候选的编码结果。
召回融合、候选排序和生成组包继续由相应分支负责。

身份分为三个用途。

1. input_snapshot_id绑定数据清单、所用产物及输入/字段版本。
2. input_bundle_id额外绑定选中资料集合、实际投影/路由字节及数量，区分有限样本和全库。
3. representation_id绑定input_bundle_id、粒度及完整BM25F或Dense配置。

实现代码SHA-256单独进入构建审计。代码注释变化不改变来源身份，文本投影变化通过实际产物字节
改变输入包身份。Dense配置显式包含模型、revision、Tokenizer摘要、维数、pooling、normalization、
文档/查询前缀及长度上限。新模型、词典、权重或配置产生独立表示身份，构建目录采用create-only规则。
表示ID是绑定合同，未来执行器仍需核对实际模型输出与配置。

中文分析支持 field / analyzer 分开的词项序列。现有版本提供Han单字、相邻双字以及连续非Han字母/数字词，
按字段多值边界分别分析，标点和空白阻断双字。源文本保持原样，词项表示中非Han词执行casefold。
中文分词要求注入明确的segmenter，并提供实现/版本、配置与专名词典摘要。
实际分词库、专名词典及权重还需后续选择。该设计允许词法表示独立替换，并保留专名的单字/双字入口。

## 4. 已实现接口

`src/genshin_corpus/indexing/`包含以下职责。

| 模块 | 责任 |
| --- | --- |
| schema.py | 六字段投影、来源/条件约束、独立表示配置身份 |
| inputs.py | Manifest/产物校验、源目录、流式共用行、create-only输入包、粒度读取、原文回读 |
| analyzers.py | 多字段单字/双字分析与显式分词适配接口 |
| validation.py | 输入投影、正文/别名/语境、自然Parent和原对白图的离线回读核验 |
| __main__.py | survey、prepare、validate命令 |

Python消费接口如下。

```python
from genshin_corpus.indexing import iter_inputs, representation_id

for row in iter_inputs(input_manifest, granularity="micro"):
    chunk_id = row["chunk_id"]
    fields = row["lexical_fields"]
    embedding_input = row["dense_text"]

build_id = representation_id(input_bundle_id, "micro", "dense", dense_configuration)
```

输入迭代器的完整性末尾检查需要完全消费。完整表示的Manifest应在消费、验证和实际构建完成后发布。
输出目录必须全新，prepare失败会留下无完成清单的目录，重试使用新目录。
当前输入导出采用简单完整重试协议，断点续跑和长期增量机制属于后续工程选择。

已有有限样本的只读检查入口如下，命令在仓库根目录执行。

```powershell
$env:PYTHONPATH = 'src'
python -m genshin_corpus.indexing validate `
  --manifest data/retrieval/index-input-v1-20261109-review/sample-final-a/manifest.json `
  --source-readback
```

## 5. 离线验证

最终证据位于 `data/retrieval/index-input-v1-20261109-review/`。
sample-final-a与sample-final-b为相同16份选择的两次独立导出，三种JSONL及完成Manifest逐字节一致。
16份覆盖角色、对白/条件、长任务、文章、表格/武器/食物、考据、来源重复、语音别名、数字选项和零候选。
具体选择由本轮工程调查确定，保存在输入Manifest及validation-accepted.json。

| 验证 | 结果 |
| --- | --- |
| 全库输入投影与连接 | 119,557条通过，双路成员、每父层序号、Chunk/Parent/Graph身份及重叠目标检查通过 |
| 全库正文引用 / 别名引用 | 1,068,778 / 26,883处，结构绑定检查通过 |
| 全库已交付语境来源 | 23,982项，来源身份检查通过 |
| 全库原图连接候选 | 44,759条 |
| 有限输入包 | 718共用行，650 micro、548 macro |
| 实际Clean文件与自然父层 | 15份Clean、474个Parent回读通过，选中另一份资料零候选 |
| 原对白图 | 6组与Clean自然父层完整group相等 |
| 实际正文 / 别名范围回读 | 3,650 / 847处通过 |
| part语境 / 已交付语境 / 标题 / 任务来源回读 | 1,773 / 137 / 339 / 577处通过 |
| 中文分析器 | 有限样本六字段的单字/双字分析通过，多值边界及专名适配合同回归通过 |
| 自动回归 | 新增17项索引、75项Chunk、40项Clean、13项构建/恢复，共145项通过 |

全库来源性质实际均为unknown，继续原样保留。条件part中472,411项为not_established、
104,047项为unresolved、7,780项为source_marked。其他part无对白条件状态字段。
这些状态表达上游已有证据及解释程度，索引投影不升级其含义。

通用Python的首次旧测试运行缺少tokenizers，随后使用仓库既有embedding运行时。
一次将全部测试从stdin执行的汇总方式触发Windows多进程spawn入口问题。
最终改用标准 `python -m unittest discover` 子进程入口，145项全部通过。
早期测试日志和样本作为工程检查点保留，最终采信入口为
[offline-tests-accepted.json](../../data/retrieval/index-input-v1-20261109-review/offline-tests-accepted.json)、
[survey-accepted.json](../../data/retrieval/index-input-v1-20261109-review/survey-accepted.json)和
[validation-accepted.json](../../data/retrieval/index-input-v1-20261109-review/validation-accepted.json)。

本轮保留旧Phase04默认运行链、所有源数据和现有索引，执行范围为本地代码、文档、只读扫描及有限输入导出。
付费API、模型推理、全量BM25F/Dense索引、Git提交/推送均不在实际执行范围内。
验证结论覆盖索引输入的工程结构和有限来源回读。全量Raw语义保真、检索相关性与最终回答质量仍有各自验收范围。

## 6. 下一工作单元需要的输入与接口

1. 固定正式Chunk/Clean Manifest和一个新的输入包目录，使用prepare的显式all选择或同一source iterator。
2. BM25F明确字段启用集合、权重、b、k1、分析器组合，以及实际中文分词器/版本/专名词典。
   执行器消费lexical_fields及analyze_fields输出，按granularity建立统计与行映射。
3. Dense确定比较模型的完整配置和本地运行资源，逐模型进行Tokenizer长度/截断检查。
   执行器消费dense_text，输出chunk_id到向量行的映射及实际模型/维数/空间校验。
4. 各模型/配置使用独立representation_id和新目录，完成校验后再明确候选索引选择。
5. 随后通过有限问题检查各入口的召回，再由召回、重排和生成分支逐项整合与验收。

物理索引引擎、最终BM25F权重和Dense模型仍由后续工作单元选择。
