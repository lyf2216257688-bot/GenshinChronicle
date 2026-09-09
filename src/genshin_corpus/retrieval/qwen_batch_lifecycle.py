"""Beijing Qwen Batch lifecycle for one later, explicitly authorized probe.

The runner has no implicit network client: tests inject a fake client and a
real caller must explicitly construct the reviewed Beijing adapter.  State is
written before each paid operation so a later resume cannot create a replacement
Batch after an ambiguous outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .qwen_batch_embedding import (
    QWEN_BATCH_EMBEDDINGS_PATH,
    QwenBatchEmbeddingConfig,
    QwenBatchEmbeddingError,
    build_qwen_batch_jsonl,
    materialize_qwen_batch_results,
)
from .qwen_embedding import QWEN_EMBEDDING_DIMENSION, QWEN_EMBEDDING_MODEL_ID, _artifact_descriptor, _contains_sensitive_value


QWEN_BEIJING_BATCH_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_BATCH_LIFECYCLE_SCHEMA_VERSION = "phase04-rag-qwen37-batch-lifecycle-0.1"
QWEN_BATCH_OFFLINE_EXECUTION_MODE = "injected_offline"
QWEN_BATCH_LIVE_EXECUTION_MODE = "live_beijing_batch"
_SAFE_PROVIDER_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class QwenBatchLifecycleError(ValueError):
    """Raised when a lifecycle state cannot safely progress."""


class QwenBatchLifecycleTransportError(Exception):
    """Safe transport failure classification; response bodies are never persisted."""

    def __init__(self, code: str, *, ambiguous: bool) -> None:
        if not isinstance(code, str) or not _SAFE_PROVIDER_ID.fullmatch(code):
            raise QwenBatchLifecycleError("Qwen Batch transport error code is invalid")
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


@dataclass(frozen=True)
class BeijingQwenBatchConfig:
    """The single reviewed Beijing OpenAI-compatible Batch origin."""

    base_url: str = QWEN_BEIJING_BATCH_BASE_URL
    timeout_seconds: float = 30.0
    api_key_env: str = "DASHSCOPE_API_KEY"

    def __post_init__(self) -> None:
        if self.base_url != QWEN_BEIJING_BATCH_BASE_URL:
            raise QwenBatchLifecycleError("Qwen Batch base URL must be the reviewed Beijing DashScope origin")
        if self.api_key_env != "DASHSCOPE_API_KEY":
            raise QwenBatchLifecycleError("Qwen Batch API key must be read only from DASHSCOPE_API_KEY")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise QwenBatchLifecycleError("Qwen Batch timeout must be a finite positive number")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "provider": "dashscope",
            "base_url": self.base_url,
            "lifecycle_schema_version": QWEN_BATCH_LIFECYCLE_SCHEMA_VERSION,
        }


class QwenBatchLifecycleClient(Protocol):
    """Minimal injectable Batch client; runner policy owns all state decisions."""

    def upload_file(self, body: bytes, *, purpose: str) -> str:
        """Upload an input file and return its provider file ID."""

    def create_batch(self, *, input_file_id: str, endpoint: str, completion_window: str) -> str:
        """Create one Batch and return its provider Batch ID."""

    def retrieve_batch(self, batch_id: str) -> Mapping[str, Any]:
        """Retrieve the exact provider Batch identified by ``batch_id``."""

    def download_file(self, file_id: str) -> bytes:
        """Download one provider File content body."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class DashScopeQwenBatchClient:
    """Explicit Beijing Files/Batch adapter; no request occurs until a method is called."""

    def __init__(
        self,
        config: BeijingQwenBatchConfig,
        *,
        environment: Mapping[str, str] | None = None,
        opener: Any | None = None,
    ) -> None:
        if not isinstance(config, BeijingQwenBatchConfig):
            raise QwenBatchLifecycleError("Qwen Batch client requires BeijingQwenBatchConfig")
        values = os.environ if environment is None else environment
        api_key = values.get("DASHSCOPE_API_KEY")
        if not isinstance(api_key, str) or not api_key:
            raise QwenBatchLifecycleError("Qwen Batch API key must be a non-empty DASHSCOPE_API_KEY value")
        self._config = config
        self._api_key = api_key
        self._opener = opener or build_opener(_RejectRedirects())
        # A supplied environment or opener is a test/injection boundary, not
        # evidence that a request reached the reviewed Beijing provider.
        self._live_evidence_eligible = environment is None and opener is None

    @classmethod
    def from_environment(
        cls,
        config: BeijingQwenBatchConfig,
        *,
        environment: Mapping[str, str] | None = None,
        opener: Any | None = None,
    ) -> "DashScopeQwenBatchClient":
        return cls(config, environment=environment, opener=opener)

    def identity_projection(self) -> dict[str, Any]:
        return self._config.identity_projection()

    def _secret_values_for_persistence(self) -> tuple[str, ...]:
        return (self._api_key,)

    def _request(self, method: str, path: str, *, body: bytes | None = None, content_type: str | None = None) -> bytes:
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        request = Request(f"{self._config.base_url}{path}", data=body, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=float(self._config.timeout_seconds)) as response:
                status = getattr(response, "status", None)
                payload = response.read()
        except HTTPError as exc:
            exc.close()
            raise QwenBatchLifecycleTransportError("HTTPError", ambiguous=False) from None
        except URLError:
            raise QwenBatchLifecycleTransportError("ConnectionError", ambiguous=True) from None
        if type(status) is not int or not 200 <= status < 300:
            raise QwenBatchLifecycleTransportError("UnexpectedHTTPStatus", ambiguous=False)
        return payload

    @staticmethod
    def _response_id(payload: bytes, label: str) -> str:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QwenBatchLifecycleTransportError("MalformedResponse", ambiguous=False) from exc
        if not isinstance(value, Mapping):
            raise QwenBatchLifecycleTransportError("MalformedResponse", ambiguous=False)
        return _provider_id(value.get("id"), label)

    def upload_file(self, body: bytes, *, purpose: str) -> str:
        if purpose != "batch" or not isinstance(body, bytes) or not body:
            raise QwenBatchLifecycleError("Qwen Batch upload requires a non-empty purpose=batch JSONL body")
        boundary = "qwenbatchlifecycle"
        multipart = b"".join((
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n".encode("ascii"),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"qwen-batch-input.jsonl\"\r\nContent-Type: application/jsonl\r\n\r\n".encode("ascii"),
            body,
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ))
        return self._response_id(
            self._request("POST", "/files", body=multipart, content_type=f"multipart/form-data; boundary={boundary}"),
            "file_id",
        )

    def create_batch(self, *, input_file_id: str, endpoint: str, completion_window: str) -> str:
        _provider_id(input_file_id, "input_file_id")
        if endpoint != QWEN_BATCH_EMBEDDINGS_PATH or completion_window != "24h":
            raise QwenBatchLifecycleError("Qwen Batch create requires the reviewed embeddings endpoint and 24h window")
        body = canonical_json_bytes({
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": completion_window,
        })
        return self._response_id(self._request("POST", "/batches", body=body, content_type="application/json"), "batch_id")

    def retrieve_batch(self, batch_id: str) -> Mapping[str, Any]:
        _provider_id(batch_id, "batch_id")
        payload = self._request("GET", f"/batches/{batch_id}")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QwenBatchLifecycleTransportError("MalformedResponse", ambiguous=False) from exc
        if not isinstance(value, Mapping):
            raise QwenBatchLifecycleTransportError("MalformedResponse", ambiguous=False)
        return value

    def download_file(self, file_id: str) -> bytes:
        _provider_id(file_id, "file_id")
        return self._request("GET", f"/files/{file_id}/content")


