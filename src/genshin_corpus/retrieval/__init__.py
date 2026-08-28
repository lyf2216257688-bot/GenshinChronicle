"""Phase 04 read-only corpus evidence and benchmark foundations.

This package intentionally contains no retrieval engine, index, embedding, or
RAG orchestration implementation.
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

__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BenchmarkValidationError",
    "CORPUS_PROFILE_VERSION",
    "CanonicalProfileError",
    "load_benchmark",
    "profile_canonical_run",
    "resolve_benchmark_locations",
    "validate_benchmark",
]
