"""Bounded experiment IO. Reuse the repository's atomic writer and kernel locks."""
from collections import Counter
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from ..build_runtime import atomic_json, checked_receipt
from ..evidence_io import digest, inside, read, require, sha
from ..retrieval.local_qwen3_build import build_lock, offline_only, prevent_idle_sleep
from .inputs import iter_inputs

VERSION = "gc-index-benchmark-1"
GIB = 1024 ** 3


@contextmanager
def child_process(command, **kwargs):
    """Own the Windows venv redirector AND its interpreter, including parent death.

    Launch suspended so the redirector cannot spawn outside the job before it
    is assigned. Closing the job's sole handle kills remaining descendants.
    """
    if os.name != "nt":
        process = subprocess.Popen(command, **kwargs)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
        return
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                    ("peak_job_memory", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    native = ctypes.WinDLL("ntdll")
    native.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    native.NtResumeProcess.restype = ctypes.c_long
    job = kernel.CreateJobObjectW(None, None)
    require(bool(job), "Cannot create child-process job")
    process = None
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        require(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                "Cannot set child-process job limits")
        process = subprocess.Popen(command, creationflags=0x4, **kwargs)  # CREATE_SUSPENDED
        require(kernel.AssignProcessToJobObject(job, int(process._handle)), "Cannot own child process tree")
        require(native.NtResumeProcess(int(process._handle)) >= 0, "Cannot resume assigned child process")
        yield process
    finally:
        kernel.CloseHandle(job)
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait()


def resources():
    result = {}
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        require(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)), "Cannot inspect RAM")
        result["available_ram_bytes"] = memory.available
    try:
        import psutil
        result.update(available_ram_bytes=psutil.virtual_memory().available,
                      rss_bytes=psutil.Process().memory_info().rss)
    except ImportError:
        pass
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_initialized():
        free, total = torch.cuda.mem_get_info()
        result["cuda"] = {"free_bytes": free, "total_bytes": total,
                          "allocated_bytes": torch.cuda.memory_allocated(),
                          "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                          "peak_reserved_bytes": torch.cuda.max_memory_reserved()}
    return result


def disk_guard(root, config, *, additional_bytes=0):
    free = shutil.disk_usage(root).free
    needed = int(config["minimum_free_gib"] * GIB) + additional_bytes
    require(free >= needed, f"Disk guard needs {needed} free bytes, found {free}")
    return {"free_bytes": free, "required_free_bytes": needed}


def ram_guard(config, *, model_loaded=False):
    observed = resources()
    minimum = config["minimum_running_ram_gib"] if model_loaded else config["minimum_available_ram_gib"]
    require(observed.get("available_ram_bytes", 0) >= minimum * GIB,
            "Insufficient available system RAM. See resource events and resume after freeing RAM")
    return observed


def event(root, name, **details):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    value = {"utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
             "event": name, "resources": resources(), **details}
    line = json.dumps(value, ensure_ascii=False, allow_nan=False)
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
    print(line, flush=True)
    return value


def attempt(root, prefix):
    path = Path(root) / f"{prefix}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def dump_lines(path, values):
    with Path(path).open("xb") as handle:
        for value in values:
            handle.write((json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def lines(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def batches(manifest, size):
    pending = []
    for number, row in enumerate(iter_inputs(manifest)):
        pending.append((number, row))
        if len(pending) == size:
            yield pending
            pending = []
    if pending:
        yield pending


def mapping(number, row):
    return {"input_row": number, **{key: row[key] for key in
            ("chunk_id", "parent_id", "observation_id", "source_item", "dense_text_sha256",
             "granularities", "display_order_by_granularity", "chunk_locator", "parent_locator", "graph_locator")}}


def routes(configuration, input_manifest, kind):
    from .schema import representation_id
    bundle = read(input_manifest)["input_bundle_id"]
    return {level: representation_id(bundle, level, kind, configuration) for level in ("micro", "macro")}


def receipt(folder, binding, **metadata):
    folder = Path(folder)
    value = {"binding": binding, "files": {p.name: sha(p) for p in folder.iterdir() if p.is_file()}, **metadata}
    atomic_json(folder / "complete.json", value)
    return value


def verify_receipt(folder, binding):
    value = checked_receipt(Path(folder) / "complete.json", Path(folder), binding)
    expected = set(value["files"]) | {"complete.json"}
    require({p.name for p in Path(folder).iterdir()} == expected, "Unexpected committed shard artifact")
    return value


def check_artifacts(root, files):
    for name, checksum in files.items():
        require(sha(inside(root, name)) == checksum, "Artifact checksum mismatch " + name)


def code_binding(repo):
    paths = sorted((repo / "src/genshin_corpus/indexing").glob("*.py"))
    paths += [repo / name for name in ("src/genshin_corpus/build_runtime.py", "src/genshin_corpus/evidence_io.py",
                                     "src/genshin_corpus/collector/storage.py",
                                     "src/genshin_corpus/retrieval/local_qwen3_build.py",
                                     "scripts/retrieval/run-index-benchmark.ps1")]
    return {p.relative_to(repo).as_posix(): sha(p) for p in paths}


def completed_input(path):
    counts = Counter({"documents": 0, "micro": 0, "macro": 0})
    for row in iter_inputs(path):
        counts["documents"] += 1
        counts.update(row["granularities"])
    require(counts["documents"] > 0, "Empty index experiment input")
    return dict(counts)
