"""Start a local, host-neutral evidence MCP server."""

import argparse
from pathlib import Path
import re
import sys

from ..evidence_io import inside, require
from .protocol import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--audit-name", required=True)
    parser.add_argument("--release", required=True, help="Release key for new studies")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.audit_name), "Invalid audit name")
    repo = args.repo.resolve()

    def open_studies():
        from .runtime import Releases
        from .study import LiveStudies

        root = inside(repo, "data/generation/mcp-live-v1")
        return LiveStudies(Releases(repo), inside(root, args.audit_name), args.release)

    serve(open_studies, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
