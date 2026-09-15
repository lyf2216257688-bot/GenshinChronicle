"""Provider-neutral single-question RAG orchestration."""

from .backend import (
    PreparedRagState,
    SingleQuestionBackendConfig,
    RagBackendPreparationError,
    prepare_rag_state,
    run_single_question,
)
from .config import (
    DEFAULT_PRODUCTION_FIELD_AWARE_LEXICAL_MANIFEST,
    DEFAULT_PRODUCTION_QWEN_DENSE_MANIFEST,
    DEFAULT_PRODUCTION_QWEN_RERANK_ENDPOINT,
    DEFAULT_PRODUCTION_QWEN_RERANK_WORKSPACE,
    DEFAULT_PRODUCTION_RAG_ROOT,
    ProductionRagArtifactPaths,
)

__all__ = [
    "PreparedRagState",
    "SingleQuestionBackendConfig",
    "RagBackendPreparationError",
    "prepare_rag_state",
    "run_single_question",
    "DEFAULT_PRODUCTION_QWEN_DENSE_MANIFEST",
    "DEFAULT_PRODUCTION_FIELD_AWARE_LEXICAL_MANIFEST",
    "DEFAULT_PRODUCTION_QWEN_RERANK_ENDPOINT",
    "DEFAULT_PRODUCTION_QWEN_RERANK_WORKSPACE",
    "DEFAULT_PRODUCTION_RAG_ROOT",
    "ProductionRagArtifactPaths",
]
