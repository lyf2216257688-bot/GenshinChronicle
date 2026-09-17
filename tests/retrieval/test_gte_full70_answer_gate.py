from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

import genshin_corpus.retrieval.gte_full70_answer_gate as gate


RUN_ID = "test-run-identity"


class _Delegate:
    def __init__(self, status="succeeded"):
        self.calls = 0
        self.status = status

    def generate(self, _request):
        self.calls += 1
        return type("Result", (), {"execution_status": self.status, "provider_audit": {"attempts": [{"attempt": 1}]}})()


class GteFull70GateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(".local/gte-full70-test-scratch") / uuid4().hex
        (self.root / "metadata").mkdir(parents=True)
        (self.root / "results").mkdir()
        self.ledger = self.root / "metadata/provider_attempts.jsonl"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _start(self, **changes):
        row = {
            "event": "run_started",
            "ledger_schema_version": gate.LEDGER_SCHEMA_VERSION,
            "recovery_contract_version": gate.RECOVERY_CONTRACT_VERSION,
            "run_identity": RUN_ID,
            "question_id": None,
            "stage": gate.GENERATION_STAGE,
            "arm": gate.GENERATION_ARM,
            "occurrence_id": None,
            "attempt_count": 1,
            "expected_question_ids": list(gate.QUESTION_IDS),
            "automatic_retry": 0,
        }
        row.update(changes)
        gate._append(self.ledger, row)

    def _occurrence(self, qid="Q001", occurrence="q001", status="succeeded", **changes):
        issued = gate._ledger_event(run_identity=RUN_ID, question_id=qid, occurrence_id=occurrence, status="issued")
        terminal = gate._ledger_event(run_identity=RUN_ID, question_id=qid, occurrence_id=occurrence, status=status, execution_status=status, observed_provider_attempt_count=1)
        issued.update(changes.pop("issued", {}))
        terminal.update(changes.pop("terminal", {}))
        gate._append(self.ledger, issued)
        gate._append(self.ledger, terminal)

    def _manifest(self, completed, *, terminal=False, failed=()):
        return {
            "schema_version": gate.SCHEMA_VERSION,
            "recovery_contract_version": gate.RECOVERY_CONTRACT_VERSION,
            "runner_sha256": gate._sha256(Path(gate.__file__)),
            "model": {"revision": gate.MODEL_REVISION},
            "run_identity": RUN_ID,
            "question_ids": list(gate.QUESTION_IDS),
            "configuration": {
                "fusion": gate.FUSION.to_dict(), "candidate_supply_depth": 500, "rerank_depth": 500,
                "final_top_n": 20, "reranker_projection_max_chars": 6000,
                "assembly": "accepted current-C assembly_config", "generation": {"max_attempts": 1},
            },
            "completed_questions": list(completed),
            "actual_provider_calls": {"embedding": 0, "reranker": len(completed), "generation": len(completed)},
            "network_provider_occurrences": len(completed),
            "execution_complete": terminal,
            "failed_question_ids": list(failed),
            "status": "partial_failed" if terminal and failed else "complete" if terminal else "in_progress",
            "terminal_status": "partial" if terminal and failed else "complete" if terminal else "in_progress",
        }

    def _result(self, qid, status="succeeded", occurrence=None):
        result_dir = self.root / "results" / qid / qid
        result_dir.mkdir(parents=True)
        result = {"status": status, "persistence": {"generation": {"request": qid}}}
        (result_dir / "rag_result.json").write_text(json.dumps(result), encoding="utf-8")
        evidence = {"question_id": qid, "status": status, "generation": result["persistence"]["generation"], "generation_occurrence": occurrence}
        (result_dir.parent / "answer_evidence.json").write_text(json.dumps(evidence), encoding="utf-8")

    def _state(self):
        return gate._ledger_state(self.ledger, run_identity=RUN_ID)

    def test_persisted_retriever_returns_exact_deep_copy_without_upstream(self):
        windows = {"Q001": {"question": "q", "windows": {"hybrid": [{"unit_id": "u", "rank": 1}], "lexical": [], "dense": []}}}
        retriever = gate._PersistedRetriever(windows)
        result = retriever.candidates_for_query("q", object(), top_k=500)
        result["hybrid"][0]["rank"] = 9
        self.assertEqual(windows["Q001"]["windows"]["hybrid"][0]["rank"], 1)
        self.assertEqual(retriever.calls, 1)

    def test_ledger_accepts_single_terminal_occurrence(self):
        self._start()
        self._occurrence()
        self.assertEqual(self._state()["completed_questions"], ("Q001",))

    def test_ledger_rejects_run_identity_stage_arm_and_unknown_question(self):
        for field, value, expected in (("run_identity", "other", "identity"), ("stage", "wrong", "identity"), ("arm", "wrong", "identity"), ("question_id", "Q999", "unknown")):
            with self.subTest(field=field):
                self.tearDown(); self.setUp(); self._start()
                self._occurrence(issued={field: value})
                with self.assertRaisesRegex(gate.GteFull70GateError, expected):
                    self._state()

    def test_ledger_rejects_duplicate_occurrence_and_question(self):
        self._start(); self._occurrence()
        self._occurrence(occurrence="q002")
        with self.assertRaisesRegex(gate.GteFull70GateError, "duplicate Generation occurrence for question"):
            self._state()

    def test_ledger_rejects_duplicate_occurrence_id(self):
        self._start(); self._occurrence()
        self._occurrence()
        with self.assertRaisesRegex(gate.GteFull70GateError, "duplicate or missing issued"):
            self._state()

    def test_unambiguous_partial_ledger_has_deterministic_next_question(self):
        self._start(); self._occurrence("Q001", "one"); self._occurrence("Q002", "two")
        self.assertEqual(self._state()["completed_questions"], ("Q001", "Q002"))
        self.assertEqual(next(qid for qid in gate.QUESTION_IDS if qid not in self._state()["completed_questions"]), "Q003")

    def test_ledger_rejects_duplicate_issued_terminal_and_unresolved(self):
        for kind, expected in (("issued", "duplicate or missing issued"), ("terminal", "duplicate or missing terminal"), ("unresolved", "duplicate or missing terminal")):
            with self.subTest(kind=kind):
                self.tearDown(); self.setUp(); self._start()
                self._occurrence()
                rows = self.ledger.read_text(encoding="utf-8").splitlines()
                if kind == "issued":
                    gate._append(self.ledger, json.loads(rows[1]))
                elif kind == "terminal":
                    gate._append(self.ledger, json.loads(rows[2]))
                else:
                    self.ledger.unlink(); self._start(); gate._append(self.ledger, json.loads(rows[1]))
                with self.assertRaisesRegex(gate.GteFull70GateError, expected):
                    self._state()

    def test_reconcile_rejects_result_and_manifest_disagreement(self):
        self._start(); self._occurrence()
        state = self._state()
        manifest = self._manifest(("Q001",))
        with self.assertRaisesRegex(gate.GteFull70GateError, "directory"):
            gate._reconcile_recovery(self.root, manifest, state)
        self._result("Q001", occurrence=state["terminal_by_question"]["Q001"])
        manifest["completed_questions"] = []
        with self.assertRaisesRegex(gate.GteFull70GateError, "completed_questions"):
            gate._reconcile_recovery(self.root, manifest, state)

    def test_reconcile_rejects_missing_result_for_ledger_completed_question(self):
        self._start(); self._occurrence()
        with self.assertRaisesRegex(gate.GteFull70GateError, "directory"):
            gate._reconcile_recovery(self.root, self._manifest(("Q001",)), self._state())

    def test_failed_and_successful_question_cannot_be_reissued(self):
        for status in ("succeeded", "provider_error"):
            with self.subTest(status=status):
                delegate = _Delegate(status)
                provider = gate._LedgerGenerationProvider(delegate, self.ledger, "Q001", {"generation": 0}, run_identity=RUN_ID)
                provider.generate(object())
                with self.assertRaisesRegex(gate.GteFull70GateError, "duplicate"):
                    provider.generate(object())
                self.assertEqual(delegate.calls, 1)

    def test_generation_budget_attempt_limit_and_durability_before_delegate(self):
        self._start()
        delegate = _Delegate()
        provider = gate._LedgerGenerationProvider(delegate, self.ledger, "Q070", {"generation": 69}, run_identity=RUN_ID)
        with patch.object(gate.os, "fsync", wraps=gate.os.fsync) as fsync:
            provider.generate(object())
        self.assertTrue(fsync.called)
        self.assertEqual(delegate.calls, 1)
        self.assertEqual(provider.terminal_event["attempt_count"], 1)
        with self.assertRaisesRegex(gate.GteFull70GateError, "budget"):
            gate._LedgerGenerationProvider(_Delegate(), self.ledger, "Q069", {"generation": 70}, run_identity=RUN_ID).generate(object())

    def test_terminal_semantics_complete_and_partial_failed(self):
        terminals = {qid: {"status": "succeeded"} for qid in gate.QUESTION_IDS}
        self.assertEqual(gate._terminal_outcome(tuple(gate.QUESTION_IDS), terminals), ("complete", "complete", [], True))
        terminals["Q060"] = {"status": "failed"}
        self.assertEqual(gate._terminal_outcome(tuple(gate.QUESTION_IDS), terminals), ("partial_failed", "partial", ["Q060"], True))
        self.assertEqual(gate._terminal_outcome(("Q001",), {"Q001": {"status": "succeeded"}}), ("in_progress", "in_progress", [], False))

    def test_manifest_validation_fails_before_source_or_provider(self):
        manifest = self._manifest(())
        manifest["recovery_contract_version"] = "wrong"
        with self.assertRaisesRegex(gate.GteFull70GateError, "identity mismatch"):
            gate._validate_recovery_manifest(self.root, manifest)

    def test_manifest_run_identity_must_be_present(self):
        manifest = self._manifest(())
        manifest["model"] = gate._expected_model_identity()
        manifest["run_identity"] = ""
        with self.assertRaisesRegex(gate.GteFull70GateError, "run identity mismatch"):
            gate._validate_recovery_manifest(self.root, manifest)

    def test_source_is_offline_local_only_and_has_no_online_fallback_or_default_change(self):
        source = Path(gate.__file__).read_text(encoding="utf-8")
        adapter = Path("src/genshin_corpus/retrieval/gte_multilingual_reranker.py").read_text(encoding="utf-8")
        self.assertIn('os.environ["HF_HUB_OFFLINE"] = "1"', source)
        self.assertIn('os.environ["TRANSFORMERS_OFFLINE"] = "1"', source)
        self.assertIn("local_files_only=True", adapter)
        self.assertNotIn("DashScopeQwenRerank", source)
        self.assertIn("candidate_supply_depth=500", source)


if __name__ == "__main__":
    unittest.main()
