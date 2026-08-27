from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON value deterministically without rewriting its meaning."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_record_id(
    *,
    parsed_run_id: str,
    parsed_identity_key: str,
    parsed_record_sha256: str,
) -> str:
    """Address one materialized observation using exactly the approved inputs."""

    return sha256_json([parsed_run_id, parsed_identity_key, parsed_record_sha256])


def canonical_dependency_fingerprint(observation: dict[str, Any], versions: dict[str, Any]) -> str:
    """Fingerprint explicit Parsed and Canonical dependencies, excluding paths."""

    return sha256_json({"canonical_versions": versions, "parsed_observation": observation})


def canonical_content_fingerprint(content_projection: dict[str, Any]) -> str:
    """Fingerprint Canonical content only, excluding run-scoped observation data."""

    return sha256_json(content_projection)
