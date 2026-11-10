"""Local no-truncation encoders, transactional vector shards and exact search."""
from collections import Counter
import gc
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import time

from ..build_runtime import atomic_json
from ..evidence_io import digest, read, require, sha
from .benchmark_io import (attempt, batches, disk_guard, dump_lines, event,
                           lines, mapping, ram_guard, receipt, resources, routes, verify_receipt)
from .inputs import iter_inputs


def snapshot(model, repo):
    root = (repo / model["path"]).resolve()
    require(sha(root / model["weight_file"]) == model["weight_sha256"], "Pinned model weight hash mismatch")
    installation = root / "installation-manifest.json"
    if installation.exists():
        installed = read(installation)
        require(installed["revision"] == model["revision"] and installed["model_id"] == model["model"],
                "Model installation revision mismatch")
    else:
        metadata = root / ".cache/huggingface/download/config.json.metadata"
        require(metadata.read_text().splitlines()[0] == model["revision"], "Downloaded revision mismatch")
    names = [p for p in root.iterdir() if p.is_file() and p.suffix in {".json", ".txt", ".model", ".jinja"}
             and p.name not in {"installation-manifest.json", "small-files-manifest.json"}]
    names += list((root / "1_Pooling").glob("*.json"))
    files = {p.relative_to(root).as_posix(): sha(p) for p in names}
    files[model["weight_file"]] = model["weight_sha256"]
    require({"config.json", "tokenizer.json", "tokenizer_config.json"} <= files.keys(), "Incomplete tokenizer snapshot")
    config = read(root / "config.json")
    require(config["hidden_size"] == model["dimensions"], "Model dimension mismatch")
    native_cap = config["max_position_embeddings"] - (2 if model["pooling"] == "cls_token" else 0)
    require(0 < model["max_tokens"] <= native_cap, "Token limit exceeds model context")
    if model["pooling"] == "cls_token":
        require(read(root / "1_Pooling/config.json")["pooling_mode_cls_token"], "BGE CLS pooling evidence missing")
    return files


class Encoder:
    def __init__(self, model, repo):
        self.model_config = model
        self.path = (repo / model["path"]).resolve()
        self.files = snapshot(model, repo)
        import torch
        from transformers import AutoTokenizer
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.path), local_files_only=True,
                                                     trust_remote_code=False, padding_side=model["padding_side"])
        self.tokenizer.padding_side = model["padding_side"]
        require(self.tokenizer.pad_token_id is not None, "Tokenizer has no pad token")
        packages = {p: version(p) for p in ("torch", "transformers", "tokenizers", "numpy", "safetensors")}
        self.contract = {key: model[key] for key in
                         ("model", "revision", "dimensions", "pooling", "padding_side", "compute_dtype",
                          "max_tokens", "document_prefix", "query_prefix", "weight_sha256")}
        self.contract.update(tokenizer_sha256=digest({k: v for k, v in self.files.items()
                                                     if k != model["weight_file"]}),
                             snapshot_files=self.files, normalization="L2", output_dtype="float32",
                             attention="sdpa", device="cuda", truncation=False, add_special_tokens=True,
                             use_cache=False, python=platform.python_version(), packages=packages)
        self.space_id = digest(self.contract)
        self.model = None

    def project(self, texts, role):
        require(role in {"document", "query"}, "Unknown encoding role")
        require(bool(texts) and all(isinstance(t, str) and t.strip() for t in texts), "Empty encoding text")
        return [self.model_config[role + "_prefix"] + text for text in texts]

    def lengths(self, texts, role="document"):
        return [len(ids) for ids in self.tokenizer(self.project(texts, role), add_special_tokens=True,
                                                 truncation=False)["input_ids"]]

    def load(self):
        if self.model is not None:
            return
        from transformers import AutoModel
        torch = self.torch
        require(torch.cuda.is_available(), "CUDA unavailable for the pinned CUDA representation")
        if self.model_config["compute_dtype"] == "bfloat16":
            require(torch.cuda.is_bf16_supported(), "BF16 unavailable")
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        torch.manual_seed(0)
        torch.set_num_threads(4)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.use_deterministic_algorithms(True)
        self.model = AutoModel.from_pretrained(str(self.path), local_files_only=True, trust_remote_code=False,
                                              dtype=getattr(torch, self.model_config["compute_dtype"]),
                                              attn_implementation="sdpa").to("cuda").eval()
        torch.cuda.reset_peak_memory_stats()

    def encode(self, texts, *, role="document"):
        import numpy as np
        lengths = self.lengths(texts, role)
        require(max(lengths) <= self.model_config["max_tokens"], "Overlength input. No truncation permitted")
        self.load()
        tokens = self.tokenizer(self.project(texts, role), padding=True, truncation=False,
                                add_special_tokens=True, return_tensors="pt").to("cuda")
        require(tokens["attention_mask"].sum(dim=1).tolist() == lengths, "Tokenizer padding changed token counts")
        torch = self.torch
        with torch.inference_mode():
            hidden = self.model(**tokens, use_cache=False).last_hidden_state
            if self.model_config["pooling"] == "cls_token":
                pooled = hidden[:, 0].float()
            else:
                positions = torch.arange(hidden.shape[1], device=hidden.device).expand(tokens["attention_mask"].shape)
                last = positions.masked_fill(tokens["attention_mask"] == 0, -1).max(dim=1).values
                pooled = hidden[torch.arange(hidden.shape[0], device=hidden.device), last].float()
            norms = torch.linalg.vector_norm(pooled, dim=1, keepdim=True)
            require(bool(torch.isfinite(pooled).all()) and bool((norms > 0).all()), "Invalid encoder output")
            result = (pooled / norms).cpu().numpy().astype(np.float32)
        validate_vectors(result, len(texts), self.model_config["dimensions"])
        return result


