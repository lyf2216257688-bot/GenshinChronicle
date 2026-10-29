# B生成与定向审核Prompt v0.2草案

历史范围说明（2026-10-28）：本页保留v0.2-rc1准备时的Prompt/Schema，不修改F2已执行
合同或结果。下文“仅静态验证/未调用模型”描述当时检查点；后来的实测由F2报告拥有。
新的前瞻性候选见[v0.3-rc1](p05-navi-b-prompts-v03.md)。

状态：**语义原则已由用户确认；模型接口为rc1候选草案，仅静态验证，未接入runner或调用模型**。语义规则见
[v0.2标准](p05-navi-semantic-standard-v02.md)，完整字段结构见
[JSON Schema草案](p05-navi-b-v02.schema.json)。不改变G1 v0.1历史协议。

## 1. 输入与输出责任

沿用G1双端原文payload及仅含已交付alias的映射。输入包括真实章节、顺序、说话者、
来源筛查和遗漏范围。审核模式额外提供原候选命题及ID，隐藏旧等级与旧复核结论。
不得传入预期等级、检索排名或目标A/B配额。保留既有请求/响应/失败记录。

审核输入的`candidates`由程序冻结：每项含id、原text、原role、text_sha256及历史记录指针。
历史等级保留在程序账本，不主动供给审核模型。单命题v0.1可由程序封装为一项；无法恢复的
原命题记录执行失败，不用空字符串伪装成已审核。模型输出的candidate_id引用该注册表。

模型只返回ID和判断。程序拥有Canonical/RU精确引文提取、覆盖与端点校验、
机械结果、请求账目、模型身份、人工状态和发布状态。`read_ids`仅是模型阅读声明，
不是程序证明它理解了全部文字。程序不得把未读/省略资料自动判为语义错误。

## 2. 共同Prompt

```text
只依据本次A/B交付原文，按Navi语义标准v0.2判断具体联读命题，不用训练记忆补全。
先确认来源段落角色与实际阅读范围，再分开判断证据支持、至少一个方向的具体阅读增量、推论边界。
拆开：直接引用/明确文本联系、解释性联读、身份/因果断言。文献提出H不等于H成立。
二手文可作引文溯源或解释入口，不是独立游戏历史证词；两个二手版本一致也不证明历史。
文学B允许无互引、无同名的具体对应；常见火/月亮/孤独/命运主题或通用物品槽位不足自动B。
说明这两文的特定对应怎样改变理解；如换成任意同主题文本仍成立，要反查增量。
目录/掉落/重复版本可准确但仅CATALOG_ONLY；有项目机制、改编差异等实质增量则另审，不能按栏目一律压级。
A=明确具体且有实质增量；B=双端有据且有增量的受限解释；C=相关但特异性/证据/增量不足；
D=关键错误/错误归属，或将无依据身份、因果、历史推论当确定事实（写critical_error和依据）；
U=必要材料不足，写evidence_gap。缺互引或文学解释未证实不自动D；未读整页或关键词未命中不自动U。
另选release：ELIGIBLE_PENDING_RELEASE、REVIEW_REQUIRED、CATALOG_ONLY、EXCLUDED、UNRESOLVED。
B的navigation_label必须为“解释性联读 / 非确定史实”。等级不表示人工认可或发布。
局部有趣或低增量反讽即使可B，也不自动ELIGIBLE；reason分别说明语义理由和正式剧情导航价值。
只用本端已交付ID，不复抄或拼接引文。不能由一个正确子命题提升其余命题。
依据完整JSON模板输出一个对象；所有字段出现，无则空数组、空串或规定的null。
不凑条数或凑价值。Pair只有汇总状态，不设覆盖整对的等级：忽略警戒命题后，有eligible则CANDIDATES；
否则有REVIEW_REQUIRED或UNRESOLVED则UNRESOLVED；其余NO_FORMAL_NAVI。零合格候选是正常结果。
```

## 3. 两个短模式补充

**生成模式**：

```text
提出必要的具体命题，不设固定条数。每条candidate_id=null、review_action=PROPOSE。
role用ORIGINAL_CORE/ORIGINAL_COMPONENT，表示本次生成原文；不把首次生成冒称审核中新发现。
只保留证据能支持的表达；可记录一个需要排除/待查的命题说明为何不建正式Navi。
不生成用户未提出的历史错误断言来充当自己的纠错成绩。
```

