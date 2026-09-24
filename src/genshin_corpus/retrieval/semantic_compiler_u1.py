"""Provider-free P05-W2-U1 projection, unit, and identity machinery.

This module deliberately stops before semantic provider execution.  It reads
accepted Canonical/Retrieval Unit artifacts, emits immutable W2 derivatives,
and keeps model-facing projection separate from provenance and binding data.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write


U1_SCHEMA_VERSION = "phase05-w2-u1-0.1"
PROJECTION_SCHEMA_VERSION = "phase05-w2-semantic-input-0.2"
SEMANTIC_OUTPUT_SCHEMA_VERSION = "phase05-w2-semantic-output-0.1"
PROJECTION_POLICY_VERSION = "phase05-w2-projection-policy-0.1"
UNIT_POLICY_VERSION = "phase05-w2-source-unit-policy-0.1"
IDENTITY_SCHEMA_VERSION = "phase05-w2-identity-0.1"
SAMPLE_SCHEMA_VERSION = "phase05-w2-sample-0.2"
ACCOUNTING_SCHEMA_VERSION = "phase05-w2-accounting-0.1"
CAP_PROFILE_SOFT_CAPS = (8_000, 12_000, 16_000, 24_000)
SEMANTIC_ITEM_KINDS = frozenset({"topic", "mention", "fact", "event", "relation"})
SEGMENT_COVERAGE_DISPOSITIONS = frozenset({"covered", "no_navigation_material", "ambiguous", "unsupported"})


class SemanticCompilerU1Error(ValueError):
    """Raised when an accepted input cannot support an auditable U1 build."""


def semantic_output_schema() -> dict[str, Any]:
    """Return the provider-neutral response envelope for the first live experiment."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "items", "segment_coverage"],
        "properties": {
            "schema_version": {"const": SEMANTIC_OUTPUT_SCHEMA_VERSION},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["local_id", "kind", "label", "source_segment_ids", "topic_path", "qualifiers"],
                    "properties": {
                        "local_id": {"type": "string", "minLength": 1},
                        "kind": {"enum": sorted(SEMANTIC_ITEM_KINDS)},
                        "label": {"type": "string", "minLength": 1},
                        "source_segment_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                        "topic_path": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                        "subject_ref": {"type": ["string", "null"]},
                        "object_ref": {"type": ["string", "null"]},
                        "predicate": {"type": ["string", "null"]},
                        "event_type": {"type": ["string", "null"]},
                        "participants": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                        "qualifiers": {"type": "object"},
                    },
                },
            },
            "segment_coverage": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["segment_id", "disposition", "reason"],
                    "properties": {
                        "segment_id": {"type": "string", "minLength": 1},
                        "disposition": {"enum": sorted(SEGMENT_COVERAGE_DISPOSITIONS)},
                        "reason": {"type": ["string", "null"]},
                    },
                },
            },
        },
    }


SEMANTIC_OUTPUT_SCHEMA_IDENTITY = sha256_json(semantic_output_schema())


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticCompilerU1Error(f"{label} must be an object")
    return value


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise SemanticCompilerU1Error(f"{label} must be a non-empty string")
    return value


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_chars(value: Any) -> int:
    return len(canonical_json_bytes(value).decode("utf-8"))


def _resolve_path(path_value: str, *, manifest_path: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    candidates = (Path.cwd() / path, manifest_path.parent / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


@dataclass(frozen=True)
class ProjectionPolicy:
    """Configurable operating points, not semantic correctness invariants."""

    record_soft_cap: int = 8_000
    hard_cap: int = 16_000
    include_text_segments: bool = True
    include_explicit_references: bool = True
    model_diagnostic_codes: tuple[str, ...] = (
        "COMPONENT_DATA_NOT_JSON",
        "DIALOGUE_DATA_NOT_OBJECT",
        "DIALOGUE_MULTIPLE_PARENT",
        "DIALOGUE_ORPHAN_NODE",
        "DIALOGUE_PARENT_MISSING",
        "DIALOGUE_ROOT_NOT_FOUND",
    )

    def __post_init__(self) -> None:
        if not isinstance(self.record_soft_cap, int) or self.record_soft_cap <= 0:
            raise SemanticCompilerU1Error("record_soft_cap must be positive")
        if not isinstance(self.hard_cap, int) or self.hard_cap < self.record_soft_cap:
            raise SemanticCompilerU1Error("hard_cap must be >= record_soft_cap")
        if len(set(self.model_diagnostic_codes)) != len(self.model_diagnostic_codes):
            raise SemanticCompilerU1Error("model_diagnostic_codes must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_soft_cap": self.record_soft_cap,
            "hard_cap": self.hard_cap,
            "include_text_segments": self.include_text_segments,
            "include_explicit_references": self.include_explicit_references,
            "model_diagnostic_codes": list(self.model_diagnostic_codes),
        }

    @property
    def identity(self) -> str:
        return sha256_json({"schema_version": PROJECTION_POLICY_VERSION, **self.to_dict()})


@dataclass(frozen=True)
class CanonicalProvenance:
    record_id: str
    canonical_record_sha256: str
    content_fingerprint: str | None
    dependency_fingerprint: str | None
    canonical_run_id: str
    canonical_manifest_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "canonical_record_sha256": self.canonical_record_sha256,
            "content_fingerprint": self.content_fingerprint,
            "dependency_fingerprint": self.dependency_fingerprint,
            "canonical_run_id": self.canonical_run_id,
            "canonical_manifest_sha256": self.canonical_manifest_sha256,
        }

    @property
    def identity(self) -> str:
        return sha256_json({"schema_version": IDENTITY_SCHEMA_VERSION, **self.to_dict()})


@dataclass(frozen=True)
class CompilerContract:
    """Provider contract identity; provider execution is not performed here."""

    provider: str = "UNKNOWN"
    model: str = "UNKNOWN"
    model_revision: str | None = None
    prompt_identity: str = "UNKNOWN"
    output_schema_identity: str = SEMANTIC_OUTPUT_SCHEMA_IDENTITY
    generation_config_identity: str = "UNKNOWN"
    semantic_parser_identity: str = "phase05-w2-semantic-parser-0.1"
    semantic_validator_identity: str = "phase05-w2-semantic-validator-0.1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_revision": self.model_revision,
            "model_revision_status": "known" if self.model_revision else "UNKNOWN",
            "prompt_identity": self.prompt_identity,
            "output_schema_identity": self.output_schema_identity,
            "generation_config_identity": self.generation_config_identity,
            "semantic_parser_identity": self.semantic_parser_identity,
            "semantic_validator_identity": self.semantic_validator_identity,
        }

    @property
    def identity(self) -> str:
        return sha256_json({"schema_version": IDENTITY_SCHEMA_VERSION, **self.to_dict()})

    @property
    def provider_request_identity(self) -> str:
        """Identity of dependencies that can change a fresh provider request."""

        return sha256_json({
            "schema_version": IDENTITY_SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.model,
            "model_revision": self.model_revision,
            "prompt_identity": self.prompt_identity,
            "output_schema_identity": self.output_schema_identity,
            "generation_config_identity": self.generation_config_identity,
        })


def semantic_input_identity(payload: Mapping[str, Any]) -> str:
    """Hash only model-facing projection content, never local sidecar data."""

    return sha256_json({"schema_version": PROJECTION_SCHEMA_VERSION, "payload": dict(payload)})


def binding_materialization_identity(
    *,
    semantic_artifact_identity: str,
    canonical_provenance_identity: str,
    retrieval_unit_build_identity: str,
    binding_policy_identity: str = "phase05-w2-binding-0.1",
) -> str:
    return sha256_json({
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "semantic_artifact_identity": semantic_artifact_identity,
        "canonical_provenance_identity": canonical_provenance_identity,
        "retrieval_unit_build_identity": retrieval_unit_build_identity,
        "binding_policy_identity": binding_policy_identity,
    })


