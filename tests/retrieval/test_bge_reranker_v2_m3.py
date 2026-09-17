from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sys
import types
import unittest
from unittest.mock import patch
from uuid import uuid4

from genshin_corpus.retrieval.bge_reranker_v2_m3 import (
    BgeRerankerV2M3,
    BgeRerankerV2M3Config,
    BgeRerankerV2M3Error,
    MAX_LENGTH,
    MODEL_ID,
    MODEL_REVISION,
    MODEL_WEIGHT_SHA256,
    _validate_runtime_versions,
    verify_pinned_local_model,
)
from genshin_corpus.retrieval.reranking import RerankCandidate, RerankRequest


class _Scalar:
    def __init__(self, value): self.value = value
    def item(self): return self.value
    def sum(self): return self
    def float(self): return self
    def detach(self): return self
    def cpu(self): return self


class _Tensor:
    def __init__(self, values): self.values = values
    def __getitem__(self, index):
        value = self.values[index]
        return _Tensor(value) if isinstance(value, list) else _Scalar(value)
    def to(self, _device): return self
    def sum(self): return _Scalar(sum(self.values))
    def reshape(self, *_shape):
        values = self.values
        while values and isinstance(values[0], list): values = values[0]
        return _Tensor(values)
    def numel(self): return len(self.reshape(-1).values)


class XLMRobertaTokenizerFast:
    padding_side = "left"
    def __init__(self): self.calls = []
    def __call__(self, pairs, *, padding, truncation, return_tensors, max_length=None):
        self.calls.append((pairs, padding, truncation, return_tensors, max_length))
        length = min(len(pairs[0][0]) + len(pairs[0][1]) + 3, max_length or 999999)
        if return_tensors is None: return {"input_ids": [[1] * length]}
        return {"input_ids": _Tensor([[1] * length]), "attention_mask": _Tensor([[1] * length])}


class _Parameter:
    device = "cuda:0"
    dtype = "torch.float16"


class XLMRobertaForSequenceClassification:
    def __init__(self, logits): self.logits, self.to_calls, self.eval_called = list(logits), [], False
    def parameters(self): return iter([_Parameter()])
    def eval(self): self.eval_called = True; return self
    def to(self, device): self.to_calls.append(device); return self
    def __call__(self, **_inputs): return types.SimpleNamespace(logits=_Tensor(self.logits.pop(0)))


class _InferenceMode:
    def __enter__(self): return self
    def __exit__(self, *_args): return False


def _request(*texts: str) -> RerankRequest:
    return RerankRequest("Q001", "query", tuple(
        RerankCandidate(f"u{index}", text, index) for index, text in enumerate(texts, 1)
    ))


class BgeRerankerV2M3Tests(unittest.TestCase):
    def test_config_pins_exact_identity_and_rejects_runtime_changes(self):
        identity = BgeRerankerV2M3Config(Path(MODEL_REVISION)).identity_projection()
        self.assertEqual((identity["model"], identity["revision"], identity["dtype"], identity["device"]), (MODEL_ID, MODEL_REVISION, "float16", "cuda"))
        for kwargs in ({"revision": "0" * 40}, {"model_weight_sha256": "0" * 64}, {"device": "cpu"}, {"batch_size": 2}, {"max_length": 1024}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BgeRerankerV2M3Error):
                BgeRerankerV2M3Config(Path(MODEL_REVISION), **kwargs)

    def test_local_root_and_weight_hash_are_verified(self):
        temporary = Path(".local/bge-reranker-v2-m3-test-scratch") / uuid4().hex
        temporary.mkdir(parents=True)
        try:
            root = temporary / MODEL_REVISION
            root.mkdir()
            weight = root / "model.safetensors"
            weight.write_bytes(b"pinned-test-weight")
            actual_hash = hashlib.sha256(weight.read_bytes()).hexdigest()
            with patch("genshin_corpus.retrieval.bge_reranker_v2_m3.MODEL_WEIGHT_SHA256", actual_hash):
                self.assertEqual(verify_pinned_local_model(root)["model_weight"]["sha256"], actual_hash)
                weight.write_bytes(b"tampered")
                with self.assertRaisesRegex(BgeRerankerV2M3Error, "SHA-256 mismatch"):
                    verify_pinned_local_model(root)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def test_runtime_versions_and_local_only_loader_are_required(self):
        torch_module = types.SimpleNamespace(__version__="2.11.0+cu128", float16=object(), cuda=types.SimpleNamespace(is_available=lambda: True))
        transformers_module = types.ModuleType("transformers")
        transformers_module.__version__ = "4.39.1"
        tokenizers_module = types.SimpleNamespace(__version__="0.15.2")
        tokenizer, model = XLMRobertaTokenizerFast(), XLMRobertaForSequenceClassification([])

        class AutoTokenizer:
            @classmethod
            def from_pretrained(cls, *_args, **kwargs):
                self.assertEqual(kwargs, {"local_files_only": True})
                return tokenizer

        class AutoModelForSequenceClassification:
            @classmethod
            def from_pretrained(cls, *_args, **kwargs):
                self.assertEqual(kwargs, {"local_files_only": True, "torch_dtype": torch_module.float16})
                return model

        transformers_module.AutoTokenizer = AutoTokenizer
        transformers_module.AutoModelForSequenceClassification = AutoModelForSequenceClassification
        with patch("genshin_corpus.retrieval.bge_reranker_v2_m3.verify_pinned_local_model"), patch(
            "genshin_corpus.retrieval.bge_reranker_v2_m3.platform.python_version", return_value="3.12.10"
        ), patch.dict(sys.modules, {"torch": torch_module, "transformers": transformers_module, "tokenizers": tokenizers_module}):
            adapter = BgeRerankerV2M3(BgeRerankerV2M3Config(Path(MODEL_REVISION)))
            adapter._lazy_load()
        self.assertEqual(model.to_calls, ["cuda"])
        self.assertEqual(tokenizer.padding_side, "right")

    def test_raw_logits_are_stably_ranked_without_provider_or_sigmoid(self):
        tokenizer = XLMRobertaTokenizerFast()
        model = XLMRobertaForSequenceClassification([[-3.25], [2.5]])
        adapter = BgeRerankerV2M3(BgeRerankerV2M3Config(Path(MODEL_REVISION)), tokenizer=tokenizer, model=model)
        with patch.dict(sys.modules, {"torch": types.SimpleNamespace(inference_mode=lambda: _InferenceMode())}):
            result = adapter.rerank(_request("negative", "positive"))
        self.assertEqual([(row.unit_id, row.rerank_score) for row in result], [("u2", 2.5), ("u1", -3.25)])
        self.assertEqual(adapter.last_response_metadata["status_code"], "local")

    def test_source_has_no_provider_or_online_fallback(self):
        source = Path("src/genshin_corpus/retrieval/bge_reranker_v2_m3.py").read_text(encoding="utf-8")
        self.assertIn("local_files_only=True", source)
        self.assertNotIn("DashScope", source)
        self.assertNotIn("urllib", source)


if __name__ == "__main__":
    unittest.main()
