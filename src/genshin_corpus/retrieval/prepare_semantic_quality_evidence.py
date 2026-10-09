"""Prepare a small, immutable evidence pack for Phase 05 semantic review.

This module only reads the frozen 2026-10-04 historical-200 manifests, the
persisted mechanical replay report, and the corresponding attempt artifacts.
It does not call a provider, rerun validation, or modify any input root.
"""

from __future__ import annotations

import argparse
import gzip
import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from genshin_corpus.canonical.fingerprints import sha256_json


SCHEMA = "phase05-w2-atomic-quality-evidence-0.1"
REPORT_PATH = Path(".local/p05-w2-historical-atomic-replay-audit-20261007-r1/report.json")
SIBLING_PATH = Path("data/retrieval/p05-w2-bound-literal-span-sibling-20261008-r3/report.json")
SOURCE_ROOT = Path("D:/Batch4_review/p05-w2-u1-provider-free-20261004-mapmedia-r3")
MANIFESTS = (
    Path("D:/Batch4_review/p05-w2-atomic-inventory-manifest-historical-200-20261004-responses-r1.json"),
    Path("D:/Batch4_review/p05-w2-atomic-inventory-manifest-historical-200-20261004-responses-metro-continuation-r1.json"),
    Path("D:/Batch4_review/p05-w2-atomic-inventory-manifest-historical-200-20261004-responses-metro-final4-r1.json"),
)
ROOTS = (
    Path("D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-responses-r1"),
    Path("D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-responses-metro-continuation-r1"),
    Path("D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-responses-metro-final4-r2"),
)

# Selection is deliberately mechanical and type-based.  It does not encode a
# semantic expectation for any row.
SELECTION = {
    "research": {
        "507825": "specified research candidate; dialogue; mechanical FAIL",
        "501287": "specified research candidate; task-relation rich structured data; mechanical FAIL",
        "723": "task-relation rich structured data; mechanical FAIL",
        "509618": "dialogue comparison control; mechanical PASS; semantic status unassessed",
        "1350": "dialogue comparison control; mechanical PASS; semantic status unassessed",
        "2778": "structured regional material; mechanical PASS",
    },
    "validation": {
        "505240": "new dialogue sample; mechanical PASS",
        "5310": "new structured rich-base sample; mechanical PASS",
        "6473": "new structured material/collapse sample; mechanical PASS",
        "508046": "new task-relation rich structured sample; mechanical FAIL",
        "502342": "new dialogue/structured sample; mechanical FAIL",
        "508276": "new structured material sample; mechanical FAIL",
    },
}

