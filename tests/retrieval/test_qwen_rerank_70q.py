from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from genshin_corpus.generation.generation import GenerationResult, write_generation_result
from genshin_corpus.retrieval import qwen_rerank_70q as runner


class QwenRerank70QDurabilityTests(unittest.TestCase):
    def test_failed_generation_result_is_persisted_before_stop(self) -> None:
        directory = Path(".local/test-qwen-rerank-70q-persistence")
        directory.mkdir(exist_ok=True)
        with patch("genshin_corpus.generation.generation.atomic_write", side_effect=lambda path, body: Path(path).write_bytes(body)):
            result = GenerationResult(
                execution_status="provider_error", answer_text=None,
                citation_validation=None, semantic_request_identity="a" * 64,
                execution_config_identity="b" * 64,
                request_audit={"question_id": "Q045"},
                provider_audit={"attempts": [{"status_code": 503, "provider_code": "ModelUnavailable", "retry_decision": "stop"}]},
            )
            artifact = write_generation_result(directory, result)
            persisted = json.loads((directory / "generation_result.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["sha256"], runner._sha256_file(directory / "generation_result.json"))
            self.assertEqual(persisted["result"]["execution_status"], "provider_error")
            self.assertEqual(persisted["audit"]["provider_execution"]["attempts"][0]["status_code"], 503)
        (directory / "generation_result.json").unlink(missing_ok=True)
        directory.rmdir()

    def test_continuation_rejects_mismatching_r1_evidence(self) -> None:
        root = Path(".local/test-qwen-rerank-70q-mismatch")
        root.mkdir(exist_ok=True)
        try:
            (root / "metadata").mkdir()
            (root / "metadata/manifest.json").write_text(json.dumps({"run_identity": "wrong"}), encoding="utf-8")
            with self.assertRaises(runner.QwenRerank70QError):
                runner._verify_r1_prefix(root)
        finally:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file(): path.unlink()
                elif path.is_dir(): path.rmdir()
            root.rmdir()

    def test_continuation_start_is_q045(self) -> None:
        self.assertEqual(runner.QUESTION_IDS[44], "Q045")
        self.assertEqual(runner.continue_qwen_rerank_70q.__doc__.split("beginning with ", 1)[1].split(".", 1)[0], "Q045")

    def test_continuation_dispatches_only_the_suffix_without_provider_prefix(self) -> None:
        with patch.object(runner, "_verify_r1_prefix", return_value={"manifest_sha256": "m"}), patch.object(
            runner, "run_qwen_rerank_70q", return_value={"status": "complete"}
        ) as execute:
            runner.continue_qwen_rerank_70q(output_root=Path(".local/continuation-test"), r1_root=Path("r1"))
        self.assertEqual(execute.call_args.kwargs["start_index"], 44)
        self.assertEqual(execute.call_args.kwargs["r1_manifest_sha256"], "m")


if __name__ == "__main__":
    unittest.main()
