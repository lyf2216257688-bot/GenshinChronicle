"""Offline G6 diagnostic; no production selector, inference or model-call path.

Keep the entire S0 candidate list byte-for-byte as values, add the exact-scored
subset of raw-window Sparse-only candidates, then reuse selected BGE ordering.
Cache availability is a study limitation, never a semantic relevance filter.
"""
from copy import deepcopy

from ..evidence_io import digest, require
from ..generation.clean_evidence import assemble, validate_citations
from ..retrieval.clean_based.rerank_core import ranked_candidates
from ..retrieval.clean_based.rerank_handoff import PROFILES
from ..retrieval.clean_based.rerank_v2 import rank_v2

VERSION = "gc-g6-s0-sparse-study-1"


def supplement(base, donor, base_scores, donor_scores, selection):
    """Return an explicit study pool and jointly reranked set; never refusion."""
    # These calls validate exact query/text, complete score coverage, binding,
    # token audit and source contracts before any cached scalar is reused.
    base_ranked = rank_v2(base, base_scores, selection)
    rank_v2(donor, donor_scores, selection)
    require(base["query"]["combination"] == "S0" and donor["query"]["combination"] == "S2",
            "Study requires the original S0 and frozen S2 donor")
    require({k: v for k, v in base["query"].items() if k != "combination"} ==
            {k: v for k, v in donor["query"].items() if k != "combination"}, "Different scoring query")
    require(base["input_bundle_id"] == donor["input_bundle_id"], "Different input bundle")
    raw0 = {h["chunk_id"] for h in base["raw_hits"]}
    donor0 = {h["chunk_id"] for h in donor["raw_hits"] if h["channel"] in {"dense", "bm25f"}}
    require(raw0 == donor0 and all(h["channel"] in {"dense", "bm25f"} for h in base["raw_hits"]),
            "Different fixed Dense/BM25F windows")
    require(sorted(digest(h) for h in base["raw_hits"]) == sorted(digest(h) for h in donor["raw_hits"]
            if h["channel"] in {"dense", "bm25f"}), "Raw window ranks/scores/mapping changed")
    sparse_hits = [h for h in donor["raw_hits"] if h["channel"] == "sparse"]
    unique = {h["chunk_id"] for h in sparse_hits} - raw0
    cached = {c["chunk_id"]: c for c in donor["candidates"]}
    base_ids = {c["chunk_id"] for c in base["candidates"]}
    require(not unique & base_ids, "Sparse-only identity already in S0")
    added = sorted(unique & cached.keys(), key=lambda cid: (
        min(h["rank"] for h in sparse_hits if h["chunk_id"] == cid), cid))
    for c in base["candidates"]:
        if c["chunk_id"] in cached:
            other = cached[c["chunk_id"]]
            require(c["source"] == other["source"] and c["chunk"] == other["chunk"] and
                    base_scores["scores"][c["chunk_id"]] == donor_scores["scores"][c["chunk_id"]],
                    "Conflicting exact source/score cache")
    candidates = deepcopy(base["candidates"])
    for cid in added:
        c = cached[cid]
        # No S2 fusion_score/rank/weight is copied into the supplement's ranking
        # input. Its immutable donor digest retains that historical provenance.
        candidates.append({k: deepcopy(c[k]) for k in ("chunk_id", "input_row", "source", "chunk")} | {
            "hits": deepcopy([h for h in sparse_hits if h["chunk_id"] == cid]),
            "supplement_origin": {"donor_candidate_digest": digest(c), "mode": "raw_sparse_only_exact_cache"}})
    require(len(candidates) <= 200, "Finite study candidate bound exceeded")
    audit = {"version": VERSION, "scope": "offline_cached_subset_not_deployable_selector",
             "baseline_digest": digest(base), "donor_digest": digest(donor),
             "baseline_scores_digest": digest(base_scores), "donor_scores_digest": digest(donor_scores),
             "baseline_candidates": len(base["candidates"]), "added_ids": added,
             "raw_sparse_only_ids": sorted(unique), "unscored_unexamined_ids": sorted(unique - set(added)),
             "policy": "all_exact_cached_raw_sparse_only_no_semantic_filter_no_refusion_no_top100_recut",
             "tie_break": "original_S0_pool_then_min_sparse_route_rank_then_chunk_id",
             "remote_calls": 0, "new_inference_pairs": 0}
    pool = {"version": "gc-g6-supplement-pool-1", "status": base["status"],
            "query": deepcopy(base["query"]), "input_bundle_id": base["input_bundle_id"],
            "study": audit, "candidates": candidates}
    scores = {c["chunk_id"]: (base_scores if c["chunk_id"] in base_ids else donor_scores)["scores"][c["chunk_id"]]
              for c in candidates}
    ranked = ranked_candidates(pool, scores, **PROFILES["bge_structural"])
    ranked.update(version="gc-clean-ranked-candidates-2", reranking_run={
        "status": "complete", "profile": "bge_structural", "pair_budget": 2048,
        "score_binding": deepcopy(selection["score_binding"]), "inference_pairs": 0,
        "exact_reused_pairs": len(candidates), "mmr_applied": False, "study_only": True})
    require(pool["candidates"][:len(base["candidates"])] == base["candidates"], "S0 was modified")
    return pool, ranked, base_ranked


