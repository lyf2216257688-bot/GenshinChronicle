"""Provider-free preflight for the bounded P05-W2 semantic comparison."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from .semantic_compiler_u1 import (
    SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
    SEMANTIC_OUTPUT_SCHEMA_VERSION,
    SemanticCompilerU1Error,
    semantic_input_identity,
    validate_semantic_output_envelope,
)
from .semantic_compilation import SemanticBuild, SemanticItem, SemanticStage, SourceBinding


LIVE_PREFLIGHT_SCHEMA_VERSION = "phase05-w2-live-preflight-0.1"
SEMANTIC_PROMPT_VERSION = "phase05-w2-semantic-compiler-prompt-0.1"
SEMANTIC_PROMPT = {
    "version": SEMANTIC_PROMPT_VERSION,
    "instruction": (
        "Extract only navigation-relevant topics, mentions, events, relations, and facts supported by the supplied source segments. "
        "Bind every item to one or more supplied segment_id values; never invent a source ID. Preserve temporal, identity, causal, "
        "and epistemic qualifiers when supported. Account for every input segment exactly once in segment_coverage, using covered, "
        "no_navigation_material, ambiguous, or unsupported. Return only the required structured-output object."
    ),
    "semantic_acceptance_boundary": "output remains unaccepted until separate source-binding and human/curated acceptance",
}


class SemanticLivePreflightError(ValueError):
    """Raised when a paid-run input cannot be frozen safely."""


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _write(path: Path, body: bytes) -> dict[str, Any]:
    if path.exists() and path.read_bytes() != body:
        raise SemanticLivePreflightError(f"refusing to overwrite immutable artifact: {path}")
    if not path.exists():
        atomic_write(path, body)
    return {"path": path.name, "sha256": _sha(body), "byte_count": len(body)}


def _write_json(path: Path, value: Any) -> dict[str, Any]:
    return _write(path, canonical_json_bytes(value))


def _write_jsonl_gzip(path: Path, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    import io

    output = io.BytesIO()
    count = 0
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as handle:
        for row in rows:
            handle.write(canonical_json_bytes(dict(row)) + b"\n")
            count += 1
    descriptor = _write(path, output.getvalue())
    descriptor["row_count"] = count
    return descriptor


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise SemanticLivePreflightError(f"expected JSON object: {path}")
    return value


def _rows(path: Path) -> list[Mapping[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def _high_risk(item: Mapping[str, Any]) -> list[str]:
    return [str(x) for x in item.get("selection_rationale", []) if str(x).startswith("challenger_high_risk:")]


def _control(item: Mapping[str, Any]) -> str | None:
    return next((str(x) for x in item.get("selection_rationale", []) if str(x).startswith("challenger_matched_control:")), None)


def _assert_sample(sample: Mapping[str, Any], units: Mapping[str, Mapping[str, Any]], sidecars: Mapping[str, Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if sample.get("membership_counts") != {"projection_contract": 42, "projection_only": 12, "semantic_quality": 30}:
        raise SemanticLivePreflightError("accepted r5 sample membership is not 42/30/12")
    items = sample.get("items")
    if not isinstance(items, list) or len(items) != 42:
        raise SemanticLivePreflightError("sample must contain exactly 42 items")
    quality = [item for item in items if item.get("semantic_quality_member") is True]
    paired = [item for item in quality if item.get("challenger_paired") is True]
    risks = [item for item in paired if _high_risk(item)]
    controls = [item for item in paired if _control(item)]
    if (len(quality), len(paired), len(risks), len(controls)) != (30, 18, 14, 4):
        raise SemanticLivePreflightError("sample is not 30 quality / 18 paired / 14 high-risk / 4 controls")
    if any(_high_risk(item) for item in controls):
        raise SemanticLivePreflightError("matched control has high-risk rationale")
    for item in items:
        unit_id = str(item.get("compilation_unit_id"))
        row = units.get(unit_id)
        if row is None:
            raise SemanticLivePreflightError(f"sample unit missing: {unit_id}")
        if row.get("status") == "oversized_unresolved" or row.get("provider_payload", {}).get("omission_summary", {}).get("partial_input"):
            raise SemanticLivePreflightError(f"sample unit is oversized or partial: {unit_id}")
        ids = row.get("segment_ids")
        if not isinstance(ids, list) or not ids:
            raise SemanticLivePreflightError(f"sample unit has no segments: {unit_id}")
        for segment_id in ids:
            sidecar = sidecars.get(str(segment_id))
            if sidecar is None or not sidecar.get("retrieval_unit_ids"):
                raise SemanticLivePreflightError(f"sample unit lacks RU binding: {unit_id}/{segment_id}")
    return quality, paired


def _dry_run_output_contract(segment_ids: Sequence[str]) -> dict[str, Any]:
    expected = tuple(segment_ids)
    kinds = ("topic", "mention", "event", "relation", "fact")
    items = [{
        "local_id": f"fixture-{kind}", "kind": kind, "label": f"fixture {kind}",
        "source_segment_ids": [expected[index % len(expected)]], "topic_path": ["fixture", kind],
        "subject_ref": "A" if kind in {"event", "relation", "fact"} else None,
        "object_ref": "B" if kind in {"relation", "fact"} else None,
        "predicate": "supports" if kind == "relation" else None,
        "event_type": "reveal" if kind == "event" else None,
        "participants": ["A", "B"] if kind in {"event", "relation"} else [],
        "qualifiers": {"temporal": "ordered", "epistemic": "asserted", "identity": "stable", "causal": "unknown"},
    } for index, kind in enumerate(kinds)]
    valid = {"schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION, "items": items, "segment_coverage": [{"segment_id": x, "disposition": "covered", "reason": None} for x in expected]}
    try:
        validate_semantic_output_envelope(valid, expected_segment_ids=expected)
    except SemanticCompilerU1Error as exc:
        raise SemanticLivePreflightError("valid output fixture rejected") from exc
    rejected: dict[str, str] = {}
    invalid = {
        "missing_coverage": {**valid, "segment_coverage": valid["segment_coverage"][:-1]},
        "extra_source_segment": {**valid, "items": [{**items[0], "source_segment_ids": ["unbound"]}] + items[1:]},
        "unsupported_top_level_field": {**valid, "unexpected": True},
        "missing_item_binding": {**valid, "items": [{**items[0], "source_segment_ids": []}] + items[1:]},
    }
    for name, value in invalid.items():
        try:
            validate_semantic_output_envelope(value, expected_segment_ids=expected)
        except SemanticCompilerU1Error:
            rejected[name] = "rejected"
        else:
            raise SemanticLivePreflightError(f"malformed output fixture accepted: {name}")
    address, lineage = {"record_id": "fixture-record"}, {"source": "fixture"}
    binding = SourceBinding("ru-fixture", address, lineage)
    semantic_items = (
        SemanticItem("accepted", "fact", "accepted", (binding,), semantic_acceptance_status="accepted_for_fixture_test", qualifiers={}),
        SemanticItem("inactive", "fact", "inactive", (binding,), semantic_acceptance_status="not_accepted", qualifiers={}),
        SemanticItem("rejected", "fact", "rejected", (SourceBinding("ru-unbound", address, lineage),), semantic_acceptance_status="accepted_for_fixture_test", qualifiers={}),
    )
    build = SemanticBuild.from_items(
        input_identity="fixture-input",
        stages=(SemanticStage("fixture", "fixture-stage", "fixture-input", "complete"),),
        items=semantic_items,
        ru_index={"ru-fixture": {"source": {"canonical_address": address, "lineage": lineage}}},
    )
    if [item.item_id for item in build.active_items] != ["accepted"] or len(build.inactive_ledger) != 2:
        raise SemanticLivePreflightError("SemanticBuild accepted/inactive/rejected dry run failed")
    return {
        "valid_all_kinds_and_qualifiers": True,
        "rejections": rejected,
        "semantic_build_dispositions": {"active": 1, "inactive": 1, "binding_rejected": 1},
    }


def build_live_preflight(u1_root: Path, output_root: Path, *, review_source_u1_root: Path | None = None) -> dict[str, Any]:
    """Freeze exact request payloads and config without provider activity."""
    u1_root, output_root = Path(u1_root), Path(output_root)
    review_source_u1_root = Path(review_source_u1_root) if review_source_u1_root is not None else u1_root
    u1_manifest = _json(u1_root / "manifest.json")
    sample = _json(u1_root / "sample_manifest.json")
    identity = _json(u1_root / "identity.json")
    review_source_manifest = _json(review_source_u1_root / "manifest.json")
    review_source_sample = _json(review_source_u1_root / "sample_manifest.json")
    if review_source_manifest.get("semantic_build_identity") != u1_manifest.get("semantic_build_identity"):
        raise SemanticLivePreflightError("sample correction changed the accepted semantic build identity")
    units_rows = _rows(u1_root / "compilation_units.jsonl.gz")
    sidecar_rows = _rows(u1_root / "projection_sidecar.jsonl.gz")
    units = {str(row["compilation_unit_id"]): row for row in units_rows}
    sidecars = {str(row["segment_id"]): row for row in sidecar_rows}
    quality, paired = _assert_sample(sample, units, sidecars)
    paired_ids = {str(item["compilation_unit_id"]) for item in paired}
    if not paired_ids.issubset({str(item["compilation_unit_id"]) for item in quality}):
        raise SemanticLivePreflightError("paired units are not a subset of Gemini units")

    def freeze(item: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        unit_id = str(item["compilation_unit_id"])
        row = units[unit_id]
        payload = row.get("provider_payload")
        if not isinstance(payload, Mapping):
            raise SemanticLivePreflightError(f"missing provider payload: {unit_id}")
        body = canonical_json_bytes(payload)
        input_id = semantic_input_identity(payload)
        if input_id != row.get("semantic_input_identity") or input_id != item.get("semantic_input_identity"):
            raise SemanticLivePreflightError(f"payload identity mismatch: {unit_id}")
        record = {
            "compilation_unit_id": unit_id, "record_id": row.get("record_id"),
            "canonical_provenance_identity": row.get("canonical_provenance_identity"),
            "record_projection_identity": row.get("record_projection_identity"),
            "semantic_input_identity": input_id, "payload_sha256": _sha(body),
            "serialized_input_bytes": len(body), "serialized_input_chars": len(body.decode("utf-8")),
            "segment_ids": list(row["segment_ids"]), "segment_count": len(row["segment_ids"]),
            "challenger_paired": unit_id in paired_ids,
            "challenger_role": "high_risk" if _high_risk(item) else ("matched_control" if _control(item) else None),
            "selection_rationale": list(item.get("selection_rationale", [])), "ru_binding": dict(item["ru_binding"]),
        }
        return dict(payload), record

    primary_payloads, primary_records, challenger_payloads, challenger_records = [], [], [], []
    for item in quality:
        payload, record = freeze(item)
        record["gemini_request_ordinal"] = len(primary_records)
        primary_payloads.append(payload); primary_records.append(record)
        if record["compilation_unit_id"] in paired_ids:
            challenger_record = dict(record)
            challenger_record["deepseek_request_ordinal"] = len(challenger_records)
            challenger_payloads.append(payload); challenger_records.append(challenger_record)

    def totals(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        sizes = [int(x["serialized_input_bytes"]) for x in records]
        chars = [int(x["serialized_input_chars"]) for x in records]
        sorted_sizes, sorted_chars = sorted(sizes), sorted(chars)
        percentile = lambda values, fraction: values[max(0, min(len(values) - 1, int((len(values) - 1) * fraction)))]
        return {"unit_count": len(records), "segment_count": sum(int(x["segment_count"]) for x in records), "serialized_input_bytes_total": sum(sizes), "serialized_input_chars_total": sum(chars), "serialized_input_bytes_min": min(sizes), "serialized_input_bytes_p50": percentile(sorted_sizes, 0.50), "serialized_input_bytes_p90": percentile(sorted_sizes, 0.90), "serialized_input_bytes_max": max(sizes), "serialized_input_chars_min": min(chars), "serialized_input_chars_p50": percentile(sorted_chars, 0.50), "serialized_input_chars_p90": percentile(sorted_chars, 0.90), "serialized_input_chars_max": max(chars), "provider_token_counts": "UNKNOWN"}

    execution_config = {
        "primary": {"provider": "Gemini", "model_alias": "Gemini 3.8 Flash", "model_revision": "UNKNOWN", "reasoning_or_thinking": "Medium", "structured_output_mode": "JSON_SCHEMA", "generation_parameters": {"temperature": 0.0}, "max_output_tokens": 8192, "timeout_seconds": 120.0, "request_attempt_policy": {"max_attempts_per_unit": 1, "automatic_retry": False}, "auth_or_endpoint": "runtime-only; not persisted"},
        "challenger": {"provider": "DeepSeek", "model_alias": "DeepSeek V4.1 Flash", "model_revision": "UNKNOWN", "reasoning_or_thinking": "no separate control configured", "structured_output_mode": "JSON_SCHEMA", "generation_parameters": {"temperature": 0.0}, "max_output_tokens": 8192, "timeout_seconds": 120.0, "request_attempt_policy": {"max_attempts_per_unit": 1, "automatic_retry": False}, "auth_or_endpoint": "runtime-only; not persisted"},
        "excluded_providers": ["Terra", "Grok"], "automatic_escalation": False, "sample_expansion": False,
    }
    contract = _dry_run_output_contract(tuple(primary_records[0]["segment_ids"]))
    prompt_identity = sha256_json(SEMANTIC_PROMPT)
    source_items = {str(item["compilation_unit_id"]): item for item in review_source_sample.get("items", [])}
    current_items = {str(item["compilation_unit_id"]): item for item in sample.get("items", [])}
    removed_ids = sorted(set(source_items) - set(current_items))
    added_ids = sorted(set(current_items) - set(source_items))
    removed_controls = sorted(unit_id for unit_id in removed_ids if _control(source_items[unit_id]))
    added_controls = sorted(unit_id for unit_id in added_ids if _control(current_items[unit_id]))
    removed_oversized = sorted(unit_id for unit_id in removed_ids if source_items[unit_id].get("serialized_chars", 0) > 16_000)
    if len(removed_controls) != 2 or len(added_controls) != 2 or len(removed_oversized) != 2:
        raise SemanticLivePreflightError("sample correction delta is not the reviewed two controls plus two oversized units")
    preflight: dict[str, Any] = {
        "schema_version": LIVE_PREFLIGHT_SCHEMA_VERSION, "status": "provider_free_ready", "u1_root": str(u1_root), "review_source_u1_root": str(review_source_u1_root),
        "u1_manifest_sha256": _sha((u1_root / "manifest.json").read_bytes()), "review_source_u1_manifest_sha256": _sha((review_source_u1_root / "manifest.json").read_bytes()), "u1_semantic_build_identity": u1_manifest.get("semantic_build_identity"),
        "canonical_manifest_sha256": u1_manifest.get("canonical_manifest_sha256"), "projection_policy_identity": u1_manifest.get("projection_policy_identity"), "retrieval_unit_build_identity": u1_manifest.get("retrieval_unit_build_identity"),
        "u1_identity_artifact_sha256": _sha((u1_root / "identity.json").read_bytes()), "semantic_output_schema_identity": identity.get("semantic_output_schema_identity", SEMANTIC_OUTPUT_SCHEMA_IDENTITY),
        "semantic_output_schema_artifact_sha256": _sha((u1_root / "semantic_output_schema.json").read_bytes()), "prompt_identity": prompt_identity,
        "sample_membership": {"gemini_semantic_quality": 30, "deepseek_paired": 18, "paired_high_risk": 14, "paired_matched_control": 4},
        "sample_correction": {"removed_unit_ids": removed_ids, "added_unit_ids": added_ids, "removed_control_unit_ids": removed_controls, "added_control_unit_ids": added_controls, "removed_oversized_unit_ids": removed_oversized, "provider_result_used": False},
        "control_review": {"replaced_count": len(removed_controls), "reason": "Two r5 controls aggregated omitted-speaker/dialogue or branch/epistemic-temporal categories; the corrected sample replaces them with ordinary-control compilation units having no high-risk category.", "provider_result_used": False},
        "payload_sets": {"gemini": {"payload_set_identity": sha256_json([x["semantic_input_identity"] for x in primary_records]), **totals(primary_records)}, "deepseek": {"payload_set_identity": sha256_json([x["semantic_input_identity"] for x in challenger_records]), **totals(challenger_records)}},
        "request_ceiling": {"primary_maximum_requests": 30, "challenger_maximum_requests": 18, "combined_maximum_requests": 48, "retry_basis": "one planned attempt per unit; automatic retry disabled"},
        "provider_execution_config": execution_config, "provider_execution_config_identity": sha256_json(execution_config), "prompt_contract": SEMANTIC_PROMPT, "output_contract": {"schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY, "dry_run": contract},
        "provider_calls_executed": 0, "network_calls_executed": 0, "model_downloads": 0,
    }
    for provider_key, payload_key in (("primary", "gemini"), ("challenger", "deepseek")):
        preflight["payload_sets"][payload_key]["provider_request_contract_identity"] = sha256_json({"provider_execution": execution_config[provider_key], "prompt_identity": prompt_identity, "output_schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY, "payload_set_identity": preflight["payload_sets"][payload_key]["payload_set_identity"]})
    preflight["preflight_identity"] = sha256_json({key: value for key, value in preflight.items() if key != "preflight_identity"})
    output_root.mkdir(parents=True, exist_ok=True)
    descriptors = {
        "gemini_payloads.jsonl.gz": _write_jsonl_gzip(output_root / "gemini_payloads.jsonl.gz", primary_payloads),
        "deepseek_payloads.jsonl.gz": _write_jsonl_gzip(output_root / "deepseek_payloads.jsonl.gz", challenger_payloads),
        "gemini_units.json": _write_json(output_root / "gemini_units.json", {"items": primary_records}),
        "deepseek_units.json": _write_json(output_root / "deepseek_units.json", {"items": challenger_records}),
        "provider_execution_config.json": _write_json(output_root / "provider_execution_config.json", execution_config),
        "prompt_contract.json": _write_json(output_root / "prompt_contract.json", SEMANTIC_PROMPT),
        "output_contract_dry_run.json": _write_json(output_root / "output_contract_dry_run.json", contract),
        "sample_snapshot.json": _write_json(output_root / "sample_snapshot.json", {"items": sample["items"], "membership_counts": sample["membership_counts"]}),
    }
    preflight["artifacts"] = descriptors
    _write(output_root / "preflight.json", canonical_json_bytes(preflight))
    return preflight


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build provider-free P05-W2 live preflight")
    parser.add_argument("--u1-root", type=Path, required=True)
    parser.add_argument("--review-source-u1-root", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = build_live_preflight(args.u1_root, args.output_root, review_source_u1_root=args.review_source_u1_root)
    print(json.dumps({"status": result["status"], "artifact_root": str(args.output_root), "payload_sets": result["payload_sets"], "request_ceiling": result["request_ceiling"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["LIVE_PREFLIGHT_SCHEMA_VERSION", "SEMANTIC_PROMPT_VERSION", "SemanticLivePreflightError", "build_live_preflight"]
