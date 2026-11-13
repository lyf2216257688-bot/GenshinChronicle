"""Operator-launched JSONL worker; CPU tokenizer audit or serial CUDA scoring."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import traceback

from ...evidence_io import read, strict_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model", choices=("bge", "gte"), required=True)
    parser.add_argument("--scratch", required=True)
    args = parser.parse_args()
    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=False)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      HF_MODULES_CACHE=str(scratch / "modules"), HF_HOME=str(scratch / "hf"))
    def emit(value):
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    try:
        with redirect_stdout(sys.stderr):
            from .rerank_model import LocalReranker
            adapter = LocalReranker(args.repo, args.model, scratch)
        emit({"status": "ready", "binding": adapter.binding, "native_cap": adapter.cap})
        for line in sys.stdin:
            request = strict_json(line)
            if request["action"] == "close":
                break
            bundle = read(request["input"])
            with redirect_stdout(sys.stderr):
                if request["action"] == "audit":
                    counts = [{"chunk_id": c["chunk_id"], "tokens": adapter.count(bundle["query"]["question"],
                               c["chunk"]["retrieval_text"])} for c in bundle["candidates"]]
                    result = {"counts": counts}
                else:
                    result = adapter.score(bundle, request["budget"], request.get("limits"))
            emit({"status": "complete", **result})
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        emit({"status": "failed", "type": type(exc).__name__, "message": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
