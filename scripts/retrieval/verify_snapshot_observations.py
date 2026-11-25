"""Independently derive a bounded set of unchanged records from the new Raw."""

import argparse
from collections import Counter
from hashlib import sha256
from pathlib import Path

from genshin_corpus.chunking.pilot import run_pilot
from genshin_corpus.chunking.snapshot_ranges import NAMES, read_range, register_flat
from genshin_corpus.clean.pipeline import _record_bytes, build_clean_run
from genshin_corpus.clean.reader import OriginResolver, validate_record
from genshin_corpus.clean.snapshot_diff import ordered_value
from genshin_corpus.evidence_io import digest, read, require, sha, write


def representative(diff, catalog):
    groups = {}
    for row in diff["records"]:
        if row["status"] != "unchanged":
            continue
        cid = row["source_item"]["content_id"]
        ranges = catalog["records"][cid]["ranges"]
        chunks = (ranges["chunks-balanced.jsonl"] or {"rows": 0})["rows"]
        if chunks > 160:
            continue
        graph = ranges["graphs.jsonl"] is not None
        a, b = (row[k]["locators"] for k in ("previous_observation", "current_observation"))
        moved = [p["pointer"] for p in a["listings"]] != [p["pointer"] for p in b["listings"]]
        group = ("empty" if not chunks else "dialogue" if graph else "long" if chunks > 15 else "short", moved)
        groups.setdefault(group, []).append(cid)
    result = {cid: {"structure": group[0], "listing_pointer_moved": group[1]}
              for group, values in sorted(groups.items())
              for cid in sorted(values, key=lambda cid: digest(["independent-20261125", cid]))[:3]}
    require(len(result) >= 12, "Insufficient representative unchanged sources")
    return result


def comparable_clean(record, raw):
    payload = {key: val for key, val in record.items() if key not in {"artifacts", "observation_id", "origins", "input"}}
    payload["input"] = {key: val for key, val in record["input"].items()
                        if key not in {"raw_run_id", "dependency_fingerprint"}}
    artifacts = {item["artifact_id"]: item for item in record["artifacts"]}
    resolver = OriginResolver(record, raw)
    roots = {}
    for key, origin in record["origins"].items():
        if "parent_origin_id" in origin:
            roots[key] = origin
        else:
            kind = artifacts[origin["artifact_id"]]["kind"]
            value = resolver.resolve(key)
            # A channel-node origin carries previews and neighbouring channels.
            # Only these fields are consumed by the Clean membership contract.
            channel_fields = ("id", "name", "parent_id")
            if kind == "map" and isinstance(value, dict) and set(channel_fields) <= value.keys():
                value = {field: value[field] for field in channel_fields}
            roots[key] = {"kind": kind, "precision": origin["precision"],
                          "exact_resolved_value": digest(ordered_value(value))}
    payload["origins"] = roots
    return sha256(_record_bytes(payload)).hexdigest()


ID_FIELDS = {
    "observation_id", "chunk_id", "parent_id", "exact_alias_of_parent_id", "ancestor_parent_id",
    "overlapping_chunk_ids", "article_scope_id", "article_scope_ids", "local_scope_id", "local_scope_ids", "condition_id",
}


def compare_chunks(old, new, cid):
    """Permit only a bijective rename of observation-dependent IDs."""
    names, reverse = {}, {}

    def pair(a, b):
        return names.setdefault(a, b) == b and reverse.setdefault(b, a) == a

    def compare(a, b, field=None):
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(compare(a[key], b[key], key) for key in a)
        if isinstance(a, list):
            if field == "overlapping_chunk_ids":
                # The producer sorts this edge set by the original hash.
                return sorted(names[item] for item in a) == sorted(b)
            return len(a) == len(b) and all(compare(x, y, field) for x, y in zip(a, b, strict=True))
        if field in ID_FIELDS and isinstance(a, str):
            return pair(a, b)
        return a == b

    counts = {}
    ranges = {name: (read_range(old, cid, name), read_range(new, cid, name)) for name in NAMES}
    for name, field in (("chunks-balanced.jsonl", "chunk_id"), ("parents.jsonl", "parent_id")):
        a, b = ranges[name]
        require(len(a) == len(b) and all(pair(x[field], y[field]) for x, y in zip(a, b, strict=True)),
                f"{cid} has different {name} identities")
    for name, (a, b) in ranges.items():
        require(compare(a, b), f"{cid} differs in {name} after observation identity substitution")
        counts[name] = len(a)
    return counts


