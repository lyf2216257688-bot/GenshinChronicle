"""Operator-run P4 recall comparison. Preflight and Smoke never start 70Q."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import time
import traceback

from ...build_runtime import atomic_json
from ...evidence_io import digest, inside, read, require, sha, write
from ...indexing.benchmark_io import (attempt, build_lock, disk_guard, event, lines, offline_only,
    prevent_idle_sleep, ram_guard, receipt, resources, verify_receipt)
from ...indexing.sparse_job import CODE_FILES as SPARSE_CODE, Tee, output_guard
from ...indexing.sparse_full import validate_store
from .core import QuerySpec
from .experiment_analysis import analyze, case_ids, profiles, render_cases, stable_comparison
from .indexes import selected
from .source import FileBinding, InputReader

VERSION = "gc-recall-experiment-1"
CODE_FILES = sorted(set(SPARSE_CODE + ["retrieval/clean_based/" + name for name in
    ("core.py", "runtime.py", "experiment.py", "experiment_analysis.py", "experiment_worker.py", "experiment_report.py")]
    + ["indexing/benchmark_lexical.py", "indexing/analyzers.py", "chunking/reader.py", "clean/reader.py"]))
SMOKE_QUESTIONS = [{"question_id": "engineering-01", "question": "坎瑞亚与深渊有什么关系？"}]
GROUPS = {"neural": {"dense", "sparse"}, "bm25f": {"bm25f"}}


def settings(repo, path):
    config = read(path)
    require(config["version"] == VERSION, "Unknown P4 configuration")
    require((repo / config["python"]).resolve() == Path(sys.executable).resolve(), "Use the pinned P4 runtime")
    for key in ("depth", "candidate_limit", "rrf_k", "representative_questions", "case_candidates", "max_case_chunks", "worker_timeout_seconds"):
        require(type(config[key]) is int and config[key] > 0, "Invalid " + key)
    require(config["depth"] <= 200 and config["candidate_limit"] <= 400, "Use a bounded P4 operating point")
    require(isinstance(config["cutoffs"], list) and config["cutoffs"] == sorted(set(config["cutoffs"]))
            and all(type(k) is int and 0 < k <= config["candidate_limit"] for k in config["cutoffs"])
            and config["cutoffs"], "Invalid cutoffs")
    for key in ("minimum_free_gib", "minimum_available_ram_gib", "minimum_running_ram_gib", "minimum_free_vram_gib"):
        require(type(config[key]) in {float, int} and math.isfinite(config[key]) and config[key] > 0, "Invalid " + key)
    profiles(config)
    return config, read(repo / config["retrieval_config"])


def guard(repo, config, base, output):
    # Reuse selected source/model/index protections; also protect all old 70Q evidence.
    output_guard(repo, base, output, [repo / config["sparse_root"], (repo / config["questions"]).parent,
        repo / "data/retrieval/clean-retrieval-p0p2-20261111-review"])
    if (output / "plan.json").exists():
        require(read(output / "plan.json").get("version") == VERSION, "Foreign output plan")


def questions_from(repo, config):
    path = repo / config["questions"]
    require(sha(path) == config["questions_sha256"], "Original 70Q bytes changed")
    questions = list(lines(path))
    require([q.get("question_id") for q in questions] == [f"Q{n:03}" for n in range(1, 71)], "Expected original Q001–Q070")
    for q in questions:
        require(set(q) == {"question_id", "question"}, "Runtime questions must not contain review/answer data")
        QuerySpec(**q)
    return questions


def code_files(repo):
    return {str(repo / "src/genshin_corpus" / name): sha(repo / "src/genshin_corpus" / name) for name in CODE_FILES}


def prepare(repo, config_path, config, base, output, *, smoke=False):
    started = time.perf_counter()
    ram_guard(config)
    disk_guard(output, config)
    selection, manifests = selected(repo, base)
    sparse_root = repo / config["sparse_root"]
    mpath = sparse_root / "manifest.json"
    require(sha(mpath) == config["sparse_manifest_sha256"], "Selected full Sparse manifest changed")
    sparse = read(mpath)
    require(sparse["scope"] == "full-selected" and sparse["status"] == "complete"
            and sparse["input_bundle_id"] == selection["source"]["input_bundle_id"]
            and sparse["counts"] == manifests["dense"][1]["counts"], "Sparse/full selected input differs")
    pointer = read(sparse_root / "validation.json")
    report_path = inside(sparse_root, pointer["folder"]) / "report.json"
    require(pointer["manifest_sha256"] == sha(mpath) and sha(report_path) == pointer["report_sha256"], "Sparse validation pointer changed")
    report = read(report_path)
    require(report["status"] == "PASS" and report["manifest_sha256"] == sha(mpath)
            and report["counts"] == sparse["counts"] and report["plan_binding"] == sparse["binding"], "No matching independent Sparse validation")
    validate_store(sparse_root)
    original = questions_from(repo, config)
    questions = SMOKE_QUESTIONS if smoke else original
    files = {}
    def bind(path, expected=None):
        value = sha(path)
        require(expected is None or value == expected, "Asset hash mismatch: " + str(path))
        files[str(Path(path).resolve())] = value
    for path in (config_path, repo / config["retrieval_config"], repo / base["selection"], repo / config["questions"],
                 mpath, sparse_root / "plan.json", sparse_root / "validation.json", report_path):
        bind(path)
    for path, m in [*manifests.values(), (mpath, sparse)]:
        bind(path)
        if "shards" in m:
            for shard in m["shards"]:
                folder = inside(path.parent, shard["folder"])
                bind(folder / "complete.json", shard["receipt_sha256"])
                rec = verify_receipt(folder, m["binding"])
                for name, checksum in rec["files"].items():
                    bind(inside(folder, name), checksum)
        else:
            for name, checksum in m["files"].items():
                bind(inside(path.parent, name), checksum)
    with InputReader(manifests["dense"][1]["input_manifest"]) as reader:
        require(reader.bundle_id == sparse["input_bundle_id"] and reader.manifest["counts"] == sparse["counts"], "Source input differs")
        for path in reader.binding.files:
            bind(path)
    del reader
    # Keep Torch out of the coordinator; release this CPU/tokenizer inspector
    # before the model worker is launched on a 16GiB machine.
    from .experiment_worker import ExperimentWorker
    inspection = attempt(output / "preflight-attempts", "inspect")
    with ExperimentWorker(repo, config_path, config, base, "inspect", inspection) as worker:
        checked = worker.ready
    require(checked["space_id"] == sparse["space_id"] and checked["configuration"] == sparse["configuration"], "Actual Sparse encoder differs")
    lengths, gpu = checked["lengths"], checked["gpu"]
    require(len(lengths) == len(original), "Query length accounting differs")
    for path, checksum in checked["files"].items():
        bind(path, checksum)
    files.update(code_files(repo))
    plan = {"version": VERSION, "scope": "engineering-smoke" if smoke else "70q",
            "configuration": config,
            "input_bundle_id": sparse["input_bundle_id"], "input_manifest": manifests["dense"][1]["input_manifest"],
            "counts": sparse["counts"], "questions": questions, "profiles": profiles(config),
            "representations": {ch: m["representations"] for ch, m in
                (("dense", manifests["dense"][1]), ("bm25f", manifests["bm25f"][1]), ("sparse", sparse))},
            "files": files, "query_length_audit": {"rows": len(lengths), "maximum": max(lengths), "overlength": 0}}
    if (output / "plan.json").exists():
        require(read(output / "plan.json") == plan, "Existing P4 plan differs; use a new Output")
    else:
        atomic_json(output / "plan.json", plan)
    folder = attempt(output / "preflight-attempts", "check")
    write(folder / "report.json", {"status": "PASS", "scope": plan["scope"], "plan_binding": digest(plan),
        "elapsed_seconds": time.perf_counter() - started, "query_length_audit": plan["query_length_audit"],
        "resources": resources(), "gpu": gpu, "inference_queries": 0})
    atomic_json(output / "preflight.json", {"folder": folder.relative_to(output).as_posix(),
        "report_sha256": sha(folder / "report.json"), "plan_binding": digest(plan)})
    event(output, "preflight_pass", scope=plan["scope"], question_count=len(questions), report=str(folder / "report.json"))
    return plan


def verify_plan(repo, config, output):
    plan = read(output / "plan.json")
    require(plan["version"] == VERSION and plan["scope"] in {"70q", "engineering-smoke"}, "Unsupported P4 plan")
    require(plan["configuration"] == config, "P4 configuration changed; use a new Output")
    expected = questions_from(repo, config) if plan["scope"] == "70q" else SMOKE_QUESTIONS
    require(plan["questions"] == expected and plan["profiles"] == profiles(config), "P4 questions/profiles differ")
    require(all(plan["files"].get(k) == v for k, v in code_files(repo).items()), "P4 implementation changed; use a new Output")
    binding = FileBinding()
    binding.bind(output / "plan.json")
    for path, checksum in plan["files"].items():
        binding.bind(path, checksum)
    pointer = read(output / "preflight.json")
    path = inside(output, pointer["folder"]) / "report.json"
    require(sha(path) == pointer["report_sha256"] and read(path)["status"] == "PASS"
            and pointer["plan_binding"] == read(path)["plan_binding"] == digest(plan), "P4 preflight missing or changed")
    return plan, binding


def units(output, plan, kind):
    result = {}
    known = {q["question_id"] for q in plan["questions"]}
    expected_files = {"result.json"} if kind in GROUPS else {"result.json", "documents.json", "cases.md", "source-readback.json"}
    for path in sorted((output / "work" / kind).glob("*/complete.json")):
        rec = verify_receipt(path.parent, digest(plan))
        qid = rec["question_id"]
        require(qid in known and qid not in result and rec["kind"] == kind
                and set(rec["files"]) == expected_files, "Duplicate/foreign P4 checkpoint")
        result[qid] = path.parent
    return result


def commit_unit(output, plan, kind, qid, payload, documents=None, cases=None, readback=None, before_commit=None):
    folder = attempt(output / "work" / kind, qid)
    write(folder / "result.json", payload)
    if documents is not None:
        write(folder / "documents.json", documents)
        write(folder / "source-readback.json", readback)
        with (folder / "cases.md").open("xb") as handle:
            handle.write(cases.encode("utf-8"))
    if before_commit:
        before_commit(folder)
    receipt(folder, digest(plan), kind=kind, question_id=qid)
    return folder


def check_raw(value, question, plan, kind, depth):
    from .experiment_worker import MAPPING_KEYS
    require(value["status"] == "complete" and value["query"] == json.loads(json.dumps(asdict(QuerySpec(**question))))
            and set(value["stages"]) == GROUPS[kind], "P4 raw question/channels differ")
    for channel, stage in value["stages"].items():
        require(type(stage["elapsed_seconds"]) in {float, int} and math.isfinite(stage["elapsed_seconds"])
                and stage["elapsed_seconds"] >= 0, "Invalid channel timing")
        positions, seen = {}, set()
        for hit in stage["hits"]:
            g = hit["granularity"]
            require(g in {"micro", "macro"} and hit["channel"] == channel
                    and hit["representation_id"] == plan["representations"][channel][g]
                    and hit["question_id"] == question["question_id"] and hit["query_id"] == "original"
                    and hit["kind"] == "original" and hit["round"] == 0
                    and type(hit["input_row"]) is int and 0 <= hit["input_row"] < plan["counts"]["documents"]
                    and isinstance(hit["chunk_id"], str) and bool(hit["chunk_id"])
                    and set(hit["index_mapping"]) == set(MAPPING_KEYS)
                    and g in hit["index_mapping"]["granularities"]
                    and type(hit["rank"]) is int and 0 < hit["rank"] <= depth
                    and type(hit["raw_score"]) in {float, int} and math.isfinite(hit["raw_score"])
                    and (channel != "sparse" or hit["raw_score"] > 0), "Invalid/foreign P4 hit")
            require((g, hit["chunk_id"]) not in seen, "Repeated P4 channel/route Chunk")
            seen.add((g, hit["chunk_id"]))
            positions.setdefault(g, []).append(hit)
        for values in positions.values():
            require([h["rank"] for h in values] == list(range(1, len(values) + 1))
                    and all(a["raw_score"] >= b["raw_score"] for a, b in zip(values, values[1:])), "P4 rank/score order differs")
            if channel != "bm25f":
                require(all(a["raw_score"] != b["raw_score"] or a["input_row"] < b["input_row"]
                            for a, b in zip(values, values[1:])), "P4 neural tie order differs")
        if channel == "dense":
            require(all(len(positions.get(g, [])) == min(depth, plan["counts"][g]) for g in ("micro", "macro")),
                    "Exact Dense route depth differs")


def load_stages(question, plan, raw, config):
    stages = {}
    for kind in GROUPS:
        value = read(raw[kind][question["question_id"]] / "result.json")
        check_raw(value, question, plan, kind, config["depth"])
        stages.update(value["stages"])
    return stages


def documents_for(reader, stages):
    documents = {}
    for stage in stages.values():
        for hit in stage["hits"]:
            cid = hit["chunk_id"]
            if cid not in documents:
                documents[cid] = reader.document(hit["input_row"], cid)
            doc = documents[cid]
            require(hit["input_row"] == doc["input_row"] and all(doc["row"][k] == v for k, v in hit["index_mapping"].items()),
                    "P4 channel/source mapping differs")
    return documents


def readback_for(reader, result, documents, config):
    from ...indexing.sparse_job import source_preview
    samples = [{"input_row": documents[c]["input_row"], **documents[c]["row"]}
               for c, _ in case_ids(result, documents, config)[:config["case_candidates"]]]
    return source_preview(reader, samples)


def check_complete(output, plan, raw, comparisons):
    expected = {q["question_id"] for q in plan["questions"]}
    require(set(comparisons) == expected and all(set(v) == expected for v in raw.values()), "Incomplete P4 coverage")
    pointers = {kind: {qid: {"folder": folder.relative_to(output).as_posix(), "receipt_sha256": sha(folder / "complete.json")}
                      for qid, folder in rows.items()} for kind, rows in {**raw, "comparison": comparisons}.items()}
    complete = {"version": VERSION, "scope": plan["scope"], "status": "complete", "plan_binding": digest(plan),
                "question_count": len(expected), "units": pointers}
    if (output / "complete.json").exists():
        require(read(output / "complete.json") == complete, "P4 completion pointers differ")
    else:
        write(output / "complete.json", complete)
    return complete


def require_smoke(repo, config, plan):
    """Do not start 70Q until the operator's one-query run passed independent replay."""
    root = repo / config["smoke_output"]
    require(all((root / name).exists() for name in ("plan.json", "complete.json", "validation.json")),
            "Run Smoke with this configuration first; its independent Validate must pass before 70Q")
    smoke = read(root / "plan.json")
    require(smoke["scope"] == "engineering-smoke" and smoke["questions"] == SMOKE_QUESTIONS
            and smoke["files"] == plan["files"] and smoke["profiles"] == plan["profiles"]
            and smoke["configuration"] == config, "Smoke is absent/stale; run Smoke with this configuration first")
    pointer = read(root / "validation.json")
    report_path = inside(root, pointer["folder"]) / "report.json"
    require(sha(report_path) == pointer["report_sha256"]
            and sha(root / "complete.json") == pointer["complete_sha256"], "Smoke validation changed")
    report = read(report_path)
    require(report["status"] == "PASS" and report["scope"] == "engineering-smoke"
            and report["plan_binding"] == digest(smoke) and report["questions"] == len(SMOKE_QUESTIONS), "Smoke validation not PASS")
    raw = {kind: units(root, smoke, kind) for kind in GROUPS}
    check_complete(root, smoke, raw, units(root, smoke, "comparison"))


