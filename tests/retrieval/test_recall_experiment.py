"""Tiny P4 provenance, fair reuse, recovery and replay tests; no full local assets."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import digest, inventory, read, sha, write
from genshin_corpus.indexing import ChunkInputSource, prepare_inputs
from genshin_corpus.indexing.benchmark_io import child_process, mapping
from genshin_corpus.retrieval.clean_based import experiment as job
from genshin_corpus.retrieval.clean_based.core import QuerySpec
from genshin_corpus.retrieval.clean_based.experiment_analysis import (
    analyze, coverage_diagnostics, difference, profiles, render_cases, span_union, stable_comparison)
from genshin_corpus.retrieval.clean_based.experiment_report import percentile, representative
from genshin_corpus.retrieval.clean_based.experiment_worker import make_hits
from genshin_corpus.retrieval.clean_based.source import FileBinding, InputReader
from genshin_corpus.retrieval.local_qwen3_embedding import LocalQwen3Error
from tests.indexing.test_indexing import build_source
from tests.retrieval.test_clean_based import document

REPO = Path(__file__).resolve().parents[2]


def fixture_config():
    config = read(REPO / "config/recall-experiment-v1.json")
    config.update(minimum_running_ram_gib=.000001, minimum_available_ram_gib=.000001, minimum_free_gib=.000001)
    return config


def simple_documents():
    docs = {c: document(c, i, i * 10, (i + 1) * 10) for i, c in enumerate("abcd")}
    for c, d in docs.items():
        d["row"].update(observation_id="obs", parent_id="parent", source_item={"content_id": c}, source_context={},
                        clean_refs=d["chunk"]["clean_refs"])
        d["chunk"]["retrieval_text"] = "实际原文" + c + "\n~~~~\n😀"
    return docs


def simple_stages(question, docs):
    result = {}
    for ch, cids in (("dense", "ab"), ("sparse", "bc"), ("bm25f", "ad")):
        hits = []
        for g in ("micro", "macro"):
            for n, c in enumerate(cids, 1):
                hits.append({"question_id": question["question_id"], "query_id": "original", "kind": "original", "round": 0,
                    "chunk_id": c, "input_row": docs[c]["input_row"], "channel": ch, "granularity": g,
                    "rank": n, "raw_score": float(3 - n), "representation_id": ch + g})
        result[ch] = {"hits": hits, "elapsed_seconds": .01, "encode_seconds": .001, "scan_seconds": .009, "resources": {}}
    return result


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.config, self.docs = fixture_config(), simple_documents()
        self.question = {"question_id": "test", "question": "问题？"}
        self.stages = simple_stages(self.question, self.docs)

    def result(self):
        return analyze(self.question, self.stages, self.docs, self.config, "bundle")

    def test_profiles_reuse_normalized_weights_and_are_replaceable(self):
        points = profiles(self.config)
        self.assertEqual(len(points), 12)
        self.assertEqual(points["S2/f0"]["channel_weights"], {"dense": .5, "sparse": .25, "bm25f": .25})
        self.config.update(depth=7, candidate_limit=8, rrf_k=9)
        self.assertEqual(profiles(self.config)["S1/f1_max"]["rrf_k"], 9)

    def test_shared_identity_occurs_once_but_all_hits_remain(self):
        result = self.result()
        for combo in ("S0", "S1", "S2"):
            rows = result["profiles"][combo + "/f1_max"]["candidates"]
            self.assertEqual(len(rows), len({r["chunk_id"] for r in rows}))
        b = next(r for r in result["profiles"]["S2/f1_max"]["candidates"] if r["chunk_id"] == "b")
        self.assertEqual(len(b["hits"]), 4)
        self.assertNotIn("chunk", b)

    def test_sparse_and_bm25_unique_residual_checks_dense(self):
        diagnostics = self.result()["diagnostics"]
        sparse = diagnostics["sparse_unique_vs_other_lexical"]
        self.assertEqual(sparse["also_dense"], ["b"])
        self.assertEqual(sparse["residual_vs_dense"]["only"], ["c"])
        self.assertEqual(diagnostics["bm25f_unique_vs_other_lexical"]["residual_vs_dense"]["only"], ["d"])

    def test_empty_routes_are_recorded_not_filled_with_zeroes(self):
        self.stages = {ch: {**s, "hits": []} for ch, s in self.stages.items()}
        self.docs = {}
        result = self.result()
        self.assertEqual(result["diagnostics"]["channel_pairs"]["dense/sparse"]["union"]["jaccard"], None)
        self.assertTrue(all(not p["candidates"] for p in result["profiles"].values()))

    def test_span_union_no_duplicate_or_cross_observation_credit(self):
        self.docs["b"]["chunk"]["clean_refs"] = deepcopy(self.docs["a"]["chunk"]["clean_refs"])
        self.assertEqual(span_union(self.docs, {"a", "b"}), 10)
        self.docs["b"]["chunk"]["clean_refs"][0]["observation_id"] = "other"
        self.assertEqual(span_union(self.docs, {"a", "b"}), 20)

    def test_same_text_different_conditions_not_declared_contained(self):
        self.docs["b"] = document("b", 1, 0, 10, condition_status="unresolved")
        self.assertEqual(difference({"b"}, {"a"}, self.docs)["body_codepoints_outside_right"], 0)
        self.assertEqual(difference({"b"}, {"a"}, self.docs)["not_proven_contained_by_right"], ["b"])

    def test_same_parent_long_story_has_no_quota(self):
        result = self.result()
        rows = result["profiles"]["S2/f1_max"]["candidates"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(coverage_diagnostics(self.docs, set(self.docs))["max_chunks_one_parent"], 4)

    def test_all_controls_and_outside_limit_dispositions_survive(self):
        self.config.update(candidate_limit=1, cutoffs=[1])
        result = self.result()
        self.assertTrue(any(r["disposition"] == "outside_candidate_limit"
                            for r in result["profiles"]["S2/f1_max"]["dispositions"]))
        self.assertIn("S2/f1_max -> S2/f0", result["diagnostics"]["comparisons"])
        self.assertIn("S2/f1_max -> S2/f1_max/no_cleanup", result["diagnostics"]["comparisons"])

    def test_actual_source_text_fence_and_caveat_are_preserved(self):
        text = render_cases(self.result(), self.docs, self.config)
        self.assertIn(self.docs["a"]["chunk"]["retrieval_text"], text)
        self.assertIn("~~~~~text", text)
        self.assertIn("不是相关性标签", text)

    def test_case_count_is_bounded_without_truncating_originals(self):
        self.config["max_case_chunks"] = 1
        text = render_cases(self.result(), self.docs, self.config)
        self.assertEqual(text.count("\n## "), 1)
        self.assertIn(self.docs["a"]["chunk"]["retrieval_text"], text)

    def test_source_case_preserves_crlf_and_unicode_bytes_on_windows(self):
        self.docs["a"]["chunk"]["retrieval_text"] = "原文\r\n下一行😀\r末行\n"
        result = self.result()
        text = render_cases(result, self.docs, self.config)
        with TemporaryDirectory() as name:
            root = Path(name)
            folder = job.commit_unit(root, {"fixture": True}, "comparison", "test", result,
                self.docs, text, [])
            self.assertEqual((folder / "cases.md").read_bytes(), text.encode("utf-8"))

    def test_replay_ignores_only_fusion_elapsed_not_rank_or_diagnostics(self):
        first, second = self.result(), self.result()
        self.assertEqual(stable_comparison(first), stable_comparison(second))
        second["profiles"]["S2/f1_max"]["candidates"][0]["fusion_rank"] += 1
        self.assertNotEqual(stable_comparison(first), stable_comparison(second))

    def test_missing_channel_or_document_fails_instead_of_silent_s0(self):
        self.stages.pop("sparse")
        with self.assertRaisesRegex(ValueError, "Missing"):
            self.result()

    def test_representative_cases_unique_deterministic_and_include_control(self):
        rows = [{"question": {"question_id": str(n)}, "diagnostics": self.result()["diagnostics"]} for n in range(8)]
        selected = representative(rows, self.config)
        self.assertEqual(selected, representative(list(reversed(rows)), self.config))
        self.assertEqual(len({r["question_id"] for r in selected}), 6)
        self.assertEqual(selected[-1]["reason"], "Low-change control")
        self.assertEqual(percentile([1, 2, 3], .95), 3)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.config = fixture_config()
        self.input = self.root / "inputs/manifest.json"
        prepare_inputs(ChunkInputSource(build_source(self.root)), self.input.parent)
        with InputReader(self.input) as reader:
            self.docs = [reader.document(n) for n in range(min(4, len(reader.offsets)))]
            self.bundle = reader.bundle_id
        self.questions = [{"question_id": f"test-{n}", "question": "旅行者问题" + str(n)} for n in range(2)]
        self.plan = {"version": job.VERSION, "scope": "engineering-smoke", "questions": self.questions,
            "input_bundle_id": self.bundle, "input_manifest": str(self.input),
            "counts": read(self.input)["counts"], "representations": {ch: {g: ch + g for g in ("micro", "macro")}
            for ch in ("dense", "sparse", "bm25f")}, "profiles": profiles(self.config)}
        write(self.output / "plan.json", self.plan)
        self.calls, self.fail_on = [], None
        owner = self
        class FakeWorker:
            def __init__(self, repo, path, config, base, kind, folder):
                self.kind = kind
                self.ready = {"status": "ready", "input_bundle_id": owner.bundle,
                    "representations": {ch: owner.plan["representations"][ch] for ch in job.GROUPS[kind]},
                    "prepare_seconds": 0., "resources": {}}
            def search(self, query, depth):
                owner.calls.append((self.kind, query.question_id))
                if owner.fail_on == (self.kind, query.question_id):
                    raise RuntimeError("injected interruption")
                return owner.raw(query, self.kind)
            def __enter__(self): return self
            def __exit__(self, *args): pass
        self.patches = [patch.object(job, "verify_plan", side_effect=lambda *a: (self.plan, FileBinding())),
            patch("genshin_corpus.retrieval.clean_based.experiment_worker.ExperimentWorker", FakeWorker),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def raw(self, query, kind):
        stages = {}
        for ch in job.GROUPS[kind]:
            docs = self.docs[:3] if ch == "dense" else self.docs[1:]
            routes = {g: [{**mapping(d["input_row"], d["row"]), "score": float(10 - i)}
                         for i, d in enumerate(docs) if g in d["row"]["granularities"]] for g in ("micro", "macro")}
            stages[ch] = {"hits": make_hits(query, ch, routes, self.plan["representations"][ch]),
                "elapsed_seconds": .1, "encode_seconds": .02, "scan_seconds": .08, "resources": {}}
        return {"status": "complete", "query": json.loads(json.dumps(asdict(query))), "stages": stages}

    def run_job(self, mode="resume"):
        return job.run_experiment(self.root, self.root / "config.json", self.config, {}, self.output, mode)

    def test_interrupt_resume_skips_committed_channels_and_preserves_bytes(self):
        self.fail_on = ("bm25f", "test-1")
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            self.run_job()
        before = inventory(self.output / "work")
        self.calls.clear()
        self.fail_on = None
        self.run_job()
        self.assertEqual(self.calls, [("bm25f", "test-1")])
        after = inventory(self.output / "work")
        self.assertTrue(all(after[k] == v for k, v in before.items()))
        self.assertTrue((self.output / "report.json").exists())
        job.validate_job(self.root, self.config, self.output)
        job.report_existing(self.root, self.config, self.output)
        self.calls.clear()
        self.run_job()
        self.assertEqual(self.calls, [])

    def test_half_written_attempt_ignored_but_committed_corruption_fails(self):
        value = self.raw(QuerySpec(**self.questions[0]), "neural")
        def crash(folder):
            raise RuntimeError("half written")
        with self.assertRaises(RuntimeError):
            job.commit_unit(self.output, self.plan, "neural", "test-0", value, before_commit=crash)
        self.assertFalse(job.units(self.output, self.plan, "neural"))
        folder = job.commit_unit(self.output, self.plan, "neural", "test-0", value)
        self.assertEqual(len(list((self.output / "work/neural").iterdir())), 2)
        with (folder / "result.json").open("ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            job.units(self.output, self.plan, "neural")

    def test_raw_foreign_rep_query_rank_score_and_mapping_rejected(self):
        value = self.raw(QuerySpec(**self.questions[0]), "neural")
        job.check_raw(value, self.questions[0], self.plan, "neural", 50)
        for key, v in (("representation_id", "foreign"), ("rank", 4), ("raw_score", float("nan")), ("question_id", "wrong")):
            changed = deepcopy(value)
            changed["stages"]["dense"]["hits"][0][key] = v
            with self.assertRaises(ValueError):
                job.check_raw(changed, self.questions[0], self.plan, "neural", 50)
        for change in (lambda v: v["stages"]["dense"]["hits"].pop(),
                       lambda v: v["stages"]["dense"]["hits"][0]["index_mapping"].clear()):
            changed = deepcopy(value)
            change(changed)
            with self.assertRaises(ValueError):
                job.check_raw(changed, self.questions[0], self.plan, "neural", 50)
        stages = deepcopy(value["stages"])
        stages["dense"]["hits"][0]["index_mapping"]["observation_id"] = "foreign"
        with InputReader(self.input) as reader:
            with self.assertRaisesRegex(ValueError, "mapping differs"):
                job.documents_for(reader, stages)

    def test_duplicate_checkpoints_and_coverage_fail_closed(self):
        value = self.raw(QuerySpec(**self.questions[0]), "neural")
        for _ in range(2):
            job.commit_unit(self.output, self.plan, "neural", "test-0", value)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            job.units(self.output, self.plan, "neural")
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            job.check_complete(self.output, self.plan, {"neural": {}, "bm25f": {}}, {})

    def test_run_requires_fresh_full_plan(self):
        with self.assertRaisesRegex(ValueError, "Existing work/smoke"):
            self.run_job("run")

    def test_matching_validated_real_scope_smoke_gate_accepts_and_stale_rejects(self):
        self.config["smoke_output"] = "output"
        self.plan.update(questions=job.SMOKE_QUESTIONS, files={"fixture-model": "fixed"}, configuration=self.config)
        self.questions = job.SMOKE_QUESTIONS
        (self.output / "plan.json").write_text(json.dumps(self.plan), encoding="utf-8")
        self.run_job()
        job.validate_job(self.root, self.config, self.output)
        full = {**self.plan, "scope": "70q"}
        job.require_smoke(self.root, self.config, full)
        full["files"] = {"fixture-model": "changed"}
        with self.assertRaisesRegex(ValueError, "absent/stale"):
            job.require_smoke(self.root, self.config, full)

    def test_validate_detects_semantic_payload_tamper_even_with_new_receipt(self):
        self.run_job()
        folder = job.units(self.output, self.plan, "comparison")["test-0"]
        value = read(folder / "result.json")
        value["profiles"]["S2/f1_max"]["candidates"][0]["fusion_rank"] += 10
        # Test an internally self-consistent changed artifact, beyond a file-hash check.
        (folder / "result.json").write_text(json.dumps(value), encoding="utf-8")
        rec = read(folder / "complete.json")
        rec["files"]["result.json"] = sha(folder / "result.json")
        (folder / "complete.json").write_text(json.dumps(rec), encoding="utf-8")
        complete = read(self.output / "complete.json")
        complete["units"]["comparison"]["test-0"]["receipt_sha256"] = sha(folder / "complete.json")
        (self.output / "complete.json").write_text(json.dumps(complete), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "replay differs"):
            job.validate_job(self.root, self.config, self.output)

    def test_real_child_stop_retains_committed_unit_and_lock_recovers(self):
        marker = self.root / "ready.txt"
        script = """import sys,time
