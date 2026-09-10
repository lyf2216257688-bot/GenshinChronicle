import gzip
import hashlib
import json
import shutil
import unittest
from pathlib import Path

import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.qwen_batch_lifecycle import QwenBatchLifecycleTransportError
from genshin_corpus.retrieval.qwen_batch_packing import QwenBatchPackingConfig, pack_qwen_batch_units
from genshin_corpus.retrieval.qwen_full_batch_runner import (
    QwenFullBatchRunnerError,
    merge_qwen_full_batch_results,
    prepare_qwen_full_batch_run,
    qwen_full_batch_disk_preflight,
    qwen_full_batch_dry_run,
    resume_qwen_full_batch_run,
    submit_pending_qwen_full_batch_run,
)


class _FakeClient:
    def __init__(self, *, ambiguous_create_at=None):
        self.uploads, self.creates, self.retrieves, self.downloads = [], [], [], []
        self.ambiguous_create_at = ambiguous_create_at
        self.statuses = {}
        self.files = {}

    def upload_file(self, body, *, purpose):
        self.uploads.append((body, purpose))
        return f"file-input-{len(self.uploads)}"

    def create_batch(self, *, input_file_id, endpoint, completion_window):
        self.creates.append(input_file_id)
        if self.ambiguous_create_at == len(self.creates) - 1:
            raise QwenBatchLifecycleTransportError("ConnectionError", ambiguous=True)
        return f"batch-{len(self.creates)}"

    def retrieve_batch(self, batch_id):
        self.retrieves.append(batch_id)
        return self.statuses.get(batch_id, {"id": batch_id, "status": "in_progress"})

    def download_file(self, file_id):
        self.downloads.append(file_id)
        return self.files[file_id]


class QwenFullBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("data/retrieval/.qwen-full-batch-runner-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.packing_root = self.root / "packing"
        self.run_root = self.root / "run"
        self.ru_manifest = self._write_ru_fixture()
        units = [self._unit(index) for index in range(11)]
        packed = pack_qwen_batch_units(
            units,
            retrieval_unit_build_identity="fixture-build",
            expected_count=11,
            output_root=self.packing_root,
            config=QwenBatchPackingConfig(max_requests_per_file=1, max_file_bytes=1_000_000, max_row_bytes=1_000_000),
        )
        packed.update({
            "schema_version": "phase04-rag-qwen37-batch-packing-0.1",
            "status": "complete",
            "provider_api_calls": 0,
            "packing_identity": "fixture-packing",
            "retrieval_unit_build_identity": "fixture-build",
            "retrieval_unit_manifest_path": str(self.ru_manifest),
        })
        (self.packing_root / "metadata").mkdir(parents=True, exist_ok=True)
        (self.packing_root / "metadata/packing_manifest.json").write_bytes(canonical_json_bytes(packed))

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root)

    @staticmethod
    def _unit(index):
        return {
            "unit_id": f"u{index}",
            "retrieval_visible_text": f"fixture text {index}",
            "source_order": [0, index],
        }

    def _write_ru_fixture(self):
        units = []
        for index in range(11):
            unit = self._unit(index)
            unit.update({
                "schema_version": "phase04-retrieval-unit-0.1",
                "source": {"canonical_address": {}, "lineage": {"raw_refs": []}, "record_context": {}, "provenance": {}, "content_role": {}},
                "content_type": "rich_text", "nested_selector": {}, "fragment_selector": {"kind": "whole"}, "structure": {},
            })
            units.append(unit)
        body = gzip.compress(b"".join(canonical_json_bytes(unit) + b"\n" for unit in units), mtime=0)
        artifact = self.root / "ru/artifacts/retrieval_units.jsonl.gz"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(body)
        empty_body = gzip.compress(b"", mtime=0)
        skip_path = self.root / "ru/artifacts/skip_ledger.jsonl.gz"
        skip_path.write_bytes(empty_body)
        failure_body = canonical_json_bytes({"status": "clear", "failure_count": 0, "failures": []})
        failure_path = self.root / "ru/metadata/failure_ledger.json"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_bytes(failure_body)
        manifest = {
            "schema_version": "phase04-retrieval-unit-build-0.1", "status": "complete", "build_identity": "fixture-build",
            "artifacts": {
                "retrieval_units": {"path": "artifacts/retrieval_units.jsonl.gz", "sha256": hashlib.sha256(body).hexdigest(), "byte_count": len(body), "row_count": 11},
                "skip_ledger": {"path": "artifacts/skip_ledger.jsonl.gz", "sha256": hashlib.sha256(empty_body).hexdigest(), "byte_count": len(empty_body), "row_count": 0},
                "failure_ledger": {"path": "metadata/failure_ledger.json", "sha256": hashlib.sha256(failure_body).hexdigest(), "byte_count": len(failure_body)},
            },
            "accounting": {"retrieval_unit_count": 11, "skipped_count": 0, "failure_count": 0},
        }
        path = self.root / "ru/metadata/manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(manifest))
        return path

    def _prepare_submit(self, client=None):
        prepare_qwen_full_batch_run(self.packing_root, self.run_root)
        client = client or _FakeClient()
        submit_pending_qwen_full_batch_run(self.packing_root, self.run_root, client)
        return client

    def _complete_all(self, client):
        for index in range(11):
            batch_id, file_id = f"batch-{index + 1}", f"file-output-{index}"
            shard = self.packing_root / f"artifacts/shards/shard-{index:05d}.jsonl"
            record = json.loads(shard.read_text(encoding="utf-8"))
            vector = [0.0] * 2048
            vector[index] = 1.0
            client.statuses[batch_id] = {"id": batch_id, "status": "completed", "output_file_id": file_id}
            client.files[file_id] = canonical_json_bytes({"custom_id": record["custom_id"], "error": None, "response": {"status_code": 200, "body": {"model": "qwen3.7-text-embedding", "data": [{"index": 0, "embedding": vector}]}}}) + b"\n"
        resume_qwen_full_batch_run(self.packing_root, self.run_root, client)

    def _rewrite_packing_manifest(self, mutate):
        path = self.packing_root / "metadata/packing_manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_bytes(canonical_json_bytes(value))

    def test_eleven_shard_iteration_dry_run_and_materialized_skip(self):
        client = self._prepare_submit()
        self.assertEqual(len(client.uploads), 11)
        self.assertEqual(len(client.creates), 11)
        dry = qwen_full_batch_dry_run(self.packing_root, self.run_root)
        self.assertEqual(dry["shard_count"], 11)
        self.assertEqual(dry["request_count"], 11)
        self.assertEqual(dry["shard_status_counts"]["created"], 11)
        state_path = self.run_root / "shards/shard-00000/metadata/lifecycle_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "materialized"
        state_path.write_bytes(canonical_json_bytes(state))
        submit_pending_qwen_full_batch_run(self.packing_root, self.run_root, client)
        self.assertEqual(len(client.creates), 11)

    def test_prepared_packing_binding_accepts_unchanged_artifact(self):
        prepare_qwen_full_batch_run(self.packing_root, self.run_root)
        dry = qwen_full_batch_dry_run(self.packing_root, self.run_root)
        self.assertEqual(dry["new_batch_jobs_next_submit"], 11)

    def test_disk_preflight_is_provider_free_and_reports_bounded_strategy(self):
        report = qwen_full_batch_disk_preflight(self.packing_root, target_path=self.root, safety_margin_bytes=0)
        self.assertEqual(report["provider_api_calls"], 0)
        self.assertEqual(report["known_raw_provider_output_bytes"], 23_634_784_581)
        self.assertEqual(report["expected_final_dense_bytes"], 11 * 2048 * 4)
        self.assertEqual(report["estimated_working_space_bytes"], 23_634_784_581 + 2 * 11 * 2048 * 4)
        self.assertIn(report["space_check"], {"pass", "fail", "unknown"})

    def test_changed_packing_identity_rejects_before_upload_or_create(self):
        prepare_qwen_full_batch_run(self.packing_root, self.run_root)
        self._rewrite_packing_manifest(lambda value: value.__setitem__("packing_identity", "coherently-replaced"))
        client = _FakeClient()
        with self.assertRaisesRegex(QwenFullBatchRunnerError, "prepared run"):
            submit_pending_qwen_full_batch_run(self.packing_root, self.run_root, client)
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.creates, [])

    def test_changed_prepared_shard_binding_rejects_before_provider_operation(self):
        prepare_qwen_full_batch_run(self.packing_root, self.run_root)
        path = self.run_root / "metadata/full_run_manifest.json"
        prepared = json.loads(path.read_text(encoding="utf-8"))
        prepared["shards"][0]["shard_id"] = "replaced-shard-id"
        path.write_bytes(canonical_json_bytes(prepared))
        client = _FakeClient()
        with self.assertRaisesRegex(QwenFullBatchRunnerError, "bindings differ"):
            submit_pending_qwen_full_batch_run(self.packing_root, self.run_root, client)
        self.assertEqual(client.uploads, [])
        self.assertEqual(client.creates, [])

    def test_resume_and_dry_run_reject_packing_drift_before_provider_operation(self):
        client = self._prepare_submit()
        self._rewrite_packing_manifest(lambda value: value.__setitem__("packing_identity", "replaced-before-resume"))
        with self.assertRaisesRegex(QwenFullBatchRunnerError, "prepared run"):
            resume_qwen_full_batch_run(self.packing_root, self.run_root, client)
        self.assertEqual(client.retrieves, [])
        with self.assertRaisesRegex(QwenFullBatchRunnerError, "prepared run"):
            qwen_full_batch_dry_run(self.packing_root, self.run_root)

    def test_dry_run_rejects_existing_run_root_without_prepared_manifest(self):
        self.run_root.mkdir(parents=True)
        with self.assertRaisesRegex(QwenFullBatchRunnerError, "lacks its prepared manifest"):
            qwen_full_batch_dry_run(self.packing_root, self.run_root)

    def test_existing_batch_resumes_without_create_and_failures_stop_later_work(self):
        client = self._prepare_submit()
        resume_qwen_full_batch_run(self.packing_root, self.run_root, client)
        self.assertEqual(len(client.creates), 11)
        self.assertEqual(len(client.retrieves), 11)
        client.retrieves.clear()
        client.statuses["batch-2"] = {"id": "batch-2", "status": "failed"}
        with self.assertRaises(QwenFullBatchRunnerError):
            resume_qwen_full_batch_run(self.packing_root, self.run_root, client)
        self.assertEqual(client.retrieves, ["batch-1", "batch-2"])
        client = _FakeClient(ambiguous_create_at=1)
        prepare_qwen_full_batch_run(self.packing_root, self.root / "ambiguous")
        with self.assertRaises(QwenFullBatchRunnerError):
            submit_pending_qwen_full_batch_run(self.packing_root, self.root / "ambiguous", client)
        self.assertEqual(len(client.creates), 2)
        self.assertEqual((self.root / "ambiguous/shards/shard-00002").exists(), False)

    def test_transient_retrieve_failure_resumes_same_batch_without_submission(self):
        client = self._prepare_submit()
        state_path = self.run_root / "shards/shard-00000/metadata/lifecycle_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "retrieve_failed"
        state["retrieve_error_code"] = "ConnectionError"
        state_path.write_bytes(canonical_json_bytes(state))
        client.retrieves.clear()
        resume_qwen_full_batch_run(self.packing_root, self.run_root, client)
        self.assertEqual(len(client.creates), 11)
        self.assertEqual(client.retrieves[0], "batch-1")

    def test_completed_shards_merge_in_deterministic_ru_order_and_reject_bad_vectors(self):
        client = self._prepare_submit()
        self._complete_all(client)
        merged = merge_qwen_full_batch_results(self.packing_root, self.run_root)
        self.assertEqual(merged["row_count"], 11)
        rows_path = self.run_root / "final/dense/artifacts/rows.jsonl.gz"
        with gzip.open(rows_path, "rt", encoding="utf-8") as handle:
            self.assertEqual([json.loads(line)["unit_id"] for line in handle], [f"u{index}" for index in range(11)])
        vectors = np.load(self.run_root / "final/dense/artifacts/vectors.f32.npy", allow_pickle=False)
        self.assertEqual(vectors.dtype, np.float32)
        self.assertEqual(vectors.shape, (11, 2048))
        self.assertTrue(np.allclose(vectors, np.eye(11, 2048, dtype=np.float32)))

    def test_final_merge_rejects_wrong_dimension(self):
        client = self._prepare_submit()
        self._complete_all(client)
        shard_vectors = self.run_root / "shards/shard-00000/materialized/dense/artifacts/vectors.f32.npy"
        np.save(shard_vectors, np.zeros((1, 1024), dtype=np.float32), allow_pickle=False)
        with self.assertRaisesRegex(QwenFullBatchRunnerError, "vectors"):
            merge_qwen_full_batch_results(self.packing_root, self.run_root)

    def test_final_merge_rejects_missing_or_duplicate_rows(self):
        client = self._prepare_submit()
        self._complete_all(client)
        rows_path = self.run_root / "shards/shard-00000/materialized/dense/artifacts/rows.jsonl.gz"
        with gzip.open(rows_path, "rt", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        rows[0]["unit_id"] = "u1"
        rows_path.write_bytes(gzip.compress(b"".join(canonical_json_bytes(row) + b"\n" for row in rows), mtime=0))
        with self.assertRaisesRegex(QwenFullBatchRunnerError, "missing, duplicate, or unknown"):
            merge_qwen_full_batch_results(self.packing_root, self.run_root)


if __name__ == "__main__":
    unittest.main()
