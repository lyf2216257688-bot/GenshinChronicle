# Gemini / DeepSeek 文档 Card 独立盲测准备

以下是准备完成时的历史检查点。后续用户实际启动 Gemini 并授权恢复接续；当前进度见
[换行恢复与接续报告](p05-gemini-blind-newline-recovery-20261030.md) 和 `docs/current-phase.md`。

本轮只授权准备。五份测试资料、共同输入、两个执行入口和匿名评审工具已准备，
**尚未生成任何测试 Card，也未调用 Metro**。不宣称已验证 Gemini Card 输出或语义质量。

## 必须披露的执行失误

检查原生权限时误把 `disableSlashCommands=true` 会话设置与 `-p /permissions` 一起使用，
导致该字符串进入模型，而非只读权限报告。发现后停止此类检查并尝试终止进程；会话已经结束。
这违反本轮不生成的边界。该诊断发生在五份资料准备前，没有交付测试原文或历史 Card。

- 一次诊断会话，日志有 **5 条模型 HTTP 请求记录**，不是五份 Card 请求。
- CLI 终态上报 input **61,931**、output **1,894**、total **63,825**，thinking **1,429**；
  thinking 与其他计数的重叠关系不另作推断。耗时 **44.0467535 秒**，实际费用 **UNKNOWN**。
- 原始 stdout/stderr/CLI log、终态用量和原因封存在控制目录 `incident/`。
- 不把它计作测试结果、成功的零调用权限探针或此前剩余实验授权；本轮不能报告“总模型调用为零”。
- 后续只做离线检查，不再执行任何 agent/slash 诊断。实际 Gemini 工作目录另用全新的 `materials/`。

## 样本与共同阅读范围

| 编号 | Canonical 来源 | 结构 | 交付正文字符 | 全部 RU 可见字符 | RU 数 |
|---|---|---|---:|---:|---:|
| T01 | 323 天空之翼 | 短武器背景 | 453 | 14,963 | 41 |
| T02 | 501 林间风 | 两卷传说/书籍 | 1,891 | 5,809 | 13 |
| T03 | 4943 千夜故事集 | 六卷长篇、多层叙述 | 10,064 | 27,369 | 56 |
| T04 | 2290 「手鞠游戏」 | 任务及分支对话 | 2,174 | 9,820 | 44 |
| T05 | 2374 今昔剧画之鬼人 | 材料背景、短传说 | 632 | 12,650 | 47 |

全部 RU 可见字符含结构化表示、重复表示及机制字段，不能当成独有剧情长度。
共交付 15,214 字符。选择只使用类型、长度、来源身份和章节元数据：先排除历史 Card
身份/标题及研究报告已讨论标题，再在五个预定类型/长度层内按
`SHA256("p05-independent-blind-20261030|" + source_id)` 取首项。
本地历史筛查处理 194 个 Card/来源文件，识别 164 个来源身份、162 个标题；
不读取旧答案来构造问题或正确答案，不能证明外部私人会话从未见过这些资料。

长篇和传说用于观察多主体、行动归属、叙述层级及具体潜在线索，不预判应发现哪条联系。
没有编写答案键或指定正确人物关系；隐含关系是否成立留待匿名评审。

按现有 narrative reading policy 选择原文；Canonical 实际文件哈希、RU 全行重投影、
来源/分支/定位与未交付区间均保留。每份 selected 范围一次直接交付，长篇不分批，
两边各 **5 次逻辑调用**。文档级目标要求综合全部交付段落；不把选定阅读范围冒充整个
Canonical 页面已读，也不把任何遗漏表示宣称为整章未读。

## 目录、Prompt 与接口

独立根：`E:\AGY_Workspace\p05-card-blind-20261030-r1`。
**仅 `materials/` 作为 agy 的 cwd / 新项目**，不添加主仓或额外目录。

`materials/` 仅含五份原始 Canonical、必要 RU、阅读计划、证据映射、共同请求文本、
现有 Card Prompt/Schema、通用说明与校验清单。没有历史模型输出、审核结论或答案键。
调用前驱动读取 `Txx/request.txt`，把同一材料通过 stdin 交付，模型无需读盘工具。

控制目录：`data/retrieval/p05-card-blind-prep-20261030-r1/`。
`prepared/` 封存源快照、代码与输入；未来 `runs/gemini`、`runs/deepseek` 保存结果。
不向任一模型提供控制目录内的对方结果、历史材料或未来匿名密钥，CLI 工具规则也拒绝读取；
这一边界不是对底层进程系统权限或原生自动上下文的完全隔离证明。

