"""Content-addressed Dense/Sparse blocks and exact active-row search."""

from collections import defaultdict
import os
from pathlib import Path
import tempfile

from ..collector.run_store import create_json
from ..evidence_io import digest, inside, read, require, sha
from .benchmark_dense import validate_vectors
from .schema import GRANULARITIES
from .sparse_trial import csr_weights


VERSION = "gc-index-snapshot-vectors-1"
BLOCK_ROWS = 256


def active_keys(source):
    active = defaultdict(list)
    for row in source.rows:
        active[row["dense_text_sha256"]].append(row["input_row"])
    return active


def identity(source, contract):
    return {"input_bundle_id": source.manifest["input_bundle_id"], "counts": source.manifest["counts"],
            "contract": contract, "block_rows": BLOCK_ROWS}


def representations(manifest, channel):
    return {route: digest([VERSION, manifest["input_bundle_id"], route, channel, manifest["contract"][channel]])
            for route in GRANULARITIES}


class VectorIndex:
    def __init__(self, manifest_path, source):
        self.manifest = read(manifest_path)
        require(self.manifest["version"] == VERSION and self.manifest["status"] == "complete"
                and all(self.manifest[k] == v for k, v in identity(source, self.manifest["contract"]).items()),
                "Foreign numerical index")
        self.active = active_keys(source)
        self.rows = source.rows
        self.contract = self.manifest["contract"]
        self.blocks = {}
        seen = {channel: set() for channel in self.contract}
        for ref in self.manifest["blocks"]:
            channel, path = ref["channel"], Path(ref["path"])
            require(path.is_absolute() and path.resolve() == path and path not in self.blocks
                    and channel in self.contract and sha(path) == ref["sha256"], "Numerical block binding differs")
            meta = read(path)
            keys = ref["active_keys"]
            require(keys and len(keys) == len(set(keys)) and set(keys) <= self.active.keys() and not seen[channel].intersection(keys),
                    "Duplicate or foreign index key")
            seen[channel].update(keys)
            stored = meta["keys"]
            require(len(stored) == len(set(stored)) <= BLOCK_ROWS and set(keys) <= set(stored)
                    and meta["contract_id"] == digest(self.contract[channel]) and meta["channel"] == channel,
                    "Numerical block key binding differs")
            expected = {"values.npy"} if channel == "dense" else {"data.npy", "indices.npy", "indptr.npy"}
            require(meta["files"].keys() == expected, "Unexpected numerical block files")
            for file, checksum in meta["files"].items():
                require(sha(inside(path.parent, file)) == checksum, "Numerical block bytes changed")
            self.blocks[path] = (ref, meta)
        require(all(keys == self.active.keys() for keys in seen.values()), "Incomplete numerical key coverage")

    def compatible_refs(self, contract, keys):
        for ref, _ in self.blocks.values():
            channel = ref["channel"]
            if channel in contract and contract[channel] == self.contract[channel]:
                active = sorted(set(ref["active_keys"]) & keys)
                if active:
                    yield {**ref, "active_keys": active}

    def matrix(self, path, meta):
        import numpy as np

        size = len(meta["keys"])
        if meta["channel"] == "dense":
            vectors = np.load(path.parent / "values.npy", mmap_mode="r", allow_pickle=False)
            validate_vectors(vectors, size, self.contract["dense"]["backbone"]["dimensions"])
            return vectors
        from scipy.sparse import csr_array

        data, indices, offsets = (np.load(path.parent / (name + ".npy"), mmap_mode="r", allow_pickle=False)
                                  for name in ("data", "indices", "indptr"))
        require(data.dtype == np.float32 and indices.dtype.kind == offsets.dtype.kind == "i"
                and len(offsets) == size + 1 and offsets[0] == 0 and offsets[-1] == len(data) == len(indices)
                and (offsets[1:] >= offsets[:-1]).all(), "Invalid Sparse block arrays")
        width = self.contract["sparse"]["vocabulary_size"]
        require(((indices >= 0) & (indices < width)).all() and np.isfinite(data).all() and (data > 0).all(),
                "Invalid Sparse block values")
        matrix = csr_array((data, indices, offsets), shape=(size, width))
        require(matrix.has_canonical_format, "Noncanonical Sparse block")
        return matrix

    def search(self, channel, vector, top_k):
        import heapq
        import numpy as np

        require(channel in self.contract and type(top_k) is int and top_k > 0, "Invalid numerical query")
        if channel == "dense":
            validate_vectors(np.asarray(vector)[None, :], 1, self.contract[channel]["backbone"]["dimensions"])
        else:
            vector = csr_weights([vector], self.contract[channel]["vocabulary_size"]).toarray()[0]
        hits = {route: [] for route in GRANULARITIES}
        for path, (ref, meta) in self.blocks.items():
            if ref["channel"] != channel:
                continue
            matrix = self.matrix(path, meta)
            # A row's dot product is independent of the block size and active-key count.
            scores = np.einsum("ij,j->i", matrix, vector, optimize=False) if channel == "dense" else matrix @ vector
            positions = {key: n for n, key in enumerate(meta["keys"])}
            for key in ref["active_keys"]:
                score = float(scores[positions[key]])
                if channel == "sparse" and score <= 0:
                    continue
                for number in self.active[key]:
                    for route in self.rows[number]["granularities"]:
                        heap = hits[route]
                        pair = (score, -number)
                        if len(heap) < top_k:
                            heapq.heappush(heap, pair)
                        elif pair > heap[0]:
                            heapq.heapreplace(heap, pair)
        return {route: [{"input_row": -number, "score": score} for score, number in sorted(heap, reverse=True)]
                for route, heap in hits.items()}


