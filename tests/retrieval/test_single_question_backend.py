from __future__ import annotations

import shutil
import tempfile
import gc
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from genshin_corpus.rag.backend import (
    PreparedRagState,
    SingleQuestionBackendConfig,
    run_single_question,
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

    def _embedding(self, transport, query):
        self.embedding_calls += 1
        return [1.0], SimpleNamespace(request_identity=f"embedding-{self.embedding_calls}"), SimpleNamespace(
            returned_model="qwen3.7-text-embedding", returned_role="query", provider_request_id=f"provider-{self.embedding_calls}"
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
            evidence = run_single_question(self.prepared, "相同问题", embedding_transport=_EmbeddingTransport(), generation_provider=generation, execution_mode="evidence_only", execution_identity="run-a")
            answer = run_single_question(self.prepared, "相同问题", embedding_transport=_EmbeddingTransport(), generation_provider=generation, execution_mode="generate_answer", execution_identity="run-b")
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
            "genshin_corpus.rag.backend.retrieval_unit_texts", return_value={"u1": "a", "u2": "b", "u3": "c"}
        ), patch("genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet", return_value=self.packet):
            result = run_single_question(self.prepared, "问题", embedding_transport=_EmbeddingTransport(), generation_provider=_Generation(), config=config, reranker=reranker, execution_identity="run-rerank")
        self.assertEqual(result["rerank_trace"]["status"], "executed_successfully")
        self.assertEqual(result["rerank_trace"]["candidate_depth"], 3)
        self.assertEqual(result["rerank_trace"]["rerank_output_k"], 2)

        failing = Mock()
        failing.rerank.side_effect = RuntimeError("rerank failed")
        with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=self._embedding), patch(
            "genshin_corpus.rag.backend.retrieval_unit_texts", return_value={"u1": "a", "u2": "b"}
        ):
            failed = run_single_question(self.prepared, "问题", embedding_transport=_EmbeddingTransport(), generation_provider=_Generation(), config=SingleQuestionBackendConfig(candidate_depth=2, rerank_output_k=1, reranker_enabled=True), reranker=failing, execution_identity="run-rerank-fail")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["stage"], "rerank")
        self.assertEqual(failed["rerank_trace"]["status"], "failed")

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
                no_persist = run_single_question(self.prepared, "无落盘", embedding_transport=_EmbeddingTransport(), generation_provider=generation, execution_mode="evidence_only", execution_identity="run-none")
                self.assertFalse(list(base.iterdir()))
                persisted = run_single_question(self.prepared, "落盘", embedding_transport=_EmbeddingTransport(), generation_provider=generation, execution_mode="evidence_only", execution_identity="run-persist", output_root=base)
            run_root = base / "run-persist"
            self.assertTrue((run_root / "rag_result.json").is_file())
            self.assertEqual(persisted["persistence"]["base_output_root"], str(base.resolve()))
            packet_writer.assert_called_once()
            self.assertTrue(str(packet_writer.call_args.args[0]).startswith(str(run_root)))
            overwritten = run_single_question(self.prepared, "再次落盘", embedding_transport=_EmbeddingTransport(), generation_provider=generation, execution_mode="evidence_only", execution_identity="run-persist", output_root=base)
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
            first = run_single_question(prepared, "问题甲", embedding_transport=_EmbeddingTransport(), execution_mode="evidence_only", execution_identity="prepared-a")
            second = run_single_question(prepared, "问题乙", embedding_transport=_EmbeddingTransport(), execution_mode="evidence_only", execution_identity="prepared-b")
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
