"""Plan or resume a local incremental build from the adopted corpus release."""

import argparse
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from genshin_corpus.clean.snapshot_diff import Snapshot
from genshin_corpus.collector.run_store import create_json
from genshin_corpus.evidence_io import read, require, sha
from genshin_corpus.indexing.snapshot_inputs import SnapshotInput
from genshin_corpus.mcp_live.runtime import production_release
from genshin_corpus.model_calls import locked
from genshin_corpus.retrieval.clean_based.snapshot import open_release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--current-raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Build or resume the saved plan")
    args = parser.parse_args(argv)
    repo, raw, output = (path.resolve() for path in (args.repo, args.current_raw, args.output))
    require(raw.is_relative_to(repo / "data/raw") and output.parent == repo / "data/retrieval",
            "Use a completed local Raw run and a dedicated retrieval output")
    selected = production_release(repo)
    selected.settings
    descriptor = selected.descriptor
    previous = read(descriptor["input"]["path"])
    previous_clean = read(previous["clean"]["path"])
    old_raw = Path(previous_clean["current_raw"]["root"])
    current = Snapshot(raw)
    require(raw != old_raw and current.binding["run_id"] != selected.spec["raw_run"], "Current Raw is already adopted")
    require(not any((repo / ref["path"]).is_relative_to(output) for ref in (
        selected.spec["artifact"], descriptor["input"], descriptor["vectors"], descriptor["bm25f"],
        previous["clean"], previous["chunk"],
    )), "Output overlaps the adopted release")
    plan = {
        "schema": "gc-incremental-update-1", "release": selected.summary(), "previous": descriptor,
        "current_raw": current.binding, "current_path": str(raw), "previous_path": str(old_raw),
        "inputs": {"clean": previous["clean"], "chunk": previous["chunk"]},
        "output": str(output),
    }
    if not args.run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    plan_path = output / "update.json"
    require(not output.exists() or plan_path.is_file(), "Use a new output or resume a registered update")
    output.mkdir(exist_ok=True)
    with locked(output / "update.lock"):
        if not plan_path.exists():
            create_json(plan_path, plan)
        require(read(plan_path) == plan, "Update plan changed")
        env = {**os.environ, "PYTHONPATH": str(repo / "src")}
        dense = repo / selected.settings["dense_python"]
        lexical = repo / selected.settings["lexical_python"]
        index = "genshin_corpus.retrieval.clean_based.snapshot"
        clean = output / "clean/manifest.json"
        chunk = output / "chunk/manifest.json"
        inputs = output / "inputs/manifest.json"
        vectors = output / "vectors/manifest.json"
        bm25f = output / "bm25f/manifest.json"
        release = output / "release.json"
        rules = repo / "config/chunking-full-20261107-balanced.json"
        qwen = repo / ".local/models/qwen3-embedding-0.6b/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/tokenizer.json"
        from genshin_corpus.retrieval.bge_reranker_v2_m3 import MODEL_REVISION
        bge = repo / ".local/models/bge-reranker-v2-m3" / MODEL_REVISION / "tokenizer.json"

        def call(python, module, *params):
            selected.files.check()
            require(sha(raw / "metadata/manifest.json") == current.binding["manifest_sha256"],
                    "Current Raw changed")
            subprocess.run([str(python), "-B", "-m", module, *map(str, params)], cwd=repo, env=env, check=True)

        call(dense, "genshin_corpus.clean.snapshot_build", "build", "--mode", "incremental",
             "--current-raw", raw, "--previous-raw", old_raw, "--previous-clean", previous["clean"]["path"],
             "--output", clean.parent)
        call(dense, "genshin_corpus.chunking.snapshot_build", "build", "--mode", "incremental",
             "--clean", clean, "--selection", rules, "--qwen-tokenizer", qwen, "--bge-tokenizer", bge,
             "--previous", previous["chunk"]["path"], "--output", chunk.parent)
        if not inputs.exists():
            staged = output / "input-attempts" / uuid4().hex
            staged.parent.mkdir(exist_ok=True)
            call(dense, "genshin_corpus.indexing.snapshot_inputs", chunk, staged)
            SnapshotInput(staged / "manifest.json")
            staged.rename(inputs.parent)
        require(SnapshotInput(inputs).manifest["chunk"]["sha256"] == sha(chunk), "Unified input changed")
        call(dense, index, "build-vectors", "--input", inputs, "--output", vectors.parent, "--sparse",
             "--previous", descriptor["vectors"]["path"])
        call(lexical, index, "build-bm25f", "--input", inputs, "--output", bm25f.parent,
             "--previous", descriptor["bm25f"]["path"])
        if not release.exists():
            call(dense, index, "package", "--input", inputs, "--vectors", vectors, "--bm25f", bm25f,
                 "--output", release)
        completed, _, _, _ = open_release(release)
        require(all(Path(completed[key]["path"]) == path for key, path in (
            ("input", inputs), ("vectors", vectors), ("bm25f", bm25f),
        )), "Unexpected update release outputs")
        print(release)


if __name__ == "__main__":
    main()
