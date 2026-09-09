"""Injected-only Qwen embedding challenger preflight support.

This module deliberately defines no HTTP endpoint, SDK, region, or Batch
contract.  Its transport is a local test/preflight boundary: a later live
adapter must separately prove how the provider maps these semantic requests to
its documented wire interface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import gzip
import json
from pathlib import Path
import re
from typing import Any, Protocol

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .candidate_retrieval import DENSE_INDEX_SCHEMA_VERSION, dense_candidates
from .retrieval_units import load_retrieval_units


QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION = "phase04-rag-qwen37-embedding-sync-preflight-0.1"
QWEN_INJECTED_TRANSPORT_CONTRACT_VERSION = "phase04-rag-qwen37-embedding-injected-transport-0.1"
QWEN_EMBEDDING_MODEL_ID = "qwen3.7-text-embedding"
QWEN_EMBEDDING_DIMENSION = 2048
QWEN_EMBEDDING_OUTPUT = "dense"
QWEN_CORPUS_ROLE = "document"
QWEN_QUERY_ROLE = "query"

_SAFE_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9._-]+$")
_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "credential", "secret", "password")


class QwenEmbeddingPreflightError(ValueError):
    """Raised for an invalid challenger request or provider response."""


@dataclass(frozen=True)
class QwenEmbeddingRequest:
    """Semantic request passed to the injected seam, not a provider wire body."""

    role: str
    texts: tuple[str, ...]
    model_id: str = QWEN_EMBEDDING_MODEL_ID
    dimension: int = QWEN_EMBEDDING_DIMENSION
    output: str = QWEN_EMBEDDING_OUTPUT
    custom_query_instruction: None = None

    def __post_init__(self) -> None:
        if self.role not in (QWEN_CORPUS_ROLE, QWEN_QUERY_ROLE):
            raise QwenEmbeddingPreflightError("Qwen embedding role must be document or query")
        if self.model_id != QWEN_EMBEDDING_MODEL_ID:
            raise QwenEmbeddingPreflightError("Qwen challenger model must be qwen3.7-text-embedding")
        if self.dimension != QWEN_EMBEDDING_DIMENSION:
            raise QwenEmbeddingPreflightError("Qwen challenger requires requested dimension 2048")
        if self.output != QWEN_EMBEDDING_OUTPUT:
            raise QwenEmbeddingPreflightError("Qwen challenger requires dense-only output")
        if self.custom_query_instruction is not None:
            raise QwenEmbeddingPreflightError("Qwen challenger custom query instruction must be null")
        if not self.texts or any(not isinstance(text, str) or not text for text in self.texts):
            raise QwenEmbeddingPreflightError("Qwen embedding request texts must be non-empty strings")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "transport_contract_version": QWEN_INJECTED_TRANSPORT_CONTRACT_VERSION,
            "model_id": self.model_id,
            "dimension": self.dimension,
            "output": self.output,
            "role": self.role,
            "custom_query_instruction": self.custom_query_instruction,
            "texts": list(self.texts),
        }

    @property
    def input_identity(self) -> str:
        return sha256_json({"role": self.role, "texts": list(self.texts)})

    @property
    def request_identity(self) -> str:
        return sha256_json(self.identity_projection())


@dataclass(frozen=True)
class QwenEmbeddingResponse:
    """Normalized response supplied by a future adapter or an injected test.

    ``returned_model`` and ``returned_role`` are optional because their
    availability is provider-contract evidence, not an assumption made here.
    ``raw_response_bytes`` must be the provider response before local vector
    normalization; this seam preserves it only after secret-safe validation.
    """

    vectors: Sequence[Sequence[float]]
    raw_response_bytes: bytes
    returned_model: str | None = None
    returned_role: str | None = None
    provider_request_id: str | None = None


class QwenEmbeddingTransportError(Exception):
    """A safe, normalized injected-transport error with optional raw evidence."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        raw_response_bytes: bytes | None = None,
    ) -> None:
        if not isinstance(code, str) or not _SAFE_PROVIDER_CODE.fullmatch(code):
            raise QwenEmbeddingPreflightError("Qwen transport error code is invalid")
        if status_code is not None and (
            not isinstance(status_code, int) or isinstance(status_code, bool) or not 100 <= status_code <= 599
        ):
            raise QwenEmbeddingPreflightError("Qwen transport error status must be an HTTP status integer or null")
        if raw_response_bytes is not None and not isinstance(raw_response_bytes, bytes):
            raise QwenEmbeddingPreflightError("Qwen transport error raw response must be bytes or null")
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.raw_response_bytes = raw_response_bytes


