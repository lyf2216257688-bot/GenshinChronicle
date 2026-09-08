import hashlib
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.generation.measure import AcceptedQuestion, M1Baseline
from genshin_corpus.retrieval import broader_admission_supply as supply


class _FakeRetriever:
    lexical_manifest = {"retrieval_unit_build_identity": "ru-accepted"}
    dense_manifest = {"instruction": "accepted instruction"}

    def __init__(self, fail_question=None):
        self.fail_question = fail_question
        self.calls = []

    def candidates_for_query(self, question, vector, *, instruction, top_k, k1, b, rrf_k):
        self.calls.append((question, vector, instruction, top_k, k1, b, rrf_k))
        if question == self.fail_question:
            raise RuntimeError("interrupted fixture retrieval")
        index = int(question.rsplit(" ", 1)[1])
        return {
            mode: [
                {"unit_id": f"{mode}-{index}-{rank}", "rank": rank, "retrieval": {"mode": mode, "score": float(100 - rank)}}
                for rank in range(1, 21)
            ]
            for mode in supply.MODES
        }


class BroaderAdmissionCandidateSupplyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("data/retrieval/.broader-admission-supply-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.runtime = self.root / "questions.runtime.jsonl"
        self.questions = [AcceptedQuestion(f"Q{index:03d}", f"Question {index}") for index in range(1, 71)]
        runtime_body = b"".join(canonical_json_bytes(question.to_dict()) + b"\n" for question in self.questions)
        self.runtime.write_bytes(runtime_body)
        self.source = self.root / "source-manifest.json"
        self._write(self.source, {"schema_version": "p04-rag-m2-0.1", "status": "prepared", "question_count": 70, "runtime_input": {"sha256": hashlib.sha256(runtime_body).hexdigest()}})
        self.baseline_metadata = {
            "retrieval_unit_build_identity": "ru-accepted",
            "lexical_build_identity": "lex-accepted",
            "dense_build_identity": "dense-accepted",
            "dense_model_revision": "7999e1d3359715c523056ef9478215996d62a620",
            "dense_model_sha256": "a" * 64,
        }
        self.m2 = self.root / "m2-manifest.json"
        self._write(self.m2, {
            "schema_version": "p04-rag-m2-0.1", "status": "complete", "runtime_question_count": 70,
            "baseline": self.baseline_metadata,
            "questions": [{"question_id": q.question_id, "question_identity": q.question_identity} for q in self.questions],
        })
        self.hashes = self.root / "historical-hashes.json"
        self.full_rows = self.root / "full-rows.json"
        self._write_history()
        self.accepted_runtime_sha = hashlib.sha256(runtime_body).hexdigest()
        self.accepted_hashes_sha = hashlib.sha256(self.hashes.read_bytes()).hexdigest()
        self.accepted_full_rows_sha = hashlib.sha256(self.full_rows.read_bytes()).hexdigest()
        self.baseline = M1Baseline(root=self.root / "baseline", model_dir=self.root / "model", runtime_root=self.root / "runtime")
        self.baseline.runtime_root.mkdir()

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root)

    def _write(self, path, value):
        path.write_bytes(canonical_json_bytes(value))

    @staticmethod
    def _windows(index):
        return {
            mode: [
                {"unit_id": f"{mode}-{index}-{rank}", "rank": rank, "retrieval": {"mode": mode, "score": float(100 - rank)}}
                for rank in range(1, 21)
            ]
            for mode in supply.MODES
        }

    @staticmethod
    def _full(row, index, units):
        return {"unit_id": row["unit_id"], "rank": row["rank"], "input_index": index, "retrieval": row["retrieval"]}

    def _write_history(self):
        hash_rows = []
        for index in range(1, 71):
            for mode, rows in self._windows(index).items():
                hash_rows.append({"question_id": f"Q{index:03d}", "mode": mode, "candidate_input": {"candidate_count": 20, "candidate_sha256": supply._candidate_sha(rows)}})
        self._write(self.hashes, {"schema_version": "p04-rag-a1-2-70q-measure-0.1", "status": "pass", "rows": hash_rows})
        questions = []
        for index in range(1, 23):
            hybrid = self._windows(index)["hybrid"]
            questions.append({"question_id": f"Q{index:03d}", "modes": {"hybrid": {"candidate_count": 20, "candidates_top20": [self._full(row, n, {}) for n, row in enumerate(hybrid)]}}})
        self._write(self.full_rows, {"schema_version": "p04-rag-a1-2-unresolved-attribution-0.1", "questions": questions})

    def _run(self, output, retriever, *, resume=False):
        with patch.object(supply, "ACCEPTED_RUNTIME_INPUT_SHA256", self.accepted_runtime_sha), patch.object(supply, "ACCEPTED_CANDIDATE_HASHES_SHA256", self.accepted_hashes_sha), patch.object(supply, "ACCEPTED_FULL_ROWS_SHA256", self.accepted_full_rows_sha), patch.object(supply, "ACCEPTED_BASELINE", self.baseline_metadata), patch.object(supply, "_baseline_metadata", return_value=self.baseline_metadata), patch.object(supply, "load_dense_query_model", return_value=object()), patch.object(supply, "encode_dense_query", return_value=[1.0]), patch.object(supply, "load_batch_candidate_retriever", return_value=retriever), patch.object(supply, "load_retrieval_units", return_value=({"build_identity": "ru-accepted"}, [])), patch.object(supply, "_full_candidate_record", side_effect=self._full):
            return supply.materialize_candidate_supply(
                output,
                runtime_input=self.runtime,
                baseline=self.baseline,
                source_manifest_path=self.source,
                m2_run_manifest_path=self.m2,
                historical_hashes_path=self.hashes,
                full_rows_path=self.full_rows,
                resume=resume,
            )

    def test_materializes_exact_210_gate_and_refuses_accepted_overwrite(self):
        output = self.root / "supply"
        retriever = _FakeRetriever()
        result = self._run(output, retriever)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["reproduction"], {"lexical": 70, "dense": 70, "hybrid": 70, "total": 210, "full_hybrid_rows": 22})
        self.assertEqual(len(list((output / "candidates").glob("Q*.json"))), 70)
        self.assertTrue(all(call[-4:] == (20, 1.2, 0.75, 60) for call in retriever.calls))
        with self.assertRaises(FileExistsError):
            self._run(output, _FakeRetriever())

    def test_retrieval_mismatch_fails_closed_and_preserves_reproduction_failure(self):
        class _MismatchedRetriever(_FakeRetriever):
            def candidates_for_query(self, *args, **kwargs):
                rows = super().candidates_for_query(*args, **kwargs)
                if args[0] == "Question 1":
                    rows["lexical"][0]["retrieval"]["score"] = -1.0
                return rows

        output = self.root / "mismatch"
        with self.assertRaisesRegex(supply.CandidateSupplyError, "historical candidate hash mismatch"):
            self._run(output, _MismatchedRetriever())
        manifest = json.loads((output / "metadata" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "reproduction_failed")
        self.assertFalse((output / "candidates" / "Q001.json").exists())
        with self.assertRaisesRegex(supply.CandidateSupplyError, "cannot be resumed"):
            self._run(output, _FakeRetriever(), resume=True)

    def test_co_tampered_structurally_valid_historical_oracle_stops_before_retrieval(self):
        history = json.loads(self.hashes.read_text(encoding="utf-8"))
        history["co_tamper_probe"] = "different bytes, still structurally valid"
        self._write(self.hashes, history)
        output = self.root / "co-tampered"
        retriever = _FakeRetriever()
        with self.assertRaisesRegex(supply.CandidateSupplyError, "candidate-hash diagnostic SHA-256"):
            self._run(output, retriever)
        self.assertEqual(retriever.calls, [])
        self.assertFalse(output.exists())

    def test_each_external_artifact_anchor_rejects_byte_drift_before_retrieval(self):
        for label, path, expected_message in (
            ("runtime", self.runtime, "runtime question input SHA-256"),
            ("attribution", self.full_rows, "22Q attribution SHA-256"),
        ):
            with self.subTest(label=label):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                output = self.root / f"drift-{label}"
                retriever = _FakeRetriever()
                with self.assertRaisesRegex(supply.CandidateSupplyError, expected_message):
                    self._run(output, retriever)
                self.assertEqual(retriever.calls, [])
                self.assertFalse(output.exists())
                path.write_bytes(original)

    def test_current_baseline_must_match_external_identity_not_only_m2_manifest(self):
        incorrect = dict(self.baseline_metadata)
        incorrect["dense_build_identity"] = "not-the-audited-dense-build"
        with patch.object(supply, "ACCEPTED_RUNTIME_INPUT_SHA256", self.accepted_runtime_sha), patch.object(supply, "ACCEPTED_CANDIDATE_HASHES_SHA256", self.accepted_hashes_sha), patch.object(supply, "ACCEPTED_FULL_ROWS_SHA256", self.accepted_full_rows_sha), patch.object(supply, "ACCEPTED_BASELINE", self.baseline_metadata), patch.object(supply, "_baseline_metadata", return_value=incorrect):
            with self.assertRaisesRegex(supply.CandidateSupplyError, "accepted external identity: dense_build_identity"):
                supply.preflight_candidate_supply(
                    self.runtime,
                    baseline=self.baseline,
                    source_manifest_path=self.source,
                    m2_run_manifest_path=self.m2,
                    historical_hashes_path=self.hashes,
                    full_rows_path=self.full_rows,
                )

    def test_explicit_resume_recovers_a_non_reproduction_interruption(self):
        output = self.root / "resume"
        with self.assertRaisesRegex(supply.CandidateSupplyError, "partial artifacts remain inspectable"):
            self._run(output, _FakeRetriever(fail_question="Question 2"))
        partial = json.loads((output / "metadata" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(partial["status"], "failed")
        self.assertEqual(partial["completed_question_ids"], ["Q001"])
        self.assertEqual(partial["completed_question_count"], 1)
        self.assertNotIn("retrieval_calls_issued", partial)
        accepted = self._run(output, _FakeRetriever(), resume=True)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(len(list((output / "candidates").glob("Q*.json"))), 70)

    def test_valid_complete_state_resumes_to_accepted_after_revalidating_artifacts(self):
        output = self.root / "complete-resume"
        self._run(output, _FakeRetriever())
        manifest_path = output / "metadata" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "complete"
        self._write(manifest_path, manifest)
        result = self._run(output, _FakeRetriever(), resume=True)
        self.assertEqual(result["status"], "accepted")

    def test_real_full_candidate_projection_matches_the_accepted_a1_row_shape(self):
        unit = {
            "unit_id": "u1", "content_type": "rich_text", "retrieval_visible_text": "visible text",
            "source_order": [1, 2, 3], "nested_selector": {"kind": "rich_text"},
            "fragment_selector": {"kind": "whole"}, "structure": {},
            "source": {
                "canonical_address": {"source_identity_key": "source", "record_id": "record", "section_ordinal": 1, "component_observation_key": "component", "component_ordinal": 2, "canonical_unit_ordinal": 3, "canonical_unit_kind": "rich_text", "parsed_json_pointer": "/unit"},
                "lineage": {"raw_refs": []}, "record_context": {"record_title": "title"},
                "provenance": {"state": "unknown"}, "content_role": {"state": "unknown"},
            },
        }
        candidate = {"unit_id": "u1", "rank": 1, "retrieval": {"mode": "hybrid", "score": 0.5}}
        actual = supply._full_candidate_record(candidate, 0, {"u1": unit})
        expected = {
            "unit_id": "u1", "rank": 1, "input_index": 0, "score": 0.5,
            "retrieval": {"mode": "hybrid", "score": 0.5}, "text": "visible text",
            "record_context": {"record_title": "title"},
            "canonical_address": unit["source"]["canonical_address"], "source_order": [1, 2, 3],
            "content_type": "rich_text", "nested_selector": {"kind": "rich_text"},
            "fragment_selector": {"kind": "whole"}, "lineage": {"raw_refs": []},
            "provenance": {"state": "unknown"},
        }
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), list(expected))


if __name__ == "__main__":
    unittest.main()
