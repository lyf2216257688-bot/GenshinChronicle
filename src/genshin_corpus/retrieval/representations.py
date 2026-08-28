"""Deterministic, rebuildable Phase 04 retrieval representations.

The representations in this module are experiment artifacts derived from one
immutable Canonical run.  They are not Canonical objects, semantic identities,
or a production retrieval index.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json


RETRIEVAL_REPRESENTATION_VERSION = "phase04-derived-representation-0.2"
RETRIEVAL_DOCUMENT_SCHEMA_VERSION = "phase04-retrieval-document-0.1"
REPRESENTATION_ARMS = (
    "naked_leaf",
    "contextualized_leaf",
    "structured_path_value",
    "dialogue_graph_local",
)


class RetrievalRepresentationError(ValueError):
    """Raised when a Canonical dependency or derived artifact is invalid."""


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalRepresentationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, Mapping):
        raise RetrievalRepresentationError(f"{label} must be an object: {path}")
    return value


def _validate_canonical_manifest(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = manifest.get("records")
    if manifest.get("status") != "complete" or not isinstance(records, list):
        raise RetrievalRepresentationError("Canonical manifest must be complete with records")
    if manifest.get("input_record_count") != len(records) or manifest.get("accounted_record_count") != len(records):
        raise RetrievalRepresentationError("Canonical manifest accounting does not match record entries")
    if manifest.get("input_integrity_failure_count") != 0:
        raise RetrievalRepresentationError("Canonical manifest has input-integrity failures")
    return records


def _load_canonical_record(entry: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    path_value = entry.get("canonical_record_path")
    expected_sha = entry.get("canonical_record_sha256")
    if not isinstance(path_value, str) or not isinstance(expected_sha, str):
        raise RetrievalRepresentationError(f"Canonical manifest record {index} lacks path or SHA-256")
    path = Path(path_value)
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise RetrievalRepresentationError(f"cannot read Canonical record {index}: {path}") from exc
    if hashlib.sha256(body).hexdigest() != expected_sha:
        raise RetrievalRepresentationError(f"Canonical record SHA-256 mismatch: {path}")
    try:
        record = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalRepresentationError(f"Canonical record is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(record, Mapping) or record.get("record_id") != entry.get("record_id"):
        raise RetrievalRepresentationError(f"Canonical record identity mismatch: {path}")
    return record


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _scalar_paths(value: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Yield decoded scalar paths without assigning row/column semantics."""

    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            yield from _scalar_paths(value[key], f"{pointer}/{_pointer_token(str(key))}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalar_paths(item, f"{pointer}/{index}")
    elif isinstance(value, str):
        text = value.strip()
        if text:
            yield pointer, text
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield pointer, str(value)
    elif isinstance(value, bool):
        yield pointer, "true" if value else "false"


def _coverage(
    record: Mapping[str, Any],
    section: Mapping[str, Any],
    context: Mapping[str, Any],
    unit: Mapping[str, Any],
    *,
    decoded_json_pointers: list[str] | None = None,
    dialogue: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    lineage = unit.get("lineage")
    if not isinstance(lineage, Mapping):
        raise RetrievalRepresentationError("Canonical unit lacks lineage")
    record_id = record.get("record_id")
    section_ordinal = section.get("ordinal")
    context_key = context.get("observation_key")
    unit_ordinal = unit.get("ordinal")
    if not isinstance(record_id, str) or not isinstance(section_ordinal, int) or not isinstance(context_key, str) or not isinstance(unit_ordinal, int):
        raise RetrievalRepresentationError("Canonical unit address is incomplete")
    coverage: dict[str, Any] = {
        "record_id": record_id,
        "section_ordinal": section_ordinal,
        "component_observation_key": context_key,
        "unit_ordinal": unit_ordinal,
        "lineage": dict(lineage),
    }
    if decoded_json_pointers is not None:
        coverage["decoded_json_pointers"] = decoded_json_pointers
    if dialogue is not None:
        coverage["dialogue"] = dict(dialogue)
    return coverage


def _document(arm: str, text: str, coverage: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not text:
        raise RetrievalRepresentationError("derived Retrieval document text must not be empty")
    identifier = sha256_json({
        "representation_version": RETRIEVAL_REPRESENTATION_VERSION,
        "arm": arm,
        "text": text,
        "coverage": coverage,
        "metadata": metadata,
    })
    return {
        "schema_version": RETRIEVAL_DOCUMENT_SCHEMA_VERSION,
        "document_id": identifier,
        "representation_version": RETRIEVAL_REPRESENTATION_VERSION,
        "arm": arm,
        "text": text,
        "source_coverage": [dict(coverage)],
        "metadata": dict(metadata),
    }


def iter_retrieval_documents(record: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Create the approved W2 experiment arms for one Canonical record."""

    metadata = record.get("record_metadata")
    title = _text(metadata.get("name")) if isinstance(metadata, Mapping) else ""
    sections = record.get("sections")
    if not isinstance(sections, list):
        raise RetrievalRepresentationError("Canonical record sections must be a list")
    for section in sections:
        if not isinstance(section, Mapping):
            raise RetrievalRepresentationError("Canonical section must be an object")
        source_metadata = section.get("source_metadata")
        section_name = _text(source_metadata.get("name")) if isinstance(source_metadata, Mapping) else ""
        contexts = section.get("component_contexts")
        units = section.get("units")
        if not isinstance(contexts, list) or not isinstance(units, list):
            raise RetrievalRepresentationError("Canonical section members must be lists")
        contexts_by_key = {
            context.get("observation_key"): context
            for context in contexts
            if isinstance(context, Mapping) and isinstance(context.get("observation_key"), str)
        }
        for unit in units:
            if not isinstance(unit, Mapping):
                raise RetrievalRepresentationError("Canonical unit must be an object")
            context = contexts_by_key.get(unit.get("parent_component_key"))
            if not isinstance(context, Mapping):
                raise RetrievalRepresentationError("Canonical unit has no matching ComponentContext")
            component_id = _text(context.get("source_component_id"))
            unit_kind = _text(unit.get("kind"))
            base_metadata = {
                "record_title": title,
                "section_name": section_name,
                "component_id": component_id,
                "unit_kind": unit_kind,
            }
            value = unit.get("value")
            if unit_kind == "rich_text" and isinstance(value, Mapping):
                leaf_text = _text(value.get("normalized_text"))
                if leaf_text:
                    coverage = _coverage(record, section, context, unit)
                    yield _document("naked_leaf", leaf_text, coverage, base_metadata)
                    context_parts = [part for part in (title, section_name, component_id, leaf_text) if part]
                    yield _document("contextualized_leaf", "\n".join(context_parts), coverage, base_metadata)
            elif unit_kind == "structured_observation" and isinstance(value, Mapping):
                decoded = value.get("decoded")
                scalar_paths = list(_scalar_paths(decoded)) if decoded is not None else []
                if scalar_paths:
                    paths = [""] + [pointer for pointer, _ in scalar_paths]
                    projection = "\n".join(f"{pointer}\t{scalar}" for pointer, scalar in scalar_paths)
                    coverage = _coverage(record, section, context, unit, decoded_json_pointers=paths)
                    yield _document("structured_path_value", projection, coverage, base_metadata)
            elif unit_kind == "dialogue_graph" and isinstance(value, Mapping):
                groups = value.get("groups")
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if not isinstance(group, Mapping) or not isinstance(group.get("ordering"), int):
                        continue
                    ordering = group["ordering"]
                    nodes = group.get("nodes")
                    node_by_id = {
                        node.get("source_id"): node
                        for node in (nodes if isinstance(nodes, list) else [])
                        if isinstance(node, Mapping) and isinstance(node.get("source_id"), str)
                    }
                    edges = group.get("edges")
                    used_nodes: set[str] = set()
                    if isinstance(edges, list):
                        for edge in edges:
                            if not isinstance(edge, Mapping):
                                continue
                            parent_id = edge.get("parent_id")
                            child_id = edge.get("child_id")
                            if not isinstance(parent_id, str) or not isinstance(child_id, str):
                                continue
                            parent = node_by_id.get(parent_id, {})
                            child = node_by_id.get(child_id, {})
                            parts = [
                                _text(parent.get("option")) if isinstance(parent, Mapping) else "",
                                _text(parent.get("dialogue")) if isinstance(parent, Mapping) else "",
                                _text(child.get("option")) if isinstance(child, Mapping) else "",
                                _text(child.get("dialogue")) if isinstance(child, Mapping) else "",
                            ]
                            text = "\n".join(part for part in parts if part)
                            if not text:
                                continue
                            used_nodes.update((parent_id, child_id))
                            dialogue = {
                                "group_ordering": ordering,
                                "node_source_ids": [parent_id, child_id],
                                "edges": [{"parent_id": parent_id, "child_id": child_id}],
                            }
                            yield _document(
                                "dialogue_graph_local",
                                text,
                                _coverage(record, section, context, unit, dialogue=dialogue),
                                base_metadata,
                            )
                    for node_id, node in node_by_id.items():
                        if node_id in used_nodes:
                            continue
                        text = "\n".join(part for part in (_text(node.get("option")), _text(node.get("dialogue"))) if part)
                        if text:
                            dialogue = {"group_ordering": ordering, "node_source_ids": [node_id], "edges": []}
                            yield _document(
                                "dialogue_graph_local",
                                text,
                                _coverage(record, section, context, unit, dialogue=dialogue),
                                base_metadata,
                            )


def _gzip_jsonl_writer(path: Path) -> tuple[Any, Any, Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    binary = os.fdopen(descriptor, "wb")
    return binary, gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0), Path(temporary_name)


def _finish_gzip_writer(binary: Any, compressed: Any, temporary: Path, target: Path) -> tuple[str, int]:
    try:
        compressed.close()
        binary.close()
        body = temporary.read_bytes()
        if target.exists() and target.read_bytes() != body:
            raise FileExistsError(f"Retrieval artifact already exists with different bytes: {target}")
        if not target.exists():
            os.replace(temporary, target)
        return hashlib.sha256(body).hexdigest(), len(body)
    finally:
        if not binary.closed:
            binary.close()
        if temporary.exists():
            temporary.unlink()


def build_retrieval_documents(manifest_path: Path, output_root: Path) -> dict[str, Any]:
    """Materialize W2 representations in one manifest-ordered Canonical pass."""

    manifest_path = Path(manifest_path)
    manifest = _read_object(manifest_path, "Canonical manifest")
    entries = _validate_canonical_manifest(manifest)
    run_id = manifest.get("canonical_run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RetrievalRepresentationError("Canonical manifest lacks canonical_run_id")
    canonical_manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    output_root = Path(output_root)
    metadata_path = output_root / "metadata" / "manifest.json"
    if metadata_path.exists():
        existing = _read_object(metadata_path, "Retrieval manifest")
        if existing.get("status") == "complete":
            if (
                existing.get("canonical_manifest_sha256") != canonical_manifest_sha256
                or existing.get("representation_version") != RETRIEVAL_REPRESENTATION_VERSION
            ):
                raise FileExistsError(f"Retrieval run already complete with a different dependency or representation version: {metadata_path}")
            return dict(existing)

    writers: dict[str, tuple[Any, Any, Path, Path]] = {}
    for arm in REPRESENTATION_ARMS:
        target = output_root / "artifacts" / f"{arm}.jsonl.gz"
        binary, compressed, temporary = _gzip_jsonl_writer(target)
        writers[arm] = (binary, compressed, temporary, target)
    counts = {arm: 0 for arm in REPRESENTATION_ARMS}
    verified_records = 0
    try:
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise RetrievalRepresentationError(f"Canonical manifest record {index} is not an object")
            record = _load_canonical_record(entry, index)
            verified_records += 1
            for document in iter_retrieval_documents(record):
                arm = document["arm"]
                binary, compressed, temporary, target = writers[arm]
                compressed.write(canonical_json_bytes(document) + b"\n")
                counts[arm] += 1
        artifacts: dict[str, Any] = {}
        for arm, (binary, compressed, temporary, target) in writers.items():
            sha256, byte_count = _finish_gzip_writer(binary, compressed, temporary, target)
            artifacts[arm] = {
                "path": str(target),
                "sha256": sha256,
                "byte_count": byte_count,
                "document_count": counts[arm],
            }
        if verified_records != len(entries):
            raise RetrievalRepresentationError("derived document record accounting does not match Canonical manifest")
        result = {
            "schema_version": "phase04-retrieval-run-0.1",
            "status": "complete",
            "canonical_run_id": run_id,
            "canonical_manifest_path": str(manifest_path),
            "canonical_manifest_sha256": canonical_manifest_sha256,
            "representation_version": RETRIEVAL_REPRESENTATION_VERSION,
            "source": manifest.get("source"),
            "locale": manifest.get("locale"),
            "canonical_record_count": len(entries),
            "verified_canonical_record_count": verified_records,
            "artifacts": artifacts,
            "note": "All counts are observations of this Canonical run; documents are rebuildable Retrieval derivatives, not Canonical evidence.",
        }
        from genshin_corpus.collector.storage import atomic_write

        atomic_write(metadata_path, canonical_json_bytes(result))
        return result
    except Exception:
        for binary, compressed, temporary, target in writers.values():
            try:
                compressed.close()
            except OSError:
                pass
            try:
                binary.close()
            except OSError:
                pass
            if temporary.exists():
                temporary.unlink()
        raise


def load_retrieval_documents(retrieval_manifest_path: Path, arm: str) -> list[Mapping[str, Any]]:
    """Load and integrity-check one run-level experiment artifact."""

    if arm not in REPRESENTATION_ARMS:
        raise RetrievalRepresentationError(f"unknown representation arm: {arm}")
    manifest = _read_object(Path(retrieval_manifest_path), "Retrieval manifest")
    if manifest.get("status") != "complete":
        raise RetrievalRepresentationError("Retrieval manifest must be complete")
    artifacts = manifest.get("artifacts")
    artifact = artifacts.get(arm) if isinstance(artifacts, Mapping) else None
    if not isinstance(artifact, Mapping):
        raise RetrievalRepresentationError(f"Retrieval artifact is missing for arm: {arm}")
    path_value = artifact.get("path")
    expected_sha = artifact.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected_sha, str):
        raise RetrievalRepresentationError(f"Retrieval artifact metadata is incomplete for arm: {arm}")
    path = Path(path_value)
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha:
        raise RetrievalRepresentationError(f"Retrieval artifact SHA-256 mismatch: {path}")
    documents: list[Mapping[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                value = json.loads(line)
                if not isinstance(value, Mapping) or value.get("arm") != arm or not isinstance(value.get("document_id"), str):
                    raise RetrievalRepresentationError(f"invalid Retrieval document at {path}:{line_number}")
                documents.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetrievalRepresentationError(f"cannot read Retrieval documents: {path}") from exc
    if artifact.get("document_count") != len(documents):
        raise RetrievalRepresentationError(f"Retrieval document count does not match artifact metadata: {path}")
    return documents


def document_covers_location(document: Mapping[str, Any], location: Mapping[str, Any]) -> bool:
    """Match explicit Canonical/lineage coverage; text never participates."""

    sources = document.get("source_coverage")
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, Mapping) or source.get("record_id") != location.get("record_id"):
            continue
        if any(source.get(field) != location[field] for field in ("section_ordinal", "component_observation_key", "unit_ordinal") if field in location):
            continue
        lineage = source.get("lineage")
        if location.get("parsed_json_pointer") is not None and (not isinstance(lineage, Mapping) or lineage.get("parsed_json_pointer") != location["parsed_json_pointer"]):
            continue
        raw_selector = location.get("raw_ref")
        if raw_selector is not None:
            raw_refs = lineage.get("raw_refs") if isinstance(lineage, Mapping) else None
            if not isinstance(raw_refs, list) or not any(isinstance(raw, Mapping) and all(raw.get(key) == expected for key, expected in raw_selector.items()) for raw in raw_refs):
                continue
        decoded = location.get("decoded_json_pointer")
        if decoded is not None and (not isinstance(source.get("decoded_json_pointers"), list) or decoded not in source["decoded_json_pointers"]):
            continue
        expected_dialogue = location.get("dialogue")
        if expected_dialogue is not None:
            actual_dialogue = source.get("dialogue")
            if not isinstance(actual_dialogue, Mapping) or actual_dialogue.get("group_ordering") != expected_dialogue.get("group_ordering"):
                continue
            node_id = expected_dialogue.get("node_source_id")
            if node_id is not None and node_id not in actual_dialogue.get("node_source_ids", []):
                continue
            edge = expected_dialogue.get("edge")
            if edge is not None and edge not in actual_dialogue.get("edges", []):
                continue
        return True
    return False
