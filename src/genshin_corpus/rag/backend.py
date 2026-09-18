"""Small reusable orchestration boundary for one arbitrary RAG question.

Retrieval, Assembly, reranking, Generation, and citation validation remain
owned by their existing packages. This module only composes those contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import math
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import (
    GenerationProvider,
    GenerationResult,
    project_generation_request,
    validate_citations,
)
from genshin_corpus.retrieval.candidate_retrieval import (
    DEFAULT_QWEN_DENSE_MANIFEST,
    BatchCandidateRetriever,
    load_batch_candidate_retriever,
)
from genshin_corpus.retrieval.evidence_assembly import (
    EvidenceAssemblyConfig,
    EvidenceAssemblyDiagnostics,
    PreparedAssemblyContext,
    assemble_deferred_footprint_charge_packet,
    write_evidence_packet,
    prepare_evidence_assembly_context,
)
import genshin_corpus.retrieval.evidence_assembly as _evidence_assembly
from genshin_corpus.retrieval.qwen_embedding import (
    QwenEmbeddingPreflightError,
    QwenEmbeddingTransport,
    encode_qwen_query,
)
from genshin_corpus.retrieval.qwen_m2_query_vectors import (
    ACCEPTED_QWEN_M2_QUERY_ARTIFACT_IDENTITY,
    ACCEPTED_QWEN_M2_QUERY_VECTORS_SHA256,
    AcceptedQwenQueryVector,
)
from genshin_corpus.retrieval.reranking import (
    RankFusionConfig,
    RerankCandidate,
    RerankRequest,
    Reranker,
    fuse_ranked_candidates,
    project_reranker_text,
    project_ranked_candidates,
    stable_rank_scores,
)


RESULT_SCHEMA_VERSION = "phase04-single-question-rag-result-0.1"
_SAFE_RUN_IDENTITY = re.compile(r"^[A-Za-z0-9._-]+$")


class RagBackendPreparationError(ValueError):
    """Raised when reusable corpus state cannot be prepared safely."""


@dataclass(frozen=True)
class SingleQuestionBackendConfig:
    """Explicit operating controls for one backend invocation."""

    candidate_depth: int = 500
    rerank_output_k: int = 20
    rrf_k: int = 60
    reranker_enabled: bool = True
    assembly_config: EvidenceAssemblyConfig = field(default_factory=EvidenceAssemblyConfig)
    candidate_supply_depth: int | None = None
    rerank_depth: int | None = None
    final_top_n: int | None = None
    reranker_projection_max_chars: int = 6000
    fusion_config: RankFusionConfig = field(default_factory=RankFusionConfig)
    final_top_n_explicit: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        explicit_final_top_n = self.final_top_n is not None
        explicit_supply_depth = self.candidate_supply_depth is not None or self.candidate_depth != 500
        if self.candidate_supply_depth is None:
            supply = self.candidate_depth
        else:
            if self.candidate_depth != 500 and self.candidate_depth != self.candidate_supply_depth:
                raise ValueError("candidate_depth and candidate_supply_depth disagree")
            supply = self.candidate_supply_depth
        object.__setattr__(self, "candidate_supply_depth", supply)
        object.__setattr__(self, "candidate_depth", supply)
        if self.final_top_n is None:
            # A default OFF control keeps production Retrieval supply while
            # sending only Hybrid Top20 to Assembly. Explicit depth-only
            # diagnostics retain their historical full-window behavior.
            final_top_n = (
                self.rerank_output_k
                if self.reranker_enabled or not explicit_supply_depth
                else supply
            )
        else:
            if self.rerank_output_k != 20 and self.rerank_output_k != self.final_top_n:
                raise ValueError("rerank_output_k and final_top_n disagree")
            final_top_n = self.final_top_n
        object.__setattr__(self, "final_top_n", final_top_n)
        object.__setattr__(self, "final_top_n_explicit", explicit_final_top_n)
        rerank_depth = supply if self.rerank_depth is None else self.rerank_depth
        object.__setattr__(self, "rerank_depth", rerank_depth)
        for name in ("candidate_supply_depth", "rerank_depth", "final_top_n", "rrf_k", "reranker_projection_max_chars"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.reranker_enabled, bool):
            raise ValueError("reranker_enabled must be a boolean")
        if self.rerank_depth > self.candidate_supply_depth:
            raise ValueError("rerank_depth cannot exceed candidate_supply_depth")
        if self.final_top_n > (self.rerank_depth if self.reranker_enabled else self.candidate_supply_depth):
            raise ValueError("final_top_n exceeds the available candidate window")
        if not isinstance(self.fusion_config, RankFusionConfig):
            raise ValueError("fusion_config must be a RankFusionConfig")

    def audit_projection(self) -> dict[str, Any]:
        return {
            "candidate_depth": self.candidate_supply_depth,
            "rerank_output_k": self.final_top_n if self.reranker_enabled else None,
            "candidate_supply_depth": self.candidate_supply_depth,
            "rerank_depth": self.rerank_depth if self.reranker_enabled else None,
            "final_top_n": self.final_top_n,
            "final_top_n_explicit": self.final_top_n_explicit,
            "rrf_k": self.rrf_k,
            "reranker_enabled": self.reranker_enabled,
            "reranker_projection_max_chars": self.reranker_projection_max_chars,
            "fusion": self.fusion_config.to_dict(),
            "assembly_config": self.assembly_config.to_dict(),
        }


@dataclass(frozen=True)
class PreparedRagState:
    """Caller-owned heavy state reusable across multiple questions."""

    retrieval_unit_manifest_path: Path
    lexical_manifest_path: Path
    dense_manifest_path: Path
    retriever: BatchCandidateRetriever
    assembly_context: PreparedAssemblyContext
    retrieval_unit_build_identity: str
    lexical_build_identity: str
    dense_build_identity: str
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Release the accepted Dense mmap owned by this prepared state.

        The retriever is caller-owned and has no generic lifecycle API.  The
        backend therefore closes only the mmap handle it loaded through this
        state, when the concrete NumPy memmap exposes one.  Non-mmap test or
        alternate retriever objects remain unaffected.
        """

        if self._closed:
            return
        # Mark closed before touching the handle so a close error cannot leave
        # a state that callers may safely reuse.
        object.__setattr__(self, "_closed", True)
        _release_dense_mmap(self.retriever)


