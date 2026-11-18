# G7 第二部分：70Q 问答质量审计

2026-11-17；对象为本目录上一层的封存基线。**GC 已具备有用的单点事实问答能力，但尚未达到“可靠、完整、由原始剧情支撑”的预期，不能据本轮宣布 G7 产品验收或切换默认入口。最值得优先改进的是：关键证据已经召回、甚至已经读到之后，如何保留进最终 Packet，并按问题的全部要求决定是否完成。**

本轮只读取现有产物与 Clean；新增模型请求、LLM/BGE 推理均为 0。没有重试 UNKNOWN、重新生成答案、改实现/配置/索引、提交或推送。原始 `complete.json` 中的 `semantic_evaluation=NOT_PERFORMED` 与逐题 `NOT_REVIEWED` 保留为采集阶段事实；本次评价另存 [question-reviews.json](../../data/generation/g7-70q-baseline-20261117/audit/question-reviews.json)。

## 1. 结论与统计口径

| 语义评价 | 题数 | 占55份最终答案 | 占70题 |
| --- | ---: | ---: | ---: |
| 正确 | 43 | 78.2% | 61.4% |
| 部分正确 | 8 | 14.5% | 11.4% |
| 错误 | 1 | 1.8% | 1.4% |
| 证据不足 | 3 | 5.5% | 4.3% |
| 无法评价 | 15 | 不入分母 | 21.4% |

“正确”指核心所问已得到现有语料支持的回答，不要求无关旁支全部展开；“部分正确”指答出一部分但遗漏关键实体/关系、或重要推理不成立；“证据不足”指核心判断无法由当前材料确定；“无法评价”只用于无最终交付。错误 Q060 指**引用不支持其身份断言**，本审计没有凭常识另造诺奇特的真实身份。

**78.2% 不是官方剧情严格正确率。**43题中12题（Q001、Q002、Q009、Q012、Q018、Q028、Q031、Q034、Q036、Q047、Q066、Q069）的核心作答目前只得到汇编／考据支持，尚缺一级文本独立闭合。另31题有任务、角色、物品等游戏正文形态的核心支持，占55份答案56.4%、全部70题44.3%；这仍不等于所有附加句都已验证。表中 A 指游戏正文形态，B 指核心仅有汇编或考据；A+B 指正文和二手桥梁并用。**分类依据内容形态，不擅自把 `source_provenance=unknown` 升格成经认证的官方作者身份。**

评价为单名 Codex 审阅者、非盲、无独立金标的有限语料审计。70题已用于前期调试，不能据此推断独立泛化率。Q017按“分别是谁”的实体识别要求判部分正确；若用户接受“草木、甘露、鲜花”三个象征称谓即完整答案，则该题可升正确，43/55变为44/55。这一口径差异不影响瓶颈判断。Q003“原题所对应时期”缺明确时间锚点，也是题目本身的限制。

## 2. 运行结果核对

核对 [complete](../../data/generation/g7-70q-baseline-20261117/complete.json)、[progress](../../data/generation/g7-70q-baseline-20261117/progress.json)、[accounting](../../data/generation/g7-70q-baseline-20261117/accounting.json) 与186份 attempt `result.json`：汇总 **41 completed、14 partial、1 failed、14 UNKNOWN** 一致。底层逐题 `status` 实为41 completed／29 partial；运行器先看 UNKNOWN，再看是否有最终答案来分桶。因此29个底层 partial中，有14个 UNKNOWN、1个无答案的引用失败，只有14个是带答案的 partial。不能直接数底层字符串。

| 运行分桶 | 正确 | 部分正确 | 错误 | 证据不足 | 无法评价 |
| --- | ---: | ---: | ---: | ---: | ---: |
| completed 41 | 36 | 5 | 0 | 0 | 0 |
| partial 14 | 7 | 3 | 1 | 3 | 0 |
| failed 1 | 0 | 0 | 0 | 0 | 1 |
| UNKNOWN 14 | 0 | 0 | 0 | 0 | 14 |

14份带答案的 partial原因：

- **7题 `sufficient`**：Q007、Q012、Q016、Q019、Q027、Q032、Q047，核心作答均正确，但使用圆括号、全角方括号等E标记，程序引用列表为空。`citation_validation=PASS` 此时只是空集未发现非法ID，绝非可点击引用或语义通过。
- **2题 `context_budget`**：Q022、Q044；是上下文门限强制收尾，非模型认为充分。
- **2题 `no_gain`**：Q003、Q060；无收益停止仍可输出无根据估算或身份串线，不能等同安全正确。
- **3题 `limited`**：Q050、Q068、Q070；保留有限成果合理，但是否遗漏库中已存在的关键证据仍需单独审阅。

186次请求记录中，172份 `CONTENT_VERIFIED`／HTTP200／已知用量，14份 UNKNOWN；独立汇加为输入2,897,493、输出64,519、合计 **2,962,012 Token**，与账本一致。14个UNKNOWN全部是 `URLError`、HTTP状态为空、无可用终态用量，属于传输故障，不能算答错；现有记录不足定位DNS、代理、连接或提供方哪一端出错。Q013是另一类问题：响应已返回，但选段/引用校验失败，**不是网络故障**。

已知部分费用 **USD 0.483625734** 仅转述本轮 accounting 的历史标价估算，本审计未重新核实价格或查询账单；含UNKNOWN的总用量、总估算和实际扣款仍未知。完整核对与逐题时间见 [run-audit.json](../../data/generation/g7-70q-baseline-20261117/audit/run-audit.json)。

## 3. 代表题：错误发生在哪一步

