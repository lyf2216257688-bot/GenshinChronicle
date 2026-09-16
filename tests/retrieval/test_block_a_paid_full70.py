from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import genshin_corpus.retrieval.block_a_paid_full70 as paid
from genshin_corpus.retrieval.block_a_paid_full70 import (
    BlockAPaidGateError,
    QUESTION_IDS,
    _LedgerGenerationProvider,
    _resume_ledger_state,
    _validate_reused_control_evidence,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BlockAPaidFull70ReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(".local") / "test-block-a-paid-full70-control-reuse"
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.questions = tuple(
            SimpleNamespace(question_id=qid, question=f"question {index}")
            for index, qid in enumerate(QUESTION_IDS, 1)
        )
        self.legacy = self.root / "legacy.json"
        self.dense = self.root / "dense.json"
        self.legacy.write_text(json.dumps({"arm_build_identity": "legacy"}), encoding="utf-8")
        self.dense.write_text(json.dumps({"arm_build_identity": "dense"}), encoding="utf-8")
        self._write_control_fixture()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_control_fixture(self) -> None:
        ledger_rows = []
        for question in self.questions:
            qid = question.question_id
            result_root = self.root / "results" / qid / "control" / qid
            packet_path = result_root / "packet" / "evidence_packet.json"
            generation_path = result_root / "generation" / "generation_result.json"
            packet_path.parent.mkdir(parents=True, exist_ok=True)
            generation_path.parent.mkdir(parents=True, exist_ok=True)
            packet_path.write_text("{}", encoding="utf-8")
            generation_path.write_text("{}", encoding="utf-8")
            result = {
                "status": "succeeded",
                "execution_mode": "generate_answer",
                "query": {"execution_identity": qid, "request_label": qid, "question_text": question.question},
                "retrieval_trace": {
                    "candidate_supply_depth": 20,
                    "rrf_k": 60,
                    "windows": {"hybrid": [{"unit_id": qid, "retrieval": {"arm_build_identities": {"lexical": "legacy", "dense": "dense"}}}]},
                },
                "citation_validation": {"citation_integrity": "pass", "citation_coverage": "pass", "semantic_faithfulness": "not_evaluated"},
                "audit": {"config": {"candidate_supply_depth": 20, "reranker_enabled": False}},
                "rerank_trace": {"status": "disabled_by_explicit_config"},
                "generation": {"result": {"audit": {"execution_config_identity": "generation-config"}}},
                "persistence": {
                    "evidence_packet": {"json": {"sha256": _sha(packet_path), "byte_count": packet_path.stat().st_size}},
                    "generation": {"sha256": _sha(generation_path), "byte_count": generation_path.stat().st_size},
                },
            }
            (result_root / "rag_result.json").write_text(json.dumps(result), encoding="utf-8")
            occurrence = f"{qid}-control"
            ledger_rows.extend([
                {"question_id": qid, "arm": "control", "stage": "generation", "occurrence_id": occurrence, "status": "issued"},
                {"question_id": qid, "arm": "control", "stage": "generation", "occurrence_id": occurrence, "status": "succeeded"},
            ])
        ledger = self.root / "metadata" / "provider_attempts.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("".join(json.dumps(row) + "\n" for row in ledger_rows), encoding="utf-8")
        manifest = {
            "run_identity": "fixture-control",
            "status": "partial_failed",
            "completed_control_questions": list(QUESTION_IDS),
            "generation_config_identity": "generation-config",
            "generation": {"model_id": paid.BASELINE_QWEN_MODEL_ID},
            "artifacts": {"provider_attempts": {"sha256": _sha(ledger), "byte_count": ledger.stat().st_size}},
        }
        (self.root / "metadata" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _validate_fixture(self) -> dict:
        manifest = self.root / "metadata" / "manifest.json"
        ledger = self.root / "metadata" / "provider_attempts.jsonl"
        with patch.object(paid, "EXPECTED_REUSABLE_CONTROL_RUN_ID", "fixture-control"), patch.object(
            paid, "EXPECTED_REUSABLE_CONTROL_MANIFEST_SHA256", _sha(manifest)
        ), patch.object(paid, "EXPECTED_REUSABLE_CONTROL_LEDGER_SHA256", _sha(ledger)):
            return _validate_reused_control_evidence(
                self.root,
                questions=self.questions,
                legacy_lexical_manifest_path=self.legacy,
                dense_manifest_path=self.dense,
                generation_config_identity="generation-config",
            )

    def test_complete_control_reuse_is_bound_by_all_70_results_and_ledger(self) -> None:
        result = self._validate_fixture()
        self.assertEqual(result["result_count"], 70)
        self.assertEqual(result["question_ids"], list(QUESTION_IDS))
        self.assertEqual(result["legacy_lexical_identity"], "legacy")
        self.assertEqual(result["dense_identity"], "dense")
        self.assertEqual(len(result["results"]), 70)

    def test_incomplete_control_manifest_fails_closed(self) -> None:
        manifest_path = self.root / "metadata" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["completed_control_questions"] = list(QUESTION_IDS[:-1])
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(BlockAPaidGateError, "identity/completeness"):
            self._validate_fixture()

    def test_c_only_generation_guard_rejects_call_71_before_delegate(self) -> None:
        class Delegate:
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, request):
                del request
                self.calls += 1
                return SimpleNamespace(execution_status="succeeded")

        delegate = Delegate()
        ledger = self.root / "c-only-ledger.jsonl"
        counter = {"embedding": 0, "reranker": 0, "generation": 0}
        provider = _LedgerGenerationProvider(delegate, ledger, "vnext", "Q001", counter, maximum_calls=70)
        for _ in range(70):
            provider.generate(None)
        with self.assertRaisesRegex(BlockAPaidGateError, "Generation budget"):
            provider.generate(None)
        self.assertEqual(delegate.calls, 70)


class BlockAPaidFull70ResumeLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(".local") / "test-block-a-paid-full70-resume"
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.ledger = self.root / "provider_attempts.jsonl"
        self.run_identity = "resume-fixture"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_ledger(self, *, complete_questions: int = 4, unresolved: bool = False) -> None:
        rows = [{"event": "run_started", "run_identity": self.run_identity, "embedding_calls": 0}]
        for qid in QUESTION_IDS[:complete_questions]:
            for stage in ("reranker", "generation"):
                occurrence = f"{qid}-{stage}"
                rows.append({"question_id": qid, "arm": "vnext", "stage": stage, "occurrence_id": occurrence, "status": "issued"})
                if not (unresolved and qid == QUESTION_IDS[0] and stage == "reranker"):
                    rows.append({"question_id": qid, "arm": "vnext", "stage": stage, "occurrence_id": occurrence, "status": "succeeded"})
        self.ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_partial_ledger_derives_cumulative_budget_and_completed_occurrences(self) -> None:
        self._write_ledger()
        counters, occurrences = _resume_ledger_state(self.ledger, self.run_identity)
        self.assertEqual(counters, {"embedding": 0, "reranker": 4, "generation": 4, "control_regeneration": 0})
        self.assertEqual(set(occurrences["Q001"]), {"reranker", "generation"})
        self.assertEqual(70 - counters["reranker"], 66)
        self.assertEqual(70 - counters["generation"], 66)

    def test_unresolved_occurrence_fails_closed(self) -> None:
        self._write_ledger(unresolved=True)
        with self.assertRaisesRegex(BlockAPaidGateError, "unresolved"):
            _resume_ledger_state(self.ledger, self.run_identity)

    def test_duplicate_or_cross_arm_occurrence_fails_closed(self) -> None:
        self._write_ledger(complete_questions=1)
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"question_id": "Q001", "arm": "control", "stage": "generation", "occurrence_id": "control", "status": "issued"}) + "\n")
        with self.assertRaisesRegex(BlockAPaidGateError, "invalid or non-C-only"):
            _resume_ledger_state(self.ledger, self.run_identity)

    def test_already_complete_resume_does_not_construct_a_provider(self) -> None:
        context = {
            "manifest": {"status": "complete"},
            "completed_question_ids": list(QUESTION_IDS),
            "questions": tuple(),
        }
        with patch.object(paid, "_reconcile_c_only_resume", return_value=context), patch.object(
            paid.BailianOpenAICompatibleTransport, "from_environment", side_effect=AssertionError("provider constructed")
        ), patch.object(paid.DashScopeQwenRerankTransport, "from_environment", side_effect=AssertionError("provider constructed")):
            result = paid.resume_block_a_paid_full70_control_reuse(output_root=self.root)
        self.assertEqual(result["status"], "complete")

    def test_replacement_root_is_rejected_before_live_validation(self) -> None:
        with self.assertRaisesRegex(BlockAPaidGateError, "replacement output root"):
            paid._reconcile_c_only_resume(
                output_root=self.root, expected_run_identity=paid.EXPECTED_C_ONLY_PARTIAL_RUN_ID,
                closure_root=paid.DEFAULT_CLOSURE_ROOT, runtime_input=paid.DEFAULT_RUNTIME_INPUT,
                ru_manifest_path=paid.DEFAULT_RU_MANIFEST,
                legacy_lexical_manifest_path=paid.DEFAULT_LEGACY_LEXICAL_MANIFEST,
                dense_manifest_path=paid.DEFAULT_DENSE_MANIFEST, environment={},
            )


if __name__ == "__main__":
    unittest.main()
