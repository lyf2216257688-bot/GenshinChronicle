"""Blind W7 Unit 2 structural candidate runner.

This module is deliberately outcome-free.  Its public runner accepts only the
four immutable inputs and an output root; benchmark text and retrieval results
are not part of the interface.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.representations import (
    RETRIEVAL_DOCUMENT_SCHEMA_VERSION,
    RETRIEVAL_REPRESENTATION_VERSION,
)


EXPECTED_CANONICAL_MANIFEST_SHA256 = "be2ce30d7cb759a3598b8ac90776abaa01f6db46d6f56603360fcb1e3a66b1e9"
EXPECTED_R02_MANIFEST_SHA256 = "e62cb7ca142f3fddbdb6d109313abf0dfa55b131d45a88c1dee8aac4f6822f56"
EXPECTED_LEAF_SHA256 = "297d413b75734dbbc716e9daf157639103e95eccd3f862855ef59a44bff527b9"
EXPECTED_PROJECTION_SHA256 = "be71379f3f026137973322f4afc11602ca76cf8bbfac1014ac850c700e735017"
EXPECTED_LEAF_COUNT = 242965
EXPECTED_PROJECTION_ACCOUNTING = {"legacy_query_count": 21, "contextualized_leaf_eligible_count": 11, "contextualized_leaf_na_count": 10, "unique_exclusion_occurrence_count": 115, "unique_exclusion_family_count": 48}
REPRESENTATION_ARM = "contextualized_leaf"
OCCURRENCE_KEY_SCHEMA_VERSION = "w7-occurrence-v1"
FAMILY_KEY_SCHEMA_VERSION = "w7-evidence-family-v1"
CANDIDATE_KEY_SCHEMA_VERSION = "w7-candidate-v1"
NORMALIZATION_VERSION = "c1-nfkc-whitespace-collapse-trim-casefold-v1"
MAX_RELATED = 12
MAX_GOLD_OCCURRENCES = 16
MAX_PER_ENTITY_PER_QUEUE = 2
QUEUE_ALLOCATIONS = {"semantic": 16, "control": 8, "WR": 12, "HN": 12}
PRIMARY_DISPOSITIONS = (
    "STRUCTURAL_INVALID",
    "TEXT_LENGTH_INELIGIBLE",
    "EXACT_DUPLICATE_REJECTED",
    "NEAR_DUPLICATE_REJECTED",
    "ELIGIBLE_SURVIVOR",
)
RELATION_TYPES = ("same_entity_cross_section_same_component", "same_entity_cross_section_cross_component")
SCIENTIFIC_CONTRACT = {
    "max_human_review_anchors": 48,
    "review_allocations": {"semantic": 16, "control": 8, "WR": 12, "HN": 12},
    "max_review_anchors_per_entity_per_queue": 2,
    "max_related_candidates_per_anchor": 12,
    "max_gold_review_occurrences_per_anchor": 16,
    "final_hard_cap": 24,
    "final_quota": {"semantic_paraphrase": 8, "wrong_role_positive": 6, "dedicated_hn": 6, "control": 4},
    "minimum_valid_rescues": 3,
    "minimum_distinct": {"entity_key": 3, "topic_key": 3, "evidence_family_key": 3},
    "dense_rescue_k": 10,
    "hn_top10_ranks": [1, 10],
    "hn_near_top10_ranks": [11, 15],
    "systematic_hn": {"top10_at_least": "4/6", "rank_le_15_at_least": "5/6", "rule": "OR"},
    "evidence_normalization_version": NORMALIZATION_VERSION,
    "text_length_filter_codepoints": [20, 2000],
    "text_length_filter_semantics": "sampling_reviewability_only",
    "source_truncation_status": "UNKNOWN",
}


class Unit2Blocked(RuntimeError):
    """Raised when a frozen Unit 2 contract gate fails."""


class StructuralInvalid(ValueError):
    """A row-level structural failure with a stable, non-sensitive reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _hash_key(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(canonical_json_bytes(value))


def occurrence_key(address: Mapping[str, Any]) -> str:
    return _hash_key({"schema": OCCURRENCE_KEY_SCHEMA_VERSION, "address": dict(address)})


def family_key(address: Mapping[str, Any]) -> str:
    family_address = {key: address[key] for key in ("record_id", "section_ordinal", "component_observation_key")}
    return _hash_key({"schema": FAMILY_KEY_SCHEMA_VERSION, "address": family_address})


def candidate_key(record: Mapping[str, Any]) -> str:
    """Return a rebuildable candidate id; never use it for structural reps."""
    return _hash_key({
        "schema": CANDIDATE_KEY_SCHEMA_VERSION,
        "representation_version": RETRIEVAL_REPRESENTATION_VERSION,
        "arm": REPRESENTATION_ARM,
        "occurrence_key": record["occurrence_key"],
        "text_sha256": _sha256_bytes(record["text"].encode("utf-8")),
    })


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value, flags=re.UNICODE)
    return value.strip().casefold()


