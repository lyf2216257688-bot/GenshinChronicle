# G4 真实连续问答有限集成与验证

2026-11-14（Asia/Tokyo），起点 `main@1d5f78b`。当前状态唯一权威为
[current-phase](../current-phase.md)，持久合同为[Generation V1](../phases/clean-generation-v1.md)。

**交付：G2＋G3＋G4真实固定问答链已打通，有限4次实测完成并关闭许可。**
复用真实S0/BGE、原文Evidence Assembly、G3 ChatSessions和G4会话/执行器/Streamlit。
本批有一组三轮连续问答和一组独立剧情题；核心答案、当前引用及限定经Codex逐项对照
交付原文复核。结果是有限样例观察通过，不是独立盲评、G7产品采用或全库质量率。

## 实际交付与边界

- `rag/clean_live.py`新增有限campaign准备/准入、来源绑定的精确请求预览、原G2
  LocalPipeline的惰性接线及CLI。仅扩展G3准入，发送器、model_calls、重试/UNKNOWN政策不变。
- 同chat上下文 → 一次真实S0/BGE → 原文回读/Assembly → 一次SSE答案 →
  引用/来源核验 → 本地持久化。固定TokenMetro / `deepseek-v4.1-flash-1017`，
  wire中thinking始终disabled；这不证明服务端没有任何内部推理。
- 追问保留原问题，以用户原话和来源消息ID补充明确的V2 follow_up查询背景；
  不做模型改写、额外检索、自动补检、主动研究工具循环或Sparse扩展。
- 新chat无历史/notes，仍共用同一campaign预算。关闭/重开不自动执行旧任务。
- Parent只为实际入包证据完整回读并展开，未读候选仍有处置；Parent不自动进入模型Packet。
- UI显式`GC_CLEAN_CHAT_MODE=LIVE`才使用真实模式，默认Mock；旧CLI/正式入口/选择器保持。
  新入口仍是个人本地应用，没有新依赖、云服务、跨chat记忆或多Agent系统。

用户明确授权本轮实施、离线测试和最多10次真实请求，并委托决定Token/超时。
本批实际使用4次，随后创建不可变`live/closed.json`；剩余6次不再使用，没有扩额。
每turn最多1请求，campaign/turn保守上限670,720/67,072；输入65,536 UTF-8字节、
输出1,536 Token、context67,072、请求超时120秒、准入窗口2小时、本地worker30分钟。
max_attempts=1，unknown_retries=0，format_retries=0。以上是有限验证工作点，
不是产品默认、精确厂商tokenizer或人民币硬限。UNKNOWN/重大协议或完整性异常阻断后续。

## 真实问答与来源支撑复核

1. **古华派三大秘术。** 正确回答枪法「刺明法」、剑法「裁雨法」、枪剑双绝「生克法」。
   引用《行秋·神之眼》及“哐哐锵锵王山行”原文，核心名称/兵器对应有直接支撑。
   E01/E02是同一观测的部分重叠范围，不能作为两份独立佐证。
2. **连续追问枪术/剑术名称、第三门为何称枪剑双绝。** 正确承接指代，依据文四爷
   对话解释生克是枪剑并用的独门绝技。当前原问题、search/rerank背景及祖先均留档；
   没有用旧助手回答替代本轮证据。
3. **用户误把枪术当作裁雨，要求改正并解释第三门融合什么。** 模型明确纠正，
   并限定只能确认枪剑并用，原文未逐字说明分别融合刺明法与裁雨法。
   本轮E01为任务对话、E03为行秋词条，编号与前轮变化，引用仍正确绑定当前Packet。
4. **全新chat询问《尘世之锁》的信物性质与是否解开。** 保留少女所称“盟约信物/挑战”
   与叙述中“并无正式契约”的差别；准确说明原文是许多年未能解开，没有推断后来已解开
   或永远无法解开。另附与须弥“石头锁”的区别，有E03/E04原文支撑，但属于非必要延伸，
   回答聚焦仍可改善。新chat的context/recent/summary/notes均为空，没有古华派内容污染。

四个案例核心答案与引用的执行者来源复核为有限PASS；不是模型自评，也不是用户最终验收。
结构/出处完整不等于原始OBC内容全部已被确认为官方客观事实，Raw语义忠实性亦不重新宣称。
完整答案及当前来源在[封存审计报告](../../data/generation/g4-live-20261114-a/audit/report.json)
及[引用原文导出](../../data/generation/g4-live-20261114-a/audit/cited-sources.json)中。

## 请求、Token与费用

| 请求 | 输入Token | 输出Token | 合计 | 实际请求字节 | 入包块数 | HTTP交换耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 15,690 | 289 | 15,979 | 64,099 | 6 | 12.63s |
| 2 | 16,340 | 698 | 17,038 | 65,141 | 5 | 15.12s |
| 3 | 15,873 | 731 | 16,604 | 62,550 | 5 | 8.56s |
| 4 | 16,226 | 677 | 16,903 | 63,758 | 4 | 14.68s |

合计 **64,129输入＋2,395输出＝66,524供应商报告Token**，4请求/4attempt，
0重试、0 UNKNOWN、0超限，全部HTTP200、精确返回模型、完整SSE终态和usage。
4份保守allowance合计268,288，是准入预留而非计费Token；没有消耗10份额度。
第三次usage另含640 cached input tokens，原样保留，不能直接变为真实扣费。
**实际金额/币种 UNKNOWN**：响应和现有可核实材料没有账户账单，未猜人民币价格、
未将费用算作0，也没有调用未经验证的账单端点。

