"""Register immutable per-source ranges in a sealed flat Chunking V2 export."""

import argparse
import hashlib
import json
from pathlib import Path

from ..collector.run_store import create_json
from ..evidence_io import inside, read, require, sha


VERSION = "gc-chunk-flat-ranges-1"
NAMES = ("admission.jsonl", "parents.jsonl", "graphs.jsonl", "chunks-balanced.jsonl")


def _scan(path, candidates, checksum):
    ranges = {}
    file_hash = hashlib.sha256()
    offset = 0
    current_key = None
    with path.open("rb") as handle:
        for line in handle:
            file_hash.update(line)
            row = json.loads(line)
            key = row["content_id"]
            require(key in candidates, "Unknown source in Chunk export")
            if path.name != "admission.jsonl":
                require(row["observation_id"] == candidates[key]["observation_id"], "Chunk source observation differs")
            if key != current_key:
                if current_key is not None:
                    ranges[current_key]["sha256"] = range_hash.hexdigest()
                require(key not in ranges, "Flat Chunk export repeats a noncontiguous source range")
                ranges[key] = {"offset": offset, "bytes": 0, "rows": 0}
                range_hash = hashlib.sha256()
                current_key = key
            range_hash.update(line)
            ranges[key]["bytes"] += len(line)
            ranges[key]["rows"] += 1
            offset += len(line)
        if current_key is not None:
            ranges[current_key]["sha256"] = range_hash.hexdigest()
    require(file_hash.hexdigest() == checksum, "Chunk flat export artifact hash changed")
    return ranges


def register_flat(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    checksum = sha(manifest_path)
    manifest = read(manifest_path)
    require(manifest["schema_version"] == "gc-chunking-pilot-2" and manifest["status"] == "complete",
            "Complete Chunking V2 flat export required")
    clean_path = Path(manifest["clean_manifest"])
    require(sha(clean_path) == manifest["clean_manifest_sha256"], "Chunk/Clean binding changed")
    cleaner = read(clean_path)
    require(cleaner["schema_version"] == "gc-clean-1" and cleaner["status"] == "complete", "Complete Clean V1 manifest required")
    clean_entries = {row["content_id"]: row for row in cleaner["records"]}
    candidates = {row["content_id"]: row for row in manifest["sources"]}
    require(candidates and len(candidates) == len(manifest["sources"]) and len(clean_entries) == len(cleaner["records"])
            and set(candidates) == set(manifest["selection"]["content_ids"]), "Invalid Chunk source coverage")
    for key, row in candidates.items():
        clean = clean_entries[key]
        require(row["before_sha256"] == row["after_sha256"] == clean["record_sha256"]
                and row["observation_id"] == clean["observation_id"], "Chunk source differs from Clean entry")
    files = {name: manifest["artifacts"][name] for name in NAMES}
    computed = {name: _scan(inside(manifest_path.parent, name), candidates, hash_value) for name, hash_value in files.items()}
    require(sha(manifest_path) == checksum and sha(clean_path) == manifest["clean_manifest_sha256"], "Source manifest changed")
    return {"version": VERSION, "status": "complete", "manifest": str(manifest_path), "manifest_sha256": checksum,
            "files": files, "dependencies": {key: manifest[key] for key in ("code_hashes", "tokenizers", "tokenizers_version")},
            "profile": [row for row in manifest["selection"]["profiles"] if row["name"] == "balanced"],
            "bge_document_token_cap": manifest["selection"]["bge_document_token_cap"],
            "records": {key: {"clean_observation_id": row["observation_id"], "clean_sha256": row["before_sha256"],
                              "ranges": {name: file_ranges.get(key) for name, file_ranges in computed.items()}}
                        for key, row in sorted(candidates.items())}}


def read_range(catalog, content_id, name):
    require(catalog["version"] == VERSION and catalog["status"] == "complete" and name in NAMES, "Unsupported Chunk range")
    manifest = Path(catalog["manifest"])
    require(sha(manifest) == catalog["manifest_sha256"], "Bound Chunk manifest changed")
    descriptor = catalog["records"][content_id]["ranges"][name]
    if descriptor is None:
        return []
    with inside(manifest.parent, name).open("rb") as handle:
        handle.seek(descriptor["offset"])
        data = handle.read(descriptor["bytes"])
    require(len(data) == descriptor["bytes"] and hashlib.sha256(data).hexdigest() == descriptor["sha256"], "Chunk range hash changed")
    rows = [json.loads(line) for line in data.splitlines()]
    require(len(rows) == descriptor["rows"] and all(row["content_id"] == content_id for row in rows), "Chunk range coverage changed")
    if name != "admission.jsonl":
        require(all(row["observation_id"] == catalog["records"][content_id]["clean_observation_id"] for row in rows),
                "Chunk range observation changed")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    require(output == args.output.absolute() and not output.is_relative_to(args.manifest.resolve().parent)
            and not output.exists(), "Use a new Chunk range catalog outside the bound export")
    result = register_flat(args.manifest)
    create_json(output, result)
    print(len(result["records"]), "source ranges registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
