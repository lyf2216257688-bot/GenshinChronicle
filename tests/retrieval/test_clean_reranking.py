from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from genshin_corpus.evidence_io import digest, read, seal, sha, write
from genshin_corpus.indexing.benchmark_io import receipt
from genshin_corpus.retrieval.clean_based.rerank_core import ranked_candidates, candidate_vectors
from genshin_corpus.retrieval.clean_based import rerank_job as job
from genshin_corpus.retrieval.clean_based.rerank_model import LocalReranker
from tests.retrieval.test_clean_based import document


def candidate(cid, number, start=0, end=10, **kwargs):
    doc = document(cid, number, start, end, **kwargs)
    text = "原文" + cid
    doc["chunk"]["retrieval_text"] = text
    doc["row"].update(chunk_id=cid, dense_text=text, dense_text_sha256=sha256(text.encode()).hexdigest(),
                       source_context={"source_provenance": {"state": "unknown"}})
    return {"chunk_id": cid, "input_row": number, "source": doc["row"], "chunk": doc["chunk"],
            "hits": [{"channel": "dense", "rank": number + 1}], "fusion_rank": number + 1}


def bundle(rows):
    return {"input_bundle_id": "bundle", "query": {"question": "问题", "question_id": "Q001"}, "candidates": rows}