# Conservative finite-scope exclusion ledger.  The validation IDs are checked
# against this set and against the repository's prior Phase05 review notes.
KNOWN_REVIEWED = {
    "507825", "500660", "4402", "503380", "506793", "501323", "501287",
    "4333", "509684", "1078", "504120", "501752", "7199", "500761", "367",
    "507259", "2606", "507424", "6296", "2393", "500812", "1404", "3674",
    "505438", "2629", "505808", "500440", "502130", "505161", "7297",
    "1144", "6430", "502434", "6572", "506439", "500078", "723", "501361",
    "1130", "504796", "1150", "4073", "1350", "7183", "501212", "505408",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _sha256_bytes(body: bytes) -> str:
    return sha256(body).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _write_exclusive(path: Path, body: bytes) -> str:
    if path.exists():
        if path.read_bytes() != body:
            raise FileExistsError(f"refusing to overwrite evidence artifact: {path}")
        raise FileExistsError(f"evidence output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body)
    return _sha256_bytes(body)


def _record_id(record_key: str) -> str:
    return record_key.rsplit(":", 1)[-1]


def _category(payload: Mapping[str, Any]) -> list[str]:
    components = sorted({
        segment.get("value", {}).get("component")
        for segment in payload.get("segments", [])
        if isinstance(segment.get("value"), Mapping)
    })
    roles = sorted({
        role
        for segment in payload.get("segments", [])
        for role in segment.get("value", {}).get("content_role", [])
        if isinstance(role, str)
    })
    result = [str(value) for value in components if value]
    if roles:
        result.append("role:" + "+".join(roles))
    return result


def _source_location(payload: Mapping[str, Any]) -> dict[str, Any]:
    locations = []
    for index, segment in enumerate(payload.get("segments", [])):
        value = segment.get("value", {})
        dialogue = value.get("dialogue", {}) if isinstance(value, Mapping) else {}
        locations.append({
            "index": index,
            "segment_id": segment.get("segment_id"),
            "component": value.get("component"),
            "kind": value.get("kind"),
            "section": value.get("section"),
            "content_role": value.get("content_role", []),
            "dialogue_source_path": dialogue.get("source_path") if isinstance(dialogue, Mapping) else None,
            "dialogue_split": {
                "split_parent_segment_id": dialogue.get("split_parent_segment_id"),
                "split_index": dialogue.get("split_index"),
                "split_count": dialogue.get("split_count"),
            } if isinstance(dialogue, Mapping) and "split_index" in dialogue else None,
            "text_segments": value.get("text_segments", []),
        })
    return {"record_key": payload.get("record_key"), "title": payload.get("title"), "segments": locations}


def _load_inputs(repo_root: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], dict[str, Any]]:
    if len(MANIFESTS) != len(ROOTS):
        raise ValueError("manifest/root lineage length mismatch")
    units: list[dict[str, Any]] = []
    for manifest_path, root in zip(MANIFESTS, ROOTS):
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, Mapping) or "contract" not in manifest:
            raise ValueError(f"invalid historical manifest: {manifest_path}")
        if manifest["identity"] != sha256_json(manifest["contract"]):
            raise ValueError(f"historical manifest identity mismatch: {manifest_path}")
        manifest_hash = _sha256(manifest_path)
        for unit in manifest["contract"]["units"]:
            item = dict(unit)
            item["_manifest_path"] = str(manifest_path)
            item["_manifest_sha256"] = manifest_hash
            item["_manifest_identity"] = manifest.get("identity")
            item["_root"] = str(root)
            item["_contract"] = {key: value for key, value in manifest["contract"].items() if key != "units"}
            units.append(item)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        by_id.setdefault(_record_id(unit["payload"]["record_key"]), []).append(unit)
    report = _read_json(repo_root / REPORT_PATH)
    report_rows = {_record_id(row["record_key"]): row for row in report["rows"]}
    return by_id, report_rows, report


def _find_attempt(unit: Mapping[str, Any]) -> Path:
    root = Path(unit["_root"])
    unit_id = unit["compilation_unit_id"]
    matches = list(root.glob("units/*/attempt-001/issued.json"))
    found = []
    for issued_path in matches:
        issued = _read_json(issued_path)
        if issued.get("unit_id") == unit_id:
            found.append(issued_path.parent)
    if len(found) == 1:
        return found[0]
    if found:
        raise ValueError(f"multiple persisted attempts for {unit_id}")
    raise FileNotFoundError(f"no persisted attempt for {unit_id} ({unit['payload']['record_key']})")


