"""G6 source fixtures: channel isolation, complete scoring/Packet and finite scope."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest

from genshin_corpus.evidence_io import digest, read, write
from genshin_corpus.generation.clean_evidence import assemble
from genshin_corpus.rag import clean_research as research, clean_research_live as live
from genshin_corpus.retrieval.clean_based.query_v2 import WEIGHTS, fuse_v2, validate_ancestry
from genshin_corpus.retrieval.clean_based.rerank_v2 import rank_v2
from tests.generation import test_clean_evidence as evidence, test_clean_research as controller
from tests.retrieval.test_clean_based import profile


class SparseEvidenceTests(unittest.TestCase):
    setUp = evidence.EvidenceTests.setUp
    scores = evidence.EvidenceTests.scores
    receipts = evidence.EvidenceTests.receipts
    pipeline = evidence.EvidenceTests.pipeline

    def s2(self):
        q = replace(self.query, combination="S2")
        hits = deepcopy(self.hits)
        for h in hits: h.update(channel="sparse", representation_id="sparse-" + h["granularity"])
        return q, fuse_v2(q, hits, self.docs, profile(combination="S2", channel_weights=WEIGHTS["S2"]),
                         input_bundle_id=self.reader.bundle_id, code={})

    def test_sparse_only_candidates_survive_complete_bge_scoring_and_packet(self):
        q, candidates = self.s2()
        ranked = rank_v2(candidates, self.scores(candidates), self.selection)
        _, _, receipts = self.receipts(ranked)
        packet = assemble([ranked], receipts, context=evidence.CONTEXT, budget=evidence.BUDGET)
        self.assertTrue(packet["evidence"])
        self.assertEqual({c["chunk_id"] for c in candidates["candidates"]},
                         {c["chunk_id"] for c in ranked["candidates"]})
        self.assertTrue(all({h["channel"] for h in c["hits"]} == {"sparse"} for c in ranked["candidates"]))
        self.assertEqual(candidates["profile"]["channel_weights"], WEIGHTS["S2"])
        self.assertEqual(ranked["query"], q.value())
        score = self.scores(candidates); score["audit"].pop()
        with self.assertRaises(ValueError): rank_v2(candidates, score, self.selection)

    def test_no_mixed_combination_or_parent_and_no_fusion_tuning(self):
        q, _ = self.s2()
        with self.assertRaises(ValueError):
            fuse_v2(q, [], {}, profile(), input_bundle_id=self.reader.bundle_id, code={})
        child = replace(q, query_id="sub", kind="subquestion", round=1,
                        trigger_gap="缺口", parent_query_ids=("original",))
        with self.assertRaises(ValueError): validate_ancestry(child, (self.query,))
        validate_ancestry(child, (q,))
        for change in ({"fusion": "f0"}, {"channel_weights": {"dense": .5, "sparse": .3, "bm25f": .2}},
                       {"candidate_limit": 101}, {"rrf_k": 61}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                fuse_v2(q, [], {}, profile(**{"combination": "S2", "channel_weights": WEIGHTS["S2"], **change}),
                        input_bundle_id=self.reader.bundle_id, code={})

    def s2_pipeline(self, **kwargs):
        pipeline, events = self.pipeline(**kwargs)
        pipeline.config.update(combination="S2", channel_weights=WEIGHTS["S2"])
        pipeline.manifests["sparse"] = (None, {"representations": {g: "sparse-" + g for g in ("micro", "macro")}})
        worker = pipeline.worker_factory
        def factory(*args, **kwargs):
            value = worker(*args, **kwargs)
            if value.channel == "sparse": value.ready["representations"] = pipeline.manifests["sparse"][1]["representations"]
            return value
        pipeline.worker_factory = factory
        return pipeline, events

    def test_all_workers_exit_before_next_and_no_s0_sparse_dispatch(self):
        pipeline, events = self.s2_pipeline()
        result = pipeline.run(replace(self.query, combination="S2"))
        self.assertEqual(events, [v + c for c in ("dense", "bm25f", "sparse", "bge") for v in ("open ", "closed ")])
        self.assertTrue(all(r["exit_confirmed"] for r in pipeline.resources))
        self.assertEqual(result["profile"]["combination"], "S2")

    def test_s2_empty_skips_bge(self):
        pipeline, events = self.s2_pipeline(empty=True)
        self.assertEqual(pipeline.run(replace(self.query, combination="S2"))["status"], "empty")
        self.assertNotIn("open bge", events)

    def test_s2_oom_never_falls_back_or_publishes_partial_ranking(self):
        pipeline, events = self.s2_pipeline(oom=True)
        with self.assertRaisesRegex(ValueError, "OOM"): pipeline.run(replace(self.query, combination="S2"))
        self.assertEqual(events[-1], "closed bge")
        self.assertFalse((pipeline.output / "ranked.json").exists())


class SparseScopeTests(unittest.TestCase):
    setUp = controller.ResearchTests.setUp

    def official_settings(self):
        config = read(controller.REPO / "config/clean-generation-v1.json")
        s = {**research.fixture_settings(), "mode": "LIVE", "thinking": "dynamic", "route": config["route"],
             "generation_profile": "gc-g6-official-1", "retrieval_combination": "S0", "roles": config["roles"], "campaign": "g6-fixture",
             "context_tokens": 196608, "timeout_seconds": 300, "worker_timeout_seconds": 1800,
             "history_bytes": 24000, "fee_risk_usd": 5, "tool_error_corrections": 2, "stop_policy": "evidence-value-1"}
        s["limits"] = {"campaign_requests": 20, "campaign_tokens": 20*196608,
                       "turn_requests": 6, "turn_tokens": 6*196608}
        return s

    def test_g6_twenty_request_ceiling_does_not_expand_g5(self):
        s = self.official_settings(); live.validate_settings(s)
        live.validate_settings({**s, "retrieval_combination": "S2"})
        for change in ({"limits": {**s["limits"], "campaign_requests": 21, "campaign_tokens": 21*196608}},
                       {"generation_profile": "gc-g5-official-4"}, {"retrieval_combination": "S1"}):
            with self.subTest(change=change), self.assertRaises(ValueError): live.validate_settings({**s, **change})

    def test_both_combinations_share_exact_permit_and_cannot_expand_other_fields(self):
        s = self.official_settings()
        p = {"version": research.VERSION, "settings": s, "data_root": str(self.store.root),
             "campaign": s["campaign"], "live_enabled": True, "retrieval_combinations": ["S0", "S2"]}
        write(self.store.root / "live/permit.json", p)
        self.assertEqual(live.permit_for(self.store, {**s, "retrieval_combination": "S2"}), p)
        with self.assertRaises(ValueError): live.permit_for(self.store, {**s, "initial_reads": 5})

    def test_controller_inherits_explicit_combination_for_original_and_enhanced_queries(self):
        self.sid = self.store.create_chat("S2 fixture", {**self.settings, "retrieval_combination": "S2"})
        tid = self.store.enqueue(self.sid, "s2-query", "原文条件是什么？")
        from genshin_corpus.rag.clean_mock import MockOpener
        replies = [controller.ToolOpener([controller.search()]),
                   controller.ToolOpener([controller.finish()]), MockOpener("条件仍须核对。[E01]")]
        result = research.run_turn(self.store, tid, self.pipeline, opener_factory=lambda _: replies.pop(0))
        self.assertEqual([q.combination for q in self.pipeline.queries], ["S2", "S2"])
        data = self.store.load(result["research_ref"])
        for ref in data["groups"]:
            ranked = self.store.load(ref)
            self.assertEqual(ranked["profile"]["channel_weights"], WEIGHTS["S2"])
            self.assertTrue(all({h["channel"] for h in c["hits"]} == set(WEIGHTS["S2"]) for c in ranked["candidates"]))

    def test_sparse_manifest_and_independent_validation_are_bound(self):
        from genshin_corpus.evidence_io import sha
        from genshin_corpus.retrieval.clean_based.source import FileBinding
        from genshin_corpus.retrieval.clean_based.sparse_v2 import selected_sparse
        root = self.store.root; sparse = root / "sparse"
        dense = {"input_bundle_id": "fixture", "counts": {"documents": 1}}
        manifest = {**dense, "status": "complete", "scope": "full-selected", "configuration": {},
                    "space_id": digest({}), "binding": "fixture-binding"}
        write(sparse / "manifest.json", manifest)
        report = {"status": "PASS", "counts": dense["counts"], "manifest_sha256": sha(sparse / "manifest.json"),
                  "plan_binding": "fixture-binding"}
        write(sparse / "validation/report.json", report)
        write(sparse / "validation.json", {"folder": "validation", "report_sha256": sha(sparse / "validation/report.json"),
                                           "manifest_sha256": sha(sparse / "manifest.json")})
        config = profile(combination="S2", channel_weights=WEIGHTS["S2"])
        reference = {**config, "channel_weights": {"S2": WEIGHTS["S2"]}, "sparse_root": "sparse",
                     "sparse_manifest_sha256": sha(sparse / "manifest.json")}
        write(root / "selection.json", reference)
        config.update(sparse_selection="selection.json", sparse_selection_sha256=sha(root / "selection.json"),
                      sparse_root="sparse", sparse_manifest_sha256=reference["sparse_manifest_sha256"])
        binding = FileBinding(); self.assertEqual(selected_sparse(root, config, dense, binding)[1], manifest)
        (sparse / "manifest.json").write_text("{}", encoding="utf8")
        with self.assertRaises(ValueError): binding.check()
        with self.assertRaises(ValueError): selected_sparse(root, config, dense, FileBinding())


if __name__ == "__main__": unittest.main()
