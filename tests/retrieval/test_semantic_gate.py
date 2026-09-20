from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genshin_corpus.generation.generation import (
    REQUESTED_ALIAS_POLICY,
    BailianControlConfig,
    BailianTransportResponse,
    CitationValidation,
    GenerationResult,
    project_generation_request,
)
from genshin_corpus.rag.adaptive import (
    AdaptiveContractError,
    AnswerDisposition,
    EvidenceAssessmentRequest,
    EvidenceAssessmentResult,
    EvidenceCondition,
    OrchestrationAction,
    bind_evidence_packet,
    run_bounded_adaptive_question,
)
from genshin_corpus.rag.semantic_gate import (
    ASSESSOR_ENDPOINT,
    ASSESSOR_MODEL_ID,
    ASSESSOR_REGION,
    ASSESSOR_WORKSPACE,
    HISTORICAL_QWEN38_EXECUTION_CONFIG_IDENTITY,
    BailianEvidenceAssessor,
    EvidenceAssessorProviderError,
    SemanticGateContractError,
    SharedRound0Replay,
    ReusingGenerationProvider,
    build_targeted_case_manifest,
    _directive_only_request,
    parse_assessment_response,
    persist_targeted_case_manifest,
    persist_round0_replay,
    run_targeted_gate_case,
    _validate_frozen_runtime_bindings,
)


def _packet(unit_id: str = "u1") -> dict:
    return {
        "schema_version": "phase04-evidence-packet-0.1",
        "assembly_version": "formal-deferred-test",
        "retrieval_unit_build": {"build_identity": "ru-build"},
        "assembly_config_identity": "assembly-config",
        "selection_policy_identity": "selection-policy",
        "evidence": [{
            "evidence_id": "E01",
            "text": "official evidence",
            "members": [{"unit_id": unit_id}],
        }],
    }


def _assessment_request() -> EvidenceAssessmentRequest:
    packet = _packet()
    return EvidenceAssessmentRequest(
        original_question="What happened?",
        current_query="What happened?",
        round_index=0,
        supplements_remaining=1,
        packet=packet,
        packet_binding=bind_evidence_packet(packet),
    )


class _AssessorTransport:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def invoke(self, payload: dict, *, timeout_seconds: float) -> BailianTransportResponse:
        self.payloads.append(payload)
        request = json.loads(payload["messages"][1]["content"])
        answer = {
            "assessment_request_identity": request["request_identity"],
            "condition": "sufficient",
            "action": "answer_now",
            "answer_disposition": "full",
            "supported_scope": [],
            "unresolved_aspects": [],
            "missing_information": [],
            "conflicts": [],
            "supplemental_query": None,
        }
        return BailianTransportResponse(json.dumps(answer), provider_request_id="fake-assessor")


class _RawAssessorTransport:
    def __init__(self, answer_text: str) -> None:
        self.answer_text = answer_text
        self.calls = 0

    def invoke(self, payload: dict, *, timeout_seconds: float) -> BailianTransportResponse:
        self.calls += 1
        return BailianTransportResponse(
            self.answer_text,
            provider_request_id="provider-request-1",
            usage={"input_tokens": 11, "output_tokens": 5},
            finish_reason="stop",
        )


class _Round0Capability:
    def __init__(self) -> None:
        self.calls = 0

    def run_round(self, *, query: str, round_index: int, request_identity: str) -> dict:
        self.calls += 1
        packet = _packet()
        return {
            "status": "succeeded",
            "query": {"question_text": query, "execution_identity": request_identity},
            "audit": {"embedding": {"request_identity": "embedding-round-0"}},
            "evidence_packet": packet,
        }


