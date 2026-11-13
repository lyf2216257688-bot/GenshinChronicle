"""P4 local workers: one shared neural model, then a separate lexical process."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from ...evidence_io import read, require, strict_json
from ...indexing.benchmark_io import child_process, offline_only, ram_guard, resources
from .core import QuerySpec
from .indexes import DenseSession, LexicalSession, selected
from .runtime import Worker

MAPPING_KEYS = ("parent_id", "observation_id", "source_item", "dense_text_sha256", "granularities",
                "display_order_by_granularity", "chunk_locator", "parent_locator", "graph_locator")


def make_hits(query, channel, routes, representations):
    return [{"question_id": query.question_id, "query_id": query.query_id, "round": query.round,
             "kind": query.kind, "channel": channel, "granularity": g, "rank": rank,
             "raw_score": row["score"], "representation_id": representations[g],
             "input_row": row["input_row"], "chunk_id": row["chunk_id"],
             "index_mapping": {k: row[k] for k in MAPPING_KEYS}}
            for g, rows in routes.items() for rank, row in enumerate(rows, 1)]


class ExperimentWorker(Worker):
    """Reuse the existing bounded RPC/close/Windows descendant ownership contract."""
    def __init__(self, repo, config_path, config, base, channel, output):
        self.stack, self.pool = ExitStack(), ThreadPoolExecutor(max_workers=1)
        self.timeout, self.channel = config["worker_timeout_seconds"], channel
        try:
            log = self.stack.enter_context((Path(output) / (channel + ".stderr.log")).open("x", encoding="utf-8"))
            env = dict(os.environ, PYTHONPATH=str(Path(repo) / "src"), PYTHONIOENCODING="utf-8",
                       PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false",
                       OPENBLAS_NUM_THREADS="4", OMP_NUM_THREADS="4")
            python = Path(repo) / (base["lexical_python"] if channel == "bm25f" else config["python"])
            command = [str(python), "-B", "-u", "-m", __name__, "--repo", str(repo),
                       "--config", str(config_path), "--channel", channel, "--scratch", str(Path(output) / "scratch")]
            self.process = self.stack.enter_context(child_process(command, cwd=repo, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8"))
            self.ready = self.receive()
            require(self.ready["status"] == "ready", "P4 worker preparation failed")
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--channel", choices=("inspect", "neural", "bm25f"), required=True)
    parser.add_argument("--scratch", required=True)
    args = parser.parse_args(argv)
    repo, encoder = Path(args.repo).resolve(), None
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        with offline_only():
            config = read(args.config)
            base = read(repo / config["retrieval_config"])
            selection, manifests = selected(repo, base)
            started = time.perf_counter()
            ram_guard(config)
            if args.channel == "inspect":
                from ...indexing.sparse_job import gpu_guard, make_encoder
                from .experiment import questions_from
                encoder, _ = make_encoder(repo, base, selection, manifests["dense"][1])
                lengths = encoder.encoder.lengths([q["question"] for q in questions_from(repo, config)], role="query")
                require(max(lengths) <= encoder.encoder.model_config["max_tokens"], "Overlength query; truncation forbidden")
                files = {str(encoder.encoder.path / n): s for n, s in encoder.encoder.files.items()}
                files[str(encoder.path)] = encoder.contract["head_sha256"]
                emit({"status": "ready", "space_id": encoder.space_id, "configuration": encoder.contract,
                    "files": files, "lengths": lengths, "gpu": gpu_guard(encoder, config), "resources": resources()})
                return 0  # No model.load(), inference, or index query.
            if args.channel == "neural":
                from ...indexing.sparse_full import StreamingSparseSession
                from ...indexing.sparse_job import gpu_guard, make_encoder
                sessions = {"dense": DenseSession(manifests["dense"][0]),
                            "sparse": StreamingSparseSession(repo / config["sparse_root"])}
                encoder, _ = make_encoder(repo, base, selection, manifests["dense"][1])
                require(encoder.space_id == sessions["sparse"].manifest["space_id"], "Foreign Sparse query encoder")
                gpu_guard(encoder, config)
            else:
                sessions = {"bm25f": LexicalSession(manifests["bm25f"][0], args.scratch)}
            emit({"status": "ready", "input_bundle_id": selection["source"]["input_bundle_id"],
                  "representations": {ch: s.manifest["representations"] for ch, s in sessions.items()},
                  "prepare_seconds": time.perf_counter() - started, "resources": resources(),
                  "inference_policy": "shared backbone; two serial single-query forwards" if encoder else "none"})
            for line in sys.stdin:
                request = strict_json(line)
                if request.get("action") == "close":
                    break
                query = QuerySpec(**request["query"])
                stages = {}
                for channel, session in sessions.items():
                    ram_guard(config, model_loaded=bool(encoder and encoder.encoder.model is not None))
                    tick = time.perf_counter()
                    if channel == "dense":
                        vector = encoder.encoder.encode([query.text], role="query")[0]
                        encode_seconds = time.perf_counter() - tick
                        scan = time.perf_counter()
                        routes = session.search(vector, space_id=encoder.encoder.space_id, top_k=request["depth"])
                        vector_info = {"dimensions": len(vector)}
                    elif channel == "sparse":
                        weights = encoder.encode([query.text], role="query")[0]
                        encode_seconds = time.perf_counter() - tick
                        scan = time.perf_counter()
                        routes = session.search(weights, space_id=encoder.space_id, top_k=request["depth"])
                        vector_info = {"nnz": len(weights)}
                    else:
                        scan, encode_seconds, vector_info = tick, 0., {}
                        routes = session.search(query.text, top_k=request["depth"])
                    ram_guard(config, model_loaded=True)
                    stages[channel] = {"hits": make_hits(query, channel, routes, session.manifest["representations"]),
                        "encode_seconds": encode_seconds, "scan_seconds": time.perf_counter() - scan,
                        "elapsed_seconds": time.perf_counter() - tick, "query_vector": vector_info,
                        "resources": resources()}
                emit({"status": "complete", "query": asdict(query), "stages": stages})
    except BaseException as exc:
        emit({"status": "failed", "type": type(exc).__name__, "message": str(exc)})
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        if encoder is not None:
            encoder.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