class QwenEmbeddingTransport(Protocol):
    """Qwen-only semantic seam. It intentionally does not specify wire details."""

    def embed(self, request: QwenEmbeddingRequest) -> QwenEmbeddingResponse:
        """Return a normalized response or raise :class:`QwenEmbeddingTransportError`."""


@dataclass(frozen=True)
class QwenSynchronousPreflightConfig:
    """The fixed first-challenger operating point and deterministic probe inputs."""

    retrieval_unit_manifest_path: Path
    document_unit_ids: tuple[str, ...]
    query_text: str
    model_id: str = QWEN_EMBEDDING_MODEL_ID
    dimension: int = QWEN_EMBEDDING_DIMENSION
    output: str = QWEN_EMBEDDING_OUTPUT
    corpus_role: str = QWEN_CORPUS_ROLE
    query_role: str = QWEN_QUERY_ROLE
    custom_query_instruction: None = None

    def __post_init__(self) -> None:
        if self.model_id != QWEN_EMBEDDING_MODEL_ID:
            raise QwenEmbeddingPreflightError("Qwen challenger model must be qwen3.7-text-embedding")
        if self.dimension != QWEN_EMBEDDING_DIMENSION:
            raise QwenEmbeddingPreflightError("Qwen challenger requires requested dimension 2048")
        if self.output != QWEN_EMBEDDING_OUTPUT:
            raise QwenEmbeddingPreflightError("Qwen challenger requires dense-only output")
        if self.corpus_role != QWEN_CORPUS_ROLE or self.query_role != QWEN_QUERY_ROLE:
            raise QwenEmbeddingPreflightError("Qwen challenger requires document corpus role and query query role")
        if self.custom_query_instruction is not None:
            raise QwenEmbeddingPreflightError("Qwen challenger custom query instruction must be null")
        if not self.document_unit_ids or any(not isinstance(unit_id, str) or not unit_id for unit_id in self.document_unit_ids):
            raise QwenEmbeddingPreflightError("Qwen preflight requires non-empty document Retrieval Unit IDs")
        if len(set(self.document_unit_ids)) != len(self.document_unit_ids):
            raise QwenEmbeddingPreflightError("Qwen preflight document Retrieval Unit IDs must be unique")
        if not isinstance(self.query_text, str) or not self.query_text:
            raise QwenEmbeddingPreflightError("Qwen preflight query text must be non-empty")


@dataclass(frozen=True)
class _AttemptResult:
    role: str
    request: QwenEmbeddingRequest
    vectors: Any | None
    returned_model: str | None
    returned_role: str | None
    provider_request_id: str | None
    response_artifact: Mapping[str, Any] | None
    error: Mapping[str, Any] | None


def _artifact_descriptor(path: str, body: bytes, count: int | None = None) -> dict[str, Any]:
    descriptor: dict[str, Any] = {"path": path, "sha256": sha256(body).hexdigest(), "byte_count": len(body)}
    if count is not None:
        descriptor["row_count"] = count
    return descriptor


def _gzip_jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    raw = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)
    output = BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as handle:
        handle.write(raw)
    return output.getvalue()


def _contains_sensitive_value(value: Any, secret_values: tuple[str, ...]) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                return True
            normalized = key.lower().replace("-", "_")
            compact = re.sub(r"[^a-z0-9]", "", normalized)
            if any(part in normalized or part.replace("_", "") in compact for part in _SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_value(item, secret_values):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive_value(item, secret_values) for item in value)
    return isinstance(value, str) and any(secret in value for secret in secret_values)


