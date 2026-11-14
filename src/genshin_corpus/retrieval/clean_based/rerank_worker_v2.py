"""Single selected BGE process; no GTE, fallback, truncation or network."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import traceback

from ...evidence_io import read, require, strict_json
from ...indexing.benchmark_io import offline_only


def main():
    parser = argparse.ArgumentParser()
    for key in ("repo", "scratch"): parser.add_argument("--" + key, required=True)
    args = parser.parse_args(); sys.stdout.reconfigure(encoding="utf-8")
    Path(args.scratch).mkdir(parents=True, exist_ok=False)
    def emit(value): print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    try:
        with offline_only(), redirect_stdout(sys.stderr):
            from .rerank_model import LocalReranker
            adapter = LocalReranker(args.repo, "bge", args.scratch)
        emit({"status": "ready", "pid": os.getpid(), "binding": adapter.binding})
        for line in sys.stdin:
            request = strict_json(line)
            if request.get("action") == "close": break
            value = read(request["input"])
            require(value["version"] == "gc-clean-scoring-input-2", "Expected explicit V2 scoring input")
            require(request["budget"] == 2048 and value["candidates"], "Invalid budget/empty scoring input")
            with offline_only(), redirect_stdout(sys.stderr):
                result = adapter.score(value, request["budget"])
            emit({"status": "complete", **result})
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        emit({"status": "failed", "type": type(exc).__name__, "message": str(exc)})
        return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