def run_experiment(repo, config_path, config, base, output, mode):
    from .experiment_worker import ExperimentWorker
    plan, binding = verify_plan(repo, config, output)
    if mode == "run":
        require(plan["scope"] == "70q" and not (output / "work").exists(), "Existing work/smoke plan: use Resume")
    if plan["scope"] == "70q":
        require_smoke(repo, config, plan)
    raw = {kind: units(output, plan, kind) for kind in GROUPS}
    comparisons = units(output, plan, "comparison")
    with prevent_idle_sleep():
        for kind in GROUPS:
            pending = [q for q in plan["questions"] if q["question_id"] not in raw[kind]]
            # Verify all committed raw results even if no worker is needed.
            for q in plan["questions"]:
                if q["question_id"] in raw[kind]:
                    check_raw(read(raw[kind][q["question_id"]] / "result.json"), q, plan, kind, config["depth"])
            if not pending:
                continue
            ram_guard(config)
            folder = attempt(output / "workers", kind)
            with ExperimentWorker(repo, config_path, config, base, kind, folder) as worker:
                require(worker.ready["input_bundle_id"] == plan["input_bundle_id"]
                        and worker.ready["representations"] == {ch: plan["representations"][ch] for ch in GROUPS[kind]}, "Foreign P4 worker")
                write(folder / "preparation.json", worker.ready)
                for q in pending:
                    binding.check()
                    disk_guard(output, config)
                    event(output, "query_started", group=kind, question_id=q["question_id"])
                    value = worker.search(QuerySpec(**q), config["depth"])
                    check_raw(value, q, plan, kind, config["depth"])
                    binding.check()
                    raw[kind][q["question_id"]] = commit_unit(output, plan, kind, q["question_id"], value)
                    progress(output, plan, raw, comparisons)
                    event(output, "query_committed", group=kind, question_id=q["question_id"], completed=len(raw[kind]))
        # The model/Whoosh workers have both exited before source/fusion processing.
        with InputReader(plan["input_manifest"]) as reader:
            for q in plan["questions"]:
                qid = q["question_id"]
                if qid in comparisons:
                    continue
                ram_guard(config, model_loaded=True)
                disk_guard(output, config)
                binding.check()
                stages = load_stages(q, plan, raw, config)
                documents = documents_for(reader, stages)
                event(output, "fusion_started", question_id=qid, unique_chunks=len(documents))
                result = analyze(q, stages, documents, config, plan["input_bundle_id"])
                readback = readback_for(reader, result, documents, config)
                binding.check()
                comparisons[qid] = commit_unit(output, plan, "comparison", qid, result, documents,
                    render_cases(result, documents, config), readback)
                progress(output, plan, raw, comparisons)
                event(output, "comparison_committed", question_id=qid, completed=len(comparisons))
    # Rehash immutable assets, including code, before publishing completion.
    verify_plan(repo, config, output)
    complete = check_complete(output, plan, raw, comparisons)
    progress(output, plan, raw, comparisons, complete=True)
    event(output, "experiment_complete", scope=plan["scope"], questions=len(plan["questions"]))
    from .experiment_report import report_job
    report_job(output, plan, raw, comparisons, config)
    return complete


