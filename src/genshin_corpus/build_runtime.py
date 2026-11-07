"""Bounded, dynamically assigned record jobs and atomic build checkpoints."""
from __future__ import annotations

import json
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from itertools import islice
import multiprocessing
from pathlib import Path
import signal

from .collector.storage import atomic_write
from .evidence_io import inside, read, sha


def atomic_json(path: Path, value) -> None:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False) + "\n"
    atomic_write(path, body.encode("utf-8"))


def _worker_initializer(initializer, initargs):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if initializer is not None:
        initializer(*initargs)


def record_jobs(function, tasks, *, workers: int = 12, initializer=None, initargs=()):
    """Bounded one-record jobs. A dead worker fails the build instead of hanging."""
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    if workers == 1:
        if initializer is not None:
            initializer(*initargs)
        yield from map(function, tasks)
        return
    # Spawn also on Unix, so behaviour matches the supported Windows entry point.
    executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                                   initializer=_worker_initializer, initargs=(initializer, initargs))
    task_iterator = iter(tasks)
    try:
        pending = {executor.submit(function, task) for task in islice(task_iterator, workers * 2)}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
                for task in islice(task_iterator, 1):
                    pending.add(executor.submit(function, task))
    except BaseException:
        if hasattr(executor, "terminate_workers"):
            executor.terminate_workers()
        else:
            # Python 3.12 has no public terminate_workers. Capture its owned
            # processes before shutdown clears them, then stop and join them.
            processes = list((executor._processes or {}).values())
            executor.shutdown(wait=False, cancel_futures=True)
            for process in processes:
                if process.is_alive():
                    process.terminate()
            for process in processes:
                process.join()
        raise
    else:
        executor.shutdown(wait=True)


def checked_receipt(path: Path, root: Path, binding: str):
    """Only an atomically published, input-bound, hash-checked receipt is reusable."""
    receipt = read(path)
    if receipt.get("binding") != binding:
        raise ValueError("Build checkpoint binds different inputs or implementation")
    if not receipt.get("files"):
        raise ValueError("Build checkpoint has no artifacts")
    for name, checksum in receipt["files"].items():
        if sha(inside(root, name)) != checksum:
            raise ValueError("Build checkpoint artifact checksum mismatch")
    return receipt