def _grams(value: str) -> frozenset[str]:
    return frozenset(value[index : index + 3] for index in range(max(0, len(value) - 2)))


def _jaccard_at_least_080(left: frozenset[str], right: frozenset[str]) -> tuple[bool, int, int]:
    intersection = len(left & right)
    union = len(left) + len(right) - intersection
    return (union > 0 and 100 * intersection >= 80 * union), intersection, union


def _validate_projection_data(projection: Mapping[str, Any], *, frozen: bool = False) -> tuple[set[str], set[str]]:
    if projection.get("schema_version") != "p04-w7-v03-identity-only-projection-v1":
        raise Unit2Blocked("identity-only projection schema mismatch")
    dependencies = projection.get("dependencies")
    if not isinstance(dependencies, Mapping):
        raise Unit2Blocked("identity-only projection dependencies missing")
    expected_dependencies = {
        "canonical_manifest_sha256": EXPECTED_CANONICAL_MANIFEST_SHA256,
        "r02_manifest_sha256": EXPECTED_R02_MANIFEST_SHA256,
        "contextualized_leaf_artifact_sha256": EXPECTED_LEAF_SHA256,
        "representation_version": RETRIEVAL_REPRESENTATION_VERSION,
        "arm": REPRESENTATION_ARM,
        "occurrence_key_schema_version": OCCURRENCE_KEY_SCHEMA_VERSION,
        "evidence_family_key_schema_version": FAMILY_KEY_SCHEMA_VERSION,
    }
    if any(dependencies.get(key) != value for key, value in expected_dependencies.items()):
        raise Unit2Blocked("identity-only projection dependency mismatch")
    accounting = projection.get("accounting")
    if not isinstance(accounting, Mapping):
        raise Unit2Blocked("identity-only projection accounting missing")
    if frozen and any(accounting.get(key) != value for key, value in EXPECTED_PROJECTION_ACCOUNTING.items()):
        raise Unit2Blocked("identity-only projection accounting mismatch")
    occurrence_values = projection.get("legacy_occurrence_exclusion_keys")
    family_values = projection.get("legacy_evidence_family_exclusion_keys")
    if not isinstance(occurrence_values, list) or not all(isinstance(value, str) for value in occurrence_values):
        raise Unit2Blocked("legacy occurrence exclusions are malformed")
    if not isinstance(family_values, list) or not all(isinstance(value, str) for value in family_values):
        raise Unit2Blocked("legacy family exclusions are malformed")
    return set(occurrence_values), set(family_values)


def _required_raw_ref(raw_ref: Any) -> bool:
    if not isinstance(raw_ref, Mapping):
        return False
    fields = ("source", "locale", "run_id", "content_id", "artifact_kind", "artifact_path", "artifact_sha256", "json_pointer", "embedded_json_pointer", "source_value_sha256")
    if any(field not in raw_ref for field in fields):
        return False
    if any(not isinstance(raw_ref[field], str) or not raw_ref[field] for field in fields[:-1]):
        return False
    return raw_ref["source_value_sha256"] is None or isinstance(raw_ref["source_value_sha256"], str)


def _validate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    required_top = ("schema_version", "document_id", "representation_version", "arm", "text", "source_coverage", "metadata")
    if any(field not in document for field in required_top):
        raise StructuralInvalid("MISSING_RETRIEVAL_DOCUMENT_FIELD")
    if document.get("schema_version") != RETRIEVAL_DOCUMENT_SCHEMA_VERSION or document.get("representation_version") != RETRIEVAL_REPRESENTATION_VERSION or document.get("arm") != REPRESENTATION_ARM:
        raise StructuralInvalid("RETRIEVAL_SCHEMA_REPRESENTATION_ARM_MISMATCH")
    if not isinstance(document.get("document_id"), str) or not isinstance(document.get("text"), str):
        raise StructuralInvalid("INVALID_RETRIEVAL_DOCUMENT_ID_OR_TEXT")
    metadata = document["metadata"]
    if not isinstance(metadata, Mapping) or any(key not in metadata for key in ("record_title", "section_name", "component_id", "unit_kind")):
        raise StructuralInvalid("INVALID_RETRIEVAL_METADATA")
    if metadata.get("unit_kind") != "rich_text" or not isinstance(metadata.get("component_id"), str) or not metadata.get("component_id"):
        raise StructuralInvalid("INVALID_RETRIEVAL_METADATA")
    if not isinstance(metadata.get("record_title"), str) or not isinstance(metadata.get("section_name"), str):
        raise StructuralInvalid("INVALID_RETRIEVAL_METADATA")
    coverage = document["source_coverage"]
    if not isinstance(coverage, list) or len(coverage) != 1 or not isinstance(coverage[0], Mapping):
        raise StructuralInvalid("INVALID_SOURCE_COVERAGE")
    source = coverage[0]
    required_coverage = ("record_id", "section_ordinal", "component_observation_key", "unit_ordinal", "lineage")
    if any(field not in source for field in required_coverage) or not isinstance(source["record_id"], str) or not isinstance(source["component_observation_key"], str):
        raise StructuralInvalid("INVALID_SOURCE_COVERAGE")
    if any(not isinstance(source[field], int) or isinstance(source[field], bool) or source[field] < 0 for field in ("section_ordinal", "unit_ordinal")):
        raise StructuralInvalid("INVALID_OCCURRENCE_ORDINAL")
    lineage = source["lineage"]
    if not isinstance(lineage, Mapping) or any(field not in lineage for field in ("evidence_scope", "parsed_json_pointer", "raw_refs", "dependency_locator")):
        raise StructuralInvalid("INCOMPLETE_LINEAGE")
    raw_refs = lineage["raw_refs"]
    if not isinstance(raw_refs, list) or len(raw_refs) != 1 or not _required_raw_ref(raw_refs[0]):
        raise StructuralInvalid("INVALID_RAW_REF")
    address = {field: source[field] for field in required_coverage}
    raw_ref = raw_refs[0]
    entity = (raw_ref["source"], raw_ref["locale"], raw_ref["content_id"])
    family_address = {field: address[field] for field in ("record_id", "section_ordinal", "component_observation_key")}
    return {
        "document_id": document["document_id"],
        "text": document["text"],
        "metadata": {key: metadata[key] for key in ("record_title", "section_name", "component_id", "unit_kind")},
        "occurrence_address": address,
        "occurrence_key": occurrence_key(address),
        "family_address": family_address,
        "family_key": family_key(address),
        "entity_key": list(entity),
        "topic_key": [address["record_id"], address["section_ordinal"]],
        "structural_role_signature": [metadata["section_name"], metadata["component_id"]],
        "candidate_key": candidate_key({"occurrence_key": occurrence_key(address), "text": document["text"]}),
        "raw_ref": dict(raw_ref),
        "lineage": dict(lineage),
    }


