"""Explicit local build, package and query entry point for versioned indexes."""

import argparse
from contextlib import ExitStack, redirect_stdout
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from ...collector.run_store import create_json
from ...evidence_io import digest, read, require, sha, strict_json
from ...indexing.benchmark_io import disk_guard, offline_only, ram_guard, resources
from ...indexing.snapshot_inputs import SnapshotInput
from ...indexing import snapshot_lexical, snapshot_vectors
from ...model_calls import locked
from .query_v2 import ResearchQuery, WEIGHTS, fuse_v2, validate_ancestry, validate_profile_v2
from .rerank_handoff import validate_selection
from .rerank_v2 import rank_v2, scoring_input
from .runtime_v2 import JsonWorker, code_identity
from .snapshot_source import SnapshotReader
from .source import FileBinding


VERSION = "gc-local-corpus-release-1"


def pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path)}


def _read_pin(ref):
    path = Path(ref["path"])
    require(path.is_absolute() and path == path.resolve() and sha(path) == ref["sha256"], "Release artifact binding differs")
    return read(path)


def package(inputs, vectors, lexical, output):
    inputs, vectors, lexical = map(pin, (inputs, vectors, lexical))
    bundle = SnapshotInput(inputs["path"])
    result = {"version": VERSION, "status": "complete", "input_bundle_id": bundle.manifest["input_bundle_id"],
              "input": inputs, "vectors": vectors, "bm25f": lexical}
    _check_indexes(result, bundle)
    require(all(not Path(output).resolve().is_relative_to(Path(ref["path"]).parent) for ref in (inputs, vectors, lexical)),
            "Release descriptor must be outside sealed artifacts")
    create_json(Path(output), result)
    return result


def _check_indexes(release, source):
    stores = {}
    for name, version in (("vectors", snapshot_vectors.VERSION), ("bm25f", snapshot_lexical.VERSION)):
        store = _read_pin(release[name])
        require(store["version"] == version and store["status"] == "complete"
                and store["input_bundle_id"] == release["input_bundle_id"] == source.manifest["input_bundle_id"]
                and store["input_sha256"] == release["input"]["sha256"]
                and store["counts"] == source.manifest["counts"], "Release index/input mismatch")
        stores[name] = store
    return stores


def open_release(path):
    binding = FileBinding()
    binding.bind(path)
    release = read(path)
    require(release["version"] == VERSION and release["status"] == "complete", "Unsupported release descriptor")
    for key in ("input", "vectors", "bm25f"):
        ref = release[key]
        binding.bind(ref["path"], ref["sha256"])
    source = SnapshotInput(release["input"]["path"])
    stores = _check_indexes(release, source)
    binding.check()
    return release, source, stores, binding


def _config(repo, combination):
    config = read(Path(repo) / "config/clean-retrieval-v1.json")
    config.update(combination=combination, channel_weights=WEIGHTS[combination])
    validate_profile_v2(config, combination)
    return config


def _emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def _worker(repo, release_path, channel, scratch):
    release, source, stores, binding = open_release(release_path)
    model = None
    if channel == "bm25f":
        effective, segment = snapshot_lexical.lexical_runtime(repo, scratch)
        index = snapshot_lexical.LexicalIndex(release["bm25f"]["path"], source, effective, segment)
        reps = snapshot_lexical.representations(index.manifest)
    else:
        from ...indexing.snapshot_encoder import SnapshotEncoder

        index = snapshot_vectors.VectorIndex(release["vectors"]["path"], source)
        model = SnapshotEncoder(repo, sparse=channel == "sparse")
        require(model.contract[channel] == index.contract[channel], "Versioned numerical query contract changed")
        reps = snapshot_vectors.representations(stores["vectors"], channel)
    return model, index, reps, binding


def worker(repo, release_path, channel, scratch):
    sys.stdout.reconfigure(encoding="utf-8")
    with offline_only(), redirect_stdout(sys.stderr):
        model, index, reps, binding = _worker(repo, release_path, channel, scratch)
    try:
        _emit({"status": "ready", "pid": os.getpid(), "representations": reps, "release_sha256": sha(release_path)})
        for line in sys.stdin:
            request = strict_json(line)
            if request.get("action") == "close":
                break
            binding.check()
            with offline_only(), redirect_stdout(sys.stderr):
                if model is not None:
                    vector = model.query(request["text"], channel)
                    hits = index.search(channel, vector, request["depth"])
                else:
                    hits = index.search(request["text"], request["depth"])
            _emit({"status": "complete", "routes": hits, "resources": resources()})
    finally:
        if model is not None:
            model.close()
        else:
            index.close()