def _selection_rows(repo_root: Path) -> list[dict[str, Any]]:
    units_by_id, report_rows, report = _load_inputs(repo_root)
    sibling = _read_json(repo_root / SIBLING_PATH)
    sibling_rows = {row["unit_id"]: row for row in sibling["units"]}
    rows = []
    for sample_set, choices in SELECTION.items():
        for record_id, reason in choices.items():
            if sample_set == "validation" and record_id in KNOWN_REVIEWED:
                raise ValueError(f"validation sample is in prior-review exclusion ledger: {record_id}")
            candidates = units_by_id.get(record_id, [])
            if not candidates:
                raise KeyError(f"selected record is absent from historical manifests: {record_id}")
            mechanical = report_rows.get(record_id)
            if mechanical is None:
                raise KeyError(f"selected record is absent from replay report: {record_id}")
            report_root_name = Path(str(mechanical.get("run_root", ""))).name
            eligible = [candidate for candidate in candidates if Path(candidate["_root"]).name == report_root_name]
            if len(eligible) != 1:
                raise ValueError(f"authoritative run root cannot be resolved: {record_id}")
            unit = eligible[0]
            attempt = _find_attempt(unit)
            output = attempt / "canonical_output.json"
            validation = attempt / "validation.json"
            issued = attempt / "issued.json"
            if not output.exists() or not validation.exists() or not issued.exists():
                raise FileNotFoundError(f"selected record lacks complete attempt evidence: {record_id}")
            payload = unit["payload"]
            terminal = _read_json(attempt / "terminal.json")
            request = _read_json(attempt / "request.json")
            issued_value = _read_json(issued)
            runtime_manifest = _read_json(Path(unit["_root"]) / "manifest.json")
            runtime_unit = next(item for item in runtime_manifest["contract"]["units"]
                                if item["unit_id"] == unit["compilation_unit_id"])
            if (runtime_manifest["identity"] != sha256_json(runtime_manifest["contract"])
                    or issued_value["run_identity"] != runtime_manifest["identity"]
                    or issued_value["request_identity"] != runtime_unit["request_identity"]
                    or issued_value["wire_request_identity"] != runtime_unit["wire_request_identity"]):
                raise ValueError(f"runtime manifest/issued identity mismatch: {record_id}")
            for name in ["canonical_output", "validation", "request"]:
                expected_hash = terminal["artifacts"][name]["sha256"]
                if _sha256(attempt / f"{name}.json") != expected_hash:
                    raise ValueError(f"terminal artifact hash mismatch: {record_id}/{name}")
            if json.loads(request["input"]) != payload:
                raise ValueError(f"issued source payload mismatch: {record_id}")
            if issued_value["unit_id"] != terminal["unit_id"] or issued_value["run_identity"] != terminal["run_identity"]:
                raise ValueError(f"issued/terminal identity mismatch: {record_id}")
            if sibling_row_status := sibling_rows[unit["compilation_unit_id"]].get("strict_replay_verdict"):
                if sibling_row_status != mechanical["current_replay_verdict"]:
                    raise ValueError(f"strict mechanical verdict drift: {record_id}")
            if _sha256(output) != mechanical["canonical_output_sha256"]:
                raise ValueError(f"mechanical report output hash mismatch: {record_id}")
            sibling_row = sibling_rows[unit["compilation_unit_id"]]
            if (sibling_row["verdict_identity"]["source_payload"] != "sha256:" + sha256_json(payload)
                    or sibling_row["verdict_identity"]["canonical_output"] != "sha256:" + _sha256(output)):
                raise ValueError(f"sibling evidence binding mismatch: {record_id}")
            rows.append({
                "sample_id": f"{sample_set}-{len([row for row in rows if row['set'] == sample_set]) + 1:02d}",
                "set": sample_set,
                "record_id": record_id,
                "record_key": payload["record_key"],
                "title": payload.get("title"),
                "selection_reason": reason,
                "categories": _category(payload),
                "mechanical_verdict": {
                    "current_replay_verdict": mechanical.get("current_replay_verdict"),
                    "historical_runtime_verdict": mechanical.get("historical_runtime_verdict"),
                    "failure_class": mechanical.get("failure_class"),
                    "failed_item_count": len(mechanical.get("failed_items", [])),
                    "semantic_correctness": mechanical.get("semantic_correctness"),
                    "validator_identity": mechanical.get("validator_identity"),
                    "validator_error": mechanical.get("validator_error", ""),
                    "validator_source_sha256": sibling["policy_identity"]["semantic_two_stage_experiment.py"],
                    "source_anchor_v01_verdict": sibling_row["source_anchor_v01_verdict"],
                    "literal_span_sibling_verdict": sibling_row["literal_span_sibling_verdict"],
                    "sibling_verdict_identity": sibling_row["verdict_identity"],
                    "sibling_report_sha256": _sha256(repo_root / SIBLING_PATH),
                },
                "unit": unit,
                "issued_identity": issued_value,
                "attempt": attempt,
                "files": {"canonical_output": output, "validation": validation, "issued": issued,
                          "request": attempt / "request.json", "terminal": attempt / "terminal.json"},
                "source_location": _source_location(payload),
                "report_sha256": _sha256(repo_root / REPORT_PATH),
                "report_schema_version": report.get("schema_version"),
            })
    return rows