def view_identity(*, semantic_build_identity: str, view_kind: str, view_policy_identity: str) -> str:
    _text(view_kind, "view_kind")
    return sha256_json({
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "semantic_build_identity": semantic_build_identity,
        "view_kind": view_kind,
        "view_policy_identity": view_policy_identity,
    })


def execution_identity(execution: Mapping[str, Any]) -> str:
    """Hash non-secret execution provenance; it never changes semantic input."""

    return sha256_json({"schema_version": IDENTITY_SCHEMA_VERSION, "execution": dict(execution)})


def semantic_artifact_identity(*, input_identity: str, compiler_contract: CompilerContract, output: Mapping[str, Any]) -> str:
    return sha256_json({
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "semantic_input_identity": input_identity,
        "compiler_contract_identity": compiler_contract.identity,
        "output": dict(output),
    })


def validate_semantic_output_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the authoritative output schema without asserting source binding."""

    envelope = _mapping(value, "semantic output")
    if envelope.get("schema_version") != SEMANTIC_OUTPUT_SCHEMA_VERSION:
        raise SemanticCompilerU1Error("semantic output schema_version is unsupported")
    if set(envelope) != {"schema_version", "items", "segment_coverage"}:
        raise SemanticCompilerU1Error("semantic output has unsupported top-level fields")
    items_in = envelope.get("items")
    coverage_in = envelope.get("segment_coverage")
    if not isinstance(items_in, list) or not isinstance(coverage_in, list):
        raise SemanticCompilerU1Error("semantic output items and segment_coverage must be lists")
    items: list[dict[str, Any]] = []
    local_ids: set[str] = set()
    allowed_item_fields = {
        "local_id", "kind", "label", "source_segment_ids", "topic_path", "subject_ref", "object_ref",
        "predicate", "event_type", "participants", "qualifiers",
    }
    for index, raw_item in enumerate(items_in):
        item = _mapping(raw_item, f"semantic output item {index}")
        if not set(item).issubset(allowed_item_fields):
            raise SemanticCompilerU1Error(f"semantic output item {index} has unsupported fields")
        local_id = _text(item.get("local_id"), f"semantic output item {index}.local_id")
        if local_id in local_ids:
            raise SemanticCompilerU1Error("semantic output local_id must be unique")
        local_ids.add(local_id)
        kind = item.get("kind")
        if kind not in SEMANTIC_ITEM_KINDS:
            raise SemanticCompilerU1Error(f"semantic output item {index}.kind is unsupported")
        label = _text(item.get("label"), f"semantic output item {index}.label")
        refs = item.get("source_segment_ids")
        topics = item.get("topic_path")
        participants = item.get("participants", [])
        qualifiers = item.get("qualifiers")
        if not isinstance(refs, list) or not refs:
            raise SemanticCompilerU1Error(f"semantic output item {index} requires source_segment_ids")
        normalized_refs = [_text(ref, f"semantic output item {index}.source_segment_ids") for ref in refs]
        if len(normalized_refs) != len(set(normalized_refs)):
            raise SemanticCompilerU1Error(f"semantic output item {index} has duplicate source_segment_ids")
        if not isinstance(topics, list) or not isinstance(participants, list) or not isinstance(qualifiers, Mapping):
            raise SemanticCompilerU1Error(f"semantic output item {index} has invalid navigation fields")
        normalized_topics = [_text(topic, f"semantic output item {index}.topic_path") for topic in topics]
        normalized_participants = [_text(participant, f"semantic output item {index}.participants") for participant in participants]
        if len(normalized_topics) != len(set(normalized_topics)) or len(normalized_participants) != len(set(normalized_participants)):
            raise SemanticCompilerU1Error(f"semantic output item {index} navigation lists must be unique")
        normalized: dict[str, Any] = {
            "local_id": local_id,
            "kind": kind,
            "label": label,
            "source_segment_ids": normalized_refs,
            "topic_path": normalized_topics,
            "subject_ref": item.get("subject_ref"),
            "object_ref": item.get("object_ref"),
            "predicate": item.get("predicate"),
            "event_type": item.get("event_type"),
            "participants": normalized_participants,
            "qualifiers": dict(qualifiers),
        }
        for name in ("subject_ref", "object_ref", "predicate", "event_type"):
            if normalized[name] is not None:
                normalized[name] = _text(normalized[name], f"semantic output item {index}.{name}")
        items.append(normalized)
    coverage: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(coverage_in):
        entry = _mapping(raw_entry, f"segment coverage {index}")
        if set(entry) != {"segment_id", "disposition", "reason"}:
            raise SemanticCompilerU1Error(f"segment coverage {index} fields are invalid")
        segment_id = _text(entry.get("segment_id"), f"segment coverage {index}.segment_id")
        disposition = entry.get("disposition")
        reason = entry.get("reason")
        if disposition not in SEGMENT_COVERAGE_DISPOSITIONS:
            raise SemanticCompilerU1Error(f"segment coverage {index}.disposition is unsupported")
        if reason is not None:
            reason = _text(reason, f"segment coverage {index}.reason")
        if disposition != "covered" and reason is None:
            raise SemanticCompilerU1Error(f"segment coverage {index} requires a reason")
        coverage.append({"segment_id": segment_id, "disposition": disposition, "reason": reason})
    return {
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
        "items": sorted(items, key=lambda item: item["local_id"]),
        "segment_coverage": list(coverage),
    }


def validate_semantic_source_binding(
    value: Mapping[str, Any],
    *,
    expected_segment_ids: Sequence[str],
) -> dict[str, Any]:
    """Require exact coverage and references to supplied, covered segments."""

    envelope = _mapping(value, "semantic output")
    expected = tuple(_text(item, f"expected_segment_ids[{index}]") for index, item in enumerate(expected_segment_ids))
    if len(expected) != len(set(expected)):
        raise SemanticCompilerU1Error("expected_segment_ids must be unique")
    items = envelope.get("items")
    coverage = envelope.get("segment_coverage")
    if not isinstance(items, list) or not isinstance(coverage, list):
        raise SemanticCompilerU1Error("semantic output items and segment_coverage must be lists")
    expected_set = set(expected)
    for index, item in enumerate(items):
        entry = _mapping(item, f"semantic output item {index}")
        refs = entry.get("source_segment_ids")
        if not isinstance(refs, list) or not set(refs).issubset(expected_set):
            raise SemanticCompilerU1Error(f"semantic output item {index} has invalid source_segment_ids")
    covered_ids: set[str] = set()
    for entry in coverage:
        coverage_entry = _mapping(entry, "segment coverage")
        segment_id = str(coverage_entry.get("segment_id"))
        if segment_id in covered_ids:
            raise SemanticCompilerU1Error("segment coverage segment_id must be unique")
        covered_ids.add(segment_id)
    if covered_ids != set(expected):
        raise SemanticCompilerU1Error("segment coverage must account for every input segment exactly once")
    item_ref_ids = {ref for item in items for ref in item["source_segment_ids"]}
    disposition_by_id = {entry["segment_id"]: entry["disposition"] for entry in coverage}
    if any(disposition_by_id[ref] != "covered" for ref in item_ref_ids):
        raise SemanticCompilerU1Error("semantic items may reference only covered segments")
    return {
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
        "items": sorted(items, key=lambda item: item["local_id"]),
        "segment_coverage": sorted(coverage, key=lambda item: item["segment_id"]),
    }


def validate_semantic_output_envelope(
    value: Mapping[str, Any],
    *,
    expected_segment_ids: Sequence[str],
) -> dict[str, Any]:
    """Validate schema and source binding without assigning semantic acceptance."""

    schema_valid = validate_semantic_output_schema(value)
    return validate_semantic_source_binding(schema_valid, expected_segment_ids=expected_segment_ids)


@dataclass(frozen=True)
class ReuseDecision:
    provider_call_required: bool
    local_reparse_required: bool
    allowed: bool
    reason: str
    equivalence_status: str


def decide_reuse(
    *,
    existing_input_identity: str,
    requested_input_identity: str,
    existing_contract: CompilerContract,
    requested_contract: CompilerContract,
    raw_response_available: bool,
    output_schema_unchanged: bool,
    local_parser_changed: bool = False,
    local_validation_changed: bool = False,
) -> ReuseDecision:
    """Separate paid provider invalidation from local response reprocessing."""

    if existing_input_identity != requested_input_identity:
        return ReuseDecision(True, False, False, "semantic_input_changed", "not_equivalent")
    provider_contract_changed = existing_contract.provider_request_identity != requested_contract.provider_request_identity
    local_contract_changed = existing_contract.identity != requested_contract.identity
    if provider_contract_changed:
        return ReuseDecision(True, False, False, "provider_request_contract_changed", "not_equivalent")
    if local_contract_changed or local_parser_changed or local_validation_changed:
        if raw_response_available and output_schema_unchanged:
            return ReuseDecision(False, True, True, "local_reparse_from_preserved_raw_response", "UNKNOWN")
        return ReuseDecision(True, False, False, "raw_response_cannot_satisfy_changed_local_contract", "UNKNOWN")
    equivalence = "equivalent_known_contract" if requested_contract.model_revision else "UNKNOWN"
    return ReuseDecision(False, False, True, "reuse_immutable_semantic_artifact", equivalence)


def _shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, list):
        return "list"
    if isinstance(value, (str, int, float, bool)):
        return "scalar"
    return type(value).__name__


def _compact_refs(value: Mapping[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    entries = value.get("entry_references")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            content_id = entry.get("content_id")
            name = entry.get("name")
            if isinstance(content_id, str) and content_id:
                refs.append({"content_id": content_id, "name": name if isinstance(name, str) else ""})
    return sorted({(item["content_id"], item["name"]): item for item in refs}.values(), key=lambda item: (item["content_id"], item["name"]))


def _compact_text_segments(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep rich-text structure without repeating lexical content already in text."""

    entries = value.get("text_segments")
    if not isinstance(entries, list):
        return []
    compact: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        text = entry.get("text")
        if isinstance(path, str) and isinstance(text, str):
            compact.append({"path": path, "char_count": len(text)})
    return compact


