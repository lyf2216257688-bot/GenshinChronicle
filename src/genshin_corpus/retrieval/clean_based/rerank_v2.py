"""Explicit rerank-text scoring projection and complete selected V2 ordering."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import platform

from ...evidence_io import digest, require, sha
from .query_v2 import ResearchQuery
from .rerank_core import ranked_candidates
from .rerank_handoff import PROFILES, validate_selection


def validate_candidates(value, selection):
    validate_selection(selection)
    require(value["version"] == "gc-clean-candidates-2" and value["status"] in {"complete", "empty"},
            "Expected complete V2 candidates")
    query = ResearchQuery.parse(value["query"])
    require(value["query_digest"] == digest(query.value()), "Query digest changed")
    require(value["input_bundle_id"] == selection["input_bundle_id"], "Foreign input bundle")
    profile = value["profile"]
    require(profile["combination"] == "S0" and profile["fusion"] == "f1_max" and profile["structural_cleanup"] is True,
            "Selected recall profile changed")
    ids = [c["chunk_id"] for c in value["candidates"]]
    require(len(set(ids)) == len(ids) and bool(ids) == (value["status"] == "complete"), "Invalid candidate status/identity")
    for c in value["candidates"]:
        source, chunk = c["source"], c["chunk"]
        require({"clean_record", "chunk_locator", "parent_locator", "conditions", "source_context",
                 "input_snapshot_id", "granularities"} <= set(source), "Missing source contract")
        require(all(source[k] == chunk[k] for k in ("chunk_id", "observation_id", "parent_id", "clean_refs"))
                and c["chunk_id"] == chunk["chunk_id"] and type(c["input_row"]) is int and c["input_row"] >= 0,
                "Source/candidate identity differs")
        require(source["dense_text"] == chunk["retrieval_text"] and bool(source["dense_text"])
                and source["dense_text_sha256"] == sha256(source["dense_text"].encode()).hexdigest(), "Scoring text/hash changed")
        require(c["hits"] and all(h["question_id"] == query.turn_id and h["query_id"] == query.query_id for h in c["hits"]),
                "Foreign recall query")
    return query


def scoring_input(value, selection):
    query = validate_candidates(value, selection)
    return {"version": "gc-clean-scoring-input-2", "query": {"question": query.rerank_text},
            "research_query": query.value(), "candidate_set_digest": digest(value),
            "candidates": deepcopy(value["candidates"]), "input_bundle_id": value["input_bundle_id"]}


def rank_v2(value, score, selection, *, pair_budget=2048):
    query = validate_candidates(value, selection)
    require(type(pair_budget) is int and pair_budget == 2048, "New length point requires explicit finite validation")
    if not value["candidates"]:
        require(score is None, "Empty input must skip scoring")
        return {**deepcopy(value), "version": "gc-clean-ranked-candidates-2",
                "reranking_run": {"status": "empty", "inference_pairs": 0, "mmr_applied": False}}
    projected = scoring_input(value, selection)
    require(score is not None and score.get("status", "complete") == "complete"
            and score["binding"] == selection["score_binding"]
            and score["candidate_set_sha256"] == digest(projected), "Foreign/incomplete scoring result")
    ids = [c["chunk_id"] for c in value["candidates"]]
    require([a["chunk_id"] for a in score["audit"]] == ids
            and all(type(a["tokens"]) is int and 0 < a["tokens"] <= pair_budget for a in score["audit"]),
            "Incomplete/reordered/overlength token audit")
    result = ranked_candidates(value, score["scores"], **PROFILES["bge_structural"])
    result.update(version="gc-clean-ranked-candidates-2", reranking_run={
        "status": "complete", "selection_id": digest(selection), "profile": "bge_structural",
        "candidate_set_sha256": digest(value), "scoring_input_digest": digest(projected),
        "rerank_text": query.rerank_text, "score_result_digest": digest(score),
        "scoring": deepcopy({k: v for k, v in score.items() if k != "scores"}),
        "pair_budget": pair_budget, "mmr_applied": False, "ordering_python": platform.python_version(),
        "adapter_code": sha(__file__)})
    return result
