import gzip
import hashlib
import json
import shutil
import unittest
from pathlib import Path

import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.candidate_retrieval import dense_candidates
from genshin_corpus.retrieval.qwen_batch_embedding import (
    QWEN_BATCH_EMBEDDINGS_PATH,
    QWEN_BATCH_JSONL_SCHEMA_VERSION,
    QwenBatchEmbeddingConfig,
    QwenBatchEmbeddingError,
    build_qwen_batch_jsonl,
    build_qwen_batch_records,
    materialize_qwen_batch_results,
)
from genshin_corpus.retrieval.qwen_embedding import QWEN_EMBEDDING_DIMENSION, QWEN_EMBEDDING_MODEL_ID


class QwenBatchEmbeddingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.qwen-batch-embedding-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        (self.root / "ru/artifacts").mkdir(parents=True)
        units = [self._unit("u0", "第一条检索文本", 0), self._unit("u1", "第二条检索文本", 1)]
        unit_body = gzip.compress(b"".join(canonical_json_bytes(unit) + b"\n" for unit in units), mtime=0)
        empty_body = gzip.compress(b"", mtime=0)
        failure_body = canonical_json_bytes({"status": "clear", "failure_count": 0})
        for relative, body in (("retrieval_units.jsonl.gz", unit_body), ("skip_ledger.jsonl.gz", empty_body)):
            (self.root / "ru/artifacts" / relative).write_bytes(body)
        (self.root / "ru/metadata").mkdir()
        (self.root / "ru/metadata/failure_ledger.json").write_bytes(failure_body)
        (self.root / "ru/metadata/manifest.json").write_bytes(canonical_json_bytes({
            "schema_version": "phase04-retrieval-unit-build-0.1",
            "status": "complete",
            "build_identity": "ru-build-qwen-batch-fixture",
            "canonical_input": {
                "canonical_run_id": "fixture",
                "canonical_input_identity": "fixture",
                "canonical_schema_version": "phase03-draft-0.1",
            },
            "artifacts": {
                "retrieval_units": self._descriptor("artifacts/retrieval_units.jsonl.gz", unit_body, 2),
                "skip_ledger": self._descriptor("artifacts/skip_ledger.jsonl.gz", empty_body, 0),
                "failure_ledger": self._descriptor("metadata/failure_ledger.json", failure_body),
            },
        }))
        self.config = QwenBatchEmbeddingConfig(self.root / "ru/metadata/manifest.json", ("u0", "u1"))

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    @staticmethod
    def _descriptor(path: str, body: bytes, count: int | None = None) -> dict[str, object]:
        result: dict[str, object] = {"path": path, "sha256": hashlib.sha256(body).hexdigest(), "byte_count": len(body)}
        if count is not None:
            result["row_count"] = count
        return result

    @staticmethod
    def _unit(unit_id: str, text: str, ordinal: int) -> dict[str, object]:
        return {
            "schema_version": "phase04-retrieval-unit-0.1",
            "unit_id": unit_id,
            "retrieval_visible_text": text,
            "source_order": [0, 0, 0, ordinal],
            "source": {"canonical_address": {"source_identity_key": "s", "record_id": "r", "section_ordinal": 0, "component_observation_key": "c", "component_ordinal": 0, "canonical_unit_ordinal": ordinal, "canonical_unit_kind": "rich_text", "parsed_json_pointer": f"/x/{ordinal}"}, "lineage": {"raw_refs": []}, "record_context": {}, "provenance": {}, "content_role": {}},
            "content_type": "rich_text",
            "nested_selector": {},
            "fragment_selector": {"kind": "whole"},
            "structure": {},
        }

    @staticmethod
    def _vector(index: int, *, dimension: int = QWEN_EMBEDDING_DIMENSION, value: float = 1.0) -> list[float]:
        vector = [0.0] * dimension
        vector[index] = value
        return vector

    def _success_row(self, record: dict[str, object], index: int) -> dict[str, object]:
        return {
            "custom_id": record["custom_id"],
            "error": None,
            "response": {
                "status_code": 200,
                "body": {
                    "model": QWEN_EMBEDDING_MODEL_ID,
                    "data": [{"index": 0, "embedding": self._vector(index)}],
                },
            },
        }

    def _write_results(self, rows: list[dict[str, object]], name: str = "result.jsonl") -> Path:
        path = self.root / name
        # Provider output is not a canonical project artifact; retain NaN here
        # so the parser's non-finite-vector rejection is exercised.
        path.write_bytes(b"".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True).encode("utf-8") + b"\n"
            for row in rows
        ))
        return path

    def test_builds_deterministic_document_jsonl_with_exact_visible_text(self) -> None:
        first = build_qwen_batch_jsonl(self.config)
        (self.root / "input.jsonl").write_bytes(first)
        self.assertEqual(first, build_qwen_batch_jsonl(self.config))
        records = [json.loads(line) for line in first.decode("utf-8").splitlines()]
        self.assertEqual([record["body"]["input"] for record in records], ["第一条检索文本", "第二条检索文本"])
        self.assertEqual([record["method"] for record in records], ["POST", "POST"])
        self.assertEqual([record["url"] for record in records], [QWEN_BATCH_EMBEDDINGS_PATH] * 2)
        self.assertEqual([record["body"]["model"] for record in records], [QWEN_EMBEDDING_MODEL_ID] * 2)
        self.assertEqual([record["body"]["dimensions"] for record in records], [2048, 2048])
        self.assertEqual([record["body"]["encoding_format"] for record in records], ["float", "float"])
        self.assertEqual(len({record["custom_id"] for record in records}), 2)
        self.assertNotIn("text_type", first.decode("utf-8"))
        self.assertNotIn("test-secret", first.decode("utf-8"))

    def test_successful_local_result_materializes_existing_dense_conventions(self) -> None:
        records = build_qwen_batch_records(self.config)
        (self.root / "input.jsonl").write_bytes(build_qwen_batch_jsonl(self.config))
        result = materialize_qwen_batch_results(
            self.config,
            self._write_results([self._success_row(records[1], 1), self._success_row(records[0], 0)]),
            self.root / "materialized",
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["batch_2048_wire_acceptance"], "unknown")
        manifest_path = self.root / "materialized/dense/metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["embedding_dimension"], 2048)
        self.assertEqual(manifest["dtype"], "float32")
        self.assertEqual(manifest["normalization"], "L2")
        self.assertIsNone(manifest["model_sha256"])
        self.assertEqual(manifest["remote_provider_provenance"]["kind"], "remote_provider")
        self.assertEqual(manifest["remote_provider_provenance"]["batch_2048_wire_acceptance"], "unknown")
        candidates = dense_candidates(manifest_path, np.asarray(self._vector(0), dtype=np.float32), top_k=2)
        self.assertEqual([item["unit_id"] for item in candidates], ["u0", "u1"])
        persisted = (self.root / "materialized/metadata/batch_materialization.json").read_text(encoding="utf-8")
        self.assertNotIn("test-secret", persisted)

    def test_missing_duplicate_and_unknown_custom_ids_fail_closed(self) -> None:
        records = build_qwen_batch_records(self.config)
        cases = {
            "missing": [self._success_row(records[0], 0)],
            "duplicate": [self._success_row(records[0], 0), self._success_row(records[0], 0)],
            "unknown": [{**self._success_row(records[0], 0), "custom_id": "qwen37-document-unknown"}],
        }
        for name, rows in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(QwenBatchEmbeddingError):
                    materialize_qwen_batch_results(self.config, self._write_results(rows, f"{name}.jsonl"), self.root / name)
                self.assertFalse((self.root / name).exists())

    def test_provider_error_and_invalid_embeddings_fail_closed(self) -> None:
        records = build_qwen_batch_records(self.config)
        provider_error = {"custom_id": records[0]["custom_id"], "response": None, "error": {"code": "BadRequest"}}
        cases = {
            "provider-error": [provider_error, self._success_row(records[1], 1)],
            "malformed": [{"custom_id": records[0]["custom_id"], "response": {"status_code": 200, "body": {"data": []}}}, self._success_row(records[1], 1)],
            "wrong-dimension": [{**self._success_row(records[0], 0), "response": {"status_code": 200, "body": {"model": QWEN_EMBEDDING_MODEL_ID, "data": [{"index": 0, "embedding": self._vector(0, dimension=1024)}]}}}, self._success_row(records[1], 1)],
            "non-finite": [{**self._success_row(records[0], 0), "response": {"status_code": 200, "body": {"model": QWEN_EMBEDDING_MODEL_ID, "data": [{"index": 0, "embedding": [float("nan")] + self._vector(1)[1:]}]}}}, self._success_row(records[1], 1)],
        }
        for name, rows in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(QwenBatchEmbeddingError):
                    materialize_qwen_batch_results(self.config, self._write_results(rows, f"{name}.jsonl"), self.root / name)

    def test_non_2048_configuration_is_rejected(self) -> None:
        with self.assertRaisesRegex(QwenBatchEmbeddingError, "2048"):
            QwenBatchEmbeddingConfig(self.config.retrieval_unit_manifest_path, self.config.document_unit_ids, dimension=1024)
        self.assertEqual(QWEN_BATCH_JSONL_SCHEMA_VERSION, "phase04-rag-qwen37-embedding-batch-jsonl-0.1")


if __name__ == "__main__":
    unittest.main()
