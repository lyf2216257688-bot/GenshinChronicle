"""Cheap local readiness checks. The RAG pipeline retains full verification at use."""
from pathlib import Path
from ..mcp_live.runtime import production_release
from ..retrieval.bge_reranker_v2_m3 import MODEL_REVISION


def inspect_assets(repo):
    repo = Path(repo)
    failures = []
    try:
        release = production_release(repo)
        r, rank = release.settings, release.selection
        for label, relative in (("Dense 解释器", r["dense_python"]), ("BM25F 解释器", r["lexical_python"]),
                                ("重排解释器", rank["python"])):
            if not (repo / relative).exists():
                failures.append(label)
        reranker = repo / ".local/models/bge-reranker-v2-m3" / MODEL_REVISION
        if any(not (reranker / name).is_file() for name in rank["score_binding"]["files"]):
            failures.append("重排模型文件")
        from ..evidence_io import read
        dense = read(repo / r["selection"])["dense"]
        backbone = release.stores["vectors"]["contract"]["dense"]["backbone"]
        if any(not (repo / dense["weights_dir"] / name).is_file() for name in backbone["snapshot_files"]):
            failures.append("Dense 模型文件")
        for key, label in (("clean", "Clean 清单"), ("chunk", "Chunk 清单")):
            if not Path(read(release.descriptor["input"]["path"])[key]["path"]).is_file():
                failures.append(label)
        if any(not (repo / path).is_file() for path in (
                "data/generation/g5-official-diagnostic-20261114/call/complete.json",
                "data/generation/g3-official-thinking-20261114/audit/complete.json",
                "data/generation/g3-official-thinking-20261114/capabilities.json")):
            failures.append("现有模型协议验证记录")
    except (OSError, KeyError, TypeError, ValueError):
        failures.append("本地配置或索引绑定")
    return failures
