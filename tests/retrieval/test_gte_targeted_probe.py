from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from uuid import uuid4

from genshin_corpus.retrieval.gte_multilingual_reranker import (
    GteMultilingualReranker,
    GteMultilingualRerankerConfig,
    GteRerankerError,
    prepare_pinned_model_stage,
)
from genshin_corpus.retrieval.gte_targeted_probe import _carrier_delta, _prepare_run_root
from genshin_corpus.retrieval.reranking import RerankCandidate, RerankRequest


REVISION = "8215cf04918ba6f7b6a62bb44238ce2953d8831c"
CODE_REVISION = "40ced75c3017eb27626c9d4ea981bde21a2662f4"


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

    def view(self, *_shape):
        return self

    def numel(self):
        return len(self.values)


class _Tokenizer:
    padding_side = "left"

    def __call__(self, pairs, *, padding, truncation, return_tensors, max_length=None):
        length = len(pairs[0][0]) + len(pairs[0][1]) + 3
        actual = min(length, max_length) if truncation else length
        if return_tensors is None:
            return {"input_ids": [[1] * actual]}
        return {"input_ids": _Tensor([[1] * actual]), "attention_mask": _Tensor([[1] * actual])}


class _Parameter:
    device = "cuda:0"


class _Model:
    def __init__(self):
        self.calls = []

    def parameters(self):
        return iter([_Parameter()])

    def eval(self):
        return self

    def __call__(self, **inputs):
        self.calls.append(inputs)
        return types.SimpleNamespace(logits=_Tensor([float(len(self.calls))]))


