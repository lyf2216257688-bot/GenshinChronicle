"""Isolated V2 S0 worker; reuses indexes/encoder and migration gates unchanged."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import time
import traceback

from ...evidence_io import read, strict_json
from ...indexing.benchmark_io import offline_only, ram_guard, resources
from .indexes import DenseSession, LexicalSession, model_spec, selected
from .query_v2 import ResearchQuery


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    p = argparse.ArgumentParser()
    for key in ("repo", "config", "channel", "scratch"): p.add_argument("--" + key, required=True)
    args = p.parse_args(); sys.stdout.reconfigure(encoding="utf-8")
    session, encoder = None, None
    try:
        with offline_only():
            config = read(args.config); selection, manifests = selected(args.repo, config)
            path, manifest = manifests[args.channel]
            with redirect_stdout(sys.stderr):
                if args.channel == "dense":
                    from ...indexing.benchmark_dense import Encoder
                    from ...runtime_compatibility import index_space
                    session = DenseSession(path)
                    encoder = Encoder(model_spec(selection, manifest), Path(args.repo))
                    space = index_space(Path(args.repo), config, manifest, encoder.contract)
                else:
                    session = LexicalSession(path, args.scratch)
            emit({"status": "ready", "pid": os.getpid(), "input_bundle_id": manifest["input_bundle_id"],
                  "representations": manifest["representations"], "resources": resources()})
            for line in sys.stdin:
                request = strict_json(line)
                if request.get("action") == "close": break
                query = ResearchQuery.parse(request["query"]); tick = time.perf_counter()
                with redirect_stdout(sys.stderr):
                    if encoder is not None:
                        ram_guard(config, model_loaded=encoder.model is not None)
                        vector = encoder.encode([query.search_text], role="query")[0]
                        routes = session.search(vector, space_id=space, top_k=request["depth"])
                    else:
                        routes = session.search(query.search_text, top_k=request["depth"])
                    if request.get("verify_reference"):
                        session.verify_reference(vector if encoder else query.search_text, request["depth"], routes)
                hits = [{"question_id": query.turn_id, "query_id": query.query_id,
                    "round": query.round, "kind": query.kind, "channel": args.channel,
                    "granularity": g, "rank": n, "raw_score": row["score"],
                    "representation_id": manifest["representations"][g],
                    "input_row": row["input_row"], "chunk_id": row["chunk_id"],
                    "index_mapping": {k: row[k] for k in ("parent_id", "observation_id", "source_item",
                        "dense_text_sha256", "granularities", "display_order_by_granularity",
                        "chunk_locator", "parent_locator", "graph_locator")}}
                    for g, rows in routes.items() for n, row in enumerate(rows, 1)]
                emit({"status": "complete", "query": query.value(), "hits": hits,
                      "elapsed_seconds": time.perf_counter() - tick, "resources": resources(),
                      "reference_check": "PASS" if request.get("verify_reference") else "not_requested"})
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        emit({"status": "failed", "type": type(exc).__name__, "message": str(exc)})
        return 1
    finally:
        if session is not None: session.close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
