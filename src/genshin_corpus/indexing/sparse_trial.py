"""Bounded BGE-M3 Sparse encoding and SciPy CSR reuse; not a full builder."""
import ast
from hashlib import sha256
from importlib.metadata import version
import math
from pathlib import Path
from types import SimpleNamespace
import zipfile

from ..build_runtime import atomic_json
from ..evidence_io import digest, inside, read, require, sha
from .benchmark_io import dump_lines, lines, receipt, verify_receipt

MAX_TRIAL_ROWS = 128


def official_kernel(wheel, expected_sha256):
    """Execute the unmodified pinned official sparse method, without importing
    unrelated training/data dependencies. This is kernel, not end-to-end parity.
    """
    import torch
    require(sha(wheel) == expected_sha256, "Official reference wheel checksum mismatch")
    name = "FlagEmbedding/finetune/embedder/encoder_only/m3/modeling.py"
    with zipfile.ZipFile(wheel) as archive:
        source = archive.read(name).decode("utf-8")
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "EncoderOnlyEmbedderM3Model")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_sparse_embedding")
    require(not method.decorator_list, "Unexpected reference method decorator")
    namespace = {"torch": torch}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(wheel) + ":" + name, "exec"), namespace)
    return namespace["_sparse_embedding"], {"wheel_sha256": expected_sha256,
        "source_file": name, "source_sha256": sha256(source.encode()).hexdigest(),
        "scope": "official sparse kernel on identical hidden states; not full FlagEmbedding pipeline"}


class SparseEncoder:
    def __init__(self, dense_encoder, *, head_sha256, reference=None):
        self.encoder = dense_encoder
        self.torch = dense_encoder.torch
        self.path = dense_encoder.path / "sparse_linear.pt"
        require(sha(self.path) == head_sha256, "Sparse head checksum mismatch")
        self.head_state = self.torch.load(self.path, map_location="cpu", weights_only=True)
        dimensions = dense_encoder.contract["dimensions"]
        require(set(self.head_state) == {"weight", "bias"}
                and tuple(self.head_state["weight"].shape) == (1, dimensions)
                and tuple(self.head_state["bias"].shape) == (1,)
                and all(bool(v.isfinite().all()) for v in self.head_state.values()), "Invalid Sparse head")
        self.head = None
        self.reference = reference
        self.contract = {"version": "gc-bge-sparse-1", "backbone": dense_encoder.contract,
                         "head_sha256": head_sha256, "text": "unchanged retrieval_text",
                         "normalization": "none", "aggregation": "max weight per token id",
                         "special_tokens": ["cls_token_id", "eos_token_id", "pad_token_id", "unk_token_id"],
                         "score": "sum query_weight * document_weight over shared token ids",
                         "pruning": "none; retain all positive weights", "output_dtype": "float32"}
        self.space_id = digest(self.contract)
        self.reference_checks = []

    def encode(self, texts, *, role="document", verify_reference=False):
        import numpy as np
        torch, encoder = self.torch, self.encoder
        lengths = encoder.lengths(texts, role)
        require(max(lengths) <= encoder.model_config["max_tokens"], "Overlength Sparse input; truncation forbidden")
        encoder.load()
        if self.head is None:
            self.head = torch.nn.Linear(encoder.contract["dimensions"], 1,
                                       dtype=getattr(torch, encoder.contract["compute_dtype"]))
            self.head.load_state_dict(self.head_state, strict=True)
            self.head = self.head.to("cuda").eval()
        tokens = encoder.tokenizer(encoder.project(texts, role), padding=True, truncation=False,
                                   add_special_tokens=True, return_tensors="pt").to("cuda")
        require(tokens["attention_mask"].sum(dim=1).tolist() == lengths, "Sparse padding changed token counts")
        excluded = {getattr(encoder.tokenizer, key) for key in self.contract["special_tokens"]}
        result = []
        with torch.inference_mode():
            hidden = encoder.model(**tokens, use_cache=False).last_hidden_state
            weights = torch.relu(self.head(hidden)).squeeze(-1)
            require(bool(weights.isfinite().all()), "Nonfinite Sparse token weight")
            ids = tokens["input_ids"].cpu().tolist()
            masks = tokens["attention_mask"].cpu().tolist()
            for sequence, mask, values in zip(ids, masks, weights.float().cpu().tolist()):
                item = {}
                for token, visible, weight in zip(sequence, mask, values):
                    if visible and token not in excluded and weight > 0:
                        item[token] = max(item.get(token, 0.), weight)
                result.append(item)
            if verify_reference:
                require(self.reference is not None, "Missing official reference kernel")
                context = SimpleNamespace(sparse_linear=self.head, training=False,
                    vocab_size=encoder.model.config.vocab_size, tokenizer=encoder.tokenizer)
                expected = self.reference(context, hidden, tokens["input_ids"]).float().cpu().numpy()
                maximum = 0.
                for row, item in zip(expected, result):
                    indices = np.flatnonzero(row)
                    require(set(map(int, indices)) == set(item), "Official Sparse token IDs differ")
                    maximum = max(maximum, max((abs(float(row[t]) - w) for t, w in item.items()), default=0.))
                require(maximum <= 1e-6, "Official Sparse weights differ")
                self.reference_checks.append({"rows": len(texts), "max_abs_error": maximum, "status": "PASS"})
        return result

    def close(self):
        import gc
        self.head = None
        self.encoder.model = None
        gc.collect()
        self.torch.cuda.empty_cache()


