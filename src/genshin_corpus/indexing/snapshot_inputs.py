"""Lightweight input mappings and lazy source projection for Chunk snapshots."""

import argparse
from collections import Counter
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path

from ..clean.snapshot_build import VERSION as CLEAN_VERSION
from ..chunking.snapshot_build import VERSION as CHUNK_VERSION, read_source_rows
from ..collector.run_store import create_json
from ..evidence_io import digest, read, require, sha
from .schema import FIELD_VERSION, GRANULARITIES, project_chunk


VERSION = "gc-index-snapshot-input-1"
ROWS = "rows.jsonl"


def _each_source(entry):
    parents = read_source_rows(entry, "parents.jsonl")
    graphs = read_source_rows(entry, "graphs.jsonl")
    chunks = read_source_rows(entry, "chunks-balanced.jsonl")
    pmap = {parent["parent_id"]: parent for parent in parents}
    gmap = {graph["parent_id"]: graph for graph in graphs}
    cmap = {chunk["chunk_id"]: chunk for chunk in chunks}
    require(len(pmap) == len(parents) and len(gmap) == len(graphs) and len(cmap) == len(chunks), "Duplicate source identity in Chunk snapshot")
    require(set(gmap) == {pid for pid, p in pmap.items() if p["kind"] == "dialogue_group"}, "Graph and dialogue parents differ")
    for pid, graph in gmap.items():
        require(graph["section_id"] == pmap[pid]["section_id"], "Graph/Parent section differs")
    orders = {}
    for chunk in chunks:
        parent_id = chunk["parent_id"]
        projected = project_chunk(chunk, pmap[parent_id])
        for overlap in chunk["overlapping_chunk_ids"]:
            require(overlap != chunk["chunk_id"] and cmap[overlap]["parent_id"] == parent_id, "Invalid Chunk overlap")
        for level, order in projected["display_order_by_granularity"].items():
            position = orders.setdefault((parent_id, level), set())
            require(order not in position, "Duplicate per-route display order")
            position.add(order)
        yield projected, chunk, pmap[parent_id], gmap.get(parent_id)
    require(all(positions == set(range(len(positions))) for positions in orders.values()), "Noncontiguous display order")


def map_row(number, item, projected):
    return {"input_row": number, "source_item": item, **{key: projected[key] for key in (
        "chunk_id", "observation_id", "parent_id", "granularities", "dense_text_sha256")},
        "lexical_fields_sha256": digest(projected["lexical_fields"])}


def _load_bound(path, expected):
    require(sha(path) == expected, "Snapshot input source hash differs")
    return read(path)


def _projection_binding():
    folder = Path(__file__).parent
    return {"field_version": FIELD_VERSION, "schema_sha256": sha(folder / "schema.py"), "adapter_sha256": sha(__file__)}


def build_inputs(chunk_path, output):
    chunk_path, output = Path(chunk_path).resolve(), Path(output).resolve()
    require(not output.is_relative_to(chunk_path.parent) and not chunk_path.is_relative_to(output), "Use a separate index input output root")
    chunk_sha = sha(chunk_path)
    chunk = _load_bound(chunk_path, chunk_sha)
    require(chunk["version"] == CHUNK_VERSION and chunk["status"] == "complete", "Complete Chunk snapshot required")
    bound_clean = dict(chunk["clean_snapshot"])
    clean = _load_bound(bound_clean["path"], bound_clean["sha256"])
    require(clean["schema_version"] == CLEAN_VERSION and clean["status"] == "complete", "Complete Clean snapshot required")
    refs = {entry["source_item"]["content_id"]: entry for entry in clean["records"]}
    require(len(refs) == len(clean["records"]) and refs.keys() == chunk["records"].keys(), "Clean and Chunk source coverage differs")
    projection = _projection_binding()
    output.mkdir(parents=True, exist_ok=False)
    counts = Counter({key: 0 for key in ("documents", *GRANULARITIES)})
    seen = set()
    with (output / ROWS).open("xb") as handle:
        for cid, entry in sorted(chunk["records"].items()):
            before = refs[cid]
            require(entry["source_item"] == before["source_item"]
                    and entry["clean_sha256"] == before["record"]["sha256"]
                    and entry["clean_observation_id"] == before["record"]["clean_observation_id"], "Chunk/Clean source binding differs")
            for projected, _, _, _ in _each_source(entry):
                chunk_id = projected["chunk_id"]
                require(chunk_id not in seen, "Duplicate Chunk identity")
                seen.add(chunk_id)
                row = map_row(counts["documents"], entry["source_item"], projected)
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n")
                counts["documents"] += 1
                counts.update(row["granularities"])
        handle.flush()
        os.fsync(handle.fileno())
    require(sha(chunk_path) == chunk_sha and sha(bound_clean["path"]) == bound_clean["sha256"]
            and _projection_binding() == projection, "Index snapshot source changed during export")
    rows_hash = sha(output / ROWS)
    value = {"version": VERSION, "status": "complete", "sources": len(refs), "counts": dict(counts),
             "chunk": {"path": str(chunk_path), "sha256": chunk_sha, "logical_id": chunk["logical_id"]},
             "clean": bound_clean, "rows_sha256": rows_hash, "projection": projection,
             "input_bundle_id": digest([VERSION, projection, chunk["logical_id"], rows_hash])}
    create_json(output / "manifest.json", value)
    return value


