from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from genshin_corpus.generation.generation import (
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    BailianTransportResponse,
    DEFAULT_GENERATION_INSTRUCTION,
    GenerationResult,
    project_generation_request,
    validate_citations,
)
from genshin_corpus.retrieval.rerank_fusion_semantic_validation import (
    CHALLENGER_MODEL_ID,
    CHALLENGER_THINKING_BUDGET,
    FUSED_PACKET_SHA256,
    LIVE_BUDGET,
    PRIOR_SEMANTIC_REQUEST_IDENTITY,
    SELECTED_QUESTION_IDS,
    SemanticValidationError,
    V02_GENERATION_INSTRUCTION,
    _challenger_config,
    _historical_endpoint_workspace,
    _persist_live_occurrence,
    challenger_execution_config_identity,
    challenger_execution_config_projection,
    compute_preflight_identity,
    experimental_instruction_binding,
    instruction_only_semantic_delta,
    run_live_probe,
    run_preflight,
    validate_live_budget,
)


class FakeProvider:
    def __init__(self, execution_config_identity: str, *, fail_at: int | None = None, bad_citation_at: int | None = None) -> None:
        self.execution_config_identity = execution_config_identity
        self.fail_at = fail_at
        self.bad_citation_at = bad_citation_at
        self.calls = []
        self.provider_network_calls = 0

    def generate(self, request):
        self.calls.append(request)
        self.provider_network_calls += 1
        attempt = len(self.calls)
        if self.fail_at == attempt:
            return GenerationResult(
                execution_status="provider_error",
                answer_text=None,
                citation_validation=None,
                semantic_request_identity=request.semantic_request_identity,
                execution_config_identity=self.execution_config_identity,
                request_audit=request.audit_projection(),
                provider_audit={"attempts": [{"attempt": 1, "provider_request_id": "fake-failure", "usage": {"total_tokens": 7}}]},
            )
        answer = "[E01] final answer"
        if self.bad_citation_at == attempt:
            answer = "[E999] unsupported"
        return GenerationResult(
            execution_status="succeeded",
            answer_text=answer,
            citation_validation=validate_citations(answer, request),
            semantic_request_identity=request.semantic_request_identity,
            execution_config_identity=self.execution_config_identity,
            request_audit=request.audit_projection(),
            provider_audit={"attempts": [{"attempt": 1, "provider_request_id": f"fake-{attempt}", "usage": {"total_tokens": 7, "reasoning_tokens": 4}}]},
        )


class RaisingProvider:
    def __init__(self) -> None:
        self.calls = []
        self.provider_network_calls = 0

    def generate(self, request):
        self.calls.append(request)
        self.provider_network_calls += 1
        raise RuntimeError("injected provider failure")


class RawResponse:
    def __init__(self, body: object) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class RawOpener:
    def __init__(self, body: object) -> None:
        self.body = body
        self.requests = []

    def open(self, request, timeout: float):
        self.requests.append(request)
        return RawResponse(self.body)


class RerankFusionSemanticValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.preflight_root = Path(f".local/test-qwen38-thinking-preflight-{uuid4().hex}")
        cls.preflight = run_preflight(output_root=cls.preflight_root)
        cls.execution_identity = cls.preflight["challenger_execution_config_identity"]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.preflight_root, ignore_errors=True)

    def _output(self, label: str) -> Path:
        path = Path(f".local/test-qwen38-thinking-{label}-{uuid4().hex}")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def test_v01_default_instruction_is_unchanged_and_v02_is_generic(self) -> None:
        self.assertEqual(DEFAULT_GENERATION_INSTRUCTION.instruction_id, "evidence_grounded_answer")
        self.assertEqual(DEFAULT_GENERATION_INSTRUCTION.version, "0.1")
        self.assertEqual(
            DEFAULT_GENERATION_INSTRUCTION.text,
            "仅依据提供的证据回答问题。可以综合多条证据进行自然叙述。"
            "对关键陈述使用 [E01] 形式的证据引用；不得引用未提供的证据。"
            "如果证据不能支持回答，请明确说明。",
        )
        self.assertEqual(V02_GENERATION_INSTRUCTION.instruction_id, "evidence_grounded_answer")
        self.assertEqual(V02_GENERATION_INSTRUCTION.version, "0.2")
        self.assertTrue(V02_GENERATION_INSTRUCTION.text.startswith(DEFAULT_GENERATION_INSTRUCTION.text))
        for forbidden in ("Q056", "Q068", "原神", "瓦萨克拉胡巴肯", "察阿克", "悖谬", "明晨之镜", "重排", "融合"):
            self.assertNotIn(forbidden, V02_GENERATION_INSTRUCTION.text)
        self.assertIn("确切关系或身份层级", V02_GENERATION_INSTRUCTION.text)
        self.assertIn("冲突、推测、暂定观点或后续明确揭示", V02_GENERATION_INSTRUCTION.text)

    def test_v01_v02_keep_question_and_evidence_text_order_identical(self) -> None:
        for row in self.preflight["rows"]:
            question_id = row["question_id"]
            packet = json.loads(Path(row["fused_packet"]["path"]).read_text(encoding="utf-8"))
            v01 = project_generation_request(packet, question=row["question"], question_id=question_id)
            v02 = project_generation_request(
                packet,
                question=row["question"],
                question_id=question_id,
                instruction=V02_GENERATION_INSTRUCTION,
            )
            self.assertEqual(v01.question, v02.question)
            self.assertEqual(
                [evidence.to_dict() for evidence in v01.evidence],
                [evidence.to_dict() for evidence in v02.evidence],
            )
            self.assertEqual(v01.citation_policy.to_dict(), v02.citation_policy.to_dict())
            self.assertEqual(v01.semantic_request_identity, PRIOR_SEMANTIC_REQUEST_IDENTITY[question_id])
            self.assertEqual(v02.semantic_request_identity, row["v02_semantic_request_identity"])
            self.assertEqual(row["instruction_only_semantic_delta"]["differing_subtrees"], ["instruction"])
            self.assertEqual(
                set(row["instruction_only_semantic_delta"]["unchanged_subtree_sha256"]),
                {"schema_version", "question", "evidence", "citation_policy", "evidence_packet_schema_version"},
            )

    def test_exact_two_question_packet_hashes_and_instruction_only_semantic_delta(self) -> None:
        self.assertEqual(SELECTED_QUESTION_IDS, ("Q056", "Q068"))
        self.assertEqual(self.preflight["selected_question_ids"], ["Q056", "Q068"])
        self.assertEqual(len(self.preflight["rows"]), 2)
        for row in self.preflight["rows"]:
            question_id = row["question_id"]
            self.assertEqual(row["effective_input_binding"]["fused_packet_sha256"], FUSED_PACKET_SHA256[question_id])
            self.assertEqual(row["v01_semantic_request_identity"], PRIOR_SEMANTIC_REQUEST_IDENTITY[question_id])
            self.assertNotEqual(row["v02_semantic_request_identity"], PRIOR_SEMANTIC_REQUEST_IDENTITY[question_id])
            self.assertEqual(row["instruction_only_semantic_delta"]["differing_subtrees"], ["instruction"])
            self.assertNotEqual(
                row["instruction_only_semantic_delta"]["v01_semantic_projection_sha256"],
                row["instruction_only_semantic_delta"]["v02_semantic_projection_sha256"],
            )

    def test_exact_challenger_config_and_budget_are_provider_free(self) -> None:
        validate_live_budget()
        config = self.preflight["challenger_execution_config"]
        self.assertEqual(config["model_id"], CHALLENGER_MODEL_ID)
        self.assertEqual(config["thinking_budget"], CHALLENGER_THINKING_BUDGET)
        self.assertTrue(config["enable_thinking"])
        self.assertEqual(config["max_output_tokens"], 2048)
        self.assertFalse(config["stream"])
        self.assertEqual(config["instruction_identity"], V02_GENERATION_INSTRUCTION.identity)
        self.assertEqual(self.preflight["experimental_instruction"], experimental_instruction_binding())
        self.assertEqual(self.preflight["challenger_execution_config_identity"], "88aafe9aefb9b2b8869dbb522b2ccbd0104fca036d3a5c7a18ca094eca6e8a0d")
        self.assertEqual(LIVE_BUDGET.max_generation_calls, 2)
        self.assertEqual(self.preflight["zero_provider_accounting"], {"provider_network_calls_during_preflight": 0, "generation": 0, "network": 0, "rerank": 0, "embedding": 0})

    def test_v02_semantic_identities_are_new_and_deterministically_reproducible(self) -> None:
        repeated = run_preflight(output_root=self._output("repeat"))
        self.assertEqual(
            [row["v02_semantic_request_identity"] for row in repeated["rows"]],
            [row["v02_semantic_request_identity"] for row in self.preflight["rows"]],
        )
        self.assertNotEqual(
            [row["v02_semantic_request_identity"] for row in repeated["rows"]],
            [row["v01_semantic_request_identity"] for row in repeated["rows"]],
        )

    def test_thinking_budget_changes_execution_identity_and_null_preserves_baseline_identity(self) -> None:
        endpoint, workspace = _historical_endpoint_workspace()
        challenger = _challenger_config(endpoint=endpoint, workspace=workspace)
        changed = BailianControlConfig(
            region="cn-beijing", endpoint=endpoint, workspace=workspace,
            model_id=CHALLENGER_MODEL_ID, model_reference_policy="requested_alias",
            enable_thinking=True, thinking_budget=2048, max_output_tokens=2048,
            max_attempts=1, retry_backoff_seconds=0.0,
        )
        baseline_null = BailianControlConfig(
            region="cn-beijing", endpoint=endpoint, workspace=workspace,
            max_output_tokens=2048, max_attempts=1,
        )
        self.assertNotEqual(challenger.execution_config_identity, changed.execution_config_identity)
        self.assertEqual(
            baseline_null.execution_config_identity,
            "0355068093b5a31e164edf24ff8c6df7d2f30769238ef6942aab2c89d82ccb35",
        )
        self.assertNotIn("thinking_budget", baseline_null.output_affecting_projection())

    def test_qwen38_wire_has_thinking_budget_and_no_null_budget_baseline_wire_change(self) -> None:
        endpoint = "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        challenger = _challenger_config(endpoint=endpoint, workspace="workspace-a")
        wire = BailianOpenAICompatibleTransport.wire_payload({
            "model": challenger.model_id,
            "messages": [{"role": "user", "content": "question"}],
            "generation_parameters": {
                "enable_thinking": challenger.enable_thinking,
                "thinking_budget": challenger.thinking_budget,
                "temperature": challenger.temperature,
                "max_output_tokens": challenger.max_output_tokens,
            },
        })
        self.assertEqual(wire["model"], "qwen3.8-max")
        self.assertTrue(wire["enable_thinking"])
        self.assertEqual(wire["thinking_budget"], 4096)
        self.assertEqual(wire["temperature"], 0.0)
        self.assertEqual(wire["max_tokens"], 2048)
        self.assertFalse(wire["stream"])
        baseline = BailianControlConfig(region="cn-beijing", endpoint=endpoint, workspace="workspace-a", max_attempts=1)
        baseline_wire = BailianOpenAICompatibleTransport.wire_payload({
            "model": baseline.model_id,
            "messages": [{"role": "user", "content": "question"}],
            "generation_parameters": {
                "enable_thinking": baseline.enable_thinking,
                "temperature": baseline.temperature,
                "max_output_tokens": baseline.max_output_tokens,
            },
        })
        self.assertNotIn("thinking_budget", baseline_wire)

    def test_reasoning_content_is_ignored_and_only_final_content_is_persisted(self) -> None:
        endpoint, workspace = _historical_endpoint_workspace()
        config = _challenger_config(endpoint=endpoint, workspace=workspace)
        opener = RawOpener({
            "id": "provider-request",
            "choices": [{"message": {"content": "[E01] final content", "reasoning_content": "must never persist"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 9, "reasoning_tokens": 5},
        })
        transport = BailianOpenAICompatibleTransport(config, "test-key", opener=opener)
        provider = BailianGenerationProvider(config, transport, occurrence_factory=lambda: "occurrence", clock=lambda: "2026-09-13T00:00:00Z")
        output = self._output("reasoning")
        result = run_live_probe(preflight_root=self.preflight_root, output_root=output, provider=provider)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(provider.provider_network_calls, 2)
        self.assertEqual(result["provider_network_calls"], 2)
        persisted = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*.json"))
        self.assertIn("final content", persisted)
        self.assertNotIn("reasoning_content", persisted)
        self.assertNotIn("must never persist", persisted)
        self.assertNotIn("test-key", persisted)
        self.assertNotIn("https://", persisted)

    def test_two_successes_issue_exactly_two_calls_and_persist_incrementally_without_overwrite(self) -> None:
        output = self._output("success")
        provider = FakeProvider(self.execution_identity)
        result = run_live_probe(preflight_root=self.preflight_root, output_root=output, provider=provider)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(result["provider_attempts"], 2)
        self.assertEqual(result["provider_network_calls"], 2)
        self.assertEqual([row["question_id"] for row in result["rows"]], ["Q056", "Q068"])
        artifacts = list((output / "results").glob("*/generation_result.json"))
        self.assertEqual(len(artifacts), 2)
        first = (output / "results" / "Q056" / "generation_result.json").read_bytes()
        self.assertEqual(sha256(first).hexdigest(), sha256(first).hexdigest())
        conflicting = FakeProvider(self.execution_identity, fail_at=1).generate(provider.calls[0])
        with self.assertRaises(SemanticValidationError):
            _persist_live_occurrence(output, "Q056", conflicting)
        self.assertEqual((output / "results" / "Q056" / "generation_result.json").read_bytes(), first)
        with self.assertRaises(SemanticValidationError):
            run_live_probe(preflight_root=self.preflight_root, output_root=output, provider=provider)

    def test_first_failure_stops_second_call_and_valid_provider_result_is_persisted_before_local_failure(self) -> None:
        output = self._output("first-failure")
        provider = FakeProvider(self.execution_identity, bad_citation_at=1)
        result = run_live_probe(preflight_root=self.preflight_root, output_root=output, provider=provider)
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result["provider_attempts"], 1)
        self.assertEqual(result["provider_network_calls"], 1)
        self.assertEqual([row["execution_status"] for row in result["rows"]], ["local_validation_failed", "NOT_RUN"])
        persisted = json.loads((output / "results" / "Q056" / "generation_result.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["result"]["answer_text"], "[E999] unsupported")
        self.assertEqual(persisted["audit"]["provider_execution"]["attempts"][0]["usage"]["reasoning_tokens"], 4)

    def test_provider_error_stops_second_call_and_persists_returned_audit(self) -> None:
        output = self._output("provider-failure")
        provider = FakeProvider(self.execution_identity, fail_at=1)
        result = run_live_probe(preflight_root=self.preflight_root, output_root=output, provider=provider)
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result["provider_network_calls"], 1)
        persisted = json.loads((output / "results" / "Q056" / "generation_result.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["result"]["execution_status"], "provider_error")
        self.assertEqual(persisted["audit"]["provider_execution"]["attempts"][0]["provider_request_id"], "fake-failure")

    def test_provider_exception_stops_second_call_without_fabricating_returned_audit(self) -> None:
        output = self._output("raised-provider-failure")
        provider = RaisingProvider()
        result = run_live_probe(preflight_root=self.preflight_root, output_root=output, provider=provider)
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result["provider_network_calls"], 1)
        self.assertEqual([row["execution_status"] for row in result["rows"]], ["local_failure", "NOT_RUN"])
        self.assertFalse((output / "results" / "Q056" / "generation_result.json").exists())
        persisted = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*.json"))
        self.assertNotIn("provider_request_id", persisted)
        self.assertNotIn("usage", persisted)

    def test_live_provider_setup_failure_writes_no_partial_live_root(self) -> None:
        output = self._output("provider-setup-failure")
        with self.assertRaises(SemanticValidationError):
            run_live_probe(preflight_root=self.preflight_root, output_root=output, environment={})
        self.assertFalse(output.exists())

    def test_preflight_identity_is_repeatable_and_binds_challenger_config(self) -> None:
        kwargs = {
            "source_bindings": {"x": "y"},
            "effective_question_bindings": [{"question_id": "Q056", "fused_packet_sha256": "a" * 64}],
            "implementation_sha256": "b" * 64,
            "challenger_config": challenger_execution_config_projection(),
            "challenger_config_identity": challenger_execution_config_identity(),
        }
        self.assertEqual(compute_preflight_identity(**kwargs), compute_preflight_identity(**kwargs))
        changed = dict(kwargs["challenger_config"], thinking_budget=2048)
        self.assertNotEqual(compute_preflight_identity(**kwargs), compute_preflight_identity(**dict(kwargs, challenger_config=changed)))


if __name__ == "__main__":
    unittest.main()
