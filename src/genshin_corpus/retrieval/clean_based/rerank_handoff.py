"""Selected candidate interface and saved-score handoff, without model execution."""
import argparse
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import platform
import json

from ...evidence_io import digest, inside, read, require, seal, sha, verify, write
from .core import QuerySpec, validate_profile
from .rerank_core import ranked_candidates

SELECTION_VERSION = "gc-clean-reranking-selection-1"
HANDOFF_VERSION = "gc-clean-reranking-handoff-1"
DEFAULT_SELECTION = "config/reranking-selected-v1.json"
PROFILES = {"bge_structural": {"structural": True, "mmr_lambda": 1.0},
            "bge_relevance": {"structural": False, "mmr_lambda": 1.0}}


def ordering_code():
    folder = Path(__file__).parent
    return {**{name: sha(folder / name) for name in ("rerank_handoff.py", "rerank_core.py", "core.py")},
            "evidence_io.py": sha(folder.parent.parent / "evidence_io.py")}


def validate_selection(selection):
    require(selection["version"] == SELECTION_VERSION
            and selection["model"] == "BAAI/bge-reranker-v2-m3", "Unsupported selected Reranker")
    require(selection["profiles"] == PROFILES and selection["default_profile"] == "bge_structural"
            and selection["fallback_profile"] == "bge_relevance"
            and selection["automatic_fallback"] is False, "Unsupported ordering selection or automatic fallback")
    require(all(type(p["structural"]) is bool and type(p["mmr_lambda"]) in {int, float}
                for p in selection["profiles"].values()), "Invalid policy field types")
    binding = selection["score_binding"]
    require(binding["model"] == "bge" and binding["dtype"] == "float16" and binding["device"] == "cuda"
            and binding["attention"] == "eager" and binding["batch_size"] == 1
            and binding["pairing"] == "query_full_retrieval_text" and binding["truncation"] is False,
            "Unsupported scoring contract")
    require(binding["runtime"]["python"] == "3.14.5" and selection["native_pair_cap"] == 8192,
            "Unsupported model/runtime capability")
    require(isinstance(selection["input_bundle_id"], str) and bool(selection["input_bundle_id"]),
            "Missing input bundle identity")
    return selection


def rank_candidate_set(candidate_set, score_result, selection, *, pair_budget, profile=None):
    """CandidateSet + exact bound BGE scores -> complete RankedCandidateSet.

    Scores come from the validated local adapter; this function never scores,
    discovers, reads Parent text, trims candidates or automatically falls back.
    """
    validate_selection(selection)
    profile = selection["default_profile"] if profile is None else profile
    require(profile in PROFILES, "Select an explicit supported ordering profile")
    require(candidate_set["version"] == "gc-clean-candidates-1" and candidate_set["status"] == "complete"
            and "reranking_run" not in candidate_set, "Expected a completed unranked CandidateSet")
    require(candidate_set["input_bundle_id"] == selection["input_bundle_id"], "Foreign input bundle")
    QuerySpec(**candidate_set["query"])
    validate_profile(candidate_set["profile"])
    require(candidate_set["profile"]["combination"] == "S0" and candidate_set["profile"]["fusion"] == "f1_max"
            and candidate_set["profile"]["structural_cleanup"] is True, "Selected upstream profile differs")
    require(type(pair_budget) is int and 0 < pair_budget <= selection["native_pair_cap"], "Invalid explicit pair budget")
    candidates = candidate_set["candidates"]
    ids = [c["chunk_id"] for c in candidates]
    require(ids and len(set(ids)) == len(ids), "Empty or repeated candidate identity")
    for c in candidates:
        source, chunk = c["source"], c["chunk"]
        require({"clean_record", "chunk_locator", "parent_locator", "conditions", "source_context",
                 "input_snapshot_id", "granularities"} <= set(source), "Missing upstream source locators/context")
        require("reranking" not in c and type(c["input_row"]) is int and c["input_row"] >= 0,
                "Already ranked or invalid input row")
        require(all(source[k] == chunk[k] for k in ("chunk_id", "observation_id", "parent_id", "clean_refs"))
                and c["chunk_id"] == chunk["chunk_id"], "Chunk/source identity differs")
        text = chunk["retrieval_text"]
        require(isinstance(text, str) and bool(text) and source["dense_text"] == text
                and source["dense_text_sha256"] == sha256(text.encode("utf8")).hexdigest(), "Input text/hash differs")
        require(isinstance(c["hits"], list) and bool(c["hits"]), "Missing original recall hits")
    require(score_result.get("status", "complete") == "complete"
            and score_result["binding"] == selection["score_binding"], "Foreign or incomplete score result")
    require(score_result["candidate_set_sha256"] == digest(candidate_set), "Scores belong to a different CandidateSet")
    require([a["chunk_id"] for a in score_result["audit"]] == ids
            and all(type(a["tokens"]) is int and 0 < a["tokens"] <= pair_budget for a in score_result["audit"]),
            "Missing, reordered or overlength token audit; no truncation")
    result = ranked_candidates(candidate_set, score_result["scores"], **PROFILES[profile])
    result["reranking_run"] = {
        "selection_id": digest(selection), "profile": profile, "model": selection["model"],
        "input_version": candidate_set["version"], "candidate_set_sha256": digest(candidate_set),
        "score_result_digest": digest(score_result), "pair_budget": pair_budget,
        "mmr_applied": False, "ordering_code_sha256": ordering_code(),
        "ordering_python": platform.python_version(),
        "scoring": deepcopy({k: v for k, v in score_result.items() if k != "scores"})}
    return result


