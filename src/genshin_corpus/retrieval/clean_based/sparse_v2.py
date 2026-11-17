"""Read-only G6 Sparse selection. Frozen P3/P4 artifacts are never rewritten."""
from pathlib import Path

from ...evidence_io import digest, inside, read, require, sha
from .query_v2 import validate_profile_v2


def selected_sparse(repo, config, dense, binding):
    validate_profile_v2(config, "S2")
    reference_path = Path(repo) / config["sparse_selection"]
    binding.bind(reference_path, config["sparse_selection_sha256"])
    reference = read(reference_path)
    require(all(config[k] == reference[k] for k in ("depth", "candidate_limit", "rrf_k"))
            and config["channel_weights"] == reference["channel_weights"]["S2"]
            and config["sparse_root"] == reference["sparse_root"]
            and config["sparse_manifest_sha256"] == reference["sparse_manifest_sha256"],
            "Frozen P4 Sparse selection differs")
    root = Path(repo) / config["sparse_root"]
    path = root / "manifest.json"
    binding.bind(path, config["sparse_manifest_sha256"])
    manifest = read(path)
    require(manifest["scope"] == "full-selected" and manifest["status"] == "complete"
            and manifest["input_bundle_id"] == dense["input_bundle_id"]
            and manifest["counts"] == dense["counts"]
            and manifest["space_id"] == digest(manifest["configuration"]), "Foreign Sparse index")
    binding.bind(root / "validation.json")
    pointer = read(root / "validation.json")
    report_path = inside(root, pointer["folder"]) / "report.json"
    binding.bind(report_path, pointer["report_sha256"])
    report = read(report_path)
    require(pointer["manifest_sha256"] == sha(path) and report["status"] == "PASS"
            and report["manifest_sha256"] == sha(path) and report["counts"] == manifest["counts"]
            and report["plan_binding"] == manifest["binding"], "Sparse independent validation differs")
    return path, manifest
