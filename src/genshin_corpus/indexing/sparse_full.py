"""Streaming SciPy Sparse shards. No resident full-corpus matrix or serving switch."""
from collections import Counter
import gc
import heapq
from itertools import islice
from pathlib import Path
import time

from ..build_runtime import atomic_json
from ..evidence_io import digest, inside, read, require, sha
from .benchmark_io import attempt, disk_guard, dump_lines, event, lines, ram_guard, receipt, verify_receipt
from .sparse_trial import csr_weights

VERSION = "gc-bge-sparse-full-1"
LEVELS = ("micro", "macro")


def representations(plan):
    return {g: digest({"version": VERSION, "input_bundle_id": plan["input_bundle_id"],
                      "space_id": plan["space_id"], "scope": plan["scope"], "granularity": g}) for g in LEVELS}


def ensure_plan(root, plan):
    require(plan["version"] == VERSION and plan["scope"] in {"full-selected", "finite-probe"}, "Invalid Sparse plan")
    require(type(plan["shard_rows"]) is int and 1 <= plan["shard_rows"] <= 4096, "Invalid shard size")
    require(plan["space_id"] == digest(plan["configuration"]), "Sparse plan space differs")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "plan.json").exists():
        require(read(root / "plan.json") == plan, "Sparse resume plan binding changed")
    else:
        require(not (root / "shards").exists() and not (root / "manifest.json").exists(), "Unbound Sparse artifacts")
        atomic_json(root / "plan.json", plan)
    return digest(plan)


def matrix_read(folder, rec, vocabulary_size):
    import numpy as np
    from scipy.sparse import load_npz
    require(set(rec["files"]) == {"weights.npz", "rows.jsonl"}, "Unexpected Sparse shard file set")
    rows = list(lines(folder / "rows.jsonl"))
    matrix = load_npz(folder / "weights.npz")
    require(matrix.format == "csr", "Expected CSR Sparse matrix")
    matrix.check_format(full_check=True)
    require(len(rows) == rec["rows"] and matrix.shape == (len(rows), vocabulary_size)
            and matrix.dtype == np.dtype("float32") and matrix.has_canonical_format
            and bool(np.isfinite(matrix.data).all()) and bool((matrix.data > 0).all()), "Invalid Sparse matrix")
    counts = Counter({"documents": len(rows), "micro": 0, "macro": 0})
    for row in rows:
        require(type(row["input_row"]) is int and row["input_row"] >= 0 and bool(row["chunk_id"])
                and bool(row["granularities"]) and set(row["granularities"]) <= set(LEVELS)
                and len(set(row["granularities"])) == len(row["granularities"]), "Invalid Sparse row mapping")
        counts.update(row["granularities"])
    require(rec["nnz"] == matrix.nnz and rec["empty_rows"] == int((np.diff(matrix.indptr) == 0).sum())
            and rec["counts"] == dict(counts), "Sparse shard accounting differs")
    return matrix, rows


def committed(root, binding):
    result = {}
    for path in sorted((Path(root) / "shards").glob("*/complete.json")):
        rec = verify_receipt(path.parent, binding)
        require(type(rec["number"]) is int and rec["number"] >= 0 and rec["number"] not in result,
                "Duplicate/invalid committed Sparse shard")
        result[rec["number"]] = (path.parent, rec)
    return result


