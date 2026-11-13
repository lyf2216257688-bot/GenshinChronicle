"""Candidate-only ordering. No discovery, source rewriting or Packet assembly."""
from copy import deepcopy
import math

from ...evidence_io import digest, require
from .core import covered_by, _intersection_size


def ranked_candidates(candidate_set, scores, *, structural=True, mmr_lambda=1., vectors=None,
                      local_depth=30, protected_top=3, protected_gap=.1):
    require(type(structural) is bool, "Invalid structural selector")
    require(type(local_depth) is int and local_depth > 0 and type(protected_top) is int
            and protected_top >= 0, "Invalid local/protection depth")
    require(math.isfinite(mmr_lambda) and 0 < mmr_lambda <= 1
            and math.isfinite(protected_gap) and 0 <= protected_gap <= 1, "Invalid MMR policy")
    candidates = deepcopy(candidate_set["candidates"])
    ids = [c["chunk_id"] for c in candidates]
    require(ids and len(ids) == len(set(ids)) and set(scores) == set(ids), "Score identity coverage differs")
    require(all(type(v) in {float, int} and math.isfinite(v) for v in scores.values()), "Nonfinite score")
    original = {cid: n for n, cid in enumerate(ids, 1)}
    candidates.sort(key=lambda c: (-scores[c["chunk_id"]], original[c["chunk_id"]]))
    main, deferred = [], []
    for rank, row in enumerate(candidates, 1):
        cid = row["chunk_id"]
        row["reranking"] = {"recall_rank": original[cid], "logit": scores[cid], "relevance_rank": rank,
                            "covered_by": None, "overlaps": [], "mmr": None}
        for previous in main:
            overlap = _intersection_size(row["chunk"], previous["chunk"])
            if overlap:
                row["reranking"]["overlaps"].append({"chunk_id": previous["chunk_id"],
                                                        "body_codepoints": overlap})
        covering = next((c for c in main if structural and covered_by(row["chunk"], c["chunk"])), None)
        if covering:
            row["reranking"]["covered_by"] = covering["chunk_id"]
            deferred.append(row)
        else:
            main.append(row)
    pool = [c for c in main if c["reranking"]["relevance_rank"] <= local_depth]
    if mmr_lambda < 1 and pool:
        import numpy as np
        require(vectors is not None and set(ids) <= set(vectors), "Missing candidate vectors")
        matrix = np.asarray([vectors[cid] for cid in ids], dtype=np.float32)
        require(matrix.ndim == 2 and matrix.shape[1] > 0 and bool(np.isfinite(matrix).all()), "Invalid vectors")
        norms = np.linalg.norm(matrix, axis=1)
        require(bool((norms > 0).all()), "Zero candidate vector")
        matrix = matrix / norms[:, None]
        positions = {cid: n for n, cid in enumerate(ids)}
        high = max(scores[c["chunk_id"]] for c in pool)
        low = min(scores[c["chunk_id"]] for c in pool)
        span = high - low
        selected, remaining = [], []
        for n, row in enumerate(pool):
            gap = (high - scores[row["chunk_id"]]) / span if span else 0.
            reason = "equal_scores" if not span else "top_baseline" if n < protected_top else "score_band" if gap <= protected_gap else None
            row["reranking"]["mmr"] = {"relative_gap": gap, "relevance": 1 - gap,
                                         "protected": reason, "redundancy": 0., "reference": None}
            (selected if reason else remaining).append(row)
        while remaining:
            choices = []
            for row in remaining:
                cid = row["chunk_id"]
                sims = [(max(0., min(1., float(matrix[positions[cid]] @ matrix[positions[p["chunk_id"]]]))),
                         p["chunk_id"]) for p in selected]
                redundancy, reference = max(sims, key=lambda x: x[0]) if sims else (0., None)
                value = mmr_lambda * row["reranking"]["mmr"]["relevance"] - (1 - mmr_lambda) * redundancy
                choices.append((value, -row["reranking"]["relevance_rank"], row, redundancy, reference))
            value, _, chosen, redundancy, reference = max(choices, key=lambda x: x[:2])
            chosen["reranking"]["mmr"].update(objective=value, redundancy=redundancy, reference=reference)
            selected.append(chosen)
            remaining.remove(chosen)
        pool_ids = {c["chunk_id"] for c in pool}
        main = selected + [c for c in main if c["chunk_id"] not in pool_ids]
    ordered = main + deferred
    for rank, row in enumerate(ordered, 1):
        row["reranking"]["final_rank"] = rank
    policy = {"structural": structural, "mmr_lambda": mmr_lambda, "local_depth": local_depth,
              "protected_top": protected_top, "protected_gap": protected_gap,
              "relevance_scale": "local_logit_minmax", "similarity": "nonnegative_cosine"}
    require(len(ordered) == len(ids) and {c["chunk_id"] for c in ordered} == set(ids), "Candidate loss")
    return {**{k: deepcopy(v) for k, v in candidate_set.items() if k != "candidates"},
            "version": "gc-clean-ranked-candidates-1", "policy": policy, "policy_id": digest(policy),
            "candidates": ordered}


def candidate_vectors(manifest_path, candidates):
    """Read only requested rows; verify each used shard and Chunk mapping."""
    from pathlib import Path
    import numpy as np
    from ...evidence_io import inside, read, sha
    from ...indexing.benchmark_io import lines, verify_receipt
    path = Path(manifest_path)
    manifest = read(path)
    require(manifest["status"] == "complete", "Incomplete Dense index")
    wanted = {c["input_row"]: c["chunk_id"] for c in candidates}
    require(len(wanted) == len(candidates), "Repeated Dense input row")
    found = {}
    for summary in manifest["shards"]:
        folder = inside(path.parent, summary["folder"])
        require(sha(folder / "complete.json") == summary["receipt_sha256"], "Dense receipt changed")
        rows = list(lines(folder / "rows.jsonl"))
        selected = [(i, r) for i, r in enumerate(rows) if r["input_row"] in wanted]
        if not selected:
            continue
        verify_receipt(folder, manifest["binding"])
        vectors = np.load(folder / "vectors.npy", mmap_mode="r", allow_pickle=False)
        for i, row in selected:
            require(row["chunk_id"] == wanted[row["input_row"]] and row["chunk_id"] not in found,
                    "Dense mapping differs")
            found[row["chunk_id"]] = vectors[i].copy()
        del vectors
    require(set(found) == set(wanted.values()), "Missing Dense mapping")
    return found
