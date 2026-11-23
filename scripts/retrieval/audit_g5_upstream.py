"""Compare rank guards and fixed-set local BGE query wording on sealed G7 inputs."""

import argparse
from pathlib import Path
import sys
import time

from evaluate_g5_evidence import archived_g7, g7_index, new_output
from genshin_corpus.evidence_io import digest, read, require, seal, sha, write
from genshin_corpus.indexing.benchmark_io import offline_only
from genshin_corpus.retrieval.clean_based.rerank_core import ranked_candidates
from genshin_corpus.retrieval.clean_based.rerank_v2 import scoring_input
from genshin_corpus.retrieval.clean_based.runtime_v2 import JsonWorker


# Used only for evaluation, never in the query, BGE input, or ranking rules.
WITNESSES = {
    "Q068": {"first_person_bridge": ("505631", "我只是「明晨之镜」的一个念头"),
             "called_paradox": ("505631", "那时还被称作「悖谬」"),
             "current_profile_distraction": ("505631", "【称号】\n轰隆雷鸣波")},
    "Q043": {"earliest_identity": ("2415", "流放者——这是埃洛伊最先拥有的身份"),
             "later_profile_distraction": ("2415", "【称号】\n异界的救世主")},
    "Q046": {"direct_speaker_identity": ("7480", "卡莉露：不…抱歉瞒了大家这么久")},
    "Q060": {"direct_note": ("505871", "他说他叫做"),
             "attributed_disguise": ("506072", "诺奇特梨服装")},
}
EVIDENCE_QUERY_PREFIX = ("匹配直接支持问题所问身份、称谓或来历的原文。核对称谓对应的对象、时期、相互关系及其发言者或记载出处。\n"
                         "原问题：")
TOP_K = 6


def ranks(candidates):
    return {row["chunk_id"]: rank for rank, row in enumerate(candidates, 1)}


def witness_ranks(question_id, candidates, positions):
    result = {}
    for label, (content_id, phrase) in WITNESSES.get(question_id, {}).items():
        matched = [candidate for candidate in candidates
                   if str(candidate["source"]["source_item"]["content_id"]) == content_id
                   and phrase in candidate["chunk"]["retrieval_text"]]
        require(matched, "Frozen witness did not match: " + question_id + "/" + label)
        result[label] = [{"chunk_id": row["chunk_id"], "input_row": row["input_row"],
                          "ranks": {key: ranking[row["chunk_id"]] for key, ranking in positions.items()}}
                         for row in matched]
    return result


def merge_ranks(candidates):
    """Keep all candidates and the existing structurally deferred tail."""
    ordered = sorted(candidates, key=lambda row: (
        row["reranking"]["covered_by"] is not None,
        -(1 / (60 + row["organized_rank"]) + 1 / (60 + row["reranking"]["final_rank"])),
        row["reranking"]["final_rank"],
    ))
    return ranks(ordered)


