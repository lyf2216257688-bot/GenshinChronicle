"""Provider-neutral candidate reranking contract and deterministic projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any, Protocol


class RerankingError(ValueError):
    pass


@dataclass(frozen=True)
class RerankCandidate:
    unit_id: str
    text: str
    original_rank: int


@dataclass(frozen=True)
class RerankRequest:
    question_id: str
    query: str
    candidates: tuple[RerankCandidate, ...]

    def __post_init__(self) -> None:
        if not self.question_id or not isinstance(self.question_id, str):
            raise RerankingError("question_id must be a non-empty string")
        if not isinstance(self.query, str) or not self.query:
            raise RerankingError("query must be a non-empty string")
        if not self.candidates:
            raise RerankingError("rerank request requires candidates")
        ranks = [row.original_rank for row in self.candidates]
        ids = [row.unit_id for row in self.candidates]
        if any(not isinstance(row.unit_id, str) or not row.unit_id for row in self.candidates):
            raise RerankingError("candidate unit_id must be non-empty")
        if len(set(ids)) != len(ids) or ranks != list(range(1, len(ranks) + 1)):
            raise RerankingError("candidates must be uniquely and contiguously ranked")


@dataclass(frozen=True)
class RerankScore:
    unit_id: str
    original_rank: int
    rerank_score: float
    rerank_rank: int


class Reranker(Protocol):
    def rerank(self, request: RerankRequest) -> Sequence[RerankScore]:
        """Score every request candidate exactly once."""


def stable_rank_scores(request: RerankRequest, scores: Sequence[RerankScore]) -> tuple[RerankScore, ...]:
    """Validate provider identity coverage and apply score-desc/original-rank order."""
    if len(scores) != len(request.candidates):
        raise RerankingError("reranker response count does not match candidate count")
    expected = {row.unit_id: row.original_rank for row in request.candidates}
    seen: set[str] = set()
    normalized: list[RerankScore] = []
    for row in scores:
        if not isinstance(row, RerankScore) or row.unit_id in seen or row.unit_id not in expected:
            raise RerankingError("reranker response has unknown or duplicate candidate identity")
        if row.original_rank != expected[row.unit_id] or not math.isfinite(float(row.rerank_score)):
            raise RerankingError("reranker response has invalid candidate binding or score")
        seen.add(row.unit_id)
        normalized.append(row)
    if seen != set(expected):
        raise RerankingError("reranker response omitted candidate identity")
    ordered = sorted(normalized, key=lambda row: (-float(row.rerank_score), row.original_rank, row.unit_id))
    return tuple(
        RerankScore(row.unit_id, row.original_rank, float(row.rerank_score), index)
        for index, row in enumerate(ordered, 1)
    )


def project_ranked_candidates(
    original_candidates: Sequence[Mapping[str, Any]],
    ranked_scores: Sequence[RerankScore],
) -> list[dict[str, Any]]:
    """Copy Hybrid rows while retaining both original and reranked provenance."""
    by_id = {str(row["unit_id"]): row for row in original_candidates}
    output: list[dict[str, Any]] = []
    for score in ranked_scores:
        source = by_id.get(score.unit_id)
        if source is None:
            raise RerankingError("reranked candidate is absent from original Hybrid pool")
        row = dict(source)
        retrieval = dict(row.get("retrieval", {}))
        retrieval["original_hybrid_rank"] = score.original_rank
        retrieval["rerank_rank"] = score.rerank_rank
        retrieval["rerank_score"] = score.rerank_score
        row["rank"] = score.rerank_rank
        row["original_hybrid_rank"] = score.original_rank
        row["rerank_rank"] = score.rerank_rank
        row["rerank_score"] = score.rerank_score
        row["retrieval"] = retrieval
        output.append(row)
    return output
