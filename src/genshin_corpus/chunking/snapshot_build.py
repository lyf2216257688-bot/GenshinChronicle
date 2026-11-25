"""Build a balanced Chunk snapshot with immutable per-source ranges."""

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from ..clean.snapshot_build import VERSION as CLEAN_VERSION, validate_snapshot
from ..collector.run_store import create_json
from ..evidence_io import digest, inside, read, require, sha
from ..model_calls import locked
from . import pilot
from .snapshot_ranges import NAMES, VERSION as FLAT_VERSION, read_range


VERSION = "gc-chunk-snapshot-1"
CORE = ("core.py", "policy.py", "reader.py", "structure.py", "pilot.py")


def _rules(selection, tokenizers):
    from tokenizers import __version__

    folder = Path(__file__).parent
    profiles = [profile for profile in selection["profiles"] if profile["name"] == "balanced"]
    cap = selection["bge_document_token_cap"]
    require(len(profiles) == 1 and type(cap) is int and cap > 0, "Expected one balanced Chunk profile and a positive token cap")
    return {"profile": profiles, "bge_document_token_cap": cap, "tokenizers": [sha(path) for path in tokenizers],
            "tokenizers_version": __version__, "code": {name: sha(folder / name) for name in CORE}}


def _compatible_flat(base, rules):
    deps = base["dependencies"]
    return (sha(base["manifest"]) == base["manifest_sha256"] and base["status"] == "complete"
            and base["profile"] == rules["profile"] and base["bge_document_token_cap"] == rules["bge_document_token_cap"]
            and [row["sha256"] for row in deps["tokenizers"]] == rules["tokenizers"]
            and deps["tokenizers_version"] == rules["tokenizers_version"]
            and all(deps["code_hashes"].get(name) == checksum for name, checksum in rules["code"].items()))


def _record_id(entry, rules):
    return digest([VERSION, rules, entry["source_item"], entry["clean_observation_id"], entry["clean_sha256"],
                   {name: None if ref is None else [ref["rows"], ref["bytes"], ref["sha256"]]
                    for name, ref in entry["ranges"].items()}])


def _read_bytes(ref):
    path = Path(ref["path"])
    require(path.is_absolute() and path == path.resolve()
            and type(ref["offset"]) is int and ref["offset"] >= 0
            and type(ref["bytes"]) is int and ref["bytes"] > 0, "Invalid Chunk snapshot range")
    with path.open("rb") as handle:
        handle.seek(ref["offset"])
        data = handle.read(ref["bytes"])
    require(len(data) == ref["bytes"] and hashlib.sha256(data).hexdigest() == ref["sha256"], "Chunk snapshot range changed")
    require(data.count(b"\n") == ref["rows"] and data.endswith(b"\n"), "Chunk snapshot range framing changed")
    return data


def read_source_rows(entry, name):
    from ..evidence_io import strict_json

    require(name in NAMES, "Unsupported Chunk snapshot artifact")
    ref = entry["ranges"][name]
    if ref is None:
        return []
    result = [strict_json(line.decode("utf-8")) for line in _read_bytes(ref).splitlines()]
    require(len(result) == ref["rows"] and all(row["content_id"] == entry["source_item"]["content_id"] for row in result),
            "Chunk snapshot source range differs")
    if name != "admission.jsonl":
        require(all(row["observation_id"] == entry["clean_observation_id"] for row in result), "Chunk snapshot observation differs")
    return result


def _reuse(base, entry, matches):
    if not matches:
        return None
    cid = entry["source_item"]["content_id"]
    old = base["records"].get(cid)
    if old is None or any(old[name] != entry[name] for name in ("clean_sha256", "clean_observation_id")):
        return None
    if base.get("version") == FLAT_VERSION:
        parent = Path(base["manifest"]).parent
        return {name: None if ref is None else {**ref, "path": str(inside(parent, name))}
                for name, ref in old["ranges"].items()}
    require(old["artifact_id"] == _record_id(old, base["rules"]), "Historical Chunk artifact identity changed")
    return old["ranges"]


def _append(name, temp, paths, handles):
    path, handle = paths[name], handles[name]
    offset = handle.tell()
    hasher, rows = hashlib.sha256(), 0
    with (temp / name).open("rb") as source:
        for line in source:
            handle.write(line)
            hasher.update(line)
            rows += 1
    if not rows:
        return None
    return {"path": str(path), "offset": offset, "bytes": handle.tell() - offset, "rows": rows, "sha256": hasher.hexdigest()}


def _clean_records(clean_path, expected):
    clean = read(clean_path)
    require(clean["schema_version"] == CLEAN_VERSION and clean["status"] == "complete"
            and (expected is None or sha(clean_path) == expected), "Bound Clean snapshot differs")
    return clean["records"]


def validate_snapshot_manifest(path):
    path = Path(path).resolve()
    manifest = read(path)
    require(manifest["version"] == VERSION and manifest["status"] == "complete", "Unsupported Chunk snapshot")
    plan = Path(path.parent / "plan.json")
    require(sha(plan) == manifest["plan_sha256"], "Chunk plan changed")
    clean_path = Path(manifest["clean_snapshot"]["path"])
    expected_rows = _clean_records(clean_path, manifest["clean_snapshot"]["sha256"])
    ids = []
    for source in expected_rows:
        cid = source["source_item"]["content_id"]
        entry = manifest["records"][cid]
        require(entry["source_item"] == source["source_item"] and entry["clean_sha256"] == source["record"]["sha256"]
                and entry["clean_observation_id"] == source["record"]["clean_observation_id"]
                and entry["artifact_id"] == _record_id(entry, manifest["rules"]), "Clean/Chunk binding changed")
        for name in NAMES:
            read_source_rows(entry, name)
        ids.append(entry["artifact_id"])
    require(len(expected_rows) == len(manifest["records"]) and digest(ids) == manifest["logical_id"], "Chunk snapshot coverage changed")
    return manifest


