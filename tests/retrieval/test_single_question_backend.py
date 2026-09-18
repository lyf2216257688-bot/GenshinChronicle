from __future__ import annotations

import shutil
import tempfile
import gc
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
import numpy as np

from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.rag.backend import (
    PreparedRagState,
    SingleQuestionBackendConfig,
    run_single_question,
)
from genshin_corpus.retrieval.qwen_m2_query_vectors import (
    ACCEPTED_QWEN_M2_QUERY_ARTIFACT_IDENTITY,
    ACCEPTED_QWEN_M2_QUERY_VECTORS_SHA256,
    AcceptedQwenQueryVector,
)


class _Retriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.lexical_manifest = {"arm_build_identity": "lex", "retrieval_unit_build_identity": "ru"}
        self.dense_manifest = {"arm_build_identity": "dense", "retrieval_unit_build_identity": "ru"}

    def candidates_for_query(self, query, vector, *, instruction, top_k=20, rrf_k=60):
        self.calls.append((query, top_k, rrf_k))
        rows = [
            {"unit_id": f"u{index}", "rank": index, "retrieval": {"mode": "hybrid", "score": 1.0 / index}}
            for index in range(1, top_k + 1)
        ]
        lexical = [dict(row, retrieval={"mode": "lexical", "score": row["retrieval"]["score"], "arm_build_identity": "lex"}) for row in rows]
        dense = [dict(row, retrieval={"mode": "dense", "score": row["retrieval"]["score"], "arm_build_identity": "dense"}) for row in rows]
        hybrid = [dict(row, retrieval={"mode": "hybrid", "score": row["retrieval"]["score"], "arm_build_identities": {"lexical": "lex", "dense": "dense"}}) for row in rows]
        return {"lexical": lexical, "dense": dense, "hybrid": hybrid}


class _Reranker:
    def __init__(self) -> None:
        self.calls = 0
        self.last_response_metadata = {"request_id": "rerank-1", "usage": {"total_tokens": 5}}

    def runtime_identity(self):
        return {"implementation": "test-reranker", "revision": "stable-a", "max_length": 8192}

    def rerank(self, request):
        from genshin_corpus.retrieval.reranking import RerankScore

        self.calls += 1
        return [RerankScore(row.unit_id, row.original_rank, float(len(request.candidates) - row.original_rank), 0) for row in request.candidates]


class _Generation:
    def __init__(self, answer_text: str = "回答 [E01]") -> None:
        self.calls = 0
        self.answer_text = answer_text

    def generate(self, request):
        self.calls += 1
        validation = SimpleNamespace(to_dict=lambda: {
            "citation_tokens": ["E01"],
            "citation_integrity": "pass",
            "citation_coverage": "pass",
            "validation_reasons": [],
            "semantic_faithfulness": "not_evaluated",
        })
        return SimpleNamespace(
            execution_status="succeeded",
            answer_text=self.answer_text,
            citation_validation=validation,
            to_dict=lambda: {"result": {"answer_text": self.answer_text, "citation_integrity": "pass"}},
        )


class _EmbeddingTransport:
    def embed(self, request):
        raise AssertionError("encode_qwen_query is patched in these unit tests")


