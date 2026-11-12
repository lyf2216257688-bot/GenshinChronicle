"""Finite real-source Sparse trial. No 70Q input and no full-build option."""
import argparse
import os
from pathlib import Path
import time

from ...evidence_io import inventory, read, require, sha, write
from ...indexing.benchmark_io import child_process, disk_guard, lines, ram_guard, resources
from .indexes import model_spec, selected
from .source import InputReader


def select_rows(reader, dense_root, dense_manifest, limit):
    require(type(limit) is int and 0 < limit <= 128, "Sparse trial limit must be 1–128")
    audit = dense_manifest["length_audit"]
    folder = Path(dense_root) / audit["folder"]
    require(sha(folder / "complete.json") == audit["receipt_sha256"], "Length audit receipt changed")
    receipt = read(folder / "complete.json")
    require(receipt["status"] == "PASS" and receipt["exceeded"] == 0, "Invalid selected length audit")
    for name, checksum in receipt["files"].items():
        require(sha(folder / name) == checksum, "Length audit artifact changed")
    import heapq
    longest = heapq.nlargest(8, lines(folder / "lengths.jsonl"), key=lambda r: (r["tokens"], -r["input_row"]))
    indices = []
    # Round-robin explicit observed structures; never claim random corpus sampling.
    pools = [list(r["input_row"] for r in longest), *reader.trial_pools.values()]
    for position in range(8):
        for pool in pools:
            if position < len(pool) and pool[position] not in indices:
                indices.append(pool[position])
    indices += [i for i in range(len(reader.offsets)) if i not in indices][:limit]
    indices = indices[:limit]
    documents = [reader.document(i) for i in indices]
    lengths = {r["input_row"]: r for r in longest}
    for doc in documents:
        if doc["input_row"] in lengths:
            entry = lengths[doc["input_row"]]
            require(all(doc["row"][k] == entry[k] for k in ("chunk_id", "dense_text_sha256")), "Trial length/source mismatch")
    return documents, {"strategy": "round-robin longest/shared/micro-only/macro-only/dialogue/condition, then input order",
                       "requested": limit, "selected": len(documents), "input_rows": indices,
                       "maximum_source_tokens": receipt["maximum"]}