**定向审核模式**：

```text
先阅读双方原文，再逐项审核candidate原文。保留candidate_id，以KEEP/REWRITE/REJECT/UNRESOLVED区分结果。
每个输入原命题必须先有一条原text不变、role不变的实际判断，用KEEP/REJECT/UNRESOLVED。
原句A/B/C用KEEP（仅保留原句判断，不表示准入），D用REJECT，U用UNRESOLVED。
安全改写另起一项，role=SAFE_REPHRASING、review_action=REWRITE，绑定原candidate_id，说明删改了什么。
审核中新发现独立联系用NEW_DISCOVERY、PROPOSE、candidate_id=null，不能算原生成已发现。
反事实警戒用COUNTERFACTUAL_GUARD、PROPOSE，永不准入或计入旧模型错误；可引用相关candidate_id。
原命题错误不否决独立成立的安全改写/新发现；正确改写也不能追认原句正确。
只对当前争议及必要上下文复核；若关键材料未交付则UNRESOLVED，不能要求全页全部内容才允许局部判断。
不沿用候选自检作为证明。指出最可能反驳自己判断的原文或缺口。
```

两个模式使用同一完整输出结构；后续若获得实验授权，模型请求必须实际附上完整模板或
受支持的结构化输出约束。仅写“另增加这些字段”会重现G1初始17次缺字段风险。

## 4. 紧凑完整模板

```json
{
  "schema_version": "p05-navi-b-v0.2-rc1",
  "decision": "UNRESOLVED",
  "reading": {
    "A": {"read_ids": [], "unread_ids": [], "limitations": ""},
    "B": {"read_ids": [], "unread_ids": [], "limitations": ""}
  },
  "source_roles": {
    "A": [{"role": "unknown", "basis": "字段模板未提供来源材料", "evidence_ids": [], "attribution": "未知"}],
    "B": [{"role": "unknown", "basis": "字段模板未提供来源材料", "evidence_ids": [], "attribution": "未知"}]
  },
  "propositions": [{
    "id": "p1", "candidate_id": null, "review_action": "PROPOSE", "role": "ORIGINAL_CORE",
    "text": "待证据交付后审核的命题（字段示意）", "relation": "none", "a_ids": [], "b_ids": [],
    "support": "UNDETERMINED", "increment_a_from_b": "", "increment_b_from_a": "",
    "boundary": "字段模板未提供真实证据", "grade": "U", "release": "UNRESOLVED",
    "navigation_label": null, "critical_error": null, "evidence_gap": "尚无必要原文材料", "reason": "模板不是实际候选"
  }],
  "no_link_reason": ""
}
```

这是字段模板，不是实际生成的新Link，也不是可准入的完成样例。空ID与空描述只能用于
模板/无法建立命题的说明；实际A/B候选必须满足下一节语义及机械约束。

## 5. 程序约束与离线验证边界

Schema检查形状、枚举及可表达的跨字段条件；独立的`navi_v02_contract.py`仅检查离线绑定与
原命题保留，未接入G1或生产runner。非空增量是否真实、文学价值和身份/因果证据是否充足仍须语义审核。

- ID唯一且属于本端交付；read/unread互斥并完整核算实际交付范围；不得把未交付项列为已读。
- A/B子命题需要双端非空ID、明确阅读增量与理由；不把非空字符串当语义质量证明。
- A要求明确支持；B要求具体解释支持及固定标签；D必须说明关键错误；U说明具体缺口。
- `ELIGIBLE_PENDING_RELEASE`只可用于满足内容条件的A/B；有A/B也可待审。
- Pair状态按共同Prompt的优先级汇总；有候选与有U可以同时发生，不能把其他U自动发布。
- SAFE_REPHRASING必须绑定原命题且另有原句实际判断；NEW_DISCOVERY无旧candidate_id；
  警戒项不进入准入清单或旧模型错误分母。每个输入原句必须恰有一条原文判断，不能遗漏或覆盖。
- 拒绝/待查也保留原候选与原因；零命题时必须有`no_link_reason`，不能静默丢失来源对。
- 模型不能填写或覆盖`human_acceptance`、`navigation_status`及机械PASS。

## 6. 与G1 v0.1 Link字段逐项对照