以下链路均复用本轮排序、回读、选段、Packet及模型轨迹，未重新计算评分。完整定位见 [representative-traces.json](../../data/generation/g7-70q-baseline-20261117/audit/representative-traces.json)，含原始 group/read 引用、Chunk ID与Clean定位。

**Q046：有证据却答不出姓名，主要损失在选段与停止判断。**《幽潭心》身份段在S0融合第94，BGE升到第3，且已回读；原文由卡莉露自述“是一只…纯水精灵”，再回答迪奥娜“你就是清泉镇的泉水精灵？”为“是啊”。最终模型只选迪奥娜故事等3段，身份段记为 `not_selected`，并将姓名缺口降成“未知而非缺口”后 `finish(sufficient)`。因此不能归咎于没有召回、BGE排后或源回读失败；最终答案的“原文没有名字／物种”超出了最终小包可支持的范围。Clean独立读见 `records/a26c88…json` 的b850、b858–b864，完整路径及原话在 [source-excerpts.json](../../data/generation/g7-70q-baseline-20261117/audit/source-excerpts.json)。

**Q013：选错片段，加上最终引用未绑定当前包。**《云堇·长命锁》S0第1、BGE第3，已经回读，明确“专心从事武器锻造”。模型说明在讨论这一段，却提交了《云堇·角色详细》的fragment ID。最终Packet仅含角色详细的E01，正确原文为 `not_selected`；最终原始响应仍以旧E03支持锻造并追加E02，导致校验失败、答案为空。这既有模型选段错误，也暴露最终生成未严守新包证据边界；不可简单补一个旧E编号就当修好了。原始响应核心“武器锻造”虽对，也不能替代已发布的无答案结果。

**Q020：关系覆盖不全；BGE与选段同时有贡献。**婚姻原文“又将女儿希琳许配给了他”在一段候选中由S0第1降到BGE第97，但同一内容还包含在BGE第4的已读片段中；卷二第3也读到了婚姻。模型最后只选第1名石碑，漏掉希琳与基斯拉婚姻、母系及义子层次，便称核心关系足够。不能只凭第97名就说是纯BGE故障，也不能把不同叙述者的碑文与传说无条件压成同一条生物学谱系。下一次需要覆盖所有被问人物与亲属／婚姻关系，并保留来源间的叙述差异。

**Q050：候选中已有更多姓名，回读窗口与排名阻止了使用。**本轮S0候选《古老的碑铭》有“大调律师优恩尼娅”、另段有“大调律师奥里略斯”，原轮BGE第16／26；第一次补检分别第14／34，均 `not_read`。《关于北境蛮族的考察》补检第17也明称两人，未读。独立Clean读还找到《香韵奏者》及考察原文，足以否定“现有语料只点名波爱修斯”的泛化。模型做了两次补检，却仍只接触局部头部材料；主要是目标姓名/证据覆盖与回读准入，BGE将有用材料排出窗口也有贡献。**本审计不据这些新读片段拼出未经完整身份核验的四人金标名单**，尤其不将同僚、共事者直接当作四人之一。

**Q060：检索补检放大了未经验证的身份假设。**笔记的诺奇特男子与剧情中的渊上伪装，各有原文；但后者是“特诺约洛钦”与“诺奇特梨服装”，不是“诺奇特”化名证明。第三次查询已把“诺奇特 渊上 化名”组合搜索，属于用未证假设引导补检。最后答案从“没有逐字同一性”跳成“综合确认”，引用结构PASS也拦不住这种不蕴含。主因是实体桥梁与生成推断，不是确定性Assembly改坏了原文。

**Q068：有用身份语音多轮停在回读窗口外。**《想要了解伊涅芙·其五》原轮S0第6→BGE第43，后两轮BGE第9／21，均未读；它直接说明她诞生于“明晨之镜”的一个念头，后来被剥离。最终只选角色故事4／5而答“悖谬”，错过所问古龙来源称号。合理答案应区分：**源于明晨之镜，分离出的意志曾称悖谬**；不能反过来把伊涅芙无条件等同完整的第十一领主。其角色语音的独立Clean位置为 `7801dc…json` b3276等，见 [second-excerpts.json](../../data/generation/g7-70q-baseline-20261117/audit/second-excerpts.json)。

其他共性：Q017以象征物名替代实体映射，Q021混淆政治范围与地下地理，Q022过度要求逐字种族标签；Q014、Q032、Q051、Q067虽核心答对，仍加入无必要的“未逐字明说”保留。Q003从另一人的16岁及“年轻／几年”推到25岁，属于缺时间锚点的数值估算。相反，Q006、Q010、Q015、Q019、Q024、Q025、Q026、Q029、Q035、Q038、Q051、Q059、Q062、Q063、Q067有清楚的直接正文支持，证明当前链条能处理不少单点事实题。

## 4. 研究效率与优化优先级

70题记录共 **99个检索排序组、713次read计数、178次工具计数、186次模型请求**；read计数为控制器口径，不等于713份独立文献。逐题起止时间相加约200.7分钟，中位138秒，P90为294秒，最长Q070为566秒；不是GPU净时间或去重后的完整批次墙钟。现有检索／排序／回读计时相加约147.9分钟，占上述逐题时间73.7%，只能说明本轮本地链条耗时较重，不能据此精确拆成BGE、加载与IO成本。35题各用2次模型请求。

高耗时未保证高收益：Q022用5请求／5检索组／177,734已知Token，仍对人类身份过度保守；Q060用5请求／3组／145,329 Token仍串错身份；Q068用5请求／3组／113,270 Token遗漏正文语音；Q070用6请求／5组／119,583 Token仍未完成父亲身份。可优化的是每轮新增了什么**能改变核心答案的证据**，不宜仅增加调用、TopK或上下文预算。