def execute_trial(repo, config_path, output):
    import numpy as np
    from ...indexing.benchmark_dense import Encoder
    from ...indexing.sparse_trial import SparseEncoder, SparseSession, build_trial, official_kernel
    repo, output = Path(repo), Path(output)
    config = read(config_path)
    require(config.get("version") == "gc-clean-retrieval-1", "Unsupported trial configuration")
    require(type(config["sparse_trial_rows"]) is int and 0 < config["sparse_trial_rows"] <= 128,
            "Sparse full build is outside P0–P2")
    require(type(config["sparse_trial_batch"]) is int and 1 <= config["sparse_trial_batch"] <= 2,
            "Trial batch must be1 or2")
    started, before = time.perf_counter(), resources()
    ram_guard(config)
    disk_guard(output, config)
    selection, manifests = selected(repo, config)
    dense_path, dense_manifest = manifests["dense"]
    reference, reference_metadata = official_kernel(repo / config["sparse_reference"]["wheel"],
                                                   config["sparse_reference"]["sha256"])
    encoder = None
    with InputReader(dense_manifest["input_manifest"]) as reader:
        docs, sampling = select_rows(reader, dense_path.parent, dense_manifest, config["sparse_trial_rows"])
        rows = [{"input_row": d["input_row"], **{k: d["row"][k] for k in
                 ("chunk_id", "granularities", "dense_text_sha256")}} for d in docs]
        write(output / "selection.json", {**sampling, "input_bundle_id": reader.bundle_id, "rows": rows})
        try:
            base = Encoder(model_spec(selection, dense_manifest), repo)
            require(base.space_id == dense_manifest["space_id"], "Trial backbone differs from selected model")
            encoder = SparseEncoder(base, head_sha256=config["sparse_head_sha256"], reference=reference)
            vectors, batch, encoding_started = [], config["sparse_trial_batch"], time.perf_counter()
            progress = []
            position = 0
            while position < len(docs):
                ram_guard(config, model_loaded=base.model is not None)
                disk_guard(output, config)
                current = docs[position:position + batch]
                try:
                    encoded = encoder.encode([d["row"]["dense_text"] for d in current], verify_reference=True)
                except base.torch.cuda.OutOfMemoryError:
                    if batch == 1:
                        raise
                    batch = 1
                    base.torch.cuda.empty_cache()
                    continue
                vectors.extend(encoded)
                position += len(current)
                progress.append({"rows": position, "resources": resources()})
                print(f"Sparse trial {position}/{len(docs)}", flush=True)
            encoding_seconds = time.perf_counter() - encoding_started
            vocabulary_size = int(base.model.config.vocab_size)
            # Reference provenance is validation metadata, not a different vector
            # space. Query encoding must supply its OWN space identity below.
            contract = encoder.contract
            sparse_root = output / "sparse"
            interrupted = build_trial(rows, vectors, sparse_root, input_bundle_id=reader.bundle_id,
                contract=contract, vocabulary_size=vocabulary_size, shard_rows=config["sparse_trial_shard_rows"], stop_after=1)
            old_receipts = {p.relative_to(sparse_root).as_posix(): sha(p)
                            for p in sparse_root.glob("shards/*/*") if p.is_file()}
            manifest = build_trial(rows, vectors, sparse_root, input_bundle_id=reader.bundle_id,
                contract=contract, vocabulary_size=vocabulary_size, shard_rows=config["sparse_trial_shard_rows"])
            require(all(sha(sparse_root / name) == value for name, value in old_receipts.items()),
                    "Sparse Resume rewrote a committed artifact")
            session = SparseSession(sparse_root)
            require(manifest["space_id"] == encoder.space_id, "Sparse query encoder/index space mismatch")
            questions = ["塔利雅的新颂歌灵感来自哪里？", "至冬先驱报里的交互文本", "旅行者 旅行者 Paimon 2026"]
            query_vectors = encoder.encode(questions, role="query", verify_reference=True)
            query_results = []
            for question, weights in zip(questions, query_vectors):
                begin = time.perf_counter()
                found = session.search(weights, space_id=encoder.space_id, top_k=10)
                seconds = time.perf_counter() - begin
                for g in ("micro", "macro"):
                    expected = [{**row, "score": sum(float(weight) * vector.get(token, 0.) for token, weight in weights.items())}
                                for row, vector in zip(rows, vectors) if g in row["granularities"]]
                    expected = sorted((r for r in expected if r["score"] > 0), key=lambda r: (-r["score"], r["input_row"]))[:10]
                    require([r["chunk_id"] for r in found[g]] == [r["chunk_id"] for r in expected], "Sparse ranking differs from brute dot")
                    require(np.allclose([r["score"] for r in found[g]], [r["score"] for r in expected], rtol=1e-5, atol=1e-6),
                            "Sparse scores differ from brute dot")
                query_results.append({"question": question, "elapsed_seconds": seconds, "routes": found,
                                      "reference": "brute_dot_PASS"})
            # Batch-vs-single padding check on distinct-length inputs, within
            # declared FP16 tolerance; official-kernel checks above remain exact.
            probe = ["旅行者旅行者", docs[0]["row"]["dense_text"]]
            together = encoder.encode(probe, verify_reference=True)
            separate = [encoder.encode([text], verify_reference=True)[0] for text in probe]
            error = max(abs(a.get(t, 0.) - b.get(t, 0.)) for a, b in zip(together, separate) for t in set(a) | set(b))
            require(error <= .005, "FP16 batch/padding discrepancy exceeds declared trial tolerance")
            reader.binding.check()
            report = {"status": "PASS", "scope": "bounded-trial; engineering only; not70Q or relevance acceptance",
                      "input_bundle_id": reader.bundle_id, "selection": sampling, "counts": manifest["counts"],
                      "empty_rows": manifest["empty_rows"], "encoding_seconds": encoding_seconds,
                      "nnz": sum(len(v) for v in vectors), "sparse_bytes": sum(p.stat().st_size for p in sparse_root.rglob('*') if p.is_file()),
                      "reference": reference_metadata, "kernel_checks": encoder.reference_checks,
                      "padding_check": {"status": "PASS", "max_abs_weight_error": error, "tolerance": .005},
                      "resume": {"status": "PASS", "interruption": interrupted, "preserved_files": len(old_receipts)},
                      "queries": query_results, "resources_before": before, "progress": progress,
                      "resources_after": resources(), "elapsed_seconds": time.perf_counter() - started,
                      "full_build_authorized": False, "serving_changed": False}
            write(output / "validation.json", report)
            # Actual source texts are provided for inspection without a semantic grade.
            samples = ["# Sparse 有限工程验证原文", "", "仅为32条左右的结构样本，不是相关性选型。", ""]
            by_id = {d["row"]["chunk_id"]: d for d in docs}
            for item in query_results:
                samples += ["## " + item["question"], ""]
                for row in item["routes"]["micro"][:3]:
                    doc = by_id[row["chunk_id"]]
                    samples += [f"Chunk {row['chunk_id']}，分数 {row['score']:.6f}", "", "~~~~text",
                                doc["chunk"]["retrieval_text"], "~~~~", ""]
            (output / "source-cases.md").write_text("\n".join(samples), encoding="utf-8")
        finally:
            if encoder is not None:
                encoder.close()


def run_trial(repo, config_path, output):
    from .runtime import validate_output
    config, output, repo = read(config_path), Path(output).resolve(), Path(repo).resolve()
    validate_output(repo, config, output)
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(repo / "src"), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               OPENBLAS_NUM_THREADS="4", OMP_NUM_THREADS="4", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    command = [str(repo / config["dense_python"]), "-B", "-u", "-m", __name__,
               "--repo", str(repo), "--config", str(config_path), "--output", str(output)]
    with (output / "stdout.log").open("x", encoding="utf-8") as stdout, \
         (output / "stderr.log").open("x", encoding="utf-8") as stderr:
        with child_process(command, cwd=repo, env=env, stdout=stdout, stderr=stderr) as process:
            code = process.wait()
    require(code == 0 and (output / "validation.json").exists(), "Sparse trial failed; inspect output logs/failure.json")
    write(output / "complete.json", {"status": "complete", "scope": "bounded-trial", "files": inventory(output),
          "config_sha256": sha(config_path), "code_sha256": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")},
          "sparse_code_sha256": sha(repo / "src/genshin_corpus/indexing/sparse_trial.py")})
    print("Sparse trial complete -> " + str(output), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("repo", "config", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        from ...indexing.benchmark_io import offline_only
        with offline_only():
            execute_trial(args.repo, args.config, args.output)
    except BaseException as exc:
        write(Path(args.output) / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