def _release_dense_mmap(retriever: Any) -> None:
    """Release only the concrete Dense mmap owned by this backend state."""

    vectors = getattr(retriever, "dense_vectors", None)
    handle = getattr(vectors, "_mmap", None)
    if handle is not None and callable(getattr(handle, "close", None)):
        if not bool(getattr(handle, "closed", False)):
            handle.close()


def prepare_rag_state(
    *,
    retrieval_unit_manifest_path: Path,
    lexical_manifest_path: Path,
    dense_manifest_path: Path = DEFAULT_QWEN_DENSE_MANIFEST,
) -> PreparedRagState:
    """Validate and load heavy accepted state once for a caller's query batch."""

    retriever: BatchCandidateRetriever | None = None
    try:
        retriever = load_batch_candidate_retriever(
            Path(lexical_manifest_path),
            Path(dense_manifest_path),
            accepted_qwen=True,
        )
        assembly_context = prepare_evidence_assembly_context(Path(retrieval_unit_manifest_path))
        ru_identity = assembly_context.retrieval_unit_build_identity
        lexical_identity = str(retriever.lexical_manifest.get("arm_build_identity"))
        dense_identity = str(retriever.dense_manifest.get("arm_build_identity"))
        if retriever.lexical_manifest.get("retrieval_unit_build_identity") != ru_identity:
            raise RagBackendPreparationError("lexical artifact is not bound to the prepared RU snapshot")
        if retriever.dense_manifest.get("retrieval_unit_build_identity") != ru_identity:
            raise RagBackendPreparationError("Dense artifact is not bound to the prepared RU snapshot")
        if lexical_identity == "None" or dense_identity == "None":
            raise RagBackendPreparationError("prepared retrieval artifacts lack build identities")
        return PreparedRagState(
            retrieval_unit_manifest_path=Path(retrieval_unit_manifest_path).resolve(),
            lexical_manifest_path=Path(lexical_manifest_path).resolve(),
            dense_manifest_path=Path(dense_manifest_path).resolve(),
            retriever=retriever,
            assembly_context=assembly_context,
            retrieval_unit_build_identity=ru_identity,
            lexical_build_identity=lexical_identity,
            dense_build_identity=dense_identity,
        )
    except RagBackendPreparationError:
        if retriever is not None:
            try:
                _release_dense_mmap(retriever)
            except Exception:
                pass
        raise
    except Exception as exc:
        if retriever is not None:
            try:
                _release_dense_mmap(retriever)
            except Exception:
                pass
        raise RagBackendPreparationError("RAG prepared state could not be validated") from exc


