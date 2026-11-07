"""Resumable record-worker Clean builds over a bound, immutable Raw run."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

from ..collector.storage import atomic_write
from ..evidence_io import digest, inside, read, sha
from ..build_runtime import atomic_json, checked_receipt, record_jobs
from ..model_calls import locked
from .models import SCHEMA_VERSION, dependencies, observation_id
from .obc import clean_obc_detail, record_fingerprint
from .reader import iter_records, render_record, validate_record


def _bytes(value: Any, *, pretty: bool = True) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None,
                       separators=None if pretty else (",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _record_bytes(record) -> bytes:
    # The reused parser emits orphan warnings from a set. Stabilize only those
    # diagnostic positions within their origin, leaving source arrays intact.
    diagnostics = list(record["diagnostics"])
    positions = defaultdict(list)
    for index, diagnostic in enumerate(diagnostics):
        if diagnostic["code"] == "DIALOGUE_ORPHAN_NODE":
            positions[diagnostic["origin_id"]].append(index)
    for indexes in positions.values():
        ordered = sorted((diagnostics[index] for index in indexes), key=lambda value: _bytes(value, pretty=False))
        for index, diagnostic in zip(indexes, ordered):
            diagnostics[index] = diagnostic
    return _bytes({**record, "diagnostics": diagnostics}, pretty=False)


class RawRun:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.manifest = read(self.root / "metadata/manifest.json")
        if self.manifest.get("status") != "complete" or self.manifest.get("source_system") != "mihoyo_obc":
            raise ValueError("Clean requires a complete mihoyo_obc Raw manifest")
        for key in ("run_id", "locale"):
            if not isinstance(self.manifest.get(key), str) or not self.manifest[key]:
                raise ValueError("Raw manifest identity is incomplete")
        self.manifest_sha256 = hashlib.sha256((self.root / "metadata/manifest.json").read_bytes()).hexdigest()
        self.inventory_artifact = self._local_artifact("metadata/inventory.json", "inventory")
        self.inventory = read(self.root / "metadata/inventory.json")
        self.items: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, item in enumerate(self.inventory):
            content_id = str(item["content_id"])
            if content_id in self.items or not isinstance(item.get("channels"), list):
                raise ValueError("Raw inventory has duplicate or invalid item")
            self.items[content_id] = (index, item)
        details = self.manifest.get("details", [])
        self.details = {str(item["content_id"]): item for item in details}
        if len(self.details) != len(details):
            raise ValueError("Raw manifest has duplicate details")
        self.discovery_artifacts: dict[str, dict[str, Any]] = {"inventory": self.inventory_artifact}
        self.channel_nodes: dict[str, dict[str, Any]] = {}
        self.listings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._load_discovery()

    def _path(self, value: str) -> Path:
        # Existing manifests contain repository-relative Windows paths. Also
        # support relocated runs while keeping every artifact under this root.
        normalized = value.replace("\\", "/")
        candidate = Path(normalized).resolve()
        if candidate.is_relative_to(self.root):
            return candidate
        parts = normalized.split("/")
        if self.root.name in parts:
            position = len(parts) - 1 - parts[::-1].index(self.root.name)
            normalized = "/".join(parts[position + 1:])
        elif not parts or parts[0] not in {"responses", "metadata"}:
            raise ValueError("Raw manifest path is outside its run")
        return inside(self.root, normalized)

    def _local_artifact(self, relative: str, kind: str) -> dict[str, Any]:
        body = inside(self.root, relative).read_bytes()
        return {"path": relative, "kind": kind, "body": body, "sha256": hashlib.sha256(body).hexdigest()}

    def response(self, manifest_key: str) -> dict[str, Any]:
        entry = self.manifest["paths"][manifest_key]
        kind, key = manifest_key.split(":", 1)
        path = self._path(entry["raw"])
        metadata_path = self._path(entry["metadata"])
        body = path.read_bytes()
        checksum = hashlib.sha256(body).hexdigest()
        metadata = read(metadata_path)
        if checksum != entry["sha256"] or metadata.get("sha256") != checksum:
            raise ValueError("RAW_CHECKSUM_MISMATCH")
        if metadata.get("kind") != kind or str(metadata.get("key")) != key:
            raise ValueError("RAW_METADATA_IDENTITY_MISMATCH")
        if metadata.get("ok") is not True or metadata.get("status") != 200:
            raise ValueError("RAW_METADATA_STATUS_INVALID")
        return {"path": path.relative_to(self.root).as_posix(), "kind": kind, "body": body, "sha256": checksum}

    def _load_discovery(self) -> None:
        map_keys = [key for key in self.manifest["paths"] if key.startswith("map:")]
        if not map_keys:
            raise ValueError("Raw run has no channel map")
        for key in sorted(self.manifest["paths"]):
            kind = key.split(":", 1)[0]
            if kind not in {"map", "listings"}:
                continue
            artifact = self.response(key)
            self.discovery_artifacts[key] = artifact
            data = json.loads(artifact["body"])
            if data.get("retcode") != 0 or not isinstance(data.get("data"), dict):
                raise ValueError("Discovery envelope invalid")
            nodes = data["data"].get("list")
            self._index_nodes(nodes, "/data/list", key, kind == "map")

    def _index_nodes(self, nodes: Any, pointer: str, artifact_name: str, is_map: bool) -> None:
        if not isinstance(nodes, list):
            raise ValueError("Discovery channel nodes must be an array")
        for index, node in enumerate(nodes):
            if not isinstance(node, dict) or "id" not in node:
                raise ValueError("Discovery channel node identity missing")
            node_pointer = pointer + f"/{index}"
            channel_id = str(node["id"])
            if is_map:
                if channel_id in self.channel_nodes:
                    raise ValueError("Duplicate map channel identity")
                self.channel_nodes[channel_id] = {
                    "id": channel_id, "name": node["name"], "parent_id": str(node["parent_id"]),
                    "pointer": node_pointer, "artifact_name": artifact_name,
                }
            rows = node.get("list", [])
            if not isinstance(rows, list):
                raise ValueError("Discovery listing rows must be an array")
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict) or "content_id" not in row:
                    raise ValueError("Discovery row content identity missing")
                self.listings[str(row["content_id"])].append({
                    "artifact_name": artifact_name, "pointer": node_pointer + f"/list/{row_index}",
                    "channel_id": channel_id, "requested_channel_id": artifact_name.split(":", 1)[1],
                    "discovery_kind": "map_preview" if is_map else "listing",
                })
            self._index_nodes(node.get("children", []), node_pointer + "/children", artifact_name, is_map)

    def discovery(self, content_id: str) -> dict[str, Any]:
        index, item = self.items[content_id]
        memberships = [{"channel_id": str(channel_id), "artifact_name": "inventory",
                        "pointer": f"/{index}/channels/{position}"}
                       for position, channel_id in enumerate(item["channels"])]
        rows = self.listings.get(content_id, [])
        channel_nodes = {}
        for membership in memberships:
            channel = membership["channel_id"]
            while channel in self.channel_nodes and channel not in channel_nodes:
                channel_nodes[channel] = self.channel_nodes[channel]
                channel = self.channel_nodes[channel]["parent_id"]
        names = {"inventory"} | {row["artifact_name"] for row in rows}
        names.update(node["artifact_name"] for node in channel_nodes.values())
        return {"artifacts": {name: self.discovery_artifacts[name] for name in sorted(names)},
                "memberships": memberships, "listings": rows, "channel_nodes": channel_nodes}


_clean_context = None


def _init_clean_worker(root, raw_root, source, locale, raw_run_id, binding, complete):
    global _clean_context
    _clean_context = (Path(root), Path(raw_root), source, locale, raw_run_id, binding, complete)


def _clean_record_job(task):
    root, _, _, _, _, _, complete = _clean_context
    if complete:
        return _build_clean_record(task)
    # A previous coordinator may have died while its child was still writing.
    # The per-record kernel lock also protects that recovery boundary.
    with locked(root / "metadata/job-locks" / (digest(task["content_id"]) + ".lock")):
        return _build_clean_record(task)


def _build_clean_record(task):
    root, raw_root, source, locale, raw_run_id, binding, complete = _clean_context
    content_id = task["content_id"]
    try:
        if "error" in task:
            raise ValueError(task["error"])
        detail = task["detail"]
        discovery = task["discovery"]
        fingerprint = record_fingerprint(detail, discovery)
        identity = observation_id({"source": source, "locale": locale, "content_id": content_id},
                                  raw_run_id, fingerprint)
        path = "records/" + identity + ".json"
        record_path = inside(root, path)
        receipt_path = root / "metadata/completed" / (identity + ".json")
        old_entry = task.get("old_entry", {})
        if receipt_path.exists():
            receipt = checked_receipt(receipt_path, root, binding)
            entry = receipt["entry"]
            if (entry["content_id"], entry["observation_id"], entry["record_path"]) != (content_id, identity, path):
                raise ValueError("Clean checkpoint identity mismatch")
            if receipt["files"] != {path: entry["record_sha256"]}:
                raise ValueError("Clean checkpoint artifact set mismatch")
            return {**entry, "reused": True}
        if complete and not record_path.exists():
            raise ValueError("Completed Clean run has a missing record")
        reused = record_path.exists()
        body = record_path.read_bytes() if reused else None
        expected_sha = old_entry.get("record_sha256")
        if body is not None and expected_sha:
            if hashlib.sha256(body).hexdigest() != expected_sha:
                raise ValueError("Existing Clean record checksum mismatch")
            if (old_entry["observation_id"], old_entry["record_path"]) != (identity, path):
                raise ValueError("Existing Clean dependency mismatch")
            # The bound input and validated output bytes are unchanged. Reuse the
            # original validation result instead of reparsing all Raw locators.
            entry = {**old_entry, "reused": True}
        else:
            record = None
            prior = task.get("prior")
            if body is None and prior and prior["entry"].get("observation_id") == identity:
                prior_entry = prior["entry"]
                prior_body = inside(Path(prior["root"]), prior_entry["record_path"]).read_bytes()
                if hashlib.sha256(prior_body).hexdigest() != prior_entry["record_sha256"]:
                    raise ValueError("Reusable Clean record checksum mismatch")
                record = json.loads(prior_body)
                reused = True
            if record is None:
                loaded_discovery = {**discovery, "artifacts": {}}
                for name, artifact in discovery["artifacts"].items():
                    loaded_discovery["artifacts"][name] = {
                        **artifact, "body": inside(raw_root, artifact["path"]).read_bytes()}
                detail_body = inside(raw_root, detail["path"]).read_bytes()
                record = clean_obc_detail(detail_body, artifact_path=detail["path"],
                    artifact_sha256=detail["sha256"], source=source, locale=locale,
                    raw_run_id=raw_run_id, content_id=content_id, discovery=loaded_discovery)
            if record["observation_id"] != identity:
                raise ValueError("Clean observation dependency mismatch")
            validation = validate_record(record, raw_root)
            serialized = _record_bytes(record)
            if body is None:
                atomic_write(record_path, serialized)
            elif body != serialized:
                if _record_bytes(json.loads(body)) != serialized:
                    raise ValueError("Existing record has conflicting serialization")
                # Recover a pre-checkpoint legacy file without rewriting it.
                serialized = body
            entry = {"content_id": content_id, "observation_id": identity,
                     "record_path": path, "record_sha256": hashlib.sha256(serialized).hexdigest(),
                     "status": record["coverage"]["status"], "coverage": record["coverage"],
                     "validation": validation, "reused": reused}
        if not complete:
            atomic_json(receipt_path, {"binding": binding, "entry": entry,
                                      "files": {path: entry["record_sha256"]}})
        return entry
    except (ValueError, KeyError, TypeError, OSError, UnicodeError) as error:
        if complete:
            raise ValueError(f"Completed Clean run failed verification for {content_id}: {error}") from error
        return {"content_id": content_id, "status": "blocked_integrity",
                "error": {"type": type(error).__name__, "message": str(error)}}


def build_clean_run(*, raw_run_root: Path, clean_run_id: str,
                    output_root: Path = Path("data/clean"), content_ids: list[str] | None = None,
                    reuse_manifest: Path | None = None, workers: int = 12) -> dict[str, Any]:
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", clean_run_id):
        raise ValueError("Invalid Clean run ID")
    raw = RawRun(raw_run_root)
    source, locale, raw_run_id = (raw.manifest[key] for key in ("source_system", "locale", "run_id"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", locale):
        raise ValueError("Invalid locale path component")
    selected = list(raw.items) if content_ids is None else list(content_ids)
    if len(selected) != len(set(selected)) or any(item not in raw.items for item in selected):
        raise ValueError("Selection must contain unique, discovered content IDs")
    root = Path(output_root) / source / locale / clean_run_id
    if root.resolve().is_relative_to(raw.root) or raw.root.is_relative_to(root.resolve()):
        raise ValueError("Clean output must be separate from its immutable Raw input")
    manifest_path = root / "metadata/manifest.json"
    with locked(root / "build.lock"):
        binding = {"raw_run_id": raw_run_id, "raw_manifest_sha256": raw.manifest_sha256,
                   "selection": selected, "dependencies": dependencies(),
                   "discovery_sha256": {key: artifact["sha256"] for key, artifact in raw.discovery_artifacts.items()}}
        existing = read(manifest_path) if manifest_path.exists() else None
        if existing is not None and existing.get("binding") != binding:
            raise FileExistsError("Clean run ID already binds different inputs or versions")
        prior_entries = {}
        if reuse_manifest is not None:
            prior = read(reuse_manifest)
            if (prior.get("source"), prior.get("locale"), prior.get("raw_run_id")) == (source, locale, raw_run_id):
                prior_entries = {entry["content_id"]: {"root": str(Path(reuse_manifest).parent.parent), "entry": entry}
                                 for entry in prior["records"] if entry.get("record_path")}
        old_entries = {entry["content_id"]: entry for entry in existing["records"]} if existing else {}
        complete = existing is not None and existing["status"] == "complete"
        if complete and (len(old_entries) != len(selected) or set(old_entries) != set(selected)):
            raise ValueError("Complete Clean manifest has inconsistent coverage")
        manifest = {"schema_version": SCHEMA_VERSION, "clean_run_id": clean_run_id,
                    "source": source, "locale": locale, "raw_run_id": raw_run_id, "binding": binding,
                    "status": "partial", "input_detail_count": len(selected), "records": [],
                    "accounted_detail_count": 0, "counts": {}, "coverage": {}}
        results = {}

        def checkpoint(status="partial"):
            manifest["records"] = [results[cid] for cid in selected if cid in results]
            manifest["accounted_detail_count"] = len(manifest["records"])
            counts = Counter(entry["status"] for entry in manifest["records"])
            coverage = Counter()
            for entry in manifest["records"]:
                coverage.update({key: value for key, value in entry.get("coverage", {}).items() if type(value) is int})
            manifest.update(status=status, counts=dict(counts), coverage=dict(coverage))
            atomic_write(manifest_path, _bytes(manifest))

        def tasks():
            for content_id in selected:
                task = {"content_id": content_id, "old_entry": old_entries.get(content_id, {}),
                        "prior": prior_entries.get(content_id)}
                try:
                    if raw.details.get(content_id, {}).get("status") != "completed":
                        raise ValueError("Raw detail not completed")
                    detail = raw.response("details:" + content_id)
                    discovery = raw.discovery(content_id)
                    task["detail"] = {key: value for key, value in detail.items() if key != "body"}
                    task["discovery"] = {**discovery, "artifacts": {
                        name: {key: value for key, value in artifact.items() if key != "body"}
                        for name, artifact in discovery["artifacts"].items()}}
                except (ValueError, KeyError, TypeError, OSError, UnicodeError) as error:
                    task["error"] = str(error)
                yield task

        if not complete:
            checkpoint()
        last_flush = time.monotonic()
        try:
            for entry in record_jobs(_clean_record_job, tasks(), workers=workers,
                    initializer=_init_clean_worker,
                    initargs=(root, raw.root, source, locale, raw_run_id, digest(binding), complete)):
                results[entry["content_id"]] = entry
                if not complete and time.monotonic() - last_flush >= 2:
                    checkpoint()
                    last_flush = time.monotonic()
                print(json.dumps({"done": len(results), "total": len(selected),
                                  "record": entry["content_id"], "status": entry["status"]}), flush=True)
        except BaseException:
            if not complete:
                checkpoint()
            raise
        if sha(raw.root / "metadata/manifest.json") != raw.manifest_sha256:
            raise ValueError("Raw manifest changed during Clean build")
        for artifact in raw.discovery_artifacts.values():
            if sha(inside(raw.root, artifact["path"])) != artifact["sha256"]:
                raise ValueError("Raw discovery changed during Clean build")
        for cid, entry in results.items():
            if entry["status"] != "blocked_integrity":
                detail_path = raw._path(raw.manifest["paths"]["details:" + cid]["raw"])
                if sha(detail_path) != raw.manifest["paths"]["details:" + cid]["sha256"]:
                    raise ValueError("Raw detail changed during Clean build")
        if complete:
            return existing
        status = "complete_with_errors" if any(e["status"] == "blocked_integrity" for e in results.values()) else "complete"
        checkpoint(status)
        return manifest

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Raw-to-Clean builder and reader")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--raw-run-root", required=True, type=Path)
    build.add_argument("--clean-run-id", required=True)
    build.add_argument("--output-root", type=Path, default=Path("data/clean"))
    build.add_argument("--content-id", action="append", dest="content_ids")
    build.add_argument("--reuse-manifest", type=Path)
    build.add_argument("--workers", type=int, default=12)
    export = subparsers.add_parser("export")
    export.add_argument("--manifest", type=Path, required=True)
    export.add_argument("--content-id")
    export.add_argument("--format", choices=("text", "markdown", "jsonl"), default="text")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--raw-run-root", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "build":
        manifest = build_clean_run(raw_run_root=args.raw_run_root, clean_run_id=args.clean_run_id,
                                   output_root=args.output_root, content_ids=args.content_ids,
                                   reuse_manifest=args.reuse_manifest, workers=args.workers)
        print(json.dumps({key: manifest[key] for key in ("status", "accounted_detail_count", "counts", "coverage")}, ensure_ascii=False))
        return 0 if manifest["status"] == "complete" else 1
    if args.command == "validate":
        from .validation import validate_clean_run
        print(json.dumps(validate_clean_run(args.manifest, raw_run_root=args.raw_run_root), ensure_ascii=False))
        return 0
    for record in iter_records(args.manifest):
        if args.content_id is None or record["source_item"]["content_id"] == args.content_id:
            print(json.dumps(record, ensure_ascii=False) if args.format == "jsonl" else render_record(record, format=args.format))
    return 0
