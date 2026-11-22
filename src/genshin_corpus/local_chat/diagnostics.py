"""Cheap local readiness checks. The RAG pipeline retains full verification at use."""
from pathlib import Path
from ..evidence_io import read, sha
from ..retrieval.bge_reranker_v2_m3 import MODEL_REVISION


def inspect_assets(repo):
    repo = Path(repo)
    failures = []
    try:
        r = read(repo / "config/clean-retrieval-v1.json")
        index = read(repo / r["selection"])
        rank = read(repo / "config/reranking-selected-v1.json")
        for label, relative in (("Dense 解释器", r["dense_python"]), ("BM25F 解释器", r["lexical_python"]),
                                ("重排解释器", rank["python"]), ("Chunk 清单", index["source"]["chunk_manifest"]),
                                ("运行环境兼容性记录", r["runtime_compatibility"]),
                                ("BGE-M3 权重", index["dense"]["weights_dir"])):
            if not (repo / relative).exists():
                failures.append(label)
        reranker = repo / ".local/models/bge-reranker-v2-m3" / MODEL_REVISION
        if any(not (reranker / name).is_file() for name in rank["score_binding"]["files"]):
            failures.append("重排模型文件")
        inputs = set()
        for key in ("dense", "lexical"):
            record = index[key]
            path = repo / record["manifest"]
            if not path.is_file() or sha(path) != record["manifest_sha256"]:
                failures.append(key + " 索引清单")
                continue
            manifest = read(path)
            inputs.add(Path(manifest["input_manifest"]))
            if key == "dense" and any(not (repo / record["weights_dir"] / name).is_file()
                                      for name in manifest["configuration"]["snapshot_files"]):
                failures.append("Dense 模型文件")
        for path in inputs:
            if not path.is_file() or not all(Path(p).is_file() for p in read(path)["source_manifests"].values()):
                failures.append("索引输入包或来源清单")
        if any(not (repo / path).is_file() for path in (
                "data/generation/g5-official-diagnostic-20261114/call/complete.json",
                "data/generation/g3-official-thinking-20261114/audit/complete.json",
                "data/generation/g3-official-thinking-20261114/capabilities.json")):
            failures.append("现有模型协议验证记录")
    except (OSError, KeyError, TypeError, ValueError):
        failures.append("本地配置或索引绑定")
    return failures