| 优先级 | 建议的下一项有限优化 | 本轮证据与可检查目标 |
| --- | --- | --- |
| P0 | 保护核心证据选段与新Packet引用绑定 | Q013/Q046/Q020：已有正确片段却选错或删掉；先离线重放，要求选中内容与选段理由一致，最终每一实质主张在当前包有依据。不能用旧E号静默指向新片段。 |
| P1 | 按题目所需实体／关系检查覆盖，再决定充分或有限停止 | Q046缺姓名仍sufficient；Q020漏婚姻；Q050需四人却只见一人。允许明确有限作答，但“本包未见”不能写成“资料没有”。 |
| P1 | 完善身份桥梁与来源强度判断 | Q060没有同一人证据；Q068混淆源体和分出意志；12题仅二手支持，另有多题把OBC汇编称官方。保留人物观点、游戏文内史书与作者考据差异。 |
| P1 | 对明确缺口提供受预算约束的候选检查／回读 | Q050第14/16/17、Q068第9/21有关键材料但未读；先用既有候选做小型离线覆盖对照，之后再决定BGE调整是否必要。尚无依据大改召回或默认切Sparse。 |
| P2 | 修复引用标记兼容与空引用的交付表述 | 7份语义正确答案无程序引用；可以先用封存响应做离线显示验证，不需要付费重答。但兼容格式不能掩盖Q013的错片段。 |
| P2 | 以同一批固定输入拆本地耗时，减少无收益补检及泛化旁支 | 本地链条147.9分钟；多个简单题附无关未解事项，多轮难题仍未解决核心缺口。先获得阶段耗时再决定缓存／加载优化，不提前扩基础设施。 |

这些是**下一工作单建议，未执行**。没有观察到本轮的确定性Assembly改写剧情或伪造Raw；其按模型选择排除证据的行为有审计记录。要优化的是读／选／入包的整体政策，而非将所有损失统称为Assembly算法错误。

## 5. UNKNOWN与失败题处理

14个UNKNOWN为 **Q033、Q039、Q041、Q042、Q043、Q045、Q048、Q049、Q052、Q053、Q057、Q058、Q064、Q065**。本次均记无法评价，无草稿转最终、无补齐答案、无重派发。逐题问题见下表；原始operation/attempt与状态见 [run-audit.json](../../data/generation/g7-70q-baseline-20261117/audit/run-audit.json) 的 `unknowns`。

建议保留每个原始UNKNOWN及占额，先由操作者核对提供方请求／账单，尽可能确定是否执行及计费。若以后明确授权补测，应做逐origin恢复并明确潜在重复计费风险，或在证明未发送后走对应恢复；不能因HTTP状态为空就认定未发送，也不能批量重置额度。即使日后取得答案，原14个UNKNOWN仍保留为本轮可用性损失。Q057已进行多轮研究，不应因为“已有研究”而把最后的未知请求当作零成本。

Q013建议先离线验证正确fragment、最终新引用表与原始响应的对应；保留本轮failed。需要重新生成最终答案时再单独授权付费调用；本轮不把可读的原始锻造回答直接发布或改写为completed。没有最终答案的15题不进入55份有效回答的质量分母，也不能从70题成功率中删除。

## 6. 70Q逐题评价表

表内E编号严格限定在本题，不跨题共用；“证据”链接指封存Packet及回读索引。最终答案与原始状态未改。额外Clean补读只用于审计，不倒灌为当时模型见过的材料。