def validate_vectors(vectors, count, dimensions):
    import numpy as np
    require(vectors.shape == (count, dimensions) and vectors.dtype == np.dtype("float32"), "Vector shape/dtype mismatch")
    require(bool(np.isfinite(vectors).all()) and bool(np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5, rtol=0)),
            "Nonfinite or non-normalized vector")


def encode_bounded(encoder, texts, lengths, config, root, *, identities=None):
    """Sort only one bounded shard and scatter to input order. Retry only CUDA OOM."""
    import numpy as np
    result = np.empty((len(texts), encoder.contract["dimensions"]), dtype=np.float32)
    order = sorted(range(len(texts)), key=lambda i: (lengths[i], i))
    position, cap = 0, config["max_batch"]
    heartbeat = time.monotonic()
    while position < len(order):
        take = 1
        while (take < cap and position + take < len(order)
               and lengths[order[position + take]] * (take + 1) <= config["padded_token_budget"]):
            take += 1
        selected = order[position:position + take]
        try:
            encoded = encoder.encode([texts[i] for i in selected])
        except encoder.torch.cuda.OutOfMemoryError:
            event(root, "cuda_oom", chunk_positions=selected, token_lengths=[lengths[i] for i in selected], batch_size=take,
                  inputs=[identities[i] for i in selected] if identities is not None else [])
            gc.collect()
            encoder.torch.cuda.empty_cache()
            if take == 1:
                raise
            cap = max(1, take // 2)
            continue
        result[selected] = encoded
        position += take
        if time.monotonic() - heartbeat >= 15:
            event(root, "encoding_batch", processed_in_shard=position, shard_rows=len(texts), batch_size=take)
            heartbeat = time.monotonic()
    return result


def length_audit(encoder, input_manifest, root, config):
    bundle = read(input_manifest)
    binding = digest({"input": bundle["input_bundle_id"], "space": encoder.space_id})
    pointer = root / "lengths.json"
    if pointer.exists():
        folder = root / read(pointer)["folder"]
        return folder, verify_receipt(folder, binding)
    folder = attempt(root / "length-attempts", "scan")
    histogram, longest = Counter(), []
    total, exceeded = 0, 0
    started = time.monotonic()
    with (folder / "lengths.jsonl").open("x", encoding="utf-8") as handle, \
         (folder / "overlength.jsonl").open("x", encoding="utf-8") as issues:
        for group in batches(input_manifest, config["shard_rows"]):
            disk_guard(root, config)
            texts = [row["dense_text"] for _, row in group]
            lengths = encoder.lengths(texts)
            for (number, row), tokens in zip(group, lengths):
                value = {"input_row": number, "chunk_id": row["chunk_id"],
                         "dense_text_sha256": row["dense_text_sha256"], "tokens": tokens}
                handle.write(json.dumps(value, ensure_ascii=False) + "\n")
                if tokens > encoder.contract["max_tokens"]:
                    exceeded += 1
                    issues.write(json.dumps(value, ensure_ascii=False) + "\n")
                histogram[tokens] += 1
                longest.append((tokens, number, row["dense_text"], row["chunk_id"]))
                longest.sort(key=lambda item: (-item[0], item[1]))
                del longest[config["smoke_documents"]:]
                total += 1
            if total % (config["shard_rows"] * 16) == 0:
                event(root, "tokenizer_scan", rows=total, total=bundle["counts"]["documents"], exceeded=exceeded,
                      elapsed_seconds=time.monotonic() - started)
        for handle_to_flush in (handle, issues):
            handle_to_flush.flush()
            os.fsync(handle_to_flush.fileno())
    require(total == bundle["counts"]["documents"], "Length scan row count mismatch")
    dump_lines(folder / "smoke-documents.jsonl", [{"tokens": n, "input_row": i, "text": text, "chunk_id": key}
                                                for n, i, text, key in longest])
    result = receipt(folder, binding, status="PASS" if exceeded == 0 else "BLOCKED_OVERLENGTH", rows=total,
                     exceeded=exceeded, maximum=max(histogram), histogram=dict(sorted(histogram.items())),
                     elapsed_seconds=time.monotonic() - started)
    atomic_json(pointer, {"folder": folder.relative_to(root).as_posix()})
    return folder, result


def preflight(encoder, input_manifest, root, config):
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "encoder.json", {"space_id": encoder.space_id, "configuration": encoder.contract})
    folder, audit = length_audit(encoder, input_manifest, root, config)
    require(audit["exceeded"] == 0, f"{audit['exceeded']} overlength chunks. See {folder / 'overlength.jsonl'}")
    ram_guard(config)
    documents = list(lines(folder / "smoke-documents.jsonl"))
    started = time.monotonic()
    vectors = encode_bounded(encoder, [d["text"] for d in documents], [d["tokens"] for d in documents], config, root,
                             identities=[{k: d[k] for k in ("input_row", "chunk_id")} for d in documents])
    query = encoder.encode(["坎瑞亚与深渊有什么关系？"], role="query")
    report = {"status": "PASS", "space_id": encoder.space_id, "input_bundle_id": read(input_manifest)["input_bundle_id"],
              "length_audit_folder": folder.relative_to(root).as_posix(), "length_receipt_sha256": sha(folder / "complete.json"),
              "smoke_rows": [d["input_row"] for d in documents], "query_shape": list(query.shape),
              "vector_shape": list(vectors.shape), "elapsed_seconds": time.monotonic() - started, "resources": resources()}
    atomic_json(root / "preflight.json", report)
    event(root, "preflight_pass", **report)
    return report


