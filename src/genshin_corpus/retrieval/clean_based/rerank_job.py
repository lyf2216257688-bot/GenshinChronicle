"""Saved-S0 experiment. Prepare is model-free; real Preflight/Smoke/Run are manual."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import traceback

from ...build_runtime import atomic_json
from ...evidence_io import digest, inside, read, require, seal, sha, verify, write
from ...indexing.benchmark_io import attempt, build_lock, child_process, receipt, verify_receipt
from .rerank_core import ranked_candidates, candidate_vectors

VERSION = "gc-clean-reranking-1"
CODE = ["retrieval/clean_based/" + n for n in
        ("rerank_job.py", "rerank_core.py", "rerank_model.py", "rerank_worker.py", "core.py")]
CODE += ["evidence_io.py", "indexing/benchmark_io.py", "build_runtime.py",
         "retrieval/gte_multilingual_reranker.py", "retrieval/bge_reranker_v2_m3.py"]


def code_files(repo):
    return {n: sha(repo / "src/genshin_corpus" / n) for n in CODE}


def new_root(repo, output):
    output = Path(output).resolve()
    require(output.parent == (repo / "data/retrieval").resolve(), "Use a new direct child of data/retrieval")
    output.mkdir(parents=True, exist_ok=False)
    return output


def prepare(repo, config):
    source = repo / config["recall_output"]
    complete = read(source / "complete.json")
    pointer = read(source / "validation.json")
    report = inside(source, pointer["folder"]) / "report.json"
    require(sha(source / "complete.json") == pointer["complete_sha256"]
            and sha(report) == pointer["report_sha256"], "P4 validation binding differs")
    checked = read(report)
    require(checked["status"] == "PASS" and checked["questions"] == config["expected_questions"]
            and checked["complete_sha256"] == pointer["complete_sha256"], "P4 not independently validated")
    original = read(source / "plan.json")
    require(complete["plan_binding"] == digest(original), "P4 plan differs")
    output = new_root(repo, repo / config["input"])
    entries, count = [], 0
    for question in original["questions"]:
        qid = question["question_id"]
        item = complete["units"]["comparison"][qid]
        folder = inside(source, item["folder"])
        require(sha(folder / "complete.json") == item["receipt_sha256"], "P4 receipt differs")
        rec = verify_receipt(folder, complete["plan_binding"])
        result, documents = read(folder / "result.json"), read(folder / "documents.json")
        require(result["question"] == question, "Question bytes differ")
        selected = result["profiles"][config["profile"]]
        require(selected["profile"]["combination"] == "S0" and selected["profile"]["structural_cleanup"]
                and selected["profile"]["fusion"] == "f1_max", "Wrong frozen S0 profile")
        candidates = []
        for c in selected["candidates"]:
            d = documents[c["chunk_id"]]
            require(d["input_row"] == c["input_row"] and d["chunk"]["chunk_id"] == c["chunk_id"]
                    and d["row"]["chunk_id"] == c["chunk_id"], "Saved candidate/source identity differs")
            text = d["chunk"]["retrieval_text"]
            require(text == d["row"]["dense_text"] and sha256(text.encode()).hexdigest()
                    == d["row"]["dense_text_sha256"], "Saved text differs")
            candidates.append({**c, "source": d["row"], "chunk": d["chunk"]})
        bundle = {**selected, "candidates": candidates, "source_binding":
                  {"folder": item["folder"], "receipt_sha256": item["receipt_sha256"], "files": rec["files"]}}
        path = output / (qid + ".json")
        write(path, bundle)
        entries.append({"question_id": qid, "file": path.name, "sha256": sha(path), "candidates": len(candidates)})
        count += len(candidates)
    require(len(entries) == config["expected_questions"] and count == config["expected_candidates"],
            "Frozen question/candidate count differs")
    write(output / "manifest.json", {"version": VERSION, "profile": config["profile"],
        "input_bundle_id": original["input_bundle_id"], "source_complete_sha256": pointer["complete_sha256"],
        "source": config["recall_output"], "questions": entries, "candidate_count": count})
    seal(output, {"scope": "fixed-S0-input", "semantic_quality": "NOT_REVIEWED"})
    return {"status": "PASS", "questions": len(entries), "candidates": count, "output": str(output)}


def input_manifest(repo, config):
    root = repo / config["input"]
    verify(root)
    manifest = read(root / "manifest.json")
    require(manifest["version"] == VERSION and manifest["profile"] == config["profile"], "Foreign input")
    source = repo / manifest["source"]
    require(sha(source / "complete.json") == manifest["source_complete_sha256"], "P4 completion changed")
    for item in manifest["questions"]:
        bundle = read(inside(root, item["file"]))
        binding = bundle["source_binding"]
        folder = inside(source, binding["folder"])
        require(sha(folder / "complete.json") == binding["receipt_sha256"], "Source receipt changed")
        for name in ("result.json", "documents.json"):
            require(sha(folder / name) == binding["files"][name], "Saved source changed")
    return root, manifest


@contextmanager
def worker(repo, config, model, output):
    folder = attempt(output / "workers", model)
    pool = ThreadPoolExecutor(max_workers=1)
    process = None
    try:
        with (folder / "stderr.log").open("x", encoding="utf-8") as log:
            env = dict(os.environ, PYTHONPATH=str(repo / "src"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                       PYTHONDONTWRITEBYTECODE="1", TOKENIZERS_PARALLELISM="false",
                       OPENBLAS_NUM_THREADS="4", OMP_NUM_THREADS="4")
            command = [str(repo / config["python"]), "-B", "-u", "-m",
                       "genshin_corpus.retrieval.clean_based.rerank_worker", "--repo", str(repo),
                       "--model", model, "--scratch", str(folder / "scratch")]
            with child_process(command, cwd=repo, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=log, text=True, encoding="utf-8") as process:
                def receive():
                    line = pool.submit(process.stdout.readline).result(config["worker_timeout_seconds"])
                    require(bool(line), "Worker closed; see " + str(folder / "stderr.log"))
                    value = json.loads(line)
                    require(value["status"] != "failed", "Worker failure: " + str(value))
                    return value
                ready = receive()
                def request(value):
                    process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                    return receive()
                yield ready, request
                process.stdin.write('{"action":"close"}\n')
                process.stdin.flush()
                process.wait(timeout=15)
    finally:
        if process is not None:
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    pipe.close()
        pool.shutdown(wait=True, cancel_futures=True)


def preflight(repo, config, output):
    root, manifest = input_manifest(repo, config)
    new_root(repo, output)
    audits, bindings, caps = {}, {}, []
    for model in config["models"]:
        with worker(repo, config, model, output) as (ready, request):
            bindings[model] = ready["binding"]
            caps.append(ready["native_cap"])
            audits[model] = {}
            for entry in manifest["questions"]:
                value = request({"action": "audit", "input": str(root / entry["file"])})
                require(len(value["counts"]) == entry["candidates"], "Length audit omitted candidates")
                audits[model][entry["question_id"]] = value["counts"]
    maximum = max(a["tokens"] for model in audits.values() for rows in model.values() for a in rows)
    budget = max(config["initial_pair_budget"], maximum)
    require(budget <= min(caps), "Pair exceeds common native capability; preserve candidates and review")
    locations = [(row["tokens"], qid, row["chunk_id"]) for data in audits.values() for qid, rows in data.items() for row in rows]
    smoke_cases = [min(locations), max(locations)]
    plan = {"version": VERSION, "scope": "70q", "configuration": config, "code": code_files(repo),
            "input_complete_sha256": sha(root / "complete.json"), "models": bindings, "budget": budget,
            "questions": manifest["questions"], "smoke_cases": smoke_cases}
    write(output / "length-audit.json", audits)
    plan["length_audit_sha256"] = sha(output / "length-audit.json")
    write(output / "plan.json", plan)
    return {"status": "PASS", "pair_budget": budget, "inference_pairs": 0, "real_smoke": "NOT_RUN"}


def load_plan(repo, config, output):
    plan = read(output / "plan.json")
    require(plan["version"] == VERSION and plan["configuration"] == config and plan["code"] == code_files(repo),
            "Code/config changed; use a new preflight/output")
    root, _ = input_manifest(repo, config)
    require(sha(root / "complete.json") == plan["input_complete_sha256"], "Frozen input changed")
    require(sha(output / "length-audit.json") == plan["length_audit_sha256"], "Length audit changed")
    return root, plan


def checkpoints(output, plan):
    found = {}
    for p in (output / "work").glob("*/*/complete.json"):
        rec = verify_receipt(p.parent, digest(plan))
        key = (rec["model"], rec["question_id"])
        require(key not in found and set(rec["files"]) == {"result.json"}, "Duplicate/foreign score checkpoint")
        found[key] = p.parent
    return found


def execute(repo, config, output, smoke=False, resume=False):
    if smoke:
        root, base = load_plan(repo, config, repo / config["output"])
        new_root(repo, output)
        cases = []
        for n, (_, qid, cid) in enumerate(base["smoke_cases"]):
            bundle = read(root / (qid + ".json"))
            sample = {**bundle, "query": {**bundle["query"], "question_id": "engineering-" + str(n),
                      "question": "这段资料明确记载了哪些人物、事件与条件？"},
                      "candidates": [c for c in bundle["candidates"] if c["chunk_id"] == cid]}
            name = "engineering-" + str(n) + ".json"
            write(output / name, sample)
            cases.append({"question_id": sample["query"]["question_id"], "file": name,
                          "sha256": sha(output / name), "candidates": 1})
        # Audit the actual engineering query too; it must fit the common budget.
        plan = {**base, "scope": "engineering-smoke", "base_plan_binding": digest(base), "questions": cases}
        write(output / "plan.json", plan)
        root = output
    else:
        root, plan = load_plan(repo, config, output)
        smoke_root = repo / config["smoke_output"]
        smoke_plan = read(smoke_root / "plan.json")
        require(smoke_plan["base_plan_binding"] == digest(plan), "Smoke does not match this plan")
        require(verified_completion(smoke_root, smoke_plan)["scope"] == "engineering-smoke", "Real validated Smoke required")
    with build_lock(output / "build.lock"):
        found = checkpoints(output, plan)
        require(resume or not found, "Existing scores; use Resume")
        for model in config["models"]:
            pending = [e for e in plan["questions"] if (model, e["question_id"]) not in found]
            if not pending:
                continue
            with worker(repo, config, model, output) as (ready, request):
                require(ready["binding"] == plan["models"][model], "Actual model/runtime differs")
                for entry in pending:
                    path = root / entry["file"]
                    require(sha(path) == entry["sha256"], "Question input changed")
                    value = request({"action": "score", "input": str(path), "budget": plan["budget"],
                                     "limits": config["limits"]})
                    bundle = read(path)
                    ranked_candidates(bundle, value["scores"], structural=False)
                    require(value["candidate_set_sha256"] == digest(bundle), "Scored input differs")
                    folder = attempt(output / "work" / model, entry["question_id"])
                    tick = time.perf_counter()
                    write(folder / "result.json", value)
                    receipt(folder, digest(plan), model=model, question_id=entry["question_id"],
                            result_write_seconds=time.perf_counter() - tick)
                    print(json.dumps({"event": "question_committed", "model": model,
                                      "question_id": entry["question_id"]}), flush=True)
        found = checkpoints(output, plan)
        expected = {(m, e["question_id"]) for m in config["models"] for e in plan["questions"]}
        require(set(found) == expected, "Incomplete model/question coverage")
        complete = {"plan_binding": digest(plan), "scope": plan["scope"],
                    "units": {m + "/" + q: {"folder": p.relative_to(output).as_posix(),
                              "receipt_sha256": sha(p / "complete.json")} for (m, q), p in found.items()}}
        if (output / "complete.json").exists():
            require(read(output / "complete.json") == complete, "Completion changed")
        else:
            write(output / "complete.json", complete)
    return {"status": "complete", "semantic_quality": "NOT_REVIEWED", "scope": plan["scope"]}


def validate(repo, config, output):
    plan = read(output / "plan.json")
    require(plan["code"] == code_files(repo) and plan["configuration"] == config, "Stale validation code/config")
    root = output if plan["scope"] == "engineering-smoke" else load_plan(repo, config, output)[0]
    complete = read(output / "complete.json")
    require(complete["plan_binding"] == digest(plan), "Completion plan changed")
    found = checkpoints(output, plan)
    expected = {(m, e["question_id"]) for m in config["models"] for e in plan["questions"]}
    require(set(found) == expected and len(complete["units"]) == len(expected), "Missing/foreign score group")
    for model, qid in expected:
        folder = found[(model, qid)]
        require(complete["units"][model + "/" + qid] == {"folder": folder.relative_to(output).as_posix(),
                "receipt_sha256": sha(folder / "complete.json")}, "Completion receipt differs")
        entry = next(e for e in plan["questions"] if e["question_id"] == qid)
        path = root / entry["file"]
        require(sha(path) == entry["sha256"], "Scoring input changed")
        bundle, value = read(path), read(folder / "result.json")
        require(value["binding"] == plan["models"][model] and value["candidate_set_sha256"] == digest(bundle), "Score binding differs")
        ranked_candidates(bundle, value["scores"], structural=False)
        require({a["chunk_id"] for a in value["audit"]} == set(value["scores"])
                and len(value["audit"]) == len(value["scores"])
                and all(0 < a["tokens"] <= plan["budget"] for a in value["audit"]), "Invalid token audit")
    folder = attempt(output / "validations", "check")
    report = {"status": "PASS", "scope": plan["scope"], "plan_binding": digest(plan),
              "complete_sha256": sha(output / "complete.json"), "groups": len(found), "semantic_quality": "NOT_REVIEWED"}
    write(folder / "report.json", report)
    atomic_json(output / "validation.json", {"folder": folder.relative_to(output).as_posix(),
                "report_sha256": sha(folder / "report.json"), "complete_sha256": report["complete_sha256"]})
    return report


def verified_completion(output, plan):
    pointer = read(output / "validation.json")
    path = inside(output, pointer["folder"]) / "report.json"
    require(sha(path) == pointer["report_sha256"] and sha(output / "complete.json") == pointer["complete_sha256"],
            "Independent Validate required")
    report = read(path)
    require(report["status"] == "PASS" and report["plan_binding"] == digest(plan)
            and report["complete_sha256"] == pointer["complete_sha256"], "Stale validation")
    checkpoints(output, plan)
    return report


def witness_ranks(candidates, evidence):
    """Literal/source-bound diagnostic only, never whole-Chunk equivalence."""
    result = []
    for item in evidence:
        source, quote = item.get("source", {}), item.get("quote", "")
        ranks = [rank for rank, candidate in enumerate(candidates, 1)
                 if quote and quote in candidate["chunk"]["retrieval_text"]
                 and all(candidate["source"].get(k) == source.get(k) and k in source
                         for k in ("observation_id", "parent_id", "conditions"))]
        result.append({"input_row": item.get("input_row"), "quote": quote,
                       "best_rank": min(ranks) if ranks else None, "kind": "literal_fragment_diagnostic"})
    return result


def report(repo, config, output, model=None):
    root, plan = load_plan(repo, config, output)
    verified_completion(output, plan)
    found = checkpoints(output, plan)
    folder = attempt(output / "reports", "analysis" if model else "comparison")
    selection = read(repo / "config/index-selected-v1.json")
    totals = {}
    review_path = repo / "data/retrieval/p4-quality-review-20261113/review.json"
    reviews = {r["question_id"]: r.get("evidence", []) for r in read(review_path)} if review_path.exists() else {}
    for entry in plan["questions"]:
        bundle = read(root / entry["file"])
        variants, telemetry = {}, {}
        for key in config["models"]:
            value = read(found[key, entry["question_id"]] / "result.json")
            variants[key] = ranked_candidates(bundle, value["scores"], structural=False)
            telemetry[key] = {k: value[k] for k in ("elapsed_seconds", "load_seconds", "resources")}
            telemetry[key]["load_resources"] = value.get("load_resources")
            telemetry[key].update(
                tokenization_seconds=sum(a["tokenization_seconds"] for a in value["audit"]),
                forward_seconds=sum(a["forward_seconds"] for a in value["audit"]),
                tokens=sum(a["tokens"] for a in value["audit"]),
                result_write_seconds=read(found[key, entry["question_id"]] / "complete.json")["result_write_seconds"])
            totals.setdefault(key, []).append(value["elapsed_seconds"])
        if model:
            require(model in config["models"], "Select bge or gte explicitly")
            value = read(found[model, entry["question_id"]] / "result.json")
            vectors = candidate_vectors(repo / selection["dense"]["manifest"], bundle["candidates"])
            for lam in config["mmr_lambdas"]:
                variants["structural/" + str(lam)] = ranked_candidates(bundle, value["scores"], vectors=vectors,
                    mmr_lambda=lam, local_depth=config["local_depth"], protected_top=config["protected_top"],
                    protected_gap=config["protected_gap"])
        evidence = reviews.get(entry["question_id"], [])
        diagnostics = {"recall": witness_ranks(bundle["candidates"], evidence),
                       **{k: witness_ranks(v["candidates"], evidence) for k, v in variants.items()}}
        compact = {k: {"policy": v["policy"], "policy_id": v["policy_id"], "candidates":
                   [{"chunk_id": c["chunk_id"], "input_row": c["input_row"], "reranking": c["reranking"]}
                    for c in v["candidates"]]} for k, v in variants.items()}
        write(folder / (entry["question_id"] + ".json"), {"input": str(root / entry["file"]),
            "input_sha256": entry["sha256"], "input_bundle_id": bundle["input_bundle_id"],
            "model_bindings": plan["models"],
            "selected_for_analysis": model,
            "recall_order": [c["chunk_id"] for c in bundle["candidates"]],
            "topk": {name: {str(k): [c["chunk_id"] for c in rows[:k]] for k in (5, 10, 20)}
                     for name, rows in [("S0", bundle["candidates"])]
                     + [(name, value["candidates"]) for name, value in variants.items()]},
            "variants": compact, "telemetry": telemetry, "witness_diagnostics": diagnostics,
            "semantic_judgment": "NOT_REVIEWED"})
        text = ["# " + entry["question_id"], "", bundle["query"]["question"], "",
                "有限原文复核入口；未完成语义审阅，不是最终Packet。", ""]
        for name, rows in [("S0", bundle["candidates"])] + [(name, value["candidates"]) for name, value in variants.items()]:
            text += ["## " + name, ""]
            for rank, c in enumerate(rows[:20], 1):
                ordering = c.get("reranking", {k: c[k] for k in ("fusion_rank", "organized_rank", "deferred_by") if k in c})
                text += [f"### {rank}. {c['chunk_id']}", "",
                         "排序记录: " + json.dumps(ordering, ensure_ascii=False), "",
                         "来源性质: " + json.dumps(c["source"]["source_context"].get("source_provenance"), ensure_ascii=False),
                         "", *["> " + line for line in c["chunk"]["retrieval_text"].splitlines()], ""]
        (folder / (entry["question_id"] + ".md")).write_text("\n".join(text), encoding="utf-8")
    summary = {"scope": "finite-70q", "selected_for_analysis": model, "semantic_quality": "NOT_REVIEWED",
        "review_witness_sha256": sha(review_path) if review_path.exists() else None,
        "timing_seconds": {m: {"median": statistics.median(v), "p95": sorted(v)[int(.95 * (len(v) - 1))], "sum": sum(v)}
                           for m, v in totals.items()}, "plan_binding": digest(plan)}
    write(folder / "summary.json", summary)
    seal(folder, summary)
    return {**summary, "report": str(folder)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "preflight", "smoke", "run", "resume", "validate", "report", "analyze", "status"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--config", default="config/clean-reranking-v1.json")
    parser.add_argument("--output")
    parser.add_argument("--model", choices=("bge", "gte"))
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    config = read(repo / args.config)
    require(config["version"] == VERSION and config["models"] == ["bge", "gte"], "Unsupported model/config scope")
    output = Path(args.output).resolve() if args.output else repo / config["smoke_output" if args.mode == "smoke" else "output"]
    require(output.parent == (repo / "data/retrieval").resolve()
            and output not in {repo / config["input"], repo / config["recall_output"]}, "Protected/invalid output")
    try:
        if args.mode == "prepare": result = prepare(repo, config)
        elif args.mode == "preflight": result = preflight(repo, config, output)
        elif args.mode in {"run", "resume", "smoke"}:
            result = execute(repo, config, output, smoke=args.mode == "smoke", resume=args.mode == "resume")
        elif args.mode == "validate": result = validate(repo, config, output)
        elif args.mode in {"report", "analyze"}:
            require(args.mode != "analyze" or args.model, "Analyze requires operator-selected --model")
            result = report(repo, config, output, args.model if args.mode == "analyze" else None)
        else: result = {"complete": (output / "complete.json").exists(), "validation": (output / "validation.json").exists()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except BaseException as exc:
        # Never remove incomplete attempts or replace a committed result.
        if output.exists() and (output / "plan.json").exists() and read(output / "plan.json").get("version") == VERSION:
            folder = attempt(output / "failures", args.mode)
            write(folder / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