def _sources(repo, selection_path):
    """Verify the original experiment and reviewed analysis without changing them."""
    from . import rerank_job as job
    selection_path = Path(selection_path).resolve()
    selection = validate_selection(read(selection_path))
    config_path = inside(repo, selection["experiment_config"])
    require(sha(config_path) == selection["experiment_config_sha256"], "Frozen experiment config changed")
    config = read(config_path)
    run = inside(repo, config["output"])
    frozen, plan = job.load_plan(repo, config, run)
    checked = job.verified_completion(run, plan)
    require(plan["scope"] == "70q" and digest(plan) == selection["plan_binding"]
            and sha(run / "complete.json") == selection["run_complete_sha256"]
            and plan["models"]["bge"] == selection["score_binding"], "Frozen score contract differs")
    analysis = inside(repo, selection["analysis"])
    meta = verify(analysis)
    require(sha(analysis / "complete.json") == selection["analysis_complete_sha256"]
            and meta["plan_binding"] == digest(plan) and meta["selected_for_analysis"] == "bge", "P3 analysis changed")
    review = inside(repo, selection["p3_review"])
    verify(review)
    require(sha(review / "complete.json") == selection["p3_review_complete_sha256"], "P3 review changed")
    references = {"input": {"path": frozen.relative_to(repo).as_posix(), "complete_sha256": sha(frozen / "complete.json")},
                  "scores": {"path": run.relative_to(repo).as_posix(), "complete_sha256": sha(run / "complete.json")},
                  "analysis": {"path": analysis.relative_to(repo).as_posix(), "complete_sha256": sha(analysis / "complete.json")},
                  "p3_review": {"path": review.relative_to(repo).as_posix(), "complete_sha256": sha(review / "complete.json")},
                  "score_validation": checked}
    return selection, frozen, plan, job.checkpoints(run, plan), analysis, references


def _compare_analysis(result, page, profile):
    key = "structural/1.0" if profile == "bge_structural" else "bge"
    compact = [{"chunk_id": c["chunk_id"], "input_row": c["input_row"], "reranking": c["reranking"]}
               for c in result["candidates"]]
    require(page["selected_for_analysis"] == "bge" and result["policy"] == page["variants"][key]["policy"]
            and compact == page["variants"][key]["candidates"], "Selected ordering differs from reviewed P3")