class _RealAdaptiveCapability(_Round0Capability):
    """Minimal complete Block A-shaped capability for the real adaptive kernel."""

    def __init__(self, supplemental_packet: dict | None = None) -> None:
        super().__init__()
        self.supplemental_packet = supplemental_packet or _packet("u2")
        self.rounds: list[int] = []

    def run_round(self, *, query: str, round_index: int, request_identity: str) -> dict:
        self.rounds.append(round_index)
        if round_index == 0:
            result = super().run_round(query=query, round_index=round_index, request_identity=request_identity)
            result["audit"].update({
                "retrieval_unit_build_identity": "ru-build",
                "lexical_build_identity": "lexical-build",
                "dense_build_identity": "dense-build",
                "config": {"final_top_n": 1, "reranker_enabled": False},
            })
            result["retrieval_trace"] = {"windows": {"hybrid": [{"unit_id": "u1", "rank": 1}]}}
            result["rerank_trace"] = {"status": "disabled_by_explicit_config"}
            result["telemetry"] = {"provider_call_counts": {"embedding": 1, "reranker": 0, "generation": 0}}
            return result
        packet = self.supplemental_packet
        return {
            "status": "succeeded",
            "query": {"question_text": query, "execution_identity": request_identity},
            "audit": {
                "retrieval_unit_build_identity": "ru-build",
                "lexical_build_identity": "lexical-build",
                "dense_build_identity": "dense-build",
                "config": {"final_top_n": 1, "reranker_enabled": False},
                "embedding": {"request_identity": "embedding-round-1"},
            },
            "retrieval_trace": {"windows": {"hybrid": [{"unit_id": "u2", "rank": 1}]}},
            "rerank_trace": {"status": "disabled_by_explicit_config"},
            "telemetry": {"provider_call_counts": {"embedding": 1, "reranker": 0, "generation": 0}},
            "evidence_packet": packet,
        }

    def rebuild_packet(self, ranked_candidates, *, retrieval_audit):
        self.rebuild_candidates = [dict(row) for row in ranked_candidates]
        self.rebuild_audit = dict(retrieval_audit)
        return _packet("u2")


class _RealAdaptiveAssessor:
    def __init__(self, supplement: bool) -> None:
        self.supplement = supplement
        self.requests = []

    def assess(self, request):
        self.requests.append(request)
        if self.supplement and len(self.requests) == 1:
            return EvidenceAssessmentResult(
                assessment_request_identity=request.request_identity,
                condition=EvidenceCondition.INCOMPLETE,
                action=OrchestrationAction.SUPPLEMENT_ONCE,
                answer_disposition=AnswerDisposition.NONE,
                missing_information=("missing fact",),
                unresolved_aspects=("requires one bounded retrieval",),
                supplemental_query="supplemental fact",
            )
        return EvidenceAssessmentResult(
            assessment_request_identity=request.request_identity,
            condition=EvidenceCondition.SUFFICIENT,
            action=OrchestrationAction.ANSWER_NOW,
            answer_disposition=AnswerDisposition.FULL,
        )


class _RealAdaptiveGeneration:
    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return GenerationResult(
            execution_status="succeeded",
            answer_text="answer [E01]",
            citation_validation=CitationValidation(("E01",), "pass", "pass", ()),
            semantic_request_identity=request.semantic_request_identity,
            execution_config_identity="fake-generation-config",
            request_audit=request.audit_projection(),
            provider_audit={"attempts": []},
        )


class _PreflightCheckingRound0Capability(_Round0Capability):
    def __init__(self, preflight_path: Path) -> None:
        super().__init__()
        self.preflight_path = preflight_path
        self.preflight_seen = False

    def run_round(self, *, query: str, round_index: int, request_identity: str) -> dict:
        self.preflight_seen = self.preflight_path.is_file()
        return super().run_round(query=query, round_index=round_index, request_identity=request_identity)


class _GenerationDelegate:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        raise AssertionError("cached Generation occurrence should be reused")


class _GenerationSuccessDelegate:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return GenerationResult(
            execution_status="succeeded",
            answer_text="answer [E01]",
            citation_validation=CitationValidation(("E01",), "pass", "pass", ()),
            semantic_request_identity=request.semantic_request_identity,
            execution_config_identity="generation-config",
            request_audit=request.audit_projection(),
            provider_audit={"attempts": []},
        )


