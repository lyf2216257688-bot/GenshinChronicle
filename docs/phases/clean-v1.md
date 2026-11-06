# Clean V1 数据合同

2026-11-06 用户批准技术方案实施。当前采用状态与后续动作由
[current-phase](../current-phase.md) 负责，试点事实见
[实施记录](../research/clean-v1-implementation-20261106.md)。

## 1. 责任与输入

长期基础资料目标为 Raw 与 Clean。Clean 直接读取同一 Raw run 的详情、
栏目树、列表和 inventory，保存全部文本、数值、字段关系、表格、对话和资源引用。
现行 Phase04 的 Canonical/RU 引用由原有路径负责。

Clean 进行确定性结构恢复、技术整理和有来源依据的标签提取。
分片负责内容准入、组合、去重和长度策略，索引与生成分别拥有其下游策略。
技术解释保持在派生层，原始措辞和来源证据独立保存。

首个源适配器为 `mihoyo_obc`。locale 从 Raw manifest 读取，原始内容键使用
现有已验证 `content_id` 合同。输入必须是完整 Raw run，所选 ID 必须存在于
inventory。详情缺失、校验失败或关键 envelope 失配进入阻断记录。

## 2. 持久化结构

每条观测保存一个紧凑 UTF-8 JSON 文件，运行清单保存为
`data/clean/<source>/<locale>/<clean_run_id>/metadata/manifest.json`。
记录位于同一 run 的 `records/<observation_id>.json`。
数组及成员列表保留来源顺序，JSON 对象的序列化键排序承担确定性写入。

顶层 `CleanRecord` 包含以下字段。

| 字段 | 责任 |
| --- | --- |
| `schema_version` | 当前为 `gc-clean-1` |
| `observation_id` | 来源、locale、内容 ID、Raw run、输入指纹及规则版本的散列 |
| `source_item` | `source`、`locale`、`content_id` |
| `input` | Raw run、依赖指纹和 schema/cleaner/ruleset 版本 |
| `artifacts` | 引用文件的类别、相对 Raw run 路径、SHA-256 和本记录内 ID |
| `origins` | 共享的源定位链 |
| `document` | 标题、描述、源字段、栏目归属、标签、列表观测和出处状态 |
| `sections` | 模块源顺序、标题、布局观察、源字段和组件 |
| `relations` | 链接、条目目标、原始关联属性及其定位 |
| `media` | 资源类型、地址、标签和归属，`binary_available=false` |
| `extensions` | 展示控制元信息、未知 HTML 属性和保留载荷 |
| `diagnostics` | 结构异常、未知字段形态、标签冲突等 |
| `coverage` | 处理状态与组件源值去向计数 |

`schema_version`、`cleaner_version`、`ruleset_version` 共同定义重放规则。
修改提取、定位或字段解释时升级相应版本，并使用新的 run ID。

### 结构块

每块具有 `block_id`、`kind` 和 `origin_id`。块 ID 只在一次观测内使用。

- `paragraph`、`heading` 保存原文，heading 同时保存级别。
- `group` 保存有序对象、数组、页签、条目或 HTML 分组。来源明确的名称成为
  `title`，同时保留其 `title_source_key`。
- `field` 保存 `field_key`、原标签及值。机器键采用组件类型与规范化源路径，
  例如 `role_ascension:/list/*/attr/*`。
- `scalar` 保存 `source_type` 与 `lexeme`。数字保留 `-0`、小数位和指数写法，
  字符串中的百分号、单位和括号保持原值。
- `table` 保存表头、行列及单元格内部块。HTML 合并单元格保存原始 span，
  并给出逻辑起始列。空表头和空行保持可区分。
- `list`、`quote`、`separator` 保存相应边界与来源属性。
- `dialogue` 保存分组、根节点、节点字段及有序边。
- `media` 引用资源池，`generic` 保存通用结构，`opaque` 保存无法解码的原片段。

`null`、空字符串、空数组、空对象及缺失字段保持各自状态。
不同源位置的相同文本具有各自的出现身份。

### TextValue 与定位链

```json
{
  "text": "50（27无武器）",
  "origin_id": "o42"
}
```

多节点组成的文本另有 `segments`。每段包含 `clean_range`、`origin_id` 和
`source_range`。生成的换行使用空来源与明确 `rule_id`。
`marks` 保存强调、链接、媒体、上下标和展示注记的文本范围。
所有字符范围统一为 Unicode code point，起点包含、终点排除。

根定位包含 `artifact_id` 和 `steps`。派生定位共享 `parent_origin_id`，仅保存
新增步骤，避免重复展开长路径。操作包括 RFC 6901 pointer、JSON 解码、
JSON 键、HTML 节点、HTML 文本与 HTML 属性。
`precision` 声明字段值或文本节点精度。HTML 节点选择器绑定清洗规则版本。

真实攻击力字段的定位从 `/data/page/modules/1/components/0/data` 开始，
JSON 解码后进入 `/list/0/attr/4/value/0`，再定位 HTML 文本节点。
解析器使用分段回溯验证转换后的每个文本值。

## 3. 处理规则

常规 HTML 的段落、标题、列表、表格和可见文本进入主结构，完整源 HTML
通过 Raw 恢复。语义标记和 CSS 明示的强调保留为范围标记，CSS 展示信息
进入注记。折叠及隐藏状态随内容保存。图片、音视频和关联 URL 进入资源或关系池。
正文中实际写出的 URL 继续作为原文。
目标 ID 来自显式条目属性或已核验的相对 OBC 路径。绝对 URL 保留完整链接，
其目标身份通过后续有依据的解析规则扩展。

