"""Versioned, deterministic Retrieval Units derived from Canonical evidence.

This module is deliberately below retrieval selection: it creates auditable
source-occurrence units, not a BM25, Dense, Hybrid, or semantic index.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import gzip
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.contracts import CANONICAL_SCHEMA_VERSION
from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write


RETRIEVAL_UNIT_SCHEMA_VERSION = "phase04-retrieval-unit-0.1"
RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION = "phase04-retrieval-unit-build-0.1"
RETRIEVAL_UNIT_GENERATOR_VERSION = "phase04-rag-w1-builder-0.1"
IDENTITY_ADDRESS_POLICY_VERSION = "phase04-ru-source-occurrence-address-0.1"
TEXT_FRAGMENT_POLICY_VERSION = "unicode-codepoint-fixed-v1"
STRUCTURED_FRAGMENT_POLICY_VERSION = "scalar-line-greedy-v1"

_SUPPORTED_KINDS = {"rich_text", "structured_observation", "dialogue_graph", "unsupported"}
_NORMAL_RECORD_STATUSES = {"canonical", "canonical_with_anomalies"}
_LINEAGE_SCOPES = {"direct_raw", "inherited_parent_raw", "parsed_dependency"}


class RetrievalUnitError(ValueError):
    """Raised when a Canonical input cannot form a complete RU build."""


@dataclass(frozen=True)
class RetrievalUnitBuildConfig:
    """Projection configuration; query-time retrieval/assembly settings stay out."""

    text_fragment_chars: int = 1200
    structured_fragment_chars: int = 1200

    def __post_init__(self) -> None:
        for name in ("text_fragment_chars", "structured_fragment_chars"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def projection_dict(self) -> dict[str, Any]:
        return {
            "text_fragment_chars": self.text_fragment_chars,
            "structured_fragment_chars": self.structured_fragment_chars,
            "text_fragment_policy_version": TEXT_FRAGMENT_POLICY_VERSION,
            "structured_fragment_policy_version": STRUCTURED_FRAGMENT_POLICY_VERSION,
        }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalUnitError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RetrievalUnitError(f"{label} must be a list")
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _required_str(value: Mapping[str, Any], key: str, label: str, *, allow_empty: bool = False) -> str:
    result = value.get(key)
    if not isinstance(result, str) or (not allow_empty and not result):
        raise RetrievalUnitError(f"{label}.{key} must be {'a string' if allow_empty else 'a non-empty string'}")
    return result


def _required_int(value: Mapping[str, Any], key: str, label: str) -> int:
    result = value.get(key)
    if not _is_int(result) or result < 0:
        raise RetrievalUnitError(f"{label}.{key} must be a non-negative integer")
    return result


def _validate_raw_ref(value: Any, label: str) -> None:
    raw_ref = _mapping(value, label)
    for key in ("source", "locale", "run_id", "artifact_kind", "artifact_path", "artifact_sha256"):
        _required_str(raw_ref, key, label)
    digest = raw_ref["artifact_sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
        raise RetrievalUnitError(f"{label}.artifact_sha256 must be a SHA-256 hex digest")
    for key in ("json_pointer", "embedded_json_pointer"):
        pointer = raw_ref.get(key)
        if pointer is not None and (not isinstance(pointer, str) or (pointer and not pointer.startswith("/"))):
            raise RetrievalUnitError(f"{label}.{key} must be an RFC 6901 pointer, empty string, or null")


def _validate_lineage(value: Any, label: str) -> Mapping[str, Any]:
    lineage = _mapping(value, label)
    pointer = lineage.get("parsed_json_pointer")
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise RetrievalUnitError(f"{label}.parsed_json_pointer must be an RFC 6901 pointer or empty")
    scope = lineage.get("evidence_scope")
    if scope not in _LINEAGE_SCOPES:
        raise RetrievalUnitError(f"{label}.evidence_scope is not supported")
    raw_refs = _list(lineage.get("raw_refs"), f"{label}.raw_refs")
    for index, raw_ref in enumerate(raw_refs):
        _validate_raw_ref(raw_ref, f"{label}.raw_refs[{index}]")
    dependency_locator = lineage.get("dependency_locator")
    if scope in {"direct_raw", "inherited_parent_raw"} and not raw_refs:
        raise RetrievalUnitError(f"{label} with raw evidence scope requires RawRef entries")
    if scope == "parsed_dependency":
        if raw_refs or not isinstance(dependency_locator, str) or not dependency_locator:
            raise RetrievalUnitError(f"{label} parsed dependency must contain only a dependency locator")
    elif dependency_locator is not None:
        raise RetrievalUnitError(f"{label}.dependency_locator is only valid for parsed dependencies")
    return lineage


def _validate_source_position(value: Any, label: str) -> None:
    position = _mapping(value, label)
    pointer = position.get("json_pointer")
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise RetrievalUnitError(f"{label}.json_pointer must be an RFC 6901 pointer or empty")
    for key in ("array_index", "ordering"):
        number = position.get(key)
        if number is not None and (not _is_int(number) or number < 0):
            raise RetrievalUnitError(f"{label}.{key} must be null or a non-negative integer")
    _list(position.get("layout_path"), f"{label}.layout_path")


def _canonical_address(
    *,
    source_identity_key: str,
    record_id: str,
    section: Mapping[str, Any],
    context: Mapping[str, Any],
    unit: Mapping[str, Any],
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source_identity_key": source_identity_key,
        "record_id": record_id,
        "section_ordinal": section["ordinal"],
        "component_observation_key": context["observation_key"],
        "component_ordinal": context["ordinal"],
        "canonical_unit_ordinal": unit["ordinal"],
        "canonical_unit_kind": unit["kind"],
        "parsed_json_pointer": lineage["parsed_json_pointer"],
    }


def _source_order(record_index: int, address: Mapping[str, Any], nested_order: list[int]) -> list[int]:
    return [
        record_index,
        int(address["section_ordinal"]),
        int(address["component_ordinal"]),
        int(address["canonical_unit_ordinal"]),
        *nested_order,
    ]


def _unit_id(address: Mapping[str, Any], nested_selector: Mapping[str, Any], fragment_selector: Mapping[str, Any]) -> str:
    """Hash only a stable source occurrence and segmentation identity.

    Build/generator implementation identity intentionally remains outside this
    value, so an unrelated corpus record or implementation-only revision does
    not churn all retrieval source-occurrence identifiers.
    """

    occurrence = {key: value for key, value in address.items() if key != "record_id"}
    return sha256_json({
        "identity_address_policy_version": IDENTITY_ADDRESS_POLICY_VERSION,
        "source_occurrence_address": occurrence,
        "nested_selector": dict(nested_selector),
        "fragment_selector": dict(fragment_selector),
    })


def _text_fragments(text: str, limit: int) -> list[tuple[str, dict[str, Any]]]:
    if len(text) <= limit:
        return [(text, {"kind": "whole"})]
    count = (len(text) + limit - 1) // limit
    return [
        (
            text[start:start + limit],
            {
                "kind": "unicode_codepoint_range",
                "policy_version": TEXT_FRAGMENT_POLICY_VERSION,
                "limit": limit,
                "fragment_index": index,
                "fragment_count": count,
                "char_start": start,
                "char_end": min(start + limit, len(text)),
            },
        )
        for index, start in enumerate(range(0, len(text), limit))
    ]


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _scalar_paths(value: Any, pointer: str = "") -> Iterable[tuple[str, str]]:
    """Use the established W2 scalar traversal: sorted mappings, list order."""

    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            yield from _scalar_paths(value[key], f"{pointer}/{_pointer_token(str(key))}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalar_paths(item, f"{pointer}/{index}")
    elif isinstance(value, str):
        scalar = value.strip()
        if scalar:
            yield pointer, scalar
    elif isinstance(value, bool):
        yield pointer, "true" if value else "false"
    elif isinstance(value, (int, float)):
        yield pointer, str(value)


def _structured_fragments(decoded: Any, limit: int) -> list[tuple[str, dict[str, Any], dict[str, Any], list[int]]]:
    """Pack full scalar lines, hard-splitting only an oversized complete line."""

    lines = [
        {"scalar_line_index": index, "decoded_json_pointer": pointer, "scalar_text": scalar, "rendered": f"{pointer}\t{scalar}"}
        for index, (pointer, scalar) in enumerate(_scalar_paths(decoded))
    ]
    fragments: list[tuple[str, dict[str, Any], dict[str, Any], list[int]]] = []
    packed: list[dict[str, Any]] = []
    packed_length = 0

    def flush_packed() -> None:
        nonlocal packed, packed_length
        if not packed:
            return
        indices = [item["scalar_line_index"] for item in packed]
        pointers = [item["decoded_json_pointer"] for item in packed]
        fragments.append((
            "\n".join(item["rendered"] for item in packed),
            {"kind": "structured_scalars", "scalar_line_indices": indices, "decoded_json_pointers": pointers},
            {
                "kind": "packed_scalar_lines",
                "policy_version": STRUCTURED_FRAGMENT_POLICY_VERSION,
                "limit": limit,
                "scalar_line_start": indices[0],
                "scalar_line_end": indices[-1],
            },
            [indices[0], 0],
        ))
        packed = []
        packed_length = 0

    for line in lines:
        rendered = line["rendered"]
        if len(rendered) > limit:
            flush_packed()
            count = (len(rendered) + limit - 1) // limit
            for index, start in enumerate(range(0, len(rendered), limit)):
                end = min(start + limit, len(rendered))
                fragments.append((
                    rendered[start:end],
                    {
                        "kind": "structured_scalar_line",
                        "scalar_line_index": line["scalar_line_index"],
                        "decoded_json_pointer": line["decoded_json_pointer"],
                    },
                    {
                        "kind": "oversized_scalar_line_range",
                        "policy_version": STRUCTURED_FRAGMENT_POLICY_VERSION,
                        "limit": limit,
                        "scalar_line_index": line["scalar_line_index"],
                        "fragment_index": index,
                        "fragment_count": count,
                        "char_start": start,
                        "char_end": end,
                    },
                    [line["scalar_line_index"], index],
                ))
            continue
        proposed = len(rendered) if not packed else packed_length + 1 + len(rendered)
        if packed and proposed > limit:
            flush_packed()
        packed.append(line)
        packed_length = len(rendered) if len(packed) == 1 else packed_length + 1 + len(rendered)
    flush_packed()
    return fragments


def _rich_unit(
    *,
    text: str,
    address: Mapping[str, Any],
    common: Mapping[str, Any],
    config: RetrievalUnitBuildConfig,
) -> list[dict[str, Any]]:
    nested = {"kind": "rich_text", "normalized_text_pointer": "/normalized_text"}
    values: list[dict[str, Any]] = []
    for text_value, fragment_selector in _text_fragments(text, config.text_fragment_chars):
        fragment_index = int(fragment_selector.get("fragment_index", 0))
        values.append(_make_unit(
            text=text_value,
            content_type="rich_text",
            address=address,
            nested_selector=nested,
            fragment_selector=fragment_selector,
            common=common,
            structure={"rich_text": {"fragment_index": fragment_index, "fragment_count": int(fragment_selector.get("fragment_count", 1))}},
            source_order=_source_order(int(common["manifest_record_index"]), address, [fragment_index]),
        ))
    return values


def _structured_units(
    *,
    decoded: Any,
    address: Mapping[str, Any],
    common: Mapping[str, Any],
    config: RetrievalUnitBuildConfig,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for text, nested, fragment, nested_order in _structured_fragments(decoded, config.structured_fragment_chars):
        values.append(_make_unit(
            text=text,
            content_type="structured",
            address=address,
            nested_selector=nested,
            fragment_selector=fragment,
            common=common,
            structure={"structured": {"nested_selector": nested, "fragment_selector": fragment}},
            source_order=_source_order(int(common["manifest_record_index"]), address, nested_order),
        ))
    return values


def _dialogue_text(node: Mapping[str, Any]) -> str:
    """Use only actual option/dialogue fields; speaker is never inferred."""

    parts: list[str] = []
    for raw_key, rich_key in (("option", "option_rich_text"), ("dialogue", "dialogue_rich_text")):
        rich = node.get(rich_key)
        normalized = _text(rich.get("normalized_text")) if isinstance(rich, Mapping) else ""
        raw = _text(node.get(raw_key))
        if normalized or raw:
            parts.append(normalized or raw)
    return "\n".join(parts)


def _validate_dialogue(value: Any, label: str) -> list[Mapping[str, Any]]:
    graph = _mapping(value, label)
    if graph.get("kind") != "dialogue_graph":
        raise RetrievalUnitError(f"{label}.kind must be dialogue_graph")
    groups = _list(graph.get("groups"), f"{label}.groups")
    seen_group_orders: set[int] = set()
    result: list[Mapping[str, Any]] = []
    for group_index, raw_group in enumerate(groups):
        group = _mapping(raw_group, f"{label}.groups[{group_index}]")
        ordering = _required_int(group, "ordering", f"{label}.groups[{group_index}]")
        if ordering in seen_group_orders:
            raise RetrievalUnitError(f"{label}.groups has duplicate ordering {ordering}")
        seen_group_orders.add(ordering)
        _required_str(group, "source_path", f"{label}.groups[{group_index}]", allow_empty=True)
        root_id = group.get("root_id")
        if root_id is not None and not isinstance(root_id, str):
            raise RetrievalUnitError(f"{label}.groups[{group_index}].root_id must be a string or null")
        nodes = _list(group.get("nodes"), f"{label}.groups[{group_index}].nodes")
        edges = _list(group.get("edges"), f"{label}.groups[{group_index}].edges")
        node_ids: set[str] = set()
        for node_index, raw_node in enumerate(nodes):
            node = _mapping(raw_node, f"{label}.groups[{group_index}].nodes[{node_index}]")
            node_id = _required_str(node, "source_id", f"{label}.groups[{group_index}].nodes[{node_index}]")
            if node_id in node_ids:
                raise RetrievalUnitError(f"{label}.groups[{group_index}] has duplicate node source_id {node_id!r}")
            node_ids.add(node_id)
            _required_int(node, "ordering", f"{label}.groups[{group_index}].nodes[{node_index}]")
            if "option" not in node or "dialogue" not in node:
                raise RetrievalUnitError(f"{label}.groups[{group_index}].nodes[{node_index}] lacks option or dialogue")
            speaker = node.get("speaker")
            if speaker is not None and not isinstance(speaker, str):
                raise RetrievalUnitError(f"{label}.groups[{group_index}].nodes[{node_index}].speaker must be a string or null")
            raw_ref = node.get("raw_ref")
            if raw_ref is not None:
                _validate_raw_ref(raw_ref, f"{label}.groups[{group_index}].nodes[{node_index}].raw_ref")
            _validate_source_position(node.get("source_position"), f"{label}.groups[{group_index}].nodes[{node_index}].source_position")
        for edge_index, raw_edge in enumerate(edges):
            edge = _mapping(raw_edge, f"{label}.groups[{group_index}].edges[{edge_index}]")
            _required_str(edge, "parent_id", f"{label}.groups[{group_index}].edges[{edge_index}]")
            _required_str(edge, "child_id", f"{label}.groups[{group_index}].edges[{edge_index}]")
            _required_int(edge, "ordering", f"{label}.groups[{group_index}].edges[{edge_index}]")
            _validate_raw_ref(edge.get("raw_ref"), f"{label}.groups[{group_index}].edges[{edge_index}].raw_ref")
            _validate_source_position(edge.get("source_position"), f"{label}.groups[{group_index}].edges[{edge_index}].source_position")
        result.append(group)
    return result


def _dialogue_units(
    *,
    groups: list[Mapping[str, Any]],
    address: Mapping[str, Any],
    common: Mapping[str, Any],
    config: RetrievalUnitBuildConfig,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for group in sorted(groups, key=lambda item: int(item["ordering"])):
        group_ordering = int(group["ordering"])
        edges = [dict(item) for item in group["edges"] if isinstance(item, Mapping)]
        nodes = [item for item in group["nodes"] if isinstance(item, Mapping)]
        for node in sorted(nodes, key=lambda item: (int(item["ordering"]), str(item["source_id"]))):
            node_id = str(node["source_id"])
            text = _dialogue_text(node)
            if not text:
                continue
            node_ordering = int(node["ordering"])
            incident_edges = [
                edge for edge in edges if edge.get("parent_id") == node_id or edge.get("child_id") == node_id
            ]
            nested = {"kind": "dialogue_node", "group_ordering": group_ordering, "node_source_id": node_id}
            for text_value, fragment_selector in _text_fragments(text, config.text_fragment_chars):
                fragment_index = int(fragment_selector.get("fragment_index", 0))
                structure = {
                    "dialogue": {
                        "group_ordering": group_ordering,
                        "node_source_id": node_id,
                        "node_ordering": node_ordering,
                        "speaker": node.get("speaker"),
                        "node_raw_ref": node.get("raw_ref"),
                        "observed_edges": incident_edges,
                        "fragment_index": fragment_index,
                        "fragment_count": int(fragment_selector.get("fragment_count", 1)),
                    },
                }
                values.append(_make_unit(
                    text=text_value,
                    content_type="dialogue_node",
                    address=address,
                    nested_selector=nested,
                    fragment_selector=fragment_selector,
                    common=common,
                    structure=structure,
                    source_order=_source_order(int(common["manifest_record_index"]), address, [group_ordering, node_ordering, fragment_index]),
                ))
    return values


def _make_unit(
    *,
    text: str,
    content_type: str,
    address: Mapping[str, Any],
    nested_selector: Mapping[str, Any],
    fragment_selector: Mapping[str, Any],
    common: Mapping[str, Any],
    structure: Mapping[str, Any],
    source_order: list[int],
) -> dict[str, Any]:
    return {
        "schema_version": RETRIEVAL_UNIT_SCHEMA_VERSION,
        "unit_id": _unit_id(address, nested_selector, fragment_selector),
        "content_type": content_type,
        "retrieval_visible_text": text,
        "source": {
            "canonical_address": dict(address),
            "lineage": dict(common["lineage"]),
            "record_context": dict(common["record_context"]),
            "provenance": dict(common["provenance"]),
            "content_role": dict(common["content_role"]),
        },
        "nested_selector": dict(nested_selector),
        "fragment_selector": dict(fragment_selector),
        "structure": dict(structure),
        "source_order": list(source_order),
    }


def _validate_manifest(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if manifest.get("status") != "complete":
        raise RetrievalUnitError("Canonical manifest must be complete")
    records = _list(manifest.get("records"), "Canonical manifest.records")
    if manifest.get("input_record_count") != len(records) or manifest.get("accounted_record_count") != len(records):
        raise RetrievalUnitError("Canonical manifest accounting does not match record entries")
    if manifest.get("input_integrity_failure_count") != 0:
        raise RetrievalUnitError("Canonical manifest has input-integrity failures")
    _required_str(manifest, "canonical_run_id", "Canonical manifest")
    dependencies = _mapping(manifest.get("dependencies"), "Canonical manifest.dependencies")
    versions = _mapping(dependencies.get("canonical_versions"), "Canonical manifest.dependencies.canonical_versions")
    if versions.get("schema_version") != CANONICAL_SCHEMA_VERSION:
        raise RetrievalUnitError("Canonical manifest uses an unsupported Canonical schema version")
    return [_mapping(entry, f"Canonical manifest.records[{index}]") for index, entry in enumerate(records)]


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalUnitError(f"cannot read {label}") from exc
    return _mapping(value, label)


def _load_record(entry: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    path_value = _required_str(entry, "canonical_record_path", f"Canonical manifest.records[{index}]")
    expected_sha = _required_str(entry, "canonical_record_sha256", f"Canonical manifest.records[{index}]")
    try:
        body = Path(path_value).read_bytes()
    except OSError as exc:
        raise RetrievalUnitError(f"cannot read Canonical record {index}") from exc
    if sha256(body).hexdigest() != expected_sha:
        raise RetrievalUnitError(f"Canonical record {index} SHA-256 mismatch")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalUnitError(f"Canonical record {index} is not valid UTF-8 JSON") from exc
    record = _mapping(value, f"Canonical record {index}")
    if record.get("record_id") != entry.get("record_id"):
        raise RetrievalUnitError(f"Canonical record {index} identity mismatch")
    return record


def _validate_record(
    record: Mapping[str, Any],
    entry: Mapping[str, Any],
    record_index: int,
    config: RetrievalUnitBuildConfig,
    *,
    source: str,
    locale: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    record_label = f"Canonical record {record_index}"
    record_id = _required_str(record, "record_id", record_label)
    status = record.get("status")
    if status not in _NORMAL_RECORD_STATUSES or entry.get("canonical_status") != status:
        raise RetrievalUnitError(f"{record_label} has unsupported or inconsistent record status")
    _validate_lineage(record.get("lineage"), f"{record_label}.lineage")
    observation = _mapping(record.get("observation"), f"{record_label}.observation")
    for key in ("parsed_run_id", "parsed_manifest_sha256", "parsed_record_sha256", "parsed_schema_version", "parsed_parser_version", "parsed_pipeline_version"):
        _required_str(observation, key, f"{record_label}.observation")
    versions = _mapping(record.get("versions"), f"{record_label}.versions")
    if versions.get("schema_version") != CANONICAL_SCHEMA_VERSION:
        raise RetrievalUnitError(f"{record_label} uses an unsupported Canonical schema version")
    for key in ("transform_version", "structural_normalization_version"):
        _required_str(versions, key, f"{record_label}.versions")
    if not isinstance(versions.get("classification_rule_versions"), Mapping):
        raise RetrievalUnitError(f"{record_label}.versions.classification_rule_versions must be an object")
    source_identity = _mapping(record.get("source_identity"), f"{record_label}.source_identity")
    if source_identity.get("kind") != "detail" or source_identity.get("stability") != "logical":
        raise RetrievalUnitError(f"{record_label}.source_identity is not a verified logical detail identity")
    source_identity_key = _required_str(source_identity, "key", f"{record_label}.source_identity")
    source_components = _mapping(source_identity.get("components"), f"{record_label}.source_identity.components")
    content_id = _required_str(source_components, "content_id", f"{record_label}.source_identity.components")
    if source_components.get("source") != source or source_components.get("locale") != locale:
        raise RetrievalUnitError(f"{record_label}.source_identity does not match Canonical manifest source/locale")
    if source_identity_key != f"{source}:{locale}:{content_id}":
        raise RetrievalUnitError(f"{record_label}.source_identity key is inconsistent with its observed components")
    record_metadata = _mapping(record.get("record_metadata"), f"{record_label}.record_metadata")
    record_title = _text(record_metadata.get("name"))
    sections = _list(record.get("sections"), f"{record_label}.sections")
    seen_section_ordinals: set[int] = set()
    units_out: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    for section_index, raw_section in enumerate(sections):
        section = _mapping(raw_section, f"{record_label}.sections[{section_index}]")
        section_ordinal = _required_int(section, "ordinal", f"{record_label}.sections[{section_index}]")
        if section_ordinal in seen_section_ordinals:
            raise RetrievalUnitError(f"{record_label} has duplicate section ordinal {section_ordinal}")
        seen_section_ordinals.add(section_ordinal)
        _validate_lineage(section.get("lineage"), f"{record_label}.sections[{section_index}].lineage")
        source_metadata = _mapping(section.get("source_metadata"), f"{record_label}.sections[{section_index}].source_metadata")
        section_name = _text(source_metadata.get("name"))
        contexts = _list(section.get("component_contexts"), f"{record_label}.sections[{section_index}].component_contexts")
        raw_units = _list(section.get("units"), f"{record_label}.sections[{section_index}].units")
        contexts_by_key: dict[str, Mapping[str, Any]] = {}
        for context_index, raw_context in enumerate(contexts):
            context = _mapping(raw_context, f"{record_label}.sections[{section_index}].component_contexts[{context_index}]")
            key = _required_str(context, "observation_key", f"{record_label}.sections[{section_index}].component_contexts[{context_index}]")
            if key in contexts_by_key:
                raise RetrievalUnitError(f"{record_label}.sections[{section_index}] has duplicate ComponentContext key {key!r}")
            _required_int(context, "ordinal", f"{record_label}.sections[{section_index}].component_contexts[{context_index}]")
            _validate_lineage(context.get("lineage"), f"{record_label}.sections[{section_index}].component_contexts[{context_index}].lineage")
            _validate_source_position(context.get("source_position"), f"{record_label}.sections[{section_index}].component_contexts[{context_index}].source_position")
            component_id = context.get("source_component_id")
            if component_id is not None and not isinstance(component_id, str):
                raise RetrievalUnitError(f"{record_label} ComponentContext source_component_id must be a string or null")
            contexts_by_key[key] = context
        by_context: dict[str, list[Mapping[str, Any]]] = {key: [] for key in contexts_by_key}
        for unit_index, raw_unit in enumerate(raw_units):
            unit = _mapping(raw_unit, f"{record_label}.sections[{section_index}].units[{unit_index}]")
            kind = unit.get("kind")
            if kind not in _SUPPORTED_KINDS:
                raise RetrievalUnitError(f"{record_label} has unknown Canonical unit kind {kind!r}")
            parent_key = _required_str(unit, "parent_component_key", f"{record_label}.sections[{section_index}].units[{unit_index}]")
            if parent_key not in contexts_by_key:
                raise RetrievalUnitError(f"{record_label} unit has no matching ComponentContext")
            _required_int(unit, "ordinal", f"{record_label}.sections[{section_index}].units[{unit_index}]")
            _validate_lineage(unit.get("lineage"), f"{record_label}.sections[{section_index}].units[{unit_index}].lineage")
            if not isinstance(unit.get("metadata"), Mapping) or not isinstance(unit.get("provenance"), Mapping) or not isinstance(unit.get("content_role"), Mapping):
                raise RetrievalUnitError(f"{record_label} unit lacks supported metadata/provenance/content-role shape")
            by_context[parent_key].append(unit)
        for context_key, context in contexts_by_key.items():
            child_units = by_context[context_key]
            ordinals = [item["ordinal"] for item in child_units]
            if len(ordinals) != len(set(ordinals)):
                raise RetrievalUnitError(f"{record_label} ComponentContext has duplicate unit ordinal")
            expected_ordinals = _list(context.get("child_unit_ordinals"), f"{record_label} ComponentContext.child_unit_ordinals")
            if expected_ordinals != ordinals or context.get("unit_count") != len(ordinals):
                raise RetrievalUnitError(f"{record_label} ComponentContext child-unit accounting does not match units")
            for unit in child_units:
                address = _canonical_address(
                    source_identity_key=source_identity_key,
                    record_id=record_id,
                    section=section,
                    context=context,
                    unit=unit,
                    lineage=_mapping(unit["lineage"], "unit lineage"),
                )
                common = {
                    "manifest_record_index": record_index,
                    "lineage": unit["lineage"],
                    "record_context": {
                        "record_title": record_title,
                        "section_name": section_name,
                        "source_component_id": context.get("source_component_id"),
                    },
                    "provenance": unit["provenance"],
                    "content_role": unit["content_role"],
                }
                kind = unit["kind"]
                value = unit.get("value")
                generated: list[dict[str, Any]] = []
                skip_reason: str | None = None
                if kind == "unsupported":
                    skip_reason = "known_unsupported_canonical_unit"
                elif kind == "rich_text":
                    rich_value = _mapping(value, f"{record_label} rich_text value")
                    if not isinstance(rich_value.get("normalized_text"), str):
                        raise RetrievalUnitError(f"{record_label} rich_text lacks normalized_text")
                    text = _text(rich_value["normalized_text"])
                    generated = _rich_unit(text=text, address=address, common=common, config=config) if text else []
                    skip_reason = None if generated else "known_empty_rich_text"
                elif kind == "structured_observation":
                    structured_value = _mapping(value, f"{record_label} structured_observation value")
                    if "decoded" not in structured_value:
                        raise RetrievalUnitError(f"{record_label} structured_observation lacks decoded")
                    generated = _structured_units(decoded=structured_value["decoded"], address=address, common=common, config=config)
                    skip_reason = None if generated else "known_structured_without_scalar"
                elif kind == "dialogue_graph":
                    groups = _validate_dialogue(value, f"{record_label} dialogue_graph value")
                    generated = _dialogue_units(groups=groups, address=address, common=common, config=config)
                    skip_reason = None if generated else "known_dialogue_without_text"
                if generated:
                    units_out.extend(generated)
                else:
                    skips.append({
                        "schema_version": RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION,
                        "skip_id": sha256_json({"address": address, "reason": skip_reason}),
                        "reason": skip_reason,
                        "canonical_address": address,
                        "lineage": unit["lineage"],
                    })
    return units_out, skips


def _gzip_jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    output = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        for row in rows:
            compressed.write(canonical_json_bytes(dict(row)) + b"\n")
    return output.getvalue()


def _artifact_descriptor(relative_path: str, body: bytes, count: int | None = None) -> dict[str, Any]:
    descriptor = {"path": relative_path, "sha256": sha256(body).hexdigest(), "byte_count": len(body)}
    if count is not None:
        descriptor["row_count"] = count
    return descriptor


def _canonical_input_identity(manifest: Mapping[str, Any], entries: list[Mapping[str, Any]]) -> str:
    """Identify Canonical content inputs without hashing workstation paths."""

    return sha256_json({
        "canonical_run_id": manifest["canonical_run_id"],
        "source": manifest["source"],
        "locale": manifest["locale"],
        "canonical_dependencies": manifest["dependencies"]["canonical_versions"],
        "records": [
            {
                "record_id": entry.get("record_id"),
                "canonical_record_sha256": entry.get("canonical_record_sha256"),
                "canonical_status": entry.get("canonical_status"),
            }
            for entry in entries
        ],
    })


def _build_identity(manifest: Mapping[str, Any], canonical_input_identity: str, config: RetrievalUnitBuildConfig) -> str:
    return sha256_json({
        "canonical_input": {
            "canonical_run_id": manifest["canonical_run_id"],
            "canonical_input_identity": canonical_input_identity,
            "canonical_schema_version": manifest["dependencies"]["canonical_versions"]["schema_version"],
        },
        "retrieval_unit_schema_version": RETRIEVAL_UNIT_SCHEMA_VERSION,
        "identity_address_policy_version": IDENTITY_ADDRESS_POLICY_VERSION,
        "generator_version": RETRIEVAL_UNIT_GENERATOR_VERSION,
        "projection_config": config.projection_dict(),
    })


def _failure_ledger(output_root: Path, *, build_identity: str | None, error: Exception) -> None:
    manifest_path = output_root / "metadata" / "manifest.json"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, Mapping) and existing.get("status") == "complete":
            return
    payload = {
        "schema_version": RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION,
        "status": "failed",
        "build_identity": build_identity,
        "failure_count": 1,
        "failures": [{"code": "CANONICAL_INPUT_UNSUPPORTED", "message": str(error)}],
    }
    atomic_write(output_root / "metadata" / "failure_ledger.json", canonical_json_bytes(payload))


def build_retrieval_units(
    canonical_manifest_path: Path,
    output_root: Path,
    *,
    config: RetrievalUnitBuildConfig | None = None,
) -> dict[str, Any]:
    """Create a complete immutable RU build, or fail closed with an audit ledger."""

    config = config or RetrievalUnitBuildConfig()
    canonical_manifest_path = Path(canonical_manifest_path)
    output_root = Path(output_root)
    build_identity: str | None = None
    try:
        try:
            manifest_body = canonical_manifest_path.read_bytes()
        except OSError as exc:
            raise RetrievalUnitError("cannot read Canonical manifest") from exc
        manifest = _mapping(json.loads(manifest_body.decode("utf-8")), "Canonical manifest")
        entries = _validate_manifest(manifest)
        source = _required_str(manifest, "source", "Canonical manifest")
        locale = _required_str(manifest, "locale", "Canonical manifest")
        canonical_input_identity = _canonical_input_identity(manifest, entries)
        build_identity = _build_identity(manifest, canonical_input_identity, config)
        existing_path = output_root / "metadata" / "manifest.json"
        if existing_path.exists():
            existing = _read_json(existing_path, "Retrieval Unit manifest")
            if existing.get("status") == "complete" and existing.get("build_identity") == build_identity:
                return dict(existing)
            if existing.get("status") == "complete":
                raise FileExistsError("Retrieval Unit build is already complete with different inputs")
        all_units: list[dict[str, Any]] = []
        skips: list[dict[str, Any]] = []
        for record_index, entry in enumerate(entries):
            record = _load_record(entry, record_index)
            record_units, record_skips = _validate_record(
                record,
                entry,
                record_index,
                config,
                source=source,
                locale=locale,
            )
            all_units.extend(record_units)
            skips.extend(record_skips)
        ids = [unit["unit_id"] for unit in all_units]
        if len(ids) != len(set(ids)):
            raise RetrievalUnitError("Retrieval Unit source-occurrence identity collision")
        all_units.sort(key=lambda item: (tuple(item["source_order"]), item["unit_id"]))
        skips.sort(key=lambda item: (item["canonical_address"]["record_id"], item["canonical_address"]["section_ordinal"], item["canonical_address"]["component_ordinal"], item["canonical_address"]["canonical_unit_ordinal"], item["reason"]))
        units_body = _gzip_jsonl_bytes(all_units)
        skips_body = _gzip_jsonl_bytes(skips)
        clean_failure = {
            "schema_version": RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION,
            "status": "clear",
            "build_identity": build_identity,
            "failure_count": 0,
            "failures": [],
        }
        failure_body = canonical_json_bytes(clean_failure)
        skip_counts = dict(sorted(Counter(item["reason"] for item in skips).items()))
        result = {
            "schema_version": RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION,
            "status": "complete",
            "build_identity": build_identity,
            "canonical_input": {
                "canonical_run_id": manifest["canonical_run_id"],
                "canonical_input_identity": canonical_input_identity,
                "canonical_schema_version": manifest["dependencies"]["canonical_versions"]["schema_version"],
            },
            "retrieval_unit_schema_version": RETRIEVAL_UNIT_SCHEMA_VERSION,
            "identity_address_policy_version": IDENTITY_ADDRESS_POLICY_VERSION,
            "generator_version": RETRIEVAL_UNIT_GENERATOR_VERSION,
            "projection_config": config.projection_dict(),
            "artifacts": {
                "retrieval_units": _artifact_descriptor("artifacts/retrieval_units.jsonl.gz", units_body, len(all_units)),
                "skip_ledger": _artifact_descriptor("artifacts/skip_ledger.jsonl.gz", skips_body, len(skips)),
                "failure_ledger": _artifact_descriptor("metadata/failure_ledger.json", failure_body),
            },
            "accounting": {
                "canonical_record_count": len(entries),
                "verified_canonical_record_count": len(entries),
                "retrieval_unit_count": len(all_units),
                "skipped_count": len(skips),
                "skip_reason_counts": skip_counts,
                "failure_count": 0,
            },
        }
        # Artifacts precede the manifest, so a visible complete manifest is always loadable.
        atomic_write(output_root / "artifacts" / "retrieval_units.jsonl.gz", units_body)
        atomic_write(output_root / "artifacts" / "skip_ledger.jsonl.gz", skips_body)
        atomic_write(output_root / "metadata" / "failure_ledger.json", failure_body)
        atomic_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(result))
        return result
    except Exception as exc:
        _failure_ledger(output_root, build_identity=build_identity, error=exc)
        raise


def _read_gzip_jsonl(path: Path, label: str) -> list[Mapping[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            values = [json.loads(line) for line in handle if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        raise RetrievalUnitError(f"cannot read {label}: {path}") from exc
    return [_mapping(value, f"{label} row {index}") for index, value in enumerate(values, 1)]


def load_retrieval_units(build_manifest_path: Path) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Load a completed RU artifact and verify every recorded content hash."""

    build_manifest_path = Path(build_manifest_path)
    manifest = _read_json(build_manifest_path, "Retrieval Unit manifest")
    if manifest.get("status") != "complete" or manifest.get("schema_version") != RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION:
        raise RetrievalUnitError("Retrieval Unit manifest is not a supported complete build")
    artifacts = _mapping(manifest.get("artifacts"), "Retrieval Unit manifest.artifacts")
    root = build_manifest_path.parent.parent

    def artifact_body(key: str) -> tuple[Mapping[str, Any], Path, bytes]:
        descriptor = _mapping(artifacts.get(key), f"Retrieval Unit manifest.artifacts.{key}")
        relative_path = _required_str(descriptor, "path", f"Retrieval Unit {key} artifact")
        path = root / relative_path
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise RetrievalUnitError(f"cannot read Retrieval Unit {key} artifact") from exc
        if sha256(body).hexdigest() != descriptor.get("sha256"):
            raise RetrievalUnitError(f"Retrieval Unit {key} artifact SHA-256 mismatch")
        if descriptor.get("byte_count") != len(body):
            raise RetrievalUnitError(f"Retrieval Unit {key} artifact byte accounting mismatch")
        return descriptor, path, body

    descriptor, path, _ = artifact_body("retrieval_units")
    rows = _read_gzip_jsonl(path, "Retrieval Unit artifact")
    if descriptor.get("row_count") != len(rows):
        raise RetrievalUnitError("Retrieval Unit artifact accounting mismatch")
    skip_descriptor, skip_path, _ = artifact_body("skip_ledger")
    skips = _read_gzip_jsonl(skip_path, "Retrieval Unit skip ledger")
    if skip_descriptor.get("row_count") != len(skips):
        raise RetrievalUnitError("Retrieval Unit skip ledger accounting mismatch")
    failure_descriptor, _, failure_body = artifact_body("failure_ledger")
    try:
        failure = _mapping(json.loads(failure_body.decode("utf-8")), "Retrieval Unit failure ledger")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalUnitError("Retrieval Unit failure ledger is not valid JSON") from exc
    if failure_descriptor.get("byte_count") != len(failure_body) or failure.get("status") != "clear" or failure.get("failure_count") != 0:
        raise RetrievalUnitError("Retrieval Unit build failure ledger is not clear")
    for index, row in enumerate(rows, 1):
        if row.get("schema_version") != RETRIEVAL_UNIT_SCHEMA_VERSION or not isinstance(row.get("unit_id"), str):
            raise RetrievalUnitError(f"invalid Retrieval Unit row {index}")
    return manifest, rows
