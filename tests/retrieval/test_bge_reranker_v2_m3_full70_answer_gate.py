from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

import genshin_corpus.retrieval.bge_reranker_v2_m3_full70_answer_gate as gate


RUN_ID = "test-bge-run-identity"
MODEL_ROOT = Path(".local/bge-full70-test-model") / "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


class _Delegate:
    def __init__(self, status="succeeded", ledger: Path | None = None):
        self.calls = 0
        self.status = status
        self.ledger = ledger
        self.issued_was_durable = False

    def generate(self, _request):
        self.calls += 1
        if self.ledger is not None:
            rows = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines()]
            self.issued_was_durable = any(row.get("status") == "issued" for row in rows)
        return type("Result", (), {"execution_status": self.status, "provider_audit": {"attempts": [{"attempt": 1}]}})()


class _Reranker:
    def __init__(self, *_args, **_kwargs):
        self.last_request_metadata = {"candidate_count": 500, "max_length": 2048}

    def _lazy_load(self):
        return None


class _State:
    def close(self):
        return None


class BgeFull70GateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(".local/bge-full70-test-scratch") / uuid4().hex
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
        terminal = gate._ledger_event(
            run_identity=RUN_ID,
            question_id=qid,
            occurrence_id=occurrence,
            status=status,
            execution_status=status,
            observed_provider_attempt_count=1,
        )
        issued.update(changes.pop("issued", {}))
        terminal.update(changes.pop("terminal", {}))
        gate._append(self.ledger, issued)
        gate._append(self.ledger, terminal)

    def _manifest(self, completed, *, terminal=False, failed=()):
        return {
            "schema_version": gate.SCHEMA_VERSION,
            "recovery_contract_version": gate.RECOVERY_CONTRACT_VERSION,
            "runner_sha256": gate._sha256(Path(gate.__file__)),
            "model": gate._expected_model_identity(MODEL_ROOT),
            "run_identity": RUN_ID,
            "question_ids": list(gate.QUESTION_IDS),
            "configuration": {
                "fusion": gate.FUSION.to_dict(),
                "candidate_supply_depth": 500,
                "rerank_depth": 500,
                "final_top_n": 20,
                "reranker_projection_max_chars": 6000,
                "assembly": "accepted current-C assembly_config",
                "generation": {"max_attempts": 1},
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
        evidence = {
            "question_id": qid,
            "status": status,
            "generation": result["persistence"]["generation"],
            "generation_occurrence": occurrence,
        }
        (result_dir.parent / "answer_evidence.json").write_text(json.dumps(evidence), encoding="utf-8")

    def _state(self):
        return gate._ledger_state(self.ledger, run_identity=RUN_ID)

    def test_bge_model_revision_root_sha_and_runtime_are_exactly_bound(self):
        identity = gate._expected_model_identity(MODEL_ROOT)
        self.assertEqual(identity["model"], "BAAI/bge-reranker-v2-m3")
        self.assertEqual(identity["revision"], "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
        self.assertEqual(identity["model_weight_sha256"], "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286")
        self.assertEqual(identity["max_length"], 2048)
        self.assertEqual(identity["dtype"], "float16")
        self.assertEqual(identity["device"], "cuda")
        self.assertEqual(identity["batch_size"], 1)
        self.assertEqual(identity["model_path"], str(MODEL_ROOT.resolve()))

    def test_local_model_verification_rejects_root_or_weight_disagreement(self):
        expected = gate._expected_model_identity(MODEL_ROOT)
        good = {
            "model": expected["model"],
            "revision": expected["revision"],
            "model_path": expected["model_path"],
            "model_weight": {"sha256": expected["model_weight_sha256"]},
        }
        with patch.object(gate, "verify_pinned_local_model", return_value=good):
            self.assertEqual(gate._verify_bge_model_identity(MODEL_ROOT), good)
        bad = {**good, "model_weight": {"sha256": "0" * 64}}
        with patch.object(gate, "verify_pinned_local_model", return_value=bad):
            with self.assertRaisesRegex(gate.BgeFull70GateError, "identity mismatch"):
                gate._verify_bge_model_identity(MODEL_ROOT)

    def test_persisted_hybrid500_retriever_returns_deep_copy_without_upstream_retrieval(self):
        windows = {"Q001": {"question": "q", "windows": {"hybrid": [{"unit_id": "u", "rank": 1}], "lexical": [], "dense": []}}}
        retriever = gate._PersistedRetriever(windows)
        result = retriever.candidates_for_query("q", object(), top_k=500)
        result["hybrid"][0]["rank"] = 9
        self.assertEqual(windows["Q001"]["windows"]["hybrid"][0]["rank"], 1)
        self.assertEqual(retriever.calls, 1)

    def test_execute_wires_bge_rerank500_same_fusion_final20_and_formal_assembly(self):
        question = type("Question", (), {"question_id": "Q001", "question": "question"})()
        vector = object()
        source = {"audit": {"config": {"assembly_config": {}}}}
        windows = {"Q001": {"source_result": source}}
        manifest = self._manifest(())
        observed = {}

        def run_single(state, text, **kwargs):
            observed.update(kwargs)
            qid = kwargs["execution_identity"]
            result_dir = self.root / "results" / qid / qid
            result_dir.mkdir(parents=True)
            result = {
                "query": {"question_text": text},
                "status": "succeeded",
                "persistence": {"evidence_packet": {"packet": qid}, "generation": {"generation": qid}},
                "citation_validation": {"integrity": "pass", "coverage": "pass"},
            }
            (result_dir / "rag_result.json").write_text(json.dumps(result), encoding="utf-8")
            kwargs["generation_provider"].generate(object())
            return result

        self._start()
        provider = _Delegate()
        with (
            patch.object(gate, "BgeRerankerV2M3", _Reranker),
            patch.object(gate, "BailianGenerationProvider", return_value=provider),
            patch.object(gate.BailianOpenAICompatibleTransport, "from_environment", return_value=object()),
            patch.object(gate, "_build_state", return_value=_State()),
            patch.object(gate, "run_single_question", side_effect=run_single),
        ):
            output = gate._execute_from_manifest(
                self.root,
                manifest,
                windows,
                [question],
                [vector],
                object(),
                self.ledger,
                {},
                model_path=MODEL_ROOT,
            )
        config = observed["config"]
        self.assertTrue(config.reranker_enabled)
        self.assertEqual((config.candidate_supply_depth, config.rerank_depth, config.rerank_output_k, config.final_top_n), (500, 500, 20, 20))
        self.assertEqual(config.fusion_config, gate.FUSION)
        self.assertEqual(config.rrf_k, 60)
        self.assertEqual(config.reranker_projection_max_chars, 6000)
        self.assertIsInstance(config.assembly_config, gate.EvidenceAssemblyConfig)
        self.assertEqual(observed["precomputed_query"], vector)
        self.assertEqual(output["actual_provider_calls"], {"embedding": 0, "reranker": 1, "generation": 1})

    def test_ledger_accepts_single_terminal_occurrence(self):
        self._start()
        self._occurrence()
        self.assertEqual(self._state()["completed_questions"], ("Q001",))

    def test_ledger_rejects_run_identity_stage_arm_and_unknown_question(self):
        for field, value, expected in (("run_identity", "other", "identity"), ("stage", "wrong", "identity"), ("arm", "wrong", "identity"), ("question_id", "Q999", "unknown")):
            with self.subTest(field=field):
                self.tearDown()
                self.setUp()
                self._start()
                self._occurrence(issued={field: value})
                with self.assertRaisesRegex(gate.BgeFull70GateError, expected):
                    self._state()

    def test_ledger_rejects_duplicate_occurrence_question_and_unresolved_issue(self):
        self._start()
        self._occurrence()
        self._occurrence(occurrence="q002")
        with self.assertRaisesRegex(gate.BgeFull70GateError, "duplicate Generation occurrence for question"):
            self._state()
        self.tearDown()
        self.setUp()
        self._start()
        issued = gate._ledger_event(run_identity=RUN_ID, question_id="Q001", occurrence_id="open", status="issued")
        gate._append(self.ledger, issued)
        with self.assertRaisesRegex(gate.BgeFull70GateError, "duplicate or missing terminal"):
            self._state()

    def test_ledger_rejects_duplicate_issued_terminal_and_occurrence_id(self):
        for kind, expected in (("issued", "duplicate or missing issued"), ("terminal", "duplicate or missing terminal"), ("occurrence", "duplicate or missing issued")):
            with self.subTest(kind=kind):
                self.tearDown()
                self.setUp()
                self._start()
                self._occurrence()
                rows = self.ledger.read_text(encoding="utf-8").splitlines()
                if kind == "issued":
                    gate._append(self.ledger, json.loads(rows[1]))
                elif kind == "terminal":
                    gate._append(self.ledger, json.loads(rows[2]))
                else:
                    self._occurrence()
                with self.assertRaisesRegex(gate.BgeFull70GateError, expected):
                    self._state()

    def test_manifest_ledger_and_result_disagreement_fail_closed(self):
        self._start()
        self._occurrence()
        state = self._state()
        manifest = self._manifest(("Q001",))
        with self.assertRaisesRegex(gate.BgeFull70GateError, "directory"):
            gate._reconcile_recovery(self.root, manifest, state)
        self._result("Q001", occurrence=state["terminal_by_question"]["Q001"])
        manifest["completed_questions"] = []
        with self.assertRaisesRegex(gate.BgeFull70GateError, "completed_questions"):
            gate._reconcile_recovery(self.root, manifest, state)

    def test_terminal_success_or_failure_cannot_be_reissued(self):
        for status in ("succeeded", "provider_error"):
            with self.subTest(status=status):
                delegate = _Delegate(status)
                provider = gate._LedgerGenerationProvider(delegate, self.ledger, "Q001", {"generation": 0}, run_identity=RUN_ID)
                provider.generate(object())
                with self.assertRaisesRegex(gate.BgeFull70GateError, "duplicate"):
                    provider.generate(object())
                self.assertEqual(delegate.calls, 1)

    def test_issued_row_is_fsynced_before_delegate_and_max_attempts_is_one(self):
        self._start()
        delegate = _Delegate(ledger=self.ledger)
        provider = gate._LedgerGenerationProvider(delegate, self.ledger, "Q070", {"generation": 69}, run_identity=RUN_ID)
        with patch.object(gate.os, "fsync", wraps=gate.os.fsync) as fsync:
            provider.generate(object())
        self.assertTrue(fsync.called)
        self.assertTrue(delegate.issued_was_durable)
        self.assertEqual(provider.terminal_event["attempt_count"], 1)
        self.assertEqual(provider.terminal_event["observed_provider_attempt_count"], 1)
        with self.assertRaisesRegex(gate.BgeFull70GateError, "budget"):
            gate._LedgerGenerationProvider(_Delegate(), self.ledger, "Q069", {"generation": 70}, run_identity=RUN_ID).generate(object())

    def test_terminal_complete_and_partial_failed_semantics(self):
        terminals = {qid: {"status": "succeeded"} for qid in gate.QUESTION_IDS}
        self.assertEqual(gate._terminal_outcome(tuple(gate.QUESTION_IDS), terminals), ("complete", "complete", [], True))
        terminals["Q060"] = {"status": "failed"}
        self.assertEqual(gate._terminal_outcome(tuple(gate.QUESTION_IDS), terminals), ("partial_failed", "partial", ["Q060"], True))
        self.assertEqual(gate._terminal_outcome(("Q001",), {"Q001": {"status": "succeeded"}}), ("in_progress", "in_progress", [], False))

    def test_unambiguous_partial_ledger_has_deterministic_safe_next_question(self):
        self._start()
        self._occurrence("Q001", "one")
        self._occurrence("Q002", "two")
        state = self._state()
        self.assertEqual(state["completed_questions"], ("Q001", "Q002"))
        self.assertEqual(next(qid for qid in gate.QUESTION_IDS if qid not in state["completed_questions"]), "Q003")

    def test_manifest_identity_mismatch_fails_before_source_or_delegate(self):
        manifest = self._manifest(())
        manifest["recovery_contract_version"] = "wrong"
        with self.assertRaisesRegex(gate.BgeFull70GateError, "identity mismatch"):
            gate._validate_recovery_manifest(self.root, manifest)
        manifest = self._manifest(())
        manifest["run_identity"] = ""
        with self.assertRaisesRegex(gate.BgeFull70GateError, "run identity mismatch"):
            gate._validate_recovery_manifest(self.root, manifest)

    def test_source_has_local_bge_only_and_no_upstream_or_online_reranker_fallback(self):
        source = Path(gate.__file__).read_text(encoding="utf-8")
        self.assertIn("BgeRerankerV2M3", source)
        self.assertIn("BgeRerankerV2M3Config(Path(model_path), max_length=MAX_LENGTH, batch_size=1)", source)
        self.assertIn('os.environ["HF_HUB_OFFLINE"] = "1"', source)
        self.assertIn('os.environ["TRANSFORMERS_OFFLINE"] = "1"', source)
        self.assertNotIn("GteMultilingualReranker", source)
        self.assertNotIn("DashScopeQwenRerank", source)
        self.assertNotIn("encode_qwen_query", source)
        self.assertIn("candidate_supply_depth=500", source)
        self.assertIn("rerank_depth=500", source)
        self.assertIn("rerank_output_k=20", source)


if __name__ == "__main__":
    unittest.main()
