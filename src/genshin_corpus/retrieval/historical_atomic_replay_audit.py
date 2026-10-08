"""Provider-free audit of historical atomic-inventory replay failures.

This module only reads immutable manifests and run roots.  It intentionally
does not change the production schema, prompt, or validator.  The report keeps
historical runtime dispositions separate from the current replay verdict.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from genshin_corpus.retrieval.semantic_two_stage_experiment import validate_atomic_inventory


ANALYSIS_SCHEMA = "phase05-w2-historical-atomic-replay-audit-0.1"
SOURCE_ANCHOR_POLICY = "phase05-w2-source-anchor-0.1"
METADATA_KEYS = {"text_segments", "tab_name", "moduleName", "layout_", "record_key", "schema_version"}
ANCHOR_EXCLUDED_KEYS = {"moduleName", "tab_name", "layout_", "record_key", "schema_version", "component"}
HTML_RE = re.compile(r"<[^>]+>")


class _TextProjection(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _flatten_strings(value: Any, *, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}/{key}" if path else f"/{key}"
            if isinstance(child, str):
                out.append((child_path, child))
            else:
                out.extend(_flatten_strings(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            out.extend(_flatten_strings(child, path=f"{path}/{index}"))
    return out


def _source_texts(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for segment in payload.get("segments", []):
        parts: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if key in {"text", "rich_text", "dialogue", "speaker", "option"} and isinstance(child, str):
                        if key == "rich_text":
                            parts.append(child)
                        parser = _TextProjection()
                        parser.feed(child)
                        parts.append("".join(parser.parts))
                    elif isinstance(child, (Mapping, list)):
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(segment)
        result[str(segment["segment_id"])] = "\n".join(parts)
    return result


def _json_pointer_get(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be absolute")
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            current = current[token]
        elif isinstance(current, list):
            current = current[int(token)]
        else:
            raise ValueError("JSON Pointer traverses a scalar")
    return current


def _json_pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _scalar_paths(value: Any, pointer: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, Mapping):
        out: list[tuple[str, Any]] = []
        for key, child in value.items():
            out.extend(_scalar_paths(child, f"{pointer}/{_json_pointer_escape(str(key))}"))
        return out
    if isinstance(value, list):
        out = []
        for index, child in enumerate(value):
            out.extend(_scalar_paths(child, f"{pointer}/{index}"))
        return out
    return [(pointer, value)]


def _structured_anchor(payload: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Derive a minimal, auditable anchor only for an exact decoded scalar value."""
    quote = item.get("qualifiers", {}).get("source_quote")
    if not isinstance(quote, str) or not quote:
        return None
    refs = set(item.get("source_segment_ids", []))
    candidates = []
    for segment in payload.get("segments", []):
        segment_id = segment.get("segment_id")
        if segment_id not in refs:
            continue
        value = segment.get("value")
        if not isinstance(value, Mapping) or "decoded" not in value:
            continue
        for pointer, scalar in _scalar_paths(value["decoded"]):
            leaf = pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
            if leaf in ANCHOR_EXCLUDED_KEYS:
                continue
            if isinstance(scalar, str) and scalar == quote:
                candidates.append({"policy": SOURCE_ANCHOR_POLICY, "kind": "decoded_scalar",
                                   "segment_id": segment_id, "json_pointer": f"/value/decoded{pointer}",
                                   "value_sha256": sha256(scalar.encode("utf-8")).hexdigest(),
                                   "source_quote": quote})
    if len(candidates) != 1:
        return None
    return candidates[0]


