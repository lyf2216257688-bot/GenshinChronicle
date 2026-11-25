"""Supervise the sealed, local-only versioned corpus comparison."""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from genshin_corpus.build_runtime import atomic_json
from genshin_corpus.collector.run_store import create_json
from genshin_corpus.evidence_io import copy, inside, read, require, seal, sha, verify
from genshin_corpus.indexing.benchmark_io import GIB, child_process, prevent_idle_sleep, resources
from genshin_corpus.model_calls import locked
from genshin_corpus.retrieval.clean_based.query_v2 import ResearchQuery

from snapshot_acceptance import register_cases


VERSION = "gc-versioned-corpus-execution-1"
MODULE = "genshin_corpus.retrieval.clean_based.snapshot"
CLEAN = "genshin_corpus.clean.snapshot_build"
CHUNK = "genshin_corpus.chunking.snapshot_build"
RAW = "data/raw/mihoyo_obc/zh-cn"
OLD = RAW + "/p01eb_full_20260824"
NEW = RAW + "/p01eb_full_20261124"
OLD_CLEAN = "data/clean/mihoyo_obc/zh-cn/clean-v1-full-20261106/metadata/manifest.json"
OLD_CHUNK = "data/retrieval/clean-chunking-full-v2-1-20261108/manifest.json"
RANGES = "data/retrieval/incremental-chunk-baseline-20261124.json"
RULES = "config/chunking-full-20261107-balanced.json"
DIFF = "data/retrieval/incremental-raw-diff-20261125.json"
QUESTIONS = ("冻原的枪匠", "风行迷踪", "挪德卡莱生态短片 - 月亮的馈赠", "阿托【西风骑士】",
             "坎瑞亚与深渊有什么关系？", "雷电将军的传说任务里发生了什么？")
PINS = {
    OLD + "/metadata/manifest.json": "b7b519c94fd5001cc3b7128ea59bd8117d9e3a88b7a26db71431349010f26978",
    NEW + "/metadata/manifest.json": "11922535ef653966002f4f05f839f7be1a9f703f83d0cdbf0e7951086632f8c5",
    NEW + "/metadata/inventory.json": "890061a4af470f0930696d7eb047f585fd8582d7cae804612463dbc2e4c8740c",
    OLD_CLEAN: "f31b5d5252999046e611474f24a1504fbe3d00ef030846484c5c1b455b955faf",
    RANGES: "bfdab210f0d1fed0f920847d69bff08760fbca1017615362751f32c48fea6d40",
    DIFF: "48833b87601d76b9f0a27cf8335729a6adb2cce332f49c781beaa158b89b127b",
}
SCRIPTS = ("run-incremental-corpus.ps1", "snapshot_full_run.py", "snapshot_acceptance.py", "verify_snapshot_observations.py")


def now():
    return datetime.now(timezone.utc).isoformat()


def pinned(repo, files):
    for name, checksum in files.items():
        require(sha(inside(repo, name)) == checksum, "Bound input differs: " + name)


def runtime(repo, binary):
    # Record installed package versions and the actual interpreter used by the venv.
    code = ("import hashlib,importlib.metadata as m,json,sys,sysconfig;"
            "print(json.dumps(dict(executable=sys.executable,base=sys._base_executable,version=sys.version,"
            "platform=sysconfig.get_platform(),packages=sorted((d.metadata['Name'],d.version,"
            "hashlib.sha256((d.read_text('RECORD') or '').encode()).hexdigest()) for d in m.distributions()))))")
    value = json.loads(subprocess.check_output([repo / binary, "-B", "-c", code], cwd=repo, timeout=35))
    base = Path(value["base"])
    files = {repo / binary, base, (repo / binary).parent.parent / "pyvenv.cfg", *base.parent.glob("python3*.dll")}
    value["files"] = {str(path): sha(path) for path in files}
    return value