def _provider_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_PROVIDER_ID.fullmatch(value):
        raise QwenBatchLifecycleError(f"Qwen Batch {label} is invalid")
    return value


def _client_secrets(client: QwenBatchLifecycleClient) -> tuple[str, ...]:
    values = getattr(client, "_secret_values_for_persistence", None)
    result = values() if callable(values) else ()
    if not isinstance(result, tuple) or any(not isinstance(value, str) or not value for value in result):
        raise QwenBatchLifecycleError("Qwen Batch client secret redaction values are invalid")
    return result


def _state_path(output_root: Path) -> Path:
    return output_root / "metadata" / "lifecycle_state.json"


def _write_state(output_root: Path, state: Mapping[str, Any], *, secrets: Sequence[str]) -> dict[str, Any]:
    value = dict(state)
    if _contains_sensitive_value(value, tuple(secrets)):
        raise QwenBatchLifecycleError("Qwen Batch lifecycle state contains credential-like data")
    body = canonical_json_bytes(value)
    atomic_write(_state_path(output_root), body)
    return value


def _read_state(output_root: Path, *, secrets: Sequence[str]) -> dict[str, Any]:
    try:
        value = json.loads(_state_path(output_root).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenBatchLifecycleError("Qwen Batch lifecycle state is unavailable or invalid") from exc
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != QWEN_BATCH_LIFECYCLE_SCHEMA_VERSION
        or _contains_sensitive_value(value, tuple(secrets))
    ):
        raise QwenBatchLifecycleError("Qwen Batch lifecycle state is malformed or credential-like")
    return dict(value)