def _validate_with_source_anchor(
    output: Mapping[str, Any], payload: Mapping[str, Any], *,
    failed_item_classes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the unchanged strict validator, then permit only exact decoded scalars."""
    from genshin_corpus.retrieval.semantic_compiler_u1 import (
        semantic_segment_binding_metadata, validate_semantic_output_envelope,
    )
    from genshin_corpus.retrieval.semantic_live_runner import validate_b_v2_navigation_references

    validate_semantic_output_envelope(output, expected_segment_ids=[s["segment_id"] for s in payload["segments"]],
                                      segment_metadata=semantic_segment_binding_metadata(payload))
    validate_b_v2_navigation_references(output)
    texts = _source_texts(payload)
    allowed = {"source_quote", "attribution", "modality", "polarity", "time", "condition"}
    item_verdicts: list[dict[str, Any]] = []
    unresolved = False
    for item in output["items"]:
        qualifiers = item["qualifiers"]
        quote = qualifiers.get("source_quote")
        if not isinstance(quote, str) or not quote:
            raise ValueError(f"atomic item {item['local_id']} lacks source_quote")
        if not set(qualifiers).issubset(allowed):
            raise ValueError(f"atomic item {item['local_id']} uses unsupported qualifier field")
        refs = item["source_segment_ids"]
        if any(quote in texts[segment_id] for segment_id in refs):
            item_verdicts.append({"local_id": item["local_id"], "source_quote": quote,
                                  "verdict": "PASS_NATURAL_QUOTE"})
            continue
        # Eligibility comes from the immutable audit ledger, not a looser
        # second search that could promote a quote from another payload field.
        item_class = (failed_item_classes or {}).get(item["local_id"], "unclassified")
        if item_class != "structured_value_quote_projection_boundary":
            unresolved = True
            item_verdicts.append({"local_id": item["local_id"], "source_quote": quote,
                                  "verdict": "FAIL", "failure_class": item_class,
                                  "error": f"not eligible for Source Anchor v0.1: {item_class}"})
            continue
        anchor = _structured_anchor(payload, item)
        if anchor is None:
            unresolved = True
            item_verdicts.append({"local_id": item["local_id"], "source_quote": quote,
                                  "verdict": "FAIL", "failure_class": item_class,
                                  "error": "no unique decoded-scalar anchor"})
            continue
        segment = next(s for s in payload["segments"] if s["segment_id"] == anchor["segment_id"])
        resolved = _json_pointer_get(segment, anchor["json_pointer"])
        if not isinstance(resolved, str) or resolved != quote or sha256(resolved.encode("utf-8")).hexdigest() != anchor["value_sha256"]:
            unresolved = True
            item_verdicts.append({"local_id": item["local_id"], "source_quote": quote,
                                  "verdict": "FAIL", "failure_class": item_class,
                                  "error": "structured anchor integrity mismatch"})
            continue
        item_verdicts.append({"local_id": item["local_id"], "source_quote": quote,
                              "verdict": "PASS_SOURCE_ANCHOR_V0_1", **anchor})
    return {"status": "FAIL" if unresolved else "PASS", "semantic_acceptance": "UNASSESSED",
            "item_verdicts": item_verdicts,
            "anchors": [row for row in item_verdicts if row.get("verdict") == "PASS_SOURCE_ANCHOR_V0_1"],
            "item_count": len(output["items"]), "provider_calls": 0, "network_calls": 0}


def _source_evidence(payload: Mapping[str, Any], quote: str | None, refs: list[str] | None = None) -> dict[str, Any]:
    if not isinstance(quote, str) or not quote:
        return {"quote_presence": "nowhere", "matched_paths": [], "source_types": []}
    source_texts = _source_texts(payload)
    bound_refs = refs or list(source_texts)
    natural = [f"/{segment_id}" for segment_id in bound_refs
               if segment_id in source_texts and quote in source_texts[segment_id]]
    structured: list[str] = []
    serialized: list[str] = []
    serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    serialized_quote = quote in serialized_payload and (
        quote.startswith('"') or '"' in quote or ":" in quote or "{" in quote or "}" in quote
    )
    for segment in payload.get("segments", []):
        for path, value in _flatten_strings(segment):
            if quote not in value:
                continue
            if any(key in path for key in METADATA_KEYS):
                serialized.append(path)
            elif HTML_RE.search(value) or "rich_text" in path or "decoded" in path:
                structured.append(path)
            else:
                natural.append(path)
    if serialized_quote and not (natural or structured or serialized):
        serialized.append("/serialized_payload")
    if natural:
        presence = "natural_text"
    elif structured:
        presence = "structured_value"
    elif serialized or serialized_quote:
        presence = "serialized_metadata"
    else:
        presence = "nowhere"
    source_types = [name for present, name in ((natural, "natural_text"),
                                                (structured, "structured_value"),
                                                (serialized, "serialized_metadata")) if present]
    return {"quote_presence": presence, "matched_paths": natural + structured + serialized,
            "source_types": source_types}


def _source_type(payload: Mapping[str, Any]) -> str:
    kinds = {str(s.get("value", {}).get("kind")) for s in payload.get("segments", []) if isinstance(s, Mapping)}
    components = {str(s.get("value", {}).get("component")) for s in payload.get("segments", []) if isinstance(s, Mapping)}
    if kinds <= {"map_desc"}:
        return "pure_media_map_desc"
    if any(k in {"dialogue", "dialogue_group"} for k in kinds):
        return "dialogue"
    if any(k in {"rich_text", "structured_observation"} for k in kinds) or "collapse_panel" in components:
        return "rich_text_or_structured"
    return "other"


def _failure_class(error: str, evidence: Mapping[str, Any]) -> tuple[str, bool]:
    if "requires an explicit predicate" in error:
        return "relation_missing_predicate", False
    if "local reference" in error or "participant" in error or "subject_ref" in error or "object_ref" in error:
        return "local_reference_error", False
    presence = evidence.get("quote_presence")
    if presence == "serialized_metadata":
        return "metadata_serialized_json_contamination", True
    if presence == "structured_value":
        return "structured_value_quote_projection_boundary", True
    if presence == "nowhere":
        return "fabricated_or_unbound_quote", False
    if "source_quote" in error:
        return "source_projection_or_quote_binding", False
    if "unsupported qualifier" in error:
        return "unsupported_qualifier_field", False
    return "other_validator_failure", False


def _attempt_for(root: Path, unit_id: str) -> tuple[Path | None, dict[str, Any] | None]:
    for directory in (root / "units").glob("*"):
        if not directory.is_dir():
            continue
        for attempt in directory.glob("attempt-*"):
            terminal_path = attempt / "terminal.json"
            if not terminal_path.exists():
                continue
            terminal = _read_json(terminal_path)
            if terminal.get("unit_id") == unit_id:
                return attempt, terminal
    return None, None


def _attempt_candidates(roots: list[Path], unit_id: str) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for root in roots:
        for directory in (root / "units").glob("*"):
            for attempt in directory.glob("attempt-*"):
                terminal_path = attempt / "terminal.json"
                if terminal_path.exists():
                    terminal = _read_json(terminal_path)
                    if terminal.get("unit_id") == unit_id:
                        candidates.append((attempt, terminal))
    return candidates


def audit(*, manifests: list[Path], roots: list[Path], output: Path,
          classification_ledger: Path | None = None) -> dict[str, Any]:
    ledger_rows: dict[str, dict[str, Any]] = {}
    classification_ledger_identity = None
    if classification_ledger is not None:
        ledger = _read_json(classification_ledger)
        ledger_rows = {row["unit_id"]: row for row in ledger.get("rows", [])}
        classification_ledger_identity = f"sha256:{_sha256(classification_ledger)}"
    units: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        body = _read_json(manifest)
        for unit in body.get("contract", {}).get("units", []):
            units.setdefault(unit["compilation_unit_id"], {"unit": unit, "manifest": manifest.as_posix()})
    rows: list[dict[str, Any]] = []
    for unit_id, record in sorted(units.items()):
        unit = record["unit"]
        if unit.get("provider_omitted"):
            continue
        candidates = _attempt_candidates(roots, unit_id)
        if not candidates:
            rows.append({"unit_id": unit_id, "historical_runtime_verdict": "UNKNOWN",
                         "replay_verdict": "UNKNOWN", "failure_class": "artifact_binding_unknown",
                         "semantic_correctness": "UNKNOWN"})
            continue
        ranked = sorted(candidates, key=lambda pair: (
            not (pair[0] / "canonical_output.json").exists(),
            pair[1].get("disposition") not in {"local_validation_failed", "accepted_for_local_contract"},
        ))
        attempt, terminal = ranked[0]
        root = next(root for root in roots if attempt.is_relative_to(root))
        canonical_path = attempt / "canonical_output.json" if attempt else None
        if canonical_path is None or not canonical_path.exists():
            payload = unit.get("payload", {})
            payload_identity = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":")).encode("utf-8")).hexdigest()
            validator_path = Path(__file__).with_name("semantic_two_stage_experiment.py")
            rows.append({"unit_id": unit_id, "record_key": unit.get("payload", {}).get("record_key"),
                         "title": unit.get("payload", {}).get("title"),
                         "historical_runtime_verdict": terminal.get("disposition", "UNKNOWN"),
                         "current_replay_verdict": "FAIL", "replay_verdict": "FAIL", "validator_error": "canonical_output.json is absent",
                         "failed_item": None, "failed_items": [], "source_type": _source_type(unit.get("payload", {})),
                         "source_quote": None, "quote_presence": "nowhere", "matched_paths": [],
                         "failure_class": "no_canonical", "structured_anchor_candidate": False,
                         "semantic_correctness": "UNKNOWN", "source_anchor_v01_verdict": "UNKNOWN",
                         "source_anchor_v01_error": "canonical output unavailable", "source_anchor_v01_items": [],
                         "source_payload_identity": f"sha256:{payload_identity}",
                         "validator_identity": f"validate_atomic_inventory:sha256:{_sha256(validator_path)}",
                         "source_anchor_v01_verdict_identity": {
                             "policy_identity": SOURCE_ANCHOR_POLICY,
                             "policy_implementation_identity": f"sha256:{_sha256(Path(__file__))}",
                             "validator_identity": f"validate_atomic_inventory:sha256:{_sha256(validator_path)}",
                             "source_payload_identity": f"sha256:{payload_identity}",
                             "canonical_output_identity": None,
                             "classification_ledger_identity": classification_ledger_identity,
                         }})
            continue
        output_value = _read_json(canonical_path)
        output_sha256 = _sha256(canonical_path)
        prior_ledger_row = ledger_rows.get(unit_id)
        if classification_ledger is not None and prior_ledger_row is not None:
            if prior_ledger_row.get("canonical_output_sha256") != output_sha256:
                raise ValueError(f"classification ledger output hash mismatch for {unit_id}")
        payload = unit.get("payload", {})
        error = ""
        replay = "PASS"
        anchor_replay = "PASS"
        anchor_error = ""
        anchor_rows: list[dict[str, Any]] = []
        anchor_item_verdicts: list[dict[str, Any]] = []
        failed_item = None
        try:
            validate_atomic_inventory(output_value, payload)
        except Exception as exc:  # validator intentionally exposes ValueError subclasses
            replay = "FAIL"
            error = str(exc)
            match = re.search(r"atomic item ([^ ]+)", error)
            failed_item = next((i for i in output_value.get("items", []) if match and i.get("local_id") == match.group(1)), None)
        failed_items = []
        if replay == "FAIL" and "source_quote is not in source" in error:
            source_texts = _source_texts(payload)
            for item in output_value.get("items", []):
                quote_value = item.get("qualifiers", {}).get("source_quote")
                refs = item.get("source_segment_ids", [])
                if not isinstance(quote_value, str) or not any(
                    quote_value in source_texts.get(ref, "") for ref in refs
                ):
                    failed_items.append(item)
        elif failed_item:
            failed_items = [failed_item]
        if not failed_items and failed_item:
            failed_items = [failed_item]
        classified_items = []
        prior_items = {
            row.get("item", {}).get("local_id"): row
            for row in (prior_ledger_row or {}).get("failed_items", [])
        }
        for item in failed_items:
            quote = item.get("qualifiers", {}).get("source_quote")
            prior = prior_items.get(item.get("local_id"))
            if prior is not None:
                if prior.get("item") != item:
                    raise ValueError(f"classification ledger item mismatch for {unit_id}/{item.get('local_id')}")
                classified_items.append(prior)
                continue
            evidence = _source_evidence(payload, quote, item.get("source_segment_ids", []))
            failure_class, anchor_candidate = _failure_class(error, evidence)
            classified_items.append({"item": item, "source_quote": quote, **evidence,
                                     "failure_class": failure_class,
                                     "structured_anchor_candidate": anchor_candidate})
        eligible_item_classes = {
            row["item"]["local_id"]: row["failure_class"]
            for row in classified_items
        }
        try:
            anchor_result = _validate_with_source_anchor(
                output_value, payload, failed_item_classes=eligible_item_classes,
            )
            anchor_rows = anchor_result.get("anchors", [])
            anchor_item_verdicts = anchor_result.get("item_verdicts", [])
            anchor_replay = anchor_result["status"]
            if anchor_replay == "FAIL":
                anchor_error = "one or more items remain fail-closed"
        except Exception as exc:
            anchor_replay = "FAIL"
            anchor_error = str(exc)
        primary = classified_items[0] if classified_items else {
            "item": failed_item, "source_quote": None, "quote_presence": "nowhere",
            "matched_paths": [], "source_types": [], "failure_class": _failure_class(error, {})[0],
            "structured_anchor_candidate": False}
        failure_class = primary["failure_class"] if replay == "FAIL" else None
        anchor_candidate = any(row["structured_anchor_candidate"] for row in classified_items)
        payload_identity = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        validator_path = Path(__file__).with_name("semantic_two_stage_experiment.py")
        validator_identity = f"validate_atomic_inventory:sha256:{_sha256(validator_path)}"
        policy_implementation_identity = f"sha256:{_sha256(Path(__file__))}"
        rows.append({"unit_id": unit_id, "record_key": payload.get("record_key"), "title": payload.get("title"),
                     "historical_runtime_verdict": terminal.get("disposition", "UNKNOWN"),
                     "current_replay_verdict": replay, "validator_error": error,
                     "source_anchor_v01_verdict": anchor_replay, "source_anchor_v01_error": anchor_error,
                     "source_anchor_v01_items": anchor_rows,
                     "source_anchor_v01_item_verdicts": anchor_item_verdicts,
                     "failed_item": primary.get("item"), "failed_items": classified_items,
                     "source_type": _source_type(payload), "source_quote": primary.get("source_quote"),
                     "quote_presence": primary.get("quote_presence"), "matched_paths": primary.get("matched_paths"),
                     "failure_class": failure_class, "structured_anchor_candidate": anchor_candidate,
                     "semantic_correctness": "UNKNOWN", "canonical_output_sha256": output_sha256,
                     "source_payload_identity": f"sha256:{payload_identity}",
                     "source_anchor_v01_verdict_identity": {
                         "policy_identity": SOURCE_ANCHOR_POLICY,
                         "policy_implementation_identity": policy_implementation_identity,
                         "validator_identity": validator_identity,
                         "source_payload_identity": f"sha256:{payload_identity}",
                         "canonical_output_identity": f"sha256:{_sha256(canonical_path)}",
                         "classification_ledger_identity": classification_ledger_identity,
                     },
                     "run_root": root.as_posix(), "validator_identity": validator_identity})
    failures = [r for r in rows if r.get("current_replay_verdict") == "FAIL"]
    omitted = [u for u in units.values() if u["unit"].get("provider_omitted")]
    report = {"schema_version": ANALYSIS_SCHEMA, "provider_calls": 0, "network_calls": 0,
              "inputs": {"manifests": [p.as_posix() for p in manifests], "roots": [p.as_posix() for p in roots],
                         "classification_ledger": (classification_ledger.as_posix() if classification_ledger else None),
                         "classification_ledger_sha256": (_sha256(classification_ledger) if classification_ledger else None)},
              "counts": {"logical": len(units), "pass": sum(r.get("current_replay_verdict") == "PASS" for r in rows),
                         "fail": len(failures), "canonical_fail": sum(r.get("current_replay_verdict") == "FAIL" and r.get("failure_class") != "no_canonical" for r in rows),
                         "no_canonical": sum(r.get("failure_class") == "no_canonical" for r in rows),
                         "provider_omitted": len(omitted), "unknown": sum(r.get("current_replay_verdict") == "UNKNOWN" for r in rows)},
              "failure_classes": dict(Counter(r.get("failure_class") for r in failures)),
              "source_anchor_v01": {
                  "policy_identity": SOURCE_ANCHOR_POLICY,
                  "pass_units": sum(r.get("source_anchor_v01_verdict") == "PASS" for r in rows),
                  "fail_units": sum(r.get("source_anchor_v01_verdict") == "FAIL" for r in rows),
                  "unknown_units": sum(r.get("source_anchor_v01_verdict") == "UNKNOWN" for r in rows),
                  "recovered_units": sum(r.get("current_replay_verdict") == "FAIL" and r.get("source_anchor_v01_verdict") == "PASS" for r in rows),
                  "recovered_items": sum(len(r.get("source_anchor_v01_items", [])) for r in rows),
                  "anchor_candidate_units": sum(bool(r.get("source_anchor_v01_items")) for r in rows),
                  "failed_items": sum(row.get("verdict") == "FAIL" for r in rows for row in r.get("source_anchor_v01_item_verdicts", [])),
                  "provider_calls": 0, "network_calls": 0,
              }, "rows": rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode())
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--classification-ledger", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(manifests=args.manifest, roots=args.root, output=args.output,
                   classification_ledger=args.classification_ledger)
    print(json.dumps({"output": args.output.as_posix(), "counts": report["counts"],
                      "failure_classes": report["failure_classes"], "provider_calls": 0,
                      "network_calls": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
