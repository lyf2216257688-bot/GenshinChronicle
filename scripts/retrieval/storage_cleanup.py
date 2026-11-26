"""Inventory the checkout and reclaim an explicitly approved list of regenerable caches."""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

from genshin_corpus.evidence_io import read, require, sha, write
from genshin_corpus.model_calls import locked


VERSION = "gc-storage-cache-plan-1"
DIRECT_CACHES = (".local/npm-cache/_logs", ".local/npm-cache/_npx", ".local/tmp/mcp-p0-codex-schema")
CODE_ROOTS = ("src", "scripts", "tests")


def is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def checked_stat(repo, relative):
    path = Path(relative)
    require(not path.is_absolute() and not path.drive and len(path.parts) > 0
            and all(p not in {"..", "."} and ":" not in p for p in path.parts), "Foreign cache path")
    current = repo
    for part in path.parts:
        current /= part
        info = current.lstat()
        require(not is_link(info), "Cache path has a symbolic link or reparse point")
    return current, info


def entries(folder):
    """Never follow a reparse point, including ones inside disposable directories."""
    folder = Path(folder)
    scan_path = folder
    if os.name == "nt" and folder.drive.endswith(":"):
        scan_path = "\\\\?\\" + str(folder)
    with os.scandir(scan_path) as listing:
        for node in sorted(listing, key=lambda item: item.name):
            info = node.stat(follow_symlinks=False)
            child = folder / node.name
            yield child, info
            if not is_link(info) and stat.S_ISDIR(info.st_mode):
                yield from entries(child)


def allocation(path, info):
    if os.name != "nt":
        return info.st_blocks * 512
    import msvcrt

    class StandardInfo(ctypes.Structure):
        _fields_ = [("allocation", ctypes.c_int64), ("length", ctypes.c_int64), ("links", wintypes.DWORD),
                    ("delete_pending", ctypes.c_ubyte), ("directory", ctypes.c_ubyte)]

    handle_info = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    handle_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    handle_info.restype = wintypes.BOOL
    value = StandardInfo()
    with path.open("rb") as file:
        ok = handle_info(msvcrt.get_osfhandle(file.fileno()), 1, ctypes.byref(value), ctypes.sizeof(value))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
    require(value.links == info.st_nlink and value.length == info.st_size and not value.delete_pending,
            "Cache filesystem state changed")
    return value.allocation


def identity(path, info):
    return {**{name[3:]: getattr(info, name) for name in ("st_dev", "st_ino", "st_nlink", "st_size", "st_mtime_ns")},
            "allocation_bytes": allocation(path, info), "sha256": sha(path)}


def tracked_files(repo):
    return {os.path.normcase(name) for name in subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=repo).decode("utf8").split("\0") if name}


def cache_plan(repo):
    tracked = tracked_files(repo)
    files, blocked = [], []
    for root in (*DIRECT_CACHES, *CODE_ROOTS):
        try:
            folder, info = checked_stat(repo, root)
        except FileNotFoundError:
            continue
        require(stat.S_ISDIR(info.st_mode), "Cache root is not a directory")
        for path, item in entries(folder):
            relative = path.relative_to(repo)
            if root in CODE_ROOTS and ("__pycache__" not in relative.parts or path.suffix != ".pyc"):
                continue
            if is_link(item):
                blocked.append({"path": relative.as_posix(), "reason": "reparse point"})
                continue
            if stat.S_ISDIR(item.st_mode):
                continue
            actual, info = checked_stat(repo, relative)
            if (os.path.normcase(str(relative)) in tracked or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                blocked.append({"path": relative.as_posix(), "reason": "tracked, shared, or nonregular"})
                continue
            files.append({"path": relative.as_posix(), "class": root if root in DIRECT_CACHES else "python-bytecode",
                          **identity(actual, info)})
    return {"version": VERSION, "repo": str(repo), "scope": "regenerable-caches-only",
            "protected": ["data", "config", ".git", ".local/runtime-py314", ".local/models",
                          ".local/npm-cache/_cacache", "frontend/node_modules"],
            "candidate_files": files, "blocked": blocked,
            "estimated_allocation_bytes": sum(item["allocation_bytes"] for item in files)}


def inventory(repo):
    result = {}
    for directory in sorted(repo.iterdir()):
        info = directory.lstat()
        members = entries(directory) if stat.S_ISDIR(info.st_mode) and not is_link(info) else ((directory, info),)
        total, files, links = 0, 0, 0
        for _, item in members:
            if is_link(item):
                links += 1
            elif stat.S_ISREG(item.st_mode):
                files += 1
                total += item.st_size
        result[directory.name] = {"logical_bytes": total, "files": files, "skipped_links": links}
    return {"repo": str(repo), "volume_free_bytes": shutil.disk_usage(repo).free, "roots": result}


def apply_caches(repo, plan_path, checksum, output):
    require(sha(plan_path) == checksum, "Cache plan digest differs")
    plan = read(plan_path)
    before = shutil.disk_usage(repo).free
    removed = []
    with locked(repo / ".local/storage-cleanup.lock"):
        require(plan == cache_plan(repo), "Cache inputs drifted. Make a new dry run.")
        output.mkdir(parents=True, exist_ok=False)
        write(output / "start.json", {"plan_sha256": checksum, "started_at": time.time(), "free_before": before})
        try:
            with (output / "records.jsonl").open("x", encoding="utf8", newline="\n") as journal:
                def record(number, action):
                    journal.write(json.dumps({"index": number, "action": action}) + "\n")
                    journal.flush()
                    os.fsync(journal.fileno())

                for number, entry in enumerate(plan["candidate_files"], 1):
                    path, info = checked_stat(repo, entry["path"])
                    require(identity(path, info) == {k: v for k, v in entry.items() if k not in {"path", "class"}},
                            "Cache file changed before unlink")
                    record(number, "started")
                    path.unlink()
                    removed.append(entry)
                    record(number, "removed")
        finally:
            write(output / "result.json", {
                "status": "complete" if len(removed) == len(plan["candidate_files"]) else "interrupted",
                "plan_sha256": checksum, "removed": len(removed),
                "released_allocation_bytes": sum(v["allocation_bytes"] for v in removed),
                "free_before": before, "free_after": shutil.disk_usage(repo).free})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("Inventory", "DryRun", "ApplyCaches"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    require((repo / "AGENTS.md").is_file() and (repo / ".git").exists(), "Use a GC checkout")
    if args.action == "Inventory":
        write(args.output, inventory(repo))
    elif args.action == "DryRun":
        write(args.output, cache_plan(repo))
    else:
        require(args.plan is not None and args.plan_sha256, "ApplyCaches requires an approved dry-run file and SHA-256")
        apply_caches(repo, args.plan, args.plan_sha256, args.output)


if __name__ == "__main__":
    main()
