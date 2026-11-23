"""Single product worker, reusing the G4 Mock and G5 Live graph entrypoints."""
import argparse
import logging
import os
from pathlib import Path
import sqlite3
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
from .credentials import worker_credential
from .store import ProductStore


class ProductExecutor(LocalExecutor):
    def __init__(self, store, repo, *, delay=.025):
        super().__init__(store.root, repo, delay=delay, store=store)

    def _recover_jobs(self):
        self.store.park_old_jobs()

    def cancel(self, tid):
        try:
            self.store.cancel(tid)
        finally:
            self.wake()

    def wake(self):
        with self.condition:
            self.condition.notify_all()

    def _loop(self):
        try:
            while not self.stop_event.is_set():
                try:
                    claim = self.store.claim()
                    with self.condition:
                        if claim is None:
                            self.condition.wait(.25)
                            continue
                        tid, resume = claim
                        self.active = tid
                except (OSError, sqlite3.Error) as exc:
                    self._storage_error(exc)
                    continue
                try:
                    self.store.job(tid)  # Do not let malformed job metadata block the queue head.
                    self.store.ensure_submission(tid)
                    if self.store.cancelled(tid):
                        self.store.ensure_cancellation(tid)
                    if not self.stop_event.is_set():
                        self._execute_child(tid, resume)
                    require(self.store.job(tid)["status"] in TERMINAL, "WORKER_UNSETTLED")
                except Exception as exc:
                    self._park(tid, exc)
                finally:
                    with self.condition:
                        self.active = None
                        self.condition.notify_all()
        finally:
            self.owner.__exit__(None, None, None)

    def _storage_error(self, exc):
        self.last_error = type(exc).__name__
        logging.getLogger(__name__).warning("Chat queue storage failure (%s)", self.last_error)
        self.stop_event.wait(.25)

    def _park(self, tid, exc):
        self.last_error = type(exc).__name__
        while True:
            try:
                self.store.interrupt(tid)
                break
            except (OSError, sqlite3.Error) as storage_error:
                self._storage_error(storage_error)
                if self.stop_event.is_set():
                    return  # The next owner will park this claim under the application lock.
        try:
            if self.store.job(tid)["status"] == "interrupted":
                self.store.event(tid, "g4-interrupted-" + uuid4().hex,
                    {"kind": "interrupted", "error_type": type(exc).__name__, "continue_required": True}, atomic=True)
        except (OSError, sqlite3.Error, ValueError, KeyError) as event_error:
            logging.getLogger(__name__).warning("Chat interruption event failed (%s)", type(event_error).__name__)

    def wait_idle(self, timeout=10):
        end = time.monotonic() + timeout
        with self.condition:
            while self.active or self.store.has_work():
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.condition.wait(min(left, .25))
        return True

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
        with worker_credential(store.root, binding["credential_revision"]):
            pipeline = ResearchPipeline(view, args.turn, args.repo,
                                        staged_local_queries=True, explicit_resume=args.resume)
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
