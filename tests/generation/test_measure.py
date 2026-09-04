import json
import shutil
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.generation.generation import BailianTransportResponse
from genshin_corpus.generation.measure import (
    AcceptedQuestion,
    M1Baseline,
    M1MeasureError,
    load_accepted_questions,
    preflight_m1,
    run_m1_measure,
    write_question_packets,
)
from genshin_corpus.retrieval.candidate_retrieval import DEFAULT_DENSE_MODEL_REVISION


class _FakeTransport:
    def __init__(self) -> None:
        self.payloads = []

    def invoke(self, payload, *, timeout_seconds):
        self.payloads.append(dict(payload))
        return BailianTransportResponse("答案 [E01]", provider_request_id=f"request-{len(self.payloads)}")


class M1MeasureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("tmp/.m1-measure-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.baseline = M1Baseline(root=self.root / "baseline", model_dir=self.root / "model")
        self.baseline.model_dir.mkdir(parents=True)
        weights = b"fixture-model-weights"
        (self.baseline.model_dir / "model.safetensors").write_bytes(weights)
        self._write_baseline_manifest(self.baseline.retrieval_unit_manifest, {
            "status": "complete", "build_identity": "ru-build",
        })
        self._write_baseline_manifest(self.baseline.lexical_manifest, {
            "status": "complete", "arm_build_identity": "lex-build", "retrieval_unit_build_identity": "ru-build",
        })
        self._write_baseline_manifest(self.baseline.dense_manifest, {
            "status": "complete", "arm_build_identity": "dense-build", "retrieval_unit_build_identity": "ru-build",
            "model_revision": DEFAULT_DENSE_MODEL_REVISION, "model_sha256": sha256(weights).hexdigest(),
            "embedding_dimension": 512, "instruction": "为这个句子生成表示以用于检索相关文章：",
        })

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _write_baseline_manifest(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(value))

    def _write_questions(self, rows: list[dict]) -> Path:
        path = self.root / "questions.accepted.jsonl"
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
        return path

    @staticmethod
    def _packet(question, mode):
        return {
            "schema_version": "phase04-evidence-packet-0.1",
            "evidence": [{"evidence_id": "E01", "text": f"证据 {mode}", "members": []}],
            "retrieval_audit": {"retrieval_metadata": {"query_id": question, "mode": mode}},
            "budget": {"omitted_blocks": []},
        }

    def test_strict_accepted_question_schema_and_identity(self) -> None:
        sentinel = "SOURCE_ANSWER_SENTINEL"
        for forbidden_key in ("answer", "explanation", "reference", "transcript"):
            path = self._write_questions([{"question_id": "q01", "question": "问题", forbidden_key: sentinel}])
            with self.assertRaisesRegex(M1MeasureError, "exactly"):
                load_accepted_questions(path, required_count=1)
        first = AcceptedQuestion("q01", "精确问题")
        second = AcceptedQuestion("q01", "精确问题？")
        self.assertNotEqual(first.question_identity, second.question_identity)

    def test_preflight_stops_without_accepted_input(self) -> None:
        result = preflight_m1(self.root / "missing.accepted.jsonl", baseline=self.baseline)
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["provider_attempts_issued"], 0)

    def test_missing_dense_runtime_is_not_ready_before_live_execution(self) -> None:
        missing = M1Baseline(
            root=self.baseline.root,
            model_dir=self.baseline.model_dir,
            runtime_root=self.root / "missing-runtime",
        )
        questions = self._write_questions([
            {"question_id": f"q{index:02d}", "question": f"问题 {index}"}
            for index in range(1, 7)
        ])
        result = preflight_m1(questions, baseline=missing)
        self.assertEqual(result["status"], "not_ready")
        self.assertIn("runtime root", result["preflight_error"])
        output = self.root / "blocked-run"
        transport = _FakeTransport()
        with self.assertRaises(M1MeasureError):
            run_m1_measure(
                questions,
                output,
                baseline=missing,
                environment={"BAILIAN_BASE_URL": "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"},
                transport_factory=lambda config, environment: transport,
            )
        self.assertFalse(output.exists())
        self.assertEqual(transport.payloads, [])

    def test_unusable_dense_query_encoder_is_not_ready(self) -> None:
        questions = self._write_questions([
            {"question_id": f"q{index:02d}", "question": f"问题 {index}"}
            for index in range(1, 7)
        ])
        runtime_root = self.root / "runtime"
        runtime_root.mkdir()
        baseline = M1Baseline(
            root=self.baseline.root,
            model_dir=self.baseline.model_dir,
            runtime_root=runtime_root,
        )
        with patch(
            "genshin_corpus.generation.measure.encode_dense_query",
            side_effect=RuntimeError("sentence_transformers unavailable"),
        ):
            result = preflight_m1(questions, baseline=baseline)
        self.assertEqual(result["status"], "not_ready")
        self.assertIn("runtime/model", result["preflight_error"])

    def test_healthy_injected_dense_runtime_probe_reaches_ready(self) -> None:
        questions = self._write_questions([
            {"question_id": f"q{index:02d}", "question": f"问题 {index}"}
            for index in range(1, 7)
        ])
        with patch(
            "genshin_corpus.generation.measure._probe_dense_query_runtime",
            return_value={"runtime_root": "injected", "embedding_dimension": 512},
        ):
            result = preflight_m1(questions, baseline=self.baseline)
        self.assertEqual(result["status"], "ready_to_execute")
        self.assertEqual(result["accepted_question_count"], 6)

    def test_revision_mismatch_stops_m1_before_batch_model_load(self) -> None:
        questions = self._write_questions([
            {"question_id": f"q{index:02d}", "question": f"问题 {index}"}
            for index in range(1, 7)
        ])
        manifest = json.loads(self.baseline.dense_manifest.read_text(encoding="utf-8"))
        manifest["model_revision"] = "untrusted-revision"
        self.baseline.dense_manifest.write_bytes(canonical_json_bytes(manifest))
        transport = _FakeTransport()
        result = preflight_m1(questions, baseline=self.baseline)
        self.assertEqual(result["status"], "not_ready")
        self.assertIn("revision", result["preflight_error"])
        with patch("genshin_corpus.generation.measure.load_dense_query_model") as load_model:
            with self.assertRaises(M1MeasureError):
                run_m1_measure(
                    questions,
                    self.root / "revision-blocked-run",
                    baseline=self.baseline,
                    environment={"BAILIAN_BASE_URL": "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"},
                    transport_factory=lambda config, environment: transport,
                )
        load_model.assert_not_called()
        self.assertFalse((self.root / "revision-blocked-run").exists())
        self.assertEqual(transport.payloads, [])

    def test_packet_write_is_deterministic_and_conflict_safe(self) -> None:
        packets = {mode: self._packet("q01", mode) for mode in ("lexical", "dense", "hybrid")}
        output = self.root / "packets"
        first = write_question_packets(output, packets)
        second = write_question_packets(output, packets)
        self.assertEqual(first, second)
        changed = dict(packets)
        changed["hybrid"] = self._packet("q01", "changed")
        with self.assertRaises(FileExistsError):
            write_question_packets(output, changed)

    def test_six_question_run_keeps_reference_sentinel_out_and_never_overwrites(self) -> None:
        questions = self._write_questions([
            {"question_id": f"q{index:02d}", "question": f"问题 {index}"}
            for index in range(1, 7)
        ])
        transport = _FakeTransport()

        def fake_packets(question, baseline, dense_model):
            return {mode: self._packet(question.question_id, mode) for mode in ("lexical", "dense", "hybrid")}

        output = self.root / "run"
        configs = []
        with patch("genshin_corpus.generation.measure._probe_dense_query_runtime", return_value={"runtime_root": "injected", "embedding_dimension": 512}), patch("genshin_corpus.generation.measure.load_dense_query_model", return_value=object()), patch("genshin_corpus.generation.measure._packet_for_question", side_effect=fake_packets):
            result = run_m1_measure(
                questions,
                output,
                baseline=self.baseline,
                environment={"BAILIAN_BASE_URL": "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"},
                transport_factory=lambda config, environment: (configs.append(config), transport)[1],
            )
        self.assertEqual(result["provider_attempts_issued"], 6)
        self.assertEqual(len(transport.payloads), 6)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].max_attempts, 1)
        for payload in transport.payloads:
            self.assertEqual(payload["model"], "qwen3.7-plus-2026-05-26")
            self.assertFalse(payload["generation_parameters"]["enable_thinking"])
        visible = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*.json") if path.name != "manifest.json")
        self.assertNotIn("SOURCE_ANSWER_SENTINEL", visible)
        self.assertEqual(len((output / "review.jsonl").read_text(encoding="utf-8").splitlines()), 6)
        with patch("genshin_corpus.generation.measure._probe_dense_query_runtime", return_value={"runtime_root": "injected", "embedding_dimension": 512}), patch("genshin_corpus.generation.measure.load_dense_query_model", return_value=object()), patch("genshin_corpus.generation.measure._packet_for_question") as packets:
            with self.assertRaises(FileExistsError):
                run_m1_measure(
                    questions,
                    output,
                    baseline=self.baseline,
                    environment={"BAILIAN_BASE_URL": "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"},
                    transport_factory=lambda config, environment: transport,
                )
        packets.assert_not_called()
        self.assertEqual(len(transport.payloads), 6)

    def test_six_question_batch_reuses_one_dense_model_and_vector_per_question(self) -> None:
        questions = self._write_questions([
            {"question_id": f"q{index:02d}", "question": f"问题 {index}"}
            for index in range(1, 7)
        ])
        transport = _FakeTransport()
        model = object()
        encoded: list[tuple[str, object, str]] = []
        retrieval_calls: list[tuple[str, dict]] = []

        def fake_encode(model_dir, query, *, instruction, model):
            self.assertEqual(model_dir, self.baseline.model_dir)
            encoded.append((query, model, instruction))
            return [1.0, 0.0]

        def fake_retrieve(mode, **kwargs):
            retrieval_calls.append((mode, kwargs))
            return [{"unit_id": "u1", "rank": 1, "retrieval": {"mode": mode}}]

        def fake_assemble(manifest_path, candidates, *, config, retrieval_audit):
            return self._packet(retrieval_audit["query_id"], retrieval_audit["mode"])

        with patch("genshin_corpus.generation.measure._probe_dense_query_runtime", return_value={"runtime_root": "injected", "embedding_dimension": 512}), patch("genshin_corpus.generation.measure.load_dense_query_model", return_value=model) as load_model, patch("genshin_corpus.generation.measure.encode_dense_query", side_effect=fake_encode), patch("genshin_corpus.generation.measure.retrieve_candidates", side_effect=fake_retrieve), patch("genshin_corpus.generation.measure.assemble_evidence_packet", side_effect=fake_assemble):
            result = run_m1_measure(
                questions,
                self.root / "reuse-run",
                baseline=self.baseline,
                environment={"BAILIAN_BASE_URL": "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"},
                transport_factory=lambda config, environment: transport,
            )

        self.assertEqual(result["provider_attempts_issued"], 6)
        load_model.assert_called_once_with(self.baseline.model_dir)
        self.assertEqual(len(encoded), 6)
        self.assertEqual({id(item[1]) for item in encoded}, {id(model)})
        self.assertEqual([item[0] for item in encoded], [f"问题 {index}" for index in range(1, 7)])
        self.assertEqual(len(retrieval_calls), 18)
        for mode, kwargs in retrieval_calls:
            self.assertNotIn("model_dir", kwargs)
            self.assertEqual(kwargs["instruction"], "为这个句子生成表示以用于检索相关文章：")
            if mode in {"dense", "hybrid"}:
                self.assertEqual(kwargs["query_vector"], [1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
