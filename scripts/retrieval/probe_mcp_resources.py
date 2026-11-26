"""Measure cold and warm local worker calls on the same audited flat inputs."""

import argparse
from pathlib import Path
import time

from genshin_corpus.evidence_io import digest, inside, read, require, sha, write
from genshin_corpus.indexing.benchmark_io import ram_guard, resources
from genshin_corpus.model_calls import locked
from genshin_corpus.retrieval.clean_based.runtime_v2 import JsonWorker


def compare_worker(repo, python, module, args, output, request, projection):
    started = time.perf_counter()
    samples = []
    with JsonWorker(repo, python, module, args, output, timeout=180) as worker:
        ready = time.perf_counter()
        for _ in range(2):
            tick = time.perf_counter()
            result = worker.request(request)
            samples.append({"seconds": time.perf_counter() - tick, "resources": result["resources"],
                            "model_load_seconds": result.get("load_seconds"),
                            "result_digest": digest(projection(result))})
        done = time.perf_counter()
    require(samples[0]["result_digest"] == samples[1]["result_digest"], "Warm result differs")
    return {"prepare_seconds": ready - started, "requests": samples, "exit_seconds": time.perf_counter() - done,
            "pid": worker.ready["pid"], "exit_confirmed": True}


def verify_release_lexical(repo, config, output):
    root = repo / "data/retrieval/vcr-1126"
    point = read(repo / "config/mcp-live-v1.json")["releases"]["20261124"]
    release = inside(repo, point["artifact"]["path"])
    require(sha(release) == point["artifact"]["sha256"], "Foreign release")
    stage = "quick-01-S0-left"
    quick = read(root / "quick-complete.json")
    receipt = root / "receipts" / (stage + ".json")
    require(quick["status"] == "PASS" and quick["receipts"][stage] == sha(receipt), "Quick receipt changed")
    outputs = read(receipt)["outputs"]
    candidates, run = [inside(root, outputs[key]["path"]) for key in ("candidates", "main")]
    require(sha(candidates) == outputs["candidates"]["sha256"] and sha(run) == outputs["main"]["sha256"],
            "Quick query evidence changed")
    run = read(run)
    require(run["release"]["sha256"] == point["artifact"]["sha256"], "Quick query targets another release")
    baseline = read(candidates)
    expected = {route: [{"input_row": h["input_row"], "score": h["raw_score"]} for h in baseline["raw_hits"]
                       if h["channel"] == "bm25f" and h["granularity"] == route] for route in ("micro", "macro")}
    started = time.perf_counter()
    with JsonWorker(repo, config["lexical_python"], "genshin_corpus.mcp_live.lexical_worker",
                    ["--repo", str(repo), "--release", str(release), "--scratch", str(output / "lexical")],
                    output / "lexical.log", timeout=180) as worker:
        ready = time.perf_counter()
        require(worker.ready["release_sha256"] == point["artifact"]["sha256"], "Worker loaded another release")
        result = worker.request({"text": baseline["query"]["search_text"], "depth": config["depth"]})
        done = time.perf_counter()
    require(result["routes"] == expected, "Sequential route scores or positions differ")
    return {"scores_and_ranks_exact": True, "counts": {route: len(hits) for route, hits in expected.items()},
            "reference_resources": run["usage"]["bm25f"], "sequential_resources": result["resources"],
            "prepare_seconds": ready - started, "search_seconds": done - ready, "exit_confirmed": True,
            "snapshot_receipt": sha(receipt)}


def score_projection(result):
    token_counts = [[entry["chunk_id"], entry["tokens"]] for entry in result["audit"]]
    return [result["scores"], token_counts]


def verify_flat_workers(repo, config, profile, selection, output, audit, run):
    ram_guard(config)
    dense = compare_worker(repo, config["dense_python"], "genshin_corpus.retrieval.clean_based.worker_v2",
                           ["--repo", str(repo), "--config", str(profile), "--channel", "dense",
                            "--scratch", str(output / "dense")], output / "dense.log",
                           {"query": run["query"], "depth": config["depth"], "verify_reference": False},
                           lambda result: result["hits"])
    expected_hits = [hit for hit in read(audit / "candidates.json")["raw_hits"] if hit["channel"] == "dense"]
    require(dense["requests"][0]["result_digest"] == digest(expected_hits), "Dense replay differs from live query")
    write(output / "dense-parity.json", dense)
    ram_guard(config)
    bge = compare_worker(repo, selection["python"], "genshin_corpus.retrieval.clean_based.rerank_worker_v2",
                         ["--repo", str(repo), "--scratch", str(output / "reranking")], output / "bge.log",
                         {"action": "score", "input": str(audit / "scoring-input.json"), "budget": 2048},
                         score_projection)
    baseline = read(audit / "scores.json")
    require(bge["requests"][0]["result_digest"] == digest(score_projection(baseline)),
            "Rerank replay differs from live query")
    write(output / "bge-parity.json", bge)
    return dense, bge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path.cwd()
    root = repo / "data/generation/mcp-live-v1"
    audit, output = args.query_audit.resolve(), args.output.resolve()
    require(audit.is_relative_to(root) and output.parent == root, "Use separate MCP audit roots")
    run = read(audit / "run.json")
    require(run["status"] == "complete" and run["release"] == "20260824", "Expected a complete flat query audit")
    profile = repo / "config/clean-retrieval-v1.json"
    config = read(profile)
    selection = read(repo / "config/reranking-selected-v1.json")
    output.mkdir(exist_ok=False)
    stage = "snapshot-lexical"
    try:
        with locked(repo / ".local/clean-generation-gpu.lock"):
            before = ram_guard(config)
            lexical = verify_release_lexical(repo, config, output)
            write(output / "lexical-parity.json", lexical)
            stage = "flat-workers"
            dense, bge = verify_flat_workers(repo, config, profile, selection, output, audit, run)
        write(output / "result.json", {"status": "PASS", "dense": dense, "bge": bge, "lexical": lexical, "before": before,
                                       "after": resources(), "query_audit": str(audit), "remote_calls": 0})
    except Exception as exc:
        write(output / "failure.json", {"status": "failed", "stage": stage, "resources": resources(),
                                       "error_type": type(exc).__name__, "detail": str(exc)[:512]})
        raise
    print(output / "result.json")


if __name__ == "__main__":
    main()