def _upstream_sources(repo_root: Path, rows: list[dict[str, Any]]) -> dict[str, Path]:
    manifest_path = SOURCE_ROOT / "manifest.json"
    manifest = _read_json(manifest_path)
    sidecar_path = SOURCE_ROOT / "projection_sidecar.jsonl.gz"
    if _sha256(sidecar_path) != manifest["artifacts"][sidecar_path.name]["sha256"]:
        raise ValueError("source sidecar hash mismatch")
    expected = {sid for row in rows for sid in row["unit"]["segment_ids"]}
    sidecars = {}
    with gzip.open(sidecar_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            sidecar = json.loads(line)
            if sidecar["segment_id"] in expected:
                if sidecar["segment_id"] in sidecars:
                    raise ValueError("duplicate selected source sidecar identity")
                sidecars[sidecar["segment_id"]] = sidecar
    if expected != sidecars.keys():
        raise ValueError(f"missing selected source sidecars: {sorted(expected - sidecars.keys())}")
    inputs = {str(manifest_path): manifest_path, str(sidecar_path): sidecar_path}
    canonical_manifest = repo_root / "data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/metadata/manifest.json"
    if _sha256(canonical_manifest) != manifest["canonical_manifest_sha256"]:
        raise ValueError("Canonical manifest hash mismatch")
    canonical_records = {record["record_id"]: record for record in _read_json(canonical_manifest)["records"]}
    inputs[str(canonical_manifest)] = canonical_manifest
    for row in rows:
        selected = [sidecars[sid] for sid in row["unit"]["segment_ids"]]
        raw_files = {}
        for sidecar in selected:
            if sidecar["canonical_address"]["source_identity_key"] != row["record_key"]:
                raise ValueError("sidecar record identity mismatch")
            provenance = sidecar["canonical_provenance"]
            canonical = repo_root / canonical_records[provenance["record_id"]]["canonical_record_path"]
            if _sha256(canonical) != provenance["canonical_record_sha256"]:
                raise ValueError("Canonical source record hash mismatch")
            inputs[str(canonical)] = canonical
            for ref in sidecar["raw_refs"]:
                path = repo_root / Path(ref["artifact_path"])
                if _sha256(path) != ref["artifact_sha256"]:
                    raise ValueError("official Raw source hash mismatch")
                raw_files[str(path)] = path
                inputs[str(path)] = path
        row["source_sidecars"] = selected
        row["raw_files"] = raw_files
    return inputs


def prepare(repo_root: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite evidence output root: {output_root}")
    repo_root = repo_root.resolve()
    rows = _selection_rows(repo_root)
    if len(rows) != 12 or sum(row["set"] == "research" for row in rows) != 6:
        raise ValueError("selection must contain six research and six validation rows")
    validation_ids = {row["record_id"] for row in rows if row["set"] == "validation"}
    if validation_ids & KNOWN_REVIEWED:
        raise ValueError("validation overlaps prior-review exclusion ledger")

    original_paths = _upstream_sources(repo_root, rows)
    for path in [*MANIFESTS, repo_root / REPORT_PATH, repo_root / SIBLING_PATH]:
        original_paths[str(path)] = path
    for row in rows:
        for path in row["files"].values():
            original_paths[str(path)] = path
    before_hashes = {name: _sha256(path) for name, path in original_paths.items()}

    output_root.mkdir(parents=True)
    index_rows = []
    for row in rows:
        sample_root = output_root / "samples" / row["sample_id"]
        sample_root.mkdir(parents=True)
        unit = row["unit"]
        payload_body = _json_bytes(unit["payload"])
        source_hash = _write_exclusive(sample_root / "source_payload.json", payload_body)
        source_text = "\n\n".join(
            f"[{segment.get('segment_id')}]\n{json.dumps(segment, ensure_ascii=False, indent=2, sort_keys=True)}"
            for segment in unit["payload"].get("segments", [])
        ) + ("\n" if unit["payload"].get("segments") else "[no retained source segments]\n")
        _write_exclusive(sample_root / "source_rendering.txt", source_text.encode("utf-8"))
        copied = {}
        for label, source in row["files"].items():
            body = source.read_bytes()
            copied[label] = {
                "path": str(source),
                "sha256": _write_exclusive(sample_root / f"{label}.json", body),
                "source_sha256": _sha256(source),
            }
        _write_exclusive(sample_root / "source_location.json", _json_bytes(row["source_location"]))
        _write_exclusive(sample_root / "source_sidecar.json", _json_bytes(row["source_sidecars"]))
        raw_descriptors = []
        for ordinal, source in enumerate(row["raw_files"].values(), 1):
            filename = f"official_raw-{ordinal:02d}.json"
            body = source.read_bytes()
            raw_descriptors.append({"path": str(source), "copy": filename,
                                    "sha256": _write_exclusive(sample_root / filename, body)})
        _write_exclusive(sample_root / "mechanical_verdict.json", _json_bytes({
            "schema_version": row["report_schema_version"],
            "report_sha256": row["report_sha256"],
            "record_id": row["record_id"],
            "record_key": row["record_key"],
            "unit_id": unit["compilation_unit_id"],
            "verdict": row["mechanical_verdict"],
        }))
        provenance = {
            "record_key": row["record_key"],
            "title": row["title"],
            "compilation_unit_id": unit["compilation_unit_id"],
            "historical_unit_id": unit["historical_unit_id"],
            "semantic_input_identity": unit["semantic_input_identity"],
            "request_identity": unit["request_identity"],
            "wire_request_identity": unit["wire_request_identity"],
            "issued_request_identity": row["issued_identity"]["request_identity"],
            "issued_wire_request_identity": row["issued_identity"]["wire_request_identity"],
            "run_identity": row["issued_identity"]["run_identity"],
            "historical_request_sha256": unit["historical_request_sha256"],
            "segment_ids": unit["segment_ids"],
            "accounted_segment_ids": unit["accounted_segment_ids"],
            "manifest_path": unit["_manifest_path"],
            "manifest_sha256": unit["_manifest_sha256"],
            "manifest_identity": unit["_manifest_identity"],
            "run_root": unit["_root"],
            "attempt_root": str(row["attempt"]),
            "source_payload_sha256": source_hash,
            "source_payload_identity": "sha256:" + sha256_json(unit["payload"]),
            "frozen_extraction_contract": unit["_contract"],
            "source_sidecar_sha256": _sha256(sample_root / "source_sidecar.json"),
            "official_raw": raw_descriptors,
            "artifacts": copied,
        }
        _write_exclusive(sample_root / "provenance.json", _json_bytes(provenance))
        index_rows.append({
            "sample_id": row["sample_id"],
            "set": row["set"],
            "record_id": row["record_id"],
            "record_key": row["record_key"],
            "title": row["title"],
            "categories": row["categories"],
            "selection_reason": row["selection_reason"],
            "mechanical_verdict": row["mechanical_verdict"],
            "sample_root": f"samples/{row['sample_id']}",
            "payload_identity": provenance["source_payload_identity"],
            "canonical_output_sha256": copied["canonical_output"]["sha256"],
            "compilation_unit_id": unit["compilation_unit_id"],
            "semantic_status": "NOT_REVIEWED",
        })

    exclusions = {
        "policy": "validation excludes every record in the finite prior-review ledger; research may include explicitly permitted reviewed cases",
        "known_reviewed_record_ids": sorted(KNOWN_REVIEWED),
        "validation_overlap": sorted(validation_ids & KNOWN_REVIEWED),
        "evidence_scope": [
            "docs/research/phase-05/*.md and *.json exact record-key search",
            "prior 2026-10-08 review row ledger and prior-turn reviewed IDs",
            "mechanical recovery/control ledgers named in the research note",
        ],
        "early_spot_review": {
            "source": "docs/research/phase-05/p05-w2-scoped-semantic-build-20260927-r1.md#semantic-spot-review",
            "recovered_method": "units sorted by (stratum, compilation_unit_id); positions 0,15,...,195",
            "record_ids": ["501752", "500812", "506439", "500078", "723", "501361", "1130", "504796", "1150", "4073", "1350", "7183", "501212", "505408"],
        },
        "limitation": "Repository evidence is finite; absence from the searched records is not proof that no untracked human inspection ever occurred.",
    }
    _write_exclusive(output_root / "prior-review-exclusion-ledger.json", _json_bytes(exclusions))
    completeness = {
        "expected": {"research": 6, "validation": 6, "total": 12},
        "actual": {
            "research": sum(row["set"] == "research" for row in rows),
            "validation": sum(row["set"] == "validation" for row in rows),
            "total": len(rows),
            "mechanical_pass": sum(row["mechanical_verdict"]["current_replay_verdict"] == "PASS" for row in rows),
            "mechanical_fail": sum(row["mechanical_verdict"]["current_replay_verdict"] == "FAIL" for row in rows),
        },
        "validation_prior_review_overlap": sorted(validation_ids & KNOWN_REVIEWED),
        "all_selected_attempts_complete": True,
        "source_output_pairing": True,
        "source_payloads_are_frozen_manifest_payloads": True,
        "provider_calls": 0,
        "network_calls": 0,
    }
    _write_exclusive(output_root / "completeness.json", _json_bytes(completeness))
    index = {
        "schema_version": SCHEMA,
        "status": "prepared_for_independent_review",
        "boundary": "offline evidence preparation only; no semantic labels, threshold, repair, contract, provider, network, or production change",
        "historical_sample": {"logical_units": 200, "date": "2026-10-04", "source": "Responses atomic extraction"},
        "inputs": {
            "replay_report": str(repo_root / REPORT_PATH),
            "replay_report_sha256": _sha256(repo_root / REPORT_PATH),
            "manifests": [str(path) for path in MANIFESTS],
            "roots": [str(path) for path in ROOTS],
            "literal_span_sibling_report": str(repo_root / SIBLING_PATH),
            "literal_span_sibling_report_sha256": _sha256(repo_root / SIBLING_PATH),
            "source_manifest": str(SOURCE_ROOT / "manifest.json"),
        },
        "rows": index_rows,
        "completeness": completeness,
        "prior_review_exclusion": "prior-review-exclusion-ledger.json",
        "preparation_implementation_sha256": _sha256(Path(__file__)),
    }
    after_hashes = {name: _sha256(path) for name, path in original_paths.items()}
    if before_hashes != after_hashes:
        raise ValueError("original evidence changed during preparation")
    _write_exclusive(output_root / "immutable-inputs.json", _json_bytes({
        "before_sha256": before_hashes, "after_sha256": after_hashes, "unchanged": True,
        "scope": "all files consumed by this pack; not a new audit of all 200 attempts",
    }))
    packet_hashes = {path.relative_to(output_root).as_posix(): _sha256(path)
                     for path in output_root.rglob("*") if path.is_file()}
    index["artifacts_sha256"] = packet_hashes
    readme = ["# Atomic Quality Evidence Pack", "", "No semantic judgments are included. Compare only retained source segments to the frozen atomic output; full Raw pages are context, not additional requested extraction coverage.", "", "| Sample | Record | Type | Packet |", "|---|---|---|---|"]
    for row in index_rows:
        readme.append(f"| {row['sample_id']} | {row['record_id']} {row['title']} | {', '.join(row['categories'])} | [source/output](samples/{row['sample_id']}/review.md) |")
        sample = output_root / row["sample_root"]
        source = (sample / "source_rendering.txt").read_text(encoding="utf-8")
        output = (sample / "canonical_output.json").read_text(encoding="utf-8")
        review = f"# {row['sample_id']}: {row['title']}\n\nRecord: {row['record_key']}\n\nSemantic status: NOT_REVIEWED. Source scope is the retained payload, not the complete Raw page. Exact input/output JSON and official Raw context are adjacent. Provenance and mechanical metadata are stored separately.\n\n## Retained Official Source\n\n````json\n{source}\n````\n\n## Frozen Atomic Model Output\n\n````json\n{output}\n````\n"
        index["artifacts_sha256"][f"{row['sample_root']}/review.md"] = _write_exclusive(sample / "review.md", review.encode("utf-8"))
    index["artifacts_sha256"]["README.md"] = _write_exclusive(output_root / "README.md", ("\n".join(readme) + "\n").encode("utf-8"))
    index_body = _json_bytes(index)
    index_hash = _write_exclusive(output_root / "index.json", index_body)
    return {"index_sha256": index_hash, "rows": index_rows, "completeness": completeness}


def verify(output_root: Path) -> dict[str, Any]:
    """Re-read the pack and its original paths without touching either side."""
    index = _read_json(output_root / "index.json")
    checks: list[dict[str, Any]] = []
    for row in index["rows"]:
        sample_root = output_root / row["sample_root"]
        provenance = _read_json(sample_root / "provenance.json")
        source_payload = sample_root / "source_payload.json"
        source_payload_hash = _sha256(source_payload)
        checks.append({
            "sample_id": row["sample_id"],
            "record_id": row["record_id"],
            "source_payload_hash_matches_provenance": source_payload_hash == provenance["source_payload_sha256"],
            "source_payload_record_key_matches": _read_json(source_payload).get("record_key") == row["record_key"],
            "artifacts": {
                label: {
                    "copied_hash_matches_source": _sha256(sample_root / f"{label}.json") == descriptor["source_sha256"] == _sha256(Path(descriptor["path"])),
                }
                for label, descriptor in provenance["artifacts"].items()
            },
        })
    input_paths = [Path(path) for path in index["inputs"]["manifests"]] + [Path(index["inputs"]["replay_report"])]
    input_hashes = {str(path): _sha256(path) for path in input_paths}
    artifact_checks = {name: _sha256(output_root / name) == expected
                       for name, expected in index["artifacts_sha256"].items()}
    frozen_inputs = _read_json(output_root / "immutable-inputs.json")
    original_checks = {name: _sha256(Path(name)) == expected
                       for name, expected in frozen_inputs["before_sha256"].items()}
    result = {
        "schema_version": "phase05-w2-atomic-quality-evidence-integrity-0.1",
        "status": "PASS" if all(artifact_checks.values()) and all(original_checks.values()) and all(
            check["source_payload_hash_matches_provenance"]
            and check["source_payload_record_key_matches"]
            and all(item["copied_hash_matches_source"] for item in check["artifacts"].values())
            for check in checks
        ) else "FAIL",
        "checks": checks,
        "input_hashes_at_verification": input_hashes,
        "packet_artifact_checks": artifact_checks,
        "original_input_checks": original_checks,
        "provider_calls": 0,
        "network_calls": 0,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.repo_root, args.output_root)
    integrity = verify(args.output_root)
    _write_exclusive(args.output_root / "integrity-check.json", _json_bytes(integrity))
    if integrity["status"] != "PASS":
        raise SystemExit("evidence integrity verification failed")
    print(json.dumps({"index_sha256": result["index_sha256"], "completeness": result["completeness"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
