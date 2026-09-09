"""Offline full-corpus packing for the reviewed Qwen Batch JSONL seam."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
import gzip
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .qwen_batch_embedding import (
    QWEN_BATCH_EMBEDDINGS_PATH,
    QWEN_BATCH_ENCODING_FORMAT,
    QWEN_BATCH_JSONL_SCHEMA_VERSION,
    QwenBatchEmbeddingError,
    qwen_batch_record_for_unit,
)
from .qwen_embedding import QWEN_EMBEDDING_DIMENSION, QWEN_EMBEDDING_MODEL_ID, _artifact_descriptor
from .retrieval_units import RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION


QWEN_BATCH_PACKING_SCHEMA_VERSION = "phase04-rag-qwen37-batch-packing-0.1"
QWEN_BATCH_MAX_REQUESTS_PER_FILE = 50_000
QWEN_BATCH_MAX_FILE_BYTES = 500_000_000
# The reviewed provider row ceiling is kept separate from the file ceiling so
# one oversized request fails before any shard-boundary decision.
QWEN_BATCH_MAX_ROW_BYTES = 10_000_000
QWEN_BATCH_INPUT_PRICE_CNY_PER_MILLION = 0.25
QWEN_SYNCHRONOUS_INPUT_PRICE_CNY_PER_MILLION = 0.50


class QwenBatchPackingError(ValueError):
    """Raised when a full-corpus Batch packing input or output is invalid."""


@dataclass(frozen=True)
class QwenBatchPackingConfig:
    max_requests_per_file: int = QWEN_BATCH_MAX_REQUESTS_PER_FILE
    max_file_bytes: int = QWEN_BATCH_MAX_FILE_BYTES
    max_row_bytes: int = QWEN_BATCH_MAX_ROW_BYTES

    def __post_init__(self) -> None:
        for field in ("max_requests_per_file", "max_file_bytes", "max_row_bytes"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise QwenBatchPackingError(f"Qwen Batch packing {field} must be a positive integer")
        if self.max_row_bytes > self.max_file_bytes:
            raise QwenBatchPackingError("Qwen Batch row limit cannot exceed the file limit")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise QwenBatchPackingError(f"cannot read {path}") from exc
    return digest.hexdigest()


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QwenBatchPackingError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, Mapping):
        raise QwenBatchPackingError(f"{label} must be an object")
    return value


def _accepted_ru_input(manifest_path: Path) -> tuple[Mapping[str, Any], Path, int]:
    manifest_path = Path(manifest_path)
    manifest = _read_object(manifest_path, "Retrieval Unit manifest")
    if manifest.get("status") != "complete" or manifest.get("schema_version") != RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION:
        raise QwenBatchPackingError("Retrieval Unit manifest is not a complete accepted artifact")
    build_identity = manifest.get("build_identity")
    accounting = manifest.get("accounting")
    artifacts = manifest.get("artifacts")
    if not isinstance(build_identity, str) or not isinstance(accounting, Mapping) or not isinstance(artifacts, Mapping):
        raise QwenBatchPackingError("Retrieval Unit manifest lacks accepted bindings")
    expected_count = accounting.get("retrieval_unit_count")
    descriptor = artifacts.get("retrieval_units")
    if not isinstance(expected_count, int) or expected_count <= 0 or not isinstance(descriptor, Mapping):
        raise QwenBatchPackingError("Retrieval Unit manifest lacks retrieval-unit accounting")
    relative_path, expected_sha, expected_bytes = descriptor.get("path"), descriptor.get("sha256"), descriptor.get("byte_count")
    if not isinstance(relative_path, str) or not isinstance(expected_sha, str) or not isinstance(expected_bytes, int):
        raise QwenBatchPackingError("Retrieval Unit artifact descriptor is invalid")
    artifact_path = manifest_path.parent.parent / relative_path
    if _sha256_file(artifact_path) != expected_sha or artifact_path.stat().st_size != expected_bytes:
        raise QwenBatchPackingError("Retrieval Unit artifact integrity does not match its manifest")
    return manifest, artifact_path, expected_count


def _stream_accepted_units(artifact_path: Path) -> Iterator[Mapping[str, Any]]:
    previous_order: tuple[Any, ...] | None = None
    previous_unit_id: str | None = None
    try:
        with gzip.open(artifact_path, "rt", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    raise QwenBatchPackingError("accepted Retrieval Unit artifact contains a blank row")
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise QwenBatchPackingError("accepted Retrieval Unit row is not an object")
                unit_id, text, source_order = value.get("unit_id"), value.get("retrieval_visible_text"), value.get("source_order")
                if not isinstance(unit_id, str) or not unit_id or not isinstance(text, str) or not text or not isinstance(source_order, list):
                    raise QwenBatchPackingError("accepted Retrieval Unit row lacks identity, text, or deterministic order")
                order = tuple(source_order)
                if previous_order is not None and (order, unit_id) <= (previous_order, previous_unit_id):
                    raise QwenBatchPackingError("accepted Retrieval Unit artifact is not in deterministic accepted order")
                previous_order, previous_unit_id = order, unit_id
                yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        raise QwenBatchPackingError("cannot stream accepted Retrieval Unit artifact") from exc


def _shard_id(build_identity: str, index: int) -> str:
    return sha256_json({"schema_version": QWEN_BATCH_PACKING_SCHEMA_VERSION, "retrieval_unit_build_identity": build_identity, "shard_index": index})


def _token_cost_estimate(utf8_bytes: int) -> dict[str, Any]:
    """Use a deliberately broad byte proxy when no accepted Qwen tokenizer exists."""

    lower = (utf8_bytes + 3) // 4
    upper = utf8_bytes
    def cost(rate: float) -> dict[str, float]:
        return {"lower": lower * rate / 1_000_000, "upper": upper * rate / 1_000_000}
    batch_cost, synchronous_cost = cost(QWEN_BATCH_INPUT_PRICE_CNY_PER_MILLION), cost(QWEN_SYNCHRONOUS_INPUT_PRICE_CNY_PER_MILLION)
    balance = "appears_sufficient_for_proxy_range" if batch_cost["upper"] <= 50 else "proxy_range_exceeds_or_spans_cny_50"
    return {
        "status": "heuristic_not_provider_billing",
        "method": "UTF-8-byte proxy range: ceil(bytes/4) through bytes; no accepted local Qwen tokenizer was available",
        "uncertainty": "This deliberately broad proxy is not an exact Qwen tokenizer count or provider bill.",
        "input_token_estimate_range": {"lower": lower, "upper": upper},
        "batch_list_price_cny_per_million_input_tokens": QWEN_BATCH_INPUT_PRICE_CNY_PER_MILLION,
        "batch_cost_cny_estimate_range": batch_cost,
        "synchronous_list_price_cny_per_million_input_tokens": QWEN_SYNCHRONOUS_INPUT_PRICE_CNY_PER_MILLION,
        "synchronous_cost_cny_estimate_range": synchronous_cost,
        "cny_50_balance_assessment": balance,
    }


def _open_mapping(path: Path) -> tuple[Any, Any]:
    raw = path.open("wb")
    return raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)


def _pack_units(
    units: Iterable[Mapping[str, Any]],
    *,
    retrieval_unit_build_identity: str,
    expected_count: int,
    output_root: Path,
    config: QwenBatchPackingConfig,
) -> dict[str, Any]:
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError("Qwen Batch packing output root already exists")
    shards_root = output_root / "artifacts" / "shards"
    shards_root.mkdir(parents=True)
    mapping_relative = "artifacts/ru_mapping.jsonl.gz"
    mapping_path = output_root / mapping_relative
    mapping_raw, mapping_handle = _open_mapping(mapping_path)
    shard_records: list[dict[str, Any]] = []
    seen_unit_ids: set[str] = set()
    seen_custom_ids: set[str] = set()
    text_sizes: list[int] = []
    corpus_chars = corpus_bytes = total_jsonl_bytes = 0
    next_shard_index = shard_count = shard_bytes = 0
    active_shard_index: int | None = None
    shard_handle: Any | None = None
    shard_digest: Any | None = None

    def close_shard() -> None:
        nonlocal shard_handle, shard_digest, shard_count, shard_bytes, active_shard_index
        if shard_handle is None or shard_digest is None or active_shard_index is None:
            return
        shard_handle.close()
        relative = f"artifacts/shards/shard-{active_shard_index:05d}.jsonl"
        shard_records.append({
            "shard_index": active_shard_index,
            "shard_id": _shard_id(retrieval_unit_build_identity, active_shard_index),
            "path": relative,
            "request_count": shard_count,
            "byte_count": shard_bytes,
            "sha256": shard_digest.hexdigest(),
        })
        shard_handle = shard_digest = active_shard_index = None
        shard_count = shard_bytes = 0

    try:
        for accepted_index, unit in enumerate(units):
            unit_id = unit.get("unit_id")
            text = unit.get("retrieval_visible_text")
            if not isinstance(unit_id, str) or unit_id in seen_unit_ids or not isinstance(text, str) or not text:
                raise QwenBatchPackingError("accepted Retrieval Unit identity/text is duplicate or invalid")
            try:
                record = qwen_batch_record_for_unit(retrieval_unit_build_identity=retrieval_unit_build_identity, unit=unit)
            except QwenBatchEmbeddingError as exc:
                raise QwenBatchPackingError(str(exc)) from exc
            line = canonical_json_bytes(record) + b"\n"
            if len(line) > config.max_row_bytes or len(line) > config.max_file_bytes:
                raise QwenBatchPackingError("one Qwen Batch request violates the configured provider/model row or file limit")
            custom_id = record["custom_id"]
            if custom_id in seen_custom_ids:
                raise QwenBatchPackingError("Qwen Batch custom_id is not unique across accepted Retrieval Units")
            if shard_handle is None or shard_count >= config.max_requests_per_file or shard_bytes + len(line) > config.max_file_bytes:
                close_shard()
                active_shard_index = next_shard_index
                shard_path = shards_root / f"shard-{active_shard_index:05d}.jsonl"
                shard_handle = shard_path.open("wb")
                shard_digest = sha256()
                shard_count = shard_bytes = 0
                next_shard_index += 1
            assert shard_handle is not None and shard_digest is not None and active_shard_index is not None
            current_index = shard_count
            shard_handle.write(line)
            shard_digest.update(line)
            shard_count += 1
            shard_bytes += len(line)
            total_jsonl_bytes += len(line)
            text_bytes = len(text.encode("utf-8"))
            text_sizes.append(text_bytes)
            corpus_chars += len(text)
            corpus_bytes += text_bytes
            seen_unit_ids.add(unit_id)
            seen_custom_ids.add(custom_id)
            mapping_handle.write(canonical_json_bytes({
                "accepted_index": accepted_index,
                "unit_id": unit_id,
                "retrieval_visible_text_sha256": sha256(text.encode("utf-8")).hexdigest(),
                "shard_id": _shard_id(retrieval_unit_build_identity, active_shard_index),
                "shard_index": active_shard_index,
                "row_index": current_index,
                "custom_id": custom_id,
            }) + b"\n")
        close_shard()
    finally:
        mapping_handle.close()
        mapping_raw.close()
    if len(seen_unit_ids) != expected_count or len(seen_custom_ids) != expected_count:
        raise QwenBatchPackingError("accepted Retrieval Unit accounting does not match the requested packing count")
    if not shard_records:
        raise QwenBatchPackingError("Qwen Batch packing has no shards")
    mapping_body = mapping_path.read_bytes()
    return {
        "shards": shard_records,
        "mapping": _artifact_descriptor(mapping_relative, mapping_body, expected_count),
        "corpus": {
            "retrieval_unit_count": expected_count,
            "text_character_count": corpus_chars,
            "text_utf8_byte_count": corpus_bytes,
            "request_text_utf8_bytes": {"min": min(text_sizes), "median": median(text_sizes), "max": max(text_sizes)},
            "total_jsonl_bytes": total_jsonl_bytes,
            "expected_dense_fp32_bytes": expected_count * QWEN_EMBEDDING_DIMENSION * 4,
        },
    }


def _read_mapping(path: Path) -> Iterator[Mapping[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    raise QwenBatchPackingError("Qwen Batch mapping contains a blank row")
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise QwenBatchPackingError("Qwen Batch mapping row is invalid")
                yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        raise QwenBatchPackingError("Qwen Batch mapping is unreadable") from exc


def _verify_packing(
    *,
    artifact_path: Path,
    retrieval_unit_build_identity: str,
    output_root: Path,
    result: Mapping[str, Any],
    config: QwenBatchPackingConfig,
) -> None:
    mapping_path = output_root / result["mapping"]["path"]
    mapping_descriptor = result["mapping"]
    mapping_body = mapping_path.read_bytes()
    if mapping_descriptor.get("byte_count") != len(mapping_body) or mapping_descriptor.get("sha256") != sha256(mapping_body).hexdigest():
        raise QwenBatchPackingError("Qwen Batch mapping artifact byte accounting is invalid")
    seen_units: set[str] = set()
    seen_custom: set[str] = set()
    mapping_count = 0
    for index, (unit, mapping) in enumerate(zip(_stream_accepted_units(artifact_path), _read_mapping(mapping_path), strict=True)):
        text = unit["retrieval_visible_text"]
        expected_custom = qwen_batch_record_for_unit(retrieval_unit_build_identity=retrieval_unit_build_identity, unit=unit)["custom_id"]
        if (
            mapping.get("accepted_index") != index
            or mapping.get("unit_id") != unit["unit_id"]
            or mapping.get("custom_id") != expected_custom
            or mapping.get("retrieval_visible_text_sha256") != sha256(text.encode("utf-8")).hexdigest()
            or mapping["unit_id"] in seen_units
            or mapping["custom_id"] in seen_custom
        ):
            raise QwenBatchPackingError("Qwen Batch mapping does not exactly bind accepted Retrieval Units")
        seen_units.add(mapping["unit_id"])
        seen_custom.add(mapping["custom_id"])
        mapping_count += 1
    if mapping_count != result["corpus"]["retrieval_unit_count"] or len(seen_units) != mapping_count or len(seen_custom) != mapping_count:
        raise QwenBatchPackingError("Qwen Batch mapping has missing or duplicate Retrieval Units")
    mapping_iterator = iter(_read_mapping(mapping_path))
    physical_count = 0
    for expected_index, shard in enumerate(result["shards"]):
        if shard.get("shard_index") != expected_index or shard.get("shard_id") != _shard_id(retrieval_unit_build_identity, expected_index):
            raise QwenBatchPackingError("Qwen Batch shard order or identity is non-deterministic")
        path = output_root / str(shard["path"])
        digest, byte_count, row_count = sha256(), 0, 0
        try:
            with path.open("rb") as handle:
                for row_index, line in enumerate(handle):
                    digest.update(line)
                    byte_count += len(line)
                    if not line.endswith(b"\n") or len(line) > config.max_row_bytes:
                        raise QwenBatchPackingError("Qwen Batch shard row limit is invalid")
                    try:
                        row, mapping = json.loads(line), next(mapping_iterator)
                    except StopIteration as exc:
                        raise QwenBatchPackingError("Qwen Batch mapping ends before the shard rows") from exc
                    except json.JSONDecodeError as exc:
                        raise QwenBatchPackingError("Qwen Batch shard row is invalid JSON") from exc
                    custom_id = row.get("custom_id") if isinstance(row, Mapping) else None
                    body_value = row.get("body") if isinstance(row, Mapping) else None
                    if (
                        not isinstance(mapping, Mapping)
                        or mapping.get("shard_index") != expected_index
                        or mapping.get("shard_id") != shard["shard_id"]
                        or mapping.get("row_index") != row_index
                        or mapping.get("custom_id") != custom_id
                        or not isinstance(body_value, Mapping)
                        or body_value.get("model") != QWEN_EMBEDDING_MODEL_ID
                        or body_value.get("dimensions") != QWEN_EMBEDDING_DIMENSION
                        or body_value.get("encoding_format") != QWEN_BATCH_ENCODING_FORMAT
                        or row.get("url") != QWEN_BATCH_EMBEDDINGS_PATH
                    ):
                        raise QwenBatchPackingError("Qwen Batch shard row does not match its deterministic mapping/operating point")
                    row_count += 1
                    physical_count += 1
        except OSError as exc:
            raise QwenBatchPackingError("Qwen Batch shard is unreadable") from exc
        if byte_count != shard.get("byte_count") or digest.hexdigest() != shard.get("sha256") or row_count != shard.get("request_count") or row_count > config.max_requests_per_file or byte_count > config.max_file_bytes:
            raise QwenBatchPackingError("Qwen Batch shard request/file byte accounting is invalid")
    try:
        next(mapping_iterator)
    except StopIteration:
        pass
    else:
        raise QwenBatchPackingError("Qwen Batch mapping contains rows absent from shards")
    if physical_count != result["corpus"]["retrieval_unit_count"]:
        raise QwenBatchPackingError("Qwen Batch physical shard count is invalid")


def pack_qwen_batch_units(
    units: Iterable[Mapping[str, Any]],
    *,
    retrieval_unit_build_identity: str,
    expected_count: int,
    output_root: Path,
    config: QwenBatchPackingConfig | None = None,
) -> dict[str, Any]:
    """Pack a supplied deterministic RU iterable for small provider-free tests."""

    if not isinstance(retrieval_unit_build_identity, str) or not retrieval_unit_build_identity or expected_count <= 0:
        raise QwenBatchPackingError("Qwen Batch packing input identity/count is invalid")
    return _pack_units(units, retrieval_unit_build_identity=retrieval_unit_build_identity, expected_count=expected_count, output_root=Path(output_root), config=config or QwenBatchPackingConfig())


def verify_qwen_full_corpus_batch_packing(
    retrieval_unit_manifest_path: Path,
    output_root: Path,
    *,
    config: QwenBatchPackingConfig | None = None,
) -> dict[str, Any]:
    """Read and verify an existing complete offline packing without rewriting it."""

    config = config or QwenBatchPackingConfig()
    ru_manifest, artifact_path, expected_count = _accepted_ru_input(Path(retrieval_unit_manifest_path))
    output_root = Path(output_root)
    packing = _read_object(output_root / "metadata" / "packing_manifest.json", "Qwen Batch packing manifest")
    if (
        packing.get("schema_version") != QWEN_BATCH_PACKING_SCHEMA_VERSION
        or packing.get("status") != "complete"
        or packing.get("provider_api_calls") != 0
        or packing.get("retrieval_unit_build_identity") != ru_manifest.get("build_identity")
    ):
        raise QwenBatchPackingError("Qwen Batch packing manifest does not bind the accepted offline input")
    limits = packing.get("provider_limits")
    corpus = packing.get("corpus")
    if (
        not isinstance(limits, Mapping)
        or limits != {
            "max_requests_per_file": config.max_requests_per_file,
            "max_file_bytes": config.max_file_bytes,
            "max_row_bytes": config.max_row_bytes,
        }
        or not isinstance(corpus, Mapping)
        or corpus.get("retrieval_unit_count") != expected_count
        or not isinstance(packing.get("shards"), list)
        or not isinstance(packing.get("mapping"), Mapping)
    ):
        raise QwenBatchPackingError("Qwen Batch packing manifest has invalid limits or accounting")
    _verify_packing(
        artifact_path=artifact_path,
        retrieval_unit_build_identity=str(ru_manifest["build_identity"]),
        output_root=output_root,
        result=packing,
        config=config,
    )
    return dict(packing)


def finalize_qwen_full_corpus_batch_packing(
    retrieval_unit_manifest_path: Path,
    output_root: Path,
    *,
    config: QwenBatchPackingConfig | None = None,
) -> dict[str, Any]:
    """Self-verify existing offline shards and write their complete manifest once."""

    config = config or QwenBatchPackingConfig()
    manifest, artifact_path, expected_count = _accepted_ru_input(Path(retrieval_unit_manifest_path))
    output_root = Path(output_root)
    manifest_path = output_root / "metadata" / "packing_manifest.json"
    if manifest_path.exists():
        raise FileExistsError("Qwen Batch packing manifest already exists")
    if expected_count != 535_802:
        raise QwenBatchPackingError("full-corpus Qwen Batch packing requires the accepted 535,802-RU artifact")
    shard_paths = sorted((output_root / "artifacts" / "shards").glob("shard-*.jsonl"))
    if not shard_paths:
        raise QwenBatchPackingError("Qwen Batch packing has no shard files to finalize")
    shard_records: list[dict[str, Any]] = []
    total_jsonl_bytes = 0
    for index, path in enumerate(shard_paths):
        body = path.read_bytes()
        request_count = sum(1 for _ in body.splitlines())
        relative = str(path.relative_to(output_root)).replace("\\", "/")
        shard_records.append({
            "shard_index": index,
            "shard_id": _shard_id(str(manifest["build_identity"]), index),
            "path": relative,
            "request_count": request_count,
            "byte_count": len(body),
            "sha256": sha256(body).hexdigest(),
        })
        total_jsonl_bytes += len(body)
    mapping_path = output_root / "artifacts" / "ru_mapping.jsonl.gz"
    mapping_body = mapping_path.read_bytes()
    text_sizes: list[int] = []
    corpus_chars = corpus_bytes = 0
    for unit in _stream_accepted_units(artifact_path):
        text = str(unit["retrieval_visible_text"])
        size = len(text.encode("utf-8"))
        text_sizes.append(size)
        corpus_chars += len(text)
        corpus_bytes += size
    packed = {
        "shards": shard_records,
        "mapping": _artifact_descriptor("artifacts/ru_mapping.jsonl.gz", mapping_body, expected_count),
        "corpus": {
            "retrieval_unit_count": expected_count,
            "text_character_count": corpus_chars,
            "text_utf8_byte_count": corpus_bytes,
            "request_text_utf8_bytes": {"min": min(text_sizes), "median": median(text_sizes), "max": max(text_sizes)},
            "total_jsonl_bytes": total_jsonl_bytes,
            "expected_dense_fp32_bytes": expected_count * QWEN_EMBEDDING_DIMENSION * 4,
        },
    }
    _verify_packing(
        artifact_path=artifact_path,
        retrieval_unit_build_identity=str(manifest["build_identity"]),
        output_root=output_root,
        result=packed,
        config=config,
    )
    manifest_value = {
        "schema_version": QWEN_BATCH_PACKING_SCHEMA_VERSION,
        "status": "complete",
        "provider_api_calls": 0,
        "retrieval_unit_manifest_path": str(Path(retrieval_unit_manifest_path)),
        "retrieval_unit_build_identity": manifest["build_identity"],
        "operating_point": {
            "model": QWEN_EMBEDDING_MODEL_ID,
            "dimension": QWEN_EMBEDDING_DIMENSION,
            "encoding_format": QWEN_BATCH_ENCODING_FORMAT,
            "endpoint": QWEN_BATCH_EMBEDDINGS_PATH,
        },
        "provider_limits": {
            "max_requests_per_file": config.max_requests_per_file,
            "max_file_bytes": config.max_file_bytes,
            "max_row_bytes": config.max_row_bytes,
        },
        **packed,
        "token_cost_estimate": _token_cost_estimate(corpus_bytes),
    }
    manifest_value["packing_identity"] = sha256_json({
        "schema_version": QWEN_BATCH_PACKING_SCHEMA_VERSION,
        "retrieval_unit_build_identity": manifest["build_identity"],
        "provider_limits": manifest_value["provider_limits"],
        "shards": manifest_value["shards"],
    })
    atomic_write(manifest_path, canonical_json_bytes(manifest_value))
    return manifest_value


def run_qwen_full_corpus_batch_packing(
    retrieval_unit_manifest_path: Path,
    output_root: Path,
    *,
    config: QwenBatchPackingConfig | None = None,
) -> dict[str, Any]:
    """Stream and self-verify the accepted full RU corpus without any provider call."""

    config = config or QwenBatchPackingConfig()
    manifest, artifact_path, expected_count = _accepted_ru_input(Path(retrieval_unit_manifest_path))
    if expected_count != 535_802:
        raise QwenBatchPackingError("full-corpus Qwen Batch packing requires the accepted 535,802-RU artifact")
    _pack_units(
        _stream_accepted_units(artifact_path),
        retrieval_unit_build_identity=str(manifest["build_identity"]),
        expected_count=expected_count,
        output_root=Path(output_root),
        config=config,
    )
    return finalize_qwen_full_corpus_batch_packing(retrieval_unit_manifest_path, output_root, config=config)
