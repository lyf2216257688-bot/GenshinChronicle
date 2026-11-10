"""One isolated runtime per stage. All inputs and models are local."""
import argparse
from pathlib import Path
import time
import traceback

from ..build_runtime import atomic_json
from ..evidence_io import digest, read, require
from .benchmark_io import attempt, build_lock, event, lines, offline_only


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("dense-preflight", "dense-build", "dense-validate", "dense-query",
                                          "bm25f-preflight", "bm25f-build", "bm25f-validate", "bm25f-query"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model")
    parser.add_argument("--questions")
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args(argv)
    config, root, repo = read(args.config), Path(args.root), Path(args.repo)
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        with build_lock(root), offline_only():
            if args.action.startswith("dense"):
                from . import benchmark_dense as backend
                model = next(m for m in config["models"] if m["key"] == args.model)
                encoder = backend.Encoder(model, repo)
                if args.action == "dense-preflight":
                    result = backend.preflight(encoder, args.input, root, config)
                elif args.action == "dense-build":
                    result = backend.build(encoder, args.input, root, config)
                elif args.action == "dense-validate":
                    require(read(root / "manifest.json")["configuration"] == encoder.contract,
                            "Validated index differs from actual local encoder/runtime")
                    result = backend.validate(args.input, root)
                else:
                    manifest = read(root / "manifest.json")
                    require(encoder.space_id == manifest["space_id"], "Query encoder/index configuration mismatch")
                    result = []
                    for question in lines(args.questions):
                        vector = encoder.encode([question["question"]], role="query")[0]
                        for level in ("micro", "macro"):
                            hits = backend.search(root, vector, space_id=encoder.space_id,
                                                  granularity=level, top_k=args.top_k)
                            result.append({**question, "granularity": level,
                                           "representation_id": manifest["representations"][level], "hits": hits})
            else:
                from . import benchmark_lexical as backend
                if args.action == "bm25f-preflight":
                    effective, segment = backend.configuration(config["bm25f"], root)
                    backend.schema()
                    require(bool(segment("坎瑞亚与深渊")), "Empty Chinese analyzer smoke")
                    result = {"status": "PASS", "configuration": effective}
                    atomic_json(root / "preflight.json", result)
                elif args.action == "bm25f-build":
                    result = backend.build(args.input, root, config)
                elif args.action == "bm25f-validate":
                    result = backend.validate(args.input, root, config)
                else:
                    manifest = read(root / "manifest.json")
                    result = [{**question, "granularity": level,
                               "representation_id": manifest["representations"][level],
                               "hits": backend.search(root, question["question"], granularity=level,
                                                       top_k=args.top_k, config=config)}
                              for question in lines(args.questions) for level in ("micro", "macro")]
            if args.action.endswith("query"):
                output = attempt(root / "queries", "results")
                atomic_json(output / "results.json", {"status": "PASS", "questions_sha256": digest(list(lines(args.questions))),
                                                       "results": result})
            event(root, "stage_complete", action=args.action, elapsed_seconds=time.monotonic() - started)
        return 0
    except BaseException as exc:
        event(root, "stage_failed", action=args.action, error_type=type(exc).__name__, message=str(exc))
        output = attempt(root / "failures", "failure")
        atomic_json(output / "error.json", {"action": args.action, "type": type(exc).__name__,
                                           "message": str(exc), "traceback": traceback.format_exc()})
        traceback.print_exc()
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
