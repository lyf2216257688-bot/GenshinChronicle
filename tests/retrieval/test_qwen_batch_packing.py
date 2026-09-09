import gzip
import hashlib
import json
import shutil
import unittest
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.qwen_batch_embedding import qwen_batch_record_for_unit
from genshin_corpus.retrieval.qwen_batch_packing import (
    QwenBatchPackingConfig,
    QwenBatchPackingError,
    pack_qwen_batch_units,
)


class QwenBatchPackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.qwen-batch-packing-test")
        if self.root.exists():
            shutil.rmtree(self.root)

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    @staticmethod
    def _unit(index: int, text: str = "同一检索文本") -> dict[str, object]:
        return {"unit_id": f"unit-{index}", "retrieval_visible_text": text, "source_order": [0, index]}

    def test_request_count_shards_are_deterministic_and_preserve_mapping(self) -> None:
        units = [self._unit(index) for index in range(5)]
        config = QwenBatchPackingConfig(max_requests_per_file=2, max_file_bytes=1_000_000, max_row_bytes=1_000_000)
        first = pack_qwen_batch_units(units, retrieval_unit_build_identity="fixture-build", expected_count=5, output_root=self.root / "first", config=config)
        second = pack_qwen_batch_units(units, retrieval_unit_build_identity="fixture-build", expected_count=5, output_root=self.root / "second", config=config)
        self.assertEqual([item["request_count"] for item in first["shards"]], [2, 2, 1])
        self.assertEqual(first["shards"], second["shards"])
        self.assertEqual((self.root / "first/artifacts/ru_mapping.jsonl.gz").read_bytes(), (self.root / "second/artifacts/ru_mapping.jsonl.gz").read_bytes())
        rows = (self.root / "first/artifacts/shards/shard-00000.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(row)["custom_id"] for row in rows], [
            qwen_batch_record_for_unit(retrieval_unit_build_identity="fixture-build", unit=units[index])["custom_id"]
            for index in range(2)
        ])

    def test_one_shard_descriptor_uses_the_physical_zero_index_and_path(self) -> None:
        result = pack_qwen_batch_units(
            [self._unit(0)],
            retrieval_unit_build_identity="fixture-build",
            expected_count=1,
            output_root=self.root / "one-shard",
        )
        shard = result["shards"][0]
        self.assertEqual(shard["shard_index"], 0)
        self.assertEqual(shard["path"], "artifacts/shards/shard-00000.jsonl")
        physical = self.root / "one-shard" / shard["path"]
        self.assertEqual(shard["byte_count"], physical.stat().st_size)
        self.assertEqual(shard["sha256"], hashlib.sha256(physical.read_bytes()).hexdigest())
        with gzip.open(self.root / "one-shard/artifacts/ru_mapping.jsonl.gz", "rt", encoding="utf-8") as handle:
            mapping = json.loads(handle.readline())
        self.assertEqual(mapping["shard_index"], 0)
        self.assertEqual(mapping["shard_id"], shard["shard_id"])
        self.assertEqual(mapping["row_index"], 0)

    def test_two_shards_and_mapping_have_matching_indexes_ids_paths_and_rows(self) -> None:
        result = pack_qwen_batch_units(
            [self._unit(index) for index in range(3)],
            retrieval_unit_build_identity="fixture-build",
            expected_count=3,
            output_root=self.root / "two-shards",
            config=QwenBatchPackingConfig(max_requests_per_file=2, max_file_bytes=1_000_000, max_row_bytes=1_000_000),
        )
        shards = result["shards"]
        self.assertEqual([(item["shard_index"], item["path"], item["request_count"]) for item in shards], [
            (0, "artifacts/shards/shard-00000.jsonl", 2),
            (1, "artifacts/shards/shard-00001.jsonl", 1),
        ])
        for shard in shards:
            physical = self.root / "two-shards" / shard["path"]
            self.assertTrue(physical.is_file())
            self.assertEqual(shard["byte_count"], physical.stat().st_size)
            self.assertEqual(shard["sha256"], hashlib.sha256(physical.read_bytes()).hexdigest())
        with gzip.open(self.root / "two-shards/artifacts/ru_mapping.jsonl.gz", "rt", encoding="utf-8") as handle:
            mappings = [json.loads(line) for line in handle]
        self.assertEqual([(row["shard_index"], row["row_index"]) for row in mappings], [(0, 0), (0, 1), (1, 0)])
        self.assertEqual([row["shard_id"] for row in mappings], [shards[0]["shard_id"], shards[0]["shard_id"], shards[1]["shard_id"]])

    def test_file_byte_boundary_starts_a_new_shard_before_overflow(self) -> None:
        units = [self._unit(index, "相同文本") for index in range(3)]
        row_bytes = len(canonical_json_bytes(qwen_batch_record_for_unit(retrieval_unit_build_identity="fixture-build", unit=units[0])) + b"\n")
        result = pack_qwen_batch_units(
            units,
            retrieval_unit_build_identity="fixture-build",
            expected_count=3,
            output_root=self.root / "byte-boundary",
            config=QwenBatchPackingConfig(max_requests_per_file=10, max_file_bytes=row_bytes * 2 - 1, max_row_bytes=row_bytes),
        )
        self.assertEqual([item["request_count"] for item in result["shards"]], [1, 1, 1])
        self.assertTrue(all(item["byte_count"] <= row_bytes * 2 - 1 for item in result["shards"]))

    def test_single_request_over_row_limit_fails_closed(self) -> None:
        unit = self._unit(0)
        row_bytes = len(canonical_json_bytes(qwen_batch_record_for_unit(retrieval_unit_build_identity="fixture-build", unit=unit)) + b"\n")
        with self.assertRaisesRegex(QwenBatchPackingError, "row or file limit"):
            pack_qwen_batch_units(
                [unit],
                retrieval_unit_build_identity="fixture-build",
                expected_count=1,
                output_root=self.root / "too-large",
                config=QwenBatchPackingConfig(max_requests_per_file=1, max_file_bytes=row_bytes, max_row_bytes=row_bytes - 1),
            )


if __name__ == "__main__":
    unittest.main()
