# G4最终审计与Git收尾提案

2026-11-14（Asia/Tokyo）。结论：**有条件通过，可结束G4离线工程，等待授权进行Git收尾。**
条件是先保留main尚未提交的G3工作，再人工汇合共同文档；当前不是可以直接覆盖main的文件集。
本轮没有commit、merge、rebase、cherry-pick、push或付费请求，也没有修改主工作树或交接目录。

## 现场与证据

- main分支与G4 detached HEAD均为`03379637f7bc56f953a1023d92199ca98e8f8376`。
  main有7个已跟踪修改文件、4个新增文件；G4初始有8个已跟踪修改文件和12个新增文件。
  两边未提交成果均保留。本报告和本轮修复加入G4原有改动中。
- 已审阅双方current-phase、Generation合同、正式Plan、G4报告、G3新Live报告、最新runner及相关源码/测试。
  主工作树Git状态通过提权只读检查取得；初次G4交付报告中的权限限制是历史事实，已不妨碍本次审计。
- main的新G3批次：关闭thinking的非流式原生双工具、SSE文本、SSE双工具循环，
  各一个合成样例`OBSERVED_LIMITED_PASS`；5请求/5attempt、0重试/UNKNOWN，
  1,887输入+216输出=2,103供应商报告Token。许可已耗尽关闭；费用币值UNKNOWN。
  动态thinking仍NOT_QUALIFIED。不能以初始2请求/212Token批次的旧状态否定此新观察。
- 只读核验main的preparation、probe report、独立审计三个seal，分别匹配
  `ff31d175…a1c0`、`5c3bdbe5…3e6`、`ab2d7ca6…557`；读取了capabilities的精确profile、
  scope及call-seal绑定。没有重新运行Live或重新发送封存调用。
- 对双方源码/测试按文本比较，main独有语义代码只有`clean_tools_probe.py`和其新测试；
  G4新增模块与其没有同名代码冲突。267个既有源码/测试文件存在仅换行符造成的字节差异。
  原框架依赖锁字节相同，`model_calls.py`字节相同；适配器、会话桥接的文本相同但字节不同。
  **已有调用的代码hash绑定不可忽视换行符**：保留原源码/原运行根，不在另一工作树盲目恢复，
  不更新旧hash绕过保护。文本兼容不等于封存操作可跨根迁移。

## 必须修复的问题与实际修复

三项均先新增确定性回归，在原实现复现失败，再进行修复：

| 缺陷 | 实际影响 | 最小修复 |
| --- | --- | --- |
| Packet已写入、source checkpoint未提交时取消 | source节点重跑因取消跳过读取，试图写入不同Packet，无法完成取消对账 | 复用create-only已发布source文件；验证Packet、ranked/context/原文引用及turn绑定后补checkpoint，不重算成空包 |
| 结果已写入、check checkpoint未提交时取消 | check节点把原completed结果重算为不同取消状态，触发不可变冲突 | 验证并复用既有结果及op/source绑定，原账本继续对账，保留已知结果，不额外派发 |
| 多页面编辑读到旧草稿 | 已提交后的下一条相同编辑可能重复分配不同token，存在重复提交风险 | BEGIN IMMEDIATE内重新读取token/text并决定是否轮换，串行化读改写 |

另修正UI警告：当前入口仍为Mock，但不再断言tools/SSE尚未获得任何真实验证。
不改变实际能力profile或开启Live。G4技术合同与current-phase更新对应的独立能力门禁；
初次实施报告标记为历史检查点并路由本报告，没有改写历史测试事实。

修复涉及新增G4 Store/graph/UI及G4测试，没有修改G0–G3核心或main的新runner。
G4 graph代码身份会随修复改变；旧未完成G4开发根应继续只读，不能自动迁移或重发。

## 工程审阅结果及有限测试

本轮**42项通过**：G4后端30项、Streamlit AppTest4项、main新增G3 runner只读overlay8项。
原始交付的125项是历史检查点，本轮未为形式完整性重跑全部历史测试。

- G4覆盖连续对话/跨进程、chat隔离、提交幂等、上下文余量、原历史回查、纠正依赖失效、
  不提升用户断言/助手文本的事实身份、完整协议单元保护、引用/Parent、预算、UNKNOWN、
  五个持久边界及新增取消交错、关闭/执行器互斥、损坏文件、来源失败和UI操作。
- G3 overlay只将main新runner加入模块搜索路径，用G4既有适配器/共享核心运行其8项Mock测试。
  首次测试harness沿main的`__file__`选择了不可写scratch，失败；随后只在内存重定向测试
  `__file__`至G4的Windows extended path，所有临时输出落G4，8项通过。没有复制/改写G3源码，
  也没有将main付费根交给新代码恢复。该测试不替代原始Live证据。