def split_prefix(candidates):
    """Keep three BGE results and admit three previously unseen recall results."""
    head = candidates[:TOP_K // 2]
    original = {row["chunk_id"] for row in head}
    promoted = [row for row in sorted(candidates, key=lambda row: row["organized_rank"])
                if row["chunk_id"] not in original and row["reranking"]["covered_by"] is None][:TOP_K // 2]
    selected = original | {row["chunk_id"] for row in promoted}
    return ranks(head + promoted + [row for row in candidates if row["chunk_id"] not in selected])


def original_candidates(group):
    candidates = [{key: val for key, val in row.items() if key != "reranking"}
                  for row in sorted(group["candidates"], key=lambda row: row["organized_rank"])]
    value = {key: val for key, val in group.items() if key not in {"reranking_run", "policy", "policy_id", "candidates"}}
    value.update(version="gc-clean-candidates-2", candidates=candidates)
    require(digest(value) == group["reranking_run"]["candidate_set_sha256"], "Original scoring candidates changed")
    return value


def audit(repo, output, *, rescore=False):
    new_output(repo, output)
    index = g7_index(repo)
    cases, rescore_sets = {}, {}
    for question_id, item in index.items():
        archive, result = archived_g7(repo, item)
        request = archive.artifact("g5-request-0")
        state = archive.load(request["data_ref"])
        group = archive.load(state["groups"][0])
        candidates = group["candidates"]
        baseline = ranks(candidates)
        positions = {"recall": {row["chunk_id"]: row["organized_rank"] for row in candidates}, "bge_structural": baseline,
                     "bge_relevance": {row["chunk_id"]: row["reranking"]["relevance_rank"] for row in candidates},
                     "equal_rank_fusion_k60": merge_ranks(candidates), "bge3_then_recall3": split_prefix(candidates)}
        require(all(set(order) == set(baseline) and sorted(order.values()) == list(range(1, len(baseline) + 1))
                    for order in positions.values()), "Candidate order lost identities")
        packet = archive.load(result["sources_ref"])["packet"]
        initial_by_fragment = {row["fragment_id"]: receipt["chunk_id"] for ref in state["reads"].values()
                               for receipt in [archive.load(ref)] for row in receipt["fragments"]}
        initially_available_citations = {initial_by_fragment[packet["citations"][c["citation_id"]]["fragment_id"]]
                                         for c in result["citations"]
                                         if packet["citations"][c["citation_id"]]["fragment_id"] in initial_by_fragment}
        keep = {name: sum(position[key] <= TOP_K for key in initially_available_citations) for name, position in positions.items()}
        existing = {row["chunk_id"] for row in candidates[:TOP_K]}
        cases[question_id] = {"source_group": state["groups"][0], "initially_available_historical_cited_chunks": sorted(initially_available_citations),
                              "historical_cited_top6_count": keep,
                              "baseline_top6_overlap": {name: sum(order[key] <= TOP_K for key in existing) for name, order in positions.items()},
                              "witnesses": witness_ranks(question_id, candidates, positions)}
        if question_id in WITNESSES and rescore:
            rescore_sets[question_id] = (original_candidates(group), cases[question_id]["witnesses"], baseline)
    comparisons = {}
    if rescore_sets:
        selection_path = repo / "config/reranking-selected-v1.json"
        selection = read(selection_path)
        write(output / "local-arm.json", {"cases": list(rescore_sets), "preface": EVIDENCE_QUERY_PREFIX, "pair_budget": 2048,
                                          "selection_sha256": sha(selection_path), "same_text_and_candidates": True,
                                          "input_candidate_digests": {case: digest(v[0]) for case, v in rescore_sets.items()}})
        started = time.perf_counter()
        with JsonWorker(repo, selection["python"], "genshin_corpus.retrieval.clean_based.rerank_worker_v2",
                        ["--repo", str(repo), "--scratch", str(output / "scratch")], output / "bge.stderr.log", timeout=180) as worker:
            require(worker.ready["binding"] == selection["score_binding"], "Foreign BGE worker")
            ready_seconds = time.perf_counter() - started
            for question_id, (candidates, witnesses, baseline) in rescore_sets.items():
                # This is only a scorer counterfactual. Keep the original query,
                # candidate set and lineage exactly as the runtime sealed them.
                prompt = EVIDENCE_QUERY_PREFIX + candidates["query"]["rerank_text"]
                variant = {**scoring_input(candidates, selection), "query": {"question": prompt}}
                path = output / (question_id + "-score-input.json")
                write(path, variant)
                score = worker.request({"action": "score", "input": str(path), "budget": 2048})
                require(score["status"] == "complete" and score["binding"] == selection["score_binding"]
                        and score["candidate_set_sha256"] == digest(variant), "Foreign or incomplete local score")
                require([row["chunk_id"] for row in score["audit"]] == [row["chunk_id"] for row in candidates["candidates"]]
                        and all(type(row["tokens"]) is int and 0 < row["tokens"] <= 2048 for row in score["audit"]),
                        "Incomplete token audit")
                ranked = ranked_candidates(candidates, score["scores"])
                write(output / (question_id + "-score.json"), score)
                positions = ranks(ranked["candidates"])
                comparison = {"new_query": prompt, "ready_seconds_for_shared_worker": ready_seconds,
                              "elapsed_seconds": score["elapsed_seconds"], "load_seconds": score["load_seconds"],
                              "pair_count": len(positions), "resources": score["resources"],
                              "baseline_top6_overlap": sum(positions[key] <= TOP_K for key, rank in baseline.items() if rank <= TOP_K),
                              "witnesses": {label: [{**witness, "local_bge_rank": positions[witness["chunk_id"]]} for witness in rows]
                                            for label, rows in witnesses.items()}}
                comparisons[question_id] = comparison
    report = {"schema": "gc-g5-upstream-audit-1", "script_sha256": sha(__file__), "g7_index_sha256": sha(repo / "data/generation/g7-70q-baseline-20261117/index.jsonl"),
              "top_k": TOP_K, "historical_cites_proxy_only": True, "paid_calls": 0, "new_local_bge": comparisons,
              "cases": cases}
    write(output / "report.json", report)
    seal(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rescore", action="store_true", help="Run one bounded offline local BGE worker for four cases")
    args = parser.parse_args()
    with offline_only():
        report = audit(Path.cwd(), args.out.resolve(), rescore=args.rescore)
    print(f"Sealed {len(report['cases'])} replay cases and {len(report['new_local_bge'])} local GPU comparisons.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
