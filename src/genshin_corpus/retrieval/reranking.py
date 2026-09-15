"""Provider-neutral candidate reranking contract and deterministic projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any, Protocol

from genshin_corpus.canonical.fingerprints import sha256_json


RERANKER_PROJECTION_VERSION = "phase04-rag-reranker-projection-0.1"
RANK_FUSION_VERSION = "phase04-rag-rank-fusion-0.1"


class RerankingError(ValueError):
    pass


@dataclass(frozen=True)
class RankFusionConfig:
    """Rank-only fusion controls; these are execution operating parameters."""

    hybrid_weight: float = 0.35
    rerank_weight: float = 0.65
    denominator: int = 60
    version: str = RANK_FUSION_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise RerankingError("fusion version must be a non-empty string")
        if not math.isfinite(float(self.hybrid_weight)) or not math.isfinite(float(self.rerank_weight)):
            raise RerankingError("fusion weights must be finite")
        if self.hybrid_weight < 0 or self.rerank_weight < 0 or self.hybrid_weight + self.rerank_weight <= 0:
            raise RerankingError("fusion weights must be non-negative and not both zero")
        if not isinstance(self.denominator, int) or isinstance(self.denominator, bool) or self.denominator <= 0:
            raise RerankingError("fusion denominator must be a positive integer")

    @property
    def identity(self) -> str:
        return sha256_json({
            "version": self.version,
            "hybrid_weight": float(self.hybrid_weight),
            "rerank_weight": float(self.rerank_weight),
            "denominator": self.denominator,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "hybrid_weight": float(self.hybrid_weight),
            "rerank_weight": float(self.rerank_weight),
            "denominator": self.denominator,
            "identity": self.identity,
        }


def project_reranker_text(
    fields: Mapping[str, Any],
    *,
    max_chars: int = 6000,
) -> tuple[str, dict[str, Any]]:
    """Build a deterministic, provider-visible RU projection.

    The projection is a ranking input only.  It carries no source identity or
    provenance authority; those remain on the candidate row and RU snapshot.
    """

    if not isinstance(fields, Mapping):
        raise RerankingError("reranker projection fields must be an object")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise RerankingError("reranker projection max_chars must be a positive integer")
    labels = (
        ("record_title", "Title"),
        ("section_name", "Section"),
        ("speaker", "Speaker"),
        ("retrieval_visible_text", "Text"),
    )
    lines: list[str] = []
    for key, label in labels:
        value = fields.get(key, "")
        if not isinstance(value, str):
            raise RerankingError(f"reranker projection field {key} must be a string")
        value = value.strip()
        if value:
            lines.append(f"{label}: {value}")
    if not fields.get("retrieval_visible_text", "").strip():
        raise RerankingError("reranker projection requires retrieval_visible_text")
    raw = "\n".join(lines)
    projected = raw[:max_chars]
    return projected, {
        "version": RERANKER_PROJECTION_VERSION,
        "max_chars": max_chars,
        "source_char_count": len(raw),
        "projected_char_count": len(projected),
        "truncated": len(projected) < len(raw),
        "token_estimate": None,
        "identity": sha256_json({
            "version": RERANKER_PROJECTION_VERSION,
            "max_chars": max_chars,
        }),
    }


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


def fuse_ranked_candidates(
    original_candidates: Sequence[Mapping[str, Any]],
    reranked_candidates: Sequence[Mapping[str, Any]],
    *,
    config: RankFusionConfig | None = None,
) -> list[dict[str, Any]]:
    """Fuse Hybrid and actual rerank ranks with deterministic RRF-style math."""

    config = config or RankFusionConfig()
    original_by_id: dict[str, Mapping[str, Any]] = {}
    for expected_rank, row in enumerate(original_candidates, 1):
        if not isinstance(row, Mapping) or not isinstance(row.get("unit_id"), str) or not row["unit_id"]:
            raise RerankingError("Hybrid candidate identity is invalid")
        if row.get("rank") != expected_rank or row["unit_id"] in original_by_id:
            raise RerankingError("Hybrid candidates must have unique contiguous ranks")
        original_by_id[row["unit_id"]] = row
    if not reranked_candidates:
        raise RerankingError("rank fusion requires reranked candidates")
    seen: set[str] = set()
    scored: list[tuple[float, int, str, Mapping[str, Any]]] = []
    for expected_rank, row in enumerate(reranked_candidates, 1):
        if not isinstance(row, Mapping) or not isinstance(row.get("unit_id"), str):
            raise RerankingError("reranked candidate identity is invalid")
        unit_id = row["unit_id"]
        if unit_id in seen or unit_id not in original_by_id:
            raise RerankingError("reranked candidate is unknown or duplicated")
        hybrid_rank = row.get("original_hybrid_rank")
        rerank_rank = row.get("rerank_rank")
        if hybrid_rank != original_by_id[unit_id].get("rank") or rerank_rank != expected_rank:
            raise RerankingError("rank fusion candidate lineage is inconsistent")
        if not isinstance(hybrid_rank, int) or hybrid_rank <= 0 or not isinstance(rerank_rank, int) or rerank_rank <= 0:
            raise RerankingError("rank fusion ranks must be positive integers")
        value = (
            float(config.hybrid_weight) / (config.denominator + hybrid_rank)
            + float(config.rerank_weight) / (config.denominator + rerank_rank)
        )
        if not math.isfinite(value):
            raise RerankingError("rank fusion score is non-finite")
        seen.add(unit_id)
        scored.append((value, hybrid_rank, unit_id, row))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    output: list[dict[str, Any]] = []
    for fusion_rank, (value, hybrid_rank, unit_id, source) in enumerate(scored, 1):
        row = dict(source)
        retrieval = dict(row.get("retrieval", {}))
        retrieval["fusion"] = {
            "method": "weighted_reciprocal_rank",
            "version": config.version,
            "config_identity": config.identity,
            "original_hybrid_rank": hybrid_rank,
            "rerank_rank": source["rerank_rank"],
        }
        row["rank"] = fusion_rank
        row["fusion_rank"] = fusion_rank
        row["fusion_score"] = value
        row["original_hybrid_rank"] = hybrid_rank
        row["retrieval"] = retrieval
        output.append(row)
    return output
