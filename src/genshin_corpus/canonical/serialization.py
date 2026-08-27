from __future__ import annotations

from .contracts import CanonicalRecord
from .fingerprints import canonical_json_bytes


def serialize_canonical_record(record: CanonicalRecord) -> bytes:
    """Return deterministic UTF-8 JSON for one in-memory Canonical record."""

    return canonical_json_bytes(record.to_dict())
