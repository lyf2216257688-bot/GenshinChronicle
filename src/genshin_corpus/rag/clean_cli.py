"""Explicit local Generation workbench; legacy CLI/Serving selectors stay unchanged."""
import argparse
import json
from pathlib import Path

from ..model_call_sessions import SessionCalls
from .clean_graph import run_mock
from .clean_store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    mock = sub.add_parser("mock", help="G1 offline fixture, no remote model")
    mock.add_argument("--root", default="data/generation/clean-v1-local")
    mock.add_argument("--session", required=True)
    mock.add_argument("--submit-token", required=True)
    mock.add_argument("--question", required=True)
    mock.add_argument("--continue-turn", action="store_true")
    args = parser.parse_args(argv)
    store = Store(args.root)
    tid = store.submit(args.session, args.submit_token, args.question, "g1-mock-fixture")
    # Explicit fixture allowances are not product/live defaults.
    bridge = SessionCalls(store, "g1-mock-fixture", {
        "campaign_requests": 4, "campaign_tokens": 100, "turn_requests": 1, "turn_tokens": 10})
    result = run_mock(bridge, tid, continue_turn=args.continue_turn)
    print(json.dumps({"mode": "MOCK", "turn_id": tid, "state": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
