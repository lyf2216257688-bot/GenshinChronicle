"""Offline local adapter for Alibaba-NLP/gte-multilingual-reranker-base."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any

from .reranking import RerankCandidate, RerankRequest, RerankScore, stable_rank_scores


MODEL_ID = "Alibaba-NLP/gte-multilingual-reranker-base"
MODEL_REVISION = "8215cf04918ba6f7b6a62bb44238ce2953d8831c"
CUSTOM_CODE_REVISION = "40ced75c3017eb27626c9d4ea981bde21a2662f4"
MAX_MODEL_TOKENS = 8192
PINNED_MODEL_FILE_SHA256 = {
    "config.json": "995730781d157e147c13ccdfe0eb20a0875c486b6c4de8c97f0bbd845549dbc0",
    "model.safetensors": "10ebaa49322dd7e01a13a91c49810939e3f91f231aceaa47fdf0cab3083954f6",
    "README.md": "3d010454d2949f8188bb3496951594e19c35076652e04d203cbd7e20ef3a3871",
    "special_tokens_map.json": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
    "tokenizer_config.json": "6f00514620aff01ba8b7291b2394e98daca5be264cb743805232d9ae27494b2a",
    "tokenizer.json": "d6f76fe13d42f80dcee0cb86a1aeb5f14f8909bb8a8782f7a4a4ad76697ef164",
}
PINNED_CUSTOM_CODE_SHA256 = {
    "configuration.py": "3411088045ffb8a9a0aa9936eae275896b39983a2ee5b08f091b44e6289e4fe4",
    "modeling.py": "374670b416fcc82f081c9cd28b5fd61c2bd91bbe18eb4798fcc48a81f9c250a0",
}


class GteRerankerError(ValueError):
    """Raised when the pinned local GTE runtime cannot score safely."""


@dataclass(frozen=True)
class GteMultilingualRerankerConfig:
    model_path: Path
    custom_code_path: Path
    revision: str = MODEL_REVISION
    custom_code_revision: str = CUSTOM_CODE_REVISION
    dtype: str = "float16"
    device: str = "cuda"
    max_length: int = MAX_MODEL_TOKENS
    batch_size: int = 1

    def __post_init__(self) -> None:
        if self.revision != MODEL_REVISION or self.custom_code_revision != CUSTOM_CODE_REVISION:
            raise GteRerankerError("GTE revisions are not the pinned immutable revisions")
        if self.dtype != "float16" or self.device != "cuda":
            raise GteRerankerError("GTE targeted probe requires FP16 CUDA")
        if not isinstance(self.max_length, int) or isinstance(self.max_length, bool) or not 1 <= self.max_length <= MAX_MODEL_TOKENS:
            raise GteRerankerError("GTE max_length must be between 1 and 8192")
        if self.batch_size != 1:
            raise GteRerankerError("GTE targeted probe requires batch size 1")

    def identity_projection(self) -> dict[str, Any]:
        return {
            "model": MODEL_ID,
            "revision": self.revision,
            "custom_code_revision": self.custom_code_revision,
            "dtype": self.dtype,
            "device": self.device,
            "max_length": self.max_length,
            "batch_size": self.batch_size,
            "scoring_contract": "tokenizer_pair_sequence_classification_single_logit",
        }


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise GteRerankerError("GTE returned a non-finite score")
    return result


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_pinned_local_files(model_path: Path, custom_code_path: Path) -> dict[str, Any]:
    model_path = Path(model_path)
    custom_code_path = Path(custom_code_path)
    if model_path.name != MODEL_REVISION or custom_code_path.name != CUSTOM_CODE_REVISION:
        raise GteRerankerError("local GTE paths are not revision-named pinned snapshots")
    inventory: dict[str, dict[str, Any]] = {"model": {}, "custom_code": {}}
    for group, root, expected in (
        ("model", model_path, PINNED_MODEL_FILE_SHA256),
        ("custom_code", custom_code_path, PINNED_CUSTOM_CODE_SHA256),
    ):
        for name, expected_hash in expected.items():
            path = root / name
            if not path.is_file():
                raise GteRerankerError(f"pinned GTE file is missing: {path}")
            actual = _sha256_file(path)
            if actual != expected_hash:
                raise GteRerankerError(f"pinned GTE file hash mismatch: {path}")
            inventory[group][name] = {
                "path": str(path.resolve()),
                "byte_count": path.stat().st_size,
                "sha256": actual,
            }
    return inventory


def prepare_pinned_model_stage(model_path: Path, custom_code_path: Path, stage_path: Path) -> dict[str, Any]:
    """Create a local-only Transformers load view without changing source bytes."""

    inventory = verify_pinned_local_files(model_path, custom_code_path)
    stage_path = Path(stage_path)
    if stage_path.exists():
        raise FileExistsError(f"refusing to overwrite GTE staging path: {stage_path}")
    stage_path.mkdir(parents=True)
    for name in PINNED_MODEL_FILE_SHA256:
        if name == "model.safetensors":
            os.link(Path(model_path) / name, stage_path / name)
        else:
            shutil.copy2(Path(model_path) / name, stage_path / name)
    for name in PINNED_CUSTOM_CODE_SHA256:
        shutil.copy2(Path(custom_code_path) / name, stage_path / name)
    config_path = stage_path / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    auto_map = config.get("auto_map")
    if not isinstance(auto_map, dict) or not auto_map:
        raise GteRerankerError("pinned GTE config lacks auto_map")
    patched: dict[str, Any] = {}
    for key, value in auto_map.items():
        patched[key] = value.split("--", 1)[1] if isinstance(value, str) and "--" in value else value
    config["auto_map"] = patched
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "source_inventory": inventory,
        "stage_path": str(stage_path.resolve()),
        "stage_weight_hardlinked": True,
        "patched_auto_map": patched,
    }


class GteMultilingualReranker:
    """Direct Transformers adapter; it never has a network fallback."""

    def __init__(self, config: GteMultilingualRerankerConfig, *, tokenizer: Any | None = None, model: Any | None = None) -> None:
        self.config = config
        self._tokenizer = tokenizer
        self._model = model
        self.last_request_metadata: dict[str, Any] = {}
        self.last_response_metadata: dict[str, Any] = {}
        if tokenizer is not None:
            tokenizer.padding_side = "right"
        if model is not None:
            self._validate_model(model)

    def _validate_model(self, model: Any) -> None:
        devices = {str(parameter.device) for parameter in model.parameters()}
        if devices != {"cuda:0"}:
            raise GteRerankerError(f"GTE model is not fully resident on cuda:0: {sorted(devices)}")
        model.eval()

    def _lazy_load(self) -> None:
        if self._tokenizer is not None and self._model is not None:
            return
        if not self.config.model_path.is_dir() or not self.config.custom_code_path.is_dir():
            raise GteRerankerError("pinned local GTE model or custom code path is missing")
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            if not torch.cuda.is_available():
                raise GteRerankerError("CUDA is unavailable for GTE")
            self._tokenizer = AutoTokenizer.from_pretrained(str(self.config.model_path), local_files_only=True)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(self.config.model_path),
                trust_remote_code=True,
                local_files_only=True,
                torch_dtype=torch.float16,
            )
            self._model.to("cuda")
            self._validate_model(self._model)
        except GteRerankerError:
            raise
        except Exception as exc:
            raise GteRerankerError("pinned local GTE failed to load offline on CUDA") from exc

    def runtime_identity(self) -> dict[str, Any]:
        return self.config.identity_projection()

    def rerank(self, request: RerankRequest) -> tuple[RerankScore, ...]:
        if not isinstance(request, RerankRequest):
            raise GteRerankerError("GTE rerank request has invalid type")
        self._lazy_load()
        if self._tokenizer is None or self._model is None:
            raise GteRerankerError("GTE runtime is not initialized")
        try:
            import torch
            scores: list[RerankScore] = []
            audits: list[dict[str, Any]] = []
            for candidate in request.candidates:
                pair = [[request.query, candidate.text]]
                full = self._tokenizer(pair, padding=False, truncation=False, return_tensors=None)
                full_length = len(full["input_ids"][0])
                encoded = self._tokenizer(
                    pair,
                    padding=True,
                    truncation=True,
                    max_length=self.config.max_length,
                    return_tensors="pt",
                )
                actual_length = int(encoded["attention_mask"][0].sum().item())
                truncated = actual_length < full_length
                gpu_inputs = {key: value.to("cuda") for key, value in encoded.items()}
                with torch.inference_mode():
                    logits = self._model(**gpu_inputs, return_dict=True).logits.view(-1)
                if logits.numel() != 1:
                    raise GteRerankerError("GTE returned an unexpected logit shape")
                score = _finite(logits[0].float().detach().cpu().item())
                scores.append(RerankScore(candidate.unit_id, candidate.original_rank, score, 0))
                audits.append({
                    "unit_id": candidate.unit_id,
                    "full_token_count": full_length,
                    "actual_token_count": actual_length,
                    "max_length": self.config.max_length,
                    "truncated": truncated,
                    "truncation": "tokenizer_pair_right" if truncated else None,
                })
            self.last_request_metadata = {
                "candidate_count": len(scores),
                "batch_size": 1,
                "max_length": self.config.max_length,
                "truncated_count": sum(1 for item in audits if item["truncated"]),
                "total_input_tokens": sum(int(item["actual_token_count"]) for item in audits),
                "candidate_token_audit": audits,
            }
            self.last_response_metadata = {
                "model": MODEL_ID,
                "status_code": "local",
                "usage": {"prompt_tokens": self.last_request_metadata["total_input_tokens"]},
            }
            return stable_rank_scores(request, scores)
        except GteRerankerError:
            raise
        except Exception as exc:
            raise GteRerankerError("GTE local scoring failed") from exc


__all__ = [
    "CUSTOM_CODE_REVISION",
    "GteMultilingualReranker",
    "GteMultilingualRerankerConfig",
    "GteRerankerError",
    "MAX_MODEL_TOKENS",
    "MODEL_ID",
    "MODEL_REVISION",
    "PINNED_CUSTOM_CODE_SHA256",
    "PINNED_MODEL_FILE_SHA256",
    "prepare_pinned_model_stage",
    "verify_pinned_local_files",
]
