"""Foreground standalone supervisor for the Clean/Chunk indexing experiment."""
import argparse
import os
from pathlib import Path
import subprocess
import time
import traceback

from ..build_runtime import atomic_json
from ..evidence_io import digest, read, require, sha
from .benchmark_io import (VERSION, attempt, build_lock, code_binding, completed_input, disk_guard,
                           dump_lines, event, offline_only, prevent_idle_sleep, ram_guard, child_process)
from .inputs import ChunkInputSource, prepare_inputs


def load_config(path):
    value = read(path)
    require(value["version"] == VERSION, "Unsupported benchmark configuration")
    require(len(value["models"]) == 3 and len({m["key"] for m in value["models"]}) == 3, "Three unique Dense models required")
    for model in value["models"]:
        require(model["key"] and all(c.isalnum() or c in "-." for c in model["key"]), "Unsafe model directory key")
        require(model["pooling"] in {"last_nonpadding_token", "cls_token"}
                and model["padding_side"] in {"left", "right"}
                and model["compute_dtype"] in {"float16", "bfloat16"}, "Unsupported encoder contract")
    for key in ("shard_rows", "max_batch", "padded_token_budget", "smoke_documents"):
        require(type(value[key]) is int and value[key] > 0, "Invalid bounded resource setting " + key)
    require(1 <= value["smoke_documents"] <= 8 and value["max_batch"] <= 8, "Resource preflight must remain bounded")
    require(value["minimum_free_gib"] > 0 and value["minimum_available_ram_gib"] > 0
            and value["minimum_running_ram_gib"] > 0, "Resource guards must be positive")
    require(value["bm25f"]["commit_rows"] > 0 and value["bm25f"]["writer_mb"] > 0, "Invalid Whoosh write limits")
    return value


def prepare(root, repo, config, existing=None, allow_subset=False):
    pointer = root / "input.json"
    if pointer.exists():
        path = Path(read(pointer)["manifest"])
        require(sha(path) == read(pointer)["manifest_sha256"], "Completed input manifest changed")
        completed_input(path)
        return path
    if existing:
        path = Path(existing).resolve()
        completed_input(path)
        require(allow_subset or read(path)["selection"] is None, "Subset bundle requires explicit --allow-subset")
    else:
        ram_guard(config)
        event(root, "source_validation_start", manifest=str(repo / config["chunk_manifest"]))
        source = ChunkInputSource(repo / config["chunk_manifest"])
        # Input projection and all four indexes, with headroom for Whoosh commit temporaries.
        count = source.report["profiles"]["balanced"]["chunks"]
        event(root, "source_validation_complete", chunks=count, parents=len(source.parents), graphs=len(source.graphs))
        disk_guard(root, config, additional_bytes=(source.root / "chunks-balanced.jsonl").stat().st_size * 3
                   + count * 1024 * 4 * 3)
        path = None
        for prior in sorted((root / "input-attempts").glob("*/manifest.json")):
            if read(prior)["input_snapshot_id"] == source.input_snapshot_id and read(prior)["selection"] is None:
                completed_input(prior)
                path = prior
                break
        if path is None:
            # Export remains create-only. Retain any interrupted export and restart in a fresh attempt.
            container = attempt(root / "input-attempts", "export")
            path = container / "bundle/manifest.json"
            started = time.monotonic()
            def progress(counts):
                event(root, "input_export_progress", counts=counts, total=count, elapsed_seconds=time.monotonic() - started)
            prepare_inputs(source, path.parent, progress=progress)
        source.verify_unchanged()
    atomic_json(pointer, {"manifest": str(path.resolve()), "manifest_sha256": sha(path),
                          "input_bundle_id": read(path)["input_bundle_id"]})
    return path