def _parts(root, binding):
    result = {}
    for path in sorted((root / "shards").glob("*/complete.json")):
        part = verify_receipt(path.parent, binding)
        require(part["number"] not in result, "Duplicate committed vector shard")
        result[part["number"]] = (path.parent, part)
    return result


def build(encoder, input_manifest, root, config, *, stop_after=None):
    import numpy as np
    require(read(root / "preflight.json")["space_id"] == encoder.space_id, "Preflight encoder differs")
    bundle = read(input_manifest)
    binding = digest({"input": bundle["input_bundle_id"], "space": encoder.space_id})
    parts = _parts(root, binding)
    if (root / "manifest.json").exists():
        return validate(input_manifest, root)
    folder, audit = length_audit(encoder, input_manifest, root, config)
    require(audit["exceeded"] == 0, "Length scan has overlength inputs")
    lengths_iterator = iter(lines(folder / "lengths.jsonl"))
    written, count = 0, 0
    summaries = []
    started = time.monotonic()
    for number, group in enumerate(batches(input_manifest, config["shard_rows"])):
        expected, lengths = [], []
        for index, row in group:
            length_row = next(lengths_iterator)
            require({k: length_row[k] for k in ("input_row", "chunk_id", "dense_text_sha256")} ==
                    {"input_row": index, "chunk_id": row["chunk_id"], "dense_text_sha256": row["dense_text_sha256"]},
                    "Length audit/input alignment mismatch")
            lengths.append(length_row["tokens"])
            expected.append({**mapping(index, row), "tokens": length_row["tokens"]})
        if number in parts:
            shard, part = parts[number]
            require(list(lines(shard / "rows.jsonl")) == expected, "Resumed vector/input row mapping differs")
            validate_vectors(np.load(shard / "vectors.npy", mmap_mode="r", allow_pickle=False), len(group), encoder.contract["dimensions"])
        else:
            disk_guard(root, config, additional_bytes=len(group) * encoder.contract["dimensions"] * 4)
            ram_guard(config, model_loaded=getattr(encoder, "model", None) is not None)
            texts = [row["dense_text"] for _, row in group]
            require(encoder.lengths(texts) == lengths, "Tokenizer changed since complete audit")
            encoding_started = time.monotonic()
            encoded = encode_bounded(encoder, texts, lengths, config, root,
                                     identities=[{k: row[k] for k in ("input_row", "chunk_id")} for row in expected])
            shard = attempt(root / "shards", f"{number:06d}")
            with (shard / "vectors.npy").open("xb") as handle:
                np.save(handle, encoded, allow_pickle=False)
                handle.flush()
                os.fsync(handle.fileno())
            dump_lines(shard / "rows.jsonl", expected)
            part = receipt(shard, binding, number=number, start=expected[0]["input_row"], rows=len(group),
                           encoding_seconds=time.monotonic() - encoding_started, resources=resources())
            written += 1
        summaries.append({"folder": shard.relative_to(root).as_posix(), "receipt_sha256": sha(shard / "complete.json")})
        count += len(group)
        event(root, "dense_progress", rows=count, total=bundle["counts"]["documents"],
              elapsed_seconds=time.monotonic() - started,
              eta_seconds=(time.monotonic() - started) / max(count, 1) * (bundle["counts"]["documents"] - count))
        if stop_after is not None and written >= stop_after:
            return {"status": "INTERRUPTED_FOR_TEST", "rows": count}
    require(next(lengths_iterator, None) is None, "Trailing tokenizer audit rows")
    require(count == bundle["counts"]["documents"] and set(parts) <= set(range(len(summaries))), "Unexpected vector shard range")
    candidate = {"version": "gc-dense-sharded-1", "status": "complete", "binding": binding,
                 "input_bundle_id": bundle["input_bundle_id"], "input_manifest": str(Path(input_manifest).resolve()),
                 "space_id": encoder.space_id, "configuration": encoder.contract,
                 "length_audit": {"folder": folder.relative_to(root).as_posix(),
                                  "receipt_sha256": sha(folder / "complete.json")},
                 "representations": routes(encoder.contract, input_manifest, "dense"), "counts": bundle["counts"],
                 "shard_rows": config["shard_rows"], "shards": summaries,
                 "search": "exact cosine over FP32 L2 shards, filtered by route", "elapsed_seconds": time.monotonic() - started}
    validate(input_manifest, root, candidate=candidate)
    atomic_json(root / "manifest.json", candidate)
    return candidate


