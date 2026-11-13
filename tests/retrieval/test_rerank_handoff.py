"""Small source-bound interface fixtures; no local corpus, models or network."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import digest, read, sha, write, seal
from genshin_corpus.retrieval.clean_based import rerank_handoff as handoff
from genshin_corpus.retrieval.clean_based.rerank_core import ranked_candidates
from tests.retrieval.test_clean_reranking import candidate, bundle
from tests.retrieval.test_clean_based import profile as retrieval_profile


def selection():
    return {"version": handoff.SELECTION_VERSION, "model": "BAAI/bge-reranker-v2-m3",
            "input_bundle_id": "bundle", "default_profile": "bge_structural", "fallback_profile": "bge_relevance",
            "automatic_fallback": False, "profiles": deepcopy(handoff.PROFILES), "native_pair_cap": 8192,
            "score_binding": {"model": "bge", "files": {"model.safetensors": "fixture-only"},
                "runtime": {"python": "3.14.5", "platform": "windows-amd64", "gil_disabled": False,
                            "packages": {"torch": "fixture-only"}},
                "dtype": "float16", "device": "cuda", "attention": "eager", "batch_size": 1,
                "pairing": "query_full_retrieval_text", "truncation": False}}


def inputs():
    rows = [candidate("micro", 0, 2, 5), candidate("macro", 1, 0, 10), candidate("other", 2, 20, 25)]
    for c in rows:
        c["source"].update({k: deepcopy(c["chunk"][k]) for k in ("observation_id", "parent_id", "clean_refs")})
        c["source"].update(clean_record={"record_path": "fixture-only.json"}, chunk_locator={},
                           parent_locator=None, conditions=[], input_snapshot_id="fixture-only")
        c.update(organized_rank=c["input_row"]+1, disposition="selected", original_extension={"unknown": True})
    value = {**bundle(rows), "version": "gc-clean-candidates-1", "status": "complete",
             "profile": retrieval_profile(), "raw_hits": [{"preserved": True}], "dispositions": []}
    return value


def scores(value, tokens=10):
    return {"status": "complete", "binding": selection()["score_binding"],
            "candidate_set_sha256": digest(value), "scores": {"macro": 9., "micro": 8., "other": 7.},
            "audit": [{"chunk_id": c["chunk_id"], "tokens": tokens} for c in value["candidates"]],
            "resources": {"fixture": True}, "elapsed_seconds": .1}


class InterfaceTests(unittest.TestCase):
    def test_complete_interface_preserves_every_source_and_recall_field(self):
        source = inputs(); raw = scores(source); original = deepcopy(source)
        with patch("genshin_corpus.retrieval.clean_based.rerank_core.candidate_vectors",
                   side_effect=AssertionError("No Dense reader for MMR-off")):
            result = handoff.rank_candidate_set(source, raw, selection(), pair_budget=2048)
        self.assertEqual(source, original)
        self.assertEqual([c["chunk_id"] for c in result["candidates"]], ["macro", "other", "micro"])
        for c in result["candidates"]:
            self.assertEqual({k: v for k, v in c.items() if k != "reranking"},
                             next(row for row in source["candidates"] if row["chunk_id"] == c["chunk_id"]))
            self.assertIsNone(c["reranking"]["mmr"])
        for k, v in source.items():
            if k not in {"version", "candidates"}: self.assertEqual(result[k], v)
        self.assertEqual(result["reranking_run"]["scoring"]["binding"], raw["binding"])
        self.assertEqual(result["candidates"][-1]["reranking"]["covered_by"], "macro")

    def test_fallback_requires_explicit_selection_and_never_masks_errors(self):
        source = inputs(); raw = scores(source)
        result = handoff.rank_candidate_set(source, raw, selection(), pair_budget=2048, profile="bge_relevance")
        self.assertEqual([c["chunk_id"] for c in result["candidates"]], ["macro", "micro", "other"])
        self.assertTrue(all(c["reranking"]["covered_by"] is None for c in result["candidates"]))
        raw["binding"]["model"] = "gte"
        with self.assertRaisesRegex(ValueError, "Foreign"):
            handoff.rank_candidate_set(source, raw, selection(), pair_budget=2048)
        with self.assertRaises(ValueError):
            handoff.rank_candidate_set(source, scores(source), selection(), pair_budget=2048, profile="mmr")

    def test_changed_query_text_hits_or_input_order_rejects_saved_scores(self):
        for kind in ("question", "hits", "order"):
            source = inputs(); raw = scores(source)
            if kind == "question": source["query"]["question"] += "改变"
            elif kind == "hits": source["candidates"][0]["hits"].append({"other": True})
            else: source["candidates"].reverse()
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "different CandidateSet"):
                handoff.rank_candidate_set(source, raw, selection(), pair_budget=2048)

    def test_source_identity_or_text_hash_drift_rejects_even_with_new_score_digest(self):
        for key in ("parent_id", "dense_text_sha256", "chunk_id"):
            source = inputs(); source["candidates"][0]["source"][key] = "foreign"
            with self.subTest(key=key), self.assertRaises(ValueError):
                handoff.rank_candidate_set(source, scores(source), selection(), pair_budget=2048)

    def test_missing_nonfinite_extra_scores_or_token_audits_fail(self):
        source = inputs()
        for kind in ("missing", "extra", "nan", "audit_missing", "audit_reordered", "tokens"):
            raw = scores(source)
            if kind == "missing": del raw["scores"]["micro"]
            elif kind == "extra": raw["scores"]["foreign"] = 1.
            elif kind == "nan": raw["scores"]["micro"] = float("nan")
            elif kind == "audit_missing": raw["audit"].pop()
            elif kind == "audit_reordered": raw["audit"].reverse()
            else: raw["audit"][0]["tokens"] = 2049
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                handoff.rank_candidate_set(source, raw, selection(), pair_budget=2048)

    def test_budget_is_explicit_and_2048_is_not_a_permanent_ceiling(self):
        source = inputs(); raw = scores(source, tokens=2500)
        result = handoff.rank_candidate_set(source, raw, selection(), pair_budget=3072)
        self.assertEqual(len(result["candidates"]), 3)
        self.assertEqual(result["reranking_run"]["pair_budget"], 3072)
        for budget in (2048, 8193, 0, True):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                handoff.rank_candidate_set(source, raw, selection(), pair_budget=budget)

    def test_wrong_bundle_profile_policy_or_scoring_runtime_fails(self):
        for kind in ("bundle", "upstream", "mmr", "automatic", "runtime"):
            source = inputs(); sel = selection(); raw = scores(source)
            if kind == "bundle": source["input_bundle_id"] = "foreign"
            elif kind == "upstream": source["profile"]["combination"] = "S1"
            elif kind == "mmr": sel["profiles"]["bge_structural"]["mmr_lambda"] = .85
            elif kind == "automatic": sel["automatic_fallback"] = True
            else: raw["binding"]["runtime"]["python"] = "3.12.10"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                handoff.rank_candidate_set(source, raw, sel, pair_budget=2048)

    def test_ranked_or_empty_inputs_are_explicit_failures(self):
        source = inputs()
        ranked = handoff.rank_candidate_set(source, scores(source), selection(), pair_budget=2048)
        with self.assertRaises(ValueError):
            handoff.rank_candidate_set(ranked, scores(source), selection(), pair_budget=2048)
        source["candidates"] = []
        with self.assertRaises(ValueError):
            handoff.rank_candidate_set(source, scores(source), selection(), pair_budget=2048)


class ExportTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.output = self.repo / "data/retrieval/handoff"
        self.frozen = self.repo / "data/retrieval/input"
        self.analysis = self.repo / "data/retrieval/analysis"
        self.selpath = self.repo / "config/selected.json"
        self.sel = selection(); write(self.selpath, self.sel)
        self.value = inputs(); self.raw = scores(self.value)
        write(self.frozen / "Q001.json", self.value)
        self.scored = self.repo / "data/retrieval/scored/Q001"
        write(self.scored / "result.json", self.raw)
        variants = {}
        for k, structural in (("bge", False), ("structural/1.0", True)):
            ranked = ranked_candidates(self.value, self.raw["scores"], structural=structural)
            variants[k] = {"policy": ranked["policy"], "candidates": [
                {name: c[name] for name in ("chunk_id", "input_row", "reranking")} for c in ranked["candidates"]]}
        write(self.analysis / "Q001.json", {"selected_for_analysis": "bge", "variants": variants})
        self.plan = {"budget": 2048, "questions": [{"question_id": "Q001", "file": "Q001.json",
                                                  "sha256": sha(self.frozen / "Q001.json")}]}
        stub = (self.sel, self.frozen, self.plan, {("bge", "Q001"): self.scored}, self.analysis, {"fixture": True})
        mock = patch.object(handoff, "_sources", return_value=stub); mock.start(); self.addCleanup(mock.stop)

    def test_export_validate_and_consumer_read_preserve_every_candidate(self):
        result = handoff.export_handoff(self.repo, self.selpath, self.output)
        self.assertEqual(result["candidates"], 3)
        checked = handoff.validate_handoff(self.repo, self.selpath, self.output)
        self.assertEqual(checked["status"], "PASS")
        self.assertEqual(checked["fallback_questions_checked"], 1)
        value = handoff.read_handoff(self.output, "Q001")
        self.assertEqual(len(value["candidates"]), 3)
        with self.assertRaises(ValueError): handoff.read_handoff(self.output, "Q999")
        with self.assertRaises(ValueError): handoff.read_handoff(self.output, "Q001", profile="bge_relevance")

    def test_tampered_file_rejected_and_resealed_semantic_change_still_rejected(self):
        handoff.export_handoff(self.repo, self.selpath, self.output)
        path = self.output / "ranked/Q001.json"
        value = read(path); value["candidates"][0]["source"]["parent_id"] = "foreign"
        path.unlink(); write(path, value)
        with self.assertRaises(ValueError): handoff.validate_handoff(self.repo, self.selpath, self.output)
        manifest = read(self.output / "manifest.json"); manifest["questions"][0]["sha256"] = sha(path)
        (self.output / "manifest.json").unlink(); write(self.output / "manifest.json", manifest)
        (self.output / "complete.json").unlink(); seal(self.output)
        with self.assertRaisesRegex(ValueError, "content/order differs"):
            handoff.validate_handoff(self.repo, self.selpath, self.output)

    def test_explicit_fallback_export_is_labelled_and_not_read_as_the_default(self):
        result = handoff.export_handoff(self.repo, self.selpath, self.output, profile="bge_relevance")
        self.assertEqual(result["deferred"], 0)
        self.assertEqual(handoff.validate_handoff(self.repo, self.selpath, self.output)["status"], "PASS")
        with self.assertRaises(ValueError): handoff.read_handoff(self.output, "Q001")
        value = handoff.read_handoff(self.output, "Q001", profile="bge_relevance")
        self.assertEqual([c["chunk_id"] for c in value["candidates"]], ["macro", "micro", "other"])

    def test_existing_and_upstream_outputs_are_never_overwritten(self):
        handoff.export_handoff(self.repo, self.selpath, self.output)
        before = sha(self.output / "complete.json")
        with self.assertRaises(FileExistsError): handoff.export_handoff(self.repo, self.selpath, self.output)
        self.assertEqual(before, sha(self.output / "complete.json"))
        with self.assertRaises(ValueError):
            handoff.export_handoff(self.repo, self.selpath, self.frozen / "nested")

    def test_failure_retains_incomplete_output_without_completion(self):
        with patch.object(handoff, "_compare_analysis", side_effect=ValueError("Mismatch")):
            with self.assertRaises(ValueError): handoff.export_handoff(self.repo, self.selpath, self.output)
        self.assertTrue((self.output / "failure.json").exists())
        self.assertFalse((self.output / "complete.json").exists())
        with self.assertRaises(FileNotFoundError): handoff.read_handoff(self.output, "Q001")


if __name__ == "__main__":
    unittest.main()
