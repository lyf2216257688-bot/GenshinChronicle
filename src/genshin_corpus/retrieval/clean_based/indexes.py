"""Read-only selected-index sessions. No build/resume or index-root writes."""
from pathlib import Path

from ...evidence_io import digest, inside, read, require, sha
from ...indexing.benchmark_io import lines, verify_receipt
from ...indexing.schema import representation_id
from .source import FileBinding


def selected(repo, config):
    selection = read(Path(repo) / config["selection"])
    if selection["schema"] == "gc-snapshot-dense-model-1":
        require(sha(Path(repo) / config["selection"]) == config["selection_sha256"],
                "Versioned Dense model selection changed")
        return selection, {"dense": (None, {"configuration": selection["configuration"]})}
    manifests = {}
    for channel, key in (("dense", "dense"), ("bm25f", "lexical")):
        item = selection[key]
        path = Path(repo) / item["manifest"]
        binding = FileBinding()
        binding.bind(path, item["manifest_sha256"])
        manifest = read(path)
        require(manifest["status"] == "complete" and manifest["input_bundle_id"] == selection["source"]["input_bundle_id"],
                "Selected index input/status differs")
        require(manifest["counts"] == {"documents": selection["source"]["shared_chunk_count"],
                                      "micro": selection["source"]["micro_route_count"],
                                      "macro": selection["source"]["macro_route_count"]}, "Selected index count differs")
        expected = {g: representation_id(manifest["input_bundle_id"], g, channel, manifest["configuration"])
                    for g in ("micro", "macro")}
        require(expected == manifest["representations"], "Representation binding differs")
        manifests[channel] = (path, manifest)
    require(Path(manifests["dense"][1]["input_manifest"]).resolve()
            == Path(manifests["bm25f"][1]["input_manifest"]).resolve(), "Selected input paths differ")
    return selection, manifests


def model_spec(selection, manifest):
    pinned = selection["dense"]
    require(all(pinned[k] == manifest["configuration"][k] for k in ("model", "revision")), "Selected model differs")
    require(pinned["dimension"] == manifest["configuration"]["dimensions"], "Selected dimension differs")
    return {**manifest["configuration"], "path": pinned["weights_dir"], "weight_file": "pytorch_model.bin",
            "key": pinned["key"]}


class DenseSession:
    def __init__(self, manifest_path):
        import numpy as np
        from ...indexing.benchmark_dense import validate_vectors
        self.root = Path(manifest_path).parent
        self.binding = FileBinding()
        self.binding.bind(manifest_path)
        self.manifest = read(manifest_path)
        manifest = self.manifest
        require(manifest["status"] == "complete" and manifest["space_id"] == digest(manifest["configuration"]),
                "Incomplete or unbound Dense space")
        count, memberships = 0, {"micro": 0, "macro": 0}
        for summary in manifest["shards"]:
            folder = inside(self.root, summary["folder"])
            self.binding.bind(folder / "complete.json", summary["receipt_sha256"])
            receipt = verify_receipt(folder, manifest["binding"])
            for name, checksum in receipt["files"].items():
                self.binding.bind(inside(folder, name), checksum)
            vectors = np.load(folder / "vectors.npy", mmap_mode="r", allow_pickle=False)
            rows = list(lines(folder / "rows.jsonl"))
            validate_vectors(vectors, len(rows), manifest["configuration"]["dimensions"])
            for row in rows:
                require(row["input_row"] == count, "Dense row order mismatch")
                require(bool(row["granularities"]) and len(set(row["granularities"])) == len(row["granularities"]),
                        "Invalid Dense membership")
                for g in row["granularities"]:
                    require(g in memberships, "Unknown Dense route")
                    memberships[g] += 1
                count += 1
            del vectors
        require({"documents": count, **memberships} == manifest["counts"], "Dense coverage mismatch")
        self.binding.check()

    def search(self, vector, *, space_id, top_k):
        import heapq
        import numpy as np
        from ...indexing.benchmark_dense import validate_vectors
        require(type(top_k) is int and top_k > 0 and space_id == self.manifest["space_id"], "Foreign Dense query")
        validate_vectors(np.asarray(vector, dtype=np.float32).reshape(1, -1), 1,
                         self.manifest["configuration"]["dimensions"])
        self.binding.check()
        heaps = {g: [] for g in ("micro", "macro")}
        for summary in self.manifest["shards"]:
            folder = inside(self.root, summary["folder"])
            vectors = np.load(folder / "vectors.npy", mmap_mode="r", allow_pickle=False)
            for row, score in zip(lines(folder / "rows.jsonl"), vectors @ vector):
                item = (float(score), -row["input_row"], row)
                for g in row["granularities"]:
                    heap = heaps[g]
                    if len(heap) < top_k:
                        heapq.heappush(heap, item)
                    elif item[:2] > heap[0][:2]:
                        heapq.heapreplace(heap, item)
            del vectors
        self.binding.check()
        return {g: [{"score": score, **row} for score, _, row in sorted(heap, key=lambda x: (-x[0], -x[1]))]
                for g, heap in heaps.items()}

    def close(self):
        pass  # Per-shard mmap lifetimes end in search; no model is owned here.

    def verify_reference(self, vector, top_k, result):
        from ...indexing.benchmark_dense import search
        expected = {g: search(self.root, vector, space_id=self.manifest["space_id"], granularity=g, top_k=top_k)
                    for g in ("micro", "macro")}
        require(expected == result, "Prepared Dense search differs from original exact search")


