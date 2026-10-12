from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.retrieval import candidate_retrieval as lexical
from genshin_corpus.retrieval import retrieval_units as ru
from genshin_corpus.retrieval.evidence_assembly import EvidenceAssemblyConfig, prepare_evidence_assembly_context
from genshin_corpus.retrieval.navi_exploration import (
    ExplorationBudget, ExplorationBudgetExceeded, ExplorationError, LocalExplorationTools,
    response_chars, reviewed_topic_build,
)
from genshin_corpus.retrieval.semantic_compilation import build_views
from genshin_corpus.rag.navi_experiment import run_recorded_exploration, wire_recorded_navigation

FIXTURE = Path(__file__).parents[1] / "fixtures/retrieval/canonical-rag-w1-record.json"


class NaviExplorationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.canonical_path = self.root / "canonical/metadata/manifest.json"
        self.canonical_path.parent.mkdir(parents=True)
        record = json.loads(FIXTURE.read_text(encoding="utf-8"))
        records = []
        self.record_paths = []
        for index in range(2):
            copied = deepcopy(record)
            copied["record_id"] = f"record-{index}"
            copied["source_identity"]["components"]["content_id"] = f"fixture-{index}"
            copied["source_identity"]["key"] = f"mihoyo_obc:zh-cn:fixture-{index}"
            copied["record_metadata"]["name"] = f"Library {index}"
            for section in copied["sections"]:
                for unit in section["units"]:
                    if unit["kind"] == "rich_text":
                        unit["value"]["normalized_text"] = f"Library {index} says: a legend is not established history."
            path = self.root / f"canonical/record-{index}.json"
            body = canonical_json_bytes(copied)
            path.write_bytes(body)
            self.record_paths.append(path)
            records.append({"record_id": copied["record_id"], "canonical_record_path": str(path),
                            "canonical_record_sha256": sha256(body).hexdigest(), "canonical_status": "canonical"})
        manifest = {"status": "complete", "canonical_run_id": "e1-fixture", "source": "mihoyo_obc", "locale": "zh-cn",
                    "input_record_count": 2, "accounted_record_count": 2, "input_integrity_failure_count": 0,
                    "dependencies": {"canonical_versions": record["versions"]}, "records": records}
        self.canonical_path.write_bytes(canonical_json_bytes(manifest))
        ru.build_retrieval_units(self.canonical_path, self.root / "ru", config=ru.RetrievalUnitBuildConfig(text_fragment_chars=1200, structured_fragment_chars=1200))
        self.ru_path = self.root / "ru/metadata/manifest.json"
        _, rows = ru.load_retrieval_units(self.ru_path)
        self.index = {r["unit_id"]: r for r in rows}
        self.rich = [r for r in rows if r["content_type"] == "rich_text"]
        self.uid = self.rich[0]["unit_id"]
        self.other = next(r["unit_id"] for r in self.rich if r["source"]["canonical_address"]["record_id"] == "record-1")
        lexical.build_lexical_index(self.ru_path, self.root / "lexical")
        self.lexical_path = self.root / "lexical/metadata/manifest.json"
        self.tools = LocalExplorationTools(self.canonical_path, self.ru_path, self.lexical_path)
        self.question = "What does the library claim?"

    def run_session(self, decisions=None, budget=None, name="session", action_limit=None):
        return run_recorded_exploration(self.tools, question=self.question, decisions=decisions or [
            {"action": "read", "unit_id": self.uid, "max_chars": 18000},
            {"action": "read", "unit_id": self.other, "max_chars": 18000},
            {"action": "stop", "reason": "recorded complete"},
        ], output_root=self.root / name, budget=budget or ExplorationBudget(max_context_chars=80000), action_limit=action_limit)

    def group(self, ids=None):
        return {"group_id": "g1", "label": "Fixture source reading", "unit_ids": ids or [self.uid, self.other], "topic_path": ["fixture", "literature"]}

    def review(self, session, group):
        # Synthetic review belongs only to this checked-in fixture test.
        return {"group_id": group["group_id"], "group_identity": sha256_json(group),
                "session_identity": session["checkpoint_identity"], "reviewer_kind": "human", "reviewer": "synthetic_fixture_reviewer",
                "decision": "accept_navigation_only", "source_qualification": "fixture_only",
                "rationale": "fixture text grouping only", "narrative_caveat": "Claims are attributed, no historical facts inferred"}

    def test_lexical_scores_match_existing_arm(self):
        hits = self.tools.search("Library legend", top_k=3)["hits"]
        expected = lexical.lexical_candidates(self.lexical_path, "Library legend", top_k=3)
        self.assertEqual([h["unit_id"] for h in hits], [e["unit_id"] for e in expected])
        self.assertEqual([h["retrieval"] for h in hits], [e["retrieval"] for e in expected])

    def test_field_aware_scores_match_existing_arm(self):
        lexical.build_field_aware_lexical_index(self.ru_path, self.root / "field")
        path = self.root / "field/metadata/manifest.json"
        tools = LocalExplorationTools(self.canonical_path, self.ru_path, path)
        hits = tools.search("Library legend", top_k=3)["hits"]
        self.assertEqual([h["retrieval"] for h in hits], [h["retrieval"] for h in lexical.lexical_candidates(path, "Library legend", top_k=3)])

    def test_full_source_read_and_visible_truncation(self):
        full = self.tools.read(self.uid, max_chars=18000, neighbors=1)
        self.assertFalse(full["partial"])
        self.assertIn("units", json.loads(full["canonical_payload"]))
        partial = self.tools.read(self.uid, max_chars=2200)
        self.assertTrue(partial["partial"])
        self.assertGreater(partial["omitted_chars"], 0)
        self.assertLessEqual(response_chars(partial), 2200)

    def test_tiny_metadata_budget_rejected(self):
        with self.assertRaises(ExplorationBudgetExceeded):
            self.tools.read(self.uid, max_chars=10)

    def test_unknown_ru_rejected(self):
        with self.assertRaises(ExplorationError):
            self.tools.read("absent", max_chars=10000)

    def test_canonical_mutation_rejected(self):
        self.record_paths[0].write_text("{}", encoding="utf-8")
        with self.assertRaises(ru.RetrievalUnitError):
            self.tools.read(self.uid, max_chars=10000)

    def test_ru_projection_mismatch_rejected(self):
        row = deepcopy(self.index[self.uid]); row["source"]["canonical_address"]["section_ordinal"] = 99
        self.tools._ru_cache[self.uid] = row
        with self.assertRaisesRegex(ExplorationError, "projection"):
            self.tools.read(self.uid, max_chars=10000)

    def test_manifest_mutation_rejected(self):
        self.ru_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ExplorationError, "manifest changed"):
            self.tools.search("Library", top_k=1)

    def test_no_network_and_hypothesis_stays_separate(self):
        decisions = [{"action": "read", "unit_id": self.uid, "max_chars": 18000},
                     {"action": "clue", "text": "possible cross-source clue", "source_unit_ids": [self.uid]},
                     {"action": "stop", "reason": "done"}]
        with patch("socket.socket.connect", side_effect=AssertionError("network prohibited")):
            session = self.run_session(decisions)
        self.assertEqual(session["provider_calls"], 0)
        self.assertEqual(session["pending_clues"][0]["status"], "unverified_hypothesis_not_fact")
        self.assertEqual(session["read_sources"][0]["semantic_acceptance_status"], "not_accepted")

    def test_resume_does_not_repeat_completed_action(self):
        decisions = [{"action": "search", "query": "Library legend", "top_k": 2},
                     {"action": "read", "hit": {"round": 0, "index": 0}, "max_chars": 18000},
                     {"action": "stop", "reason": "done"}]
        first = self.run_session(decisions, action_limit=1)
        self.assertEqual(first["status"], "running")
        with patch.object(self.tools, "search", side_effect=AssertionError("search repeated")):
            second = self.run_session(decisions)
        self.assertEqual(second["status"], "stopped")
        with patch.object(self.tools, "read", side_effect=AssertionError("read repeated")):
            self.assertEqual(second, self.run_session(decisions))

    def test_changed_replay_rejected(self):
        self.run_session(action_limit=1)
        with self.assertRaisesRegex(ExplorationError, "identity"):
            self.run_session([{ "action": "stop", "reason": "different"}])

    def test_tampered_checkpoint_rejected(self):
        self.run_session()
        path = self.root / "session/session.json"; data = json.loads(path.read_bytes()); data["provider_calls"] = 1
        path.write_bytes(canonical_json_bytes(data))
        with self.assertRaisesRegex(ExplorationError, "integrity"):
            self.run_session()

    def test_round_budget_stops(self):
        session = self.run_session(budget=ExplorationBudget(max_rounds=1, max_context_chars=80000))
        self.assertEqual(session["status"], "budget_exhausted")
        self.assertEqual(len(session["events"]), 1)

    def test_search_read_and_context_budgets(self):
        session = self.run_session([{ "action": "search", "query": "Library", "top_k": 1}]*2,
                                   ExplorationBudget(max_searches=1), name="search-limit")
        self.assertEqual(session["status"], "budget_exhausted")
        session = self.run_session(budget=ExplorationBudget(max_reads=1, max_context_chars=80000), name="read-limit")
        self.assertEqual(session["status"], "budget_exhausted")
        session = self.run_session(budget=ExplorationBudget(max_context_chars=10), name="context-limit")
        self.assertEqual(session["status"], "budget_exhausted")
        self.assertLessEqual(session["budget_used"]["context_chars"], 10)

    def test_invalid_action_or_candidate_budget_rejected(self):
        with self.assertRaises(ExplorationError):
            self.run_session([{ "action": "provider_call"}])
        with self.assertRaises(ExplorationError):
            self.run_session([{ "action": "search", "query": "Library", "top_k": 11}])

    def test_unreviewed_topics_inactive_and_no_graph_edges(self):
        session = self.run_session(); group = self.group()
        build = reviewed_topic_build(session, [group], [], self.index)
        self.assertEqual(len(build.active_items), 0)
        self.assertEqual(len(build.inactive_ledger), 1)
        self.assertEqual(build_views(build).graph.edges, ())

    def test_review_identity_and_partial_read_fail_closed(self):
        session = self.run_session(); group = self.group(); review = self.review(session, group)
        bad = {**review, "session_identity": "wrong"}
        with self.assertRaisesRegex(ExplorationError, "review binding"):
            reviewed_topic_build(session, [group], [bad], self.index)
        partial = self.run_session([{ "action": "read", "unit_id": self.uid, "max_chars": 2200}, {"action": "stop", "reason": "done"}], name="partial")
        group = self.group([self.uid])
        with self.assertRaisesRegex(ExplorationError, "partial"):
            reviewed_topic_build(partial, [group], [self.review(partial, group)], self.index)

    def test_fixture_opt_in_cannot_be_curated(self):
        session = self.run_session(); group = self.group(); review = self.review(session, group)
        review["reviewer_kind"] = "recorded_mechanical_fixture"
        with self.assertRaisesRegex(ExplorationError, "opt-in"):
            reviewed_topic_build(session, [group], [review], self.index)
        build = reviewed_topic_build(session, [group], [review], self.index, allow_mechanical_fixture=True)
        self.assertEqual(build.active_items[0].semantic_acceptance_status, "accepted_for_fixture_test")

    def test_explicit_human_review_projection(self):
        session = self.run_session(); group = self.group(); review = self.review(session, group)
        review["source_qualification"] = "official_game_text_reviewed"
        build = reviewed_topic_build(session, [group], [review], self.index)
        self.assertEqual(build.active_items[0].semantic_acceptance_status, "curated_recorded")
        self.assertEqual(build.active_items[0].item_kind, "topic")
        self.assertEqual(build_views(build).graph.edges, ())

    def test_structural_dialogue_context_retained_without_branch_inference(self):
        row = next(r for r in self.index.values() if r["content_type"] == "dialogue_node")
        receipt = self.tools.read(row["unit_id"], max_chars=20000)
        self.assertFalse(receipt["partial"])
        payload = json.loads(receipt["canonical_payload"])
        self.assertTrue(any("groups" in u["value"] for u in payload["units"]))
        self.assertIn("not_dialogue_branch_inference", receipt["context_scope"])

    def test_unknown_source_qualification_rejected(self):
        session = self.run_session(); group = self.group(); review = self.review(session, group)
        review["source_qualification"] = "model_says_official"
        with self.assertRaisesRegex(ExplorationError, "qualification"):
            reviewed_topic_build(session, [group], [review], self.index)

    def test_scoped_companions_are_authoritative_for_p05(self):
        session = self.run_session(); group = self.group()
        views = build_views(reviewed_topic_build(session, [group], [self.review(session, group)], self.index))
        context = prepare_evidence_assembly_context(self.ru_path, selected_record_ids=["record-0", "record-1"])
        result = wire_recorded_navigation(question=self.question, session=session, views=views, ru_index=self.index,
                                         context=context, baseline_candidates=[{"unit_id": self.uid, "rank": 1}],
                                         hierarchy_seeds=[n.node_id for n in views.hierarchy.nodes.values() if n.path == ("fixture", "literature")])
        self.assertEqual(result["navigation"]["status"], "succeeded")


    def wiring(self, config=None, only_baseline=False):
        session = self.run_session(); group = self.group([self.uid] if only_baseline else None)
        build = reviewed_topic_build(session, [group], [self.review(session, group)], self.index)
        views = build_views(build)
        context = prepare_evidence_assembly_context(self.ru_path, selected_record_ids=["record-0", "record-1"])
        return wire_recorded_navigation(question=self.question, session=session, views=views, ru_index=self.index, context=context,
                                        baseline_candidates=[{"unit_id": self.uid, "rank": 1}],
                                        hierarchy_seeds=[n.node_id for n in views.hierarchy.nodes.values() if n.path == ("fixture", "literature")],
                                        assembly_config=config or EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, dialogue_hops=0))

    def test_real_deferred_assembly_wiring_fixture_and_scope(self):
        result = self.wiring()
        self.assertEqual(result["navigation"]["status"], "succeeded")
        self.assertIn(self.other, result["audit"]["novel_navigation_packet_visible_unit_ids"])
        self.assertEqual(result["assembly_read_scope"]["record_ids"], ["record-0", "record-1"])

    def test_baseline_only_and_omitted_are_not_gain(self):
        result = self.wiring(only_baseline=True)
        self.assertNotEqual(result["navigation"]["status"], "succeeded")
        self.assertEqual(result["audit"]["novel_navigation_packet_visible_unit_ids"], [])
        result = self.wiring(EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, dialogue_hops=0, total_context_chars=55, per_block_chars=55))
        self.assertEqual(result["audit"]["novel_navigation_packet_visible_unit_ids"], [])

    def test_scoped_loader_unknown_and_duplicate_selection(self):
        _, rows = ru.load_retrieval_units(self.ru_path, selected_unit_ids=[self.uid, self.uid])
        self.assertEqual(len(rows), 1)
        with self.assertRaises(ru.RetrievalUnitError):
            ru.load_retrieval_units(self.ru_path, selected_record_ids=["missing"])


if __name__ == "__main__":
    unittest.main()
