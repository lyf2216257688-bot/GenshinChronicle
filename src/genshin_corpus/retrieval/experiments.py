"""Reproducible offline evaluation for the Phase 04 lexical experiment."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.collector.storage import atomic_write

from .benchmark import load_benchmark, validate_benchmark
from .lexical import (
    LEXICAL_ANALYZER_VERSION,
    LEXICAL_BIGRAM_ANALYZER_VERSION,
    LEXICAL_SCORER_VERSION,
    analyze,
    analyze_with_bigrams,
    evaluate_lexical_arm,
)
from .representations import REPRESENTATION_ARMS, load_retrieval_documents


LEXICAL_EXPERIMENT_VERSION = "phase04-lexical-experiment-0.2"
W3_LEXICAL_EXPERIMENT_VERSION = "phase04-w3-lexical-matrix-0.1"
W5_LEXICAL_EXPERIMENT_VERSION = "phase04-w5-lexical-matrix-0.1"


def _read_manifest(path: Path) -> Mapping[str, Any]:
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"Retrieval manifest must be an object: {path}")
    return value


def _slice_metrics(arm_result: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    results = arm_result.get("queries")
    queries = benchmark.get("queries")
    if not isinstance(results, list) or not isinstance(queries, list):
        return {}
    by_query = {result.get("query_id"): result for result in results if isinstance(result, Mapping)}
    slices: dict[str, list[Mapping[str, Any]]] = {}
    for query in queries:
        if not isinstance(query, Mapping):
            continue
        result = by_query.get(query.get("query_id"))
        values = query.get("slices")
        if not isinstance(result, Mapping) or not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str):
                slices.setdefault(value, []).append(result)
    output: dict[str, Any] = {}
    for name, grouped in sorted(slices.items()):
        total = len(grouped)
        output[name] = {
            "query_count": total,
            "recall_at_10": round(sum(result.get("first_positive_rank") is not None and result["first_positive_rank"] <= 10 for result in grouped) / total, 4),
            "primary_sufficient_coverage_at_10": round(sum(result.get("primary_sufficient_rank") is not None and result["primary_sufficient_rank"] <= 10 for result in grouped) / total, 4),
        }
    return output


def _cohort_metrics(arm_result: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize pre-frozen W5 anchor/new cohorts without reranking."""

    queries = benchmark.get("queries")
    results = arm_result.get("queries")
    anchor_ids = benchmark.get("v0_2_anchor_query_ids")
    if not isinstance(queries, list) or not isinstance(results, list) or not isinstance(anchor_ids, list):
        return {}
    anchors = {item for item in anchor_ids if isinstance(item, str)}
    by_query = {result.get("query_id"): result for result in results if isinstance(result, Mapping)}

    def summary(ids: set[str]) -> dict[str, Any]:
        grouped = [by_query[query_id] for query_id in ids if query_id in by_query]
        total = len(grouped)
        return {
            "query_count": total,
            "recall_at_1": round(sum(result.get("first_positive_rank") is not None and result["first_positive_rank"] <= 1 for result in grouped) / total, 4) if total else 0.0,
            "recall_at_5": round(sum(result.get("first_positive_rank") is not None and result["first_positive_rank"] <= 5 for result in grouped) / total, 4) if total else 0.0,
            "recall_at_10": round(sum(result.get("first_positive_rank") is not None and result["first_positive_rank"] <= 10 for result in grouped) / total, 4) if total else 0.0,
            "mrr": round(sum(1 / result["first_positive_rank"] if result.get("first_positive_rank") else 0 for result in grouped) / total, 4) if total else 0.0,
            "primary_sufficient_coverage_at_10": round(sum(result.get("primary_sufficient_rank") is not None and result["primary_sufficient_rank"] <= 10 for result in grouped) / total, 4) if total else 0.0,
            "hard_negative_top10_query_count": sum(bool(result.get("hard_negative_ranks")) and any(rank <= 10 for rank in result["hard_negative_ranks"].values()) for result in grouped),
        }

    all_ids = {query.get("query_id") for query in queries if isinstance(query, Mapping) and isinstance(query.get("query_id"), str)}
    return {
        "all": summary(all_ids),
        "v0_2_anchor": summary(anchors),
        "w5_new": summary(all_ids - anchors),
    }


