"""Streamlit-independent wiring for the single-question RAG UI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Any

from genshin_corpus.generation.generation import (
    BASELINE_QWEN_MODEL_ID,
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    GenerationProvider,
    workspace_from_bailian_base_url,
)
from genshin_corpus.rag.backend import (
    PreparedRagState,
    SingleQuestionBackendConfig,
    prepare_rag_state,
    run_single_question,
)
from genshin_corpus.rag.config import (
    DEFAULT_PRODUCTION_QWEN_RERANK_ENDPOINT,
    DEFAULT_PRODUCTION_QWEN_RERANK_WORKSPACE,
    ProductionRagArtifactPaths,
)
from genshin_corpus.retrieval.qwen_embedding import (
    DashScopeQwenEmbeddingConfig,
    DashScopeQwenEmbeddingTransport,
    QwenEmbeddingTransport,
)
from genshin_corpus.retrieval.qwen_rerank import (
    DashScopeQwenRerankConfig,
    DashScopeQwenRerankTransport,
    QWEN_RERANK_MODEL_ID,
)


class UiConfigurationError(ValueError):
    """Raised when submit-time UI configuration is unavailable or unsafe."""


@dataclass(frozen=True)
class UiQuery:
    question_text: str
    execution_mode: str
    output_root: Path


class PreparedStateLease:
    """One active query lease held against a session-owned prepared state."""

    def __init__(self, owner: "PreparedStateOwner", state: PreparedRagState) -> None:
        self._owner = owner
        self._state = state
        self._released = False

    def __enter__(self) -> PreparedRagState:
        return self._state

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if not self._released:
            self._released = True
            self._owner._release_lease()


class PreparedStateOwner:
    """Protect one session resource from release while a query is active."""

    def __init__(self, state: PreparedRagState) -> None:
        self._state = state
        self._condition = threading.Condition()
        self._active_leases = 0
        self._closing = False
        self._closed = False

    @property
    def open(self) -> bool:
        with self._condition:
            return not self._closed and not self._closing and not self._state.closed

    def lease(self) -> PreparedStateLease:
        with self._condition:
            if self._closed or self._closing or self._state.closed:
                raise RuntimeError("PreparedRagState is closed")
            self._active_leases += 1
            return PreparedStateLease(self, self._state)

    def _release_lease(self) -> None:
        with self._condition:
            self._active_leases -= 1
            if self._active_leases == 0:
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closing = True
            while self._active_leases:
                self._condition.wait()
            try:
                self._state.close()
            finally:
                self._closed = True
                self._closing = False
                self._condition.notify_all()


def resolve_output_root(value: str | Path) -> Path:
    """Validate a caller-selected Windows-compatible base output directory."""

    if isinstance(value, str) and not value.strip():
        raise UiConfigurationError("output root must be a non-empty path")
    path = Path(value).expanduser()
    if path.exists() and not path.is_dir():
        raise UiConfigurationError("output root exists but is not a directory")
    return path.resolve(strict=False)


def production_artifact_paths() -> ProductionRagArtifactPaths:
    """Return fixed production paths without reading files or preparing state."""

    return ProductionRagArtifactPaths()


def validate_prepared_state(state: PreparedStateOwner) -> bool:
    """Allow Streamlit to retain only an open prepared state."""

    return state.open


def release_prepared_state(state: PreparedStateOwner) -> None:
    """Release the session-owned mmap-backed state idempotently."""

    state.close()


def prepare_production_state(paths_key: tuple[str, str, str]) -> PreparedStateOwner:
    """Prepare the fixed accepted baseline represented by a cache key."""

    ru_manifest, lexical_manifest, dense_manifest = (Path(item) for item in paths_key)
    return PreparedStateOwner(prepare_rag_state(
        retrieval_unit_manifest_path=ru_manifest,
        lexical_manifest_path=lexical_manifest,
        dense_manifest_path=dense_manifest,
    ))


def build_embedding_transport(environment: Mapping[str, str] | None = None) -> QwenEmbeddingTransport:
    """Build the existing Qwen query transport from runtime environment only."""

    values = os.environ if environment is None else environment
    endpoint = values.get("DASHSCOPE_QWEN_EMBEDDING_ENDPOINT")
    workspace = values.get("DASHSCOPE_QWEN_WORKSPACE")
    region = values.get("DASHSCOPE_QWEN_REGION", "cn-beijing")
    if not endpoint or not workspace:
        raise UiConfigurationError(
            "Qwen query embedding requires DASHSCOPE_QWEN_EMBEDDING_ENDPOINT and "
            "DASHSCOPE_QWEN_WORKSPACE"
        )
    try:
        config = DashScopeQwenEmbeddingConfig(
            region=region,
            endpoint=endpoint,
            workspace=workspace,
        )
        return DashScopeQwenEmbeddingTransport.from_environment(config, environment=values)
    except Exception as exc:
        raise UiConfigurationError("Qwen query embedding transport is unavailable") from exc


def build_generation_provider(environment: Mapping[str, str] | None = None) -> GenerationProvider:
    """Build the current exact-snapshot Bailian/Qwen Generation control."""

    values = os.environ if environment is None else environment
    endpoint = values.get("BAILIAN_BASE_URL")
    if not endpoint:
        raise UiConfigurationError("generate_answer requires BAILIAN_BASE_URL")
    try:
        config = BailianControlConfig(
            region="cn-beijing",
            endpoint=endpoint,
            workspace=workspace_from_bailian_base_url(endpoint),
            model_id=BASELINE_QWEN_MODEL_ID,
            enable_thinking=False,
            max_attempts=1,
        )
        transport = BailianOpenAICompatibleTransport.from_environment(config, environment=values)
        return BailianGenerationProvider(config, transport)
    except Exception as exc:
        raise UiConfigurationError("Generation provider is unavailable") from exc


def build_reranker(environment: Mapping[str, str] | None = None) -> DashScopeQwenRerankTransport:
    """Build the accepted online Qwen reranker; failure is configuration-fatal."""

    values = os.environ if environment is None else environment
    endpoint = values.get("DASHSCOPE_QWEN_RERANK_ENDPOINT", DEFAULT_PRODUCTION_QWEN_RERANK_ENDPOINT)
    workspace = values.get("DASHSCOPE_QWEN_RERANK_WORKSPACE", DEFAULT_PRODUCTION_QWEN_RERANK_WORKSPACE)
    region = values.get("DASHSCOPE_QWEN_RERANK_REGION", "cn-beijing")
    try:
        config = DashScopeQwenRerankConfig(region=region, endpoint=endpoint, workspace=workspace)
        transport = DashScopeQwenRerankTransport.from_environment(config, environment=values)
    except Exception as exc:
        raise UiConfigurationError(
            f"{QWEN_RERANK_MODEL_ID} reranker is unavailable"
        ) from exc
    return transport


def execute_query(
    prepared_state: PreparedRagState,
    query: UiQuery,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Wire one UI request into the existing backend boundary."""

    if query.execution_mode not in {"evidence_only", "generate_answer"}:
        raise UiConfigurationError("execution mode must be evidence_only or generate_answer")
    if not isinstance(query.question_text, str) or not query.question_text.strip():
        raise UiConfigurationError("question must be a non-empty string")
    resolved_output_root = resolve_output_root(query.output_root)
    embedding_transport = build_embedding_transport(environment)
    reranker = build_reranker(environment)
    generation_provider = (
        build_generation_provider(environment)
        if query.execution_mode == "generate_answer"
        else None
    )
    return run_single_question(
        prepared_state,
        query.question_text,
        embedding_transport=embedding_transport,
        generation_provider=generation_provider,
        config=SingleQuestionBackendConfig(),
        reranker=reranker,
        execution_mode=query.execution_mode,
        output_root=resolved_output_root,
    )


