import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional


_ATOMIC_REPLACE_ATTEMPTS = 3
_ATOMIC_REPLACE_BACKOFF = 0.01


def _replace_with_retry(source: str, target: Path) -> None:
    for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= _ATOMIC_REPLACE_ATTEMPTS:
                raise
            time.sleep(_ATOMIC_REPLACE_BACKOFF * (attempt + 1))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RunStore:
    def __init__(self, root: Path, source: str, locale: str, run_id: str):
        self.root = root / source / locale / run_id
        self.responses = self.root / "responses"
        self.metadata = self.root / "metadata"
        self.failures = self.root / "failures.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.metadata / "manifest.json"

    def response_paths(self, kind: str, key: str) -> tuple[Path, Path]:
        # Remote identifiers are evidence, not filesystem syntax. Keep the
        # original key in metadata while using a deterministic safe filename.
        storage_key = sha256(str(key).encode("utf-8"))
        raw_path = self.responses / kind / f"{storage_key}.json"
        meta_path = raw_path.with_suffix(".meta.json")
        return raw_path, meta_path

    def write_response(self, kind: str, key: str, body: bytes, meta: dict[str, Any]) -> dict[str, Any]:
        raw_path, meta_path = self.response_paths(kind, key)
        atomic_write(raw_path, body)
        record = {
            **meta,
            "kind": kind,
            "key": key,
            "raw_path": str(raw_path),
            "metadata_path": str(meta_path),
            "sha256": sha256(body),
            "ok": True,
        }
        write_json(meta_path, record)
        return record

    def load_response_record(self, kind: str, key: str) -> Optional[dict[str, Any]]:
        raw_path, meta_path = self.response_paths(kind, key)
        if not raw_path.exists() or not meta_path.exists():
            return None
        try:
            record = read_json(meta_path)
            if not record.get("ok") or record.get("status") != 200 or record.get("kind") != kind or record.get("key") != key:
                return None
            if not isinstance(record.get("sha256"), str):
                return None
            if sha256(raw_path.read_bytes()) != record["sha256"]:
                return None
            record.setdefault("raw_path", str(raw_path))
            record.setdefault("metadata_path", str(meta_path))
            return record
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            return None

    def valid_response(self, kind: str, key: str, sanity_check: Callable[[bytes], bool]) -> Optional[dict[str, Any]]:
        record = self.load_response_record(kind, key)
        if record is None:
            return None
        raw_path, _ = self.response_paths(kind, key)
        try:
            body = raw_path.read_bytes()
        except OSError:
            return None
        try:
            if not sanity_check(body):
                return None
        except Exception:
            return None
        return record

    def failure(self, record: dict[str, Any]) -> None:
        self.failures.parent.mkdir(parents=True, exist_ok=True)
        with self.failures.open("ab") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")