| v0.1字段 | v0.2草案责任/变化 |
|---|---|
| `schema_version`, `link_id` | 新独立schema版本；原pair ID保留，子命题ID局部唯一；不立刻建多条正式Link |
| `endpoints` | 程序保留，不让模型写来源路径/哈希 |
| `proposition` / 模型`proposed_claim` | `propositions[].text`；审核用candidate_id及review_action保留原命题与改写区别 |
| 新增`role` | 原核心/组成、安全改写、新发现、反事实警戒分开；保留既有candidate_id/review_action，不另建Agent或Pair评分机制 |
| `relation_type` / 模型`relation` | 下移到每个子命题，原枚举不变 |
| `evidence_ids`, `extracted_evidence` / 模型`a_ids,b_ids` | 每命题a_ids/b_ids；程序沿原绑定提取引文；禁止模型引文字段 |
| `increment_a_from_b`, `increment_b_from_a` | 下移到子命题，方向不反转；无增量可明确无 |
| `explanation`, `anchored_correspondences` | 合并为命题text、双端ID、增量和reason，减少重复写同一解释 |
| `interpretation_boundary` / 模型`counterevidence` | 每命题boundary，保留条件、替代解释与缺口 |
| 新增`evidence_gap` | U必须说明具体必要缺口；仅是文本字段，程序不能证明其语义正确 |
| `claim_level` | 由support独立轴替代；不再用一个interpretive把所有直接引用一起压级 |
| `endpoint_roles`, `role_basis` | source_roles按端保存段落角色、依据ID、说话者/传闻归属；支持混合段落 |
| `unsupported_identity_or_cause` | 不用整对boolean一票投C；在具体错误命题support/critical_error/boundary记录 |
| `model_verdict` / 模型`verdict` | 用decision和每命题release表达是否建立正式Navi；不从useful等粗标签自动投等级 |
| `model_grade`, `grade_recommendation`, `grade_policy` | 程序保留原始模型等级；按v0.2另记审查建议，禁止以规则声称已语义核准 |
| `mechanical_status`, `model_review` | 程序审计字段，与语义、人工状态分离；原格式失败保留 |
| `human_acceptance`, `navigation_status` | 仍由程序固定NOT_REVIEWED/INACTIVE，不在模型输出Schema |
| `certainty` | B固定标签，加每命题boundary；A同样不认证其引用假说为真 |
| `independent_history_witness` | 保留false；来源角色与具体文本关系另审，不能据此禁止游戏文本的A级互补 |
| `reuse_origin`, `discovery` | 程序原样保留运行来源与发现路径，不发送排名和历史评分给审核 |
| `generation_self_check` / 模型`self_check` | 分解为support、reason、boundary、critical_error，取消宽泛“已自检”背书 |
| `reading_coverage` | 模型只声明read/unread/limitations；程序保留交付/遗漏与真实结构，不能以全页false自动U |
| `initial_grade_recommendation`, `review_disposition`, `targeted_review_reference` | 历史不可覆盖；新审核用版本化命题与独立结果记录 |
| 新增release / review_action | 等级与准入分离、原断言与安全改写分离；发布仍另需授权 |

Card、缓存语义Query、原文Hybrid检索及Canonical/RU字段不变。

### rc1兼容迁移

旧`p05-navi-b-v0.2-DRAFT`快照保存在本轮独立审计根；旧G1/S1数据不迁移、不重标。
rc1新增必填role/evidence_gap并收紧跨字段条件，不对旧对象静默填值；缺字段作为旧版本保留。
如后续需要适配旧单命题，程序只封装原文/来源/hash注册表，不把安全收窄或新发现猜成原始内容。
S1的SAFE_REPHRASING有些兼含新发现，不能无人工检查一律换成NEW_DISCOVERY。
历史Pair等级仅用于当时统计；新结果按命题grade/release及引用关系储存。

## 7. 是否分开调用生成与审核

建议保留一次生成并结构化自检，机械检验在程序完成；有关键错误/文学边界/混合命题等争议时
才用单独审核。48条显示自检存在主体颠倒和无依据包装，但没有新模型对照实验，无法证明
两次调用一定更好，也不能估计收益。原G1定向复核有4次格式失败，且重复版本一例说明与评分
不一致；“多调用一次”本身不是质量保证。是否换独立模型及新Prompt效果，留给后续另行授权的实验。