def _compact_error(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    compact = {
        key: value[key]
        for key in ("stage", "category", "code")
        if isinstance(value.get(key), str) and value[key]
    }
    return compact or None


def _compact_citation_validation(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    compact = {
        key: value[key]
        for key in ("status", "citation_integrity", "citation_coverage", "semantic_faithfulness")
        if isinstance(value.get(key), str) and value[key]
    }
    return compact or None


def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project bounded product-facing fields from the backend result contract."""

    packet = result.get("evidence_packet")
    packet = packet if isinstance(packet, Mapping) else {}
    evidence_rows = packet.get("evidence")
    evidence_rows = evidence_rows if isinstance(evidence_rows, list) else []
    evidence = [
        {
            key: item[key]
            for key in ("evidence_id", "text", "char_count")
            if key in item
        }
        for item in evidence_rows
        if isinstance(item, Mapping)
    ]
    budget = packet.get("budget")
    budget = budget if isinstance(budget, Mapping) else {}
    persistence = result.get("persistence")
    persistence = persistence if isinstance(persistence, Mapping) else {}
    return {
        "status": result.get("status"),
        "execution_mode": result.get("execution_mode"),
        "execution_identity": (result.get("query") or {}).get("execution_identity")
        if isinstance(result.get("query"), Mapping)
        else None,
        "final_answer": result.get("final_answer"),
        "citation_validation": _compact_citation_validation(result.get("citation_validation")),
        "evidence_count": budget.get("used_evidence_blocks", len(evidence)),
        "used_context_chars": budget.get("used_context_chars"),
        "total_context_chars": budget.get("total_context_chars"),
        "timing_seconds": dict(result.get("timing_seconds") or {})
        if isinstance(result.get("timing_seconds"), Mapping)
        else {},
        "run_root": persistence.get("run_root"),
        "persistence": dict(persistence),
        "error": _compact_error(result.get("error")),
        "evidence": evidence,
    }
