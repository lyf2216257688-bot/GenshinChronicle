"""Read-only, provider-free repair-routing research over a frozen replay.

Routes are not new validator verdicts or semantic approval. No output is repaired.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

from genshin_corpus.canonical.fingerprints import sha256_json
from .historical_atomic_replay_audit import _read_json, _scalar_paths
from .semantic_two_stage_experiment import _source_texts


POLICY = "phase05-w2-mechanical-repair-routing-0.1"
TECH_KEYS = {"component", "moduleName", "tab_name", "layout_", "record_key",
             "schema_version", "segment_id", "section", "flags", "shape",
             "content_role", "text_segments", "references", "image", "img"}
TECH_ATTRIBUTES = {"data-entry-id", "data-entry-img", "data-entry-link",
                   "data-entry-ratio", "data-entry-status"}
NATURAL_FIELDS = {"text", "rich_text", "dialogue", "speaker", "option"}


class _HTMLFacts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append({"tag": tag, "attributes": dict(attrs),
                          "raw_starttag": self.get_starttag_text()})

    handle_startendtag = handle_starttag


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _tokens(pointer: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]


def _technical(pointer: str) -> bool:
    return bool(set(_tokens(pointer)) & TECH_KEYS)


def source_evidence(payload: Mapping[str, Any], quote: str, refs: list[str]) -> dict[str, Any]:
    texts = _source_texts(payload)
    evidence: dict[str, Any] = {
        "bound_natural_segments": [ref for ref in refs if quote in texts.get(ref, "")],
        "unbound_natural_segments": [ref for ref, text in texts.items() if ref not in refs and quote in text],
        "content_locations": [], "technical_locations": [], "serialized_technical_assertions": [],
    }
    parsed = None
    for fragment in (quote, "{" + quote + "}"):
        try:
            parsed = json.loads(fragment)
            break
        except (ValueError, TypeError):
            pass
    if isinstance(parsed, dict) and parsed and set(parsed) <= TECH_KEYS:
        for segment in payload["segments"]:
            scalars = _scalar_paths(segment)
            if all(any(_tokens(pointer)[-1] == key and scalar == expected
                       for pointer, scalar in scalars if pointer)
                   for key, expected in parsed.items()):
                evidence["serialized_technical_assertions"].append({
                    "segment_id": segment["segment_id"], "fields": parsed})
    for segment in payload["segments"]:
        for pointer, scalar in _scalar_paths(segment):
            if not isinstance(scalar, str) or quote not in scalar:
                continue
            technical = _technical(pointer)
            tokens = _tokens(pointer)
            # Only source-text fields and decoded source values are content candidates.
            content = not technical and ("decoded" in tokens or tokens[-1] in NATURAL_FIELDS)
            if not (technical or content):
                continue
            starts = []
            pos = scalar.find(quote)
            while pos != -1:
                starts.append(pos)
                pos = scalar.find(quote, pos + 1)
            row = {"segment_id": segment["segment_id"], "json_pointer": pointer,
                   "bound": segment["segment_id"] in refs, "exact_scalar": scalar == quote,
                   "value_sha256": sha256(scalar.encode("utf-8")).hexdigest(),
                   "match_offsets": starts, "offset_unit": "unicode_codepoint",
                   "value_excerpt": scalar[:240], "html_tag_matches": []}
            if content and "<" in scalar:
                parser = _HTMLFacts()
                parser.feed(scalar)
                row["html_tag_matches"] = [tag for tag in parser.tags if quote in tag["raw_starttag"]]
            evidence["technical_locations" if technical else "content_locations"].append(row)
    return evidence


def _technical_quote(quote: str, evidence: Mapping[str, Any]) -> str | None:
    if evidence["serialized_technical_assertions"]:
        return "serialized technical key/value assertion verified against actual source fields"
    parser = _HTMLFacts()
    parser.feed("<span " + quote + ">")
    if parser.tags:
        attrs = parser.tags[0]["attributes"]
        if attrs and set(attrs) <= TECH_ATTRIBUTES:
            source_matches = [tag for loc in evidence["content_locations"]
                              for tag in loc["html_tag_matches"]]
            if source_matches:
                return "source HTML resource/UI attribute(s): " + ", ".join(sorted(attrs))
    resource_locations = [loc for loc in evidence["technical_locations"] if loc["exact_scalar"]
                          and _tokens(loc["json_pointer"])[-1] in {"image", "img", "component", "moduleName", "layout_"}]
    if resource_locations and not evidence["content_locations"]:
        return "exact quote is a resource/UI scalar, not natural text"
    return None


def classify(item_row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    item = item_row["item"]
    quote = item_row["source_quote"]
    if not isinstance(quote, str) or not quote:
        return {"route": "UNKNOWN", "reason": "missing quote", "evidence": {},
                "operation_candidate": None}
    evidence = source_evidence(payload, quote, item["source_segment_ids"])
    technical = _technical_quote(quote, evidence)
    if technical:
        route, reason, operation = "NON_SEMANTIC_METADATA", technical, "retain_in_inactive_metadata_ledger"
    elif item_row["failure_class"] in {
        "fabricated_or_unbound_quote", "metadata_serialized_json_contamination",
    }:
        # A repair route cannot retroactively admit these classes, even if a
        # fresh search finds literal content. The existing rejection stays intact.
        route, reason, operation = ("MODEL_REPAIRABLE",
                                   "output quote/binding requires fresh source-bound output; success unproven",
                                   "regenerate_source_binding_or_omit_with_reason")
    else:
        bound = [loc for loc in evidence["content_locations"] if loc["bound"]]
        if len(bound) == 1 and len(bound[0]["match_offsets"]) == 1:
            tag_matches = bound[0]["html_tag_matches"]
            if tag_matches and all("data-entry-name" in tag["attributes"] and
                                   "data-entry-amount" in tag["attributes"] for tag in tag_matches):
                operation = "research_html_name_amount_attribute_anchor"
            else:
                operation = "research_bound_literal_span_anchor"
            route, reason = "CONTRACT_FIXABLE", "one exact literal span in one bound source content value"
        elif bound:
            route, reason, operation = "UNKNOWN", "multiple bound source values or occurrences; anchor selection ambiguous", None
        elif evidence["unbound_natural_segments"]:
            route, reason, operation = ("MODEL_REPAIRABLE", "natural quote exists only outside declared supporting refs",
                                       "regenerate_source_segment_binding")
        elif evidence["content_locations"]:
            route, reason, operation = "UNKNOWN", "content exists outside refs but supporting identity is not established", None
        else:
            route, reason, operation = "UNKNOWN", "no eligible source-content location established", None
    return {"route": route, "reason": reason, "evidence": evidence,
            "operation_candidate": operation,
            "deterministic_binding_candidate": route == "CONTRACT_FIXABLE",
            "semantic_correctness": "UNKNOWN", "mechanically_approved_by_routing": False}


def aggregate(routes: Counter[str]) -> str:
    if "UNKNOWN" in routes:
        return "UNKNOWN"
    if "MODEL_REPAIRABLE" in routes:
        return "MODEL_REPAIRABLE"
    if "NON_SEMANTIC_METADATA" in routes:
        return "NON_SEMANTIC_METADATA"
    if "CONTRACT_FIXABLE" in routes:
        return "CONTRACT_FIXABLE"
    return "NO_REMAINING_FAILED_ITEMS"


def investigate(*, replay: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("routing artifact already exists; use a fresh sibling path")
    report = _read_json(replay)
    inputs = report["inputs"]
    tracked_hashes = {replay: _sha256(replay)}
    ledger_path = Path(inputs["classification_ledger"])
    tracked_hashes[ledger_path] = _sha256(ledger_path)
    if tracked_hashes[ledger_path] != inputs["classification_ledger_sha256"]:
        raise ValueError("classification ledger identity changed")
    units: dict[str, Mapping[str, Any]] = {}
    for manifest_name in inputs["manifests"]:
        path = Path(manifest_name)
        tracked_hashes[path] = _sha256(path)
        manifest = _read_json(path)
        if manifest["identity"] != sha256_json(manifest["contract"]):
            raise ValueError("manifest identity mismatch")
        for unit in manifest["contract"]["units"]:
            uid = unit["compilation_unit_id"]
            if uid in units and units[uid]["payload"] != unit["payload"]:
                raise ValueError("conflicting unit payloads")
            units.setdefault(uid, unit)
    artifacts: dict[str, list[Path]] = defaultdict(list)
    for root_name in inputs["roots"]:
        for terminal in sorted(Path(root_name).glob("units/*/attempt-*/terminal.json")):
            tracked_hashes[terminal] = _sha256(terminal)
            body = _read_json(terminal)
            artifacts[body["unit_id"]].append(terminal.parent)
    item_rows = []
    unit_rows = []
    for unit_row in report["rows"]:
        uid = unit_row["unit_id"]
        payload = units[uid]["payload"]
        if "source_payload_identity" in unit_row and unit_row["source_payload_identity"] != "sha256:" + sha256_json(payload):
            raise ValueError(f"payload identity mismatch for {uid}")
        if unit_row.get("failure_class") == "no_canonical":
            unit_rows.append({"unit_id": uid, "routing_verdict": "UNKNOWN", "reason": "no canonical",
                              "route_counts": {}, "remaining_failed_item_count": None,
                              "historical_runtime_verdict": unit_row["historical_runtime_verdict"],
                              "strict_replay_verdict": unit_row["current_replay_verdict"],
                              "source_anchor_unit_verdict": unit_row["source_anchor_v01_verdict"]})
            continue
        matches = []
        for attempt in artifacts[uid]:
            canonical = attempt / "canonical_output.json"
            if canonical.exists():
                tracked_hashes[canonical] = _sha256(canonical)
                if tracked_hashes[canonical] == unit_row["canonical_output_sha256"]:
                    matches.append(canonical)
        if not matches:
            raise ValueError(f"canonical output binding failed for {uid}")
        canonical = matches[0]
        terminal_body = _read_json(canonical.parent / "terminal.json")
        if terminal_body.get("disposition") != unit_row["historical_runtime_verdict"]:
            raise ValueError("historical runtime disposition mismatch")
        canonical_body = _read_json(canonical)
        canonical_items = {item["local_id"]: item for item in canonical_body["items"]}
        if len(canonical_items) != len(canonical_body["items"]):
            raise ValueError("duplicate canonical local_id")
        evidence_files = []
        for name in ("terminal.json", "validation.json", "request_metadata.json"):
            path = canonical.parent / name
            if path.exists():
                tracked_hashes[path] = _sha256(path)
                _read_json(path)
                evidence_files.append({"path": path.as_posix(), "sha256": tracked_hashes[path]})
        verdicts = {row["local_id"]: row for row in unit_row["source_anchor_v01_item_verdicts"]}
        if set(verdicts) != set(canonical_items) or len(verdicts) != len(unit_row["source_anchor_v01_item_verdicts"]):
            raise ValueError("replay item accounting mismatch")
        legacy_ids = [row["item"]["local_id"] for row in unit_row["failed_items"]]
        if len(legacy_ids) != len(set(legacy_ids)) or set(legacy_ids) != {
            key for key, row in verdicts.items() if row["verdict"] in {"FAIL", "PASS_SOURCE_ANCHOR_V0_1"}
        }:
            raise ValueError("failed item accounting mismatch")
        routes: Counter[str] = Counter()
        all_routes: Counter[str] = Counter()
        for failure in unit_row["failed_items"]:
            item = failure["item"]
            item_id = item["local_id"]
            if canonical_items.get(item_id) != item or failure["source_quote"] != item["qualifiers"]["source_quote"]:
                raise ValueError(f"failed item identity mismatch for {uid}/{item_id}")
            verdict = verdicts[item_id]["verdict"]
            result = classify(failure, payload)
            scope = "remaining_failed" if verdict == "FAIL" else "already_anchor_approved"
            if scope == "already_anchor_approved":
                if verdict != "PASS_SOURCE_ANCHOR_V0_1":
                    raise ValueError("unexpected failed-item replay disposition")
                result.update(route="CONTRACT_FIXABLE", reason="preserve existing v0.1 sibling approval; not rerouted",
                              operation_candidate="retain_existing_source_anchor_v01",
                              deterministic_binding_candidate=True)
            all_routes[result["route"]] += 1
            if scope == "remaining_failed":
                routes[result["route"]] += 1
            item_rows.append({"unit_id": uid, "record_key": unit_row["record_key"],
                              "title": unit_row["title"], "local_id": item_id,
                              "item": item, "source_type": unit_row["source_type"],
                              "source_quote": failure["source_quote"], "legacy_failure": failure,
                              "failure_class": failure["failure_class"], "scope": scope,
                              "strict_validator_error": unit_row["validator_error"],
                              "source_anchor_item_verdict": verdicts[item_id],
                              "historical_runtime_verdict": unit_row["historical_runtime_verdict"],
                              "strict_replay_verdict": unit_row["current_replay_verdict"],
                              "identity": {"payload": unit_row["source_payload_identity"],
                                           "canonical_output": "sha256:" + tracked_hashes[canonical],
                                           "canonical_path": canonical.as_posix(),
                                           "item": "sha256:" + sha256_json(item)},
                              "attempt_evidence": evidence_files, **result})
        unit_rows.append({"unit_id": uid, "title": unit_row["title"],
                          "routing_verdict": aggregate(routes), "route_counts": dict(routes),
                          "route_set": sorted(routes), "all_legacy_failed_route_counts": dict(all_routes),
                          "remaining_failed_item_count": sum(routes.values()),
                          "approved_natural_item_count": sum(row["verdict"] == "PASS_NATURAL_QUOTE" for row in verdicts.values()),
                          "approved_anchor_item_count": sum(row["verdict"] == "PASS_SOURCE_ANCHOR_V0_1" for row in verdicts.values()),
                          "historical_runtime_verdict": unit_row["historical_runtime_verdict"],
                          "strict_replay_verdict": unit_row["current_replay_verdict"],
                          "source_anchor_unit_verdict": unit_row["source_anchor_v01_verdict"],
                          "mechanical_routing_verdict": "FAIL" if routes else "PASS",
                          "semantic_correctness": "UNKNOWN"})
    remaining = [row for row in item_rows if row["scope"] == "remaining_failed"]
    if len(remaining) != report["source_anchor_v01"]["failed_items"] or len(item_rows) - len(remaining) != report["source_anchor_v01"]["recovered_items"]:
        raise ValueError("replay total accounting mismatch")
    by_class: dict[str, Counter[str]] = defaultdict(Counter)
    for row in remaining:
        by_class[row["failure_class"]][row["route"]] += 1
    for path, identity in tracked_hashes.items():
        if _sha256(path) != identity:
            raise ValueError(f"immutable artifact changed during investigation: {path}")
    result = {
        "schema_version": POLICY, "provider_calls": 0, "network_calls": 0,
        "identity": {"replay": "sha256:" + tracked_hashes[replay],
                     "policy_implementation": "sha256:" + _sha256(Path(__file__))},
        "inputs": [{"path": path.as_posix(), "sha256": identity} for path, identity in tracked_hashes.items()],
        "input_hashes_unchanged": True, "original_accounting": report["counts"],
        "counts": {"legacy_failed_items": len(item_rows), "remaining_failed_items": len(remaining),
                   "already_anchor_approved_items": len(item_rows) - len(remaining),
                   "remaining_item_routes": dict(Counter(row["route"] for row in remaining)),
                   "remaining_class_routes": {key: dict(counts) for key, counts in by_class.items()},
                   "remaining_operation_candidates": dict(Counter(row["operation_candidate"] or "NONE" for row in remaining)),
                   "remaining_reason_counts": dict(Counter(row["reason"] for row in remaining)),
                   "unit_routing_verdicts": dict(Counter(row["routing_verdict"] for row in unit_rows)),
                   "remaining_unit_route_presence": {route: sum(route in row["route_counts"] for row in unit_rows)
                       for route in ("CONTRACT_FIXABLE", "MODEL_REPAIRABLE", "UNKNOWN", "NON_SEMANTIC_METADATA")},
                   "failed_unit_route_sets": dict(Counter("+".join(row.get("route_set", [])) for row in unit_rows
                       if row.get("remaining_failed_item_count"))),
                   "provider_omitted": report["counts"]["provider_omitted"]},
        "items": item_rows, "units": unit_rows,
        "repair_queue_design": {
            "schema_version": "phase05-w2-repair-queue-proposal-0.1", "execution_authorized": False,
            "fields": ["queue_id", "routing_policy_identity", "unit_id", "record_key", "local_id",
                       "original_item_identity", "payload_identity", "canonical_output_identity",
                       "canonical_path", "replay_identity", "historical_runtime_verdict",
                       "strict_replay_verdict", "source_anchor_item_verdict", "failure_class",
                       "route", "reason", "source_segment_ids", "source_quote", "evidence_locations",
                       "dependency_local_ids", "operation_candidate", "output_disposition",
                       "semantic_correctness", "execution_authorized"],
            "output_disposition": "new sibling artifact only; never mutate or silently drop original",
            "model_repairable_meaning": "output-level intervention candidate, not proven model success or semantic truth",
            "contract_fixable_meaning": "source binding can be located deterministically; no new approval in this investigation",
            "dependency_policy": "retain whole output/envelope context and all referenced local IDs",
            "unknown_policy": "blocked; do not auto-enqueue for model or contract admission",
            "metadata_policy": "quarantine with evidence, never silently delete or make PASS",
            "example": ({"queue_id": sha256_json({"policy": POLICY, "replay": tracked_hashes[replay],
                                                  "unit_id": remaining[0]["unit_id"], "local_id": remaining[0]["local_id"]}),
                         "unit_id": remaining[0]["unit_id"], "record_key": remaining[0]["record_key"],
                         "local_id": remaining[0]["local_id"], "identity": remaining[0]["identity"],
                         "source_segment_ids": remaining[0]["item"]["source_segment_ids"],
                         "source_quote": remaining[0]["source_quote"], "route": remaining[0]["route"],
                         "reason": remaining[0]["reason"], "evidence_locations": remaining[0]["evidence"],
                         "semantic_correctness": "UNKNOWN", "execution_authorized": False} if remaining else None),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = investigate(replay=args.replay, output=args.output)
    print(json.dumps({"counts": result["counts"], "provider_calls": 0, "network_calls": 0}))


if __name__ == "__main__":
    main()
