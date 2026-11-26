"""Build or validate a mixed-observation Clean snapshot from sealed Raw runs."""

import argparse
from collections import Counter, defaultdict
import hashlib
from pathlib import Path

from ..collector.run_store import create_json
from ..evidence_io import digest, inside, read, require, sha, strict_json
from ..model_calls import locked
from .models import SCHEMA_VERSION, dependencies, observation_id
from .obc import record_fingerprint
from .pipeline import _bytes, _record_bytes, build_clean_run
from .reader import validate_record
from .snapshot_diff import Snapshot, compare_snapshots


VERSION = "gc-clean-snapshot-1"
REVIEWED_LEGACY_CLEAN_SHA = "f31b5d5252999046e611474f24a1504fbe3d00ef030846484c5c1b455b955faf"
SERIALIZATION = "compact-json-orphan-diagnostics-by-origin-v1"


def _rules():
    package = Path(__file__).parent.parent
    names = ("clean/models.py", "clean/obc.py", "clean/pipeline.py", "clean/reader.py", "clean/context.py",
             "clean/field_rules.py", "clean/markup.py", "parser/contracts.py", "parser/dialogue.py", "parser/obc/adapter.py",
             "evidence_io.py", "build_runtime.py")
    return {"versions": dependencies(), "code": {name: sha(package / name) for name in names},
            "serialization": SERIALIZATION}


def legacy_rules(rules):
    return {key: value for key, value in rules.items() if key != "serialization"}


def serialization_target(body, record):
    canonical = _record_bytes(record)
    if canonical != body:
        require(_bytes(record, pretty=False) == body, "Unsupported historical Clean serialization")
    return hashlib.sha256(canonical).hexdigest()


def _raw_ref(snapshot):
    return {"root": str(snapshot.raw.root), "manifest_sha256": snapshot.binding["manifest_sha256"],
            "run_id": snapshot.binding["run_id"]}


def _record_ref(manifest, entry):
    return {"path": str(inside(manifest.parent.parent, entry["record_path"])),
            "sha256": entry["record_sha256"], "clean_observation_id": entry["observation_id"]}


def _read_record(entry, raw, *, verify_origins=False, check_serialization=False):
    ref = entry["record"]
    path = Path(ref["path"])
    require(path.is_absolute() and path == path.resolve(), "Invalid Clean snapshot path")
    body = path.read_bytes()
    require(hashlib.sha256(body).hexdigest() == ref["sha256"], "Clean snapshot record changed")
    item = entry["source_item"]
    cid = item["content_id"]
    detail = entry["effective_observation"]["locators"]["detail"]
    descriptor = {"kind": "details", "path": detail["path"], "sha256": detail["sha256"]}
    fingerprint = record_fingerprint(descriptor, raw.raw.discovery(cid))
    expected = observation_id(item, raw.binding["run_id"], fingerprint)
    record = strict_json(body.decode("utf-8"))
    require(record["observation_id"] == ref["clean_observation_id"] == expected
            and record["source_item"] == item and record["input"]["raw_run_id"] == raw.binding["run_id"]
            and record["input"]["dependency_fingerprint"] == fingerprint, "Clean record has foreign Raw provenance")
    validate_record(record, raw.raw.root if verify_origins else None)
    return serialization_target(body, record) if check_serialization else ref["sha256"]


def _previous_entries(path, previous, rules):
    value = read(path)
    if value["schema_version"] == VERSION:
        require(value["status"] == "complete" and value["current_raw"] == _raw_ref(previous),
                "Previous Clean snapshot is foreign or incomplete")
        require(value["logical_id"] == _logical_id(value), "Previous Clean logical identity mismatch")
        entries = {row["source_item"]["content_id"]: row for row in value["records"]}
        require(len(entries) == len(value["records"]) and entries.keys() == previous.raw.items.keys(), "Previous Clean coverage differs")
        return entries if value["rules"] in (rules, legacy_rules(rules)) else {}

    require(value["schema_version"] == SCHEMA_VERSION and value["status"] == "complete"
            and value["binding"]["raw_manifest_sha256"] == previous.binding["manifest_sha256"]
            and value["raw_run_id"] == previous.binding["run_id"], "Previous Clean manifest is foreign or incomplete")
    if not previous.raw.versioned:
        require(sha(path) == REVIEWED_LEGACY_CLEAN_SHA, "Legacy Clean input must be the reviewed historical baseline")
    entries = {row["content_id"]: {"record": _record_ref(path, row), "effective_raw": _raw_ref(previous)} for row in value["records"]}
    require(len(entries) == len(value["records"]) and entries.keys() == previous.raw.items.keys(), "Previous Clean coverage differs")
    return entries if value["binding"]["dependencies"] == rules["versions"] else {}