def validate(input_manifest, root, *, candidate=None):
    import numpy as np
    root = Path(root)
    manifest = candidate or read(root / "manifest.json")
    bundle = read(input_manifest)
    require(manifest["version"] == "gc-dense-sharded-1" and manifest["status"] == "complete", "Incomplete Dense index")
    require(manifest["input_bundle_id"] == bundle["input_bundle_id"], "Foreign Dense input")
    require(digest(manifest["configuration"]) == manifest["space_id"], "Encoder space binding mismatch")
    require(manifest["binding"] == digest({"input": bundle["input_bundle_id"], "space": manifest["space_id"]}), "Dense binding mismatch")
    require(manifest["representations"] == routes(manifest["configuration"], input_manifest, "dense"), "Dense route representation mismatch")
    require(manifest["counts"] == bundle["counts"], "Dense count manifest mismatch")
    audit_folder = root / manifest["length_audit"]["folder"]
    require(audit_folder.resolve().is_relative_to(root.resolve()), "Length audit escapes index root")
    require(sha(audit_folder / "complete.json") == manifest["length_audit"]["receipt_sha256"], "Length receipt hash mismatch")
    audit = verify_receipt(audit_folder, manifest["binding"])
    require(audit["status"] == "PASS" and audit["exceeded"] == 0
            and audit["rows"] == bundle["counts"]["documents"], "Incomplete tokenizer audit")
    lengths_iterator = iter(lines(audit_folder / "lengths.jsonl"))
    parts = iter(manifest["shards"])
    count, memberships = 0, Counter({"micro": 0, "macro": 0})
    for number, group in enumerate(batches(input_manifest, manifest["shard_rows"])):
        summary = next(parts)
        folder = root / summary["folder"]
        require(folder.resolve().is_relative_to(root.resolve()), "Shard path escapes root")
        require(sha(folder / "complete.json") == summary["receipt_sha256"], "Shard receipt hash mismatch")
        part = verify_receipt(folder, manifest["binding"])
        require(part["number"] == number and part["start"] == count and part["rows"] == len(group), "Shard sequence mismatch")
        observed = list(lines(folder / "rows.jsonl"))
        require(len(observed) == len(group), "Vector row mapping count mismatch")
        for actual, (index, row) in zip(observed, group):
            require({k: v for k, v in actual.items() if k != "tokens"} == mapping(index, row), "Vector/source mapping mismatch")
            require(type(actual["tokens"]) is int and 0 < actual["tokens"] <= manifest["configuration"]["max_tokens"], "Invalid encoded length")
            require(next(lengths_iterator) == {"input_row": index, "chunk_id": row["chunk_id"],
                                              "dense_text_sha256": row["dense_text_sha256"], "tokens": actual["tokens"]},
                    "Vector/tokenizer audit mismatch")
            memberships.update(row["granularities"])
        vectors = np.load(folder / "vectors.npy", mmap_mode="r", allow_pickle=False)
        validate_vectors(vectors, len(group), manifest["configuration"]["dimensions"])
        count += len(group)
    require(next(parts, None) is None and count == bundle["counts"]["documents"], "Dense coverage mismatch")
    require(next(lengths_iterator, None) is None, "Trailing tokenizer audit")
    require(dict(memberships) == {k: bundle["counts"][k] for k in memberships}, "Dense route coverage mismatch")
    result = {"status": "PASS", "rows": count, "memberships": dict(memberships), "space_id": manifest["space_id"]}
    atomic_json(root / "validation.json", result)
    return result


