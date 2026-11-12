"""Finite query CLI and reusable local-worker sessions; no Serving selectors."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import time

from ...evidence_io import inventory, read, require, sha, strict_json, write
from ...indexing.benchmark_io import child_process, disk_guard, resources
from .core import QuerySpec, fuse_candidates, validate_profile
from .indexes import selected
from .source import FileBinding, InputReader


def validate_output(repo, config, output):
    """Derived outputs must not be nested inside immutable selected assets."""
    repo, output = Path(repo).resolve(), Path(output).resolve()
    selection, manifests = selected(repo, config)
    input_path = Path(manifests["dense"][1]["input_manifest"])
    input_manifest = read(input_path)
    protected = [path.parent for path, _ in manifests.values()]
    protected += [input_path.parent,
                  Path(input_manifest["source_manifests"]["chunk"]).parent,
                  Path(input_manifest["source_manifests"]["clean"]).parent.parent,
                  repo / selection["dense"]["weights_dir"]]
    # Also protect the experiment container, not just its Dense/BM25F children.
    protected.append(manifests["bm25f"][0].parent.parent)
    require(not any(output == root.resolve() or output.is_relative_to(root.resolve()) for root in protected),
            "Output must be outside selected source/model/index roots")


class Worker:
    def __init__(self, repo, config_path, config, channel, output):
        self.stack = ExitStack()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.timeout = config["worker_timeout_seconds"]
        self.channel = channel
        try:
            log = self.stack.enter_context((Path(output) / (channel + ".stderr.log")).open("x", encoding="utf-8"))
            env = dict(os.environ, PYTHONPATH=str(Path(repo) / "src"), PYTHONIOENCODING="utf-8",
                       PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       OPENBLAS_NUM_THREADS="4", OMP_NUM_THREADS="4")
            python = Path(repo) / config["dense_python" if channel == "dense" else "lexical_python"]
            command = [str(python), "-B", "-u", "-m", "genshin_corpus.retrieval.clean_based.worker",
                       "--repo", str(repo), "--config", str(config_path), "--channel", channel,
                       "--scratch", str(Path(output) / (channel + "-scratch"))]
            self.process = self.stack.enter_context(child_process(command, cwd=repo, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8"))
            self.ready = self.receive()
            require(self.ready["status"] == "ready", "Worker failed to prepare: " + str(self.ready))
        except BaseException:
            self.close()
            raise

    def receive(self):
        line = self.pool.submit(self.process.stdout.readline).result(timeout=self.timeout)
        require(bool(line), "Worker closed unexpectedly; inspect " + self.channel + ".stderr.log")
        value = strict_json(line)
        require(value.get("status") != "failed", "Worker failed: " + str(value))
        return value

    def search(self, query, depth, *, verify_reference=False):
        self.process.stdin.write(json.dumps({"query": asdict(query), "depth": depth,
                                            "verify_reference": verify_reference}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        result = self.receive()
        require(result.get("status") == "complete", "Incomplete worker response")
        require(result.get("query") == json.loads(json.dumps(asdict(query))), "Worker query mismatch")
        return result

    def close(self):
        if hasattr(self, "process") and self.process.poll() is None:
            try:
                self.process.stdin.write('{"action":"close"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.stack.close()  # The existing Job Object owns the entire child tree.
        if hasattr(self, "process"):
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe is not None:
                    pipe.close()
        self.pool.shutdown(wait=True, cancel_futures=True)


class PreparedRetriever:
    def __init__(self, repo, config_path, output):
        self.repo, self.config_path = Path(repo).resolve(), Path(config_path).resolve()
        self.config = read(self.config_path)
        require(self.config.get("version") == "gc-clean-retrieval-1", "Unsupported retrieval configuration")
        validate_profile(self.config)
        require(self.config["combination"] == "S0", "P0–P2 live runtime supports S0 only; Sparse is a bounded trial")
        self.output = Path(output).resolve()
        validate_output(self.repo, self.config, self.output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.stack = ExitStack()
        self.workers = {}
        self.binding = FileBinding()
        self.binding.bind(self.config_path)
        self.binding.bind(self.repo / self.config["selection"])
        started = time.perf_counter()
        try:
            disk_guard(self.output, self.config)
            selection, manifests = selected(self.repo, self.config)
            for path, manifest in manifests.values():
                self.binding.bind(path)
            self.reader = self.stack.enter_context(InputReader(manifests["dense"][1]["input_manifest"]))
            require(self.reader.bundle_id == selection["source"]["input_bundle_id"], "Foreign input reader")
            for channel in ("dense", "bm25f"):
                worker = Worker(self.repo, self.config_path, self.config, channel, self.output)
                self.stack.callback(worker.close)
                self.workers[channel] = worker
                require(worker.ready["input_bundle_id"] == self.reader.bundle_id
                        and worker.ready["representations"] == manifests[channel][1]["representations"],
                        "Foreign worker index")
            self.preparation = {"status": "complete", "input_bundle_id": self.reader.bundle_id,
                                "elapsed_seconds": time.perf_counter() - started,
                                "workers": {k: w.ready for k, w in self.workers.items()}, "resources": resources()}
            write(self.output / "preparation.json", self.preparation)
        except BaseException as exc:
            self.close()
            write(self.output / "failure.json", {"stage": "prepare", "type": type(exc).__name__, "message": str(exc)})
            raise

    def search(self, query, *, verify_reference=False):
        self.binding.check()
        started = time.perf_counter()
        stages = {channel: worker.search(query, self.config["depth"], verify_reference=verify_reference)
                  for channel, worker in self.workers.items()}
        for channel, stage in stages.items():
            require(all(h["channel"] == channel and h["representation_id"] == self.workers[channel].ready["representations"].get(h["granularity"])
                        for h in stage["hits"]), "Worker returned a foreign route representation")
        hits = [h for stage in stages.values() for h in stage["hits"]]
        documents = {}
        for hit in hits:
            if hit["chunk_id"] not in documents:
                documents[hit["chunk_id"]] = self.reader.document(hit["input_row"], hit["chunk_id"])
            doc = documents[hit["chunk_id"]]
            require(hit["input_row"] == doc["input_row"]
                    and all(doc["row"][k] == v for k, v in hit["index_mapping"].items()), "Index/source mapping differs")
        result = fuse_candidates(query, hits, documents, self.config, input_bundle_id=self.reader.bundle_id)
        result["raw_hits"] = hits
        result["telemetry"] = {"elapsed_seconds": time.perf_counter() - started,
                               "channels": {k: {n: v[n] for n in ("elapsed_seconds", "resources", "reference_check")}
                                            for k, v in stages.items()}}
        self.binding.check()
        self.reader.binding.check()
        return result

    def close(self):
        self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def render_result(result, *, top_n=10):
    text = ["# Clean-based 召回原文", "", result["query"]["question"], "",
            "仅为召回候选，不是最终 Evidence Packet 或答案。", ""]
    for row in result["candidates"][:top_n]:
        chunk = row["chunk"]
        title = chunk["context"].get("label_evidence", {}).get("document_title") or {}
        text += [f"## {row['organized_rank']}. {title.get('text', row['chunk_id'])}", "",
                 f"Chunk: {row['chunk_id']}；融合名次: {row['fusion_rank']}；处置: {row['disposition']}", "",
                 "命中: " + ", ".join(f"{h['channel']}/{h['granularity']} #{h['rank']}" for h in row["hits"]), ""]
        body = chunk["retrieval_text"]
        fence = "~" * max(4, max((len(line) + 1 for line in body.splitlines() if set(line) == {"~"}), default=0))
        text += [fence + "text", body, fence, ""]
        text += ["条件状态: " + ", ".join(sorted({p.get("condition_status", "not_established") for p in chunk["parts"]})),
                 "", "来源范围保存在 results.json 的 source.clean_refs；来源性质沿用 source_context。", ""]
    return "\n".join(text)


def run_query(repo, config_path, output, query, *, verify_reference=False):
    output = Path(output).resolve()
    with PreparedRetriever(repo, config_path, output) as prepared:
        try:
            result = prepared.search(query, verify_reference=verify_reference)
            write(output / "results.json", result)
            (output / "results.md").write_text(render_result(result), encoding="utf-8")
            bundle_id = prepared.reader.bundle_id
        except BaseException as exc:
            write(output / "failure.json", {"stage": "query", "type": type(exc).__name__, "message": str(exc)})
            raise
    write(output / "complete.json", {"scope": "single-query", "input_bundle_id": bundle_id,
        "files": inventory(output), "config_sha256": sha(config_path),
        "code_sha256": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")}})
    return result