def encode_shard(encoder, texts, config, root, identities):
    """Length-sort within a bounded shard; scatter back. Only CUDA OOM is retried."""
    lengths = encoder.encoder.lengths(texts)
    require(max(lengths) <= encoder.encoder.model_config["max_tokens"], "Overlength input; truncation forbidden")
    order = sorted(range(len(texts)), key=lambda i: (lengths[i], i))
    output, position, cap = [None] * len(texts), 0, config["max_batch"]
    heartbeat = time.monotonic()
    while position < len(order):
        ram_guard(config, model_loaded=encoder.encoder.model is not None)
        disk_guard(root, config)
        take = 1
        while (take < cap and position + take < len(order)
               and lengths[order[position + take]] * (take + 1) <= config["padded_token_budget"]):
            take += 1
        selected = order[position:position + take]
        try:
            values = encoder.encode([texts[i] for i in selected])
        except encoder.torch.cuda.OutOfMemoryError:
            event(root, "cuda_oom", inputs=[identities[i] for i in selected],
                  tokens=[lengths[i] for i in selected], batch_size=take)
            gc.collect()
            encoder.torch.cuda.empty_cache()
            if take == 1:
                raise
            cap = max(1, take // 2)
            continue
        require(len(values) == take, "Encoder omitted rows")
        for i, value in zip(selected, values):
            output[i] = value
        position += take
        if time.monotonic() - heartbeat >= config.get("heartbeat_seconds", 15):
            event(root, "encoding_batch", processed_in_shard=position, shard_rows=len(texts), batch_size=take)
            heartbeat = time.monotonic()
    return output


def build_stream(root, plan, source, encode, *, config, stop_after=None, before_commit=None, before_publish=None):
    """Source yields (mapping, unchanged text); completed shards never call encode.

    A failed attempt without complete.json is ignored, preserved, and replaced by
    a new create-only attempt. A corrupt *committed* shard fails closed.
    """
    from scipy.sparse import save_npz
    root = Path(root).resolve()
    binding = ensure_plan(root, plan)
    parts = committed(root, binding)
    iterator, number, start = iter(source), 0, 0
    summaries, counts, empty, nnz = [], Counter({"documents": 0, "micro": 0, "macro": 0}), 0, 0
    started, new_rows = time.monotonic(), 0
    for group in iter(lambda: list(islice(iterator, plan["shard_rows"])), []):
        shard_started = time.monotonic()
        rows, texts = [r for r, _ in group], [t for _, t in group]
        require(len({r["input_row"] for r in rows}) == len(rows), "Duplicate Sparse mapping")
        if plan["scope"] == "full-selected":
            require([r["input_row"] for r in rows] == list(range(start, start + len(rows))), "Noncontiguous full source")
        if number in parts:
            folder, rec = parts.pop(number)
            matrix, stored = matrix_read(folder, rec, plan["vocabulary_size"])
            require(rec["start"] == start and stored == rows, "Committed Sparse source mapping differs")
            del matrix
            event(root, "shard_reused", number=number, rows=len(rows), completed_rows=start + len(rows))
        else:
            require(not (root / "manifest.json").exists(), "Completed index has missing committed shards")
            folder = attempt(root / "shards", f"part-{number:05}")
            event(root, "shard_started", number=number, start=start, rows=len(rows), folder=str(folder))
            ram_guard(config, model_loaded=True)
            disk_guard(root, config)
            values = encode(texts, rows)
            require(len(values) == len(rows), "Sparse encoded row count differs")
            matrix = csr_weights(values, plan["vocabulary_size"])
            require(matrix.shape[0] == len(rows), "Sparse matrix row count differs")
            # Worst-case next artifact space is checked again before writes.
            disk_guard(root, config, additional_bytes=matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
            save_npz(folder / "weights.npz", matrix, compressed=False)
            # NPZ close flushes; explicitly fsync before committing the receipt.
            import os
            with (folder / "weights.npz").open("r+b") as handle:
                os.fsync(handle.fileno())
            dump_lines(folder / "rows.jsonl", rows)
            local_counts = Counter({"documents": len(rows), "micro": 0, "macro": 0})
            for row in rows:
                local_counts.update(row["granularities"])
            metadata = {"number": number, "start": start, "rows": len(rows), "counts": dict(local_counts),
                        "nnz": int(matrix.nnz), "empty_rows": sum(not v for v in values),
                        "execution": {k: config[k] for k in ("max_batch", "padded_token_budget")}}
            if before_commit is not None:
                before_commit(folder)  # Fault injection; no production caller.
            rec = receipt(folder, binding, **metadata)
            del matrix, values
            new_rows += len(rows)
            event(root, "shard_committed", **metadata, completed_rows=start + len(rows),
                  elapsed_seconds=time.monotonic() - shard_started)
        summaries.append({"folder": folder.relative_to(root).as_posix(), "receipt_sha256": sha(folder / "complete.json")})
        counts.update(rec["counts"])
        empty += rec["empty_rows"]
        nnz += rec["nnz"]
        number, start = number + 1, start + len(rows)
        atomic_json(root / "progress.json", {"status": "building", "completed_rows": start,
                    "total_rows": plan["counts"]["documents"], "committed_shards": number, "nnz": nnz,
                    "invocation_elapsed_seconds": time.monotonic() - started, "new_rows_this_invocation": new_rows})
        if stop_after is not None and number >= stop_after:
            return {"status": "interrupted_for_test", "committed_shards": number}
    require(not parts and dict(counts) == plan["counts"], "Sparse source coverage differs")
    manifest = {"version": VERSION, "status": "complete", "scope": plan["scope"], "binding": binding,
                "plan_sha256": sha(root / "plan.json"), "input_bundle_id": plan["input_bundle_id"],
                "space_id": plan["space_id"], "configuration": plan["configuration"],
                "vocabulary_size": plan["vocabulary_size"], "counts": dict(counts), "empty_rows": empty,
                "nnz": nnz, "representations": representations(plan), "shards": summaries}
    # Publication follows an independent read of every stored shard and mapping.
    validate_store(root, source=None, manifest=manifest)
    if before_publish is not None:
        before_publish()
    if (root / "manifest.json").exists():
        require(read(root / "manifest.json") == manifest, "Completed Sparse manifest differs")
    else:
        atomic_json(root / "manifest.json", manifest)
    atomic_json(root / "progress.json", {"status": "complete", "completed_rows": start,
                "total_rows": start, "committed_shards": number, "nnz": nnz})
    event(root, "build_complete", counts=dict(counts), nnz=nnz, empty_rows=empty)
    return manifest


def validate_store(root, *, source=None, manifest=None):
    """Independent, one-shard-at-a-time integrity/coverage pass. No model needed."""
    root = Path(root).resolve()
    m = read(root / "manifest.json") if manifest is None else manifest
    plan = read(root / "plan.json")
    require(m["version"] == VERSION and m["status"] == "complete" and m["scope"] == plan["scope"]
            and m["plan_sha256"] == sha(root / "plan.json") and m["binding"] == digest(plan)
            and m["space_id"] == digest(plan["configuration"]) and m["space_id"] == plan["space_id"]
            and m["configuration"] == plan["configuration"] and m["input_bundle_id"] == plan["input_bundle_id"]
            and m["vocabulary_size"] == plan["vocabulary_size"]
            and m["representations"] == representations(plan), "Sparse manifest/plan binding differs")
    all_parts = committed(root, m["binding"])
    counts, nnz, empty, start = Counter({"documents": 0, "micro": 0, "macro": 0}), 0, 0, 0
    iterator = iter(source) if source is not None else None
    seen = set()
    for number, summary in enumerate(m["shards"]):
        folder = inside(root, summary["folder"])
        require(sha(folder / "complete.json") == summary["receipt_sha256"], "Sparse receipt hash differs")
        rec = verify_receipt(folder, m["binding"])
        require(number in all_parts and all_parts.pop(number)[0] == folder and rec["number"] == number
                and rec["start"] == start and rec["rows"] == min(plan["shard_rows"], plan["counts"]["documents"] - start),
                "Sparse shard order/count differs")
        matrix, rows = matrix_read(folder, rec, m["vocabulary_size"])
        for ordinal, row in enumerate(rows, start):
            require(row["chunk_id"] not in seen, "Duplicate Sparse Chunk identity")
            seen.add(row["chunk_id"])
            if plan["scope"] == "full-selected":
                require(row["input_row"] == ordinal, "Sparse full input order differs")
            if iterator is not None:
                expected = next(iterator, None)
                require(expected is not None and row == expected[0], "Sparse mapping/source differs")
        counts.update(rec["counts"])
        nnz, empty, start = nnz + rec["nnz"], empty + rec["empty_rows"], start + len(rows)
        del matrix, rows
    require(not all_parts and dict(counts) == plan["counts"] == m["counts"]
            and nnz == m["nnz"] and empty == m["empty_rows"], "Sparse complete coverage/accounting differs")
    require(iterator is None or next(iterator, None) is None, "Trailing source rows")
    return {"status": "PASS", "counts": dict(counts), "nnz": nnz, "empty_rows": empty,
            "manifest_sha256": sha(root / "manifest.json") if manifest is None else None}


class StreamingSparseSession:
    """Bounded exact scan; one physical row contributes once to each membership."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        validate_store(self.root)
        self.manifest = read(self.root / "manifest.json")
        from ..retrieval.clean_based.source import FileBinding
        self.binding = FileBinding()
        self.binding.bind(self.root / "manifest.json")
        self.binding.bind(self.root / "plan.json", self.manifest["plan_sha256"])
        for summary in self.manifest["shards"]:
            folder = inside(self.root, summary["folder"])
            self.binding.bind(folder / "complete.json", summary["receipt_sha256"])
            for name, checksum in read(folder / "complete.json")["files"].items():
                self.binding.bind(folder / name, checksum)

    def search(self, weights, *, space_id, top_k=50):
        import numpy as np
        require(space_id == self.manifest["space_id"] and type(top_k) is int and top_k > 0, "Foreign Sparse query")
        self.binding.check()
        query = csr_weights([weights], self.manifest["vocabulary_size"]).toarray()[0]
        heaps = {g: [] for g in LEVELS}
        for summary in self.manifest["shards"]:
            folder = inside(self.root, summary["folder"])
            matrix, rows = matrix_read(folder, read(folder / "complete.json"), self.manifest["vocabulary_size"])
            scores = matrix @ query
            require(bool(np.isfinite(scores).all()), "Nonfinite Sparse scores")
            for row, score in zip(rows, scores):
                if score <= 0:
                    continue
                for g in row["granularities"]:
                    item = (float(score), -row["input_row"], row)
                    if len(heaps[g]) < top_k:
                        heapq.heappush(heaps[g], item)
                    elif item[:2] > heaps[g][0][:2]:
                        heapq.heapreplace(heaps[g], item)
            del matrix, rows, scores
        self.binding.check()
        return {g: [{**r, "score": s} for s, _, r in sorted(h, key=lambda v: (-v[0], -v[1]))]
                for g, h in heaps.items()}
