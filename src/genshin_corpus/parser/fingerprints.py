from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON values deterministically without semantic rewriting."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def source_fingerprint(value: Any) -> str:
    """Fingerprint the exact decoded Raw sub-value supplied by the caller."""

    return _sha256(value)


def parsed_fingerprint(value: Any) -> str:
    """Fingerprint a parser-provided semantic projection.

    The helper intentionally does not remove timestamps, paths, diagnostics,
    or other fields: only the parser knows which projection is semantic.
    """

    return _sha256(value)