def run(repo, output):
    data = repo / "data"
    raw = data / "raw/mihoyo_obc/zh-cn"
    sources = (raw / "p01eb_full_20260824", raw / "p01eb_full_20261124")
    diff = data / "retrieval/incremental-raw-diff-20261125.json"
    catalog = data / "retrieval/incremental-chunk-baseline-20261124.json"
    old_chunks = read(catalog)
    old_clean = data / "clean/mihoyo_obc/zh-cn/clean-v1-full-20261106/metadata/manifest.json"
    old = {row["content_id"]: row for row in read(old_clean)["records"]}
    selected = representative(read(diff), old_chunks)
    output.mkdir(parents=True, exist_ok=True)
    clean = build_clean_run(raw_run_root=sources[1], clean_run_id="new-raw-independent", output_root=output / "clean",
                            content_ids=sorted(selected), workers=1)
    require(clean["status"] == "complete", "Incomplete independent Clean sample")
    clean_root = output / "clean/mihoyo_obc/zh-cn/new-raw-independent"
    selection = read(repo / "config/chunking-full-20261107-balanced.json")
    selection.update(content_ids=sorted(selected), review_content_ids=[])
    selection_path = output / "selection.json"
    if not selection_path.exists():
        write(selection_path, selection)
    require(read(selection_path) == selection, "Independent sample selection changed")
    tokenizers = [Path(item["path"]) for item in old_chunks["dependencies"]["tokenizers"]]
    if not (output / "chunk/manifest.json").exists():
        run_pilot(clean_root / "metadata/manifest.json", selection_path, *tokenizers, output / "chunk", workers=1)
    new_chunks = register_flat(output / "chunk/manifest.json")
    require(read(output / "chunk/manifest.json")["clean_manifest_sha256"] == sha(clean_root / "metadata/manifest.json"),
            "New sample Chunk binds a different Clean manifest")
    rows = []
    for entry in clean["records"]:
        cid = entry["content_id"]
        old_path, new_path = old_clean.parent.parent / old[cid]["record_path"], clean_root / entry["record_path"]
        require(sha(old_path) == old[cid]["record_sha256"] and sha(new_path) == entry["record_sha256"],
                "Clean record hash differs")
        before, after = read(old_path), read(new_path)
        require(before["observation_id"] != after["observation_id"], "Expected different Clean observations")
        checks = [validate_record(value, archive) for value, archive in zip((before, after), sources, strict=True)]
        left, right = (comparable_clean(value, root) for value, root in zip((before, after), sources, strict=True))
        require(left == right, f"Independent logical Clean differs for {cid}")
        rows.append({"content_id": cid, **selected[cid], "raw_origins_checked": checks, "clean_semantics_sha256": left,
                     "old_observation_id": before["observation_id"], "new_observation_id": after["observation_id"],
                     "counts": compare_chunks(old_chunks, new_chunks, cid)})
    write(output / "report.json", {"status": "PASS", "diff_sha256": sha(diff), "prior_catalog_sha256": sha(catalog),
                                   "new_chunk_manifest_sha256": sha(output / "chunk/manifest.json"), "records": rows})
    print(len(rows), dict(sum((Counter(item["counts"]) for item in rows), Counter())), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=Path.cwd(), type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.repo.resolve(), args.output.resolve())
