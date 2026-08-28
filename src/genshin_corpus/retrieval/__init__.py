"""Phase 04 Retrieval experiment foundations.

This package contains deterministic, offline representation and lexical
experiments only; it has no production retrieval service, embeddings, or RAG.
"""

from .benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkValidationError,
    load_benchmark,
    resolve_benchmark_locations,
    validate_benchmark,
)
from .profiler import (
    CORPUS_PROFILE_VERSION,
    CanonicalProfileError,
    profile_canonical_run,
)
from .representations import (
    REPRESENTATION_ARMS,
    RETRIEVAL_DOCUMENT_SCHEMA_VERSION,
    RETRIEVAL_REPRESENTATION_VERSION,
    RetrievalRepresentationError,
    build_retrieval_documents,
    document_covers_location,
    load_retrieval_documents,
)

__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BenchmarkValidationError",
    "CORPUS_PROFILE_VERSION",
    "CanonicalProfileError",
    "load_benchmark",
    "profile_canonical_run",
    "REPRESENTATION_ARMS",
    "RETRIEVAL_DOCUMENT_SCHEMA_VERSION",
    "RETRIEVAL_REPRESENTATION_VERSION",
    "RetrievalRepresentationError",
    "build_retrieval_documents",
    "document_covers_location",
    "load_retrieval_documents",
    "resolve_benchmark_locations",
    "validate_benchmark",
]
