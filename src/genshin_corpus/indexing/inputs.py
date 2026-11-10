"""Hash-bound shared Chunk input, with source readback and create-only exports."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from ..chunking import read_natural_parent
from ..evidence_io import digest, inside, read, require, sha, strict_json, write
from .schema import FIELD_VERSION, GRANULARITIES, INPUT_VERSION, project_chunk


def _lines(path):
    with Path(path).open("rb") as handle:
        while True:
            offset = handle.tell()
            body = handle.readline()
            if not body:
                break
            yield strict_json(body.decode("utf-8")), {
                "file": Path(path).name, "offset": offset, "length": len(body),
                "sha256": sha256(body).hexdigest(),
            }, body


def _identity(value):
    return tuple(value[key] for key in ("observation_id", "content_id", "section_id"))


def _read_locator(root, locator, *, handle=None):
    require(type(locator["offset"]) is int and locator["offset"] >= 0
            and type(locator["length"]) is int and locator["length"] > 0, "Invalid JSONL locator")
    if handle is None:
        with inside(root, locator["file"]).open("rb") as opened:
            return _read_locator(root, locator, handle=opened)
    handle.seek(locator["offset"])
    body = handle.read(locator["length"])
    require(sha256(body).hexdigest() == locator["sha256"], "JSONL readback checksum mismatch")
    return strict_json(body.decode("utf-8"))


class ChunkInputSource:
    """One source snapshot shared by both granularities and all representations.

    Only byte locators and identities are catalogued in memory. Source text,
    complete parents and dialogue graphs remain in their original artifacts.
    """

    def __init__(self, chunk_manifest, *, clean_manifest=None):
        self.manifest_path = Path(chunk_manifest).resolve()
        self.root = self.manifest_path.parent
        self.manifest_sha256 = sha(self.manifest_path)
        manifest = read(self.manifest_path)
        require(manifest["status"] == "complete" and manifest["schema_version"] == "gc-chunking-pilot-2",
                "Index input requires a complete V2 Chunk manifest")
        self.clean_manifest_path = Path(clean_manifest or manifest["clean_manifest"]).resolve()
        require(sha(self.clean_manifest_path) == manifest["clean_manifest_sha256"], "Clean manifest checksum mismatch")
        self.clean_root = self.clean_manifest_path.parent.parent
        clean = read(self.clean_manifest_path)
        require(clean["schema_version"] == "gc-clean-1" and clean["status"] == "complete",
                "Index input requires a complete Clean V1 manifest")
        self.source_item = {key: clean[key] for key in ("source", "locale")}
        self.records = {}
        for entry in clean["records"]:
            require(entry["status"] in {"complete", "preserved_unknown"}, "Unavailable Clean observation")
            require(entry["observation_id"] not in self.records, "Duplicate Clean observation")
            self.records[entry["observation_id"]] = entry
        selected = set()
        self.sources = {}
        for entry in manifest["sources"]:
            observation = entry["observation_id"]
            require(observation in self.records and observation not in self.sources, "Invalid Chunk observation")
            for key in ("content_id", "record_path", "record_sha256"):
                require(entry[key] == self.records[observation][key], "Chunk/Clean source binding mismatch")
            require(entry["content_id"] not in selected, "Duplicate selected content key")
            selected.add(entry["content_id"])
            self.sources[observation] = self.records[observation]
        require(selected == set(manifest["selection"]["content_ids"]), "Chunk selection/source mismatch")
        names = ("chunks-balanced.jsonl", "parents.jsonl", "graphs.jsonl", "report.json")
        self.artifacts = {name: manifest["artifacts"][name] for name in names}
        for name in ("chunks-balanced.jsonl", "report.json"):
            require(sha(inside(self.root, name)) == self.artifacts[name], "Chunk artifact checksum mismatch: " + name)
        self.report = read(self.root / "report.json")
        require(self.report["status"] == "complete" and self.report["policy_version"] == "gc-story-admission-pilot-4",
                "Index input requires completed V2.1 admission")
        require(self.report["record_count"] == len(self.sources), "Chunk source count mismatch")
        self.parents = self._catalog("parents.jsonl")
        self.graphs = self._catalog("graphs.jsonl")
        require(len(self.parents) == self.report["parents"] and len(self.graphs) == self.report["graphs"],
                "Parent/Graph count mismatch")
        for parent_id, entry in self.parents.items():
            require((entry["kind"] == "dialogue_group") == (parent_id in self.graphs), "Missing or unexpected dialogue graph")
        for parent_id, entry in self.graphs.items():
            require(parent_id in self.parents and entry["identity"] == self.parents[parent_id]["identity"],
                    "Graph/Parent identity mismatch")
        self.binding = {"schema_version": INPUT_VERSION, "field_version": FIELD_VERSION,
                        "chunk_manifest_sha256": self.manifest_sha256,
                        "clean_manifest_sha256": manifest["clean_manifest_sha256"], "artifacts": self.artifacts}
        self.projection_code_sha256 = {name: sha(Path(__file__).parent / name)
                                       for name in ("schema.py", "inputs.py")}
        self.input_snapshot_id = digest(self.binding)

    def _catalog(self, name):
        entries = {}
        hasher = sha256()
        for value, locator, body in _lines(self.root / name):
            hasher.update(body)
            observation = value["observation_id"]
            require(observation in self.sources and value["content_id"] == self.sources[observation]["content_id"],
                    "Unbound source in " + name)
            key = value["parent_id"]
            require(key not in entries, "Duplicate parent identity in " + name)
            entries[key] = {"locator": locator, "identity": _identity(value), "kind": value.get("kind")}
        require(hasher.hexdigest() == self.artifacts[name], "Chunk artifact checksum mismatch: " + name)
        return entries

    def verify_unchanged(self):
        require(sha(self.manifest_path) == self.manifest_sha256, "Chunk manifest changed")
        require(sha(self.clean_manifest_path) == self.binding["clean_manifest_sha256"], "Clean manifest changed")
        for name, expected in self.artifacts.items():
            require(sha(self.root / name) == expected, "Chunk source changed: " + name)
        for name, expected in self.projection_code_sha256.items():
            require(sha(Path(__file__).parent / name) == expected, "Index projection implementation changed")

    def iter_documents(self, *, content_ids=None):
        selected = None if content_ids is None else set(content_ids)
        if selected is not None:
            require(bool(selected) and selected <= {entry["content_id"] for entry in self.sources.values()},
                    "Unknown or empty content selection")
        seen = {}
        overlap_targets = {}
        orders = {}
        hasher = sha256()
        last_parent_id = None
        with (self.root / "parents.jsonl").open("rb") as parent_handle:
            for chunk, locator, body in _lines(self.root / "chunks-balanced.jsonl"):
                hasher.update(body)
                chunk_id, parent_id = chunk["chunk_id"], chunk["parent_id"]
                require(chunk_id not in seen, "Duplicate Chunk identity")
                seen[chunk_id] = parent_id
                for target in chunk["overlapping_chunk_ids"]:
                    require(target != chunk_id, "Self-overlapping Chunk identity")
                    require(overlap_targets.setdefault(target, parent_id) == parent_id, "Cross-parent overlap")
                require(parent_id in self.parents and _identity(chunk) == self.parents[parent_id]["identity"],
                        "Chunk/Parent source mismatch")
                if last_parent_id != parent_id:
                    parent = _read_locator(self.root, self.parents[parent_id]["locator"], handle=parent_handle)
                    last_parent_id = parent_id
                row = project_chunk(chunk, parent)
                for level, order in row["display_order_by_granularity"].items():
                    key = (parent_id, level)
                    observed = orders.setdefault(key, set())
                    require(order not in observed, "Duplicate per-parent display order")
                    observed.add(order)
                if selected is not None and chunk["content_id"] not in selected:
                    continue
                observation = row["observation_id"]
                record = self.sources[observation]
                yield {**row, "input_snapshot_id": self.input_snapshot_id,
                       "source_item": {**self.source_item, "content_id": record["content_id"]},
                       "clean_record": {key: record[key] for key in ("record_path", "record_sha256")},
                       "chunk_locator": locator, "parent_locator": dict(self.parents[parent_id]["locator"]),
                       "graph_locator": dict(self.graphs[parent_id]["locator"]) if parent_id in self.graphs else None}
        require(hasher.hexdigest() == self.artifacts["chunks-balanced.jsonl"], "Chunk source changed during read")
        require(len(seen) == self.report["profiles"]["balanced"]["chunks"], "Chunk count mismatch")
        require(all(seen.get(target) == parent_id for target, parent_id in overlap_targets.items()),
                "Missing or cross-parent overlap target")
        require(all(values == set(range(len(values))) for values in orders.values()),
                "Non-contiguous per-parent display order")

    def _check_row(self, row):
        require(row["schema_version"] == INPUT_VERSION and row["input_snapshot_id"] == self.input_snapshot_id,
                "Input row belongs to another snapshot")
        record = self.sources[row["observation_id"]]
        require(row["source_item"] == {**self.source_item, "content_id": record["content_id"]},
                "Input row source identity mismatch")
        require(self.parents[row["parent_id"]]["identity"] ==
                (row["observation_id"], record["content_id"], row["structure"]["section_id"]),
                "Input row Parent identity mismatch")

    def read_chunk(self, row):
        self._check_row(row)
        require(row["chunk_locator"]["file"] == "chunks-balanced.jsonl", "Unexpected Chunk locator")
        value = _read_locator(self.root, row["chunk_locator"])
        require(value["chunk_id"] == row["chunk_id"] and value["parent_id"] == row["parent_id"]
                and value["observation_id"] == row["observation_id"], "Chunk readback identity mismatch")
        return value

    def read_parent(self, row):
        self._check_row(row)
        require(row["parent_locator"] == self.parents[row["parent_id"]]["locator"], "Parent locator mismatch")
        return _read_locator(self.root, row["parent_locator"])

    def read_graph(self, row):
        self._check_row(row)
        expected = self.graphs.get(row["parent_id"])
        require(row["graph_locator"] == (expected["locator"] if expected else None), "Graph locator mismatch")
        return _read_locator(self.root, row["graph_locator"]) if expected else None

    def read_clean(self, row):
        self._check_row(row)
        entry = self.sources[row["observation_id"]]
        require(row["clean_record"] == {key: entry[key] for key in ("record_path", "record_sha256")},
                "Clean record locator mismatch")
        path = inside(self.clean_root, entry["record_path"])
        require(sha(path) == entry["record_sha256"], "Clean record checksum mismatch")
        value = read(path)
        require(value["observation_id"] == row["observation_id"] and value["source_item"] == row["source_item"],
                "Clean readback identity mismatch")
        return value

    def read_natural_parent(self, row):
        return read_natural_parent(self.read_parent(row), self.read_clean(row))


def _write_line(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def prepare_inputs(source, output, *, content_ids=None, progress=None):
    """Export a shared table and text-free route maps. Publish the manifest last."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    counts = Counter({"documents": 0, "micro": 0, "macro": 0})
    selected = sorted(set(content_ids)) if content_ids is not None else None
    with (output / "inputs.jsonl").open("x", encoding="utf-8", newline="\n") as rows, \
         (output / "micro.jsonl").open("x", encoding="utf-8", newline="\n") as micro, \
         (output / "macro.jsonl").open("x", encoding="utf-8", newline="\n") as macro:
        routes = {"micro": micro, "macro": macro}
        for number, row in enumerate(source.iter_documents(content_ids=selected)):
            _write_line(rows, row)
            counts["documents"] += 1
            for level in row["granularities"]:
                _write_line(routes[level], {"row_number": number, "chunk_id": row["chunk_id"],
                                           "display_order": row["display_order_by_granularity"][level]})
                counts[level] += 1
            if progress is not None and counts["documents"] % 1024 == 0:
                progress(dict(counts))
    source.verify_unchanged()
    if progress is not None:
        progress(dict(counts))
    manifest = {"schema_version": INPUT_VERSION, "field_version": FIELD_VERSION, "status": "complete",
                "input_snapshot_id": source.input_snapshot_id, "binding": source.binding,
                "projection_code_sha256": dict(source.projection_code_sha256),
                "selection": selected, "counts": dict(counts),
                "source_manifests": {"chunk": str(source.manifest_path), "clean": str(source.clean_manifest_path)},
                "artifacts": {name: sha(output / name) for name in ("inputs.jsonl", "micro.jsonl", "macro.jsonl")}}
    manifest["input_bundle_id"] = digest({key: manifest[key] for key in
                                         ("input_snapshot_id", "selection", "counts", "artifacts")})
    write(output / "manifest.json", manifest)
    return manifest


