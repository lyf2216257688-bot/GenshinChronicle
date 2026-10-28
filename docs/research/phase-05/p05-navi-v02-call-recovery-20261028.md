# Navi v0.2 调用恢复验证（2026-10-28）

**内容级跑通一次；全流协议仍有异常，尚不能直接恢复 F2。** 两次独立诊断均非正式 F2 槽位，均无重试。

使用 F2 冻结生成资料《讨龙英杰谭》与《亡国的美奈姬》、冻结 rc1 Prompt、jizhi/deepseek-v4.1-flash、OpenAI SDK + httpx、Responses stream，无 text.format，max_output_tokens=12000（此前同任务成功消耗 10989 输出 Token）。首次完全使用 G1 的顶层 instructions 和 input 分离；HTTP 200、completed、原流和 SDK 一致，消息和 reasoning ID 也一致，但返回询问任务的自然语言、非 JSON。输入2751、输出482、总3233 Token。无法证明服务端在哪里丢弃指令，也不能证明模型权重变化。

第二次按最小修正将相同 Prompt 与资料放入 user input，不发送 Schema；HTTP 200 completed，原始 SSE 与 SDK 事件逐项一致。可见 message 身份、正文 delta/done/终态及关键顺序、usage 独立核验 PASS；输出为合法 JSON，通过完整 v0.2 rc1 Schema 及交付双端 alias 绑定，生成级别 C、发布建议 EXCLUDED 的受限文学对照（NO_FORMAL_NAVI）。模型依据 A001/A004 的虚构屠龙和作者自嘲，以及 B004/B007/B012/B016 的小说故事，认为存在弱类型比较，不据此推断同一故事或历史。定向查阅 A001/A004 和 B004/B007/B012/B016，发现命题所指文本锚点存在；该有限检查不是对全部语义和阅读覆盖的人工认可，也未由模型生成引用冒充原文。

第二次 reasoning output 的 added/done ID `item_2db7672db513dd046fe648e1` 与终止项 `item_f50e035e231ca206f8c61d2b` 冲突：消息可独立核验但全流一致性 FAIL，保留原始事件，没有修补身份。输入5865、输出10823、总16688 Token。两次合计输入8616、输出11305、总19921；费用 UNKNOWN，已完成两次，无本轮 UNKNOWN。此前正式 F2 首条失败与旧探测 UNKNOWN 保留。

独立脚本 `navi_v02_call_recovery.py` 复用 SDK/httpx 原流存档，完成结果单独记录；`navi_v02_message_verifier.py` 区分消息可靠性和全流身份，校验 raw/SDK、response/model/usage、消息 ID、文本、content index 和关键事件顺序，未知输出或消息冲突拒绝。后者在本次请求后加入脚本作为后续执行门槛，并对本次两个完整存档进行离线重验；没有重新调用。新增模拟和现有合同测试共33项 PASS。有限样例无法证明全面的流语义与语义质量，程序不发布任何 Navi。

F2 原实验不应混用这次新输入包装。新版仅一个内容级真实成功，旧 Prompt 尚未在相同包装下验证，reasoning 冲突尚无上游解释；不足以称两臂已有稳定共同调用方式。若后续采用该包装，应建立新版本、两臂统一参数和输入条件、记录完整请求指纹、保存原实验分母，并另行授权正式对照；不自动发送剩余47条。
