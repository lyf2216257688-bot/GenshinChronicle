"""Provider-free BGE-vs-Qwen Dense comparison over frozen 70Q inputs."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from . import broader_admission_comparison as frozen_comparison
from .broader_admission_supply import (
    ACCEPTED_CANDIDATE_HASHES_SHA256,
    ACCEPTED_RUNTIME_INPUT_SHA256,
    DEFAULT_CANDIDATE_HASHES,
    _historical_hashes,
)
from .candidate_retrieval import (
    _dense_candidates_from_loaded,
    _read_gzip_jsonl,
    hybrid_candidates,
)
from .evidence_assembly import (
    DEFERRED_FOOTPRINT_CHARGE_POLICY,
    EvidenceAssemblyConfig,
    EvidenceAssemblyDiagnostics,
    assemble_deferred_footprint_charge_packet,
    evidence_packet_json_bytes,
    prepare_evidence_assembly_context,
)
from .qwen_m2_query_vectors import load_qwen_m2_query_vectors


SCHEMA_VERSION = "p04-rag-bge-qwen-dense-comparison-0.1"
QUESTION_IDS = tuple(f"Q{number:03d}" for number in range(1, 71))
DEFAULT_SUPPLY_ROOT = Path(".local/p04-rag-broader-admission-70q-candidate-supply")
DEFAULT_RU_MANIFEST = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json"
)
DEFAULT_QWEN_DENSE_MANIFEST = Path(
    ".local/p04-qwen-full-batch-beijing-20260909/final/dense/metadata/manifest.json"
)
DEFAULT_QWEN_QUERY_ROOT = Path(".local/p04-qwen-m2-query-vectors-beijing-20260910-162427")
ACCEPTED_QWEN_QUERY_ARTIFACT_IDENTITY = "a286fc34c643800cf5ba9d8071ce78be9938fa2f64507fa0ab828b71eeea3732"
ACCEPTED_QWEN_QUERY_VECTORS_SHA256 = "40eb1b2558aa8a7a44b98a09867a038ef4730670a75b3b14f1f161cda309a8bf"
ACCEPTED_QWEN_CORPUS_ARM_BUILD_IDENTITY = "be3efd531bcf514148e9f2b3162dbaed99fe1ac0b5257121864dff6416525922"
ACCEPTED_QWEN_CORPUS_VECTORS_SHA256 = "4d6337822459ede93f18d5384e37cbbbef4363b830a5e705d788864dc02dfb8a"
ACCEPTED_QWEN_CORPUS_ROWS_SHA256 = "54590bc5a198ad65301cf6e274c9c0931b48288015596760f5d3b7d12caee701"


class BgeQwenDenseComparisonError(ValueError):
    """Raised when an immutable comparison input or output is unsafe."""


def _sha256_file(path: Path) -> str:
    try:
        digest = sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise BgeQwenDenseComparisonError(f"unreadable artifact: {path}") from exc


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BgeQwenDenseComparisonError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, Mapping):
        raise BgeQwenDenseComparisonError(f"{label} must be a JSON object")
    return value


def _descriptor(path: Path, body: bytes) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


def _immutable_write(path: Path, body: bytes) -> dict[str, Any]:
    if path.exists():
        if path.read_bytes() != body:
            raise BgeQwenDenseComparisonError(f"refusing to overwrite different comparison artifact: {path}")
    else:
        atomic_write(path, body)
    return _descriptor(path, body)


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    atomic_write(path, canonical_json_bytes(dict(manifest)))


def _expected_config() -> EvidenceAssemblyConfig:
    config = EvidenceAssemblyConfig()
    if config.total_context_chars != 12000 or config.per_block_chars != 3000:
        raise BgeQwenDenseComparisonError("accepted B0 Evidence Assembly configuration is unavailable")
    return config


def _config_projection(config: EvidenceAssemblyConfig) -> dict[str, Any]:
    return {
        "neighbor_before": config.neighbor_before,
        "neighbor_after": config.neighbor_after,
        "structured_neighbor_before": config.structured_neighbor_before,
        "structured_neighbor_after": config.structured_neighbor_after,
        "dialogue_hops": config.dialogue_hops,
        "per_block_chars": config.per_block_chars,
        "total_context_chars": config.total_context_chars,
    }


def _validate_arm_rows(
    rows: Any,
    *,
    question_id: str,
    mode: str,
    expected_build_identity: str,
) -> list[Mapping[str, Any]]:
    if not isinstance(rows, list) or len(rows) != 20:
        raise BgeQwenDenseComparisonError(f"frozen {mode} Top20 is incomplete: {question_id}")
    seen: set[str] = set()
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or row.get("rank") != rank:
            raise BgeQwenDenseComparisonError(f"frozen {mode} Top20 ordering is invalid: {question_id}")
        unit_id, retrieval = row.get("unit_id"), row.get("retrieval")
        if not isinstance(unit_id, str) or not unit_id or unit_id in seen:
            raise BgeQwenDenseComparisonError(f"frozen {mode} Top20 membership is invalid: {question_id}")
        if not isinstance(retrieval, Mapping) or retrieval.get("mode") != mode:
            raise BgeQwenDenseComparisonError(f"frozen {mode} candidate mode is invalid: {question_id}")
        if retrieval.get("arm_build_identity") != expected_build_identity:
            raise BgeQwenDenseComparisonError(f"frozen {mode} arm binding is invalid: {question_id}")
        seen.add(unit_id)
    return list(rows)


def _validate_hybrid_rows(rows: Any, *, question_id: str, lexical_identity: str, dense_identity: str) -> list[Mapping[str, Any]]:
    if not isinstance(rows, list) or len(rows) != 20:
        raise BgeQwenDenseComparisonError(f"frozen Hybrid Top20 is incomplete: {question_id}")
    seen: set[str] = set()
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or row.get("rank") != rank:
            raise BgeQwenDenseComparisonError(f"frozen Hybrid Top20 ordering is invalid: {question_id}")
        unit_id, retrieval = row.get("unit_id"), row.get("retrieval")
        if not isinstance(unit_id, str) or not unit_id or unit_id in seen:
            raise BgeQwenDenseComparisonError(f"frozen Hybrid Top20 membership is invalid: {question_id}")
        if not isinstance(retrieval, Mapping) or retrieval.get("mode") != "hybrid":
            raise BgeQwenDenseComparisonError(f"frozen Hybrid candidate mode is invalid: {question_id}")
        identities, fusion = retrieval.get("arm_build_identities"), retrieval.get("fusion")
        if not isinstance(identities, Mapping) or identities != {"lexical": lexical_identity, "dense": dense_identity}:
            raise BgeQwenDenseComparisonError(f"frozen Hybrid arm binding is invalid: {question_id}")
        if not isinstance(fusion, Mapping) or fusion.get("method") != "rrf":
            raise BgeQwenDenseComparisonError(f"frozen Hybrid fusion binding is invalid: {question_id}")
        seen.add(unit_id)
    return list(rows)


def _accepted_candidate_hashes() -> dict[tuple[str, str], str]:
    path = Path(DEFAULT_CANDIDATE_HASHES)
    if _sha256_file(path) != ACCEPTED_CANDIDATE_HASHES_SHA256:
        raise BgeQwenDenseComparisonError("accepted 70Q candidate diagnostic does not match its external SHA-256 binding")
    try:
        hashes = _historical_hashes(path)
    except ValueError as exc:
        raise BgeQwenDenseComparisonError("accepted 70Q candidate diagnostic is structurally invalid") from exc
    if set(hashes) != {(question_id, mode) for question_id in QUESTION_IDS for mode in ("lexical", "dense", "hybrid")}:
        raise BgeQwenDenseComparisonError("accepted 70Q candidate diagnostic does not contain exactly 210 hashes")
    return hashes


def _load_frozen_bge_question(
    supply_root: Path,
    question_id: str,
    manifest: Mapping[str, Any],
    accepted_hashes: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    record_path = supply_root / "candidates" / f"{question_id}.json"
    record = _read_object(record_path, "frozen BGE candidate record")
    if (
        record.get("schema_version") != manifest.get("schema_version")
        or record.get("run_identity") != manifest.get("run_identity")
        or record.get("question_id") != question_id
        or not isinstance(record.get("question_identity"), str)
    ):
        raise BgeQwenDenseComparisonError(f"frozen BGE question binding is invalid: {question_id}")
    windows, hashes = record.get("candidate_windows"), record.get("candidate_hashes")
    baseline = manifest.get("baseline")
    if not isinstance(windows, Mapping) or not isinstance(hashes, Mapping) or not isinstance(baseline, Mapping):
        raise BgeQwenDenseComparisonError(f"frozen BGE candidate windows are invalid: {question_id}")
    lexical = _validate_arm_rows(windows.get("lexical"), question_id=question_id, mode="lexical", expected_build_identity=str(baseline["lexical_build_identity"]))
    dense = _validate_arm_rows(windows.get("dense"), question_id=question_id, mode="dense", expected_build_identity=str(baseline["dense_build_identity"]))
    hybrid = _validate_hybrid_rows(windows.get("hybrid"), question_id=question_id, lexical_identity=str(baseline["lexical_build_identity"]), dense_identity=str(baseline["dense_build_identity"]))
    for mode, rows in (("lexical", lexical), ("dense", dense), ("hybrid", hybrid)):
        actual = frozen_comparison._candidate_sha(rows)
        if hashes.get(mode) != actual or accepted_hashes.get((question_id, mode)) != actual:
            raise BgeQwenDenseComparisonError(f"frozen BGE {mode} candidate SHA mismatch: {question_id}")
    rebuilt = hybrid_candidates(
        lexical,
        dense,
        lexical_build_identity=str(baseline["lexical_build_identity"]),
        dense_build_identity=str(baseline["dense_build_identity"]),
        top_k=20,
        rrf_k=60,
    )
    if canonical_json_bytes(rebuilt) != canonical_json_bytes(hybrid):
        raise BgeQwenDenseComparisonError(f"frozen BGE Hybrid RRF reconstruction mismatch: {question_id}")
    return {
        "record_path": record_path,
        "record": record,
        "lexical": lexical,
        "dense": dense,
        "hybrid": hybrid,
    }


def _validate_frozen_bge_supply(supply_root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        manifest = frozen_comparison._validate_supply_manifest(supply_root)
    except ValueError as exc:
        raise BgeQwenDenseComparisonError(str(exc)) from exc
    accepted_hashes = _accepted_candidate_hashes()
    frozen: dict[str, dict[str, Any]] = {}
    for question_id in QUESTION_IDS:
        frozen[question_id] = _load_frozen_bge_question(supply_root, question_id, manifest, accepted_hashes)
    return dict(manifest), frozen


def _validate_qwen_inputs(
    query_root: Path,
    dense_manifest_path: Path,
) -> tuple[list[dict[str, Any]], Any, Mapping[str, Any], Any, Sequence[Mapping[str, Any]], Mapping[str, Any]]:
    try:
        question_rows, query_vectors, query_manifest = load_qwen_m2_query_vectors(query_root)
        dense_manifest, corpus_vectors, corpus_rows = _load_qwen_dense(dense_manifest_path)
    except ValueError as exc:
        raise BgeQwenDenseComparisonError(str(exc)) from exc
    if query_manifest.get("artifact_identity") != ACCEPTED_QWEN_QUERY_ARTIFACT_IDENTITY:
        raise BgeQwenDenseComparisonError("Qwen query-vector artifact identity does not match accepted binding")
    descriptor = query_manifest.get("artifacts", {}).get("vectors")
    if not isinstance(descriptor, Mapping) or descriptor.get("sha256") != ACCEPTED_QWEN_QUERY_VECTORS_SHA256:
        raise BgeQwenDenseComparisonError("Qwen query-vector SHA-256 does not match accepted binding")
    runtime = query_manifest.get("runtime_input")
    if not isinstance(runtime, Mapping) or runtime.get("question_count") != 70 or runtime.get("sha256") != ACCEPTED_RUNTIME_INPUT_SHA256:
        raise BgeQwenDenseComparisonError("Qwen query-vector runtime binding is invalid")
    if len(question_rows) != 70 or query_vectors.shape != (70, 2048):
        raise BgeQwenDenseComparisonError("Qwen query-vector shape is invalid")
    _validate_accepted_qwen_corpus_binding(dense_manifest)
    return question_rows, query_vectors, query_manifest, corpus_vectors, corpus_rows, dense_manifest


def _validate_accepted_qwen_corpus_binding(manifest: Mapping[str, Any]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise BgeQwenDenseComparisonError("Qwen corpus Dense artifact bindings are invalid")
    vectors, rows = artifacts.get("vectors"), artifacts.get("rows")
    if not isinstance(vectors, Mapping) or not isinstance(rows, Mapping):
        raise BgeQwenDenseComparisonError("Qwen corpus Dense artifact bindings are invalid")
    if (
        manifest.get("arm_build_identity") != ACCEPTED_QWEN_CORPUS_ARM_BUILD_IDENTITY
        or vectors.get("sha256") != ACCEPTED_QWEN_CORPUS_VECTORS_SHA256
        or rows.get("sha256") != ACCEPTED_QWEN_CORPUS_ROWS_SHA256
        or manifest.get("row_count") != 535802
        or rows.get("row_count") != 535802
        or manifest.get("embedding_dimension") != 2048
        or manifest.get("retrieval_unit_build_identity") != frozen_comparison.ACCEPTED_RU_BUILD_IDENTITY
    ):
        raise BgeQwenDenseComparisonError("Qwen corpus Dense does not match accepted arm/vector/rows/RU bindings")


def _load_qwen_dense(path: Path) -> tuple[Mapping[str, Any], Any, list[Mapping[str, Any]]]:
    """Validate the accepted remote Dense artifact without copying its 4 GiB matrix."""

    import numpy as np

    manifest = _read_object(path, "Qwen corpus Dense manifest")
    artifacts = manifest.get("artifacts")
    if manifest.get("status") != "complete" or manifest.get("schema_version") != "phase04-rag-w2-dense-index-0.1" or not isinstance(artifacts, Mapping):
        raise BgeQwenDenseComparisonError("Qwen corpus Dense manifest is not complete")
    _validate_accepted_qwen_corpus_binding(manifest)
    vectors_descriptor, rows_descriptor = artifacts.get("vectors"), artifacts.get("rows")
    if not isinstance(vectors_descriptor, Mapping) or not isinstance(rows_descriptor, Mapping):
        raise BgeQwenDenseComparisonError("Qwen corpus Dense artifacts are invalid")
    root = path.parent.parent
    vectors_path = root / str(vectors_descriptor.get("path"))
    rows_path = root / str(rows_descriptor.get("path"))
    if _sha256_file(vectors_path) != vectors_descriptor.get("sha256") or _sha256_file(rows_path) != rows_descriptor.get("sha256"):
        raise BgeQwenDenseComparisonError("Qwen corpus Dense artifact SHA-256 mismatch")
    try:
        vectors = np.load(vectors_path, allow_pickle=False, mmap_mode="r")
        rows = _read_gzip_jsonl(rows_path)
    except (OSError, ValueError) as exc:
        raise BgeQwenDenseComparisonError("Qwen corpus Dense artifacts are unreadable") from exc
    if vectors.ndim != 2 or vectors.dtype != np.dtype("float32") or vectors.shape != (535802, 2048) or len(rows) != 535802:
        raise BgeQwenDenseComparisonError("Qwen corpus Dense row/vector accounting is invalid")
    for start in range(0, vectors.shape[0], 8192):
        block = vectors[start:start + 8192]
        norms = np.linalg.norm(block, axis=1)
        if not np.isfinite(block).all() or np.any(norms == 0) or not np.allclose(norms, 1.0, rtol=0.0, atol=1e-5):
            raise BgeQwenDenseComparisonError("Qwen corpus Dense vector-space contract is invalid")
    seen_unit_ids: set[str] = set()
    for index, row in enumerate(rows):
        if (
            row.get("occurrence_index") != index
            or not isinstance(row.get("unit_id"), str)
            or not row["unit_id"]
            or row["unit_id"] in seen_unit_ids
        ):
            raise BgeQwenDenseComparisonError("Qwen corpus Dense row mapping is invalid")
        seen_unit_ids.add(row["unit_id"])
    return manifest, vectors, rows


def _rank_delta(control: Sequence[Mapping[str, Any]], challenger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    control_ranks = {str(row["unit_id"]): int(row["rank"]) for row in control}
    challenger_ranks = {str(row["unit_id"]): int(row["rank"]) for row in challenger}
    return {
        "control_only": [row["unit_id"] for row in control if str(row["unit_id"]) not in challenger_ranks],
        "challenger_only": [row["unit_id"] for row in challenger if str(row["unit_id"]) not in control_ranks],
        "shared_rank_changes": [
            {"unit_id": row["unit_id"], "control_rank": control_ranks[str(row["unit_id"])], "challenger_rank": challenger_ranks[str(row["unit_id"])]}
            for row in challenger
            if str(row["unit_id"]) in control_ranks and control_ranks[str(row["unit_id"])] != challenger_ranks[str(row["unit_id"])]
        ],
    }


def _packet_summary(packet: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return frozen_comparison._arm_summary(packet, candidates)


def _visible_delta(control_packet: Mapping[str, Any], challenger_packet: Mapping[str, Any]) -> dict[str, list[str]]:
    control = frozen_comparison._visible_unit_ids(control_packet)
    challenger = frozen_comparison._visible_unit_ids(challenger_packet)
    challenger_set = set(challenger)
    control_set = set(control)
    return {
        "bge_only": [unit_id for unit_id in control if unit_id not in challenger_set],
        "qwen_only": [unit_id for unit_id in challenger if unit_id not in control_set],
        "shared": [unit_id for unit_id in control if unit_id in challenger_set],
    }


def _compare_question(
    frozen: Mapping[str, Any],
    qwen_query_vector: Any,
    *,
    qwen_dense_manifest: Mapping[str, Any],
    qwen_corpus_vectors: Any,
    qwen_corpus_rows: Sequence[Mapping[str, Any]],
    retrieval_unit_manifest_path: Path,
    prepared_context: Any,
    config: EvidenceAssemblyConfig,
) -> dict[str, Any]:
    question_id = str(frozen["record"]["question_id"])
    qwen_dense = _dense_candidates_from_loaded(
        qwen_dense_manifest,
        qwen_corpus_vectors,
        qwen_corpus_rows,
        qwen_query_vector,
        top_k=20,
        query_instruction=None,
    )
    qwen_hybrid = hybrid_candidates(
        frozen["lexical"],
        qwen_dense,
        lexical_build_identity=frozen_comparison.ACCEPTED_LEXICAL_BUILD_IDENTITY,
        dense_build_identity=str(qwen_dense_manifest["arm_build_identity"]),
        top_k=20,
        rrf_k=60,
    )
    audit = {"query_id": question_id, "mode": "hybrid", "candidate_supply": "frozen_bge_vs_qwen"}
    bge_diagnostics = EvidenceAssemblyDiagnostics()
    qwen_diagnostics = EvidenceAssemblyDiagnostics()
    bge_packet = assemble_deferred_footprint_charge_packet(
        retrieval_unit_manifest_path,
        frozen["hybrid"],
        config=config,
        retrieval_audit={**audit, "dense_arm": "bge_frozen"},
        prepared_context=prepared_context,
        diagnostics=bge_diagnostics,
    )
    qwen_packet = assemble_deferred_footprint_charge_packet(
        retrieval_unit_manifest_path,
        qwen_hybrid,
        config=config,
        retrieval_audit={**audit, "dense_arm": "qwen"},
        prepared_context=prepared_context,
        diagnostics=qwen_diagnostics,
    )
    for packet, label in ((bge_packet, "BGE"), (qwen_packet, "Qwen")):
        if (
            packet.get("selection_policy", {}).get("identity") != DEFERRED_FOOTPRINT_CHARGE_POLICY
            or packet.get("assembly_config") != _config_projection(config)
            or packet.get("retrieval_unit_build", {}).get("build_identity") != frozen_comparison.ACCEPTED_RU_BUILD_IDENTITY
        ):
            raise BgeQwenDenseComparisonError(f"{label} Packet does not satisfy the formal Deferred control: {question_id}")
    bge_summary = _packet_summary(bge_packet, frozen["hybrid"])
    qwen_summary = _packet_summary(qwen_packet, qwen_hybrid)
    visible_delta = _visible_delta(bge_packet, qwen_packet)
    return {
        "schema_version": SCHEMA_VERSION,
        "question_id": question_id,
        "question_identity": frozen["record"]["question_identity"],
        "frozen_bge_input": {
            "supply_run_identity": frozen["record"]["run_identity"],
            "record_path": str(frozen["record_path"]),
            "lexical_candidate_sha256": frozen_comparison._candidate_sha(frozen["lexical"]),
            "dense_candidate_sha256": frozen_comparison._candidate_sha(frozen["dense"]),
            "hybrid_candidate_sha256": frozen_comparison._candidate_sha(frozen["hybrid"]),
            "rrf_reconstruction_exact": True,
        },
        "qwen": {
            "dense_candidates": qwen_dense,
            "dense_candidate_sha256": frozen_comparison._candidate_sha(qwen_dense),
            "hybrid_candidates": qwen_hybrid,
            "hybrid_candidate_sha256": frozen_comparison._candidate_sha(qwen_hybrid),
        },
        "candidate_differences": {
            "dense": _rank_delta(frozen["dense"], qwen_dense),
            "hybrid": _rank_delta(frozen["hybrid"], qwen_hybrid),
        },
        "bge": {"packet_summary": bge_summary, "packet": bge_packet, "selection_trace": bge_diagnostics.selection_trace},
        "qwen_packet": {"packet_summary": qwen_summary, "packet": qwen_packet, "selection_trace": qwen_diagnostics.selection_trace},
        "packet_evidence": visible_delta,
        "generation_visible_evidence_identical": (
            bge_summary["generation_visible_projection_sha256"] == qwen_summary["generation_visible_projection_sha256"]
        ),
    }


def _run_identity(supply_manifest: Mapping[str, Any], query_manifest: Mapping[str, Any], dense_manifest: Mapping[str, Any], config: EvidenceAssemblyConfig) -> str:
    return sha256_json({
        "schema_version": SCHEMA_VERSION,
        "question_ids": list(QUESTION_IDS),
        "runtime_input_sha256": supply_manifest["runtime_input_sha256"],
        "frozen_bge_supply_run_identity": supply_manifest["run_identity"],
        "bge_baseline": supply_manifest["baseline"],
        "qwen_query_artifact_identity": query_manifest["artifact_identity"],
        "qwen_query_vectors_sha256": query_manifest["artifacts"]["vectors"]["sha256"],
        "qwen_corpus_dense_arm_build_identity": dense_manifest["arm_build_identity"],
        "qwen_corpus_vectors_sha256": dense_manifest["artifacts"]["vectors"]["sha256"],
        "qwen_corpus_rows_sha256": dense_manifest["artifacts"]["rows"]["sha256"],
        "rrf_k": 60,
        "top_k": 20,
        "formal_deferred_policy": DEFERRED_FOOTPRINT_CHARGE_POLICY,
        "assembly_config": _config_projection(config),
        "runner_source_sha256": _sha256_file(Path(__file__)),
        "provider_calls": 0,
        "generation_calls": 0,
        "bge_query_encodings": 0,
        "bm25_reruns": 0,
    })


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    non_identical = [row for row in rows if not row["generation_visible_evidence_identical"]]
    return {
        "question_count": len(rows),
        "generation_visible_packet_identical_count": len(rows) - len(non_identical),
        "generation_visible_packet_non_identical_count": len(non_identical),
        "dense_control_only_total": sum(len(row["candidate_differences"]["dense"]["control_only"]) for row in rows),
        "dense_qwen_only_total": sum(len(row["candidate_differences"]["dense"]["challenger_only"]) for row in rows),
        "hybrid_control_only_total": sum(len(row["candidate_differences"]["hybrid"]["control_only"]) for row in rows),
        "hybrid_qwen_only_total": sum(len(row["candidate_differences"]["hybrid"]["challenger_only"]) for row in rows),
        "bge_packet_only_evidence_total": sum(len(row["packet_evidence"]["bge_only"]) for row in rows),
        "qwen_packet_only_evidence_total": sum(len(row["packet_evidence"]["qwen_only"]) for row in rows),
        "shared_packet_evidence_total": sum(len(row["packet_evidence"]["shared"]) for row in rows),
        "notable_packet_difference_question_ids": [row["question_id"] for row in non_identical],
        "candidate_difference_question_ids": [
            row["question_id"] for row in rows
            if any(row["candidate_differences"][mode][key] for mode in ("dense", "hybrid") for key in ("control_only", "challenger_only"))
        ],
    }


def _blinded_records(
    rows: Sequence[Mapping[str, Any]],
    run_identity: str,
    questions_by_id: Mapping[str, str],
) -> tuple[bytes, bytes]:
    review_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    for row in rows:
        bge = row["bge"]["packet_summary"]["generation_visible_projection"]
        qwen = row["qwen_packet"]["packet_summary"]["generation_visible_projection"]
        bge_side_a = int(sha256(f"{run_identity}:{row['question_id']}".encode("ascii")).hexdigest(), 16) % 2 == 0
        sides = {"side_a": bge if bge_side_a else qwen, "side_b": qwen if bge_side_a else bge}
        review_rows.append({
            "question_id": row["question_id"],
            "question": questions_by_id[row["question_id"]],
            "packet_a": sides["side_a"],
            "packet_b": sides["side_b"],
        })
        mapping_rows.append({"question_id": row["question_id"], "side_a": "bge" if bge_side_a else "qwen", "side_b": "qwen" if bge_side_a else "bge"})
    return (
        b"".join(canonical_json_bytes(row) + b"\n" for row in review_rows),
        b"".join(canonical_json_bytes(row) + b"\n" for row in mapping_rows),
    )


def run_bge_qwen_dense_comparison(
    output_root: Path,
    *,
    supply_root: Path = DEFAULT_SUPPLY_ROOT,
    retrieval_unit_manifest_path: Path = DEFAULT_RU_MANIFEST,
    qwen_query_root: Path = DEFAULT_QWEN_QUERY_ROOT,
    qwen_dense_manifest_path: Path = DEFAULT_QWEN_DENSE_MANIFEST,
    progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Materialize a new immutable provider-free BGE-vs-Qwen comparison."""

    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError("comparison output root already exists; refusing to overwrite or resume")
    supply_manifest, frozen = _validate_frozen_bge_supply(Path(supply_root))
    query_rows, query_vectors, query_manifest, corpus_vectors, corpus_rows, dense_manifest = _validate_qwen_inputs(
        Path(qwen_query_root), Path(qwen_dense_manifest_path)
    )
    if [row["question_id"] for row in query_rows] != list(QUESTION_IDS):
        raise BgeQwenDenseComparisonError("Qwen query-vector order is not Q001-Q070")
    if [row["question_identity"] for row in query_rows] != [frozen[qid]["record"]["question_identity"] for qid in QUESTION_IDS]:
        raise BgeQwenDenseComparisonError("Qwen query-vector identities do not match frozen BGE supply")
    config = _expected_config()
    ru_manifest = _read_object(Path(retrieval_unit_manifest_path), "accepted Retrieval Unit manifest")
    if ru_manifest.get("status") != "complete" or ru_manifest.get("build_identity") != frozen_comparison.ACCEPTED_RU_BUILD_IDENTITY:
        raise BgeQwenDenseComparisonError("accepted Retrieval Unit manifest does not match frozen supply")
    run_identity = _run_identity(supply_manifest, query_manifest, dense_manifest, config)
    output_root.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "partial",
        "run_identity": run_identity,
        "question_ids": list(QUESTION_IDS),
        "provenance": {
            "runtime_input_sha256": supply_manifest["runtime_input_sha256"],
            "frozen_bge_supply_root": str(supply_root),
            "frozen_bge_supply_run_identity": supply_manifest["run_identity"],
            "frozen_bge_baseline": supply_manifest["baseline"],
            "retrieval_unit_manifest": str(retrieval_unit_manifest_path),
            "retrieval_unit_build_identity": ru_manifest["build_identity"],
            "qwen_query_root": str(qwen_query_root),
            "qwen_query_artifact_identity": query_manifest["artifact_identity"],
            "qwen_query_vectors_sha256": query_manifest["artifacts"]["vectors"]["sha256"],
            "qwen_dense_manifest": str(qwen_dense_manifest_path),
            "qwen_dense_arm_build_identity": dense_manifest["arm_build_identity"],
            "qwen_corpus_vectors_sha256": dense_manifest["artifacts"]["vectors"]["sha256"],
            "qwen_corpus_rows_sha256": dense_manifest["artifacts"]["rows"]["sha256"],
            "rrf_k": 60,
            "top_k": 20,
            "formal_deferred_policy": DEFERRED_FOOTPRINT_CHARGE_POLICY,
            "assembly_config": _config_projection(config),
            "runner_source_sha256": _sha256_file(Path(__file__)),
        },
        "execution_counters": {
            "provider_calls": 0,
            "generation_calls": 0,
            "bge_query_encodings": 0,
            "bge_dense_reruns": 0,
            "bm25_reruns": 0,
            "frozen_bge_rrf_validations": 70,
            "qwen_dense_retrievals": 0,
            "qwen_rrf_fusions": 0,
        },
        "completed_question_ids": [],
        "completed_artifacts": {},
    }
    _write_manifest(output_root / "metadata" / "manifest.json", manifest)
    try:
        context = prepare_evidence_assembly_context(Path(retrieval_unit_manifest_path))
        if context.retrieval_unit_build_identity != frozen_comparison.ACCEPTED_RU_BUILD_IDENTITY:
            raise BgeQwenDenseComparisonError("prepared Assembly context does not match frozen supply")
        rows: list[dict[str, Any]] = []
        for index, question_id in enumerate(QUESTION_IDS):
            row = _compare_question(
                frozen[question_id], query_vectors[index], qwen_dense_manifest=dense_manifest,
                qwen_corpus_vectors=corpus_vectors, qwen_corpus_rows=corpus_rows,
                retrieval_unit_manifest_path=Path(retrieval_unit_manifest_path), prepared_context=context, config=config,
            )
            body = canonical_json_bytes(row)
            manifest["completed_artifacts"][question_id] = _immutable_write(output_root / "comparisons" / f"{question_id}.json", body)
            manifest["completed_question_ids"].append(question_id)
            manifest["execution_counters"]["qwen_dense_retrievals"] += 1
            manifest["execution_counters"]["qwen_rrf_fusions"] += 1
            _write_manifest(output_root / "metadata" / "manifest.json", manifest)
            rows.append(row)
            if progress is not None:
                progress({"event": "question_complete", "question_id": question_id, "processed": index + 1, "total": 70})
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"reason": type(exc).__name__, "message": str(exc)}
        _write_manifest(output_root / "metadata" / "manifest.json", manifest)
        if isinstance(exc, BgeQwenDenseComparisonError):
            raise
        raise BgeQwenDenseComparisonError("comparison stopped; partial artifacts remain inspectable") from exc
    aggregate = _aggregate(rows)
    review, unblinding = _blinded_records(
        rows,
        run_identity,
        {row["question_id"]: row["question"] for row in query_rows},
    )
    manifest["aggregate"] = aggregate
    manifest["final_artifacts"] = {
        "aggregate": _immutable_write(output_root / "metadata" / "aggregate.json", canonical_json_bytes(aggregate)),
        "blinded_review": _immutable_write(output_root / "review" / "blinded_packets.jsonl", review),
        "unblinding": _immutable_write(output_root / "review" / "unblinding.jsonl", unblinding),
    }
    manifest["status"] = "complete"
    _write_manifest(output_root / "metadata" / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the provider-free frozen-supply BGE-vs-Qwen Dense comparison")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--supply-root", type=Path, default=DEFAULT_SUPPLY_ROOT)
    parser.add_argument("--ru-manifest", type=Path, default=DEFAULT_RU_MANIFEST)
    parser.add_argument("--qwen-query-root", type=Path, default=DEFAULT_QWEN_QUERY_ROOT)
    parser.add_argument("--qwen-dense-manifest", type=Path, default=DEFAULT_QWEN_DENSE_MANIFEST)
    args = parser.parse_args(argv)
    result = run_bge_qwen_dense_comparison(
        args.output_root, supply_root=args.supply_root, retrieval_unit_manifest_path=args.ru_manifest,
        qwen_query_root=args.qwen_query_root, qwen_dense_manifest_path=args.qwen_dense_manifest,
        progress=lambda event: print(canonical_json_bytes(dict(event)).decode("utf-8"), flush=True),
    )
    print(canonical_json_bytes(result).decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