def build_snapshot(clean_path, selection_path, tokenizer_paths, output, *, mode, previous=None):
    require(mode in {"full", "incremental"}, "Select a Chunk snapshot build mode")
    clean_path, selection_path, output = map(lambda path: Path(path).resolve(), (clean_path, selection_path, output))
    tokenizer_paths = tuple(Path(path).resolve() for path in tokenizer_paths)
    require(len(tokenizer_paths) == 2, "Two pinned tokenizers are required")
    require(not output.is_relative_to(clean_path.parent) and not clean_path.is_relative_to(output), "Use a downstream output directory")
    if previous is not None:
        previous = Path(previous).resolve()
        require(not previous.is_relative_to(output), "Use an output outside the previous Chunk release")
    selection = read(selection_path)
    rules = _rules(selection, tokenizer_paths)
    pilot_selection = {"profiles": rules["profile"], "bge_document_token_cap": rules["bge_document_token_cap"], "review_content_ids": []}
    baseline = read(previous) if previous is not None else None
    matches = False
    if mode == "incremental" and baseline is not None:
        if baseline.get("version") == FLAT_VERSION:
            matches = _compatible_flat(baseline, rules)
        else:
            require(baseline["version"] == VERSION and baseline["status"] == "complete", "Incompatible previous Chunk snapshot")
            matches = baseline["rules"] == rules
    clean_ref = {"path": str(clean_path), "sha256": sha(clean_path)}
    plan = {"version": VERSION, "mode": mode, "clean": clean_ref, "rules": rules,
            "previous_sha256": None if previous is None else sha(previous)}
    require(not output.exists() or (output / "plan.json").is_file() or not any(output.iterdir()), "Use a dedicated Chunk snapshot directory")
    with locked(output / "build.lock"):
        plan_path, manifest_path = output / "plan.json", output / "manifest.json"
        if plan_path.exists():
            require(read(plan_path) == plan, "Chunk snapshot resume binding changed")
        else:
            create_json(plan_path, plan)
        if manifest_path.exists():
            return validate_snapshot_manifest(manifest_path)
        clean = validate_snapshot(clean_path, verify_origins=False)
        require(sha(clean_path) == clean_ref["sha256"], "Clean snapshot changed during Chunk preparation")
        records, ids, counts = {}, [], Counter()
        pack_dir = Path(mkdtemp(prefix="pack-", dir=output))
        with TemporaryDirectory(prefix="work-", dir=output) as job, ExitStack() as cleanup:
            job = Path(job)
            paths = {name: pack_dir / name for name in NAMES}
            handles = {name: cleanup.enter_context(path.open("xb")) for name, path in paths.items()}
            initialized = None
            for source in clean["records"]:
                item, ref = source["source_item"], source["record"]
                cid = item["content_id"]
                entry = {"source_item": item, "clean_observation_id": ref["clean_observation_id"], "clean_sha256": ref["sha256"]}
                ranges = _reuse(baseline, entry, matches)
                if ranges is None:
                    record_path = Path(ref["path"])
                    identity = (item["source"], item["locale"], source["effective_observation"]["run_id"])
                    key = record_path.parent, identity
                    if key != initialized:
                        pilot._init_chunk_worker(record_path.parent, job, identity, pilot_selection, tokenizer_paths, digest(plan))
                        initialized = key
                    shard = job / "_records" / ref["clean_observation_id"]
                    pilot._chunk_record_job({"content_id": cid, "observation_id": ref["clean_observation_id"],
                                             "record_sha256": ref["sha256"], "record_path": record_path.name})
                    ranges = {name: _append(name, shard, paths, handles) for name in NAMES}
                    counts["built"] += 1
                else:
                    for ref_range in ranges.values():
                        if ref_range is not None:
                            _read_bytes(ref_range)
                    counts["reused"] += 1
                entry["ranges"] = ranges
                entry["artifact_id"] = _record_id(entry, rules)
                ids.append(entry["artifact_id"])
                records[cid] = entry
            for handle in handles.values():
                handle.flush()
                os.fsync(handle.fileno())
        require(sha(clean_path) == clean_ref["sha256"] and _rules(selection, tokenizer_paths) == rules
                and (previous is None or sha(previous) == plan["previous_sha256"]), "A Chunk snapshot input changed during build")
        value = {"version": VERSION, "status": "complete", "clean_snapshot": clean_ref, "rules": rules,
                 "logical_id": digest(ids), "counts": dict(counts), "plan_sha256": sha(plan_path), "records": records}
        create_json(manifest_path, value)
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for name in ("clean", "selection", "qwen-tokenizer", "bge-tokenizer", "output"):
        build.add_argument("--" + name, type=Path, required=True)
    build.add_argument("--previous", type=Path)
    build.add_argument("--mode", choices=("full", "incremental"), required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    value = (build_snapshot(args.clean, args.selection, (args.qwen_tokenizer, args.bge_tokenizer), args.output,
                            mode=args.mode, previous=args.previous)
             if args.command == "build" else validate_snapshot_manifest(args.manifest))
    print(value["logical_id"], value["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