def _logical_id(manifest):
    return digest([VERSION, manifest["rules"], manifest["current_raw"]["manifest_sha256"],
                   [[row["source_item"], row["current_observation"]["observation_id"],
                     row["effective_observation"]["observation_id"], row["record"]["clean_observation_id"], row["record"]["sha256"]]
                    for row in manifest["records"]]])


def _open_raw(ref, archives):
    root = Path(ref["root"])
    if root not in archives:
        archives[root] = Snapshot(root, allow_legacy=True)
    snapshot = archives[root]
    require(ref == _raw_ref(snapshot), "Raw archive identity changed")
    return snapshot


def _paths_separate(output, sources):
    require(all(not output.is_relative_to(path) and not path.is_relative_to(output) for path in sources),
            "Clean snapshot output must be separate from bound input roots")


def derive_records(entries, archives, output, *, workers=1, expected=None):
    groups = defaultdict(list)
    for entry in entries:
        groups[Path(entry["effective_raw"]["root"])].append(entry)
    for root, group in groups.items():
        source = archives[root]
        stage = output / "build"
        run = source.binding["manifest_sha256"]
        ids = [entry["source_item"]["content_id"] for entry in group]
        derived = build_clean_run(raw_run_root=root, output_root=stage, clean_run_id=run, content_ids=ids, workers=workers)
        if derived["status"] != "complete":
            failure = next((entry for entry in derived["records"] if "error" in entry), None)
            raise ValueError("Clean record generation did not complete: " + str(failure))
        manifest = stage / source.coverage["source"] / source.coverage["locale"] / run / "metadata/manifest.json"
        refs = {entry["content_id"]: _record_ref(manifest, entry) for entry in derived["records"]}
        for entry in group:
            cid = entry["source_item"]["content_id"]
            entry["record"] = refs[cid]
            if expected is not None and cid in expected:
                require(refs[cid]["sha256"] == expected[cid], "Historical Clean replay differs: " + cid)


