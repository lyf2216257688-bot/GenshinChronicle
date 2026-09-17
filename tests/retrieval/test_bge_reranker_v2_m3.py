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
    PROJECTION_MAX_CHARS,
    _validate_runtime_versions,
    verify_pinned_local_model,
)
from genshin_corpus.retrieval.reranking import (
    RerankCandidate,
    RerankRequest,
    RerankScore,
    RerankingError,
    project_reranker_text,
    stable_rank_scores,
)


class _Scalar:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value

    def sum(self):
        return self

    def float(self):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self


class _Tensor:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, index):
        value = self.values[index]
        return _Tensor(value) if isinstance(value, list) else _Scalar(value)

    def to(self, _device):
        return self

    def sum(self):
        return _Scalar(sum(self.values))

    def reshape(self, *_shape):
        def flatten(values):
            for value in values:
                if isinstance(value, list):
                    yield from flatten(value)
                else:
                    yield value

        return _Tensor(list(flatten(self.values)))

    def numel(self):
        return len(self.reshape(-1).values)


class XLMRobertaTokenizerFast:
    padding_side = "left"

    def __init__(self):
        self.calls = []

    def __call__(self, pairs, *, padding, truncation, return_tensors, max_length=None):
        self.calls.append({
            "pairs": pairs,
            "padding": padding,
            "truncation": truncation,
            "return_tensors": return_tensors,
            "max_length": max_length,
        })
        full_length = len(pairs[0][0]) + len(pairs[0][1]) + 3
        actual_length = min(full_length, max_length) if truncation else full_length
        if return_tensors is None:
            return {"input_ids": [[1] * actual_length]}
        return {
            "input_ids": _Tensor([[1] * actual_length]),
            "attention_mask": _Tensor([[1] * actual_length]),
        }


class _Parameter:
    device = "cuda:0"
    dtype = "torch.float16"


class XLMRobertaForSequenceClassification:
    def __init__(self, logits):
        self.logits = list(logits)
        self.calls = []
        self.to_calls = []
        self.eval_called = False

    def parameters(self):
        return iter([_Parameter()])

    def eval(self):
        self.eval_called = True
        return self

    def to(self, device):
        self.to_calls.append(device)
        return self

    def __call__(self, **inputs):
        self.calls.append(inputs)
        return types.SimpleNamespace(logits=_Tensor(self.logits.pop(0)))


class _InferenceMode:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _request(*texts: str) -> RerankRequest:
    return RerankRequest(
        "Q001",
        "query",
        tuple(RerankCandidate(f"u{index}", text, index) for index, text in enumerate(texts, 1)),
    )


