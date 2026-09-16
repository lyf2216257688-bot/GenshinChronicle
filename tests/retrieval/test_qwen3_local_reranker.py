from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from genshin_corpus.retrieval.qwen3_local_reranker import (
    ASSISTANT_SUFFIX,
    DEFAULT_INSTRUCTION,
    MODEL_ID,
    PROMPT_ALGORITHM_VERSION,
    SYSTEM_PREFIX,
    Qwen3LocalReranker,
    Qwen3LocalRerankerConfig,
    Qwen3LocalRerankerError,
    build_prompt_parts,
    prompt_text,
    token_budget_prompt,
    token_budget_ids,
    verify_local_model_manifest,
    yes_probability,
)
from genshin_corpus.retrieval.reranking import RerankCandidate, RerankRequest


REVISION = "0123456789abcdef0123456789abcdef01234567"


class FakeTokenizer:
    padding_side = "right"

    def encode(self, text: str, *, add_special_tokens: bool = False):
        if text == "yes":
            return [1]
        if text == "no":
            return [2]
        return [1000 + ord(char) for char in text]

    def convert_tokens_to_ids(self, token: str):
        return {"yes": 1, "no": 2}.get(token, -1)

    def decode(self, ids, **kwargs):
        reverse = {1: "yes", 2: "no"}
        values = []
        for item in ids:
            item = int(item)
            values.append(reverse[item] if item in reverse else chr(item - 1000))
        return "".join(values)


class LocalQwen3RerankerTests(unittest.TestCase):
    def test_official_prompt_structure_is_exact_and_deterministic(self):
        prefix, content, suffix = build_prompt_parts("Q", "D")
        self.assertEqual(prefix, SYSTEM_PREFIX)
        self.assertEqual(suffix, ASSISTANT_SUFFIX)
        self.assertEqual(content, f"<Instruct>: {DEFAULT_INSTRUCTION}\n<Query>: Q\n<Document>: D")
        self.assertEqual(prompt_text("Q", "D"), prefix + content + suffix)
        self.assertIn('<|im_start|>system\nJudge whether the Document', prompt_text("Q", "D"))
        self.assertEqual(PROMPT_ALGORITHM_VERSION, "qwen3-reranker-official-yesno-v1")

    def test_left_padding_and_explicit_prefix_suffix_token_budget(self):
        tokenizer = FakeTokenizer()
        config = Qwen3LocalRerankerConfig(Path("model"), REVISION)
        adapter = Qwen3LocalReranker(config, tokenizer=tokenizer)
        self.assertEqual(tokenizer.padding_side, "left")
        text, audit = token_budget_prompt(tokenizer, "Q", "D" * 600, max_length=512)
        self.assertTrue(text.startswith(SYSTEM_PREFIX))
        self.assertTrue(text.endswith(ASSISTANT_SUFFIX))
        self.assertEqual(audit["actual_token_count"], 512)
        self.assertTrue(audit["truncated"])
        self.assertEqual(adapter.runtime_identity()["model"], MODEL_ID)
        ids, id_audit = token_budget_ids(tokenizer, "Q", "D" * 600, max_length=512)
        self.assertEqual(len(ids), id_audit["actual_token_count"])
        self.assertEqual(ids[: id_audit["prefix_token_count"]], tokenizer.encode(SYSTEM_PREFIX, add_special_tokens=False))

    def test_answer_tokens_are_single_token_round_trips(self):
        Qwen3LocalReranker(Qwen3LocalRerankerConfig(Path("model"), REVISION), tokenizer=FakeTokenizer())
        class Bad(FakeTokenizer):
            def encode(self, text, *, add_special_tokens=False):
                if text == "yes":
                    return [1, 3]
                return super().encode(text, add_special_tokens=add_special_tokens)
        with self.assertRaises(Qwen3LocalRerankerError):
            Qwen3LocalReranker(Qwen3LocalRerankerConfig(Path("model"), REVISION), tokenizer=Bad())

    def test_official_yes_probability_transformation(self):
        self.assertAlmostEqual(yes_probability(0.0, 0.0), 0.5)
        self.assertGreater(yes_probability(0.0, 2.0), 0.8)
        with self.assertRaises(Qwen3LocalRerankerError):
            yes_probability(float("nan"), 1.0)

    def test_complete_identity_stable_order_and_batch_size_one(self):
        tokenizer = FakeTokenizer()
        config = Qwen3LocalRerankerConfig(Path("model"), REVISION, batch_size=1, max_length=256)
        adapter = Qwen3LocalReranker(config, tokenizer=tokenizer)
        adapter._lazy_load = lambda: None
        calls = []
        adapter.score_input_ids = lambda input_ids, **kwargs: calls.append(list(input_ids)) or [float(len(input_ids) - index) for index in range(len(input_ids))]
        request = RerankRequest("Q1", "query", tuple(RerankCandidate(f"u{i}", f"doc {i}", i) for i in range(1, 4)))
        result = adapter.rerank(request)
        self.assertEqual([row.unit_id for row in result], ["u1", "u2", "u3"])
        self.assertEqual([len(batch) for batch in calls], [1, 1, 1])
        self.assertEqual(adapter.last_response_metadata["status_code"], "local")

    def test_local_missing_path_fails_closed_without_importing_torch(self):
        sys.modules.pop("torch", None)
        adapter = Qwen3LocalReranker(Qwen3LocalRerankerConfig(Path("does-not-exist"), REVISION))
        with self.assertRaises(Qwen3LocalRerankerError):
            adapter._lazy_load()
        self.assertNotIn("torch", sys.modules)

    def test_manifest_is_hash_verified_and_revision_bound(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            payload = root_path / "config.json"
            payload.write_text("{}", encoding="utf-8")
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            manifest_path = root_path / "manifest.json"
            manifest_path.write_text(json.dumps({"model": MODEL_ID, "revision": REVISION, "files": [{"path": "config.json", "sha256": digest}]}), encoding="utf-8")
            self.assertEqual(verify_local_model_manifest(manifest_path)["revision"], REVISION)
            payload.write_text("tampered", encoding="utf-8")
            with self.assertRaises(Qwen3LocalRerankerError):
                verify_local_model_manifest(manifest_path)

    def test_source_keeps_local_only_loading_and_has_no_remote_fallback(self):
        source = Path("src/genshin_corpus/retrieval/qwen3_local_reranker.py").read_text(encoding="utf-8")
        self.assertIn("local_files_only=True", source)
        self.assertNotIn("DashScope", source)
        self.assertNotIn("qwen3.7-text-rerank", source)


if __name__ == "__main__":
    unittest.main()
