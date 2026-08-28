"""Small stdlib-only lexical experiment for Phase 04 representation tests."""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from collections.abc import Mapping
from typing import Any

from .representations import document_covers_location


LEXICAL_ANALYZER_VERSION = "phase04-cjk-unigram-ascii-token-0.1"
LEXICAL_BIGRAM_ANALYZER_VERSION = "phase04-cjk-unigram-bigram-ascii-token-0.1"
LEXICAL_SCORER_VERSION = "phase04-bm25-source-address-tiebreak-0.2"
_ASCII_TOKEN = re.compile(r"[a-z0-9]+")


def analyze(text: str) -> list[str]:
    """Use CJK character unigrams plus lower-cased contiguous ASCII tokens."""

    lowered = text.lower()
    tokens = [character for character in lowered if "\u3400" <= character <= "\u9fff"]
    tokens.extend(_ASCII_TOKEN.findall(lowered))
    return tokens


def analyze_with_bigrams(text: str) -> list[str]:
    """Use CJK unigrams plus adjacent CJK bigrams and ASCII tokens."""

    lowered = text.lower()
    cjk = [character for character in lowered if "\u3400" <= character <= "\u9fff"]
    tokens = list(cjk)
    tokens.extend(
        first + second
        for first, second in zip(lowered, lowered[1:])
        if "\u3400" <= first <= "\u9fff" and "\u3400" <= second <= "\u9fff"
    )
    tokens.extend(_ASCII_TOKEN.findall(lowered))
    return tokens


def _build_index(documents: list[Mapping[str, Any]], analyzer=analyze) -> tuple[list[Counter[str]], list[int], float]:
    started = time.perf_counter()
    tokenized = [Counter(analyzer(str(document.get("text", "")))) for document in documents]
    return tokenized, [sum(tokens.values()) for tokens in tokenized], (time.perf_counter() - started) * 1000


def _address_text(value: Any) -> tuple[int, str]:
    """Sort an optional observed text address without a derivative fallback."""

    return (0, "") if value is None else (1, str(value))


def _address_ordinal(value: Any) -> tuple[int, int]:
    """Sort an optional observed ordinal without treating bool as an ordinal."""

    return (1, value) if isinstance(value, int) and not isinstance(value, bool) else (0, -1)


def _ordered_text_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted(str(item) for item in value if isinstance(item, str)))


def _dialogue_address_key(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, Mapping):
        return ((0, -1), (), ())
    edges = value.get("edges")
    ordered_edges: list[tuple[tuple[int, str], tuple[int, str]]] = []
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, Mapping):
                ordered_edges.append((_address_text(edge.get("parent_id")), _address_text(edge.get("child_id"))))
    return (
        _address_ordinal(value.get("group_ordering")),
        _ordered_text_values(value.get("node_source_ids")),
        tuple(sorted(ordered_edges)),
    )


def _coverage_address_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return only the explicit Canonical coverage addresses used for ties."""

    return (
        _address_text(value.get("record_id")),
        _address_ordinal(value.get("section_ordinal")),
        _address_text(value.get("component_observation_key")),
        _address_ordinal(value.get("unit_ordinal")),
        _ordered_text_values(value.get("decoded_json_pointers")),
        _dialogue_address_key(value.get("dialogue")),
    )


def source_coverage_tie_break_key(document: Mapping[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Deterministically order equal scores by observed Canonical coverage only.

    A document with multiple covered source positions is represented by their
    canonical sorted address keys.  When two documents have exactly the same
    complete coverage key, the caller's stable document/artifact occurrence
    order is deliberately preserved; derivative document identity is never a
    ranking signal.
    """

    coverage = document.get("source_coverage")
    values = [_coverage_address_key(item) for item in coverage if isinstance(item, Mapping)] if isinstance(coverage, list) else []
    return tuple(sorted(values))


