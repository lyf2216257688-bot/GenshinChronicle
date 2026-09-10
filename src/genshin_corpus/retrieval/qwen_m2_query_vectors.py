"""Materialize the fixed M2 Q001-Q070 Qwen query-vector artifact.

This is intentionally a narrow paid-execution seam.  It reuses the accepted
Qwen synchronous request and response contract, but keeps the 70-query run
state separate from corpus Batch embedding and production Dense selection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from .qwen_embedding import (
    DASHSCOPE_QWEN_EMBEDDING_TRANSPORT_VERSION,
    QWEN_EMBEDDING_DIMENSION,
    QWEN_EMBEDDING_MODEL_ID,
    QWEN_EMBEDDING_OUTPUT,
    QWEN_INJECTED_TRANSPORT_CONTRACT_VERSION,
    QWEN_QUERY_ROLE,
    DashScopeQwenEmbeddingTransport,
    QwenEmbeddingPreflightError,
    QwenEmbeddingRequest,
    QwenEmbeddingResponse,
    QwenEmbeddingTransport,
    QwenEmbeddingTransportError,
    _dashscope_transport_provenance,
    _safe_response_bytes,
    _transport_secret_values,
    _validate_response,
)


QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION = "phase04-rag-qwen37-m2-query-vectors-0.1"
QWEN_M2_QUERY_VECTOR_COUNT = 70
QWEN_M2_QUERY_MAX_BATCH_SIZE = 20


class QwenM2QueryVectorsError(ValueError):
    """Raised when the dedicated 70Q query-vector run cannot safely proceed."""


@dataclass(frozen=True)
class QwenM2QueryVectorConfig:
    """The immutable operating point for one M2 Qwen query-vector run."""

    runtime_input: Path
    batch_size: int = QWEN_M2_QUERY_MAX_BATCH_SIZE

    def __post_init__(self) -> None:
        if (
            not isinstance(self.batch_size, int)
            or isinstance(self.batch_size, bool)
            or not 1 <= self.batch_size <= QWEN_M2_QUERY_MAX_BATCH_SIZE
        ):
            raise QwenM2QueryVectorsError("Qwen M2 query batch size must be between 1 and 20")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "schema_version": QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION,
            "model_id": QWEN_EMBEDDING_MODEL_ID,
            "dimension": QWEN_EMBEDDING_DIMENSION,
            "output": QWEN_EMBEDDING_OUTPUT,
            "role": QWEN_QUERY_ROLE,
            "custom_query_instruction": None,
            "batch_size": self.batch_size,
        }


def _descriptor(path: str, body: bytes, *, row_count: int | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"path": path, "sha256": sha256(body).hexdigest(), "byte_count": len(body)}
    if row_count is not None:
        value["row_count"] = row_count
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenM2QueryVectorsError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise QwenM2QueryVectorsError(f"{label} must be a JSON object")
    return value


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise QwenM2QueryVectorsError("Qwen M2 provider attempt ledger is unreadable") from exc
    attempts: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QwenM2QueryVectorsError(f"Qwen M2 provider attempt ledger line {line_number} is invalid") from exc
        if not isinstance(value, dict):
            raise QwenM2QueryVectorsError("Qwen M2 provider attempt ledger row must be an object")
        attempts.append(value)
    return attempts


def _write_ledger(output_root: Path, attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    body = b"".join(canonical_json_bytes(dict(attempt)) + b"\n" for attempt in attempts)
    atomic_write(output_root / "metadata" / "provider_attempts.jsonl", body)
    return _descriptor("metadata/provider_attempts.jsonl", body, row_count=len(attempts))


def _question_rows(questions: Sequence[AcceptedQuestion]) -> list[dict[str, Any]]:
    return [
        {
            "row_index": index,
            "question_id": question.question_id,
            "question": question.question,
            "question_identity": sha256_json({"question_id": question.question_id, "question": question.question}),
        }
        for index, question in enumerate(questions)
    ]


def _validate_configuration(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise QwenM2QueryVectorsError("Qwen M2 configuration is invalid")
    batch_size = value.get("batch_size")
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or not 1 <= batch_size <= QWEN_M2_QUERY_MAX_BATCH_SIZE
    ):
        raise QwenM2QueryVectorsError("Qwen M2 configuration batch size is invalid")
    expected = {
        "schema_version": QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION,
        "model_id": QWEN_EMBEDDING_MODEL_ID,
        "dimension": QWEN_EMBEDDING_DIMENSION,
        "output": QWEN_EMBEDDING_OUTPUT,
        "role": QWEN_QUERY_ROLE,
        "custom_query_instruction": None,
        "batch_size": batch_size,
    }
    if dict(value) != expected:
        raise QwenM2QueryVectorsError("Qwen M2 configuration does not match the fixed query operating point")
    return expected


def _validate_question_mapping(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != QWEN_M2_QUERY_VECTOR_COUNT:
        raise QwenM2QueryVectorsError("Qwen M2 question mapping must contain exactly Q001-Q070")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        expected_id = f"Q{index + 1:03d}"
        if not isinstance(row, Mapping) or set(row) != {"row_index", "question_id", "question", "question_identity"}:
            raise QwenM2QueryVectorsError("Qwen M2 question mapping row is invalid")
        question_id, question, question_identity = row["question_id"], row["question"], row["question_identity"]
        if row["row_index"] != index or question_id != expected_id or not isinstance(question, str) or not question.strip():
            raise QwenM2QueryVectorsError("Qwen M2 question mapping order is invalid")
        expected_identity = sha256_json({"question_id": question_id, "question": question})
        if question_identity != expected_identity:
            raise QwenM2QueryVectorsError("Qwen M2 question mapping identity is invalid")
        rows.append(dict(row))
    return rows


def _input_descriptor(config: QwenM2QueryVectorConfig, questions: Sequence[AcceptedQuestion]) -> dict[str, Any]:
    try:
        body = Path(config.runtime_input).read_bytes()
    except OSError as exc:
        raise QwenM2QueryVectorsError("M2 runtime input is unreadable") from exc
    return {
        "path": str(Path(config.runtime_input)),
        "sha256": sha256(body).hexdigest(),
        "byte_count": len(body),
        "question_count": len(questions),
        "question_mapping_sha256": sha256_json(_question_rows(questions)),
    }


def _transport_binding(transport: QwenEmbeddingTransport, execution_mode: str) -> dict[str, Any]:
    if execution_mode not in {"injected_offline", "live_dashscope"}:
        raise QwenM2QueryVectorsError("Qwen M2 execution mode is invalid")
    dashscope = _dashscope_transport_provenance(transport)
    if execution_mode == "live_dashscope":
        if not isinstance(transport, DashScopeQwenEmbeddingTransport):
            raise QwenM2QueryVectorsError("live Qwen M2 execution requires DashScopeQwenEmbeddingTransport")
        return {"execution_mode": execution_mode, "transport": dashscope}
    if isinstance(transport, DashScopeQwenEmbeddingTransport):
        raise QwenM2QueryVectorsError("DashScope Qwen transport requires explicit live execution mode")
    return {
        "execution_mode": execution_mode,
        "transport_contract_version": QWEN_INJECTED_TRANSPORT_CONTRACT_VERSION,
    }


def _batch_bindings_from_rows(question_rows: Sequence[Mapping[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(question_rows), batch_size), 1):
        batch_rows = question_rows[start:start + batch_size]
        request = QwenEmbeddingRequest(role=QWEN_QUERY_ROLE, texts=tuple(str(row["question"]) for row in batch_rows))
        bindings.append({
            "batch_index": batch_index,
            "question_ids": [str(row["question_id"]) for row in batch_rows],
            "question_mapping_sha256": sha256_json([
                {**dict(row), "row_index": index} for index, row in enumerate(batch_rows)
            ]),
            "request_identity": request.request_identity,
            "input_identity": request.input_identity,
        })
    return bindings


def _batch_bindings(questions: Sequence[AcceptedQuestion], config: QwenM2QueryVectorConfig) -> list[dict[str, Any]]:
    return _batch_bindings_from_rows(_question_rows(questions), config.batch_size)


def _run_identity(
    configuration: Mapping[str, Any],
    input_descriptor: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    transport_binding: Mapping[str, Any],
) -> str:
    return sha256_json({
        "schema_version": QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION,
        "configuration": dict(configuration),
        "runtime_input": dict(input_descriptor),
        "batches": [dict(binding) for binding in bindings],
        "transport_binding": dict(transport_binding),
    })


def _validate_transport_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("execution_mode") not in {"injected_offline", "live_dashscope"}:
        raise QwenM2QueryVectorsError("Qwen M2 transport binding is invalid")
    if value["execution_mode"] == "injected_offline":
        expected = {
            "execution_mode": "injected_offline",
            "transport_contract_version": QWEN_INJECTED_TRANSPORT_CONTRACT_VERSION,
        }
        if dict(value) != expected:
            raise QwenM2QueryVectorsError("Qwen M2 injected transport binding is invalid")
    elif set(value) != {"execution_mode", "transport"} or not isinstance(value.get("transport"), Mapping):
        raise QwenM2QueryVectorsError("Qwen M2 DashScope transport binding is invalid")
    elif (
        value["transport"].get("provider") != "dashscope"
        or value["transport"].get("transport_contract_version") != DASHSCOPE_QWEN_EMBEDDING_TRANSPORT_VERSION
    ):
        raise QwenM2QueryVectorsError("Qwen M2 DashScope transport provenance is invalid")
    return dict(value)


def _write_manifest(output_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    body = canonical_json_bytes(dict(manifest))
    atomic_write(output_root / "metadata" / "manifest.json", body)
    return _descriptor("metadata/manifest.json", body)


def _expected_attempt(binding: Mapping[str, Any]) -> dict[str, Any]:
    batch_index = binding["batch_index"]
    return {
        "schema_version": QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION,
        "attempt_number": batch_index,
        "attempt_id": sha256_json({
            "schema_version": QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION,
            "batch_index": batch_index,
            "request_identity": binding["request_identity"],
        }),
        "batch_index": batch_index,
        "role": QWEN_QUERY_ROLE,
        "request_identity": binding["request_identity"],
        "input_identity": binding["input_identity"],
        "status": "issued",
    }


def _assert_descriptor(root: Path, descriptor: Mapping[str, Any], label: str) -> Path:
    path, expected_hash = descriptor.get("path"), descriptor.get("sha256")
    if not isinstance(path, str) or not isinstance(expected_hash, str) or Path(path).is_absolute() or ".." in Path(path).parts:
        raise QwenM2QueryVectorsError(f"{label} descriptor is unsafe")
    target = root / path
    try:
        body = target.read_bytes()
    except OSError as exc:
        raise QwenM2QueryVectorsError(f"{label} artifact is missing") from exc
    if sha256(body).hexdigest() != expected_hash or descriptor.get("byte_count") != len(body):
        raise QwenM2QueryVectorsError(f"{label} artifact hash does not match")
    return target


def _persist_batch_response(
    output_root: Path,
    *,
    batch_index: int,
    raw_response_bytes: bytes | None,
    secret_values: tuple[str, ...],
) -> dict[str, Any] | None:
    if raw_response_bytes is None:
        return None
    body = _safe_response_bytes(raw_response_bytes, secret_values)
    relative_path = f"responses/batch-{batch_index:03d}.response.json"
    atomic_write(output_root / relative_path, body)
    return _descriptor(relative_path, body)


def _load_batch_vectors(output_root: Path, binding: Mapping[str, Any], attempt: Mapping[str, Any]) -> Any:
    import numpy as np

    if attempt.get("status") != "succeeded" or attempt.get("batch_index") != binding["batch_index"]:
        raise QwenM2QueryVectorsError("Qwen M2 completed batch ledger is invalid")
    if attempt.get("request_identity") != binding["request_identity"] or attempt.get("input_identity") != binding["input_identity"]:
        raise QwenM2QueryVectorsError("Qwen M2 completed batch is bound to different input")
    artifact = attempt.get("vectors_artifact")
    if not isinstance(artifact, Mapping):
        raise QwenM2QueryVectorsError("Qwen M2 completed batch lacks vector artifact")
    path = _assert_descriptor(output_root, artifact, "Qwen M2 batch vectors")
    try:
        vectors = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise QwenM2QueryVectorsError("Qwen M2 batch vectors are unreadable") from exc
    expected_shape = (len(binding["question_ids"]), QWEN_EMBEDDING_DIMENSION)
    if vectors.dtype != np.float32 or vectors.shape != expected_shape or not np.isfinite(vectors).all():
        raise QwenM2QueryVectorsError("Qwen M2 batch vectors violate the float32 shape contract")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any(norms == 0) or not np.allclose(norms, 1.0, rtol=0.0, atol=1e-5):
        raise QwenM2QueryVectorsError("Qwen M2 batch vectors violate L2 normalization")
    return vectors


def _validate_succeeded_attempt(
    output_root: Path,
    binding: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> None:
    expected = _expected_attempt(binding)
    if any(attempt.get(key) != value for key, value in expected.items() if key != "status"):
        raise QwenM2QueryVectorsError("Qwen M2 provider attempt is not bound to this run")
    if attempt.get("status") == "issued":
        raise QwenM2QueryVectorsError("ambiguous issued-but-not-resolved Qwen M2 provider attempt")
    if attempt.get("status") != "succeeded":
        raise QwenM2QueryVectorsError("resolved Qwen M2 provider failure must not be automatically re-issued")
    response_artifact = attempt.get("response_artifact")
    expected_response_path = f"responses/batch-{binding['batch_index']:03d}.response.json"
    if not isinstance(response_artifact, Mapping) or response_artifact.get("path") != expected_response_path:
        raise QwenM2QueryVectorsError("Qwen M2 provider response evidence is invalid")
    _assert_descriptor(output_root, response_artifact, "Qwen M2 provider response evidence")
    if attempt.get("returned_model") not in {None, QWEN_EMBEDDING_MODEL_ID}:
        raise QwenM2QueryVectorsError("Qwen M2 provider model binding is invalid")
    if attempt.get("returned_role") not in {None, QWEN_QUERY_ROLE}:
        raise QwenM2QueryVectorsError("Qwen M2 provider role binding is invalid")
    request_id = attempt.get("provider_request_id")
    if request_id is not None and (not isinstance(request_id, str) or not request_id):
        raise QwenM2QueryVectorsError("Qwen M2 provider request ID is invalid")
    _load_batch_vectors(output_root, binding, attempt)


def _validate_run_provenance(
    output_root: Path,
    manifest: Mapping[str, Any],
    *,
    require_complete: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if manifest.get("schema_version") != QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION:
        raise QwenM2QueryVectorsError("Qwen M2 query-vector run has an unsupported schema")
    expected_status = "complete" if require_complete else "in_progress"
    if manifest.get("status") != expected_status:
        raise QwenM2QueryVectorsError("Qwen M2 query-vector run status is invalid for provenance validation")
    configuration = _validate_configuration(manifest.get("configuration"))
    question_rows = _validate_question_mapping(manifest.get("question_mapping"))
    runtime_input = manifest.get("runtime_input")
    if (
        not isinstance(runtime_input, Mapping)
        or not isinstance(runtime_input.get("path"), str)
        or not isinstance(runtime_input.get("sha256"), str)
        or len(runtime_input["sha256"]) != 64
        or not isinstance(runtime_input.get("byte_count"), int)
        or runtime_input.get("byte_count") < 0
        or runtime_input.get("question_count") != QWEN_M2_QUERY_VECTOR_COUNT
        or runtime_input.get("question_mapping_sha256") != sha256_json(question_rows)
    ):
        raise QwenM2QueryVectorsError("Qwen M2 runtime input binding is invalid")
    transport_binding = _validate_transport_binding(manifest.get("transport_binding"))
    expected_batches = _batch_bindings_from_rows(question_rows, configuration["batch_size"])
    if manifest.get("batches") != expected_batches:
        raise QwenM2QueryVectorsError("Qwen M2 batch binding does not match the question mapping")
    if manifest.get("run_identity") != _run_identity(configuration, runtime_input, expected_batches, transport_binding):
        raise QwenM2QueryVectorsError("Qwen M2 run identity does not match fixed provenance")
    ledger_descriptor = manifest.get("provider_attempts")
    if (
        not isinstance(ledger_descriptor, Mapping)
        or ledger_descriptor.get("path") != "metadata/provider_attempts.jsonl"
    ):
        raise QwenM2QueryVectorsError("Qwen M2 provider attempt ledger descriptor is invalid")
    ledger_path = _assert_descriptor(output_root, ledger_descriptor, "Qwen M2 provider attempt ledger")
    attempts = _read_ledger(ledger_path)
    if ledger_descriptor.get("row_count") != len(attempts) or len(attempts) > len(expected_batches):
        raise QwenM2QueryVectorsError("Qwen M2 provider attempt ledger has too many rows")
    expected_count = len(expected_batches) if require_complete else len(attempts)
    if len(attempts) != expected_count:
        raise QwenM2QueryVectorsError("Qwen M2 completed batch count is invalid")
    for binding, attempt in zip(expected_batches, attempts, strict=False):
        _validate_succeeded_attempt(output_root, binding, attempt)
    if manifest.get("completed_batch_count") != len(attempts):
        raise QwenM2QueryVectorsError("Qwen M2 completed batch count is invalid")
    return question_rows, expected_batches, attempts


def _validate_complete_provenance(output_root: Path, manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    question_rows, _, attempts = _validate_run_provenance(output_root, manifest, require_complete=True)
    return question_rows, attempts


def _batch_vector_body(vectors: Any) -> bytes:
    import numpy as np

    output = BytesIO()
    np.save(output, np.asarray(vectors, dtype=np.float32), allow_pickle=False)
    return output.getvalue()


def _complete_artifact(
    output_root: Path,
    manifest: dict[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    import numpy as np

    batches = manifest["batches"]
    vectors = np.concatenate(
        [_load_batch_vectors(output_root, binding, attempt) for binding, attempt in zip(batches, attempts, strict=True)],
        axis=0,
    )
    question_rows = manifest["question_mapping"]
    if vectors.dtype != np.float32 or vectors.shape != (QWEN_M2_QUERY_VECTOR_COUNT, QWEN_EMBEDDING_DIMENSION):
        raise QwenM2QueryVectorsError("Qwen M2 final vectors do not have shape 70x2048")
    vector_body = _batch_vector_body(vectors)
    rows_body = b"".join(canonical_json_bytes(row) + b"\n" for row in question_rows)
    vector_path = output_root / "artifacts" / "vectors.f32.npy"
    rows_path = output_root / "artifacts" / "question_rows.jsonl"
    if vector_path.exists() or rows_path.exists():
        raise QwenM2QueryVectorsError("Qwen M2 final query-vector artifacts already exist and are immutable")
    atomic_write(vector_path, vector_body)
    atomic_write(rows_path, rows_body)
    manifest["status"] = "complete"
    manifest["completed_batch_count"] = len(attempts)
    manifest["provider_attempts"] = _write_ledger(output_root, attempts)
    manifest["artifacts"] = {
        "vectors": _descriptor("artifacts/vectors.f32.npy", vector_body, row_count=QWEN_M2_QUERY_VECTOR_COUNT),
        "question_rows": _descriptor("artifacts/question_rows.jsonl", rows_body, row_count=QWEN_M2_QUERY_VECTOR_COUNT),
    }
    manifest["artifact_identity"] = sha256_json({
        "run_identity": manifest["run_identity"],
        "provider_attempts": manifest["provider_attempts"],
        "vectors": manifest["artifacts"]["vectors"],
        "question_rows": manifest["artifacts"]["question_rows"],
    })
    _write_manifest(output_root, manifest)
    return manifest


def _validate_complete_artifact(output_root: Path, manifest: Mapping[str, Any]) -> None:
    import numpy as np

    artifacts = manifest.get("artifacts")
    if manifest.get("schema_version") != QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION:
        raise QwenM2QueryVectorsError("Qwen M2 query-vector artifact has an unsupported schema")
    if manifest.get("status") != "complete" or not isinstance(artifacts, Mapping):
        raise QwenM2QueryVectorsError("Qwen M2 query-vector run is not complete")
    question_mapping, attempts = _validate_complete_provenance(output_root, manifest)
    vector_path = _assert_descriptor(output_root, artifacts.get("vectors", {}), "Qwen M2 final vectors")
    rows_path = _assert_descriptor(output_root, artifacts.get("question_rows", {}), "Qwen M2 final question rows")
    if artifacts["vectors"].get("row_count") != QWEN_M2_QUERY_VECTOR_COUNT or artifacts["question_rows"].get("row_count") != QWEN_M2_QUERY_VECTOR_COUNT:
        raise QwenM2QueryVectorsError("Qwen M2 final artifact row counts are invalid")
    try:
        vectors = np.load(vector_path, allow_pickle=False)
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise QwenM2QueryVectorsError("Qwen M2 final artifacts are unreadable") from exc
    if vectors.dtype != np.float32 or vectors.shape != (70, QWEN_EMBEDDING_DIMENSION) or len(rows) != 70:
        raise QwenM2QueryVectorsError("Qwen M2 final artifacts violate the 70x2048 contract")
    if rows != question_mapping:
        raise QwenM2QueryVectorsError("Qwen M2 final question mapping does not match the run binding")
    batch_vectors = np.concatenate(
        [_load_batch_vectors(output_root, binding, attempt) for binding, attempt in zip(manifest["batches"], attempts, strict=True)],
        axis=0,
    )
    if not np.array_equal(vectors, batch_vectors):
        raise QwenM2QueryVectorsError("Qwen M2 final vectors do not match validated batch vectors")
    expected_identity = sha256_json({
        "run_identity": manifest.get("run_identity"),
        "provider_attempts": manifest.get("provider_attempts"),
        "vectors": artifacts.get("vectors"),
        "question_rows": artifacts.get("question_rows"),
    })
    if manifest.get("artifact_identity") != expected_identity:
        raise QwenM2QueryVectorsError("Qwen M2 final artifact identity does not match")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.isfinite(vectors).all() or np.any(norms == 0) or not np.allclose(norms, 1.0, rtol=0.0, atol=1e-5):
        raise QwenM2QueryVectorsError("Qwen M2 final vectors violate the L2 contract")


def materialize_qwen_m2_query_vectors(
    config: QwenM2QueryVectorConfig,
    output_root: Path,
    transport: QwenEmbeddingTransport,
    *,
    resume: bool = False,
    execution_mode: str = "injected_offline",
) -> dict[str, Any]:
    """Materialize the exact M2 70Q Qwen query vectors through bounded batches.

    Calls are made only through the injected transport.  The caller must opt
    into ``live_dashscope`` before a concrete DashScope transport is accepted.
    An issued ledger row is atomically published before each call; an
    unresolved row is therefore intentionally unrecoverable without review.
    """

    if not callable(getattr(transport, "embed", None)):
        raise QwenM2QueryVectorsError("Qwen M2 transport lacks the injected embed method")
    # Keep Retrieval package initialization independent from Generation.  The
    # exact M2 loader is still the only runtime-input authority at execution.
    from genshin_corpus.generation.measure import load_m2_runtime_questions

    questions = load_m2_runtime_questions(Path(config.runtime_input))
    if len(questions) != QWEN_M2_QUERY_VECTOR_COUNT:
        raise QwenM2QueryVectorsError("M2 query-vector materialization requires exactly 70 questions")
    output_root = Path(output_root)
    input_descriptor = _input_descriptor(config, questions)
    transport_binding = _transport_binding(transport, execution_mode)
    bindings = _batch_bindings(questions, config)
    run_identity = _run_identity(config.identity_projection(), input_descriptor, bindings, transport_binding)
    question_mapping = _question_rows(questions)

    if output_root.exists():
        if not resume:
            raise FileExistsError("Qwen M2 query-vector output root already exists")
        manifest = _read_json(output_root / "metadata" / "manifest.json", "existing Qwen M2 query-vector manifest")
        if manifest.get("status") == "complete":
            _validate_run_provenance(output_root, manifest, require_complete=True)
            if manifest.get("run_identity") != run_identity or manifest.get("batches") != bindings:
                raise QwenM2QueryVectorsError("existing Qwen M2 query-vector run is bound to different input, config, or transport")
            _validate_complete_artifact(output_root, manifest)
            return manifest
        question_rows, persisted_batches, attempts = _validate_run_provenance(output_root, manifest, require_complete=False)
        if manifest.get("run_identity") != run_identity or persisted_batches != bindings or question_rows != question_mapping:
            raise QwenM2QueryVectorsError("existing Qwen M2 query-vector run is bound to different input, config, or transport")
    else:
        output_root.mkdir(parents=True)
        attempts = []
        manifest = {
            "schema_version": QWEN_M2_QUERY_VECTORS_SCHEMA_VERSION,
            "status": "in_progress",
            "run_identity": run_identity,
            "configuration": config.identity_projection(),
            "runtime_input": input_descriptor,
            "transport_binding": transport_binding,
            "question_mapping": question_mapping,
            "batches": bindings,
            "completed_batch_count": 0,
            "provider_attempts": _write_ledger(output_root, attempts),
        }
        _write_manifest(output_root, manifest)

    secrets = _transport_secret_values(transport)
    for binding in bindings[len(attempts):]:
        batch_index = binding["batch_index"]
        questions_by_id = {question.question_id: question for question in questions}
        request = QwenEmbeddingRequest(
            role=QWEN_QUERY_ROLE,
            texts=tuple(questions_by_id[question_id].question for question_id in binding["question_ids"]),
        )
        issued = _expected_attempt(binding)
        attempts.append(issued)
        # atomic_write fsyncs the issued state before a provider call can occur.
        manifest["provider_attempts"] = _write_ledger(output_root, attempts)
        manifest["completed_batch_count"] = len(attempts) - 1
        _write_manifest(output_root, manifest)
        response_artifact: dict[str, Any] | None = None
        try:
            response = transport.embed(request)
            if not isinstance(response, QwenEmbeddingResponse):
                raise QwenM2QueryVectorsError("Qwen M2 transport returned an unexpected response type")
            response_artifact = _persist_batch_response(
                output_root,
                batch_index=batch_index,
                raw_response_bytes=response.raw_response_bytes,
                secret_values=secrets,
            )
            vectors = _validate_response(response, request, secrets)
            vector_relative = f"batches/batch-{batch_index:03d}.vectors.f32.npy"
            vector_body = _batch_vector_body(vectors)
            atomic_write(output_root / vector_relative, vector_body)
            resolved = dict(issued)
            resolved.update({
                "status": "succeeded",
                "returned_model": response.returned_model,
                "returned_role": response.returned_role,
                "provider_request_id": response.provider_request_id,
                "response_artifact": response_artifact,
                "vectors_artifact": _descriptor(vector_relative, vector_body, row_count=len(request.texts)),
            })
        except QwenEmbeddingTransportError as exc:
            resolved = dict(issued)
            if exc.provider_request_id is not None and any(secret in exc.provider_request_id for secret in secrets):
                resolved.update({"status": "failed", "error": {"category": "response_evidence_rejected", "code": "UnsafeProviderRequestId"}})
            else:
                try:
                    response_artifact = _persist_batch_response(
                        output_root,
                        batch_index=batch_index,
                        raw_response_bytes=exc.raw_response_bytes,
                        secret_values=secrets,
                    )
                except QwenEmbeddingPreflightError:
                    resolved.update({"status": "failed", "error": {"category": "response_evidence_rejected", "code": "UnsafeResponseEvidence"}})
                else:
                    error: dict[str, Any] = {"category": "transport_error", "code": exc.code, "status_code": exc.status_code}
                    if exc.provider_request_id is not None:
                        error["provider_request_id"] = exc.provider_request_id
                    resolved.update({"status": "failed", "error": error})
                    if response_artifact is not None:
                        resolved["response_artifact"] = response_artifact
        except QwenEmbeddingPreflightError as exc:
            resolved = dict(issued)
            resolved.update({"status": "failed", "error": {"category": "response_invalid", "code": type(exc).__name__}})
            if response_artifact is not None:
                resolved["response_artifact"] = response_artifact
        except QwenM2QueryVectorsError:
            raise
        attempts[-1] = resolved
        manifest["provider_attempts"] = _write_ledger(output_root, attempts)
        if resolved["status"] != "succeeded":
            manifest["status"] = "failed"
            manifest["failure"] = dict(resolved["error"])
            _write_manifest(output_root, manifest)
            return manifest
        manifest["completed_batch_count"] = len(attempts)
        _write_manifest(output_root, manifest)
    return _complete_artifact(output_root, manifest, attempts)


def load_qwen_m2_query_vectors(output_root: Path) -> tuple[list[dict[str, Any]], Any, dict[str, Any]]:
    """Load a completed artifact only after revalidating its immutable binding."""

    import numpy as np

    output_root = Path(output_root)
    manifest = _read_json(output_root / "metadata" / "manifest.json", "Qwen M2 query-vector manifest")
    _validate_complete_artifact(output_root, manifest)
    artifacts = manifest["artifacts"]
    vectors = np.load(output_root / artifacts["vectors"]["path"], allow_pickle=False)
    rows = [json.loads(line) for line in (output_root / artifacts["question_rows"]["path"]).read_text(encoding="utf-8").splitlines()]
    return rows, vectors, manifest