def _write_block(folder, names, values, channel, contract, keys):
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="block-", dir=folder.parent) as temporary:
        staged = Path(temporary) / "block"
        staged.mkdir()
        for name, value in zip(names, values, strict=True):
            with (staged / name).open("xb") as handle:
                np.save(handle, value, allow_pickle=False)
                handle.flush()
                os.fsync(handle.fileno())
        meta = {"channel": channel, "contract_id": digest(contract), "keys": keys,
                "files": {name: sha(staged / name) for name in names}}
        create_json(staged / "complete.json", meta)
        staged.rename(folder)


def build_vectors(source, output, encoder, *, previous=None, disk_guard=lambda: None):
    output = Path(output).resolve()
    require(not output.is_relative_to(source.path.parent) and not source.path.is_relative_to(output),
            "Use a separate numerical index root")
    if previous is not None:
        root = Path(previous[0]).resolve().parent
        require(not output.is_relative_to(root) and not root.is_relative_to(output), "Keep the previous index immutable")
    contract = encoder.contract
    require(contract.keys() in ({"dense"}, {"dense", "sparse"}), "Unsupported numerical index channels")
    active = active_keys(source)
    inherited = [] if previous is None else list(VectorIndex(*previous).compatible_refs(contract, active.keys()))
    prior = {channel: set() for channel in contract}
    for ref in inherited:
        prior[ref["channel"]].update(ref["active_keys"])
    plan = {**identity(source, contract), "input_manifest": str(source.path), "input_sha256": sha(source.path),
            "previous_blocks_sha256": digest(inherited)}
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "plan.json"
    if not plan_path.exists():
        create_json(plan_path, plan)
    require(read(plan_path) == plan, "Numerical resume plan differs")
    target = output / "manifest.json"
    if target.exists():
        return VectorIndex(target, source).manifest
    for channel in contract:
        (output / channel).mkdir(exist_ok=True)
    needed = {key: tuple(channel for channel in contract if key not in prior[channel]) for key in active}
    pending = defaultdict(dict)
    refs = inherited.copy()
    completed = set()
    numbers = defaultdict(int)

    def flush(texts, channels):
        folders = {}
        keys = list(texts)
        for channel in channels:
            folders[channel] = output / channel / f"{numbers[channel]:06d}"
            numbers[channel] += 1
        missing = tuple(channel for channel, path in folders.items() if not path.exists())
        if missing:
            disk_guard()
            values = encoder.encode(list(texts.values()), missing)
            for channel in missing:
                if channel == "dense":
                    validate_vectors(values[channel], len(keys), contract[channel]["backbone"]["dimensions"])
                    _write_block(folders[channel], ("values.npy",), (values[channel],), channel, contract[channel], keys)
                else:
                    require(len(values[channel]) == len(keys), "Missing Sparse vectors")
                    matrix = csr_weights(values[channel], contract[channel]["vocabulary_size"])
                    _write_block(folders[channel], ("data.npy", "indices.npy", "indptr.npy"),
                                 (matrix.data, matrix.indices, matrix.indptr), channel, contract[channel], keys)
        for channel, folder in folders.items():
            path = folder / "complete.json"
            meta = read(path)
            require(meta["keys"] == keys and meta["channel"] == channel and meta["contract_id"] == digest(contract[channel]),
                    "Sealed numerical block differs from resumed inputs")
            refs.append({"channel": channel, "path": str(path), "sha256": sha(path), "active_keys": keys})
        texts.clear()

    if any(needed.values()):
        changed = {source.rows[active[key][0]]["source_item"]["content_id"] for key, channels in needed.items() if channels}
        for document in source.iter_documents(changed):
            key = document["row"]["dense_text_sha256"]
            channels = needed[key]
            if not channels or key in completed:
                continue
            group = pending[channels]
            group[key] = document["projected"]["dense_text"]
            completed.add(key)
            if len(group) == BLOCK_ROWS:
                flush(group, channels)
        for channels, group in pending.items():
            if group:
                flush(group, channels)
    require(all(not channels or key in completed for key, channels in needed.items()), "Missing numerical inputs")
    require(sha(source.path) == plan["input_sha256"], "Numerical input manifest changed")
    value = {"version": VERSION, "status": "complete", **plan, "blocks": refs, "unique_texts": len(active),
             "reused_keys": {channel: len(keys) for channel, keys in prior.items()},
             "encoded_keys": {channel: len(active) - len(keys) for channel, keys in prior.items()}}
    # Validate references and payload metadata before publishing the manifest.
    with tempfile.TemporaryDirectory(prefix="validate-", dir=output) as temporary:
        draft = Path(temporary) / "manifest.json"
        create_json(draft, value)
        VectorIndex(draft, source)
    create_json(target, value)
    return value
