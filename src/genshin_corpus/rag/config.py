"""Stable production artifact-path configuration for the RAG product path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Mapping

from genshin_corpus.retrieval.candidate_retrieval import DEFAULT_QWEN_DENSE_MANIFEST


DEFAULT_PRODUCTION_RAG_ROOT = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02"
)
DEFAULT_PRODUCTION_QWEN_DENSE_MANIFEST = DEFAULT_QWEN_DENSE_MANIFEST
ONLINE_QWEN_PROFILE = "online_qwen_2048"
LOCAL_QWEN3_PROFILE = "local_qwen3_1024"
DEFAULT_EMBEDDING_PROFILE = LOCAL_QWEN3_PROFILE
DEFAULT_PRODUCTION_LOCAL_DENSE_MANIFEST = Path(
    "data/retrieval/p04-local-qwen3-1024-20261102-v1/metadata/manifest.json"
)
DEFAULT_PRODUCTION_FIELD_AWARE_LEXICAL_MANIFEST = Path(
    ".local/p04-block-a-closure-20260914-r2/field-aware-lexical/metadata/manifest.json"
)
DEFAULT_PRODUCTION_QWEN_RERANK_WORKSPACE = "ws-gdq9z4ufdb87egio"
DEFAULT_PRODUCTION_QWEN_RERANK_ENDPOINT = (
    "https://ws-gdq9z4ufdb87egio.cn-beijing.maas.aliyuncs.com/"
    "api/v1/services/rerank/text-rerank/text-rerank"
)


@dataclass(frozen=True)
class ProductionRagArtifactPaths:
    """The accepted production artifact paths used by the product query path."""

    root: Path = DEFAULT_PRODUCTION_RAG_ROOT
    dense_manifest_path: Path | None = None
    embedding_profile: str = DEFAULT_EMBEDDING_PROFILE

    def __post_init__(self) -> None:
        path = dense_manifest_for_profile(self.embedding_profile, self.dense_manifest_path)
        if self.embedding_profile == ONLINE_QWEN_PROFILE and self.root != DEFAULT_PRODUCTION_RAG_ROOT:
            path = self.root / "dense" / "metadata" / "manifest.json"
        object.__setattr__(self, "dense_manifest_path", path)

    @property
    def retrieval_unit_manifest_path(self) -> Path:
        return self.root / "ru" / "metadata" / "manifest.json"

    @property
    def lexical_manifest_path(self) -> Path:
        if self.root == DEFAULT_PRODUCTION_RAG_ROOT:
            return DEFAULT_PRODUCTION_FIELD_AWARE_LEXICAL_MANIFEST
        return self.root / "lexical" / "metadata" / "manifest.json"

    @property
    def accepted_qwen_dense_manifest_path(self) -> Path:
        """Compatibility alias for existing artifact-path callers."""
        return self.dense_manifest_path

    def resolved_key(self) -> tuple[str, str, str, str]:
        """Return a stable cache key without exposing paths as UI controls."""

        return tuple(
            str(path.expanduser().resolve(strict=False))
            for path in (
                self.retrieval_unit_manifest_path,
                self.lexical_manifest_path,
                self.dense_manifest_path,
            )
        ) + (self.embedding_profile,)


def dense_manifest_for_profile(profile: str, path: Path | None = None) -> Path:
    if profile == LOCAL_QWEN3_PROFILE:
        return DEFAULT_PRODUCTION_LOCAL_DENSE_MANIFEST if path is None else Path(path)
    if profile == ONLINE_QWEN_PROFILE:
        return DEFAULT_PRODUCTION_QWEN_DENSE_MANIFEST if path is None else Path(path)
    raise ValueError("GENSHIN_EMBEDDING_PROFILE must be local_qwen3_1024 or online_qwen_2048")


def artifact_paths_from_environment(environment: Mapping[str, str] | None = None) -> ProductionRagArtifactPaths:
    values = os.environ if environment is None else environment
    profile = values.get("GENSHIN_EMBEDDING_PROFILE", DEFAULT_EMBEDDING_PROFILE)
    path = values.get("GENSHIN_LOCAL_DENSE_MANIFEST", "").strip()
    return ProductionRagArtifactPaths(
        dense_manifest_path=Path(path) if path and profile == LOCAL_QWEN3_PROFILE else None,
        embedding_profile=profile,
    )
