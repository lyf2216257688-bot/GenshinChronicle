"""Single local background executor. UI reconnects never schedule old work."""
import atexit
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Condition, Event, Thread
from uuid import uuid4

from ..evidence_io import require, read
from ..indexing.benchmark_io import child_process
from ..model_calls import locked
from ..retrieval.clean_based.runtime_v2 import wait_pid
from .clean_conversation import ConversationStore, TERMINAL
from .clean_store import put


class LocalExecutor:
    def __init__(self, root, repo, *, delay=.015):
        self.store = ConversationStore(root)
        self.repo, self.delay = repo, delay
        self.condition, self.stop_event = Condition(), Event()
        self.pending, self.active, self.last_error = {}, None, None
        self.process = None
        self.owner = locked(self.store.root / "locks/executor.lock")
        self.owner.__enter__()
        try:
            # The application lock proves no CLI is running before deriving
            # interrupted status. Boot does not enqueue any prior submission.
            with locked(self.store.root / "locks/application.lock"):
                for job in self.store.jobs():
                    if job["status"] == "running": self.store.status(job["turn_id"], "interrupted")
            self.thread = Thread(target=self._loop, name="gc-g4-local-executor", daemon=True)
            self.thread.start()
        except BaseException:
            self.owner.__exit__(None, None, None)
            raise
        self.closed = False
        atexit.register(self.close)

    def schedule(self, tid, *, continue_turn=False):
        require(not self.stop_event.is_set(), "Application is stopping")
        job = self.store.job(tid)
        if job["status"] in TERMINAL: return
        with self.condition:
            if tid == self.active or tid in self.pending: return
            if continue_turn: self.store.request_continue(tid)
            else: require(job["status"] == "queued", "Interrupted turn requires explicit continue")
            self.pending[tid] = continue_turn
            self.condition.notify_all()

    def cancel(self, tid):
        self.store.cancel(tid)
        # Cancellation itself may deterministically close a queued/interrupted
        # turn; it authorizes reconciliation, never a new dispatch.
        with self.condition:
            if tid != self.active:
                self.pending[tid] = True
            self.condition.notify_all()

    def _loop(self):
        try:
            while not self.stop_event.is_set():
                with self.condition:
                    eligible = [tid for tid in self.pending if self.store.cancelled(tid) or self.store.next_turn() == tid]
                    if not eligible:
                        self.condition.wait(timeout=.25)
                        continue
                    tid = min(eligible, key=lambda t: self.store.turn(t)["sequence"])
                    resume = self.pending.pop(tid)
                    self.active = tid
                try:
                    self._execute_child(tid, resume)
                except Exception as exc:
                    self.last_error = type(exc).__name__
                    self.store.status(tid, "interrupted")
                    self.store.event(tid, "g4-interrupted-" + uuid4().hex,
                        {"kind": "interrupted", "error_type": type(exc).__name__, "continue_required": True})
                finally:
                    with self.condition:
                        self.active = None
                        self.condition.notify_all()
        finally:
            self.owner.__exit__(None, None, None)

    def _execute_child(self, tid, resume):
        """G3's offline guard mutates process-wide sockets. Isolate it from
        Streamlit's loopback sockets without weakening that guard or adapter.
        """
        folder = self.store.root / "executor_runs" / uuid4().hex
        folder.mkdir(parents=True)
        stop_file = folder / "shutdown.json"
        command = [sys.executable, "-B", "-u", "-m", "genshin_corpus.rag.clean_turn_worker",
                   "--root", str(self.store.root), "--repo", str(self.repo), "--turn", tid,
                   "--run-root", str(folder), "--delay", str(self.delay)]
        if resume: command.append("--continue-turn")
        env = dict(os.environ, PYTHONPATH=str(Path(self.repo) / "src"), PYTHONUTF8="1",
                   PYTHONDONTWRITEBYTECODE="1", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
        extra = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            extra["startupinfo"] = startup
        started, stopping = time.monotonic(), None
        settings = self.store.chat(self.store.turn(tid)["session"])["settings"]
        timeout = settings.get("worker_timeout_seconds", 90)
        with (folder / "worker.log").open("xb") as log:
            with child_process(command, cwd=self.repo, env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=log, **extra) as process:
                self.process = process
                try:
                    while process.poll() is None:
                        if self.stop_event.is_set():
                            put(stop_file, {"kind": "application_shutdown", "turn_id": tid})
                            stopping = stopping or time.monotonic()
                            require(time.monotonic() - stopping < 3, "WORKER_SHUTDOWN_TIMEOUT")
                        require(time.monotonic() - started < timeout, "LOCAL_WORKER_TIMEOUT")
                        time.sleep(.03)
                    require(process.returncode == 0, "LOCAL_WORKER_INTERRUPTED_SEE_LOCAL_LOG")
                finally:
                    self.process = None
        if (folder / "worker.json").exists(): wait_pid(read(folder / "worker.json")["pid"])

    def close(self):
        if self.closed: return
        self.stop_event.set()
        with self.condition: self.condition.notify_all()
        self.thread.join(timeout=10)
        # The OS releases ownership on forced process exit. A live worker keeps
        # its lock until it exits; never close another thread's file handle.
        self.closed = True
        atexit.unregister(self.close)

    def wait_idle(self, timeout=10):
        """Bounded test/CLI convenience, never used to block the Streamlit UI."""
        import time
        end = time.monotonic() + timeout
        with self.condition:
            while self.active or self.pending:
                left = end - time.monotonic()
                if left <= 0: return False
                self.condition.wait(min(left, .25))
        return True
