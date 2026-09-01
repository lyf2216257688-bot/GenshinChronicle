"""Frozen W7 Unit 3 mechanical boundaries.

This module deliberately separates pre-exposure review-pack extraction from
human semantic judgment.  It never prints evidence text or legacy-query data.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from genshin_corpus.canonical.fingerprints import canonical_json_bytes


UNIT3_SCHEMA_VERSION = "p04-w7-unit3-v1"
REVIEW_PACK_SCHEMA_VERSION = "p04-w7-unit3-review-pack-v1"
OVERLAP_SCHEMA_VERSION = "p04-w7-legacy-query-overlap-index-v1"
NORMALIZATION_VERSION = "c1-nfkc-whitespace-collapse-trim-casefold-v1"
EXPECTED_UNIT2_MANIFEST_SHA256 = "20baa9d92cdefc01731a234dc37fada6ca833456ec116a031fe557b4eb2796e8"
EXPECTED_SANITIZER_MANIFEST_SHA256 = "237377715cc413cf87b1f6d1d77f54d7a380b09275f8e475bf73f239b93b33fe"
EXPECTED_OVERLAP_INDEX_SHA256 = "171dc3a10420d880570201e0c62c7352154d7627dcd9564103c60d19b93354b7"
EXPECTED_OVERLAP_ENTRY_COUNT = 21
QUEUE_ORDER = ("semantic", "control", "WR", "HN")
QUEUE_ALLOCATIONS = {"semantic": 16, "control": 8, "WR": 12, "HN": 12}
FINAL_QUOTAS = {"semantic": 8, "WR": 6, "HN": 6, "control": 4}
MAX_PERSISTED_AUTHORED_QUERY_ATTEMPTS = 2
UNIT3B_PERSISTENCE_SCHEMA_VERSION = "p04-w7-unit3b-persistence-v1"
FROZEN_A2_REVIEW_PACK_MANIFEST_SHA256 = "704c8ea3aeb77f984b68c83d8e7ce5e936ce04dec3e82439a59d893d4a24e95d"
FROZEN_A2_REVIEW_PACK_ARTIFACT_SHA256 = "fb797a4bd224ae75e0cc2ff36253726413dfe6822786e9a8dc61f60f18380a05"
FROZEN_A2_SOURCE_CHECKPOINT = "9a90ef46f43f1719be1d3e77b14e97bbedc62e9f"
FROZEN_A2_REVIEW_PACK_BYTES = 103456
FROZEN_A2_REVIEW_PACK_ROWS = 48
_PRE_PROPOSITION_REJECT_REASONS = frozenset(
    {
        "ANCHOR_NOT_VALID_POSITIVE",
        "GOLD_SCOPE_INCOMPLETE",
        "GOLD_NOT_COLLECTIVELY_SUFFICIENT",
        "PRIMARY_GOLD_GAMEPLAY_BUILD",
        "PROVENANCE_UNRESOLVED",
        "REVIEWER_UNCERTAIN",
    }
)


class Unit3Blocked(RuntimeError):
    """Raised for a fail-closed Unit 3 mechanical contract violation."""


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value, flags=re.UNICODE)
    return value.strip().casefold()


def _hash_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _windows(value: str, width: int) -> set[str]:
    return {_hash_text(value[index : index + width]) for index in range(max(0, len(value) - width + 1))}


def _grams(value: str) -> set[str]:
    return _windows(value, 3)


def _safe_json_load(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unit3Blocked("JSON_READ_FAILED") from exc
    if not isinstance(value, Mapping):
        raise Unit3Blocked("JSON_OBJECT_REQUIRED")
    return value


def _read_jsonl_gzip(path: Path) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise Unit3Blocked("JSONL_RECORD_INVALID") from exc
                if not isinstance(value, Mapping):
                    raise Unit3Blocked("JSONL_OBJECT_REQUIRED")
                values.append(value)
    except Unit3Blocked:
        raise
    except (OSError, UnicodeDecodeError, gzip.BadGzipFile) as exc:
        raise Unit3Blocked("JSONL_GZIP_READ_FAILED") from exc
    return values


def _write_jsonl_gzip(path: Path, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".w7-unit3-", dir=str(path.parent))
    temporary = Path(temporary_name)
    count = 0
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                for row in rows:
                    compressed.write(canonical_json_bytes(row) + b"\n")
                    count += 1
        body = temporary.read_bytes()
        temporary.replace(path)
        return {"path": path.name, "sha256": _sha256_bytes(body), "byte_count": len(body), "row_count": count}
    finally:
        if temporary.exists():
            temporary.unlink()


def _deterministic_jsonl_gzip_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        for row in rows:
            compressed.write(canonical_json_bytes(row) + b"\n")
    return output.getvalue()


def _deterministic_jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Canonical plain UTF-8/LF JSONL used by Unit3B artifacts."""
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    try:
        body = path.read_bytes()
        if body.startswith(b"\x1f\x8b"):
            raise Unit3Blocked("JSONL_PLAIN_REQUIRED")
        for line in body.split(b"\n"):
            if not line:
                continue
            value = json.loads(line.decode("utf-8"))
            if not isinstance(value, Mapping):
                raise Unit3Blocked("JSONL_OBJECT_REQUIRED")
            values.append(value)
        if body != _deterministic_jsonl_bytes(values):
            raise Unit3Blocked("JSONL_NONCANONICAL")
    except Unit3Blocked:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unit3Blocked("JSONL_READ_FAILED") from exc
    return values


def _required_string(value: Mapping[str, Any], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str) or not candidate:
        raise Unit3Blocked("REQUIRED_STRING_MISSING")
    return candidate


def _pack_occurrence(row: Mapping[str, Any]) -> dict[str, Any]:
    required = ("occurrence_key", "candidate_key", "family_key", "entity_key", "topic_key", "occurrence_address", "text", "lineage", "raw_ref")
    if any(field not in row for field in required):
        raise Unit3Blocked("REVIEW_PACK_OCCURRENCE_INCOMPLETE")
    if not isinstance(row["text"], str):
        raise Unit3Blocked("REVIEW_PACK_TEXT_INVALID")
    return {
        "occurrence_key": _required_string(row, "occurrence_key"),
        "candidate_key": _required_string(row, "candidate_key"),
        "evidence_family_key": _required_string(row, "family_key"),
        "entity_key": row["entity_key"],
        "topic_key": row["topic_key"],
        "occurrence_address": row["occurrence_address"],
        "text": row["text"],
        "lineage": row["lineage"],
        "raw_ref": row["raw_ref"],
    }


