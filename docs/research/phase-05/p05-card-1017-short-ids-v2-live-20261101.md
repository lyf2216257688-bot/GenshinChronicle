# P05 Card：1017短片段编号修复与真实复测（2026-11-01）

## 结论

本轮修复模型复制长片段ID失败的问题。旧版真实10次中4次因不存在的长片段ID被拒绝；新合同将模型可见编号换成本次请求内唯一的 S0001、S0002…，程序仍保存旧稳定ID、原文范围与RU精确来源；任何错误或跨证据编号均被拒绝，不做模糊猜测。原始数据和旧响应没有被改写。

抽样范围66份不变：任务26份全选、书籍20份全选、其他类别精选20份。模型固定TokenMetro Chat Completions deepseek-v4.1-flash-1017，stream=false，thinking disabled，JSON Object，max_tokens=32000，生成策略 deepseek-card-evidence-32k-20261031。

## 工程改动

- anchors.py：新引用合同 card-evidence-selection-2、局部短编号和程序内稳定ID/原文精确位置映射；旧稳定ID生成规则保留。
- card_generation.py：模型必须复制已提供的短编号，不得猜测或创造；旧生成策略的模型名称未改。
- card_build.py、card_runtime.py：审计版本不符的旧档案时使用封存的原版本代码执行审计，避免拿新版合同错误核验历史档案。
- 单元测试：test_navi_card_build、test_navi_card_evidence、test_navi_card_32k、test_navi_card_generation、test_navi_card_short_ids合计54 PASS；含跨证据拒绝、逆序拒绝、重复原文坐标、旧版审计。
- 新运行根：data/retrieval/p05-card-live66-1017-shortid-v2-20261101-r1；准备哈希 efdd8b6399b0be4ab9c669b71b7b22ba5855beebe13ee1dc2be7f17a7e12dfda。
- 原运行根：data/retrieval/p05-card-live66-1017-20261101-r1；历史10次请求及失败保持不变。

## 真实复测

已执行两个有界批次共18次真实请求；全部HTTP200、全部精确模型deepseek-v4.1-flash-1017、引文合同card-evidence-selection-2，18次CONTENT_VERIFIED，0次引用ID失败、0次未知用量、0次自动重试。

上游报告输入124,148 Token、输出185,335 Token，总计309,483 Token；实际渠道账单金额UNKNOWN。

3/66份来源完整：
- 1160《风暴后遗症》
- 1702《金翅鹏王之章 第一幕「槐柯胡蝶，傩佑之梦」》
- 2599《第二章 第一幕「不动鸣神，恒常乐土」》

其余63份尚未完成，不能宣称66份已经全测。Card全部保持INACTIVE/NOT_REVIEWED/NOT_VALIDATED；Schema与引用PASS不代表剧情理解必然正确。

最终归档审计：旧版10调用0完整，archive_integrity=PASS；新版18调用3完整，archive_integrity=PASS。未运行Link、KG、Embedding，未修改Phase04生产行为，未提交或推送Git。

## 接续条件

66份选源清单及来源快照已封存。后续可沿用新运行根与card-resume继续，不重发历史成功请求或未知扣费请求。优先观察复杂分支、多卷综合和人物行动归属，明确区分引用有效性与语义判断。

## 后续状态（明确保留上面的18次历史检查点）

后续同一短编号档案扩展到66次真实请求后停止：64通过、2失败、10/66完整；
输入467,848、输出683,545、合计1,151,393 Token。失败分别为上游报告输出
32,215超过请求32,000，以及综合假说混用A锚点编号和E来源编号。
因此18次全通过只说明当时的部分改进，不证明整条生成链已经稳定。
新的故障调查、版本化修复和完整66份接续状态由
[修复研究记录](p05-card-live66-repair-20261101.md)拥有，原结果与失败未改写。
