# G3/G4 Git整合收尾

2026-11-14（Asia/Tokyo）。用户明确授权分别提交G3/G4、合并main并推送origin/main。
本次整合不增加付费请求、默认入口采用、G5–G7或上游重建权限。

## 提交与文档汇合

- 基线：双方HEAD为0337963；G3主工作树7个修改/4个新增，G4为8个修改/13个新增（含最终审计）。
  其他三个已有工作树均无未提交改动，保持原分支/锁定状态，没有合并无关历史分支。
- G3独立提交：`e090cd0`，11个文件，保留最新关闭thinking工具/SSE Live方案、runner、测试、报告和文档。
- G4分支：`codex/g4-offline`；独立提交`7953d78`，21个文件，含最终审计的三项修复。
- main执行一次有两个父提交的merge，保留两条工作线的提交归属；没有reset、stash、rebase或cherry-pick。
- 五份共同文档逐项核查。current-phase以G3最新5请求/2,103 Token和已关闭许可为基础，
  加入G4交付、审计及Git授权；Generation合同保留tools-first独立profile与G4会话/恢复合同；
  data README与project-structure同时保留两侧职责；免费参数核查保留G3新结果路由及G4历史限定。
  实际只有current-phase与Generation合同需要人工解冲突，另三份自动合并后核验。
- 旧2请求/212Token批次与新5请求/2,103Token批次保持独立，动态thinking仍NOT_QUALIFIED；
  三个关闭thinking工作点仅OBSERVED_LIMITED_PASS，不等同于真实剧情问答或主动研究验收。

## 整合验证与保护

在主工作树自己的regular-GIL Python3.14.5 Generation环境安装可选UI锁，原框架pins不变，
不修改任何ML环境。使用其自身解释器运行整合回归，测试和子进程从main导入，无G4代码/runtime依赖。

整合Generation/UI/文档回归116项及共享model_calls回归20项，共136项通过；pip check通过。
没有新模型推理或付费请求，未重跑G3 Live。框架弃用警告及AppTest裸上下文提示保留为已知非失败提示。

提交前保存的439个main既有源码、测试、配置、Plan及依赖文件在整合后逐字节hash一致。
特别保留适配器/会话桥接在main的原换行符，既有Call code identity没有用G4的LF文件替换。
Raw/Clean/Chunk、索引、调用账本、冻结证据和历史运行根没有改写；不更新旧hash绕过保护。
新G4文件在main按该checkout的换行规则检出，旧G4开发运行根继续原地保留，不自动迁移或恢复。

日志与保护清单位于主仓库`.local/g34-git-close/`，均Git-ignored。只提交明确列出的源码、测试、
依赖锁和文档，不提交运行环境、完整会话、日志、凭据或生成数据。Git diff检查、文档链接、
冲突标记和暂存文件范围均检查；最终提交与远端分支一致性以Git收尾回执为准。

## 后续工作位置与边界

后续统一使用主仓库main开发。独立G4分支、工作树和忽略的本地历史保留为审计备份，
不删除，也不再作为主程序的代码或解释器路径依赖。PowerShell 7从主仓库根使用
`./.local/runtime-py314/generation/Scripts/python.exe`与已合并的独立opt-in入口即可。

下一步仍是另行批准关闭thinking的小规模真实连续QA工作单，绑定GC实际工具/schema、
Packet/来源/引用、上下文与预算，并分开检查语义质量。无需为该工作点重跑已完成G3探针，
也不必等待动态thinking；动态thinking诊断、G5–G7和正式默认采用仍各自有门禁。
