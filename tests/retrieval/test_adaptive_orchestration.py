from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.generation.generation import CitationValidation, GenerationResult
from genshin_corpus.rag.adaptive import (
    AdaptiveContractError,
    AnswerDisposition,
    EvidenceAssessmentResult,
    EvidenceCondition,
    FrozenBlockAEvidenceCapability,
    OrchestrationAction,
    run_bounded_adaptive_question,
)
from genshin_corpus.rag.backend import SingleQuestionBackendConfig
from genshin_corpus.retrieval.evidence_assembly import (
    EvidenceAssemblyConfig,
    assemble_deferred_footprint_charge_packet,
    prepare_evidence_assembly_context,
)
from genshin_corpus.retrieval.retrieval_units import (
    RetrievalUnitBuildConfig,
    build_retrieval_units,
    load_retrieval_units,
)


_STATIC = {
    "assembly_version": "formal-deferred-test",
    "retrieval_unit_build_identity": "ru-build",
    "assembly_config_identity": "assembly-config",
    "selection_policy_identity": "selection-policy",
}


def _packet(
    unit_ids: list[str],
    *,
    static: dict[str, str] | None = None,
    texts: dict[str, str] | None = None,
    group_members: bool = False,
) -> dict:
    identity = dict(_STATIC if static is None else static)
    if group_members:
        evidence = [{
            "evidence_id": "E01",
            "text": "\n".join((texts or {}).get(unit_id, f"text:{unit_id}") for unit_id in unit_ids),
            "members": [{"unit_id": unit_id} for unit_id in unit_ids],
        }]
    else:
        evidence = [
            {
                "evidence_id": f"E{index:02d}",
                "text": (texts or {}).get(unit_id, f"text:{unit_id}"),
                "members": [{"unit_id": unit_id}],
            }
            for index, unit_id in enumerate(unit_ids, 1)
        ]
    return {
        "schema_version": "phase04-evidence-packet-0.1",
        "assembly_version": identity["assembly_version"],
        "retrieval_unit_build": {"build_identity": identity["retrieval_unit_build_identity"]},
        "assembly_config_identity": identity["assembly_config_identity"],
        "selection_policy_identity": identity["selection_policy_identity"],
        "evidence": evidence,
    }


def _candidates(unit_ids: list[str]) -> list[dict]:
    return [
        {
            "unit_id": unit_id,
            "rank": index,
            "retrieval": {"mode": "test", "score": 1.0 / index},
        }
        for index, unit_id in enumerate(unit_ids, 1)
    ]


def _round_result(
    *,
    query: str,
    execution_identity: str,
    round_index: int,
    unit_ids: list[str],
    packet: dict,
    request_identity: str | None = None,
    reranker_enabled: bool = False,
    reranker_runtime_identity: dict | None = None,
) -> dict:
    candidates = _candidates(unit_ids)
    return {
        "status": "succeeded",
        "query": {"question_text": query, "execution_identity": execution_identity},
        "audit": {
            "retrieval_unit_build_identity": packet["retrieval_unit_build"]["build_identity"],
            "lexical_build_identity": "lexical-build",
            "dense_build_identity": "dense-build",
            "config": {"final_top_n": len(candidates), "reranker_enabled": reranker_enabled},
            "embedding": {"request_identity": request_identity or f"embedding:{round_index}:{query}"},
        },
        "telemetry": {
            "provider_call_counts": {"embedding": 1, "reranker": 1, "generation": 0},
        },
        "retrieval_trace": {"windows": {"hybrid": candidates}},
        "rerank_trace": (
            {
                "status": "executed_successfully",
                "runtime_identity": reranker_runtime_identity,
                "after": candidates,
            }
            if reranker_enabled
            else {"status": "disabled_by_explicit_config"}
        ),
        "evidence_packet": packet,
        "error": None,
    }


