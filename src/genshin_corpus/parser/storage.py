from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..collector.storage import atomic_write, sha256, write_json


class ParsedRunStore:
    """Filesystem boundary for immutable Parsed runs."""

    def __init__(self, root: Path, source: str, locale: str, parsed_run_id: str):
        self.root = root / source / locale / parsed_run_id
        self.records = self.root / "records"
        self.metadata = self.root / "metadata"

    @property
    def manifest_path(self) -> Path:
        return self.metadata / "manifest.json"

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        write_json(self.manifest_path, manifest)

    def write_record(self, identity_key: str, value: dict[str, Any]) -> dict[str, Any]:
        digest = sha256(identity_key.encode("utf-8"))
        path = self.records / f"{digest}.json"
        body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        if path.exists() and path.read_bytes() != body:
            raise FileExistsError(f"Parsed record already exists with different bytes: {path}")
        if not path.exists():
            atomic_write(path, body)
        return {"path": str(path), "sha256": sha256(body), "identity_key": identity_key}


def blank_manifest(*, source: str, locale: str, parsed_run_id: str, raw_run_id: str, raw_manifest_sha256: str) -> dict[str, Any]:
    return {
        "source": source,
        "locale": locale,
        "parsed_run_id": parsed_run_id,
        "raw_run_id": raw_run_id,
        "raw_manifest_sha256": raw_manifest_sha256,
        "schema_version": "phase02-draft-0.1",
        "parser_version": "obc-foundation-0.1",
        "status": "partial",
        "records": [],
        "diagnostics": [],
        "counts": {
            "parsed": 0,
            "parsed_with_anomalies": 0,
            "preserved_unsupported": 0,
            "blocked_integrity": 0,
        },
    }
