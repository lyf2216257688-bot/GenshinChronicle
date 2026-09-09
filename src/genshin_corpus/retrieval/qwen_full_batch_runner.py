"""Fail-closed coordinator for the reviewed, immutable Qwen full Batch packing.

This module deliberately has no client construction and no command that can
reach a provider by itself.  A later explicitly authorized caller supplies the
reviewed lifecycle client.  The packed JSONL files are read and hash-bound;
they are never regenerated or modified here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
import gzip
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .candidate_retrieval import DENSE_INDEX_SCHEMA_VERSION
from .qwen_batch_embedding import QwenBatchEmbeddingConfig
from .qwen_batch_lifecycle import (
    QWEN_BATCH_LIVE_EXECUTION_MODE,
    QWEN_BATCH_OFFLINE_EXECUTION_MODE,
    QwenBatchLifecycleClient,
    QwenBatchLifecycleError,
    resume_qwen_batch_lifecycle,
    submit_qwen_batch_lifecycle,
)
from .qwen_embedding import (
    QWEN_EMBEDDING_DIMENSION,
    QWEN_EMBEDDING_MODEL_ID,
    QwenEmbeddingRequest,
    _artifact_descriptor,
)


QWEN_FULL_BATCH_RUNNER_SCHEMA_VERSION = "phase04-rag-qwen37-full-batch-runner-0.1"


class QwenFullBatchRunnerError(ValueError):
    """Raised when a full-run action could duplicate paid work or corrupt a merge."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenFullBatchRunnerError(f"Qwen full Batch {label} is unavailable or invalid") from exc
    if not isinstance(value, Mapping):
        raise QwenFullBatchRunnerError(f"Qwen full Batch {label} must be an object")
    return dict(value)


def _sha_descriptor(path: Path, *, row_count: int | None = None) -> dict[str, Any]:
    digest, byte_count = sha256(), 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_count += len(chunk)
    except OSError as exc:
        raise QwenFullBatchRunnerError("Qwen full Batch artifact is unreadable") from exc
    value: dict[str, Any] = {"sha256": digest.hexdigest(), "byte_count": byte_count}
    if row_count is not None:
        value["row_count"] = row_count
    return value


