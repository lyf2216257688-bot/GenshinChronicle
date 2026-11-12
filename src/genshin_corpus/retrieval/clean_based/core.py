"""Deterministic candidate fusion and source-range conservative organization."""
from dataclasses import asdict, dataclass
import math

from ...evidence_io import digest, require

CHANNELS = {"S0": {"bm25f", "dense"}, "S1": {"sparse", "dense"},
            "S2": {"bm25f", "sparse", "dense"}}


@dataclass(frozen=True)
class QuerySpec:
    question: str
    question_id: str = "operator"
    query_id: str = "original"
    retrieval_text: str | None = None
    kind: str = "original"
    round: int = 0
    parent_query_ids: tuple[str, ...] = ()

    def __post_init__(self):
        require(isinstance(self.question, str) and bool(self.question.strip()), "Empty question")
        require(isinstance(self.question_id, str) and bool(self.question_id)
                and isinstance(self.query_id, str) and bool(self.query_id), "Missing query identity")
        require(self.kind == "original" and type(self.round) is int and self.round == 0
                and not self.parent_query_ids, "Query enhancement is not implemented in V1")
        require(self.retrieval_text is None or self.retrieval_text == self.question,
                "Original-only V1 does not rewrite the question")

    @property
    def text(self):
        return self.question if self.retrieval_text is None else self.retrieval_text


def validate_profile(profile):
    require(profile.get("combination") in CHANNELS, "Unknown retrieval combination")
    require(profile.get("fusion") in {"f0", "f1_max", "f1_rrf"}, "Unknown fusion strategy")
    for name in ("depth", "candidate_limit", "rrf_k"):
        require(type(profile.get(name)) is int and profile[name] > 0, "Invalid " + name)
    require(type(profile.get("structural_cleanup")) is bool, "Invalid structural cleanup selector")
    weights = profile.get("channel_weights", {})
    require(set(weights) == CHANNELS[profile["combination"]], "Channel weights differ from combination")
    require(all(type(w) in {int, float} and math.isfinite(w) and w > 0 for w in weights.values()),
            "Invalid channel weight")


def _ref_key(ref):
    return tuple(ref[key] for key in ("observation_id", "block_id", "value_pointer"))


def _qualifier(part):
    # Unknown metadata is included, never discarded to obtain an equivalence.
    return {k: v for k, v in part.items() if k not in {"text", "clean_ref"}}


def covered_by(target, earlier):
    """Proof of containment by ONE earlier candidate, never semantic similarity."""
    if any(target[k] != earlier[k] for k in ("observation_id", "parent_id", "context")):
        return False
    if target.get("alias_clean_refs", []) != earlier.get("alias_clean_refs", []):
        return False
    if not {digest(v) for v in target["context_sources"]} <= {digest(v) for v in earlier["context_sources"]}:
        return False
    if not target["parts"] or any(p.get("condition_status") == "unresolved" for p in target["parts"]):
        return False
    for part in target["parts"]:
        ref = part["clean_ref"]
        if not any(_ref_key(ref) == _ref_key(other["clean_ref"])
                   and other["clean_ref"]["char_start"] <= ref["char_start"]
                   and ref["char_end"] <= other["clean_ref"]["char_end"]
                   and _qualifier(part) == _qualifier(other) for other in earlier["parts"]):
            return False
    return True


def _intersection_size(a, b):
    # Union intersections so overlapping parts do not double count characters.
    ranges = {}
    for left in a["clean_refs"]:
        for right in b["clean_refs"]:
            if _ref_key(left) == _ref_key(right):
                start, end = max(left["char_start"], right["char_start"]), min(left["char_end"], right["char_end"])
                if start < end:
                    ranges.setdefault(_ref_key(left), []).append((start, end))
    total = 0
    for intervals in ranges.values():
        end = -1
        for start, stop in sorted(intervals):
            total += max(0, stop - max(start, end))
            end = max(end, stop)
    return total