def _index_unique(rows: Iterable[Mapping[str, Any]], key: str, code: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = _required_string(row, key)
        if value in indexed:
            raise Unit3Blocked(code)
        indexed[value] = row
    return indexed


def _queue_rows(provisional_queues: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    values: dict[str, list[Mapping[str, Any]]] = {}
    for entry in provisional_queues:
        queue = _required_string(entry, "queue")
        rows = entry.get("rows")
        if queue not in QUEUE_ORDER or not isinstance(rows, list) or queue in values or not all(isinstance(row, Mapping) for row in rows):
            raise Unit3Blocked("FROZEN_QUEUE_SCHEMA_INVALID")
        values[queue] = rows
    if tuple(values) != QUEUE_ORDER:
        raise Unit3Blocked("FROZEN_QUEUE_ORDER_INVALID")
    if {queue: len(values[queue]) for queue in QUEUE_ORDER} != QUEUE_ALLOCATIONS:
        raise Unit3Blocked("FROZEN_QUEUE_COUNT_INVALID")
    return values


def build_review_pack(
    provisional_queues: Iterable[Mapping[str, Any]],
    input_rows: Iterable[Mapping[str, Any]],
    gold_bundles: Iterable[Mapping[str, Any]],
    pair_views: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join frozen Unit 2 artifacts without assigning semantic labels."""
    queue_rows = _queue_rows(provisional_queues)
    occurrences = _index_unique(input_rows, "occurrence_key", "OCCURRENCE_IDENTITY_COLLISION")
    bundles = _index_unique(gold_bundles, "anchor_occurrence_key", "GOLD_BUNDLE_COLLISION")
    views = _index_unique(pair_views, "pair_key", "PAIR_VIEW_COLLISION")
    packed: list[dict[str, Any]] = []
    global_order = 0
    used_anchors: set[str] = set()
    for queue in QUEUE_ORDER:
        for queue_order, candidate in enumerate(queue_rows[queue], 1):
            global_order += 1
            anchor_key = _required_string(candidate, "anchor_occurrence_key")
            if anchor_key in used_anchors:
                raise Unit3Blocked("FROZEN_QUEUE_CANDIDATE_DUPLICATED")
            used_anchors.add(anchor_key)
            anchor = occurrences.get(anchor_key)
            bundle = bundles.get(anchor_key)
            if anchor is None or bundle is None:
                raise Unit3Blocked("REVIEW_PACK_REQUIRED_JOIN_MISSING")
            occurrence_keys = bundle.get("occurrence_keys")
            if not isinstance(occurrence_keys, list) or not occurrence_keys or not all(isinstance(key, str) for key in occurrence_keys):
                raise Unit3Blocked("GOLD_BUNDLE_MEMBERSHIP_INVALID")
            if anchor_key not in occurrence_keys or len(set(occurrence_keys)) != len(occurrence_keys):
                raise Unit3Blocked("GOLD_BUNDLE_MEMBERSHIP_INVALID")
            if bundle.get("status") != "VALID" or bundle.get("subreason") is not None or bundle.get("gold_review_occurrence_count") != len(occurrence_keys):
                raise Unit3Blocked("GOLD_BUNDLE_STATUS_INVALID")
            gold_rows: list[dict[str, Any]] = []
            for occurrence_key in occurrence_keys:
                row = occurrences.get(occurrence_key)
                if row is None:
                    raise Unit3Blocked("GOLD_OCCURRENCE_JOIN_MISSING")
                gold_rows.append(_pack_occurrence(row))
            expected_pair_keys = []
            if queue in ("WR", "HN"):
                candidate_views = candidate.get("wr_hn_relation_views")
                if not isinstance(candidate_views, list) or not candidate_views:
                    raise Unit3Blocked("WR_HN_PAIR_VIEW_MISSING")
                expected_pair_keys = [_required_string(view, "pair_key") for view in candidate_views if isinstance(view, Mapping)]
                if len(expected_pair_keys) != len(candidate_views) or len(set(expected_pair_keys)) != len(expected_pair_keys):
                    raise Unit3Blocked("WR_HN_PAIR_VIEW_INVALID")
            joined_views: list[Mapping[str, Any]] = []
            for pair_key in expected_pair_keys:
                view = views.get(pair_key)
                if view is None or view.get("anchor_occurrence_key") != anchor_key or view.get("anchor_gold_bundle_key") != anchor_key:
                    raise Unit3Blocked("PAIR_VIEW_JOIN_INVALID")
                related_key = _required_string(view, "related_representative_occurrence_key")
                if related_key not in occurrence_keys or related_key not in occurrences:
                    raise Unit3Blocked("PAIR_VIEW_OUTSIDE_GOLD_SCOPE")
                relevant_keys = view.get("pair_relevant_occurrence_keys")
                if (
                    not isinstance(relevant_keys, list)
                    or not relevant_keys
                    or not all(isinstance(key, str) for key in relevant_keys)
                    or len(set(relevant_keys)) != len(relevant_keys)
                    or not set(relevant_keys) <= set(occurrence_keys)
                ):
                    raise Unit3Blocked("PAIR_VIEW_OUTSIDE_GOLD_SCOPE")
                joined_views.append({
                    "pair_key": pair_key,
                    "anchor_occurrence_key": anchor_key,
                    "anchor_gold_bundle_key": anchor_key,
                    "relation_type": _required_string(view, "relation_type"),
                    "anchor_family_key": _required_string(view, "anchor_family_key"),
                    "related_family_key": _required_string(view, "related_family_key"),
                    "related_representative_occurrence_key": related_key,
                    "pair_relevant_occurrence_keys": relevant_keys,
                })
            anchor_pack = _pack_occurrence(anchor)
            packed.append({
                "schema_version": REVIEW_PACK_SCHEMA_VERSION,
                "queue": queue,
                "queue_review_order": queue_order,
                "global_review_order": global_order,
                "candidate_key": anchor_pack["candidate_key"],
                "anchor_occurrence_key": anchor_key,
                "entity_key": anchor_pack["entity_key"],
                "topic_key": anchor_pack["topic_key"],
                "evidence_family_key": anchor_pack["evidence_family_key"],
                "anchor": anchor_pack,
                "gold_occurrences": gold_rows,
                "pair_views": joined_views,
            })
    if global_order != 48:
        raise Unit3Blocked("FROZEN_REVIEW_COUNT_INVALID")
    validate_review_pack(packed)
    return packed


_PACK_FIELDS = frozenset({"schema_version", "queue", "queue_review_order", "global_review_order", "candidate_key", "anchor_occurrence_key", "entity_key", "topic_key", "evidence_family_key", "anchor", "gold_occurrences", "pair_views"})
_OCCURRENCE_FIELDS = frozenset({"occurrence_key", "candidate_key", "evidence_family_key", "entity_key", "topic_key", "occurrence_address", "text", "lineage", "raw_ref"})
_PAIR_FIELDS = frozenset({"pair_key", "anchor_occurrence_key", "anchor_gold_bundle_key", "relation_type", "anchor_family_key", "related_family_key", "related_representative_occurrence_key", "pair_relevant_occurrence_keys"})
_ADDRESS_FIELDS = frozenset({"record_id", "section_ordinal", "component_observation_key", "unit_ordinal", "lineage"})
_LINEAGE_FIELDS = frozenset({"evidence_scope", "parsed_json_pointer", "raw_refs", "dependency_locator"})
_RAW_REF_FIELDS = frozenset({"source", "locale", "run_id", "content_id", "artifact_kind", "artifact_path", "artifact_sha256", "json_pointer", "embedded_json_pointer", "source_value_sha256"})
_SENSITIVE_FIELD_TOKENS = ("legacy", "c1", "query", "overlap", "benchmark", "rank", "result", "outcome", "retrieval", "embedding", "vector")
_PACK_DEPENDENCY_FIELDS = frozenset({"review_pack_manifest_sha256", "review_pack_artifact_sha256"})


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _validate_no_sensitive_fields(value: Any) -> int:
    if isinstance(value, Mapping):
        count = sum(any(token in key.casefold() for token in _SENSITIVE_FIELD_TOKENS) for key in value if isinstance(key, str))
        return count + sum(_validate_no_sensitive_fields(item) for item in value.values())
    if isinstance(value, list):
        return sum(_validate_no_sensitive_fields(item) for item in value)
    return 0


def _review_pack_leak_counts(row: Mapping[str, Any]) -> dict[str, int]:
    """Count pre-exposure violations from actual record contents, never text."""
    forbidden = len(set(row) - _PACK_FIELDS)
    outside_scope = 0
    gold = row.get("gold_occurrences")
    if isinstance(gold, list):
        occurrence_keys = {item.get("occurrence_key") for item in gold if isinstance(item, Mapping)}
        for occurrence in gold:
            if isinstance(occurrence, Mapping):
                forbidden += len(set(occurrence) - _OCCURRENCE_FIELDS)
        views = row.get("pair_views")
        if isinstance(views, list):
            for view in views:
                if not isinstance(view, Mapping):
                    forbidden += 1
                    continue
                forbidden += len(set(view) - _PAIR_FIELDS)
                pointers = view.get("pair_relevant_occurrence_keys")
                if not isinstance(pointers, list) or not all(isinstance(key, str) for key in pointers):
                    outside_scope += 1
                elif not set(pointers) <= occurrence_keys:
                    outside_scope += len(set(pointers) - occurrence_keys)
                related = view.get("related_representative_occurrence_key")
                if related not in occurrence_keys:
                    outside_scope += 1
    return {
        "forbidden_field_count": forbidden,
        "outside_scope_text_count": outside_scope,
        "legacy_c1_sensitive_field_count": _validate_no_sensitive_fields(row),
    }


def _validate_lineage(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != _LINEAGE_FIELDS:
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    if not isinstance(value["evidence_scope"], str) or not isinstance(value["parsed_json_pointer"], str):
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    if value["dependency_locator"] is not None and not isinstance(value["dependency_locator"], str):
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    refs = value["raw_refs"]
    if not isinstance(refs, list) or len(refs) != 1:
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    ref = refs[0]
    if not isinstance(ref, Mapping) or set(ref) != _RAW_REF_FIELDS:
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    for field in _RAW_REF_FIELDS - {"source_value_sha256"}:
        if not isinstance(ref[field], str) or not ref[field]:
            raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    if ref["source_value_sha256"] is not None and not isinstance(ref["source_value_sha256"], str):
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")


def _validate_occurrence(occurrence: Any) -> str:
    if not isinstance(occurrence, Mapping) or set(occurrence) != _OCCURRENCE_FIELDS:
        raise Unit3Blocked("REVIEW_PACK_FIELD_ALLOWLIST_INVALID")
    key = _required_string(occurrence, "occurrence_key")
    address = occurrence["occurrence_address"]
    if not isinstance(address, Mapping) or set(address) != _ADDRESS_FIELDS:
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    if not isinstance(address["record_id"], str) or not isinstance(address["component_observation_key"], str):
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    if any(not isinstance(address[field], int) or isinstance(address[field], bool) or address[field] < 0 for field in ("section_ordinal", "unit_ordinal")):
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    _validate_lineage(address["lineage"])
    _validate_lineage(occurrence["lineage"])
    raw_ref = occurrence["raw_ref"]
    if not isinstance(raw_ref, Mapping) or set(raw_ref) != _RAW_REF_FIELDS:
        raise Unit3Blocked("REVIEW_PACK_NESTED_SCHEMA_INVALID")
    if raw_ref != occurrence["lineage"]["raw_refs"][0] or address["lineage"] != occurrence["lineage"]:
        raise Unit3Blocked("REVIEW_PACK_PROVENANCE_INVALID")
    _validate_lineage({"evidence_scope": occurrence["lineage"]["evidence_scope"], "parsed_json_pointer": occurrence["lineage"]["parsed_json_pointer"], "raw_refs": [raw_ref], "dependency_locator": occurrence["lineage"]["dependency_locator"]})
    if not _required_string(occurrence, "candidate_key") or not _required_string(occurrence, "evidence_family_key"):
        raise Unit3Blocked("REVIEW_PACK_IDENTITY_INVALID")
    entity = occurrence["entity_key"]
    topic = occurrence["topic_key"]
    if not isinstance(entity, list) or len(entity) != 3 or not all(isinstance(value, str) and value for value in entity):
        raise Unit3Blocked("REVIEW_PACK_IDENTITY_INVALID")
    if not isinstance(topic, list) or len(topic) != 2 or not isinstance(topic[0], str) or not isinstance(topic[1], int) or isinstance(topic[1], bool) or topic[1] < 0:
        raise Unit3Blocked("REVIEW_PACK_IDENTITY_INVALID")
    return key


def _validate_review_pack_record(row: Mapping[str, Any]) -> set[str]:
    if set(row) != _PACK_FIELDS or row.get("schema_version") != REVIEW_PACK_SCHEMA_VERSION or row.get("queue") not in QUEUE_ORDER:
        raise Unit3Blocked("REVIEW_PACK_FIELD_ALLOWLIST_INVALID")
    if not isinstance(row.get("queue_review_order"), int) or not isinstance(row.get("global_review_order"), int):
        raise Unit3Blocked("REVIEW_PACK_ORDER_INVALID")
    anchor = row.get("anchor")
    gold = row.get("gold_occurrences")
    views = row.get("pair_views")
    if not isinstance(anchor, Mapping) or not isinstance(gold, list) or not isinstance(views, list) or not gold:
        raise Unit3Blocked("REVIEW_PACK_SCHEMA_INVALID")
    occurrence_map = {_validate_occurrence(occurrence): occurrence for occurrence in gold}
    occurrence_keys = set(occurrence_map)
    if len(occurrence_keys) != len(gold):
        raise Unit3Blocked("GOLD_BUNDLE_MEMBERSHIP_INVALID")
    anchor_key = _required_string(row, "anchor_occurrence_key")
    if _validate_occurrence(anchor) != anchor_key or anchor_key not in occurrence_keys:
        raise Unit3Blocked("REVIEW_PACK_ANCHOR_INVALID")
    for field in ("candidate_key", "entity_key", "topic_key", "evidence_family_key"):
        if anchor[field] != row[field]:
            raise Unit3Blocked("REVIEW_PACK_ANCHOR_INVALID")
    if any(occurrence["entity_key"] != row["entity_key"] for occurrence in occurrence_map.values()):
        raise Unit3Blocked("REVIEW_PACK_ENTITY_SCOPE_INVALID")
    pair_keys: set[str] = set()
    for view in views:
        if not isinstance(view, Mapping) or set(view) != _PAIR_FIELDS:
            raise Unit3Blocked("REVIEW_PACK_FIELD_ALLOWLIST_INVALID")
        pair_key = _required_string(view, "pair_key")
        if pair_key in pair_keys:
            raise Unit3Blocked("PAIR_VIEW_COLLISION")
        pair_keys.add(pair_key)
        if view.get("anchor_occurrence_key") != anchor_key or view.get("anchor_gold_bundle_key") != anchor_key or view.get("anchor_family_key") != row["evidence_family_key"]:
            raise Unit3Blocked("PAIR_VIEW_IDENTITY_INVALID")
        pointers = view["pair_relevant_occurrence_keys"]
        if (not isinstance(pointers, list) or not pointers or not all(isinstance(key, str) for key in pointers) or len(set(pointers)) != len(pointers) or not set(pointers) <= occurrence_keys):
            raise Unit3Blocked("PAIR_VIEW_OUTSIDE_GOLD_SCOPE")
        related_key = _required_string(view, "related_representative_occurrence_key")
        related = occurrence_map.get(related_key)
        if related is None:
            raise Unit3Blocked("PAIR_VIEW_OUTSIDE_GOLD_SCOPE")
        if view.get("related_family_key") != related["evidence_family_key"]:
            raise Unit3Blocked("PAIR_VIEW_IDENTITY_INVALID")
    if row["queue"] in ("WR", "HN") and not views:
        raise Unit3Blocked("WR_HN_PAIR_VIEW_MISSING")
    if row["queue"] in ("semantic", "control") and views:
        raise Unit3Blocked("NON_WR_HN_PAIR_VIEW_PRESENT")
    return occurrence_keys


def review_pack_record_sha256(record: Mapping[str, Any]) -> str:
    """Digest the record payload; the digest is external to avoid self-reference."""
    _validate_review_pack_record(record)
    return _sha256_bytes(canonical_json_bytes(record))


def validate_review_pack(review_pack: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Validate frozen order, nested allowlists and scope without exposing text."""
    rows = list(review_pack)
    if len(rows) != 48:
        raise Unit3Blocked("FROZEN_REVIEW_COUNT_INVALID")
    seen_anchors: set[str] = set()
    queue_counts: dict[str, int] = defaultdict(int)
    expected_queues = [queue for queue in QUEUE_ORDER for _ in range(QUEUE_ALLOCATIONS[queue])]
    summary = {"candidate_count": len(rows), "forbidden_field_count": 0, "outside_scope_text_count": 0, "legacy_c1_sensitive_field_count": 0}
    for expected_global_order, (row, expected_queue) in enumerate(zip(rows, expected_queues, strict=True), 1):
        counts = _review_pack_leak_counts(row)
        for key in ("forbidden_field_count", "outside_scope_text_count", "legacy_c1_sensitive_field_count"):
            summary[key] += counts[key]
        if summary["legacy_c1_sensitive_field_count"]:
            raise Unit3Blocked("REVIEW_PACK_SENSITIVE_FIELD_PRESENT")
        if summary["forbidden_field_count"]:
            raise Unit3Blocked("REVIEW_PACK_FIELD_ALLOWLIST_INVALID")
        if summary["outside_scope_text_count"]:
            raise Unit3Blocked("PAIR_VIEW_OUTSIDE_GOLD_SCOPE")
        _validate_review_pack_record(row)
        if row["global_review_order"] != expected_global_order:
            raise Unit3Blocked("REVIEW_PACK_ORDER_INVALID")
        queue = row["queue"]
        if queue != expected_queue:
            raise Unit3Blocked("REVIEW_PACK_QUEUE_SEQUENCE_INVALID")
        queue_counts[queue] += 1
        if row["queue_review_order"] != queue_counts[queue]:
            raise Unit3Blocked("REVIEW_PACK_ORDER_INVALID")
        anchor_key = _required_string(row, "anchor_occurrence_key")
        if anchor_key in seen_anchors:
            raise Unit3Blocked("FROZEN_QUEUE_CANDIDATE_DUPLICATED")
        seen_anchors.add(anchor_key)
    if dict(queue_counts) != QUEUE_ALLOCATIONS:
        raise Unit3Blocked("FROZEN_QUEUE_COUNT_INVALID")
    return summary


def _validate_pack_dependency(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _PACK_DEPENDENCY_FIELDS or not all(_is_sha256(value[field]) for field in _PACK_DEPENDENCY_FIELDS):
        raise Unit3Blocked("REVIEW_PACK_DEPENDENCY_INVALID")
    return {field: value[field] for field in sorted(_PACK_DEPENDENCY_FIELDS)}


def _frozen_state_payload(state: Mapping[str, Any], record: Mapping[str, Any], dependency: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("frozen_gold_occurrence_keys", "accepted_gold_occurrence_keys", "reviewed_non_gold_occurrence_keys", "occurrence_reviews", "anchor_review_result", "gameplay_build_exclusion_status", "queue_semantic_validity", "semantic_status", "semantic_reason", "gold_review_complete", "accepted_gold_sufficient", "target_proposition_status", "target_proposition", "anchor_source_span", "sentence_or_clause_basis", "query_intent", "answer_proposition", "pair_judgments", "selected_pair_key")
    if any(field not in state for field in fields):
        raise Unit3Blocked("SEMANTIC_STATE_INCOMPLETE")
    return {
        **{field: state[field] for field in fields},
        "pack_record_sha256": review_pack_record_sha256(record),
        "review_pack_dependency": _validate_pack_dependency(dependency),
        "candidate_key": record["candidate_key"],
        "queue": record["queue"],
        "global_review_order": record["global_review_order"],
        "queue_review_order": record["queue_review_order"],
        "anchor_occurrence_key": record["anchor_occurrence_key"],
        "entity_key": record["entity_key"],
        "topic_key": record["topic_key"],
        "evidence_family_key": record["evidence_family_key"],
    }


def _validate_occurrence_reviews(payload: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    reviews = payload["occurrence_reviews"]
    if not isinstance(reviews, list) or len(reviews) != len(record["gold_occurrences"]):
        raise Unit3Blocked("OCCURRENCE_REVIEW_INVALID")
    expected_keys = [_required_string(item, "occurrence_key") for item in record["gold_occurrences"]]
    accepted = set(payload["accepted_gold_occurrence_keys"])
    non_gold = set(payload["reviewed_non_gold_occurrence_keys"])
    for expected_key, review in zip(expected_keys, reviews, strict=True):
        if not isinstance(review, Mapping) or set(review) != {"occurrence_key", "judgment", "reason_code", "provenance_caveat"} or review.get("occurrence_key") != expected_key or review.get("judgment") not in ("ACCEPTED_GOLD", "REVIEWED_NON_GOLD") or not isinstance(review.get("reason_code"), str) or not review["reason_code"] or (review.get("provenance_caveat") is not None and not isinstance(review.get("provenance_caveat"), str)):
            raise Unit3Blocked("OCCURRENCE_REVIEW_INVALID")
        if (review["judgment"] == "ACCEPTED_GOLD") != (expected_key in accepted) or (review["judgment"] == "REVIEWED_NON_GOLD") != (expected_key in non_gold):
            raise Unit3Blocked("OCCURRENCE_REVIEW_INVALID")
    anchor_review = reviews[expected_keys.index(record["anchor_occurrence_key"])]
    if payload["anchor_review_result"] != anchor_review["judgment"]:
        raise Unit3Blocked("ANCHOR_REVIEW_INVALID")


def _validate_semantic_outcome(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload["gameplay_build_exclusion_status"], str) or not payload["gameplay_build_exclusion_status"]:
        raise Unit3Blocked("GAMEPLAY_BUILD_STATUS_INVALID")
    if payload["queue_semantic_validity"] not in ("VALID", "INVALID") or payload["semantic_status"] not in ("ACCEPT", "REJECT"):
        raise Unit3Blocked("SEMANTIC_OUTCOME_INVALID")
    if payload["semantic_status"] == "ACCEPT":
        if payload["queue_semantic_validity"] != "VALID" or payload["semantic_reason"] is not None:
            raise Unit3Blocked("SEMANTIC_OUTCOME_INVALID")
    elif payload["queue_semantic_validity"] != "INVALID" or not isinstance(payload["semantic_reason"], str) or not payload["semantic_reason"]:
        raise Unit3Blocked("SEMANTIC_OUTCOME_INVALID")


def _validate_pair_judgments(payload: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    judgments = payload["pair_judgments"]
    views = record["pair_views"]
    if not isinstance(judgments, list):
        raise Unit3Blocked("PAIR_JUDGMENT_INVALID")
    if payload["queue"] in ("semantic", "control"):
        if judgments or payload["selected_pair_key"] is not None:
            raise Unit3Blocked("SELECTED_PAIR_NOT_ALLOWED")
        return
    expected_keys = [_required_string(view, "pair_key") for view in views]
    if len(judgments) != len(expected_keys):
        raise Unit3Blocked("PAIR_JUDGMENT_INVALID")
    valid_keys: list[str] = []
    for pair_key, judgment in zip(expected_keys, judgments, strict=True):
        if not isinstance(judgment, Mapping) or set(judgment) != {"pair_key", "decision", "reason_code"} or judgment.get("pair_key") != pair_key or judgment.get("decision") not in ("VALID", "REJECT"):
            raise Unit3Blocked("PAIR_JUDGMENT_INVALID")
        if judgment["decision"] == "VALID":
            if judgment["reason_code"] is not None:
                raise Unit3Blocked("PAIR_JUDGMENT_INVALID")
            valid_keys.append(pair_key)
        elif not isinstance(judgment["reason_code"], str) or not judgment["reason_code"]:
            raise Unit3Blocked("PAIR_JUDGMENT_INVALID")
    if payload["semantic_status"] == "ACCEPT" and (not valid_keys or payload["selected_pair_key"] != valid_keys[0]):
        raise Unit3Blocked("SELECTED_PAIR_NOT_FIRST_VALID")
    if payload["semantic_status"] == "REJECT" and payload["selected_pair_key"] != (valid_keys[0] if valid_keys else None):
        raise Unit3Blocked("SELECTED_PAIR_NOT_FIRST_VALID")


def freeze_semantic_state(state: Mapping[str, Any], *, record: Mapping[str, Any], review_pack_dependency: Mapping[str, Any]) -> dict[str, Any]:
    payload = _frozen_state_payload(state, record, review_pack_dependency)
    gold = payload["frozen_gold_occurrence_keys"]
    accepted = payload["accepted_gold_occurrence_keys"]
    non_gold = payload["reviewed_non_gold_occurrence_keys"]
    if not all(isinstance(values, list) and all(isinstance(value, str) for value in values) for values in (gold, accepted, non_gold)):
        raise Unit3Blocked("GOLD_PARTITION_INVALID")
    record_gold = [_required_string(item, "occurrence_key") for item in record["gold_occurrences"]]
    if gold != record_gold or len(gold) != len(set(gold)) or len(accepted) != len(set(accepted)) or len(non_gold) != len(set(non_gold)) or set(accepted) & set(non_gold) or set(accepted) | set(non_gold) != set(gold):
        raise Unit3Blocked("GOLD_PARTITION_INVALID")
    _validate_occurrence_reviews(payload, record)
    _validate_semantic_outcome(payload)
    status = payload["target_proposition_status"]
    if status not in ("FROZEN", "NOT_REACHED", "TARGET_PROPOSITION_AMBIGUOUS"):
        raise Unit3Blocked("TARGET_PROPOSITION_STATUS_INVALID")
    if not isinstance(payload["gold_review_complete"], bool) or not isinstance(payload["accepted_gold_sufficient"], bool):
        raise Unit3Blocked("GOLD_COMPLETENESS_STATUS_INVALID")
    if status == "FROZEN":
        if record["anchor_occurrence_key"] not in accepted or not payload["gold_review_complete"] or not payload["accepted_gold_sufficient"]:
            raise Unit3Blocked("FROZEN_GOLD_STATE_INVALID")
        span = payload["anchor_source_span"]
        anchor_text = record["anchor"].get("text")
        if not isinstance(anchor_text, str) or not isinstance(span, Mapping) or set(span) != {"start", "end"} or not all(isinstance(span[key], int) and not isinstance(span[key], bool) for key in span) or not 0 <= span["start"] < span["end"] <= len(anchor_text):
            raise Unit3Blocked("ANCHOR_SOURCE_SPAN_INVALID")
        for field in ("target_proposition", "sentence_or_clause_basis", "query_intent", "answer_proposition"):
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise Unit3Blocked("SEMANTIC_STATE_INCOMPLETE")
        _validate_pair_judgments(payload, record)
    else:
        if (
            payload["semantic_status"] != "REJECT"
            or not payload["gold_review_complete"]
            or any(payload[field] is not None for field in ("target_proposition", "anchor_source_span", "sentence_or_clause_basis", "query_intent", "answer_proposition", "selected_pair_key"))
            or payload["pair_judgments"]
        ):
            raise Unit3Blocked("TARGET_PROPOSITION_PRE_FREEZE_INVALID")
        if status == "TARGET_PROPOSITION_AMBIGUOUS" and payload["semantic_reason"] != "TARGET_PROPOSITION_AMBIGUOUS":
            raise Unit3Blocked("TARGET_PROPOSITION_AMBIGUITY_INVALID")
        if status == "NOT_REACHED" and payload["semantic_reason"] not in _PRE_PROPOSITION_REJECT_REASONS:
            raise Unit3Blocked("TARGET_PROPOSITION_STAGE_INVALID")
    frozen = dict(payload)
    frozen["anchor_text_codepoint_count"] = len(record["anchor"]["text"])
    frozen["semantic_state_sha256"] = _sha256_bytes(canonical_json_bytes(frozen))
    return frozen


def validate_frozen_semantic_state(state: Mapping[str, Any], *, record: Mapping[str, Any], review_pack_dependency: Mapping[str, Any]) -> None:
    digest = state.get("semantic_state_sha256")
    if not _is_sha256(digest):
        raise Unit3Blocked("SEMANTIC_STATE_HASH_MISSING")
    payload = dict(state)
    payload.pop("semantic_state_sha256", None)
    if _sha256_bytes(canonical_json_bytes(payload)) != digest:
        raise Unit3Blocked("SEMANTIC_STATE_MUTATED")
    expected = _frozen_state_payload(payload, record, review_pack_dependency)
    expected["anchor_text_codepoint_count"] = len(record["anchor"]["text"])
    if payload != expected:
        raise Unit3Blocked("SEMANTIC_STATE_PACK_BINDING_INVALID")
    # Revalidate mechanical state fields after binding to the authoritative record.
    freeze_semantic_state(payload, record=record, review_pack_dependency=review_pack_dependency)


def _validate_attempt(attempt: Mapping[str, Any], state: Mapping[str, Any], expected_number: int) -> None:
    if attempt.get("attempt_number") != expected_number or attempt.get("candidate_key") != state["candidate_key"] or attempt.get("semantic_state_sha256") != state["semantic_state_sha256"]:
        raise Unit3Blocked("QUERY_ATTEMPT_HISTORY_INVALID")
    fields = {"attempt_id", "candidate_key", "semantic_state_sha256", "attempt_number", "query", "attempt_sha256"}
    if set(attempt) != fields or not isinstance(attempt.get("attempt_id"), str) or not isinstance(attempt.get("query"), str) or not attempt["query"].strip():
        raise Unit3Blocked("QUERY_ATTEMPT_INVALID")
    copied = dict(attempt)
    digest = copied.pop("attempt_sha256", None)
    if not _is_sha256(digest) or _sha256_bytes(canonical_json_bytes(copied)) != digest:
        raise Unit3Blocked("QUERY_ATTEMPT_MUTATED")


def persist_query_attempt(state: Mapping[str, Any], attempt_number: int, query: str, *, record: Mapping[str, Any], review_pack_dependency: Mapping[str, Any]) -> dict[str, Any]:
    validate_frozen_semantic_state(state, record=record, review_pack_dependency=review_pack_dependency)
    if state.get("target_proposition_status") != "FROZEN" or state.get("semantic_status") != "ACCEPT" or attempt_number not in (1, 2) or not isinstance(query, str) or not query.strip():
        raise Unit3Blocked("QUERY_ATTEMPT_INVALID")
    attempt = {"attempt_id": f"{state['candidate_key']}:attempt:{attempt_number}", "candidate_key": state["candidate_key"], "semantic_state_sha256": state["semantic_state_sha256"], "attempt_number": attempt_number, "query": query}
    attempt["attempt_sha256"] = _sha256_bytes(canonical_json_bytes(attempt))
    return attempt


_QUALITY_REJECT_REASONS = {
    "QUERY_NOT_NATURAL",
    "QUERY_INTENT_DRIFT",
    "QUERY_REQUIRES_OUT_OF_SCOPE_EVIDENCE",
    "QUERY_EVIDENCE_COPYING",
    "PAIR_QUERY_INCONSISTENT",
}


_QUALITY_RETRY_DISPOSITIONS = {"ATTEMPT_2_AUTHORIZED", "TERMINAL"}


def persist_query_quality_result(
    attempt: Mapping[str, Any],
    quality_status: str,
    quality_reason: str | None,
    retry_disposition: str | None = None,
) -> dict[str, Any]:
    """Persist the post-authoring quality decision without changing the attempt."""
    _validate_attempt(attempt, {"candidate_key": attempt.get("candidate_key"), "semantic_state_sha256": attempt.get("semantic_state_sha256")}, attempt.get("attempt_number"))
    if quality_status not in ("PASS", "REJECT"):
        raise Unit3Blocked("QUERY_QUALITY_STATUS_INVALID")
    if quality_status == "PASS" and quality_reason is not None:
        raise Unit3Blocked("QUERY_QUALITY_REASON_INVALID")
    if quality_status == "REJECT" and quality_reason not in _QUALITY_REJECT_REASONS:
        raise Unit3Blocked("QUERY_QUALITY_REASON_INVALID")
    if quality_status == "PASS" and retry_disposition is not None:
        raise Unit3Blocked("QUERY_QUALITY_RETRY_DISPOSITION_INVALID")
    attempt_number = attempt.get("attempt_number")
    if quality_status == "REJECT" and attempt_number == 1 and retry_disposition not in _QUALITY_RETRY_DISPOSITIONS:
        raise Unit3Blocked("MATERIAL_QUALITY_RETRY_DISPOSITION_MISSING")
    if quality_status == "REJECT" and attempt_number == 2 and retry_disposition is not None:
        raise Unit3Blocked("QUERY_QUALITY_RETRY_DISPOSITION_INVALID")
    result = {"attempt_id": attempt["attempt_id"], "attempt_sha256": attempt["attempt_sha256"], "quality_status": quality_status, "quality_reason": quality_reason}
    if quality_status == "REJECT" and attempt_number == 1:
        result["retry_disposition"] = retry_disposition
    result["query_quality_result_sha256"] = _sha256_bytes(canonical_json_bytes(result))
    return result


def validate_query_quality_result(attempt: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    _validate_attempt(attempt, {"candidate_key": attempt.get("candidate_key"), "semantic_state_sha256": attempt.get("semantic_state_sha256")}, attempt.get("attempt_number"))
    fields = {"attempt_id", "attempt_sha256", "quality_status", "quality_reason", "query_quality_result_sha256"}
    if result.get("quality_status") == "REJECT" and attempt.get("attempt_number") == 1:
        fields.add("retry_disposition")
    if set(result) != fields or result.get("attempt_id") != attempt["attempt_id"] or result.get("attempt_sha256") != attempt["attempt_sha256"] or result.get("quality_status") not in ("PASS", "REJECT"):
        raise Unit3Blocked("QUERY_QUALITY_RESULT_INVALID")
    if (result["quality_status"] == "PASS" and result.get("quality_reason") is not None) or (result["quality_status"] == "REJECT" and result.get("quality_reason") not in _QUALITY_REJECT_REASONS):
        raise Unit3Blocked("QUERY_QUALITY_REASON_INVALID")
    if result["quality_status"] == "PASS" and result.get("retry_disposition") is not None:
        raise Unit3Blocked("QUERY_QUALITY_RETRY_DISPOSITION_INVALID")
    if result["quality_status"] == "REJECT" and attempt.get("attempt_number") == 1 and result.get("retry_disposition") not in _QUALITY_RETRY_DISPOSITIONS:
        raise Unit3Blocked("MATERIAL_QUALITY_RETRY_DISPOSITION_INVALID")
    if result["quality_status"] == "REJECT" and attempt.get("attempt_number") == 2 and "retry_disposition" in result:
        raise Unit3Blocked("QUERY_QUALITY_RETRY_DISPOSITION_INVALID")
    copied = dict(result)
    digest = copied.pop("query_quality_result_sha256")
    if not _is_sha256(digest) or _sha256_bytes(canonical_json_bytes(copied)) != digest:
        raise Unit3Blocked("QUERY_QUALITY_RESULT_MUTATED")


def validate_c1_result(attempt: Mapping[str, Any], quality_result: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    _validate_attempt(attempt, {"candidate_key": attempt.get("candidate_key"), "semantic_state_sha256": attempt.get("semantic_state_sha256")}, attempt.get("attempt_number"))
    validate_query_quality_result(attempt, quality_result)
    if quality_result["quality_status"] != "PASS":
        raise Unit3Blocked("C1_REQUIRES_QUERY_QUALITY_PASS")
    fields = {"attempt_id", "attempt_sha256", "checker_schema_version", "overlap_index_sha256", "normalization_version", "exact_rule", "shared_8_rule", "char_3gram_threshold_rule", "overall", "c1_result_sha256"}
    if set(result) != fields or result.get("attempt_id") != attempt.get("attempt_id") or result.get("attempt_sha256") != attempt.get("attempt_sha256") or result.get("checker_schema_version") != UNIT3_SCHEMA_VERSION or not _is_sha256(result.get("overlap_index_sha256")) or result.get("normalization_version") != NORMALIZATION_VERSION or result.get("overall") not in ("PASS", "REJECT") or not all(isinstance(result.get(field), bool) for field in ("exact_rule", "shared_8_rule", "char_3gram_threshold_rule")):
        raise Unit3Blocked("C1_RESULT_INVALID")
    copied = dict(result)
    digest = copied.pop("c1_result_sha256")
    if not _is_sha256(digest) or _sha256_bytes(canonical_json_bytes(copied)) != digest:
        raise Unit3Blocked("C1_RESULT_MUTATED")


def validate_attempt_history(state: Mapping[str, Any], attempts: Iterable[Mapping[str, Any]], quality_results: Iterable[Mapping[str, Any]], c1_results: Iterable[Mapping[str, Any]], *, record: Mapping[str, Any], review_pack_dependency: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    validate_frozen_semantic_state(state, record=record, review_pack_dependency=review_pack_dependency)
    values = list(attempts)
    qualities = list(quality_results)
    results = list(c1_results)
    if len(values) > MAX_PERSISTED_AUTHORED_QUERY_ATTEMPTS:
        raise Unit3Blocked("QUERY_ATTEMPT_LIMIT_EXCEEDED")
    quality_by_attempt: dict[str, Mapping[str, Any]] = {}
    for quality in qualities:
        attempt_id = _required_string(quality, "attempt_id")
        if attempt_id in quality_by_attempt:
            raise Unit3Blocked("QUERY_QUALITY_RESULT_INVALID")
        quality_by_attempt[attempt_id] = quality
    result_by_attempt: dict[str, Mapping[str, Any]] = {}
    for result in results:
        attempt_id = _required_string(result, "attempt_id")
        if attempt_id in result_by_attempt:
            raise Unit3Blocked("C1_RESULT_INVALID")
        result_by_attempt[attempt_id] = result
    for expected_number, attempt in enumerate(values, 1):
        _validate_attempt(attempt, state, expected_number)
        quality = quality_by_attempt.get(attempt["attempt_id"])
        if quality is not None:
            validate_query_quality_result(attempt, quality)
        result = result_by_attempt.get(attempt["attempt_id"])
        if result is not None:
            if quality is None or quality["quality_status"] != "PASS":
                raise Unit3Blocked("C1_REQUIRES_QUERY_QUALITY_PASS")
            validate_c1_result(attempt, quality, result)
        if expected_number == 2:
            first = values[0]
            first_quality = quality_by_attempt.get(first["attempt_id"])
            first_result = result_by_attempt.get(first["attempt_id"])
            if (first_quality is None or first_quality.get("quality_status") != "REJECT" or first_quality.get("retry_disposition") != "ATTEMPT_2_AUTHORIZED") and (first_result is None or first_result.get("overall") != "REJECT"):
                raise Unit3Blocked("UNJUSTIFIED_QUERY_RETRY")
    attempt_ids = {attempt["attempt_id"] for attempt in values}
    if set(quality_by_attempt) - attempt_ids:
        raise Unit3Blocked("QUERY_QUALITY_RESULT_INVALID")
    if set(result_by_attempt) - attempt_ids:
        raise Unit3Blocked("C1_RESULT_INVALID")
    return values


def _validate_overlap_index_payload(index: Mapping[str, Any]) -> None:
    expected_metadata = {
        "unicode_form": "NFKC",
        "whitespace": "unicode_whitespace_collapse_to_ascii_space",
        "trim": True,
        "casefold": True,
        "hash_encoding": "UTF-8",
        "codepoint_basis": "Unicode_code_points",
        "continuous_window_length": 8,
        "unique_gram_length": 3,
    }
    expected_fields = {"schema_version", "FOR_UNIT3_ONLY", "FOR_UNIT2_CANDIDATE_SELECTION", "normalization_version", "normalization_metadata", "source_benchmark_sha256", "accounting", "entries"}
    if set(index) != expected_fields or index.get("schema_version") != OVERLAP_SCHEMA_VERSION or index.get("FOR_UNIT3_ONLY") is not True or index.get("FOR_UNIT2_CANDIDATE_SELECTION") is not False:
        raise Unit3Blocked("OVERLAP_INDEX_SCHEMA_INVALID")
    if index.get("normalization_version") != NORMALIZATION_VERSION or index.get("normalization_metadata") != expected_metadata or not _is_sha256(index.get("source_benchmark_sha256")) or index.get("accounting", {}).get("legacy_query_count") != EXPECTED_OVERLAP_ENTRY_COUNT:
        raise Unit3Blocked("OVERLAP_INDEX_CONTRACT_INVALID")
    entries = index.get("entries")
    if not isinstance(entries, list) or len(entries) != EXPECTED_OVERLAP_ENTRY_COUNT:
        raise Unit3Blocked("OVERLAP_INDEX_ENTRY_COUNT_INVALID")
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("opaque_legacy_id"), str):
            raise Unit3Blocked("OVERLAP_INDEX_ENTRY_INVALID")
        for field in ("normalized_query_sha256", "normalized_continuous_8char_window_sha256", "normalized_unique_char_3gram_sha256"):
            value = entry.get(field)
            if field == "normalized_query_sha256":
                valid = _is_sha256(value)
            else:
                valid = isinstance(value, list) and value == sorted(set(value)) and all(_is_sha256(item) for item in value)
            if not valid:
                raise Unit3Blocked("OVERLAP_INDEX_ENTRY_INVALID")


def parse_overlap_index_bytes(raw_bytes: bytes, *, expected_sha256: str = EXPECTED_OVERLAP_INDEX_SHA256) -> Mapping[str, Any]:
    """Bind C1 parsing to raw bytes before any JSON object is trusted."""
    if not isinstance(raw_bytes, bytes) or _sha256_bytes(raw_bytes) != expected_sha256:
        raise Unit3Blocked("OVERLAP_INDEX_SHA_MISMATCH")
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unit3Blocked("OVERLAP_INDEX_JSON_INVALID") from exc
    if not isinstance(parsed, Mapping):
        raise Unit3Blocked("OVERLAP_INDEX_SCHEMA_INVALID")
    _validate_overlap_index_payload(parsed)
    return parsed


def restricted_c1_check(attempts: Iterable[Mapping[str, Any]], quality_results: Iterable[Mapping[str, Any]], overlap_index_raw_bytes: bytes, *, expected_index_sha256: str = EXPECTED_OVERLAP_INDEX_SHA256) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return immutable restricted audit and author-safe PASS/REJECT projection."""
    index = parse_overlap_index_bytes(overlap_index_raw_bytes, expected_sha256=expected_index_sha256)
    attempt_values = list(attempts)
    quality_by_attempt: dict[str, Mapping[str, Any]] = {}
    for quality in quality_results:
        attempt_id = _required_string(quality, "attempt_id")
        if attempt_id in quality_by_attempt:
            raise Unit3Blocked("QUERY_QUALITY_RESULT_INVALID")
        quality_by_attempt[attempt_id] = quality
    audits: list[dict[str, Any]] = []
    feedback: list[dict[str, Any]] = []
    for attempt in attempt_values:
        _validate_attempt(attempt, {"candidate_key": attempt.get("candidate_key"), "semantic_state_sha256": attempt.get("semantic_state_sha256")}, attempt.get("attempt_number"))
        quality = quality_by_attempt.get(attempt["attempt_id"])
        if quality is None:
            raise Unit3Blocked("C1_REQUIRES_QUERY_QUALITY_PASS")
        validate_query_quality_result(attempt, quality)
        if quality["quality_status"] != "PASS":
            raise Unit3Blocked("C1_REQUIRES_QUERY_QUALITY_PASS")
        query = attempt.get("query")
        if not isinstance(query, str):
            raise Unit3Blocked("QUERY_ATTEMPT_INVALID")
        normalized = _normalise(query)
        query_hash, windows, grams = _hash_text(normalized), _windows(normalized, 8), _grams(normalized)
        exact = shared_8 = gram_threshold = False
        for legacy in index["entries"]:
            exact = exact or query_hash == legacy["normalized_query_sha256"]
            shared_8 = shared_8 or bool(windows & set(legacy["normalized_continuous_8char_window_sha256"]))
            legacy_grams = set(legacy["normalized_unique_char_3gram_sha256"])
            intersection, union = len(grams & legacy_grams), len(grams | legacy_grams)
            gram_threshold = gram_threshold or (union > 0 and 100 * intersection >= 50 * union)
        result = "REJECT" if exact or shared_8 or gram_threshold else "PASS"
        audit = {"attempt_id": _required_string(attempt, "attempt_id"), "attempt_sha256": _required_string(attempt, "attempt_sha256"), "checker_schema_version": UNIT3_SCHEMA_VERSION, "overlap_index_sha256": expected_index_sha256, "normalization_version": NORMALIZATION_VERSION, "exact_rule": exact, "shared_8_rule": shared_8, "char_3gram_threshold_rule": gram_threshold, "overall": result}
        audit["c1_result_sha256"] = _sha256_bytes(canonical_json_bytes(audit))
        validate_c1_result(attempt, quality, audit)
        audits.append(audit)
        feedback.append({"attempt_id": audit["attempt_id"], "overall": result})
    if set(quality_by_attempt) - {attempt["attempt_id"] for attempt in attempt_values}:
        raise Unit3Blocked("QUERY_QUALITY_RESULT_INVALID")
    return audits, feedback


def _ledger_fully_valid(ledger: Mapping[str, Any], *, accepted_review_pack_dependency: Mapping[str, Any], expected_overlap_index_sha256: str) -> bool:
    record, dependency, state = ledger.get("pack_record"), ledger.get("review_pack_dependency"), ledger.get("semantic_state")
    accepted_dependency = _validate_pack_dependency(accepted_review_pack_dependency)
    if not isinstance(record, Mapping) or not isinstance(dependency, Mapping) or not isinstance(state, Mapping):
        return False
    validate_frozen_semantic_state(state, record=record, review_pack_dependency=accepted_dependency)
    attempts = list(ledger.get("attempts", []))
    quality_results = list(ledger.get("query_quality_results", []))
    results = list(ledger.get("c1_results", []))
    validate_attempt_history(state, attempts, quality_results, results, record=record, review_pack_dependency=accepted_dependency)
    if any(result.get("overlap_index_sha256") != expected_overlap_index_sha256 for result in results):
        raise Unit3Blocked("FINALIZER_C1_DEPENDENCY_INVALID")
    if state["semantic_status"] != "ACCEPT" or state["target_proposition_status"] != "FROZEN" or not state["gold_review_complete"] or not state["accepted_gold_sufficient"] or not attempts:
        return False
    final_attempt = attempts[-1]
    matching_quality = [quality for quality in quality_results if quality.get("attempt_id") == final_attempt.get("attempt_id")]
    if len(matching_quality) != 1 or matching_quality[0].get("quality_status") != "PASS":
        return False
    matching = [result for result in results if result.get("attempt_id") == final_attempt.get("attempt_id")]
    return len(matching) == 1 and matching[0].get("overall") == "PASS" and matching[0].get("overlap_index_sha256") == expected_overlap_index_sha256


def finalize_candidates(ledgers: Iterable[Mapping[str, Any]], *, accepted_review_pack_dependency: Mapping[str, Any], expected_overlap_index_sha256: str = EXPECTED_OVERLAP_INDEX_SHA256) -> dict[str, Any]:
    """Apply frozen first-fully-valid quota truncation without semantic scoring."""
    values = list(ledgers)
    accepted_dependency = _validate_pack_dependency(accepted_review_pack_dependency)
    if not _is_sha256(expected_overlap_index_sha256):
        raise Unit3Blocked("FINALIZER_C1_DEPENDENCY_INVALID")
    if len(values) != 48:
        raise Unit3Blocked("FINALIZER_QUEUE_INVALID")
    by_queue: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    expected_queues = [queue for queue in QUEUE_ORDER for _ in range(QUEUE_ALLOCATIONS[queue])]
    for expected_global_order, (ledger, expected_queue) in enumerate(zip(values, expected_queues, strict=True), 1):
        record = ledger.get("pack_record")
        if not isinstance(record, Mapping) or record.get("global_review_order") != expected_global_order or record.get("queue") != expected_queue:
            raise Unit3Blocked("FINALIZER_ORDER_INVALID")
        _validate_review_pack_record(record)
        if _validate_pack_dependency(ledger.get("review_pack_dependency")) != accepted_dependency:
            raise Unit3Blocked("FINALIZER_REVIEW_PACK_DEPENDENCY_INVALID")
        queue, key = record["queue"], record["candidate_key"]
        if key in seen:
            raise Unit3Blocked("FINALIZER_IDENTITY_INVALID")
        seen.add(key)
        by_queue[queue].append(ledger)
    if {queue: len(by_queue[queue]) for queue in QUEUE_ORDER} != QUEUE_ALLOCATIONS:
        raise Unit3Blocked("FINALIZER_QUEUE_INVALID")
    selected: list[Mapping[str, Any]] = []
    accounting: dict[str, dict[str, Any]] = {}
    for queue in QUEUE_ORDER:
        queue_ledgers = by_queue[queue]
        if [ledger["pack_record"]["queue_review_order"] for ledger in queue_ledgers] != list(range(1, QUEUE_ALLOCATIONS[queue] + 1)):
            raise Unit3Blocked("FINALIZER_ORDER_INVALID")
        valid = [ledger for ledger in queue_ledgers if _ledger_fully_valid(ledger, accepted_review_pack_dependency=accepted_review_pack_dependency, expected_overlap_index_sha256=expected_overlap_index_sha256)]
        chosen = valid[:FINAL_QUOTAS[queue]]
        selected.extend(chosen)
        accounting[queue] = {"quota": FINAL_QUOTAS[queue], "reviewed": len(queue_ledgers), "fully_valid": len(valid), "selected": len(chosen), "valid_not_selected_quota": len(valid) - len(chosen), "rejected": len(queue_ledgers) - len(valid), "status": "COMPLETE" if len(chosen) == FINAL_QUOTAS[queue] else "SHORTFALL / EVIDENCE_INSUFFICIENT"}
    diversity = {"distinct_entity_key_count": len({tuple(item["pack_record"]["entity_key"]) for item in selected}), "distinct_topic_key_count": len({tuple(item["pack_record"]["topic_key"]) for item in selected}), "distinct_evidence_family_key_count": len({item["pack_record"]["evidence_family_key"] for item in selected})}
    return {"selected": selected, "queue_accounting": accounting, "diversity_accounting": diversity}


_UNIT3B_PATHS = {
    "semantic_ledger": "review/semantic_ledger.jsonl",
    "query_attempts": "review/query_attempts.jsonl",
    "restricted_c1_audit": "c1/restricted_c1_audit.jsonl",
    "author_feedback": "c1/author_feedback.jsonl",
    "final_ledger": "review/final_ledger.jsonl",
    "freeze_candidates": "final/w7_new_freeze_candidates.jsonl",
    "manifest": "metadata/unit3_manifest.json",
}
_A2_DEPENDENCY_FIELDS = {
    "review_pack_manifest_sha256",
    "review_pack_artifact_sha256",
    "review_pack_byte_count",
    "review_pack_row_count",
    "source_checkpoint",
    "source_generator_sha256",
}
_UNIT3B_TOOLING_FIELDS = {"checkpoint_commit", "generator_sha256"}


def _validate_a2_dependency(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != _A2_DEPENDENCY_FIELDS
        or not _is_sha256(value.get("review_pack_manifest_sha256"))
        or not _is_sha256(value.get("review_pack_artifact_sha256"))
        or not _is_sha256(value.get("source_generator_sha256"))
        or not isinstance(value.get("source_checkpoint"), str)
        or not isinstance(value.get("review_pack_byte_count"), int)
        or not isinstance(value.get("review_pack_row_count"), int)
    ):
        raise Unit3Blocked("UNIT3B_A2_DEPENDENCY_INVALID")
    return {field: value[field] for field in sorted(_A2_DEPENDENCY_FIELDS)}


def _validate_unit3b_tooling_binding(value: Any) -> dict[str, str]:
    if (
        not isinstance(value, Mapping)
        or set(value) != _UNIT3B_TOOLING_FIELDS
        or not isinstance(value.get("checkpoint_commit"), str)
        or not value["checkpoint_commit"]
        or not _is_sha256(value.get("generator_sha256"))
    ):
        raise Unit3Blocked("UNIT3B_TOOLING_BINDING_INVALID")
    return {field: value[field] for field in sorted(_UNIT3B_TOOLING_FIELDS)}


def _unit3b_pack_dependency(a2_dependency: Mapping[str, Any]) -> dict[str, str]:
    return {
        "review_pack_manifest_sha256": a2_dependency["review_pack_manifest_sha256"],
        "review_pack_artifact_sha256": a2_dependency["review_pack_artifact_sha256"],
    }


def _atomic_write_bytes(path: Path, body: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".w7-unit3-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
        if replace:
            temporary.replace(path)
        else:
            temporary.rename(path)
    except OSError as exc:
        raise Unit3Blocked("PERSISTENCE_WRITE_FAILED") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _artifact_descriptor(path: Path, relative_path: str, rows: int) -> dict[str, Any]:
    if not path.exists():
        body = b""
    else:
        body = path.read_bytes()
    return {"path": relative_path, "sha256": _sha256_bytes(body), "byte_count": len(body), "row_count": rows}


def _artifact_descriptor_from_bytes(relative_path: str, body: bytes, rows: int) -> dict[str, Any]:
    return {"path": relative_path, "sha256": _sha256_bytes(body), "byte_count": len(body), "row_count": rows}


def _ensure_artifact_bytes(path: Path, relative_path: str, body: bytes, rows: int) -> dict[str, Any]:
    expected = _artifact_descriptor_from_bytes(relative_path, body, rows)
    if path.exists():
        if path.read_bytes() != body:
            raise Unit3Blocked("FINAL_ARTIFACT_COLLISION")
    else:
        _atomic_write_bytes(path, body, replace=False)
    return expected


def _append_immutable_jsonl(path: Path, expected_rows: list[Mapping[str, Any]], entry: Mapping[str, Any]) -> None:
    """Atomically extend one canonical JSONL prefix, never replacing its content."""
    if path.exists():
        current = _read_jsonl(path)
        if current != expected_rows:
            raise Unit3Blocked("IMMUTABLE_PREFIX_MISMATCH")
    elif expected_rows:
        raise Unit3Blocked("IMMUTABLE_PREFIX_MISSING")
    body = _deterministic_jsonl_bytes([*expected_rows, entry])
    _atomic_write_bytes(path, body, replace=True)


def _write_once_or_verify_jsonl(path: Path, relative_path: str, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    expected = _deterministic_jsonl_bytes(rows)
    if path.exists():
        if path.read_bytes() != expected:
            raise Unit3Blocked("FINAL_ARTIFACT_COLLISION")
    else:
        _atomic_write_bytes(path, expected, replace=False)
    return _artifact_descriptor(path, relative_path, len(rows))


class Unit3BPersistenceStore:
    """Immutable, resumable persistence boundary for future Unit 3B review work."""

    def __init__(
        self,
        output_root: Path,
        review_pack: Iterable[Mapping[str, Any]],
        *,
        a2_dependency: Mapping[str, Any],
        tooling_binding: Mapping[str, Any],
        expected_overlap_index_sha256: str = EXPECTED_OVERLAP_INDEX_SHA256,
    ) -> None:
        self.output_root = Path(output_root)
        self.records = list(review_pack)
        validate_review_pack(self.records)
        self.a2_dependency = _validate_a2_dependency(a2_dependency)
        self.tooling_binding = _validate_unit3b_tooling_binding(tooling_binding)
        if not _is_sha256(expected_overlap_index_sha256):
            raise Unit3Blocked("FINALIZER_C1_DEPENDENCY_INVALID")
        self.expected_overlap_index_sha256 = expected_overlap_index_sha256
        self.pack_dependency = _unit3b_pack_dependency(self.a2_dependency)
        self._c1_authority_nonce = object()
        self._finalized = False
        if self._path("manifest").exists():
            self._load_existing_manifest()
            self._finalized = True
        self._validate_persisted()
        if not self._finalized:
            final_paths = (self._path("final_ledger"), self._path("freeze_candidates"))
            if any(path.exists() for path in final_paths):
                states = self._semantic_states()
                if len(states) != len(self.records) or self.next_action().get("action") != "FINALIZE":
                    raise Unit3Blocked("PREMATURE_FINAL_OUTPUT")
                ledgers = self._terminal_ledgers()
                expected = self._expected_finalization(ledgers)
                expected_bodies = {
                    "final_ledger": _deterministic_jsonl_bytes(expected["final_rows"]),
                    "freeze_candidates": _deterministic_jsonl_bytes(expected["freeze_rows"]),
                }
                for name, body in expected_bodies.items():
                    path = self._path(name)
                    if path.exists() and path.read_bytes() != body:
                        raise Unit3Blocked("FINAL_ARTIFACT_INTEGRITY_MISMATCH")
        if self._finalized:
            self._validate_existing_manifest()

    def _load_existing_manifest(self) -> None:
        """Treat a complete, exactly matching manifest as an immutable terminal marker."""
        manifest = _safe_json_load(self._path("manifest"))
        if manifest.get("status") != "complete" or manifest.get("a2_dependency") != self.a2_dependency or manifest.get("unit3b_tooling") != self.tooling_binding or manifest.get("overlap_index_sha256") != self.expected_overlap_index_sha256:
            raise Unit3Blocked("OUTPUT_COLLISION")
        if not isinstance(manifest.get("artifacts"), Mapping):
            raise Unit3Blocked("OUTPUT_COLLISION")

    def _validate_existing_manifest(self) -> None:
        manifest = _safe_json_load(self._path("manifest"))
        if manifest.get("a2_dependency") != self.a2_dependency or manifest.get("unit3b_tooling") != self.tooling_binding or manifest.get("overlap_index_sha256") != self.expected_overlap_index_sha256:
            raise Unit3Blocked("OUTPUT_COLLISION")
        ledgers = self._terminal_ledgers()
        expected = self._expected_finalization(ledgers)
        if manifest != expected["manifest"]:
            raise Unit3Blocked("OUTPUT_COLLISION")
        for name, descriptor in expected["artifacts"].items():
            path = self._path(name)
            if not path.is_file():
                raise Unit3Blocked("OUTPUT_COLLISION")
            if name in _UNIT3B_PATHS and name != "manifest":
                rows = _read_jsonl(path)
                if path.read_bytes() != _deterministic_jsonl_bytes(rows):
                    raise Unit3Blocked("OUTPUT_COLLISION")
            actual = _artifact_descriptor_from_bytes(descriptor["path"], path.read_bytes(), descriptor["row_count"])
            if actual != descriptor:
                raise Unit3Blocked("OUTPUT_COLLISION")

    def _expected_finalization(self, ledgers: list[dict[str, Any]]) -> dict[str, Any]:
        result = finalize_candidates(ledgers, accepted_review_pack_dependency=self.pack_dependency, expected_overlap_index_sha256=self.expected_overlap_index_sha256)
        selected_hashes = {ledger["semantic_state"]["semantic_state_sha256"] for ledger in result["selected"]}
        final_rows: list[dict[str, Any]] = []
        freeze_rows: list[dict[str, Any]] = []
        for ledger in ledgers:
            state = ledger["semantic_state"]
            selected = state["semantic_state_sha256"] in selected_hashes
            final_rows.append({"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, "candidate_key": state["candidate_key"], "queue": state["queue"], "global_review_order": state["global_review_order"], "queue_review_order": state["queue_review_order"], "semantic_state_sha256": state["semantic_state_sha256"], "semantic_status": state["semantic_status"], "semantic_reason": state["semantic_reason"], "selected": selected, "selection_disposition": "SELECTED" if selected else "NOT_SELECTED"})
            if selected:
                final_attempt = ledger["attempts"][-1]
                freeze_rows.append({"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, "candidate_key": state["candidate_key"], "queue": state["queue"], "global_review_order": state["global_review_order"], "anchor_occurrence_key": state["anchor_occurrence_key"], "entity_key": state["entity_key"], "topic_key": state["topic_key"], "evidence_family_key": state["evidence_family_key"], "accepted_gold_occurrence_keys": state["accepted_gold_occurrence_keys"], "selected_pair_key": state["selected_pair_key"], "query": final_attempt["query"], "answer_proposition": state["answer_proposition"], "semantic_state_sha256": state["semantic_state_sha256"], "attempt_sha256": final_attempt["attempt_sha256"]})
        semantic_rows = self._read_rows("semantic_ledger")
        query_rows = self._read_rows("query_attempts")
        c1_rows = self._read_rows("restricted_c1_audit")
        feedback_rows = self._read_rows("author_feedback")
        descriptors = {
            "semantic_ledger": _artifact_descriptor_from_bytes(_UNIT3B_PATHS["semantic_ledger"], _deterministic_jsonl_bytes(semantic_rows), len(semantic_rows)),
            "query_attempts": _artifact_descriptor_from_bytes(_UNIT3B_PATHS["query_attempts"], _deterministic_jsonl_bytes(query_rows), len(query_rows)),
            "restricted_c1_audit": _artifact_descriptor_from_bytes(_UNIT3B_PATHS["restricted_c1_audit"], _deterministic_jsonl_bytes(c1_rows), len(c1_rows)),
            "author_feedback": _artifact_descriptor_from_bytes(_UNIT3B_PATHS["author_feedback"], _deterministic_jsonl_bytes(feedback_rows), len(feedback_rows)),
            "final_ledger": _artifact_descriptor_from_bytes(_UNIT3B_PATHS["final_ledger"], _deterministic_jsonl_bytes(final_rows), len(final_rows)),
            "freeze_candidates": _artifact_descriptor_from_bytes(_UNIT3B_PATHS["freeze_candidates"], _deterministic_jsonl_bytes(freeze_rows), len(freeze_rows)),
        }
        semantic_by_queue = {queue: {"ACCEPT": 0, "REJECT": 0} for queue in QUEUE_ORDER}
        reasons: dict[str, int] = defaultdict(int)
        for ledger in ledgers:
            state = ledger["semantic_state"]
            semantic_by_queue[state["queue"]][state["semantic_status"]] += 1
            if state["semantic_reason"] is not None:
                reasons[state["semantic_reason"]] += 1
        events = self._attempt_events(); audits = self._read_rows("restricted_c1_audit"); feedback = self._read_rows("author_feedback")
        accounting = {"reviewed": len(ledgers), "semantic_by_queue": semantic_by_queue, "reject_reason_counts": dict(sorted(reasons.items())), "attempt_events": len(events), "authored_attempts": sum(1 for row in events if row.get("event_type") == "AUTHORED_ATTEMPT"), "query_quality_total": sum(1 for row in events if row.get("event_type") == "QUERY_QUALITY_RESULT"), "query_quality_pass": sum(1 for row in events if row.get("event_type") == "QUERY_QUALITY_RESULT" and row.get("quality_result", {}).get("quality_status") == "PASS"), "query_quality_reject": sum(1 for row in events if row.get("event_type") == "QUERY_QUALITY_RESULT" and row.get("quality_result", {}).get("quality_status") == "REJECT"), "restricted_c1_audits": len(audits), "restricted_c1_total": len(audits), "restricted_c1_pass": sum(1 for row in audits if row.get("c1_result", {}).get("overall") == "PASS"), "restricted_c1_reject": sum(1 for row in audits if row.get("c1_result", {}).get("overall") == "REJECT"), "author_feedback": len(feedback), "final": result["queue_accounting"], "diversity": result["diversity_accounting"], "benchmark_v0_4": "NOT_CREATED", "retrieval_evaluation": "NOT_EXECUTED"}
        manifest = {"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, "status": "complete", "a2_dependency": self.a2_dependency, "unit3b_tooling": self.tooling_binding, "overlap_index_sha256": self.expected_overlap_index_sha256, "artifacts": descriptors, "accounting": accounting}
        return {"manifest": manifest, "artifacts": descriptors, "final_rows": final_rows, "freeze_rows": freeze_rows}

    def _path(self, name: str) -> Path:
        return self.output_root / _UNIT3B_PATHS[name]

    def _ensure_open(self) -> None:
        if self._finalized:
            raise Unit3Blocked("OUTPUT_COLLISION")

    def _read_rows(self, name: str) -> list[Mapping[str, Any]]:
        path = self._path(name)
        return _read_jsonl(path) if path.exists() else []

    def _ensure_empty_children(self) -> None:
        for name in ("query_attempts", "restricted_c1_audit", "author_feedback"):
            path = self._path(name)
            if not path.exists():
                _atomic_write_bytes(path, b"", replace=False)

    def _semantic_states(self) -> list[Mapping[str, Any]]:
        rows = self._read_rows("semantic_ledger")
        states: list[Mapping[str, Any]] = []
        if len(rows) > len(self.records):
            raise Unit3Blocked("SEMANTIC_LEDGER_ORDER_INVALID")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or set(row) != {"schema_version", "semantic_state"} or row.get("schema_version") != UNIT3B_PERSISTENCE_SCHEMA_VERSION or not isinstance(row.get("semantic_state"), Mapping):
                raise Unit3Blocked("SEMANTIC_LEDGER_SCHEMA_INVALID")
            state = row["semantic_state"]
            validate_frozen_semantic_state(state, record=self.records[index], review_pack_dependency=self.pack_dependency)
            states.append(state)
        return states

    def _attempt_events(self) -> list[Mapping[str, Any]]:
        rows = self._read_rows("query_attempts")
        for row in rows:
            if not isinstance(row, Mapping) or row.get("schema_version") != UNIT3B_PERSISTENCE_SCHEMA_VERSION or row.get("event_type") not in ("AUTHORED_ATTEMPT", "QUERY_QUALITY_RESULT"):
                raise Unit3Blocked("QUERY_EVENT_SCHEMA_INVALID")
            if set(row) != ({"schema_version", "event_type", "attempt"} if row["event_type"] == "AUTHORED_ATTEMPT" else {"schema_version", "event_type", "quality_result"}):
                raise Unit3Blocked("QUERY_EVENT_SCHEMA_INVALID")
        return rows

    def _c1_rows(self) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        audits, feedback = self._read_rows("restricted_c1_audit"), self._read_rows("author_feedback")
        for audit in audits:
            if not isinstance(audit, Mapping) or set(audit) != {"schema_version", "c1_result"} or audit.get("schema_version") != UNIT3B_PERSISTENCE_SCHEMA_VERSION or not isinstance(audit.get("c1_result"), Mapping):
                raise Unit3Blocked("RESTRICTED_C1_AUDIT_SCHEMA_INVALID")
        for row in feedback:
            if not isinstance(row, Mapping) or set(row) != {"schema_version", "attempt_id", "overall"} or row.get("schema_version") != UNIT3B_PERSISTENCE_SCHEMA_VERSION or not isinstance(row.get("attempt_id"), str) or row.get("overall") not in ("PASS", "REJECT"):
                raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
        return audits, feedback

    def _validate_global_log_order(self, events: list[Mapping[str, Any]], audits: list[Mapping[str, Any]], feedback: list[Mapping[str, Any]], states: list[Mapping[str, Any]]) -> None:
        order = {state["candidate_key"]: index for index, state in enumerate(states)}
        attempt_numbers: dict[str, int] = {}
        for event in events:
            payload = event.get("attempt") if event["event_type"] == "AUTHORED_ATTEMPT" else event.get("quality_result")
            if not isinstance(payload, Mapping):
                raise Unit3Blocked("PERSISTED_LOG_ORDER_INVALID")
            attempt_id = _required_string(payload, "attempt_id")
            if event["event_type"] == "AUTHORED_ATTEMPT":
                number = payload.get("attempt_number")
                if not isinstance(number, int) or isinstance(number, bool):
                    raise Unit3Blocked("PERSISTED_LOG_ORDER_INVALID")
                attempt_numbers[attempt_id] = number
        def event_key(attempt_id: Any) -> tuple[int, int]:
            if not isinstance(attempt_id, str) or ":attempt:" not in attempt_id:
                raise Unit3Blocked("PERSISTED_LOG_ORDER_INVALID")
            candidate = attempt_id.rsplit(":attempt:", 1)[0]
            if candidate not in order:
                raise Unit3Blocked("PERSISTED_LOG_ORDER_INVALID")
            number = attempt_numbers.get(attempt_id)
            if number is None:
                raise Unit3Blocked("PERSISTED_LOG_ORDER_INVALID")
            return order[candidate], number
        for rows, getter, code in (
            (events, lambda row: (row.get("attempt") or row.get("quality_result", {})).get("attempt_id"), "QUERY_EVENT_ORDER_INVALID"),
            (audits, lambda row: row.get("c1_result", {}).get("attempt_id"), "C1_LOG_ORDER_INVALID"),
            (feedback, lambda row: row.get("attempt_id"), "AUTHOR_FEEDBACK_ORDER_INVALID"),
        ):
            previous = (-1, -1)
            for row in rows:
                index = event_key(getter(row))
                if index < previous:
                    raise Unit3Blocked(code)
                previous = index

    def _candidate_events(self, state: Mapping[str, Any], events: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        attempts: list[Mapping[str, Any]] = []
        qualities: list[Mapping[str, Any]] = []
        for event in events:
            payload = event.get("attempt") if event["event_type"] == "AUTHORED_ATTEMPT" else event.get("quality_result")
            if isinstance(payload, Mapping) and payload.get("attempt_id", "").startswith(f"{state['candidate_key']}:attempt:"):
                (attempts if event["event_type"] == "AUTHORED_ATTEMPT" else qualities).append(payload)
        return attempts, qualities

    def _candidate_c1(self, state: Mapping[str, Any], audits: Iterable[Mapping[str, Any]], feedback: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        prefix = f"{state['candidate_key']}:attempt:"
        candidate_audits = [row["c1_result"] for row in audits if row["c1_result"].get("attempt_id", "").startswith(prefix)]
        candidate_feedback = [row for row in feedback if row.get("attempt_id", "").startswith(prefix)]
        return candidate_audits, candidate_feedback

    def _validate_persisted(self) -> None:
        states = self._semantic_states()
        events = self._attempt_events()
        audits, feedback = self._c1_rows()
        all_attempts: dict[str, Mapping[str, Any]] = {}
        all_qualities: dict[str, Mapping[str, Any]] = {}
        all_audits: dict[str, Mapping[str, Any]] = {}
        all_feedback: dict[str, Mapping[str, Any]] = {}
        for event in events:
            payload = event.get("attempt") if event["event_type"] == "AUTHORED_ATTEMPT" else event.get("quality_result")
            if not isinstance(payload, Mapping):
                raise Unit3Blocked("QUERY_EVENT_SCHEMA_INVALID")
            attempt_id = _required_string(payload, "attempt_id")
            target = all_attempts if event["event_type"] == "AUTHORED_ATTEMPT" else all_qualities
            if attempt_id in target:
                raise Unit3Blocked("QUERY_EVENT_ID_DUPLICATED")
            target[attempt_id] = payload
        for audit_row in audits:
            audit = audit_row["c1_result"]
            attempt_id = _required_string(audit, "attempt_id")
            if attempt_id in all_audits:
                raise Unit3Blocked("C1_RESULT_INVALID")
            all_audits[attempt_id] = audit
        for feedback_row in feedback:
            attempt_id = _required_string(feedback_row, "attempt_id")
            if attempt_id in all_feedback:
                raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
            all_feedback[attempt_id] = feedback_row
        seen_attempt_ids: set[str] = set()
        seen_quality_ids: set[str] = set()
        audit_by_attempt: dict[str, Mapping[str, Any]] = {}
        for audit_row in audits:
            audit = audit_row["c1_result"]
            attempt_id = _required_string(audit, "attempt_id")
            if attempt_id in audit_by_attempt:
                raise Unit3Blocked("C1_RESULT_INVALID")
            audit_by_attempt[attempt_id] = audit
        feedback_by_attempt_global: dict[str, Mapping[str, Any]] = {}
        for feedback_row in feedback:
            attempt_id = _required_string(feedback_row, "attempt_id")
            if attempt_id in feedback_by_attempt_global:
                raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
            feedback_by_attempt_global[attempt_id] = feedback_row
        for index, (state, record) in enumerate(zip(states, self.records)):
            attempts, qualities = self._candidate_events(state, events)
            c1_results, candidate_feedback = self._candidate_c1(state, audits, feedback)
            if state["semantic_status"] == "REJECT" and (attempts or qualities or c1_results or candidate_feedback):
                raise Unit3Blocked("QUERY_EVENT_AFTER_SEMANTIC_REJECT")
            candidate_events = [
                event
                for event in events
                if isinstance(
                    (event.get("attempt") if event["event_type"] == "AUTHORED_ATTEMPT" else event.get("quality_result")),
                    Mapping,
                )
                and (event.get("attempt") if event["event_type"] == "AUTHORED_ATTEMPT" else event.get("quality_result"))["attempt_id"].startswith(f"{state['candidate_key']}:attempt:")
            ]
            expected_types: list[str] = []
            for position, attempt in enumerate(attempts):
                expected_types.append("AUTHORED_ATTEMPT")
                if position < len(qualities):
                    if qualities[position].get("attempt_id") != attempt.get("attempt_id"):
                        raise Unit3Blocked("QUERY_EVENT_ORDER_INVALID")
                    expected_types.append("QUERY_QUALITY_RESULT")
            if len(qualities) > len(attempts):
                raise Unit3Blocked("QUERY_EVENT_ORDER_INVALID")
            if [event["event_type"] for event in candidate_events] != expected_types:
                raise Unit3Blocked("QUERY_EVENT_ORDER_INVALID")
            for attempt in attempts:
                if attempt["attempt_id"] in seen_attempt_ids:
                    raise Unit3Blocked("QUERY_ATTEMPT_ID_DUPLICATED")
                seen_attempt_ids.add(attempt["attempt_id"])
            for quality in qualities:
                if quality["attempt_id"] in seen_quality_ids:
                    raise Unit3Blocked("QUERY_QUALITY_RESULT_INVALID")
                seen_quality_ids.add(quality["attempt_id"])
            validate_attempt_history(state, attempts, qualities, c1_results, record=record, review_pack_dependency=self.pack_dependency)
            feedback_by_attempt = {row["attempt_id"]: row for row in candidate_feedback}
            for result in c1_results:
                if result["overlap_index_sha256"] != self.expected_overlap_index_sha256:
                    raise Unit3Blocked("FINALIZER_C1_DEPENDENCY_INVALID")
                item = feedback_by_attempt.get(result["attempt_id"])
                if item is not None and item["overall"] != result["overall"]:
                    raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
                if item is not None:
                    attempt = next((a for a in attempts if a["attempt_id"] == result["attempt_id"]), None)
                    quality = next((q for q in qualities if q["attempt_id"] == result["attempt_id"]), None)
                    if attempt is None or quality is None:
                        raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
                    validate_c1_result(attempt, quality, result)
            if set(feedback_by_attempt) - {result["attempt_id"] for result in c1_results}:
                raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
            if index < len(states) - 1 and not self._candidate_terminal(state, record, events, audits, feedback):
                raise Unit3Blocked("SEMANTIC_LEDGER_PREFIX_NOT_TERMINAL")
        known_prefixes = {f"{state['candidate_key']}:attempt:" for state in states}
        for event in events:
            payload = event.get("attempt") if event["event_type"] == "AUTHORED_ATTEMPT" else event.get("quality_result")
            if not isinstance(payload, Mapping) or not any(payload.get("attempt_id", "").startswith(prefix) for prefix in known_prefixes):
                raise Unit3Blocked("QUERY_EVENT_OUTSIDE_SEMANTIC_PREFIX")
        for audit in audits:
            if not any(audit["c1_result"].get("attempt_id", "").startswith(prefix) for prefix in known_prefixes):
                raise Unit3Blocked("RESTRICTED_C1_AUDIT_OUTSIDE_SEMANTIC_PREFIX")
        for row in feedback:
            if not any(row.get("attempt_id", "").startswith(prefix) for prefix in known_prefixes):
                raise Unit3Blocked("AUTHOR_FEEDBACK_OUTSIDE_SEMANTIC_PREFIX")
            attempt_id = row["attempt_id"]
            attempt = all_attempts.get(attempt_id)
            quality = all_qualities.get(attempt_id)
            audit = all_audits.get(attempt_id)
            if attempt is None or quality is None or audit is None or quality.get("quality_status") != "PASS":
                raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
            if row.get("overall") != audit.get("overall"):
                raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
            validate_query_quality_result(attempt, quality)
            validate_c1_result(attempt, quality, audit)
        self._validate_global_log_order(events, audits, feedback, states)

    def _candidate_terminal(self, state: Mapping[str, Any], record: Mapping[str, Any], events: list[Mapping[str, Any]], audits: list[Mapping[str, Any]], feedback: list[Mapping[str, Any]]) -> bool:
        if state["semantic_status"] == "REJECT":
            return True
        attempts, qualities = self._candidate_events(state, events)
        c1_results, candidate_feedback = self._candidate_c1(state, audits, feedback)
        if any(result["attempt_id"] not in {row["attempt_id"] for row in candidate_feedback} for result in c1_results):
            return False
        if not attempts:
            return False
        final = attempts[-1]
        quality_by_attempt = {item["attempt_id"]: item for item in qualities}
        result_by_attempt = {item["attempt_id"]: item for item in c1_results}
        quality = quality_by_attempt.get(final["attempt_id"])
        if quality is None:
            return False
        if quality["quality_status"] == "REJECT":
            if final["attempt_number"] == 1:
                return quality.get("retry_disposition") == "TERMINAL"
            return final["attempt_number"] == MAX_PERSISTED_AUTHORED_QUERY_ATTEMPTS
        result = result_by_attempt.get(final["attempt_id"])
        if result is None:
            return False
        return result["overall"] == "PASS" or final["attempt_number"] == MAX_PERSISTED_AUTHORED_QUERY_ATTEMPTS

    def next_action(self) -> dict[str, Any]:
        self._validate_persisted()
        states, events = self._semantic_states(), self._attempt_events()
        audits, feedback = self._c1_rows()
        for index, record in enumerate(self.records):
            if index >= len(states):
                return {"action": "REVIEW_SEMANTIC", "global_review_order": record["global_review_order"], "queue": record["queue"]}
            state = states[index]
            if state["semantic_status"] == "REJECT":
                continue
            attempts, qualities = self._candidate_events(state, events)
            c1_results, candidate_feedback = self._candidate_c1(state, audits, feedback)
            feedback_ids = {row["attempt_id"] for row in candidate_feedback}
            for result in c1_results:
                if result["attempt_id"] not in feedback_ids:
                    return {"action": "PERSIST_AUTHOR_FEEDBACK", "global_review_order": record["global_review_order"], "attempt_id": result["attempt_id"]}
            if not attempts:
                return {"action": "AUTHOR_ATTEMPT", "attempt_number": 1, "global_review_order": record["global_review_order"]}
            final = attempts[-1]
            quality_by_attempt = {item["attempt_id"]: item for item in qualities}
            result_by_attempt = {item["attempt_id"]: item for item in c1_results}
            quality = quality_by_attempt.get(final["attempt_id"])
            if quality is None:
                return {"action": "PERSIST_QUERY_QUALITY", "global_review_order": record["global_review_order"], "attempt_id": final["attempt_id"]}
            if quality["quality_status"] == "PASS" and final["attempt_id"] not in result_by_attempt:
                return {"action": "RUN_RESTRICTED_C1", "global_review_order": record["global_review_order"], "attempt_id": final["attempt_id"]}
            if result_by_attempt.get(final["attempt_id"], {}).get("overall") == "PASS":
                continue
            if final["attempt_number"] == 1 and result_by_attempt.get(final["attempt_id"], {}).get("overall") == "REJECT":
                return {"action": "AUTHOR_ATTEMPT", "attempt_number": 2, "global_review_order": record["global_review_order"]}
            if final["attempt_number"] == 1 and quality.get("retry_disposition") == "ATTEMPT_2_AUTHORIZED":
                return {"action": "AUTHOR_ATTEMPT", "attempt_number": 2, "global_review_order": record["global_review_order"]}
            if final["attempt_number"] == 1 and quality.get("retry_disposition") == "TERMINAL":
                continue
            if final["attempt_number"] == 1:
                raise Unit3Blocked("MATERIAL_QUALITY_RETRY_MAPPING_UNRESOLVED")
        return {"action": "FINALIZE"}

    def append_semantic_state(self, state: Mapping[str, Any]) -> None:
        self._ensure_open()
        action = self.next_action()
        if action["action"] != "REVIEW_SEMANTIC":
            raise Unit3Blocked("SEMANTIC_APPEND_OUT_OF_ORDER")
        record = self.records[action["global_review_order"] - 1]
        validate_frozen_semantic_state(state, record=record, review_pack_dependency=self.pack_dependency)
        rows = self._read_rows("semantic_ledger")
        _append_immutable_jsonl(self._path("semantic_ledger"), rows, {"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, "semantic_state": state})

    def append_authored_attempt(self, attempt: Mapping[str, Any]) -> None:
        self._ensure_open()
        action = self.next_action()
        if action["action"] != "AUTHOR_ATTEMPT" or attempt.get("attempt_number") != action["attempt_number"]:
            raise Unit3Blocked("QUERY_ATTEMPT_APPEND_OUT_OF_ORDER")
        state = self._semantic_states()[action["global_review_order"] - 1]
        _validate_attempt(attempt, state, action["attempt_number"])
        rows = self._attempt_events()
        _append_immutable_jsonl(self._path("query_attempts"), rows, {"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, "event_type": "AUTHORED_ATTEMPT", "attempt": attempt})

    def append_query_quality_result(self, quality_result: Mapping[str, Any]) -> None:
        self._ensure_open()
        action = self.next_action()
        if action["action"] != "PERSIST_QUERY_QUALITY" or quality_result.get("attempt_id") != action["attempt_id"]:
            raise Unit3Blocked("QUERY_QUALITY_APPEND_OUT_OF_ORDER")
        state = self._semantic_states()[action["global_review_order"] - 1]
        attempts, _ = self._candidate_events(state, self._attempt_events())
        validate_query_quality_result(attempts[-1], quality_result)
        rows = self._attempt_events()
        _append_immutable_jsonl(self._path("query_attempts"), rows, {"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, "event_type": "QUERY_QUALITY_RESULT", "quality_result": quality_result})

    def _persist_restricted_c1_result(self, c1_result: Mapping[str, Any], *, _authority: object | None = None) -> dict[str, str]:
        self._ensure_open()
        if _authority is not self._c1_authority_nonce:
            raise Unit3Blocked("C1_AUTHORITY_BOUNDARY")
        action = self.next_action()
        if action["action"] != "RUN_RESTRICTED_C1" or c1_result.get("attempt_id") != action["attempt_id"]:
            raise Unit3Blocked("C1_APPEND_OUT_OF_ORDER")
        state = self._semantic_states()[action["global_review_order"] - 1]
        attempts, qualities = self._candidate_events(state, self._attempt_events())
        validate_c1_result(attempts[-1], qualities[-1], c1_result)
        if c1_result["overlap_index_sha256"] != self.expected_overlap_index_sha256:
            raise Unit3Blocked("FINALIZER_C1_DEPENDENCY_INVALID")
        rows = self._read_rows("restricted_c1_audit")
        _append_immutable_jsonl(self._path("restricted_c1_audit"), rows, {"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, "c1_result": c1_result})
        return {"attempt_id": c1_result["attempt_id"], "overall": c1_result["overall"]}

    def append_restricted_c1_result(self, c1_result: Mapping[str, Any]) -> dict[str, str]:
        """C1 audits are authoritative only when produced by run_restricted_c1."""
        raise Unit3Blocked("C1_AUTHORITY_BOUNDARY")

    def append_author_feedback(self, feedback: Mapping[str, Any]) -> None:
        self._ensure_open()
        action = self.next_action()
        if action["action"] != "PERSIST_AUTHOR_FEEDBACK" or set(feedback) != {"attempt_id", "overall"} or feedback.get("attempt_id") != action["attempt_id"] or feedback.get("overall") not in ("PASS", "REJECT"):
            raise Unit3Blocked("AUTHOR_FEEDBACK_APPEND_OUT_OF_ORDER")
        audits, _ = self._c1_rows()
        result = next((row["c1_result"] for row in audits if row["c1_result"]["attempt_id"] == feedback["attempt_id"]), None)
        if result is None or result["overall"] != feedback["overall"]:
            raise Unit3Blocked("AUTHOR_FEEDBACK_SCHEMA_INVALID")
        rows = self._read_rows("author_feedback")
        _append_immutable_jsonl(self._path("author_feedback"), rows, {"schema_version": UNIT3B_PERSISTENCE_SCHEMA_VERSION, **dict(feedback)})

    def run_restricted_c1(self, overlap_index_raw_bytes: bytes) -> dict[str, str]:
        """Restricted boundary: persist audit internally and return author-safe feedback only."""
        self._ensure_open()
        action = self.next_action()
        if action["action"] != "RUN_RESTRICTED_C1":
            raise Unit3Blocked("C1_APPEND_OUT_OF_ORDER")
        state = self._semantic_states()[action["global_review_order"] - 1]
        attempts, qualities = self._candidate_events(state, self._attempt_events())
        audits, feedback = restricted_c1_check([attempts[-1]], [qualities[-1]], overlap_index_raw_bytes, expected_index_sha256=self.expected_overlap_index_sha256)
        safe_feedback = self._persist_restricted_c1_result(audits[0], _authority=self._c1_authority_nonce)
        if feedback != [safe_feedback]:
            raise Unit3Blocked("RESTRICTED_C1_FEEDBACK_INVALID")
        return safe_feedback

    def _terminal_ledgers(self) -> list[dict[str, Any]]:
        states, events = self._semantic_states(), self._attempt_events()
        audits, feedback = self._c1_rows()
        if len(states) != len(self.records) or self.next_action().get("action") != "FINALIZE":
            raise Unit3Blocked("FINALIZATION_INCOMPLETE")
        ledgers: list[dict[str, Any]] = []
        for state, record in zip(states, self.records, strict=True):
            attempts, qualities = self._candidate_events(state, events)
            c1_results, _ = self._candidate_c1(state, audits, feedback)
            ledgers.append({"pack_record": record, "review_pack_dependency": self.pack_dependency, "semantic_state": state, "attempts": attempts, "query_quality_results": qualities, "c1_results": c1_results})
        return ledgers

    def finalize(self) -> dict[str, Any]:
        manifest_path = self._path("manifest")
        if manifest_path.exists():
            if not self._finalized:
                raise Unit3Blocked("OUTPUT_COLLISION")
            manifest = _safe_json_load(manifest_path)
            return {"manifest": manifest, "manifest_descriptor": _artifact_descriptor(manifest_path, _UNIT3B_PATHS["manifest"], 1)}
        ledgers = self._terminal_ledgers()
        self._ensure_empty_children()
        expected = self._expected_finalization(ledgers)
        artifacts = expected["artifacts"]
        _ensure_artifact_bytes(self._path("final_ledger"), _UNIT3B_PATHS["final_ledger"], _deterministic_jsonl_bytes(expected["final_rows"]), len(expected["final_rows"]))
        _ensure_artifact_bytes(self._path("freeze_candidates"), _UNIT3B_PATHS["freeze_candidates"], _deterministic_jsonl_bytes(expected["freeze_rows"]), len(expected["freeze_rows"]))
        self._validate_persisted()
        for name, descriptor in artifacts.items():
            path = self._path(name)
            if _artifact_descriptor(path, descriptor["path"], descriptor["row_count"]) != descriptor:
                raise Unit3Blocked("FINAL_ARTIFACT_INTEGRITY_MISMATCH")
        manifest = expected["manifest"]
        _atomic_write_bytes(manifest_path, canonical_json_bytes(manifest), replace=False)
        self._finalized = True
        return {"manifest": manifest, "manifest_descriptor": _artifact_descriptor(manifest_path, _UNIT3B_PATHS["manifest"], 1)}


class Unit3BSemanticExposureController:
    """Sanctioned one-candidate body boundary for semantic-facing code."""

    __slots__ = ("_output_root", "_tooling_checkpoint")

    def __init__(self, output_root: Path, *, tooling_checkpoint: str) -> None:
        self._output_root = Path(output_root)
        self._tooling_checkpoint = tooling_checkpoint

    def current_candidate_body(self) -> dict[str, Any]:
        """Return one detached current body, or fail closed before exposure."""
        return _run_semantic_controller_operation(self, _semantic_current_body)

    def current_action(self) -> dict[str, Any]:
        """Return only the amendment-authorized current workflow action."""
        return _run_semantic_controller_operation(self, _semantic_current_action)

    def persist_semantic_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a state only for the mechanically current candidate/action."""
        return _run_semantic_controller_operation(self, lambda store: _semantic_persist_state(store, state))

    def author_query(self, query: str) -> dict[str, Any]:
        """Create the mechanically authorized current attempt without caller numbering."""
        return _run_semantic_controller_operation(self, lambda store: _semantic_author_query(store, query))

    def persist_query_quality(
        self,
        quality_status: str,
        quality_reason: str | None,
    ) -> dict[str, Any]:
        """Persist quality only for the mechanically current authored attempt."""
        if quality_status == "REJECT":
            raise Unit3Blocked("MATERIAL_QUALITY_RETRY_MAPPING_UNRESOLVED")
        return _run_semantic_controller_operation(
            self,
            lambda store: _semantic_persist_quality(store, quality_status, quality_reason),
        )

    def persist_author_feedback(self, overall: str) -> dict[str, str]:
        """Persist only the current restricted-checker safe feedback projection."""
        return _run_semantic_controller_operation(self, lambda store: _semantic_persist_feedback(store, overall))


def _run_semantic_controller_operation(
    controller: Unit3BSemanticExposureController,
    operation: Any,
) -> Any:
    """Module-private trusted operation: open, validate, operate once, return safe data."""
    store = open_production_unit3b_store(
        controller._output_root, tooling_checkpoint=controller._tooling_checkpoint
    )
    return operation(store)


class Unit3BQualityAuthority:
    """Sanctioned quality/pair-consistency persistence boundary."""

    __slots__ = ("_output_root", "_tooling_checkpoint")

    def __init__(self, output_root: Path, *, tooling_checkpoint: str) -> None:
        self._output_root = Path(output_root)
        self._tooling_checkpoint = tooling_checkpoint

    def persist_quality_judgment(self, quality_result: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the exact current authorized quality judgment, without classifying it."""
        return _run_quality_authority_operation(
            self, lambda store: _persist_quality_authority_judgment(store, quality_result)
        )


def _run_quality_authority_operation(
    authority: Unit3BQualityAuthority,
    operation: Any,
) -> Any:
    """Module-private trusted quality operation returning only role-safe data."""
    store = open_production_unit3b_store(
        authority._output_root, tooling_checkpoint=authority._tooling_checkpoint
    )
    return operation(store)


def _candidate_exposure_terminal(
    store: Unit3BPersistenceStore,
    state: Mapping[str, Any],
    events: list[Mapping[str, Any]],
    audits: list[Mapping[str, Any]],
    feedback: list[Mapping[str, Any]],
) -> bool:
    if state["semantic_status"] == "REJECT":
        return True
    attempts, qualities = store._candidate_events(state, events)
    c1_results, candidate_feedback = store._candidate_c1(state, audits, feedback)
    if not attempts or len(qualities) != len(attempts):
        return False
    first_attempt = attempts[0]
    first_quality = next((item for item in qualities if item.get("attempt_id") == first_attempt.get("attempt_id")), None)
    if first_quality is not None and first_quality["quality_status"] == "REJECT":
        disposition = first_quality.get("retry_disposition")
        if disposition not in _QUALITY_RETRY_DISPOSITIONS:
            raise Unit3Blocked("MATERIAL_QUALITY_RETRY_MAPPING_UNRESOLVED")
        if disposition == "ATTEMPT_2_AUTHORIZED" and len(attempts) < 2:
            return False
        if disposition == "TERMINAL":
            return True
    final = attempts[-1]
    quality = next((item for item in qualities if item.get("attempt_id") == final.get("attempt_id")), None)
    if quality is None:
        return False
    if quality["quality_status"] == "REJECT":
        return final["attempt_number"] == 2
    result = next((item for item in c1_results if item.get("attempt_id") == final.get("attempt_id")), None)
    matching_feedback = [item for item in candidate_feedback if item.get("attempt_id") == final.get("attempt_id")]
    if result is None or len(matching_feedback) != 1:
        return False
    return final["attempt_number"] == 2 or result["overall"] == "PASS"


def _semantic_current_record(store: Unit3BPersistenceStore) -> tuple[Mapping[str, Any] | None, int | None]:
    store._validate_persisted()
    states, events = store._semantic_states(), store._attempt_events()
    audits, feedback = store._c1_rows()
    for index, record in enumerate(store.records):
        if index >= len(states) or not _candidate_exposure_terminal(store, states[index], events, audits, feedback):
            return record, index
    return None, None


def _semantic_current_action(store: Unit3BPersistenceStore) -> dict[str, Any]:
    record, _ = _semantic_current_record(store)
    action = store.next_action()
    if record is None:
        if action.get("action") != "FINALIZE":
            raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
        return {"action": "FINALIZE"}
    if action.get("global_review_order") != record["global_review_order"]:
        raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
    safe = {key: action[key] for key in ("action", "attempt_number", "attempt_id") if key in action}
    return safe


def _semantic_current_body(store: Unit3BPersistenceStore) -> dict[str, Any]:
    record, _ = _semantic_current_record(store)
    if record is None:
        raise Unit3Blocked("UNIT3B_EXPOSURE_COMPLETE")
    return copy.deepcopy(dict(record))


def _semantic_persist_state(store: Unit3BPersistenceStore, state: Mapping[str, Any]) -> dict[str, Any]:
    record, _ = _semantic_current_record(store)
    action = _semantic_current_action(store)
    if record is None or action["action"] != "REVIEW_SEMANTIC" or state.get("candidate_key") != record["candidate_key"]:
        raise Unit3Blocked("SEMANTIC_EXPOSURE_CANDIDATE_MISMATCH")
    store.append_semantic_state(state)
    return {"global_review_order": record["global_review_order"], "semantic_status": state.get("semantic_status")}


def _semantic_author_query(store: Unit3BPersistenceStore, query: str) -> dict[str, Any]:
    record, index = _semantic_current_record(store)
    action = _semantic_current_action(store)
    if record is None or index is None or action["action"] != "AUTHOR_ATTEMPT":
        raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
    state = store._semantic_states()[index]
    attempt = persist_query_attempt(
        state, action["attempt_number"], query, record=record, review_pack_dependency=store.pack_dependency
    )
    store.append_authored_attempt(attempt)
    return {"attempt_id": attempt["attempt_id"], "attempt_number": attempt["attempt_number"]}


def _semantic_persist_quality(
    store: Unit3BPersistenceStore, quality_status: str, quality_reason: str | None
) -> dict[str, Any]:
    record, index = _semantic_current_record(store)
    action = _semantic_current_action(store)
    if record is None or index is None or action["action"] != "PERSIST_QUERY_QUALITY":
        raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
    state = store._semantic_states()[index]
    attempts, _ = store._candidate_events(state, store._attempt_events())
    quality = persist_query_quality_result(attempts[-1], quality_status, quality_reason)
    if quality["attempt_id"] != action["attempt_id"]:
        raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
    store.append_query_quality_result(quality)
    return {"attempt_id": quality["attempt_id"], "quality_status": quality["quality_status"]}


def _persist_quality_authority_judgment(
    store: Unit3BPersistenceStore, quality_result: Mapping[str, Any]
) -> dict[str, Any]:
    record, index = _semantic_current_record(store)
    action = _semantic_current_action(store)
    if record is None or index is None or action["action"] != "PERSIST_QUERY_QUALITY":
        raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
    state = store._semantic_states()[index]
    attempts, _ = store._candidate_events(state, store._attempt_events())
    if not attempts or quality_result.get("attempt_id") != action.get("attempt_id"):
        raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
    validate_query_quality_result(attempts[-1], quality_result)
    store.append_query_quality_result(quality_result)
    return {"attempt_id": quality_result["attempt_id"], "quality_status": quality_result["quality_status"]}


def _semantic_persist_feedback(store: Unit3BPersistenceStore, overall: str) -> dict[str, str]:
    action = _semantic_current_action(store)
    if action["action"] != "PERSIST_AUTHOR_FEEDBACK" or overall not in ("PASS", "REJECT"):
        raise Unit3Blocked("SEMANTIC_EXPOSURE_ACTION_INVALID")
    feedback = {"attempt_id": action["attempt_id"], "overall": overall}
    store.append_author_feedback(feedback)
    return dict(feedback)


def open_production_unit3b_store(output_root: Path, *, tooling_checkpoint: str) -> Unit3BPersistenceStore:
    """Open the sole frozen A-2 pack for Unit 3B after its review gate allows exposure."""
    root = Path(output_root)
    accepted_root = _accepted_review_pack_root(root)
    manifest_path = accepted_root / "metadata" / "review_pack_manifest.json"
    artifact_path = accepted_root / "review_pack" / "frozen_48_review_pack.jsonl.gz"
    if (
        not manifest_path.is_file()
        or not artifact_path.is_file()
        or _sha256_path(manifest_path) != FROZEN_A2_REVIEW_PACK_MANIFEST_SHA256
        or _sha256_path(artifact_path) != FROZEN_A2_REVIEW_PACK_ARTIFACT_SHA256
        or artifact_path.stat().st_size != FROZEN_A2_REVIEW_PACK_BYTES
    ):
        raise Unit3Blocked("FROZEN_REVIEW_PACK_DEPENDENCY_MISMATCH")
    manifest = _safe_json_load(manifest_path)
    review_pack = manifest.get("review_pack")
    generator = manifest.get("generator")
    if (
        manifest.get("checkpoint_commit") != FROZEN_A2_SOURCE_CHECKPOINT
        or not isinstance(review_pack, Mapping)
        or review_pack.get("sha256") != FROZEN_A2_REVIEW_PACK_ARTIFACT_SHA256
        or review_pack.get("byte_count") != FROZEN_A2_REVIEW_PACK_BYTES
        or review_pack.get("row_count") != FROZEN_A2_REVIEW_PACK_ROWS
        or not isinstance(generator, Mapping)
        or not _is_sha256(generator.get("sha256"))
    ):
        raise Unit3Blocked("FROZEN_REVIEW_PACK_MANIFEST_INVALID")
    tooling = verify_checkpoint_generator_binding(tooling_checkpoint)
    rows = _read_jsonl_gzip(artifact_path)
    if len(rows) != FROZEN_A2_REVIEW_PACK_ROWS:
        raise Unit3Blocked("FROZEN_REVIEW_PACK_DEPENDENCY_MISMATCH")
    return Unit3BPersistenceStore(
        root,
        rows,
        a2_dependency={
            "review_pack_manifest_sha256": FROZEN_A2_REVIEW_PACK_MANIFEST_SHA256,
            "review_pack_artifact_sha256": FROZEN_A2_REVIEW_PACK_ARTIFACT_SHA256,
            "review_pack_byte_count": FROZEN_A2_REVIEW_PACK_BYTES,
            "review_pack_row_count": FROZEN_A2_REVIEW_PACK_ROWS,
            "source_checkpoint": FROZEN_A2_SOURCE_CHECKPOINT,
            "source_generator_sha256": generator["sha256"],
        },
        tooling_binding={"checkpoint_commit": tooling["checkpoint_commit"], "generator_sha256": tooling["generator_sha256"]},
    )


def open_production_unit3b_semantic_exposure(output_root: Path, *, tooling_checkpoint: str) -> Unit3BSemanticExposureController:
    """Open the sanctioned semantic-facing single-candidate exposure boundary."""
    return Unit3BSemanticExposureController(output_root, tooling_checkpoint=tooling_checkpoint)


def open_production_unit3b_quality_authority(output_root: Path, *, tooling_checkpoint: str) -> Unit3BQualityAuthority:
    """Open the sanctioned quality/pair-consistency persistence boundary."""
    return Unit3BQualityAuthority(output_root, tooling_checkpoint=tooling_checkpoint)


def _verify_artifact(root: Path, relative_path: str, metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    path = root / relative_path
    if not path.is_file() or _sha256_path(path) != metadata.get("sha256") or path.stat().st_size != metadata.get("byte_count"):
        raise Unit3Blocked("UNIT2_ARTIFACT_INTEGRITY_MISMATCH")
    rows = _read_jsonl_gzip(path)
    if len(rows) != metadata.get("row_count"):
        raise Unit3Blocked("UNIT2_ARTIFACT_ROW_COUNT_MISMATCH")
    return rows


def verify_checkpoint_generator_binding(checkpoint_commit: str, *, module_path: Path | None = None) -> dict[str, str]:
    """Prove the executed generator bytes are exactly those at the checkpoint."""
    if not isinstance(checkpoint_commit, str) or not checkpoint_commit:
        raise Unit3Blocked("CHECKPOINT_BINDING_INVALID")
    generator_path = (module_path or Path(__file__)).resolve()
    try:
        repository_root = generator_path.parents[3]
        resolved = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "--verify", f"{checkpoint_commit}^{{commit}}"],
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()
        committed = subprocess.run(
            ["git", "-C", str(repository_root), "show", f"{resolved}:src/genshin_corpus/retrieval/w7_unit3.py"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as exc:
        raise Unit3Blocked("CHECKPOINT_BINDING_INVALID") from exc
    current = generator_path.read_bytes()
    if committed != current:
        raise Unit3Blocked("CHECKPOINT_GENERATOR_MISMATCH")
    return {"checkpoint_commit": resolved, "generator_sha256": _sha256_bytes(current), "checkpoint_generator_sha256": _sha256_bytes(committed)}


def _attempt_log_path(output_root: Path) -> Path:
    return output_root / "metadata" / "unit3_review_pack_extraction_attempts.json"


def _load_extraction_attempts(output_root: Path) -> list[dict[str, Any]]:
    path = _attempt_log_path(output_root)
    if not path.exists():
        return []
    payload = _safe_json_load(path)
    if set(payload) != {"schema_version", "attempts"} or payload.get("schema_version") != UNIT3_SCHEMA_VERSION:
        raise Unit3Blocked("EXTRACTION_ATTEMPT_ACCOUNTING_INVALID")
    values = payload.get("attempts")
    if not isinstance(values, list) or not all(isinstance(value, Mapping) for value in values):
        raise Unit3Blocked("EXTRACTION_ATTEMPT_ACCOUNTING_INVALID")
    records: list[dict[str, Any]] = []
    for number, value in enumerate(values, 1):
        if set(value) != {"attempt_number", "status", "failure_reason", "checkpoint_commit", "generator_sha256"} or value.get("attempt_number") != number or value.get("status") not in ("MECHANICAL_FAILURE", "COMPLETE") or not isinstance(value.get("failure_reason"), (str, type(None))) or not isinstance(value.get("checkpoint_commit"), str) or not _is_sha256(value.get("generator_sha256")):
            raise Unit3Blocked("EXTRACTION_ATTEMPT_ACCOUNTING_INVALID")
        records.append(dict(value))
    if records and records[-1]["status"] == "COMPLETE":
        raise Unit3Blocked("OUTPUT_COLLISION")
    return records


def _write_extraction_attempts(output_root: Path, attempts: list[Mapping[str, Any]]) -> None:
    path = _attempt_log_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(canonical_json_bytes({"schema_version": UNIT3_SCHEMA_VERSION, "attempts": attempts}))
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _attempt_record(number: int, status: str, failure_reason: str | None, binding: Mapping[str, str]) -> dict[str, Any]:
    if status not in ("MECHANICAL_FAILURE", "COMPLETE") or (status == "COMPLETE") != (failure_reason is None):
        raise Unit3Blocked("EXTRACTION_ATTEMPT_ACCOUNTING_INVALID")
    return {"attempt_number": number, "status": status, "failure_reason": failure_reason, "checkpoint_commit": binding["checkpoint_commit"], "generator_sha256": binding["generator_sha256"]}


def _accepted_review_pack_root(output_root: Path) -> Path:
    return output_root / "unit3_review_pack_accepted"


def _staging_review_pack_root(output_root: Path, attempt_number: int) -> Path:
    return output_root / f".unit3-review-pack-staging-{attempt_number:04d}"


def _stage_and_accept_review_pack(
    output_root: Path,
    *,
    attempt_number: int,
    pack: Iterable[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    fault_injector: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write both accepted files under one staging directory, then rename once."""
    accepted_root = _accepted_review_pack_root(output_root)
    if accepted_root.exists():
        raise Unit3Blocked("OUTPUT_COLLISION")
    staging_root = _staging_review_pack_root(output_root, attempt_number)
    if staging_root.exists():
        try:
            shutil.rmtree(staging_root)
        except OSError as exc:
            raise Unit3Blocked("STAGING_CLEANUP_FAILED") from exc
    try:
        artifact = _write_jsonl_gzip(staging_root / "review_pack" / "frozen_48_review_pack.jsonl.gz", pack)
        artifact["path"] = "review_pack/frozen_48_review_pack.jsonl.gz"
        if fault_injector is not None:
            fault_injector("after_pack_write")
        accepted_manifest = {**manifest, "review_pack": artifact}
        manifest_path = staging_root / "metadata" / "review_pack_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(canonical_json_bytes(accepted_manifest))
        if fault_injector is not None:
            fault_injector("after_manifest_write")
        # Validate the staged bytes before the sole acceptance rename.
        staged_rows = _read_jsonl_gzip(staging_root / artifact["path"])
        if len(staged_rows) != artifact["row_count"] or _sha256_path(staging_root / artifact["path"]) != artifact["sha256"]:
            raise Unit3Blocked("STAGED_REVIEW_PACK_INTEGRITY_MISMATCH")
        staged_integrity = validate_review_pack(staged_rows)
        if manifest.get("integrity") is not None and staged_integrity != manifest["integrity"]:
            raise Unit3Blocked("STAGED_REVIEW_PACK_INTEGRITY_MISMATCH")
        if fault_injector is not None:
            fault_injector("before_accept_replace")
        staging_root.replace(accepted_root)
        return artifact, accepted_manifest
    except Exception as exc:
        try:
            if staging_root.exists():
                shutil.rmtree(staging_root)
        except OSError as cleanup_exc:
            raise Unit3Blocked("STAGING_CLEANUP_FAILED") from cleanup_exc
        if isinstance(exc, Unit3Blocked):
            raise
        raise Unit3Blocked("REVIEW_PACK_STAGING_WRITE_FAILED") from exc


def _extract_production_review_pack(unit2_manifest_path: Path, sanitizer_manifest_path: Path, output_root: Path, binding: Mapping[str, str], attempts: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Unit3A-2 entry point.  It never opens the real overlap-index bytes."""
    if _sha256_path(unit2_manifest_path) != EXPECTED_UNIT2_MANIFEST_SHA256 or _sha256_path(sanitizer_manifest_path) != EXPECTED_SANITIZER_MANIFEST_SHA256:
        raise Unit3Blocked("FROZEN_DEPENDENCY_MISMATCH")
    unit2 = _safe_json_load(unit2_manifest_path)
    sanitizer = _safe_json_load(sanitizer_manifest_path)
    if unit2.get("schema_version") != "p04-w7-unit2-runner-v1" or unit2.get("status") != "complete":
        raise Unit3Blocked("UNIT2_MANIFEST_INVALID")
    if sanitizer.get("schema_version") != "p04-w7-sanitizer-manifest-v1" or sanitizer.get("overlap_index", {}).get("sha256") != EXPECTED_OVERLAP_INDEX_SHA256:
        raise Unit3Blocked("SANITIZER_MANIFEST_INVALID")
    artifacts = unit2.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise Unit3Blocked("EXTRACTION_INPUT_INVALID")
    source_root = unit2_manifest_path.parent.parent
    paths = {
        "input_rows": "unit2/input_rows.jsonl.gz",
        "gold_bundles": "gold/anchor_gold_bundles.jsonl.gz",
        "pair_views": "relations/pair_views.jsonl.gz",
        "queues": "queues/provisional_queues.jsonl.gz",
    }
    rows: dict[str, list[Mapping[str, Any]]] = {}
    for name, relative_path in paths.items():
        metadata = artifacts.get(name)
        if not isinstance(metadata, Mapping):
            raise Unit3Blocked("UNIT2_ARTIFACT_METADATA_MISSING")
        rows[name] = _verify_artifact(source_root, relative_path, metadata)
    if _accepted_review_pack_root(output_root).exists():
        raise Unit3Blocked("OUTPUT_COLLISION")
    pack = build_review_pack(rows["queues"], rows["input_rows"], rows["gold_bundles"], rows["pair_views"])
    integrity = validate_review_pack(pack)
    attempt_count = len(attempts) + 1
    manifest = {
        "schema_version": UNIT3_SCHEMA_VERSION,
        "status": "complete",
        "checkpoint_commit": binding["checkpoint_commit"],
        "generator": {"file_path": "src/genshin_corpus/retrieval/w7_unit3.py", "sha256": binding["generator_sha256"], "checkpoint_generator_sha256": binding["checkpoint_generator_sha256"]},
        "dependencies": {
            "unit2_manifest_sha256": EXPECTED_UNIT2_MANIFEST_SHA256,
            "sanitizer_manifest_sha256": EXPECTED_SANITIZER_MANIFEST_SHA256,
            "overlap_index_sha256": EXPECTED_OVERLAP_INDEX_SHA256,
        },
        "input_artifacts": {name: artifacts[name] for name in paths},
        "integrity": integrity,
        "production_extraction_attempt_count": attempt_count,
        "pre_freeze_mechanical_failures": [attempt for attempt in attempts if attempt["status"] == "MECHANICAL_FAILURE"],
        "extraction_attempt": _attempt_record(attempt_count, "COMPLETE", None, binding),
    }
    artifact, accepted_manifest = _stage_and_accept_review_pack(output_root, attempt_number=attempt_count, pack=pack, manifest=manifest)
    return {"manifest": accepted_manifest, "pre_exposure_summary": {**integrity, "review_pack_rows": artifact["row_count"], "review_pack_bytes": artifact["byte_count"]}}


def extract_production_review_pack(unit2_manifest_path: Path, sanitizer_manifest_path: Path, output_root: Path, checkpoint_commit: str) -> dict[str, Any]:
    """Unit3A-2 entry point. It records only pre-freeze mechanical attempts."""
    if _accepted_review_pack_root(output_root).exists():
        raise Unit3Blocked("OUTPUT_COLLISION")
    attempts = _load_extraction_attempts(output_root)
    binding = verify_checkpoint_generator_binding(checkpoint_commit)
    if any(attempt["checkpoint_commit"] != binding["checkpoint_commit"] or attempt["generator_sha256"] != binding["generator_sha256"] for attempt in attempts):
        raise Unit3Blocked("EXTRACTION_ATTEMPT_BINDING_MISMATCH")
    attempt_count = len(attempts) + 1
    try:
        return _extract_production_review_pack(unit2_manifest_path, sanitizer_manifest_path, output_root, binding, attempts)
    except Unit3Blocked as exc:
        _write_extraction_attempts(output_root, [*attempts, _attempt_record(attempt_count, "MECHANICAL_FAILURE", str(exc), binding)])
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract the frozen W7 Unit 3 review pack")
    parser.add_argument("--unit2-manifest", type=Path, required=True)
    parser.add_argument("--sanitizer-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--checkpoint-commit", required=True)
    args = parser.parse_args()
    try:
        result = extract_production_review_pack(args.unit2_manifest, args.sanitizer_manifest, args.output_root, args.checkpoint_commit)
    except Unit3Blocked as exc:
        print(f"UNIT3A2 = BLOCKED: {exc}")
        return 2
    print(f"UNIT3A2 = PASS: {result['pre_exposure_summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
