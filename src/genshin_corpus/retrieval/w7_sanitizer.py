"""Experiment-local W7 sanitizer for the frozen legacy benchmark.

The generator intentionally exposes benchmark text only in process memory.  Its
outputs contain structural addresses and cryptographic hashes, never query or
evidence text or benchmark role labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.retrieval.benchmark import (
    BenchmarkValidationError,
    _load_canonical_record,
    _resolve_location,
    load_benchmark,
    validate_benchmark,
)
from genshin_corpus.retrieval.representations import (
    RETRIEVAL_REPRESENTATION_VERSION,
    document_covers_location,
    load_retrieval_documents,
)


GENERATOR_ID = "p04-w7-legacy-benchmark-sanitizer"
GENERATOR_VERSION = "1.0.0"
IDENTITY_SCHEMA_VERSION = "p04-w7-v03-identity-only-projection-v1"
OVERLAP_SCHEMA_VERSION = "p04-w7-legacy-query-overlap-index-v1"
MANIFEST_SCHEMA_VERSION = "p04-w7-sanitizer-manifest-v1"
OCCURRENCE_KEY_SCHEMA_VERSION = "w7-occurrence-v1"
FAMILY_KEY_SCHEMA_VERSION = "w7-evidence-family-v1"
NORMALIZATION_VERSION = "c1-nfkc-whitespace-collapse-trim-casefold-v1"

EXPECTED_BENCHMARK_SHA256 = "e6adee5dd7b235af5306e4e1fc6d5a2387789c1021921cffd8e4d8c635890647"
EXPECTED_CANONICAL_MANIFEST_SHA256 = "be2ce30d7cb759a3598b8ac90776abaa01f6db46d6f56603360fcb1e3a66b1e9"
EXPECTED_R02_MANIFEST_SHA256 = "e62cb7ca142f3fddbdb6d109313abf0dfa55b131d45a88c1dee8aac4f6822f56"
EXPECTED_LEAF_SHA256 = "297d413b75734dbbc716e9daf157639103e95eccd3f862855ef59a44bff527b9"
EXPECTED_LEAF_COUNT = 242965
EXPECTED_QUERY_COUNT = 21


class SanitizerBlocked(RuntimeError):
    """Raised for a fail-closed W7 gate."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value, flags=re.UNICODE)
    return value.strip().casefold()


def _overlap_entry(opaque_id: str, query_text: str) -> dict[str, Any]:
    normalized = _normalise(query_text)
    windows = {
        _hash_text(normalized[index : index + 8])
        for index in range(max(0, len(normalized) - 7))
    }
    grams = {
        _hash_text(normalized[index : index + 3])
        for index in range(max(0, len(normalized) - 2))
    }
    return {
        "opaque_legacy_id": opaque_id,
        "normalized_query_sha256": _hash_text(normalized),
        "normalized_continuous_8char_window_sha256": sorted(windows),
        "normalized_unique_char_3gram_sha256": sorted(grams),
    }


def _address(coverage: Mapping[str, Any]) -> dict[str, Any]:
    required = ("record_id", "section_ordinal", "component_observation_key", "unit_ordinal", "lineage")
    if any(key not in coverage for key in required):
        raise SanitizerBlocked("contextualized-leaf coverage address is incomplete")
    if not isinstance(coverage["record_id"], str) or not isinstance(coverage["component_observation_key"], str):
        raise SanitizerBlocked("contextualized-leaf coverage address has invalid identity types")
    if not isinstance(coverage["section_ordinal"], int) or isinstance(coverage["section_ordinal"], bool):
        raise SanitizerBlocked("contextualized-leaf coverage section ordinal is invalid")
    if not isinstance(coverage["unit_ordinal"], int) or isinstance(coverage["unit_ordinal"], bool):
        raise SanitizerBlocked("contextualized-leaf coverage unit ordinal is invalid")
    lineage = coverage["lineage"]
    if not isinstance(lineage, Mapping) or "parsed_json_pointer" not in lineage or "raw_refs" not in lineage:
        raise SanitizerBlocked("contextualized-leaf lineage is incomplete")
    return {key: coverage[key] for key in required}


