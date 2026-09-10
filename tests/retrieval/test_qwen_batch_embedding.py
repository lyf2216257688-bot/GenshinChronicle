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
from genshin_corpus.retrieval.qwen_batch_embedding import (
    QWEN_BATCH_EMBEDDINGS_PATH,
    QWEN_BATCH_JSONL_SCHEMA_VERSION,
    QwenBatchEmbeddingConfig,
    QwenBatchEmbeddingError,
    build_qwen_batch_jsonl,
    build_qwen_batch_records,
    materialize_qwen_batch_results,
    materialize_qwen_batch_results_streaming,
)
from genshin_corpus.retrieval.qwen_batch_lifecycle import (
    QWEN_BATCH_DOWNLOAD_CHUNK_BYTES,
    QWEN_BATCH_LIVE_EXECUTION_MODE,
    QWEN_BEIJING_BATCH_BASE_URL,
    BeijingQwenBatchConfig,
    DashScopeQwenBatchClient,
    QwenBatchLifecycleError,
    QwenBatchLifecycleTransportError,
    resume_qwen_batch_probe,
    submit_qwen_batch_probe,
)
from genshin_corpus.retrieval.qwen_embedding import QWEN_EMBEDDING_DIMENSION, QWEN_EMBEDDING_MODEL_ID