def _exact_and_near(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [record for record in records if record["primary_disposition"] == "PENDING"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in eligible:
        record["normalized_text"] = _normalise(record["text"])
        groups[record["normalized_text"]].append(record)
    for group in groups.values():
        group.sort(key=lambda item: item["occurrence_key"])
        survivor = group[0]
        if len(group) > 1:
            for record in group:
                record["orthogonal_flags"].append("EXACT_TEXT_DUPLICATE_GROUP")
            survivor["exact_duplicate_survivor_key"] = survivor["occurrence_key"]
            for record in group[1:]:
                record["primary_disposition"] = "EXACT_DUPLICATE_REJECTED"
                record["exact_duplicate_survivor_key"] = survivor["occurrence_key"]
                record["reason_code"] = "EXACT_TEXT_DUPLICATE"

    remaining = [record for record in records if record["primary_disposition"] == "PENDING"]
    for record in remaining:
        record["char_3grams"] = _grams(record["normalized_text"])
    frequency: dict[str, int] = defaultdict(int)
    for record in remaining:
        for gram in record["char_3grams"]:
            frequency[gram] += 1
    token_order = lambda gram: (frequency[gram], gram)
    ordered_grams = {record["occurrence_key"]: sorted(record["char_3grams"], key=token_order) for record in remaining}
    postings: dict[str, list[str]] = defaultdict(list)
    for record in remaining:
        grams = ordered_grams[record["occurrence_key"]]
        prefix_length = len(grams) - (4 * len(grams) + 4) // 5 + 1
        for gram in grams[:max(0, prefix_length)]:
            postings[gram].append(record["occurrence_key"])
    by_key = {record["occurrence_key"]: record for record in remaining}
    pair_audit: list[dict[str, Any]] = []
    qualifying: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    seen_pairs: set[tuple[str, str]] = set()
    for record in remaining:
        left_key = record["occurrence_key"]
        left_set = record["char_3grams"]
        for gram in ordered_grams[left_key][: max(0, len(left_set) - (4 * len(left_set) + 4) // 5 + 1)]:
            for right_key in postings[gram]:
                if left_key == right_key:
                    continue
                pair = tuple(sorted((left_key, right_key)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                right = by_key[right_key]
                if 100 * min(len(left_set), len(right["char_3grams"])) < 80 * max(len(left_set), len(right["char_3grams"])):
                    continue
                qualifies, intersection, union = _jaccard_at_least_080(left_set, right["char_3grams"])
                if not qualifies:
                    continue
                pair_audit.append({"left_occurrence_key": pair[0], "right_occurrence_key": pair[1], "intersection_size": intersection, "union_size": union, "threshold": ">=0.80", "result": "QUALIFYING_NEAR_DUPLICATE"})
                qualifying[pair[0]].append((pair[1], intersection, union))
                qualifying[pair[1]].append((pair[0], intersection, union))
    survivors: list[str] = []
    for record in sorted(remaining, key=lambda item: item["occurrence_key"]):
        prior = sorted((key for key in qualifying.get(record["occurrence_key"], ()) if key[0] in survivors), key=lambda item: item[0])
        if prior:
            record["primary_disposition"] = "NEAR_DUPLICATE_REJECTED"
            record["near_duplicate_rejecting_survivor"] = prior[0][0]
            record["reason_code"] = "NEAR_DUPLICATE_JACCARD_080"
        else:
            survivors.append(record["occurrence_key"])
    for record in records:
        record.pop("char_3grams", None)
        record.pop("normalized_text", None)
    return pair_audit


def _write_jsonl_gzip(path: Path, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".w7-", dir=str(path.parent))
    temporary = Path(temporary_name)
    row_count = 0
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                for row in rows:
                    compressed.write(canonical_json_bytes(row) + b"\n")
                    row_count += 1
        body = temporary.read_bytes()
        temporary.replace(path)
        return {"path": path.name, "sha256": _sha256_bytes(body), "byte_count": len(body), "row_count": row_count}
    finally:
        if temporary.exists():
            temporary.unlink()


def _select_queue(rows: list[dict[str, Any]], queue: str, allocation: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: list[dict[str, Any]] = []
    entity_counts: dict[tuple[str, ...], int] = defaultdict(int)
    cap_excluded = 0
    ordered = sorted(rows, key=lambda item: item["anchor_occurrence_key"])
    for row in ordered:
        if len(selected) >= allocation:
            break
        entity = tuple(row["entity_key"])
        if entity_counts[entity] >= MAX_PER_ENTITY_PER_QUEUE:
            cap_excluded += 1
            continue
        selected.append(row)
        entity_counts[entity] += 1
    return selected, {"candidate_eligible": len(rows), "entity_cap_exclusions": cap_excluded, "selected": min(len(selected), allocation), "allocation": allocation}


def execute_documents(documents: Iterable[Mapping[str, Any]], projection: Mapping[str, Any], *, output_root: Path | None = None, dependency_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Execute the deterministic Unit 2 stages over an already-loaded fixture."""
    occurrence_exclusions, family_exclusions = _validate_projection_data(projection, frozen=False)
    records: list[dict[str, Any]] = []
    seen_occurrences: dict[str, dict[str, Any]] = {}
    topic_families: dict[tuple[str, int], str] = {}
    for index, document in enumerate(documents, 1):
        row = {"input_row": index, "primary_disposition": "STRUCTURAL_INVALID", "orthogonal_flags": []}
        try:
            parsed = _validate_document(document)
            prior = seen_occurrences.get(parsed["occurrence_key"])
            if prior is not None and canonical_json_bytes(prior["occurrence_address"]) != canonical_json_bytes(parsed["occurrence_address"]):
                raise Unit2Blocked("occurrence identity collision")
            if prior is not None:
                raise Unit2Blocked("duplicate occurrence address")
            seen_occurrences[parsed["occurrence_key"]] = parsed
            topic = tuple(parsed["topic_key"])
            prior_family = topic_families.setdefault(topic, parsed["family_key"])
            if prior_family != parsed["family_key"]:
                raise Unit2Blocked("topic maps to multiple evidence families")
            row.update(parsed)
            if not 20 <= len(parsed["text"]) <= 2000:
                row["primary_disposition"] = "TEXT_LENGTH_INELIGIBLE"
                row["reason_code"] = "TEXT_LENGTH_OUTSIDE_20_2000_CODEPOINTS"
            else:
                row["primary_disposition"] = "PENDING"
        except Unit2Blocked:
            raise
        except StructuralInvalid as exc:
            row["reason_code"] = exc.reason
        except (TypeError, ValueError, KeyError):
            row["reason_code"] = "INVALID_RETRIEVAL_DOCUMENT"
        records.append(row)
    pair_audit: list[dict[str, Any]] = []
    pair_audit = _exact_and_near(records)
    for row in records:
        if row["primary_disposition"] == "PENDING":
            row["primary_disposition"] = "ELIGIBLE_SURVIVOR"
        if row.get("occurrence_key") in occurrence_exclusions:
            row["orthogonal_flags"].append("LEGACY_OCCURRENCE_EXCLUDED")
        if row.get("family_key") in family_exclusions:
            row["orthogonal_flags"].append("LEGACY_FAMILY_EXCLUDED")
        row["sampling_eligible"] = (
            row["primary_disposition"] == "ELIGIBLE_SURVIVOR"
            and "LEGACY_OCCURRENCE_EXCLUDED" not in row["orthogonal_flags"]
            and "LEGACY_FAMILY_EXCLUDED" not in row["orthogonal_flags"]
        )
    primary_counts = {disposition: sum(row["primary_disposition"] == disposition for row in records) for disposition in PRIMARY_DISPOSITIONS}
    if sum(primary_counts.values()) != len(records):
        raise Unit2Blocked("primary disposition accounting is not mutually exclusive")

    structural = [row for row in records if "occurrence_key" in row]
    families: dict[str, dict[str, Any]] = {}
    for row in structural:
        family = families.setdefault(row["family_key"], {"family_key": row["family_key"], "family_address": row["family_address"], "entity_key": row["entity_key"], "topic_key": row["topic_key"], "structural_role_signature": row["structural_role_signature"], "occurrence_keys": [], "occurrences": [], "eligible_representative_keys": []})
        if any(family[field] != row[field] for field in ("family_address", "entity_key", "topic_key", "structural_role_signature")):
            raise Unit2Blocked("evidence family structural identity inconsistency")
        family["occurrence_keys"].append(row["occurrence_key"])
        family["occurrences"].append(row["occurrence_address"])
        if row["sampling_eligible"]:
            family["eligible_representative_keys"].append(row["occurrence_key"])
    for family in families.values():
        family["representative_occurrence_key"] = min(family["eligible_representative_keys"], default=None)
        family["family_representative_available"] = family["representative_occurrence_key"] is not None
        family.pop("eligible_representative_keys")
        family["occurrence_keys"].sort()
        family["occurrences"].sort(key=canonical_json_bytes)
    families_by_entity: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for family in families.values():
        families_by_entity[tuple(family["entity_key"])].append(family)
    for value in families_by_entity.values():
        value.sort(key=lambda family: family["family_key"])

    rows_by_occurrence = {row["occurrence_key"]: row for row in structural}
    anchors = [rows_by_occurrence[family["representative_occurrence_key"]] for family in families.values() if family["family_representative_available"]]
    relations: list[dict[str, Any]] = []
    gold_bundles: dict[str, dict[str, Any]] = {}
    queue_candidates: dict[str, dict[str, Any]] = {}
    structural_related_by_anchor: dict[str, dict[str, Any]] = {}
    pair_views: list[dict[str, Any]] = []
    occurrence_map = {row["occurrence_key"]: row for row in structural}
    for anchor in sorted(anchors, key=lambda item: item["occurrence_key"]):
        if anchor["occurrence_key"] != families[anchor["family_key"]]["representative_occurrence_key"]:
            raise Unit2Blocked("anchor is not its family representative")
        entity_families = [family for family in families_by_entity[tuple(anchor["entity_key"])] if family["family_key"] != anchor["family_key"]]
        all_related = entity_families
        executable = [family for family in all_related if family["family_representative_available"]]
        structural_related_by_anchor[anchor["occurrence_key"]] = {
            "anchor_occurrence_key": anchor["occurrence_key"],
            "structural_related_family_keys": sorted(family["family_key"] for family in all_related),
            "executable_related_family_keys": sorted(family["family_key"] for family in executable),
            "structural_related_count": len(all_related),
            "executable_related_count": len(executable),
            "relation_status": "RELATED_SCOPE_OVERFLOW" if len(executable) > MAX_RELATED else "VALID",
            "relation_reason": "RELATED_SCOPE_OVERFLOW" if len(executable) > MAX_RELATED else None,
        }
        rel_items = []
        for family in executable:
            relation_type = "same_entity_cross_section_same_component" if family["structural_role_signature"][1] == anchor["structural_role_signature"][1] else "same_entity_cross_section_cross_component"
            rel_items.append((relation_type, family))
        rel_items.sort(key=lambda item: (RELATION_TYPES.index(item[0]), item[1]["topic_key"], item[1]["family_key"], item[1]["representative_occurrence_key"]))
        relation_overflow = len(rel_items) > MAX_RELATED
        rel_views = []
        if not relation_overflow:
            for relation_type, family in rel_items:
                pair_key = _hash_key({"schema": "w7-pair-v1", "relation_type": relation_type, "anchor_family_key": anchor["family_key"], "related_family_key": family["family_key"], "anchor_representative_occurrence_key": families[anchor["family_key"]]["representative_occurrence_key"], "related_representative_occurrence_key": family["representative_occurrence_key"]})
                view = {"pair_key": pair_key, "anchor_occurrence_key": anchor["occurrence_key"], "relation_type": relation_type, "anchor_family_key": anchor["family_key"], "related_family_key": family["family_key"], "related_representative_occurrence_key": family["representative_occurrence_key"], "anchor_gold_bundle_key": anchor["occurrence_key"], "pair_relevant_occurrence_keys": sorted(set(families[anchor["family_key"]]["occurrence_keys"]) | set(family["occurrence_keys"]))}
                rel_views.append(view)
                pair_views.append(view)
                relations.append({"anchor_occurrence_key": anchor["occurrence_key"], "anchor_family_key": anchor["family_key"], "related_family_key": family["family_key"], "relation_type": relation_type, "pair_key": pair_key})

        neighborhood = [families[anchor["family_key"]], *all_related]
        union_keys = sorted({key for family in neighborhood for key in family["occurrence_keys"]})
        gold_status = "VALID" if len(union_keys) <= MAX_GOLD_OCCURRENCES else "GOLD_AMBIGUITY"
        bundle = {"anchor_occurrence_key": anchor["occurrence_key"], "anchor_family_key": anchor["family_key"], "neighborhood_family_keys": sorted(family["family_key"] for family in neighborhood), "occurrence_keys": union_keys, "occurrence_addresses": [occurrence_map[key]["occurrence_address"] for key in union_keys], "gold_review_occurrence_count": len(union_keys), "status": gold_status, "subreason": None if gold_status == "VALID" else "GOLD_REVIEW_SCOPE_OVERFLOW"}
        gold_bundles[anchor["occurrence_key"]] = bundle
        role_contrast = any(family["structural_role_signature"] != anchor["structural_role_signature"] for family in all_related)
        executable_role_contrast_views = [
            view for view in rel_views
            if families[view["related_family_key"]]["structural_role_signature"] != anchor["structural_role_signature"]
        ]
        anchor["capabilities"] = {"general_anchor_capable": True, "cross_family_related_capable": bool(all_related), "related_role_contrast_capable": role_contrast}
        queue_candidates[anchor["occurrence_key"]] = {"anchor_occurrence_key": anchor["occurrence_key"], "anchor_family_key": anchor["family_key"], "entity_key": anchor["entity_key"], "capabilities": anchor["capabilities"], "relation_overflow": relation_overflow, "relation_views": rel_views, "wr_hn_relation_views": executable_role_contrast_views, "anchor_gold_bundle_valid": gold_status == "VALID", "gold_status": gold_status, "gold_subreason": None if gold_status == "VALID" else "GOLD_REVIEW_SCOPE_OVERFLOW", "wr_hn_candidate_eligible": bool(executable_role_contrast_views) and not relation_overflow and gold_status == "VALID", "general_candidate_eligible": gold_status == "VALID"}

    wr_hn = [value for value in queue_candidates.values() if value["wr_hn_candidate_eligible"]]
    wr_hn.sort(key=lambda value: value["anchor_occurrence_key"])
    provisional = {"semantic": [], "control": [], "WR": [], "HN": []}
    for index, value in enumerate(wr_hn):
        provisional["WR" if index % 2 == 0 else "HN"].append(value)
    shared_keys = {value["anchor_occurrence_key"] for value in wr_hn}
    general = [value for value in queue_candidates.values() if value["anchor_occurrence_key"] not in shared_keys and value["anchor_gold_bundle_valid"]]
    general.sort(key=lambda value: value["anchor_occurrence_key"])
    for value in general:
        digest = hashlib.sha256(("w7-general-queue-v1\0" + value["anchor_occurrence_key"]).encode("utf-8")).digest()
        provisional["control" if digest[0] % 2 == 0 else "semantic"].append(value)
    selected: dict[str, list[dict[str, Any]]] = {}
    queue_accounting: dict[str, dict[str, int]] = {}
    for queue, allocation in QUEUE_ALLOCATIONS.items():
        selected[queue], queue_accounting[queue] = _select_queue(provisional[queue], queue, allocation)
        queue_accounting[queue]["relation_exclusions"] = sum(not item["wr_hn_relation_views"] for item in queue_candidates.values()) if queue in ("WR", "HN") else 0
        queue_accounting[queue]["gold_exclusions"] = sum(item["gold_status"] != "VALID" for item in queue_candidates.values())
        queue_accounting[queue]["structural_capable_anchors"] = len(anchors)
        queue_accounting[queue]["sampling_exclusions"] = sum(row["primary_disposition"] != "ELIGIBLE_SURVIVOR" for row in records)
        queue_accounting[queue]["legacy_exclusions"] = sum("LEGACY_OCCURRENCE_EXCLUDED" in row["orthogonal_flags"] or "LEGACY_FAMILY_EXCLUDED" in row["orthogonal_flags"] for row in records)
        queue_accounting[queue]["queue_candidate_eligible"] = queue_accounting[queue]["candidate_eligible"]
        queue_accounting[queue]["queue_selected"] = queue_accounting[queue]["selected"]
        queue_accounting[queue]["status"] = "COMPLETE" if len(selected[queue]) == allocation else "EVIDENCE_INSUFFICIENT"

    gold_histogram: dict[str, int] = {}
    for bundle in gold_bundles.values():
        count_key = str(bundle["gold_review_occurrence_count"])
        gold_histogram[count_key] = gold_histogram.get(count_key, 0) + 1
    gold_histogram = {key: gold_histogram[key] for key in sorted(gold_histogram, key=int)}
    accounting = {
        "input_rows": len(records),
        "structural_valid": sum("occurrence_key" in row for row in records),
        "structural_invalid": sum(row["primary_disposition"] == "STRUCTURAL_INVALID" for row in records),
        "text_length_eligible": sum("occurrence_key" in row and row["primary_disposition"] != "TEXT_LENGTH_INELIGIBLE" for row in records),
        "text_length_ineligible": sum(row["primary_disposition"] == "TEXT_LENGTH_INELIGIBLE" for row in records),
        "exact_duplicate_group_count": len({row.get("exact_duplicate_survivor_key") for row in records if "EXACT_TEXT_DUPLICATE_GROUP" in row["orthogonal_flags"]}),
        "exact_duplicate_rejected": sum(row["primary_disposition"] == "EXACT_DUPLICATE_REJECTED" for row in records),
        "post_exact_duplicate_rows": sum(
            row["primary_disposition"] in ("NEAR_DUPLICATE_REJECTED", "ELIGIBLE_SURVIVOR")
            for row in records
        ),
        "near_duplicate_qualifying_pair_count": len(pair_audit),
        "near_duplicate_rejected": sum(row["primary_disposition"] == "NEAR_DUPLICATE_REJECTED" for row in records),
        "eligible_survivor": sum(row["primary_disposition"] == "ELIGIBLE_SURVIVOR" for row in records),
        "legacy_occurrence_excluded_rows": sum("LEGACY_OCCURRENCE_EXCLUDED" in row["orthogonal_flags"] for row in records),
        "legacy_family_excluded_rows": sum("LEGACY_FAMILY_EXCLUDED" in row["orthogonal_flags"] for row in records),
        "legacy_occurrence_exclusion_key_count": len(occurrence_exclusions),
        "legacy_family_exclusion_key_count": len(family_exclusions),
        "sampling_eligible_survivor": sum(row.get("sampling_eligible", False) for row in records),
        "distinct_entity_count": len({tuple(row["entity_key"]) for row in structural}),
        "distinct_topic_count": len({tuple(row["topic_key"]) for row in structural}),
        "distinct_family_count": len(families),
        "family_representative_available_count": sum(family["family_representative_available"] for family in families.values()),
        "family_representative_unavailable_count": sum(not family["family_representative_available"] for family in families.values()),
        "anchor_count": len(anchors),
        "structural_related_family_membership_count": sum(scope["structural_related_count"] for scope in structural_related_by_anchor.values()),
        "executable_related_family_membership_count": sum(scope["executable_related_count"] for scope in structural_related_by_anchor.values()),
        "relation_pair_count": len(relations),
        "pair_view_count": len(pair_views),
        "related_scope_overflow_anchor_count": sum(scope["relation_status"] == "RELATED_SCOPE_OVERFLOW" for scope in structural_related_by_anchor.values()),
        "valid_relation_scope_anchor_count": sum(scope["relation_status"] == "VALID" for scope in structural_related_by_anchor.values()),
        "wr_hn_executable_contrast_anchor_count": sum(bool(value["wr_hn_relation_views"]) for value in queue_candidates.values()),
        "denominator_notes": {
            "primary_disposition": "mutually_exclusive_input_rows",
            "structural_related_family_membership": "directed_per_anchor",
            "executable_related_family_membership": "directed_per_anchor",
            "gold_review_occurrence": "unique_real_occurrence_address_per_anchor",
            "queue_selection": "provisional_queue_candidates_after_entity_cap",
        },
    }
    gold_accounting = {"anchors_considered": len(anchors), "valid_anchor_bundles": sum(item["status"] == "VALID" for item in gold_bundles.values()), "gold_overflow": sum(item["status"] != "VALID" for item in gold_bundles.values()), "gold_review_occurrence_count_histogram": gold_histogram, "gold_ambiguity_count": sum(item["status"] == "GOLD_AMBIGUITY" for item in gold_bundles.values()), "gold_review_scope_overflow_count": sum(item["subreason"] == "GOLD_REVIEW_SCOPE_OVERFLOW" for item in gold_bundles.values())}
    pair_accounting = {"relation_pairs": len(relations), "pair_views": len(pair_views), "relation_overflow": sum(item["relation_overflow"] for item in queue_candidates.values())}

    result = {
        "records": records,
        "families": list(sorted(families.values(), key=lambda item: item["family_key"])),
        "relations": sorted(relations, key=lambda item: (item["anchor_occurrence_key"], item["pair_key"])),
        "gold_bundles": [gold_bundles[key] for key in sorted(gold_bundles)],
        "queue_candidates": list(sorted(queue_candidates.values(), key=lambda item: item["anchor_occurrence_key"])),
        "structural_related_scopes": [structural_related_by_anchor[key] for key in sorted(structural_related_by_anchor)],
        "pair_views": sorted(pair_views, key=lambda item: item["pair_key"]),
        "queues": {queue: sorted(values, key=lambda item: item["anchor_occurrence_key"]) for queue, values in selected.items()},
        "pair_audit": sorted(pair_audit, key=lambda item: (item["left_occurrence_key"], item["right_occurrence_key"])),
        "primary_disposition_counts": primary_counts,
        "queue_accounting": queue_accounting,
        "accounting": accounting,
        "gold_accounting": gold_accounting,
        "pair_accounting": pair_accounting,
        "scientific_contract": json.loads(json.dumps(SCIENTIFIC_CONTRACT)),
        "dependency_metadata": dict(dependency_metadata or {}),
    }
    if output_root is not None:
        root = Path(output_root)
        artifacts = {
            "input_rows": _write_jsonl_gzip(root / "unit2" / "input_rows.jsonl.gz", result["records"]),
            "family_index": _write_jsonl_gzip(root / "unit2" / "family_index.jsonl.gz", result["families"]),
            "relations": _write_jsonl_gzip(root / "relations" / "relations.jsonl.gz", result["relations"]),
            "pair_views": _write_jsonl_gzip(root / "relations" / "pair_views.jsonl.gz", result["pair_views"]),
            "structural_related_scopes": _write_jsonl_gzip(root / "relations" / "structural_related_scopes.jsonl.gz", result["structural_related_scopes"]),
            "near_duplicate_pairs": _write_jsonl_gzip(root / "unit2" / "near_duplicate_pairs.jsonl.gz", result["pair_audit"]),
            "gold_bundles": _write_jsonl_gzip(root / "gold" / "anchor_gold_bundles.jsonl.gz", result["gold_bundles"]),
            "queue_candidates": _write_jsonl_gzip(root / "queues" / "queue_candidates.jsonl.gz", result["queue_candidates"]),
            "queues": _write_jsonl_gzip(root / "queues" / "provisional_queues.jsonl.gz", [{"queue": queue, "rows": rows, "accounting": result["queue_accounting"][queue]} for queue, rows in result["queues"].items()]),
        }
        module_path = Path(__file__).resolve()
        manifest = {"schema_version": "p04-w7-unit2-runner-v1", "status": "complete", "generator": {"id": "p04-w7-unit2-runner", "version": "1.0.0", "file_path": "src/genshin_corpus/retrieval/w7_unit2.py", "sha256": _sha256_path(module_path)}, "dependencies": result["dependency_metadata"], "primary_disposition_counts": primary_counts, "accounting": result["accounting"], "queue_accounting": result["queue_accounting"], "gold_accounting": result["gold_accounting"], "pair_accounting": result["pair_accounting"], "scientific_contract": result["scientific_contract"], "artifacts": artifacts}
        metadata = root / "metadata"
        metadata.mkdir(parents=True, exist_ok=True)
        (metadata / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        result["manifest"] = manifest
    return result


def _load_leaf(path: Path) -> list[Mapping[str, Any]]:
    documents: list[Mapping[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise Unit2Blocked(f"leaf JSON invalid at line {line_number}") from exc
            if not isinstance(value, Mapping):
                raise Unit2Blocked(f"leaf row is not an object at line {line_number}")
            documents.append(value)
    return documents


def run_unit2(canonical_manifest_path: Path, r02_manifest_path: Path, leaf_path: Path, projection_path: Path, output_root: Path) -> dict[str, Any]:
    """Run the production-gated runner.  This function is not called by tests."""
    paths = ((Path(canonical_manifest_path), EXPECTED_CANONICAL_MANIFEST_SHA256), (Path(r02_manifest_path), EXPECTED_R02_MANIFEST_SHA256), (Path(leaf_path), EXPECTED_LEAF_SHA256), (Path(projection_path), EXPECTED_PROJECTION_SHA256))
    for path, expected in paths:
        if _sha256_path(path) != expected:
            raise Unit2Blocked(f"frozen dependency SHA-256 mismatch: {path}")
    try:
        r02 = json.loads(Path(r02_manifest_path).read_text(encoding="utf-8"))
        projection = json.loads(Path(projection_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unit2Blocked("frozen dependency JSON cannot be read") from exc
    artifact = r02.get("artifacts", {}).get(REPRESENTATION_ARM, {}) if isinstance(r02, Mapping) else {}
    if artifact.get("sha256") != EXPECTED_LEAF_SHA256 or artifact.get("document_count") != EXPECTED_LEAF_COUNT:
        raise Unit2Blocked("r02 contextualized-leaf metadata mismatch")
    _validate_projection_data(projection, frozen=True)
    documents = _load_leaf(Path(leaf_path))
    if len(documents) != EXPECTED_LEAF_COUNT:
        raise Unit2Blocked("contextualized-leaf count mismatch")
    result = execute_documents(documents, projection, output_root=Path(output_root), dependency_metadata={"canonical_manifest_sha256": EXPECTED_CANONICAL_MANIFEST_SHA256, "r02_manifest_sha256": EXPECTED_R02_MANIFEST_SHA256, "contextualized_leaf_artifact_sha256": EXPECTED_LEAF_SHA256, "identity_projection_sha256": EXPECTED_PROJECTION_SHA256, "representation_version": RETRIEVAL_REPRESENTATION_VERSION, "arm": REPRESENTATION_ARM})
    if sum(result["primary_disposition_counts"].values()) != EXPECTED_LEAF_COUNT:
        raise Unit2Blocked("primary disposition count does not equal frozen leaf count")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the frozen W7 Unit 2 runner")
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--r02-manifest", type=Path, required=True)
    parser.add_argument("--contextualized-leaf", type=Path, required=True)
    parser.add_argument("--identity-only-projection", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_unit2(args.canonical_manifest, args.r02_manifest, args.contextualized_leaf, args.identity_only_projection, args.output_root)
    except Unit2Blocked as exc:
        print(f"UNIT2 = BLOCKED: {exc}")
        return 2
    print(f"UNIT2 = PASS: {result['manifest']['artifacts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
