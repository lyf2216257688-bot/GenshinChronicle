"""Stable production artifact-path configuration for the RAG product path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from genshin_corpus.retrieval.candidate_retrieval import DEFAULT_QWEN_DENSE_MANIFEST


DEFAULT_PRODUCTION_RAG_ROOT = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02"
)
DEFAULT_PRODUCTION_QWEN_DENSE_MANIFEST = DEFAULT_QWEN_DENSE_MANIFEST


@dataclass(frozen=True)
class ProductionRagArtifactPaths:
    """The accepted production artifact paths used by the product query path."""

    root: Path = DEFAULT_PRODUCTION_RAG_ROOT
    dense_manifest_path: Path = DEFAULT_PRODUCTION_QWEN_DENSE_MANIFEST

    @property
    def retrieval_unit_manifest_path(self) -> Path:
        return self.root / "ru" / "metadata" / "manifest.json"

    @property
    def lexical_manifest_path(self) -> Path:
        return self.root / "lexical" / "metadata" / "manifest.json"

    @property
    def accepted_qwen_dense_manifest_path(self) -> Path:
        if self.root != DEFAULT_PRODUCTION_RAG_ROOT:
            return self.root / "dense" / "metadata" / "manifest.json"
        return self.dense_manifest_path

    def resolved_key(self) -> tuple[str, str, str]:
        """Return a stable cache key without exposing paths as UI controls."""

        return tuple(
            str(path.expanduser().resolve(strict=False))
            for path in (
                self.retrieval_unit_manifest_path,
                self.lexical_manifest_path,
                self.accepted_qwen_dense_manifest_path,
            )
        )