class SemanticGateTests(unittest.TestCase):
    def test_targeted_manifest_validates_exact_nine_cases_and_corrected_q056_binding(self):
        manifest = build_targeted_case_manifest(Path("."))
        self.assertEqual({case.question_id for case in manifest.cases}, {
            "Q011", "Q015", "Q045", "Q049", "Q050", "Q052", "Q056", "Q058", "Q068",
        })
        q056 = next(case for case in manifest.cases if case.question_id == "Q056")
        self.assertIn(
            "25de9d1385fb3db6c43f4abe47ebae9c24835e82fba3187c858b8562d0a5951f",
            {artifact.sha256 for artifact in q056.source_artifacts},
        )
        self.assertNotIn("p04-qwen-rag-q011-formal-deferred-oracle-20260912", {
            artifact.path for case in manifest.cases for artifact in case.source_artifacts
        })
        self.assertTrue(all(case.packet_source_identities for case in manifest.cases))
        self.assertTrue(all(item.to_dict()["source_identity_keys"] for case in manifest.cases for item in case.packet_source_identities))

    def test_manifest_tampering_fails_closed(self):
        import genshin_corpus.rag.semantic_gate as gate

        altered = tuple(dict(spec, source=tuple(
            (item[0], "0" * 64, item[2]) if item[0].endswith("q011_oracle.json") else item
            for item in spec["source"]
        )) if spec["id"] == "Q011" else spec for spec in gate._CASE_SPECS)
        with patch.object(gate, "_CASE_SPECS", altered):
            with self.assertRaises(SemanticGateContractError):
                build_targeted_case_manifest(Path("."))

    def test_pre_call_manifest_persistence_is_conflict_safe(self):
        manifest = build_targeted_case_manifest(Path("."))
        with tempfile.TemporaryDirectory() as temp_root:
            path = Path(temp_root) / "targeted_case_manifest.json"
            descriptor = persist_targeted_case_manifest(manifest, path)
            self.assertEqual(descriptor["manifest_identity"], manifest.manifest_identity)
            self.assertEqual(descriptor["case_count"], 9)
            self.assertEqual(persist_targeted_case_manifest(manifest, path), descriptor)
            path.write_bytes(b"different")
            with self.assertRaises(FileExistsError):
                persist_targeted_case_manifest(manifest, path)

    def test_assessor_parser_and_adapter_are_strict_and_provider_is_injected(self):
        request = _assessment_request()
        transport = _AssessorTransport()
        config = BailianControlConfig(
            region=ASSESSOR_REGION,
            endpoint=ASSESSOR_ENDPOINT,
            workspace=ASSESSOR_WORKSPACE,
            model_id=ASSESSOR_MODEL_ID,
            model_reference_policy=REQUESTED_ALIAS_POLICY,
            enable_thinking=True,
            thinking_budget=4096,
            max_output_tokens=2048,
            max_attempts=1,
        )
        assessor = BailianEvidenceAssessor(config, transport)
        self.assertNotEqual(assessor.execution_config_identity, HISTORICAL_QWEN38_EXECUTION_CONFIG_IDENTITY)
        self.assertEqual(len(assessor.execution_config_identity), 64)
        self.assertEqual(assessor.provider_network_calls, 0)
        result = assessor.assess(request)
        self.assertEqual(result.action.value, "answer_now")
        self.assertEqual(assessor.provider_network_calls, 1)
        self.assertEqual(len(transport.payloads), 1)
        payload_text = transport.payloads[0]["messages"][1]["content"]
        self.assertNotIn("source_identity_key", payload_text)
        self.assertNotIn("provenance", payload_text.lower())

        valid = json.loads(transport.payloads[0]["messages"][1]["content"])
        valid_result = {
            "assessment_request_identity": valid["request_identity"],
            "condition": "sufficient",
            "action": "answer_now",
            "answer_disposition": "full",
            "supported_scope": [],
            "unresolved_aspects": [],
            "missing_information": [],
            "conflicts": [],
            "supplemental_query": None,
        }
        self.assertEqual(parse_assessment_response(json.dumps(valid_result), request).result_identity,
                         parse_assessment_response(json.dumps(valid_result), request).result_identity)
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response("```json\n{}\n```", request)
        invalid = dict(valid_result, unexpected=True)
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(json.dumps(invalid), request)
        invalid_pair = dict(valid_result, action="supplement_once", supplemental_query="new query")
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(json.dumps(invalid_pair), request)

        mismatched = dict(valid_result, assessment_request_identity="0" * 64)
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(json.dumps(mismatched), request)
        duplicate = (
            '{"assessment_request_identity":"%s","assessment_request_identity":"%s",'
            '"condition":"sufficient","action":"answer_now","answer_disposition":"full",'
            '"supported_scope":[],"unresolved_aspects":[],"missing_information":[],'
            '"conflicts":[],"supplemental_query":null}'
        ) % (request.request_identity, request.request_identity)
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(duplicate, request)

        incomplete_no_gap = dict(valid_result, condition="incomplete", action="supplement_once", supplemental_query="new query")
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(json.dumps(incomplete_no_gap), request)
        conflicting_no_conflict = dict(valid_result, condition="conflicting", action="supplement_once", supplemental_query="new query", missing_information=["gap"])
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(json.dumps(conflicting_no_conflict), request)
        citation_scope = dict(valid_result, condition="incomplete", action="answer_now", answer_disposition="bounded_partial", supported_scope=["E01"], unresolved_aspects=["unknown"], missing_information=["gap"])
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(json.dumps(citation_scope), request)
        invalid_query = dict(valid_result, condition="incomplete", action="supplement_once", answer_disposition="none", missing_information=["gap"], supplemental_query=request.original_question)
        with self.assertRaises(SemanticGateContractError):
            parse_assessment_response(json.dumps(invalid_query), request)

    def test_assessor_rejects_unapproved_runtime_binding(self):
        for region, endpoint, workspace in (
            ("cn-hangzhou", ASSESSOR_ENDPOINT, ASSESSOR_WORKSPACE),
            (ASSESSOR_REGION, ASSESSOR_ENDPOINT, "other-workspace"),
            (ASSESSOR_REGION, "https://other-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "other-workspace"),
        ):
            config = BailianControlConfig(
                region=region,
                endpoint=endpoint,
                workspace=workspace,
                model_id=ASSESSOR_MODEL_ID,
                model_reference_policy=REQUESTED_ALIAS_POLICY,
                enable_thinking=True,
                thinking_budget=4096,
                max_output_tokens=2048,
                max_attempts=1,
            )
            with self.assertRaises(SemanticGateContractError):
                BailianEvidenceAssessor(config, _AssessorTransport())

    def test_assessor_prompt_publishes_exact_nine_field_contract(self):
        assessor = BailianEvidenceAssessor(
            BailianControlConfig(
                region=ASSESSOR_REGION,
                endpoint=ASSESSOR_ENDPOINT,
                workspace=ASSESSOR_WORKSPACE,
                model_id=ASSESSOR_MODEL_ID,
                model_reference_policy=REQUESTED_ALIAS_POLICY,
                enable_thinking=True,
                thinking_budget=4096,
                max_output_tokens=2048,
                max_attempts=1,
            ),
            _AssessorTransport(),
        )
        payload = assessor.invocation_payload(_assessment_request())
        system = payload["messages"][0]["content"]
        for field in (
            "assessment_request_identity", "condition", "action", "answer_disposition",
            "supported_scope", "unresolved_aspects", "missing_information", "conflicts",
            "supplemental_query",
        ):
            self.assertIn(field, system)
        self.assertIn("exactly nine keys", system)
        self.assertIn("each exactly once", system)
        self.assertIn("no other keys", system)
        self.assertIn("raw JSON object", system)
        self.assertNotIn("contain exactly the object must contain exactly", system)
        for rule in (
            "assessment_request_identity (non-empty single-line string; copy the exact supplied request identity)",
            "Every array entry must be a unique, non-empty, single-line string within its field",
            "sufficient requires missing_information and conflicts to be empty",
            "cannot use supplement_once",
            "when using answer_now requires answer_disposition full",
            "incomplete requires missing_information to be non-empty",
            "conflicting requires conflicts to be non-empty",
            "unable requires action stop, answer_disposition none, and unresolved_aspects to be non-empty",
            "answer_now requires answer_disposition full or bounded_partial and supplemental_query null",
            "incomplete or conflicting may use answer_now only with bounded_partial",
            "supplement_once requires incomplete or conflicting, answer_disposition none",
            "one concrete non-empty single-line supplemental_query",
            "stop requires answer_disposition none and supplemental_query null",
            "bounded_partial requires incomplete or conflicting",
            "requires both supported_scope and unresolved_aspects to be non-empty",
            "supported_scope must be empty unless answer_disposition is bounded_partial",
            "answer_disposition full requires unresolved_aspects to be empty",
            "supported_scope, unresolved_aspects, and conflicts must not contain Evidence Packet citation IDs",
        ):
            self.assertIn(rule, system)

    def test_invalid_assessor_response_is_persisted_and_replayable_offline(self):
        request = _assessment_request()
        raw = "{\"assessment_request_identity\":\"wrong\"}"
        transport = _RawAssessorTransport(raw)
        config = BailianControlConfig(
            region=ASSESSOR_REGION,
            endpoint=ASSESSOR_ENDPOINT,
            workspace=ASSESSOR_WORKSPACE,
            model_id=ASSESSOR_MODEL_ID,
            model_reference_policy=REQUESTED_ALIAS_POLICY,
            enable_thinking=True,
            thinking_budget=4096,
            max_output_tokens=2048,
            max_attempts=1,
        )
        with tempfile.TemporaryDirectory() as temp_root:
            assessor = BailianEvidenceAssessor(config, transport)
            assessor.bind_response_artifact_root(Path(temp_root))
            with self.assertRaises(EvidenceAssessorProviderError):
                assessor.assess(request)
            self.assertEqual(transport.calls, 1)
            audit = assessor.last_audit
            evidence = audit["response_evidence"]
            self.assertEqual(evidence["request_identity"], request.request_identity)
            self.assertEqual(evidence["execution_config_identity"], assessor.execution_config_identity)
            self.assertEqual(evidence["raw_answer_text"], raw)
            self.assertEqual(evidence["raw_answer_text_sha256"], sha256(raw.encode("utf-8")).hexdigest())
            self.assertEqual(audit["status"], "response_invalid")
            self.assertIn("parse_failure", audit)
            artifact = Path(evidence["raw_response_artifact"]["path"])
            self.assertTrue(artifact.is_file())
            persisted = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(persisted["response_evidence"]["raw_answer_text"], raw)
            with self.assertRaises(SemanticGateContractError) as replay_error:
                parse_assessment_response(raw, request)
            self.assertEqual(str(replay_error.exception), audit["parse_failure"]["reason"])

    def test_successful_assessor_response_retains_provider_evidence(self):
        request = _assessment_request()
        answer = {
            "assessment_request_identity": request.request_identity,
            "condition": "sufficient",
            "action": "answer_now",
            "answer_disposition": "full",
            "supported_scope": [],
            "unresolved_aspects": [],
            "missing_information": [],
            "conflicts": [],
            "supplemental_query": None,
        }
        raw = json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
        transport = _RawAssessorTransport(raw)
        config = BailianControlConfig(
            region=ASSESSOR_REGION,
            endpoint=ASSESSOR_ENDPOINT,
            workspace=ASSESSOR_WORKSPACE,
            model_id=ASSESSOR_MODEL_ID,
            model_reference_policy=REQUESTED_ALIAS_POLICY,
            enable_thinking=True,
            thinking_budget=4096,
            max_output_tokens=2048,
            max_attempts=1,
        )
        assessor = BailianEvidenceAssessor(config, transport)
        assessor.assess(request)
        evidence = assessor.last_audit["response_evidence"]
        self.assertEqual(assessor.last_audit["status"], "succeeded")
        self.assertEqual(evidence["provider_request_id"], "provider-request-1")
        self.assertEqual(evidence["usage"], {"input_tokens": 11, "output_tokens": 5})
        self.assertEqual(evidence["finish_reason"], "stop")
        self.assertEqual(evidence["raw_answer_text_sha256"], sha256(raw.encode("utf-8")).hexdigest())
        self.assertGreaterEqual(evidence["wall_clock_ms"], 0)

    def test_shared_round0_replay_calls_block_a_once(self):
        delegate = _Round0Capability()
        replay = SharedRound0Replay.capture(delegate, question="What happened?", execution_identity="gate-round0")
        capability = replay.capability(delegate)
        first = capability.run_round(query="What happened?", round_index=0, request_identity="gate-round0.round0")
        second = capability.run_round(query="What happened?", round_index=0, request_identity="gate-round0.round0")
        self.assertEqual(delegate.calls, 1)
        self.assertIs(first, second)
        self.assertEqual(replay.packet_sha256, bind_evidence_packet(first["evidence_packet"]).packet_sha256)

    def test_real_adaptive_kernel_reuses_round0_and_reaches_assessor(self):
        delegate = _RealAdaptiveCapability()
        replay = SharedRound0Replay.capture(delegate, question="What happened?", execution_identity="gate-round0")
        assessor = _RealAdaptiveAssessor(False)
        generation = _RealAdaptiveGeneration()
        result = run_bounded_adaptive_question(
            "What happened?", block_a=replay.capability(delegate), assessor=assessor,
            generation_provider=generation, execution_identity="gate-round0",
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(delegate.rounds, [0])
        self.assertEqual(len(assessor.requests), 1)
        self.assertEqual(len(generation.requests), 1)

    def test_real_adaptive_kernel_supplement_only_delegates_round1(self):
        delegate = _RealAdaptiveCapability()
        replay = SharedRound0Replay.capture(delegate, question="What happened?", execution_identity="gate-round1")
        assessor = _RealAdaptiveAssessor(True)
        generation = _RealAdaptiveGeneration()
        result = run_bounded_adaptive_question(
            "What happened?", block_a=replay.capability(delegate), assessor=assessor,
            generation_provider=generation, execution_identity="gate-round1",
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(delegate.rounds, [0, 1])
        self.assertEqual([request.round_index for request in assessor.requests], [0, 1])

    def test_replay_rejects_wrong_round_identity(self):
        delegate = _Round0Capability()
        replay = SharedRound0Replay.capture(delegate, question="What happened?", execution_identity="gate-round0")
        with self.assertRaises(AdaptiveContractError):
            replay.capability(delegate).run_round(
                query="What happened?", round_index=0, request_identity="gate-round0"
            )

    def test_replay_fails_closed_on_wrong_question_round_and_captured_source(self):
        delegate = _RealAdaptiveCapability()
        replay = SharedRound0Replay.capture(delegate, question="What happened?", execution_identity="gate-bound")
        capability = replay.capability(delegate)
        with self.assertRaises(AdaptiveContractError):
            capability.run_round(query="Different question", round_index=0, request_identity="gate-bound.round0")
        with self.assertRaises(AdaptiveContractError):
            capability.run_round(query="supplement", round_index=2, request_identity="gate-bound.round2")
        replay.source_backend_result["audit"]["lexical_build_identity"] = "tampered-static"
        with self.assertRaises(AdaptiveContractError):
            capability.run_round(query="What happened?", round_index=0, request_identity="gate-bound.round0")

    def test_round0_persistence_is_hash_bound_and_conflict_safe(self):
        delegate = _Round0Capability()
        replay = SharedRound0Replay.capture(delegate, question="What happened?", execution_identity="gate-round0")
        with tempfile.TemporaryDirectory() as temp_root:
            descriptor = persist_round0_replay(replay, Path(temp_root), "QTEST")
            self.assertEqual(descriptor["packet_sha256"], replay.packet_sha256)
            packet_path = Path(temp_root) / "round0" / "QTEST" / "evidence_packet.json"
            self.assertEqual(bind_evidence_packet(json.loads(packet_path.read_text(encoding="utf-8"))).packet_sha256, replay.packet_sha256)
            self.assertEqual(persist_round0_replay(replay, Path(temp_root), "QTEST"), descriptor)

    def test_recovery_reuses_parent_round0_and_baseline_with_explicit_lineage(self):
        import genshin_corpus.rag.semantic_gate as gate

        manifest = build_targeted_case_manifest(Path("."))
        case = manifest.cases[0]
        delegate = _RealAdaptiveCapability()
        source = delegate.run_round(
            query=case.question, round_index=0, request_identity="parent-adaptive"
        )
        delegate.calls = 0
        delegate.rounds.clear()
        packet = source["evidence_packet"]
        baseline_request = project_generation_request(
            packet, question=case.question, question_id=case.question_id
        )
        baseline = GenerationResult(
            execution_status="succeeded",
            answer_text="answer [E01]",
            citation_validation=CitationValidation(("E01",), "pass", "pass", ()),
            semantic_request_identity=baseline_request.semantic_request_identity,
            execution_config_identity="generation-config",
            request_audit=baseline_request.audit_projection(),
            provider_audit={"attempts": []},
        )
        lineage = {
            "parent_root": "immutable-parent",
            "parent_run_identity": "parent",
            "manifest_identity": manifest.manifest_identity,
            "preflight": {"path": "parent/preflight.json", "sha256": "1" * 64},
            "review_package": {"path": "parent/package.json", "sha256": "2" * 64},
            "round0_result": {"path": "parent/block_a_result.json", "sha256": "3" * 64},
            "packet": {"path": "parent/evidence_packet.json", "sha256": "4" * 64},
            "baseline": {"path": "parent/generation_result.json", "sha256": "5" * 64},
        }
        parent = {
            case.question_id: {
                "case": case,
                "source": source,
                "packet": packet,
                "baseline": baseline,
                "round0_descriptor": {"execution_identity": "parent-adaptive"},
                "lineage": lineage,
            }
        }
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            manifest_path = temp / "targeted_case_manifest.json"
            persist_targeted_case_manifest(manifest, manifest_path)
            with patch.object(gate, "_validate_frozen_runtime_bindings", return_value={"test": "frozen"}), \
                 patch.object(gate, "load_parent_targeted_gate", return_value=parent):
                package = run_targeted_gate_case(
                    case,
                    repo_root=Path("."),
                    manifest_path=manifest_path,
                    block_a=delegate,
                    assessor=_RealAdaptiveAssessor(False),
                    generation_provider=_GenerationDelegate(),
                    execution_identity="recovery-case",
                    output_root=temp / "runs",
                    parent_root=Path("immutable-parent"),
                )
            run_root = temp / "runs" / "recovery-case"
            self.assertTrue((run_root / "preflight_started.json").is_file())
            self.assertTrue((run_root / "round0_import.json").is_file())
            self.assertEqual(delegate.rounds, [])
            self.assertEqual(delegate.calls, 0)
            self.assertEqual(package["parent_lineage"], lineage)
            self.assertTrue(package["baseline"]["artifact"]["reused_parent_occurrence"])
            self.assertEqual(
                package["provider_calls"]["generation_occurrences"]["baseline"]["current_run_provider_network_calls"],
                0,
            )

    def test_live_seam_requires_persisted_validated_manifest_before_block_a(self):
        from genshin_corpus.rag.semantic_gate import run_targeted_gate_case

        manifest = build_targeted_case_manifest(Path("."))
        case = manifest.cases[0]
        block_a = _Round0Capability()
        with tempfile.TemporaryDirectory() as temp_root:
            with self.assertRaises(SemanticGateContractError):
                run_targeted_gate_case(
                    case,
                    repo_root=Path("."),
                    manifest_path=Path(temp_root) / "missing-manifest.json",
                    block_a=block_a,
                    assessor=object(),
                    generation_provider=_GenerationDelegate(),
                    execution_identity="gate-test",
                    output_root=Path(temp_root) / "runs",
                )
        self.assertEqual(block_a.calls, 0)

    def test_live_seam_persists_started_root_before_first_block_a_call(self):
        import genshin_corpus.rag.semantic_gate as gate
        from genshin_corpus.rag.semantic_gate import run_targeted_gate_case

        manifest = build_targeted_case_manifest(Path("."))
        case = manifest.cases[0]
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            manifest_path = temp / "targeted_case_manifest.json"
            persist_targeted_case_manifest(manifest, manifest_path)
            run_root = temp / "runs" / "gate-started"
            block_a = _PreflightCheckingRound0Capability(run_root / "preflight_started.json")
            with patch.object(gate, "_validate_frozen_runtime_bindings", return_value={"test": "frozen"}):
                with self.assertRaises(AssertionError):
                    run_targeted_gate_case(
                        case,
                        repo_root=Path("."),
                        manifest_path=manifest_path,
                        block_a=block_a,
                        assessor=object(),
                        generation_provider=_GenerationDelegate(),
                        execution_identity="gate-started",
                        output_root=temp / "runs",
                    )
            self.assertTrue(block_a.preflight_seen)
            preflight = json.loads((run_root / "preflight_started.json").read_text(encoding="utf-8"))
            self.assertEqual(preflight["status"], "STARTED")
            self.assertEqual(preflight["run_identity"], "gate-started")

    def test_frozen_runtime_binding_rejects_wrong_block_a_without_invocation(self):
        calls = []
        with self.assertRaises(SemanticGateContractError):
            _validate_frozen_runtime_bindings(object(), object(), object())
        self.assertEqual(calls, [])

    def test_frozen_runtime_binding_rejects_wrong_assessor_without_invocation(self):
        import genshin_corpus.rag.semantic_gate as gate

        with patch.object(gate, "_validate_frozen_block_a_binding", return_value={"ok": True}):
            with self.assertRaises(SemanticGateContractError):
                _validate_frozen_runtime_bindings(object(), object(), object())

    def test_frozen_runtime_binding_rejects_wrong_generation_without_invocation(self):
        import genshin_corpus.rag.semantic_gate as gate

        with patch.object(gate, "_validate_frozen_block_a_binding", return_value={"ok": True}), \
             patch.object(gate, "_validate_frozen_assessor_binding", return_value={"ok": True}):
            with self.assertRaises(SemanticGateContractError):
                _validate_frozen_runtime_bindings(object(), object(), object())

    def test_assessor_accounting_is_case_local_when_reused(self):
        import genshin_corpus.rag.semantic_gate as gate
        from genshin_corpus.rag.semantic_gate import run_targeted_gate_case

        manifest = build_targeted_case_manifest(Path("."))
        case = manifest.cases[0]
        config = BailianControlConfig(
            region=ASSESSOR_REGION,
            endpoint=ASSESSOR_ENDPOINT,
            workspace=ASSESSOR_WORKSPACE,
            model_id=ASSESSOR_MODEL_ID,
            model_reference_policy=REQUESTED_ALIAS_POLICY,
            enable_thinking=True,
            thinking_budget=4096,
            max_output_tokens=2048,
            max_attempts=1,
        )
        assessor = BailianEvidenceAssessor(config, _AssessorTransport())
        generation = _GenerationSuccessDelegate()
        packet = _packet()
        baseline_request = project_generation_request(packet, question=case.question, question_id=case.question_id)

        def fake_adaptive(question, *, block_a, assessor, generation_provider, execution_identity):
            result = assessor.assess(_assessment_request())
            return {
                "assessments": [{"result": result.to_dict()}],
                "final_packet_binding": bind_evidence_packet(packet).to_dict(),
                "generation": {"status": "succeeded", "semantic_request_identity": baseline_request.semantic_request_identity},
                "admission": {},
            }

        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            manifest_path = temp / "targeted_case_manifest.json"
            persist_targeted_case_manifest(manifest, manifest_path)
            with patch.object(gate, "_validate_frozen_runtime_bindings", return_value={"test": "frozen"}), \
                 patch.object(gate, "run_bounded_adaptive_question", side_effect=fake_adaptive):
                first = run_targeted_gate_case(
                    case,
                    repo_root=Path("."),
                    manifest_path=manifest_path,
                    block_a=_Round0Capability(),
                    assessor=assessor,
                    generation_provider=generation,
                    execution_identity="case-one",
                    output_root=temp / "runs",
                )
                second = run_targeted_gate_case(
                    case,
                    repo_root=Path("."),
                    manifest_path=manifest_path,
                    block_a=_Round0Capability(),
                    assessor=assessor,
                    generation_provider=generation,
                    execution_identity="case-two",
                    output_root=temp / "runs",
                )
        self.assertEqual(first["provider_calls"]["assessor"], 1)
        self.assertEqual(second["provider_calls"]["assessor"], 1)
        self.assertEqual(len(first["provider_calls"]["assessor_attempts"]), 1)
        self.assertEqual(len(second["provider_calls"]["assessor_attempts"]), 1)

    def test_generation_reuses_identical_semantic_request(self):
        request = project_generation_request(_packet(), question="What happened?", question_id="QTEST")
        cached = GenerationResult(
            execution_status="succeeded",
            answer_text="answer [E01]",
            citation_validation=CitationValidation(("E01",), "pass", "pass", ()),
            semantic_request_identity=request.semantic_request_identity,
            execution_config_identity="generation-config",
            request_audit=request.audit_projection(),
            provider_audit={"attempts": []},
        )
        reused: list[str] = []
        provider = ReusingGenerationProvider(_GenerationDelegate(), {request.semantic_request_identity: cached}, reused)
        actual = provider.generate(request)
        self.assertIs(actual, cached)
        self.assertEqual(reused, [request.semantic_request_identity])

    def test_directive_only_round0_request_preserves_bounded_directive(self):
        baseline = project_generation_request(_packet(), question="What happened?", question_id="QTEST")
        adaptive = {
            "answer_disposition": "bounded_partial",
            "assessments": [{
                "result": {
                    "assessment_request_identity": "a" * 64,
                    "condition": "incomplete",
                    "action": "answer_now",
                    "answer_disposition": "bounded_partial",
                    "supported_scope": ["the supported fact"],
                    "unresolved_aspects": ["the unresolved relation"],
                    "missing_information": ["relation"],
                    "conflicts": [],
                    "supplemental_query": None,
                },
            }],
        }
        directive = _directive_only_request(_packet(), "What happened?", "QTEST", adaptive)
        self.assertIsNotNone(directive)
        self.assertNotEqual(directive.semantic_request_identity, baseline.semantic_request_identity)
        self.assertEqual(directive.answer_scope.supported_scope, ("the supported fact",))


if __name__ == "__main__":
    unittest.main()