## 资源失败与恢复

第一次真实本地检索在Dense首次编码前被既有1.5GiB RAM门禁拒绝，付费operation/预约/API均为0。
独立有限诊断仅构造Encoder/tokenizer，未加载GPU模型：可用RAM
1,636,200,448→941,490,176字节，进程RSS 40,030,208→958,468,096字节。
用户随后释放内存，可用量回升到5,250,772,992字节（约4.89GiB）。
在证明0个生成operation/预约后，将未封存本地失败输出原样保留到
`local_queries/<turn>-local-ram-failure-01/`，记录`live/local-recovery-01.json`，
显式继续原turn/checkpoint/permit/campaign。没有降低门禁、自动fallback、API重试或增加额度。

第三个真实请求在call/attempt完整封存后、checkpoint提交前注入本地故障。
另起进程、移除凭据并禁止socket连接后，原turn恢复完成，预算前后完全一致，新增请求0。
没有故意中断在途供应商请求；这验证的是已知响应的checkpoint恢复，不能证明真实渠道断流可靠性。
许可关闭后，4个封存调用另全部在无凭据/禁止网络条件下重放，通过且预算不变。
UNKNOWN停止/占额/不重发由离线夹具验证，本批没有遇到真实UNKNOWN。

## 离线、UI与审计

- 115项不同离线检查通过（含新增8项Live路径、G1/G2/G3/G4、UI及模型接口一致性）。
  较广初测中，新增UI测试放置位置错误导致一次NameError，修正后相关检查通过；
  最终受影响的43项G4/Live/UI检查再次通过。日志在`.local/g4-live-20261114/`。
- 新增Live夹具验证跨chat共用预算、追问/纠正、缺许可/过期/无凭据不预约、来源损坏、
  UNKNOWN关闭后续、封存后无凭据恢复，以及completed/partial交付说明一致性。
  均用小型checked-in源夹具和注入SSE，单元测试无真实网络/GPU/全库Raw依赖。
- 真实Live历史经Streamlit AppTest恢复显示，引用/Parent可展开；重复刷新无新提交、外部连接0。
  这是AppTest接线验收，不是浏览器像素截图验收。
- 四个call/attempt seal、请求body与精确预览、当前Packet/读取文件、逐17字节SSE重放、
  每attempt usage/预算及ledger逐项核验通过。400候选/400读取均核验，20块入包，0来源失败。
  单独封存`live-observation/capabilities.json`，记录精确QA profile及4个call seal的有限观察，
  不改请求准备时的NOT_LIVE_VALIDATED标签，不授予未来派发权限。
- `pip check`、diff/链接/忽略范围/凭据扫描及645项保护文件hash复核通过。
  既有框架弃用和Streamlit bare-context提示不影响结果。上游模型/索引/共享调用核心未改。

收尾发现completed结果仍带未完成的`delivery_note`。已仅修正未来completed元数据为空，
并补一项覆盖completed与partial的回归；另去掉worker模块注释中的Mock限定。
没有修改四个历史结果、请求、账本或permit。实测代码字节按permit SHA逐项验证后，
原样保存在`live/code-snapshot/`并封存；恢复这两项收尾差异后hash完全匹配，不改旧hash。
该元数据/注释收尾后仅做离线回归，没有新增付费请求；本批许可始终关闭。

## 限制与使用

- 当前是固定问答链，不能以此宣称复杂剧情研究、必要补检、反证循环或G5–G7已实现/验收。
- 追问采用有界用户原话投影，不是成熟语义指代改写；超长查询或保护上下文不足会显式阻断。
- 每turn子进程重做校验和冷启动，本次本地链为数分钟；模型交换8.56–15.12秒。
  后续可在明确工作单中处理本地reader/worker复用与响应速度，不通过降低证据/资源门禁提速。
- 整块Assembly预算排除了大部分候选，入包少不代表研究充分；本次四例有直接关键证据，
  不能据此推导其它复杂题的召回或组包质量。
- 动态thinking仍未证实；金额未知；实际网络断流/服务可靠性未被本批实证。

本地根`data/generation/g4-live-20261114-a/`保留全部会话、原文、调用、审计与失败记录。
本批关闭后可通过下列PowerShell7命令查看历史（不会恢复付费许可）：

```powershell
$env:PYTHONPATH = 'src'
$env:GC_CLEAN_CHAT_ROOT = 'data/generation/g4-live-20261114-a'
$env:GC_CLEAN_CHAT_MODE = 'LIVE'
& ./.local/runtime-py314/generation/Scripts/python.exe -m streamlit run `
  src/genshin_corpus/ui/clean_chat_app.py --server.address 127.0.0.1 `
  --server.headless true --browser.gatherUsageStats false
```

新的真实问答必须有新的明确有限授权及新permit；不能重开此批或借新chat扩额。
未切换正式默认入口，未删除既有资产，未自动提交/推送Git，未修改外部交接文档。
用户随后明确授权将本轮代码、测试与文档创建本地Git提交；运行数据与凭据不入库，不推送。
