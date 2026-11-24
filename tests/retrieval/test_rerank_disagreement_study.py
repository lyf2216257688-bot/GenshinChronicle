"""Small fixtures for the isolated ordering experiment."""

import importlib.util
from pathlib import Path
import unittest

from genshin_corpus.evidence_io import digest
from genshin_corpus.retrieval.clean_based.query_v2 import ResearchQuery


POLICY_PATH = Path(__file__).resolve().parents[2] / "scripts/retrieval/rerank_disagreement_policy.py"
spec = importlib.util.spec_from_file_location("rerank_disagreement_policy", POLICY_PATH)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def candidate(number, *, section="剧情", text="一般背景", organized=None, relevance=None, covered=None):
    chunk = str(number)
    context = {"label_evidence": {"document_title": {"text": "甲"}, "section_title": {"text": section}},
               "group_title": {"text": "记述"}}
    return {"chunk_id": chunk, "input_row": number, "source": {"source_context": context},
            "chunk": {"observation_id": chunk, "parts": [{"text": text, "clean_ref": {"block_id": chunk}}]},
            "hits": [{"channel": "bm25f", "rank": 8}, {"channel": "dense", "rank": 15}],
            "organized_rank": organized if organized is not None else number,
            "reranking": {"final_rank": number, "relevance_rank": relevance if relevance is not None else number,
                          "logit": -0.1 * number, "covered_by": covered}}


def ranked(sixth, *late):
    question = "甲早年的来历是什么？"
    query = ResearchQuery("fixture", "fixture", "original", *([question] * 4))
    return {"version": "gc-clean-ranked-candidates-2", "query": query.value(), "policy_id": "fixture",
            "reranking_run": {"profile": "bge_structural"}, "candidates": [*(candidate(i) for i in range(1, 6)), sixth, *late]}


class DisagreementPolicyTests(unittest.TestCase):
    def test_scope_guard_preserves_raw_score_and_other_candidates(self):
        proposed = candidate(8, text="早年甲是北地来客。", organized=1, relevance=30)
        original = ranked(candidate(6, section="天赋"), candidate(7), proposed)
        before = digest(original)
        group, trace = policy.apply_policy(original, "guarded_disagreement")
        self.assertEqual([r["chunk_id"] for r in group["candidates"]], ["1", "2", "3", "4", "5", "8", "6", "7"])
        self.assertEqual((trace["promoted"], trace["displaced"]), ("8", "6"))
        self.assertEqual(group["candidates"][5]["reranking"],
                         {**proposed["reranking"], "structural_rank": 8, "final_rank": 6})
        self.assertEqual(digest(original), before)
        self.assertEqual(group["study_ordering"]["baseline_group_digest"], before)
        self.assertEqual(policy.apply_policy(original, "guarded_disagreement"), (group, trace))

    def test_abstention_does_not_change_ranked_group(self):
        cases = [
            ("剧情", "早年甲是北地来客。", None, "cannot_justify_displacing_original_sixth"),
            ("天赋", "有人猜测早年甲是北地来客。", None, "no_literal_scope_witness"),
            ("天赋", "早年甲是北地来客。", "2", "no_literal_scope_witness"),
        ]
        for sixth, text, covered, reason in cases:
            with self.subTest(text=text, reason=reason):
                original = ranked(candidate(6, section=sixth),
                                  candidate(7, text=text, organized=1, relevance=30, covered=covered))
                group, trace = policy.apply_policy(original, "guarded_disagreement")
                self.assertIs(group, original)
                self.assertEqual(trace["reason"], reason)

    def test_same_channel_at_two_granularities_cannot_supply_consensus(self):
        proposed = candidate(7, text="早年甲是北地来客。", organized=1, relevance=30)
        proposed["hits"] = [{"channel": "bm25f", "rank": 4, "granularity": "micro"},
                            {"channel": "bm25f", "rank": 5, "granularity": "macro"}]
        original = ranked(candidate(6), proposed)
        self.assertIs(policy.apply_policy(original, "disagreement_only")[0], original)

    def test_rejects_invalid_group_order(self):
        original = ranked(candidate(6), candidate(8))
        with self.assertRaisesRegex(ValueError, "Invalid baseline ordering"):
            policy.apply_policy(original, "baseline")