def _family_address(address: Mapping[str, Any]) -> dict[str, Any]:
    return {key: address[key] for key in ("record_id", "section_ordinal", "component_observation_key")}


def _key(schema: str, address: Mapping[str, Any]) -> str:
    return _hash_text(canonical_json_bytes({"schema": schema, "address": address}).decode("utf-8"))


def _lineage_verification(lineage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "parsed_json_pointer": lineage.get("parsed_json_pointer"),
        "dependency_locator": lineage.get("dependency_locator"),
        "raw_refs": lineage.get("raw_refs"),
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def _resolve_benchmark_identity(benchmark: Mapping[str, Any], canonical_manifest_path: Path) -> None:
    """Reuse benchmark validation and exact location resolver without loading other arms."""

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
    for query in benchmark["queries"]:
        for evidence in query["evidence"]:
            location = evidence["location"]
            record_id = location["record_id"]
            entry = by_id.get(record_id)
            if not isinstance(entry, Mapping):
                raise BenchmarkValidationError("benchmark record does not exist")
            if record_id not in cache:
                path = entry.get("canonical_record_path")
                sha = entry.get("canonical_record_sha256")
                if not isinstance(path, str) or not isinstance(sha, str):
                    raise BenchmarkValidationError("Canonical manifest entry is incomplete")
                cache[record_id] = _load_canonical_record(Path(path), sha)
            _resolve_location(cache[record_id], location, "benchmark evidence")


def generate(
    benchmark_path: Path,
    canonical_manifest_path: Path,
    retrieval_manifest_path: Path,
    leaf_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    benchmark_path = Path(benchmark_path)
    canonical_manifest_path = Path(canonical_manifest_path)
    retrieval_manifest_path = Path(retrieval_manifest_path)
    leaf_path = Path(leaf_path)
    output_root = Path(output_root)

    expected_inputs = (
        (benchmark_path, EXPECTED_BENCHMARK_SHA256),
        (canonical_manifest_path, EXPECTED_CANONICAL_MANIFEST_SHA256),
        (retrieval_manifest_path, EXPECTED_R02_MANIFEST_SHA256),
        (leaf_path, EXPECTED_LEAF_SHA256),
    )
    for path, expected in expected_inputs:
        actual = _sha256(path)
        if actual != expected:
            raise SanitizerBlocked(f"input SHA-256 mismatch: {_relative(path)}")

    benchmark = load_benchmark(benchmark_path)
    queries = benchmark.get("queries")
    if not isinstance(queries, list) or len(queries) != EXPECTED_QUERY_COUNT:
        raise SanitizerBlocked("benchmark query count gate failed")
    try:
        _resolve_benchmark_identity(benchmark, canonical_manifest_path)
    except (BenchmarkValidationError, OSError, json.JSONDecodeError) as exc:
        raise SanitizerBlocked("benchmark and Canonical lineage resolution failed") from exc

    documents = load_retrieval_documents(retrieval_manifest_path, "contextualized_leaf")
    if len(documents) != EXPECTED_LEAF_COUNT:
        raise SanitizerBlocked("contextualized-leaf document count gate failed")
    for document in documents:
        if document.get("representation_version") != RETRIEVAL_REPRESENTATION_VERSION or document.get("arm") != "contextualized_leaf":
            raise SanitizerBlocked("contextualized-leaf representation gate failed")

    occurrences_by_key: dict[str, dict[str, Any]] = {}
    for document in documents:
        coverages = document.get("source_coverage")
        if not isinstance(coverages, list) or len(coverages) != 1 or not isinstance(coverages[0], Mapping):
            raise SanitizerBlocked("contextualized-leaf source coverage contract failed")
        address = _address(coverages[0])
        occurrence_key = _key(OCCURRENCE_KEY_SCHEMA_VERSION, address)
        family_address = _family_address(address)
        family_key = _key(FAMILY_KEY_SCHEMA_VERSION, family_address)
        occurrence = {
            "occurrence_key": occurrence_key,
            "occurrence_address": address,
            "evidence_family_key": family_key,
            "evidence_family_address": family_address,
            "lineage_verification": _lineage_verification(address["lineage"]),
        }
        prior = occurrences_by_key.get(occurrence_key)
        if prior is not None and canonical_json_bytes(prior) != canonical_json_bytes(occurrence):
            raise SanitizerBlocked("occurrence key collision detected")
        occurrences_by_key[occurrence_key] = occurrence

    legacy_entries: list[dict[str, Any]] = []
    exclusion_occurrences: set[str] = set()
    exclusion_families: set[str] = set()
    overlap_entries: list[dict[str, Any]] = []
    eligible_count = 0
    for index, query in enumerate(queries, 1):
        opaque_id = f"legacy-v03-{index:04d}"
        matches: set[str] = set()
        scorable_matches: set[str] = set()
        evidence = query.get("evidence")
        if not isinstance(evidence, list):
            raise SanitizerBlocked("benchmark evidence structure gate failed")
        for document in documents:
            matched_items = [
                isinstance(item, Mapping)
                and isinstance(item.get("location"), Mapping)
                and document_covers_location(document, item["location"])
                for item in evidence
            ]
            if any(matched_items):
                coverage = document["source_coverage"][0]
                occurrence_key = _key(OCCURRENCE_KEY_SCHEMA_VERSION, _address(coverage))
                matches.add(occurrence_key)
                if any(
                    matched
                    and isinstance(item, Mapping)
                    and item.get("relevance") != "hard_negative"
                    for matched, item in zip(matched_items, evidence)
                ):
                    scorable_matches.add(occurrence_key)
        eligible = bool(scorable_matches)
        if not eligible:
            matches.clear()
        if eligible:
            eligible_count += 1
            exclusion_occurrences.update(matches)
            exclusion_families.update(occurrences_by_key[key]["evidence_family_key"] for key in matches)
        legacy_entries.append({
            "opaque_legacy_id": opaque_id,
            "source_query_id_sha256": _hash_text(str(query.get("query_id"))),
            "contextualized_leaf_eligibility": {
                "status": "ELIGIBLE" if eligible else "NA",
                "reason_code": "CONTEXTUALIZED_LEAF_ELIGIBLE" if eligible else "CONTEXTUALIZED_LEAF_NA_NO_SCORABLE_COVERAGE",
            },
            "contextualized_occurrences": [occurrences_by_key[key] for key in sorted(matches)],
        })
        overlap_entries.append(_overlap_entry(opaque_id, query.get("query", "")))

    if eligible_count != 11 or len(queries) - eligible_count != 10:
        raise SanitizerBlocked("contextualized-leaf eligibility gate failed")

    identity = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "source_benchmark": {
            "source_path": _relative(benchmark_path),
            "source_sha256": EXPECTED_BENCHMARK_SHA256,
            "source_schema_version": benchmark.get("schema_version"),
            "source_query_count": len(queries),
        },
        "dependencies": {
            "canonical_manifest_sha256": EXPECTED_CANONICAL_MANIFEST_SHA256,
            "r02_manifest_sha256": EXPECTED_R02_MANIFEST_SHA256,
            "contextualized_leaf_artifact_sha256": EXPECTED_LEAF_SHA256,
            "representation_version": RETRIEVAL_REPRESENTATION_VERSION,
            "arm": "contextualized_leaf",
            "occurrence_key_schema_version": OCCURRENCE_KEY_SCHEMA_VERSION,
            "evidence_family_key_schema_version": FAMILY_KEY_SCHEMA_VERSION,
        },
        "accounting": {
            "legacy_query_count": len(queries),
            "contextualized_leaf_eligible_count": eligible_count,
            "contextualized_leaf_na_count": len(queries) - eligible_count,
            "unique_exclusion_occurrence_count": len(exclusion_occurrences),
            "unique_exclusion_family_count": len(exclusion_families),
        },
        "legacy_entries": legacy_entries,
        "legacy_occurrence_exclusion_keys": sorted(exclusion_occurrences),
        "legacy_evidence_family_exclusion_keys": sorted(exclusion_families),
    }
    overlap = {
        "schema_version": OVERLAP_SCHEMA_VERSION,
        "FOR_UNIT3_ONLY": True,
        "FOR_UNIT2_CANDIDATE_SELECTION": False,
        "normalization_version": NORMALIZATION_VERSION,
        "normalization_metadata": {
            "unicode_form": "NFKC",
            "whitespace": "unicode_whitespace_collapse_to_ascii_space",
            "trim": True,
            "casefold": True,
            "hash_encoding": "UTF-8",
            "codepoint_basis": "Unicode_code_points",
            "continuous_window_length": 8,
            "unique_gram_length": 3,
        },
        "source_benchmark_sha256": EXPECTED_BENCHMARK_SHA256,
        "accounting": {"legacy_query_count": len(queries)},
        "entries": overlap_entries,
    }
    identity_path = output_root / "sanitized_inputs" / "identity_only_projection.json"
    overlap_path = output_root / "sanitized_inputs" / "legacy_query_overlap_index.json"
    atomic_write(identity_path, canonical_json_bytes(identity))
    atomic_write(overlap_path, canonical_json_bytes(overlap))

    generator_path = Path(__file__).resolve()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generator": {
            "id": GENERATOR_ID,
            "version": GENERATOR_VERSION,
            "file_path": _relative(generator_path),
            "sha256": _sha256(generator_path),
        },
        "inputs": {
            "benchmark": {"path": _relative(benchmark_path), "sha256": EXPECTED_BENCHMARK_SHA256},
            "canonical_manifest": {"path": _relative(canonical_manifest_path), "sha256": EXPECTED_CANONICAL_MANIFEST_SHA256},
            "r02_manifest": {"path": _relative(retrieval_manifest_path), "sha256": EXPECTED_R02_MANIFEST_SHA256},
            "contextualized_leaf": {"path": _relative(leaf_path), "sha256": EXPECTED_LEAF_SHA256},
        },
        "benchmark_query_count": len(queries),
        "leaf_document_count": len(documents),
        "eligible_count": eligible_count,
        "na_count": len(queries) - eligible_count,
        "normalization_version": NORMALIZATION_VERSION,
        "occurrence_key_version": OCCURRENCE_KEY_SCHEMA_VERSION,
        "family_key_version": FAMILY_KEY_SCHEMA_VERSION,
        "identity_projection": {
            "path": _relative(identity_path),
            "sha256": _sha256(identity_path),
            "byte_count": identity_path.stat().st_size,
        },
        "overlap_index": {
            "path": _relative(overlap_path),
            "sha256": _sha256(overlap_path),
            "byte_count": overlap_path.stat().st_size,
        },
        "exclusion_occurrence_count": len(exclusion_occurrences),
        "exclusion_family_count": len(exclusion_families),
        "deterministic_serialization": {
            "encoding": "UTF-8",
            "ensure_ascii": False,
            "sort_keys": True,
            "separators": [",", ":"],
            "allow_nan": False,
            "newline": "none",
            "runtime_metadata": "excluded",
        },
        "status": "complete",
        "run_status": "PASS",
    }
    manifest_path = output_root / "metadata" / "sanitizer-manifest.json"
    atomic_write(manifest_path, canonical_json_bytes(manifest))
    return {"identity_path": identity_path, "overlap_path": overlap_path, "manifest_path": manifest_path, **manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the W7 legacy benchmark sanitizer artifacts")
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--retrieval-manifest", type=Path, required=True)
    parser.add_argument("--leaf", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = generate(args.benchmark, args.canonical_manifest, args.retrieval_manifest, args.leaf, args.output_root)
    except SanitizerBlocked as exc:
        print(f"SANITIZER = BLOCKED: {exc}")
        return 2
    print(f"SANITIZER = PASS: {result['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
