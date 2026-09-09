"""Provider-free Qwen Batch JSONL construction and result materialization.

This module deliberately has no upload, job, polling, download, credential,
or network surface.  A later separately authorized lifecycle may supply its
local Batch output to ``materialize_qwen_batch_results``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .qwen_embedding import (
    QWEN_CORPUS_ROLE,
    QWEN_EMBEDDING_DIMENSION,
    QWEN_EMBEDDING_MODEL_ID,
    QwenEmbeddingPreflightError,
    QwenEmbeddingRequest,
    QwenEmbeddingResponse,
    _artifact_descriptor,
    _contains_sensitive_value,
    _select_document_units,
    _validate_response,
    _write_qwen_dense_artifact,
)


QWEN_BATCH_JSONL_SCHEMA_VERSION = "phase04-rag-qwen37-embedding-batch-jsonl-0.1"
QWEN_BATCH_EMBEDDINGS_PATH = "/v1/embeddings"
QWEN_BATCH_ENCODING_FORMAT = "float"


class QwenBatchEmbeddingError(ValueError):
    """Raised when Batch input or a local Batch result is not fail-closed valid."""


@dataclass(frozen=True)
class QwenBatchEmbeddingConfig:
    """Fixed document-only Batch operating point for selected W1 Retrieval Units."""

    retrieval_unit_manifest_path: Path
    document_unit_ids: tuple[str, ...]
    model_id: str = QWEN_EMBEDDING_MODEL_ID
    dimension: int = QWEN_EMBEDDING_DIMENSION
    encoding_format: str = QWEN_BATCH_ENCODING_FORMAT

    def __post_init__(self) -> None:
        if self.model_id != QWEN_EMBEDDING_MODEL_ID:
            raise QwenBatchEmbeddingError("Qwen Batch model must be qwen3.7-text-embedding")
        if self.dimension != QWEN_EMBEDDING_DIMENSION:
            raise QwenBatchEmbeddingError("Qwen Batch requires requested dimension 2048")
        if self.encoding_format != QWEN_BATCH_ENCODING_FORMAT:
            raise QwenBatchEmbeddingError("Qwen Batch requires encoding_format=float")
        if not self.document_unit_ids or any(not isinstance(unit_id, str) or not unit_id for unit_id in self.document_unit_ids):
            raise QwenBatchEmbeddingError("Qwen Batch requires non-empty document Retrieval Unit IDs")
        if len(set(self.document_unit_ids)) != len(self.document_unit_ids):
            raise QwenBatchEmbeddingError("Qwen Batch document Retrieval Unit IDs must be unique")


def _select_batch_document_units(
    config: QwenBatchEmbeddingConfig,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Reuse synchronous RU selection without inventing an equivalent path."""

    try:
        return _select_document_units(config.retrieval_unit_manifest_path, config.document_unit_ids)
    except QwenEmbeddingPreflightError as exc:
        raise QwenBatchEmbeddingError(str(exc)) from exc


def qwen_batch_custom_id(retrieval_unit_build_identity: str, unit: Mapping[str, Any]) -> str:
    """Bind one provider-safe custom ID to one exact RU identity and visible text."""

    unit_id = unit.get("unit_id")
    text = unit.get("retrieval_visible_text")
    if not isinstance(retrieval_unit_build_identity, str) or not retrieval_unit_build_identity:
        raise QwenBatchEmbeddingError("Qwen Batch Retrieval Unit build identity is invalid")
    if not isinstance(unit_id, str) or not unit_id or not isinstance(text, str) or not text:
        raise QwenBatchEmbeddingError("Qwen Batch Retrieval Unit identity/text is invalid")
    identity = sha256_json({
        "schema_version": QWEN_BATCH_JSONL_SCHEMA_VERSION,
        "retrieval_unit_build_identity": retrieval_unit_build_identity,
        "unit_id": unit_id,
        "retrieval_visible_text_sha256": sha256(text.encode("utf-8")).hexdigest(),
    })
    return f"qwen37-document-{identity}"


