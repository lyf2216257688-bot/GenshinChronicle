"""Finite new-query S0/BGE wiring, independent of saved 70Q score directories."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time

from ...evidence_io import digest, read, require, seal, sha, strict_json, write
from ...indexing.benchmark_io import child_process, disk_guard, resources
from ...model_calls import locked
from .core import validate_profile
from .indexes import selected
from .query_v2 import fuse_v2, validate_ancestry
from .rerank_handoff import validate_selection
from .rerank_v2 import rank_v2, scoring_input
from .runtime import validate_output
from .source import FileBinding, InputReader


def code_identity():
    folder = Path(__file__).parent
    return {p.name: sha(p) for p in [folder / n for n in
        ("runtime_v2.py", "worker_v2.py", "query_v2.py", "rerank_v2.py", "rerank_worker_v2.py",
         "core.py", "rerank_core.py", "rerank_model.py", "indexes.py", "source.py")]}


def wait_pid(pid):
    """Confirm the actual venv interpreter exited, including its redirector tree."""
    if os.name != "nt": return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        require(ctypes.get_last_error() == 87, "Cannot verify worker exit")
        return
    try:
        require(kernel.WaitForSingleObject(handle, 10000) == 0, "Worker still alive; GPU lease retained until exit")
    finally:
        kernel.CloseHandle(handle)


class JsonWorker:
    def __init__(self, repo, python, module, args, output, *, timeout=600):
        self.stack, self.pool = ExitStack(), ThreadPoolExecutor(max_workers=1)
        self.timeout, self.closed = timeout, False
        try:
            log = self.stack.enter_context(Path(output).open("x", encoding="utf8"))
            env = dict(os.environ, PYTHONPATH=str(Path(repo) / "src"), PYTHONIOENCODING="utf-8",
                PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                HF_HUB_DISABLE_TELEMETRY="1", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false",
                OPENBLAS_NUM_THREADS="4", OMP_NUM_THREADS="4")
            self.process = self.stack.enter_context(child_process(
                [str(Path(repo) / python), "-B", "-u", "-m", module, *args], cwd=repo, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf8"))
            self.ready = self.receive()
            require(self.ready["status"] == "ready", "Worker not ready")
        except BaseException:
            self.close(); raise

    def receive(self):
        line = self.pool.submit(self.process.stdout.readline).result(timeout=self.timeout)
        require(bool(line), "Worker exited unexpectedly; inspect stderr")
        value = strict_json(line)
        require(value.get("status") != "failed", "Worker failed: " + str(value))
        return value

    def request(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n"); self.process.stdin.flush()
        result = self.receive(); require(result["status"] == "complete", "Incomplete worker result")
        return result

    def close(self):
        if self.closed: return
        self.closed = True
        try:
            if hasattr(self, "process") and self.process.poll() is None:
                try:
                    self.process.stdin.write('{"action":"close"}\n'); self.process.stdin.flush()
                    self.process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired): pass
        finally:
            self.stack.close()  # KILL_ON_JOB_CLOSE includes the venv interpreter.
            if hasattr(self, "process"):
                for pipe in (self.process.stdin, self.process.stdout):
                    if pipe: pipe.close()
            self.pool.shutdown(wait=True, cancel_futures=True)
        if hasattr(self, "ready"): wait_pid(self.ready["pid"])

    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class LocalPipeline:
    def __init__(self, repo, config_path, selection_path, output, *, worker_factory=JsonWorker):
        self.repo, self.output = Path(repo).resolve(), Path(output).resolve()
        self.config_path, self.selection_path = Path(config_path).resolve(), Path(selection_path).resolve()
        self.config = read(self.config_path); self.selection = validate_selection(read(self.selection_path))
        validate_profile(self.config)
        require(self.config["combination"] == "S0" and self.config["fusion"] == "f1_max"
                and self.config["structural_cleanup"], "Selected S0 profile required")
        validate_output(self.repo, self.config, self.output)
        self.output.mkdir(parents=True, exist_ok=False)
        disk_guard(self.output, self.config)
        self.worker_factory, self.binding = worker_factory, FileBinding()
        self.binding.bind(self.config_path); self.binding.bind(self.selection_path)
        self.code = code_identity()
        self.resources = []
        _, self.manifests = selected(self.repo, self.config)
        self.reader = InputReader(self.manifests["dense"][1]["input_manifest"])
        try:
            require(self.reader.bundle_id == self.selection["input_bundle_id"], "Foreign Reader/selection")
        except BaseException:
            self.reader.close(); raise

    def run(self, query, *, parents=(), evidence_refs=(), verify_reference=False):
        validate_ancestry(query, parents, evidence_refs)
        self.binding.check(); started = time.perf_counter(); hits = []
        # Repo-wide GPU lock also protects independent workbench roots.
        with locked(self.repo / ".local/clean-generation-gpu.lock"):
            for channel in ("dense", "bm25f"):
                python = self.config["dense_python" if channel == "dense" else "lexical_python"]
                args = ["--repo", str(self.repo), "--config", str(self.config_path), "--channel", channel,
                        "--scratch", str(self.output / (channel + "-scratch"))]
                with self.worker_factory(self.repo, python, "genshin_corpus.retrieval.clean_based.worker_v2", args,
                                         self.output / (channel + ".stderr.log"), timeout=self.config["worker_timeout_seconds"]) as worker:
                    require(worker.ready["input_bundle_id"] == self.reader.bundle_id
                            and worker.ready["representations"] == self.manifests[channel][1]["representations"], "Foreign worker")
                    stage = worker.request({"query": query.value(), "depth": self.config["depth"],
                                            "verify_reference": verify_reference})
                    require(stage["query"] == query.value(), "Worker query changed")
                    require(all(h["channel"] == channel and h["representation_id"] == worker.ready["representations"][h["granularity"]]
                                for h in stage["hits"]), "Foreign worker hit")
                    hits.extend(stage["hits"])
                    self.resources.append({"channel": channel, "pid": worker.ready["pid"], "stage": stage,
                                           "start_resources": worker.ready.get("resources")})
                self.resources[-1]["exit_confirmed"] = True
            documents = {}
            for h in hits:
                if h["chunk_id"] not in documents:
                    documents[h["chunk_id"]] = self.reader.document(h["input_row"], h["chunk_id"])
                doc = documents[h["chunk_id"]]
                require(h["input_row"] == doc["input_row"] and all(doc["row"][k] == v for k, v in h["index_mapping"].items()),
                        "Index/source mismatch")
            candidates = fuse_v2(query, hits, documents, self.config, input_bundle_id=self.reader.bundle_id, code=self.code)
            candidates["raw_hits"] = hits
            write(self.output / "candidates.json", candidates)
            score = None
            if candidates["candidates"]:
                path = self.output / "scoring-input.json"
                write(path, scoring_input(candidates, self.selection))
                with self.worker_factory(self.repo, self.selection["python"],
                    "genshin_corpus.retrieval.clean_based.rerank_worker_v2",
                    ["--repo", str(self.repo), "--scratch", str(self.output / "bge-scratch")],
                    self.output / "bge.stderr.log", timeout=self.config["worker_timeout_seconds"]) as worker:
                    require(worker.ready["binding"] == self.selection["score_binding"], "Unvalidated scoring runtime/model")
                    score = worker.request({"action": "score", "input": str(path), "budget": 2048})
                    self.resources.append({"channel": "bge", "pid": worker.ready["pid"], "resources": score["resources"]})
                self.resources[-1]["exit_confirmed"] = True
                write(self.output / "scores.json", score)
            result = rank_v2(candidates, score, self.selection)
        self.binding.check(); self.reader.binding.check()
        require(self.code == code_identity(), "Wiring code changed during execution")
        write(self.output / "ranked.json", result)
        write(self.output / "local-run.json", {"resources": self.resources, "code": self.code,
            "elapsed_seconds": time.perf_counter() - started, "mode": "local-inference-no-paid-API",
            "remote_calls": 0, "answer_quality": "NOT_EVALUATED"})
        return result

    def close(self): self.reader.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
