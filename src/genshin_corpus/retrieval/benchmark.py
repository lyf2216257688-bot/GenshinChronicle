"""Validation and evidence resolution for the Phase 04 benchmark foundation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


BENCHMARK_SCHEMA_VERSION = "phase04-benchmark-0.1"


class BenchmarkValidationError(ValueError):
    """Raised when a benchmark annotation is incomplete or unresolvable."""


def load_benchmark(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkValidationError(f"cannot read benchmark: {path}") from exc
    if not isinstance(value, Mapping):
        raise BenchmarkValidationError("benchmark must be a JSON object")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BenchmarkValidationError(f"{field} must be a non-empty string")
    return value


def _location(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkValidationError(f"{label} location must be an object")
    _reject_unknown_keys(
        value,
        {
            "record_id",
            "section_ordinal",
            "component_observation_key",
            "unit_ordinal",
            "parsed_json_pointer",
            "decoded_json_pointer",
            "raw_ref",
            "dialogue",
        },
        label,
    )
    _require_text(value.get("record_id"), f"{label}.record_id")
    for field in ("section_ordinal", "unit_ordinal"):
        optional = value.get(field)
        if optional is not None and (not isinstance(optional, int) or isinstance(optional, bool) or optional < 0):
            raise BenchmarkValidationError(f"{label}.{field} must be a non-negative integer when present")
    if value.get("component_observation_key") is not None:
        _require_text(value["component_observation_key"], f"{label}.component_observation_key")
    for field in ("parsed_json_pointer", "decoded_json_pointer"):
        pointer = value.get(field)
        if pointer is not None and (not isinstance(pointer, str) or (pointer != "" and not pointer.startswith("/"))):
            raise BenchmarkValidationError(f"{label}.{field} must be an RFC 6901-style pointer when present")
    raw_ref = value.get("raw_ref")
    if raw_ref is not None:
        if not isinstance(raw_ref, Mapping) or not raw_ref:
            raise BenchmarkValidationError(f"{label}.raw_ref must be a non-empty object when present")
        if not all(isinstance(key, str) and key for key in raw_ref):
            raise BenchmarkValidationError(f"{label}.raw_ref field names must be non-empty strings")
    dialogue = value.get("dialogue")
    if dialogue is not None:
        if not isinstance(dialogue, Mapping):
            raise BenchmarkValidationError(f"{label}.dialogue must be an object when present")
        _reject_unknown_keys(dialogue, {"group_ordering", "node_source_id", "edge"}, f"{label}.dialogue")
        ordering = dialogue.get("group_ordering")
        if not isinstance(ordering, int) or isinstance(ordering, bool) or ordering < 0:
            raise BenchmarkValidationError(f"{label}.dialogue.group_ordering must be a non-negative integer")
        node_id = dialogue.get("node_source_id")
        if node_id is not None:
            _require_text(node_id, f"{label}.dialogue.node_source_id")
        edge = dialogue.get("edge")
        if edge is not None:
            if not isinstance(edge, Mapping):
                raise BenchmarkValidationError(f"{label}.dialogue.edge must be an object when present")
            _reject_unknown_keys(edge, {"parent_id", "child_id"}, f"{label}.dialogue.edge")
            _require_text(edge.get("parent_id"), f"{label}.dialogue.edge.parent_id")
            _require_text(edge.get("child_id"), f"{label}.dialogue.edge.child_id")
    if value.get("section_ordinal") is None:
        for field in ("component_observation_key", "unit_ordinal"):
            if value.get(field) is not None:
                raise BenchmarkValidationError(f"{label}.{field} requires section_ordinal")
    if value.get("unit_ordinal") is None:
        for field in ("decoded_json_pointer", "dialogue"):
            if value.get(field) is not None:
                raise BenchmarkValidationError(f"{label}.{field} requires unit_ordinal")
    return value


def _reject_unknown_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise BenchmarkValidationError(f"{label} has unsupported selector field(s): {', '.join(sorted(map(str, unknown)))}")


def validate_benchmark(benchmark: Mapping[str, Any]) -> None:
    if benchmark.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkValidationError("unsupported benchmark schema_version")
    _require_text(benchmark.get("benchmark_id"), "benchmark_id")
    queries = benchmark.get("queries")
    if not isinstance(queries, list) or not queries:
        raise BenchmarkValidationError("queries must be a non-empty list")
    identifiers: set[str] = set()
    for query in queries:
        if not isinstance(query, Mapping):
            raise BenchmarkValidationError("query must be an object")
        query_id = _require_text(query.get("query_id"), "query_id")
        if query_id in identifiers:
            raise BenchmarkValidationError(f"duplicate query_id: {query_id}")
        identifiers.add(query_id)
        _require_text(query.get("query"), f"{query_id}.query")
        slices = query.get("slices")
        if not isinstance(slices, list) or not slices or not all(isinstance(item, str) and item for item in slices):
            raise BenchmarkValidationError(f"{query_id}.slices must be a non-empty list of strings")
        if query.get("benchmark_track") not in {"main", "diagnostic", "both"}:
            raise BenchmarkValidationError(f"{query_id}.benchmark_track must be main, diagnostic, or both")
        weight = query.get("product_weight")
        if weight is not None and (not isinstance(weight, int | float) or isinstance(weight, bool) or weight <= 0):
            raise BenchmarkValidationError(f"{query_id}.product_weight must be a positive number when present")
        assembly_expectations = query.get("assembly_expectations")
        if assembly_expectations is not None and not isinstance(assembly_expectations, Mapping):
            raise BenchmarkValidationError(f"{query_id}.assembly_expectations must be an object when present")
        evidence = query.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise BenchmarkValidationError(f"{query_id}.evidence must be a non-empty list")
        evidence_ids: set[str] = set()
        relevance_by_evidence_id: dict[str, str] = {}
        for item in evidence:
            if not isinstance(item, Mapping):
                raise BenchmarkValidationError(f"{query_id}.evidence item must be an object")
            evidence_id = _require_text(item.get("evidence_id"), f"{query_id}.evidence_id")
            if evidence_id in evidence_ids:
                raise BenchmarkValidationError(f"{query_id} has duplicate evidence_id: {evidence_id}")
            evidence_ids.add(evidence_id)
            if item.get("relevance") not in {"direct", "supporting", "hard_negative"}:
                raise BenchmarkValidationError(f"{query_id}.{evidence_id}.relevance is invalid")
            relevance_by_evidence_id[evidence_id] = item["relevance"]
            _location(item.get("location"), f"{query_id}.{evidence_id}")
        primary_sets = query.get("primary_sufficient_evidence_sets")
        if not isinstance(primary_sets, list) or not primary_sets:
            raise BenchmarkValidationError(f"{query_id} needs a primary sufficient evidence set")
        for group in primary_sets:
            if not isinstance(group, list) or not group or not all(item in evidence_ids for item in group):
                raise BenchmarkValidationError(f"{query_id} has an invalid primary evidence set")
            if any(relevance_by_evidence_id[item] == "hard_negative" for item in group):
                raise BenchmarkValidationError(f"{query_id} primary sufficient evidence cannot contain hard_negative")
        alternatives = query.get("alternative_sufficient_evidence_sets", [])
        if not isinstance(alternatives, list):
            raise BenchmarkValidationError(f"{query_id}.alternative_sufficient_evidence_sets must be a list when present")
        for group in alternatives:
            if not isinstance(group, list) or not group or not all(item in evidence_ids for item in group):
                raise BenchmarkValidationError(f"{query_id} has an invalid alternative evidence set")
            if any(relevance_by_evidence_id[item] == "hard_negative" for item in group):
                raise BenchmarkValidationError(f"{query_id} alternative sufficient evidence cannot contain hard_negative")


def _load_canonical_record(path: Path, expected_sha: str) -> Mapping[str, Any]:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise BenchmarkValidationError(f"cannot read Canonical record: {path}") from exc
    import hashlib

    if hashlib.sha256(body).hexdigest() != expected_sha:
        raise BenchmarkValidationError(f"Canonical record SHA-256 mismatch: {path}")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkValidationError(f"invalid Canonical record JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise BenchmarkValidationError(f"Canonical record must be an object: {path}")
    return value


def _resolve_location(record: Mapping[str, Any], location: Mapping[str, Any], label: str) -> None:
    if record.get("record_id") != location["record_id"]:
        raise BenchmarkValidationError(f"{label} resolves to the wrong record")
    section_ordinal = location.get("section_ordinal")
    target: Mapping[str, Any] = record
    section: Mapping[str, Any] | None = None
    if section_ordinal is not None:
        sections = record.get("sections")
        if not isinstance(sections, list):
            raise BenchmarkValidationError(f"{label} record has no sections")
        section = next((item for item in sections if isinstance(item, Mapping) and item.get("ordinal") == section_ordinal), None)
        if not isinstance(section, Mapping):
            raise BenchmarkValidationError(f"{label} section does not exist")
        target = section
    context_key = location.get("component_observation_key")
    if context_key is not None:
        if section is None:
            raise BenchmarkValidationError(f"{label} component context requires a section")
        contexts = section.get("component_contexts")
        context = next((item for item in contexts if isinstance(item, Mapping) and item.get("observation_key") == context_key), None) if isinstance(contexts, list) else None
        if not isinstance(context, Mapping):
            raise BenchmarkValidationError(f"{label} component context does not exist")
        target = context
    unit_ordinal = location.get("unit_ordinal")
    if unit_ordinal is not None:
        if section is None:
            raise BenchmarkValidationError(f"{label} unit requires a section")
        units = section.get("units")
        unit = next((item for item in units if isinstance(item, Mapping) and item.get("ordinal") == unit_ordinal), None) if isinstance(units, list) else None
        if not isinstance(unit, Mapping):
            raise BenchmarkValidationError(f"{label} unit does not exist")
        if context_key is not None and unit.get("parent_component_key") != context_key:
            raise BenchmarkValidationError(f"{label} unit does not belong to the annotated context")
        target = unit
        decoded_pointer = location.get("decoded_json_pointer")
        if decoded_pointer is not None:
            value = unit.get("value")
            decoded = value.get("decoded") if isinstance(value, Mapping) else None
            _resolve_json_pointer(decoded, decoded_pointer, f"{label}.decoded_json_pointer")
        dialogue = location.get("dialogue")
        if dialogue is not None:
            value = unit.get("value")
            groups = value.get("groups") if isinstance(value, Mapping) else None
            group = next((item for item in groups if isinstance(item, Mapping) and item.get("ordering") == dialogue.get("group_ordering")), None) if isinstance(groups, list) else None
            if not isinstance(group, Mapping):
                raise BenchmarkValidationError(f"{label} dialogue group does not exist")
            node_id = dialogue.get("node_source_id")
            node: Mapping[str, Any] | None = None
            if node_id is not None:
                nodes = group.get("nodes")
                node = next((item for item in nodes if isinstance(item, Mapping) and item.get("source_id") == node_id), None) if isinstance(nodes, list) else None
                if not isinstance(node, Mapping):
                    raise BenchmarkValidationError(f"{label} dialogue node does not exist")
            edge = dialogue.get("edge")
            if edge is not None:
                edges = group.get("edges")
                matched_edge = next((item for item in edges if (
                    isinstance(item, Mapping)
                    and item.get("parent_id") == edge.get("parent_id")
                    and item.get("child_id") == edge.get("child_id")
                )), None) if isinstance(edges, list) else None
                if not isinstance(matched_edge, Mapping):
                    raise BenchmarkValidationError(f"{label} dialogue edge does not exist")
                if node_id is not None and node_id not in {matched_edge.get("parent_id"), matched_edge.get("child_id")}:
                    raise BenchmarkValidationError(f"{label} dialogue node does not participate in the selected edge")
    _resolve_lineage_selectors(target, location, label)


def _resolve_lineage_selectors(target: Mapping[str, Any], location: Mapping[str, Any], label: str) -> None:
    if location.get("parsed_json_pointer") is None and location.get("raw_ref") is None:
        return
    lineage = target.get("lineage")
    if not isinstance(lineage, Mapping):
        raise BenchmarkValidationError(f"{label} addressed Canonical scope has no lineage")
    expected_pointer = location.get("parsed_json_pointer")
    if expected_pointer is not None and lineage.get("parsed_json_pointer") != expected_pointer:
        raise BenchmarkValidationError(f"{label} Parsed pointer does not match")
    expected_raw_ref = location.get("raw_ref")
    if expected_raw_ref is not None:
        raw_refs = lineage.get("raw_refs")
        if not isinstance(raw_refs, list) or not any(
            isinstance(item, Mapping) and all(item.get(key) == expected for key, expected in expected_raw_ref.items())
            for item in raw_refs
        ):
            raise BenchmarkValidationError(f"{label} RawRef does not match")


def _resolve_json_pointer(value: Any, pointer: str, label: str) -> Any:
    if pointer == "":
        if value is None:
            raise BenchmarkValidationError(f"{label} does not resolve")
        return value
    current = value
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise BenchmarkValidationError(f"{label} does not resolve")
    return current


def resolve_benchmark_locations(benchmark: Mapping[str, Any], canonical_manifest_path: Path) -> dict[str, int]:
    """Resolve every annotated location against an immutable Canonical manifest."""

    validate_benchmark(benchmark)
    manifest = load_benchmark(canonical_manifest_path)
    if manifest.get("status") != "complete" or not isinstance(manifest.get("records"), list):
        raise BenchmarkValidationError("Canonical manifest must be complete with records")
    by_id = {
        entry.get("record_id"): entry
        for entry in manifest["records"]
        if isinstance(entry, Mapping) and isinstance(entry.get("record_id"), str)
    }
    cache: dict[str, Mapping[str, Any]] = {}
    resolved = 0
    for query in benchmark["queries"]:
        for evidence in query["evidence"]:
            location = evidence["location"]
            record_id = location["record_id"]
            entry = by_id.get(record_id)
            if not isinstance(entry, Mapping):
                raise BenchmarkValidationError(f"{query['query_id']}.{evidence['evidence_id']} record does not exist")
            if record_id not in cache:
                path = entry.get("canonical_record_path")
                sha = entry.get("canonical_record_sha256")
                if not isinstance(path, str) or not isinstance(sha, str):
                    raise BenchmarkValidationError(f"Canonical manifest entry is incomplete for {record_id}")
                cache[record_id] = _load_canonical_record(Path(path), sha)
            _resolve_location(cache[record_id], location, f"{query['query_id']}.{evidence['evidence_id']}")
            resolved += 1
    return {"query_count": len(benchmark["queries"]), "evidence_location_count": resolved, "record_count": len(cache)}
