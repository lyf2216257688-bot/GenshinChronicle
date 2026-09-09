from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.candidate_retrieval import dense_candidates
from genshin_corpus.retrieval.qwen_embedding import (
    QWEN_EMBEDDING_DIMENSION,
    QWEN_EMBEDDING_MODEL_ID,
    QWEN_QUERY_ROLE,
    QwenEmbeddingPreflightError,
    QwenEmbeddingRequest,
    QwenEmbeddingResponse,
    QwenEmbeddingTransportError,
    QwenSynchronousPreflightConfig,
    run_qwen_synchronous_preflight,
)
from genshin_corpus.retrieval import qwen_embedding as qwen_module


class _FakeTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.requests: list[QwenEmbeddingRequest] = []

    def embed(self, request: QwenEmbeddingRequest) -> QwenEmbeddingResponse:
        self.requests.append(request)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


class _IssuanceObservingTransport:
    def __init__(self, ledger_path: Path, responses: dict[str, QwenEmbeddingResponse]) -> None:
        self.ledger_path = ledger_path
        self.responses = responses
        self.observed: list[list[dict[str, object]]] = []

    def embed(self, request: QwenEmbeddingRequest) -> QwenEmbeddingResponse:
        self.observed.append([json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines()])
        return self.responses[request.role]


class QwenEmbeddingPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.qwen-embedding-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        (self.root / "ru/artifacts").mkdir(parents=True)
        units = [
            self._unit("u0", "第一条检索文本", 0),
            self._unit("u1", "第二条检索文本", 1),
            self._unit("u2", "第三条检索文本", 2),
        ]
        body = gzip.compress(b"".join(canonical_json_bytes(unit) + b"\n" for unit in units), mtime=0)
        skip = gzip.compress(b"", mtime=0)
        failure = canonical_json_bytes({"status": "clear", "failure_count": 0})
        for relative, value in (("retrieval_units.jsonl.gz", body), ("skip_ledger.jsonl.gz", skip)):
            (self.root / "ru/artifacts" / relative).write_bytes(value)
        (self.root / "ru/metadata").mkdir()
        (self.root / "ru/metadata/failure_ledger.json").write_bytes(failure)
        manifest = {
            "schema_version": "phase04-retrieval-unit-build-0.1",
            "status": "complete",
            "build_identity": "ru-build-qwen-fixture",
            "canonical_input": {"canonical_run_id": "fixture", "canonical_input_identity": "fixture", "canonical_schema_version": "phase03-draft-0.1"},
            "artifacts": {
                "retrieval_units": self._descriptor("artifacts/retrieval_units.jsonl.gz", body, 3),
                "skip_ledger": self._descriptor("artifacts/skip_ledger.jsonl.gz", skip, 0),
                "failure_ledger": self._descriptor("metadata/failure_ledger.json", failure),
            },
        }
        (self.root / "ru/metadata/manifest.json").write_bytes(canonical_json_bytes(manifest))
        self.ru_manifest = self.root / "ru/metadata/manifest.json"

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

    def _config(self, *, ids: tuple[str, ...] = ("u0", "u2")) -> QwenSynchronousPreflightConfig:
        return QwenSynchronousPreflightConfig(self.ru_manifest, ids, "测试查询")

    @staticmethod
    def _response(role: str, count: int, *, model: str | None = QWEN_EMBEDDING_MODEL_ID, returned_role: str | None = None, raw: bytes | None = None) -> QwenEmbeddingResponse:
        vectors = np.zeros((count, QWEN_EMBEDDING_DIMENSION), dtype=np.float32)
        for index in range(count):
            vectors[index, index] = float(index + 1)
        return QwenEmbeddingResponse(
            vectors=vectors,
            raw_response_bytes=raw or canonical_json_bytes({"id": f"request-{role}", "model": model, "role": returned_role or role, "data": [{"embedding": [0]}]}),
            returned_model=model,
            returned_role=returned_role or role,
            provider_request_id=f"request-{role}",
        )

    def test_injected_preflight_preserves_operating_point_and_writes_loadable_dense_artifact(self) -> None:
        transport = _FakeTransport([self._response("document", 2), self._response("query", 1)])
        result = run_qwen_synchronous_preflight(self._config(), self.root / "run", transport)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(transport.requests[0].role, "document")
        self.assertEqual(transport.requests[1].role, QWEN_QUERY_ROLE)
        for request in transport.requests:
            self.assertEqual(request.model_id, QWEN_EMBEDDING_MODEL_ID)
            self.assertEqual(request.dimension, 2048)
            self.assertEqual(request.output, "dense")
            self.assertIsNone(request.custom_query_instruction)
        manifest_path = self.root / "run/dense/metadata/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["retrieval_unit_build_identity"], "ru-build-qwen-fixture")
        self.assertEqual(manifest["model_sha256"], None)
        self.assertEqual(manifest["remote_provider_provenance"]["kind"], "remote_provider")
        self.assertEqual(manifest["remote_provider_provenance"]["provider_api_status"], "unverified_injected_only")
        candidates = dense_candidates(manifest_path, np.eye(1, QWEN_EMBEDDING_DIMENSION, 0, dtype=np.float32)[0], top_k=2)
        self.assertEqual([item["unit_id"] for item in candidates], ["u0", "u2"])
        self.assertTrue((self.root / "run/responses/document.response.json").is_file())
        self.assertTrue((self.root / "run/responses/query.response.json").is_file())

    def test_request_identity_is_deterministic_and_rejects_other_operating_points(self) -> None:
        first = QwenEmbeddingRequest(role="query", texts=("问题",))
        self.assertEqual(first.request_identity, QwenEmbeddingRequest(role="query", texts=("问题",)).request_identity)
        with self.assertRaisesRegex(QwenEmbeddingPreflightError, "2048"):
            QwenEmbeddingRequest(role="query", texts=("问题",), dimension=1024)
        with self.assertRaisesRegex(QwenEmbeddingPreflightError, "model"):
            QwenEmbeddingRequest(role="query", texts=("问题",), model_id="other")
        with self.assertRaisesRegex(QwenEmbeddingPreflightError, "instruction"):
            QwenEmbeddingRequest(role="query", texts=("问题",), custom_query_instruction="prefix")  # type: ignore[arg-type]

    def test_returned_model_role_dimension_and_row_binding_fail_closed(self) -> None:
        cases = [
            self._response("document", 2, model="other"),
            self._response("document", 2, returned_role="query"),
            QwenEmbeddingResponse(np.zeros((2, 1024), dtype=np.float32), canonical_json_bytes({"id": "bad"}), QWEN_EMBEDDING_MODEL_ID, "document"),
        ]
        for index, document_response in enumerate(cases):
            with self.subTest(index=index):
                result = run_qwen_synchronous_preflight(
                    self._config(), self.root / f"bad-{index}", _FakeTransport([document_response])
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["provider_attempts"]["row_count"], 1)
        with self.assertRaisesRegex(QwenEmbeddingPreflightError, "manifest order"):
            self._config(ids=("u2", "u0"))
            run_qwen_synchronous_preflight(
                self._config(ids=("u2", "u0")), self.root / "bad-order", _FakeTransport([])
            )

    def test_query_failure_preserves_document_response_and_attempt_ledger(self) -> None:
        result = run_qwen_synchronous_preflight(
            self._config(),
            self.root / "partial",
            _FakeTransport([
                self._response("document", 2),
                QwenEmbeddingTransportError("Throttled", status_code=429, raw_response_bytes=canonical_json_bytes({"code": "Throttled"})),
            ]),
        )
        self.assertEqual(result["status"], "partial")
        self.assertTrue((self.root / "partial/responses/document.response.json").is_file())
        self.assertTrue((self.root / "partial/responses/query.response.json").is_file())
        rows = [json.loads(line) for line in (self.root / "partial/metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["status"] for row in rows], ["succeeded", "failed"])
        self.assertEqual(rows[1]["error"]["code"], "Throttled")

    def test_attempt_is_durable_before_injected_transport_and_resolves_same_record(self) -> None:
        run_root = self.root / "issued-before-invoke"
        transport = _IssuanceObservingTransport(
            run_root / "metadata/provider_attempts.jsonl",
            {"document": self._response("document", 2), "query": self._response("query", 1)},
        )
        result = run_qwen_synchronous_preflight(self._config(), run_root, transport)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(transport.observed[0][0]["status"], "issued")
        self.assertEqual(transport.observed[1][-1]["status"], "issued")
        final_rows = [json.loads(line) for line in (run_root / "metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(final_rows), 2)
        self.assertEqual(final_rows[0]["attempt_id"], transport.observed[0][0]["attempt_id"])
        self.assertEqual(final_rows[0]["status"], "succeeded")

    def test_unexpected_transport_or_post_response_failure_leaves_issued_attempt_auditable(self) -> None:
        transport = _FakeTransport([RuntimeError("local process stopped")])
        run_root = self.root / "transport-crash"
        with self.assertRaisesRegex(RuntimeError, "local process stopped"):
            run_qwen_synchronous_preflight(self._config(), run_root, transport)
        rows = [json.loads(line) for line in (run_root / "metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(row["attempt_number"], row["status"]) for row in rows], [(1, "issued")])

        post_response_root = self.root / "post-response-crash"
        with patch.object(qwen_module, "_validate_response", side_effect=RuntimeError("local validation stopped")):
            with self.assertRaisesRegex(RuntimeError, "local validation stopped"):
                run_qwen_synchronous_preflight(
                    self._config(), post_response_root, _FakeTransport([self._response("document", 2)])
                )
        post_rows = [json.loads(line) for line in (post_response_root / "metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(row["attempt_number"], row["status"]) for row in post_rows], [(1, "issued")])
        self.assertTrue((post_response_root / "responses/document.response.json").is_file())

    def test_secret_like_response_evidence_is_rejected_and_never_written(self) -> None:
        secret = "live-secret-value"
        response = self._response("document", 2, raw=canonical_json_bytes({"apiKey": secret, "data": []}))
        result = run_qwen_synchronous_preflight(
            self._config(), self.root / "secret", _FakeTransport([response]), secret_values=(secret,)
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.root / "secret/responses/document.response.json").exists())
        manifest_body = (self.root / "secret/metadata/preflight_manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(secret, manifest_body)
        id_response = self._response("document", 2)
        id_response = QwenEmbeddingResponse(
            id_response.vectors,
            id_response.raw_response_bytes,
            id_response.returned_model,
            id_response.returned_role,
            secret,
        )
        id_result = run_qwen_synchronous_preflight(
            self._config(), self.root / "secret-id", _FakeTransport([id_response]), secret_values=(secret,)
        )
        self.assertEqual(id_result["status"], "failed")
        self.assertNotIn(secret, (self.root / "secret-id/metadata/preflight_manifest.json").read_text(encoding="utf-8"))

    def test_existing_output_and_non_finite_vectors_fail_before_or_without_overwrite(self) -> None:
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaises(FileExistsError):
            run_qwen_synchronous_preflight(self._config(), output, _FakeTransport([]))
        response = self._response("document", 2)
        values = np.asarray(response.vectors, dtype=np.float32)
        values[0, 0] = np.nan
        result = run_qwen_synchronous_preflight(
            self._config(), self.root / "non-finite", _FakeTransport([
                QwenEmbeddingResponse(values, response.raw_response_bytes, response.returned_model, response.returned_role)
            ])
        )
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
