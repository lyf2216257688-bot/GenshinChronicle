"""Minimal production materialization orchestration for the accepted RAG baseline.

This module owns orchestration only. W1/W2 builders, loaders, retrieval, and
Evidence Assembly remain the authoritative implementation and validation
layers. Full-corpus execution is intentionally explicit and is not performed
by import or by tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.candidate_retrieval import (
    DEFAULT_DENSE_MODEL_REVISION,
    build_dense_index,
    build_lexical_index,
    retrieve_candidates,
)
from genshin_corpus.retrieval.evidence_assembly import (
    EvidenceAssemblyConfig,
    assemble_deferred_footprint_charge_packet,
    write_evidence_packet,
)
from genshin_corpus.retrieval.profiler import profile_canonical_run
from genshin_corpus.retrieval.retrieval_units import (
    RetrievalUnitBuildConfig,
    build_retrieval_units,
    load_retrieval_units,
)


PINNED_BGE_SMALL_MODEL_SHA256 = "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026"
_QUERY_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class ProductionMaterializationError(ValueError):
    """Raised when production materialization cannot proceed safely."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionMaterializationError(f"{label} must be an object")
    return value


def _validate_query_rows(queries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, query in enumerate(queries):
        value = _mapping(query, f"query {index}")
        query_id, text = value.get("query_id"), value.get("query")
        if (
            not isinstance(query_id, str)
            or query_id in {".", ".."}
            or not _QUERY_ID.fullmatch(query_id)
            or Path(query_id).name != query_id
            or Path(query_id).is_absolute()
        ):
            raise ProductionMaterializationError(f"query {index}.query_id is not a safe stable directory name")
        if query_id in seen:
            raise ProductionMaterializationError(f"duplicate query_id: {query_id}")
        if not isinstance(text, str) or not text.strip():
            raise ProductionMaterializationError(f"query {query_id}.query must be a non-empty string")
        seen.add(query_id)
        rows.append({"query_id": query_id, "query": text})
    if not rows:
        raise ProductionMaterializationError("at least one real query is required")
    return rows


def _assert_new_output_root(output_root: Path) -> None:
    if output_root.exists():
        raise ProductionMaterializationError(
            f"production output root already exists; refusing to overwrite: {output_root}"
        )


def preflight_production_inputs(
    canonical_manifest_path: Path,
    model_dir: Path,
    *,
    expected_model_sha256: str = PINNED_BGE_SMALL_MODEL_SHA256,
    runtime_root: Path | None = None,
    require_dense_runtime: bool = True,
) -> dict[str, Any]:
    """Perform read-only Canonical and pinned local-model preflight checks."""

    canonical_manifest_path = Path(canonical_manifest_path)
    model_dir = Path(model_dir)
    try:
        profile = profile_canonical_run(canonical_manifest_path, top_n=1)
    except Exception as exc:
        raise ProductionMaterializationError("Canonical production preflight failed") from exc
    model_file = model_dir / "model.safetensors"
    try:
        actual_sha = hashlib.sha256(model_file.read_bytes()).hexdigest()
    except OSError as exc:
        raise ProductionMaterializationError("pinned local bge-small model is unavailable") from exc
    if actual_sha != expected_model_sha256:
        raise ProductionMaterializationError("pinned local bge-small model SHA-256 mismatch")
    runtime_info: dict[str, Any] = {}
    if require_dense_runtime:
        if runtime_root is None:
            raise ProductionMaterializationError("Dense runtime root is required for production preflight")
        runtime_root = Path(runtime_root)
        if not runtime_root.is_dir():
            raise ProductionMaterializationError("Dense runtime root is unavailable")
        original_path = list(sys.path)
        try:
            sys.path.insert(0, str(runtime_root))
            from sentence_transformers import SentenceTransformer
            import numpy as np

            model = SentenceTransformer(str(model_dir), local_files_only=True, device="cpu")
            probe = np.asarray(
                model.encode(["生产 Dense runtime preflight"], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False),
                dtype=np.float32,
            )
            if probe.ndim != 2 or probe.shape[0] != 1 or probe.shape[1] != 512 or not np.isfinite(probe).all():
                raise ProductionMaterializationError("Dense runtime returned an invalid bge-small vector")
            runtime_info = {"runtime_root": str(runtime_root), "embedding_dimension": int(probe.shape[1]), "device": "cpu", "local_files_only": True}
        except ProductionMaterializationError:
            raise
        except Exception as exc:
            raise ProductionMaterializationError("accepted local Dense runtime/model unavailable") from exc
        finally:
            sys.path[:] = original_path
    return {
        "canonical_manifest_path": str(canonical_manifest_path),
        "canonical_run_id": profile["observation_scope"].get("canonical_run_id"),
        "canonical_status": profile["observation_scope"].get("manifest_status"),
        "canonical_record_count": profile["observation_scope"].get("manifest_accounted_record_count"),
        "model_dir": str(model_dir),
        "model_revision": DEFAULT_DENSE_MODEL_REVISION,
        "model_sha256": actual_sha,
        **runtime_info,
    }


def _materialize(
    canonical_manifest_path: Path,
    output_root: Path,
    *,
    model_dir: Path,
    queries: Sequence[Mapping[str, Any]],
    ru_config: RetrievalUnitBuildConfig | None = None,
    assembly_config: EvidenceAssemblyConfig | None = None,
    top_k: int = 20,
    rrf_k: int = 60,
    expected_model_sha256: str = PINNED_BGE_SMALL_MODEL_SHA256,
    dense_vectors_for_test: Any | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Build one fresh production-shaped artifact tree and real query packets.

    ``dense_vectors_for_test`` is an internal fixture-only escape hatch.
    """

    canonical_manifest_path = Path(canonical_manifest_path)
    output_root = Path(output_root)
    model_dir = Path(model_dir)
    query_rows = _validate_query_rows(queries)
    _assert_new_output_root(output_root)
    preflight = preflight_production_inputs(
        canonical_manifest_path,
        model_dir,
        expected_model_sha256=expected_model_sha256,
        runtime_root=runtime_root,
        require_dense_runtime=dense_vectors_for_test is None,
    )

    ru_root = output_root / "ru"
    lexical_root = output_root / "lexical"
    dense_root = output_root / "dense"
    packet_root = output_root / "packets"

    ru_manifest = build_retrieval_units(canonical_manifest_path, ru_root, config=ru_config)
    ru_manifest_path = ru_root / "metadata" / "manifest.json"
    loaded_ru_manifest, units = load_retrieval_units(ru_manifest_path)
    if loaded_ru_manifest.get("build_identity") != ru_manifest.get("build_identity"):
        raise ProductionMaterializationError("W1 manifest changed during reload")

    lexical_manifest = build_lexical_index(ru_manifest_path, lexical_root)
    dense_manifest = build_dense_index(
        ru_manifest_path,
        dense_root,
        model_dir=model_dir,
        vectors=dense_vectors_for_test,
    )
    if lexical_manifest.get("retrieval_unit_build_identity") != ru_manifest["build_identity"]:
        raise ProductionMaterializationError("lexical artifact is not bound to the W1 RU build")
    if dense_manifest.get("retrieval_unit_build_identity") != ru_manifest["build_identity"]:
        raise ProductionMaterializationError("Dense artifact is not bound to the W1 RU build")

    packets: list[dict[str, Any]] = []
    for query in query_rows:
        query_id, text = query["query_id"], query["query"]
        for mode in ("lexical", "dense", "hybrid"):
            candidates = retrieve_candidates(
                mode,
                lexical_manifest_path=lexical_root / "metadata" / "manifest.json",
                dense_manifest_path=dense_root / "metadata" / "manifest.json",
                model_dir=model_dir,
                query=text,
                top_k=top_k,
                rrf_k=rrf_k,
            )
            packet = assemble_deferred_footprint_charge_packet(
                ru_manifest_path,
                candidates,
                config=assembly_config,
                retrieval_audit={
                    "query_id": query_id,
                    "query_text": text,
                    "mode": mode,
                    "lexical_build_identity": lexical_manifest.get("arm_build_identity"),
                    "dense_build_identity": dense_manifest.get("arm_build_identity"),
                },
            )
            packet_path = packet_root / query_id / mode
            artifacts = write_evidence_packet(packet_path, packet)
            packets.append({"query_id": query_id, "mode": mode, "packet": artifacts})

    return {
        "status": "complete",
        "preflight": preflight,
        "output_root": str(output_root),
        "ru": {"manifest_path": str(ru_manifest_path), "build_identity": ru_manifest["build_identity"], "row_count": len(units)},
        "lexical": {"manifest_path": str(lexical_root / "metadata" / "manifest.json"), "build_identity": lexical_manifest["arm_build_identity"]},
        "dense": {"manifest_path": str(dense_root / "metadata" / "manifest.json"), "build_identity": dense_manifest["arm_build_identity"]},
        "packets": packets,
    }


def materialize_production(
    canonical_manifest_path: Path,
    output_root: Path,
    *,
    model_dir: Path,
    queries: Sequence[Mapping[str, Any]],
    ru_config: RetrievalUnitBuildConfig | None = None,
    assembly_config: EvidenceAssemblyConfig | None = None,
    top_k: int = 20,
    rrf_k: int = 60,
    expected_model_sha256: str = PINNED_BGE_SMALL_MODEL_SHA256,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Production surface; always builds Dense vectors from the local model."""

    return _materialize(
        canonical_manifest_path,
        output_root,
        model_dir=model_dir,
        queries=queries,
        ru_config=ru_config,
        assembly_config=assembly_config,
        top_k=top_k,
        rrf_k=rrf_k,
        expected_model_sha256=expected_model_sha256,
        runtime_root=runtime_root or Path(".local/w6-runtime"),
    )


def _materialize_fixture(
    canonical_manifest_path: Path,
    output_root: Path,
    *,
    model_dir: Path,
    queries: Sequence[Mapping[str, Any]],
    dense_vectors: Any,
    ru_config: RetrievalUnitBuildConfig | None = None,
    assembly_config: EvidenceAssemblyConfig | None = None,
    top_k: int = 20,
    rrf_k: int = 60,
    expected_model_sha256: str = PINNED_BGE_SMALL_MODEL_SHA256,
) -> dict[str, Any]:
    """Fixture-only helper, intentionally not exported or exposed by CLI."""

    return _materialize(
        canonical_manifest_path,
        output_root,
        model_dir=model_dir,
        queries=queries,
        ru_config=ru_config,
        assembly_config=assembly_config,
        top_k=top_k,
        rrf_k=rrf_k,
        expected_model_sha256=expected_model_sha256,
        dense_vectors_for_test=dense_vectors,
    )


def _parse_query(value: str) -> dict[str, str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("--query must be a JSON object") from exc
    if not isinstance(parsed, Mapping):
        raise argparse.ArgumentTypeError("--query must be a JSON object")
    return dict(parsed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize the accepted W1+W2 production RAG baseline")
    parser.add_argument("--canonical-manifest", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--runtime-root", type=Path, default=Path(".local/w6-runtime"))
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--query", action="append", required=True, type=_parse_query, help="JSON object with query_id and query; repeatable")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--rrf-k", type=int, default=60)
    args = parser.parse_args()
    result = materialize_production(
        args.canonical_manifest,
        args.output_root,
        model_dir=args.model_dir,
        queries=args.query,
        top_k=args.top_k,
        rrf_k=args.rrf_k,
        runtime_root=args.runtime_root,
    )
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
