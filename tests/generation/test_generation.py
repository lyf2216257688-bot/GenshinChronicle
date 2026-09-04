from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from genshin_corpus.generation.generation import (
    BASELINE_QWEN_MODEL_ID,
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    BailianTransportError,
    BailianTransportResponse,
    CitationCoveragePolicy,
    GenerationConfigurationError,
    GenerationContractError,
    GenerationResult,
    CitationValidation,
    project_generation_request,
    run_bailian_control_smoke,
    validate_citations,
    workspace_from_bailian_base_url,
    write_generation_result,
)


class _FakeTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.payloads: list[dict] = []
        self.timeouts: list[float] = []

    def invoke(self, payload: dict, *, timeout_seconds: float) -> BailianTransportResponse:
        self.payloads.append(payload)
        self.timeouts.append(timeout_seconds)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


class _FakeResponse:
    def __init__(self, body: object, *, headers: dict[str, str] | None = None) -> None:
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[object] = []
        self.timeouts: list[float] = []

    def open(self, request: object, timeout: float) -> _FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


class GenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("tmp/.generation-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.packet = {
            "schema_version": "phase04-evidence-packet-0.1",
            "assembly_version": "fixture",
            "retrieval_unit_build": {"build_identity": "ru-fixture"},
            "assembly_config": {"total_context_chars": 10},
            "retrieval_audit": {
                "retrieval_metadata": {"secret_like": "must-not-be-visible"},
                "deduplicated_candidates": [],
            },
            "evidence": [
                {
                    "evidence_id": "E01",
                    "text": "阿贝多是一名炼金术士。",
                    "members": [{
                        "record_context": {"record_title": "阿贝多", "section_name": "角色故事"},
                        "canonical_address": {"record_id": "must-not-be-visible"},
                        "lineage": {"raw_refs": [{"artifact_path": "must-not-be-visible"}]},
                        "provenance": {"private": "must-not-be-visible"},
                        "content_role": {},
                        "structure": {},
                    }],
                },
                {
                    "evidence_id": "E02",
                    "text": "他在蒙德活动。",
                    "members": [{
                        "record_context": {"record_title": "阿贝多", "section_name": "档案"},
                        "structure": {"dialogue": {"speaker": "派蒙"}},
                    }],
                },
            ],
            "budget": {"used_evidence_blocks": 2},
        }
        self.request = project_generation_request(self.packet, question="阿贝多在哪里活动？", question_id="q1")
        self.config = BailianControlConfig(
            region="cn-beijing",
            endpoint="https://workspace.example.invalid",
            workspace="workspace-a",
            timeout_seconds=0.25,
            retry_backoff_seconds=0.0,
        )

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _provider(self, outcomes: list[object], *, config: BailianControlConfig | None = None, sleeps: list[float] | None = None) -> tuple[BailianGenerationProvider, _FakeTransport]:
        transport = _FakeTransport(outcomes)
        ids = iter(["occurrence-1", "occurrence-2"])
        provider = BailianGenerationProvider(
            config or self.config,
            transport,
            occurrence_factory=lambda: next(ids),
            clock=lambda: "2026-09-03T00:00:00Z",
            sleeper=(sleeps.append if sleeps is not None else lambda _: None),
        )
        return provider, transport

    def test_packet_projection_is_provider_neutral_and_excludes_audit_fields(self) -> None:
        self.assertEqual([item.evidence_id for item in self.request.evidence], ["E01", "E02"])
        self.assertEqual(self.request.evidence[0].display_context, ("阿贝多 / 角色故事",))
        semantic = json.dumps(self.request.semantic_projection(), ensure_ascii=False)
        self.assertNotIn("retrieval_audit", semantic)
        self.assertNotIn("must-not-be-visible", semantic)
        self.assertEqual(self.request.audit_projection()["generation_visible_evidence_ids"], ["E01", "E02"])

    def test_identity_keeps_same_evidence_ab_and_repeat_distinct_from_occurrence(self) -> None:
        repeated = project_generation_request(self.packet, question="阿贝多在哪里活动？", question_id="q1")
        changed_question_id = project_generation_request(self.packet, question="阿贝多在哪里活动？", question_id="q2")
        self.assertEqual(self.request.semantic_request_identity, repeated.semantic_request_identity)
        self.assertEqual(self.request.semantic_request_identity, changed_question_id.semantic_request_identity)
        changed_config = BailianControlConfig(
            region="cn-beijing", endpoint="https://workspace.example.invalid", workspace="workspace-a", temperature=0.2,
        )
        self.assertNotEqual(self.config.execution_config_identity, changed_config.execution_config_identity)
        thinking_config = BailianControlConfig(
            region="cn-beijing", endpoint="https://workspace.example.invalid", workspace="workspace-a", enable_thinking=True,
        )
        self.assertNotEqual(self.config.execution_config_identity, thinking_config.execution_config_identity)
        provider, _ = self._provider([BailianTransportResponse("他在蒙德活动。[E01][E02]"), BailianTransportResponse("他在蒙德活动。[E01]")])
        first, second = provider.generate(self.request), provider.generate(self.request)
        self.assertEqual(first.semantic_request_identity, second.semantic_request_identity)
        self.assertEqual(first.execution_config_identity, second.execution_config_identity)
        self.assertNotEqual(
            first.provider_audit["generation_occurrence"]["occurrence_id"],
            second.provider_audit["generation_occurrence"]["occurrence_id"],
        )

    def test_control_adapter_maps_multi_evidence_without_claiming_synthesis_quality(self) -> None:
        provider, transport = self._provider([BailianTransportResponse("综合来说，阿贝多在蒙德活动。[E01][E02]", provider_request_id="req-1", usage={"output_tokens": 12})])
        result = provider.generate(self.request)
        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.citation_validation.integrity, "pass")  # type: ignore[union-attr]
        self.assertEqual(result.citation_validation.coverage, "pass")  # type: ignore[union-attr]
        self.assertEqual(result.citation_validation.semantic_faithfulness, "not_evaluated")  # type: ignore[union-attr]
        payload = transport.payloads[0]
        self.assertEqual(payload["model"], BASELINE_QWEN_MODEL_ID)
        self.assertEqual(payload["generation_parameters"]["enable_thinking"], False)
        self.assertIn("[E01]", payload["messages"][1]["content"])
        self.assertIn("[E02]", payload["messages"][1]["content"])
        self.assertNotIn("must-not-be-visible", json.dumps(payload, ensure_ascii=False))

    def test_citation_validation_checks_only_current_packet_membership_and_coverage(self) -> None:
        valid = validate_citations("回答 [E02] [E01] [E02]", self.request)
        self.assertEqual(valid.citation_tokens, ("E02", "E01"))
        self.assertEqual(valid.integrity, "pass")
        unknown = validate_citations("回答 [E03]", self.request)
        self.assertEqual(unknown.integrity, "fail")
        self.assertEqual(unknown.coverage, "fail")
        malformed = validate_citations("回答 [E1]", self.request)
        self.assertEqual(malformed.integrity, "fail")
        no_citation = validate_citations("没有引用", self.request)
        self.assertEqual(no_citation.integrity, "pass")
        self.assertEqual(no_citation.coverage, "fail")
        empty = project_generation_request(dict(self.packet, evidence=[]), question="无证据怎么办？")
        empty_validation = validate_citations("证据不足", empty)
        self.assertEqual(empty_validation.coverage, "not_applicable")
        optional = project_generation_request(self.packet, question="可选引用", citation_policy=CitationCoveragePolicy(mode="optional", min_unique_evidence_ids=0))
        self.assertEqual(validate_citations("无引用", optional).coverage, "pass")

    def test_semantic_faithfulness_is_an_immutable_not_evaluated_contract_value(self) -> None:
        validation = validate_citations("回答 [E01]", self.request)
        self.assertEqual(validation.semantic_faithfulness, "not_evaluated")
        self.assertEqual(validation.to_dict()["semantic_faithfulness"], "not_evaluated")
        with self.assertRaises(TypeError):
            CitationValidation((), "pass", "pass", (), semantic_faithfulness="supported")  # type: ignore[call-arg]
        with self.assertRaises(FrozenInstanceError):
            validation.semantic_faithfulness = "supported"  # type: ignore[misc]

    def test_retry_429_then_success_and_503_model_unavailable_are_bounded(self) -> None:
        sleeps: list[float] = []
        provider, transport = self._provider([
            BailianTransportError(status_code=429, code="Throttling", message="slow down", provider_request_id="r1", retry_after_seconds=0.5),
            BailianTransportResponse("正常回答 [E01]", provider_request_id="r2"),
        ], sleeps=sleeps)
        result = provider.generate(self.request)
        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(len(transport.payloads), 2)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(result.provider_audit["attempts"][0]["error_category"], "throttled")
        errors = [BailianTransportError(status_code=503, code="ModelUnavailable", message="busy") for _ in range(3)]
        provider, transport = self._provider(errors)
        failure = provider.generate(self.request)
        self.assertEqual(failure.execution_status, "provider_error")
        self.assertEqual(len(transport.payloads), 3)
        self.assertEqual(failure.provider_audit["attempts"][-1]["retry_decision"], "stop")

    def test_retry_classifier_uses_only_documented_status_code_pairs(self) -> None:
        for status, code in (
            (429, "Throttling"),
            (429, "Throttling.RateQuota"),
            (429, "LimitRequests"),
            (429, "limit_requests"),
            (429, "Throttling.BurstRate"),
            (429, "limit_burst_rate"),
            (429, "Throttling.AllocationQuota"),
            (429, "insufficient_quota"),
            (500, "InternalError"),
            (500, "internal_error"),
            (500, "SystemError"),
            (500, "ModelServiceFailed"),
            (500, "RequestTimeOut"),
            (500, "ModelServingError"),
            (503, "ModelServingError"),
            (503, "ModelUnavailable"),
        ):
            provider, transport = self._provider([
                BailianTransportError(status_code=status, code=code, message="transient"),
                BailianTransportResponse("恢复 [E01]"),
            ])
            self.assertEqual(provider.generate(self.request).execution_status, "succeeded")
            self.assertEqual(len(transport.payloads), 2)
        for status, code in (
            (400, "Throttling.AllocationQuota"),
            (500, "Throttling.AllocationQuota"),
            (503, "InternalError"),
            (503, "SystemError"),
            (503, "ModelServiceFailed"),
            (503, "RequestTimeOut"),
            (429, "ModelServingError"),
            (500, "ModelUnavailable"),
            (429, "Throttling.User"),
            (429, "Throttling.RateLimit"),
            (429, "PurchaseRequired"),
            (429, "BillingError"),
            (429, "AccountArrears"),
            (401, "InvalidApiKey"),
            (403, "AccessDenied"),
            (400, "InvalidParameter"),
            (429, "Unknown429"),
            (500, "Unknown500"),
            (503, "Unknown503"),
        ):
            provider, transport = self._provider([
                BailianTransportError(status_code=status, code=code, message="stop"),
                BailianTransportResponse("不应调用 [E01]"),
            ])
            failure = provider.generate(self.request)
            self.assertEqual(failure.execution_status, "provider_error")
            self.assertEqual(len(transport.payloads), 1)
            self.assertFalse(failure.provider_audit["attempts"][0]["retryable"])

    def test_default_sleeper_honors_configured_retry_backoff(self) -> None:
        config = BailianControlConfig(
            region="cn-beijing",
            endpoint="https://workspace.example.invalid",
            workspace="workspace-a",
            retry_backoff_seconds=0.25,
        )
        transport = _FakeTransport([
            BailianTransportError(status_code=503, code="ModelUnavailable", message="busy"),
            BailianTransportResponse("恢复回答 [E01]"),
        ])
        with patch("genshin_corpus.generation.generation.time.sleep") as sleeper:
            provider = BailianGenerationProvider(
                config,
                transport,
                occurrence_factory=lambda: "occurrence-default-sleep",
                clock=lambda: "2026-09-03T00:00:00Z",
            )
            result = provider.generate(self.request)
        self.assertEqual(result.execution_status, "succeeded")
        sleeper.assert_called_once_with(0.25)

    def test_local_timeout_is_bounded_and_audited_without_provider_error_text(self) -> None:
        sleeps: list[float] = []
        provider, transport = self._provider([TimeoutError(), BailianTransportResponse("超时后回答 [E01]")], sleeps=sleeps)
        result = provider.generate(self.request)
        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(len(transport.payloads), 2)
        self.assertEqual(sleeps, [0.0])
        self.assertEqual(result.provider_audit["attempts"][0]["error_category"], "timeout")

    def test_non_retryable_and_malformed_provider_responses_fail_closed(self) -> None:
        provider, transport = self._provider([BailianTransportError(status_code=401, code="InvalidApiKey", message="bad credential")])
        failure = provider.generate(self.request)
        self.assertEqual(failure.execution_status, "provider_error")
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual(failure.provider_audit["attempts"][0]["retryable"], False)
        provider, _ = self._provider([BailianTransportResponse(answer_text=None)])
        self.assertEqual(provider.generate(self.request).execution_status, "response_invalid")

    def test_unexpected_transport_or_programming_failure_is_not_normalized(self) -> None:
        provider, _ = self._provider([RuntimeError("test defect")])
        with self.assertRaisesRegex(RuntimeError, "test defect"):
            provider.generate(self.request)

    def test_baseline_config_rejects_alias_missing_thinking_and_non_baseline_model(self) -> None:
        with self.assertRaisesRegex(GenerationConfigurationError, "exact dated Qwen"):
            BailianControlConfig(region="cn", endpoint="https://example.invalid", workspace="w", model_id="qwen3.7-plus")
        with self.assertRaisesRegex(GenerationConfigurationError, "control baseline model"):
            BailianControlConfig(region="cn", endpoint="https://example.invalid", workspace="w", model_id="qwen3.7-max-2026-05-26")
        with self.assertRaisesRegex(GenerationConfigurationError, "enable_thinking"):
            BailianControlConfig(region="cn", endpoint="https://example.invalid", workspace="w", enable_thinking="false")  # type: ignore[arg-type]
        with self.assertRaisesRegex(GenerationContractError, "unsupported"):
            GenerationResult(
                execution_status="configuration_error",
                answer_text=None,
                citation_validation=None,
                semantic_request_identity=self.request.semantic_request_identity,
                execution_config_identity=self.config.execution_config_identity,
                request_audit=self.request.audit_projection(),
                provider_audit={},
            )
        with self.assertRaisesRegex(GenerationContractError, "status_code"):
            BailianTransportError(status_code="503", code="ModelUnavailable", message="bad transport")  # type: ignore[arg-type]

    def test_result_persistence_is_conflict_safe_and_excludes_secrets(self) -> None:
        provider, _ = self._provider([BailianTransportResponse("回答 [E01]", provider_request_id="req-safe")])
        result = provider.generate(self.request)
        first = write_generation_result(self.root, result)
        second = write_generation_result(self.root, result)
        self.assertEqual(first, second)
        body = (self.root / "generation_result.json").read_text(encoding="utf-8")
        self.assertIn('"result"', body)
        self.assertIn('"audit"', body)
        self.assertNotIn("DASHSCOPE_API_KEY", body)
        self.assertNotIn("api_key", body.lower())
        changed_provider, _ = self._provider([BailianTransportResponse("不同回答 [E01]", provider_request_id="req-other")])
        with self.assertRaises(FileExistsError):
            write_generation_result(self.root, changed_provider.generate(self.request))

    def test_distinct_occurrences_use_explicit_distinct_output_roots(self) -> None:
        provider, _ = self._provider([
            BailianTransportResponse("第一条回答 [E01]"),
            BailianTransportResponse("第二条回答 [E02]"),
        ])
        first, second = provider.generate(self.request), provider.generate(self.request)
        first_root = self.root / "occurrences" / first.provider_audit["generation_occurrence"]["occurrence_id"]
        second_root = self.root / "occurrences" / second.provider_audit["generation_occurrence"]["occurrence_id"]
        self.assertNotEqual(first_root, second_root)
        write_generation_result(first_root, first)
        write_generation_result(second_root, second)
        self.assertTrue((first_root / "generation_result.json").is_file())
        self.assertTrue((second_root / "generation_result.json").is_file())

    def test_invalid_packet_and_empty_answer_fail_before_or_at_validation(self) -> None:
        with self.assertRaisesRegex(GenerationContractError, "unsupported"):
            project_generation_request(dict(self.packet, schema_version="future"), question="x")
        with self.assertRaisesRegex(GenerationContractError, "answer_text"):
            validate_citations("", self.request)


class BailianLiveTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = BailianControlConfig(
            region="cn-beijing",
            endpoint="https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            workspace="workspace-a",
            max_attempts=1,
        )
        self.payload = {
            "model": BASELINE_QWEN_MODEL_ID,
            "messages": [{"role": "user", "content": "问题"}],
            "generation_parameters": {
                "enable_thinking": False,
                "temperature": 0.0,
                "max_output_tokens": 1024,
            },
            "workspace": "workspace-a",
        }

    def test_wire_mapping_is_official_shape_and_never_sends_workspace(self) -> None:
        opener = _FakeOpener([_FakeResponse({
            "id": "request-1",
            "choices": [{"message": {"content": "回答 [E01]"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        })])
        transport = BailianOpenAICompatibleTransport(self.config, "test-secret", opener=opener)
        response = transport.invoke(self.payload, timeout_seconds=2.5)
        request = opener.requests[0]
        body = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        self.assertEqual(request.full_url, "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions")  # type: ignore[attr-defined]
        self.assertEqual(body["model"], BASELINE_QWEN_MODEL_ID)
        self.assertEqual(body["enable_thinking"], False)
        self.assertEqual(body["max_tokens"], 1024)
        self.assertFalse(body["stream"])
        self.assertNotIn("workspace", body)
        self.assertNotIn("generation_parameters", body)
        self.assertEqual(response.provider_request_id, "request-1")
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.usage, {"prompt_tokens": 12, "completion_tokens": 3})
        self.assertNotIn("test-secret", body.__repr__())

    def test_endpoint_and_credential_boundary_fail_closed_before_environment_read(self) -> None:
        bad = BailianControlConfig(
            region="cn-beijing",
            endpoint="https://attacker.example/compatible-mode/v1",
            workspace="workspace-a",
        )
        reads: list[str] = []

        class _Environment(dict[str, str]):
            def get(self, key: str, default: object = None) -> object:
                reads.append(key)
                return super().get(key, default)

        with self.assertRaisesRegex(GenerationConfigurationError, "workspace-dedicated"):
            BailianOpenAICompatibleTransport.from_environment(bad, environment=_Environment(DASHSCOPE_API_KEY="test-secret"))
        self.assertEqual(reads, [])
        for value in (
            "http://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "https://workspace-a.cn-beijing.maas.aliyuncs.com:444/compatible-mode/v1",
            "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1?x=1",
            "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/extra",
        ):
            with self.assertRaises(GenerationConfigurationError):
                workspace_from_bailian_base_url(value)

    def test_http_error_redirect_and_connection_error_are_safe_and_normalized(self) -> None:
        response_headers = {"x-acs-request-id": "server-request", "Retry-After": "1.5"}
        transient = HTTPError(
            "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
            429,
            "ignored",
            response_headers,
            _FakeResponse({"code": "Throttling.AllocationQuota", "message": "secret provider detail"}),
        )
        transport = BailianOpenAICompatibleTransport(self.config, "test-secret", opener=_FakeOpener([transient]))
        with self.assertRaises(BailianTransportError) as caught:
            transport.invoke(self.payload, timeout_seconds=1)
        error = caught.exception
        self.assertEqual((error.status_code, error.code, error.provider_request_id, error.retry_after_seconds), (429, "Throttling.AllocationQuota", "server-request", 1.5))
        self.assertNotIn("secret provider detail", repr(error))
        redirect = HTTPError("https://safe", 302, "redirect", {"Location": "https://attacker"}, _FakeResponse(b""))
        transport = BailianOpenAICompatibleTransport(self.config, "test-secret", opener=_FakeOpener([redirect]))
        with self.assertRaises(BailianTransportError) as caught:
            transport.invoke(self.payload, timeout_seconds=1)
        self.assertEqual(caught.exception.code, "RedirectRejected")
        transport = BailianOpenAICompatibleTransport(self.config, "test-secret", opener=_FakeOpener([URLError("offline")]))
        with self.assertRaises(BailianTransportError) as caught:
            transport.invoke(self.payload, timeout_seconds=1)
        self.assertEqual((caught.exception.status_code, caught.exception.code), (None, "TransportConnectionError"))

    def test_malformed_success_is_response_invalid_not_provider_error(self) -> None:
        transport = BailianOpenAICompatibleTransport(self.config, "test-secret", opener=_FakeOpener([_FakeResponse(b"not json")]))
        response = transport.invoke(self.payload, timeout_seconds=1)
        self.assertIsNone(response.answer_text)


class BailianSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("tmp/.generation-smoke-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.packet = self.root / "evidence_packet.json"
        self.packet.write_text(json.dumps({
            "schema_version": "phase04-evidence-packet-0.1",
            "evidence": [{"evidence_id": "E01", "text": "证据", "members": []}],
            "retrieval_audit": {"retrieval_metadata": {"query_id": "q01", "query_text": "问题"}},
        }, ensure_ascii=False), encoding="utf-8")
        self.environment = {
            "BAILIAN_BASE_URL": "https://workspace-a.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "DASHSCOPE_API_KEY": "test-secret",
        }

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def test_smoke_prevents_second_request_for_an_existing_output(self) -> None:
        output = self.root / "output"
        output.mkdir()
        (output / "generation_result.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            run_bailian_control_smoke(packet_path=self.packet, output_root=output, environment=self.environment)

    def test_smoke_uses_one_exact_baseline_occurrence_and_persists_safe_result(self) -> None:
        transport = _FakeTransport([BailianTransportResponse("回答 [E01]", provider_request_id="request-safe")])
        output = self.root / "output"
        with patch(
            "genshin_corpus.generation.generation.BailianOpenAICompatibleTransport.from_environment",
            return_value=transport,
        ) as factory:
            result = run_bailian_control_smoke(
                packet_path=self.packet,
                output_root=output,
                environment=self.environment,
            )
        self.assertEqual(result["execution_status"], "succeeded")
        self.assertEqual(result["semantic_faithfulness"], "not_evaluated")
        self.assertEqual(len(transport.payloads), 1)
        config = factory.call_args.args[0]
        self.assertEqual(config.model_id, BASELINE_QWEN_MODEL_ID)
        self.assertFalse(config.enable_thinking)
        self.assertEqual(config.max_attempts, 1)
        body = (output / "generation_result.json").read_text(encoding="utf-8")
        self.assertNotIn("test-secret", body)
        self.assertNotIn("DASHSCOPE_API_KEY", body)


if __name__ == "__main__":
    unittest.main()