| 题号／运行 | 问题与现有结论 | 评价／来源 | 依据与主要问题 |
| --- | --- | --- | --- |
| [Q001](../../data/generation/g7-70q-baseline-20261117/records/Q001/result.json) · completed | 如果一个须弥教令院学者主要研究历史、社会与知识来源等问题，他最适合归属六大学派中的哪一派？<br>答：因论派 | **正确** · B | E02《教令院·简介》直载“探究知识的来源、构成及其本质”；历史、社会被明确标为延伸推断。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q001/evidence.json) |
| [Q002](../../data/generation/g7-70q-baseline-20261117/records/Q002/result.json) · completed | 前任大贤者阿扎尔出身于须弥教令院的哪一个学派？<br>答：明论派 | **正确** · B | E01/E02《教令院》均写“明论派出身”；E03为考据转述，三处不能算独立官方佐证。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q002/evidence.json) |
| [Q003](../../data/generation/g7-70q-baseline-20261117/records/Q003/result.json) · partial | 根据阿蕾奇诺已公开的经历与时间线推算，她在原题所对应时期大约多少岁？可以简要说明判断依据。<br>答：约25岁、二十出头至近30 | **证据不足** · A+B | E02只能确认克雷薇16岁去世，E07“几年前”来自组织汇编；缺阿蕾奇诺同龄、准备期与原题时期的定量锚点，无法推出该区间。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q003/evidence.json) |
| [Q004](../../data/generation/g7-70q-baseline-20261117/records/Q004/result.json) · completed | 原神设定中，被称作「贵金之神」的是哪位神明？<br>答：摩拉克斯／钟离 | **正确** · A | E01那维莱特《关于岩之神》直接说“贵金之神赢取了岩之大权”；E02奥罗巴斯逃亡与这个称谓没有证明关系。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q004/evidence.json) |
| [Q005](../../data/generation/g7-70q-baseline-20261117/records/Q005/result.json) · completed | 蒙德旧贵族中，除劳伦斯、古恩希尔德与莱艮芬德外，曾经还有哪一个以勇武著称、后来断绝传承的家族？<br>答：伊蒙洛卡 | **部分正确** · B | E03确指家族名与战斗职责，但“被吞并”原文只是“极有可能”，不足证明断绝；答案已提示社区推测，核心条件仍未完成。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q005/evidence.json) |
| [Q006](../../data/generation/g7-70q-baseline-20261117/records/Q006/result.json) · completed | 古华派传承的三大秘术分别叫什么？<br>答：刺明法、裁雨法、生克法 | **正确** · A | E01行秋《神之眼》逐项明载，E03王山行对白吻合。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q006/evidence.json) |
| [Q007](../../data/generation/g7-70q-baseline-20261117/records/Q007/result.json) · partial | 哪位角色与派蒙同为6月1日生日？<br>答：荒泷一斗 | **正确** · A | E01角色基础信息明确“生日6月1日”；派蒙日期作为题设使用，未偷换为独立核验。圆括号E标记未进入引用列表。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q007/evidence.json) |
| [Q008](../../data/generation/g7-70q-baseline-20261117/records/Q008/result.json) · completed | 归终与摩拉克斯初次相识时，归终交给摩拉克斯、既作为盟约信物又作为挑战的道具是什么？<br>答：石锁（尘世之锁） | **正确** · A | E01武器故事直接写“盟约的信物”“我的一切智慧，都藏在这把石锁之中”；人物对应已作限定。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q008/evidence.json) |
| [Q009](../../data/generation/g7-70q-baseline-20261117/records/Q009/result.json) · completed | 魔神战争时期，为阻止天衡山崩塌波及山下村庄，牺牲鹿角支撑山岳并最终战死的仙人是谁？<br>答：移霄导天真君 | **正确** · B | E01/E02汇编直载鹿角支山；天衡山、战死等细节只引社区《璃月简史》，答案已区分，但称汇编为官方不妥。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q009/evidence.json) |
| [Q010](../../data/generation/g7-70q-baseline-20261117/records/Q010/result.json) · completed | 枫丹动能工程科学研究院（枫丹科学院）的创立者是谁？<br>答：阿兰·吉约丹 | **正确** · A | E03《加载页面提示·奇械公》直接称其为科学院创始人，E01/E02汇编相符。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q010/evidence.json) |
| [Q011](../../data/generation/g7-70q-baseline-20261117/records/Q011/result.json) · completed | 现任火神玛薇卡在成为火神之前，出身于纳塔六大部族中的哪一个部族？<br>答：悬木人 | **正确** · A | E01玛薇卡《神之眼》末句“悬木人的女儿”直接支持。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q011/evidence.json) |
| [Q012](../../data/generation/g7-70q-baseline-20261117/records/Q012/result.json) · partial | 纳塔历史上，哪位被称为「圣主」的统治者曾对龙族实施严厉压迫与迫害？<br>答：奥奇坎 | **正确** · B | E01历史整理直接记统治与囚禁龙族；E03材料中的禁城圣主为间接对照，答案明确对应是推断。圆括号E未被识别。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q012/evidence.json) |
| [Q013](../../data/generation/g7-70q-baseline-20261117/records/Q013/result.json) · failed | 云堇所在的云家在以戏曲闻名之前，家族传统行业是什么？<br>答：未交付；原始响应答武器锻造 | **无法评价** · A | 《云堇·长命锁》已召回、回读并直接支持武器锻造；模型却选中《角色详细》，最终包只有E01，原始响应仍引E03与E02，校验失败。原始响应不计最终答案。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q013/evidence.json) |
| [Q014](../../data/generation/g7-70q-baseline-20261117/records/Q014/result.json) · completed | 琴与芭芭拉之间是什么亲属关系？<br>答：琴为姐姐、芭芭拉为妹妹 | **正确** · A | E01琴《角色故事5》写“血脉相通的妹妹”；E02还载共同母亲。答案“同父姐妹”成立，但对更细分类的长篇保留并无必要。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q014/evidence.json) |
| [Q015](../../data/generation/g7-70q-baseline-20261117/records/Q015/result.json) · completed | 曾说出「人属于他出生的地方，畜生属于它吃饱的地方」这句话的角色是谁？<br>答：阿祇 | **正确** · A | E01《海洲逐雾》保留说话者与该句原话。她自称龙蜥不应另升格为客观种族结论；本答未这样做。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q015/evidence.json) |
| [Q016](../../data/generation/g7-70q-baseline-20261117/records/Q016/result.json) · partial | 申鹤身上的红绳主要起到什么作用？<br>答：红绳缚魂，压制凶煞杀气 | **正确** · A | E04申鹤《角色故事5》明确支持主作用及压抑情绪副作用；服饰考据被分开标示。全角括号E未被识别。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q016/evidence.json) |
| [Q017](../../data/generation/g7-70q-baseline-20261117/records/Q017/result.json) · completed | 花灵一族常说自己有三位「母亲」。这三位分别是谁？<br>答：只答草木、甘露、鲜花 | **部分正确** · A | E01加载提示和E02圣遗物确有三称；未回答这些称谓分别指谁。Clean同一圣遗物其他部位还有草木女王、旧水神化甘露等桥梁。按实体识别题计不完整。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q017/evidence.json) |
| [Q018](../../data/generation/g7-70q-baseline-20261117/records/Q018/result.json) · completed | 按照阿贝多所说的炼金术四阶段体系，第三阶段是什么？<br>答：赤成 | **正确** · B | E01/E02考据转述四阶段顺序，答名正确且承认未读语音；情感炼化等扩写不能算独立官方核验。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q018/evidence.json) |
| [Q019](../../data/generation/g7-70q-baseline-20261117/records/Q019/result.json) · partial | 蒙德传承至今的西风剑术源自哪位历史人物？<br>答：幼狼鲁斯坦 | **正确** · A | E01《西风剑》直接写西风剑术来自鲁斯坦；E04《西风大剑》支持与艾伦德林失传支系的区别。全角方括号未被识别。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q019/evidence.json) |
| [Q020](../../data/generation/g7-70q-baseline-20261117/records/Q020/result.json) · completed | 请梳理利露帕尔、奥尔玛兹、希琳、基斯拉（帕维兹拉万）与胡拉姆丁（希鲁伊）之间最核心的亲属或婚姻关系。<br>答：部分家族关系，漏婚姻与母系关系 | **部分正确** · A | E01碑文支持伴侣、子女及希鲁伊为基斯拉之子；已回读《希鲁伊与希琳的故事》明确“女儿希琳许配给了他”，却未入包。把全部叙述压为同胞关系，未处理义子／婚姻与叙述者差异。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q020/evidence.json) |
| [Q021](../../data/generation/g7-70q-baseline-20261117/records/Q021/result.json) · completed | 古国坎瑞亚位于七国中的哪一个国家地下？<br>答：坎瑞亚地下、七国之外；不答国家 | **部分正确** · A+B | E01阿贝多故事确有“七国之外”“深藏地下”；这不能推出地理上不在某国地下，也不能据此排斥E02的须弥线索。未完成所问位置。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q021/evidence.json) |
| [Q022](../../data/generation/g7-70q-baseline-20261117/records/Q022/result.json) · partial | 反抗高塔孤王迭卡拉庇安的队伍中，那位后来被温迪借用外貌的无名少年是什么种族？<br>答：承认通常理解为人类，最终称未明示 | **部分正确** · A | E03/E06说明少年、友人与借形；同一Clean《温迪·角色故事4》b5520另有“用人类的形体”。答案过度要求逐字种族标签，但未误答为元素精灵。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q022/evidence.json) |
| [Q023](../../data/generation/g7-70q-baseline-20261117/records/Q023/result.json) · completed | 反抗高塔孤王时，女猎手阿莫斯使用的武器是什么？<br>答：弓／阿莫斯之弓 | **正确** · A | E01武器故事写女猎手射箭，物品身份为弓；“她持有同名弓”为合理关联，答案已限定。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q023/evidence.json) |
| [Q024](../../data/generation/g7-70q-baseline-20261117/records/Q024/result.json) · completed | 神樱大祓剧情中的花散里继承或凝结了哪位稻妻旧友的记忆？<br>答：狐斋宫 | **正确** · A | E01花散里亲口称“确实继承了狐斋宫的记忆”，与引用结论一致。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q024/evidence.json) |
| [Q025](../../data/generation/g7-70q-baseline-20261117/records/Q025/result.json) · completed | 芙宁娜的「孤心沙龙」成员中，外形像章鱼的那一位叫什么？<br>答：乌瑟勋爵 | **正确** · A | E01芙宁娜战技逐项列出“球球章鱼形态的乌瑟勋爵”；形态不依赖E02故事推断。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q025/evidence.json) |
| [Q026](../../data/generation/g7-70q-baseline-20261117/records/Q026/result.json) · completed | 温妮莎反抗旧贵族时期，巴巴托斯伪造的「背风的密约」声称蒙德被贵族卖给了谁？<br>答：邻国岩神／岩王帝君 | **正确** · A | E01温迪《背风的密约》直接写出买方，并明确密约为伪造。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q026/evidence.json) |
| [Q027](../../data/generation/g7-70q-baseline-20261117/records/Q027/result.json) · partial | 璃月传说中，被摩拉克斯以缚龙之法镇压、其遗留祟力与轻策庄传说有关的古老魔物叫什么？<br>答：螭 | **正确** · A | E01《罗尔德日志·轻策庄》支持传说中的螭与镇服；区分考据与罗尔德的怀疑。全角引用未被识别。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q027/evidence.json) |
| [Q028](../../data/generation/g7-70q-baseline-20261117/records/Q028/result.json) · completed | 渊下宫的人造太阳「大日御舆」是谁建造的？<br>答：阿倍良久 | **正确** · B | E02白夜国汇编直载建造者；E01乌帽子对白称阿布拉克。核心名相符，但未完成两种名字的一级文本身份桥梁。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q028/evidence.json) |
| [Q029](../../data/generation/g7-70q-baseline-20261117/records/Q029/result.json) · completed | 稻妻旧时的歌牌游戏中，谁曾因总是落败而一度吃不到狐斋宫准备的点心？<br>答：雷电影 | **正确** · A | E01《角色故事3》完整支持败局、未得糕点、练习后获胜；没有倒置先后。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q029/evidence.json) |
| [Q030](../../data/generation/g7-70q-baseline-20261117/records/Q030/result.json) · completed | 虎千代（御舆千代）的两个孩子分别是谁？其中谁是亲生、谁是养子？<br>答：道启亲生，长正养子 | **正确** · A+B | E02《倾落伽蓝》的文内历史文章明确长正为千代养子、道启养弟；亲生及半鬼细节还依赖E01考据，后者不可冒充独立官方证明。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q030/evidence.json) |
| [Q031](../../data/generation/g7-70q-baseline-20261117/records/Q031/result.json) · completed | 漩涡之魔神奥赛尔的妻子是谁？<br>答：跋掣 | **正确** · B | E01/E02同一敌人页面以“老婆”直述；核心对应成立于汇编，未独立回读任务对白。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q031/evidence.json) |
| [Q032](../../data/generation/g7-70q-baseline-20261117/records/Q032/result.json) · partial | 西风骑士团的创立者、初代团长是谁？<br>答：温妮莎 | **正确** · A | E03/E04书籍描述明确创始者；答案对“初代团长”字面头衔的保留不影响人物识别，但显得僵硬。圆括号E未被识别。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q032/evidence.json) |
| [Q033](../../data/generation/g7-70q-baseline-20261117/records/Q033/result.json) · UNKNOWN | 温妮莎原本来自哪个国家？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN，无HTTP状态及终态用量；不判温妮莎国籍答错。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q033/evidence.json) |
| [Q034](../../data/generation/g7-70q-baseline-20261117/records/Q034/result.json) · completed | 蒙德贵族时代，温妮莎曾被迫与哪条魔龙决斗？<br>答：乌萨 | **正确** · B | E01历史考据、E02组织整理及E03/E04条目一致；核心名有支持，额外封印细节仍为整理内容。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q034/evidence.json) |
| [Q035](../../data/generation/g7-70q-baseline-20261117/records/Q035/result.json) · completed | 稻妻旧时童谣中并称的四位著名武人是谁？<br>答：大手门荒泷、胤之岩藏、长蛇喜多院、雾切高岭 | **正确** · A | E02《喜多院十文字》直接给出完整童谣；明确原先还有其他名字。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q035/evidence.json) |
| [Q036](../../data/generation/g7-70q-baseline-20261117/records/Q036/result.json) · completed | 清籁岛上，被封印的雷音权现是谁释放出来的？<br>答：浅濑响 | **正确** · B | E01/E02考据、E03编年整理一致支持；答案自己承认未直接读《破魔之弓》。不能把三份转述当独立剧情证明。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q036/evidence.json) |
| [Q037](../../data/generation/g7-70q-baseline-20261117/records/Q037/result.json) · completed | 五大护法夜叉中，擅长衣物设计、连帝君的衣装都与其审美有关的是谁？<br>答：弥怒／心猿大将 | **正确** · A+B | E02海灯节魈的对白直接支持服装与帝君；E01汇编补上弥怒与心猿大将映射，E03列五夜叉。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q037/evidence.json) |
| [Q038](../../data/generation/g7-70q-baseline-20261117/records/Q038/result.json) · completed | 枫丹巡轨船的三条水道线路分别叫什么？<br>答：卡雷斯线、克莱门汀线、娜维娅线 | **正确** · A | E01/E02格罗斯利对白直载三名；E03攻略只列两条运营线路，答案未让攻略覆盖对白。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q038/evidence.json) |
| [Q039](../../data/generation/g7-70q-baseline-20261117/records/Q039/result.json) · UNKNOWN | 后来成为「女士」的罗莎琳，在五百年前所爱的人是谁？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；没有可评分的罗莎琳恋人答案。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q039/evidence.json) |
| [Q040](../../data/generation/g7-70q-baseline-20261117/records/Q040/result.json) · completed | 长蛇喜多院是什么身份背景的人物？他所属的家系长期承担了什么职责？<br>答：喜多院武人、家系诛祟并任八酝守 | **正确** · A+B | E02武器故事直接支持家系职责与武人地位；“长蛇＝文宗”来自E01考据，应保持此层区别。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q040/evidence.json) |
| [Q041](../../data/generation/g7-70q-baseline-20261117/records/Q041/result.json) · UNKNOWN | 按剧情中大胃王挑战的表现，把瓦雷莎、玛薇卡、伊安珊和派蒙从饭量最大到最小排序。<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；饭量排序未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q041/evidence.json) |
| [Q042](../../data/generation/g7-70q-baseline-20261117/records/Q042/result.json) · UNKNOWN | 旅行者的角色语音中，养派蒙一个月大约要花多少摩拉？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；月饮食费用未交付，保留现有角色语音即可供日后复核。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q042/evidence.json) |
| [Q043](../../data/generation/g7-70q-baseline-20261117/records/Q043/result.json) · UNKNOWN | 埃洛伊在自己的早年经历中，最初获得的称号是什么？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；埃洛伊称号未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q043/evidence.json) |
| [Q044](../../data/generation/g7-70q-baseline-20261117/records/Q044/result.json) · partial | 纳塔古龙的至高领主CL-04「众命途的隐者」后来对应哪位角色？<br>答：仅给CL-04古代名，无现代角色对应 | **证据不足** · A+B | E04评议会只支持第四席姓名、缺席与研究内容；不能据当前包给现代身份。谨慎保留合理，但没有完成问题，也不能确认全库无桥梁。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q044/evidence.json) |
| [Q045](../../data/generation/g7-70q-baseline-20261117/records/Q045/result.json) · UNKNOWN | 五百年前，哪位「沃陆之邦」英雄曾在摔跤中正面赢过尚未成神的玛薇卡？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；沃陆之邦摔跤英雄未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q045/evidence.json) |
| [Q046](../../data/generation/g7-70q-baseline-20261117/records/Q046/result.json) · completed | 迪奥娜无论如何调酒都容易变得好喝，这份祝福来自哪位纯水精灵？<br>答：只答泉水精灵，遗漏卡莉露 | **部分正确** · A | E01支持祝福来源；BGE第3且已回读的《幽潭心》明确卡莉露为纯水精灵、清泉镇精灵，却被not_selected。答称“没有名字”与已回读证据冲突。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q046/evidence.json) |
| [Q047](../../data/generation/g7-70q-baseline-20261117/records/Q047/result.json) · partial | 雷内童年生活过的水仙十字院，当时担任院长的纯水精灵是谁？<br>答：莉利丝 | **正确** · B | E01水仙十字院管理人员直接称院长为纯水精灵莉利丝；E02为同主题汇编。圆括号E未被识别。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q047/evidence.json) |
| [Q048](../../data/generation/g7-70q-baseline-20261117/records/Q048/result.json) · UNKNOWN | 水仙十字院副院长贝瑟·埃尔顿曾任枫丹海军司令，他所率「白色舰队」的旗舰叫什么？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；白色舰队旗舰未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q048/evidence.json) |
| [Q049](../../data/generation/g7-70q-baseline-20261117/records/Q049/result.json) · UNKNOWN | 温迪（巴巴托斯）的弓术/普通攻击天赋名称是什么？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；温迪普通攻击天赋名未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q049/evidence.json) |
| [Q050](../../data/generation/g7-70q-baseline-20261117/records/Q050/result.json) · partial | 雷穆利亚时期的四位大调律师分别是谁？<br>答：只答波爱修斯一人 | **部分正确** · A | E02加载提示直接支持；召回候选另有明确大调律师优恩尼娅、奥里略斯的碑铭等，均未读。所谓“其余姓名不详”把局部未见泛化了。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q050/evidence.json) |
| [Q051](../../data/generation/g7-70q-baseline-20261117/records/Q051/result.json) · completed | 安柏的祖父原本来自哪个国家？<br>答：璃月港／璃月 | **正确** · A | E01安柏《角色故事2》“他来自璃月港”直接支持；“国家还是港口”旁支保留无助作答。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q051/evidence.json) |
| [Q052](../../data/generation/g7-70q-baseline-20261117/records/Q052/result.json) · UNKNOWN | 2.3版本雪山剧情中，算上阿贝多本人，一共出现了几个拥有阿贝多外形的生物？分别可概括为什么？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；阿贝多外形生物数量未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q052/evidence.json) |
| [Q053](../../data/generation/g7-70q-baseline-20261117/records/Q053/result.json) · UNKNOWN | 芙宁娜传说任务中最终登台演出的那部音乐剧叫什么？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；音乐剧名未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q053/evidence.json) |
| [Q054](../../data/generation/g7-70q-baseline-20261117/records/Q054/result.json) · completed | 璃月两大著名菜系中，新月轩与琉璃亭分别主打什么菜系？<br>答：新月轩月菜海味，琉璃亭璃菜山珍 | **正确** · A | E01喜儿对白对应新月轩与月菜，E03剧情按顺序列山珍／海味及两馆；E02考据为补充。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q054/evidence.json) |
| [Q055](../../data/generation/g7-70q-baseline-20261117/records/Q055/result.json) · completed | 赤王七柱中，被称为「羊之王」并担任宰相者的真名是什么？<br>答：赫里沙夫 | **正确** · A | E03艾瑟娜对白直接点名“羊之王·众相之相赫里沙夫”；E01/E02向导笔记补职责。开头希伯来／埃及转写旁注缺引证，应删。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q055/evidence.json) |
| [Q056](../../data/generation/g7-70q-baseline-20261117/records/Q056/result.json) · completed | 传说中，最初接受燃素并将其带给纳塔人类的那名人类是谁？<br>答：传说中的盗火贤者瓦萨克拉胡巴肯 | **正确** · A | E01奇科娅、E02曼科确实这样讲传说，符合题目“传说中”限定；不得外推为已经裁定真实物种或历史。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q056/evidence.json) |
| [Q057](../../data/generation/g7-70q-baseline-20261117/records/Q057/result.json) · UNKNOWN | 玛薇卡在现代召集的六大部族英雄分别是谁？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；六部族英雄名单未交付，虽已有多轮研究也不计答案。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q057/evidence.json) |
| [Q058](../../data/generation/g7-70q-baseline-20261117/records/Q058/result.json) · UNKNOWN | 五百年前悬木人的战士中，与玛薇卡从小相识、并比她更早获得古名的人是谁？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；玛薇卡儿时战友未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q058/evidence.json) |
| [Q059](../../data/generation/g7-70q-baseline-20261117/records/Q059/result.json) · completed | 无相之水的代号/名字是什么？<br>答：希伊 | **正确** · A | E02敌人背景与E01卡牌故事都明确写“代号为希伊”。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q059/evidence.json) |
| [Q060](../../data/generation/g7-70q-baseline-20261117/records/Q060/result.json) · partial | 「诺奇特」是谁曾经使用过的化名？<br>答：无据断言诺奇特＝渊上 | **错误** · A | E01/E03仅证明笔记男子自称诺奇特；E05证明渊上伪装特诺约洛钦、留下诺奇特梨服装。两条身份链无桥梁，答案却称可确认。错误在断言与引证，不在本审计另造真名。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q060/evidence.json) |
| [Q061](../../data/generation/g7-70q-baseline-20261117/records/Q061/result.json) · completed | 毕弗隆斯是否是「螭」的魔神名？二者能否直接对应？<br>答：不能确认毕弗隆斯＝螭 | **正确** · A+B | E04任务只述螭传说，未给此魔神名；答案保留“未命中≠不存在”，核心否定对应合理，但“官方图鉴明确”是来源升格。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q061/evidence.json) |
| [Q062](../../data/generation/g7-70q-baseline-20261117/records/Q062/result.json) · completed | 坎瑞亚五大罪人中，哪一位与戴因斯雷布有血缘关系？<br>答：预言家维瑟弗尼尔，戴因哥哥 | **正确** · A | E01《睡前故事》同段列五罪人并称其为戴因哥哥，支持完整。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q062/evidence.json) |
| [Q063](../../data/generation/g7-70q-baseline-20261117/records/Q063/result.json) · completed | 卡利贝尔只有一半坎瑞亚血统，他的另一半血统来自哪个国家？<br>答：蒙德血统 | **正确** · A | E02埃德亲述母亲有蒙德血统；E01考据存在额外推测，不影响本题直接依据。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q063/evidence.json) |
| [Q064](../../data/generation/g7-70q-baseline-20261117/records/Q064/result.json) · UNKNOWN | 钟离为什么特别排斥某些海鲜，而经过处理、看不出原形的海鲜料理又可能接受？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；钟离海鲜偏好解释未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q064/evidence.json) |
| [Q065](../../data/generation/g7-70q-baseline-20261117/records/Q065/result.json) · UNKNOWN | 现行剧情所说的远古「三月」中，哪一轮月亮体积最小？<br>答：无最终答案 | **无法评价** · — | URLError，UNKNOWN；三月大小比较未交付。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q065/evidence.json) |
| [Q066](../../data/generation/g7-70q-baseline-20261117/records/Q066/result.json) · completed | 魔女会成员「魔女M」的全名是什么？<br>答：安雅·玛丽·安德斯托特 | **正确** · B | E01魔女会整理给全名，E02—E04 PV署名只支持姓与代号；已承认托／多异写，但全名未获一级文本独立复核。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q066/evidence.json) |
| [Q067](../../data/generation/g7-70q-baseline-20261117/records/Q067/result.json) · completed | 罗莎莉亚幼年被盗贼团带走，后来西风骑士团剿灭盗贼团后，是谁将她带到教会接受教化？<br>答：法尔伽 | **正确** · A | E01罗莎莉亚《角色故事4》明确团长带她到蒙德并嘱去教会，满足人物识别。无需额外纠结亲手送进教堂。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q067/evidence.json) |
| [Q068](../../data/generation/g7-70q-baseline-20261117/records/Q068/result.json) · partial | 伊涅芙的前身在纳塔古龙体系中的称号是什么？<br>答：只答悖谬，未答来源称号明晨之镜 | **部分正确** · A | E01/E02已提示分裂与镜像；BGE43→9→21但未读的角色语音更明确“诞生之初…明晨之镜的一个念头”。应答源于明晨之镜、分出部分曾称悖谬，不能简单说她就是完整领主。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q068/evidence.json) |
| [Q069](../../data/generation/g7-70q-baseline-20261117/records/Q069/result.json) · completed | 5.6版本相关剧情中，阿贝多为来自希穆兰卡的小杜林炼成新肉身时，使用了哪三类关键素材？<br>答：童话之魂、龙心血液、魔兽碎块与胚胎 | **正确** · B | E01骑士团整理完整列炼金模型，实际三类物质素材亦均提到；E02/E03只述仪式。跨版本推论被标出，未完成任务对白独立验证。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q069/evidence.json) |
| [Q070](../../data/generation/g7-70q-baseline-20261117/records/Q070/result.json) · partial | 与旅行者同行的冒险家玻娜的父亲是谁？他与菲耶蒂娜之间有什么传承关系？<br>答：父亲未具名；未证明菲耶蒂娜关系 | **证据不足** · A | E01只支持未归冒险者、遗物与未见过父亲；E02只支持瓦雷莎致敬菲耶蒂娜。未找到父系关系是可保留的缺口，猜用户混淆没有支持。 [证据](../../data/generation/g7-70q-baseline-20261117/records/Q070/evidence.json) |

