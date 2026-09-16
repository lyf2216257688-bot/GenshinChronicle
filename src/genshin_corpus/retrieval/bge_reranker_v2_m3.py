"""Offline local adapter for BAAI/bge-reranker-v2-m3."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
import platform
from typing import Any

from .reranking import RerankRequest, RerankScore, RerankingError, stable_rank_scores


MODEL_ID = "BAAI/bge-reranker-v2-m3"
MODEL_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
MODEL_WEIGHT_SHA256 = "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286"
MODEL_WEIGHT_NAME = "model.safetensors"
EXPECTED_TOKENIZER_CLASS = "XLMRobertaTokenizerFast"
EXPECTED_MODEL_CLASS = "XLMRobertaForSequenceClassification"
PYTHON_VERSION = "3.12.10"
TORCH_VERSION = "2.11.0+cu128"
TRANSFORMERS_VERSION = "4.39.1"
TOKENIZERS_VERSION = "0.15.2"
MAX_LENGTH = 2048
PROJECTION_MAX_CHARS = 6000


class BgeRerankerV2M3Error(RerankingError):
    """Raised when the pinned local BGE runtime cannot score safely."""


@dataclass(frozen=True)
class BgeRerankerV2M3Config:
    model_path: Path
    revision: str = MODEL_REVISION
    model_weight_sha256: str = MODEL_WEIGHT_SHA256
    dtype: str = "float16"
    device: str = "cuda"
    batch_size: int = 1
    max_length: int = MAX_LENGTH
    projection_max_chars: int = PROJECTION_MAX_CHARS

    def __post_init__(self) -> None:
        if not isinstance(self.model_path, Path):
            object.__setattr__(self, "model_path", Path(self.model_path))
        if self.revision != MODEL_REVISION:
            raise BgeRerankerV2M3Error("BGE revision is not the pinned immutable revision")
        if self.model_weight_sha256 != MODEL_WEIGHT_SHA256:
            raise BgeRerankerV2M3Error("BGE model weight SHA-256 is not pinned")
        if self.dtype != "float16" or self.device != "cuda":
            raise BgeRerankerV2M3Error("BGE reranker requires FP16 CUDA")
        if self.batch_size != 1:
            raise BgeRerankerV2M3Error("BGE reranker requires batch size 1")
        if self.max_length != MAX_LENGTH:
            raise BgeRerankerV2M3Error(f"BGE reranker requires max_length {MAX_LENGTH}")
        if self.projection_max_chars != PROJECTION_MAX_CHARS:
            raise BgeRerankerV2M3Error(
                f"BGE reranker requires the verified {PROJECTION_MAX_CHARS}-character projection cap"
            )

    def identity_projection(self) -> dict[str, Any]:
        return {
            "model": MODEL_ID,
            "revision": self.revision,
            "model_path": str(self.model_path.resolve()),
            "model_weight_name": MODEL_WEIGHT_NAME,
            "model_weight_sha256": self.model_weight_sha256,
            "dtype": self.dtype,
            "device": self.device,
            "batch_size": self.batch_size,
            "max_length": self.max_length,
            "projection_max_chars": self.projection_max_chars,
            "python_version": PYTHON_VERSION,
            "torch_version": TORCH_VERSION,
            "transformers_version": TRANSFORMERS_VERSION,
            "tokenizers_version": TOKENIZERS_VERSION,
            "scoring_contract": "tokenizer_pair_sequence_classification_raw_single_logit",
        }


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_pinned_local_model(model_path: Path) -> dict[str, Any]:
    """Bind the local snapshot root and weight bytes to the accepted identity."""

    root = Path(model_path)
    if not root.is_dir() or root.name != MODEL_REVISION:
        raise BgeRerankerV2M3Error("local BGE root is not the revision-named pinned snapshot")
    weight_path = root / MODEL_WEIGHT_NAME
    if not weight_path.is_file():
        raise BgeRerankerV2M3Error("pinned BGE model weight is missing")
    actual_hash = _sha256_file(weight_path)
    if actual_hash != MODEL_WEIGHT_SHA256:
        raise BgeRerankerV2M3Error("pinned BGE model weight SHA-256 mismatch")
    return {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "model_path": str(root.resolve()),
        "model_weight": {
            "path": str(weight_path.resolve()),
            "byte_count": weight_path.stat().st_size,
            "sha256": actual_hash,
        },
    }


def _validate_runtime_versions(torch_module: Any, transformers_module: Any, tokenizers_module: Any) -> None:
    actual = {
        "python": platform.python_version(),
        "torch": getattr(torch_module, "__version__", None),
        "transformers": getattr(transformers_module, "__version__", None),
        "tokenizers": getattr(tokenizers_module, "__version__", None),
    }
    expected = {
        "python": PYTHON_VERSION,
        "torch": TORCH_VERSION,
        "transformers": TRANSFORMERS_VERSION,
        "tokenizers": TOKENIZERS_VERSION,
    }
    if actual != expected:
        raise BgeRerankerV2M3Error(f"local BGE runtime version mismatch: {actual!r}")


def _finite_raw_logit(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise BgeRerankerV2M3Error("BGE returned a non-finite raw logit")
    return result


class BgeRerankerV2M3:
    """Direct local Transformers adapter with no network or provider path."""

    def __init__(
        self,
        config: BgeRerankerV2M3Config,
        *,
        tokenizer: Any | None = None,
        model: Any | None = None,
    ) -> None:
        self.config = config
        self._tokenizer = tokenizer
        self._model = model
        self.last_request_metadata: dict[str, Any] = {}
        self.last_response_metadata: dict[str, Any] = {}
        if tokenizer is not None:
            self._validate_tokenizer(tokenizer)
        if model is not None:
            self._validate_model(model)

    @staticmethod
    def _validate_tokenizer(tokenizer: Any) -> None:
        if type(tokenizer).__name__ != EXPECTED_TOKENIZER_CLASS:
            raise BgeRerankerV2M3Error("local BGE tokenizer class mismatch")
        tokenizer.padding_side = "right"

    @staticmethod
    def _validate_model(model: Any) -> None:
        if type(model).__name__ != EXPECTED_MODEL_CLASS:
            raise BgeRerankerV2M3Error("local BGE model class mismatch")
        parameters = list(model.parameters())
        if not parameters:
            raise BgeRerankerV2M3Error("local BGE model has no parameters")
        devices = {str(parameter.device) for parameter in parameters}
        dtypes = {str(parameter.dtype) for parameter in parameters}
        if any(not device.startswith("cuda") or device.startswith("meta") for device in devices):
            raise BgeRerankerV2M3Error(f"local BGE model is not fully resident on CUDA: {sorted(devices)}")
        if dtypes not in ({"torch.float16"}, {"float16"}):
            raise BgeRerankerV2M3Error(f"local BGE model is not fully FP16: {sorted(dtypes)}")
        model.eval()

    def _lazy_load(self) -> None:
        if self._tokenizer is not None and self._model is not None:
            return
        verify_pinned_local_model(self.config.model_path)
        try:
            import tokenizers
            import torch
            import transformers
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except Exception as exc:
            raise BgeRerankerV2M3Error("local BGE runtime dependencies are unavailable") from exc
        _validate_runtime_versions(torch, transformers, tokenizers)
        if not torch.cuda.is_available():
            raise BgeRerankerV2M3Error("CUDA is unavailable for the local BGE reranker")
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                str(self.config.model_path),
                local_files_only=True,
            )
            model = AutoModelForSequenceClassification.from_pretrained(
                str(self.config.model_path),
                local_files_only=True,
                torch_dtype=torch.float16,
            )
            model.to("cuda")
            self._validate_tokenizer(tokenizer)
            self._validate_model(model)
        except BgeRerankerV2M3Error:
            raise
        except Exception as exc:
            raise BgeRerankerV2M3Error("pinned local BGE model failed to load offline on CUDA") from exc
        self._tokenizer = tokenizer
        self._model = model

    def runtime_identity(self) -> dict[str, Any]:
        return self.config.identity_projection()

    def _score_candidate(self, query: str, passage: str) -> tuple[float, dict[str, Any]]:
        if self._tokenizer is None or self._model is None:
            raise BgeRerankerV2M3Error("local BGE runtime is not initialized")
        if len(passage) > self.config.projection_max_chars:
            raise BgeRerankerV2M3Error("BGE candidate exceeds the verified projection cap")
        pair = [[query, passage]]
        full = self._tokenizer(
            pair,
            padding=False,
            truncation=False,
            return_tensors=None,
        )
        try:
            full_token_count = len(full["input_ids"][0])
        except Exception as exc:
            raise BgeRerankerV2M3Error("BGE tokenizer returned invalid full-pair token IDs") from exc
        encoded = self._tokenizer(
            pair,
            padding=True,
            truncation=True,
            max_length=self.config.max_length,
            return_tensors="pt",
        )
        try:
            actual_token_count = int(encoded["attention_mask"][0].sum().item())
            gpu_inputs = {key: value.to("cuda") for key, value in encoded.items()}
        except Exception as exc:
            raise BgeRerankerV2M3Error("BGE tokenizer returned invalid model inputs") from exc
        try:
            import torch

            with torch.inference_mode():
                logits = self._model(**gpu_inputs, return_dict=True).logits
            if logits.numel() != 1:
                raise BgeRerankerV2M3Error("BGE must return exactly one scalar logit per batch item")
            score = _finite_raw_logit(logits.reshape(-1)[0].float().detach().cpu().item())
        except BgeRerankerV2M3Error:
            raise
        except Exception as exc:
            raise BgeRerankerV2M3Error("local BGE scoring failed") from exc
        truncated_token_count = max(0, full_token_count - actual_token_count)
        return score, {
            "full_pair_token_count": full_token_count,
            "actual_token_count": actual_token_count,
            "max_length": self.config.max_length,
            "truncated": truncated_token_count > 0,
            "truncated_token_count": truncated_token_count,
        }

    def rerank(self, request: RerankRequest) -> tuple[RerankScore, ...]:
        if not isinstance(request, RerankRequest):
            raise BgeRerankerV2M3Error("BGE rerank request has invalid type")
        self._lazy_load()
        scores: list[RerankScore] = []
        audits: list[dict[str, Any]] = []
        for candidate in request.candidates:
            score, audit = self._score_candidate(request.query, candidate.text)
            scores.append(RerankScore(candidate.unit_id, candidate.original_rank, score, 0))
            audits.append({"unit_id": candidate.unit_id, **audit})
        self.last_request_metadata = {
            "candidate_count": len(scores),
            "batch_size": self.config.batch_size,
            "batch_count": len(scores),
            "max_length": self.config.max_length,
            "truncated_count": sum(1 for item in audits if item["truncated"]),
            "total_truncated_tokens": sum(int(item["truncated_token_count"]) for item in audits),
            "total_input_token_count": sum(int(item["actual_token_count"]) for item in audits),
            "candidate_token_audit": audits,
        }
        self.last_response_metadata = {
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "status_code": "local",
            "usage": {"prompt_tokens": self.last_request_metadata["total_input_token_count"]},
        }
        try:
            return stable_rank_scores(request, scores)
        except RerankingError as exc:
            raise BgeRerankerV2M3Error("BGE score identities do not cover the request exactly") from exc


__all__ = [
    "BgeRerankerV2M3",
    "BgeRerankerV2M3Config",
    "BgeRerankerV2M3Error",
    "EXPECTED_MODEL_CLASS",
    "EXPECTED_TOKENIZER_CLASS",
    "MAX_LENGTH",
    "MODEL_ID",
    "MODEL_REVISION",
    "MODEL_WEIGHT_SHA256",
    "PROJECTION_MAX_CHARS",
    "PYTHON_VERSION",
    "TOKENIZERS_VERSION",
    "TORCH_VERSION",
    "TRANSFORMERS_VERSION",
    "verify_pinned_local_model",
]