def _rank(
    documents: list[Mapping[str, Any]],
    tokenized: list[Counter[str]],
    lengths: list[int],
    query: str,
    analyzer=analyze,
) -> tuple[list[Mapping[str, Any]], float]:
    started = time.perf_counter()
    query_terms = Counter(analyzer(query))
    if not query_terms:
        return [], (time.perf_counter() - started) * 1000
    average_length = sum(lengths) / len(lengths) if lengths else 0.0
    document_frequency = Counter()
    for tokens in tokenized:
        document_frequency.update(token for token in query_terms if token in tokens)
    scores: list[tuple[float, tuple[tuple[Any, ...], ...], Mapping[str, Any]]] = []
    total = len(documents)
    for document, tokens, length in zip(documents, tokenized, lengths):
        score = 0.0
        for token, query_frequency in query_terms.items():
            frequency = tokens.get(token, 0)
            if not frequency:
                continue
            idf = math.log(1 + (total - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * (length / average_length if average_length else 0.0))
            score += query_frequency * idf * ((frequency * 2.2) / denominator)
        if score:
            scores.append((score, source_coverage_tie_break_key(document), document))
    scores.sort(key=lambda value: (-value[0], value[1]))
    return [document for _, _, document in scores], (time.perf_counter() - started) * 1000


def _query_metrics(ranked: list[Mapping[str, Any]], query: Mapping[str, Any]) -> dict[str, Any]:
    evidence = query.get("evidence")
    positives = [item for item in evidence if isinstance(item, Mapping) and item.get("relevance") != "hard_negative"] if isinstance(evidence, list) else []
    negatives = [item for item in evidence if isinstance(item, Mapping) and item.get("relevance") == "hard_negative"] if isinstance(evidence, list) else []

    def matches(document: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
        location = item.get("location")
        return isinstance(location, Mapping) and document_covers_location(document, location)

    def first_ranks(items: list[Mapping[str, Any]]) -> dict[str, int]:
        ranks: dict[str, int] = {}
        for index, document in enumerate(ranked, 1):
            for item in items:
                identifier = str(item.get("evidence_id"))
                if identifier not in ranks and matches(document, item):
                    ranks[identifier] = index
        return ranks

    positive_ranks = first_ranks(positives)
    negative_ranks = first_ranks(negatives)
    primary_sets = query.get("primary_sufficient_evidence_sets", [])
    sufficient_rank: int | None = None
    if isinstance(primary_sets, list):
        for group in primary_sets:
            if isinstance(group, list) and group and all(str(item) in positive_ranks for item in group):
                candidate = max(positive_ranks[str(item)] for item in group)
                sufficient_rank = candidate if sufficient_rank is None else min(sufficient_rank, candidate)
    first_rank = min(positive_ranks.values()) if positive_ranks else None
    return {
        "query_id": query.get("query_id"),
        "positive_evidence_ranks": positive_ranks,
        "hard_negative_ranks": negative_ranks,
        "first_positive_rank": first_rank,
        "primary_sufficient_rank": sufficient_rank,
        "top10_document_ids": [document.get("document_id") for document in ranked[:10]],
    }


def evaluate_lexical_arm(
    documents: list[Mapping[str, Any]],
    benchmark: Mapping[str, Any],
    *,
    analyzer=analyze,
    analyzer_version: str = LEXICAL_ANALYZER_VERSION,
) -> dict[str, Any]:
    """Evaluate one representation arm by explicit source coverage only."""

    queries = benchmark.get("queries")
    if not isinstance(queries, list):
        raise ValueError("benchmark queries must be a list")
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    tokenized, lengths, index_build_latency = _build_index(documents, analyzer)
    for query in queries:
        if not isinstance(query, Mapping):
            raise ValueError("benchmark query must be an object")
        ranked, latency = _rank(documents, tokenized, lengths, str(query.get("query", "")), analyzer)
        result = _query_metrics(ranked, query)
        result["latency_ms"] = round(latency, 3)
        results.append(result)
        latencies.append(latency)
    total = len(results)

    def recall(limit: int) -> float:
        return round(sum(result["first_positive_rank"] is not None and result["first_positive_rank"] <= limit for result in results) / total, 4) if total else 0.0

    def sufficient(limit: int) -> float:
        return round(sum(result["primary_sufficient_rank"] is not None and result["primary_sufficient_rank"] <= limit for result in results) / total, 4) if total else 0.0

    return {
        "analyzer_version": analyzer_version,
        "scorer_version": LEXICAL_SCORER_VERSION,
        "document_count": len(documents),
        "query_count": total,
        "metrics": {
            "recall_at_1": recall(1),
            "recall_at_5": recall(5),
            "recall_at_10": recall(10),
            "mrr": round(sum(1 / result["first_positive_rank"] if result["first_positive_rank"] else 0 for result in results) / total, 4) if total else 0.0,
            "primary_sufficient_coverage_at_10": sufficient(10),
            "hard_negative_top10_query_count": sum(any(rank <= 10 for rank in result["hard_negative_ranks"].values()) for result in results),
            "index_build_latency_ms": round(index_build_latency, 3),
            "mean_query_latency_ms": round(sum(latencies) / total, 3) if total else 0.0,
        },
        "queries": results,
    }
