"""Start a frozen-evidence stdio server. Configuration and audit storage are local."""
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
    args = parser.parse_args()
    require(re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", args.audit_name), "Invalid audit name")
    def open_studies():
        from .archive import Archive
        from .study import Studies
        root = inside(args.repo, "data/generation/mcp-offline-v1")
        return Studies(Archive(args.repo), inside(root, args.audit_name))
    serve(open_studies, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
