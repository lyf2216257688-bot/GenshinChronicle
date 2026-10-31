# Card真实批量生成与离线恢复

2026-10-29。用户授权自主组合渠道、模型、Prompt、Schema和确定性修复，最多使用原实验剩余8次请求。
**结果：工程链路已跑通，五份资料完成结构/来源合同构建；语义质量仍有具体缺陷，尚不能无审核全库生产。**

## 真实产物

主产物：`data/retrieval/p05-card-reliable-final-20261029-r1/`。
`samples.md`展示文档概要、解释、假说和复核意见；`samples.json`保存42张文档/阅读批次/线索Card，
`quality-review.json`绑定精确Card及文件哈希；`research-projections.json`提供三个有条件研究投影。
均为INACTIVE / NOT_REVIEWED。以下是助理对原文的内容复核，不是人工确认或总体语义通过率。

| 资料 | 选择正文字符 | 读取/合成请求 | 文档观察数 | 助理复核 |
|---|---:|---:|---:|---|
| 破魔之弓 | 359 | 1/0 | 10 | 有条件通过：教射术者出征未误归左卫门，没有虚构海祇阵营；孩子、姓氏为反事实 |
| 曚云之月 | 431 | 1/0 | 11 | 待复核：把双子具体写成姐妹、灭亡对象具体化；挽弓/提枪归属概括过粗 |
| 来歆余响 | 2112 | 1/0 | 46 | 有条件通过：保留药君异说、山主形貌改变、茶玉约定等；混合overview有主体歧义，剧情投影不采用它 |
| 龙蛇藏归辑录 | 3251 | 1/0 | 34 | 有条件通过：保留残影、图书身份质疑、口头说明与分支选项；复合观察仍需上下文复核 |
| 暮夜剧团团长的手稿 | 11422 | 2/1 | 71 | 待复核：真实综合两份子卡并保留戏中戏，但未读范围表述错误、概要仍带批次结构 |

短资料不强拆。长篇两份阅读批次分别保留真实章节和片段定位，不冒充章节；文档由一次独立
真实请求综合，direct阅读IDs=0、inherited IDs=12。选择正文全部交付不等于直接读完整份Canonical。
Card默认不能自动成为已审核事实，也不自动进入发布。

## 最终技术路线

实际可用策略为`deepseek-card-structured-1`：Metro `deepseek-v4.1-flash`，
`/v1/chat/completions`，非流式，`thinking.type=disabled`，`max_tokens=12288`，
`response_format=json_object`。将完整Schema和明确对象示例放入system Prompt，来源材料单列user消息。
要求处理主体、代词、条件句、传说/台本模态和不确定解释；没有删减解释及假说能力。
内部Card v2 Schema保持完整；冻结v0.3 Link不变。

Qwen3.8 Flash关闭思考+严格json_schema首请求HTTP524，127.166秒，无Token用量；封存，不重发。
其后六份DeepSeek阅读响应已取得，因返回模型拼写及格式/引用问题首先被拒绝；随后离线恢复，
没有重新生成。最后一次真实请求用于文档综合并直接通过。当前策略不是对旧失败的无条件放行。

### 严格内容与可诊断元数据分开

- 实际HTTP body、完整压缩原始响应、SDK对象、trace、reasoning/final、来源快照及账本独立保留。
- 本次部分返回`deepseek-v4-1-flash`，请求仍是`deepseek-v4.1-flash`：按明确白名单记录
  `PROVIDER_MODEL_SPELLING_ALIAS`，保留两个值；原生上游身份认证仍UNKNOWN，其他模型错绑拒绝。
- 提供商用量原样记录。非负整数计数有效但加和矛盾可警告，不否决独立合格内容；预算保守取
  reported total与input+output较大者。缺失计数/发送UNKNOWN仍阻止该运行继续派发。
- 不处理Responses ID漂移，不修改旧runner或其准入规则。当前路径没有SSE item ID一致性声明。
- 明确截断、身份错误、意外reasoning、超限、来源或Schema失败仍拒绝；SDK/传输均零自动重试。

### 可审计的确定性转换

1. 请求内E001等短别名可逆映射至原始证据ID；生成不计算字符位置，由程序唯一定位引文。
2. 来歆余响末尾JSON字符串外有一个多余英文单引号。只移除这一非JSON标点，不改字符串内正文。
3. 手稿两条引文写错同一资料内片段ID。仅在完整交付的同一来源中逐字、唯一匹配后改绑，
   日志并存模型ID、实际ID与引文，不能跨资料或做模糊匹配。
4. 对话某条引文重复添加了说话人前缀。仅当后半段逐字唯一匹配且同一原文行明确属于该说话人，
   去除非逐字前缀并保存原值；不修改事实叙述。
