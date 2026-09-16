"""Provider-free mechanical closure for the Phase 04 Retrieval/Reranking candidate.

This module deliberately stops before live reranking or Generation.  It binds
the accepted RU, Qwen corpus Dense, and Q001-Q070 query-vector artifacts, then
records a reproducible field-aware retrieval run and a small deterministic
reranker/fusion preflight.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import gzip
import json
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.measure import load_m2_runtime_questions

from .candidate_retrieval import (
    ACCEPTED_QWEN_DENSE_ARM_BUILD_IDENTITY,
    ACCEPTED_QWEN_DENSE_MANIFEST_SHA256,
    ACCEPTED_QWEN_DENSE_ROWS,
    ACCEPTED_QWEN_DENSE_ROWS_SHA256,
    ACCEPTED_QWEN_DENSE_VECTORS_SHA256,
    ACCEPTED_QWEN_RU_BUILD_IDENTITY,
    DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS,
    DEFAULT_QWEN_DENSE_MANIFEST,
    FIELD_AWARE_LEXICAL_SCORER_VERSION,
    FIELD_AWARE_LEXICAL_FIELDS,
    FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION,
    FIELD_AWARE_LEXICAL_PROJECTION_VERSION,
    FIELD_AWARE_LEXICAL_ROW_ORDER_POLICY,
    LEXICAL_ANALYZER_VERSION,
    _field_aware_lexical_arm_identity,
    _load_lexical,
    build_field_aware_lexical_index,
    hybrid_candidates,
    load_batch_candidate_retriever,
    lexical_candidates,
)
from .evidence_assembly import EvidenceAssemblyConfig
from .qwen_m2_query_vectors import load_qwen_m2_query_vectors
from .qwen_rerank import (
    DASHSCOPE_QWEN_RERANK_TRANSPORT_VERSION,
    QWEN_RERANK_MODEL_ID,
)
from .reranking import (
    RERANKER_PROJECTION_VERSION,
    RankFusionConfig,
    RerankCandidate,
    RerankRequest,
    RerankScore,
    fuse_ranked_candidates,
    project_ranked_candidates,
    project_reranker_text,
    stable_rank_scores,
)
from .retrieval_units import load_retrieval_units


SCHEMA_VERSION = "phase04-rag-block-a-closure-0.1"
QUESTION_IDS = tuple(f"Q{index:03d}" for index in range(1, 71))
DEFAULT_RU_MANIFEST = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/"
    "ru/metadata/manifest.json"
)
DEFAULT_LEGACY_LEXICAL_MANIFEST = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/"
    "lexical/metadata/manifest.json"
)
DEFAULT_QWEN_QUERY_ROOT = Path(".local/p04-qwen-m2-query-vectors-beijing-20260910-162427")
DEFAULT_RUNTIME_INPUT = Path(".local/p04-rag-m2/questions.runtime.jsonl")
DEFAULT_CLOSURE_ROOT = Path(".local/p04-block-a-closure-20260914")
ACCEPTED_QUERY_ARTIFACT_IDENTITY = "a286fc34c643800cf5ba9d8071ce78be9938fa2f64507fa0ab828b71eeea3732"
ACCEPTED_QUERY_VECTORS_SHA256 = "40eb1b2558aa8a7a44b98a09867a038ef4730670a75b3b14f1f161cda309a8bf"
ACCEPTED_RUNTIME_INPUT_SHA256 = "dab333ddfe3061758596cf4196443df274a2f065b8c9c36cbbfe5c52bebe380c"
ACCEPTED_RU_MANIFEST_SHA256 = "dc6bbd30cc6fbc83fa38132085fb8550f20322082467fe24aadcb4239ca78673"
ACCEPTED_RERANK_WORKSPACE = "ws-gdq9z4ufdb87egio"
ACCEPTED_RERANK_ENDPOINT = (
    "https://ws-gdq9z4ufdb87egio.cn-beijing.maas.aliyuncs.com/"
    "api/v1/services/rerank/text-rerank/text-rerank"
)
EXPECTED_RU_ROW_COUNT = 535802
PROVISIONAL_CANDIDATE_SUPPLY_DEPTH = 500
PROVISIONAL_RERANK_DEPTH = 500
PROVISIONAL_FINAL_TOP_N = 20


class BlockAClosureError(ValueError):
    """Raised when provider-free Block A closure cannot preserve its bindings."""


def _sha256_file(path: Path) -> str:
    digest = sha256()
    try:
        with Path(path).open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise BlockAClosureError(f"unreadable file: {path}") from exc
    return digest.hexdigest()


def _descriptor(path: Path, *, relative: str | None = None) -> dict[str, Any]:
    body = Path(path).read_bytes()
    return {
        "path": relative if relative is not None else str(path),
        "sha256": sha256(body).hexdigest(),
        "byte_count": len(body),
    }


def _immutable_write(path: Path, body: bytes) -> dict[str, Any]:
    path = Path(path)
    if path.exists():
        if path.read_bytes() != body:
            raise BlockAClosureError(f"refusing to overwrite closure artifact: {path}")
    else:
        atomic_write(path, body)
    return _descriptor(path)


def _verify_descriptor(path: Path, descriptor: Mapping[str, Any]) -> None:
    actual = _descriptor(Path(path))
    if actual.get("sha256") != descriptor.get("sha256") or actual.get("byte_count") != descriptor.get("byte_count"):
        raise BlockAClosureError(f"closure output binding mismatch: {path}")


def _materialize_required_outputs(
    output_root: Path,
    question_records: Sequence[Mapping[str, Any]],
    control_record: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Write and immediately verify the final outputs before manifest creation."""

    retrieval_body = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in question_records)
    control_body = canonical_json_bytes(dict(control_record))
    retrieval_path = Path(output_root) / "retrieval" / "q001-q070.jsonl"
    control_path = Path(output_root) / "control" / "legacy-q001.json"
    retrieval_descriptor = _immutable_write(retrieval_path, retrieval_body)
    _verify_descriptor(retrieval_path, retrieval_descriptor)
    control_descriptor = _immutable_write(control_path, control_body)
    _verify_descriptor(control_path, control_descriptor)
    return {"retrieval": retrieval_descriptor, "control": control_descriptor}


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BlockAClosureError(f"{label} is unreadable") from exc
    if not isinstance(value, Mapping):
        raise BlockAClosureError(f"{label} must be an object")
    return value