def progress(output, plan, raw, comparisons, *, complete=False):
    atomic_json(output / "progress.json", {"status": "complete" if complete else "running", "scope": plan["scope"],
        "total_questions": len(plan["questions"]), "committed": {k: len(v) for k, v in raw.items()}, "comparisons": len(comparisons)})


def validate_job(repo, config, output):
    plan, binding = verify_plan(repo, config, output)
    raw = {kind: units(output, plan, kind) for kind in GROUPS}
    comparisons = units(output, plan, "comparison")
    require((output / "complete.json").exists(), "Experiment incomplete: use Resume")
    complete = check_complete(output, plan, raw, comparisons)
    with InputReader(plan["input_manifest"]) as reader:
        for q in plan["questions"]:
            stages = load_stages(q, plan, raw, config)
            documents = documents_for(reader, stages)
            folder = comparisons[q["question_id"]]
            require(read(folder / "documents.json") == documents, "Saved source originals differ")
            saved = read(folder / "result.json")
            replay = analyze(q, stages, documents, config, plan["input_bundle_id"])
            require(stable_comparison(replay) == stable_comparison(saved), "P4 fusion/diagnostic replay differs")
            require((folder / "cases.md").read_bytes() == render_cases(saved, documents, config).encode("utf-8"), "Source case rendering differs")
            require(read(folder / "source-readback.json") == readback_for(reader, saved, documents, config), "Sampled parent/Clean readback differs")
            event(output, "question_validated", question_id=q["question_id"])
    binding.check()
    if (output / "report.json").exists():
        pointer = read(output / "report.json")
        report_folder = inside(output, pointer["folder"])
        require(sha(report_folder / "complete.json") == pointer["receipt_sha256"], "Report pointer changed")
        verify_receipt(report_folder, digest(plan))
    report = {"status": "PASS", "scope": plan["scope"], "questions": len(plan["questions"]),
              "plan_binding": digest(plan), "complete_sha256": sha(output / "complete.json"),
              "checks": ["receipts", "all raw route bindings", "all hit source originals", "12-profile replay", "source case text"],
              "semantic_quality": "UNKNOWN; original-text review required", "resources": resources()}
    folder = attempt(output / "validations", "check")
    write(folder / "report.json", report)
    atomic_json(output / "validation.json", {"folder": folder.relative_to(output).as_posix(),
        "report_sha256": sha(folder / "report.json"), "complete_sha256": sha(output / "complete.json")})
    event(output, "validation_pass", scope=plan["scope"], questions=len(plan["questions"]))
    return complete


