from __future__ import annotations

import gzip
import hashlib
from io import BytesIO
import io
import json
import shutil
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError, URLError

import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.candidate_retrieval import dense_candidates
from genshin_corpus.retrieval.qwen_embedding import (
    QWEN_EMBEDDING_DIMENSION,
    QWEN_EMBEDDING_MODEL_ID,
    QWEN_QUERY_ROLE,
    DashScopeQwenEmbeddingConfig,
    DashScopeQwenEmbeddingTransport,
    QwenEmbeddingPreflightError,
    QwenEmbeddingRequest,
    QwenEmbeddingResponse,
    QwenEmbeddingTransportError,
    QwenSynchronousPreflightConfig,
    run_qwen_dashscope_synchronous_preflight,
    run_qwen_synchronous_preflight,
)
from genshin_corpus.retrieval import qwen_embedding as qwen_module
from genshin_corpus.retrieval import __main__ as retrieval_cli


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


class _FakeHttpResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, *, status: int = 200) -> None:
        self._body = body
        self.headers = headers or {}
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _FakeHttpOpener:
    def __init__(self, outcomes: list[object], *, on_open=None) -> None:
        self.outcomes = iter(outcomes)
        self.on_open = on_open
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.on_open is not None:
            self.on_open()
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


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

    @staticmethod
    def _dashscope_body(count: int, role: str) -> bytes:
        embeddings = []
        for index in range(count):
            vector = [0.0] * QWEN_EMBEDDING_DIMENSION
            vector[index] = float(index + 1)
            embeddings.append({"text_index": index, "embedding": vector})
        return canonical_json_bytes({
            "code": "",
            "request_id": f"live-{role}",
            "model": QWEN_EMBEDDING_MODEL_ID,
            "output": {"text_type": role, "embeddings": embeddings},
        })

    def _dashscope_config(self) -> DashScopeQwenEmbeddingConfig:
        return DashScopeQwenEmbeddingConfig(
            region="cn-beijing",
            workspace="workspace-a",
            endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding",
        )

    def test_live_dashscope_success_is_distinct_from_injected_evidence(self) -> None:
        opener = _FakeHttpOpener([
            _FakeHttpResponse(self._dashscope_body(2, "document")),
            _FakeHttpResponse(self._dashscope_body(1, "query")),
        ])
        result = run_qwen_dashscope_synchronous_preflight(
            self._config(),
            self.root / "live-success",
            self._dashscope_config(),
            environment={"DASHSCOPE_API_KEY": "test-secret"},
            opener=opener,
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["execution_mode"], "live_dashscope")
        self.assertEqual(result["provider_api_status"], "live_dashscope_succeeded")
        rows = [json.loads(line) for line in (self.root / "live-success/metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["status"] for row in rows], ["succeeded", "succeeded"])
        manifest = json.loads((self.root / "live-success/metadata/preflight_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["provider_api_status"], "live_dashscope_succeeded")
        self.assertEqual(manifest["remote_provider_provenance"]["provider_api_status"], "live_dashscope_succeeded")
        self.assertNotIn("test-secret", json.dumps(manifest))
        self.assertEqual(len(opener.requests), 2)

    def test_live_dashscope_document_failure_is_not_provider_success(self) -> None:
        body = canonical_json_bytes({"code": "InvalidParameter", "request_id": "live-failure"})
        result = run_qwen_dashscope_synchronous_preflight(
            self._config(),
            self.root / "live-failure",
            self._dashscope_config(),
            environment={"DASHSCOPE_API_KEY": "test-secret"},
            opener=_FakeHttpOpener([HTTPError(self._dashscope_config().endpoint, 400, "bad", {}, BytesIO(body))]),
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["execution_mode"], "live_dashscope")
        self.assertEqual(result["provider_api_status"], "live_dashscope_failed")
        self.assertEqual(result["provider_attempts"]["row_count"], 1)
        self.assertEqual(result["provider_attempts"]["path"], "metadata/provider_attempts.jsonl")
        self.assertFalse((self.root / "live-failure/dense").exists())

    def test_live_dashscope_query_failure_is_partial_and_keeps_raw_evidence(self) -> None:
        query_body = canonical_json_bytes({"code": "Throttled", "request_id": "live-query-failure"})
        result = run_qwen_dashscope_synchronous_preflight(
            self._config(),
            self.root / "live-partial",
            self._dashscope_config(),
            environment={"DASHSCOPE_API_KEY": "test-secret"},
            opener=_FakeHttpOpener([
                _FakeHttpResponse(self._dashscope_body(2, "document")),
                HTTPError(self._dashscope_config().endpoint, 429, "throttled", {}, BytesIO(query_body)),
            ]),
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["provider_api_status"], "live_dashscope_partial")
        rows = [json.loads(line) for line in (self.root / "live-partial/metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["status"] for row in rows], ["succeeded", "failed"])
        self.assertTrue((self.root / "live-partial/responses/query.response.json").is_file())
        self.assertNotIn("test-secret", (self.root / "live-partial/metadata/preflight_manifest.json").read_text(encoding="utf-8"))

    def test_generic_runner_with_dashscope_transport_preserves_injected_evidence(self) -> None:
        transport = DashScopeQwenEmbeddingTransport(
            self._dashscope_config(),
            "test-secret",
            opener=_FakeHttpOpener([
                _FakeHttpResponse(self._dashscope_body(2, "document")),
                _FakeHttpResponse(self._dashscope_body(1, "query")),
            ]),
        )
        result = run_qwen_synchronous_preflight(self._config(), self.root / "generic-dashscope", transport)
        self.assertEqual(result["execution_mode"], "injected_offline")
        self.assertEqual(result["provider_api_status"], "unverified_injected_only")

    def test_live_dashscope_cli_exit_status_matches_preflight_status(self) -> None:
        args = [
            "qwen-dashscope-preflight",
            "--retrieval-unit-manifest", "ru.json",
            "--document-unit-id", "u0",
            "--query-text", "查询",
            "--output-root", "out",
            "--region", "cn-beijing",
            "--workspace", "workspace-a",
            "--endpoint", "https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding",
        ]
        for status, expected in (("complete", 0), ("failed", 1), ("partial", 1)):
            with self.subTest(status=status):
                with patch.object(retrieval_cli, "run_qwen_dashscope_synchronous_preflight", return_value={"status": status}):
                    with patch("sys.stdout", new_callable=io.StringIO) as output:
                        self.assertEqual(retrieval_cli.main(args), expected)
                self.assertIn('"status"', output.getvalue())


class DashScopeQwenEmbeddingTransportTests(unittest.TestCase):
    endpoint = "https://workspace-a.cn-beijing.maas.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"

    def _config(self) -> DashScopeQwenEmbeddingConfig:
        return DashScopeQwenEmbeddingConfig(region="cn-beijing", workspace="workspace-a", endpoint=self.endpoint)

    @staticmethod
    def _provider_response(
        count: int,
        *,
        model: str | None = QWEN_EMBEDDING_MODEL_ID,
        role: str | None = None,
        status_code: int | str | None = 200,
    ) -> bytes:
        embeddings = []
        for index in reversed(range(count)):
            vector = [0.0] * QWEN_EMBEDDING_DIMENSION
            vector[index] = float(index + 1)
            embeddings.append({"text_index": index, "embedding": vector})
        payload = {
            "code": "",
            "message": "",
            "request_id": "body-request-id",
            "output": {"embeddings": embeddings},
        }
        if status_code is not None:
            payload["status_code"] = status_code
        if model is not None:
            payload["model"] = model
        if role is not None:
            payload["output"]["text_type"] = role
        return canonical_json_bytes(payload)

    @staticmethod
    def _headers(request) -> dict[str, str]:
        return {name.lower(): value for name, value in request.header_items()}

    def test_document_and_query_wire_requests_preserve_fixed_operating_point(self) -> None:
        opener = _FakeHttpOpener([
            _FakeHttpResponse(self._provider_response(2, role="document")),
            _FakeHttpResponse(self._provider_response(1, role="query")),
        ])
        transport = DashScopeQwenEmbeddingTransport(self._config(), "test-secret", opener=opener)
        document = transport.embed(QwenEmbeddingRequest(role="document", texts=("文档一", "文档二")))
        query = transport.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))

        self.assertEqual(document.returned_model, QWEN_EMBEDDING_MODEL_ID)
        self.assertEqual(document.returned_role, "document")
        self.assertEqual(query.returned_role, "query")
        self.assertEqual(document.provider_request_id, "body-request-id")
        self.assertEqual(document.vectors[0][0], 1.0)
        self.assertEqual(document.vectors[1][1], 2.0)
        self.assertEqual(opener.timeouts, [30.0, 30.0])
        for request, role, texts in zip(opener.requests, ("document", "query"), (("文档一", "文档二"), ("查询",))):
            self.assertEqual(request.full_url, self.endpoint)
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(self._headers(request)["authorization"], "Bearer test-secret")
            wire = json.loads(request.data.decode("utf-8"))
            self.assertEqual(wire["model"], QWEN_EMBEDDING_MODEL_ID)
            self.assertEqual(wire["input"]["texts"], list(texts))
            self.assertEqual(wire["parameters"], {"text_type": role, "dimension": 2048, "output_type": "dense"})
            self.assertNotIn("instruct", wire)
            self.assertNotIn("instruction", wire)

    def test_realistic_success_payload_with_empty_code_is_not_an_error(self) -> None:
        body = self._provider_response(1, role="query")
        transport = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([_FakeHttpResponse(body)])
        )
        response = transport.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        self.assertEqual(response.provider_request_id, "body-request-id")
        self.assertEqual(response.returned_model, QWEN_EMBEDDING_MODEL_ID)
        self.assertEqual(response.raw_response_bytes, body)

    def test_string_200_body_status_is_success_and_other_body_statuses_fail_closed(self) -> None:
        string_success = DashScopeQwenEmbeddingTransport(
            self._config(),
            "test-secret",
            opener=_FakeHttpOpener([_FakeHttpResponse(self._provider_response(1, role="query", status_code="200"))]),
        ).embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        self.assertEqual(string_success.provider_request_id, "body-request-id")

        omitted_success = DashScopeQwenEmbeddingTransport(
            self._config(),
            "test-secret",
            opener=_FakeHttpOpener([_FakeHttpResponse(self._provider_response(1, role="query", status_code=None))]),
        ).embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        self.assertEqual(omitted_success.provider_request_id, "body-request-id")

        non_success_body = self._provider_response(1, role="query", status_code=201)
        transport = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([_FakeHttpResponse(non_success_body)])
        )
        with self.assertRaisesRegex(QwenEmbeddingTransportError, "UnexpectedResponseStatus") as caught:
            transport.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        self.assertEqual(caught.exception.status_code, 200)
        self.assertEqual(caught.exception.provider_request_id, "body-request-id")
        self.assertEqual(caught.exception.raw_response_bytes, non_success_body)

    def test_non_200_transport_status_cannot_become_success(self) -> None:
        body = self._provider_response(1, role="query", status_code=200)
        transport = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([_FakeHttpResponse(body, status=201)])
        )
        with self.assertRaisesRegex(QwenEmbeddingTransportError, "UnexpectedHTTPStatus") as caught:
            transport.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        self.assertEqual(caught.exception.status_code, 201)
        self.assertEqual(caught.exception.provider_request_id, "body-request-id")
        self.assertEqual(caught.exception.raw_response_bytes, body)

    def test_secret_free_configuration_identity_and_environment_boundary(self) -> None:
        secret = "test-secret"
        config = self._config()
        identity = config.identity_projection()
        self.assertEqual(identity["region"], "cn-beijing")
        self.assertEqual(identity["workspace"], "workspace-a")
        self.assertEqual(identity["endpoint"], self.endpoint)
        self.assertNotIn("api_key_env", identity)
        self.assertNotIn(secret, json.dumps(identity))
        transport = DashScopeQwenEmbeddingTransport.from_environment(
            config,
            environment={"DASHSCOPE_API_KEY": secret},
            opener=_FakeHttpOpener([]),
        )
        self.assertNotIn(secret, json.dumps(transport.identity_projection()))
        self.assertNotIn("DASHSCOPE_API_KEY", json.dumps(transport.identity_projection()))
        with self.assertRaisesRegex(QwenEmbeddingPreflightError, "environment"):
            DashScopeQwenEmbeddingTransport.from_environment(config, environment={}, opener=_FakeHttpOpener([]))

    def test_endpoint_must_bind_configured_workspace_and_region_before_transport_creation(self) -> None:
        path = "/api/v1/services/embeddings/text-embedding/text-embedding"
        for endpoint, workspace, region in (
            (f"https://other.cn-beijing.maas.aliyuncs.com{path}", "workspace-a", "cn-beijing"),
            (f"https://workspace-a.cn-shanghai.maas.aliyuncs.com{path}", "workspace-a", "cn-beijing"),
            (f"https://unrelated.example.com{path}", "workspace-a", "cn-beijing"),
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(QwenEmbeddingPreflightError, "workspace-region"):
                    DashScopeQwenEmbeddingConfig(region=region, workspace=workspace, endpoint=endpoint)

    def test_success_missing_or_wrong_model_follows_existing_response_contract(self) -> None:
        missing_model = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([_FakeHttpResponse(self._provider_response(1, model=None))])
        ).embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        self.assertIsNone(missing_model.returned_model)

        wrong_model = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([_FakeHttpResponse(self._provider_response(1, model="other"))])
        ).embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        with self.assertRaisesRegex(QwenEmbeddingPreflightError, "returned model"):
            qwen_module._validate_response(wrong_model, QwenEmbeddingRequest(role="query", texts=("查询",)), ())

    def test_http_provider_malformed_and_transport_errors_preserve_no_false_success(self) -> None:
        http_body = canonical_json_bytes({"request_id": "error-id", "code": "Throttling"})
        http_error = HTTPError(self.endpoint, 429, "throttled", {"x-acs-request-id": "header-id"}, BytesIO(http_body))
        transport = DashScopeQwenEmbeddingTransport(self._config(), "test-secret", opener=_FakeHttpOpener([http_error]))
        with self.assertRaises(QwenEmbeddingTransportError) as caught:
            transport.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))
        self.assertEqual(caught.exception.code, "Throttling")
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.provider_request_id, "error-id")
        self.assertEqual(caught.exception.raw_response_bytes, http_body)

        provider_error = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([_FakeHttpResponse(canonical_json_bytes({"code": "InvalidParameter"}))])
        )
        with self.assertRaisesRegex(QwenEmbeddingTransportError, "InvalidParameter"):
            provider_error.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))

        malformed = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([_FakeHttpResponse(canonical_json_bytes({"output": {}}))])
        )
        with self.assertRaisesRegex(QwenEmbeddingTransportError, "MalformedResponse"):
            malformed.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))

        network = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([URLError("offline")])
        )
        with self.assertRaisesRegex(QwenEmbeddingTransportError, "TransportConnectionError"):
            network.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))

        unexpected = DashScopeQwenEmbeddingTransport(
            self._config(), "test-secret", opener=_FakeHttpOpener([RuntimeError("local HTTP failure")])
        )
        with self.assertRaisesRegex(RuntimeError, "local HTTP failure"):
            unexpected.embed(QwenEmbeddingRequest(role="query", texts=("查询",)))

    def test_adapter_preserves_issued_attempt_accounting_and_redacts_secret(self) -> None:
        root = Path("data/retrieval/.qwen-dashscope-transport-test")
        if root.exists():
            shutil.rmtree(root)
        try:
            (root / "ru/artifacts").mkdir(parents=True)
            unit = QwenEmbeddingPreflightTests._unit("u0", "文档", 0)
            units_body = gzip.compress(canonical_json_bytes(unit) + b"\n", mtime=0)
            skip_body = gzip.compress(b"", mtime=0)
            failure_body = canonical_json_bytes({"status": "clear", "failure_count": 0})
            (root / "ru/artifacts/retrieval_units.jsonl.gz").write_bytes(units_body)
            (root / "ru/artifacts/skip_ledger.jsonl.gz").write_bytes(skip_body)
            (root / "ru/metadata").mkdir()
            (root / "ru/metadata/failure_ledger.json").write_bytes(failure_body)
            manifest = {
                "schema_version": "phase04-retrieval-unit-build-0.1",
                "status": "complete",
                "build_identity": "dashscope-fixture-ru",
                "canonical_input": {"canonical_run_id": "fixture", "canonical_input_identity": "fixture", "canonical_schema_version": "phase03-draft-0.1"},
                "artifacts": {
                    "retrieval_units": QwenEmbeddingPreflightTests._descriptor("artifacts/retrieval_units.jsonl.gz", units_body, 1),
                    "skip_ledger": QwenEmbeddingPreflightTests._descriptor("artifacts/skip_ledger.jsonl.gz", skip_body, 0),
                    "failure_ledger": QwenEmbeddingPreflightTests._descriptor("metadata/failure_ledger.json", failure_body),
                },
            }
            (root / "ru/metadata/manifest.json").write_bytes(canonical_json_bytes(manifest))
            observed = []

            def observe_issuance() -> None:
                ledger = root / "run/metadata/provider_attempts.jsonl"
                observed.append([json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()])

            opener = _FakeHttpOpener([
                _FakeHttpResponse(self._provider_response(1, role="document")),
                _FakeHttpResponse(self._provider_response(1, role="query")),
            ], on_open=observe_issuance)
            transport = DashScopeQwenEmbeddingTransport(self._config(), "test-secret", opener=opener)
            result = run_qwen_synchronous_preflight(
                QwenSynchronousPreflightConfig(root / "ru/metadata/manifest.json", ("u0",), "查询"),
                root / "run",
                transport,
            )
            self.assertEqual(result["status"], "complete")
            self.assertEqual(observed[0][-1]["status"], "issued")
            self.assertEqual(observed[1][-1]["status"], "issued")
            ledger = [json.loads(line) for line in (root / "run/metadata/provider_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["status"] for row in ledger], ["succeeded", "succeeded"])
            manifest_text = (root / "run/metadata/preflight_manifest.json").read_text(encoding="utf-8")
            self.assertIn("dashscope", manifest_text)
            self.assertNotIn("test-secret", manifest_text)
            self.assertNotIn("DASHSCOPE_API_KEY", manifest_text)
        finally:
            if root.exists():
                shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