def run_lexical_experiment(retrieval_manifest_path: Path, benchmark_path: Path, output_path: Path) -> dict[str, Any]:
    """Evaluate each W2 arm against benchmark evidence coverage, not text equality."""

    retrieval_manifest_path = Path(retrieval_manifest_path)
    retrieval_manifest = _read_manifest(retrieval_manifest_path)
    if retrieval_manifest.get("status") != "complete":
        raise ValueError("Retrieval manifest must be complete")
    benchmark = load_benchmark(Path(benchmark_path))
    validate_benchmark(benchmark)
    results: dict[str, Any] = {}
    for arm in REPRESENTATION_ARMS:
        arm_result = evaluate_lexical_arm(load_retrieval_documents(retrieval_manifest_path, arm), benchmark)
        arm_result["slice_metrics"] = _slice_metrics(arm_result, benchmark)
        results[arm] = arm_result
    output = {
        "schema_version": LEXICAL_EXPERIMENT_VERSION,
        "analyzer_version": LEXICAL_ANALYZER_VERSION,
        "scorer_version": LEXICAL_SCORER_VERSION,
        "canonical_run_id": retrieval_manifest.get("canonical_run_id"),
        "canonical_manifest_sha256": retrieval_manifest.get("canonical_manifest_sha256"),
        "retrieval_manifest_path": str(retrieval_manifest_path),
        "retrieval_manifest_sha256": hashlib.sha256(retrieval_manifest_path.read_bytes()).hexdigest(),
        "benchmark_path": str(benchmark_path),
        "benchmark_sha256": hashlib.sha256(Path(benchmark_path).read_bytes()).hexdigest(),
        "results": results,
        "note": "This is a small lexical representation experiment, not a production retrieval system or technology-selection result.",
    }
    atomic_write(Path(output_path), canonical_json_bytes(output))
    return output


def run_lexical_matrix(retrieval_manifest_path: Path, benchmark_path: Path, output_path: Path) -> dict[str, Any]:
    """Run the W3 A/B analyzer matrix over the unchanged r02 artifacts."""

    retrieval_manifest_path = Path(retrieval_manifest_path)
    retrieval_manifest = _read_manifest(retrieval_manifest_path)
    if retrieval_manifest.get("status") != "complete":
        raise ValueError("Retrieval manifest must be complete")
    benchmark = load_benchmark(Path(benchmark_path))
    validate_benchmark(benchmark)
    analyzers = {
        "A": (LEXICAL_ANALYZER_VERSION, analyze),
        "B": (LEXICAL_BIGRAM_ANALYZER_VERSION, analyze_with_bigrams),
    }
    results: dict[str, Any] = {}
    for arm in REPRESENTATION_ARMS:
        documents = load_retrieval_documents(retrieval_manifest_path, arm)
        results[arm] = {}
        for label, (version, analyzer) in analyzers.items():
            arm_result = evaluate_lexical_arm(documents, benchmark, analyzer=analyzer, analyzer_version=version)
            arm_result["slice_metrics"] = _slice_metrics(arm_result, benchmark)
            arm_result["cohort_metrics"] = _cohort_metrics(arm_result, benchmark)
            results[arm][label] = arm_result
    output = {
        "schema_version": W5_LEXICAL_EXPERIMENT_VERSION if benchmark.get("benchmark_id") == "p04-w5-obc-zh-cn-benchmark-v0.3" else W3_LEXICAL_EXPERIMENT_VERSION,
        "analyzers": {
            "A": LEXICAL_ANALYZER_VERSION,
            "B": LEXICAL_BIGRAM_ANALYZER_VERSION,
        },
        "canonical_run_id": retrieval_manifest.get("canonical_run_id"),
        "canonical_manifest_sha256": retrieval_manifest.get("canonical_manifest_sha256"),
        "retrieval_manifest_path": str(retrieval_manifest_path),
        "retrieval_manifest_sha256": hashlib.sha256(retrieval_manifest_path.read_bytes()).hexdigest(),
        "representation_dependency": retrieval_manifest.get("representation_version"),
        "benchmark_path": str(benchmark_path),
        "benchmark_sha256": hashlib.sha256(Path(benchmark_path).read_bytes()).hexdigest(),
        "scorer_version": LEXICAL_SCORER_VERSION,
        "results": results,
        "note": "Diagnostic A/B analyzer matrix only; no representation, routing, or production technology winner is selected.",
    }
    atomic_write(Path(output_path), canonical_json_bytes(output))
    return output
