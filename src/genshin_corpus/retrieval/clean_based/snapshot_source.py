"""Read-back adapter for versioned candidates and their two Raw observations."""

from pathlib import Path

from ...chunking import read_natural_parent
from ...clean.reader import validate_record
from ...evidence_io import inside, read, require
from ...indexing.snapshot_inputs import ROWS
from .source import FileBinding


class SnapshotReader:
    def __init__(self, source):
        self.source = source
        self.bundle_id = source.manifest["input_bundle_id"]
        self.binding = FileBinding()
        self.raw_hashes = {}
        self.binding.bind(source.path)
        self.binding.bind(source.path.parent / ROWS, source.manifest["rows_sha256"])
        for name in ("clean", "chunk"):
            ref = source.manifest[name]
            self.binding.bind(ref["path"], ref["sha256"])
        raw_refs = {source.current_raw["run_id"]: source.current_raw}
        for entry in source.clean.values():
            ref = entry["effective_raw"]
            require(raw_refs.setdefault(ref["run_id"], ref) == ref, "Raw run reference changed")
        for ref in raw_refs.values():
            self.binding.bind(Path(ref["root"]) / "metadata/manifest.json", ref["manifest_sha256"])

    def document(self, number, chunk_id=None):
        self.binding.check()
        doc = self.source.document(number)
        mapping, projected = doc["row"], doc["projected"]
        require(chunk_id is None or mapping["chunk_id"] == chunk_id, "Hit Chunk identity mismatch")
        cid = mapping["source_item"]["content_id"]
        ranges = self.source.chunk["records"][cid]["ranges"]
        row = {**projected, **mapping, "input_snapshot_id": self.bundle_id,
               "clean_record": {k: doc["clean_record"][k] for k in ("sha256", "clean_observation_id")}}
        for kind, filename, identity in (("chunk", "chunks-balanced.jsonl", "chunk_id"),
                                         ("parent", "parents.jsonl", "parent_id"),
                                         ("graph", "graphs.jsonl", "parent_id")):
            row[kind + "_locator"] = None if doc[kind] is None else {
                "file": filename, "range_sha256": ranges[filename]["sha256"], "id": doc[kind][identity]}
        row["raw_observations"] = {kind: {**doc[kind + "_observation"], "archive": doc[kind + "_raw"]}
                                   for kind in ("current", "effective")}
        return {"input_row": number, "row": row, "chunk": doc["chunk"]}

    def _verified(self, row):
        require(self.document(row["input_row"], row["chunk_id"])["row"] == row, "Versioned source binding changed")
        return self.source.document(row["input_row"])

    def read_clean(self, row):
        # A fresh range and exact record are verified before each evidence read.
        self.source._cached_source.cache_clear()
        doc = self._verified(row)
        actual = doc["clean_record"]
        self.binding.bind(actual["path"], actual["sha256"])
        record = read(actual["path"])
        require(record["source_item"] == row["source_item"]
                and record["observation_id"] == actual["clean_observation_id"] == row["observation_id"]
                and record["input"]["raw_run_id"] == doc["effective_raw"]["run_id"], "Foreign versioned Clean record")
        validate_record(record, Path(doc["effective_raw"]["root"]))
        current, effective = (row["raw_observations"][kind] for kind in ("current", "effective"))
        require(current["consumed_source_sha256"] == effective["consumed_source_sha256"],
                "Evidence does not match the current observation")
        for observation in (current, effective):
            root = Path(observation["archive"]["root"])
            locators = observation["locators"]
            for locator in [locators["detail"], *locators["memberships"], *locators["listings"], *locators["channels"].values()]:
                path = inside(root, locator["path"])
                if path not in self.raw_hashes:
                    self.binding.bind(path, locator["sha256"])
                    self.raw_hashes[path] = locator["sha256"]
                require(self.raw_hashes[path] == locator["sha256"], "Conflicting Raw locator digests")
        self.binding.check()
        return record

    def read_parent(self, row):
        return self._verified(row)["parent"]

    def read_graph(self, row):
        return self._verified(row)["graph"]

    def read_natural_parent(self, row):
        return read_natural_parent(self.read_parent(row), self.read_clean(row))