def search(root, query_vector, *, space_id, granularity, top_k=20):
    """Exact bounded-memory cosine index. Ties follow input order."""
    import heapq
    import numpy as np
    require(granularity in {"micro", "macro"} and type(top_k) is int and top_k > 0, "Invalid search options")
    root = Path(root)
    manifest = read(root / "manifest.json")
    require(manifest["status"] == "complete" and manifest["space_id"] == space_id, "Foreign query vector space")
    require(digest(manifest["configuration"]) == space_id, "Corrupt query vector space binding")
    validate_vectors(np.asarray(query_vector, dtype=np.float32).reshape(1, -1), 1, manifest["configuration"]["dimensions"])
    best = []
    for summary in manifest["shards"]:
        folder = root / summary["folder"]
        require(folder.resolve().is_relative_to(root.resolve()), "Search shard escapes root")
        require(sha(folder / "complete.json") == summary["receipt_sha256"], "Search shard receipt changed")
        verify_receipt(folder, manifest["binding"])
        vectors = np.load(folder / "vectors.npy", mmap_mode="r", allow_pickle=False)
        rows = list(lines(folder / "rows.jsonl"))
        validate_vectors(vectors, len(rows), manifest["configuration"]["dimensions"])
        scores = vectors @ query_vector
        for row, score in zip(rows, scores):
            if granularity not in row["granularities"]:
                continue
            item = (float(score), -row["input_row"], row)
            if len(best) < top_k:
                heapq.heappush(best, item)
            elif item[:2] > best[0][:2]:
                heapq.heapreplace(best, item)
    return [{"score": score, **row} for score, _, row in sorted(best, key=lambda item: (-item[0], -item[1]))]
