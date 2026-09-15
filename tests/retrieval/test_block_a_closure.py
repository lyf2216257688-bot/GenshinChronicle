from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import genshin_corpus.retrieval.block_a_closure as closure
from genshin_corpus.retrieval.block_a_closure import (
    BlockAClosureError,
    BlockAQualityGateConfig,
    _reranker_preflight,
    _materialize_required_outputs,
    _verify_descriptor,
    run_block_a_closure,
)
from genshin_corpus.retrieval.candidate_retrieval import FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION


class BlockAClosureTests(unittest.TestCase):
    def test_operating_parameters_are_bound_and_validated(self) -> None:
        first = BlockAQualityGateConfig(candidate_supply_depth=21, rerank_depth=21, final_top_n=20)
        second = BlockAQualityGateConfig(candidate_supply_depth=22, rerank_depth=22, final_top_n=20)
        self.assertNotEqual(first.identity, second.identity)
        self.assertEqual(first.projection()["lexical"]["schema_version"], FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION)
        with self.assertRaises(BlockAClosureError):
            BlockAQualityGateConfig(candidate_supply_depth=20, rerank_depth=21)
        with self.assertRaises(BlockAClosureError):
            BlockAQualityGateConfig(candidate_supply_depth=20, rerank_depth=20, final_top_n=21)

    def test_provider_free_reranker_preflight_is_deterministic(self) -> None:
        units = {
            f"u{index}": {
                "unit_id": f"u{index}",
                "retrieval_visible_text": f"正文 {index}",
                "source": {"record_context": {"record_title": f"标题 {index}", "section_name": "章节"}},
                "structure": {"dialogue": {"speaker": "派蒙"}},
            }
            for index in range(3)
        }
        result = _reranker_preflight(units, BlockAQualityGateConfig(final_top_n=2))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["request_candidate_count"], 3)
        self.assertEqual(result["final_ids"], result["fused_ids"][:2])

    def test_existing_output_root_is_never_overwritten(self) -> None:
        root = Path(".local") / "test-block-a-closure-existing-root"
        root.mkdir(parents=True, exist_ok=True)
        try:
            with self.assertRaises(FileExistsError):
                run_block_a_closure(output_root=root)
        finally:
            root.rmdir()

    def test_required_outputs_are_materialized_and_bound_before_manifest(self) -> None:
        root = Path(".local") / "test-block-a-closure-finalization"
        root.mkdir(parents=True, exist_ok=True)
        try:
            descriptors = _materialize_required_outputs(root, [{"question_id": "Q001"}], {"status": "pass"})
            self.assertEqual(set(descriptors), {"retrieval", "control"})
            self.assertEqual(descriptors["retrieval"]["byte_count"], (root / "retrieval/q001-q070.jsonl").stat().st_size)
            self.assertEqual(descriptors["control"]["byte_count"], (root / "control/legacy-q001.json").stat().st_size)
            self.assertFalse((root / "metadata/manifest.json").exists())
            (root / "control/legacy-q001.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(BlockAClosureError):
                _verify_descriptor(root / "control/legacy-q001.json", descriptors["control"])
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_required_output_failure_cannot_create_complete_manifest(self) -> None:
        root = Path(".local") / "test-block-a-closure-finalization-failure"
        root.mkdir(parents=True, exist_ok=True)
        real_write = closure._immutable_write

        def fail_control(path, body):
            if Path(path).name == "legacy-q001.json":
                raise OSError("injected control write failure")
            return real_write(path, body)

        try:
            with patch.object(closure, "_immutable_write", side_effect=fail_control):
                with self.assertRaises(OSError):
                    _materialize_required_outputs(root, [{"question_id": "Q001"}], {"status": "pass"})
            self.assertFalse((root / "metadata/manifest.json").exists())
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
