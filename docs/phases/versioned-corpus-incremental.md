# GC 语料版本与增量构建

本合同描述 Clean 链的跨版本边界。当前工作、运行选择和授权见 [当前阶段](../current-phase.md)。

## Raw 差分与证据身份

- 来源身份为当前 OBC 请求合同内的 `(source, locale, content_id)`。游戏更新版本作为观测标签。Raw run 记录观测起止时间与采集覆盖。
- 差分输入为同一可比较来源范围的两份完整 Raw 归档。新版使用 `gc-raw-2`，旧侧可使用已核验并固定清单与库存摘要的历史基线。只做目录差分与实际详情回读。部分、限量、范围不一致及损坏的输入停止处理。频道集合或父关系变化需要解释覆盖变化，相关缺失保留为未决。
- 差分以详情原字节、有序归属、完整列表和 map preview 条目及实际消费的频道祖先字段判断。成员顺序、重复出现、对象顺序和原数值字面保留。文件整体哈希与定位归入具体观测。邻居内容导致的物理位置变化可单独记录。只有完整可比覆盖中的确认缺失才进入新版移除记录。
- 各发布清单同时登记当前观测、实际引用观测及跨版复用所依据的源值摘要。Clean 产物保留原观测 ID 与原 Raw 定位。Chunk 复用要求 Clean 产物、观察版本和规则一致。Dense、Sparse 与词法数值的复用键拥有各自的实际输入与执行绑定。复用计算结果的资料继续拥有各自独立的行映射和来源。
- Clean 快照的规则身份登记紧凑 JSON 序列化及按来源位置稳定排序的孤立节点诊断。采用历史 Clean 时，先校验原清单、实际文件 SHA-256、观测身份和当前与有效 Raw 的等价证据。规范化仅允许在同一 `origin_id` 的 `DIALOGUE_ORPHAN_NODE` 位置内调整排列，保留诊断多重集合和其余字段原样。文件已符合规则时可直引。仅存在许可的诊断顺序差异时，从原有效 Raw 重派生到新根，要求结果 SHA-256 精确等于兼容检查所得目标，并登记旧文件引用和目标摘要。其他序列化或回放差异阻断。旧档案按原执行绑定验证。

## 本地构建入口

从项目根设置 `PYTHONPATH=src`。输入和输出通过本机路径显式传给各模块。

| 入口 | 持久输出和验证 |
| --- | --- |
| `python -m genshin_corpus.clean.snapshot_diff --previous … --current … --output …` | `gc-raw-snapshot-diff-1` 差分报告。目标文件在两份 Raw 根之外，覆盖待复核时保存原因并以状态 1 退出。 |
| `python -m genshin_corpus.clean.snapshot_build build --mode full\|incremental --current-raw … --previous-raw … --previous-clean … --output …` | `gc-clean-snapshot-1`，每份状态有效的资料都有本次 Raw 与实际引用观测及直接 Clean 记录引用。增量模式领用符合规则的旧记录。全建模式从同一组可核验的实际观测重新生成记录。两者以 `logical_id` 核对血缘和字节，分别记录物理位置及构建处置。 |
| `python -m genshin_corpus.clean.snapshot_build validate …` | 独立回读两侧观测，检查源切片、Clean 指纹和 Raw locators。构建在验证完成输入和派生结果后写完成清单，重复运行仅允许同计划恢复。 |
| `python -m genshin_corpus.chunking.snapshot_ranges <Chunk manifest> <output>` | `gc-chunk-flat-ranges-1`，从现有已封存的 balanced 扁平导出登记候选、父层、图和准入范围。逐范围保存哈希与计数，包含零候选资料。 |
| `python -m genshin_corpus.chunking.snapshot_build build --mode full\|incremental --clean … --selection … --qwen-tokenizer … --bge-tokenizer … --output …` | `gc-chunk-snapshot-1`，保存直接引用的逐资料范围。`--previous` 接受旧扁平范围目录或前一 Chunk 快照。其 `validate` 子命令独立检查范围和 Clean 绑定。 |
| `python -m genshin_corpus.indexing.snapshot_inputs <Chunk snapshot> <output>` | `gc-index-snapshot-input-1`，只物化共享行号、证据身份、路由和实际输入摘要，使用同一投影按资料读取证据和索引字段。逻辑上等价的全建和增量输入拥有相同 `input_bundle_id`。 |
| `python -m genshin_corpus.retrieval.clean_based.snapshot build-vectors --input … --output … [--previous …] [--sparse]` | `gc-index-snapshot-vectors-1`，以固定长度双条前向生成 Dense，可共享 BGE 主干生成 Sparse。每个载荷块具有计算合同、键、文件及校验封条。 |
| 同模块 `build-bm25f --input … --output … [--previous …]` | `gc-index-snapshot-bm25f-1`。上一兼容清单可提供同成员桶。每路按全局统一 BM25F 评分。使用既有词法 Python 环境。 |
| 同模块 `package`、`query`、`compare` | `gc-local-corpus-release-1` 固定统一输入和两个索引清单。显式传入 release 执行本地 S0/S2、可选 BGE 重排及首证据回读。对照器分别验收所有活跃向量键和 BM25F 全路统计、固定查询全部评分。 |