def fuse_candidates(query, hits, documents, profile, *, input_bundle_id):
    """documents maps chunk_id to verified {input_row, row, chunk}."""
    validate_profile(profile)
    weights = profile["channel_weights"]
    weight_sum = sum(weights.values())
    unique, positions, representations = {}, {}, {}
    for hit in hits:
        channel, granularity = hit["channel"], hit["granularity"]
        require(channel in weights and granularity in {"micro", "macro"}, "Foreign hit route")
        require(hit["query_id"] == query.query_id and hit["question_id"] == query.question_id,
                "Foreign hit query")
        require(type(hit["rank"]) is int and 0 < hit["rank"] <= profile["depth"], "Invalid hit rank")
        require(type(hit["raw_score"]) in {int, float} and math.isfinite(hit["raw_score"]), "Invalid raw score")
        require(isinstance(hit["representation_id"], str) and bool(hit["representation_id"]), "Unbound hit")
        route = (channel, granularity)
        require(representations.setdefault(route, hit["representation_id"]) == hit["representation_id"],
                "Mixed representations in one route")
        key = (*route, hit["chunk_id"])
        require(key not in unique or unique[key] == hit, "Conflicting duplicate hit")
        unique[key] = hit
        position = (*route, hit["rank"])
        require(positions.setdefault(position, hit["chunk_id"]) == hit["chunk_id"], "Duplicate route rank")
        doc = documents[hit["chunk_id"]]
        require(type(hit["input_row"]) is int and hit["input_row"] == doc["input_row"]
                and granularity in doc["row"]["granularities"], "Hit/source mapping mismatch")
    for route in representations:
        ranks = sorted(h["rank"] for h in unique.values() if (h["channel"], h["granularity"]) == route)
        require(ranks == list(range(1, len(ranks) + 1)), "Non-contiguous route ranks")
    grouped = {}
    for hit in unique.values():
        grouped.setdefault(hit["chunk_id"], []).append(hit)
    k = profile["rrf_k"]
    route_scores, route_ranks = {}, {}
    for granularity in ("micro", "macro"):
        scores = {cid: sum(weights[h["channel"]] / weight_sum / (k + h["rank"])
                           for h in values if h["granularity"] == granularity)
                  for cid, values in grouped.items() if any(h["granularity"] == granularity for h in values)}
        route_scores[granularity] = scores
        route_ranks[granularity] = {cid: n for n, cid in enumerate(
            sorted(scores, key=lambda cid: (-scores[cid], documents[cid]["input_row"])), 1)}
    scored = []
    for cid, values in grouped.items():
        ranks = {g: route_ranks[g][cid] for g in route_ranks if cid in route_ranks[g]}
        if profile["fusion"] == "f0":
            score = sum(weights[h["channel"]] / weight_sum / (k + h["rank"]) for h in values)
        elif profile["fusion"] == "f1_max":
            score = max(1 / (k + rank) for rank in ranks.values())
        else:
            score = sum(1 / (k + rank) for rank in ranks.values())
        doc = documents[cid]
        scored.append({"chunk_id": cid, "input_row": doc["input_row"], "fusion_score": score,
                       "granularity_ranks": ranks,
                       "granularity_scores": {g: route_scores[g][cid] for g in ranks},
                       "hits": sorted(values, key=lambda h: (h["channel"], h["granularity"])),
                       "source": doc["row"], "chunk": doc["chunk"], "overlaps": []})
    scored.sort(key=lambda row: (-row["fusion_score"], row["input_row"]))
    main, deferred = [], []
    for rank, candidate in enumerate(scored, 1):
        candidate["fusion_rank"] = rank
        for previous in scored[:rank - 1]:
            overlap = _intersection_size(candidate["chunk"], previous["chunk"])
            if overlap:
                candidate["overlaps"].append({"chunk_id": previous["chunk_id"], "body_codepoints": overlap})
        covering = next((previous["chunk_id"] for previous in main
                         if profile["structural_cleanup"] and covered_by(candidate["chunk"], previous["chunk"])), None)
        candidate["deferred_by"] = covering
        (deferred if covering else main).append(candidate)
    organized = main + deferred
    selected = organized[:profile["candidate_limit"]]
    selected_ids = {row["chunk_id"] for row in selected}
    for rank, row in enumerate(organized, 1):
        row["organized_rank"] = rank
        row["disposition"] = ("selected_deferred_refill" if row["deferred_by"] else "selected") \
            if row["chunk_id"] in selected_ids else "outside_candidate_limit"
    return {"version": "gc-clean-candidates-1", "status": "complete", "query": asdict(query),
            "input_bundle_id": input_bundle_id, "profile": profile, "profile_id": digest(profile),
            "counts": {"raw_hits": len(hits), "distinct_hits": len(unique), "unique_chunks": len(scored),
                       "deferred": len(deferred), "selected": len(selected)},
            "candidates": selected, "raw_hits": list(unique.values()),
            "dispositions": [{k: row[k] for k in ("chunk_id", "fusion_rank", "organized_rank", "disposition", "deferred_by")}
                             for row in organized]}