def freeze(repo, execution):
    dataset = {"old": OLD, "new": NEW, "old_clean": OLD_CLEAN, "ranges": RANGES,
               "rules": Path(RULES).name}
    pinned(repo, PINS)
    report = "data/retrieval/incremental-new-raw-equivalence-20261125/report.json"
    require(read(repo / report)["status"] == "PASS", "Independent new-Raw evidence is not complete")
    metadata = read(repo / OLD_CHUNK)
    checks = {**PINS, OLD_CHUNK: sha(repo / OLD_CHUNK), report: sha(repo / report)}
    publish_execution(repo, execution, dataset, checks, metadata["tokenizers"], list(QUESTIONS), register_cases(repo, repo / DIFF))


def publish_execution(repo, execution, dataset, checks, tokenizers, quick, acceptance, *, smoke=False):
    retrieval = read(repo / "config/clean-retrieval-v1.json")
    reranking = read(repo / "config/reranking-selected-v1.json")
    paths = {"dense": retrieval["dense_python"], "lexical": retrieval["lexical_python"], "reranker": reranking["python"]}
    config = sorted(path for path in (repo / "config").glob("*.json"))
    source = sorted(path for path in (repo / "src").rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    scripts = [repo / "scripts/retrieval" / name for name in SCRIPTS]
    require(smoke or [item["sha256"] for item in tokenizers] == [
        "def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a",
        "69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15"], "Unexpected tokenizer set")
    checks = checks.copy()
    for item in tokenizers:
        checks[Path(item["path"]).resolve().relative_to(repo).as_posix()] = item["sha256"]
    selection = read(repo / retrieval["selection"])
    for channel in ("dense", "lexical"):
        item = selection[channel]
        checks[item["manifest"]] = item["manifest_sha256"]
    pinned(repo, checks)
    runtime_ids = {name: runtime(repo, interpreter) for name, interpreter in paths.items()}
    execution.mkdir(parents=True, exist_ok=False)
    for path in source + config + scripts:
        copy(path, execution / path.relative_to(repo))
    require(all(sha(path) == sha(execution / path.relative_to(repo)) for path in source + config + scripts),
            "Implementation changed while freezing")
    create_json(execution / "questions.json", {"quick": quick, "extended": acceptance})
    create_json(execution / "quick-questions.json", quick)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, timeout=15).strip()
    seal(execution, {"version": VERSION, "created_at": now(), "repo": str(repo), "git_parent": head,
                     "inputs": checks, "runtime_paths": paths, "runtimes": runtime_ids, "dataset": dataset,
                     "scope": "smoke" if smoke else "official-comparison", "gpu_identity": gpu_identity(),
                     "tokenizers": [item["path"] for item in tokenizers],
                     "limits": {"start_disk_gib": 1 if smoke else 40, "running_disk_gib": .25 if smoke else 6,
                                "start_ram_gib": 1.5 if smoke else 6,
                                "running_ram_gib": .75, "start_gpu_mib": 2048, "heartbeat_seconds": 10}})
    print(sha(execution / "complete.json"), flush=True)


@contextmanager
def readonly_files(paths):
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    invalid = ctypes.c_void_p(-1).value
    with ExitStack() as stack:
        for path in paths:
            handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
            require(handle not in (None, invalid), "Could not protect live configuration or sealed file: " + str(path))
            stack.callback(api.CloseHandle, handle)
        yield


def gpu_memory():
    query = subprocess.check_output(["nvidia-smi", "--query-gpu=index,memory.total,memory.free",
                                     "--format=csv,noheader,nounits"], text=True, timeout=12)
    lines = [tuple(map(int, row.split(","))) for row in query.splitlines() if row.strip()]
    require(len(lines) == 1 and lines[0][0] == 0, "The sealed run expects one NVIDIA device")
    return {"total_mib": lines[0][1], "free_mib": lines[0][2]}


def gpu_identity():
    return subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version,uuid", "--format=csv,noheader"],
                                   text=True, timeout=12).strip()


@dataclass(frozen=True)
class Stage:
    name: str
    python: str
    args: tuple
    outputs: dict
    stable_dir: Path | None = None
    gpu: bool = False
    recover: tuple | None = None