def _error(stage: str, exc: BaseException, *, category: str | None = None) -> dict[str, Any]:
    return {
        "stage": stage,
        "category": category or type(exc).__name__,
        "code": type(exc).__name__,
        "message": str(exc),
        "retryable": False,
    }


def _reranker_fields_from_verified_ru(context: PreparedAssemblyContext, unit_ids: list[str]) -> dict[str, dict[str, str]]:
    """Project ranking-only fields from the verified prepared RU snapshot."""

    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("reranker projection requires unique unit IDs")
    state = _evidence_assembly._prepared_state(context)
    wanted = set(unit_ids)
    result: dict[str, dict[str, str]] = {}
    for unit in state.units_by_id.values():
        unit_id = unit.get("unit_id")
        if unit_id not in wanted:
            continue
        source = unit.get("source")
        context = source.get("record_context") if isinstance(source, Mapping) else None
        structure = unit.get("structure")
        dialogue = structure.get("dialogue") if isinstance(structure, Mapping) else None
        def _text(value: Any) -> str:
            return value.strip() if isinstance(value, str) else ""
        result[str(unit_id)] = {
            "record_title": _text(context.get("record_title") if isinstance(context, Mapping) else None),
            "section_name": _text(context.get("section_name") if isinstance(context, Mapping) else None),
            "speaker": _text(dialogue.get("speaker") if isinstance(dialogue, Mapping) else None),
            "retrieval_visible_text": _text(unit.get("retrieval_visible_text")),
        }
    if set(result) != wanted or any(not row["retrieval_visible_text"] for row in result.values()):
        raise ValueError("reranker projection candidate is absent or lacks retrieval_visible_text")
    return {unit_id: result[unit_id] for unit_id in unit_ids}


def _safe_reranker_provider_metadata(reranker: Any) -> dict[str, Any] | None:
    """Retain only non-sensitive adapter metadata in the execution audit."""

    value = getattr(reranker, "last_response_metadata", None)
    if not isinstance(value, Mapping):
        return None
    safe: dict[str, Any] = {}
    for key in ("request_id", "model", "status_code"):
        if isinstance(value.get(key), (str, int)) and not isinstance(value.get(key), bool):
            safe[key] = value[key]
    usage = value.get("usage")
    if isinstance(usage, Mapping):
        allowed_usage = {"total_tokens", "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"}
        safe["usage"] = {
            key: item
            for key, item in usage.items()
            if key in allowed_usage
            and isinstance(item, (int, float))
            and not isinstance(item, bool)
            and item >= 0
            and (not isinstance(item, float) or math.isfinite(item))
        }
        if not safe["usage"]:
            safe.pop("usage")
    return safe or None


def _safe_reranker_runtime_identity(reranker: Any) -> dict[str, Any] | None:
    """Return the adapter's declared stable runtime identity, if available.

    Object identity and process-local paths are deliberately never synthesized
    here.  Adaptive cross-round binding must fail closed when an enabled
    reranker cannot declare its implementation/configuration identity.
    """

    method = getattr(reranker, "runtime_identity", None)
    if not callable(method):
        return None
    try:
        value = method()
        if not isinstance(value, Mapping):
            return None
        canonical_json_bytes(value)
    except Exception:
        return None
    return dict(value)