@dataclass(frozen=True)
class BlockAQualityGateConfig:
    """One provisional operating point for the later paid quality gate."""

    candidate_supply_depth: int = PROVISIONAL_CANDIDATE_SUPPLY_DEPTH
    rerank_depth: int = PROVISIONAL_RERANK_DEPTH
    final_top_n: int = PROVISIONAL_FINAL_TOP_N
    rrf_k: int = 60
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    reranker_projection_max_chars: int = 6000
    fusion_config: RankFusionConfig = RankFusionConfig()
    assembly_config: EvidenceAssemblyConfig = EvidenceAssemblyConfig()

    def __post_init__(self) -> None:
        for name in ("candidate_supply_depth", "rerank_depth", "final_top_n", "rrf_k", "reranker_projection_max_chars"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise BlockAClosureError(f"{name} must be a positive integer")
        if self.rerank_depth > self.candidate_supply_depth:
            raise BlockAClosureError("rerank_depth cannot exceed candidate_supply_depth")
        if self.final_top_n > self.rerank_depth:
            raise BlockAClosureError("final_top_n cannot exceed rerank_depth")
        for name in ("bm25_k1", "bm25_b"):
            value = float(getattr(self, name))
            if value != value or value in (float("inf"), float("-inf")):
                raise BlockAClosureError(f"{name} must be finite")
        if self.bm25_k1 < 0 or not 0 <= self.bm25_b <= 1:
            raise BlockAClosureError("invalid BM25 operating point")
        if not isinstance(self.fusion_config, RankFusionConfig):
            raise BlockAClosureError("fusion_config must be RankFusionConfig")
        if not isinstance(self.assembly_config, EvidenceAssemblyConfig):
            raise BlockAClosureError("assembly_config must be EvidenceAssemblyConfig")

    @property
    def field_weights(self) -> dict[str, float]:
        return {field: float(DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS[field]) for field in FIELD_AWARE_LEXICAL_FIELDS}

    def projection(self) -> dict[str, Any]:
        return {
            "candidate_supply_depth": self.candidate_supply_depth,
            "rerank_depth": self.rerank_depth,
            "final_top_n": self.final_top_n,
            "rrf_k": self.rrf_k,
            "bm25": {"k1": float(self.bm25_k1), "b": float(self.bm25_b)},
            "lexical": {
                "schema_version": FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION,
                "projection_version": FIELD_AWARE_LEXICAL_PROJECTION_VERSION,
                "fields": list(FIELD_AWARE_LEXICAL_FIELDS),
                "analyzer_version": LEXICAL_ANALYZER_VERSION,
                "scorer_version": FIELD_AWARE_LEXICAL_SCORER_VERSION,
                "field_weights": self.field_weights,
                "row_order_policy": FIELD_AWARE_LEXICAL_ROW_ORDER_POLICY,
            },
            "dense": {
                "model": "qwen3.7-text-embedding",
                "arm_build_identity": ACCEPTED_QWEN_DENSE_ARM_BUILD_IDENTITY,
                "query_projection": {
                    "role": "query",
                    "custom_query_instruction": None,
                    "dimension": 2048,
                    "output": "dense",
                },
            },
            "reranker_projection": {
                "version": RERANKER_PROJECTION_VERSION,
                "max_chars": self.reranker_projection_max_chars,
                "token_estimate": None,
            },
            "reranker_provider": {
                "provider": "dashscope",
                "transport_contract_version": DASHSCOPE_QWEN_RERANK_TRANSPORT_VERSION,
                "model": QWEN_RERANK_MODEL_ID,
                "endpoint": ACCEPTED_RERANK_ENDPOINT,
            },
            "fusion": self.fusion_config.to_dict(),
            "assembly": self.assembly_config.to_dict(),
        }

    @property
    def identity(self) -> str:
        return sha256_json(self.projection())


def _git_state() -> dict[str, Any]:
    def output(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BlockAClosureError(f"git state unavailable: {' '.join(args)}") from exc

    return {
        "branch": output("branch", "--show-current"),
        "head": output("rev-parse", "HEAD"),
        "status_porcelain": output("status", "--porcelain=v1"),
    }


def _validate_accepted_inputs(
    ru_manifest_path: Path,
    query_root: Path,
    dense_manifest_path: Path,
    runtime_input: Path,
) -> tuple[list[dict[str, Any]], Any, Mapping[str, Any], list[dict[str, Any]], Mapping[str, Any]]:
    try:
        query_rows, query_vectors, query_manifest = load_qwen_m2_query_vectors(query_root)
    except Exception as exc:
        raise BlockAClosureError("accepted Qwen query-vector artifact could not be validated") from exc
    if (
        query_manifest.get("artifact_identity") != ACCEPTED_QUERY_ARTIFACT_IDENTITY
        or query_manifest.get("artifacts", {}).get("vectors", {}).get("sha256") != ACCEPTED_QUERY_VECTORS_SHA256
        or len(query_rows) != 70
        or getattr(query_vectors, "shape", None) != (70, 2048)
    ):
        raise BlockAClosureError("accepted Qwen query-vector binding mismatch")
    runtime_questions = load_m2_runtime_questions(runtime_input)
    if _sha256_file(runtime_input) != ACCEPTED_RUNTIME_INPUT_SHA256:
        raise BlockAClosureError("accepted runtime question input hash mismatch")
    expected_question_ids = [item.question_id for item in runtime_questions]
    if expected_question_ids != list(QUESTION_IDS) or [row.get("question_id") for row in query_rows] != list(QUESTION_IDS):
        raise BlockAClosureError("accepted question order is not Q001-Q070")
    expected_identities = [item.question_identity for item in runtime_questions]
    if [row.get("question_identity") for row in query_rows] != expected_identities:
        raise BlockAClosureError("accepted query-vector question identities do not match runtime input")
    ru_manifest, units = load_retrieval_units(ru_manifest_path)
    if (
        _sha256_file(ru_manifest_path) != ACCEPTED_RU_MANIFEST_SHA256
        or ru_manifest.get("build_identity") != ACCEPTED_QWEN_RU_BUILD_IDENTITY
        or len(units) != EXPECTED_RU_ROW_COUNT
    ):
        raise BlockAClosureError("accepted Retrieval Unit binding mismatch")
    dense_manifest_path = Path(dense_manifest_path)
    if _sha256_file(dense_manifest_path) != ACCEPTED_QWEN_DENSE_MANIFEST_SHA256:
        raise BlockAClosureError("accepted Qwen Dense manifest hash mismatch")
    return query_rows, query_vectors, query_manifest, [dict(unit) for unit in units], ru_manifest


def _validate_field_artifact(
    manifest_path: Path,
    ru_units: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    manifest = _read_json(manifest_path, "field-aware lexical manifest")
    if (
        manifest.get("status") != "complete"
        or manifest.get("schema_version") != FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION
        or tuple(manifest.get("fields", ())) != FIELD_AWARE_LEXICAL_FIELDS
        or manifest.get("projection_version") != FIELD_AWARE_LEXICAL_PROJECTION_VERSION
        or manifest.get("analyzer_version") != LEXICAL_ANALYZER_VERSION
        or manifest.get("scorer_version") != FIELD_AWARE_LEXICAL_SCORER_VERSION
        or manifest.get("row_order_policy") != FIELD_AWARE_LEXICAL_ROW_ORDER_POLICY
        or manifest.get("row_count") != EXPECTED_RU_ROW_COUNT
        or manifest.get("retrieval_unit_build_identity") != ACCEPTED_QWEN_RU_BUILD_IDENTITY
        or manifest.get("arm_build_identity") != _field_aware_lexical_arm_identity(ACCEPTED_QWEN_RU_BUILD_IDENTITY)
    ):
        raise BlockAClosureError("field-aware lexical manifest binding mismatch")
    artifacts = manifest.get("artifacts")
    descriptor = artifacts.get("index") if isinstance(artifacts, Mapping) else None
    if not isinstance(descriptor, Mapping):
        raise BlockAClosureError("field-aware lexical index descriptor is missing")
    body_path = Path(manifest_path).parent.parent / str(descriptor.get("path"))
    actual = _descriptor(body_path)
    for key in ("sha256", "byte_count"):
        if actual[key] != descriptor.get(key):
            raise BlockAClosureError(f"field-aware lexical index {key} mismatch")
    if descriptor.get("row_count") != EXPECTED_RU_ROW_COUNT:
        raise BlockAClosureError("field-aware lexical index row count mismatch")
    expected_ids = [str(unit.get("unit_id")) for unit in ru_units]
    actual_ids: list[str] = []
    try:
        with gzip.open(body_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if not isinstance(row, Mapping) or not isinstance(row.get("unit_id"), str):
                    raise BlockAClosureError("field-aware lexical row identity is invalid")
                fields = row.get("fields")
                if not isinstance(fields, Mapping) or tuple(sorted(fields)) != tuple(sorted(FIELD_AWARE_LEXICAL_FIELDS)):
                    raise BlockAClosureError("field-aware lexical row field projection is invalid")
                actual_ids.append(row["unit_id"])
    except (OSError, UnicodeError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        raise BlockAClosureError("field-aware lexical index is unreadable") from exc
    if actual_ids != expected_ids:
        raise BlockAClosureError("field-aware lexical row order is not the RU order")
    return manifest


def _candidate_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    return sha256(canonical_json_bytes(list(rows))).hexdigest()


def _validate_window(rows: Sequence[Mapping[str, Any]], *, expected_count: int, mode: str) -> None:
    if len(rows) != expected_count:
        raise BlockAClosureError(f"{mode} candidate supply count is {len(rows)}, expected {expected_count}")
    seen: set[str] = set()
    for rank, row in enumerate(rows, 1):
        unit_id = row.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id or unit_id in seen or row.get("rank") != rank:
            raise BlockAClosureError(f"{mode} candidate identity/rank lineage is invalid")
        seen.add(unit_id)


def _unit_fields(unit: Mapping[str, Any]) -> dict[str, str]:
    source = unit.get("source")
    record_context = source.get("record_context") if isinstance(source, Mapping) else None
    structure = unit.get("structure")
    dialogue = structure.get("dialogue") if isinstance(structure, Mapping) else None

    def text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    return {
        "record_title": text(record_context.get("record_title") if isinstance(record_context, Mapping) else None),
        "section_name": text(record_context.get("section_name") if isinstance(record_context, Mapping) else None),
        "speaker": text(dialogue.get("speaker") if isinstance(dialogue, Mapping) else None),
        "retrieval_visible_text": text(unit.get("retrieval_visible_text")),
    }


def _reranker_preflight(units_by_id: Mapping[str, Mapping[str, Any]], config: BlockAQualityGateConfig) -> dict[str, Any]:
    selected = list(units_by_id.values())[:3]
    if len(selected) != 3:
        raise BlockAClosureError("reranker preflight fixture lacks candidates")
    hybrid = [
        {
            "unit_id": str(unit["unit_id"]),
            "rank": rank,
            "retrieval": {"mode": "hybrid", "arm_build_identities": {"lexical": "fixture", "dense": "fixture"}},
        }
        for rank, unit in enumerate(selected, 1)
    ]
    request = RerankRequest(
        "block-a-preflight",
        "provider-free reranker preflight",
        tuple(
            RerankCandidate(str(unit["unit_id"]), project_reranker_text(_unit_fields(unit), max_chars=config.reranker_projection_max_chars)[0], rank)
            for rank, unit in enumerate(selected, 1)
        ),
    )
    fake_scores = [RerankScore(row.unit_id, row.original_rank, float(4 - row.original_rank), 0) for row in request.candidates]
    ranked = project_ranked_candidates(hybrid, stable_rank_scores(request, fake_scores))
    fused = fuse_ranked_candidates(hybrid, ranked, config=config.fusion_config)
    repeated = fuse_ranked_candidates(hybrid, ranked, config=config.fusion_config)
    if fused != repeated or len(fused[: config.final_top_n]) != min(config.final_top_n, len(fused)):
        raise BlockAClosureError("provider-free reranker/fusion preflight is non-deterministic")
    return {
        "status": "pass",
        "provider_calls": 0,
        "request_candidate_count": len(request.candidates),
        "request_identity": request.question_id,
        "projected_candidate_ids": [row.unit_id for row in request.candidates],
        "reranked_ids": [row["unit_id"] for row in ranked],
        "fused_ids": [row["unit_id"] for row in fused],
        "final_top_n": config.final_top_n,
        "final_ids": [row["unit_id"] for row in fused[: config.final_top_n]],
        "disabled_control": {"status": "available", "reranker_enabled": False},
        "malformed_response_checks": "covered_by_focused_reranking_tests",
    }


def run_block_a_closure(
    *,
    output_root: Path,
    field_artifact_root: Path | None = None,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    legacy_lexical_manifest_path: Path = DEFAULT_LEGACY_LEXICAL_MANIFEST,
    dense_manifest_path: Path = DEFAULT_QWEN_DENSE_MANIFEST,
    query_root: Path = DEFAULT_QWEN_QUERY_ROOT,
    runtime_input: Path = DEFAULT_RUNTIME_INPUT,
    config: BlockAQualityGateConfig | None = None,
) -> dict[str, Any]:
    """Materialize and execute the provider-free Block A closure."""

    config = config or BlockAQualityGateConfig()
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError(f"closure output root already exists: {output_root}")
    field_artifact_root = output_root / "field-aware-lexical" if field_artifact_root is None else Path(field_artifact_root)
    if field_artifact_root.exists():
        raise FileExistsError(f"field-aware lexical output root already exists: {field_artifact_root}")

    query_rows, query_vectors, query_manifest, ru_units, ru_manifest = _validate_accepted_inputs(
        Path(ru_manifest_path), Path(query_root), Path(dense_manifest_path), Path(runtime_input)
    )
    output_root.mkdir(parents=True)
    started = perf_counter()
    lexical_manifest = build_field_aware_lexical_index(Path(ru_manifest_path), field_artifact_root)
    lexical_build_seconds = perf_counter() - started
    field_manifest_path = field_artifact_root / "metadata" / "manifest.json"
    field_manifest = _validate_field_artifact(field_manifest_path, ru_units)
    retriever = load_batch_candidate_retriever(field_manifest_path, Path(dense_manifest_path), accepted_qwen=True)
    units_by_id = {str(unit["unit_id"]): unit for unit in ru_units}
    question_records: list[dict[str, Any]] = []
    control_records: list[dict[str, Any]] = []
    retrieval_started = perf_counter()
    try:
        for index, question_id in enumerate(QUESTION_IDS):
            question = str(query_rows[index]["question"])
            stage_timings: dict[str, float] = {}
            windows = retriever.candidates_for_query(
                question,
                query_vectors[index],
                instruction=None,
                top_k=config.candidate_supply_depth,
                candidate_supply_depth=config.candidate_supply_depth,
                k1=config.bm25_k1,
                b=config.bm25_b,
                field_weights=config.field_weights,
                rrf_k=config.rrf_k,
                telemetry=stage_timings,
            )
            for mode in ("lexical", "dense", "hybrid"):
                _validate_window(windows[mode], expected_count=config.candidate_supply_depth, mode=mode)
            if len(windows["hybrid"]) <= 20:
                raise BlockAClosureError(f"{question_id} did not exceed the hidden Top20 boundary")
            projected_chars = 0
            for row in windows["hybrid"][: config.rerank_depth]:
                unit = units_by_id.get(str(row["unit_id"]))
                if unit is None:
                    raise BlockAClosureError(f"{question_id} candidate is absent from RU snapshot")
                _, audit = project_reranker_text(_unit_fields(unit), max_chars=config.reranker_projection_max_chars)
                projected_chars += int(audit["projected_char_count"])
            question_records.append({
                "question_id": question_id,
                "question_identity": query_rows[index]["question_identity"],
                "query_vector_row_index": index,
                "query_request_identity": query_manifest["batches"][index // 20]["request_identity"],
                "candidate_supply_depth": config.candidate_supply_depth,
                "rerank_depth": config.rerank_depth,
                "final_top_n": config.final_top_n,
                "counts": {mode: len(windows[mode]) for mode in ("lexical", "dense", "hybrid")},
                "candidate_hashes": {mode: _candidate_hash(windows[mode]) for mode in ("lexical", "dense", "hybrid")},
                "top20_ids": [str(row["unit_id"]) for row in windows["hybrid"][:20]],
                "last_ids": [str(row["unit_id"]) for row in windows["hybrid"][-3:]],
                "reranker_projection": {
                    "candidate_count": config.rerank_depth,
                    "projected_char_count": projected_chars,
                    "token_estimate": None,
                },
                "timing_seconds": {
                    "lexical": stage_timings.get("lexical"),
                    "dense": stage_timings.get("dense"),
                    "rrf": stage_timings.get("fusion"),
                },
            })
            if index == 0:
                legacy_rows = lexical_candidates(Path(legacy_lexical_manifest_path), question, top_k=20)
                control_hybrid = hybrid_candidates(
                    legacy_rows,
                    windows["dense"][:20],
                    lexical_build_identity=str(_load_lexical(Path(legacy_lexical_manifest_path))[0]["arm_build_identity"]),
                    dense_build_identity=ACCEPTED_QWEN_DENSE_ARM_BUILD_IDENTITY,
                    top_k=20,
                    rrf_k=config.rrf_k,
                )
                control_records.append({
                    "question_id": question_id,
                    "legacy_lexical_count": len(legacy_rows),
                    "legacy_hybrid_count": len(control_hybrid),
                    "legacy_hybrid_candidate_hash": _candidate_hash(control_hybrid),
                    "status": "pass",
                })
    finally:
        vectors = getattr(retriever, "dense_vectors", None)
        handle = getattr(vectors, "_mmap", None)
        if handle is not None and callable(getattr(handle, "close", None)) and not bool(getattr(handle, "closed", False)):
            handle.close()
    retrieval_seconds = perf_counter() - retrieval_started
    rerank_preflight = _reranker_preflight(units_by_id, config)
    code_paths = [
        Path(__file__),
        Path(__file__).with_name("candidate_retrieval.py"),
        Path(__file__).with_name("reranking.py"),
    ]
    output_descriptors = _materialize_required_outputs(
        output_root,
        question_records,
        control_records[0] if control_records else {"status": "unavailable"},
    )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "run_identity": sha256_json({
            "schema_version": SCHEMA_VERSION,
            "config_identity": config.identity,
            "ru_manifest_sha256": _sha256_file(Path(ru_manifest_path)),
            "field_lexical_manifest": _descriptor(field_manifest_path),
            "dense_manifest_sha256": _sha256_file(Path(dense_manifest_path)),
            "query_artifact_identity": query_manifest["artifact_identity"],
            "query_vectors_sha256": query_manifest["artifacts"]["vectors"]["sha256"],
            "runtime_input_sha256": _sha256_file(Path(runtime_input)),
            "retrieval_output": output_descriptors["retrieval"],
            "control_output": output_descriptors["control"],
            "code": {str(path): _sha256_file(path) for path in code_paths},
        }),
        "question_ids": list(QUESTION_IDS),
        "configuration": config.projection(),
        "configuration_identity": config.identity,
        "provenance": {
            "retrieval_unit_manifest": _descriptor(Path(ru_manifest_path)),
            "retrieval_unit_build_identity": ru_manifest["build_identity"],
            "retrieval_unit_row_count": len(ru_units),
            "field_aware_lexical_manifest": _descriptor(field_manifest_path),
            "field_aware_lexical_arm_build_identity": field_manifest["arm_build_identity"],
            "qwen_dense_manifest": _descriptor(Path(dense_manifest_path)),
            "qwen_dense_arm_build_identity": ACCEPTED_QWEN_DENSE_ARM_BUILD_IDENTITY,
            "qwen_dense_vectors_sha256": ACCEPTED_QWEN_DENSE_VECTORS_SHA256,
            "qwen_dense_rows_sha256": ACCEPTED_QWEN_DENSE_ROWS_SHA256,
            "qwen_dense_row_count": ACCEPTED_QWEN_DENSE_ROWS,
            "qwen_query_manifest": _descriptor(Path(query_root) / "metadata" / "manifest.json"),
            "qwen_query_artifact_identity": query_manifest["artifact_identity"],
            "qwen_query_vectors_sha256": query_manifest["artifacts"]["vectors"]["sha256"],
            "runtime_input": _descriptor(Path(runtime_input)),
            "query_projection": query_manifest["configuration"],
        },
        "code_git_state": {"git": _git_state(), "source_sha256": {str(path): _sha256_file(path) for path in code_paths}},
        "execution": {
            "provider_calls": {"embedding": 0, "reranker": 0, "generation": 0, "network": 0},
            "field_lexical_build_seconds": lexical_build_seconds,
            "retrieval_seconds": retrieval_seconds,
            "retrieval_question_count": len(question_records),
            "control_question_count": len(control_records),
            "query_vector_reuse": True,
            "reranker_preflight": rerank_preflight,
        },
        "artifacts": {
            "field_aware_lexical_manifest": _descriptor(field_manifest_path),
            "field_aware_lexical_index": _descriptor(field_artifact_root / "artifacts" / "field_aware_lexical_index.jsonl.gz"),
            "retrieval_output": output_descriptors["retrieval"],
            "control_output": output_descriptors["control"],
        },
    }
    _immutable_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(manifest))
    return manifest


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the provider-free Block A Retrieval/Reranking closure")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_block_a_closure(output_root=args.output_root)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
