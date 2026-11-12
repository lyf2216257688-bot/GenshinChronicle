"""Opt-in single-query and bounded Sparse trial commands."""
import argparse
from pathlib import Path

from .core import QuerySpec


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/clean-retrieval-v1.json")
    sub = parser.add_subparsers(dest="command", required=True)
    query = sub.add_parser("query")
    query.add_argument("--question", required=True)
    query.add_argument("--question-id", default="operator")
    query.add_argument("--output", required=True)
    query.add_argument("--verify-reference", action="store_true", help="Finite original-search parity check")
    trial = sub.add_parser("sparse-trial")
    trial.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[4]
    config_path = (repo / args.config).resolve()
    if args.command == "query":
        from .runtime import run_query
        result = run_query(repo, config_path, args.output, QuerySpec(args.question, args.question_id),
                           verify_reference=args.verify_reference)
        print(f"complete: {result['counts']['selected']} candidates -> {Path(args.output).resolve()}")
    else:
        from .trial import run_trial
        run_trial(repo, config_path, args.output)


if __name__ == "__main__":
    main()
