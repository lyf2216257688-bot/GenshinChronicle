from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.evidence_assembly import EvidenceAssemblyConfig
from genshin_corpus.retrieval.production_materialization import (
    ProductionMaterializationError,
    _materialize_fixture,
    materialize_production,
    preflight_production_inputs,
)


class ProductionMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.rag-production-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        fixture = Path(__file__).parents[1] / "fixtures" / "retrieval" / "canonical-rag-w1-record.json"
        self.record = json.loads(fixture.read_text(encoding="utf-8"))
        self.record_path = self.root / "canonical-record.json"
        record_body = canonical_json_bytes(self.record)
        self.record_path.write_bytes(record_body)
        manifest = {
            "status": "complete", "canonical_run_id": "production-fixture",
            "source": "mihoyo_obc", "locale": "zh-cn", "input_record_count": 1,
            "accounted_record_count": 1, "input_integrity_failure_count": 0,
            "dependencies": {"canonical_versions": {"schema_version": "phase03-draft-0.1"}},
            "records": [{"record_id": self.record["record_id"], "canonical_record_path": str(self.record_path),
                         "canonical_record_sha256": hashlib.sha256(record_body).hexdigest(), "canonical_status": "canonical"}],
        }
        self.canonical_manifest = self.root / "canonical" / "metadata" / "manifest.json"
        self.canonical_manifest.parent.mkdir(parents=True)
        self.canonical_manifest.write_bytes(canonical_json_bytes(manifest))
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        self.model_file = self.model_dir / "model.safetensors"
        self.model_file.write_bytes(b"fixture-model")
        self.model_sha = hashlib.sha256(self.model_file.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def test_preflight_is_read_only_and_validates_canonical_and_model(self) -> None:
        result = preflight_production_inputs(self.canonical_manifest, self.model_dir, expected_model_sha256=self.model_sha, require_dense_runtime=False)
        self.assertEqual(result["canonical_record_count"], 1)
        self.assertEqual(result["model_sha256"], self.model_sha)

    def test_runtime_failure_happens_before_output_root_creation(self) -> None:
        output = self.root / "blocked-production"
        with self.assertRaisesRegex(ProductionMaterializationError, "Dense runtime root"):
            materialize_production(
                self.canonical_manifest,
                output,
                model_dir=self.model_dir,
                runtime_root=self.root / "missing-runtime",
                expected_model_sha256=self.model_sha,
                queries=[{"query_id": "q1", "query": "阿贝多"}],
            )
        self.assertFalse(output.exists())

    def test_query_id_rejects_alias_and_traversal(self) -> None:
        vectors = np.asarray([[1.0, 0.0]] * 6, dtype=np.float32)
        for query_id in (".", "..", "../escape", "nested/name", "C:\\escape"):
            with self.assertRaisesRegex(ProductionMaterializationError, "safe stable directory"):
                _materialize_fixture(
                    self.canonical_manifest, self.root / ("out-" + str(len(query_id))),
                    model_dir=self.model_dir, expected_model_sha256=self.model_sha,
                    queries=[{"query_id": query_id, "query": "阿贝多"}], dense_vectors=vectors,
                )

    def test_production_surface_does_not_accept_synthetic_vectors(self) -> None:
        with self.assertRaises(TypeError):
            materialize_production(
                self.canonical_manifest, self.root / "production", model_dir=self.model_dir,
                expected_model_sha256=self.model_sha, queries=[{"query_id": "q1", "query": "阿贝多"}],
                dense_vectors_for_test=np.asarray([[1.0, 0.0]], dtype=np.float32),  # type: ignore[call-arg]
            )

    def test_preflight_rejects_incomplete_accounting_and_integrity_failure(self) -> None:
        manifest = json.loads(self.canonical_manifest.read_text(encoding="utf-8"))
        for change in ({"status": "incomplete"}, {"accounted_record_count": 0}, {"input_integrity_failure_count": 1}):
            altered = dict(manifest)
            altered.update(change)
            path = self.root / ("bad-" + str(len(change)) + ".json")
            path.write_bytes(canonical_json_bytes(altered))
            with self.assertRaisesRegex(ProductionMaterializationError, "preflight failed"):
                preflight_production_inputs(path, self.model_dir, expected_model_sha256=self.model_sha)

    def test_orchestration_reaches_all_packets_and_refuses_existing_root(self) -> None:
        output = self.root / "production"
        vectors = np.asarray([[1.0, 0.0]] * 6, dtype=np.float32)
        with patch("genshin_corpus.retrieval.candidate_retrieval.encode_dense_query", return_value=np.asarray([1.0, 0.0], dtype=np.float32)):
            result = _materialize_fixture(
                self.canonical_manifest,
                output,
                model_dir=self.model_dir,
                expected_model_sha256=self.model_sha,
                queries=[{"query_id": "q1", "query": "阿贝多"}],
                dense_vectors=vectors,
                assembly_config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=1000, total_context_chars=10000),
            )
        self.assertEqual(result["status"], "complete")
        for mode in ("lexical", "dense", "hybrid"):
            packet = output / "packets" / "q1" / mode
            self.assertTrue((packet / "evidence_packet.json").exists())
            self.assertTrue((packet / "evidence_packet.md").exists())
        with self.assertRaisesRegex(ProductionMaterializationError, "already exists"):
            _materialize_fixture(
                self.canonical_manifest, output, model_dir=self.model_dir,
                expected_model_sha256=self.model_sha,
                queries=[{"query_id": "q1", "query": "阿贝多"}],
                dense_vectors=vectors,
            )


if __name__ == "__main__":
    unittest.main()
