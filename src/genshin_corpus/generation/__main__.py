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
    preflight_m1,
    run_m1_measure,
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"m1-preflight", "m1-run"}:
        return _m1_main(argv)
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