5. 对话27项观察还声明了没有对应引文的上下文ID。完整原声明保留在日志，Card证据列表按已验证
   引文确定；不伪造缺失引文、不把两种表示认定等价、不把未读材料补成证据。

不把解释字符串自动包装并猜来源：前一次Chat的11处缺失绑定无法安全推导，仍保留失败。
不修复截断，不补写事实，不把正确引文当作整条陈述必然正确。所有原始失败result/ledger不变；
新`recovered-jobs`绑定原complete哈希、转换、验证和新value，单独封存。

## 工程验证与实际限制

45项针对性回归通过（含6项模型接口一致性检查），覆盖实际wire、Schema/别名/精确引文、确定性转换边界、跨资料拒绝、
截断/超限/用量矛盾、账本重复/pending、完成恢复、增量索引与旧路径。
真实五源完成后拦截socket恢复，新增SDK调用=0；只读重放与来源/hash校验通过。243个历史
保护文件哈希不变。没有Embedding、Link、Phase04切换、全库生产、commit或push。

产物完成后另做两个确定性工程修正，**真实请求的Prompt/Schema未变**：
- 合成范围提示改成逐章节selected字符数与遗漏表示字符数；不再把“某章节有遗漏范围”
  暗示成整章未读。原文档limitations的错误仍保留并列入复核，不冒充已被新模型纠正。
- 剧情投影升级为`card_narrative_attributed_v4`：排除明确机制、攻略、目录观察；混合Card
  不使用污染overview，保留故事观察、解释、带标签假说和问题。旧v3索引保留，不跨投影复用。
  来歆余响的4条非剧情观察退出投影；未实际重嵌入，召回效果尚未知。

因此final-audit中的current_code_matches_run=false是上述后续工程变化，执行快照和postrun代码
分别保存；不是历史原件改变。已实际验证的零调用恢复记录发生于执行快照一致时。代码改变时
resume拒绝旧准备包，需新prepare并显式reuse原件，不能修改旧code哈希绕过保护。

## Token、耗时与成本

| 来源 | input | output | total | 请求秒数 |
|---|---:|---:|---:|---:|
| 破魔之弓 | 2128 | 2371 | 4499 | 15.402 |
| 曚云之月 | 2044 | 2491 | 4535 | 30.203 |
| 来歆余响 | 5433 | 7507 | 12940 | 40.949 |
| 龙蛇藏归辑录 | 6402 | 4458 | 10860 | 37.281 |
| 手稿：两读一合成 | 26557 | 19577 | 46134 | 276.473 |
| DeepSeek已知合计 | 42564 | 36404 | 78968 | 400.308 |
| Qwen HTTP524 | UNKNOWN | UNKNOWN | UNKNOWN | 127.166 |

本轮8/8发送用完；父实验累计12/12，剩余0。实际总费用UNKNOWN，不能用Token表代替账单，
不能认为524免费。长篇综合单次169.395秒，输出9404 Token，是当前延迟和输出成本主要风险；
全库前须评估长篇尾延迟、输出规模、限流及磁盘余量。旧全库census仍可用于任务量规划，
旧模型/Prompt成本外推不能当作本策略报价。

## PowerShell 7使用

只读核验现有样本（零模型调用）：

```powershell
$env:PYTHONPATH = 'src'
python -m genshin_corpus.retrieval.navi_offline card-audit --run data/retrieval/p05-card-reliable-final-20261029-r1
```

未来获新规模/费用授权后，先准备新运行。已有verified Sources可免全库扫描：

```powershell
python -m genshin_corpus.retrieval.navi_offline card-prepare-snapshot --run data/retrieval/NEW-RUN --snapshot data/retrieval/p05-card-reliable-final-20261029-r1/prepared/sources --total-request-limit 8
```

也可用`card-prepare --canonical ... --ru ... --sources ... --generation-strategy deepseek-card-structured-1`
准备新来源；常规CLI Card准备默认该策略，显式legacy-responses仍可复现旧工程路径。
读取prepared/complete.json的SHA256后，`card-run/card-resume --backend live --execute-authorized
--ack-plan-sha256 HASH --max-new-requests N --max-provider-tokens BUDGET --reuse PRIOR-RUN`
执行有界批次；这段用法说明不构成新增调用授权。

**结论：可继续使用的批量工程基础和不同结构真实样本已经具备；不能宣称五源都通过语义验收。**
三份样本可有条件研究使用，两份文档需复核；全库还受长文归纳质量、数据类别覆盖、费用未知、
Qwen超时未决、资源/独立备份和规模授权约束。本轮不再调用；若需确认新范围提示与长篇质量，
最小后续是单次手稿合成复测，但须新增授权，不能使用已耗尽的额度。
