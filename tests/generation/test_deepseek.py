"""Fixture HTTP only: no credentials, models, corpus or live API required."""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from genshin_corpus.evidence_io import read, verify
from genshin_corpus.generation.deepseek import (
    DEFAULT_DEEPSEEK_MODEL, DeepSeekGenerationConfig, DeepSeekGenerationProvider,
)
from genshin_corpus.generation.generation import (
    DEFAULT_GENERATION_INSTRUCTION, BailianGenerationProvider, CitationCoveragePolicy,
    GenerationConfigurationError, GenerationEvidence, GenerationRequest, generation_messages,
)


def request(question="钟离与若陀龙王是什么关系？", evidence=True):
    return GenerationRequest(DEFAULT_GENERATION_INSTRUCTION, question,
        (GenerationEvidence("E01", "钟离与若陀龙王曾订立契约。", ("匪石",)),) if evidence else (),
        CitationCoveragePolicy(), "phase04-evidence-packet-0.1", "a" * 64)


def response(text="两者曾订立契约。[E01]", **updates):
    value = {"id": "fixture-chat-1", "object": "chat.completion", "model": DEFAULT_DEEPSEEK_MODEL,
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}}
    value.update(updates)
    return value


class FakeResponse(BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status
        self.headers = {"content-type": "application/json"}


class FakeOpener:
    def __init__(self, value=None, *, error=None, status=200):
        self.body = json.dumps(value or response(), ensure_ascii=False).encode()
        self.error, self.status, self.calls = error, status, []

    def open(self, req, *, timeout):
        self.calls.append((req, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.body, self.status)


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        network = patch("socket.socket.connect", side_effect=AssertionError("Offline tests forbid sockets"))
        blocked = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(blocked.assert_not_called)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = DeepSeekGenerationConfig(self.root / "campaign", 3, 100000)

    def tearDown(self):
        self.temp.cleanup()

    def provider(self, opener=None, config=None):
        return DeepSeekGenerationProvider(config or self.config, api_key="fixture-secret",
            authorization="OFFLINE_MOCK", opener=opener or FakeOpener(), mock=True)

    def test_neutral_rendering_matches_preserved_bailian_wire_and_excludes_audit(self):
        req = request()
        messages = generation_messages(req)
        self.assertEqual(messages, BailianGenerationProvider._messages(req))
        wire = self.provider().invocation_payload(req)
        self.assertEqual(wire["messages"], messages)
        self.assertEqual(wire["thinking"], {"type": "disabled"})
        self.assertFalse(wire["stream"])
        self.assertNotIn("a" * 64, json.dumps(wire))
        self.assertNotIn("response_format", wire)

    def test_success_archives_exact_bytes_reuses_shared_calls_and_replays_without_http(self):
        opener = FakeOpener()
        provider = self.provider(opener)
        result = provider.generate(request())
        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.citation_validation.integrity, "pass")
        self.assertEqual(result.citation_validation.semantic_faithfulness, "not_evaluated")
        raw = Path(result.provider_audit["raw_archive"])
        verify(raw)
        self.assertEqual((raw / "response.body.raw").read_bytes(), opener.body)
        self.assertEqual((raw / "request.body.bin").read_bytes(), opener.calls[0][0].data)
        self.assertEqual(result.provider_audit["accounting"]["total_tokens"], 18)
        self.assertEqual(result.provider_audit["accounting"]["retry_attempts"], 0)
        self.assertEqual(len(list((self.config.run_root / "budget/requests").iterdir())), 1)
        replay = self.provider(FakeOpener(error=AssertionError("No resend allowed"))).generate(request())
        self.assertEqual(replay.answer_text, result.answer_text)
        self.assertTrue(replay.provider_audit["replayed"])
        self.assertEqual(replay.provider_audit["new_transport_attempts"], 0)
        self.assertEqual(len(opener.calls), 1)
        for path in self.config.run_root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"fixture-secret", path.read_bytes())

    def test_rejects_wrong_model_truncation_missing_usage_reasoning_and_bad_citations(self):
        variants = [response(model="other"), response(usage={}), response("无证据引用的断言。"), response("错误引用。[E99]")]
        truncated = response(); truncated["choices"][0]["finish_reason"] = "length"; variants.append(truncated)
        reasoning = response(); reasoning["choices"][0]["message"]["reasoning_content"] = "private reasoning"; variants.append(reasoning)
        arithmetic = response(); arithmetic["usage"]["total_tokens"] = 99; variants.append(arithmetic)
        overcap = response(usage={"prompt_tokens": 10, "completion_tokens": 1025, "total_tokens": 1035}); variants.append(overcap)
        for i, value in enumerate(variants):
            with self.subTest(i=i):
                config = replace(self.config, run_root=self.root / str(i))
                opener = FakeOpener(value)
                result = self.provider(opener, config).generate(request())
                self.assertEqual(result.execution_status, "response_invalid")
                self.assertIsNone(result.answer_text)
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(result.provider_audit["accounting"]["retry_attempts"], 0)

    def test_empty_packet_allows_honest_insufficient_evidence_response(self):
        result = self.provider(FakeOpener(response("提供的资料不足以回答。"))).generate(request(evidence=False))
        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.citation_validation.coverage, "not_applicable")

    def test_http_errors_and_unknown_transport_are_archived_once_and_never_resent(self):
        for i, error in enumerate((HTTPError("https://tokenmetro.com/v1/chat/completions", 524, "Gateway", {}, BytesIO(b'{"error":"gateway"}')),
                                   URLError("fixture-secret must not be recorded"))):
            with self.subTest(i=i):
                config = replace(self.config, run_root=self.root / str(i))
                opener = FakeOpener(error=error)
                result = self.provider(opener, config).generate(request())
                self.assertEqual(result.execution_status, "provider_error")
                self.assertEqual(result.provider_audit["accounting"]["unknown_usage_attempts"], [1])
                self.provider(FakeOpener(error=AssertionError("No resend")), config).generate(request())
                self.assertEqual(len(opener.calls), 1)
                verify(Path(result.provider_audit["raw_archive"]))

    def test_partial_response_is_preserved_and_classified_unknown(self):
        class Partial(FakeResponse):
            def read(self, count):
                if self.tell():
                    raise ConnectionResetError("private details")
                return super().read(8)
        opener = FakeOpener()
        opener.open = lambda req, **kwargs: Partial(opener.body)
        result = self.provider(opener).generate(request())
        raw = Path(result.provider_audit["raw_archive"])
        self.assertEqual(len((raw / "response.body.raw").read_bytes()), 8)
        self.assertEqual(read(raw / "result.json")["attempt_classifications"][0]["execution"], "UNKNOWN")

    def test_pending_attempt_never_resends(self):
        class Crash(FakeOpener):
            def open(self, req, **kwargs):
                raise KeyboardInterrupt()
        provider = self.provider(Crash())
        with self.assertRaises(KeyboardInterrupt):
            provider.generate(request())
        result = self.provider(FakeOpener(error=AssertionError("No resend"))).generate(request())
        self.assertIsNone(result.answer_text)
        self.assertEqual(result.provider_audit["accounting"]["total_tokens"], "UNKNOWN")

    def test_finite_budget_blocks_another_question_without_dispatch(self):
        config = replace(self.config, max_requests=1)
        opener = FakeOpener()
        provider = self.provider(opener, config)
        provider.generate(request())
        from genshin_corpus.model_calls import Deferred
        with self.assertRaises(Deferred):
            provider.generate(request("另一个问题"))
        self.assertEqual(len(opener.calls), 1)

    def test_configuration_authorization_and_mock_separation_fail_before_http(self):
        for updates in ({"base_url": "https://unknown.example/v1"}, {"max_tokens": 0},
                        {"timeout_seconds": float("nan")}, {"max_output_tokens": 32001}):
            with self.subTest(updates=updates), self.assertRaises(GenerationConfigurationError):
                replace(self.config, **updates)
        with self.assertRaises(GenerationConfigurationError):
            DeepSeekGenerationProvider(self.config, api_key="key", authorization="")
        with self.assertRaises(GenerationConfigurationError):
            DeepSeekGenerationProvider(self.config, api_key="key", authorization="tag", opener=FakeOpener())
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No sockets")):
            with self.assertRaises(GenerationConfigurationError):
                DeepSeekGenerationProvider.from_environment({})

    def test_existing_backend_passes_packet_to_deepseek_and_persists_answer_citations(self):
        from genshin_corpus.rag.backend import PreparedRagState, SingleQuestionBackendConfig, run_single_question
        from genshin_corpus.rag.config import ONLINE_QWEN_PROFILE
        packet = {"schema_version": "phase04-evidence-packet-0.1",
                  "budget": {"used_evidence_blocks": 1},
                  "evidence": [{"evidence_id": "E01", "text": "钟离与若陀龙王曾订立契约。", "members": [{
                      "unit_id": "fixture-ru", "record_context": {"record_title": "匪石"},
                      "canonical_address": {"source_identity_key": "fixture-source", "section_ordinal": 1,
                                            "component_observation_key": "fixture-component", "canonical_unit_ordinal": 1}}]}]}
        retriever = SimpleNamespace(candidates_for_query=lambda *args, **kwargs: {
            "lexical": [], "dense": [], "hybrid": [{"unit_id": "fixture-ru", "rank": 1}]})
        state = PreparedRagState(Path("fixture-ru.json"), Path("fixture-lex.json"), Path("fixture-dense.json"),
                                 retriever, object(), "ru", "lex", "dense", embedding_profile=ONLINE_QWEN_PROFILE)
        encoded = ([1.0], SimpleNamespace(request_identity="fixture-query"),
                   SimpleNamespace(returned_model="qwen3.7-text-embedding", returned_role="query", provider_request_id="mock"))
        opener = FakeOpener()
        with patch("genshin_corpus.rag.backend.encode_qwen_query", return_value=encoded), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=packet):
            result = run_single_question(state, request().question,
                embedding_transport=SimpleNamespace(embed=lambda _: None), generation_provider=self.provider(opener),
                config=SingleQuestionBackendConfig(reranker_enabled=False), output_root=self.root / "qa-results")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertIn("[E01]", result["final_answer"])
        self.assertEqual(result["citation_validation"]["citation_integrity"], "pass")
        self.assertEqual(result["citation_validation"]["semantic_faithfulness"], "not_evaluated")
        run_root = Path(result["persistence"]["run_root"])
        self.assertTrue((run_root / "rag_result.json").exists())
        self.assertTrue((run_root / "packet/evidence_packet.json").exists())
        self.assertTrue((run_root / "generation/generation_result.json").exists())
        sent = json.loads(opener.calls[0][0].data)
        self.assertIn("钟离与若陀龙王曾订立契约", sent["messages"][1]["content"])
        self.assertNotIn("fixture-source", str(sent))
        self.assertIn("fixture-source", (run_root / "packet/evidence_packet.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
