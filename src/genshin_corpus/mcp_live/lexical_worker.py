"""Versioned BM25F with one open route reader at a time."""

import argparse
from contextlib import ExitStack, redirect_stdout
import gc
import heapq
import json
import os
from pathlib import Path
import sys

from ..evidence_io import sha, strict_json
from ..indexing.benchmark_io import offline_only, resources
from ..indexing.schema import GRANULARITIES
from ..indexing.snapshot_lexical import LexicalIndex, _terms, lexical_runtime, representations
from ..retrieval.clean_based.snapshot import open_release


class SequentialLexicalIndex(LexicalIndex):
    def _search_route(self, route, clauses, top_k):
        self.cleanup = ExitStack()
        with self.cleanup:
            searcher, ids = self._open_route(route)
            hits = searcher.search(clauses, limit=None, optimize=False) if searcher is not None else ()
            # Collect the complete score boundary and retain the same logical row tie-break.
            return heapq.nsmallest(top_k, ({"input_row": ids[hit["chunk_id"]], "score": hit.score} for hit in hits),
                                   key=lambda row: (-row["score"], row["input_row"])), resources()

    def search_with_resources(self, text, top_k):
        clauses = _terms(text, self.effective, self.segment)
        result, observed = {}, {}
        for route in GRANULARITIES:
            result[route], observed[route] = self._search_route(route, clauses, top_k)
            gc.collect()
        return result, observed


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("repo", "release", "scratch"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    with offline_only(), redirect_stdout(sys.stderr):
        release, source, stores, files = open_release(args.release)
        effective, segment = lexical_runtime(Path(args.repo), Path(args.scratch))
        index = SequentialLexicalIndex(release["bm25f"]["path"], source, effective, segment)
    try:
        emit({"status": "ready", "pid": os.getpid(), "release_sha256": sha(args.release),
              "representations": representations(stores["bm25f"])})
        for line in sys.stdin:
            request = strict_json(line)
            if request.get("action") == "close":
                break
            files.check()
            with offline_only(), redirect_stdout(sys.stderr):
                routes, per_route = index.search_with_resources(request["text"], request["depth"])
                observed = resources()
            emit({"status": "complete", "routes": routes, "resources": {**observed, "by_route": per_route}})
    finally:
        index.close()


if __name__ == "__main__":
    main()