def _diagnostic_codes(value: Mapping[str, Any], allowed: set[str]) -> list[str]:
    result: set[str] = set()
    diagnostics = value.get("diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if isinstance(diagnostic, Mapping) and diagnostic.get("code") in allowed:
                result.add(str(diagnostic["code"]))
    return sorted(result)


def _dialogue_group_projection(group: Mapping[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for node in group.get("nodes", []) if isinstance(group.get("nodes"), list) else []:
        if not isinstance(node, Mapping):
            continue
        nodes.append({
            "source_id": node.get("source_id"),
            "ordering": node.get("ordering"),
            "option": node.get("option", ""),
            "dialogue": node.get("dialogue", ""),
            "speaker": node.get("speaker"),
        })
    edges: list[dict[str, Any]] = []
    for edge in group.get("edges", []) if isinstance(group.get("edges"), list) else []:
        if not isinstance(edge, Mapping):
            continue
        edges.append({"parent_id": edge.get("parent_id"), "child_id": edge.get("child_id"), "ordering": edge.get("ordering")})
    return {
        "ordering": group.get("ordering"),
        "source_path": group.get("source_path"),
        "root_id": group.get("root_id"),
        "nodes": sorted(nodes, key=lambda item: (item.get("ordering", 0), str(item.get("source_id")))),
        "edges": sorted(edges, key=lambda item: (item.get("ordering", 0), str(item.get("parent_id")), str(item.get("child_id")))),
    }


@dataclass
class _Segment:
    segment_id: str
    canonical_address: dict[str, Any]
    canonical_unit_id: str
    section_name: str
    component_key: str
    kind: str
    provider_value: dict[str, Any]
    source_unit: Mapping[str, Any]
    canonical_provenance: CanonicalProvenance
    omission: dict[str, Any] | None = None


def _canonical_address(record: Mapping[str, Any], section: Mapping[str, Any], context: Mapping[str, Any], unit: Mapping[str, Any], lineage: Mapping[str, Any]) -> dict[str, Any]:
    source_identity = _mapping(record.get("source_identity"), "source_identity")
    return {
        "source_identity_key": source_identity.get("key"),
        "record_id": record.get("record_id"),
        "section_ordinal": section.get("ordinal"),
        "component_observation_key": context.get("observation_key"),
        "component_ordinal": context.get("ordinal"),
        "canonical_unit_kind": unit.get("kind"),
        "canonical_unit_ordinal": unit.get("ordinal"),
        "parsed_json_pointer": lineage.get("parsed_json_pointer"),
    }


def _segment_from_unit(
    *, record: Mapping[str, Any], section: Mapping[str, Any], context: Mapping[str, Any], unit: Mapping[str, Any], provenance: CanonicalProvenance, policy: ProjectionPolicy,
) -> tuple[list[_Segment], list[dict[str, Any]]]:
    kind = unit.get("kind")
    value = _mapping(unit.get("value", {}), f"Canonical unit {kind} value")
    lineage = _mapping(unit.get("lineage"), "Canonical unit lineage")
    address = _canonical_address(record, section, context, unit, lineage)
    base = {
        "kind": kind,
        "section": section.get("source_metadata", {}).get("name", ""),
        "component": context.get("source_component_id"),
        "content_role": sorted((_mapping(unit.get("content_role", {}), "content_role").get("labels") or [])),
        "flags": _diagnostic_codes(unit, set(policy.model_diagnostic_codes)),
    }
    omissions: list[dict[str, Any]] = []
    entries: list[tuple[str, dict[str, Any]]] = []
    if kind == "rich_text":
        text = value.get("normalized_text")
        if not isinstance(text, str) or not text:
            omissions.append({"reason": "known_empty_rich_text", "canonical_address": address})
        else:
            payload = {**base, "text": text}
            compact_segments = _compact_text_segments(value)
            if policy.include_text_segments and compact_segments:
                payload["text_segments"] = compact_segments
            if policy.include_explicit_references:
                refs = _compact_refs(value)
                if refs:
                    payload["references"] = refs
            entries.append(("unit", payload))
    elif kind == "structured_observation":
        if "decoded" not in value:
            omissions.append({"reason": "structured_value_missing_decoded", "canonical_address": address})
        else:
            entries.append(("unit", {**base, "shape": _shape(value.get("decoded")), "decoded": value.get("decoded")}))
    elif kind == "dialogue_graph":
        groups = value.get("groups")
        if not isinstance(groups, list):
            omissions.append({"reason": "dialogue_groups_missing", "canonical_address": address})
        else:
            for group_index, group in enumerate(groups):
                if not isinstance(group, Mapping):
                    omissions.append({"reason": "dialogue_group_invalid", "group_index": group_index, "canonical_address": address})
                    continue
                projected = _dialogue_group_projection(group)
                if not projected["nodes"]:
                    omissions.append({"reason": "known_dialogue_without_text", "group_index": group_index, "canonical_address": address})
                    continue
                entries.append((f"group:{group_index}", {**base, "group_index": group_index, "dialogue": projected}))
    elif kind == "unsupported":
        omissions.append({"reason": "known_unsupported_canonical_unit", "canonical_address": address})
    else:
        raise SemanticCompilerU1Error(f"unsupported Canonical unit kind: {kind!r}")

    segments: list[_Segment] = []
    for suffix, payload in entries:
        segment_id = f"seg-{sha256_json({'canonical_address': address, 'source_subunit': suffix})[:20]}"
        segments.append(_Segment(segment_id, address, str(unit.get("unit_id")), str(section.get("source_metadata", {}).get("name", "")), str(context.get("observation_key")), str(kind), payload, unit, provenance))
    return segments, omissions


def _sidecar_segment(segment: _Segment, *, ru_unit_ids: Sequence[str], ru_build_identity: str) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "canonical_unit_id": segment.canonical_unit_id,
        "canonical_address": segment.canonical_address,
        "canonical_provenance": segment.canonical_provenance.to_dict(),
        "lineage": segment.source_unit.get("lineage"),
        "raw_refs": (segment.source_unit.get("lineage") or {}).get("raw_refs", []),
        "retrieval_unit_build_identity": ru_build_identity,
        "retrieval_unit_ids": list(ru_unit_ids),
    }


def _unit_payload(record_key: str, title: str, segments: Sequence[_Segment], omissions: Sequence[Mapping[str, Any]], *, partial: bool = False) -> dict[str, Any]:
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "record_key": record_key,
        "title": title,
        "segments": [{"segment_id": segment.segment_id, "value": segment.provider_value} for segment in segments],
        "omission_summary": {
            "count": len(omissions),
            "reasons": dict(sorted(Counter(str(item.get("reason", "unknown")) for item in omissions).items())),
            "partial_input": partial,
        },
    }


def _segment_chars(segment: _Segment) -> int:
    return _json_chars({"segment_id": segment.segment_id, "value": segment.provider_value})


def _split_dialogue_segment(segment: _Segment, policy: ProjectionPolicy, *, next_sequence: int) -> tuple[list[_Segment], list[dict[str, Any]], int]:
    dialogue = segment.provider_value.get("dialogue")
    if not isinstance(dialogue, Mapping):
        return [segment], [], next_sequence
    nodes = list(dialogue.get("nodes", []))
    if _json_chars(segment.provider_value) <= policy.hard_cap:
        return [segment], [], next_sequence
    chunks: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for node in nodes:
        candidate = current + [node]
        if current and _json_chars({**segment.provider_value, "dialogue": {**dialogue, "nodes": candidate}}) > policy.hard_cap:
            chunks.append(current)
            current = [node]
        else:
            current = candidate
    if current:
        chunks.append(current)
    if not chunks:
        return [], [{"reason": "oversized_empty_dialogue", "segment_id": segment.segment_id}], next_sequence
    result: list[_Segment] = []
    all_edges = list(dialogue.get("edges", [])) if isinstance(dialogue.get("edges"), list) else []
    for index, chunk in enumerate(chunks):
        ids = {node.get("source_id") for node in chunk}
        edges = [edge for edge in all_edges if edge.get("parent_id") in ids and edge.get("child_id") in ids]
        boundary_edges = [
            edge for edge in all_edges
            if (edge.get("parent_id") in ids) != (edge.get("child_id") in ids)
        ]
        payload = dict(segment.provider_value)
        payload["dialogue"] = {
            **dialogue,
            "nodes": chunk,
            "edges": edges,
            "boundary_edges": boundary_edges,
            "split_parent_segment_id": segment.segment_id,
            "split_index": index,
            "split_count": len(chunks),
        }
        # Keep the parent segment identity visible in split IDs.  These IDs
        # remain local source references and do not pretend to be Canonical
        # identities.
        result.append(_Segment(f"{segment.segment_id}:p{index}", segment.canonical_address, segment.canonical_unit_id, segment.section_name, segment.component_key, segment.kind, payload, segment.source_unit, segment.canonical_provenance))
        next_sequence += 1
    return result, [], next_sequence


def _make_units(record_key: str, title: str, sections: Sequence[dict[str, Any]], policy: ProjectionPolicy) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_units: list[dict[str, Any]] = []
    oversize: list[dict[str, Any]] = []
    record_segments = [segment for section in sections for segment in section["segments"]]
    record_omissions = [item for section in sections for item in section["omissions"]]
    whole_payload = _unit_payload(record_key, title, record_segments, record_omissions)
    if _json_chars(whole_payload) <= policy.record_soft_cap and not any(_segment_chars(segment) > policy.hard_cap for segment in record_segments):
        all_units.append({"unit_scope": "record", "parent_unit_id": None, "segment_ids": [s.segment_id for s in record_segments], "provider_payload": whole_payload})
        return all_units, oversize
    for section in sections:
        section_segments = list(section["segments"])
        section_omissions = list(section["omissions"])
        section_payload = _unit_payload(record_key, title, section_segments, section_omissions)
        if _json_chars(section_payload) <= policy.record_soft_cap:
            all_units.append({"unit_scope": "section", "parent_unit_id": None, "section_ordinal": section["ordinal"], "segment_ids": [s.segment_id for s in section_segments], "provider_payload": section_payload})
            continue
        for component in section["components"]:
            component_segments = list(component["segments"])
            component_omissions = list(component["omissions"])
            component_payload = _unit_payload(record_key, title, component_segments, component_omissions)
            if _json_chars(component_payload) <= policy.record_soft_cap:
                all_units.append({"unit_scope": "component", "parent_unit_id": None, "section_ordinal": section["ordinal"], "component_key": component["key"], "segment_ids": [s.segment_id for s in component_segments], "provider_payload": component_payload})
                continue
            for segment in component_segments:
                pieces, piece_omissions, _ = _split_dialogue_segment(segment, policy, next_sequence=0)
                for piece in pieces:
                    payload = _unit_payload(record_key, title, [piece], component_omissions + piece_omissions)
                    chars = _json_chars(payload)
                    row = {"unit_scope": "segment", "parent_unit_id": None, "section_ordinal": section["ordinal"], "component_key": component["key"], "segment_ids": [piece.segment_id], "provider_payload": payload}
                    if chars > policy.hard_cap:
                        row["status"] = "oversized_unresolved"
                        row["oversized_reason"] = "source_meaningful_boundary_exceeds_hard_cap"
                        oversize.append({"segment_id": piece.segment_id, "serialized_chars": chars, "reason": row["oversized_reason"]})
                    all_units.append(row)
    return all_units, oversize


def _feature_tags(segment: _Segment) -> set[str]:
    tags: set[str] = {f"kind:{segment.kind}"}
    value = segment.provider_value
    if segment.kind == "dialogue_graph":
        tags.add("dialogue")
        tags.add("category:dialogue")
        dialogue = value.get("dialogue", {})
        nodes = dialogue.get("nodes", []) if isinstance(dialogue, Mapping) else []
        edges = dialogue.get("edges", []) if isinstance(dialogue, Mapping) else []
        if len(nodes) >= 4:
            tags.add("dialogue_heavy")
        if edges:
            tags.add("dialogue_branch")
        if any(node.get("speaker") in (None, "") for node in nodes if isinstance(node, Mapping)):
            tags.add("omitted_speaker")
            tags.add("category:pronoun_omitted_subject")
        if edges:
            tags.add("category:dialogue_branch")
    if segment.kind == "structured_observation":
        tags.add("structured")
        tags.add(f"shape:{value.get('shape', 'unknown')}")
    if segment.kind == "rich_text":
        text = str(value.get("text", ""))
        if len(text) >= 500:
            tags.add("narrative_long")
            tags.add("category:narrative_long")
        if value.get("references"):
            tags.add("explicit_cross_record_reference")
            tags.add("category:cross_record_reference")
        if any(token in text for token in ("据说", "相信", "听说", "认为", "可能", "似乎", "知道", "不知")):
            tags.add("category:knowledge_belief")
        if any(token in text for token in ("传闻", "谣言", "传言", "真相", "揭露", " reveal ")):
            tags.add("category:rumor_reveal")
        if any(token in text for token in ("曾经", "后来", "最终", "之前", "之后", "随后", "当时", "终于", "再次", "一度")):
            tags.add("category:temporal_order")
        if any(token in text for token in ("因此", "因为", "由于", "导致", "所以", "于是", "为了", "使得")):
            tags.add("category:causality")
        if any(token in text for token in ("身份", "角色", "成为", "变成", "伪装", "冒充", "真身", "继任")):
            tags.add("category:identity_role")
        if not any(tag.startswith("category:") for tag in tags) and 40 <= len(text) <= 500:
            tags.add("category:ordinary_control")
        if any(token in text for token in ("据说", "传闻", "相信", "听说", "曾经", "后来", "最终", "因此", "身份")):
            tags.add("epistemic_temporal_identity_cue")
    return tags


_HIGH_RISK_CATEGORIES = (
    "category:dialogue_branch",
    "category:pronoun_omitted_subject",
    "category:identity_role",
    "category:temporal_order",
    "category:causality",
    "category:knowledge_belief",
    "category:rumor_reveal",
    "category:cross_record_reference",
    "category:narrative_long",
)


def _select_sample(units: Sequence[Mapping[str, Any]], sidecars: Mapping[str, Mapping[str, Any]], *, target: int = 42) -> dict[str, Any]:
    if target < 1:
        raise SemanticCompilerU1Error("sample target must be positive")
    candidates: list[dict[str, Any]] = []
    considered_unit_ids: set[str] = set()
    ineligible_unit_ids: set[str] = set()
    for row in units:
        ids = row.get("segment_ids", [])
        if not isinstance(ids, list) or not ids:
            continue
        if row.get("status") == "oversized_unresolved" or row.get("provider_payload", {}).get("omission_summary", {}).get("partial_input"):
            continue
        unit_id = str(row.get("compilation_unit_id"))
        if unit_id in considered_unit_ids:
            continue
        considered_unit_ids.add(unit_id)
        bound_sidecars = [sidecars.get(str(segment_id)) for segment_id in ids]
        if any(sidecar is None or not sidecar.get("retrieval_unit_ids") for sidecar in bound_sidecars):
            ineligible_unit_ids.add(unit_id)
            continue
        tags = set().union(*(set(sidecar.get("tags", [])) for sidecar in bound_sidecars if sidecar is not None))
        candidates.append({
            "segment_id": str(ids[0]),
            "row": row,
            "tags": tags,
            "source_segment_count": len(ids),
            "ru_bound_segment_count": sum(bool(sidecar and sidecar.get("retrieval_unit_ids")) for sidecar in bound_sidecars),
            "ru_unit_ids": sorted({str(ru_id) for sidecar in bound_sidecars if sidecar is not None for ru_id in sidecar.get("retrieval_unit_ids", [])}),
        })
    candidates.sort(key=lambda item: sha256_json({"compilation_unit_id": item["row"].get("compilation_unit_id"), "tags": sorted(item["tags"])}))
    available_unit_count = len(candidates)
    effective_target = min(target, available_unit_count)
    selected_candidates: list[dict[str, Any]] = []
    selected_unit_ids: set[str] = set()
    quality_unit_ids: set[str] = set()
    paired_unit_ids: set[str] = set()
    rationale: dict[str, list[str]] = defaultdict(list)

    def add(candidate: dict[str, Any], *, quality: bool = False, paired: bool = False, reason: str) -> bool:
        unit_id = str(candidate["row"].get("compilation_unit_id"))
        if unit_id in selected_unit_ids:
            if quality:
                quality_unit_ids.add(unit_id)
            if paired:
                paired_unit_ids.add(unit_id)
            rationale[unit_id].append(reason)
            return False
        selected_unit_ids.add(unit_id)
        selected_candidates.append(candidate)
        if quality:
            quality_unit_ids.add(unit_id)
        if paired:
            paired_unit_ids.add(unit_id)
        rationale[unit_id].append(reason)
        return True

    def categories(candidate: Mapping[str, Any]) -> set[str]:
        return set(candidate["tags"]) & set(_HIGH_RISK_CATEGORIES)

    quality_target = min(30, effective_target)
    paired_target = min(18, quality_target)
    high_risk_target = min(14, paired_target)
    control_target = min(4, paired_target - high_risk_target)
    high_risk_candidates = [candidate for candidate in candidates if categories(candidate)]
    for category in _HIGH_RISK_CATEGORIES:
        if len([unit_id for unit_id in paired_unit_ids if unit_id in quality_unit_ids]) >= high_risk_target:
            break
        for candidate in high_risk_candidates:
            if category in categories(candidate) and add(candidate, quality=True, paired=True, reason=f"challenger_high_risk:{category}"):
                break
    for candidate in sorted(high_risk_candidates, key=lambda item: (-len(categories(item)), sha256_json(item["row"].get("compilation_unit_id")))):
        if len(paired_unit_ids) >= high_risk_target:
            break
        add(candidate, quality=True, paired=True, reason="challenger_high_risk:coverage_fill")

    controls = [
        candidate for candidate in candidates
        if "category:ordinary_control" in candidate["tags"] and not categories(candidate)
    ]
    controls.sort(key=lambda item: sha256_json(item["row"].get("compilation_unit_id")))
    risk_for_matching = [candidate for candidate in selected_candidates if str(candidate["row"].get("compilation_unit_id")) in paired_unit_ids]
    for index in range(control_target):
        available = [candidate for candidate in controls if str(candidate["row"].get("compilation_unit_id")) not in selected_unit_ids]
        if not available:
            break
        anchor = risk_for_matching[index % len(risk_for_matching)] if risk_for_matching else None
        if anchor is None:
            candidate = available[0]
        else:
            target_chars = int(anchor["row"].get("serialized_chars", 0))
            candidate = min(available, key=lambda item: (abs(int(item["row"].get("serialized_chars", 0)) - target_chars), sha256_json(item["row"].get("compilation_unit_id"))))
        add(candidate, quality=True, paired=True, reason=f"challenger_matched_control:{index + 1}")

    quality_candidates = sorted(
        [candidate for candidate in candidates if categories(candidate) or "category:ordinary_control" in candidate["tags"]],
        key=lambda item: (-len(categories(item)), sha256_json(item["row"].get("compilation_unit_id"))),
    )
    for candidate in quality_candidates:
        if len(quality_unit_ids) >= quality_target:
            break
        add(candidate, quality=True, reason="semantic_quality:coverage_fill")
    for candidate in candidates:
        if len(quality_unit_ids) >= quality_target:
            break
        add(candidate, quality=True, reason="semantic_quality:ordinary_or_structural_fill")

    structural_targets = ("kind:rich_text", "kind:structured_observation", "kind:dialogue_graph")
    selected_tags = lambda candidate: candidate["tags"]
    for tag in structural_targets:
        if len(selected_candidates) >= effective_target:
            break
        if not any(tag in selected_tags(candidate) for candidate in selected_candidates):
            candidate = next((item for item in candidates if tag in item["tags"] and str(item["row"].get("compilation_unit_id")) not in selected_unit_ids), None)
            if candidate is not None:
                add(candidate, reason=f"projection_contract:structural:{tag}")
    for candidate in candidates:
        if len(selected_candidates) >= effective_target:
            break
        add(candidate, reason="projection_contract:bounded_fill")
    if len(selected_candidates) < effective_target or len(quality_unit_ids) < quality_target:
        raise SemanticCompilerU1Error("insufficient eligible sample candidates")

    selected: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        row = candidate["row"]
        unit_id = str(row.get("compilation_unit_id"))
        quality = unit_id in quality_unit_ids
        selected.append({
            "segment_id": candidate["segment_id"],
            "compilation_unit_id": unit_id,
            "record_id": row.get("record_id"),
            "unit_scope": row.get("unit_scope"),
            "semantic_input_identity": row.get("semantic_input_identity"),
            "serialized_chars": row.get("serialized_chars"),
            "purpose": "semantic_quality" if quality else "projection_only",
            "projection_contract_member": True,
            "semantic_quality_member": quality,
            "challenger_paired": unit_id in paired_unit_ids,
            "selection_rationale": sorted(set(rationale[unit_id])),
            "tags": sorted(candidate["tags"]),
            "coverage_categories": sorted(tag.split(":", 1)[1] for tag in candidate["tags"] if tag.startswith("category:")),
            "ru_binding": {
                "eligible_for_live_sample": True,
                "source_segment_count": candidate["source_segment_count"],
                "ru_bound_segment_count": candidate["ru_bound_segment_count"],
                "ru_unbound_segment_count": candidate["source_segment_count"] - candidate["ru_bound_segment_count"],
                "retrieval_unit_id_count": len(candidate["ru_unit_ids"]),
                "retrieval_unit_ids_sha256": sha256_json(candidate["ru_unit_ids"]),
            },
        })
    return {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "target": target,
        "eligibility_contract": "all_source_segments_have_accepted_retrieval_unit_binding",
        "available_compilation_unit_count": available_unit_count,
        "considered_compilation_unit_count": len(considered_unit_ids),
        "ru_ineligible_compilation_unit_count": len(ineligible_unit_ids),
        "selected_count": len(selected),
        "membership_counts": {
            "projection_contract": sum(item["projection_contract_member"] for item in selected),
            "semantic_quality": sum(item["semantic_quality_member"] for item in selected),
            "projection_only": sum(not item["semantic_quality_member"] for item in selected),
        },
        "challenger_paired_count": sum(item["challenger_paired"] for item in selected),
        "items": selected,
    }


def _validate_canonical_manifest(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if manifest.get("status") != "complete":
        raise SemanticCompilerU1Error("Canonical manifest must be complete")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise SemanticCompilerU1Error("Canonical manifest.records must be a list")
    if manifest.get("input_record_count") != len(records) or manifest.get("accounted_record_count") != len(records):
        raise SemanticCompilerU1Error("Canonical manifest accounting mismatch")
    if manifest.get("input_integrity_failure_count") != 0:
        raise SemanticCompilerU1Error("Canonical manifest has integrity failures")
    _text(manifest.get("canonical_run_id"), "canonical_run_id")
    return records


def _load_ru_bindings(ru_manifest_path: Path) -> tuple[str, str, str, dict[str, list[str]]]:
    try:
        manifest_body = ru_manifest_path.read_bytes()
        manifest = json.loads(manifest_body.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticCompilerU1Error("cannot read Retrieval Unit manifest") from exc
    if manifest.get("status") != "complete":
        raise SemanticCompilerU1Error("Retrieval Unit manifest must be complete")
    build_identity = _text(manifest.get("build_identity"), "Retrieval Unit build_identity")
    descriptor = _mapping(_mapping(manifest.get("artifacts"), "RU artifacts").get("retrieval_units"), "RU retrieval_units artifact")
    artifact = ru_manifest_path.parent.parent / _text(descriptor.get("path"), "RU artifact path")
    if not artifact.exists():
        raise SemanticCompilerU1Error(f"RU artifact missing: {artifact}")
    digest = hashlib.sha256()
    with artifact.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != descriptor.get("sha256"):
        raise SemanticCompilerU1Error("RU retrieval_units artifact SHA-256 mismatch")
    bindings: dict[str, list[str]] = defaultdict(list)
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise SemanticCompilerU1Error(f"invalid RU row {line_number}") from exc
            source = _mapping(row.get("source"), f"RU row {line_number}.source")
            address = source.get("canonical_address")
            if isinstance(address, Mapping):
                bindings[canonical_json_bytes(address).decode("utf-8")].append(str(row.get("unit_id")))
    return build_identity, _sha256_bytes(manifest_body), str(descriptor.get("sha256")), dict(bindings)


def _write_jsonl_gzip(path: Path, rows: Iterable[Mapping[str, Any]]) -> tuple[str, int, int]:
    import io

    output = io.BytesIO()
    count = 0
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        for row in rows:
            compressed.write(canonical_json_bytes(dict(row)) + b"\n")
            count += 1
    body = output.getvalue()
    _write_immutable(path, body)
    return _sha256_bytes(body), len(body), count


def _write_immutable(path: Path, body: bytes) -> None:
    """Publish a derivative without overwriting a different prior result."""

    if path.exists():
        if path.read_bytes() != body:
            raise SemanticCompilerU1Error(f"refusing to overwrite U1 artifact: {path}")
        return
    atomic_write(path, body)


def build_u1(
    canonical_manifest_path: Path,
    ru_manifest_path: Path,
    output_root: Path,
    *,
    policy: ProjectionPolicy | None = None,
    sample_target: int = 42,
) -> dict[str, Any]:
    """Build provider-free projection/profile/sample artifacts."""

    policy = policy or ProjectionPolicy()
    canonical_manifest_path = Path(canonical_manifest_path)
    ru_manifest_path = Path(ru_manifest_path)
    output_root = Path(output_root)
    manifest_body = canonical_manifest_path.read_bytes()
    manifest = _mapping(json.loads(manifest_body.decode("utf-8")), "Canonical manifest")
    entries = _validate_canonical_manifest(manifest)
    canonical_manifest_sha256 = _sha256_bytes(manifest_body)
    ru_build_identity, ru_manifest_sha256, ru_artifact_sha256, ru_bindings = _load_ru_bindings(ru_manifest_path)

    projection_rows: list[dict[str, Any]] = []
    unit_rows: list[dict[str, Any]] = []
    sidecars: dict[str, dict[str, Any]] = {}
    omissions: list[dict[str, Any]] = []
    record_stats: list[dict[str, Any]] = []
    profile_counters: Counter[str] = Counter()
    canonical_record_bytes = 0
    canonical_record_chars = 0
    cap_points = tuple(sorted(set(CAP_PROFILE_SOFT_CAPS) | {policy.record_soft_cap}))
    cap_stats: dict[int, dict[str, Any]] = {
        cap: {"soft_cap": cap, "hard_cap": max(policy.hard_cap, cap), "compilation_unit_count": 0, "scope_counts": Counter(), "oversized_unresolved_count": 0, "serialized_chars": 0, "serialized_bytes": 0}
        for cap in cap_points
    }
    compilation_segment_ids: set[str] = set()
    split_parent_ids: set[str] = set()
    split_piece_count = 0
    for record_index, entry in enumerate(entries):
        record_path = _resolve_path(_text(entry.get("canonical_record_path"), "canonical_record_path"), manifest_path=canonical_manifest_path)
        body = record_path.read_bytes()
        canonical_record_bytes += len(body)
        actual_sha = _sha256_bytes(body)
        if actual_sha != entry.get("canonical_record_sha256"):
            raise SemanticCompilerU1Error(f"Canonical record SHA mismatch at index {record_index}")
        decoded_body = body.decode("utf-8")
        canonical_record_chars += len(decoded_body)
        record = _mapping(json.loads(decoded_body), f"Canonical record {record_index}")
        if record.get("record_id") != entry.get("record_id"):
            raise SemanticCompilerU1Error(f"Canonical record identity mismatch at index {record_index}")
        provenance = CanonicalProvenance(
            record_id=_text(record.get("record_id"), "record_id"),
            canonical_record_sha256=actual_sha,
            content_fingerprint=record.get("content_fingerprint") if isinstance(record.get("content_fingerprint"), str) else None,
            dependency_fingerprint=record.get("dependency_fingerprint") if isinstance(record.get("dependency_fingerprint"), str) else None,
            canonical_run_id=_text(manifest.get("canonical_run_id"), "canonical_run_id"),
            canonical_manifest_sha256=canonical_manifest_sha256,
        )
        source_identity = _mapping(record.get("source_identity"), "source_identity")
        record_key = _text(source_identity.get("key"), "source_identity.key")
        title = str(_mapping(record.get("record_metadata"), "record_metadata").get("name", ""))
        sections_out: list[dict[str, Any]] = []
        record_omissions: list[dict[str, Any]] = []
        for section in record.get("sections", []):
            section = _mapping(section, "Canonical section")
            profile_counters["canonical_sections"] += 1
            contexts = {str(context.get("observation_key")): context for context in section.get("component_contexts", []) if isinstance(context, Mapping)}
            profile_counters["canonical_component_contexts"] += len(contexts)
            by_component: dict[str, dict[str, Any]] = {}
            for unit in section.get("units", []):
                unit = _mapping(unit, "Canonical unit")
                profile_counters["canonical_units"] += 1
                component_key = _text(unit.get("parent_component_key"), "parent_component_key")
                context = contexts.get(component_key)
                if context is None:
                    raise SemanticCompilerU1Error("Canonical unit has no component context")
                segments, unit_omissions = _segment_from_unit(record=record, section=section, context=context, unit=unit, provenance=provenance, policy=policy)
                for omission in unit_omissions:
                    omission = dict(omission)
                    omission.update({"record_id": record.get("record_id"), "canonical_unit_id": unit.get("unit_id")})
                    record_omissions.append(omission)
                    omissions.append(omission)
                    profile_counters[f"omission:{omission.get('reason')}"] += 1
                component = by_component.setdefault(component_key, {"key": component_key, "segments": [], "omissions": []})
                component["segments"].extend(segments)
                component["omissions"].extend(unit_omissions)
                for segment in segments:
                    if segment.segment_id in sidecars:
                        raise SemanticCompilerU1Error(f"source-segment identity collision: {segment.segment_id}")
                    tags = _feature_tags(segment)
                    ru_ids = ru_bindings.get(canonical_json_bytes(segment.canonical_address).decode("utf-8"), [])
                    sidecars[segment.segment_id] = {
                        **_sidecar_segment(segment, ru_unit_ids=ru_ids, ru_build_identity=ru_build_identity),
                        "tags": sorted(tags),
                        "projection_identity": None,
                    }
                    profile_counters[f"unit_kind:{segment.kind}"] += 1
                    profile_counters["ru_bound" if ru_ids else "ru_unbound"] += 1
            sections_out.append({
                "ordinal": section.get("ordinal"),
                "name": str(_mapping(section.get("source_metadata"), "source_metadata").get("name", "")),
                "components": list(by_component.values()),
                "segments": [segment for component in by_component.values() for segment in component["segments"]],
                "omissions": [item for component in by_component.values() for item in component["omissions"]],
            })
        record_segments = [segment for section in sections_out for segment in section["segments"]]
        record_payload = _unit_payload(record_key, title, record_segments, record_omissions)
        record_projection_identity = semantic_input_identity(record_payload)
        for segment in record_segments:
            sidecars[segment.segment_id]["projection_identity"] = record_projection_identity
        units, oversize = _make_units(record_key, title, sections_out, policy)
        for cap in cap_points:
            variant_policy = ProjectionPolicy(
                record_soft_cap=cap,
                hard_cap=max(policy.hard_cap, cap),
                include_text_segments=policy.include_text_segments,
                include_explicit_references=policy.include_explicit_references,
                model_diagnostic_codes=policy.model_diagnostic_codes,
            )
            variant_units, variant_oversize = _make_units(record_key, title, sections_out, variant_policy)
            stats = cap_stats[cap]
            stats["compilation_unit_count"] += len(variant_units)
            stats["oversized_unresolved_count"] += len(variant_oversize)
            for variant_unit in variant_units:
                scope = str(variant_unit.get("unit_scope", "unknown"))
                stats["scope_counts"][scope] += 1
                stats["serialized_chars"] += _json_chars(variant_unit["provider_payload"])
                stats["serialized_bytes"] += len(canonical_json_bytes(variant_unit["provider_payload"]))
        # Split dialogue pieces inherit the authoritative Canonical locator;
        # their local IDs are only compilation references.
        for unit in units:
            for segment_id in unit.get("segment_ids", []):
                if segment_id in sidecars:
                    continue
                parent_id = str(segment_id).split(":p", 1)[0]
                parent = sidecars.get(parent_id)
                if parent is None:
                    raise SemanticCompilerU1Error(f"missing sidecar for split segment {segment_id}")
                sidecars[segment_id] = {**parent, "segment_id": segment_id, "split_parent_segment_id": parent_id}
        for unit in units:
            input_identity = semantic_input_identity(unit["provider_payload"])
            unit_id = sha256_json({
                "schema_version": UNIT_POLICY_VERSION,
                "semantic_input_identity": input_identity,
                "scope": unit["unit_scope"],
                "segment_ids": unit["segment_ids"],
            })
            unit["compilation_unit_id"] = unit_id
            unit["record_id"] = record.get("record_id")
            unit["record_projection_identity"] = record_projection_identity
            unit["semantic_input_identity"] = input_identity
            unit["canonical_provenance_identity"] = provenance.identity
            split_parents = sorted({str(segment_id).split(":p", 1)[0] for segment_id in unit["segment_ids"] if ":p" in str(segment_id)})
            unit["merge_parent_segment_ids"] = split_parents
            compilation_segment_ids.update(str(segment_id) for segment_id in unit["segment_ids"])
            split_parent_ids.update(split_parents)
            split_piece_count += sum(":p" in str(segment_id) for segment_id in unit["segment_ids"])
            unit["serialized_chars"] = _json_chars(unit["provider_payload"])
            unit["serialized_bytes"] = len(canonical_json_bytes(unit["provider_payload"]))
            unit["omission_count"] = unit["provider_payload"]["omission_summary"]["count"]
            unit_rows.append(unit)
            profile_counters[f"unit_scope:{unit['unit_scope']}"] += 1
        for item in oversize:
            item.update({"record_id": record.get("record_id"), "record_projection_identity": record_projection_identity})
            omissions.append(item)
            profile_counters["oversized_unresolved"] += 1
        projection_rows.append({
            "schema_version": PROJECTION_SCHEMA_VERSION,
            "record_id": record.get("record_id"),
            "record_projection_identity": record_projection_identity,
            "canonical_provenance_identity": provenance.identity,
            "provider_payload": record_payload,
            "serialized_chars": _json_chars(record_payload),
            "serialized_bytes": len(canonical_json_bytes(record_payload)),
            "canonical_record_bytes": len(body),
            "canonical_record_chars": len(decoded_body),
            "sidecar_segment_ids": [segment.segment_id for segment in record_segments],
        })
        record_stats.append({
            "record_id": record.get("record_id"),
            "record_index": record_index,
            "canonical_record_sha256": actual_sha,
            "record_projection_identity": record_projection_identity,
            "serialized_chars": _json_chars(record_payload),
            "serialized_bytes": len(canonical_json_bytes(record_payload)),
            "segment_count": len(record_segments),
            "section_count": len(sections_out),
            "omission_count": len(record_omissions),
            "provenance_identity": provenance.identity,
        })

    # Manifest/source traversal order is deterministic and keeps record-local
    # provenance adjacent for effective gzip compression.
    sidecar_rows = [{"segment_id": key, **value} for key, value in sidecars.items()]
    sample = _select_sample(unit_rows, sidecars, target=sample_target)
    semantic_build_identity = sha256_json({"schema_version": U1_SCHEMA_VERSION, "units": [row["compilation_unit_id"] for row in unit_rows], "projection_policy": policy.identity})
    profile = {
        "schema_version": U1_SCHEMA_VERSION,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "projection_policy_version": PROJECTION_POLICY_VERSION,
        "unit_policy_version": UNIT_POLICY_VERSION,
        "policy": policy.to_dict(),
        "cap_profile": [
            {
                "soft_cap": cap,
                "hard_cap": stats["hard_cap"],
                "compilation_unit_count": stats["compilation_unit_count"],
                "oversized_unresolved_count": stats["oversized_unresolved_count"],
                "scope_counts": dict(sorted(stats["scope_counts"].items())),
                "serialized_chars": stats["serialized_chars"],
                "serialized_bytes": stats["serialized_bytes"],
            }
            for cap, stats in sorted(cap_stats.items())
        ],
        "canonical_run_id": manifest.get("canonical_run_id"),
        "canonical_manifest_sha256": canonical_manifest_sha256,
        "retrieval_unit_build_identity": ru_build_identity,
        "retrieval_unit_manifest_sha256": ru_manifest_sha256,
        "retrieval_unit_artifact_sha256": ru_artifact_sha256,
        "record_count": len(record_stats),
        "canonical_record_bytes": canonical_record_bytes,
        "canonical_record_chars": canonical_record_chars,
        "compilation_unit_count": len(unit_rows),
        "source_segment_count": profile_counters["ru_bound"] + profile_counters["ru_unbound"],
        "compilation_segment_count": len(compilation_segment_ids),
        "sidecar_segment_count": len(sidecar_rows),
        "split_parent_segment_count": len(split_parent_ids),
        "split_piece_segment_count": split_piece_count,
        "serialized_provider_payload_bytes": sum(row["serialized_bytes"] for row in unit_rows),
        "serialized_provider_payload_chars": sum(row["serialized_chars"] for row in unit_rows),
        "provider_payload_to_canonical_byte_ratio": round(sum(row["serialized_bytes"] for row in unit_rows) / canonical_record_bytes, 6) if canonical_record_bytes else None,
        "projection_record_bytes": sum(row["serialized_bytes"] for row in projection_rows),
        "projection_record_chars": sum(row["serialized_chars"] for row in projection_rows),
        "projection_to_canonical_byte_ratio": round(sum(row["serialized_bytes"] for row in projection_rows) / canonical_record_bytes, 6) if canonical_record_bytes else None,
        "counts": dict(sorted(profile_counters.items())),
        "omission_count": len(omissions),
        "oversized_unresolved_count": sum(1 for item in omissions if item.get("reason") == "source_meaningful_boundary_exceeds_hard_cap"),
        "provider_token_counts": None,
        "provider_calls": 0,
        "semantic_build_identity": semantic_build_identity,
        "record_stats": record_stats,
    }
    identity = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "semantic_build_identity": semantic_build_identity,
        "projection_policy_identity": policy.identity,
        "canonical_manifest_sha256": canonical_manifest_sha256,
        "retrieval_unit_build_identity": ru_build_identity,
        "retrieval_unit_manifest_sha256": ru_manifest_sha256,
        "retrieval_unit_artifact_sha256": ru_artifact_sha256,
        "compiler_contract": CompilerContract().to_dict(),
        "semantic_output_schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
        "provider_revision_status": "UNKNOWN",
        "execution_identity": None,
        "provider_calls": 0,
    }
    manifest_out = {
        "schema_version": U1_SCHEMA_VERSION,
        "status": "complete",
        "semantic_build_identity": semantic_build_identity,
        "canonical_manifest_sha256": canonical_manifest_sha256,
        "retrieval_unit_build_identity": ru_build_identity,
        "retrieval_unit_manifest_sha256": ru_manifest_sha256,
        "retrieval_unit_artifact_sha256": ru_artifact_sha256,
        "projection_policy_identity": policy.identity,
        "artifacts": {},
        "accounting": {
            "provider_calls": 0,
            "provider_token_counts": None,
            "record_count": len(record_stats),
            "compilation_unit_count": len(unit_rows),
            "source_segment_count": profile_counters["ru_bound"] + profile_counters["ru_unbound"],
            "compilation_segment_count": len(compilation_segment_ids),
            "sidecar_segment_count": len(sidecar_rows),
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    artifact_defs: dict[str, Any] = {
        "projection.jsonl.gz": projection_rows,
        "compilation_units.jsonl.gz": unit_rows,
        "projection_sidecar.jsonl.gz": sidecar_rows,
        "omission_ledger.jsonl.gz": omissions,
    }
    for name, rows in artifact_defs.items():
        digest, byte_count, row_count = _write_jsonl_gzip(output_root / name, rows)
        manifest_out["artifacts"][name] = {"path": name, "sha256": digest, "byte_count": byte_count, "row_count": row_count}
    for name, value in (
        ("profile.json", profile),
        ("sample_manifest.json", sample),
        ("semantic_output_schema.json", semantic_output_schema()),
        ("identity.json", identity),
    ):
        body = canonical_json_bytes(value)
        _write_immutable(output_root / name, body)
        manifest_out["artifacts"][name] = {"path": name, "sha256": _sha256_bytes(body), "byte_count": len(body)}
    _write_immutable(output_root / "manifest.json", canonical_json_bytes(manifest_out))
    return manifest_out


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build provider-free P05-W2-U1 artifacts")
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--retrieval-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--record-soft-cap", type=int, default=8_000)
    parser.add_argument("--hard-cap", type=int, default=16_000)
    parser.add_argument("--sample-target", type=int, default=42)
    args = parser.parse_args()
    result = build_u1(args.canonical_manifest, args.retrieval_manifest, args.output_root, policy=ProjectionPolicy(args.record_soft_cap, args.hard_cap), sample_target=args.sample_target)
    print(json.dumps({"status": result["status"], "semantic_build_identity": result["semantic_build_identity"], "artifact_root": str(args.output_root), "accounting": result["accounting"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "ACCOUNTING_SCHEMA_VERSION",
    "CAP_PROFILE_SOFT_CAPS",
    "CanonicalProvenance",
    "CompilerContract",
    "IDENTITY_SCHEMA_VERSION",
    "PROJECTION_POLICY_VERSION",
    "PROJECTION_SCHEMA_VERSION",
    "ProjectionPolicy",
    "ReuseDecision",
    "SAMPLE_SCHEMA_VERSION",
    "SEMANTIC_ITEM_KINDS",
    "SEMANTIC_OUTPUT_SCHEMA_IDENTITY",
    "SEMANTIC_OUTPUT_SCHEMA_VERSION",
    "SEGMENT_COVERAGE_DISPOSITIONS",
    "SemanticCompilerU1Error",
    "UNIT_POLICY_VERSION",
    "binding_materialization_identity",
    "build_u1",
    "decide_reuse",
    "execution_identity",
    "semantic_artifact_identity",
    "semantic_input_identity",
    "semantic_output_schema",
    "validate_semantic_output_envelope",
    "validate_semantic_output_schema",
    "validate_semantic_source_binding",
    "view_identity",
]