def _persist(
    output_root: Path,
    execution_identity: str,
    result: dict[str, Any],
    packet: Mapping[str, Any] | None,
    generation_result: GenerationResult | None,
) -> dict[str, Any]:
    base = Path(output_root).expanduser()
    if not _SAFE_RUN_IDENTITY.fullmatch(execution_identity):
        raise ValueError("execution_identity is not a safe run directory name")
    if base.exists() and not base.is_dir():
        raise NotADirectoryError(f"output root is not a directory: {base}")
    base.mkdir(parents=True, exist_ok=True)
    root = base / execution_identity
    if root.exists():
        raise FileExistsError(f"refusing to overwrite existing RAG run: {root}")
    root.mkdir()
    persistence: dict[str, Any] = {}
    if packet is not None:
        persistence["evidence_packet"] = write_evidence_packet(root / "packet", packet)
    if generation_result is not None:
        from genshin_corpus.generation.generation import write_generation_result

        persistence["generation"] = write_generation_result(root / "generation", generation_result)
    persisted = dict(result)
    persisted["persistence"] = {
        "base_output_root": str(base.resolve()),
        "run_root": str(root.resolve()),
        **persistence,
    }
    # The result descriptor is intentionally path-only: hashing a document
    # that embeds its own hash would be self-referential. Artifact writers
    # already return content hashes for the Packet and Generation files.
    persisted["persistence"]["result"] = {"path": "rag_result.json"}
    result_body = canonical_json_bytes(persisted)
    atomic_write(root / "rag_result.json", result_body)
    return persisted


