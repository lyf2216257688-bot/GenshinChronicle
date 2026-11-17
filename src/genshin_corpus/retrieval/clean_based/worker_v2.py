"""Isolated V2 S0/S2 worker; reuses indexes/encoder and migration gates unchanged."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import time
import traceback

from ...evidence_io import read, require, strict_json
from ...indexing.benchmark_io import offline_only, ram_guard, resources
from .indexes import DenseSession, LexicalSession, model_spec, selected
from .query_v2 import ResearchQuery, validate_profile_v2
from .source import FileBinding
from .sparse_v2 import selected_sparse


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
            validate_profile_v2(config)
            binding = FileBinding(); binding.bind(args.config)
            if args.channel == "sparse":
                manifests["sparse"] = selected_sparse(args.repo, config, manifests["dense"][1], binding)
            path, manifest = manifests[args.channel]
            with redirect_stdout(sys.stderr):
                if args.channel == "dense":
                    from ...indexing.benchmark_dense import Encoder
                    from ...runtime_compatibility import index_space
                    session = DenseSession(path)
                    encoder = Encoder(model_spec(selection, manifest), Path(args.repo))
                    space = index_space(Path(args.repo), config, manifest, encoder.contract)
                elif args.channel == "sparse":
                    from ...indexing.benchmark_dense import Encoder
                    from ...indexing.sparse_trial import SparseEncoder
                    from ...indexing.sparse_full import StreamingSparseSession
                    from ...runtime_compatibility import index_space
                    session = StreamingSparseSession(path.parent)
                    backbone = Encoder(model_spec(selection, manifests["dense"][1]), Path(args.repo))
                    index_space(Path(args.repo), config, manifests["dense"][1], backbone.contract)
                    encoder = SparseEncoder(backbone, head_sha256=config["sparse_head_sha256"])
                    space = index_space(Path(args.repo), config, manifest, encoder.contract, kind="sparse")
                else:
                    require(args.channel == "bm25f", "Unknown V2 channel")
                    session = LexicalSession(path, args.scratch)
            emit({"status": "ready", "pid": os.getpid(), "input_bundle_id": manifest["input_bundle_id"],
                  "representations": manifest["representations"], "resources": resources()})
            for line in sys.stdin:
                request = strict_json(line)
                if request.get("action") == "close": break
                query = ResearchQuery.parse(request["query"]); tick = time.perf_counter()
                validate_profile_v2(config, query.combination); binding.check()
                with redirect_stdout(sys.stderr):
                    if encoder is not None:
                        owner = encoder.encoder if args.channel == "sparse" else encoder
                        ram_guard(config, model_loaded=owner.model is not None)
                        vector = encoder.encode([query.search_text], role="query")[0]
                        routes = session.search(vector, space_id=space, top_k=request["depth"])
                    else:
                        routes = session.search(query.search_text, top_k=request["depth"])
                    if request.get("verify_reference"):
                        if args.channel == "sparse":
                            # Same streaming scan is checked against independent CSR dot-product below.
                            from ...indexing.sparse_full import matrix_read
                            from ...indexing.sparse_trial import csr_weights
                            expected = {g: [] for g in ("micro", "macro")}
                            q = csr_weights([vector], manifest["vocabulary_size"]).toarray()[0]
                            for item in manifest["shards"]:
                                folder = path.parent / item["folder"]
                                matrix, rows = matrix_read(folder, read(folder / "complete.json"), manifest["vocabulary_size"])
                                for row, score in zip(rows, matrix @ q):
                                    if score > 0:
                                        for g in row["granularities"]: expected[g].append({**row, "score": float(score)})
                            expected = {g: sorted(v, key=lambda r: (-r["score"], r["input_row"]))[:request["depth"]]
                                        for g, v in expected.items()}
                            require(routes == expected, "Sparse streaming/reference ranking differs")
                        else:
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
        if session is not None and hasattr(session, "close"): session.close()
        if args.channel == "sparse" and encoder is not None: encoder.close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
