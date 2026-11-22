# Clean RAG 工作台

本页给出六阶段实现、配置、数据和本地操作入口。[当前阶段](current-phase.md)负责选择与授权，[数据目录](../data/README.md)负责持久化路径与依赖。

日常连续对话使用[个人聊天软件](local-chat.md)。下文列出研究工作台、命令行与原 Streamlit 入口。

## 六阶段交付

| 阶段 | 代码与合同 | 配置或输入 | 正式产物与入口 |
| --- | --- | --- | --- |
| Clean | [`clean/`](../src/genshin_corpus/clean)、[Clean V1](phases/clean-v1.md) | Raw run manifest、可配置的 source/locale | Clean V1 manifest、逐记录结构及 Raw locator。模块 `genshin_corpus.clean` 的 `build`、`export` 和 `validate` 见合同。 |
| Chunking | [`chunking/`](../src/genshin_corpus/chunking)、[V2/V2.1](phases/chunking-v2.md) | Clean manifest、[全量 selection](../config/chunking-full-v2-20261107.json)及 V2.1 准入策略 `gc-story-admission-pilot-4` | 已验证的 V2.1 balanced 扁平导出。构建及有限试验用 `genshin_corpus.chunking.pilot`，图校验用 `genshin_corpus.chunking.audit_unreachable`。 |
| Indexing | [`indexing/`](../src/genshin_corpus/indexing)、[索引输入合同](phases/indexing-input-v1.md) | [在用索引选择器](../config/index-selected-v1.json) 绑定统一输入包及两个索引的 manifest | BGE-M3 1024d Dense、BM25F 及独立的 Sparse。输入准备和验证用 `genshin_corpus.indexing`。历史物理构建与独立搜索见[索引合同](phases/index-benchmark-v1.md)及 [Sparse 构建合同](phases/sparse-build-v1.md)。 |
| Retrieval | [`retrieval/clean_based/`](../src/genshin_corpus/retrieval/clean_based)、[候选接口](phases/clean-retrieval-v1.md) | [S0](../config/clean-retrieval-v1.json)、显式 [S2](../config/clean-generation-s2-v1.json) | 返回候选、来源和融合轨迹。`genshin_corpus.retrieval.clean_based query` 是独立 S0 召回入口，工作台的 `clean_local query` 使用 V2 候选及后续阶段。 |
| Reranking | [`rerank_worker_v2.py`](../src/genshin_corpus/retrieval/clean_based/rerank_worker_v2.py)、[排序与交接合同](phases/clean-reranking-v1.md) | [Reranker 选择器](../config/reranking-selected-v1.json) | 输出完整有序候选与评分绑定。工作台串接本地打分。冻结 P4 交接由 [`run-rerank-handoff.ps1`](../scripts/retrieval/run-rerank-handoff.ps1) 导出或核验。 |
| Generation | [`generation/`](../src/genshin_corpus/generation)、[`rag/`](../src/genshin_corpus/rag)、[Generation V1](phases/clean-generation-v1.md) | [有限 Live 配置](../config/clean-generation-v1.json)、[安全样例](../config/clean-generation-v1.example.json) | `genshin_corpus.rag.clean_local` 生成 provider-neutral Evidence Packet。Mock 对话用 `genshin_corpus.rag.clean_cli mock` 或 [`ui/clean_chat_app.py`](../src/genshin_corpus/ui/clean_chat_app.py)。远程研究入口为 `genshin_corpus.rag.clean_research_live`，须匹配显式有限许可。 |

## 运行前提

在仓库根用 PowerShell 7 执行。解释器使用现有隔离的 Windows AMD64 CPython 3.14.5 环境。运行工作台需本地模型、CUDA、索引、统一输入包、Chunk 与 Clean，以及选择器绑定的兼容性证明。查询额外占用的工作目录应为 `data/generation/` 下尚不存在的直接子目录。

| 用途 | 环境与依赖 |
| --- | --- |
| Generation / Mock UI | `.local/runtime-py314/generation/Scripts/python.exe` · [Generation 锁](../requirements-py314-generation.lock) · [UI 锁](../requirements-py314-generation-ui.lock) |
| Dense / Sparse 编码 | `.local/runtime-py314/dense-sparse/Scripts/python.exe` · [Dense 依赖](../requirements-py314-dense.txt) |
| BM25F 查询 | `.local/rag-index-prep-20261109/bm25f-runtime/Scripts/python.exe` · [运行时迁移说明](research/python314-migration-20261113.md) |
| BGE 重排 | `.local/runtime-py314/reranker/Scripts/python.exe` · [Reranker 依赖](../requirements-py314-reranker.txt) |

## 离线运行

S0 单问题示例。它执行本地检索和重排，并把候选、回读、取舍和 Evidence Packet 写到新目录。

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$python = './.local/runtime-py314/generation/Scripts/python.exe'
& $python -m genshin_corpus.rag.clean_local query `
  --output "data/generation/local-query-$stamp" `
  --question '前任大贤者阿扎尔出身于哪个学派？' `
  --context-tokens 16000 --output-tokens 1000 --thinking-tokens 0
```

需要比较冻结 S2 时，在独立的新输出目录调用同一入口并加 `--combination S2`。只复核已保存的 P4 交接时，使用同一模块的 `handoff --question-id Q007`，并显式传入新 `--output` 和三个 token 预算。此路径从已保存排序组装 Packet，无需模型推理。

显式以 Mock 模式启动独立本地对话 UI：

```powershell
$env:PYTHONPATH = 'src'
$env:GC_CLEAN_CHAT_MODE = 'MOCK'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$env:GC_CLEAN_CHAT_ROOT = (Join-Path (Get-Location) "data/generation/mock-ui-$stamp")
& './.local/runtime-py314/generation/Scripts/python.exe' -m streamlit run src/genshin_corpus/ui/clean_chat_app.py
```

## 研究入口与历史接口

`clean_research_live` 的准备流程、许可校验、停止策略和恢复规则见 [Generation 合同](phases/clean-generation-v1.md)。本地工作台、目录留存和文档示例不启用远程请求。查询质量、工程验收和正式 Serving 采用分别记录。原 RU/Qwen3、Legacy Streamlit 和 Phase05 历史接口由[研究索引](research/README.md)查找。