def run_query(repo, release_path, query, output, *, rerank=False):
    repo, release_path, output = (Path(path).resolve() for path in (repo, release_path, output))
    validate_ancestry(query)
    release, source, stores, binding = open_release(release_path)
    config = _config(repo, query.combination)
    if query.combination == "S2":
        require("sparse" in stores["vectors"]["contract"], "S2 requires a versioned Sparse index")
    require(not output.is_relative_to(repo / "data/raw")
            and all(not output.is_relative_to(Path(release[key]["path"]).parent) for key in ("input", "vectors", "bm25f")),
            "Use a separate query output root")
    output.mkdir(parents=True, exist_ok=False)
    reader = SnapshotReader(source)
    hits, documents, usage = [], {}, {}
    expected_release = sha(release_path)
    code = {**code_identity(), "snapshot.py": sha(__file__), "snapshot_source.py": sha(Path(__file__).with_name("snapshot_source.py"))}
    # A sealed launcher may execute this module from its frozen source tree.
    # Child interpreters stay in the configured installation, but import that same source tree.
    execution = Path(__file__).resolve().parents[4]
    with locked(repo / ".local/clean-generation-gpu.lock"):
        for channel in config["channel_weights"]:
            python = config["lexical_python" if channel == "bm25f" else "dense_python"]
            module = "genshin_corpus.retrieval.clean_based.snapshot"
            args = ["--repo", str(repo), "worker", "--release", str(release_path), "--channel", channel,
                    "--scratch", str(output / (channel + "-scratch"))]
            expected = (snapshot_lexical.representations(stores["bm25f"]) if channel == "bm25f"
                        else snapshot_vectors.representations(stores["vectors"], channel))
            with JsonWorker(execution, repo / python, module, args, output / (channel + ".log"),
                            timeout=config["worker_timeout_seconds"]) as process:
                require(process.ready["release_sha256"] == expected_release and process.ready["representations"] == expected,
                        "Versioned worker binding differs")
                stage = process.request({"text": query.search_text, "depth": config["depth"]})
            usage[channel] = stage["resources"]
            for route, rows in stage["routes"].items():
                for rank, entry in enumerate(rows, 1):
                    number = entry["input_row"]
                    cid = source.rows[number]["chunk_id"]
                    if cid not in documents:
                        documents[cid] = reader.document(number, cid)
                    hits.append({"question_id": query.turn_id, "query_id": query.query_id,
                                 "round": query.round, "kind": query.kind, "channel": channel, "granularity": route,
                                 "rank": rank, "raw_score": entry["score"], "representation_id": expected[route],
                                 "input_row": number, "chunk_id": cid})
        candidates = fuse_v2(query, hits, documents, config, input_bundle_id=reader.bundle_id, code=code)
        create_json(output / "candidates.json", candidates)
        if rerank:
            original = read(repo / "config/reranking-selected-v1.json")
            selection = validate_selection({**original, "input_bundle_id": reader.bundle_id})
            result = None
            if candidates["candidates"]:
                projected = output / "scoring-input.json"
                create_json(projected, scoring_input(candidates, selection))
                args = ["--repo", str(repo), "--scratch", str(output / "reranking-scratch")]
                with JsonWorker(execution, repo / selection["python"], "genshin_corpus.retrieval.clean_based.rerank_worker_v2", args,
                                output / "reranking.log", timeout=config["worker_timeout_seconds"]) as process:
                    require(process.ready["binding"] == selection["score_binding"], "Foreign local reranker")
                    result = process.request({"input": str(projected), "budget": 2048})
                    create_json(output / "scores.json", result)
            ranked = rank_v2(candidates, result, selection)
            create_json(output / "ranked.json", ranked)
            from ...generation.clean_evidence import EvidenceReader

            evidence = EvidenceReader(reader, verified_parent_ranges=True)
            handles = evidence.register(ranked)
            if handles:
                receipt = evidence.read(next(iter(handles.values())))
                create_json(output / "first-evidence.json", receipt)
    reader.binding.check()
    binding.check()
    create_json(output / "run.json", {"status": "complete", "release": pin(release_path), "bundle_id": reader.bundle_id,
                                     "query_id": digest(query.value()), "usage": usage, "remote_calls": 0})
    return candidates


