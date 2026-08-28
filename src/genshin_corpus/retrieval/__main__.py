"""Command-line entry point for the Phase 04 read-only profiler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .profiler import profile_canonical_run, write_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only profile of one accepted Canonical run")
    parser.add_argument("--manifest", required=True, type=Path, help="Canonical run metadata/manifest.json")
    parser.add_argument("--output", type=Path, help="Optional aggregate JSON output path; Canonical data is never written")
    parser.add_argument("--top-n", type=int, default=30, help="Maximum ranked values per aggregate table")
    args = parser.parse_args()
    profile = profile_canonical_run(args.manifest, top_n=args.top_n)
    if args.output is None:
        print(json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        write_profile(profile, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