class _Capability:
    def __init__(
        self,
        rounds: dict[int, list[str]],
        *,
        static_by_round: dict[int, dict[str, str]] | None = None,
        texts: dict[str, str] | None = None,
        rebuild=None,
        failure_stage: str | None = None,
        reranker_enabled: bool = False,
        reranker_runtime_by_round: dict[int, dict] | None = None,
    ) -> None:
        self.rounds = rounds
        self.static_by_round = static_by_round or {}
        self.texts = texts or {}
        self.rebuild = rebuild
        self.failure_stage = failure_stage
        self.reranker_enabled = reranker_enabled
        self.reranker_runtime_by_round = reranker_runtime_by_round or {}
        self.queries: list[str] = []
        self.rebuild_candidates: list[dict] | None = None
        self.rebuild_audit: dict | None = None

    def run_round(self, *, query, round_index, request_identity):
        self.queries.append(query)
        if round_index == 1 and self.failure_stage is not None:
            return {
                "status": "failed",
                "query": {"question_text": query, "execution_identity": request_identity},
                "telemetry": {"provider_call_counts": {"embedding": 1, "reranker": 0, "generation": 0}},
                "error": {"stage": self.failure_stage, "category": "synthetic"},
            }
        unit_ids = self.rounds[round_index]
        packet = _packet(
            unit_ids,
            static=self.static_by_round.get(round_index),
            texts=self.texts,
        )
        return _round_result(
            query=query,
            execution_identity=request_identity,
            round_index=round_index,
            unit_ids=unit_ids,
            packet=packet,
            reranker_enabled=self.reranker_enabled,
            reranker_runtime_identity=self.reranker_runtime_by_round.get(round_index),
        )

    def rebuild_packet(self, ranked_candidates, *, retrieval_audit):
        self.rebuild_candidates = [dict(row) for row in ranked_candidates]
        self.rebuild_audit = dict(retrieval_audit)
        if self.rebuild is not None:
            return self.rebuild(ranked_candidates)
        return _packet([str(row["unit_id"]) for row in ranked_candidates], texts=self.texts)


class _Assessor:
    def __init__(self, *factories) -> None:
        self.factories = list(factories)
        self.requests = []

    def assess(self, request):
        self.requests.append(request)
        factory = self.factories[len(self.requests) - 1]
        if isinstance(factory, BaseException):
            raise factory
        return factory(request)


class _Generation:
    def __init__(self, answer_text="answer [E01]", *, fail=False) -> None:
        self.answer_text = answer_text
        self.fail = fail
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("synthetic generation failure")
        return GenerationResult(
            execution_status="succeeded",
            answer_text=self.answer_text,
            citation_validation=CitationValidation(("E01",), "pass", "pass", ()),
            semantic_request_identity=request.semantic_request_identity,
            execution_config_identity="fake-generation-config",
            request_audit=request.audit_projection(),
            provider_audit={"attempts": []},
        )


def _answer_now(
    request,
    *,
    condition=EvidenceCondition.SUFFICIENT,
    disposition=AnswerDisposition.FULL,
    supported_scope=(),
    unresolved_aspects=(),
    missing_information=(),
    conflicts=(),
):
    return EvidenceAssessmentResult(
        assessment_request_identity=request.request_identity,
        condition=condition,
        action=OrchestrationAction.ANSWER_NOW,
        answer_disposition=disposition,
        supported_scope=tuple(supported_scope),
        unresolved_aspects=tuple(unresolved_aspects),
        missing_information=tuple(missing_information),
        conflicts=tuple(conflicts),
    )


def _supplement(request, query="supplemental query", *, conflicting=False):
    return EvidenceAssessmentResult(
        assessment_request_identity=request.request_identity,
        condition=EvidenceCondition.CONFLICTING if conflicting else EvidenceCondition.INCOMPLETE,
        action=OrchestrationAction.SUPPLEMENT_ONCE,
        answer_disposition=AnswerDisposition.NONE,
        missing_information=() if conflicting else ("missing relation",),
        conflicts=("two incompatible observations",) if conflicting else (),
        unresolved_aspects=("requires one bounded retrieval",),
        supplemental_query=query,
    )


def _unable(request):
    return EvidenceAssessmentResult(
        assessment_request_identity=request.request_identity,
        condition=EvidenceCondition.UNABLE,
        action=OrchestrationAction.STOP,
        answer_disposition=AnswerDisposition.NONE,
        unresolved_aspects=("no reliable answer scope",),
    )


