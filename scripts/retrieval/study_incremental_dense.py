"""Bounded local Dense comparison. Reads selected assets and writes a new study root."""

import argparse
from collections import defaultdict
from pathlib import Path
import time

from genshin_corpus.evidence_io import digest, read, require, seal, sha, write
from genshin_corpus.indexing.benchmark_dense import Encoder, encode_bounded, validate_vectors
from genshin_corpus.indexing.benchmark_io import lines, offline_only, ram_guard, resources
from genshin_corpus.retrieval.clean_based.indexes import model_spec, selected
from genshin_corpus.runtime_compatibility import index_space


BUCKET_LENGTHS = (64, 128, 256, 512, 768, 1024, 1152, 2048, 4096, 8192)
QUESTIONS = (
    "前任大贤者阿扎尔出身于哪个学派？", "白夜国的历史和禁忌知识有什么关系？",
    "提瓦特的元素反应有哪些？", "原石和摩拉有哪些获取途径？",
    "枫丹的预言说了什么？", "钟离和若陀龙王是什么关系？",
    "纳塔的部族有哪些传统？", "蒙德的冒险家协会有哪些任务？",
)
RTOL, ATOL = 1e-5, 1e-6


def sample(manifest_path, manifest, config):
    import numpy as np

    root = manifest_path.parent
    length_root = root / read(root / "lengths.json")["folder"]
    length_path = length_root / "lengths.jsonl"
    require(sha(length_path) == read(length_root / "complete.json")["files"][length_path.name], "Length audit changed")
    lengths = [row["tokens"] for row in lines(length_path)]
    width = config["shard_rows"]
    longest = max(range(len(lengths)), key=lengths.__getitem__) // width
    count = len(manifest["shards"])
    # Keep complete historical shards so batching, padding and partners replay exactly.
    chosen = sorted({0, count // 3, 2 * count // 3, longest})
    wanted = set()
    stored = {}
    for part in chosen:
        shard = manifest["shards"][part]
        folder = root / shard["folder"]
        receipt = folder / "complete.json"
        require(sha(receipt) == shard["receipt_sha256"], "Dense receipt changed")
        for name, checksum in read(receipt)["files"].items():
            require(sha(folder / name) == checksum, "Dense source shard changed")
        values = np.load(folder / "vectors.npy", allow_pickle=False)
        for row, vector in zip(lines(folder / "rows.jsonl"), values, strict=True):
            number = row["input_row"]
            wanted.add(number)
            stored[number] = row, vector

    bundle = Path(manifest["input_manifest"])
    input_path = bundle.parent / "inputs.jsonl"
    require(sha(input_path) == read(bundle)["artifacts"][input_path.name], "Input data changed")
    matched = []
    for number, row in enumerate(lines(input_path)):
        if number in wanted:
            index, vector = stored[number]
            require(all(row[k] == index[k] for k in ("chunk_id", "dense_text_sha256", "granularities")), "Dense/input binding changed")
            matched.append({"row": number, "group": number // width, "tokens": lengths[number],
                            "vector": vector, "text": row["dense_text"], "text_sha256": index["dense_text_sha256"],
                            "chunk_id": row["chunk_id"], "routes": row["granularities"]})
    require(len(matched) == len(wanted), "Dense sample is incomplete")
    return matched


def fixed_pair(encoder, rows):
    import numpy as np
    import torch

    groups = defaultdict(list)
    for number, row in enumerate(rows):
        pad = next(length for length in BUCKET_LENGTHS if row["tokens"] <= length)
        groups[pad].append((number, row))
    vectors = np.empty((len(rows), encoder.contract["dimensions"]), dtype=np.float32)
    padded_tokens = 0
    for pad, group in sorted(groups.items()):
        for offset in range(0, len(group), 2):
            actual = group[offset:offset + 2]
            batch = actual if len(actual) == 2 else actual * 2
            tokenized = encoder.tokenizer(encoder.project([row["text"] for _, row in batch], "document"),
                                          padding="max_length", max_length=pad, truncation=False,
                                          add_special_tokens=True, return_tensors="pt").to("cuda")
            require(tuple(tokenized["input_ids"].shape) == (2, pad)
                    and tokenized["attention_mask"].sum(dim=1).tolist() == [row["tokens"] for _, row in batch],
                    "Fixed padding changed actual tokens")
            with torch.inference_mode():
                pooled = encoder.model(**tokenized, use_cache=False).last_hidden_state[:, 0].float()
                result = (pooled / torch.linalg.vector_norm(pooled, dim=1, keepdim=True)).cpu().numpy()
            for (number, _), vector in zip(actual, result):
                vectors[number] = vector
            padded_tokens += 2 * pad
    validate_vectors(vectors, len(rows), encoder.contract["dimensions"])
    return vectors, padded_tokens


def encode(encoder, method, rows, config, output):
    import numpy as np

    encoder.torch.cuda.empty_cache()
    encoder.torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    if method == "fixed_pair":
        vectors, padded = fixed_pair(encoder, rows)
    elif method == "single":
        vectors = np.concatenate([encoder.encode([row["text"]]) for row in rows])
        padded = sum(row["tokens"] for row in rows)
    else:
        groups = defaultdict(list)
        for row in rows:
            groups[row["group"]].append(row)
        vectors = np.concatenate([encode_bounded(encoder, [row["text"] for row in group],
                                                  [row["tokens"] for row in group], config, output)
                                  for group in groups.values()])
        padded = None
    encoder.torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    print(method, len(rows), round(elapsed, 3), flush=True)
    return vectors, {"seconds": elapsed, "rows_per_second": len(rows) / elapsed,
                     "padded_tokens": padded, "resources": resources()}


def compare(reference, candidate):
    import numpy as np

    return {"rows": len(reference), "bitwise_equal_rows": int(np.all(reference == candidate, axis=1).sum()),
            "failed_rows": int((~np.all(np.isclose(reference, candidate, rtol=RTOL, atol=ATOL), axis=1)).sum()),
            "max_absolute_error": float(np.max(np.abs(reference - candidate))),
            "min_cosine": float(np.min(np.sum(reference * candidate, axis=1)))}


def top20(matrix, query, rows, route):
    scores = (matrix @ query).tolist()
    indices = sorted((i for i, row in enumerate(rows) if route in row["routes"]), key=lambda i: (-scores[i], rows[i]["row"]))[:20]
    return [rows[i]["row"] for i in indices]


def run(repo, output):
    import numpy as np

    config = read(repo / "config/index-benchmark-v1.json")
    query_config = read(repo / "config/clean-retrieval-v1.json")
    selection, manifests = selected(repo, query_config)
    path, manifest = manifests["dense"]
    rows = sample(path, manifest, config)
    write(output / "sample.json", {"rows": [{k: v for k, v in row.items() if k not in {"text", "vector"}} for row in rows],
                                   "source_manifest": sha(path), "input_manifest": sha(manifest["input_manifest"]),
                                   "questions": QUESTIONS})
    ram_guard(config)
    started = time.perf_counter()
    encoder = Encoder(model_spec(selection, manifest), repo)
    index_space(repo, query_config, manifest, encoder.contract)
    encoder.load()
    require(encoder.lengths([row["text"] for row in rows]) == [row["tokens"] for row in rows], "Token counts changed")
    # Warm the same model before timing each document profile.
    queries = [encoder.encode([question], role="query")[0] for question in QUESTIONS]
    load_seconds = time.perf_counter() - started
    stored = np.stack([row["vector"] for row in rows])
    matrices = {"stored": stored}
    results = {}
    for method in ("historic_dynamic", "single", "fixed_pair"):
        matrix, timing = encode(encoder, method, rows, config, output)
        matrices[method] = matrix
        results[method] = {"timing": timing, "against_stored": compare(stored, matrix)}

    # Replace one row in each shard, then compare unaffected rows against a
    # full encode with the new identities, input order, companions and padding.
    first_per_shard = {}
    for i, row in enumerate(rows):
        first_per_shard.setdefault(row["group"], i)
    removed = set(first_per_shard.values())
    updated = [row for i, row in enumerate(rows) if i not in removed]
    for i in sorted(removed):
        row = rows[i]
        text = "离线增量验证用替换文本。" + row["text"]
        updated.append({**row, "row": row["row"] + 10**6, "text": text, "tokens": encoder.lengths([text])[0]})
    updated.sort(key=lambda row: (row["group"], row["row"]))
    old = {row["row"]: i for i, row in enumerate(rows)}
    unchanged = [(old[row["row"]], i) for i, row in enumerate(updated) if row["row"] in old]
    old_order, new_order = zip(*unchanged, strict=True)
    for method in results:
        after, timing = encode(encoder, method, updated, config, output)
        results[method]["unchanged_after_edit"] = compare(matrices[method][list(old_order)], after[list(new_order)])
        results[method]["edited_timing"] = timing
        cached = after.copy()
        cached[list(new_order)] = matrices[method][list(old_order)]
        results[method]["edited_top20_exact"] = sum(top20(after, query, updated, route) == top20(cached, query, updated, route)
                                                  for query in queries for route in ("micro", "macro"))
        results[method]["unchanged_context_top20"] = [
            {"question": question, "route": route, "stored": top20(stored, query, rows, route),
             "candidate": top20(matrices[method], query, rows, route)}
            for question, query in zip(QUESTIONS, queries, strict=True) for route in ("micro", "macro")]
        np.save(output / (method + ".npy"), matrices[method], allow_pickle=False)
    # Reorder and rotate the same rows so slots and neighboring inputs change
    # within the fixed padding groups. One removal also changes tail handling.
    permuted = list(reversed(rows[1:]))
    permuted = permuted[1:] + permuted[:1]
    for method in ("single", "fixed_pair"):
        reordered, timing = encode(encoder, method, permuted, config, output)
        reference = matrices[method][[old[row["row"]] for row in permuted]]
        results[method]["changed_partner_slot_and_tail"] = compare(reference, reordered)
        results[method]["reordered_timing"] = timing
    write(output / "report.json", {"scope": "selected shard sample only", "results": results, "rows": len(rows),
                                    "load_and_warm_seconds": load_seconds, "target_contract": encoder.contract,
                                    "source_space": manifest["space_id"], "sample_id": digest(read(output / "sample.json")),
                                    "fixed_sequence_lengths": BUCKET_LENGTHS, "rtol": RTOL, "atol": ATOL,
                                    "runner_sha256": sha(__file__)})
    seal(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    require(output.parent == repo / "data/retrieval" and output.name.startswith("incremental-study-dense-"), "Use a fresh named study root")
    output.mkdir()
    with offline_only():
        from transformers.utils.logging import disable_progress_bar

        disable_progress_bar()
        run(repo, output)


if __name__ == "__main__":
    main()
