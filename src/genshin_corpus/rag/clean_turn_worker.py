"""Private single-turn child, owned by the local executor's Windows Job."""
import argparse
import os
from pathlib import Path

from ..evidence_io import inside, write
from ..retrieval.clean_based.source import InputReader
from .clean_conversation import ConversationStore
from .clean_conversation_graph import run_turn
from .clean_mock import fixture_inputs, FixturePipeline, MockOpener, mock_answer


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True); p.add_argument("--repo", required=True)
    p.add_argument("--turn", required=True); p.add_argument("--run-root", required=True)
    p.add_argument("--delay", type=float, required=True); p.add_argument("--continue-turn", action="store_true")
    args = p.parse_args(argv)
    store = ConversationStore(args.root)
    folder = inside(store.root / "executor_runs", args.run_root)
    mode = store.chat(store.turn(args.turn)["session"])["settings"]["mode"]
    write(folder / "worker.json", {"pid": os.getpid(), "mode": mode, "turn_id": args.turn})
    store.shutdown_file = folder / "shutdown.json"
    if mode == "LIVE":
        from .clean_live import LivePipeline
        pipeline = LivePipeline(store, args.turn, args.repo)
        try: run_turn(store, args.turn, pipeline, continue_turn=args.continue_turn)
        finally: pipeline.close()
        return 0
    manifest = fixture_inputs(Path(args.repo), store.root / "mock_corpus")
    with InputReader(manifest) as reader:
        run_turn(store, args.turn, FixturePipeline(reader, args.repo), continue_turn=args.continue_turn,
                 opener_factory=lambda packet, scenario: MockOpener(mock_answer(packet, scenario),
                     disconnect=scenario == "disconnect", delay=args.delay))
    return 0


if __name__ == "__main__": raise SystemExit(main())