未知标签的字面形式保留，未知属性和脚本载荷保留于扩展信息。
未知组件或字段以通用结构输出并给出诊断。
`field_rules.py` 登记调查中的 150 条组件字段形态，承担结构演进检测。
字段含义仍由具体源结构决定。

对话同时读取顶层载荷和 `list[]` 分组。节点出现顺序及边子数组顺序分别保存。
汇合、多父、循环、悬空和孤立关系保留并附诊断。当前快照缺少显式说话人字段，
节点 `speaker` 为 `null`，正文称谓继续存在于原文。

标签使用命名空间、作用范围和证据定位。

| 命名空间 | 依据 |
| --- | --- |
| `source_channel` | 栏目树与实际归属 |
| `source_filter` | 详情和列表实际赋值的筛选字符串 |
| `source_section` | 模块标题 |
| `source_component` | 源组件 ID |
| `content_role` | 对话结构、任务栏目筛选及角色栏目中的明确故事标题规则 |
| `provenance` | 缺少明确出处证据时保持 `unknown` |

详情 `ext.fe_ext`、列表 `ext` 及内部 `filter.text` 按已观测编码逐层解码。
标签在首个分隔处拆为键和值，值内其余斜杠完整保留。
相同标签汇集证据，冲突赋值共同保存。
列表嵌套子栏目使用条目实际所在节点 ID，请求栏目单独记录。
map 预览列表以独立观测保存。

## 4. 构建、续跑与读取接口

单个 run 使用复用的操作系统文件锁。记录先原子写入，随后更新进度清单。
出现中断时，已登记记录通过 SHA-256 校验复用，未登记文件通过确定性重建比较。
已完成 run 校验全部绑定输入与记录，冲突保持可见。

运行清单绑定 Raw manifest、inventory、发现响应、选择集合及规则版本。
记录依赖采用实际使用的文件校验和与定位信息。因此，同一列表文件变化会使
引用该文件的记录获得新身份。当前版本采用整记录更新。
同一 Raw run 可通过 `reuse_manifest` 复用完全一致的观测。
跨 Raw run 创建新的观测及来源绑定。

记录覆盖状态为 `complete` 或 `preserved_unknown`。
构建清单为 `partial`、`complete` 或 `complete_with_errors`。
阻断项以 `blocked_integrity` 记录原因。默认读取要求完整清单，
显式 `allow_partial=True` 提供诊断用途的可用记录读取。

Python 接口由 `genshin_corpus.clean` 导出。

```python
from genshin_corpus.clean import (
    BlockSelector, CleanRef, iter_records, iter_blocks,
    iter_text_values, resolve_clean_ref, render_record,
)

for record in iter_records(manifest_path):
    selector = BlockSelector(field_labels=("攻击力",))
    for block in iter_blocks(record, selector):
        for value in iter_text_values(block):
            ref = CleanRef(record["observation_id"], block["block_id"],
                           value["value_pointer"], 0, len(value["text"]))
            resolved = resolve_clean_ref(ref, record=record, raw_run_root=raw_root)
```

`iter_blocks` 提供结构块及继承上下文，包括文档、章节、组件、条目组标题、字段及标签。
`iter_text_values` 仅返回当前块拥有的文本，子块由块迭代器单独提供。
筛选在同一维度内使用 OR，不同维度之间使用 AND，标签匹配命名空间、键和值三元组。
支持栏目、组件、块类型、字段精确键、前缀、原标签与标签三元组。

标题和章节标题也可直接从其元数据对象读取。
媒体和关系池单独向分片提供资源及关联信息。
跨字段组合保留多个 `CleanRef`，分片新增的上下文由分片记录其生成依据。

阅读导出按需生成正文及列表标题、摘要等文本，资源和分支使用生成的标记。
字段控制元信息、详细媒体属性等通过 JSON 接口访问。
渲染视图由 `clean-reader-1` 标识，证据定位使用 TextValue 和 CleanRef。

PowerShell 7 的有限样本入口如下。

```powershell
$env:PYTHONPATH = "src"
python -m genshin_corpus.clean build `
  --raw-run-root data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824 `
  --clean-run-id clean-sample-review `
  --content-id 501157 --content-id 509653

python -m genshin_corpus.clean export `
  --manifest data/clean/mihoyo_obc/zh-cn/clean-sample-review/metadata/manifest.json `
  --format text --content-id 501157
```

export 同时支持 `markdown` 和 `jsonl`。读取与构建使用现有 Python 标准库和仓库能力。

独立保真检查入口如下，它读取所选清单中的记录与对应 Raw。

```powershell
python -m genshin_corpus.clean validate `
  --manifest data/clean/mihoyo_obc/zh-cn/clean-sample-review/metadata/manifest.json `
  --raw-run-root data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824
```

该检查核对有内容的 HTML 文本出现位置、表格数量、对话根与节点身份、边顺序、
源值计数及全部来源定位。

## 5. 验收与迁移

自动验收覆盖内容与数值、结构关系、全部来源链、TextValue 分段、未知处理、
身份确定性、记录校验、单写入者、中断恢复与跨观测重绑定。
试点选择覆盖 30 种组件的有限真实资料，单元测试使用约 37 KB 的结构摘录 fixture
及合成边界输入。

全量构建后的核对基线属于后续实施验收，包括 16,437 条详情、84,072 个组件和
当前统计的 5,087 组、60,682 个节点、62,727 条边。
列表出现次数与 inventory 归属数分别核对。

生产采用需要将分片、索引及引用解析绑定到同一 Clean 数据版本。
现有历史证据保留独立解析能力，归档和生产采用时机由对应工作单决定。
完整 Raw 是恢复常规 HTML、原始载荷及资源出处所需的档案依赖。
