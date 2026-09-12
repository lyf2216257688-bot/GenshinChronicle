"""Small reusable orchestration boundary for one arbitrary RAG question.

Retrieval, Assembly, reranking, Generation, and citation validation remain
owned by their existing packages. This module only composes those contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
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
    retrieval_unit_texts,
)
from genshin_corpus.retrieval.qwen_embedding import (
    QwenEmbeddingPreflightError,
    QwenEmbeddingTransport,
    encode_qwen_query,
)
from genshin_corpus.retrieval.reranking import (
    RerankCandidate,
    RerankRequest,
    Reranker,
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

    candidate_depth: int = 20
    rerank_output_k: int = 20
    rrf_k: int = 60
    reranker_enabled: bool = False
    assembly_config: EvidenceAssemblyConfig = field(default_factory=EvidenceAssemblyConfig)

    def __post_init__(self) -> None:
        for name in ("candidate_depth", "rerank_output_k", "rrf_k"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.reranker_enabled, bool):
            raise ValueError("reranker_enabled must be a boolean")
        if self.reranker_enabled and self.rerank_output_k > self.candidate_depth:
            raise ValueError("rerank_output_k cannot exceed candidate_depth")

    def audit_projection(self) -> dict[str, Any]:
        return {
            "candidate_depth": self.candidate_depth,
            "rerank_output_k": self.rerank_output_k if self.reranker_enabled else None,
            "rrf_k": self.rrf_k,
            "reranker_enabled": self.reranker_enabled,
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
    embedding_transport: QwenEmbeddingTransport,
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
        "error": None,
        "final_answer": None,
        "citations": [],
        "citation_validation": None,
        "evidence_packet": None,
        "retrieval_trace": None,
        "rerank_trace": {
            "status": "disabled_by_explicit_config",
            "candidate_depth": config.candidate_depth,
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
        if not callable(getattr(embedding_transport, "embed", None)):
            raise QwenEmbeddingPreflightError("embedding transport lacks embed")
        if execution_mode == "generate_answer" and not callable(getattr(generation_provider, "generate", None)):
            raise TypeError("generation provider lacks generate")

        current_stage = "embedding"
        started = perf_counter()
        query_vector, query_request, query_response = encode_qwen_query(embedding_transport, question_text)
        timings["embedding"] = perf_counter() - started
        result["audit"]["embedding"] = {
            "request_identity": query_request.request_identity,
            "returned_model": query_response.returned_model,
            "returned_role": query_response.returned_role,
            "provider_request_id": query_response.provider_request_id,
        }

        current_stage = "retrieval"
        started = perf_counter()
        candidates = prepared_state.retriever.candidates_for_query(
            question_text,
            query_vector,
            instruction=None,
            top_k=config.candidate_depth,
            rrf_k=config.rrf_k,
        )
        timings["retrieval"] = perf_counter() - started
        result["retrieval_trace"] = {
            "candidate_depth": config.candidate_depth,
            "rrf_k": config.rrf_k,
            "windows": candidates,
        }

        assembly_candidates = candidates["hybrid"]
        if config.reranker_enabled:
            current_stage = "rerank"
            if reranker is None:
                raise ValueError("reranker is required when reranker_enabled is true")
            candidate_ids = [str(row["unit_id"]) for row in assembly_candidates]
            texts = retrieval_unit_texts(prepared_state.assembly_context, candidate_ids)
            request = RerankRequest(
                query_request.request_identity,
                question_text,
                tuple(RerankCandidate(unit_id, texts[unit_id], int(row["rank"])) for unit_id, row in zip(candidate_ids, assembly_candidates)),
            )
            started = perf_counter()
            scores = stable_rank_scores(request, reranker.rerank(request))
            timings["rerank"] = perf_counter() - started
            ranked = project_ranked_candidates(assembly_candidates, scores)
            assembly_candidates = ranked[: config.rerank_output_k]
            result["rerank_trace"] = {
                "status": "executed_successfully",
                "candidate_depth": config.candidate_depth,
                "rerank_output_k": config.rerank_output_k,
                "request_identity": request.question_id,
                "before": list(candidates["hybrid"]),
                "after": ranked,
                "provider": dict(getattr(reranker, "last_response_metadata", {})) if isinstance(getattr(reranker, "last_response_metadata", {}), Mapping) else None,
            }

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
                "mode": "hybrid",
                "candidate_depth": config.candidate_depth,
                "reranker_stage": result["rerank_trace"]["status"],
                "rerank_output_k": config.rerank_output_k if config.reranker_enabled else None,
            },
        )
        timings["assembly"] = perf_counter() - started
        result["evidence_packet"] = packet
        result["assembly_diagnostics"] = diagnostics.to_dict()

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
            started = perf_counter()
            generation_result = generation_provider.generate(generation_request)
            timings["generation"] = perf_counter() - started
            result["generation"] = {
                "status": "executed",
                "execution_status": generation_result.execution_status,
                "result": generation_result.to_dict(),
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