class _FakeBatchLifecycleClient:
    def __init__(self, *, create_error: QwenBatchLifecycleTransportError | None = None) -> None:
        self.create_error = create_error
        self.uploads: list[tuple[bytes, str]] = []
        self.creates: list[dict[str, str]] = []
        self.retrieves: list[str] = []
        self.downloads: list[str] = []
        self.retrieve_result: dict[str, object] = {"id": "batch-1", "status": "in_progress"}
        self.files: dict[str, bytes] = {}

    def _secret_values_for_persistence(self) -> tuple[str, ...]:
        return ("test-secret",)

    def upload_file(self, body: bytes, *, purpose: str) -> str:
        self.uploads.append((body, purpose))
        return "file-input-1"

    def create_batch(self, *, input_file_id: str, endpoint: str, completion_window: str) -> str:
        self.creates.append({"input_file_id": input_file_id, "endpoint": endpoint, "completion_window": completion_window})
        if self.create_error is not None:
            raise self.create_error
        return "batch-1"

    def retrieve_batch(self, batch_id: str) -> dict[str, object]:
        self.retrieves.append(batch_id)
        return self.retrieve_result

    def download_file(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        return self.files[file_id]


class _FakeBatchHttpResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeBatchHttpResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _FakeBatchHttpOpener:
    def __init__(self, outcomes: list[_FakeBatchHttpResponse]) -> None:
        self.outcomes = iter(outcomes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return next(self.outcomes)


class _ChunkedBatchHttpResponse:
    def __init__(self, body: bytes, *, chunk_size: int = 3, fail_after: int | None = None, read_failure: BaseException | None = None, content_length: int | None = None) -> None:
        self.body = body
        self.chunk_size = chunk_size
        self.fail_after = fail_after
        self.read_failure = read_failure
        self.offset = 0
        self.read_sizes: list[int | None] = []
        self.status = 200
        self.headers = {"Content-Length": str(len(body) if content_length is None else content_length)}

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if self.fail_after is not None and self.offset >= self.fail_after:
            if self.read_failure is not None:
                raise self.read_failure
            raise OSError("interrupted")
        end = min(self.offset + min(size, self.chunk_size), len(self.body))
        chunk = self.body[self.offset:end]
        self.offset = end
        return chunk

    def __enter__(self) -> "_ChunkedBatchHttpResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _InterruptedDownloadClient(_FakeBatchLifecycleClient):
    def __init__(self) -> None:
        super().__init__()
        self.download_attempts = 0

    def download_file_to_path(self, file_id: str, output_path: Path) -> dict[str, object]:
        self.downloads.append(file_id)
        self.download_attempts += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        incomplete = output_path.with_name(output_path.name + ".incomplete")
        if self.download_attempts == 1:
            incomplete.write_bytes(b"partial\n")
            raise QwenBatchLifecycleTransportError("ConnectionError", ambiguous=True)
        body = self.files[file_id]
        incomplete.write_bytes(body)
        incomplete.replace(output_path)
        return {"path": str(output_path), "byte_count": len(body), "sha256": hashlib.sha256(body).hexdigest()}


class _RetryingRetrieveClient(_FakeBatchLifecycleClient):
    def __init__(self) -> None:
        super().__init__()
        self.retrieve_attempts = 0

    def retrieve_batch(self, batch_id: str) -> dict[str, object]:
        self.retrieves.append(batch_id)
        self.retrieve_attempts += 1
        if self.retrieve_attempts == 1:
            raise QwenBatchLifecycleTransportError("ConnectionError", ambiguous=True)
        return self.retrieve_result


class _MalformedDownloadClient(_FakeBatchLifecycleClient):
    def download_file_to_path(self, file_id: str, output_path: Path) -> dict[str, object]:
        self.downloads.append(file_id)
        raise QwenBatchLifecycleTransportError("MalformedDownloadChunk", ambiguous=False)


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

    def test_streaming_materialization_is_ordered_and_publishes_relative_artifacts(self) -> None:
        records = build_qwen_batch_records(self.config)
        result_path = self._write_results([self._success_row(records[1], 1), self._success_row(records[0], 0)], "streaming.jsonl")
        output_root = self.root / "streaming-materialized"
        result = materialize_qwen_batch_results_streaming(self.config, result_path, output_root)
        self.assertEqual(result["status"], "complete")
        manifest = json.loads((output_root / "dense/metadata/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["artifacts"]["vectors"]["path"], "artifacts/vectors.f32.npy")
        self.assertEqual(manifest["artifacts"]["rows"]["path"], "artifacts/rows.jsonl.gz")
        self.assertFalse((output_root / "metadata/materialization.incomplete").exists())
        with gzip.open(output_root / "dense/artifacts/rows.jsonl.gz", "rt", encoding="utf-8") as handle:
            self.assertEqual([json.loads(line)["unit_id"] for line in handle], ["u0", "u1"])
        with self.assertRaises(FileExistsError):
            materialize_qwen_batch_results_streaming(self.config, result_path, output_root)

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

    def _one_document_config(self) -> QwenBatchEmbeddingConfig:
        return QwenBatchEmbeddingConfig(self.config.retrieval_unit_manifest_path, ("u0",))

    def test_lifecycle_persists_upload_and_batch_ids_before_returning_success(self) -> None:
        client = _FakeBatchLifecycleClient()
        root = self.root / "lifecycle-submit"
        state = submit_qwen_batch_probe(self._one_document_config(), root, client)
        self.assertEqual(state["status"], "created")
        self.assertEqual(state["upload"]["file_id"], "file-input-1")
        self.assertEqual(state["batch"]["batch_id"], "batch-1")
        self.assertEqual(client.uploads[0][1], "batch")
        self.assertEqual(client.creates, [{"input_file_id": "file-input-1", "endpoint": "/v1/embeddings", "completion_window": "24h"}])
        state_text = (root / "metadata/lifecycle_state.json").read_text(encoding="utf-8")
        self.assertNotIn("test-secret", state_text)
        self.assertNotIn("DASHSCOPE_API_KEY", state_text)

    def test_lifecycle_resume_retrieves_existing_batch_without_replacement(self) -> None:
        client = _FakeBatchLifecycleClient()
        root = self.root / "lifecycle-resume"
        submit_qwen_batch_probe(self._one_document_config(), root, client)
        client.retrieve_result = {"id": "batch-1", "status": "in_progress"}
        state = submit_qwen_batch_probe(self._one_document_config(), root, client)
        self.assertEqual(state["status"], "retrieved")
        self.assertEqual(client.retrieves, ["batch-1"])
        self.assertEqual(len(client.creates), 1)

    def test_ambiguous_create_is_auditable_and_forbids_replacement(self) -> None:
        client = _FakeBatchLifecycleClient(create_error=QwenBatchLifecycleTransportError("ConnectionError", ambiguous=True))
        root = self.root / "lifecycle-ambiguous"
        state = submit_qwen_batch_probe(self._one_document_config(), root, client)
        self.assertEqual(state["status"], "create_ambiguous")
        self.assertEqual(state["batch"]["input_file_id"], "file-input-1")
        with self.assertRaisesRegex(QwenBatchLifecycleError, "forbids automatic replacement"):
            submit_qwen_batch_probe(self._one_document_config(), root, client)
        self.assertEqual(len(client.creates), 1)

    def test_terminal_batches_do_not_trigger_retry_or_replacement(self) -> None:
        for provider_status in ("failed", "expired", "cancelled"):
            with self.subTest(provider_status=provider_status):
                client = _FakeBatchLifecycleClient()
                root = self.root / f"lifecycle-{provider_status}"
                submit_qwen_batch_probe(self._one_document_config(), root, client)
                client.retrieve_result = {"id": "batch-1", "status": provider_status}
                state = resume_qwen_batch_probe(self._one_document_config(), root, client)
                self.assertEqual(state["status"], f"terminal_{provider_status}")
                self.assertEqual(len(client.creates), 1)
                self.assertEqual(client.downloads, [])

    def test_completed_batch_downloads_output_and_error_then_materializes(self) -> None:
        config = self._one_document_config()
        client = _FakeBatchLifecycleClient()
        root = self.root / "lifecycle-completed"
        submit_qwen_batch_probe(config, root, client)
        record = build_qwen_batch_records(config)[0]
        client.retrieve_result = {
            "id": "batch-1",
            "status": "completed",
            "output_file_id": "file-output-1",
            "error_file_id": "file-error-1",
        }
        client.files = {
            "file-output-1": canonical_json_bytes(self._success_row(record, 0)) + b"\n",
            "file-error-1": canonical_json_bytes({"custom_id": record["custom_id"], "error": {"code": "NoErrorRows"}}) + b"\n",
        }
        state = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(state["status"], "materialized")
        self.assertEqual(client.downloads, ["file-output-1", "file-error-1"])
        self.assertEqual(state["batch"]["error_file_id"], "file-error-1")
        self.assertTrue((root / "downloads/output.jsonl").is_file())
        self.assertTrue((root / "downloads/error.jsonl").is_file())
        manifest = json.loads((root / "materialized/dense/metadata/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["embedding_dimension"], 2048)
        state_body = (root / "metadata/lifecycle_state.json").read_bytes()
        manifest_body = (root / "materialized/dense/metadata/manifest.json").read_bytes()
        resumed = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(resumed["status"], "materialized")
        self.assertEqual(resumed["batch"]["batch_id"], "batch-1")
        self.assertEqual(len(client.uploads), 1)
        self.assertEqual(len(client.creates), 1)
        self.assertEqual(client.downloads, ["file-output-1", "file-error-1"])
        self.assertEqual((root / "metadata/lifecycle_state.json").read_bytes(), state_body)
        self.assertEqual((root / "materialized/dense/metadata/manifest.json").read_bytes(), manifest_body)
        self.assertEqual(state["probe_evidence"]["provider_api_status"], "unverified_batch_result_only")
        self.assertEqual(state["probe_evidence"]["batch_2048_wire_acceptance"], "unknown")

    def test_concrete_download_streams_chunks_and_keeps_interrupted_output_incomplete(self) -> None:
        body = b'{"custom_id":"x"}\n'
        response = _ChunkedBatchHttpResponse(body, chunk_size=2)
        client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=_FakeBatchHttpOpener([response])
        )
        path = self.root / "streamed-output.jsonl"
        descriptor = client.download_file_to_path("file-output-1", path)
        self.assertEqual(path.read_bytes(), body)
        self.assertEqual(descriptor["byte_count"], len(body))
        self.assertEqual(descriptor["sha256"], hashlib.sha256(body).hexdigest())
        self.assertTrue(response.read_sizes)
        self.assertTrue(all(size == QWEN_BATCH_DOWNLOAD_CHUNK_BYTES for size in response.read_sizes))
        with self.assertRaises(QwenBatchLifecycleError):
            client.download_file_to_path("file-output-1", path)

        interrupted = _ChunkedBatchHttpResponse(body, chunk_size=2, fail_after=2)
        interrupted_path = self.root / "interrupted-output.jsonl"
        interrupted_client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=_FakeBatchHttpOpener([interrupted])
        )
        with self.assertRaises(QwenBatchLifecycleTransportError):
            interrupted_client.download_file_to_path("file-output-2", interrupted_path)
        self.assertFalse(interrupted_path.exists())
        self.assertTrue(interrupted_path.with_name(interrupted_path.name + ".incomplete").exists())

    def test_concrete_adapter_classifies_timeout_and_connection_reset_as_retryable(self) -> None:
        body = b'{"custom_id":"x"}\n'
        for failure, name in ((TimeoutError("timed out"), "timeout"), (ConnectionResetError("reset"), "reset")):
            with self.subTest(name=name):
                response = _ChunkedBatchHttpResponse(body, chunk_size=2, fail_after=2, read_failure=failure)
                client = DashScopeQwenBatchClient.from_environment(
                    BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=_FakeBatchHttpOpener([response])
                )
                path = self.root / f"{name}-output.jsonl"
                with self.assertRaises(QwenBatchLifecycleTransportError) as raised:
                    client.download_file_to_path("file-output-1", path)
                self.assertEqual(raised.exception.code, "ConnectionError")
                self.assertTrue(path.with_name(path.name + ".incomplete").exists())
                self.assertFalse(path.exists())

    def test_concrete_adapter_short_content_length_is_retryable(self) -> None:
        body = b'{"custom_id":"x"}\n'
        response = _ChunkedBatchHttpResponse(body, content_length=len(body) + 3)
        client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=_FakeBatchHttpOpener([response])
        )
        path = self.root / "short-output.jsonl"
        with self.assertRaises(QwenBatchLifecycleTransportError) as raised:
            client.download_file_to_path("file-output-1", path)
        self.assertEqual(raised.exception.code, "ContentLengthMismatch")
        self.assertTrue(path.with_name(path.name + ".incomplete").exists())
        self.assertFalse(path.exists())

    def test_concrete_adapter_publish_failure_remains_blocked(self) -> None:
        body = b'{"custom_id":"x"}\n'
        response = _ChunkedBatchHttpResponse(body)
        client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=_FakeBatchHttpOpener([response])
        )
        path = self.root / "publish-failure.jsonl"
        with patch("genshin_corpus.retrieval.qwen_batch_lifecycle.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(QwenBatchLifecycleTransportError) as raised:
                client.download_file_to_path("file-output-1", path)
        self.assertEqual(raised.exception.code, "DownloadPublishError")
        self.assertFalse(path.exists())

    def test_concrete_adapter_resume_restarts_same_output_after_timeout(self) -> None:
        config = self._one_document_config()
        record = build_qwen_batch_records(config)[0]
        output = canonical_json_bytes(self._success_row(record, 0)) + b"\n"
        opener = _FakeBatchHttpOpener([
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "file-input-1"})),
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1"})),
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"})),
            _ChunkedBatchHttpResponse(output, chunk_size=2, fail_after=2, read_failure=TimeoutError("timed out")),
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"})),
            _ChunkedBatchHttpResponse(output, chunk_size=2),
        ])
        client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=opener
        )
        root = self.root / "adapter-resume"
        submit_qwen_batch_probe(config, root, client)
        first = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(first["status"], "output_download_retryable")
        self.assertFalse((root / "downloads/output.jsonl").exists())
        second = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(second["status"], "materialized")
        self.assertEqual(second["batch"]["batch_id"], "batch-1")
        self.assertEqual(len(opener.requests), 6)

    def test_interrupted_download_is_retryable_without_reissuing_batch(self) -> None:
        config = self._one_document_config()
        record = build_qwen_batch_records(config)[0]
        client = _InterruptedDownloadClient()
        client.retrieve_result = {"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"}
        client.files["file-output-1"] = canonical_json_bytes(self._success_row(record, 0)) + b"\n"
        root = self.root / "retryable-download"
        submit_qwen_batch_probe(config, root, client)
        first = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(first["status"], "output_download_retryable")
        self.assertFalse((root / "downloads/output.jsonl").exists())
        self.assertTrue((root / "downloads/output.jsonl.incomplete").exists())
        second = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(second["status"], "materialized")
        self.assertEqual(len(client.uploads), 1)
        self.assertEqual(len(client.creates), 1)
        self.assertEqual(client.downloads, ["file-output-1", "file-output-1"])

    def test_transient_retrieve_failure_is_read_only_retryable(self) -> None:
        config = self._one_document_config()
        client = _RetryingRetrieveClient()
        root = self.root / "retryable-retrieve"
        submit_qwen_batch_probe(config, root, client)
        first = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(first["status"], "retrieve_retryable")
        client.retrieve_result = {"id": "batch-1", "status": "in_progress"}
        second = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(second["status"], "retrieved")
        self.assertEqual(len(client.uploads), 1)
        self.assertEqual(len(client.creates), 1)
        self.assertEqual(client.retrieves, ["batch-1", "batch-1"])

    def test_malformed_download_remains_blocked(self) -> None:
        config = self._one_document_config()
        client = _MalformedDownloadClient()
        client.retrieve_result = {"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"}
        root = self.root / "malformed-download"
        submit_qwen_batch_probe(config, root, client)
        first = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(first["status"], "output_download_failed")
        second = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(second["status"], "output_download_failed")
        self.assertEqual(client.downloads, ["file-output-1"])
        self.assertEqual(len(client.creates), 1)

    def test_injected_concrete_dashscope_client_remains_offline_unverified(self) -> None:
        config = self._one_document_config()
        record = build_qwen_batch_records(config)[0]
        output = canonical_json_bytes(self._success_row(record, 0)) + b"\n"
        opener = _FakeBatchHttpOpener([
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "file-input-1"})),
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1"})),
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"})),
            _FakeBatchHttpResponse(output),
        ])
        client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=opener
        )
        root = self.root / "concrete-offline"
        with self.assertRaisesRegex(QwenBatchLifecycleError, "non-injected"):
            submit_qwen_batch_probe(config, root, client, execution_mode=QWEN_BATCH_LIVE_EXECUTION_MODE)
        self.assertEqual(opener.requests, [])
        submit_qwen_batch_probe(config, root, client)
        state = resume_qwen_batch_probe(config, root, client)
        self.assertEqual(state["status"], "materialized")
        self.assertEqual(state["probe_evidence"]["provider_api_status"], "unverified_batch_result_only")
        self.assertEqual(state["probe_evidence"]["batch_2048_wire_acceptance"], "unknown")
        self.assertEqual(len(opener.requests), 4)

    def test_explicit_live_evidence_requires_completed_downloaded_valid_materialization(self) -> None:
        config = self._one_document_config()
        record = build_qwen_batch_records(config)[0]
        output_row = self._success_row(record, 0)
        del output_row["response"]["body"]["model"]
        opener = _FakeBatchHttpOpener([
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "file-input-1"})),
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1"})),
            _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"})),
            _FakeBatchHttpResponse(canonical_json_bytes(output_row) + b"\n"),
        ])
        client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=opener
        )
        root = self.root / "explicit-live-evidence"
        # Unit-test the explicit live branch without treating the injected
        # opener as real provider evidence in ordinary lifecycle tests.
        with patch.object(client, "_live_evidence_eligible", True):
            submit_qwen_batch_probe(config, root, client, execution_mode=QWEN_BATCH_LIVE_EXECUTION_MODE)
            state = resume_qwen_batch_probe(config, root, client, execution_mode=QWEN_BATCH_LIVE_EXECUTION_MODE)
        evidence = state["probe_evidence"]
        self.assertEqual(evidence["provider_api_status"], "live_beijing_batch_succeeded")
        self.assertEqual(evidence["batch_2048_wire_acceptance"], "verified")
        self.assertEqual(evidence["requested_model_identity"], QWEN_EMBEDDING_MODEL_ID)
        self.assertIsNone(evidence["returned_model_identity"])
        self.assertEqual(evidence["validated_embedding_dimension"], 2048)

    def test_non_successful_live_paths_never_record_verified_evidence(self) -> None:
        config = self._one_document_config()
        record = build_qwen_batch_records(config)[0]
        cases = {
            "failed": [
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "file-input-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1", "status": "failed"})),
            ],
            "malformed": [
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "file-input-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"custom_id": record["custom_id"], "response": {"status_code": 200, "body": {"data": []}}}) + b"\n"),
            ],
            "partial": [
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "file-input-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"id": "batch-1", "status": "completed", "output_file_id": "file-output-1"})),
                _FakeBatchHttpResponse(canonical_json_bytes({"custom_id": record["custom_id"], "response": None, "error": {"code": "PartialFailure"}}) + b"\n"),
            ],
        }
        for name, outcomes in cases.items():
            with self.subTest(name=name):
                client = DashScopeQwenBatchClient.from_environment(
                    BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}, opener=_FakeBatchHttpOpener(outcomes)
                )
                with patch.object(client, "_live_evidence_eligible", True):
                    submit_qwen_batch_probe(config, self.root / f"live-{name}", client, execution_mode=QWEN_BATCH_LIVE_EXECUTION_MODE)
                    state = resume_qwen_batch_probe(config, self.root / f"live-{name}", client, execution_mode=QWEN_BATCH_LIVE_EXECUTION_MODE)
                self.assertNotEqual(state["probe_evidence"]["provider_api_status"], "live_beijing_batch_succeeded")
                self.assertEqual(state["probe_evidence"]["batch_2048_wire_acceptance"], "unknown")

    def test_beijing_origin_and_environment_boundary_are_fixed_and_secret_free(self) -> None:
        with self.assertRaisesRegex(QwenBatchLifecycleError, "Beijing"):
            BeijingQwenBatchConfig(base_url="https://cn-beijing.example.invalid/compatible-mode/v1")
        with self.assertRaisesRegex(QwenBatchLifecycleError, "DASHSCOPE_API_KEY"):
            BeijingQwenBatchConfig(api_key_env="OTHER_API_KEY")
        client = DashScopeQwenBatchClient.from_environment(
            BeijingQwenBatchConfig(), environment={"DASHSCOPE_API_KEY": "test-secret"}
        )
        self.assertEqual(client.identity_projection()["base_url"], QWEN_BEIJING_BATCH_BASE_URL)
        self.assertNotIn("test-secret", json.dumps(client.identity_projection()))


if __name__ == "__main__":
    unittest.main()