- 责任边界清晰：SQLite/checkpoint记录流程，GC不可变文件/共享账本记录来源、派发、attempt和预算。
  取消禁止新准入；UNKNOWN不释放预算或换指纹重发；新修复不创建第二套调用政策。
- 引用地址包含turn/Packet；EvidenceReader和Assembly复用既有来源合同，条件、否定及Parent结构
  仍可回查；源失败阻断/部分交付，完整引用检查不是语义支撑判断。
- regular-GIL Python3.14.5、UI可选锁安装环境`pip check`通过，原框架pins未变。
  Job-owned子进程隔离Mock的全局socket保护，应用/session/GPU锁所有权保留，无新增远程服务。
- 447个既有保护文件仍与初始G4清单相同；main源码/测试/配置/文档（含未提交G3文件）
  在本次读取前后完整文件hash清单未变。日志、会话、环境、生成数据均保持忽略状态。

建议优化：长期聊天中UI每次刷新会重复读取验证历史/来源，可在以后测得实际性能瓶颈后优化；
不属于本次合并阻断项。自动语义摘要、指代/纠正质量、主动研究及真实工具闭环属于后续集成/阶段验收，
不是通过增加Mock场景即可证明的能力。

日志及保护清单位于`.local/g4-final-audit/`：`reproduced.log`、`g4-tests.log`、
`g3-overlay-final.log`、`main-protected.json`。三份Live seal只做只读验证。

## 五个共同文档的处理方案

以下方案已生成可审阅的**文档预览**到`.local/g4-final-audit/merge-preview/`，没有执行Git合并，
也没有把main未提交的G3内容覆盖进任何正式文件。预览以最终提交时的main内容再次核对为准。

| 文件 | 汇合规则 |
| --- | --- |
| `docs/current-phase.md` | 以main最新G3段落/5请求结果/已关闭许可为基础；加入G4离线交付和本次审计、修复/下一步；将旧2请求说明限定为历史；不采用G4旧的全能力未验证结论 |
| `docs/phases/clean-generation-v1.md` | 同时保留main的tools-first独立profile合同与G4会话/来源/恢复/UI合同；关闭thinking集成不被thinking未通过一概阻塞；都不产生新调用权限 |
| `data/README.md` | 保留G3单独probe/audit/capabilities数据职责，再加入G4表、artifacts、提交意图和执行器职责；不迁移/覆盖实际数据 |
| `docs/project-structure.md` | 保留`clean_tools_probe.py`职责，叠加G4新增模块/可选UI锁；不改变包/层所有权 |
| `docs/research/clean-generation-g3-parameter-check-20261114.md` | 保留main顶部关于后续tools/SSE结果的历史限定，同时保留G4尾部历史检查点说明；原免费核查结论不擦除 |

main独有的`docs/model-interfaces.md`、G3旧报告增补、新Live方案/报告、新runner/测试全部保留。
G4独有的README、架构、roadmap、源码/锁/测试/报告按G4提交保留。
不得把任一工作树整份共享文档“选ours/theirs”覆盖另一侧。

## Git收尾建议（尚未执行）

1. 获得明确授权后，在G4 detached HEAD上创建`codex/g4-offline`分支，保留当前全部改动；
   按明确文件清单提交G4源码、UI锁、测试和文档。不要在detached HEAD上留下无分支归属的提交。
2. main尚有G3未提交成果，应先由其工作单明确审阅并提交成独立G3续验提交；
   不stash/reset、不从G4覆盖main、不将G3未提交工作混入G4提交。此步骤也需Git授权。
3. 两侧提交都具名后，建议在main工作区干净时进行一次明确merge，保留G3/G4边界，
   手工合并上述五份文档；不需要为了线性历史rebase或重复cherry-pick。
   预览只是解析意图，实际冲突位置以届时diff为准。
4. 在整合基线上运行G4相关检查与新增G3离线runner检查，核对能力scope、忽略清单和原pins，
   重点确认源码换行符未意外改变main封存调用的code identity。若改变则只读历史、新运行另行绑定，
   不修旧hash或借此重发。检查完成再决定是否推送。

提交清单必须排除`.local/`、`data/generation/`、完整会话/日志、环境、凭据及任何冻结证据。
G4离线完成不等于真实连续问答已采用。下一步可制定关闭thinking的少量真实QA工作单：
绑定实际GC schema/工具/Packet、上下文/引用与预算、语义/连续对话案例，并取得新批次授权。
不需要为了该普通工作点重新跑已完成G3探针，也不需要先证明动态thinking。
动态thinking、G5–G7和正式默认入口仍各自保留门禁。