def report_existing(repo, config, output):
    from .experiment_report import report_job
    plan, _ = verify_plan(repo, config, output)
    raw = {kind: units(output, plan, kind) for kind in GROUPS}
    comparisons = units(output, plan, "comparison")
    require((output / "complete.json").exists(), "Experiment incomplete: use Resume")
    check_complete(output, plan, raw, comparisons)
    for q in plan["questions"]:
        load_stages(q, plan, raw, config)
    return report_job(output, plan, raw, comparisons, config)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "smoke", "run", "resume", "validate", "report", "status"))
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[4]))
    parser.add_argument("--config", default="config/recall-experiment-v1.json")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    config_path = (repo / args.config).resolve()
    config, base = settings(repo, config_path)
    output = (repo / (args.output or config["smoke_output" if args.mode == "smoke" else "output"])).resolve()
    guard(repo, config, base, output)
    if args.mode == "status":
        value = {k: read(output / k) for k in ("progress.json", "preflight.json", "report.json", "validation.json") if (output / k).exists()}
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    with build_lock(output), offline_only():
        folder = attempt(output / "runs", args.mode)
        with (folder / "stdout.log").open("x", encoding="utf-8") as outlog, (folder / "stderr.log").open("x", encoding="utf-8") as errlog:
            with redirect_stdout(Tee(sys.stdout, outlog)), redirect_stderr(Tee(sys.stderr, errlog)):
                try:
                    event(output, "invocation_started", mode=args.mode, run_folder=str(folder))
                    if args.mode in {"preflight", "smoke"}:
                        require(args.mode != "smoke" or output != (repo / config["output"]).resolve(), "Smoke requires a separate Output")
                        result = prepare(repo, config_path, config, base, output, smoke=args.mode == "smoke")
                        if args.mode == "smoke":
                            result = run_experiment(repo, config_path, config, base, output, "resume")
                            validate_job(repo, config, output)
                    elif args.mode in {"run", "resume"}:
                        result = run_experiment(repo, config_path, config, base, output, args.mode)
                    elif args.mode == "validate":
                        result = validate_job(repo, config, output)
                    else:
                        result = report_existing(repo, config, output)
                    write(folder / "result.json", {"status": "PASS", "mode": args.mode, "result_sha256": digest(result)})
                except BaseException as exc:
                    failure = {"mode": args.mode, "type": type(exc).__name__, "message": str(exc), "resources": resources()}
                    write(folder / "failure.json", failure)
                    event(output, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", **failure)
                    traceback.print_exc()
                    return 130 if isinstance(exc, KeyboardInterrupt) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