def _safe_response_bytes(raw_response_bytes: bytes, secret_values: tuple[str, ...]) -> bytes:
    if not isinstance(raw_response_bytes, bytes) or not raw_response_bytes:
        raise QwenEmbeddingPreflightError("Qwen response evidence must be non-empty bytes")
    try:
        decoded = json.loads(raw_response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenEmbeddingPreflightError("Qwen response evidence must be UTF-8 JSON") from exc
    if _contains_sensitive_value(decoded, secret_values):
        raise QwenEmbeddingPreflightError("Qwen response evidence contains credential-like data")
    return raw_response_bytes


def _persist_response(
    output_root: Path,
    *,
    role: str,
    raw_response_bytes: bytes | None,
    secret_values: tuple[str, ...],
) -> dict[str, Any] | None:
    if raw_response_bytes is None:
        return None
    body = _safe_response_bytes(raw_response_bytes, secret_values)
    relative_path = f"responses/{role}.response.json"
    atomic_write(output_root / relative_path, body)
    return _artifact_descriptor(relative_path, body)


def _validate_response(
    response: QwenEmbeddingResponse,
    request: QwenEmbeddingRequest,
    secret_values: tuple[str, ...],
) -> Any:
    if response.returned_model is not None and response.returned_model != request.model_id:
        raise QwenEmbeddingPreflightError("Qwen returned model does not match requested model")
    if response.returned_role is not None and response.returned_role != request.role:
        raise QwenEmbeddingPreflightError("Qwen returned role does not match requested role")
    if response.provider_request_id is not None and (
        not isinstance(response.provider_request_id, str) or not response.provider_request_id
    ):
        raise QwenEmbeddingPreflightError("Qwen provider request ID must be a non-empty string or null")
    if response.provider_request_id is not None and any(secret in response.provider_request_id for secret in secret_values):
        raise QwenEmbeddingPreflightError("Qwen provider request ID contains credential-like data")
    try:
        import numpy as np

        values = np.asarray(response.vectors, dtype=np.float32)
    except Exception as exc:
        raise QwenEmbeddingPreflightError("Qwen response vectors are not numeric") from exc
    if (
        values.ndim != 2
        or values.shape[0] != len(request.texts)
        or values.shape[1] != request.dimension
        or not np.isfinite(values).all()
    ):
        raise QwenEmbeddingPreflightError("Qwen response vectors do not satisfy the requested 2048-dimensional contract")
    norms = np.linalg.norm(values, axis=1)
    if np.any(norms == 0):
        raise QwenEmbeddingPreflightError("Qwen response vectors must be non-zero")
    return values / norms[:, None]


def _issued_attempt_record(request: QwenEmbeddingRequest, number: int) -> dict[str, Any]:
    """Create the durable record that must exist before transport invocation."""

    return {
        "schema_version": QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION,
        "attempt_number": number,
        "attempt_id": sha256_json({
            "schema_version": QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION,
            "attempt_number": number,
            "role": request.role,
            "request_identity": request.request_identity,
        }),
        "role": request.role,
        "request_identity": request.request_identity,
        "input_identity": request.input_identity,
        "status": "issued",
    }


def _resolve_attempt(issued: Mapping[str, Any], result: _AttemptResult) -> dict[str, Any]:
    """Resolve exactly the durable issued attempt; never add a second record."""

    if (
        issued.get("role") != result.role
        or issued.get("request_identity") != result.request.request_identity
        or issued.get("input_identity") != result.request.input_identity
    ):
        raise QwenEmbeddingPreflightError("Qwen attempt resolution does not match the issued request")
    record = dict(issued)
    record["status"] = "succeeded" if result.error is None else "failed"
    if result.error is None:
        record.update({
            "returned_model": result.returned_model,
            "returned_role": result.returned_role,
            "provider_request_id": result.provider_request_id,
            "response_artifact": dict(result.response_artifact or {}),
        })
    else:
        record["error"] = dict(result.error)
        if result.response_artifact is not None:
            record["response_artifact"] = dict(result.response_artifact)
    return record


def _write_attempt_ledger(output_root: Path, attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    body = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in attempts)
    relative_path = "metadata/provider_attempts.jsonl"
    atomic_write(output_root / relative_path, body)
    return _artifact_descriptor(relative_path, body, len(attempts))


def _issue_and_resolve_attempt(
    output_root: Path,
    attempts: list[dict[str, Any]],
    transport: QwenEmbeddingTransport,
    request: QwenEmbeddingRequest,
    *,
    secret_values: tuple[str, ...],
) -> tuple[_AttemptResult, dict[str, Any]]:
    """Persist issuance before one call, then resolve that exact ledger row."""

    issued = _issued_attempt_record(request, len(attempts) + 1)
    attempts.append(issued)
    # atomic_write fsyncs this ledger body before the transport can observe it.
    _write_attempt_ledger(output_root, attempts)
    result = _run_attempt(output_root, transport, request, secret_values=secret_values)
    attempts[-1] = _resolve_attempt(issued, result)
    return result, _write_attempt_ledger(output_root, attempts)


def _run_attempt(
    output_root: Path,
    transport: QwenEmbeddingTransport,
    request: QwenEmbeddingRequest,
    *,
    secret_values: tuple[str, ...],
) -> _AttemptResult:
    try:
        response = transport.embed(request)
    except QwenEmbeddingTransportError as exc:
        response_artifact: dict[str, Any] | None = None
        if exc.raw_response_bytes is not None:
            try:
                response_artifact = _persist_response(
                    output_root,
                    role=request.role,
                    raw_response_bytes=exc.raw_response_bytes,
                    secret_values=secret_values,
                )
            except QwenEmbeddingPreflightError:
                return _AttemptResult(
                    role=request.role,
                    request=request,
                    vectors=None,
                    returned_model=None,
                    returned_role=None,
                    provider_request_id=None,
                    response_artifact=None,
                    error={"category": "response_evidence_rejected", "code": "UnsafeResponseEvidence"},
                )
        return _AttemptResult(
            role=request.role,
            request=request,
            vectors=None,
            returned_model=None,
            returned_role=None,
            provider_request_id=None,
            response_artifact=response_artifact,
            error={"category": "transport_error", "code": exc.code, "status_code": exc.status_code},
        )
    if not isinstance(response, QwenEmbeddingResponse):
        return _AttemptResult(
            role=request.role,
            request=request,
            vectors=None,
            returned_model=None,
            returned_role=None,
            provider_request_id=None,
            response_artifact=None,
            error={"category": "response_invalid", "code": "UnexpectedResponseType"},
        )
    response_artifact: dict[str, Any] | None = None
    try:
        response_artifact = _persist_response(
            output_root,
            role=request.role,
            raw_response_bytes=response.raw_response_bytes,
            secret_values=secret_values,
        )
        vectors = _validate_response(response, request, secret_values)
    except QwenEmbeddingPreflightError as exc:
        return _AttemptResult(
            role=request.role,
            request=request,
            vectors=None,
            returned_model=None,
            returned_role=None,
            provider_request_id=None,
            response_artifact=response_artifact,
            error={"category": "response_invalid", "code": type(exc).__name__},
        )
    return _AttemptResult(
        role=request.role,
        request=request,
        vectors=vectors,
        returned_model=response.returned_model,
        returned_role=response.returned_role,
        provider_request_id=response.provider_request_id,
        response_artifact=response_artifact,
        error=None,
    )


def _select_document_units(config: QwenSynchronousPreflightConfig) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    try:
        manifest, all_units = load_retrieval_units(Path(config.retrieval_unit_manifest_path))
    except Exception as exc:
        raise QwenEmbeddingPreflightError("Qwen preflight requires a complete Retrieval Unit artifact") from exc
    build_identity = manifest.get("build_identity")
    if not isinstance(build_identity, str) or not build_identity:
        raise QwenEmbeddingPreflightError("Qwen preflight Retrieval Unit manifest lacks build identity")
    positions = {str(unit.get("unit_id")): index for index, unit in enumerate(all_units)}
    if any(unit_id not in positions for unit_id in config.document_unit_ids):
        raise QwenEmbeddingPreflightError("Qwen preflight document unit is absent from the Retrieval Unit artifact")
    selected_positions = [positions[unit_id] for unit_id in config.document_unit_ids]
    if selected_positions != sorted(selected_positions):
        raise QwenEmbeddingPreflightError("Qwen preflight document units must follow Retrieval Unit manifest order")
    selected = [all_units[index] for index in selected_positions]
    if any(not isinstance(unit.get("retrieval_visible_text"), str) or not unit["retrieval_visible_text"] for unit in selected):
        raise QwenEmbeddingPreflightError("Qwen preflight Retrieval Unit text is invalid")
    return manifest, selected


def _preflight_identity(
    config: QwenSynchronousPreflightConfig,
    ru_manifest: Mapping[str, Any],
    units: Sequence[Mapping[str, Any]],
) -> str:
    return sha256_json({
        "schema_version": QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION,
        "retrieval_unit_build_identity": ru_manifest["build_identity"],
        "documents": [
            {"unit_id": unit["unit_id"], "text_sha256": sha256(unit["retrieval_visible_text"].encode("utf-8")).hexdigest()}
            for unit in units
        ],
        "query_text": config.query_text,
        "operating_point": {
            "model_id": config.model_id,
            "dimension": config.dimension,
            "output": config.output,
            "corpus_role": config.corpus_role,
            "query_role": config.query_role,
            "custom_query_instruction": config.custom_query_instruction,
        },
    })


def _write_preflight_manifest(output_root: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    body = canonical_json_bytes(dict(value))
    relative_path = "metadata/preflight_manifest.json"
    atomic_write(output_root / relative_path, body)
    return _artifact_descriptor(relative_path, body)


def run_qwen_synchronous_preflight(
    config: QwenSynchronousPreflightConfig,
    output_root: Path,
    transport: QwenEmbeddingTransport,
    *,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Run two injected semantic probes and write a local compatibility artifact.

    The successful result proves only this module's local request/response and
    storage contracts.  It does not prove an endpoint, region, SDK, Batch API,
    or live Qwen capability.
    """

    if not callable(getattr(transport, "embed", None)):
        raise QwenEmbeddingPreflightError("Qwen preflight transport lacks the injected embed method")
    secrets = tuple(secret_values)
    if any(not isinstance(value, str) or not value for value in secrets):
        raise QwenEmbeddingPreflightError("Qwen preflight secret values must be non-empty strings")
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError("Qwen preflight output root already exists")
    ru_manifest, units = _select_document_units(config)
    preflight_identity = _preflight_identity(config, ru_manifest, units)
    document_request = QwenEmbeddingRequest(
        role=QWEN_CORPUS_ROLE,
        texts=tuple(str(unit["retrieval_visible_text"]) for unit in units),
    )
    query_request = QwenEmbeddingRequest(role=QWEN_QUERY_ROLE, texts=(config.query_text,))
    output_root.mkdir(parents=True)
    attempts: list[dict[str, Any]] = []

    document, ledger = _issue_and_resolve_attempt(
        output_root, attempts, transport, document_request, secret_values=secrets
    )
    if document.error is not None:
        result = {
            "schema_version": QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION,
            "status": "failed",
            "preflight_identity": preflight_identity,
            "retrieval_unit_build_identity": ru_manifest["build_identity"],
            "provider_api_status": "unverified_injected_only",
            "provider_attempts": ledger,
            "failure": dict(document.error),
        }
        _write_preflight_manifest(output_root, result)
        return result

    query, ledger = _issue_and_resolve_attempt(
        output_root, attempts, transport, query_request, secret_values=secrets
    )
    if query.error is not None:
        result = {
            "schema_version": QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION,
            "status": "partial",
            "preflight_identity": preflight_identity,
            "retrieval_unit_build_identity": ru_manifest["build_identity"],
            "provider_api_status": "unverified_injected_only",
            "provider_attempts": ledger,
            "document_response_artifact": dict(document.response_artifact or {}),
            "failure": dict(query.error),
        }
        _write_preflight_manifest(output_root, result)
        return result

    if document.vectors is None or query.vectors is None:
        raise AssertionError("successful Qwen preflight attempts require vectors")
    document_vectors = document.vectors
    vector_file = BytesIO()
    import numpy as np

    np.save(vector_file, document_vectors.astype(np.float32), allow_pickle=False)
    vector_body = vector_file.getvalue()
    row_rows = [{"occurrence_index": index, "unit_id": str(unit["unit_id"])} for index, unit in enumerate(units)]
    row_body = _gzip_jsonl(row_rows)
    remote_provenance = {
        "kind": "remote_provider",
        "transport_contract_version": QWEN_INJECTED_TRANSPORT_CONTRACT_VERSION,
        "provider_api_status": "unverified_injected_only",
        "requested_model": QWEN_EMBEDDING_MODEL_ID,
        "document_returned_model": document.returned_model,
        "query_returned_model": query.returned_model,
        "document_model_binding": "returned_model_verified" if document.returned_model is not None else "returned_model_not_available",
        "query_model_binding": "returned_model_verified" if query.returned_model is not None else "returned_model_not_available",
        "local_model_weight_sha256": None,
        "remote_model_weight_sha256": None,
        "weight_hash_status": "not_available_for_remote_provider",
    }
    metadata = {
        "model_name": QWEN_EMBEDDING_MODEL_ID,
        "model_revision": document.returned_model,
        "model_sha256": None,
        "embedding_dimension": QWEN_EMBEDDING_DIMENSION,
        "dtype": "float32",
        "normalization": "L2",
        "instruction": None,
        "vectorization_schema_version": QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION,
        "row_mapping_policy": "W1 manifest order",
        "remote_provider_provenance": remote_provenance,
        "operating_point": {
            "output": QWEN_EMBEDDING_OUTPUT,
            "corpus_role": QWEN_CORPUS_ROLE,
            "query_role": QWEN_QUERY_ROLE,
            "custom_query_instruction": None,
        },
    }
    arm_build_identity = sha256_json({
        "retrieval_unit_build_identity": ru_manifest["build_identity"],
        "dense_contract": metadata,
        "document_request_identity": document_request.request_identity,
    })
    dense_manifest = {
        "schema_version": DENSE_INDEX_SCHEMA_VERSION,
        "status": "complete",
        "arm": "dense",
        "arm_build_identity": arm_build_identity,
        "retrieval_unit_build_identity": ru_manifest["build_identity"],
        **metadata,
        "row_count": len(row_rows),
        "artifacts": {
            "vectors": _artifact_descriptor("artifacts/vectors.f32.npy", vector_body),
            "rows": _artifact_descriptor("artifacts/rows.jsonl.gz", row_body, len(row_rows)),
        },
    }
    dense_root = output_root / "dense"
    atomic_write(dense_root / "artifacts" / "vectors.f32.npy", vector_body)
    atomic_write(dense_root / "artifacts" / "rows.jsonl.gz", row_body)
    dense_manifest_body = canonical_json_bytes(dense_manifest)
    atomic_write(dense_root / "metadata" / "manifest.json", dense_manifest_body)

    candidates = dense_candidates(
        dense_root / "metadata" / "manifest.json",
        query.vectors[0],
        top_k=min(len(row_rows), 20),
        query_instruction=None,
    )
    scoring_probe = {
        "query_request_identity": query_request.request_identity,
        "candidate_count": len(candidates),
        "candidate_unit_ids": [item["unit_id"] for item in candidates],
        "query_config_identity": candidates[0]["retrieval"]["query_config_identity"] if candidates else None,
    }
    scoring_body = canonical_json_bytes(scoring_probe)
    atomic_write(output_root / "metadata" / "scoring_probe.json", scoring_body)
    result = {
        "schema_version": QWEN_SYNCHRONOUS_PREFLIGHT_SCHEMA_VERSION,
        "status": "complete",
        "preflight_identity": preflight_identity,
        "retrieval_unit_build_identity": ru_manifest["build_identity"],
        "provider_api_status": "unverified_injected_only",
        "provider_attempts": ledger,
        "dense_manifest": _artifact_descriptor("dense/metadata/manifest.json", dense_manifest_body),
        "scoring_probe": _artifact_descriptor("metadata/scoring_probe.json", scoring_body),
        "remote_provider_provenance": remote_provenance,
    }
    _write_preflight_manifest(output_root, result)
    return result