def export_handoff(repo, selection_path, output, *, profile=None):
    from .rerank_job import new_root
    repo = Path(repo).resolve()
    selection, frozen, plan, found, analysis, references = _sources(repo, selection_path)
    profile = selection["default_profile"] if profile is None else profile
    require(profile in PROFILES, "Unknown handoff profile")
    output = new_root(repo, output)
    try:
        entries, deferred = [], 0
        write(output / "selection.json", selection)
        for entry in plan["questions"]:
            qid = entry["question_id"]
            source = inside(frozen, entry["file"])
            require(sha(source) == entry["sha256"], "Frozen question changed")
            bundle = read(source)
            score_file = found["bge", qid] / "result.json"
            ranked = rank_candidate_set(bundle, read(score_file), selection, pair_budget=plan["budget"], profile=profile)
            _compare_analysis(ranked, read(analysis / (qid + ".json")), profile)
            path = inside(output, "ranked/" + qid + ".json")
            write(path, ranked)
            deferred += sum(c["reranking"]["covered_by"] is not None for c in ranked["candidates"])
            entries.append({"question_id": qid, "file": path.relative_to(output).as_posix(), "sha256": sha(path),
                            "candidates": len(ranked["candidates"]), "input_sha256": entry["sha256"],
                            "score_result_sha256": sha(score_file)})
        manifest = {"version": HANDOFF_VERSION, "status": "complete", "profile": profile,
                    "selection_id": digest(selection), "input_bundle_id": selection["input_bundle_id"],
                    "model": selection["model"], "mmr_enabled": False, "pair_budget": plan["budget"],
                    "ordering_code_sha256": ordering_code(), "references": references, "questions": entries,
                    "candidate_count": sum(e["candidates"] for e in entries), "deferred": deferred,
                    "inference_pairs": 0, "evidence_assembly": "Generation-owned; not performed"}
        write(output / "manifest.json", manifest)
        seal(output, {"version": HANDOFF_VERSION, "profile": profile, "selection_id": digest(selection),
                      "questions": len(entries), "candidates": manifest["candidate_count"]})
        return {"status": "complete", "output": str(output), "profile": profile,
                "questions": len(entries), "candidates": manifest["candidate_count"], "deferred": deferred,
                "complete_sha256": sha(output / "complete.json"), "inference_pairs": 0}
    except BaseException as exc:
        # An incomplete create-only export is retained; no completion or retry.
        if not (output / "complete.json").exists():
            write(output / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        raise


def validate_handoff(repo, selection_path, output):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    verify(output)
    manifest = read(output / "manifest.json")
    selection, frozen, plan, found, analysis, references = _sources(repo, selection_path)
    require(read(output / "selection.json") == selection and manifest["selection_id"] == digest(selection)
            and manifest["references"] == references and manifest["ordering_code_sha256"] == ordering_code(),
            "Handoff selection/source/code changed")
    require(manifest["version"] == HANDOFF_VERSION and manifest["status"] == "complete"
            and manifest["profile"] in PROFILES and manifest["mmr_enabled"] is False
            and manifest["inference_pairs"] == 0 and manifest["pair_budget"] == plan["budget"]
            and manifest["input_bundle_id"] == selection["input_bundle_id"]
            and manifest["model"] == selection["model"], "Unexpected handoff contract")
    require([e["question_id"] for e in manifest["questions"]] == [e["question_id"] for e in plan["questions"]],
            "Handoff question coverage differs")
    total, deferred = 0, 0
    for entry, original in zip(manifest["questions"], plan["questions"]):
        path = inside(output, entry["file"])
        score_path = found["bge", entry["question_id"]] / "result.json"
        require(sha(path) == entry["sha256"] and entry["input_sha256"] == original["sha256"]
                and entry["score_result_sha256"] == sha(score_path), "Handoff row binding differs")
        bundle, scores = read(frozen / original["file"]), read(score_path)
        ranked = read(path)
        expected = rank_candidate_set(bundle, scores, selection, pair_budget=plan["budget"], profile=manifest["profile"])
        require(ranked == expected and len(ranked["candidates"]) == entry["candidates"], "Handoff content/order differs")
        page = read(analysis / (entry["question_id"] + ".json"))
        _compare_analysis(ranked, page, manifest["profile"])
        # Check the explicit no-structure fallback against the existing P2 order;
        # no duplicate full-corpus export or score/model run is needed.
        fallback = rank_candidate_set(bundle, scores, selection, pair_budget=plan["budget"], profile="bge_relevance")
        _compare_analysis(fallback, page, "bge_relevance")
        total += len(ranked["candidates"])
        deferred += sum(c["reranking"]["covered_by"] is not None for c in ranked["candidates"])
    require(total == manifest["candidate_count"] and deferred == manifest["deferred"], "Handoff counts differ")
    return {"status": "PASS", "version": HANDOFF_VERSION, "profile": manifest["profile"],
            "questions": len(manifest["questions"]), "candidates": total, "deferred": deferred,
            "fallback_questions_checked": len(plan["questions"]), "inference_pairs": 0,
            "complete_sha256": sha(output / "complete.json"), "selection_id": digest(selection),
            "semantic_quality": "finite P2/P3 review only; not end-to-end answer acceptance"}


def read_handoff(output, question_id, *, profile="bge_structural"):
    """Read a sealed self-contained CandidateSet; no Parent readback or admission."""
    output = Path(output)
    verify(output)
    manifest = read(output / "manifest.json")
    selection = validate_selection(read(output / "selection.json"))
    require(manifest["version"] == HANDOFF_VERSION and manifest["profile"] == profile
            and manifest["selection_id"] == digest(selection), "Unexpected handoff selection/profile")
    entries = [e for e in manifest["questions"] if e["question_id"] == question_id]
    require(len(entries) == 1, "Unknown or ambiguous handoff question")
    entry = entries[0]
    path = inside(output, entry["file"])
    require(sha(path) == entry["sha256"], "Handoff question changed")
    value = read(path)
    require(value["version"] == "gc-clean-ranked-candidates-1" and value["query"]["question_id"] == question_id
            and value["reranking_run"]["selection_id"] == digest(selection)
            and value["reranking_run"]["profile"] == profile and len(value["candidates"]) == entry["candidates"],
            "Foreign RankedCandidateSet")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "validate"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--selection", default=DEFAULT_SELECTION)
    parser.add_argument("--output")
    parser.add_argument("--profile", choices=tuple(PROFILES))
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    selection_path = inside(repo, args.selection)
    selection = validate_selection(read(selection_path))
    output = Path(args.output).resolve() if args.output else inside(repo, selection["handoff_output"])
    require(output.parent == (repo / "data/retrieval").resolve(), "Use a direct data/retrieval child")
    if args.mode == "export":
        result = export_handoff(repo, selection_path, output, profile=args.profile)
    else:
        require(args.profile is None, "Validate reads the sealed profile; no override")
        result = validate_handoff(repo, selection_path, output)
        from ...indexing.benchmark_io import attempt
        folder = attempt(output.with_name(output.name + "-checks"), "validation")
        write(folder / "report.json", result)
        seal(folder, {"status": "PASS", "complete_sha256": result["complete_sha256"]})
        result = {**result, "validation": str(folder)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