def csr_weights(vectors, vocabulary_size):
    import numpy as np
    from scipy.sparse import csr_array
    require(type(vocabulary_size) is int and vocabulary_size > 0, "Invalid Sparse vocabulary")
    indices, values, pointers = [], [], [0]
    for item in vectors:
        for token, weight in sorted(item.items()):
            require(type(token) is int and 0 <= token < vocabulary_size
                    and type(weight) in {int, float} and math.isfinite(weight) and 0 < weight <= float(np.finfo(np.float32).max),
                    "Invalid Sparse token/weight")
            indices.append(token)
            values.append(weight)
        pointers.append(len(indices))
    matrix = csr_array((np.asarray(values, dtype=np.float32), np.asarray(indices, dtype=np.int32),
                        np.asarray(pointers, dtype=np.int64)), shape=(len(vectors), vocabulary_size))
    require(matrix.has_canonical_format and bool(np.isfinite(matrix.data).all()), "Invalid CSR representation")
    return matrix


def build_trial(rows, vectors, root, *, input_bundle_id, contract, vocabulary_size, shard_rows=8, stop_after=None):
    """A deliberately bounded builder; completed shards survive interruptions."""
    from scipy.sparse import save_npz
    require(0 < len(rows) <= MAX_TRIAL_ROWS and len(rows) == len(vectors), "Sparse trial requires 1–128 rows")
    require(type(shard_rows) is int and 0 < shard_rows <= MAX_TRIAL_ROWS, "Invalid Sparse shard size")
    require(len({r["input_row"] for r in rows}) == len(rows)
            and len({r["chunk_id"] for r in rows}) == len(rows), "Duplicate Sparse source mapping")
    for row in rows:
        require(type(row["input_row"]) is int and row["input_row"] >= 0
                and bool(row["granularities"]) and set(row["granularities"]) <= {"micro", "macro"}
                and len(row["granularities"]) == len(set(row["granularities"])), "Invalid Sparse route mapping")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    plan = {"scope": "bounded-trial", "input_bundle_id": input_bundle_id, "configuration": contract,
            "rows": rows, "vectors_sha256": digest([{str(k): v for k, v in sorted(x.items())} for x in vectors]),
            "vocabulary_size": vocabulary_size, "shard_rows": shard_rows, "scipy": version("scipy")}
    binding = digest(plan)
    if (root / "plan.json").exists():
        require(read(root / "plan.json") == plan, "Sparse resume binding changed")
    else:
        atomic_json(root / "plan.json", plan)
    committed = {}
    for path in (root / "shards").glob("*/complete.json"):
        rec = verify_receipt(path.parent, binding)
        require(rec["number"] not in committed, "Duplicate committed Sparse shard")
        committed[rec["number"]] = path.parent
    summaries = []
    from .benchmark_io import attempt
    for number, start in enumerate(range(0, len(rows), shard_rows)):
        stop = min(start + shard_rows, len(rows))
        if number in committed:
            folder = committed.pop(number)
            require(read(folder / "complete.json")["start"] == start
                    and list(lines(folder / "rows.jsonl")) == rows[start:stop], "Sparse shard source differs")
        else:
            folder = attempt(root / "shards", f"part-{number:04}")
            matrix = csr_weights(vectors[start:stop], vocabulary_size)
            save_npz(folder / "weights.npz", matrix, compressed=False)
            dump_lines(folder / "rows.jsonl", rows[start:stop])
            receipt(folder, binding, number=number, start=start, rows=stop - start)
        summaries.append({"folder": folder.relative_to(root).as_posix(),
                          "receipt_sha256": sha(folder / "complete.json")})
        if stop_after is not None and len(summaries) >= stop_after:
            return {"status": "interrupted_for_test", "committed_shards": len(summaries)}
    require(not committed, "Unexpected extra Sparse shard")
    counts = {"documents": len(rows), **{g: sum(g in r["granularities"] for r in rows) for g in ("micro", "macro")}}
    manifest = {"version": "gc-bge-sparse-trial-1", "status": "complete", "scope": "bounded-trial",
                "input_bundle_id": input_bundle_id, "binding": binding, "plan_sha256": sha(root / "plan.json"),
                "space_id": digest(contract), "configuration": contract, "vocabulary_size": vocabulary_size,
                "counts": counts, "empty_rows": sum(not v for v in vectors), "shards": summaries}
    manifest["representations"] = {g: digest({"kind": "sparse", "scope": "bounded-trial", "binding": binding,
                                             "granularity": g}) for g in ("micro", "macro")}
    if (root / "manifest.json").exists():
        require(read(root / "manifest.json") == manifest, "Completed Sparse manifest changed")
    else:
        atomic_json(root / "manifest.json", manifest)
    return manifest