def run_child(python, action, *, repo, root, input_manifest, config_path, supervisor, model=None,
              questions=None, top_k=20):
    executable = (repo / python).resolve()
    require(executable.is_file(), "Missing prepared runtime " + str(executable))
    log = attempt(supervisor / "logs", action + ("-" + model if model else "")) / "console.log"
    command = [str(executable), "-u", "-m", "genshin_corpus.indexing.benchmark_worker", action,
               "--repo", str(repo), "--config", str(config_path), "--input", str(input_manifest), "--root", str(root),
               "--top-k", str(top_k)]
    if model:
        command += ["--model", model]
    if questions:
        command += ["--questions", str(questions)]
    environment = {**os.environ, "PYTHONPATH": str(repo / "src"), "PYTHONUNBUFFERED": "1",
                   "PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "TOKENIZERS_PARALLELISM": "false", "HF_HUB_DISABLE_TELEMETRY": "1"}
    environment["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    started = time.monotonic()
    event(supervisor, "stage_start", action=action, model=model, log=str(log))
    with log.open("wb") as output, child_process(command, cwd=repo, env=environment,
                                               stdout=output, stderr=subprocess.STDOUT) as process:
        try:
            with log.open("r", encoding="utf-8", errors="replace") as tail:
                heartbeat = time.monotonic()
                while process.poll() is None:
                    for line in tail:
                        print(line.rstrip(), flush=True)
                    if time.monotonic() - heartbeat >= 20:
                        event(supervisor, "worker_running", action=action, model=model, child_pid=process.pid,
                              elapsed_seconds=time.monotonic() - started)
                        heartbeat = time.monotonic()
                    time.sleep(0.5)
                for line in tail:
                    print(line.rstrip(), flush=True)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    result = {"action": action, "model": model, "exit_code": process.returncode,
              "elapsed_seconds": time.monotonic() - started, "log": str(log)}
    event(supervisor, "stage_exit", **result)
    return result


def execute(args):
    repo, root = Path(args.repo).resolve(), Path(args.output).resolve()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    protected = (repo / name for name in (".local", "src", "config", "tests", "docs", "scripts", ".git"))
    require(root != repo and not any(root.is_relative_to(path) for path in protected), "Unsafe experiment output directory")
    require(args.mode != "resume" or (root / "experiment.json").is_file(), "Resume requires an existing experiment")
    expected = {"version": VERSION, "configuration": config, "code_sha256": code_binding(repo),
                "chunk_manifest_sha256": sha(repo / config["chunk_manifest"]),
                "input_override": str(Path(args.input_manifest).resolve()) if args.input_manifest else None,
                "allow_subset": args.allow_subset}
    with build_lock(root), prevent_idle_sleep(), offline_only():
        plan_path = root / "experiment.json"
        if plan_path.exists():
            require(read(plan_path) == expected, "Experiment source/configuration/code changed. Use original checkout/config or a new output directory")
        else:
            allowed = {"build.lock"}
            require({p.name for p in root.iterdir()} <= allowed, "Output directory belongs to another build")
            require(args.mode not in {"resume", "validate"}, "Existing experiment required")
            atomic_json(plan_path, expected)
        if args.mode == "run":
            require(not (root / "run-started.json").exists(), "Run already started. Use Resume or Validate")
            atomic_json(root / "run-started.json", {"started": time.time()})
        disk_guard(root, config)
        event(root, "experiment_start", mode=args.mode)
        if args.mode == "validate":
            require((root / "input.json").is_file(), "No completed input package")
            input_manifest = Path(read(root / "input.json")["manifest"])
            completed_input(input_manifest)
        else:
            input_manifest = prepare(root, repo, config, args.input_manifest, args.allow_subset)
        pointer = read(root / "input.json")
        require(sha(input_manifest) == pointer["manifest_sha256"]
                and read(input_manifest)["input_bundle_id"] == pointer["input_bundle_id"], "Input pointer changed")
        bundle = read(input_manifest)
        if (root / "complete.json").exists():
            published = read(root / "complete.json")
            require(published["input_bundle_id"] == bundle["input_bundle_id"], "Published experiment input differs")
            require(sha(Path(published["report"])) == published["report_sha256"], "Published experiment report changed")
            expected_indexes = {**{m["key"]: sha(root / "dense" / m["key"] / "manifest.json") for m in config["models"]},
                                "bm25f": sha(root / "bm25f/manifest.json")}
            require(published["indexes"] == expected_indexes, "Published index manifest changed")
        # Bind an externally supplied sample or full package to the approved formal Chunk/Clean snapshot.
        require(bundle["binding"]["chunk_manifest_sha256"] == sha(repo / config["chunk_manifest"]), "Input binds a different Chunk source")
        require(args.allow_subset or bundle["selection"] is None, "Full experiment requires all source records")
        stages = []
        kwargs = dict(repo=repo, input_manifest=input_manifest, config_path=config_path, supervisor=root)

        def child(python, action, output, model=None, questions=None):
            result = run_child(python, action, root=output, model=model, questions=questions,
                               top_k=args.top_k, **kwargs)
            stages.append(result)
            atomic_json(root / "progress.json", {"mode": args.mode, "stages": stages})
            return result["exit_code"] == 0

        ready = {}
        if args.mode != "validate":
            for model in config["models"]:
                ready[model["key"]] = child(model["python"], "dense-preflight", root / "dense" / model["key"], model["key"])
            lexical_ready = child(config["bm25f"]["python"], "bm25f-preflight", root / "bm25f")
            if args.mode == "preflight":
                require(code_binding(repo) == expected["code_sha256"] and read(config_path) == config,
                        "Implementation changed during preflight")
                report = {"status": "PASS" if all(s["exit_code"] == 0 for s in stages) else "BLOCKED",
                          "scope": "subset" if bundle["selection"] is not None else "full", "stages": stages,
                          "input_bundle_id": bundle["input_bundle_id"]}
                atomic_json(root / "preflight.json", report)
                return 0 if report["status"] == "PASS" else 1
            for model in config["models"]:
                if ready[model["key"]]:
                    child(model["python"], "dense-build", root / "dense" / model["key"], model["key"])
            if lexical_ready:
                child(config["bm25f"]["python"], "bm25f-build", root / "bm25f")
        for model in config["models"]:
            child(model["python"], "dense-validate", root / "dense" / model["key"], model["key"])
        child(config["bm25f"]["python"], "bm25f-validate", root / "bm25f")
        questions = []
        if args.questions:
            from .benchmark_io import lines
            questions = list(lines(args.questions))
        elif args.query:
            questions = [{"question_id": "operator-query", "question": args.query}]
        elif args.mode != "validate":
            questions = [{"question_id": "functional-smoke", "question": "坎瑞亚与深渊有什么关系？"}]
        if questions:
            require(all(isinstance(q.get("question"), str) and q["question"].strip()
                        and isinstance(q.get("question_id"), str) and q["question_id"] for q in questions), "Invalid question set")
            require(len({q["question_id"] for q in questions}) == len(questions), "Duplicate question ID")
            questions_file = attempt(root / "questions", "set") / "questions.jsonl"
            dump_lines(questions_file, questions)
            for model in config["models"]:
                if (root / "dense" / model["key"] / "manifest.json").exists():
                    child(model["python"], "dense-query", root / "dense" / model["key"], model["key"], questions_file)
            if (root / "bm25f/manifest.json").exists():
                child(config["bm25f"]["python"], "bm25f-query", root / "bm25f", questions=questions_file)
        require(code_binding(repo) == expected["code_sha256"] and read(config_path) == config, "Implementation changed during experiment")
        report = {"version": VERSION, "status": "PASS" if all(s["exit_code"] == 0 for s in stages) else "BLOCKED",
                  "scope": "subset" if bundle["selection"] is not None else "full", "input_bundle_id": bundle["input_bundle_id"],
                  "counts": bundle["counts"], "stages": stages,
                  "elapsed_stage_seconds": sum(s.get("elapsed_seconds", 0) for s in stages),
                  "disk": disk_guard(root, config)}
        report_path = attempt(root / "reports", args.mode) / "report.json"
        atomic_json(report_path, report)
        atomic_json(root / "latest-report.json", {"report": str(report_path), "status": report["status"]})
        if report["status"] == "PASS" and args.mode != "validate":
            atomic_json(root / "complete.json", {"version": VERSION, "scope": report["scope"],
                                                 "input_bundle_id": bundle["input_bundle_id"],
                                                 "report": str(report_path), "report_sha256": sha(report_path),
                                                 "indexes": {**{m["key"]: sha(root / "dense" / m["key"] / "manifest.json")
                                                                for m in config["models"]},
                                                             "bm25f": sha(root / "bm25f/manifest.json")}})
        event(root, "experiment_finished", status=report["status"], report=str(report_path))
        return 0 if report["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "run", "resume", "validate"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--config", default="config/index-benchmark-v1.json")
    parser.add_argument("--output", default="data/retrieval/index-benchmark-v1-20261109")
    parser.add_argument("--input-manifest")
    parser.add_argument("--allow-subset", action="store_true")
    parser.add_argument("--query")
    parser.add_argument("--questions")
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args(argv)
    require(args.top_k > 0, "TopK must be positive")
    try:
        return execute(args)
    except BaseException as exc:
        # Preserve the original error even if disk-full prevents writing an error file.
        try:
            root = Path(args.output)
            if (root / "experiment.json").is_file() and "another process owns" not in str(exc):
                folder = attempt(root / "failures", "supervisor")
                atomic_json(folder / "error.json", {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
        except OSError:
            pass
        traceback.print_exc()
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