class _InferenceMode:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class GteAdapterTests(unittest.TestCase):
    def test_config_is_pinned_to_fp16_cuda_batch_one_and_8192(self):
        config = GteMultilingualRerankerConfig(Path("model"), Path("code"))
        self.assertEqual(config.identity_projection()["max_length"], 8192)
        self.assertEqual(config.identity_projection()["scoring_contract"], "tokenizer_pair_sequence_classification_single_logit")
        with self.assertRaises(GteRerankerError):
            GteMultilingualRerankerConfig(Path("model"), Path("code"), batch_size=2)
        with self.assertRaises(GteRerankerError):
            GteMultilingualRerankerConfig(Path("model"), Path("code"), revision="0" * 40)

    def test_pair_scoring_records_every_token_length_and_uses_stable_order(self):
        tokenizer = _Tokenizer()
        model = _Model()
        adapter = GteMultilingualReranker(
            GteMultilingualRerankerConfig(Path("model"), Path("code"), max_length=15),
            tokenizer=tokenizer,
            model=model,
        )
        request = RerankRequest("Q001", "query", (
            RerankCandidate("u1", "x" * 20, 1),
            RerankCandidate("u2", "short", 2),
        ))
        fake_torch = types.SimpleNamespace(inference_mode=lambda: _InferenceMode())
        with patch.dict(sys.modules, {"torch": fake_torch}):
            result = adapter.rerank(request)
        self.assertEqual([row.unit_id for row in result], ["u2", "u1"])
        self.assertEqual(adapter.last_request_metadata["candidate_count"], 2)
        self.assertEqual(adapter.last_request_metadata["truncated_count"], 1)
        self.assertEqual([row["actual_token_count"] for row in adapter.last_request_metadata["candidate_token_audit"]], [15, 13])
        self.assertEqual(tokenizer.padding_side, "right")
        self.assertNotIn("instruction", adapter.runtime_identity())

    def test_pinned_stage_is_local_and_refuses_overwrite(self):
        root = Path(".local/gte-targeted-test-scratch") / f"case-{uuid4().hex}"
        root.mkdir(parents=True)
        try:
            model = root / REVISION
            code = root / CODE_REVISION
            stage = root / "stage"
            model.mkdir()
            code.mkdir()
            model_files = {
                "config.json": json.dumps({"auto_map": {"AutoConfig": "repo--configuration.NewConfig"}}),
                "model.safetensors": "weight",
                "README.md": "readme",
                "special_tokens_map.json": "{}",
                "tokenizer_config.json": "{}",
                "tokenizer.json": "{}",
            }
            code_files = {"configuration.py": "CONFIG = 1", "modeling.py": "MODEL = 1"}
            for name, body in model_files.items():
                (model / name).write_text(body, encoding="utf-8")
            for name, body in code_files.items():
                (code / name).write_text(body, encoding="utf-8")
            model_hashes = {name: hashlib.sha256((model / name).read_bytes()).hexdigest() for name in model_files}
            code_hashes = {name: hashlib.sha256((code / name).read_bytes()).hexdigest() for name in code_files}
            with patch("genshin_corpus.retrieval.gte_multilingual_reranker.PINNED_MODEL_FILE_SHA256", model_hashes), patch("genshin_corpus.retrieval.gte_multilingual_reranker.PINNED_CUSTOM_CODE_SHA256", code_hashes):
                evidence = prepare_pinned_model_stage(model, code, stage)
                self.assertTrue(evidence["stage_weight_hardlinked"])
                staged_config = json.loads((stage / "config.json").read_text(encoding="utf-8"))
                self.assertEqual(staged_config["auto_map"]["AutoConfig"], "configuration.NewConfig")
                self.assertEqual((stage / "model.safetensors").stat().st_ino, (model / "model.safetensors").stat().st_ino)
                with self.assertRaises(FileExistsError):
                    prepare_pinned_model_stage(model, code, stage)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TargetedRunnerTests(unittest.TestCase):
    @staticmethod
    def rows(unit_id: str):
        hybrid = [{"unit_id": unit_id, "rank": 1}]
        reranked = [{"unit_id": unit_id, "rerank_rank": 1}]
        fused = [{"unit_id": unit_id, "fusion_rank": 1}]
        return hybrid, reranked, fused

    def test_carrier_judgment_is_unknown_without_binding_and_lost_on_packet_loss(self):
        hybrid, reranked, fused = self.rows("u1")
        judgment, rows = _carrier_delta(None, hybrid, reranked, fused, reranked, fused, {"u1"}, {"u1"})
        self.assertEqual((judgment, rows), ("UNKNOWN", []))
        judgment, rows = _carrier_delta(
            [{"unit_id": "u1", "binding_role": "decisive", "carrier_label": "UNKNOWN"}],
            hybrid, reranked, fused, reranked, fused, {"u1"}, set(),
        )
        self.assertEqual(judgment, "lost")
        self.assertTrue(rows[0]["online_packet_present"])
        self.assertFalse(rows[0]["gte_packet_present"])

    def test_run_root_requires_exact_pre_modification_snapshot_only(self):
        root = Path(".local/gte-targeted-test-scratch") / f"case-{uuid4().hex}"
        root.mkdir(parents=True)
        try:
            (root / "pre_modification_git_snapshot.txt").write_text("snapshot", encoding="utf-8")
            _prepare_run_root(root)
            (root / "ambiguous.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                _prepare_run_root(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_source_has_offline_guards_and_no_provider_or_generation_execution(self):
        adapter = Path("src/genshin_corpus/retrieval/gte_multilingual_reranker.py").read_text(encoding="utf-8")
        runner = Path("src/genshin_corpus/retrieval/gte_targeted_probe.py").read_text(encoding="utf-8")
        self.assertIn("local_files_only=True", adapter)
        self.assertIn('os.environ["HF_HUB_OFFLINE"] = "1"', runner)
        self.assertIn('os.environ["TRANSFORMERS_OFFLINE"] = "1"', runner)
        self.assertNotIn("DashScope", adapter)
        self.assertNotIn("Bailian", runner)
        self.assertNotIn("generate(", runner)


if __name__ == "__main__":
    unittest.main()
