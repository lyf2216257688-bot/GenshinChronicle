from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from genshin_corpus.retrieval.block_a_recovery import (
    PARENT_RUN_ID,
    BlockARecoveryError,
    _ContinuationLedgerGenerationProvider,
    _ContinuationLedgerReranker,
    _continuation_preflight,
    _verify_parent_ledger,
    run_block_a_continuation,
)
from genshin_corpus.rag.backend import SingleQuestionBackendConfig
from genshin_corpus.retrieval.block_a_closure import BlockAQualityGateConfig


class BlockARecoveryTests(unittest.TestCase):
    def test_continuation_reuses_q007_and_dispatches_only_q008_suffix(self) -> None:
        preflight = _continuation_preflight()
        self.assertEqual(preflight["parent_run_identity"], PARENT_RUN_ID)
        self.assertEqual(preflight["reused_parent_control_questions"][0], "Q001")
        self.assertEqual(preflight["reused_parent_control_questions"][-1], "Q070")
        self.assertEqual(preflight["reused_parent_vnext_questions"][-1], "Q007")
        self.assertEqual(preflight["remaining_paid_question_ids"][0], "Q008")
        self.assertEqual(preflight["remaining_paid_question_ids"][-1], "Q070")
        self.assertEqual(len(preflight["remaining_paid_question_ids"]), 63)
        self.assertEqual(preflight["provider_call_budget"], {"embedding": 0, "reranker": 63, "generation": 63})
        self.assertTrue(preflight["parent_root_immutable"])
        self.assertTrue(preflight["new_output_root_required"])

    def test_real_continuation_ledger_wrappers_reject_64th_call_before_delegate(self) -> None:
        root = Path(".local") / "test-block-a-continuation-budget-wrappers"
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)

        class FakeReranker:
            def __init__(self) -> None:
                self.calls = 0

            def rerank(self, request):
                del request
                self.calls += 1
                return []

        class FakeGeneration:
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, request):
                del request
                self.calls += 1
                return SimpleNamespace(execution_status="succeeded", provider_audit={"attempts": []})

        try:
            root.mkdir(parents=True)
            ledger = root / "provider_attempts.jsonl"
            reranker_delegate = FakeReranker()
            generation_delegate = FakeGeneration()
            counters = {"embedding": 0, "reranker": 0, "generation": 0}
            reranker = _ContinuationLedgerReranker(reranker_delegate, ledger, "Q008", counters)
            generation = _ContinuationLedgerGenerationProvider(generation_delegate, ledger, "Q008", counters)
            for _ in range(63):
                reranker.rerank(None)
                generation.generate(None)
            self.assertEqual(reranker_delegate.calls, 63)
            self.assertEqual(generation_delegate.calls, 63)
            with self.assertRaisesRegex(BlockARecoveryError, "reranker budget"):
                reranker.rerank(None)
            with self.assertRaisesRegex(BlockARecoveryError, "Generation budget"):
                generation.generate(None)
            self.assertEqual(reranker_delegate.calls, 63)
            self.assertEqual(generation_delegate.calls, 63)
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 252)
            self.assertEqual(sum(row["status"] == "issued" for row in rows if row.get("stage") == "reranker"), 63)
            self.assertEqual(sum(row["status"] == "succeeded" for row in rows if row.get("stage") == "reranker"), 63)
            self.assertEqual(sum(row["status"] == "issued" for row in rows if row.get("stage") == "generation"), 63)
            self.assertEqual(sum(row["status"] == "succeeded" for row in rows if row.get("stage") == "generation"), 63)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _fake_context(self) -> dict:
        questions = tuple(SimpleNamespace(question_id=f"Q{index:03d}", question=f"问题 {index}") for index in range(1, 71))
        return {
            "questions": questions,
            "bindings": questions,
            "config": BlockAQualityGateConfig(),
            "field_manifest": Path("field.json"),
            "source_hashes": {"parent": {}, "current": {}},
            "parent_manifest": {},
        }

    def test_fake_complete_continuation_dispatches_only_suffix_and_accounts_63_each(self) -> None:
        root = Path(".local") / "test-block-a-continuation-complete"
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        dispatched: list[str] = []

        def execute(question, binding, record):
            del binding
            dispatched.append(question.question_id)
            record("reranker", lambda: {"ok": True})
            record("generation", lambda: {"ok": True})
            return {"status": "succeeded"}

        try:
            with patch("genshin_corpus.retrieval.block_a_recovery._validate_continuation_inputs", return_value=self._fake_context()):
                manifest = run_block_a_continuation(
                    parent_root=Path("parent"), recovery_root=Path("recovery"), output_root=root,
                    question_executor=execute,
                )
            self.assertEqual(dispatched, [f"Q{index:03d}" for index in range(8, 71)])
            self.assertEqual(len(dispatched), 63)
            self.assertEqual(manifest["actual_provider_calls"], {"embedding": 0, "reranker": 63, "generation": 63})
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["reused_parent_control_questions"], [f"Q{index:03d}" for index in range(1, 71)])
            self.assertEqual(manifest["reused_parent_vnext_questions"], [f"Q{index:03d}" for index in range(1, 8)])
            self.assertTrue(SingleQuestionBackendConfig().reranker_enabled)
            q056 = json.loads((root / "review" / "continuation_q008-q070.jsonl").read_text(encoding="utf-8").splitlines()[48])
            self.assertTrue(q056["excluded_from_clean_advancement_gate"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_fake_interruption_stops_suffix_and_preserves_partial_state(self) -> None:
        root = Path(".local") / "test-block-a-continuation-partial"
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        dispatched: list[str] = []

        def execute(question, binding, record):
            del binding
            dispatched.append(question.question_id)
            if question.question_id == "Q020":
                record("reranker", lambda: (_ for _ in ()).throw(TimeoutError("fake timeout")))
            record("reranker", lambda: {"ok": True})
            record("generation", lambda: {"ok": True})
            return {"status": "succeeded"}

        try:
            with patch("genshin_corpus.retrieval.block_a_recovery._validate_continuation_inputs", return_value=self._fake_context()):
                manifest = run_block_a_continuation(
                    parent_root=Path("parent"), recovery_root=Path("recovery"), output_root=root,
                    question_executor=execute,
                )
            self.assertEqual(manifest["status"], "partial_failed")
            self.assertEqual(dispatched[-1], "Q020")
            self.assertNotIn("Q021", dispatched)
            self.assertEqual(manifest["actual_provider_calls"], {"embedding": 0, "reranker": 13, "generation": 12})
            partial = json.loads((root / "errors" / "partial-state.json").read_text(encoding="utf-8"))
            self.assertEqual(partial["failed_question_id"], "Q020")
            self.assertIn("do not retry", partial["safe_recovery_position"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_existing_root_and_identity_failure_happen_before_dispatch(self) -> None:
        root = Path(".local") / "test-block-a-continuation-existing"
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        try:
            with self.assertRaises(FileExistsError):
                run_block_a_continuation(parent_root=Path("parent"), recovery_root=Path("recovery"), output_root=root, question_executor=lambda *_: {"status": "succeeded"})
        finally:
            shutil.rmtree(root, ignore_errors=True)
        blocked = Path(".local") / "test-block-a-continuation-blocked"
        if blocked.exists():
            shutil.rmtree(blocked, ignore_errors=True)
        dispatched: list[str] = []
        try:
            with patch("genshin_corpus.retrieval.block_a_recovery._validate_continuation_inputs", side_effect=BlockARecoveryError("identity mismatch")):
                with self.assertRaisesRegex(BlockARecoveryError, "identity mismatch"):
                    run_block_a_continuation(parent_root=Path("parent"), recovery_root=Path("recovery"), output_root=blocked, question_executor=lambda *args: dispatched.append("called") or {"status": "succeeded"})
            self.assertEqual(dispatched, [])
            self.assertFalse(blocked.exists())
        finally:
            shutil.rmtree(blocked, ignore_errors=True)
        try:
            ledger = root / "metadata" / "provider_attempts.jsonl"
            ledger.parent.mkdir(parents=True)
            rows = [
                {"event": "run_started", "run_identity": PARENT_RUN_ID},
                {"occurrence_id": "x", "stage": "generation", "status": "issued"},
                {"occurrence_id": "x", "stage": "generation", "status": "succeeded"},
            ]
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            manifest = {
                "artifacts": {"provider_attempts": {"sha256": __import__("hashlib").sha256(ledger.read_bytes()).hexdigest(), "byte_count": ledger.stat().st_size}},
                "actual_provider_calls": {"reranker": 0, "generation": 1},
            }
            result = _verify_parent_ledger(root, manifest)
            self.assertEqual(result["unresolved_occurrences"], [])
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows[:2]), encoding="utf-8")
            manifest["artifacts"]["provider_attempts"] = {
                "sha256": __import__("hashlib").sha256(ledger.read_bytes()).hexdigest(),
                "byte_count": ledger.stat().st_size,
            }
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                _verify_parent_ledger(root, manifest)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
