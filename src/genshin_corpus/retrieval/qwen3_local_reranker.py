"""Dormant local Qwen3 reranker challenger.

This module is intentionally optional and is not imported by the production
retrieval package.  It implements the existing provider-neutral ``Reranker``
contract using the official Qwen3 yes/no causal-LM scoring algorithm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .reranking import RerankRequest, RerankScore, RerankingError, stable_rank_scores


MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
PROMPT_ALGORITHM_VERSION = "qwen3-reranker-official-yesno-v1"
RUNTIME_IDENTITY_VERSION = "phase04-qwen3-local-reranker-runtime-v1"
DEFAULT_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
SYSTEM_PREFIX = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
    "Note that the answer can only be \"yes\" or \"no\"."
    "<|im_end|>\n<|im_start|>user\n"
)
ASSISTANT_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
INSTRUCTION_LABEL = "<Instruct>: "
QUERY_LABEL = "<Query>: "
DOCUMENT_LABEL = "<Document>: "


class Qwen3LocalRerankerError(RerankingError):
    """Raised when local model materialization or scoring cannot be trusted."""


@dataclass(frozen=True)
class Qwen3LocalRerankerConfig:
    model_path: Path
    revision: str
    dtype: str = "float16"
    device: str = "cuda"
    attention_implementation: str = "sdpa"
    max_length: int = 8192
    batch_size: int = 1
    instruction: str = DEFAULT_INSTRUCTION
    projection_max_chars: int = 6000
    use_cache: bool = False
    use_logits_to_keep: bool = True
    truncation: str = "right_content"

    def __post_init__(self) -> None:
        if not isinstance(self.model_path, Path):
            object.__setattr__(self, "model_path", Path(self.model_path))
        if not self.revision or not isinstance(self.revision, str):
            raise Qwen3LocalRerankerError("model revision must be a non-empty immutable commit")
        if len(self.revision) != 40 or any(char not in "0123456789abcdefABCDEF" for char in self.revision):
            raise Qwen3LocalRerankerError("model revision must be a 40-character commit SHA")
        if self.dtype not in {"float16", "bfloat16"}:
            raise Qwen3LocalRerankerError("local dtype must be float16 or bfloat16")
        if self.device != "cuda":
            raise Qwen3LocalRerankerError("local challenger requires explicit CUDA device")
        if self.use_cache is not False:
            raise Qwen3LocalRerankerError("local challenger requires use_cache=False")
        if self.attention_implementation not in {"sdpa", "eager"}:
            raise Qwen3LocalRerankerError("attention implementation must be sdpa or eager")
        for name in ("max_length", "batch_size", "projection_max_chars"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise Qwen3LocalRerankerError(f"{name} must be a positive integer")
        if not self.instruction:
            raise Qwen3LocalRerankerError("instruction must be non-empty")
        if self.truncation != "right_content":
            raise Qwen3LocalRerankerError("only deterministic right-content truncation is supported")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "identity_version": RUNTIME_IDENTITY_VERSION,
            "model": MODEL_ID,
            "revision": self.revision.lower(),
            "model_path": str(self.model_path.resolve()),
            "dtype": self.dtype,
            "device": self.device,
            "attention_implementation": self.attention_implementation,
            "max_length": self.max_length,
            "batch_size": self.batch_size,
            "instruction": self.instruction,
            "instruction_version": PROMPT_ALGORITHM_VERSION,
            "projection_max_chars": self.projection_max_chars,
            "use_cache": self.use_cache,
            "use_logits_to_keep": self.use_logits_to_keep,
            "truncation": self.truncation,
        }

    @property
    def identity(self) -> str:
        return hashlib.sha256(_canonical_json(self.identity_projection())).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_prompt_parts(query: str, document: str, *, instruction: str = DEFAULT_INSTRUCTION) -> tuple[str, str, str]:
    """Return the exact official prefix, content, and assistant suffix."""

    if not isinstance(query, str) or not query:
        raise Qwen3LocalRerankerError("query must be a non-empty string")
    if not isinstance(document, str) or not document:
        raise Qwen3LocalRerankerError("document must be a non-empty string")
    if not isinstance(instruction, str) or not instruction:
        raise Qwen3LocalRerankerError("instruction must be a non-empty string")
    content = (
        f"{INSTRUCTION_LABEL}{instruction}\n"
        f"{QUERY_LABEL}{query}\n"
        f"{DOCUMENT_LABEL}{document}"
    )
    return SYSTEM_PREFIX, content, ASSISTANT_SUFFIX


def prompt_text(query: str, document: str, *, instruction: str = DEFAULT_INSTRUCTION) -> str:
    prefix, content, suffix = build_prompt_parts(query, document, instruction=instruction)
    return prefix + content + suffix


def _encode(tokenizer: Any, text: str) -> list[int]:
    encoded = tokenizer.encode(text, add_special_tokens=False)
    if not isinstance(encoded, Sequence) or isinstance(encoded, (str, bytes)):
        raise Qwen3LocalRerankerError("tokenizer returned an invalid token sequence")
    values = [int(item) for item in encoded]
    if any(item < 0 for item in values):
        raise Qwen3LocalRerankerError("tokenizer returned an invalid token ID")
    return values


def _resolve_answer_token(tokenizer: Any, token: str) -> int:
    ids = _encode(tokenizer, token)
    converted = tokenizer.convert_tokens_to_ids(token)
    if len(ids) != 1 or not isinstance(converted, int) or converted != ids[0]:
        raise Qwen3LocalRerankerError(f"answer token {token!r} is not exactly one tokenizer token")
    decoded = tokenizer.decode([ids[0]], skip_special_tokens=False, clean_up_tokenization_spaces=False)
    if decoded != token:
        raise Qwen3LocalRerankerError(f"answer token {token!r} does not round-trip exactly")
    return ids[0]


def yes_probability(no_logit: float, yes_logit: float) -> float:
    """Apply the official two-class log-softmax and return ``P(yes)``."""

    import math

    values = [float(no_logit), float(yes_logit)]
    if any(not math.isfinite(value) for value in values):
        raise Qwen3LocalRerankerError("answer logits must be finite")
    pivot = max(values)
    denominator = sum(math.exp(value - pivot) for value in values)
    return math.exp(values[1] - pivot) / denominator


def token_budget_prompt(
    tokenizer: Any,
    query: str,
    document: str,
    *,
    max_length: int,
    instruction: str = DEFAULT_INSTRUCTION,
) -> tuple[str, dict[str, Any]]:
    """Preserve prefix/suffix and truncate only the content token stream."""

    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
        raise Qwen3LocalRerankerError("max_length must be a positive integer")
    prefix, content, suffix = build_prompt_parts(query, document, instruction=instruction)
    input_ids, audit = token_budget_ids(tokenizer, query, document, max_length=max_length, instruction=instruction)
    audit["text"] = prefix + tokenizer.decode(input_ids[len(_encode(tokenizer, prefix)):-len(_encode(tokenizer, suffix))], skip_special_tokens=False, clean_up_tokenization_spaces=False) + suffix
    return audit.pop("text"), audit


def token_budget_ids(
    tokenizer: Any,
    query: str,
    document: str,
    *,
    max_length: int,
    instruction: str = DEFAULT_INSTRUCTION,
) -> tuple[list[int], dict[str, Any]]:
    """Return exact model input IDs without decode/re-tokenize drift."""

    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
        raise Qwen3LocalRerankerError("max_length must be a positive integer")
    prefix, content, suffix = build_prompt_parts(query, document, instruction=instruction)
    prefix_ids = _encode(tokenizer, prefix)
    content_ids = _encode(tokenizer, content)
    suffix_ids = _encode(tokenizer, suffix)
    available = max_length - len(prefix_ids) - len(suffix_ids)
    if available <= 0:
        raise Qwen3LocalRerankerError("max_length cannot fit the official prompt prefix and suffix")
    kept = content_ids[:available]
    truncated = len(kept) < len(content_ids)
    return prefix_ids + kept + suffix_ids, {
        "max_length": max_length,
        "prefix_token_count": len(prefix_ids),
        "content_token_count": len(content_ids),
        "kept_content_token_count": len(kept),
        "suffix_token_count": len(suffix_ids),
        "actual_token_count": len(prefix_ids) + len(kept) + len(suffix_ids),
        "truncated": truncated,
        "truncation": "right_content",
    }


def verify_local_model_manifest(manifest_path: Path) -> dict[str, Any]:
    """Verify an immutable local snapshot manifest before loading any model."""

    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise Qwen3LocalRerankerError("local model manifest is unreadable") from exc
    if not isinstance(manifest, Mapping) or manifest.get("model") != MODEL_ID:
        raise Qwen3LocalRerankerError("local model manifest model identity mismatch")
    revision = manifest.get("revision")
    if not isinstance(revision, str) or len(revision) != 40 or any(c not in "0123456789abcdefABCDEF" for c in revision):
        raise Qwen3LocalRerankerError("local model manifest revision is not an immutable commit SHA")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise Qwen3LocalRerankerError("local model manifest has no file inventory")
    root = path.parent
    for item in files:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
            raise Qwen3LocalRerankerError("local model manifest file entry is malformed")
        target = (root / item["path"]).resolve()
        if not target.is_file() or not target.is_relative_to(root.resolve()):
            raise Qwen3LocalRerankerError("local model manifest file is missing or escapes model root")
        if _sha256_file(target) != item["sha256"].lower():
            raise Qwen3LocalRerankerError(f"local model file hash mismatch: {item['path']}")
    return dict(manifest)


class Qwen3LocalReranker:
    """Explicit local-only direct Transformers adapter."""

    def __init__(self, config: Qwen3LocalRerankerConfig, *, tokenizer: Any | None = None, model: Any | None = None) -> None:
        self.config = config
        self.last_response_metadata: dict[str, Any] = {}
        self.last_request_metadata: dict[str, Any] = {}
        self._tokenizer = tokenizer
        self._model = model
        self._yes_token_id: int | None = None
        self._no_token_id: int | None = None
        if tokenizer is not None:
            self._validate_tokenizer(tokenizer)
        if model is not None:
            self._validate_cuda_model(model)

    @classmethod
    def from_local_manifest(cls, config: Qwen3LocalRerankerConfig, manifest_path: Path) -> "Qwen3LocalReranker":
        manifest = verify_local_model_manifest(Path(manifest_path))
        if manifest.get("revision", "").lower() != config.revision.lower():
            raise Qwen3LocalRerankerError("manifest revision does not match runtime configuration")
        if config.model_path.resolve() != Path(manifest_path).resolve().parent:
            raise Qwen3LocalRerankerError("runtime model path does not match manifest root")
        return cls(config)

    def _lazy_load(self) -> None:
        if self._tokenizer is not None and self._model is not None:
            return
        if not self.config.model_path.is_dir():
            raise Qwen3LocalRerankerError("local model path is missing")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except Exception as exc:
            raise Qwen3LocalRerankerError("local Qwen3 runtime dependencies are unavailable") from exc
        if not torch.cuda.is_available():
            raise Qwen3LocalRerankerError("CUDA is unavailable for the local reranker")
        dtype = torch.float16 if self.config.dtype == "float16" else torch.bfloat16
        try:
            tokenizer = AutoTokenizer.from_pretrained(str(self.config.model_path), local_files_only=True, padding_side="left")
            model = AutoModelForCausalLM.from_pretrained(
                str(self.config.model_path),
                local_files_only=True,
                torch_dtype=dtype,
                attn_implementation=self.config.attention_implementation,
                use_safetensors=True,
            )
            model.to("cuda")
        except Exception as exc:
            raise Qwen3LocalRerankerError("local Qwen3 model failed to load on CUDA") from exc
        self._tokenizer, self._model = tokenizer, model
        self._validate_tokenizer(tokenizer)
        self._validate_cuda_model(model)

    def _validate_tokenizer(self, tokenizer: Any) -> None:
        tokenizer.padding_side = "left"
        self._yes_token_id = _resolve_answer_token(tokenizer, "yes")
        self._no_token_id = _resolve_answer_token(tokenizer, "no")

    @staticmethod
    def _validate_cuda_model(model: Any) -> None:
        for parameter in model.parameters():
            device = str(parameter.device)
            if not device.startswith("cuda") or device.startswith("meta"):
                raise Qwen3LocalRerankerError("local model is not fully resident on CUDA")
        model.eval()

    def runtime_identity(self) -> dict[str, Any]:
        return self.config.identity_projection()

    def _forward(self, inputs: Mapping[str, Any], *, logits_to_keep: bool) -> Any:
        if self._model is None:
            raise Qwen3LocalRerankerError("local model is not loaded")
        kwargs = dict(inputs)
        kwargs["return_dict"] = True
        kwargs["use_cache"] = self.config.use_cache
        if logits_to_keep:
            kwargs["logits_to_keep"] = 1
        try:
            return self._model(**kwargs)
        except TypeError as exc:
            if logits_to_keep:
                raise Qwen3LocalRerankerError("pinned Transformers model does not support logits_to_keep=1") from exc
            raise

    def score_prompts(self, prompts: Sequence[str], *, logits_to_keep: bool | None = None) -> list[float]:
        self._lazy_load()
        if self._tokenizer is None or self._model is None or self._yes_token_id is None or self._no_token_id is None:
            raise Qwen3LocalRerankerError("local scorer is not initialized")
        if not prompts:
            return []
        try:
            import torch
            encoded = self._tokenizer(list(prompts), padding=True, return_tensors="pt", add_special_tokens=False)
            encoded = {key: value.to("cuda") for key, value in encoded.items()}
            return self._score_encoded(encoded, logits_to_keep=logits_to_keep)
        except Qwen3LocalRerankerError:
            raise
        except Exception as exc:
            raise Qwen3LocalRerankerError("local Qwen3 scoring failed") from exc

    def _score_encoded(self, encoded: Mapping[str, Any], *, logits_to_keep: bool | None = None) -> list[float]:
        if self._model is None or self._yes_token_id is None or self._no_token_id is None:
            raise Qwen3LocalRerankerError("local scorer is not initialized")
        try:
            import torch

            optimized = self.config.use_logits_to_keep if logits_to_keep is None else logits_to_keep
            with torch.inference_mode():
                output = self._forward(encoded, logits_to_keep=optimized)
                logits = output.logits
                if logits.ndim == 3:
                    logits = logits[:, -1, :]
                elif logits.ndim != 2:
                    raise Qwen3LocalRerankerError("local model returned unexpected logits shape")
                pair_logits = torch.stack((logits[:, self._no_token_id], logits[:, self._yes_token_id]), dim=-1)
                scores = torch.log_softmax(pair_logits, dim=-1).exp()[:, 1]
                values = [float(value.detach().cpu()) for value in scores]
        except Qwen3LocalRerankerError:
            raise
        except Exception as exc:
            raise Qwen3LocalRerankerError("local Qwen3 scoring failed") from exc
        if any(not (value == value and abs(value) != float("inf")) for value in values):
            raise Qwen3LocalRerankerError("local Qwen3 score is non-finite")
        return values

    def score_input_ids(self, input_ids: Sequence[Sequence[int]], *, logits_to_keep: bool | None = None) -> list[float]:
        self._lazy_load()
        if self._tokenizer is None:
            raise Qwen3LocalRerankerError("local tokenizer is not loaded")
        if not input_ids:
            return []
        try:
            encoded = self._tokenizer.pad(
                [{"input_ids": list(ids)} for ids in input_ids],
                padding=True,
                return_tensors="pt",
            )
            encoded = {key: value.to("cuda") for key, value in encoded.items()}
        except Exception as exc:
            raise Qwen3LocalRerankerError("local input-ID padding failed") from exc
        return self._score_encoded(encoded, logits_to_keep=logits_to_keep)

    def rerank(self, request: RerankRequest) -> tuple[RerankScore, ...]:
        if not isinstance(request, RerankRequest):
            raise Qwen3LocalRerankerError("rerank request has invalid type")
        self._lazy_load()
        if self._tokenizer is None:
            raise Qwen3LocalRerankerError("local tokenizer is not loaded")
        input_ids: list[list[int]] = []
        accounting: list[dict[str, Any]] = []
        for candidate in request.candidates:
            ids, audit = token_budget_ids(
                self._tokenizer,
                request.query,
                candidate.text,
                max_length=self.config.max_length,
                instruction=self.config.instruction,
            )
            input_ids.append(ids)
            accounting.append({"unit_id": candidate.unit_id, **audit})
        scores: list[float] = []
        for start in range(0, len(input_ids), self.config.batch_size):
            scores.extend(self.score_input_ids(input_ids[start : start + self.config.batch_size]))
        self.last_request_metadata = {
            "candidate_count": len(scores),
            "batch_size": self.config.batch_size,
            "batches": (len(scores) + self.config.batch_size - 1) // self.config.batch_size,
            "max_length": self.config.max_length,
            "truncated_count": sum(1 for item in accounting if item["truncated"]),
            "total_input_tokens": sum(int(item["actual_token_count"]) for item in accounting),
            "candidate_token_audit": accounting,
        }
        self.last_response_metadata = {
            "model": MODEL_ID,
            "status_code": "local",
            "usage": {"prompt_tokens": self.last_request_metadata["total_input_tokens"]},
        }
        raw = [RerankScore(candidate.unit_id, candidate.original_rank, score, 0) for candidate, score in zip(request.candidates, scores, strict=True)]
        return stable_rank_scores(request, raw)


__all__ = [
    "ASSISTANT_SUFFIX",
    "DEFAULT_INSTRUCTION",
    "MODEL_ID",
    "PROMPT_ALGORITHM_VERSION",
    "Qwen3LocalReranker",
    "Qwen3LocalRerankerConfig",
    "Qwen3LocalRerankerError",
    "RUNTIME_IDENTITY_VERSION",
    "SYSTEM_PREFIX",
    "build_prompt_parts",
    "prompt_text",
    "token_budget_prompt",
    "token_budget_ids",
    "yes_probability",
    "verify_local_model_manifest",
]
