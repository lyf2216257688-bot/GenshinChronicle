"""Session-scoped, hash-bound random access to the existing input and sources."""
from array import array
from hashlib import sha256
from pathlib import Path

from ...chunking import read_natural_parent
from ...evidence_io import inside, read, require, sha, strict_json
from ...indexing.inputs import _read_locator, iter_inputs


class FileBinding:
    """Full hashes once per session; invalidate on file identity/stat changes."""

    def __init__(self):
        self.files = {}

    @staticmethod
    def fingerprint(path):
        stat = Path(path).stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def bind(self, path, expected=None):
        path = Path(path).resolve()
        before = self.fingerprint(path)
        checksum = sha(path)
        require(expected is None or checksum == expected, "Artifact checksum mismatch: " + str(path))
        require(self.fingerprint(path) == before, "Artifact changed during verification")
        self.files[path] = before
        return checksum

    def check(self):
        require(all(self.fingerprint(p) == expected for p, expected in self.files.items()),
                "Bound file changed; prepare a new retrieval session")


class InputReader:
    def __init__(self, manifest_path):
        self.path = Path(manifest_path).resolve()
        self.manifest = read(self.path)
        self.binding = FileBinding()
        self.binding.bind(self.path)
        self.chunk_manifest_path = Path(self.manifest["source_manifests"]["chunk"])
        self.clean_manifest_path = Path(self.manifest["source_manifests"]["clean"])
        self.binding.bind(self.chunk_manifest_path, self.manifest["binding"]["chunk_manifest_sha256"])
        self.binding.bind(self.clean_manifest_path, self.manifest["binding"]["clean_manifest_sha256"])
        chunk, clean = read(self.chunk_manifest_path), read(self.clean_manifest_path)
        require(chunk["status"] == clean["status"] == "complete", "Incomplete upstream source")
        require(chunk["schema_version"] == "gc-chunking-pilot-2" and clean["schema_version"] == "gc-clean-1",
                "Unsupported source schema")
        require(chunk["clean_manifest_sha256"] == self.manifest["binding"]["clean_manifest_sha256"],
                "Chunk/Clean binding differs")
        self.root, self.clean_root = self.chunk_manifest_path.parent, self.clean_manifest_path.parent.parent
        for name, checksum in self.manifest["binding"]["artifacts"].items():
            require(chunk["artifacts"][name] == checksum, "Source artifact binding differs")
            self.binding.bind(inside(self.root, name), checksum)
        self.records = {entry["observation_id"]: entry for entry in clean["records"]}
        require(len(self.records) == len(clean["records"]), "Duplicate Clean observation")
        self.source_item = {k: clean[k] for k in ("source", "locale")}
        for name, checksum in self.manifest["artifacts"].items():
            self.binding.bind(inside(self.path.parent, name), checksum)
        self.offsets, self.lengths, self.line_hashes = array("Q"), array("Q"), []
        self.trial_pools = {k: [] for k in ("shared", "micro", "macro", "dialogue", "condition")}
        # iter_inputs owns complete route/schema/count validation. A parallel byte
        # cursor records positions without retaining the 1.2GB JSON projection.
        with (self.path.parent / "inputs.jsonl").open("rb") as raw:
            for number, row in enumerate(iter_inputs(self.path)):
                self._validate_row(row)
                self.offsets.append(raw.tell())
                body = raw.readline()
                self.lengths.append(len(body))
                self.line_hashes.append(sha256(body).digest())
                require(strict_json(body.decode("utf-8")) == row, "Input cursors differ")
                categories = ["shared"] if len(row["granularities"]) == 2 else list(row["granularities"])
                categories += (["dialogue"] if row["graph_locator"] else [])
                categories += (["condition"] if any(c["sources"] for c in row["conditions"]) else [])
                for key in categories:
                    if len(self.trial_pools[key]) < 8:
                        self.trial_pools[key].append(number)
        self.binding.check()
        self.handle = (self.path.parent / "inputs.jsonl").open("rb")

    @property
    def bundle_id(self):
        return self.manifest["input_bundle_id"]

    def _validate_row(self, row):
        entry = self.records[row["observation_id"]]
        require(entry["status"] in {"complete", "preserved_unknown"}, "Unavailable Clean observation")
        require(row["source_item"] == {**self.source_item, "content_id": entry["content_id"]},
                "Input source identity mismatch")
        require(row["clean_record"] == {k: entry[k] for k in ("record_path", "record_sha256")},
                "Input Clean locator mismatch")
        require(row["chunk_locator"]["file"] == "chunks-balanced.jsonl"
                and row["parent_locator"]["file"] == "parents.jsonl"
                and (row["graph_locator"] is None or row["graph_locator"]["file"] == "graphs.jsonl"),
                "Unexpected source locator")

    def row(self, number):
        require(type(number) is int and 0 <= number < len(self.offsets), "Invalid input row")
        self.binding.check()
        self.handle.seek(self.offsets[number])
        body = self.handle.read(self.lengths[number])
        require(sha256(body).digest() == self.line_hashes[number], "Input row changed")
        row = strict_json(body.decode("utf-8"))
        self._validate_row(row)
        return row

    def read_chunk(self, row):
        self.binding.check()
        self._validate_row(row)
        chunk = _read_locator(self.root, row["chunk_locator"])
        for key in ("chunk_id", "parent_id", "observation_id", "granularities", "display_order_by_granularity",
                    "clean_refs", "alias_clean_refs", "context_sources"):
            require(chunk[key] == row[key], "Chunk/input mismatch: " + key)
        require(chunk["content_id"] == row["source_item"]["content_id"]
                and chunk["context"] == row["source_context"] and chunk["retrieval_text"] == row["dense_text"],
                "Chunk source/text mismatch")
        require(all(chunk[k] == v for k, v in row["structure"].items()), "Chunk structure mismatch")
        return chunk

    def document(self, number, chunk_id=None):
        row = self.row(number)
        require(chunk_id is None or row["chunk_id"] == chunk_id, "Hit Chunk identity mismatch")
        return {"input_row": number, "row": row, "chunk": self.read_chunk(row)}

    def read_parent(self, row):
        self.binding.check()
        self._validate_row(row)
        parent = _read_locator(self.root, row["parent_locator"])
        require(all(parent[k] == row[k] for k in ("parent_id", "observation_id"))
                and parent["content_id"] == row["source_item"]["content_id"], "Parent identity mismatch")
        return parent

    def read_graph(self, row):
        self.binding.check()
        self._validate_row(row)
        if row["graph_locator"] is None:
            return None
        graph = _read_locator(self.root, row["graph_locator"])
        require(all(graph[k] == row[k] for k in ("parent_id", "observation_id")), "Graph identity mismatch")
        return graph

    def read_clean(self, row):
        self.binding.check()
        self._validate_row(row)
        path = inside(self.clean_root, row["clean_record"]["record_path"])
        require(sha(path) == row["clean_record"]["record_sha256"], "Clean record changed")
        clean = read(path)
        require(clean["observation_id"] == row["observation_id"] and clean["source_item"] == row["source_item"],
                "Clean identity mismatch")
        return clean

    def read_natural_parent(self, row):
        return read_natural_parent(self.read_parent(row), self.read_clean(row))

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