def build_snapshot(current_raw, previous_raw, previous_clean, output, *, mode, workers=1):
    require(mode in {"full", "incremental"}, "Select a Clean snapshot build mode")
    previous_clean = Path(previous_clean).resolve()
    current = Snapshot(current_raw)
    previous = Snapshot(previous_raw, allow_legacy=True)
    output = Path(output).resolve()
    previous_manifest = read(previous_clean)
    previous_root = previous_clean.parent if previous_manifest["schema_version"] == VERSION else previous_clean.parent.parent
    _paths_separate(output, (current.raw.root, previous.raw.root, previous_root))
    plan_path = output / "plan.json"
    require(not output.exists() or plan_path.is_file() or
            all(path.name == "build.lock" for path in output.iterdir()), "Use a dedicated Clean snapshot directory")
    previous_clean_sha = sha(previous_clean)
    rules = _rules()
    base = _previous_entries(previous_clean, previous, rules)
    diff = compare_snapshots(previous.raw.root, current.raw.root)
    require(diff["status"] == "complete" and diff["current"] == current.binding and diff["previous"] == previous.binding,
            "Raw change coverage is unresolved")
    archive_cache = {old.raw.root: old for old in (previous, current)}
    rows, to_build, expected, historical = [], [], {}, {}
    for row in diff["records"]:
        observed = row["current_observation"]
        if observed is None:
            continue
        cid = row["source_item"]["content_id"]
        source = current
        effective = observed
        old = base.get(cid) if row["status"] == "unchanged" else None
        if old is not None:
            source = _open_raw(old["effective_raw"], archive_cache)
            effective = source.observation(cid) if source is not previous else row["previous_observation"]
            require(observed["consumed_source_sha256"] == effective["consumed_source_sha256"], "False historical equivalence claim")
        binding = {"source_item": row["source_item"], "current_observation": observed, "effective_observation": effective,
                   "effective_raw": _raw_ref(source), "disposition": "reused" if old and mode == "incremental" else "built"}
        if old and mode == "incremental":
            binding["record"] = old["record"]
            target = _read_record(binding, source, check_serialization=True)
            if target != old["record"]["sha256"]:
                expected[cid] = target
                historical[cid] = {"record": old["record"], "target_sha256": target}
                binding["disposition"] = "built"
                to_build.append(binding)
        else:
            to_build.append(binding)
        rows.append(binding)

    plan = {"version": VERSION, "mode": mode, "current": _raw_ref(current), "previous": _raw_ref(previous),
            "previous_clean_sha256": previous_clean_sha, "diff_id": digest(diff), "rules": rules,
            "serialization_replays": historical}
    with locked(output / "build.lock"):
        if plan_path.exists():
            require(read(plan_path) == plan, "Clean snapshot resume binding changed")
        else:
            create_json(plan_path, plan)
        manifest_path = output / "manifest.json"
        if manifest_path.exists():
            return validate_snapshot(manifest_path, verify_origins=False)
        derive_records(to_build, archive_cache, output, workers=workers, expected=expected)
        for snapshot in archive_cache.values():
            snapshot.verify_binding()
        require(sha(previous_clean) == previous_clean_sha and _rules() == rules, "Bound Clean input or rules changed")
        manifest = {"schema_version": VERSION, "status": "complete", "current_raw": _raw_ref(current), "rules": rules,
                    "plan_sha256": sha(plan_path), "counts": dict(Counter(entry["disposition"] for entry in rows)),
                    "records": rows}
        manifest["logical_id"] = _logical_id(manifest)
        create_json(manifest_path, manifest)
        return manifest


def validate_snapshot(path, *, verify_origins=True):
    path = Path(path).resolve()
    value = read(path)
    require(value["schema_version"] == VERSION and value["status"] == "complete" and value["rules"] == _rules(),
            "Unsupported, foreign or incomplete Clean snapshot")
    require(sha(path.parent / "plan.json") == value["plan_sha256"], "Clean snapshot plan hash mismatch")
    plan = read(path.parent / "plan.json")
    require(plan["version"] == VERSION and plan["current"] == value["current_raw"] and plan["rules"] == value["rules"],
            "Clean snapshot plan differs")
    require(value["logical_id"] == _logical_id(value), "Clean snapshot logical identity mismatch")
    archives = {}
    current = _open_raw(value["current_raw"], archives)
    require(current.raw.versioned, "Current snapshot requires a versioned Raw archive")
    require(value["counts"] == dict(Counter(row["disposition"] for row in value["records"]))
            and set(value["counts"]) <= {"built", "reused"}, "Clean snapshot disposition counts differ")
    keys = [row["source_item"]["content_id"] for row in value["records"]]
    require(keys == sorted(current.raw.items), "Clean snapshot coverage differs")
    for row, cid in zip(value["records"], keys, strict=True):
        source = _open_raw(row["effective_raw"], archives)
        require(current.observation(cid) == row["current_observation"] and source.observation(cid) == row["effective_observation"]
                and row["current_observation"]["consumed_source_sha256"] == row["effective_observation"]["consumed_source_sha256"],
                "Clean snapshot observations are not equivalent")
        target = _read_record(row, source, verify_origins=verify_origins, check_serialization=True)
        require(target == row["record"]["sha256"], "Noncanonical Clean snapshot record: " + cid)
    for raw in archives.values():
        raw.verify_binding()
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for field in ("current-raw", "previous-raw", "previous-clean", "output"):
        build.add_argument("--" + field, required=True, type=Path)
    build.add_argument("--mode", choices=("full", "incremental"), required=True)
    build.add_argument("--workers", type=int, default=1)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    value = (build_snapshot(args.current_raw, args.previous_raw, args.previous_clean, args.output, mode=args.mode, workers=args.workers)
             if args.command == "build" else validate_snapshot(args.manifest))
    print(value["logical_id"], value["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