def _safe_download(body: bytes, *, secrets: Sequence[str], label: str) -> bytes:
    if not isinstance(body, bytes) or not body:
        raise QwenBatchLifecycleError(f"Qwen Batch {label} download is empty or invalid")
    try:
        rows = [json.loads(line.decode("utf-8")) for line in body.splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenBatchLifecycleError(f"Qwen Batch {label} download is not UTF-8 JSONL") from exc
    if not rows or any(not isinstance(row, Mapping) or _contains_sensitive_value(row, tuple(secrets)) for row in rows):
        raise QwenBatchLifecycleError(f"Qwen Batch {label} download is malformed or credential-like")
    return body


def _state_with(state: Mapping[str, Any], **updates: Any) -> dict[str, Any]:
    result = dict(state)
    result.update(updates)
    return result


def _initial_probe_evidence(execution_mode: str) -> dict[str, Any]:
    if execution_mode == QWEN_BATCH_OFFLINE_EXECUTION_MODE:
        return {
            "execution_mode": execution_mode,
            "provider_api_status": "unverified_batch_result_only",
            "batch_2048_wire_acceptance": "unknown",
            "document_vector_compatibility_with_sync_document": "unknown",
        }
    return {
        "execution_mode": execution_mode,
        "provider_api_status": "live_beijing_batch_pending",
        "batch_2048_wire_acceptance": "unknown",
        "document_vector_compatibility_with_sync_document": "unknown",
    }


def _validate_execution_mode(execution_mode: str, client: QwenBatchLifecycleClient) -> None:
    if execution_mode not in {QWEN_BATCH_OFFLINE_EXECUTION_MODE, QWEN_BATCH_LIVE_EXECUTION_MODE}:
        raise QwenBatchLifecycleError("Qwen Batch execution mode is invalid")
    if execution_mode == QWEN_BATCH_LIVE_EXECUTION_MODE and not (
        isinstance(client, DashScopeQwenBatchClient) and client._live_evidence_eligible
    ):
        raise QwenBatchLifecycleError("live Beijing Batch evidence requires an explicit non-injected Beijing adapter")


def _validate_state_execution_mode(state: Mapping[str, Any], execution_mode: str) -> None:
    if state.get("execution_mode") != execution_mode:
        raise QwenBatchLifecycleError("Qwen Batch resume execution mode does not match persisted lifecycle evidence")


def _one_document_input(config: QwenBatchEmbeddingConfig) -> bytes:
    if len(config.document_unit_ids) != 1:
        raise QwenBatchLifecycleError("Qwen Batch lifecycle is limited to exactly one document Retrieval Unit")
    return build_qwen_batch_jsonl(config)


def _assert_bound_input(config: QwenBatchEmbeddingConfig, state: Mapping[str, Any]) -> None:
    input_descriptor = state.get("input")
    if not isinstance(input_descriptor, Mapping):
        raise QwenBatchLifecycleError("Qwen Batch lifecycle state lacks its input descriptor")
    expected_sha256 = sha256(build_qwen_batch_jsonl(config)).hexdigest()
    if input_descriptor.get("sha256") != expected_sha256:
        raise QwenBatchLifecycleError("Qwen Batch resume input does not match the persisted one-document JSONL")


def _live_success_evidence(
    config: QwenBatchEmbeddingConfig,
    output_root: Path,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove the local prerequisites before recording any live Batch success."""

    batch = state.get("batch")
    downloads = state.get("downloads")
    materialization = state.get("materialization")
    if (
        not isinstance(batch, Mapping)
        or batch.get("provider_status") != "completed"
        or not isinstance(batch.get("batch_id"), str)
        or not isinstance(batch.get("output_file_id"), str)
        or not isinstance(downloads, Mapping)
        or not isinstance(downloads.get("output"), Mapping)
        or downloads["output"].get("status") != "succeeded"
        or not isinstance(materialization, Mapping)
        or materialization.get("status") != "complete"
    ):
        raise QwenBatchLifecycleError("live Batch success prerequisites are incomplete")
    try:
        manifest = json.loads((output_root / "materialized" / "dense" / "metadata" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenBatchLifecycleError("live Batch materialized Dense manifest is unavailable") from exc
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("model_name") != QWEN_EMBEDDING_MODEL_ID
        or manifest.get("embedding_dimension") != QWEN_EMBEDDING_DIMENSION
        or manifest.get("row_count") != len(config.document_unit_ids)
        or manifest.get("dtype") != "float32"
        or manifest.get("normalization") != "L2"
    ):
        raise QwenBatchLifecycleError("live Batch materialization does not prove the expected 2048-dimensional vector")
    provenance = manifest.get("remote_provider_provenance")
    if not isinstance(provenance, Mapping):
        raise QwenBatchLifecycleError("live Batch materialization lacks remote-provider provenance")
    returned_models = provenance.get("document_returned_models")
    if (
        not isinstance(returned_models, list)
        or len(returned_models) != len(config.document_unit_ids)
        or any(not isinstance(item, Mapping) for item in returned_models)
    ):
        raise QwenBatchLifecycleError("live Batch materialization lacks returned-model evidence")
    values = [item.get("model") for item in returned_models]
    if any(value is not None and not isinstance(value, str) for value in values):
        raise QwenBatchLifecycleError("live Batch returned model identity is malformed")
    returned_model: str | None = values[0] if len(set(values)) == 1 else None
    return {
        "execution_mode": QWEN_BATCH_LIVE_EXECUTION_MODE,
        "provider_api_status": "live_beijing_batch_succeeded",
        "batch_2048_wire_acceptance": "verified",
        "document_vector_compatibility_with_sync_document": "unknown",
        "batch_id": batch["batch_id"],
        "output_file_id": batch["output_file_id"],
        "requested_model_identity": QWEN_EMBEDDING_MODEL_ID,
        "returned_model_identity": returned_model,
        "validated_embedding_dimension": QWEN_EMBEDDING_DIMENSION,
    }


def submit_qwen_batch_lifecycle(
    config: QwenBatchEmbeddingConfig,
    output_root: Path,
    client: QwenBatchLifecycleClient,
    *,
    execution_mode: str = QWEN_BATCH_OFFLINE_EXECUTION_MODE,
    lifecycle_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist then submit one already-selected Batch input; never replace a job."""

    output_root = Path(output_root)
    _validate_execution_mode(execution_mode, client)
    secrets = _client_secrets(client)
    if _state_path(output_root).exists():
        state = _read_state(output_root, secrets=secrets)
        _validate_state_execution_mode(state, execution_mode)
        batch = state.get("batch")
        if isinstance(batch, Mapping) and isinstance(batch.get("batch_id"), str):
            return resume_qwen_batch_lifecycle(config, output_root, client, execution_mode=execution_mode)
        raise QwenBatchLifecycleError("existing Qwen Batch lifecycle state forbids automatic replacement submission")
    if output_root.exists():
        raise FileExistsError("Qwen Batch lifecycle output root already exists")

    if lifecycle_binding is not None and not isinstance(lifecycle_binding, Mapping):
        raise QwenBatchLifecycleError("Qwen Batch lifecycle binding must be an object or null")
    input_body = build_qwen_batch_jsonl(config)
    input_path = output_root / "artifacts" / "input.jsonl"
    atomic_write(input_path, input_body)
    initial_state: dict[str, Any] = {
        "schema_version": QWEN_BATCH_LIFECYCLE_SCHEMA_VERSION,
        "status": "upload_issued",
        "execution_mode": execution_mode,
        "probe_evidence": _initial_probe_evidence(execution_mode),
        "provider": BeijingQwenBatchConfig().identity_projection(),
        "input": _artifact_descriptor("artifacts/input.jsonl", input_body, len(config.document_unit_ids)),
        "upload": {"status": "issued", "purpose": "batch"},
    }
    if lifecycle_binding is not None:
        initial_state["lifecycle_binding"] = dict(lifecycle_binding)
    state = _write_state(output_root, initial_state, secrets=secrets)
    try:
        file_id = _provider_id(client.upload_file(input_body, purpose="batch"), "file_id")
    except QwenBatchLifecycleTransportError as exc:
        status = "upload_ambiguous" if exc.ambiguous else "upload_failed"
        return _write_state(output_root, _state_with(state, status=status, upload={"status": status, "error_code": exc.code}), secrets=secrets)
    state = _write_state(output_root, _state_with(
        state,
        status="uploaded",
        upload={"status": "succeeded", "purpose": "batch", "file_id": file_id},
    ), secrets=secrets)
    state = _write_state(output_root, _state_with(
        state,
        status="create_issued",
        batch={"status": "issued", "input_file_id": file_id, "endpoint": QWEN_BATCH_EMBEDDINGS_PATH, "completion_window": "24h"},
    ), secrets=secrets)
    try:
        batch_id = _provider_id(
            client.create_batch(input_file_id=file_id, endpoint=QWEN_BATCH_EMBEDDINGS_PATH, completion_window="24h"),
            "batch_id",
        )
    except QwenBatchLifecycleTransportError as exc:
        status = "create_ambiguous" if exc.ambiguous else "create_failed"
        return _write_state(output_root, _state_with(
            state,
            status=status,
            batch={**dict(state["batch"]), "status": status, "error_code": exc.code},
        ), secrets=secrets)
    return _write_state(output_root, _state_with(
        state,
        status="created",
        batch={**dict(state["batch"]), "status": "succeeded", "batch_id": batch_id},
    ), secrets=secrets)


def _batch_snapshot(batch_id: str, provider_batch: Mapping[str, Any]) -> dict[str, Any]:
    if _contains_sensitive_value(provider_batch, ()):
        raise QwenBatchLifecycleError("Qwen Batch retrieval contains credential-like data")
    if _provider_id(provider_batch.get("id"), "retrieved batch_id") != batch_id:
        raise QwenBatchLifecycleError("Qwen Batch retrieve response does not bind to the persisted batch_id")
    status = provider_batch.get("status")
    if not isinstance(status, str) or not _SAFE_PROVIDER_ID.fullmatch(status):
        raise QwenBatchLifecycleError("Qwen Batch retrieve response status is invalid")
    snapshot: dict[str, Any] = {"batch_id": batch_id, "provider_status": status}
    for field in ("output_file_id", "error_file_id"):
        value = provider_batch.get(field)
        if value is not None:
            snapshot[field] = _provider_id(value, field)
    return snapshot


def _download_terminal_files(
    output_root: Path,
    state: Mapping[str, Any],
    client: QwenBatchLifecycleClient,
    *,
    secrets: Sequence[str],
) -> dict[str, Any]:
    batch = dict(state["batch"])
    downloads = dict(state.get("downloads") or {})
    for label, relative in (("output", "downloads/output.jsonl"), ("error", "downloads/error.jsonl")):
        file_id = batch.get(f"{label}_file_id")
        if file_id is None or label in downloads:
            continue
        issued = _write_state(output_root, _state_with(
            state,
            status=f"{label}_download_issued",
            downloads={**downloads, label: {"status": "issued", "file_id": file_id}},
        ), secrets=secrets)
        try:
            body = _safe_download(client.download_file(file_id), secrets=secrets, label=label)
        except QwenBatchLifecycleTransportError as exc:
            return _write_state(output_root, _state_with(
                issued,
                status=f"{label}_download_{'ambiguous' if exc.ambiguous else 'failed'}",
                downloads={**downloads, label: {"status": "failed", "file_id": file_id, "error_code": exc.code}},
            ), secrets=secrets)
        atomic_write(output_root / relative, body)
        downloads[label] = {"status": "succeeded", "file_id": file_id, "artifact": _artifact_descriptor(relative, body)}
        state = _write_state(output_root, _state_with(state, status="completed_downloaded", downloads=downloads), secrets=secrets)
    return state


def resume_qwen_batch_lifecycle(
    config: QwenBatchEmbeddingConfig,
    output_root: Path,
    client: QwenBatchLifecycleClient,
    *,
    execution_mode: str = QWEN_BATCH_OFFLINE_EXECUTION_MODE,
) -> dict[str, Any]:
    """Retrieve one persisted Batch; terminal failures never trigger replacement work."""

    output_root = Path(output_root)
    _validate_execution_mode(execution_mode, client)
    secrets = _client_secrets(client)
    state = _read_state(output_root, secrets=secrets)
    _validate_state_execution_mode(state, execution_mode)
    _assert_bound_input(config, state)
    batch = state.get("batch")
    if not isinstance(batch, Mapping) or not isinstance(batch.get("batch_id"), str):
        raise QwenBatchLifecycleError("Qwen Batch resume requires a persisted batch_id")
    batch_id = _provider_id(batch["batch_id"], "batch_id")
    if state.get("status") == "materialized":
        return state
    try:
        provider_batch = client.retrieve_batch(batch_id)
        snapshot = _batch_snapshot(batch_id, provider_batch)
    except QwenBatchLifecycleTransportError as exc:
        return _write_state(output_root, _state_with(state, status="retrieve_failed", retrieve_error_code=exc.code), secrets=secrets)
    state = _write_state(output_root, _state_with(state, status="retrieved", batch=snapshot), secrets=secrets)
    status = snapshot["provider_status"]
    if status in {"failed", "expired", "cancelled", "canceled"}:
        return _write_state(output_root, _state_with(state, status=f"terminal_{status}"), secrets=secrets)
    if status != "completed":
        return state
    state = _download_terminal_files(output_root, state, client, secrets=secrets)
    if "output_file_id" not in snapshot:
        return _write_state(output_root, _state_with(state, status="completed_missing_output"), secrets=secrets)
    downloads = state.get("downloads")
    if not isinstance(downloads, Mapping) or not isinstance(downloads.get("output"), Mapping) or downloads["output"].get("status") != "succeeded":
        return state
    materialized_root = output_root / "materialized"
    if materialized_root.exists():
        return state
    try:
        materialization = materialize_qwen_batch_results(config, output_root / "downloads" / "output.jsonl", materialized_root)
    except (FileExistsError, QwenBatchEmbeddingError) as exc:
        return _write_state(output_root, _state_with(state, status="materialization_failed", materialization_error=type(exc).__name__), secrets=secrets)
    completed_state = _state_with(state, status="materialized", materialization=materialization)
    if execution_mode == QWEN_BATCH_LIVE_EXECUTION_MODE:
        completed_state["probe_evidence"] = _live_success_evidence(config, output_root, completed_state)
    return _write_state(output_root, completed_state, secrets=secrets)


def submit_qwen_batch_probe(
    config: QwenBatchEmbeddingConfig,
    output_root: Path,
    client: QwenBatchLifecycleClient,
    *,
    execution_mode: str = QWEN_BATCH_OFFLINE_EXECUTION_MODE,
) -> dict[str, Any]:
    """The reviewed one-document lifecycle entry point."""

    _one_document_input(config)
    return submit_qwen_batch_lifecycle(config, output_root, client, execution_mode=execution_mode)


def resume_qwen_batch_probe(
    config: QwenBatchEmbeddingConfig,
    output_root: Path,
    client: QwenBatchLifecycleClient,
    *,
    execution_mode: str = QWEN_BATCH_OFFLINE_EXECUTION_MODE,
) -> dict[str, Any]:
    """Resume the reviewed one-document lifecycle entry point."""

    _one_document_input(config)
    return resume_qwen_batch_lifecycle(config, output_root, client, execution_mode=execution_mode)