class OrderingTests(unittest.TestCase):
    def test_containment_uses_relevance_and_preserves_candidate_source(self):
        source = bundle([candidate("micro", 0, 2, 5), candidate("macro", 1, 0, 10)])
        original = deepcopy(source)
        result = ranked_candidates(source, {"macro": 9., "micro": 8.})
        self.assertEqual(result["candidates"][1]["reranking"]["covered_by"], "macro")
        reverse = ranked_candidates(source, {"micro": 9., "macro": 8.})
        self.assertIsNone(reverse["candidates"][1]["reranking"]["covered_by"])
        self.assertEqual(source, original)
        self.assertEqual(result["candidates"][1]["source"], source["candidates"][0]["source"])

    def test_partial_overlap_and_distinct_events_stay_independent(self):
        source = bundle([candidate("a", 0, 0, 10), candidate("b", 1, 5, 15), candidate("c", 2, 20, 25)])
        rows = ranked_candidates(source, {"a": 3., "b": 2., "c": 1.})["candidates"]
        self.assertTrue(rows[1]["reranking"]["overlaps"])
        self.assertFalse(rows[2]["reranking"]["overlaps"])
        self.assertTrue(all(r["reranking"]["covered_by"] is None for r in rows))

    def test_different_sources_conditions_and_unknown_are_not_folded(self):
        for variation in ("source", "condition", "unknown"):
            a, b = candidate("a", 0), candidate("b", 1)
            if variation == "source": b["chunk"]["observation_id"] = "different"
            elif variation == "condition": b["chunk"]["parts"][0]["literal_condition"] = "如果下雨"
            else: b["chunk"]["parts"][0]["condition_status"] = "unresolved"
            result = ranked_candidates(bundle([a, b]), {"a": 2., "b": 1.})
            self.assertIsNone(result["candidates"][1]["reranking"]["covered_by"])

    def test_bad_scores_and_id_coverage_fail(self):
        source = bundle([candidate("a", 0)])
        for scores in ({}, {"wrong": 1}, {"a": float("nan")}, {"a": float("inf")}):
            with self.assertRaises(ValueError): ranked_candidates(source, scores)

    def test_relevance_ties_preserve_original_recall_order(self):
        result = ranked_candidates(bundle([candidate("b", 0), candidate("a", 1)]), {"a": 2., "b": 2.}, structural=False)
        self.assertEqual([r["chunk_id"] for r in result["candidates"]], ["b", "a"])

    def test_score_band_protects_beyond_top_three(self):
        rows = [candidate(str(n), n, n * 10, n * 10 + 5) for n in range(6)]
        scores = dict(zip([str(n) for n in range(6)], [10., 9.9, 9.8, 9.7, 2., 0.]))
        vectors = {str(n): [1., 0.] for n in range(6)}
        result = ranked_candidates(bundle(rows), scores, mmr_lambda=.7, vectors=vectors)
        self.assertEqual(result["candidates"][3]["reranking"]["mmr"]["protected"], "score_band")
        self.assertAlmostEqual(result["candidates"][3]["reranking"]["mmr"]["relative_gap"], .03)
        self.assertEqual(len(result["candidates"]), 6)

    def test_logit_gaps_not_ordinal_ranks_drive_relevance(self):
        rows = [candidate(str(n), n, n * 10, n * 10 + 5) for n in range(4)]
        result = ranked_candidates(bundle(rows), {"0": 10., "1": 9.99, "2": 9.98, "3": 0.},
                                   mmr_lambda=.85, vectors={str(n): [1., n + 1.] for n in range(4)}, protected_top=1)
        self.assertAlmostEqual(result["candidates"][1]["reranking"]["mmr"]["relevance"], .999)

    def test_score_band_boundary_ties_protect_beyond_top_three(self):
        rows = [candidate(str(n), n, n * 10, n * 10 + 5) for n in range(6)]
        scores = dict(zip([str(n) for n in range(6)], [10., 9.9, 9.8, 9., 9., 0.]))
        result = ranked_candidates(bundle(rows), scores, mmr_lambda=.7,
                                   vectors={str(n): [1., 0.] for n in range(6)})
        protected = [r for r in result["candidates"] if r["reranking"]["mmr"]["protected"]]
        self.assertEqual([r["chunk_id"] for r in protected], ["0", "1", "2", "3", "4"])
        self.assertTrue(all(r["reranking"]["mmr"]["protected"] == "score_band" for r in protected[3:]))

    def test_covered_duplicate_is_deferred_and_pool_does_not_refill_past_relevance_depth(self):
        rows = [candidate("macro", 0, 0, 10), candidate("micro", 1, 2, 5),
                candidate("independent", 2, 20, 25), candidate("outside", 3, 30, 35)]
        result = ranked_candidates(bundle(rows), {"macro": 10., "micro": 9.9, "independent": 9.8, "outside": 9.7},
            mmr_lambda=.7, local_depth=3, vectors={r["chunk_id"]: [1., 0.] for r in rows})
        byid = {r["chunk_id"]: r["reranking"] for r in result["candidates"]}
        self.assertEqual(byid["micro"]["covered_by"], "macro")
        self.assertIsNone(byid["micro"]["mmr"])
        self.assertIsNone(byid["outside"]["mmr"])
        self.assertEqual([r["chunk_id"] for r in result["candidates"]], ["macro", "independent", "outside", "micro"])

    def test_equal_scores_and_lambda_one_preserve_order(self):
        rows = [candidate(str(n), n, n * 10, n * 10 + 5) for n in range(5)]
        source, scores = bundle(rows), {str(n): 0. for n in range(5)}
        for lam in (1., .85, .7):
            result = ranked_candidates(source, scores, mmr_lambda=lam, vectors={str(n): [1., 0.] for n in range(5)})
            self.assertEqual([c["chunk_id"] for c in result["candidates"]], [str(n) for n in range(5)])

    def test_mmr_demotes_redundancy_but_preserves_tail_and_set(self):
        rows = [candidate(str(n), n, n * 10, n * 10 + 5) for n in range(6)]
        result = ranked_candidates(bundle(rows), {str(n): 10. - n for n in range(6)}, mmr_lambda=.7,
            vectors={"0": [1, 0], "1": [1, 0], "2": [1, 0], "3": [1, 0], "4": [0, 1], "5": [0, 1]},
            local_depth=5, protected_top=1, protected_gap=0.)
        ids = [c["chunk_id"] for c in result["candidates"]]
        self.assertEqual(ids[0], "0")
        self.assertEqual(ids[-1], "5")
        self.assertLess(ids.index("4"), ids.index("3"))

    def test_missing_nonfinite_and_zero_vectors_fail(self):
        source = bundle([candidate("a", 0), candidate("b", 1, 20, 25)])
        for vectors in ({"a": [1, 0]}, {"a": [1, 0], "b": [float("nan"), 0]}, {"a": [1, 0], "b": [0, 0]}):
            with self.assertRaises(ValueError): ranked_candidates(source, {"a": 2., "b": 1.}, mmr_lambda=.85, vectors=vectors)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.source = self.repo / "data/retrieval/p4"
        self.source.mkdir(parents=True)
        self.config = {"version": job.VERSION, "recall_output": "data/retrieval/p4", "input": "data/retrieval/input",
            "output": "data/retrieval/run", "smoke_output": "data/retrieval/smoke", "profile": "S0/f1_max",
            "expected_questions": 2, "expected_candidates": 4, "models": ["bge", "gte"], "initial_pair_budget": 2048,
            "limits": {"minimum_running_ram_bytes": None}}
        qs = [{"question_id": "Q001", "question": "问题一"}, {"question_id": "Q002", "question": "问题二"}]
        plan = {"questions": qs, "input_bundle_id": "bundle"}
        write(self.source / "plan.json", plan)
        units = {}
        for q in qs:
            folder = self.source / "work" / q["question_id"]
            folder.mkdir(parents=True)
            rows = [candidate("a", 0), candidate("b", 1, 20, 25)]
            selected = {**bundle([]), "query": q, "profile": {"combination": "S0", "structural_cleanup": True, "fusion": "f1_max"}}
            selected["candidates"] = [{k: v for k, v in c.items() if k not in {"source", "chunk"}} for c in rows]
            write(folder / "result.json", {"question": q, "profiles": {"S0/f1_max": selected}})
            write(folder / "documents.json", {c["chunk_id"]: {"row": c["source"], "chunk": c["chunk"], "input_row": c["input_row"]} for c in rows})
            receipt(folder, digest(plan))
            units[q["question_id"]] = {"folder": folder.relative_to(self.source).as_posix(), "receipt_sha256": sha(folder / "complete.json")}
        write(self.source / "complete.json", {"plan_binding": digest(plan), "units": {"comparison": units}})
        validation = self.source / "validations/check/report.json"
        write(validation, {"status": "PASS", "questions": 2, "complete_sha256": sha(self.source / "complete.json")})
        write(self.source / "validation.json", {"folder": "validations/check", "report_sha256": sha(validation),
                                               "complete_sha256": sha(self.source / "complete.json")})

    @contextmanager
    def fake_worker(self, repo, config, model, output):
        binding = {"model": model}
        def request(req):
            value = read(req["input"])
            ids = [c["chunk_id"] for c in value["candidates"]]
            if req["action"] == "audit": return {"counts": [{"chunk_id": c, "tokens": 2400 if c == "b" else 10} for c in ids]}
            return {"status": "complete", "scores": {c: float(n) for n, c in enumerate(ids)},
                    "candidate_set_sha256": digest(value), "binding": binding,
                    "audit": [{"chunk_id": c, "tokens": 10, "tokenization_seconds": .01,
                               "forward_seconds": .02} for c in ids], "elapsed_seconds": .1,
                    "load_seconds": .2, "resources": {}}
        yield {"binding": binding, "native_cap": 8192}, request

    def prepared(self):
        job.prepare(self.repo, self.config)
        with patch.object(job, "worker", self.fake_worker), patch.object(job, "code_files", return_value={}):
            result = job.preflight(self.repo, self.config, self.repo / self.config["output"])
        self.assertEqual(result["pair_budget"], 2400)
        self.assertEqual(result["inference_pairs"], 0)

    def test_export_preserves_text_and_fails_on_source_tamper(self):
        result = job.prepare(self.repo, self.config)
        self.assertEqual(result["candidates"], 4)
        job.input_manifest(self.repo, self.config)
        path = self.source / "work/Q001/documents.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError): job.input_manifest(self.repo, self.config)

    def test_run_requires_matching_independent_smoke_validation(self):
        self.prepared()
        with patch.object(job, "code_files", return_value={}):
            with self.assertRaises(FileNotFoundError): job.execute(self.repo, self.config, self.repo / self.config["output"])

    def test_smoke_full_resume_validate_and_corruption_rejection(self):
        self.prepared()
        with patch.object(job, "worker", self.fake_worker), patch.object(job, "code_files", return_value={}):
            smoke = self.repo / self.config["smoke_output"]
            job.execute(self.repo, self.config, smoke, smoke=True)
            job.validate(self.repo, self.config, smoke)
            output = self.repo / self.config["output"]
            job.execute(self.repo, self.config, output)
            before = sha(output / "complete.json")
            with patch.object(job, "worker", side_effect=AssertionError("Resume must not rescore complete units")):
                job.execute(self.repo, self.config, output, resume=True)
            self.assertEqual(sha(output / "complete.json"), before)
            self.assertEqual(job.validate(self.repo, self.config, output)["groups"], 4)
            write(self.repo / "config/index-selected-v1.json", {})
            reported = job.report(self.repo, self.config, output)
            page = read(Path(reported["report"]) / "Q001.json")
            self.assertEqual(page["recall_order"], ["a", "b"])
            self.assertEqual(page["topk"]["S0"]["5"], ["a", "b"])
            self.assertEqual(page["topk"]["bge"]["5"], ["b", "a"])
            self.assertGreaterEqual(page["telemetry"]["bge"]["result_write_seconds"], 0.)
            folder = next((output / "work/bge").iterdir())
            (folder / "result.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError): job.validate(self.repo, self.config, output)

    def test_checkpoint_duplicate_unknown_and_changed_code_fail_closed(self):
        self.prepared()
        with patch.object(job, "code_files", return_value={"changed": "hash"}):
            with self.assertRaises(ValueError): job.load_plan(self.repo, self.config, self.repo / self.config["output"])

    def test_interrupted_scoring_preserves_committed_questions_and_resumes_only_pending(self):
        self.prepared()
        output = self.repo / self.config["output"]
        requests = []

        class SimulatedCudaOOM(RuntimeError):
            pass

        @contextmanager
        def interrupted_worker(repo, config, model, target):
            with self.fake_worker(repo, config, model, target) as (ready, request):
                def fail_second(value):
                    requests.append((model, read(value["input"])["query"]["question_id"]))
                    if len(requests) == 2:
                        raise SimulatedCudaOOM("fixture: CUDA out of memory")
                    return request(value)
                yield ready, fail_second

        with patch.object(job, "code_files", return_value={}):
            smoke = self.repo / self.config["smoke_output"]
            with patch.object(job, "worker", self.fake_worker):
                job.execute(self.repo, self.config, smoke, smoke=True)
            job.validate(self.repo, self.config, smoke)
            with patch.object(job, "worker", interrupted_worker):
                with self.assertRaises(SimulatedCudaOOM):
                    job.execute(self.repo, self.config, output)
            plan = read(output / "plan.json")
            saved = job.checkpoints(output, plan)
            self.assertEqual(set(saved), {("bge", "Q001")})
            checkpoint = saved["bge", "Q001"] / "complete.json"
            original = sha(checkpoint)
            self.assertFalse((output / "complete.json").exists())
            # The kernel lock is released; its persistent marker stays on disk.
            # Successful Resume below proves another owner can reacquire it.

            resumed = []
            @contextmanager
            def tracking_worker(repo, config, model, target):
                with self.fake_worker(repo, config, model, target) as (ready, request):
                    def track(value):
                        resumed.append((model, read(value["input"])["query"]["question_id"]))
                        return request(value)
                    yield ready, track
            with patch.object(job, "worker", tracking_worker):
                job.execute(self.repo, self.config, output, resume=True)
            self.assertEqual(resumed, [("bge", "Q002"), ("gte", "Q001"), ("gte", "Q002")])
            self.assertEqual(sha(checkpoint), original)
            self.assertEqual(job.validate(self.repo, self.config, output)["groups"], 4)

    def test_output_cannot_overwrite_parent_or_existing_source(self):
        with self.assertRaises(ValueError): job.new_root(self.repo, self.repo / "data/retrieval")
        with self.assertRaises(FileExistsError): job.new_root(self.repo, self.source)


class AdapterFailureTests(unittest.TestCase):
    def adapter(self):
        value = LocalReranker.__new__(LocalReranker)
        value.cap = 8192
        value.model = object()
        value.load = Mock()
        value.count = Mock(return_value=2049)
        value.tokenizer = Mock()
        return value

    def test_overlength_fails_without_truncating_or_scoring(self):
        from types import SimpleNamespace
        adapter = self.adapter()
        torch_stub = SimpleNamespace(cuda=SimpleNamespace(reset_peak_memory_stats=Mock()))
        with patch.dict("sys.modules", torch=torch_stub), patch(
                "genshin_corpus.retrieval.clean_based.rerank_model.resources", return_value={}):
            with self.assertRaisesRegex(ValueError, "Overlength"):
                adapter.score(bundle([candidate("a", 0)]), 2048)
        adapter.tokenizer.assert_not_called()

    def test_model_load_oom_propagates_without_cpu_fallback(self):
        adapter = self.adapter()
        failure = RuntimeError("fixture: CUDA out of memory")
        adapter.load.side_effect = failure
        with patch.dict("sys.modules", torch=Mock()):
            with self.assertRaises(RuntimeError) as caught:
                adapter.score(bundle([candidate("a", 0)]), 2048)
        self.assertIs(caught.exception, failure)
        adapter.load.assert_called_once_with()
        adapter.tokenizer.assert_not_called()


if __name__ == "__main__": unittest.main()