def run_single_question(
    prepared_state: PreparedRagState,
    question_text: str,
    *,
    embedding_transport: QwenEmbeddingTransport | None = None,
    precomputed_query: AcceptedQwenQueryVector | None = None,
    generation_provider: GenerationProvider | None = None,
    config: SingleQuestionBackendConfig | None = None,
    reranker: Reranker | None = None,
    execution_mode: str = "generate_answer",
    request_label: str | None = None,
    execution_identity: str | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """Run one arbitrary question using caller-prepared heavy state."""

    config = config or SingleQuestionBackendConfig()
    identity = str(uuid4()) if execution_identity is None else execution_identity
    if not isinstance(identity, str) or not _SAFE_RUN_IDENTITY.fullmatch(identity):
        raise ValueError("execution_identity is not a safe execution identity")
    timings: dict[str, float] = {}
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "failed",
        "execution_mode": execution_mode,
        "query": {
            "question_text": question_text,
            "request_label": request_label,
            "execution_identity": identity,
            "execution_mode": execution_mode,
        },
        "audit": {
            "retrieval_unit_build_identity": prepared_state.retrieval_unit_build_identity,
            "lexical_build_identity": prepared_state.lexical_build_identity,
            "dense_build_identity": prepared_state.dense_build_identity,
            "config": config.audit_projection(),
        },
        "timing_seconds": timings,
        "telemetry": {
            "schema_version": "phase04-rag-runtime-telemetry-0.1",
            "stage_seconds": timings,
            "counts": {
                "candidate_supply": None,
                "rerank": 0,
                "final": None,
                "evidence_blocks": None,
            },
            "provider_call_counts": {"embedding": 0, "reranker": 0, "generation": 0},
            "reranker_projection": None,
            "reranker_usage": None,
            "generation_usage": None,
        },
        "error": None,
        "final_answer": None,
        "citations": [],
        "citation_validation": None,
        "evidence_packet": None,
        "retrieval_trace": None,
        "rerank_trace": {
            "status": "disabled_by_explicit_config",
            "candidate_depth": config.candidate_supply_depth,
            "candidate_supply_depth": config.candidate_supply_depth,
            "rerank_depth": None,
            "final_top_n": config.final_top_n,
            "rerank_output_k": None,
        } if not config.reranker_enabled else None,
        "generation": {"status": "skipped", "reason": "evidence_only"}
        if execution_mode == "evidence_only"
        else {"status": "not_started"},
    }
    packet: Mapping[str, Any] | None = None
    generation_result: GenerationResult | None = None
    generation_request: Any | None = None

    if prepared_state.closed:
        result["error"] = _error("lifecycle", RuntimeError("PreparedRagState is closed"), category="state_closed")
        return result

    current_stage = "input"
    try:
        if execution_mode not in {"evidence_only", "generate_answer"}:
            raise ValueError("execution_mode must be evidence_only or generate_answer")
        if not isinstance(question_text, str) or not question_text.strip():
            raise ValueError("question_text must be a non-empty string")
        if request_label is not None and (not isinstance(request_label, str) or not request_label.strip()):
            raise ValueError("request_label must be a non-empty string when provided")
        if precomputed_query is None and not callable(getattr(embedding_transport, "embed", None)):
            raise QwenEmbeddingPreflightError("embedding transport lacks embed")
        if precomputed_query is not None and embedding_transport is not None:
            raise QwenEmbeddingPreflightError("precomputed query cannot be combined with an embedding transport")
        if execution_mode == "generate_answer" and not callable(getattr(generation_provider, "generate", None)):
            raise TypeError("generation provider lacks generate")

        current_stage = "embedding"
        started = perf_counter()
        if precomputed_query is None:
            result["telemetry"]["provider_call_counts"]["embedding"] = 1
            query_vector, query_request, query_response = encode_qwen_query(embedding_transport, question_text)
            query_request_identity = query_request.request_identity
            result["audit"]["embedding"] = {
                "source": "provider",
                "request_identity": query_request_identity,
                "returned_model": query_response.returned_model,
                "returned_role": query_response.returned_role,
                "provider_request_id": query_response.provider_request_id,
            }
        else:
            if not isinstance(precomputed_query, AcceptedQwenQueryVector):
                raise QwenEmbeddingPreflightError("precomputed Qwen query binding is invalid")
            expected_identity = sha256_json({
                "question_id": precomputed_query.question_id,
                "question": question_text,
            })
            if (
                precomputed_query.question != question_text
                or precomputed_query.question_identity != expected_identity
                or precomputed_query.artifact_identity != ACCEPTED_QWEN_M2_QUERY_ARTIFACT_IDENTITY
                or precomputed_query.vectors_sha256 != ACCEPTED_QWEN_M2_QUERY_VECTORS_SHA256
                or not isinstance(precomputed_query.request_identity, str)
                or not precomputed_query.request_identity
                or precomputed_query.configuration != {
                    "schema_version": "phase04-rag-qwen37-m2-query-vectors-0.1",
                    "model_id": "qwen3.7-text-embedding",
                    "dimension": 2048,
                    "output": "dense",
                    "role": "query",
                    "custom_query_instruction": None,
                    "batch_size": 20,
                }
            ):
                raise QwenEmbeddingPreflightError("precomputed Qwen query binding is invalid")
            try:
                import numpy as np

                query_vector = np.asarray(precomputed_query.vector, dtype=np.float32)
                if query_vector.ndim != 1 or query_vector.shape[0] != 2048 or not np.isfinite(query_vector).all() or float(np.linalg.norm(query_vector)) == 0:
                    raise ValueError
            except Exception as exc:
                raise QwenEmbeddingPreflightError("precomputed Qwen query vector is invalid") from exc
            query_request_identity = precomputed_query.request_identity
            result["audit"]["embedding"] = {
                "source": "accepted_precomputed_query_vector",
                "request_identity": query_request_identity,
                "question_id": precomputed_query.question_id,
                "question_identity": precomputed_query.question_identity,
                "artifact_identity": precomputed_query.artifact_identity,
                "vectors_sha256": precomputed_query.vectors_sha256,
                "manifest_sha256": precomputed_query.manifest_sha256,
                "row_index": precomputed_query.row_index,
                "configuration": dict(precomputed_query.configuration),
                "provider_request_id": None,
            }
        timings["embedding"] = perf_counter() - started

        current_stage = "retrieval"
        started = perf_counter()
        retrieval_stage_timings: dict[str, float] = {}
        retrieval_method = prepared_state.retriever.candidates_for_query
        retrieval_kwargs: dict[str, Any] = {
            "instruction": None,
            "top_k": config.candidate_supply_depth,
            "rrf_k": config.rrf_k,
        }
        parameters = inspect.signature(retrieval_method).parameters
        if "candidate_supply_depth" in parameters:
            retrieval_kwargs["candidate_supply_depth"] = config.candidate_supply_depth
        if "telemetry" in parameters:
            retrieval_kwargs["telemetry"] = retrieval_stage_timings
        candidates = retrieval_method(question_text, query_vector, **retrieval_kwargs)
        timings["retrieval"] = perf_counter() - started
        timings["lexical"] = retrieval_stage_timings.get("lexical")
        timings["dense"] = retrieval_stage_timings.get("dense")
        timings["rrf"] = retrieval_stage_timings.get("fusion")
        result["retrieval_trace"] = {
            "candidate_depth": config.candidate_supply_depth,
            "candidate_supply_depth": config.candidate_supply_depth,
            "rrf_k": config.rrf_k,
            "windows": candidates,
        }
        result["telemetry"]["counts"]["candidate_supply"] = len(candidates["hybrid"])

        assembly_candidates = candidates["hybrid"][: config.final_top_n]
        if config.reranker_enabled:
            current_stage = "rerank"
            if reranker is None:
                raise ValueError("reranker is required when reranker_enabled is true")
            rerank_candidates = candidates["hybrid"][: config.rerank_depth]
            candidate_ids = [str(row["unit_id"]) for row in rerank_candidates]
            fields = _reranker_fields_from_verified_ru(
                prepared_state.assembly_context,
                candidate_ids,
            )
            projected: dict[str, dict[str, Any]] = {}
            rerank_rows: list[RerankCandidate] = []
            for unit_id, row in zip(candidate_ids, rerank_candidates):
                text, projection = project_reranker_text(
                    fields[unit_id],
                    max_chars=config.reranker_projection_max_chars,
                )
                projected[unit_id] = projection
                rerank_rows.append(RerankCandidate(unit_id, text, int(row["rank"])))
            request = RerankRequest(
                query_request_identity,
                question_text,
                tuple(rerank_rows),
            )
            result["telemetry"]["provider_call_counts"]["reranker"] = 1
            started = perf_counter()
            scores = stable_rank_scores(request, reranker.rerank(request))
            timings["rerank"] = perf_counter() - started
            ranked = project_ranked_candidates(rerank_candidates, scores)
            fusion_started = perf_counter()
            fused = fuse_ranked_candidates(rerank_candidates, ranked, config=config.fusion_config)
            timings["rank_fusion"] = perf_counter() - fusion_started
            assembly_candidates = fused[: config.final_top_n]
            result["rerank_trace"] = {
                "status": "executed_successfully",
                "candidate_depth": config.candidate_supply_depth,
                "candidate_supply_depth": config.candidate_supply_depth,
                "rerank_depth": config.rerank_depth,
                "final_top_n": config.final_top_n,
                "rerank_output_k": config.final_top_n,
                "request_identity": request.question_id,
                "before": list(candidates["hybrid"]),
                "reranked": ranked,
                "after": fused,
                "not_reranked_unit_ids": [str(row["unit_id"]) for row in candidates["hybrid"][config.rerank_depth:]],
                "fusion": config.fusion_config.to_dict(),
                "provider": _safe_reranker_provider_metadata(reranker),
                "runtime_identity": _safe_reranker_runtime_identity(reranker),
            }
            result["telemetry"]["counts"]["rerank"] = len(rerank_candidates)
            result["telemetry"]["reranker_projection"] = {
                "version": projected[candidate_ids[0]]["version"],
                "identity": projected[candidate_ids[0]]["identity"],
                "max_chars": config.reranker_projection_max_chars,
                "candidate_count": len(projected),
                "source_char_count": sum(item["source_char_count"] for item in projected.values()),
                "projected_char_count": sum(item["projected_char_count"] for item in projected.values()),
                "token_estimate": None,
                "truncated_count": sum(1 for item in projected.values() if item["truncated"]),
                "per_candidate": projected,
            }
            provider_metadata = result["rerank_trace"]["provider"]
            if isinstance(provider_metadata, Mapping):
                usage = provider_metadata.get("usage")
                result["telemetry"]["reranker_usage"] = dict(usage) if isinstance(usage, Mapping) else None

        current_stage = "assembly"
        diagnostics = EvidenceAssemblyDiagnostics()
        started = perf_counter()
        packet = assemble_deferred_footprint_charge_packet(
            prepared_state.retrieval_unit_manifest_path,
            assembly_candidates,
            config=config.assembly_config,
            prepared_context=prepared_state.assembly_context,
            diagnostics=diagnostics,
            retrieval_audit={
                "query_text": question_text,
                "mode": "hybrid_rerank_fusion" if config.reranker_enabled else "hybrid",
                "candidate_depth": config.candidate_supply_depth,
                "candidate_supply_depth": config.candidate_supply_depth,
                "reranker_stage": result["rerank_trace"]["status"],
                "rerank_depth": config.rerank_depth if config.reranker_enabled else None,
                "final_top_n": config.final_top_n,
                "rerank_output_k": config.final_top_n if config.reranker_enabled else None,
                "fusion_config_identity": config.fusion_config.identity if config.reranker_enabled else None,
            },
        )
        timings["assembly"] = perf_counter() - started
        result["evidence_packet"] = packet
        result["assembly_diagnostics"] = diagnostics.to_dict()
        result["telemetry"]["counts"]["final"] = len(assembly_candidates)
        result["telemetry"]["counts"]["evidence_blocks"] = packet.get("budget", {}).get("used_evidence_blocks") if isinstance(packet.get("budget"), Mapping) else None
        result["telemetry"]["evidence_packet"] = {
            "evidence_blocks": packet.get("budget", {}).get("used_evidence_blocks"),
            "used_context_chars": packet.get("budget", {}).get("used_context_chars"),
            "omitted_blocks": packet.get("budget", {}).get("omitted_blocks"),
        }

        if execution_mode == "evidence_only":
            result["status"] = "succeeded"
            result["citation_validation"] = {
                "status": "not_applicable",
                "citation_tokens": [],
                "citation_integrity": "not_applicable",
                "citation_coverage": "not_applicable",
                "validation_reasons": [],
                "semantic_faithfulness": "not_evaluated",
            }
            result["generation"] = {"status": "skipped", "reason": "evidence_only"}
        else:
            current_stage = "generation"
            generation_request = project_generation_request(packet, question=question_text)
            result["telemetry"]["provider_call_counts"]["generation"] = 1
            started = perf_counter()
            generation_result = generation_provider.generate(generation_request)
            timings["generation"] = perf_counter() - started
            generation_body = generation_result.to_dict()
            generation_audit = generation_body.get("audit", {}) if isinstance(generation_body, Mapping) else {}
            provider_execution = generation_audit.get("provider_execution", {}) if isinstance(generation_audit, Mapping) else {}
            attempts = provider_execution.get("attempts", []) if isinstance(provider_execution, Mapping) else []
            if isinstance(attempts, list):
                usages = [item.get("usage") for item in attempts if isinstance(item, Mapping) and isinstance(item.get("usage"), Mapping)]
                if usages:
                    result["telemetry"]["generation_usage"] = dict(usages[-1])
            result["generation"] = {
                "status": "executed",
                "execution_status": generation_result.execution_status,
                "result": generation_body,
            }
            if generation_result.execution_status != "succeeded":
                result["generation"]["status"] = "failed"
                result["error"] = {"stage": "generation", "category": generation_result.execution_status, "retryable": False}
            else:
                current_stage = "citation_validation"
                # The provider-reported field is retained in GenerationResult
                # for its existing audit contract, but local validation is the
                # authoritative backend gate and cannot be bypassed by mocks.
                validation = validate_citations(generation_result.answer_text, generation_request).to_dict()
                result["citation_validation"] = validation
                result["citations"] = validation["citation_tokens"]
                if validation["citation_integrity"] != "pass" or validation["citation_coverage"] not in {"pass", "not_applicable"}:
                    result["error"] = {"stage": "citation_validation", "category": "failed", "retryable": False}
                else:
                    result["status"] = "succeeded"
                    result["final_answer"] = generation_result.answer_text
    except Exception as exc:
        result["error"] = _error(current_stage, exc)
        if config.reranker_enabled and result["rerank_trace"] is None:
            result["rerank_trace"] = {"status": "failed", "error": result["error"]}
        if current_stage == "generation":
            result["generation"] = {"status": "failed", "error": result["error"]}
    result["timing_seconds"] = dict(timings)
    result["telemetry"]["stage_seconds"] = dict(timings)
    if output_root is not None:
        try:
            result = _persist(Path(output_root), identity, result, packet, generation_result)
        except Exception as exc:
            result["status"] = "failed"
            result["final_answer"] = None
            result["persistence"] = {
                "requested": True,
                "base_output_root": str(Path(output_root).expanduser().resolve()),
            }
            result["error"] = _error("persistence", exc)
    return result
