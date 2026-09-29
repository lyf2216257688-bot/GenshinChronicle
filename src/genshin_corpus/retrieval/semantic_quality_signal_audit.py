"""Provider-free investigation of semantic quality screening signals.

This module reads immutable Phase 05 semantic artifacts and writes a new,
append-only research report.  It does not change semantic runtime behavior,
active-view eligibility, or provider request contracts.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json


SCHEMA_VERSION = "phase05-w2-quality-signal-audit-0.1"
OBSERVATION_VERSION = "phase05-w2-quality-observation-0.1"


class QualitySignalAuditError(ValueError):
    """Raised when immutable input artifacts cannot be read consistently."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualitySignalAuditError(f"cannot read JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise QualitySignalAuditError(f"JSON artifact must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _descriptor(path: Path, *, root: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _immutable_write(path: Path, value: Any) -> None:
    body = canonical_json_bytes(value) + b"\n"
    if path.exists():
        if path.read_bytes() != body:
            raise QualitySignalAuditError(f"refusing to overwrite existing artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _extract_payload(request: Mapping[str, Any]) -> dict[str, Any] | None:
    for message in request.get("messages", []):
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("record_key"), str) and isinstance(value.get("segments"), list):
            return value
    return None


def _read_jsonl_gzip(path: Path) -> Iterable[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QualitySignalAuditError(f"invalid JSONL row {path}:{line_number}") from exc
            if isinstance(value, dict):
                yield value


def _stream_content(path: Path) -> str:
    parts: list[str] = []
    for row in _read_jsonl_gzip(path):
        for choice in row.get("choices", []):
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
                parts.append(delta["content"])
    return "".join(parts)


def _output_from_attempt(attempt: Path) -> tuple[dict[str, Any] | None, str]:
    canonical = attempt / "canonical_output.json"
    if canonical.exists():
        return _read_json(canonical), "canonical_output.json"
    stream = attempt / "stream.jsonl.gz"
    if not stream.exists():
        stream = attempt / "stream.jsonl"
    if not stream.exists():
        return None, "none"
    content = _stream_content(stream) if stream.suffix == ".gz" else ""
    if not content:
        return None, stream.name
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None, stream.name
    return value if isinstance(value, dict) else None, stream.name


def _has_explicit_stage_marker(value: Any) -> bool:
    """Detect compiler-preserved explicit stage metadata, not split IDs."""

    if isinstance(value, Mapping):
        stage = value.get("semantic_stage")
        if isinstance(stage, Mapping) and str(stage.get("boundary", "")).startswith("explicit_"):
            return True
        return any(_has_explicit_stage_marker(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_explicit_stage_marker(child) for child in value)
    return False


def _payload_features(payload: Mapping[str, Any] | None, output: Mapping[str, Any] | None, validation: Mapping[str, Any] | None) -> dict[str, Any]:
    segments = payload.get("segments", []) if isinstance(payload, Mapping) else []
    segments = [item for item in segments if isinstance(item, Mapping)]
    values = [item.get("value", {}) for item in segments if isinstance(item.get("value"), Mapping)]
    kinds = Counter(str(value.get("kind", "UNKNOWN")) for value in values)
    components = Counter(str(value.get("component", "UNKNOWN")) for value in values)
    has_text_component = any(
        bool(value.get("text"))
        or bool(value.get("dialogue"))
        or value.get("component") in {"interactive_dialogue", "rich_text"}
        for value in values
    )
    output_items = output.get("items", []) if isinstance(output, Mapping) else []
    output_items = [item for item in output_items if isinstance(item, Mapping)]
    coverage = output.get("segment_coverage", []) if isinstance(output, Mapping) else []
    coverage = [item for item in coverage if isinstance(item, Mapping)]
    qualifiers = [item for item in output_items if isinstance(item.get("qualifiers"), Mapping) and item.get("qualifiers")]
    refs = [item for item in output_items if item.get("source_segment_ids")]
    return {
        "input_serialized_chars": len(canonical_json_bytes(payload).decode("utf-8")) if payload is not None else None,
        "input_segment_count": len(segments),
        "input_kind_counts": dict(sorted(kinds.items())),
        "input_component_counts": dict(sorted(components.items())),
        "input_has_map_desc": "map_desc" in components,
        "input_has_text_component": has_text_component,
        "input_has_explicit_stage_marker": any(_has_explicit_stage_marker(value) for value in values),
        "output_item_count": len(output_items),
        "output_empty": len(output_items) == 0,
        "output_kind_counts": dict(sorted(Counter(str(item.get("kind", "UNKNOWN")) for item in output_items).items())),
        "output_qualifier_item_count": len(qualifiers),
        "output_bound_item_count": len(refs),
        "coverage_counts": dict(sorted(Counter(str(item.get("disposition", "UNKNOWN")) for item in coverage).items())),
        "coverage_noncovered_count": sum(item.get("disposition") != "covered" for item in coverage),
        "validator_terminal_disposition": validation.get("terminal_disposition") if isinstance(validation, Mapping) else None,
        "validator_binding_status": (validation.get("source_binding_validation") or {}).get("status") if isinstance(validation, Mapping) and isinstance(validation.get("source_binding_validation"), Mapping) else None,
    }


def _load_200_units(root: Path) -> list[dict[str, Any]]:
    sample = _read_json(root / "sample_manifest.json")
    sample_by_id = {str(row.get("compilation_unit_id")): row for row in sample.get("selected_units", []) if isinstance(row, Mapping)}
    rows: list[dict[str, Any]] = []
    for unit_dir in sorted((root / "run" / "units").iterdir()):
        attempt = unit_dir / "attempt-001"
        if not attempt.is_dir() or not (attempt / "issued.json").exists():
            continue
        issued = _read_json(attempt / "issued.json")
        request = _read_json(attempt / "request.json")
        payload = _extract_payload(request)
        output, output_source = _output_from_attempt(attempt)
        validation = _read_json(attempt / "validation.json") if (attempt / "validation.json").exists() else {}
        sample_row = sample_by_id.get(str(issued.get("unit_id")), {})
        rows.append({
            "unit_id": issued.get("unit_id"),
            "record_key": payload.get("record_key") if payload else None,
            "title": payload.get("title") if payload else None,
            "sample_ordinal": sample_row.get("sample_ordinal"),
            "serialized_input_bytes": sample_row.get("serialized_input_bytes"),
            "stratum": sample_row.get("stratum"),
            "tags": sample_row.get("tags", []),
            "output_source": output_source,
            "features": _payload_features(payload, output, validation),
        })
    if len(rows) != int(sample.get("selected_count", -1)):
        raise QualitySignalAuditError(f"200-unit count mismatch: {len(rows)}")
    return rows


def _load_targeted_units(root: Path) -> list[dict[str, Any]]:
    preflight = root / "preflight-final"
    units = _read_json(preflight / "units.json").get("items", [])
    payloads = {str(row.get("record_key")): row for row in _read_jsonl_gzip(preflight / "payloads.jsonl.gz")}
    live_root = root / "live-20260928-r1" / "units"
    attempts: dict[str, tuple[Path, dict[str, Any], dict[str, Any] | None, str]] = {}
    for unit_dir in live_root.iterdir():
        attempt = unit_dir / "attempt-001"
        if not (attempt / "issued.json").exists():
            continue
        issued = _read_json(attempt / "issued.json")
        request = _read_json(attempt / "request.json")
        output, output_source = _output_from_attempt(attempt)
        attempts[str(issued.get("unit_id"))] = (attempt, request, output, output_source)
    rows: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        unit_id = str(unit.get("compilation_unit_id"))
        attempt_info = attempts.get(unit_id)
        if attempt_info:
            attempt, request, output, output_source = attempt_info
            payload = _extract_payload(request)
            validation = _read_json(attempt / "validation.json") if (attempt / "validation.json").exists() else {}
            status = validation.get("terminal_disposition", "attempted_without_terminal")
            artifact_ref = str(attempt.relative_to(root)).replace("\\", "/")
        else:
            payload = payloads.get(str(unit.get("record_key")))
            output = None
            output_source = "not_attempted"
            validation = {}
            status = "not_attempted"
            artifact_ref = None
        features = _payload_features(payload, output, validation)
        rows.append({
            "unit_id": unit_id,
            "request_ordinal": unit.get("request_ordinal"),
            "record_key": unit.get("record_key"),
            "title": unit.get("title"),
            "selection_role": unit.get("selection_role"),
            "selection_reason": unit.get("selection_reason"),
            "serialized_input_bytes": unit.get("serialized_input_bytes"),
            "segment_ids": unit.get("segment_ids", []),
            "status": status,
            "output_source": output_source,
            "attempt_artifact_relative_path": artifact_ref,
            "features": features,
            "review_disposition": "mechanical_observation_only",
        })
    return rows


def _distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def band(value: Any) -> str:
        if not isinstance(value, int):
            return "UNKNOWN"
        if value < 1000:
            return "lt1k"
        if value < 4000:
            return "1to4k"
        if value < 8000:
            return "4to8k"
        return "8to16k"

    bands: dict[str, dict[str, int]] = {}
    for row in rows:
        key = band(row.get("serialized_input_bytes"))
        stats = bands.setdefault(key, {"units": 0, "empty_output": 0, "noncovered_output": 0, "zero_or_more_items": 0})
        stats["units"] += 1
        features = row["features"]
        stats["empty_output"] += int(features["output_empty"])
        stats["noncovered_output"] += int(features["coverage_noncovered_count"] > 0)
        stats["zero_or_more_items"] += int(features["output_item_count"] > 0)
    return {
        "unit_count": len(rows),
        "bands": bands,
        "output_kind_totals": dict(sorted(Counter(kind for row in rows for kind, count in row["features"]["output_kind_counts"].items() for _ in range(count)).items())),
        "empty_output_units": sum(row["features"]["output_empty"] for row in rows),
        "units_with_noncovered_coverage": sum(row["features"]["coverage_noncovered_count"] > 0 for row in rows),
        "candidate_signal_counts": {
            "input_multi_segment": sum(row["features"]["input_segment_count"] > 1 for row in rows),
            "input_map_text_mix": sum(row["features"]["input_has_map_desc"] and row["features"]["input_has_text_component"] for row in rows),
            "input_explicit_stage_marker": sum(row["features"]["input_has_explicit_stage_marker"] for row in rows),
            "output_empty": sum(row["features"]["output_empty"] for row in rows),
            "output_one_or_fewer_items": sum(row["features"]["output_item_count"] <= 1 for row in rows),
            "coverage_noncovered": sum(row["features"]["coverage_noncovered_count"] > 0 for row in rows),
            "coverage_all_covered_but_empty": sum(row["features"]["output_empty"] and row["features"]["coverage_noncovered_count"] == 0 for row in rows),
            "nonempty_without_qualifiers": sum((not row["features"]["output_empty"]) and row["features"]["output_qualifier_item_count"] == 0 for row in rows),
            "validator_nonaccepted": sum(row["features"]["validator_terminal_disposition"] != "accepted_for_local_contract" for row in rows),
        },
    }


def build_quality_signal_audit(*, semantic_root: Path, targeted_root: Path, output_root: Path) -> dict[str, Any]:
    semantic_root = Path(semantic_root)
    targeted_root = Path(targeted_root)
    output_root = Path(output_root)
    rows_200 = _load_200_units(semantic_root)
    rows_targeted = _load_targeted_units(targeted_root)
    control_review_path = Path.cwd() / "docs" / "research" / "phase-05" / "p05-w2-semantic-quality-signal-control-review-20260929.json"
    if not control_review_path.exists():
        raise QualitySignalAuditError(f"control review artifact missing: {control_review_path}")
    report = {
        "schema_version": SCHEMA_VERSION,
        "observation_version": OBSERVATION_VERSION,
        "provider_free": True,
        "provider_calls_issued_by_analyzer": 0,
        "inputs": {
            "semantic_root": str(semantic_root),
            "targeted_root": str(targeted_root),
            "semantic_sample_manifest": _descriptor(semantic_root / "sample_manifest.json", root=semantic_root),
            "semantic_run_manifest": _descriptor(semantic_root / "run" / "manifest.json", root=semantic_root),
            "targeted_preflight": _descriptor(targeted_root / "preflight-final" / "preflight.json", root=targeted_root),
            "targeted_units": _descriptor(targeted_root / "preflight-final" / "units.json", root=targeted_root),
            "targeted_live_manifest": _descriptor(targeted_root / "live-20260928-r1" / "manifest.json", root=targeted_root),
        },
        "source_identities": {
            "semantic_sample_identity": _read_json(semantic_root / "sample_manifest.json").get("sample_identity"),
            "semantic_run_identity": _read_json(semantic_root / "run" / "manifest.json").get("identity"),
            "targeted_preflight_identity": _read_json(targeted_root / "preflight-final" / "preflight.json").get("preflight_identity"),
            "targeted_payload_set_identity": _read_json(targeted_root / "preflight-final" / "preflight.json").get("payload_set_identity"),
            "targeted_live_run_identity": _read_json(targeted_root / "live-20260928-r1" / "terminal_summary.json").get("run_identity"),
        },
        "distribution": _distribution(rows_200),
        "control_review": {
            "artifact": "docs/research/phase-05/p05-w2-semantic-quality-signal-control-review-20260929.json",
            "artifact_sha256": _sha256(control_review_path),
            "status": "descriptive_read_only_review",
            "cases": 4,
            "false_positive_precision": "UNKNOWN",
            "false_negative_precision": "UNKNOWN",
        },
        "signal_scope": {
            "candidate_input_signals": ["serialized_input_size_band", "segment_count", "source_kind", "map_text_mix", "explicit_stage_metadata", "existing_compiler_tags"],
            "candidate_output_signals": ["empty_or_sparse_output", "coverage_without_items", "noncovered_segment", "qualifier_presence", "kind_profile", "validator_disposition"],
            "not_decided": ["risk_score", "threshold", "retry_count", "self_review_schema", "stronger_model", "active_eligibility_rule"],
        },
        "targeted_units": rows_targeted,
        "judgment": {
            "confirmed_observation_types": [],
            "unknown_observation_types": ["semantic_correctness", "ordering", "attribution", "validator_boundary"],
            "conclusion": "This analyzer reports objective candidate signals only; the available evidence does not validate selective-review admission or any semantic acceptance gate.",
        },
    }
    report["report_identity"] = sha256_json(report)
    _immutable_write(output_root / "analysis.json", report)
    _immutable_write(output_root / "distribution.json", report["distribution"])
    _immutable_write(output_root / "targeted-cases.json", {"schema_version": SCHEMA_VERSION, "items": rows_targeted})
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "report_identity": report["report_identity"],
        "analysis": {"path": "analysis.json", "sha256": _sha256(output_root / "analysis.json")},
        "distribution": {"path": "distribution.json", "sha256": _sha256(output_root / "distribution.json")},
        "targeted_cases": {"path": "targeted-cases.json", "sha256": _sha256(output_root / "targeted-cases.json")},
        "provider_calls": 0,
    }
    _immutable_write(output_root / "manifest.json", manifest)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a provider-free Phase 05 semantic quality signal audit")
    parser.add_argument("--semantic-root", type=Path, required=True)
    parser.add_argument("--targeted-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    report = build_quality_signal_audit(semantic_root=args.semantic_root, targeted_root=args.targeted_root, output_root=args.output_root)
    print(json.dumps({"status": "complete", "report_identity": report["report_identity"], "provider_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
