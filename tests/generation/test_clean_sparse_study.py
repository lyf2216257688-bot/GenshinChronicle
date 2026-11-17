"""Small source fixtures for the offline G6 candidate/admission diagnostic."""
from copy import deepcopy
from dataclasses import replace
import unittest

from genshin_corpus.evidence_io import digest
from genshin_corpus.generation.clean_evidence import EvidenceReader, assemble
from genshin_corpus.rag.clean_sparse_study import supplement, protected_assembly, envelope_assembly
from genshin_corpus.retrieval.clean_based.query_v2 import fuse_v2
from tests.generation import test_clean_evidence as evidence_fixture
from tests.retrieval.test_clean_based import profile


class SparseStudyTests(unittest.TestCase):
    def setUp(self):
        self.fx = evidence_fixture.EvidenceTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        docs = list(self.fx.docs.values())
        self.assertGreaterEqual(len(docs), 2)
        hits = []
        for channel, doc in zip(("dense", "sparse"), docs):
            hits.append({"question_id": "turn", "query_id": "original", "round": 0, "kind": "original",
                         "channel": channel, "granularity": doc["row"]["granularities"][0], "rank": 1,
                         "raw_score": 1., "representation_id": "fixture-" + channel,
                         "input_row": doc["input_row"], "chunk_id": doc["chunk"]["chunk_id"]})
        self.base = fuse_v2(self.fx.query, hits[:1], self.fx.docs, profile(),
                            input_bundle_id=self.fx.reader.bundle_id, code={})
        s2 = {**profile(), "combination": "S2", "channel_weights": {"bm25f": .25, "dense": .5, "sparse": .25}}
        self.donor = fuse_v2(replace(self.fx.query, combination="S2"), hits, self.fx.docs, s2,
                             input_bundle_id=self.fx.reader.bundle_id, code={})
        self.bs, self.ds = self.fx.scores(self.base), self.fx.scores(self.donor)
        for c in self.donor["candidates"]:
            cid = c["chunk_id"]
            self.ds["scores"][cid] = self.bs["scores"].get(cid, 100.)

    def build(self):
        return supplement(self.base, self.donor, self.bs, self.ds, self.fx.selection)

    def test_preserves_whole_S0_values_without_refusion_or_candidate_recut(self):
        before = deepcopy(self.base)
        pool, ranked, _ = self.build()
        self.assertEqual(before, self.base)
        self.assertEqual(pool["candidates"][:1], self.base["candidates"])
        self.assertEqual(len(ranked["candidates"]), 2)
        self.assertEqual(ranked["candidates"][0]["reranking"]["logit"], 100.)
        self.assertNotIn("fusion_score", pool["candidates"][1])
        self.assertNotIn("profile", pool)  # Not a mislabeled frozen V2 operating point.
        self.assertEqual(pool["study"]["new_inference_pairs"], 0)

    def test_raw_window_overlap_is_not_a_sparse_discovery(self):
        # Even a candidate outside the final S0 set remains part of raw0.
        shared = deepcopy(self.donor["raw_hits"][-1]); shared["channel"] = "dense"
        self.base["raw_hits"].append(shared)
        self.donor["raw_hits"].append(deepcopy(shared))
        # Candidate-set digests legitimately change; the exact scoring cache is
        # rebound only in this synthetic fixture, never in the real study.
        from genshin_corpus.retrieval.clean_based.rerank_v2 import scoring_input
        self.bs["candidate_set_sha256"] = digest(scoring_input(self.base, self.fx.selection))
        self.ds["candidate_set_sha256"] = digest(scoring_input(self.donor, self.fx.selection))
        pool, _, _ = self.build()
        self.assertEqual(pool["study"]["added_ids"], [])

    def test_foreign_query_incomplete_audit_and_conflicting_score_fail_closed(self):
        for kind in ("query", "audit", "score"):
            donor, ds = deepcopy(self.donor), deepcopy(self.ds)
            if kind == "query": donor["query"]["rerank_text"] = "another question"
            if kind == "audit": ds["audit"].pop()
            if kind == "score": ds["scores"][self.base["candidates"][0]["chunk_id"]] += 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                supplement(self.base, donor, self.bs, ds, self.fx.selection)

    def test_protection_retains_content_and_joint_order_under_finite_budget(self):
        _, ranked, baseline = self.build()
        er = EvidenceReader(self.fx.reader)
        receipts = [er.read(h) for h in er.register(ranked).values()]
        base_ids = {c["chunk_id"] for c in baseline["candidates"]}
        base_reads = [r for r in receipts if r["chunk_id"] in base_ids]
        context, full = evidence_fixture.CONTEXT, evidence_fixture.BUDGET
        bp = assemble([baseline], base_reads, context=context, budget=full)
        budget = {**full, "context_tokens": bp["estimated_message_tokens"] + bp["reserved_tokens"]}
        bp = assemble([baseline], base_reads, context=context, budget=budget)
        packet, audit = protected_assembly(ranked, receipts, baseline, bp, context=context, budget=budget)
        self.assertTrue(audit["protected_content_retained"])
        self.assertTrue({e["fragment_id"] for e in bp["evidence"]} <= {e["fragment_id"] for e in packet["evidence"]})
        self.assertLessEqual(packet["estimated_message_tokens"] + packet["reserved_tokens"], budget["context_tokens"])
        self.assertEqual(len(packet["dispositions"]), len(ranked["candidates"]))
        self.assertEqual((packet, audit), protected_assembly(ranked, receipts, baseline, bp, context=context, budget=budget))
        with self.assertRaises(ValueError):
            protected_assembly(ranked, receipts, baseline, bp, context={**context, "question": "changed"}, budget=budget)

    def test_envelope_shrinks_admission_without_losing_protected_content(self):
        _, ranked, baseline = self.build()
        er = EvidenceReader(self.fx.reader)
        receipts = [er.read(h) for h in er.register(ranked).values()]
        ids = {c["chunk_id"] for c in baseline["candidates"]}
        rs = [r for r in receipts if r["chunk_id"] in ids]
        context, budget = evidence_fixture.CONTEXT, evidence_fixture.BUDGET
        bp = assemble([baseline], rs, context=context, budget=budget)
        checks = []
        def check(packet):
            checks.append(packet["packet_id"])
            if len(checks) == 1: raise ValueError("WHOLE_MESSAGE_BUDGET_EXCEEDED")
            return {"wire_guard": "PASS"}
        p, a = envelope_assembly(ranked, receipts, context=context, budget=budget, check=check,
                                 baseline_ranked=baseline, baseline_packet=bp)
        self.assertEqual(a["effective_budget"]["context_tokens"], budget["context_tokens"] - 2048)
        self.assertEqual(len(a["trials"]), 2)
        self.assertTrue({e["fragment_id"] for e in bp["evidence"]} <= {e["fragment_id"] for e in p["evidence"]})

    def test_unrelated_envelope_failure_never_retries(self):
        _, ranked, _ = self.build()
        calls = []
        def check(packet):
            calls.append(packet)
            raise ValueError("source fault")
        with self.assertRaisesRegex(ValueError, "source fault"):
            envelope_assembly(ranked, [], context=evidence_fixture.CONTEXT, budget=evidence_fixture.BUDGET, check=check)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
