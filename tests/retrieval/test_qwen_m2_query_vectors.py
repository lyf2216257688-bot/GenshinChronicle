from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval import qwen_m2_query_vectors as query_vectors_module
from genshin_corpus.retrieval.qwen_embedding import (
    QWEN_EMBEDDING_DIMENSION,
    QWEN_EMBEDDING_MODEL_ID,
    QwenEmbeddingResponse,
    QwenEmbeddingTransportError,
)
from genshin_corpus.retrieval.qwen_m2_query_vectors import (
    QwenM2QueryVectorConfig,
    QwenM2QueryVectorsError,
    load_qwen_m2_query_vectors,
    materialize_qwen_m2_query_vectors,
)


class _FakeTransport:
    def __init__(self, *, root: Path | None = None, crash_first: bool = False) -> None:
        self.root = root
        self.crash_first = crash_first
        self.requests = []

    def embed(self, request):
        if self.root is not None:
            ledger = self.root / "metadata" / "provider_attempts.jsonl"
            assert ledger.is_file()
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            assert rows[-1]["status"] == "issued"
            assert rows[-1]["request_identity"] == request.request_identity
        self.requests.append(request)
        if self.crash_first and len(self.requests) == 1:
            raise RuntimeError("simulated interruption after issuance")
        vectors = np.zeros((len(request.texts), QWEN_EMBEDDING_DIMENSION), dtype=np.float32)
        for index in range(len(request.texts)):
            vectors[index, index + len(self.requests)] = 1.0
        return QwenEmbeddingResponse(
            vectors=vectors,
            raw_response_bytes=canonical_json_bytes({"request": len(self.requests), "data": [{"embedding": [0]}]}),
            returned_model=QWEN_EMBEDDING_MODEL_ID,
            returned_role="query",
            provider_request_id=f"fake-{len(self.requests)}",
        )


class _ScriptedTransport(_FakeTransport):
    def __init__(self, *, root: Path, outcomes: list[object]) -> None:
        super().__init__(root=root)
        self.outcomes = outcomes

    def embed(self, request):
        if self.root is not None:
            ledger = self.root / "metadata" / "provider_attempts.jsonl"
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            assert rows[-1]["status"] == "issued"
            assert rows[-1]["request_identity"] == request.request_identity
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, QwenEmbeddingResponse):
            return outcome
        vectors = np.zeros((len(request.texts), QWEN_EMBEDDING_DIMENSION), dtype=np.float32)
        for index in range(len(request.texts)):
            vectors[index, index + len(self.requests)] = 1.0
        return QwenEmbeddingResponse(
            vectors=vectors,
            raw_response_bytes=canonical_json_bytes({"request": len(self.requests), "data": [{"embedding": [0]}]}),
            returned_model=QWEN_EMBEDDING_MODEL_ID,
            returned_role="query",
            provider_request_id=f"scripted-{len(self.requests)}",
        )