def protected_assembly(ranked, receipts, baseline_ranked, baseline_packet, *, context, budget):
    """Test residual admission, retaining baseline fragments and joint BGE order.

    This is a deterministic admission diagnostic, not a semantic evidence picker.
    It protects only what that exact baseline Packet already delivered. It cannot
    rescue a baseline budget omission or an unread low-ranked supplement.
    """
    validate_citations(baseline_packet, list(baseline_packet["citations"]))
    wanted = set(baseline_packet["read_receipt_ids"])
    baseline_reads = [r for r in receipts if r["receipt_id"] in wanted]
    # A complete replay proves the protected membership belongs to this context,
    # budget, baseline ordering and exact receipts, rather than an oracle list.
    replay = assemble([baseline_ranked], baseline_reads, context=context, budget=budget,
                      selection_ids=baseline_packet["selection_ids"])
    require(replay == baseline_packet, "Baseline Packet/context/reads/budget differ")
    base_ids = {c["chunk_id"] for c in baseline_ranked["candidates"]}
    require(base_ids <= {c["chunk_id"] for c in ranked["candidates"]}, "Missing S0 candidate")
    protected = {e["fragment_id"] for e in baseline_packet["evidence"]}
    selected = set(protected)
    packet = assemble([ranked], receipts, selection_ids=sorted(selected), context=context, budget=budget)
    require(protected <= {e["fragment_id"] for e in packet["evidence"]}, "Baseline cannot be retained")
    decisions = []
    for c in ranked["candidates"]:
        cid = c["chunk_id"]
        if cid in base_ids:
            continue
        fids = {f["fragment_id"] for r in receipts if r["chunk_id"] == cid for f in r["fragments"]}
        if not fids:
            decisions.append({"chunk_id": cid, "decision": "not_read"})
            continue
        trial = assemble([ranked], receipts, selection_ids=sorted(selected | fids), context=context, budget=budget)
        delivered = {e["fragment_id"] for e in trial["evidence"]}
        if not protected <= delivered:
            reason = "would_displace_protected_fragment"
        elif not selected <= delivered:
            reason = "would_displace_prior_supplement"
        elif not fids <= delivered:
            reason = next(d["disposition"] for d in trial["dispositions"] if d["chunk_id"] == cid)
        else:
            selected |= fids
            packet = trial
            reason = "included_from_residual_budget"
        decisions.append({"chunk_id": cid, "decision": reason})
    require(protected <= {e["fragment_id"] for e in packet["evidence"]}, "Protected evidence loss")
    require(selected <= {e["fragment_id"] for e in packet["evidence"]}, "Accepted supplement loss")
    return packet, {"version": VERSION, "policy": "protect_exact_baseline_packet_then_sparse_in_joint_bge_order",
                    "baseline_packet_id": baseline_packet["packet_id"], "protected_fragment_ids": sorted(protected),
                    "protected_content_retained": True, "citation_numbers_may_change": True,
                    "nonprotected_S0": "not_admitted_by_this_residual_diagnostic", "supplements": decisions}


def envelope_assembly(ranked, receipts, *, context, budget, check, baseline_ranked=None, baseline_packet=None):
    """Replay G5's existing 2048-step admission shrink, without any dispatch.

    This is separate from the fixed-budget Assembly experiment: every effective
    cap and guard outcome is recorded. Protection must survive as exact content
    even if the envelope forces a smaller cap; it never silently shrinks S0.
    """
    current = deepcopy(budget)
    trials = []
    protect = baseline_packet is not None
    require(protect == (baseline_ranked is not None), "Incomplete baseline protection")
    protected = {e["fragment_id"] for e in baseline_packet["evidence"]} if protect else set()
    for _ in range(2 + len(ranked["candidates"])):
        admission = None
        if protect:
            by_id = {r["receipt_id"]: r for r in receipts}
            rs = [by_id[rid] for rid in baseline_packet["read_receipt_ids"]]
            bp = assemble([baseline_ranked], rs, context=context, budget=current,
                          selection_ids=baseline_packet["selection_ids"])
            require(protected <= {e["fragment_id"] for e in bp["evidence"]}, "Envelope cannot preserve baseline")
            packet, admission = protected_assembly(ranked, receipts, baseline_ranked, bp,
                                                   context=context, budget=current)
        else:
            packet = assemble([ranked], receipts, context=context, budget=current)
        try:
            checked = check(packet)
        except ValueError as exc:
            if str(exc) != "WHOLE_MESSAGE_BUDGET_EXCEEDED":
                raise
            trials.append({"admission_cap": current["context_tokens"], "envelope": str(exc)})
            current["context_tokens"] -= 2048
            continue
        trials.append({"admission_cap": current["context_tokens"], "envelope": "PASS"})
        require(protected <= {e["fragment_id"] for e in packet["evidence"]}, "Protected envelope content loss")
        return packet, {"policy": "existing_G5_envelope_guard_2048_step_admission_reduction",
                        "requested_budget": budget, "effective_budget": current, "trials": trials,
                        "check_result": checked, "protection": admission,
                        "original_baseline_packet_id": baseline_packet["packet_id"] if protect else None}
    raise ValueError("WHOLE_MESSAGE_BUDGET_EXCEEDED")