class SparseSession:
    def __init__(self, root):
        from ..retrieval.clean_based.source import FileBinding
        from scipy.sparse import load_npz
        import numpy as np
        self.root = Path(root)
        self.binding = FileBinding()
        self.binding.bind(self.root / "manifest.json")
        self.manifest = read(self.root / "manifest.json")
        m = self.manifest
        require(m["status"] == "complete" and m["scope"] == "bounded-trial"
                and m["version"] == "gc-bge-sparse-trial-1", "Not a completed Sparse trial")
        self.binding.bind(self.root / "plan.json", m["plan_sha256"])
        plan = read(self.root / "plan.json")
        require(digest(plan) == m["binding"] and digest(m["configuration"]) == m["space_id"]
                and plan["configuration"] == m["configuration"]
                and plan["input_bundle_id"] == m["input_bundle_id"]
                and plan["vocabulary_size"] == m["vocabulary_size"], "Sparse space/input binding differs")
        expected_representations = {g: digest({"kind": "sparse", "scope": "bounded-trial", "binding": m["binding"],
                                               "granularity": g}) for g in ("micro", "macro")}
        require(m["representations"] == expected_representations, "Sparse route representation differs")
        observed, matrices, empty_rows = [], [], 0
        for summary in m["shards"]:
            folder = inside(self.root, summary["folder"])
            self.binding.bind(folder / "complete.json", summary["receipt_sha256"])
            rec = verify_receipt(folder, m["binding"])
            for name, checksum in rec["files"].items():
                self.binding.bind(inside(folder, name), checksum)
            rows = list(lines(folder / "rows.jsonl"))
            require(rec["number"] == len(matrices) and rec["start"] == len(observed)
                    and rec["rows"] == len(rows), "Sparse shard order/count differs")
            matrix = load_npz(folder / "weights.npz").tocsr()
            matrix.check_format(full_check=True)
            require(matrix.shape == (len(rows), m["vocabulary_size"]) and matrix.dtype == np.dtype("float32")
                    and matrix.has_canonical_format and bool(np.isfinite(matrix.data).all())
                    and bool((matrix.data > 0).all()), "Sparse matrix contract differs")
            matrices.append((matrix, rows))
            empty_rows += int((np.diff(matrix.indptr) == 0).sum())
            observed.extend(rows)
        require(observed == plan["rows"] and 0 < len(observed) <= MAX_TRIAL_ROWS, "Sparse source rows differ")
        require(m["counts"] == {"documents": len(observed), **{
            g: sum(g in r["granularities"] for r in observed) for g in ("micro", "macro")}}, "Sparse route counts differ")
        require(empty_rows == m["empty_rows"], "Sparse empty-row accounting differs")
        self.matrices = matrices  # At most128 rows; full-corpus residency is not implied.

    def search(self, weights, *, space_id, top_k=50):
        import numpy as np
        require(space_id == self.manifest["space_id"] and type(top_k) is int and top_k > 0, "Foreign Sparse query")
        self.binding.check()
        query = csr_weights([weights], self.manifest["vocabulary_size"]).toarray()[0]
        routes = {g: [] for g in ("micro", "macro")}
        for matrix, rows in self.matrices:
            scores = matrix @ query
            require(bool(np.isfinite(scores).all()), "Nonfinite Sparse score")
            for row, score in zip(rows, scores):
                if score > 0:
                    for g in row["granularities"]:
                        routes[g].append({**row, "score": float(score)})
        return {g: sorted(rows, key=lambda r: (-r["score"], r["input_row"]))[:top_k] for g, rows in routes.items()}