class AdaptiveOrchestrationTests(unittest.TestCase):
    def test_frozen_capability_reuses_existing_evidence_only_path_and_formal_deferred(self):
        state = SimpleNamespace(
            retrieval_unit_manifest_path=Path("ru-manifest.json"),
            assembly_context=object(),
        )
        transport = object()
        reranker = object()
        config = SingleQuestionBackendConfig(reranker_enabled=False)
        capability = FrozenBlockAEvidenceCapability(state, transport, config, reranker)
        expected_round = {"status": "succeeded"}
        expected_packet = _packet(["u1"])
        candidates = _candidates(["u1"])
        with patch(
            "genshin_corpus.rag.adaptive.run_single_question",
            return_value=expected_round,
        ) as existing_path, patch(
            "genshin_corpus.rag.adaptive.assemble_deferred_footprint_charge_packet",
            return_value=expected_packet,
        ) as formal_deferred:
            actual_round = capability.run_round(
                query="  exact query  ",
                round_index=0,
                request_identity="request.round0",
            )
            actual_packet = capability.rebuild_packet(candidates, retrieval_audit={"mode": "test"})

        self.assertIs(actual_round, expected_round)
        self.assertIs(actual_packet, expected_packet)
        self.assertEqual(existing_path.call_args.args, (state, "  exact query  "))
        self.assertEqual(existing_path.call_args.kwargs["execution_mode"], "evidence_only")
        self.assertEqual(existing_path.call_args.kwargs["execution_identity"], "request.round0")
        self.assertIs(existing_path.call_args.kwargs["embedding_transport"], transport)
        self.assertIs(existing_path.call_args.kwargs["config"], config)
        self.assertIs(existing_path.call_args.kwargs["reranker"], reranker)
        self.assertEqual(formal_deferred.call_args.args, (Path("ru-manifest.json"), candidates))
        self.assertIs(formal_deferred.call_args.kwargs["prepared_context"], state.assembly_context)
        self.assertIs(formal_deferred.call_args.kwargs["config"], config.assembly_config)

    def test_round0_preserves_exact_original_query_and_one_round_answer_now(self):
        question = "  原问题保留空格？  "
        capability = _Capability({0: ["u1", "u2"]})
        assessor = _Assessor(_answer_now)
        generation = _Generation()

        result = run_bounded_adaptive_question(
            question,
            block_a=capability,
            assessor=assessor,
            generation_provider=generation,
            execution_identity="adaptive-one-round",
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(capability.queries, [question])
        self.assertEqual(generation.requests[0].question, question)
        self.assertEqual(result["call_counts"], {
            "retrieval_rounds": 1,
            "assessment": 1,
            "embedding": 1,
            "reranker": 1,
            "generation": 1,
        })

    def test_one_supplement_then_second_assessment_binds_rebuilt_packet(self):
        capability = _Capability({0: ["o1", "o2"], 1: ["s1", "s2"]})
        assessor = _Assessor(_supplement, _answer_now)
        result = run_bounded_adaptive_question(
            "original",
            block_a=capability,
            assessor=assessor,
            generation_provider=_Generation(),
            execution_identity="adaptive-supplement",
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(capability.queries, ["original", "supplemental query"])
        self.assertEqual(len(assessor.requests), 2)
        self.assertEqual(assessor.requests[1].supplements_remaining, 0)
        self.assertEqual(
            assessor.requests[1].packet_binding.packet_sha256,
            result["final_packet_binding"]["packet_sha256"],
        )
        self.assertEqual(
            list(assessor.requests[1].packet_binding.evidence_ids),
            result["final_packet_binding"]["evidence_ids"],
        )
        self.assertNotEqual(assessor.requests[0].request_identity, assessor.requests[1].request_identity)
        self.assertEqual(result["call_counts"]["retrieval_rounds"], 2)
        self.assertEqual(result["call_counts"]["assessment"], 2)

    def test_bounded_partial_scope_reaches_instruction_without_question_rewrite(self):
        def partial(request):
            return _answer_now(
                request,
                condition=EvidenceCondition.INCOMPLETE,
                disposition=AnswerDisposition.BOUNDED_PARTIAL,
                supported_scope=("已证实的时间段",),
                unresolved_aspects=("更早时期缺少证据",),
                missing_information=("更早时期记录",),
            )

        generation = _Generation()
        result = run_bounded_adaptive_question(
            "原始问题",
            block_a=_Capability({0: ["u1"]}),
            assessor=_Assessor(partial),
            generation_provider=generation,
            execution_identity="adaptive-partial",
        )

        request = generation.requests[0]
        self.assertEqual(result["answer_disposition"], "bounded_partial")
        self.assertEqual(request.question, "原始问题")
        self.assertNotIn("已证实的时间段", request.instruction.text)
        self.assertNotIn("更早时期缺少证据", request.instruction.text)
        self.assertEqual(request.answer_scope.supported_scope, ("已证实的时间段",))
        self.assertEqual(request.answer_scope.unresolved_aspects, ("更早时期缺少证据",))
        self.assertEqual(
            result["generation"]["bounded_partial"]["unresolved_aspects"],
            ["更早时期缺少证据"],
        )
        with self.assertRaisesRegex(AdaptiveContractError, "incomplete or conflicting"):
            EvidenceAssessmentResult(
                assessment_request_identity="request",
                condition=EvidenceCondition.INCOMPLETE,
                action=OrchestrationAction.ANSWER_NOW,
                answer_disposition=AnswerDisposition.FULL,
                missing_information=("still missing",),
            )
        with self.assertRaisesRegex(AdaptiveContractError, "citation IDs"):
            EvidenceAssessmentResult(
                assessment_request_identity="request",
                condition=EvidenceCondition.INCOMPLETE,
                action=OrchestrationAction.ANSWER_NOW,
                answer_disposition=AnswerDisposition.BOUNDED_PARTIAL,
                supported_scope=("supported by E01",),
                unresolved_aspects=("missing period",),
                missing_information=("period evidence",),
            )

    def test_conflicting_full_answer_is_rejected(self):
        with self.assertRaisesRegex(AdaptiveContractError, "full answer"):
            _answer_now(
                SimpleNamespace(request_identity="request"),
                condition=EvidenceCondition.CONFLICTING,
                conflicts=("sources disagree on date",),
            )

    def test_conflicting_bounded_partial_rejects_evidence_id_in_conflict_text(self):
        with self.assertRaisesRegex(AdaptiveContractError, "conflicts must not carry Evidence Packet citation IDs"):
            EvidenceAssessmentResult(
                assessment_request_identity="request",
                condition=EvidenceCondition.CONFLICTING,
                action=OrchestrationAction.ANSWER_NOW,
                answer_disposition=AnswerDisposition.BOUNDED_PARTIAL,
                supported_scope=("可支持的范围",),
                unresolved_aspects=("日期仍有冲突",),
                conflicts=("E01 与另一条记录冲突",),
            )

    def test_sufficient_supplement_is_rejected(self):
        with self.assertRaisesRegex(AdaptiveContractError, "cannot request a supplement"):
            EvidenceAssessmentResult(
                assessment_request_identity="request",
                condition=EvidenceCondition.SUFFICIENT,
                action=OrchestrationAction.SUPPLEMENT_ONCE,
                answer_disposition=AnswerDisposition.NONE,
                supplemental_query="unneeded",
            )

    def test_unable_stops_without_generation(self):
        generation = _Generation()
        result = run_bounded_adaptive_question(
            "unable question",
            block_a=_Capability({0: ["u1"]}),
            assessor=_Assessor(_unable),
            generation_provider=generation,
            execution_identity="adaptive-unable",
        )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stopping_reason"], "assessment_unable")
        self.assertEqual(generation.requests, [])

        with self.assertRaisesRegex(AdaptiveContractError, "unable evidence can only stop"):
            EvidenceAssessmentResult(
                assessment_request_identity="request",
                condition=EvidenceCondition.UNABLE,
                action=OrchestrationAction.SUPPLEMENT_ONCE,
                answer_disposition=AnswerDisposition.NONE,
                unresolved_aspects=("one bounded recovery attempt is justified",),
                supplemental_query="recovery query",
            )

    def test_generation_request_construction_failure_fails_closed_without_provider_call(self):
        def partial(request):
            return _answer_now(
                request,
                condition=EvidenceCondition.INCOMPLETE,
                disposition=AnswerDisposition.BOUNDED_PARTIAL,
                supported_scope=("已证实范围",),
                unresolved_aspects=("未解决范围",),
                missing_information=("缺失记录",),
            )

        generation = _Generation()
        with patch(
            "genshin_corpus.rag.adaptive.GenerationAnswerScope",
            side_effect=AdaptiveContractError("synthetic scope construction failure"),
        ):
            result = run_bounded_adaptive_question(
                "原始问题",
                block_a=_Capability({0: ["u1"]}),
                assessor=_Assessor(partial),
                generation_provider=generation,
                execution_identity="adaptive-generation-request-failure",
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stopping_reason"], "generation_request_construction_failure")
        self.assertEqual(result["error"]["stage"], "generation")
        self.assertEqual(result["generation"]["status"], "failed")
        self.assertEqual(result["generation"]["stage"], "request_construction")
        self.assertEqual(result["call_counts"]["generation"], 0)
        self.assertEqual(generation.requests, [])
        self.assertIsNone(result["final_answer"])

    def test_same_unit_id_is_consolidated_and_first_new_occurrence_selected(self):
        capability = _Capability({0: ["same", "o2", "o3"], 1: ["same", "new", "later"]})
        result = run_bounded_adaptive_question(
            "question",
            block_a=capability,
            assessor=_Assessor(_supplement, _answer_now),
            generation_provider=_Generation(),
            execution_identity="adaptive-consolidate",
        )
        considered = result["admission"]["considered_supplemental_candidates"]
        self.assertEqual([row["decision"] for row in considered], [
            "same_unit_id_as_round0_candidate",
            "selected_first_eligible",
        ])
        self.assertEqual(result["admission"]["selected_supplemental_unit_id"], "new")
        self.assertEqual([row["unit_id"] for row in capability.rebuild_candidates], ["same", "o2", "new"])

    def test_identical_text_different_unit_ids_remain_distinct(self):
        texts = {"occurrence-a": "identical", "occurrence-b": "identical", "tail": "tail"}
        capability = _Capability(
            {0: ["occurrence-a", "tail"], 1: ["occurrence-b", "tail"]},
            texts=texts,
        )
        result = run_bounded_adaptive_question(
            "question",
            block_a=capability,
            assessor=_Assessor(_supplement, _answer_now),
            generation_provider=_Generation(),
            execution_identity="adaptive-identical-text",
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([row["unit_id"] for row in capability.rebuild_candidates], [
            "occurrence-a",
            "occurrence-b",
        ])
        self.assertEqual(result["final_packet_binding"]["visible_unit_ids"], [
            "occurrence-a",
            "occurrence-b",
        ])

    def test_no_new_occurrence_stops(self):
        generation = _Generation()
        result = run_bounded_adaptive_question(
            "question",
            block_a=_Capability({0: ["u1", "u2"], 1: ["u2", "u1"]}),
            assessor=_Assessor(_supplement),
            generation_provider=generation,
            execution_identity="adaptive-no-new",
        )
        self.assertEqual(result["stopping_reason"], "no_new_occurrence")
        self.assertEqual(generation.requests, [])

    def test_static_identity_mismatch_stops_before_admission(self):
        changed = dict(_STATIC, retrieval_unit_build_identity="other-ru")
        result = run_bounded_adaptive_question(
            "question",
            block_a=_Capability(
                {0: ["o1", "o2"], 1: ["s1", "s2"]},
                static_by_round={0: _STATIC, 1: changed},
            ),
            assessor=_Assessor(_supplement),
            generation_provider=_Generation(),
            execution_identity="adaptive-identity-mismatch",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stopping_reason"], "static_identity_mismatch")

    def test_reranker_runtime_identity_mismatch_stops_before_admission(self):
        capability = _Capability(
            {0: ["o1", "o2"], 1: ["s1", "s2"]},
            reranker_enabled=True,
            reranker_runtime_by_round={
                0: {"model": "bge", "revision": "rev-a", "config": {"max_length": 8192}},
                1: {"model": "bge", "revision": "rev-b", "config": {"max_length": 8192}},
            },
        )
        result = run_bounded_adaptive_question(
            "question",
            block_a=capability,
            assessor=_Assessor(_supplement),
            generation_provider=_Generation(),
            execution_identity="adaptive-reranker-identity-mismatch",
        )
        self.assertEqual(result["stopping_reason"], "static_identity_mismatch")
        self.assertIsNone(result["admission"])

        collision = run_bounded_adaptive_question(
            "question",
            block_a=_Capability({0: ["u1", "u2"], 1: ["u1", "u2"]}),
            assessor=_Assessor(_supplement),
            generation_provider=_Generation(),
            execution_identity="adaptive-packet-identity-collision",
        )
        self.assertEqual(collision["status"], "failed")
        self.assertEqual(collision["stopping_reason"], "round_packet_identity_collision")

    def test_invalid_assessment_and_provider_failure_fail_closed(self):
        def wrong_binding(request):
            return EvidenceAssessmentResult(
                assessment_request_identity="wrong-request",
                condition=EvidenceCondition.SUFFICIENT,
                action=OrchestrationAction.ANSWER_NOW,
                answer_disposition=AnswerDisposition.FULL,
            )

        for identity, assessor, reason in (
            ("adaptive-invalid-assessment", _Assessor(wrong_binding), "invalid_assessment_result"),
            ("adaptive-assessment-failure", _Assessor(RuntimeError("provider failed")), "assessment_provider_failure"),
        ):
            with self.subTest(reason=reason):
                result = run_bounded_adaptive_question(
                    "question",
                    block_a=_Capability({0: ["u1"]}),
                    assessor=assessor,
                    generation_provider=_Generation(),
                    execution_identity=identity,
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["stopping_reason"], reason)

    def test_supplemental_stage_failures_do_not_fall_back(self):
        for stage in ("embedding", "retrieval", "rerank"):
            with self.subTest(stage=stage):
                generation = _Generation()
                result = run_bounded_adaptive_question(
                    "question",
                    block_a=_Capability({0: ["o1", "o2"], 1: ["s1", "s2"]}, failure_stage=stage),
                    assessor=_Assessor(_supplement),
                    generation_provider=generation,
                    execution_identity=f"adaptive-{stage}-failure",
                )
                self.assertEqual(result["stopping_reason"], f"supplemental_{stage}_failure")
                self.assertEqual(generation.requests, [])

    def test_second_supplement_request_stops_at_exhausted_budget(self):
        generation = _Generation()
        result = run_bounded_adaptive_question(
            "question",
            block_a=_Capability({0: ["o1", "o2"], 1: ["s1", "s2"]}),
            assessor=_Assessor(_supplement, lambda request: _supplement(request, "another query")),
            generation_provider=generation,
            execution_identity="adaptive-budget-exhausted",
        )
        self.assertEqual(result["stopping_reason"], "supplement_budget_exhausted")
        self.assertEqual(result["call_counts"]["retrieval_rounds"], 2)
        self.assertEqual(result["call_counts"]["assessment"], 2)
        self.assertEqual(generation.requests, [])

    def test_intermediate_citation_id_is_rejected_against_final_packet(self):
        def one_block_rebuild(candidates):
            return _packet([str(row["unit_id"]) for row in candidates], group_members=True)

        result = run_bounded_adaptive_question(
            "question",
            block_a=_Capability(
                {0: ["o1", "o2"], 1: ["s1", "s2"]},
                rebuild=one_block_rebuild,
            ),
            assessor=_Assessor(_supplement, _answer_now),
            generation_provider=_Generation(answer_text="uses stale id [E02]"),
            execution_identity="adaptive-stale-citation",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stopping_reason"], "citation_validation_failure")
        self.assertEqual(result["citation_validation"]["citation_integrity"], "fail")
        self.assertEqual(result["final_packet_binding"]["evidence_ids"], ["E01"])

    def test_generation_failure_fails_closed(self):
        result = run_bounded_adaptive_question(
            "question",
            block_a=_Capability({0: ["u1"]}),
            assessor=_Assessor(_answer_now),
            generation_provider=_Generation(fail=True),
            execution_identity="adaptive-generation-failure",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stopping_reason"], "generation_failure")
        self.assertIsNone(result["final_answer"])

    def test_candidate_order_packet_identity_stop_reason_and_audit_are_deterministic(self):
        def execute():
            capability = _Capability({0: ["o1", "o2", "o3"], 1: ["s1", "s2", "s3"]})
            outcome = run_bounded_adaptive_question(
                "question",
                block_a=capability,
                assessor=_Assessor(_supplement, _answer_now),
                generation_provider=_Generation(),
                execution_identity="adaptive-deterministic",
            )
            return outcome, capability

        first, first_capability = execute()
        second, second_capability = execute()
        self.assertEqual(first, second)
        self.assertEqual(
            [row["unit_id"] for row in first_capability.rebuild_candidates],
            ["o1", "o2", "s1"],
        )
        self.assertEqual(
            [row["rank"] for row in first_capability.rebuild_candidates],
            [1, 2, 3],
        )
        self.assertEqual(first["stopping_reason"], "answer_generated")
        self.assertEqual(
            first["final_packet_binding"]["packet_sha256"],
            second["final_packet_binding"]["packet_sha256"],
        )
        for row in second_capability.rebuild_candidates:
            self.assertTrue(row["retrieval"]["adaptive_admission"]["numeric_scores_remain_query_local"])


class _ActualAssemblyCapability:
    def __init__(self, manifest_path, context, config, rounds):
        self.manifest_path = manifest_path
        self.context = context
        self.config = config
        self.rounds = rounds

    def _assemble(self, candidates, query):
        return assemble_deferred_footprint_charge_packet(
            self.manifest_path,
            candidates,
            config=self.config,
            retrieval_audit={"query_text": query, "mode": "provider_free_test"},
            prepared_context=self.context,
        )

    def run_round(self, *, query, round_index, request_identity):
        candidates = self.rounds[round_index]
        packet = self._assemble(candidates, query)
        return _round_result(
            query=query,
            execution_identity=request_identity,
            round_index=round_index,
            unit_ids=[str(row["unit_id"]) for row in candidates],
            packet=packet,
        )

    def rebuild_packet(self, ranked_candidates, *, retrieval_audit):
        return assemble_deferred_footprint_charge_packet(
            self.manifest_path,
            ranked_candidates,
            config=self.config,
            retrieval_audit=retrieval_audit,
            prepared_context=self.context,
        )


class FormalDeferredAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("data/retrieval") / f".adaptive-kernel-test-{uuid4().hex}"
        self.root.mkdir(parents=True)
        fixture = Path(__file__).parents[1] / "fixtures" / "retrieval" / "canonical-rag-w1-record.json"
        record = json.loads(fixture.read_text(encoding="utf-8"))
        record_path = self.root / "canonical-record.json"
        record_body = canonical_json_bytes(record)
        record_path.write_bytes(record_body)
        canonical_manifest = {
            "status": "complete",
            "canonical_run_id": "adaptive-kernel-fixture",
            "source": "mihoyo_obc",
            "locale": "zh-cn",
            "input_record_count": 1,
            "accounted_record_count": 1,
            "input_integrity_failure_count": 0,
            "dependencies": {"canonical_versions": {
                "schema_version": "phase03-draft-0.1",
                "transform_version": "obc-modules-as-sections-0.1",
                "structural_normalization_version": "none-0.1",
                "classification_rule_versions": {},
            }},
            "records": [{
                "record_id": record["record_id"],
                "canonical_record_path": str(record_path),
                "canonical_record_sha256": hashlib.sha256(record_body).hexdigest(),
                "canonical_status": "canonical",
            }],
        }
        canonical_manifest_path = self.root / "canonical-manifest.json"
        canonical_manifest_path.write_bytes(canonical_json_bytes(canonical_manifest))
        output = self.root / "ru"
        build_retrieval_units(
            canonical_manifest_path,
            output,
            config=RetrievalUnitBuildConfig(text_fragment_chars=4, structured_fragment_chars=10),
        )
        self.manifest_path = output / "metadata" / "manifest.json"
        _, rows = load_retrieval_units(self.manifest_path)
        self.units = list(rows)
        self.context = prepare_evidence_assembly_context(self.manifest_path)

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root)

    def _root_chars(self, unit_id):
        packet = assemble_deferred_footprint_charge_packet(
            self.manifest_path,
            [{"unit_id": unit_id, "rank": 1, "retrieval": {}}],
            config=EvidenceAssemblyConfig(total_context_chars=12000, per_block_chars=3000),
            prepared_context=self.context,
        )
        return packet["admission_contract"]["direct_footprints"][0]["root_projection"]["char_count"]

    def _failure_pair(self):
        ids = [str(row["unit_id"]) for row in self.units]
        for left in ids:
            for right in ids:
                if left == right:
                    continue
                total = max(self._root_chars(left), self._root_chars(right))
                config = EvidenceAssemblyConfig(total_context_chars=total, per_block_chars=3000)
                packet = assemble_deferred_footprint_charge_packet(
                    self.manifest_path,
                    _candidates([left, right]),
                    config=config,
                    prepared_context=self.context,
                )
                visible = {
                    member["unit_id"]
                    for evidence in packet["evidence"]
                    for member in evidence["members"]
                }
                if left in visible and right not in visible:
                    return left, right, config
        self.fail("fixture lacks a deterministic Formal Deferred one-slot visibility failure")

    def test_supplemental_admission_success_uses_actual_formal_deferred_visibility(self):
        ids = [str(row["unit_id"]) for row in self.units[:4]]
        capability = _ActualAssemblyCapability(
            self.manifest_path,
            self.context,
            EvidenceAssemblyConfig(total_context_chars=12000, per_block_chars=3000),
            {0: _candidates(ids[:2]), 1: _candidates(ids[2:4])},
        )
        result = run_bounded_adaptive_question(
            "question",
            block_a=capability,
            assessor=_Assessor(_supplement, _answer_now),
            generation_provider=_Generation(),
            execution_identity="adaptive-formal-success",
            output_root=self.root / "runs",
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["admission"]["selected_supplemental_visible"])
        self.assertIn(
            result["admission"]["selected_supplemental_unit_id"],
            result["final_packet_binding"]["visible_unit_ids"],
        )
        run_root = self.root / "runs" / "adaptive-formal-success"
        self.assertTrue((run_root / "adaptive_rag_result.json").is_file())
        self.assertTrue((run_root / "packet" / "evidence_packet.json").is_file())
        self.assertTrue((run_root / "generation" / "generation_result.json").is_file())
        persisted = json.loads((run_root / "adaptive_rag_result.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["rounds"], result["rounds"])
        self.assertEqual(persisted["assessments"], result["assessments"])
        self.assertEqual(persisted["final_packet_binding"], result["final_packet_binding"])

    def test_supplemental_admission_failure_uses_actual_formal_deferred_visibility(self):
        left, right, config = self._failure_pair()
        extras = [
            str(row["unit_id"])
            for row in self.units
            if str(row["unit_id"]) not in {left, right}
        ][:2]
        capability = _ActualAssemblyCapability(
            self.manifest_path,
            self.context,
            config,
            {0: _candidates([left, extras[0]]), 1: _candidates([right, extras[1]])},
        )
        generation = _Generation()
        result = run_bounded_adaptive_question(
            "question",
            block_a=capability,
            assessor=_Assessor(_supplement),
            generation_provider=generation,
            execution_identity="adaptive-formal-failure",
        )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stopping_reason"], "supplemental_occurrence_not_admitted")
        self.assertFalse(result["admission"]["selected_supplemental_visible"])
        self.assertNotIn(right, result["final_packet_binding"]["visible_unit_ids"])
        self.assertEqual(generation.requests, [])


if __name__ == "__main__":
    unittest.main()