class QwenM2QueryVectorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "qwen-m2-query-vector-test-output" / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.runtime = self.root / "questions.runtime.jsonl"
        self._write_runtime()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)
        try:
            self.root.parent.rmdir()
        except OSError:
            pass

    def _write_runtime(self, *, replacement: str | None = None) -> None:
        rows = []
        for number in range(1, 71):
            question = replacement if number == 1 and replacement is not None else f"original question {number}"
            rows.append({"question_id": f"Q{number:03d}", "question": question})
        self.runtime.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))

    def _run(self, transport: _FakeTransport | None = None, *, resume: bool = False):
        root = self.root / "qwen-queries"
        return materialize_qwen_m2_query_vectors(
            QwenM2QueryVectorConfig(self.runtime), root, transport or _FakeTransport(root=root), resume=resume
        )

    def _partial_after_first_success(self, root: Path) -> None:
        original_write_manifest = query_vectors_module._write_manifest

        def stop_after_durable_first(manifest_root: Path, manifest):
            result = original_write_manifest(manifest_root, manifest)
            if manifest.get("status") == "in_progress" and manifest.get("completed_batch_count") == 1:
                raise RuntimeError("simulated stop after durable batch one")
            return result

        with patch.object(query_vectors_module, "_write_manifest", side_effect=stop_after_durable_first):
            with self.assertRaisesRegex(RuntimeError, "durable batch one"):
                materialize_qwen_m2_query_vectors(
                    QwenM2QueryVectorConfig(self.runtime), root, _FakeTransport(root=root)
                )
        manifest = json.loads((root / "metadata/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["status"], manifest["completed_batch_count"]), ("in_progress", 1))

    def test_materializes_exact_70q_float32_normalized_vectors_with_deterministic_batches(self) -> None:
        root = self.root / "qwen-queries"
        transport = _FakeTransport(root=root)
        manifest = self._run(transport)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual([len(request.texts) for request in transport.requests], [20, 20, 20, 10])
        self.assertEqual([request.texts[0] for request in transport.requests], ["original question 1", "original question 21", "original question 41", "original question 61"])
        rows, vectors, loaded = load_qwen_m2_query_vectors(root)
        self.assertEqual(loaded["run_identity"], manifest["run_identity"])
        self.assertEqual(vectors.shape, (70, 2048))
        self.assertEqual(vectors.dtype, np.float32)
        self.assertTrue(np.isfinite(vectors).all())
        self.assertTrue(np.allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=0.0, atol=1e-6))
        self.assertEqual([row["question_id"] for row in rows], [f"Q{number:03d}" for number in range(1, 71)])
        self.assertEqual(rows[0]["question"], "original question 1")

    def test_every_request_is_qwen_query_dense_without_instruction(self) -> None:
        root = self.root / "qwen-queries"
        transport = _FakeTransport(root=root)
        self._run(transport)
        for request in transport.requests:
            self.assertEqual(request.role, "query")
            self.assertEqual(request.model_id, QWEN_EMBEDDING_MODEL_ID)
            self.assertEqual(request.dimension, 2048)
            self.assertEqual(request.output, "dense")
            self.assertIsNone(request.custom_query_instruction)

    def test_provider_evidence_is_isolated_per_batch_and_issued_before_call(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        response_paths = sorted((root / "responses").glob("*.json"))
        self.assertEqual([path.name for path in response_paths], [f"batch-{number:03d}.response.json" for number in range(1, 5)])
        self.assertEqual(len({path.read_bytes() for path in response_paths}), 4)
        attempts = [json.loads(line) for line in (root / "metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["status"] for row in attempts], ["succeeded"] * 4)
        self.assertEqual([row["batch_index"] for row in attempts], [1, 2, 3, 4])

    def test_transport_failure_preserves_safe_raw_evidence_and_request_id_per_batch(self) -> None:
        root = self.root / "qwen-queries"
        failure = QwenEmbeddingTransportError(
            "Throttled",
            status_code=429,
            provider_request_id="provider-request-2",
            raw_response_bytes=canonical_json_bytes({"code": "Throttled", "request_id": "provider-request-2"}),
        )
        result = self._run(_ScriptedTransport(root=root, outcomes=[object(), failure]))
        self.assertEqual(result["status"], "failed")
        attempts = [json.loads(line) for line in (root / "metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["status"] for row in attempts], ["succeeded", "failed"])
        self.assertEqual(attempts[1]["error"]["provider_request_id"], "provider-request-2")
        self.assertEqual(attempts[1]["response_artifact"]["path"], "responses/batch-002.response.json")
        self.assertTrue((root / "responses/batch-001.response.json").is_file())
        self.assertTrue((root / "responses/batch-002.response.json").is_file())

    def test_unsafe_transport_evidence_or_request_id_is_rejected_without_persistence(self) -> None:
        root = self.root / "unsafe-evidence"
        raw_failure = QwenEmbeddingTransportError(
            "Throttled", raw_response_bytes=canonical_json_bytes({"apiKey": "unsafe", "data": []})
        )
        result = materialize_qwen_m2_query_vectors(
            QwenM2QueryVectorConfig(self.runtime), root, _ScriptedTransport(root=root, outcomes=[raw_failure])
        )
        self.assertEqual(result["failure"], {"category": "response_evidence_rejected", "code": "UnsafeResponseEvidence"})
        self.assertFalse((root / "responses/batch-001.response.json").exists())

        request_id_root = self.root / "unsafe-request-id"
        id_failure = QwenEmbeddingTransportError(
            "Throttled",
            provider_request_id="known-secret",
            raw_response_bytes=canonical_json_bytes({"code": "Throttled"}),
        )
        with patch("genshin_corpus.retrieval.qwen_m2_query_vectors._transport_secret_values", return_value=("known-secret",)):
            id_result = materialize_qwen_m2_query_vectors(
                QwenM2QueryVectorConfig(self.runtime), request_id_root,
                _ScriptedTransport(root=request_id_root, outcomes=[id_failure]),
            )
        self.assertEqual(id_result["failure"], {"category": "response_evidence_rejected", "code": "UnsafeProviderRequestId"})
        self.assertFalse((request_id_root / "responses/batch-001.response.json").exists())
        self.assertNotIn("known-secret", (request_id_root / "metadata/manifest.json").read_text(encoding="utf-8"))

    def test_completed_run_is_reused_without_reissuing_batches(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        reused = _FakeTransport(root=root)
        manifest = self._run(reused, resume=True)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(reused.requests, [])

    def test_partial_resume_rejects_missing_or_tampered_prior_response_before_transport(self) -> None:
        deleted_root = self.root / "partial-response-deleted"
        self._partial_after_first_success(deleted_root)
        (deleted_root / "responses/batch-001.response.json").unlink()
        blocked = _FakeTransport(root=deleted_root)
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider response evidence.*missing"):
            materialize_qwen_m2_query_vectors(QwenM2QueryVectorConfig(self.runtime), deleted_root, blocked, resume=True)
        self.assertEqual(blocked.requests, [])

        tampered_root = self.root / "partial-response-tampered"
        self._partial_after_first_success(tampered_root)
        response = tampered_root / "responses/batch-001.response.json"
        response.write_bytes(canonical_json_bytes({"code": "tampered"}))
        blocked = _FakeTransport(root=tampered_root)
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider response evidence.*hash"):
            materialize_qwen_m2_query_vectors(QwenM2QueryVectorConfig(self.runtime), tampered_root, blocked, resume=True)
        self.assertEqual(blocked.requests, [])

    def test_partial_resume_rejects_tampered_configuration_before_transport(self) -> None:
        root = self.root / "partial-config-tampered"
        self._partial_after_first_success(root)
        manifest_path = root / "metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["configuration"]["model_id"] = "other-model"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        blocked = _FakeTransport(root=root)
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "fixed query operating point"):
            materialize_qwen_m2_query_vectors(QwenM2QueryVectorConfig(self.runtime), root, blocked, resume=True)
        self.assertEqual(blocked.requests, [])

    def test_partial_resume_rejects_tampered_ledger_descriptor_or_contents_before_transport(self) -> None:
        descriptor_root = self.root / "partial-ledger-descriptor"
        self._partial_after_first_success(descriptor_root)
        manifest_path = descriptor_root / "metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["provider_attempts"]["sha256"] = "0" * 64
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        blocked = _FakeTransport(root=descriptor_root)
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider attempt ledger.*hash"):
            materialize_qwen_m2_query_vectors(QwenM2QueryVectorConfig(self.runtime), descriptor_root, blocked, resume=True)
        self.assertEqual(blocked.requests, [])

        contents_root = self.root / "partial-ledger-contents"
        self._partial_after_first_success(contents_root)
        ledger = contents_root / "metadata/provider_attempts.jsonl"
        ledger.write_bytes(ledger.read_bytes() + b"\n")
        blocked = _FakeTransport(root=contents_root)
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider attempt ledger.*hash"):
            materialize_qwen_m2_query_vectors(QwenM2QueryVectorConfig(self.runtime), contents_root, blocked, resume=True)
        self.assertEqual(blocked.requests, [])

    def test_valid_partial_succeeded_prefix_resumes_at_the_next_batch(self) -> None:
        root = self.root / "partial-valid"
        self._partial_after_first_success(root)
        resumed = _FakeTransport(root=root)
        manifest = materialize_qwen_m2_query_vectors(QwenM2QueryVectorConfig(self.runtime), root, resumed, resume=True)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual([len(request.texts) for request in resumed.requests], [20, 20, 10])
        self.assertEqual([request.texts[0] for request in resumed.requests], ["original question 21", "original question 41", "original question 61"])

    def test_response_invalid_failure_binds_safe_persisted_response_evidence(self) -> None:
        root = self.root / "response-invalid"
        invalid = QwenEmbeddingResponse(
            vectors=np.eye(20, QWEN_EMBEDDING_DIMENSION, dtype=np.float32),
            raw_response_bytes=canonical_json_bytes({"code": "safe-invalid-response", "data": []}),
            returned_model=QWEN_EMBEDDING_MODEL_ID,
            returned_role="document",
            provider_request_id="response-invalid-request",
        )
        result = materialize_qwen_m2_query_vectors(
            QwenM2QueryVectorConfig(self.runtime), root, _ScriptedTransport(root=root, outcomes=[invalid])
        )
        self.assertEqual(result["status"], "failed")
        attempt = json.loads((root / "metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(attempt["error"]["category"], "response_invalid")
        self.assertEqual(attempt["response_artifact"]["path"], "responses/batch-001.response.json")
        self.assertTrue((root / attempt["response_artifact"]["path"]).is_file())

    def test_completed_load_fails_when_provider_ledger_is_deleted_or_tampered(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        ledger = root / "metadata/provider_attempts.jsonl"
        ledger.unlink()
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider attempt ledger.*missing"):
            load_qwen_m2_query_vectors(root)

        tampered_root = self.root / "qwen-queries-ledger-tampered"
        materialize_qwen_m2_query_vectors(
            QwenM2QueryVectorConfig(self.runtime), tampered_root, _FakeTransport(root=tampered_root)
        )
        tampered_ledger = tampered_root / "metadata/provider_attempts.jsonl"
        tampered_ledger.write_bytes(tampered_ledger.read_bytes() + b"\n")
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider attempt ledger.*hash"):
            load_qwen_m2_query_vectors(tampered_root)

    def test_completed_load_fails_when_provider_response_evidence_is_deleted(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        (root / "responses/batch-003.response.json").unlink()
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider response evidence.*missing"):
            load_qwen_m2_query_vectors(root)

        tampered_root = self.root / "qwen-queries-response-tampered"
        materialize_qwen_m2_query_vectors(
            QwenM2QueryVectorConfig(self.runtime), tampered_root, _FakeTransport(root=tampered_root)
        )
        response = tampered_root / "responses/batch-003.response.json"
        response.write_bytes(canonical_json_bytes({"code": "tampered"}))
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "provider response evidence.*hash"):
            load_qwen_m2_query_vectors(tampered_root)

    def test_completed_load_fails_when_fixed_configuration_is_tampered(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        manifest_path = root / "metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["configuration"]["model_id"] = "other-model"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "fixed query operating point"):
            load_qwen_m2_query_vectors(root)

    def test_completed_load_fails_when_question_mapping_or_batch_binding_is_tampered(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        manifest_path = root / "metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["question_mapping"][0]["question"] = "tampered"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "question mapping identity"):
            load_qwen_m2_query_vectors(root)

        batch_root = self.root / "qwen-queries-batch"
        materialize_qwen_m2_query_vectors(
            QwenM2QueryVectorConfig(self.runtime), batch_root, _FakeTransport(root=batch_root)
        )
        manifest_path = batch_root / "metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["batches"][0]["question_ids"][0] = "Q070"
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "batch binding"):
            load_qwen_m2_query_vectors(batch_root)

    def test_resume_fails_closed_for_changed_runtime_input(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        self._write_runtime(replacement="changed question")
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "different input"):
            self._run(_FakeTransport(root=root), resume=True)

    def test_resume_fails_closed_for_changed_batch_configuration(self) -> None:
        root = self.root / "qwen-queries"
        self._run(_FakeTransport(root=root))
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "different input, config, or transport"):
            materialize_qwen_m2_query_vectors(
                QwenM2QueryVectorConfig(self.runtime, batch_size=10), root, _FakeTransport(root=root), resume=True
            )

    def test_ambiguous_issued_attempt_fails_closed_without_replay(self) -> None:
        root = self.root / "qwen-queries"
        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            self._run(_FakeTransport(root=root, crash_first=True))
        attempts = [json.loads(line) for line in (root / "metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(attempts[0]["status"], "issued")
        retry = _FakeTransport(root=root)
        with self.assertRaisesRegex(QwenM2QueryVectorsError, "ambiguous issued-but-not-resolved"):
            self._run(retry, resume=True)
        self.assertEqual(retry.requests, [])

    def test_runtime_loader_rejects_review_only_fields(self) -> None:
        rows = [{"question_id": f"Q{number:03d}", "question": f"q{number}", "reference_answer": "forbidden"} for number in range(1, 71)]
        self.runtime.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))
        with self.assertRaisesRegex(Exception, "exactly question_id and question"):
            self._run()


if __name__ == "__main__":
    unittest.main()
