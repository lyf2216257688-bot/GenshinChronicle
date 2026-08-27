from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from genshin_corpus.collector.storage import atomic_write, sha256

from .fingerprints import canonical_json_bytes


class CanonicalRunStore:
    """Filesystem boundary for immutable Canonical observation runs."""

    def __init__(self, root: Path, source: str, locale: str, canonical_run_id: str):
        self.root = root / source / locale / canonical_run_id
        self.records = self.root / "records"
        self.metadata = self.root / "metadata"

    @property
    def manifest_path(self) -> Path:
        return self.metadata / "manifest.json"

    def read_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        """Atomically checkpoint a run without replacing a completed conflict."""

        existing = self.read_manifest()
        if existing is not None and existing.get("status") == "complete" and existing != manifest:
            raise FileExistsError(f"Canonical run manifest is already complete: {self.manifest_path}")
        if existing != manifest:
            atomic_write(self.manifest_path, canonical_json_bytes(manifest))

    def write_record(self, record_id: str, body: bytes) -> dict[str, str]:
        """Write one deterministic record once; reject an immutable-byte conflict."""

        path = self.records / f"{sha256(record_id.encode('utf-8'))}.json"
        if path.exists() and path.read_bytes() != body:
            raise FileExistsError(f"Canonical record already exists with different bytes: {path}")
        if not path.exists():
            atomic_write(path, body)
        return {"path": str(path), "sha256": sha256(body), "record_id": record_id}


def blank_manifest(
    *,
    source: str,
    locale: str,
    canonical_run_id: str,
    parsed_run_id: str,
    parsed_manifest_path: str,
    parsed_manifest_sha256: str,
    dependencies: dict[str, Any],
) -> dict[str, Any]:
    """Create the deterministic, auditable Canonical-run accounting envelope."""

    return {
        "source": source,
        "locale": locale,
        "canonical_run_id": canonical_run_id,
        "parsed_run_id": parsed_run_id,
        "parsed_manifest_path": parsed_manifest_path,
        "parsed_manifest_sha256": parsed_manifest_sha256,
        "dependencies": dict(dependencies),
        "status": "partial",
        "records": [],
        "input_record_count": 0,
        "accounted_record_count": 0,
        "input_integrity_failure_count": 0,
        "input_integrity_failures": [],
        "reuse_count": 0,
        "reproject_count": 0,
        "counts": {
            "canonical": 0,
            "canonical_with_anomalies": 0,
            "blocked_integrity": 0,
        },
        "diagnostics": [],
    }