- Gemini：实际 catalog 确认 `gemini-3.8-flash-high`；本机 CLI 1.4.10。
  原生 `--json-schema card.schema.json`；新项目、新进程、单轮 stdin `user` 事件，
  `--input-format stream-json --output-format stream-json`，不使用 continue/conversation。
  已研究 `--output-format json`，但驱动统一使用 stdin/NDJSON 避免 Windows 参数长度限制。
  只从唯一终态 `result.response/structured_output` 提取 Card，二者必须相同；
  stdout 原件、stderr、CLI log、stdin、调用参数、原生 usage 与最终 Card 分别保存。
  中间推理事件只作审计，不作为 Card；原生 HTTP 请求体和未暴露思考无法由 CLI 驱动证明。
- DeepSeek：原有 Metro `deepseek-v4.1-flash`，非流式 Chat、`thinking.type=disabled`、
  `json_object`、`max_tokens=12288`；复用 `metro_chat` 和现有全局账本，不另建 Metro 传输。
- 两边共用当前 `card_generation.PROMPT`、完整 `cards_v2.SCHEMA`、来源别名和严格
  `decode/validate_value`；精确范围由程序确定。没有修改 Card 语义标准或 Link 合同。
- 每份已核对 Gemini 请求中的共同语义文本与 DeepSeek system/user 文本、原文范围和 Schema。
  这是受控任务材料一致，**不等于 CLI 内部 system/tool 上下文与 API 完全相同**。

## 隔离边界与已知风险

使用 `--sandbox`、`allowNonWorkspaceAccess=false`，显式 deny 所有 read_file、write_file、
command、read_url、execute_url、mcp 操作，包括工作区文件；源文由驱动注入。
不使用跳过权限、不加入主仓、不复用会话。原生 settings 加载日志证实六类拒绝规则被识别；
意外诊断的原始日志也保留了工具被拒绝的证据，但这不构成一次受控隔离测试。

受限 Codex 进程中的元数据检查报告 Windows sandbox 不可用、登录凭据不可读；
正常用户进程可列模型，另一次原生日志显示启用 terminal sandbox。
**没有完成系统级逃逸测试，不能宣称 OS 级完全隔离。** 官方文档还说明 Windows 沙箱对
用户目录外文件有特殊可读边界，故不能仅依赖 cwd 或 sandbox 开关。

同一用户的全局插件/规则、原生自动上下文及账号侧记忆未获完整隔离证明，CLI 内部模型
请求数/自动恢复也不能由单次进程计数保证。实际五份测试尚未运行，原生结构化 Card 输出
仍待验证。每边最多五次驱动调用，**Gemini 五次 CLI 调用不保证仅五次上游模型请求**。
TokenBudget 是驱动派发/累计守卫，不是 Gemini 服务端硬上限；两边思考预算与系统开销
不同，不能把原生总 Token 当作完全公平的模型成本测量。

官方依据：[CLI headless/结构化输出](https://antigravity.google/docs/cli/headless)、
[权限规则](https://antigravity.google/docs/permissions?tab=cli)、
[Windows 沙箱边界](https://antigravity.google/docs/sandbox?tab=cli)。
未为隔离更改全局配置、凭据、历史会话或原始档案。

## PowerShell 7 入口

以下脚本已放在独立根；不加 `-Execute` 只预览，已实际运行两个默认入口，零测试请求。
本轮不执行以下带 `-Execute` 的命令。用户决定开始时：

```powershell
$BlindRoot = 'E:\AGY_Workspace\p05-card-blind-20261030-r1'
& "$BlindRoot\Run-Gemini.ps1" -Execute
& "$BlindRoot\Run-DeepSeek.ps1" -Execute
```

每边可先加 `-MaxRequests 1`；之后以相同脚本继续，已完成请求验证后跳过。
默认累计 TokenBudget=500000（非费用报价）；失败、pending、UNKNOWN 用量阻止后续派发，
不重发。Gemini 有独立跨目录指纹保留；Metro 沿用原全局账本。不要删除保留项来重试。

两边完成后离线运行：

```powershell
& "$BlindRoot\Export-Review.ps1"
```

每份文档独立随机 A/B，语义 Card 原句不改写；原文、空白 rubric 和机械验收计数另附。
评审材料位于控制目录 `anonymous-review/`；映射只存控制目录 `review-key.json`，
不随评审包交付，评审前不打开。已导出包拒绝重随机或覆盖。不让被测模型自行判胜负。
全部输出维持 `INACTIVE / NOT_REVIEWED`，不自动转为人工认可。

35 项离线回归通过，覆盖一致输入、原生 envelope 解析、精确来源、推理分离、失败用量、
重复保护、完成恢复、材料篡改与匿名导出。五份真实资料的 Canonical 字节、Schema、
共同语义输入、未分段范围和工作区清单均核验通过。未生成 Link、Embedding，未 commit/push。