def _packing_bindings(packing_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    packing_root = Path(packing_root)
    packing = _read_json(packing_root / "metadata" / "packing_manifest.json", "packing manifest")
    if packing.get("status") != "complete" or packing.get("provider_api_calls") != 0:
        raise QwenFullBatchRunnerError("Qwen full Batch packing is not a complete provider-free artifact")
    corpus, mapping, shards = packing.get("corpus"), packing.get("mapping"), packing.get("shards")
    if not isinstance(corpus, Mapping) or not isinstance(mapping, Mapping) or not isinstance(shards, list):
        raise QwenFullBatchRunnerError("Qwen full Batch packing lacks corpus, mapping, or shards")
    expected_total = corpus.get("retrieval_unit_count")
    if not isinstance(expected_total, int) or expected_total <= 0 or mapping.get("row_count") != expected_total:
        raise QwenFullBatchRunnerError("Qwen full Batch packing row accounting is invalid")
    mapping_path = packing_root / str(mapping.get("path"))
    if _sha_descriptor(mapping_path, row_count=expected_total) != {key: mapping.get(key) for key in ("sha256", "byte_count", "row_count")}:
        raise QwenFullBatchRunnerError("Qwen full Batch mapping bytes do not match its finalized manifest")
    bindings: list[dict[str, Any]] = []
    total = 0
    for expected_index, item in enumerate(shards):
        if not isinstance(item, Mapping):
            raise QwenFullBatchRunnerError("Qwen full Batch shard descriptor is invalid")
        binding = {key: item.get(key) for key in ("shard_index", "shard_id", "path", "request_count", "byte_count", "sha256")}
        if (
            binding["shard_index"] != expected_index
            or not isinstance(binding["shard_id"], str)
            or not isinstance(binding["path"], str)
            or not isinstance(binding["request_count"], int)
            or binding["request_count"] <= 0
        ):
            raise QwenFullBatchRunnerError("Qwen full Batch shard ordering or descriptor is invalid")
        physical = packing_root / binding["path"]
        actual = _sha_descriptor(physical)
        if actual != {"sha256": binding["sha256"], "byte_count": binding["byte_count"]}:
            raise QwenFullBatchRunnerError("Qwen full Batch physical shard does not match its finalized descriptor")
        try:
            with physical.open("rb") as handle:
                count = sum(1 for _ in handle)
        except OSError as exc:
            raise QwenFullBatchRunnerError("Qwen full Batch physical shard is unreadable") from exc
        if count != binding["request_count"]:
            raise QwenFullBatchRunnerError("Qwen full Batch physical shard request count is invalid")
        bindings.append(binding)
        total += count
    if total != expected_total:
        raise QwenFullBatchRunnerError("Qwen full Batch shard total does not match the packing corpus")
    return packing, bindings


def _run_manifest_path(run_root: Path) -> Path:
    return run_root / "metadata" / "full_run_manifest.json"


def prepare_qwen_full_batch_run(packing_root: Path, run_root: Path) -> dict[str, Any]:
    """Create the local immutable binding before any lifecycle/client operation."""

    packing_root, run_root = Path(packing_root), Path(run_root)
    if run_root.exists():
        raise FileExistsError("Qwen full Batch run root already exists")
    packing, bindings = _packing_bindings(packing_root)
    source_manifest = packing_root / "metadata" / "packing_manifest.json"
    source = _sha_descriptor(source_manifest)
    result = {
        "schema_version": QWEN_FULL_BATCH_RUNNER_SCHEMA_VERSION,
        "status": "prepared",
        "provider_api_calls": 0,
        "packing": {
            "packing_root": str(packing_root),
            "packing_manifest": {"path": "metadata/packing_manifest.json", **source},
            "packing_identity": packing.get("packing_identity"),
            "retrieval_unit_build_identity": packing.get("retrieval_unit_build_identity"),
            "retrieval_unit_manifest_path": packing.get("retrieval_unit_manifest_path"),
            "mapping": dict(packing["mapping"]),
            "retrieval_unit_count": packing["corpus"]["retrieval_unit_count"],
        },
        "shards": bindings,
    }
    atomic_write(_run_manifest_path(run_root), canonical_json_bytes(result))
    return result


def _read_run(run_root: Path) -> dict[str, Any]:
    result = _read_json(_run_manifest_path(Path(run_root)), "run manifest")
    if result.get("schema_version") != QWEN_FULL_BATCH_RUNNER_SCHEMA_VERSION or result.get("status") != "prepared":
        raise QwenFullBatchRunnerError("Qwen full Batch run manifest is not prepared")
    if not isinstance(result.get("packing"), Mapping) or not isinstance(result.get("shards"), list):
        raise QwenFullBatchRunnerError("Qwen full Batch run manifest is malformed")
    return result


def _assert_prepared_packing_binding(
    run: Mapping[str, Any],
    packing_root: Path,
    packing: Mapping[str, Any],
    bindings: list[dict[str, Any]],
) -> None:
    """Prove a prepared paid run still names these exact packing bytes/identities."""

    prepared = run.get("packing")
    if not isinstance(prepared, Mapping):
        raise QwenFullBatchRunnerError("Qwen full Batch run lacks a prepared packing binding")
    manifest = prepared.get("packing_manifest")
    if not isinstance(manifest, Mapping) or manifest.get("path") != "metadata/packing_manifest.json":
        raise QwenFullBatchRunnerError("Qwen full Batch prepared packing manifest descriptor is invalid")
    actual_manifest = _sha_descriptor(Path(packing_root) / "metadata" / "packing_manifest.json")
    if actual_manifest != {key: manifest.get(key) for key in ("sha256", "byte_count")}:
        raise QwenFullBatchRunnerError("Qwen full Batch current packing manifest differs from the prepared run")
    if (
        prepared.get("packing_identity") != packing.get("packing_identity")
        or prepared.get("retrieval_unit_build_identity") != packing.get("retrieval_unit_build_identity")
        or prepared.get("retrieval_unit_count") != packing.get("corpus", {}).get("retrieval_unit_count")
        or prepared.get("mapping") != packing.get("mapping")
        or run.get("shards") != bindings
    ):
        raise QwenFullBatchRunnerError("Qwen full Batch current packing bindings differ from the prepared run")


def _state_path(run_root: Path, index: int) -> Path:
    return Path(run_root) / "shards" / f"shard-{index:05d}" / "metadata" / "lifecycle_state.json"


def _state(run_root: Path, index: int) -> dict[str, Any] | None:
    path = _state_path(run_root, index)
    return _read_json(path, "shard lifecycle state") if path.exists() else None


def _state_class(state: Mapping[str, Any] | None) -> str:
    if state is None:
        return "not_submitted"
    status = state.get("status")
    if status == "materialized":
        return "materialized"
    if isinstance(status, str) and ("failed" in status or "ambiguous" in status or status.startswith("terminal_")):
        return "failed"
    if status == "created":
        return "created"
    return "pending"


def _has_batch_id(state: Mapping[str, Any]) -> bool:
    batch = state.get("batch")
    return isinstance(batch, Mapping) and isinstance(batch.get("batch_id"), str)


def qwen_full_batch_dry_run(packing_root: Path, run_root: Path | None = None) -> dict[str, Any]:
    """Report the next paid action without constructing a client or writing state."""

    packing, bindings = _packing_bindings(Path(packing_root))
    if run_root is not None:
        run_path = Path(run_root)
        if run_path.exists() and not _run_manifest_path(run_path).exists():
            raise QwenFullBatchRunnerError("Qwen full Batch existing run root lacks its prepared manifest")
        if run_path.exists():
            _assert_prepared_packing_binding(_read_run(run_path), Path(packing_root), packing, bindings)
            states = [_state(run_path, int(binding["shard_index"])) for binding in bindings]
        else:
            states = [None for _ in bindings]
    else:
        states = [None for _ in bindings]
    classes = [_state_class(item) for item in states]
    counts = Counter(classes)
    new_jobs = 0
    stopped = False
    for classification, state in zip(classes, states, strict=True):
        if classification == "failed" or (classification == "pending" and state is not None and not _has_batch_id(state)):
            stopped = True
        elif not stopped and classification == "not_submitted":
            new_jobs += 1
    return {
        "schema_version": QWEN_FULL_BATCH_RUNNER_SCHEMA_VERSION,
        "operation": "dry_run",
        "provider_api_calls": 0,
        "shard_count": len(bindings),
        "request_count": packing["corpus"]["retrieval_unit_count"],
        "shard_status_counts": {key: counts.get(key, 0) for key in ("not_submitted", "created", "pending", "materialized", "failed")},
        "new_batch_jobs_next_submit": new_jobs,
    }


def _mapping_rows(packing_root: Path) -> Iterator[dict[str, Any]]:
    packing = _read_json(Path(packing_root) / "metadata" / "packing_manifest.json", "packing manifest")
    try:
        with gzip.open(Path(packing_root) / str(packing["mapping"]["path"]), "rt", encoding="utf-8") as handle:
            for line in handle:
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise QwenFullBatchRunnerError("Qwen full Batch mapping row is invalid")
                yield dict(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenFullBatchRunnerError("Qwen full Batch mapping is unreadable") from exc


def _shard_config(run: Mapping[str, Any], binding: Mapping[str, Any], packing_root: Path) -> QwenBatchEmbeddingConfig:
    rows = _mapping_rows(packing_root)
    ids: list[str] = []
    index = binding["shard_index"]
    for row in rows:
        if row.get("shard_index") == index:
            if row.get("shard_id") != binding["shard_id"] or row.get("row_index") != len(ids) or not isinstance(row.get("unit_id"), str):
                raise QwenFullBatchRunnerError("Qwen full Batch mapping does not bind the shard deterministically")
            ids.append(row["unit_id"])
    if len(ids) != binding["request_count"]:
        raise QwenFullBatchRunnerError("Qwen full Batch mapping count does not match the shard")
    manifest_path = Path(str(run["packing"]["retrieval_unit_manifest_path"]))
    return QwenBatchEmbeddingConfig(manifest_path, tuple(ids))


def _assert_lifecycle_binding(run_root: Path, binding: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    input_value = state.get("input")
    if not isinstance(input_value, Mapping) or input_value.get("sha256") != binding["sha256"] or input_value.get("byte_count") != binding["byte_count"] or input_value.get("row_count") != binding["request_count"]:
        raise QwenFullBatchRunnerError("Qwen full Batch lifecycle input does not match its immutable shard binding")
    if state.get("lifecycle_binding") != dict(binding):
        raise QwenFullBatchRunnerError("Qwen full Batch lifecycle lacks its immutable shard binding")
    binding_path = Path(run_root) / "shards" / f"shard-{binding['shard_index']:05d}" / "metadata" / "shard_binding.json"
    if binding_path.exists() and _read_json(binding_path, "shard binding") != dict(binding):
        raise QwenFullBatchRunnerError("Qwen full Batch persisted shard binding changed")
    if not binding_path.exists():
        atomic_write(binding_path, canonical_json_bytes(dict(binding)))


def submit_pending_qwen_full_batch_run(
    packing_root: Path,
    run_root: Path,
    client: QwenBatchLifecycleClient,
    *,
    execution_mode: str = QWEN_BATCH_OFFLINE_EXECUTION_MODE,
) -> list[dict[str, Any]]:
    """Submit only never-issued shards, in order, and stop at the first unsafe state."""

    run = _read_run(run_root)
    packing, bindings = _packing_bindings(packing_root)
    _assert_prepared_packing_binding(run, Path(packing_root), packing, bindings)
    results: list[dict[str, Any]] = []
    for binding in bindings:
        index = int(binding["shard_index"])
        prior = _state(run_root, index)
        classification = _state_class(prior)
        if classification == "materialized":
            results.append(dict(prior))
            continue
        if classification == "failed":
            raise QwenFullBatchRunnerError(f"Qwen full Batch shard {index} requires review before later submission")
        if prior is not None:
            _assert_lifecycle_binding(run_root, binding, prior)
            if not _has_batch_id(prior):
                raise QwenFullBatchRunnerError(f"Qwen full Batch shard {index} has an unresolved paid operation")
            results.append(dict(prior))
            continue
        physical = Path(packing_root) / str(binding["path"])
        body = physical.read_bytes()
        if _sha_descriptor(physical) != {"sha256": binding["sha256"], "byte_count": binding["byte_count"]}:
            raise QwenFullBatchRunnerError("Qwen full Batch physical shard changed before submission")
        config = _shard_config(run, binding, Path(packing_root))
        # This recomputation is only an equality proof; the immutable packed bytes
        # remain the authoritative upload body and are copied by the lifecycle ledger.
        from .qwen_batch_embedding import build_qwen_batch_jsonl
        if build_qwen_batch_jsonl(config) != body:
            raise QwenFullBatchRunnerError("Qwen full Batch selected RU JSONL does not equal the finalized shard")
        state = submit_qwen_batch_lifecycle(
            config,
            Path(run_root) / "shards" / f"shard-{index:05d}",
            client,
            execution_mode=execution_mode,
            lifecycle_binding=binding,
        )
        _assert_lifecycle_binding(run_root, binding, state)
        results.append(state)
        if _state_class(state) == "failed":
            raise QwenFullBatchRunnerError(f"Qwen full Batch shard {index} stopped after an auditable lifecycle failure")
    return results


def resume_qwen_full_batch_run(
    packing_root: Path,
    run_root: Path,
    client: QwenBatchLifecycleClient,
    *,
    execution_mode: str = QWEN_BATCH_OFFLINE_EXECUTION_MODE,
) -> list[dict[str, Any]]:
    """Retrieve persisted Batch IDs only; this operation cannot submit a Batch."""

    run = _read_run(run_root)
    packing, bindings = _packing_bindings(packing_root)
    _assert_prepared_packing_binding(run, Path(packing_root), packing, bindings)
    results: list[dict[str, Any]] = []
    for binding in bindings:
        index = int(binding["shard_index"])
        prior = _state(run_root, index)
        if prior is None:
            continue
        _assert_lifecycle_binding(run_root, binding, prior)
        classification = _state_class(prior)
        if classification == "materialized":
            results.append(dict(prior))
            continue
        if classification == "failed":
            raise QwenFullBatchRunnerError(f"Qwen full Batch shard {index} requires review before later resume")
        batch = prior.get("batch")
        if not isinstance(batch, Mapping) or not isinstance(batch.get("batch_id"), str):
            raise QwenFullBatchRunnerError(f"Qwen full Batch shard {index} has no resumable persisted batch_id")
        state = resume_qwen_batch_lifecycle(_shard_config(run, binding, Path(packing_root)), Path(run_root) / "shards" / f"shard-{index:05d}", client, execution_mode=execution_mode)
        _assert_lifecycle_binding(run_root, binding, state)
        results.append(state)
        if _state_class(state) == "failed":
            raise QwenFullBatchRunnerError(f"Qwen full Batch shard {index} stopped after an auditable lifecycle failure")
    return results


def merge_qwen_full_batch_results(packing_root: Path, run_root: Path) -> dict[str, Any]:
    """Merge only all-materialized shards into one deterministic Dense artifact."""

    import numpy as np

    run = _read_run(run_root)
    packing, bindings = _packing_bindings(packing_root)
    _assert_prepared_packing_binding(run, Path(packing_root), packing, bindings)
    expected_count = int(packing["corpus"]["retrieval_unit_count"])
    states = []
    for binding in bindings:
        state = _state(run_root, int(binding["shard_index"]))
        if _state_class(state) != "materialized":
            raise QwenFullBatchRunnerError("Qwen full Batch final merge requires every shard to be materialized")
        assert state is not None
        _assert_lifecycle_binding(run_root, binding, state)
        states.append(state)
    final_root = Path(run_root) / "final"
    if final_root.exists():
        raise FileExistsError("Qwen full Batch final Dense artifact already exists")
    vector_path = final_root / "dense" / "artifacts" / "vectors.f32.npy"
    vector_path.parent.mkdir(parents=True, exist_ok=False)
    vectors = np.lib.format.open_memmap(vector_path, mode="w+", dtype=np.float32, shape=(expected_count, QWEN_EMBEDDING_DIMENSION))
    seen_ids: set[str] = set()
    seen_custom: set[str] = set()
    mapping_iter = _mapping_rows(Path(packing_root))
    row_lines: list[bytes] = []
    position = 0
    try:
        for binding, state in zip(bindings, states, strict=True):
            shard_root = Path(run_root) / "shards" / f"shard-{binding['shard_index']:05d}" / "materialized"
            dense_manifest = _read_json(shard_root / "dense" / "metadata" / "manifest.json", "shard Dense manifest")
            if dense_manifest.get("embedding_dimension") != QWEN_EMBEDDING_DIMENSION or dense_manifest.get("dtype") != "float32" or dense_manifest.get("normalization") != "L2" or dense_manifest.get("row_count") != binding["request_count"]:
                raise QwenFullBatchRunnerError("Qwen full Batch shard Dense contract is invalid")
            shard_vectors = np.load(shard_root / "dense" / "artifacts" / "vectors.f32.npy", allow_pickle=False)
            if shard_vectors.dtype != np.float32 or shard_vectors.shape != (binding["request_count"], QWEN_EMBEDDING_DIMENSION) or not np.isfinite(shard_vectors).all():
                raise QwenFullBatchRunnerError("Qwen full Batch shard vectors are invalid")
            norms = np.linalg.norm(shard_vectors, axis=1)
            if np.any(norms == 0) or not np.allclose(norms, 1.0, rtol=0.0, atol=1e-5):
                raise QwenFullBatchRunnerError("Qwen full Batch shard vectors violate L2 normalization")
            with gzip.open(shard_root / "dense" / "artifacts" / "rows.jsonl.gz", "rt", encoding="utf-8") as handle:
                shard_rows = [json.loads(line) for line in handle]
            materialization = _read_json(shard_root / "metadata" / "batch_materialization.json", "shard materialization")
            returned = materialization.get("remote_provider_provenance", {}).get("document_returned_models") if isinstance(materialization.get("remote_provider_provenance"), Mapping) else None
            if not isinstance(returned, list) or len(returned) != binding["request_count"] or len(shard_rows) != binding["request_count"]:
                raise QwenFullBatchRunnerError("Qwen full Batch shard materialization row accounting is invalid")
            for row_index, (dense_row, returned_row) in enumerate(zip(shard_rows, returned, strict=True)):
                mapping = next(mapping_iter, None)
                if not isinstance(mapping, Mapping) or mapping.get("shard_index") != binding["shard_index"] or mapping.get("shard_id") != binding["shard_id"] or mapping.get("row_index") != row_index:
                    raise QwenFullBatchRunnerError("Qwen full Batch mapping order is invalid")
                unit_id, custom_id = mapping.get("unit_id"), mapping.get("custom_id")
                if (not isinstance(unit_id, str) or not isinstance(custom_id, str) or unit_id in seen_ids or custom_id in seen_custom or dense_row.get("occurrence_index") != row_index or dense_row.get("unit_id") != unit_id or returned_row.get("custom_id") != custom_id):
                    raise QwenFullBatchRunnerError("Qwen full Batch results are missing, duplicate, or unknown")
                seen_ids.add(unit_id)
                seen_custom.add(custom_id)
                vectors[position] = shard_vectors[row_index]
                row_lines.append(canonical_json_bytes({"occurrence_index": position, "unit_id": unit_id}) + b"\n")
                position += 1
        if next(mapping_iter, None) is not None or position != expected_count:
            raise QwenFullBatchRunnerError("Qwen full Batch final result count is incomplete or duplicated")
        vectors.flush()
    except Exception:
        del vectors
        raise
    del vectors
    rows_body = gzip.compress(b"".join(row_lines), mtime=0)
    atomic_write(final_root / "dense" / "artifacts" / "rows.jsonl.gz", rows_body)
    vector_descriptor = _sha_descriptor(vector_path)
    provenance = {
        "kind": "remote_provider",
        "transport_contract_version": QWEN_FULL_BATCH_RUNNER_SCHEMA_VERSION,
        "requested_model": QWEN_EMBEDDING_MODEL_ID,
        "local_model_weight_sha256": None,
        "remote_model_weight_sha256": None,
        "weight_hash_status": "not_available_for_remote_provider",
        "shards": [{"shard_index": binding["shard_index"], "shard_id": binding["shard_id"], "batch_id": state["batch"]["batch_id"]} for binding, state in zip(bindings, states, strict=True)],
    }
    request = QwenEmbeddingRequest(role="document", texts=("full-corpus-batch",))
    dense_manifest = {
        "schema_version": DENSE_INDEX_SCHEMA_VERSION,
        "status": "complete",
        "arm": "dense",
        "arm_build_identity": sha256_json({"retrieval_unit_build_identity": run["packing"]["retrieval_unit_build_identity"], "packing_identity": run["packing"]["packing_identity"], "shard_batches": provenance["shards"]}),
        "retrieval_unit_build_identity": run["packing"]["retrieval_unit_build_identity"],
        "model_name": QWEN_EMBEDDING_MODEL_ID,
        "model_revision": None,
        "model_sha256": None,
        "embedding_dimension": QWEN_EMBEDDING_DIMENSION,
        "dtype": "float32",
        "normalization": "L2",
        "instruction": None,
        "vectorization_schema_version": QWEN_FULL_BATCH_RUNNER_SCHEMA_VERSION,
        "row_mapping_policy": "packed RU mapping order",
        "document_request_identity": request.request_identity,
        "remote_provider_provenance": provenance,
        "row_count": expected_count,
        "artifacts": {
            "vectors": {"path": "artifacts/vectors.f32.npy", **vector_descriptor},
            "rows": _artifact_descriptor("artifacts/rows.jsonl.gz", rows_body, expected_count),
        },
    }
    manifest_body = canonical_json_bytes(dense_manifest)
    atomic_write(final_root / "dense" / "metadata" / "manifest.json", manifest_body)
    result = {"schema_version": QWEN_FULL_BATCH_RUNNER_SCHEMA_VERSION, "status": "materialized", "row_count": expected_count, "dense_manifest": _artifact_descriptor("dense/metadata/manifest.json", manifest_body), "shard_provenance": provenance["shards"]}
    atomic_write(final_root / "metadata" / "full_materialization.json", canonical_json_bytes(result))
    return result
