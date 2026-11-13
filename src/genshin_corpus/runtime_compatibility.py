"""Explicit, sealed CPython migration admission; index identities stay immutable."""
from pathlib import Path

from .evidence_io import digest, read, require, sha, verify

VERSION = "gc-python314-compatibility-1"


def index_space(repo, config, manifest, actual_contract, kind="dense"):
    actual = digest(actual_contract)
    if actual == manifest["space_id"]:
        return actual
    certificate = config.get("runtime_compatibility")
    require(bool(certificate), "Runtime/index identity differs; validated Python migration certificate required")
    folder = Path(repo) / certificate
    verify(folder)
    report = read(folder / "report.json")
    require(report["version"] == VERSION and report["status"] == "PASS"
            and report["checks"] == {k: True for k in ("tokens", "dense", "sparse", "rankings", "official_sparse_kernel")},
            "Incomplete runtime compatibility certificate")
    binding = report["spaces"][kind]
    require(binding["source"] == manifest["space_id"] and binding["target"] == actual
            and binding["manifest_digest"] == digest(manifest), "Compatibility certificate identity differs")
    require(report["code"] == compatibility_code(Path(repo)), "Migration implementation changed; revalidate")
    old, new = manifest["configuration"], actual_contract
    if kind == "sparse":
        require({k: v for k, v in old.items() if k != "backbone"}
                == {k: v for k, v in new.items() if k != "backbone"}, "Sparse representation changed")
        old, new = old["backbone"], new["backbone"]
    require(old["python"] == "3.12.10" and new["python"] == "3.14.5"
            and {k: v for k, v in old.items() if k != "python"}
            == {k: v for k, v in new.items() if k != "python"}, "Only the validated Python ABI migration is admitted")
    for name in ("baseline", "candidate"):
        root = Path(report[name]["path"])
        verify(root)
        require(sha(root / "complete.json") == report[name]["complete_sha256"], "Migration witness changed")
    return manifest["space_id"]


def compatibility_code(repo):
    names = ("runtime_compatibility.py", "runtime_migration.py", "indexing/benchmark_dense.py",
             "indexing/sparse_trial.py", "indexing/sparse_full.py", "indexing/benchmark_io.py",
             "retrieval/clean_based/core.py", "retrieval/clean_based/rerank_core.py",
             "retrieval/clean_based/indexes.py", "retrieval/clean_based/source.py",
             "retrieval/clean_based/trial.py", "evidence_io.py")
    return {n: sha(repo / "src/genshin_corpus" / n) for n in names}