## 数值与词法采用验收

数值载荷与本版输入行、路由成员和证据定位分开封存。Dense 的执行模式由单条、固定形状批量及历史动态批量对照确定。固定形状变更搭档、槽位和批尾时须保持兼容才能使用单行复用键。旧空间的导入需要和新版实际计算合同独立验证。Sparse 在相同输入快照中单独绑定主干、head、词表和数值规则。

新版物理格式中，Dense 与 Sparse 的复用键是编码合同与精确检索文本摘要。动态有效键集控制哪些条目参加本版查询。已移除或被替换的载荷仍可留在旧块内，当前查询只展开本版活跃键和它对应的资料行。Fresh 模式分别计算全量活跃键，构建中断时仅复用同计划且封存完整的块或桶。

BM25F 按稳定来源键固定分桶，替换真正变化的桶。micro 与 macro 分别将各自活跃桶接到一个全局评分视图。均值、DF 和 N 由本版同一路全部活跃成员共同确定。同分处理在 TopK 边界使用稳定逻辑顺序。独立全建用于核对全局统计、分数与排名。

词法桶键包含来源、路由、精确六字段摘要及同源同值重复序号。桶中保存该键和倒排，当前行映射负责引到本版的 Chunk 和证据。查询先完成得分比较，再按本版逻辑行号裁决同分。证据回读核对 Clean 及有效 Raw 字节，同时核对当前 Raw 对应的文件绑定。发布描述符本身没有自动生效动作。

正式切换前须以同一已封存 Raw 做整版增量与独立全建对照，核对资料、Clean、分片、统一输入、Dense、BM25F、适用的 Sparse、检索顺序及引用回读。正式 Raw 采集、整版全建、切换活动版本分别取得授权。现有产品开启有限真实模型批次的许可规则继续适用。

整版运行通过 PowerShell 7 启动器及 SHA-256 固定的执行封条派发。通过每阶段回执恢复，程序在同一执行身份下重新验证已完成的引用，未完成阶段按各自合同续建。Quick 验收独立比对两个新版构建臂。Extended 读取历史召回和首包，按冻结题单补充版本迁移的逐题审查。语义复核和正式切换各保留独立决定。[运行手册](../research/incremental-first-build-runbook-20261125.md)

## 双臂前置检查与兼容恢复

- 常规双臂构建先完成两侧 Clean 并比较逻辑身份；之后完成两侧 Chunk 和统一输入，核对逐资料 Clean/Chunk 绑定、完整 Chunk 逻辑身份、实际行文件 SHA-256、逐行映射和 `input_bundle_id`。两个前置结果各有绑定回执。只有第二关通过后才派发物理索引构建。Quick 继续执行全量定位、范围、数值、词法统计及查询回读检查。
- 兼容恢复使用另行封存的执行和新输出根。每臂从自己的原发布清单、阶段回执和该臂独立计算的物理载荷领用资产。入口固定允许的历史 Clean 规则和计算运行时。发生 Clean 兼容重派生的资料逐份重切并比较四类范围。新行文件和原臂逐行与逐字节一致，数值合同、活跃文本键、词法配置及分桶成员完全一致，才允许重发索引与发布描述符。恢复路径的文档编码数、词法新建桶数和跨臂借用数均为零。
- 恢复发布描述符持有新清单的绝对路径及哈希。新清单可跨根引用原臂的向量块、词法桶、既有 Chunk pack 和 Clean 文件。完成回执引用逐臂资产依赖清单，登记原审计、Raw 归档、Clean 文件、Chunk pack 及对应范围绑定、向量和词法文件。更新时按实际引用保留传递依赖。回滚以原 release 描述符和原查询、会话绑定为准。清理前沿所有保留的 release、历史引用、回滚候选及审计保留点追踪这些依赖，继续保留任何被引用的根与文件。