class BgeRerankerV2M3Tests(unittest.TestCase):
    def run_adapter(self, logits, *texts):
        tokenizer = XLMRobertaTokenizerFast()
        model = XLMRobertaForSequenceClassification([[value] for value in logits])
        adapter = BgeRerankerV2M3(BgeRerankerV2M3Config(Path(MODEL_REVISION)), tokenizer=tokenizer, model=model)
        fake_torch = types.SimpleNamespace(inference_mode=lambda: _InferenceMode())
        with patch.dict(sys.modules, {"torch": fake_torch}):
            result = adapter.rerank(_request(*texts))
        return adapter, tokenizer, model, result

    def test_raw_positive_and_negative_logits_are_preserved_without_sigmoid(self):
        _, _, _, result = self.run_adapter([-3.25, 2.5], "negative", "positive")
        self.assertEqual([(row.unit_id, row.rerank_score) for row in result], [("u2", 2.5), ("u1", -3.25)])

    def test_exactly_one_logit_and_finite_score_are_required(self):
        with self.assertRaisesRegex(BgeRerankerV2M3Error, "exactly one scalar"):
            self.run_adapter([[1.0, 2.0]], "text")
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(BgeRerankerV2M3Error, "non-finite"):
                self.run_adapter([value], "text")

    def test_ties_use_original_hybrid_rank_deterministically(self):
        _, _, _, result = self.run_adapter([1.0, 1.0, 1.0], "a", "b", "c")
        self.assertEqual([(row.unit_id, row.rerank_rank) for row in result], [("u1", 1), ("u2", 2), ("u3", 3)])

    def test_pair_token_audit_records_truncation_and_total_input_tokens(self):
        adapter, tokenizer, model, _ = self.run_adapter([0.25, -0.5], "x" * 3000, "short")
        audits = adapter.last_request_metadata["candidate_token_audit"]
        self.assertEqual(audits[0]["unit_id"], "u1")
        self.assertEqual(audits[0]["full_pair_token_count"], 3008)
        self.assertEqual(audits[0]["actual_token_count"], MAX_LENGTH)
        self.assertTrue(audits[0]["truncated"])
        self.assertEqual(audits[0]["truncated_token_count"], 960)
        self.assertFalse(audits[1]["truncated"])
        self.assertEqual(adapter.last_request_metadata["truncated_count"], 1)
        self.assertEqual(
            adapter.last_request_metadata["total_input_token_count"],
            sum(row["actual_token_count"] for row in audits),
        )
        self.assertEqual(adapter.last_request_metadata["batch_size"], 1)
        self.assertEqual(adapter.last_request_metadata["batch_count"], 2)
        self.assertEqual(adapter.last_request_metadata["max_length"], MAX_LENGTH)
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(call["pairs"][0][0] == "query" for call in tokenizer.calls))

    def test_config_pins_identity_fp16_cuda_batch_one_and_2048(self):
        identity = BgeRerankerV2M3Config(Path(MODEL_REVISION)).identity_projection()
        self.assertEqual(identity["model"], MODEL_ID)
        self.assertEqual(identity["revision"], MODEL_REVISION)
        self.assertEqual(identity["model_weight_sha256"], MODEL_WEIGHT_SHA256)
        self.assertEqual(identity["dtype"], "float16")
        self.assertEqual(identity["device"], "cuda")
        self.assertEqual(identity["batch_size"], 1)
        self.assertEqual(identity["max_length"], 2048)
        for kwargs in (
            {"revision": "0" * 40},
            {"model_weight_sha256": "0" * 64},
            {"dtype": "float32"},
            {"device": "cpu"},
            {"batch_size": 2},
            {"max_length": 1024},
            {"projection_max_chars": 5999},
        ):
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
                evidence = verify_pinned_local_model(root)
                self.assertEqual(evidence["model_path"], str(root.resolve()))
                self.assertEqual(evidence["model_weight"]["sha256"], actual_hash)
                weight.write_bytes(b"tampered")
                with self.assertRaisesRegex(BgeRerankerV2M3Error, "SHA-256 mismatch"):
                    verify_pinned_local_model(root)
            with self.assertRaisesRegex(BgeRerankerV2M3Error, "revision-named"):
                verify_pinned_local_model(Path(temporary))
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def test_runtime_versions_are_exact(self):
        torch_module = types.SimpleNamespace(__version__="2.11.0+cu128")
        transformers_module = types.SimpleNamespace(__version__="4.39.1")
        tokenizers_module = types.SimpleNamespace(__version__="0.15.2")
        with patch("genshin_corpus.retrieval.bge_reranker_v2_m3.platform.python_version", return_value="3.12.10"):
            _validate_runtime_versions(torch_module, transformers_module, tokenizers_module)
            torch_module.__version__ = "2.11.0"
            with self.assertRaisesRegex(BgeRerankerV2M3Error, "version mismatch"):
                _validate_runtime_versions(torch_module, transformers_module, tokenizers_module)

    def test_expected_runtime_classes_are_required(self):
        with self.assertRaisesRegex(BgeRerankerV2M3Error, "tokenizer class mismatch"):
            BgeRerankerV2M3(BgeRerankerV2M3Config(Path(MODEL_REVISION)), tokenizer=object())
        wrong_model = types.SimpleNamespace(parameters=lambda: iter([_Parameter()]))
        with self.assertRaisesRegex(BgeRerankerV2M3Error, "model class mismatch"):
            BgeRerankerV2M3(BgeRerankerV2M3Config(Path(MODEL_REVISION)), model=wrong_model)

    def test_loader_is_local_only_fp16_and_has_no_extra_loader_options(self):
        tokenizer = XLMRobertaTokenizerFast()
        model = XLMRobertaForSequenceClassification([])

        class AutoTokenizer:
            calls = []

            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                cls.calls.append((args, kwargs))
                return tokenizer

        class AutoModelForSequenceClassification:
            calls = []

            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                cls.calls.append((args, kwargs))
                return model

        fake_torch = types.SimpleNamespace(
            __version__="2.11.0+cu128",
            float16=object(),
            cuda=types.SimpleNamespace(is_available=lambda: True),
        )
        fake_transformers = types.ModuleType("transformers")
        fake_transformers.__version__ = "4.39.1"
        fake_transformers.AutoTokenizer = AutoTokenizer
        fake_transformers.AutoModelForSequenceClassification = AutoModelForSequenceClassification
        fake_tokenizers = types.SimpleNamespace(__version__="0.15.2")
        adapter = BgeRerankerV2M3(BgeRerankerV2M3Config(Path(MODEL_REVISION)))
        with (
            patch("genshin_corpus.retrieval.bge_reranker_v2_m3.verify_pinned_local_model"),
            patch("genshin_corpus.retrieval.bge_reranker_v2_m3.platform.python_version", return_value="3.12.10"),
            patch.dict(sys.modules, {"torch": fake_torch, "transformers": fake_transformers, "tokenizers": fake_tokenizers}),
        ):
            adapter._lazy_load()
        self.assertEqual(AutoTokenizer.calls[0][1], {"local_files_only": True})
        model_kwargs = AutoModelForSequenceClassification.calls[0][1]
        self.assertEqual(set(model_kwargs), {"local_files_only", "torch_dtype"})
        self.assertTrue(model_kwargs["local_files_only"])
        self.assertIs(model_kwargs["torch_dtype"], fake_torch.float16)
        self.assertEqual(model.to_calls, ["cuda"])

    def test_missing_and_duplicate_result_identity_fail_closed(self):
        request = _request("a", "b")
        with self.assertRaises(RerankingError):
            stable_rank_scores(request, [RerankScore("u1", 1, 1.0, 0)])
        with self.assertRaises(RerankingError):
            stable_rank_scores(request, [RerankScore("u1", 1, 1.0, 0), RerankScore("u1", 1, 0.5, 0)])

    def test_projection_contract_remains_title_section_speaker_text_at_6000(self):
        text, audit = project_reranker_text({
            "record_title": "Title value",
            "section_name": "Section value",
            "speaker": "Speaker value",
            "retrieval_visible_text": "x" * 7000,
        })
        self.assertTrue(text.startswith("Title: Title value\nSection: Section value\nSpeaker: Speaker value\nText: "))
        self.assertEqual(len(text), PROJECTION_MAX_CHARS)
        self.assertEqual(audit["max_chars"], PROJECTION_MAX_CHARS)
        self.assertTrue(audit["truncated"])

    def test_source_contains_no_online_or_provider_fallback(self):
        source = Path("src/genshin_corpus/retrieval/bge_reranker_v2_m3.py").read_text(encoding="utf-8")
        self.assertIn("local_files_only=True", source)
        self.assertNotIn("DashScope", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("requests", source)


if __name__ == "__main__":
    unittest.main()
