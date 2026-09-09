"""Command-line entry point for the Phase 04 read-only profiler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes

from .profiler import profile_canonical_run, write_profile
from .qwen_embedding import (
    DashScopeQwenEmbeddingConfig,
    QwenSynchronousPreflightConfig,
    run_qwen_dashscope_synchronous_preflight,
)


def _qwen_dashscope_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the explicit live DashScope Qwen embedding preflight")
    parser.add_argument("--retrieval-unit-manifest", required=True, type=Path)
    parser.add_argument("--document-unit-id", action="append", required=True)
    parser.add_argument("--query-text", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--region", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args(argv)
    result = run_qwen_dashscope_synchronous_preflight(
        QwenSynchronousPreflightConfig(
            args.retrieval_unit_manifest,
            tuple(args.document_unit_id),
            args.query_text,
        ),
        args.output_root,
        DashScopeQwenEmbeddingConfig(
            region=args.region,
            workspace=args.workspace,
            endpoint=args.endpoint,
        ),
    )
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0 if result.get("status") == "complete" else 1


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "qwen-dashscope-preflight":
        return _qwen_dashscope_main(argv[1:])
    parser = argparse.ArgumentParser(description="Read-only profile of one accepted Canonical run")
    parser.add_argument("--manifest", required=True, type=Path, help="Canonical run metadata/manifest.json")
    parser.add_argument("--output", type=Path, help="Optional aggregate JSON output path; Canonical data is never written")
    parser.add_argument("--top-n", type=int, default=30, help="Maximum ranked values per aggregate table")
    args = parser.parse_args(argv)
    profile = profile_canonical_run(args.manifest, top_n=args.top_n)
    if args.output is None:
        print(json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        write_profile(profile, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