class LexicalSession:
    def __init__(self, manifest_path, scratch):
        from ...indexing.benchmark_lexical import configuration
        from ...indexing.schema import LEXICAL_FIELDS
        self.root = Path(manifest_path).parent
        self.binding = FileBinding()
        self.binding.bind(manifest_path)
        self.manifest = read(manifest_path)
        require(self.manifest["status"] == "complete", "Incomplete lexical index")
        # Canonical JSON sorts object keys; analyzer names preserve the original
        # sequence explicitly. Restore it instead of changing the index contract.
        original = self.manifest["configuration"]
        execution = {**original,
                     "field_weights": {k: original["field_weights"][k] for k in LEXICAL_FIELDS},
                     "field_b": {k: original["field_b"][k] for k in LEXICAL_FIELDS},
                     "analyzer_weights": {k: original["analyzer_weights"][k] for k in original["analyzers"]["names"]}}
        self.effective, self.segment = configuration(execution, Path(scratch))
        require(self.effective == self.manifest["configuration"], "Lexical runtime/configuration differs")
        for name, checksum in self.manifest["files"].items():
            self.binding.bind(inside(self.root, name), checksum)
        self.lengths = {field + "__" + name + "_B": self.effective["field_b"][field]
                   for field in self.effective["field_weights"] for name in self.effective["analyzer_weights"]}

    def search(self, text, *, top_k):
        import gc
        from whoosh import index, query, scoring
        from ...indexing.analyzers import analyze_fields
        require(type(top_k) is int and top_k > 0, "Invalid lexical depth")
        self.binding.check()
        analyzed = analyze_fields({field: [text] for field in self.effective["field_weights"]},
                                  self.effective["analyzers"], segmenter=self.segment)
        clauses = [query.Term(field + "__" + name, term,
                             boost=self.effective["field_weights"][field] * self.effective["analyzer_weights"][name])
                   for field, channels in analyzed.items() for name, tokens in channels.items()
                   for term in sorted(set(tokens))]
        result = {g: [] for g in ("micro", "macro")}
        if clauses:
            # Real-source preflight found two resident readers consumed ~3.5GiB.
            # Keep analyzer/hash preparation, but open only one route reader at a time.
            for g in result:
                ix = index.open_dir(self.root / g)
                try:
                    with ix.searcher(weighting=scoring.BM25F(K1=self.effective["k1"], **self.lengths)) as searcher:
                        result[g] = [{"score": hit.score, **hit["payload"]}
                                     for hit in searcher.search(query.Or(clauses), limit=top_k)]
                finally:
                    ix.close()
                del searcher, ix
                gc.collect()
        self.binding.check()
        return result

    def close(self):
        pass  # Each route reader is closed before the next route is opened.

    def verify_reference(self, text, top_k, result):
        from unittest.mock import patch
        from ...indexing import benchmark_lexical
        # Original search is unchanged; redirect only its cache-creating analyzer
        # preparation to this already verified session, never into the index root.
        with patch.object(benchmark_lexical, "configuration", return_value=(self.effective, self.segment)):
            expected = {g: benchmark_lexical.search(self.root, text, granularity=g, top_k=top_k,
                                                   config={"bm25f": self.effective})
                        for g in ("micro", "macro")}
        require(expected == result, "Prepared BM25F search differs from original search")
