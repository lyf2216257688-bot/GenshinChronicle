"""Opt-in, offline Qwen3 embeddings and identity-checked GC Dense candidates.

This module never selects a production backend and contains no API transport.
CPU FP32 and CUDA FP16 are separate vector spaces in the first contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import time
from typing import Any, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from .candidate_retrieval import (
    CandidateRetrievalError, DENSE_INDEX_SCHEMA_VERSION, _descriptor,
    _dense_candidates_from_loaded, _gzip_jsonl, _load_dense,
)
from .retrieval_units import load_retrieval_units

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
WEIGHT_SHA256 = "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
DIMENSION = 1024
MAX_LENGTH = 2048
QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
CONTRACT_VERSION = "gc-local-qwen3-embedding-0.1"
RUNTIME = {"python": "3.12.10", "torch": "2.11.0+cu128", "transformers": "4.57.3", "tokenizers": "0.22.2", "numpy": "2.5.3", "safetensors": "0.8.0"}


class LocalQwen3Error(CandidateRetrievalError):
    """Missing evidence, incompatible vector space, or invalid local runtime."""


def file_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_text(text: str, role: str) -> str:
    if role not in {"query", "document"}:
        raise LocalQwen3Error("role must be query or document")
    if not isinstance(text, str) or not text.strip():
        raise LocalQwen3Error("embedding text must be a non-empty string")
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{text}" if role == "query" else text


def verify_snapshot(root: Path) -> dict[str, Any]:
    root = Path(root)
    manifest = json.loads((root / "installation-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("model_id") != MODEL_ID or manifest.get("revision") != REVISION:
        raise LocalQwen3Error("local snapshot model/revision mismatch")
    files = manifest.get("files", [])
    indexed = {row["path"]: row for row in files}
    required = {"model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json", "merges.txt", "vocab.json"}
    if len(indexed) != len(files) or not required <= indexed.keys():
        raise LocalQwen3Error("local snapshot file accounting mismatch")
    if indexed["model.safetensors"]["sha256"] != WEIGHT_SHA256:
        raise LocalQwen3Error("official model weight identity mismatch")
    for name, row in indexed.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise LocalQwen3Error("snapshot file missing or outside snapshot")
        if path.stat().st_size != row["size"] or file_sha256(path) != row["sha256"]:
            raise LocalQwen3Error(f"snapshot SHA256 mismatch: {name}")
    return manifest


@dataclass(frozen=True)
class LocalQwen3Config:
    model_path: Path
    device: str = "cuda"
    dtype: str = "float16"
    max_length: int = MAX_LENGTH

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_path", Path(self.model_path))
        if (self.device, self.dtype) not in {("cuda", "float16"), ("cpu", "float32")}:
            raise LocalQwen3Error("explicit CUDA FP16 or CPU FP32 is required; no fallback")
        if self.max_length != MAX_LENGTH:
            raise LocalQwen3Error(f"first encoding contract requires max_length={MAX_LENGTH}")

    def identity_projection(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION, "model_id": MODEL_ID,
            "revision": REVISION, "weight_sha256": WEIGHT_SHA256,
            "snapshot_files_identity": sha256_json(snapshot["files"]),
            "dimension": DIMENSION, "output_dtype": "float32", "normalization": "L2",
            "compute_dtype": self.dtype, "device": self.device, "max_length": self.max_length,
            "padding_side": "left", "truncation_side": "right", "truncation": True,
            "add_special_tokens": True, "special_token_policy": "pinned official tokenizer postprocessor; appends end-of-text 151643",
            "pooling": "last_nonpadding_token", "attention": "sdpa", "use_cache": False,
            "query_template": "Instruct: {instruction}\nQuery:{query}",
            "query_instruction": QUERY_INSTRUCTION, "document_instruction": None,
            "document_projection": "exact retrieval_visible_text", "runtime": RUNTIME,
            "repeat_contract": "same runtime/device/batch: atol=1e-6, rtol=0; no cross-device bitwise promise",
        }


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: Any
    space_identity: str
    role: str
    text_sha256: tuple[str, ...]
    token_audit: tuple[dict[str, Any], ...]


class LocalQwen3Embedding:
    def __init__(self, config: LocalQwen3Config):
        self.config = config
        self.snapshot = verify_snapshot(config.model_path)
        self.contract = config.identity_projection(self.snapshot)
        self.space_identity = sha256_json(self.contract)
        self._model = self._tokenizer = None
        self.last_encoding_metadata: dict[str, Any] = {}

    def load(self) -> None:
        if self._model is not None:
            return
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
        import torch
        import transformers
        from importlib.metadata import version
        from transformers import AutoModel, AutoTokenizer
        actual = {"python": platform.python_version(), "torch": torch.__version__, "transformers": transformers.__version__}
        actual.update({name: version(name) for name in ("tokenizers", "numpy", "safetensors")})
        if actual != RUNTIME:
            raise LocalQwen3Error(f"pinned runtime mismatch: {actual}")
        if self.config.device == "cuda" and not torch.cuda.is_available():
            raise LocalQwen3Error("CUDA unavailable; choose an explicit CPU contract/index")
        torch.manual_seed(0)
        torch.set_num_threads(4)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.use_deterministic_algorithms(True)
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.config.model_path), local_files_only=True, trust_remote_code=False, padding_side="left")
        self._tokenizer.truncation_side = "right"
        self._model = AutoModel.from_pretrained(
            str(self.config.model_path), local_files_only=True, trust_remote_code=False,
            dtype=getattr(torch, self.config.dtype), attn_implementation="sdpa",
        ).to(self.config.device).eval()
        if self._model.config.hidden_size != DIMENSION:
            raise LocalQwen3Error("model hidden dimension mismatch")

    def encode(self, texts: Sequence[str], *, role: str, batch_size: int = 2) -> EmbeddingBatch:
        import numpy as np
        import torch
        if isinstance(texts, (str, bytes)) or not texts:
            raise LocalQwen3Error("texts must be a non-empty sequence")
        if type(batch_size) is not int or batch_size < 1:
            raise LocalQwen3Error("batch_size must be a positive integer")
        projected = [project_text(t, role) for t in texts]
        self.load()
        started=time.perf_counter()
        outputs, audit = [], []
        for start in range(0, len(projected), batch_size):
            batch = projected[start:start + batch_size]
            raw_ids = self._tokenizer(batch, truncation=False, add_special_tokens=True)["input_ids"]
            tokens = self._tokenizer(batch, padding=True, truncation=True, add_special_tokens=True, max_length=self.config.max_length, return_tensors="pt").to(self.config.device)
            for ids, mask in zip(raw_ids, tokens["attention_mask"]):
                audit.append({"untruncated_tokens": len(ids), "encoded_tokens": int(mask.sum()), "truncated": len(ids) > self.config.max_length})
            with torch.inference_mode():
                hidden = self._model(**tokens, use_cache=False).last_hidden_state
                # Left padding is fixed; the final token is valid in every row.
                pooled = hidden[:, -1].float()
                norm = torch.linalg.vector_norm(pooled, dim=1, keepdim=True)
                if not torch.isfinite(pooled).all() or (norm == 0).any():
                    raise LocalQwen3Error("non-finite or zero model embedding")
                outputs.append((pooled / norm).cpu().numpy())
        array = np.concatenate(outputs).astype(np.float32)
        if array.shape != (len(texts), DIMENSION):
            raise LocalQwen3Error("embedding dimension/count mismatch")
        elapsed=time.perf_counter()-started
        self.last_encoding_metadata={"role":role,"texts":len(texts),"batch_size":batch_size,"seconds_excluding_load":elapsed,"texts_per_second":len(texts)/elapsed,"encoded_tokens":sum(a["encoded_tokens"] for a in audit),"truncated_count":sum(a["truncated"] for a in audit)}
        return EmbeddingBatch(array, self.space_identity, role, tuple(sha256(t.encode("utf-8")).hexdigest() for t in texts), tuple(audit))

    def encode_documents(self, texts: Sequence[str], *, batch_size: int = 2) -> EmbeddingBatch:
        return self.encode(texts, role="document", batch_size=batch_size)

    def encode_queries(self, texts: Sequence[str], *, batch_size: int = 2) -> EmbeddingBatch:
        return self.encode(texts, role="query", batch_size=batch_size)

    def encode_query(self, text: str) -> EmbeddingBatch:
        return self.encode_queries([text], batch_size=1)


def build_local_index(ru_manifest_path: Path, selected_unit_ids: Sequence[str], output_root: Path, encoder: LocalQwen3Embedding, *, batch_size: int = 2) -> dict[str, Any]:
    """Verify the formal RU snapshot, preserve its IDs/order and refuse overwrites."""
    if len(set(selected_unit_ids)) != len(selected_unit_ids):
        raise LocalQwen3Error("duplicate selected RU IDs")
    if Path(output_root).exists():
        raise LocalQwen3Error("new Dense output root must not exist")
    source, units = load_retrieval_units(ru_manifest_path, selected_unit_ids=selected_unit_ids)
    batch = encoder.encode_documents([u["retrieval_visible_text"] for u in units], batch_size=batch_size)
    return _write_index(output_root, source, units, batch, encoder.contract, ru_manifest_path)


def _write_index(output_root: Path, source: dict, units: Sequence[dict], batch: EmbeddingBatch, contract: dict, ru_manifest_path: Path) -> dict:
    import numpy as np
    if Path(output_root).exists():
        raise LocalQwen3Error("Dense output root already exists")
    if batch.role != "document" or batch.space_identity != sha256_json(contract):
        raise LocalQwen3Error("document vector space identity mismatch")
    hashes = tuple(sha256(u["retrieval_visible_text"].encode("utf-8")).hexdigest() for u in units)
    if hashes != batch.text_sha256 or batch.vectors.shape != (len(units), DIMENSION):
        raise LocalQwen3Error("RU text/order/vector dimension mismatch")
    if batch.vectors.dtype != np.float32 or not np.isfinite(batch.vectors).all() or not np.allclose(np.linalg.norm(batch.vectors, axis=1), 1, atol=1e-5, rtol=0):
        raise LocalQwen3Error("document vectors violate FP32/L2 contract")
    rows = [{"occurrence_index": i, "unit_id": u["unit_id"], "text_sha256": hashes[i], "source": u["source"], "record_title": u["source"].get("record_context", {}).get("record_title"), "token_audit": batch.token_audit[i]} for i, u in enumerate(units)]
    if len({r["unit_id"] for r in rows}) != len(rows):
        raise LocalQwen3Error("duplicate RU rows")
    buffer = BytesIO()
    np.save(buffer, batch.vectors, allow_pickle=False)
    vb, rb = buffer.getvalue(), _gzip_jsonl(rows)
    identity = sha256_json({"space": batch.space_identity, "ru_build": source["build_identity"], "rows": rows, "vectors_sha256": sha256(vb).hexdigest()})
    manifest = {
        "schema_version": DENSE_INDEX_SCHEMA_VERSION, "status": "complete", "arm": "dense",
        "arm_build_identity": identity, "retrieval_unit_build_identity": source["build_identity"],
        "source_ru_manifest": str(Path(ru_manifest_path).resolve()), "source_ru_manifest_sha256": file_sha256(ru_manifest_path),
        "model_name": MODEL_ID, "model_revision": REVISION, "model_sha256": WEIGHT_SHA256,
        "embedding_dimension": DIMENSION, "dtype": "float32", "normalization": "L2", "row_count": len(rows),
        "local_qwen3_contract": contract, "vector_space_identity": batch.space_identity,
        "scope": "explicit formal RU subset; no production adoption", "row_mapping_policy": "formal RU snapshot order restricted to selected IDs",
        "artifacts": {"vectors": _descriptor("artifacts/vectors.f32.npy", vb), "rows": _descriptor("artifacts/rows.jsonl.gz", rb, len(rows))},
    }
    output_root = Path(output_root)
    atomic_write(output_root / "artifacts/vectors.f32.npy", vb)
    atomic_write(output_root / "artifacts/rows.jsonl.gz", rb)
    atomic_write(output_root / "metadata/manifest.json", canonical_json_bytes(manifest))
    return manifest


def local_candidates(manifest_path: Path, query: EmbeddingBatch, *, top_k: int = 20) -> list[dict]:
    """Typed query binding rejects even equal-dimensional foreign vector spaces."""
    if not isinstance(query, EmbeddingBatch) or query.role != "query":
        raise LocalQwen3Error("identity-bound query embeddings required")
    if type(top_k) is not int or top_k <= 0:
        raise LocalQwen3Error("top_k must be a positive integer")
    manifest, vectors, rows = _load_dense(manifest_path, use_mmap=True)
    contract = manifest.get("local_qwen3_contract")
    if not isinstance(contract, dict) or sha256_json(contract) != query.space_identity or manifest.get("vector_space_identity") != query.space_identity:
        raise LocalQwen3Error("query/index vector space mismatch")
    if manifest.get("model_name") != MODEL_ID or manifest.get("model_revision") != REVISION or manifest.get("model_sha256") != WEIGHT_SHA256 or vectors.shape[1] != DIMENSION:
        raise LocalQwen3Error("query/index model or dimension mismatch")
    if query.vectors.shape != (1, DIMENSION):
        raise LocalQwen3Error("candidate retrieval requires one 1024-dimensional query")
    import numpy as np
    if len(query.text_sha256) != 1 or query.vectors.dtype != np.float32 or not np.isfinite(query.vectors).all() or not np.allclose(np.linalg.norm(query.vectors,axis=1),1,atol=1e-5,rtol=0):
        raise LocalQwen3Error("query violates bound text/FP32/L2 contract")
    candidates = _dense_candidates_from_loaded(manifest, vectors, rows, query.vectors[0], top_k=top_k, query_instruction=QUERY_INSTRUCTION)
    locators = {r["unit_id"]: r for r in rows}
    for candidate in candidates:
        row = locators[candidate["unit_id"]]
        candidate["source"] = row["source"]
        candidate["record_title"] = row["record_title"]
        candidate["retrieval"]["vector_space_identity"] = query.space_identity
        candidate["retrieval"]["query_text_sha256"] = query.text_sha256[0]
    return candidates
