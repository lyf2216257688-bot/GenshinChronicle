"""Explicit local entry point for the one-request Bailian control smoke."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.generation.generation import (
    DEFAULT_G2_SMOKE_PACKET,
    run_bailian_control_smoke,
)
from genshin_corpus.generation.measure import (
    DEFAULT_M1_ACCEPTED_INPUT,
    DEFAULT_M2_MAX_OUTPUT_TOKENS,
    DEFAULT_M2_REVIEW_ONLY_INPUT,
    DEFAULT_M2_RUNTIME_INPUT,
    DEFAULT_M2_SOURCE_MANIFEST,
    prepare_m2_question_inputs,
    preflight_m1,
    preflight_m2,
    run_m1_measure,
    run_m2_measure,
)


def _m1_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Preflight or run the six-question P04-RAG-M1 measure")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("m1-preflight", "m1-run"):
        child = subparsers.add_parser(command)
        child.add_argument("--accepted-input", type=Path, default=DEFAULT_M1_ACCEPTED_INPUT)
        if command == "m1-run":
            child.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "m1-preflight":
        result = preflight_m1(args.accepted_input)
    else:
        result = run_m1_measure(args.accepted_input, args.output_root)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


def _m2_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Prepare, preflight, or run the 70-question P04-RAG-M2 measure")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("m2-prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--runtime-input", type=Path, default=DEFAULT_M2_RUNTIME_INPUT)
    prepare.add_argument("--review-only-input", type=Path, default=DEFAULT_M2_REVIEW_ONLY_INPUT)
    prepare.add_argument("--source-manifest", type=Path, default=DEFAULT_M2_SOURCE_MANIFEST)
    preflight = subparsers.add_parser("m2-preflight")
    preflight.add_argument("--runtime-input", type=Path, default=DEFAULT_M2_RUNTIME_INPUT)
    run = subparsers.add_parser("m2-run")
    run.add_argument("--runtime-input", type=Path, default=DEFAULT_M2_RUNTIME_INPUT)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--max-output-tokens", type=int, default=DEFAULT_M2_MAX_OUTPUT_TOKENS)
    args = parser.parse_args(argv)
    if args.command == "m2-prepare":
        result = prepare_m2_question_inputs(
            args.source,
            runtime_input=args.runtime_input,
            review_only_input=args.review_only_input,
            source_manifest=args.source_manifest,
        )
    elif args.command == "m2-preflight":
        result = preflight_m2(args.runtime_input)
    else:
        result = run_m2_measure(
            args.runtime_input,
            args.output_root,
            max_output_tokens=args.max_output_tokens,
        )
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"m1-preflight", "m1-run"}:
        return _m1_main(argv)
    if argv and argv[0] in {"m2-prepare", "m2-preflight", "m2-run"}:
        return _m2_main(argv)
    parser = argparse.ArgumentParser(
        description="Run one locally authorized Bailian/Qwen Generation control smoke"
    )
    parser.add_argument("--packet", type=Path, default=DEFAULT_G2_SMOKE_PACKET)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    result = run_bailian_control_smoke(packet_path=args.packet, output_root=args.output_root)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