def _build(args):
    repo = Path(args.repo).resolve()
    config = _config(repo, "S0")
    output = Path(args.output).resolve()
    require(not output.is_relative_to(repo / "data/raw"), "Index writes must be outside Raw")
    source = SnapshotInput(args.input)
    previous = None
    if args.previous:
        manifest = read(args.previous)
        previous_source = SnapshotInput(manifest["input_manifest"])
        require(sha(previous_source.path) == manifest["input_sha256"], "Previous index input changed")
        previous = (args.previous, previous_source)
    def guard():
        disk_guard(output if output.exists() else output.parent, config)
    with offline_only():
        if args.command == "build-vectors":
            from ...indexing.snapshot_encoder import SnapshotEncoder

            with locked(repo / ".local/clean-generation-gpu.lock"):
                ram_guard(config)
                encoder = SnapshotEncoder(repo, sparse=args.sparse)
                try:
                    result = snapshot_vectors.build_vectors(source, output, encoder, previous=previous, disk_guard=guard)
                    print(json.dumps({"encoded_keys": result["encoded_keys"], "reused_keys": result["reused_keys"], "resources": resources()}))
                finally:
                    encoder.close()
        else:
            with locked(output.parent / ("." + output.name + ".lock")), TemporaryDirectory(prefix="gc-jieba-") as scratch:
                effective, segment = snapshot_lexical.lexical_runtime(repo, scratch)
                result = snapshot_lexical.build_lexical(source, output, effective, segment, previous=previous, disk_guard=guard)
                print(json.dumps({k: result[k] for k in ("reused_buckets", "built_buckets")}))


def compare_releases(repo, left_path, right_path, channel, output, queries=None):
    from ...indexing.snapshot_compare import compare_lexical, compare_vectors

    left, right = (open_release(path) for path in (left_path, right_path))
    require(left[0]["input_bundle_id"] == right[0]["input_bundle_id"] and left[1].rows == right[1].rows,
            "Independent input row maps differ")
    paths = {ref["path"] for arm in (left, right) for key, ref in arm[0].items()
             if key in {"input", "vectors", "bm25f"}}
    require(all(not Path(output).resolve().is_relative_to(Path(path).parent) for path in paths)
            and not Path(output).resolve().is_relative_to(Path(repo) / "data/raw"), "Use a separate comparison output")
    with offline_only(), ExitStack() as cleanup:
        if channel == "bm25f":
            scratch = cleanup.enter_context(TemporaryDirectory(prefix="gc-jieba-"))
            effective, segment = snapshot_lexical.lexical_runtime(repo, scratch)
            indexes = [snapshot_lexical.LexicalIndex(arm[0]["bm25f"]["path"], arm[1], effective, segment)
                       for arm in (left, right)]
            for index in indexes:
                cleanup.callback(index.close)
            require(queries is not None, "BM25F comparison requires --queries")
            result = compare_lexical(*indexes, read(queries))
        else:
            indexes = [snapshot_vectors.VectorIndex(arm[0]["vectors"]["path"], arm[1]) for arm in (left, right)]
            result = compare_vectors(*indexes)
    left[3].check()
    right[3].check()
    create_json(Path(output), {"status": "PASS", "channel": channel, "left": pin(left_path), "right": pin(right_path),
                               "input_bundle_id": left[0]["input_bundle_id"], "result": result,
                               "queries_sha256": None if queries is None else sha(queries)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build-vectors", "build-bm25f"):
        build = commands.add_parser(name)
        build.add_argument("--input", required=True, type=Path)
        build.add_argument("--output", required=True, type=Path)
        build.add_argument("--previous", type=Path)
        if name == "build-vectors":
            build.add_argument("--sparse", action="store_true")
    publish = commands.add_parser("package")
    for name in ("input", "vectors", "bm25f", "output"):
        publish.add_argument("--" + name, required=True, type=Path)
    query = commands.add_parser("query")
    query.add_argument("--release", required=True, type=Path)
    query.add_argument("--output", required=True, type=Path)
    query.add_argument("--query-json", required=True, type=Path)
    query.add_argument("--rerank", action="store_true")
    child = commands.add_parser("worker")
    child.add_argument("--release", required=True, type=Path)
    child.add_argument("--channel", required=True, choices=("dense", "sparse", "bm25f"))
    child.add_argument("--scratch", required=True, type=Path)
    compare = commands.add_parser("compare")
    for name in ("left", "right", "output"):
        compare.add_argument("--" + name, type=Path, required=True)
    compare.add_argument("--channel", required=True, choices=("vectors", "bm25f"))
    compare.add_argument("--queries", type=Path)
    args = parser.parse_args(argv)
    if args.command.startswith("build-"):
        _build(args)
    elif args.command == "package":
        package(args.input, args.vectors, args.bm25f, args.output)
    elif args.command == "query":
        run_query(args.repo, args.release, ResearchQuery.parse(read(args.query_json)), args.output, rerank=args.rerank)
    elif args.command == "compare":
        compare_releases(args.repo, args.left, args.right, args.channel, args.output, args.queries)
    else:
        worker(args.repo, args.release, args.channel, args.scratch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
