"""Local JSON-lines worker in a pinned Dense or Whoosh runtime."""
import argparse
from dataclasses import asdict
import gc
import json
from pathlib import Path
import sys
import time
import traceback

from ...evidence_io import read, require, strict_json
from ...indexing.benchmark_io import offline_only, ram_guard, resources
from .core import QuerySpec
from .indexes import DenseSession, LexicalSession, model_spec, selected


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--channel", choices=("dense", "bm25f"), required=True)
    parser.add_argument("--scratch", required=True)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    session, encoder = None, None
    try:
        with offline_only():
            config = read(args.config)
            selection, manifests = selected(Path(args.repo), config)
            path, manifest = manifests[args.channel]
            started = time.perf_counter()
            if args.channel == "dense":
                from ...indexing.benchmark_dense import Encoder
                session = DenseSession(path)
                encoder = Encoder(model_spec(selection, manifest), Path(args.repo))
                require(encoder.space_id == manifest["space_id"], "Actual query encoder differs from selected index")
            else:
                session = LexicalSession(path, args.scratch)
            emit({"status": "ready", "channel": args.channel,
                  "input_bundle_id": manifest["input_bundle_id"], "representations": manifest["representations"],
                  "prepare_seconds": time.perf_counter() - started, "resources": resources()})
            for line in sys.stdin:
                request = strict_json(line)
                if request.get("action") == "close":
                    break
                query = QuerySpec(**request["query"])
                depth = request["depth"]
                started = time.perf_counter()
                if encoder is not None:
                    ram_guard(config, model_loaded=encoder.model is not None)
                    vector = encoder.encode([query.text], role="query")[0]
                    routes = session.search(vector, space_id=encoder.space_id, top_k=depth)
                else:
                    routes = session.search(query.text, top_k=depth)
                if request.get("verify_reference", False):
                    session.verify_reference(vector if encoder is not None else query.text, depth, routes)
                hits = [{"question_id": query.question_id, "query_id": query.query_id,
                         "round": query.round, "kind": query.kind, "channel": args.channel,
                         "granularity": g, "rank": rank, "raw_score": row["score"],
                         "representation_id": manifest["representations"][g],
                         "input_row": row["input_row"], "chunk_id": row["chunk_id"],
                         "index_mapping": {k: row[k] for k in ("parent_id", "observation_id", "source_item",
                                                              "dense_text_sha256", "granularities",
                                                              "display_order_by_granularity", "chunk_locator",
                                                              "parent_locator", "graph_locator")}}
                        for g, rows in routes.items() for rank, row in enumerate(rows, 1)]
                emit({"status": "complete", "query": asdict(query), "hits": hits,
                      "reference_check": "PASS" if request.get("verify_reference", False) else "not_requested",
                      "elapsed_seconds": time.perf_counter() - started, "resources": resources()})
    except BaseException as exc:
        emit({"status": "failed", "type": type(exc).__name__, "message": str(exc)})
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()
        if encoder is not None:
            encoder.model = None
            gc.collect()
            encoder.torch.cuda.empty_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
