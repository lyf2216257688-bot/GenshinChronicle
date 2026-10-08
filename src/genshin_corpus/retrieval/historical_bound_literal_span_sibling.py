"""Research-only bound literal-span sibling for six frozen Phase05 units."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from genshin_corpus.canonical.fingerprints import sha256_json

from .historical_atomic_replay_audit import (
    _json_pointer_get,
    _read_json,
    _scalar_paths,
    _sha256,
    _validate_with_source_anchor,
)
from .semantic_two_stage_experiment import validate_atomic_inventory


POLICY = "phase05-w2-bound-literal-span-sibling-0.1"
ANCHOR_KIND = "bound_literal_span"
DISALLOWED_CLASSES = {
    "fabricated_or_unbound_quote",
    "metadata_serialized_json_contamination",
}
TARGET_OPERATION = "research_bound_literal_span_anchor"
ROUTING_SHA256 = "985239492ad19bd7057c4a079690abeb7b03edc320695ee9e0f19f64e75b4ea4"
REPLAY_SHA256 = "6fd7ce7fafe0833fe9d51125ea30cbc1c0b68b71a865af5ea7f3ce66a5cc4f90"
TARGET_ITEMS = {
    "207e61c1ee31a9a08c07ae206cb09cf5629bc3c1cbc9e1f6bab9bb56eeeb4d1d":
        {"i114", "i119", "i123", "i124", "i131", "i136"},
    "63636df90f16a60494ff9f976b5e11131c592cc4dc7fd0fc647c82e023402fca": {"e14", "e15"},
    "65d26338764b46bfcbf201fe27073b064e769ebe944ca3dc67a660f5ca610ddd": {"e19"},
    "9429ff71c478843e52bdce6f317b6cba6f7287b5a69425f069091351a5fe6202":
        {"f1", "f2", "f3", "f4", "f5", "f6"},
    "d9e8790e4b92297cc1dda73ce3672f254b089ba490c409a49107b38fe001d14b": {"i85"},
    "f255a6f8fe19d05dc70f252c33c2672cc211b198d423bc22c0c5837e5073478e":
        {"f_desc_910", "f_holder_910", "f_progress_910"},
}


def _tokens(pointer: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~")
            for part in pointer.split("/")[1:]]


def _eligible_literal_pointer(pointer: str) -> bool:
    """Keep raw source values; never interpret HTML attributes as semantics."""
    tokens = _tokens(pointer)
    if len(tokens) < 2 or tokens[0] != "value":
        return False
    if tokens[1] == "decoded":
        return len(tokens) > 2 and not set(tokens[2:]) & {
            "moduleName", "tab_name", "section", "component", "layout_",
            "record_key", "schema_version", "segment_id", "image", "img", "references",
            "text_segments", "content_role", "flags", "shape",
        }
    return tokens[1] in {"dialogue", "rich_text", "text"}


def _span_candidates(payload: Mapping[str, Any], quote: str,
                     refs: list[str]) -> list[dict[str, Any]]:
    if not isinstance(quote, str) or not quote or quote.lstrip().startswith("data-entry-"):
        return []
    segment_ids = [segment.get("segment_id") for segment in payload.get("segments", [])]
    if len(set(segment_ids)) != len(segment_ids):
        raise ValueError("duplicate source segment identity")
    candidates: list[dict[str, Any]] = []
    for segment in payload.get("segments", []):
        if segment.get("segment_id") not in refs:
            continue
        for pointer, scalar in _scalar_paths(segment):
            if not isinstance(scalar, str) or not _eligible_literal_pointer(pointer):
                continue
            start = scalar.find(quote)
            while start != -1:
                end = start + len(quote)
                candidates.append({
                    "kind": ANCHOR_KIND,
                    "segment_id": segment["segment_id"],
                    "json_pointer": pointer,
                    "value_sha256": sha256(scalar.encode("utf-8")).hexdigest(),
                    "start": start,
                    "end": end,
                    "span_unit": "unicode_codepoint",
                    "source_quote": quote,
                })
                start = scalar.find(quote, start + 1)
    return candidates


def _verify_anchor(payload: Mapping[str, Any], item: Mapping[str, Any],
                   anchor: Mapping[str, Any]) -> tuple[bool, str]:
    quote = item.get("qualifiers", {}).get("source_quote")
    refs = item.get("source_segment_ids", [])
    if anchor.get("kind") != ANCHOR_KIND:
        return False, "anchor kind mismatch"
    if anchor.get("segment_id") not in refs:
        return False, "anchor segment is not item-bound"
    if anchor.get("span_unit") != "unicode_codepoint":
        return False, "span unit mismatch"
    if not isinstance(anchor.get("json_pointer"), str) or not _eligible_literal_pointer(anchor["json_pointer"]):
        return False, "anchor pointer is ineligible"
    try:
        segment = next(s for s in payload["segments"]
                       if s.get("segment_id") == anchor["segment_id"])
        scalar = _json_pointer_get(segment, anchor["json_pointer"])
    except (KeyError, IndexError, TypeError, ValueError, StopIteration):
        return False, "anchor pointer is invalid"
    if not isinstance(scalar, str):
        return False, "anchor pointer is not a string scalar"
    if sha256(scalar.encode("utf-8")).hexdigest() != anchor.get("value_sha256"):
        return False, "source scalar hash mismatch"
    start, end = anchor.get("start"), anchor.get("end")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        return False, "invalid codepoint span"
    if end > len(scalar) or scalar[start:end] != quote or anchor.get("source_quote") != quote:
        return False, "span does not reproduce source_quote"
    try:
        candidates = _span_candidates(payload, quote, refs)
    except ValueError as exc:
        return False, str(exc)
    if len(candidates) != 1 or candidates[0] != dict(anchor):
        return False, "anchor is not the unique current bound literal span"
    return True, "unique bound literal span verified"


def _units(manifests: list[Path]) -> dict[str, Mapping[str, Any]]:
    units: dict[str, Mapping[str, Any]] = {}
    for path in manifests:
        body = _read_json(path)
        if body.get("identity") != sha256_json(body["contract"]):
            raise ValueError(f"manifest identity mismatch: {path}")
        for unit in body["contract"]["units"]:
            uid = unit["compilation_unit_id"]
            if uid in units and units[uid]["payload"] != unit["payload"]:
                raise ValueError(f"conflicting payload for {uid}")
            units.setdefault(uid, unit)
    return units


def _all_root_hashes(roots: list[Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for root in roots:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            hashes[path.as_posix()] = _sha256(path)
    return hashes


def _select_targets(routing_report: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    selected_units = [row for row in routing_report["units"]
                      if row.get("routing_verdict") == "CONTRACT_FIXABLE"
                      and row.get("route_set") == ["CONTRACT_FIXABLE"]]
    if len(selected_units) != 6 or {row["unit_id"] for row in selected_units} != set(TARGET_ITEMS):
        raise ValueError("target unit scope differs from the frozen six units")
    selected_items = [row for row in routing_report["items"]
                      if row.get("scope") == "remaining_failed" and row["unit_id"] in TARGET_ITEMS]
    expected = {(uid, local_id) for uid, ids in TARGET_ITEMS.items() for local_id in ids}
    keyed = {(row["unit_id"], row["local_id"]): row for row in selected_items}
    if len(selected_items) != 19 or set(keyed) != expected:
        raise ValueError("target item scope differs from the frozen 19 items")
    for uid, ids in TARGET_ITEMS.items():
        unit = next(row for row in selected_units if row["unit_id"] == uid)
        if unit["remaining_failed_item_count"] != len(ids):
            raise ValueError("target unit item count mismatch")
    for row in selected_items:
        if (row.get("route") != "CONTRACT_FIXABLE"
                or row.get("operation_candidate") != TARGET_OPERATION
                or row.get("failure_class") not in {
                    "source_projection_or_quote_binding", "structured_value_quote_projection_boundary"}):
            raise ValueError("target item is outside the approved literal-span contract")
    return keyed


def _approve_item(payload: Mapping[str, Any], item: Mapping[str, Any],
                  routing_item: Mapping[str, Any], *, anchor: Mapping[str, Any] | None = None) -> dict[str, Any]:
    local_id = item["local_id"]
    result = {"local_id": local_id, "source_quote": item["qualifiers"].get("source_quote"),
              "failure_class": routing_item.get("failure_class"), "verdict": "FAIL"}
    if ((routing_item.get("unit_id"), local_id) not in
            {(uid, key) for uid, ids in TARGET_ITEMS.items() for key in ids}
            or routing_item.get("route") != "CONTRACT_FIXABLE"
            or routing_item.get("scope") != "remaining_failed"
            or routing_item.get("operation_candidate") != TARGET_OPERATION
            or routing_item.get("failure_class") not in {
                "source_projection_or_quote_binding", "structured_value_quote_projection_boundary"}):
        return {**result, "error": "not an authorized literal-span target"}
    if (routing_item["item"] != item or routing_item["identity"]["item"] != "sha256:" + sha256_json(item)
            or routing_item["source_quote"] != result["source_quote"]):
        return {**result, "error": "routing item identity mismatch"}
    try:
        candidates = _span_candidates(payload, result["source_quote"], item["source_segment_ids"])
    except ValueError as exc:
        return {**result, "error": str(exc)}
    if len(candidates) != 1:
        return {**result, "error": f"expected one bound literal span, found {len(candidates)}"}
    candidate = candidates[0]
    evidence = [loc for loc in routing_item["evidence"]["content_locations"] if loc["bound"]]
    if len(evidence) != 1 or (
        evidence[0]["segment_id"], evidence[0]["json_pointer"], evidence[0]["value_sha256"],
        evidence[0]["match_offsets"]
    ) != (candidate["segment_id"], candidate["json_pointer"], candidate["value_sha256"], [candidate["start"]]):
        return {**result, "error": "frozen routing source evidence mismatch"}
    stored_anchor = dict(anchor) if anchor is not None else candidate
    valid, reason = _verify_anchor(payload, item, stored_anchor)
    if not valid:
        return {**result, "error": reason}
    return {**result, "verdict": "PASS_BOUND_LITERAL_SPAN_V0_1",
            "anchor": stored_anchor, "verification": reason}


def replay(*, source_anchor_replay: Path, routing: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output already exists; choose a fresh sibling path")
    if _sha256(routing) != ROUTING_SHA256 or _sha256(source_anchor_replay) != REPLAY_SHA256:
        raise ValueError("frozen routing/replay identity mismatch")
    source_report = _read_json(source_anchor_replay)
    routing_report = _read_json(routing)
    if routing_report["identity"]["replay"] != "sha256:" + REPLAY_SHA256:
        raise ValueError("routing report replay binding mismatch")
    target_by_key = _select_targets(routing_report)
    input_hashes = {routing.as_posix(): ROUTING_SHA256}
    for entry in routing_report["inputs"]:
        path = Path(entry["path"])
        if _sha256(path) != entry["sha256"]:
            raise ValueError(f"immutable input identity mismatch: {path}")
        input_hashes[path.as_posix()] = entry["sha256"]
    source_inputs = source_report["inputs"]
    manifests = [Path(path) for path in source_inputs["manifests"]]
    roots = [Path(path) for path in source_inputs["roots"]]
    units = _units(manifests)
    before_root_hashes = _all_root_hashes(roots)
    input_hashes.update(before_root_hashes)
    # Bind both direct policies and the shared structural validation code.
    dependencies = ["historical_bound_literal_span_sibling.py", "historical_atomic_replay_audit.py",
                    "historical_atomic_repair_routing.py", "semantic_two_stage_experiment.py",
                    "semantic_compiler_u1.py", "semantic_live_runner.py"]
    policy_hashes = {name: _sha256(Path(__file__).with_name(name)) for name in dependencies}
    canonical_paths: dict[str, list[Path]] = {}
    for name, digest in before_root_hashes.items():
        path = Path(name)
        if path.name == "canonical_output.json":
            canonical_paths.setdefault(digest, []).append(path)
    unit_results = []
    item_results = []
    replayed_strict_pass = 0
    strict_regressions = []
    v01_regressions = []
    for source_row in source_report["rows"]:
        uid = source_row["unit_id"]
        payload = units[uid]["payload"]
        if source_row["source_payload_identity"] != "sha256:" + sha256_json(payload):
            raise ValueError(f"payload identity mismatch for {uid}")
        bound_identity = {
            "policy": POLICY, "implementation": policy_hashes[dependencies[0]],
            "validation_dependencies": policy_hashes, "routing_report": "sha256:" + ROUTING_SHA256,
            "source_anchor_replay": "sha256:" + REPLAY_SHA256,
            "source_payload": source_row["source_payload_identity"],
            "canonical_output": ("sha256:" + source_row["canonical_output_sha256"]
                                 if source_row.get("canonical_output_sha256") else None),
        }
        base_unit = {"unit_id": uid, "title": source_row.get("title"),
                     "historical_runtime_verdict": source_row["historical_runtime_verdict"],
                     "strict_replay_verdict": source_row["current_replay_verdict"],
                     "source_anchor_v01_verdict": source_row["source_anchor_v01_verdict"],
                     "verdict_identity": bound_identity, "semantic_correctness": "UNKNOWN",
                     "selected_target": uid in TARGET_ITEMS}
        if source_row.get("failure_class") == "no_canonical":
            unit_results.append({**base_unit, "literal_span_sibling_verdict": "UNKNOWN",
                                 "error": "canonical output unavailable", "item_verdicts": []})
            continue
        if source_row["validator_identity"] != (
                "validate_atomic_inventory:sha256:" + policy_hashes["semantic_two_stage_experiment.py"]):
            raise ValueError("frozen strict validator implementation mismatch")
        if source_row["source_anchor_v01_verdict_identity"]["policy_implementation_identity"] != (
                "sha256:" + policy_hashes["historical_atomic_replay_audit.py"]):
            raise ValueError("frozen v0.1 implementation mismatch")
        matched = canonical_paths.get(source_row["canonical_output_sha256"], [])
        canonical_path = next((path for path in matched
                               if _read_json(path.parent / "terminal.json").get("unit_id") == uid), None)
        if canonical_path is None:
            raise ValueError(f"canonical output identity mismatch for {uid}")
        terminal = _read_json(canonical_path.parent / "terminal.json")
        if terminal["disposition"] != source_row["historical_runtime_verdict"]:
            raise ValueError("historical runtime disposition mismatch")
        canonical = _read_json(canonical_path)
        strict_status, strict_error = "PASS", ""
        try:
            validate_atomic_inventory(canonical, payload)
        except ValueError as exc:
            strict_status, strict_error = "FAIL", str(exc)
        if strict_status != source_row["current_replay_verdict"] or strict_error != source_row["validator_error"]:
            strict_regressions.append(uid)
        if source_row["current_replay_verdict"] == "PASS" and strict_status == "PASS":
            replayed_strict_pass += 1
        failed_classes = {row["item"]["local_id"]: row["failure_class"] for row in source_row["failed_items"]}
        baseline = _validate_with_source_anchor(canonical, payload, failed_item_classes=failed_classes)
        if (baseline["status"] != source_row["source_anchor_v01_verdict"]
                or baseline["item_verdicts"] != source_row["source_anchor_v01_item_verdicts"]):
            v01_regressions.append(uid)
        original_items = {item["local_id"]: item for item in canonical["items"]}
        if len(original_items) != len(canonical["items"]):
            raise ValueError("duplicate canonical local IDs")
        new_verdicts = copy.deepcopy(baseline["item_verdicts"])
        new_count = 0
        if uid in TARGET_ITEMS:
            if {row["local_id"] for row in new_verdicts if row["verdict"] == "FAIL"} != TARGET_ITEMS[uid]:
                raise ValueError("target unit remaining FAIL set changed")
            for index, existing in enumerate(new_verdicts):
                key = (uid, existing["local_id"])
                if key not in target_by_key:
                    continue
                target = target_by_key[key]
                if (target["identity"]["payload"] != source_row["source_payload_identity"]
                        or target["identity"]["canonical_output"] != "sha256:" + source_row["canonical_output_sha256"]):
                    raise ValueError("routing source/output identity mismatch")
                verdict = _approve_item(payload, original_items[key[1]], target)
                new_verdicts[index] = verdict
                new_count += verdict["verdict"] == "PASS_BOUND_LITERAL_SPAN_V0_1"
                item_results.append({
                    "unit_id": uid, "title": source_row.get("title"), "item": original_items[key[1]],
                    "historical_runtime_verdict": source_row["historical_runtime_verdict"],
                    "strict_item_verdict": "FAIL", "source_anchor_v01_item_verdict": existing,
                    "identity": {**bound_identity, "item": target["identity"]["item"]}, **verdict,
                })
        sibling_status = "FAIL" if any(row["verdict"] == "FAIL" for row in new_verdicts) else "PASS"
        unit_results.append({**base_unit, "strict_replay_check": {"status": strict_status, "error": strict_error},
                             "source_anchor_v01_replay_check": baseline["status"],
                             "literal_span_sibling_verdict": sibling_status,
                             "new_recovered_item_count": new_count, "item_verdicts": new_verdicts})

    if strict_regressions or v01_regressions or replayed_strict_pass != 107:
        raise ValueError(f"baseline replay drift: strict={strict_regressions}, v01={v01_regressions}")
    # Negative controls use unchanged verdicts, without attempting any repair.
    disallowed = [row for row in routing_report["items"] if row["scope"] == "remaining_failed"
                  and row["failure_class"] in DISALLOWED_CLASSES]
    unchanged_by_key = {(row["unit_id"], item["local_id"]): item["verdict"]
                        for row in unit_results for item in row["item_verdicts"]}
    leaks = [row for row in disallowed if unchanged_by_key[(row["unit_id"], row["local_id"])] != "FAIL"]
    outside_changes = [(row["unit_id"], item["local_id"]) for row in unit_results for item in row["item_verdicts"]
                       if item["verdict"] == "PASS_BOUND_LITERAL_SPAN_V0_1"
                       and (row["unit_id"], item["local_id"]) not in target_by_key]
    if leaks or outside_changes:
        raise ValueError("out-of-scope or disallowed item admission")
    after_root_hashes = _all_root_hashes(roots)
    if before_root_hashes != after_root_hashes:
        raise ValueError("immutable historical root contents/file set changed")
    for name, expected in input_hashes.items():
        if _sha256(Path(name)) != expected:
            raise ValueError(f"immutable input changed during replay: {name}")
    for name, expected in policy_hashes.items():
        if _sha256(Path(__file__).with_name(name)) != expected:
            raise ValueError("validator/policy implementation changed during replay")
    counts = Counter(row["literal_span_sibling_verdict"] for row in unit_results)
    remaining_classes = Counter(row.get("failure_class") for unit in unit_results
                                for row in unit["item_verdicts"] if row["verdict"] == "FAIL")
    recovered_units = [row for row in unit_results
                       if row["selected_target"] and row["literal_span_sibling_verdict"] == "PASS"]
    result = {
        "schema_version": POLICY, "policy_identity": policy_hashes,
        "inputs": {"routing_report": {"path": routing.as_posix(), "sha256": ROUTING_SHA256},
                   "source_anchor_replay": {"path": source_anchor_replay.as_posix(), "sha256": REPLAY_SHA256}},
        "policy": {"anchor_kind": ANCHOR_KIND, "span_unit": "unicode_codepoint", "span_end": "exclusive",
                   "html_attribute_extension": False, "provider_calls": 0, "network_calls": 0,
                   "semantic_correctness": "UNKNOWN"},
        "before": source_report["counts"],
        "after_sibling": {"logical": source_report["counts"]["logical"], "pass": counts["PASS"],
                          "canonical_fail": counts["FAIL"], "no_canonical_unknown": counts["UNKNOWN"],
                          "provider_omitted": source_report["counts"]["provider_omitted"],
                          "recovered_units": len(recovered_units),
                          "new_recovered_items": sum(row["verdict"] == "PASS_BOUND_LITERAL_SPAN_V0_1" for row in item_results),
                          "remaining_failed_items": sum(remaining_classes.values()),
                          "remaining_failure_classes": dict(remaining_classes),
                          "inherited_v01_items_in_targets": sum(item["verdict"] == "PASS_SOURCE_ANCHOR_V0_1"
                              for row in unit_results if row["selected_target"] for item in row["item_verdicts"])},
        "controls": {"strict_canonical_replays": len(unit_results) - counts["UNKNOWN"],
                     "strict_PASS_replayed": replayed_strict_pass,
                     "strict_replay_drift": strict_regressions, "v01_replay_drift": v01_regressions,
                     "prior_strict_PASS_regressions": 0, "fabricated_or_metadata_misadmissions": len(leaks),
                     "disallowed_failed_item_controls": len(disallowed),
                     "out_of_scope_admissions": outside_changes},
        "immutable_inputs": {"unchanged": True, "file_count": len(input_hashes),
                             "historical_root_file_count": len(before_root_hashes),
                             "before_hashes": input_hashes, "after_root_hashes": after_root_hashes},
        "items": item_results, "units": unit_results,
        "historical_runtime_verdicts_preserved": True, "strict_replay_verdicts_preserved": True,
        "source_anchor_v01_verdicts_preserved": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    return result
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-anchor-replay", type=Path, required=True)
    parser.add_argument("--routing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(source_anchor_replay=args.source_anchor_replay, routing=args.routing,
                    output=args.output)
    print(json.dumps({"before": result["before"], "after_sibling": result["after_sibling"],
                      "provider_calls": 0, "network_calls": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
