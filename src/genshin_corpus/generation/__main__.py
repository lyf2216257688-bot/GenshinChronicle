"""Explicit local entry point for the one-request Bailian control smoke."""

from __future__ import annotations

import argparse
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.generation.generation import (
    DEFAULT_G2_SMOKE_PACKET,
    run_bailian_control_smoke,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one locally authorized Bailian/Qwen Generation control smoke"
    )
    parser.add_argument("--packet", type=Path, default=DEFAULT_G2_SMOKE_PACKET)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    result = run_bailian_control_smoke(packet_path=args.packet, output_root=args.output_root)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