class Run:
    def __init__(self, repo, execution, root, seal_sha, metadata, owner):
        self.repo, self.execution, self.root = repo, execution, root
        self.sha, self.settings, self.owner = seal_sha, metadata, owner
        self.questions = read(execution / "questions.json")
        self.artifacts = root / "receipts"
        self.attempts = root / "attempts"
        self.limits = metadata["limits"]
        self.start = time.monotonic()
        self.env = dict(os.environ, PYTHONPATH=str(execution / "src"), PYTHONDONTWRITEBYTECODE="1",
                        PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        # The venv's own CPU and CUDA data stay in the pinned installation.
        for var in ("PYTHONHOME", "CUDA_VISIBLE_DEVICES", "PYTHONPYCACHEPREFIX"):
            self.env.pop(var, None)

    def measure(self, *, gpu=False):
        volume = self.root if self.root.exists() else self.repo
        return {"disk_free_bytes": shutil.disk_usage(volume).free, **resources(),
                **({"gpu": gpu_memory()} if gpu else {})}

    def guard(self, *, fresh=False, gpu=False, recorded=False):
        measured = self.measure(gpu=gpu)
        limits = self.limits
        minimum_disk = limits["start_disk_gib" if fresh else "running_disk_gib"]
        minimum_ram = limits["start_ram_gib" if fresh else "running_ram_gib"]
        reasons = []
        if measured["disk_free_bytes"] < minimum_disk * GIB:
            reasons.append(f"free disk below {minimum_disk} GiB")
        if measured["available_ram_bytes"] < minimum_ram * GIB:
            reasons.append(f"available RAM below {minimum_ram} GiB")
        if gpu and recorded and measured["gpu"]["free_mib"] < limits["start_gpu_mib"]:
            reasons.append(f"free VRAM below {limits['start_gpu_mib']} MiB")
        return measured, reasons

    def preflight(self, *, fresh):
        pinned(self.repo, self.settings["inputs"])
        require(gpu_identity() == self.settings["gpu_identity"], "GPU or driver changed since the execution was frozen")
        for name, path in self.settings["runtime_paths"].items():
            require(runtime(self.repo, path) == self.settings["runtimes"][name], "Runtime identity changed: " + name)
        measured, reasons = self.guard(fresh=fresh, gpu=True, recorded=True)
        print(json.dumps({"phase": "preflight", "status": "BLOCKED" if reasons else "PASS",
                          "execution_sha256": self.sha, "resources": measured, "reasons": reasons}), flush=True)
        return not reasons

    def path_config(self, name):
        return self.execution / "config" / name

    def emit(self, event, **fields):
        value = {"at": now(), "event": event, "controller_pid": os.getpid(),
                 "session_elapsed_seconds": round(time.monotonic() - self.start, 1), **fields}
        print(json.dumps(value, ensure_ascii=False), flush=True)
        with (self.root / "progress.jsonl").open("a", encoding="utf8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")

    def receipt(self, stage):
        path = self.artifacts / (stage.name + ".json")
        if not path.exists():
            return None
        value = read(path)
        require(value["execution_sha256"] == self.sha and value["stage"] == stage.name
                and set(value["outputs"]) == set(stage.outputs), "Foreign stage receipt")
        files = {}
        for key, ref in value["outputs"].items():
            artifact = inside(self.root, ref["path"])
            require(sha(artifact) == ref["sha256"], "Published stage output changed: " + str(artifact))
            files[key] = artifact
        return files

    def supervise(self, name, command, attempt, *, gpu, stage_dir):
        import psutil

        started = time.monotonic()
        with (attempt / "stdout.log").open("xb") as log, (attempt / "stderr.log").open("xb") as errors, \
                child_process(command, cwd=self.execution, env=self.env, stdout=log, stderr=errors) as process, \
                (attempt / "stdout.log").open("rb") as incoming:
            last, count, position = 0.0, 0, 0
            minima = {}
            while True:
                result = process.poll()
                if self.owner and not psutil.pid_exists(self.owner):
                    raise KeyboardInterrupt("PowerShell owner exited")
                incoming.seek(position)
                for line in incoming:
                    if not line.endswith(b"\n") and result is None:
                        break
                    position = incoming.tell()
                    count += 1
                    if count % 100 == 0 or result is not None:
                        print(line.decode("utf8", errors="replace").rstrip()[:360], flush=True)
                elapsed = time.monotonic() - started
                if elapsed - last >= self.limits["heartbeat_seconds"] or result is not None:
                    measured, reasons = self.guard(gpu=gpu)
                    for field in ("disk_free_bytes", "available_ram_bytes"):
                        minima[field] = min(minima.get(field, measured[field]), measured[field])
                    if gpu:
                        free = measured["gpu"]["free_mib"]
                        minima["gpu_free_mib"] = min(minima.get("gpu_free_mib", free), free)
                    saved_blocks = sum(1 for channel in ("dense", "sparse") for _ in (stage_dir / channel).glob("*/complete.json"))
                    saved_buckets = sum(1 for route in ("micro", "macro") for _ in stage_dir.glob(route + "-??/complete.json"))
                    self.emit("heartbeat", stage=name, elapsed_seconds=round(elapsed, 1), stdout_lines=count,
                              saved_blocks=saved_blocks, saved_buckets=saved_buckets, resources=measured)
                    last = elapsed
                    if result is None:
                        require(not reasons, "Resource guard paused stage: " + ", ".join(reasons))
                if result is not None:
                    require(result == 0, f"{name} exited {result}. Inspect {attempt / 'stderr.log'}")
                    return {"elapsed_seconds": round(elapsed, 3), "minimum_available": minima}
                time.sleep(1)

    def stage(self, stage):
        files = self.receipt(stage)
        if files is not None:
            self.emit("reused", stage=stage.name)
            return files
        attempt = self.attempts / stage.name / uuid4().hex[:10]
        attempt.mkdir(parents=True)
        stage_dir = stage.stable_dir if stage.stable_dir is not None else attempt / "output"
        targets = {key: stage_dir / name for key, name in stage.outputs.items()}
        recovered = stage.recover is not None and all(path.is_file() for path in targets.values())
        args = stage.recover if recovered else stage.args
        args = [str(value).replace("{output}", str(stage_dir)) for value in args]
        if args[:2] == ["-m", MODULE]:
            args[2:2] = ["--repo", str(self.repo)]
        command = [str(self.repo / self.settings["runtime_paths"][stage.python]), "-B", "-u", *args]
        measured, reasons = self.guard(gpu=stage.gpu and not recovered, recorded=True)
        create_json(attempt / "invocation.json", {"stage": stage.name, "command": command,
                                                  "execution_sha256": self.sha, "resources": measured})
        try:
            require(not reasons, "Stage preflight paused: " + ", ".join(reasons))
            self.emit("started", stage=stage.name, log=str(attempt / "stdout.log"), recovered_publication=recovered)
            metrics = self.supervise(stage.name, command, attempt, gpu=stage.gpu and not recovered, stage_dir=stage_dir)
            files = {}
            for name, path in targets.items():
                require(path.is_file(), "Expected stage artifact missing: " + str(path))
                files[name] = {"path": path.relative_to(self.root).as_posix(), "sha256": sha(path)}
            create_json(self.artifacts / (stage.name + ".json"), {"stage": stage.name, "outputs": files,
                        "execution_sha256": self.sha, "attempt": attempt.relative_to(self.root).as_posix(), **metrics})
            self.emit("completed", stage=stage.name, **metrics)
            return targets
        except BaseException as exc:
            self.emit("paused", stage=stage.name, reason=str(exc) or type(exc).__name__, attempt=str(attempt))
            raise

    def pair(self, *, build):
        releases = []
        source = self.settings["dataset"]
        for mode in ("incremental", "full"):
            arm = self.root / mode
            clean, chunk = (arm / part / "manifest.json" for part in ("clean", "chunk"))
            step = self.stage if build else self.receipt
            previous = ("--previous", self.repo / source["ranges"]) if mode == "incremental" else ()

            def item(part, module, args, directory, *, gpu=False, recovery=None, python="dense", finish="manifest.json"):
                files = step(Stage(f"{mode}-{part}", python, ("-m", module, *args), {"main": finish}, directory, gpu, recovery))
                require(files is not None, f"{mode}-{part} has no validated stage receipt. Complete Run or Resume first.")
                return files["main"]

            item("clean", CLEAN, ("build", "--mode", mode, "--current-raw", self.repo / source["new"], "--previous-raw",
                                 self.repo / source["old"], "--previous-clean", self.repo / source["old_clean"], "--output", clean.parent),
                 clean.parent, recovery=("-m", CLEAN, "validate", clean))
            item("chunk", CHUNK, ("build", "--mode", mode, "--clean", clean, "--selection",
                                 self.path_config(source["rules"]), "--qwen-tokenizer", self.settings["tokenizers"][0],
                                 "--bge-tokenizer", self.settings["tokenizers"][1], "--output", chunk.parent, *previous),
                 chunk.parent, recovery=("-m", CHUNK, "validate", chunk))
            inputs = item("inputs", "genshin_corpus.indexing.snapshot_inputs", (chunk, "{output}"), None)
            vectors = item("vectors", MODULE, ("build-vectors", "--input", inputs, "--output", arm / "vectors", "--sparse"),
                           arm / "vectors", gpu=True)
            bm25f = item("bm25f", MODULE, ("build-bm25f", "--input", inputs, "--output", arm / "bm25f"),
                         arm / "bm25f", python="lexical")
            releases.append(item("release", MODULE, ("package", "--input", inputs, "--vectors", vectors,
                                "--bm25f", bm25f, "--output", "{output}/release.json"), None, finish="release.json"))
        return releases

    def quick(self, releases, *, recheck=False):
        left, right = releases
        tag = "-" + uuid4().hex[:8] if recheck else ""
        for name, path in zip(("left", "right"), releases, strict=True):
            release = read(path)
            source = read(release["input"]["path"])
            for layer, module in (("clean", CLEAN), ("chunk", CHUNK)):
                self.stage(Stage(f"validate-{name}-{layer}{tag}", "dense", (str(self.execution / "scripts/retrieval/snapshot_full_run.py"),
                                 "validate-layer", "--module", module, "--input", source[layer]["path"],
                                 "--output", "{output}/validation.json"), {"main": "validation.json"}))
        for channel, python in (("vectors", "dense"), ("bm25f", "lexical")):
            extra = ("--queries", self.execution / "quick-questions.json") if channel == "bm25f" else ()
            self.stage(Stage("compare-" + channel + tag, python, ("-m", MODULE, "compare", "--left", left, "--right", right,
                             "--channel", channel, "--output", "{output}/parity.json", *extra), {"main": "parity.json"}))
        for n, text in enumerate(self.questions["quick"], 1):
            for profile in ("S0", "S2"):
                case = f"quick-{n:02d}-{profile}"
                query = self.root / "queries" / (case + ".json")
                spec = ResearchQuery("incremental-parity", case, "original", *([text] * 4), combination=profile).value()
                if not query.exists():
                    create_json(query, spec)
                require(read(query) == spec, "Quick case changed")
                pair = []
                for side, release in zip(("left", "right"), releases, strict=True):
                    result = self.stage(Stage(f"{case}-{side}", "dense", ("-m", MODULE, "query", "--release", release,
                                        "--query-json", query, "--rerank", "--output", "{output}"),
                                        {"main": "run.json", "candidates": "candidates.json", "ranked": "ranked.json"}, gpu=True))
                    pair.append(result)
                a, b = pair
                require(sha(a["candidates"]) == sha(b["candidates"]), "Quick candidate difference: " + case)
                order = lambda path: [item["chunk_id"] for item in read(path)["candidates"]]
                require(order(a["ranked"]) == order(b["ranked"]), "Quick reranking difference: " + case)
                evidence = [arm["main"].with_name("first-evidence.json") for arm in pair]
                require(evidence[0].is_file() == evidence[1].is_file()
                        and (not evidence[0].is_file() or sha(evidence[0]) == sha(evidence[1])), "Quick evidence difference: " + case)

    def extended(self, release):
        from snapshot_acceptance import summarize

        paths = []
        for case in self.questions["extended"]:
            script = self.execution / "scripts/retrieval/snapshot_acceptance.py"
            result = self.stage(Stage("review-" + case["id"], "dense", (str(script), "--repo", self.repo,
                                "--release", release, "--cases", self.execution / "questions.json", "--case", case["id"],
                                "--output", "{output}"), {"main": "case.json"}, gpu=True))
            paths.append(result["main"])
        atomic_json(self.root / "extended-review.json", summarize(paths))


def _validate_layer():
    parser = argparse.ArgumentParser()
    for key in ("module", "input", "output"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args(sys.argv[2:])
    import importlib
    module = importlib.import_module(args.module)
    require(args.module in {CLEAN, CHUNK}, "Unknown layer validator")
    validator = module.validate_snapshot if args.module == CLEAN else module.validate_snapshot_manifest
    result = validator(Path(args.input))
    create_json(Path(args.output), {"status": "PASS", "input_sha256": sha(args.input), "logical_id": result["logical_id"]})


def main():
    if sys.argv[1:2] == ["validate-layer"]:
        _validate_layer()
        return 0
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("freeze", "Preflight", "Run", "Resume", "Validate"))
    p.add_argument("--suite", choices=("Quick", "Extended"), default="Quick")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--execution", type=Path, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--seal")
    p.add_argument("--owner", type=int)
    args = p.parse_args()
    repo, execution = args.repo.resolve(), args.execution.resolve()
    permitted = repo / "data/retrieval"
    require(execution.is_relative_to(permitted) and execution != permitted, "Use a dedicated execution root under data/retrieval")
    if args.mode == "freeze":
        freeze(repo, execution)
        return 0
    root = args.output.resolve()
    require(root.is_relative_to(permitted) and root != permitted
            and not root.is_relative_to(execution) and not execution.is_relative_to(root), "Use a separate run root under data/retrieval")
    deepest = (root / "incremental/clean/build/mihoyo_obc/zh-cn" / ("0" * 64) /
               "metadata/completed" / ("." + "0" * 64 + ".json." + "0" * 8))
    require(os.name != "nt" or len(str(deepest)) < 260, "Output path is too long for the Windows Clean record receipts")
    checksum = sha(execution / "complete.json")
    require(checksum == args.seal, "Execution seal mismatch")
    declared = read(execution / "complete.json")
    config = {key: value for key, value in declared["files"].items() if key.startswith("config/")}
    paths = [execution / "complete.json", *[inside(execution, key) for key in declared["files"]],
             *[inside(repo, key) for key in config]]
    with readonly_files(paths):
        metadata = verify(execution)
        require(metadata["version"] == VERSION and metadata["repo"] == str(repo), "Foreign execution plan")
        pinned(repo, config)
        run = Run(repo, execution, root, checksum, metadata, args.owner)
        if metadata["scope"] == "smoke":
            require(root.name.startswith("vcs-"), "Smoke plan requires an isolated smoke output")
        if args.mode == "Preflight":
            return 0 if run.preflight(fresh=not root.exists()) else 2
        identity = {"version": VERSION, "execution_sha256": checksum, "output": str(root)}
        if args.mode == "Run":
            if not run.preflight(fresh=True):
                return 2
            with TemporaryDirectory(prefix=".init-", dir=root.parent) as staging:
                staged = Path(staging) / "run"
                create_json(staged / "run.json", {**identity, "started_at": now()})
                staged.rename(root)
        else:
            registered = read(root / "run.json")
            require(all(registered[key] == value for key, value in identity.items()), "Run identity differs")
            if not run.preflight(fresh=False):
                return 2
        with locked(root / "run.lock"), prevent_idle_sleep():
            releases = run.pair(build=args.mode in {"Run", "Resume"})
            run.quick(releases, recheck=args.mode == "Validate")
            if args.suite == "Extended":
                run.extended(releases[0])
            pinned(repo, metadata["inputs"])
            report = {"status": "PASS", "suite": args.suite, "execution_sha256": checksum,
                      "releases": [str(path) for path in releases],
                      "receipts": {p.stem: sha(p) for p in sorted(run.artifacts.glob("*.json"))},
                      "remote_calls": 0, "sealed_at": now(),
                      "elapsed_session_seconds": round(time.monotonic() - run.start, 3),
                      "elapsed_wall_seconds": (datetime.now(timezone.utc) -
                          datetime.fromisoformat(read(root / "run.json")["started_at"])).total_seconds()}
            atomic_json(root / (args.suite.lower() + "-complete.json"), report)
            run.emit("run-completed", suite=args.suite)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted. Completed stages remain available for Resume.", file=sys.stderr)
        raise SystemExit(130)