class _MMapHandle:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class SingleQuestionBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.retriever = _Retriever()
        self.prepared = PreparedRagState(
            retrieval_unit_manifest_path=Path("ru.json"),
            lexical_manifest_path=Path("lex.json"),
            dense_manifest_path=Path("dense.json"),
            retriever=self.retriever,
            assembly_context=object(),
            retrieval_unit_build_identity="ru",
            lexical_build_identity="lex",
            dense_build_identity="dense",
        )
        self.embedding_calls = 0
        self.packet = {
            "schema_version": "phase04-evidence-packet-0.1",
            "evidence": [{"evidence_id": "E01", "text": "证据", "members": []}],
        }
        self.disabled_config = SingleQuestionBackendConfig(
            candidate_depth=20,
            candidate_supply_depth=20,
            rerank_depth=20,
            final_top_n=20,
            rerank_output_k=20,
            reranker_enabled=False,
        )

    def test_production_defaults_select_c_and_online_reranker(self) -> None:
        config = SingleQuestionBackendConfig()
        self.assertTrue(config.reranker_enabled)
        self.assertEqual(config.candidate_supply_depth, 500)
        self.assertEqual(config.rerank_depth, 500)
        self.assertEqual(config.final_top_n, 20)
        self.assertEqual(config.rrf_k, 60)
        self.assertEqual(config.fusion_config.to_dict()["hybrid_weight"], 0.35)
        self.assertEqual(config.fusion_config.to_dict()["rerank_weight"], 0.65)
        self.assertEqual(config.fusion_config.to_dict()["denominator"], 60)

    def test_default_off_control_keeps_supply_and_sends_hybrid_top20_to_assembly(self) -> None:
        config = SingleQuestionBackendConfig(reranker_enabled=False)
        reranker = Mock()
        self.assertEqual(config.candidate_supply_depth, 500)
        self.assertEqual(config.rerank_depth, 500)
        self.assertEqual(config.final_top_n, 20)
        self.assertFalse(config.final_top_n_explicit)

        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.fuse_ranked_candidates",
            side_effect=AssertionError("OFF control must not call fusion"),
        ) as fusion, patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet",
            return_value=self.packet,
        ) as assemble:
            result = run_single_question(
                self.prepared,
                "问题",
                embedding_transport=_EmbeddingTransport(),
                config=config,
                reranker=reranker,
                execution_mode="evidence_only",
                execution_identity="run-default-off-control",
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.retriever.calls, [("问题", 500, 60)])
        self.assertEqual(len(assemble.call_args.args[1]), 20)
        self.assertEqual(result["retrieval_trace"]["candidate_supply_depth"], 500)
        self.assertEqual(result["rerank_trace"]["status"], "disabled_by_explicit_config")
        self.assertEqual(result["rerank_trace"]["final_top_n"], 20)
        self.assertEqual(result["telemetry"]["provider_call_counts"]["reranker"], 0)
        reranker.rerank.assert_not_called()
        fusion.assert_not_called()

    def _embedding(self, transport, query):
        self.embedding_calls += 1
        return [1.0], SimpleNamespace(request_identity=f"embedding-{self.embedding_calls}"), SimpleNamespace(
            returned_model="qwen3.7-text-embedding", returned_role="query", provider_request_id=f"provider-{self.embedding_calls}"
        )

    def _accepted_precomputed_query(self, question: str = "问题") -> AcceptedQwenQueryVector:
        question_id = "Q001"
        return AcceptedQwenQueryVector(
            question_id=question_id,
            question=question,
            question_identity=sha256_json({"question_id": question_id, "question": question}),
            vector=np.ones(2048, dtype=np.float32),
            row_index=0,
            request_identity="accepted-request-q001",
            artifact_identity=ACCEPTED_QWEN_M2_QUERY_ARTIFACT_IDENTITY,
            vectors_sha256=ACCEPTED_QWEN_M2_QUERY_VECTORS_SHA256,
            manifest_sha256="accepted-manifest-sha256",
            configuration={
                "schema_version": "phase04-rag-qwen37-m2-query-vectors-0.1",
                "model_id": "qwen3.7-text-embedding",
                "dimension": 2048,
                "output": "dense",
                "role": "query",
                "custom_query_instruction": None,
                "batch_size": 20,
            },
        )

    def test_evidence_only_skips_generation_and_returns_packet(self):
        generation = _Generation()
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ) as assemble:
            result = run_single_question(
                self.prepared,
                "问题一",
                embedding_transport=_EmbeddingTransport(),
                generation_provider=generation,
                config=self.disabled_config,
                execution_mode="evidence_only",
                execution_identity="run-evidence",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["execution_mode"], "evidence_only")
        self.assertEqual(result["generation"]["status"], "skipped")
        self.assertEqual(result["citation_validation"]["status"], "not_applicable")
        self.assertEqual(result["rerank_trace"]["status"], "disabled_by_explicit_config")
        self.assertEqual(result["rerank_trace"]["candidate_depth"], 20)
        self.assertIsNone(result["rerank_trace"]["rerank_output_k"])
        self.assertEqual(generation.calls, 0)
        assemble.assert_called_once()
        self.assertEqual(result["telemetry"]["provider_call_counts"], {"embedding": 1, "reranker": 0, "generation": 0})
        self.assertEqual(result["telemetry"]["counts"]["candidate_supply"], 20)

    def test_accepted_precomputed_query_reuses_vector_without_embedding_call(self):
        precomputed = self._accepted_precomputed_query()
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=AssertionError("must not embed")), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ):
            result = run_single_question(
                self.prepared,
                "问题",
                precomputed_query=precomputed,
                config=SingleQuestionBackendConfig(reranker_enabled=False),
                execution_mode="evidence_only",
                execution_identity="run-precomputed-query",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["telemetry"]["provider_call_counts"]["embedding"], 0)
        self.assertEqual(result["audit"]["embedding"]["source"], "accepted_precomputed_query_vector")
        self.assertEqual(result["audit"]["embedding"]["artifact_identity"], ACCEPTED_QWEN_M2_QUERY_ARTIFACT_IDENTITY)

    def test_control_and_vnext_share_accepted_query_binding_provider_free(self):
        precomputed = self._accepted_precomputed_query()
        reranker = _Reranker()
        fields = {
            "u1": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "正文一"},
            "u2": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "正文二"},
        }
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=AssertionError("must not embed")), patch(
            "genshin_corpus.rag.backend._reranker_fields_from_verified_ru", return_value=fields
        ), patch("genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet):
            control = run_single_question(
                self.prepared,
                "问题",
                precomputed_query=precomputed,
                config=SingleQuestionBackendConfig(reranker_enabled=False),
                execution_mode="evidence_only",
                execution_identity="run-preflight-control",
            )
            vnext = run_single_question(
                self.prepared,
                "问题",
                precomputed_query=precomputed,
                config=SingleQuestionBackendConfig(candidate_depth=3, candidate_supply_depth=3, rerank_depth=2, final_top_n=2, reranker_enabled=True, rerank_output_k=2),
                reranker=reranker,
                execution_mode="evidence_only",
                execution_identity="run-preflight-vnext",
            )
        self.assertEqual(control["status"], "succeeded")
        self.assertEqual(vnext["status"], "succeeded")
        self.assertEqual(control["audit"]["embedding"]["artifact_identity"], vnext["audit"]["embedding"]["artifact_identity"])
        self.assertEqual(control["audit"]["embedding"]["question_identity"], vnext["audit"]["embedding"]["question_identity"])
        self.assertEqual(control["audit"]["embedding"]["request_identity"], vnext["audit"]["embedding"]["request_identity"])
        self.assertEqual(control["telemetry"]["provider_call_counts"]["embedding"], 0)
        self.assertEqual(vnext["telemetry"]["provider_call_counts"]["embedding"], 0)
        self.assertEqual(vnext["telemetry"]["provider_call_counts"]["reranker"], 1)

    def test_precomputed_query_binding_mismatch_fails_closed(self):
        precomputed = self._accepted_precomputed_query()
        invalid = AcceptedQwenQueryVector(**{**precomputed.__dict__, "question": "different"})
        result = run_single_question(
            self.prepared,
            "问题",
            precomputed_query=invalid,
            config=SingleQuestionBackendConfig(reranker_enabled=False),
            execution_mode="evidence_only",
            execution_identity="run-precomputed-query-invalid",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["stage"], "embedding")
        self.assertEqual(result["telemetry"]["provider_call_counts"]["embedding"], 0)

    def test_disabled_legacy_rerank_output_does_not_truncate_hybrid_assembly(self):
        config = SingleQuestionBackendConfig(candidate_depth=3, rerank_output_k=1, reranker_enabled=False)
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ) as assemble:
            result = run_single_question(
                self.prepared,
                "问题",
                embedding_transport=_EmbeddingTransport(),
                config=config,
                execution_mode="evidence_only",
                execution_identity="run-legacy-disabled-depth",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(assemble.call_args.args[1]), 3)
        self.assertEqual(config.final_top_n, 3)
        self.assertFalse(result["audit"]["config"]["final_top_n_explicit"])

    def test_explicit_final_top_n_truncates_disabled_hybrid_assembly(self):
        config = SingleQuestionBackendConfig(candidate_depth=3, final_top_n=1, reranker_enabled=False)
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ) as assemble:
            result = run_single_question(
                self.prepared,
                "问题",
                embedding_transport=_EmbeddingTransport(),
                config=config,
                execution_mode="evidence_only",
                execution_identity="run-explicit-final-depth",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(assemble.call_args.args[1]), 1)
        self.assertTrue(result["audit"]["config"]["final_top_n_explicit"])

    def test_generate_answer_runs_generation_and_citation_validation(self):
        generation = _Generation()
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ):
            result = run_single_question(
                self.prepared,
                "问题一",
                embedding_transport=_EmbeddingTransport(),
                generation_provider=generation,
                config=self.disabled_config,
                execution_mode="generate_answer",
                execution_identity="run-generate",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["generation"]["status"], "executed")
        self.assertEqual(result["citation_validation"]["citation_integrity"], "pass")
        self.assertEqual(generation.calls, 1)

    def test_modes_share_upstream_behavior_and_each_question_embeds_freshly(self):
        generation = _Generation()
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ):
            evidence = run_single_question(self.prepared, "相同问题", embedding_transport=_EmbeddingTransport(), generation_provider=generation, config=self.disabled_config, execution_mode="evidence_only", execution_identity="run-a")
            answer = run_single_question(self.prepared, "相同问题", embedding_transport=_EmbeddingTransport(), generation_provider=generation, config=self.disabled_config, execution_mode="generate_answer", execution_identity="run-b")
        self.assertEqual(evidence["retrieval_trace"]["windows"], answer["retrieval_trace"]["windows"])
        self.assertEqual(self.retriever.calls, [("相同问题", 20, 60), ("相同问题", 20, 60)])
        self.assertEqual(self.embedding_calls, 2)
        self.assertEqual(generation.calls, 1)

    def test_invalid_citation_fails_local_backend_gate_even_if_provider_claims_success(self):
        generation = _Generation(answer_text="回答 [E99]")
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ):
            result = run_single_question(
                self.prepared,
                "问题一",
                embedding_transport=_EmbeddingTransport(),
                generation_provider=generation,
                config=self.disabled_config,
                execution_mode="generate_answer",
                execution_identity="run-invalid-citation",
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["stage"], "citation_validation")
        self.assertEqual(result["citation_validation"]["citation_integrity"], "fail")
        self.assertEqual(result["citation_validation"]["validation_reasons"][0]["kind"], "unknown_evidence_id")

    def test_reranker_depths_and_failure_are_audited_fail_closed(self):
        reranker = _Reranker()
        config = SingleQuestionBackendConfig(candidate_depth=3, rerank_output_k=2, reranker_enabled=True)
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend._reranker_fields_from_verified_ru", return_value={
                "u1": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "a"},
                "u2": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "b"},
                "u3": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "c"},
            }
        ), patch("genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet):
            result = run_single_question(self.prepared, "问题", embedding_transport=_EmbeddingTransport(), generation_provider=_Generation(), config=config, reranker=reranker, execution_identity="run-rerank")
        self.assertEqual(result["rerank_trace"]["status"], "executed_successfully")
        self.assertEqual(result["rerank_trace"]["candidate_depth"], 3)
        self.assertEqual(result["rerank_trace"]["rerank_output_k"], 2)

        failing = Mock()
        failing.rerank.side_effect = RuntimeError("rerank failed")
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend._reranker_fields_from_verified_ru", return_value={
                "u1": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "a"},
                "u2": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "b"},
            }
        ):
            failed = run_single_question(self.prepared, "问题", embedding_transport=_EmbeddingTransport(), generation_provider=_Generation(), config=SingleQuestionBackendConfig(candidate_depth=2, rerank_output_k=1, reranker_enabled=True), reranker=failing, execution_identity="run-rerank-fail")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["stage"], "rerank")
        self.assertEqual(failed["rerank_trace"]["status"], "failed")

    def test_reranker_projection_depth_fusion_and_telemetry_are_audited(self):
        reranker = _Reranker()
        config = SingleQuestionBackendConfig(
            candidate_supply_depth=3,
            rerank_depth=2,
            final_top_n=2,
            reranker_enabled=True,
            rerank_output_k=2,
        )
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend._reranker_fields_from_verified_ru", return_value={
                "u1": {"record_title": "标题", "section_name": "章节", "speaker": "派蒙", "retrieval_visible_text": "正文一"},
                "u2": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "正文二"},
            }
        ) as fields, patch("genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet):
            result = run_single_question(
                self.prepared,
                "问题",
                embedding_transport=_EmbeddingTransport(),
                config=config,
                reranker=reranker,
                execution_mode="evidence_only",
                execution_identity="run-rerank-fusion",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["rerank_trace"]["candidate_supply_depth"], 3)
        self.assertEqual(result["rerank_trace"]["rerank_depth"], 2)
        self.assertEqual(result["rerank_trace"]["final_top_n"], 2)
        self.assertEqual(result["rerank_trace"]["not_reranked_unit_ids"], ["u3"])
        self.assertEqual(
            result["rerank_trace"]["runtime_identity"],
            {"implementation": "test-reranker", "revision": "stable-a", "max_length": 8192},
        )
        self.assertEqual(result["telemetry"]["counts"]["rerank"], 2)
        self.assertEqual(result["telemetry"]["counts"]["final"], 2)
        self.assertEqual(result["telemetry"]["reranker_projection"]["candidate_count"], 2)
        self.assertIsNone(result["telemetry"]["reranker_projection"]["token_estimate"])
        self.assertIn("rrf", result["timing_seconds"])
        self.assertIn("rank_fusion", result["timing_seconds"])
        self.assertEqual(fields.call_count, 1)

    def test_reranker_audit_does_not_persist_raw_provider_metadata(self):
        reranker = _Reranker()
        reranker.last_response_metadata = {
            "request_id": "safe",
            "usage": {"total_tokens": 5, "raw_payload": "secret", "reasoning_tokens": 4},
            "raw_response": "secret",
            "reasoning": "private",
        }
        config = SingleQuestionBackendConfig(candidate_depth=2, candidate_supply_depth=2, final_top_n=2, reranker_enabled=True)
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend._reranker_fields_from_verified_ru", return_value={
                "u1": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "a"},
                "u2": {"record_title": "标题", "section_name": "章节", "speaker": "", "retrieval_visible_text": "b"},
            }
        ), patch("genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet):
            result = run_single_question(
                self.prepared,
                "问题",
                embedding_transport=_EmbeddingTransport(),
                config=config,
                reranker=reranker,
                execution_mode="evidence_only",
                execution_identity="run-safe-rerank-audit",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["rerank_trace"]["provider"], {"request_id": "safe", "usage": {"total_tokens": 5}})
        self.assertNotIn("raw_response", str(result))
        self.assertNotIn("reasoning", str(result))

    def test_depth_controls_reject_invalid_operating_points(self):
        with self.assertRaises(ValueError):
            SingleQuestionBackendConfig(candidate_depth=0)
        with self.assertRaises(ValueError):
            SingleQuestionBackendConfig(candidate_depth=2, rerank_output_k=3, reranker_enabled=True)

    def test_output_root_is_optional_and_run_scoped(self):
        generation = _Generation()
        raw = Path("tests") / "_single-rag-output"
        shutil.rmtree(raw, ignore_errors=True)
        raw.mkdir(parents=True)
        try:
            base = raw
            with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
                "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
            ), patch("genshin_corpus.rag.backend.write_evidence_packet", return_value={"json": {"path": "evidence_packet.json"}}) as packet_writer:
                no_persist = run_single_question(self.prepared, "无落盘", embedding_transport=_EmbeddingTransport(), generation_provider=generation, config=self.disabled_config, execution_mode="evidence_only", execution_identity="run-none")
                self.assertFalse(list(base.iterdir()))
                persisted = run_single_question(self.prepared, "落盘", embedding_transport=_EmbeddingTransport(), generation_provider=generation, config=self.disabled_config, execution_mode="evidence_only", execution_identity="run-persist", output_root=base)
            run_root = base / "run-persist"
            self.assertTrue((run_root / "rag_result.json").is_file())
            self.assertEqual(persisted["persistence"]["base_output_root"], str(base.resolve()))
            packet_writer.assert_called_once()
            self.assertTrue(str(packet_writer.call_args.args[0]).startswith(str(run_root)))
            overwritten = run_single_question(self.prepared, "再次落盘", embedding_transport=_EmbeddingTransport(), generation_provider=generation, config=self.disabled_config, execution_mode="evidence_only", execution_identity="run-persist", output_root=base)
            self.assertEqual(overwritten["status"], "failed")
            self.assertEqual(overwritten["error"]["stage"], "persistence")
        finally:
            shutil.rmtree(raw, ignore_errors=True)

    def test_generate_answer_persists_everything_under_selected_root(self):
        generation = _Generation()
        raw = Path("tests") / "_single-rag-generate-output"
        shutil.rmtree(raw, ignore_errors=True)
        raw.mkdir(parents=True)
        try:
            with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
                "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
            ), patch(
                "genshin_corpus.rag.backend.write_evidence_packet", return_value={"json": {"path": "evidence_packet.json"}}
            ) as packet_writer, patch(
                "genshin_corpus.generation.generation.write_generation_result", return_value={"path": "generation_result.json"}
            ) as generation_writer:
                result = run_single_question(
                    self.prepared,
                    "需要回答的问题",
                    embedding_transport=_EmbeddingTransport(),
                    generation_provider=generation,
                    config=self.disabled_config,
                    execution_mode="generate_answer",
                    execution_identity="run-generate-persist",
                    output_root=raw,
                )
            self.assertEqual(result["status"], "succeeded")
            run_root = (raw / "run-generate-persist").resolve()
            self.assertTrue((run_root / "rag_result.json").is_file())
            packet_path = Path(packet_writer.call_args.args[0]).resolve()
            generation_path = Path(generation_writer.call_args.args[0]).resolve()
            self.assertTrue(packet_path.is_relative_to(run_root))
            self.assertTrue(generation_path.is_relative_to(run_root))
            self.assertEqual(result["persistence"]["base_output_root"], str(raw.resolve()))
        finally:
            shutil.rmtree(raw, ignore_errors=True)

    def test_prepare_state_loads_heavy_resources_once_for_multiple_questions(self):
        from genshin_corpus.rag.backend import prepare_rag_state

        context = SimpleNamespace(retrieval_unit_build_identity="ru")
        with patch("genshin_corpus.rag.backend.load_batch_candidate_retriever", return_value=self.retriever) as load_retriever, patch(
            "genshin_corpus.rag.backend.prepare_evidence_assembly_context", return_value=context
        ) as prepare_context:
            prepared = prepare_rag_state(
                retrieval_unit_manifest_path=Path("ru.json"),
                lexical_manifest_path=Path("lex.json"),
                dense_manifest_path=Path("dense.json"),
            )
            self.assertIs(prepared.retriever, self.retriever)
            self.assertEqual(load_retriever.call_count, 1)
            self.assertEqual(prepare_context.call_count, 1)

        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet
        ):
            first = run_single_question(prepared, "问题甲", embedding_transport=_EmbeddingTransport(), config=self.disabled_config, execution_mode="evidence_only", execution_identity="prepared-a")
            second = run_single_question(prepared, "问题乙", embedding_transport=_EmbeddingTransport(), config=self.disabled_config, execution_mode="evidence_only", execution_identity="prepared-b")
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(second["status"], "succeeded")
        self.assertEqual(len(self.retriever.calls), 2)
        self.assertEqual(self.embedding_calls, 2)

    def test_prepared_state_close_releases_owned_dense_mmap_once(self):
        handle = _MMapHandle()
        retriever = SimpleNamespace(dense_vectors=SimpleNamespace(_mmap=handle))
        state = PreparedRagState(
            retrieval_unit_manifest_path=Path("ru.json"),
            lexical_manifest_path=Path("lex.json"),
            dense_manifest_path=Path("dense.json"),
            retriever=retriever,
            assembly_context=object(),
            retrieval_unit_build_identity="ru",
            lexical_build_identity="lex",
            dense_build_identity="dense",
        )
        state.close()
        state.close()
        self.assertTrue(handle.closed)
        self.assertEqual(handle.close_calls, 1)

    def test_use_after_close_fails_before_embedding_retrieval_or_generation(self):
        generation = _Generation()
        self.prepared.close()
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding):
            result = run_single_question(
                self.prepared,
                "关闭后的问题",
                embedding_transport=_EmbeddingTransport(),
                generation_provider=generation,
                config=self.disabled_config,
                execution_identity="after-close",
                output_root=Path("tests") / "_must-not-be-created",
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["stage"], "lifecycle")
        self.assertEqual(result["error"]["category"], "state_closed")
        self.assertEqual(self.embedding_calls, 0)
        self.assertEqual(self.retriever.calls, [])
        self.assertEqual(generation.calls, 0)

    def test_prepare_failure_releases_loaded_dense_mmap(self):
        handle = _MMapHandle()
        retriever = SimpleNamespace(
            dense_vectors=SimpleNamespace(_mmap=handle),
            lexical_manifest={"arm_build_identity": "lex", "retrieval_unit_build_identity": "ru"},
            dense_manifest={"arm_build_identity": "dense", "retrieval_unit_build_identity": "other"},
        )
        context = SimpleNamespace(retrieval_unit_build_identity="ru")
        from genshin_corpus.rag.backend import RagBackendPreparationError, prepare_rag_state

        with patch("genshin_corpus.rag.backend.load_batch_candidate_retriever", return_value=retriever), patch(
            "genshin_corpus.rag.backend.prepare_evidence_assembly_context", return_value=context
        ):
            with self.assertRaises(RagBackendPreparationError):
                prepare_rag_state(
                    retrieval_unit_manifest_path=Path("ru.json"),
                    lexical_manifest_path=Path("lex.json"),
                    dense_manifest_path=Path("dense.json"),
                )
        self.assertTrue(handle.closed)
        self.assertEqual(handle.close_calls, 1)

    def test_real_numpy_memmap_can_be_released_and_deleted(self):
        import numpy as np

        root = Path("tests") / f"_single-rag-mmap-{uuid4().hex}"
        root.mkdir(parents=True)
        vector_path = root / "vectors.npy"
        try:
            np.save(vector_path, np.ones((2, 2), dtype=np.float32))
            vectors = np.load(vector_path, allow_pickle=False, mmap_mode="r")
            retriever = SimpleNamespace(dense_vectors=vectors)
            state = PreparedRagState(
                retrieval_unit_manifest_path=Path("ru.json"),
                lexical_manifest_path=Path("lex.json"),
                dense_manifest_path=vector_path,
                retriever=retriever,
                assembly_context=object(),
                retrieval_unit_build_identity="ru",
                lexical_build_identity="lex",
                dense_build_identity="dense",
            )
            state.close()
            del vectors
            del retriever
            gc.collect()
            vector_path.unlink()
            self.assertFalse(vector_path.exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