def iter_inputs(manifest_path, *, granularity=None):
    """Read only completed, byte-verified shared input bundles."""
    require(granularity is None or granularity in GRANULARITIES, "Unsupported input route")
    manifest_path = Path(manifest_path)
    manifest = read(manifest_path)
    require(manifest["status"] == "complete" and manifest["schema_version"] == INPUT_VERSION
            and manifest["field_version"] == FIELD_VERSION, "Incomplete or unsupported index input")
    require(digest(manifest["binding"]) == manifest["input_snapshot_id"], "Input snapshot binding mismatch")
    require(manifest["input_bundle_id"] == digest({key: manifest[key] for key in
            ("input_snapshot_id", "selection", "counts", "artifacts")}), "Input bundle binding mismatch")
    require(set(manifest["artifacts"]) == {"inputs.jsonl", "micro.jsonl", "macro.jsonl"}, "Unexpected input artifact set")
    for name, expected in manifest["artifacts"].items():
        require(sha(inside(manifest_path.parent, name)) == expected, "Input bundle checksum mismatch")
    counts = Counter({"documents": 0, "micro": 0, "macro": 0})
    seen = set()
    with (manifest_path.parent / "micro.jsonl").open(encoding="utf-8") as micro, \
         (manifest_path.parent / "macro.jsonl").open(encoding="utf-8") as macro:
        routes = {"micro": micro, "macro": macro}
        for number, (row, _, _) in enumerate(_lines(manifest_path.parent / "inputs.jsonl")):
            require(row["schema_version"] == INPUT_VERSION and row["field_version"] == FIELD_VERSION
                    and row["input_snapshot_id"] == manifest["input_snapshot_id"], "Foreign input row")
            require(row["chunk_id"] not in seen, "Duplicate input identity")
            seen.add(row["chunk_id"])
            require(sha256(row["dense_text"].encode("utf-8")).hexdigest() == row["dense_text_sha256"],
                    "Dense text checksum mismatch")
            counts["documents"] += 1
            for level in row["granularities"]:
                require(level in GRANULARITIES, "Unsupported input granularity")
                expected = {"row_number": number, "chunk_id": row["chunk_id"],
                            "display_order": row["display_order_by_granularity"][level]}
                line = routes[level].readline()
                require(bool(line) and strict_json(line) == expected, "Route mapping mismatch")
                counts[level] += 1
            if granularity is None or granularity in row["granularities"]:
                yield row
        require(not micro.read() and not macro.read(), "Unexpected trailing route rows")
    require(dict(counts) == manifest["counts"], "Input bundle count mismatch")