## 7. 证据索引与限度

- [question-reviews.json](../../data/generation/g7-70q-baseline-20261117/audit/question-reviews.json)：70条人工语义判断、运行分桶、依据与问题标签；[summary.json](../../data/generation/g7-70q-baseline-20261117/audit/summary.json)：汇总与交叉表。
- [review-input.json](../../data/generation/g7-70q-baseline-20261117/audit/review-input.json)：原始答案与Packet正文的只读摘录；[source-bindings.json](../../data/generation/g7-70q-baseline-20261117/audit/source-bindings.json)：149处被答案文字提及的在包E证据、149份回读回执与98份独立Clean记录的SHA-256均与绑定一致。包含程序未识别的括号引用及被提及的无关E号，故149不是“149条支持结论的有效引用”。哈希一致只证明来自被绑定语料，不证明内容权威或推理成立。
- [representative-traces.json](../../data/generation/g7-70q-baseline-20261117/audit/representative-traces.json)：6道代表题中匹配诊断线索的既有候选／回读摘录与来源引用；部分同Chunk在不同query出现，disposition按query/group定位，不可当成新增独立证据。未匹配项仍在链接的原始group文件中。
- [source-excerpts.json](../../data/generation/g7-70q-baseline-20261117/audit/source-excerpts.json)、[second-excerpts.json](../../data/generation/g7-70q-baseline-20261117/audit/second-excerpts.json)：为代表问题定向检索的Clean原文、block与origin ID。包含候选之外的审计补读，不是新一轮召回结果或新金标库。
- 本轮没有全库逐字语义复核、Raw重新解析、付费重答、连续多轮会话验收或最终用户产品采用。对无充分来源的姓名／年份／四人名单继续保留未知。

**审计到此结束。**下一步应由操作者审阅后单独选定有限优化范围；S0、BGE、默认入口及所有原始运行／UNKNOWN记录保持原状。