class SnapshotInput:
    def __init__(self, manifest):
        self.path = Path(manifest).resolve()
        self.manifest = read(self.path)
        require(self.manifest["version"] == VERSION and self.manifest["status"] == "complete"
                and self.manifest["projection"] == _projection_binding(), "Unsupported versioned index input")
        binding = self.manifest["chunk"]
        self.chunk = _load_bound(binding["path"], binding["sha256"])
        require(self.chunk["logical_id"] == binding["logical_id"] and self.chunk["clean_snapshot"] == self.manifest["clean"],
                "Foreign Chunk snapshot in index input")
        binding = self.manifest["clean"]
        clean = _load_bound(binding["path"], binding["sha256"])
        require(clean["schema_version"] == CLEAN_VERSION and clean["status"] == "complete", "Complete Clean snapshot required")
        self.current_raw = clean["current_raw"]
        self.clean = {entry["source_item"]["content_id"]: entry for entry in clean["records"]}
        require(len(self.clean) == self.manifest["sources"] and self.clean.keys() == self.chunk["records"].keys(), "Foreign Clean snapshot")
        self.rows = self._read_rows()
        self.source_rows = {}
        for row in self.rows:
            cid = row["source_item"]["content_id"]
            self.source_rows.setdefault(cid, []).append(row["input_row"])
        require(list(self.source_rows) == sorted(self.source_rows), "Noncanonical input source order")
        self._cached_source = lru_cache(maxsize=4)(self._source_documents)

    def _read_rows(self):
        rows, seen, hasher = [], set(), sha256()
        counts = Counter({key: 0 for key in ("documents", *GRANULARITIES)})
        with (self.path.parent / ROWS).open("rb") as handle:
            for number, line in enumerate(handle):
                hasher.update(line)
                row = json.loads(line)
                cid = row["source_item"]["content_id"]
                expected = self.chunk["records"][cid]
                levels = row["granularities"]
                require(row["input_row"] == number and row["chunk_id"] not in seen and bool(levels)
                        and len(set(levels)) == len(levels) and set(levels) <= set(GRANULARITIES)
                        and row["source_item"] == expected["source_item"]
                        and row["observation_id"] == expected["clean_observation_id"], "Invalid versioned input row")
                seen.add(row["chunk_id"])
                counts["documents"] += 1
                counts.update(levels)
                rows.append(row)
        checksum = hasher.hexdigest()
        require(checksum == self.manifest["rows_sha256"] and dict(counts) == self.manifest["counts"]
                and self.manifest["input_bundle_id"] == digest([VERSION, self.manifest["projection"], self.chunk["logical_id"], checksum]),
                "Versioned input row binding changed")
        return rows

    def _documents(self, cid):
        entry = self.clean[cid]
        for position, (projected, chunk, parent, graph) in zip(self.source_rows.get(cid, ()),
                _each_source(self.chunk["records"][cid]), strict=True):
            row = self.rows[position]
            require(row == map_row(position, entry["source_item"], projected), "Versioned input projection changed")
            yield {"row": row, "projected": projected, "chunk": chunk, "parent": parent, "graph": graph,
                   "current_observation": entry["current_observation"],
                   "effective_observation": entry["effective_observation"], "clean_record": entry["record"],
                   "current_raw": self.current_raw, "effective_raw": entry["effective_raw"]}

    def _source_documents(self, cid):
        return {doc["row"]["input_row"]: doc for doc in self._documents(cid)}

    def iter_documents(self, sources=None):
        for cid in sorted(self.chunk["records"] if sources is None else sources):
            yield from self._documents(cid)

    def document(self, number):
        require(type(number) is int and 0 <= number < len(self.rows), "Invalid snapshot input row")
        cid = self.rows[number]["source_item"]["content_id"]
        return self._cached_source(cid)[number]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chunk_manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    value = build_inputs(args.chunk_manifest, args.output)
    print(value["input_bundle_id"], value["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