from pathlib import Path
from genshin_corpus.retrieval.clean_based.experiment import commit_unit
from genshin_corpus.evidence_io import read
from genshin_corpus.indexing.benchmark_io import build_lock,attempt
root=Path(sys.argv[1])
with build_lock(root):
 commit_unit(root,read(root/'plan.json'),'bm25f','test-0',{'fixture':True})
 folder=attempt(root/'work'/'bm25f','test-1')
 (folder/'result.json').write_text('partial')
 Path(sys.argv[2]).write_text('ready')
 time.sleep(120)
"""
        with child_process([sys.executable, "-B", "-c", script, str(self.output), str(marker)], stdout=subprocess.DEVNULL) as child:
            deadline = time.monotonic() + 15
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(marker.exists())
        from genshin_corpus.indexing.benchmark_io import build_lock
        # Windows job termination is asynchronous; the venv redirector may end
        # a few milliseconds before its interpreter's kernel lock is released.
        deadline = time.monotonic() + 5
        while True:
            try:
                with build_lock(self.output):
                    self.assertEqual(set(job.units(self.output, self.plan, "bm25f")), {"test-0"})
                break
            except LocalQwen3Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)


class BindingTests(unittest.TestCase):
    def test_70q_cannot_start_without_matching_validated_smoke(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            config = fixture_config()
            with self.assertRaisesRegex(ValueError, "Run Smoke"):
                job.require_smoke(root, config, {})

    def test_cli_preflight_never_dispatches_retrieval_and_smoke_cannot_use_full_root(self):
        with TemporaryDirectory() as name:
            root, config = Path(name), fixture_config()
            config.update(output="full", smoke_output="smoke")
            with patch.object(job, "settings", return_value=(config, {})), patch.object(job, "guard"), \
                    patch.object(job, "prepare", return_value={"status": "PASS"}) as preflight, \
                    patch.object(job, "run_experiment") as run:
                self.assertEqual(job.main(["preflight", "--repo", str(root)]), 0)
                self.assertEqual(preflight.call_count, 1)
                run.assert_not_called()
                self.assertEqual(job.main(["smoke", "--repo", str(root), "--output", "full"]), 1)
                run.assert_not_called()

    def test_preflight_binds_all_files_and_inspects_without_query(self):
        with TemporaryDirectory() as name:
            root, config = Path(name), fixture_config()
            inputs = root / "inputs/manifest.json"
            prepare_inputs(ChunkInputSource(build_source(root)), inputs.parent)
            input_manifest = read(inputs)
            config.update(questions="questions.jsonl", sparse_root="sparse", retrieval_config="base.json")
            questions = [{"question_id": f"Q{n:03}", "question": "问题"} for n in range(1, 71)]
            (root / "questions.jsonl").write_text("\n".join(map(json.dumps, questions)), encoding="utf-8")
            config["questions_sha256"] = sha(root / "questions.jsonl")
            manifests = {}
            for channel in ("dense", "bm25f", "sparse"):
                folder = root / channel
                folder.mkdir()
                (folder / "part").write_text(channel)
                manifest = {"status": "complete", "scope": "full-selected", "counts": input_manifest["counts"],
                    "input_bundle_id": input_manifest["input_bundle_id"], "configuration": {"fixture": "fixed"},
                    "space_id": "fixture-space", "binding": "fixture-binding", "input_manifest": str(inputs),
                    "representations": {g: channel + g for g in ("micro", "macro")}, "files": {"part": sha(folder / "part")}}
                write(folder / "manifest.json", manifest)
                manifests[channel] = (folder / "manifest.json", manifest)
            sparse = root / "sparse"
            write(sparse / "plan.json", {"fixture": True})
            validation = sparse / "validations/one/report.json"
            write(validation, {"status": "PASS", "manifest_sha256": sha(sparse / "manifest.json"),
                "counts": input_manifest["counts"], "plan_binding": "fixture-binding"})
            write(sparse / "validation.json", {"folder": "validations/one", "report_sha256": sha(validation),
                "manifest_sha256": sha(sparse / "manifest.json")})
            config["sparse_manifest_sha256"] = sha(sparse / "manifest.json")
            path, base = root / "config.json", {"selection": "selection.json"}
            write(path, config)
            write(root / "base.json", base)
            write(root / "selection.json", {})
            (root / "model").write_text("model")
            output = root / "output"
            output.mkdir()
            class Inspector:
                def __init__(self, repo, path, config, base, channel, output):
                    if channel != "inspect":
                        raise AssertionError("preflight launched retrieval worker")
                    self.ready = {"space_id": "fixture-space", "configuration": {"fixture": "fixed"},
                        "lengths": [5] * 70, "gpu": {}, "files": {str(root / "model"): sha(root / "model")}}
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def search(self, *args): raise AssertionError("preflight queried")
            before = {k: inventory(root / k) for k in ("dense", "bm25f", "sparse", "clean", "chunks", "inputs")}
            with patch.object(job, "selected", return_value=({"source": {"input_bundle_id": input_manifest["input_bundle_id"]}}, {k: manifests[k] for k in ("dense", "bm25f")})), \
                    patch.object(job, "validate_store"), patch.object(job, "code_files", return_value={}), \
                    patch("genshin_corpus.retrieval.clean_based.experiment_worker.ExperimentWorker", Inspector):
                plan = job.prepare(root, path, config, base, output)
                self.assertEqual(plan["scope"], "70q")
                self.assertEqual(len(plan["questions"]), 70)
                self.assertIn(str(root / "model"), plan["files"])
                job.verify_plan(root, config, output)
                self.assertFalse((output / "work").exists())
                self.assertTrue(all(inventory(root / k) == before[k] for k in before))

    def test_question_hash_ids_and_answer_leak_are_checked(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            path = root / "questions.jsonl"
            values = [{"question_id": f"Q{n:03}", "question": "问题"} for n in range(1, 71)]
            path.write_text("\n".join(map(json.dumps, values)) + "\n", encoding="utf-8")
            config = {"questions": path.name, "questions_sha256": sha(path)}
            self.assertEqual(len(job.questions_from(root, config)), 70)
            values[0]["reference_answer"] = "leak"
            path.write_text("\n".join(map(json.dumps, values)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                job.questions_from(root, config)
            config["questions_sha256"] = sha(path)
            with self.assertRaisesRegex(ValueError, "must not contain"):
                job.questions_from(root, config)

    def test_plan_code_and_asset_changes_invalidate_resume(self):
        with TemporaryDirectory() as name:
            root, config = Path(name), fixture_config()
            output = root / "output"
            output.mkdir()
            bound = root / "asset"
            bound.write_text("old")
            plan = {"version": job.VERSION, "scope": "engineering-smoke", "questions": job.SMOKE_QUESTIONS,
                    "profiles": profiles(config), "files": {str(bound): sha(bound)}, "configuration": config}
            write(output / "plan.json", plan)
            pre = output / "preflight-attempts/one/report.json"
            write(pre, {"status": "PASS", "plan_binding": digest(plan)})
            write(output / "preflight.json", {"folder": "preflight-attempts/one", "report_sha256": sha(pre), "plan_binding": digest(plan)})
            with patch.object(job, "code_files", return_value={}):
                job.verify_plan(root, config, output)
                bound.write_text("new")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    job.verify_plan(root, config, output)
            with patch.object(job, "code_files", return_value={"new-code": "hash"}):
                with self.assertRaisesRegex(ValueError, "implementation changed"):
                    job.verify_plan(root, config, output)

    def test_output_roots_protect_sparse_and_old_question_artifacts(self):
        config = fixture_config()
        seen = []
        def checked(repo, base, output, protected):
            seen.extend(protected)
        with patch.object(job, "output_guard", side_effect=checked):
            job.guard(REPO, config, {}, REPO / "data/retrieval/new")
        self.assertIn(REPO / config["sparse_root"], seen)
        self.assertIn((REPO / config["questions"]).parent, seen)


if __name__ == "__main__":
    unittest.main()