def build_qwen_batch_records(config: QwenBatchEmbeddingConfig) -> list[dict[str, Any]]:
    """Build deterministic OpenAI-compatible document embedding Batch records."""

    ru_manifest, units = _select_batch_document_units(config)
    build_identity = str(ru_manifest["build_identity"])
    return [
        {
            "custom_id": qwen_batch_custom_id(build_identity, unit),
            "method": "POST",
            "url": QWEN_BATCH_EMBEDDINGS_PATH,
            "body": {
                "model": config.model_id,
                "input": str(unit["retrieval_visible_text"]),
                "encoding_format": config.encoding_format,
                "dimensions": config.dimension,
            },
        }
        for unit in units
    ]


def build_qwen_batch_jsonl(config: QwenBatchEmbeddingConfig) -> bytes:
    """Serialize Batch records deterministically without any credential material."""

    return b"".join(canonical_json_bytes(record) + b"\n" for record in build_qwen_batch_records(config))


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    try:
        lines = Path(path).read_bytes().splitlines()
    except OSError as exc:
        raise QwenBatchEmbeddingError("Qwen Batch result JSONL is unavailable") from exc
    if not lines:
        raise QwenBatchEmbeddingError("Qwen Batch result JSONL must contain result rows")
    rows: list[Mapping[str, Any]] = []
    for index, line in enumerate(lines):
        if not line:
            raise QwenBatchEmbeddingError(f"Qwen Batch result row {index} is blank")
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QwenBatchEmbeddingError(f"Qwen Batch result row {index} is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping) or _contains_sensitive_value(value, ()):
            raise QwenBatchEmbeddingError(f"Qwen Batch result row {index} is malformed or credential-like")
        rows.append(value)
    return rows


def _result_vector(row: Mapping[str, Any], expected_custom_id: str) -> tuple[Sequence[float], str | None]:
    if row.get("custom_id") != expected_custom_id:
        raise QwenBatchEmbeddingError("Qwen Batch result custom_id association is invalid")
    if row.get("error") is not None:
        raise QwenBatchEmbeddingError("Qwen Batch provider error row is not materializable")
    response = row.get("response")
    if not isinstance(response, Mapping) or response.get("status_code") != 200:
        raise QwenBatchEmbeddingError("Qwen Batch result response status is invalid")
    body = response.get("body")
    if not isinstance(body, Mapping):
        raise QwenBatchEmbeddingError("Qwen Batch result response body is invalid")
    data = body.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
        raise QwenBatchEmbeddingError("Qwen Batch result must contain exactly one embedding")
    item = data[0]
    index = item.get("index")
    if index is not None and (type(index) is not int or index != 0):
        raise QwenBatchEmbeddingError("Qwen Batch result embedding row is malformed")
    if not isinstance(item.get("embedding"), list):
        raise QwenBatchEmbeddingError("Qwen Batch result embedding row is malformed")
    returned_model = body.get("model")
    if returned_model is not None and (not isinstance(returned_model, str) or not returned_model):
        raise QwenBatchEmbeddingError("Qwen Batch returned model is malformed")
    return item["embedding"], returned_model


def materialize_qwen_batch_results(
    config: QwenBatchEmbeddingConfig,
    result_jsonl_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Parse local provider-style Batch rows into the existing Dense artifact layout.

    This is provider-free parsing only. It deliberately records the Batch 2048
    wire contract as unknown until a separately authorized live Batch probe.
    """

    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError("Qwen Batch output root already exists")
    ru_manifest, units = _select_batch_document_units(config)
    records = build_qwen_batch_records(config)
    expected = {record["custom_id"]: unit for record, unit in zip(records, units, strict=True)}
    if len(expected) != len(records):
        raise QwenBatchEmbeddingError("Qwen Batch custom_id generation is not one-to-one with Retrieval Units")
    rows = _read_jsonl(Path(result_jsonl_path))
    actual_ids: set[str] = set()
    vectors_by_id: dict[str, Any] = {}
    returned_models: dict[str, str | None] = {}
    for row in rows:
        custom_id = row.get("custom_id")
        if not isinstance(custom_id, str) or custom_id not in expected:
            raise QwenBatchEmbeddingError("Qwen Batch result contains an unknown custom_id")
        if custom_id in actual_ids:
            raise QwenBatchEmbeddingError("Qwen Batch result contains a duplicate custom_id")
        actual_ids.add(custom_id)
        vector, returned_model = _result_vector(row, custom_id)
        request = QwenEmbeddingRequest(role=QWEN_CORPUS_ROLE, texts=(str(expected[custom_id]["retrieval_visible_text"]),))
        try:
            vectors_by_id[custom_id] = _validate_response(
                QwenEmbeddingResponse(
                    vectors=(vector,),
                    # Batch bodies are not persisted by this provider-free seam;
                    # vector validation does not consume the raw evidence bytes.
                    raw_response_bytes=b"{}",
                    returned_model=returned_model,
                ),
                request,
                (),
            )[0]
        except QwenEmbeddingPreflightError as exc:
            raise QwenBatchEmbeddingError(str(exc)) from exc
        returned_models[custom_id] = returned_model
    if actual_ids != set(expected):
        raise QwenBatchEmbeddingError("Qwen Batch result rows do not exactly match the expected custom_id set")

    document_request = QwenEmbeddingRequest(
        role=QWEN_CORPUS_ROLE,
        texts=tuple(str(unit["retrieval_visible_text"]) for unit in units),
    )
    import numpy as np

    document_vectors = np.asarray([vectors_by_id[record["custom_id"]] for record in records], dtype=np.float32)
    model_binding = "returned_model_verified" if all(value is not None for value in returned_models.values()) else "returned_model_not_available"
    remote_provenance = {
        "kind": "remote_provider",
        "transport_contract_version": QWEN_BATCH_JSONL_SCHEMA_VERSION,
        "execution_mode": "provider_free_batch_result_parse",
        "provider_api_status": "unverified_batch_result_only",
        "batch_2048_wire_acceptance": "unknown",
        "requested_model": QWEN_EMBEDDING_MODEL_ID,
        "document_returned_models": [
            {"custom_id": record["custom_id"], "model": returned_models[record["custom_id"]]}
            for record in records
        ],
        "document_model_binding": model_binding,
        "local_model_weight_sha256": None,
        "remote_model_weight_sha256": None,
        "weight_hash_status": "not_available_for_remote_provider",
    }
    metadata = {
        "model_name": QWEN_EMBEDDING_MODEL_ID,
        "model_revision": None,
        "model_sha256": None,
        "embedding_dimension": QWEN_EMBEDDING_DIMENSION,
        "dtype": "float32",
        "normalization": "L2",
        "instruction": None,
        "vectorization_schema_version": QWEN_BATCH_JSONL_SCHEMA_VERSION,
        "row_mapping_policy": "Batch custom_id -> W1 manifest order",
        "remote_provider_provenance": remote_provenance,
        "operating_point": {
            "endpoint_path": QWEN_BATCH_EMBEDDINGS_PATH,
            "encoding_format": QWEN_BATCH_ENCODING_FORMAT,
            "dimension": QWEN_EMBEDDING_DIMENSION,
        },
    }
    _, dense_manifest_body = _write_qwen_dense_artifact(
        output_root,
        ru_manifest=ru_manifest,
        units=units,
        document_request=document_request,
        document_vectors=document_vectors,
        metadata=metadata,
    )
    materialization = {
        "schema_version": QWEN_BATCH_JSONL_SCHEMA_VERSION,
        "status": "complete",
        "retrieval_unit_build_identity": ru_manifest["build_identity"],
        "expected_row_count": len(records),
        "result_row_count": len(rows),
        "batch_2048_wire_acceptance": "unknown",
        "dense_manifest": _artifact_descriptor("dense/metadata/manifest.json", dense_manifest_body),
        "remote_provider_provenance": remote_provenance,
    }
    materialization_body = canonical_json_bytes(materialization)
    atomic_write(output_root / "metadata" / "batch_materialization.json", materialization_body)
    return materialization
