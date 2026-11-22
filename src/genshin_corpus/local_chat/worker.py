"""Single product worker, reusing the G4 Mock and G5 Live graph entrypoints."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from ..evidence_io import inside, require, write
from ..indexing.benchmark_io import child_process
from ..rag.clean_conversation import TERMINAL
from ..rag.clean_executor import LocalExecutor
from ..rag.clean_store import put
from .batches import ExecutionView
from .store import ProductStore


class ProductExecutor(LocalExecutor):
    def __init__(self, store, repo, *, delay=.025):
        super().__init__(store.root, repo, delay=delay)
        self.store = store
        # The executor owns the instance lock, and its in-memory queue is still empty.
        # A previous process may have exited between committing a turn and starting it.
        with store.connect() as db:
            db.execute("UPDATE chat_jobs SET status='interrupted' WHERE status='queued'")
        # A crash after the atomic turn commit may precede publication of its user message/event.
        with store.connect() as db:
            ids = db.execute("""SELECT i.turn FROM product_inputs i WHERE NOT EXISTS
                (SELECT 1 FROM events e WHERE e.turn=i.turn AND e.event_key='g4-submitted')""").fetchall()
        for (tid,) in ids:
            store.ensure_submission(tid)

    def cancel(self, tid):
        self.store.cancel(tid)
        with self.condition:
            if tid != self.active and self.store.job(tid)["status"] not in TERMINAL:
                self.pending[tid] = True
                self.condition.notify_all()

    def _execute_child(self, tid, resume):
        binding = self.store.binding(self.store.input_for(tid)["batch_id"])
        folder = self.store.root / "executor_runs" / uuid4().hex
        folder.mkdir(parents=True)
        command = [sys.executable, "-B", "-u", "-m", "genshin_corpus.local_chat.worker",
                   "--root", str(self.store.root), "--repo", str(self.repo), "--turn", tid,
                   "--run", str(folder), "--delay", str(self.delay)]
        if resume:
            command.append("--resume")
        env = dict(os.environ, PYTHONPATH=str(Path(self.repo) / "src"), PYTHONUTF8="1",
                   LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
        end = time.monotonic() + binding["settings"].get("worker_timeout_seconds", 120)
        startup = {}
        if os.name == "nt":
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 0
            startup["startupinfo"] = info
        with (folder / "worker.log").open("xb") as log:
            with child_process(command, cwd=self.repo, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                               **startup) as process:
                self.process = process
                try:
                    while process.poll() is None:
                        if self.stop_event.is_set():
                            put(folder / "shutdown.json", {"turn": tid})
                            end = min(end, time.monotonic() + 3)
                        require(time.monotonic() < end, "WORKER_TIMEOUT")
                        time.sleep(.04)
                    require(process.returncode == 0, "WORKER_INTERRUPTED")
                finally:
                    self.process = None


def main():
    parser = argparse.ArgumentParser()
    for arg in ("root", "repo", "turn", "run"):
        parser.add_argument("--" + arg, required=True)
    parser.add_argument("--delay", type=float, default=.025)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    store = ProductStore(args.root)
    run = inside(store.root / "executor_runs", args.run)
    store.shutdown_file = run / "shutdown.json"
    binding = store.binding(store.input_for(args.turn)["batch_id"])
    view = ExecutionView(store, binding, args.turn)
    write(run / "worker.json", {"pid": os.getpid(), "turn_id": args.turn, "batch_id": binding["id"]})
    if binding["mode"] == "LIVE":
        from ..rag.clean_research import run_turn
        from ..rag.clean_research_live import ResearchPipeline
        pipeline = ResearchPipeline(view, args.turn, args.repo)
        try:
            run_turn(view, args.turn, pipeline, continue_turn=args.resume)
        finally:
            pipeline.close()
    else:
        from ..rag.clean_conversation_graph import run_turn
        from ..rag.clean_mock import fixture_inputs, FixturePipeline, MockOpener, mock_answer
        from ..retrieval.clean_based.source import InputReader
        manifest = fixture_inputs(Path(args.repo), store.root / "mock_corpus")
        with InputReader(manifest) as reader:
            run_turn(view, args.turn, FixturePipeline(reader, args.repo), continue_turn=args.resume,
                     opener_factory=lambda packet, scenario: MockOpener(mock_answer(packet, scenario), delay=args.delay))


if __name__ == "__main__":
    main()
