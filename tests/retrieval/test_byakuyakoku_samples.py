"""Unit tests for Byakuyakoku local deterministic samples, KG compilation, and Navi continuity."""

from pathlib import Path
import tempfile
import unittest

from genshin_corpus.retrieval.byakuyakoku_samples import (
    CORE_CONTENT_IDS,
    build_byakuyakoku_kg_items,
    build_byakuyakoku_navi_state,
    build_e2_baseline_findings,
    build_official_source_excerpts,
    extract_e2_read_slices,
    extract_full_retrieval_units,
    extract_full_sections,
    materialize_all,
)
from genshin_corpus.retrieval.semantic_compilation import (
    SemanticBuild,
    SemanticStage,
    TraversalPolicy,
    build_views,
    load_semantic_build,
    traverse_graph,
)


E2_SESSION_PATH = Path("data/retrieval/p05-navi-e2-deepseek-jizhi-research-20261019-103633-r1/session.json")
RU_MANIFEST_PATH = Path("data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json")
CANONICAL_MANIFEST_PATH = Path("data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/metadata/manifest.json")


class TestByakuyakokuSamples(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.slices = extract_e2_read_slices(E2_SESSION_PATH)
        cls.target_uids = [s["unit_id"] for s in cls.slices]
        cls.full_rus = extract_full_retrieval_units(RU_MANIFEST_PATH, cls.target_uids)
        cls.ru_index = {r["unit_id"]: r for r in cls.full_rus}
        cls.sections = extract_full_sections(CANONICAL_MANIFEST_PATH)

    def test_extract_e2_read_slices(self) -> None:
        self.assertEqual(len(self.slices), 16)
        content_ids = {s["content_id"] for s in self.slices}
        self.assertEqual(content_ids, set(CORE_CONTENT_IDS))
        for s in self.slices:
            self.assertTrue(s["unit_id"])
            self.assertTrue(s["e2_ru_text"])
            self.assertGreater(s["e2_read_count"], 0)
            self.assertIn("canonical_address", s)
            self.assertIn("lineage", s)

    def test_extract_full_retrieval_units(self) -> None:
        self.assertEqual(len(self.full_rus), 16)
        for uid in self.target_uids:
            self.assertIn(uid, self.ru_index)
            self.assertGreater(len(self.ru_index[uid]["retrieval_visible_text"]), 0)
            self.assertIn("source", self.ru_index[uid])

    def test_extract_full_sections(self) -> None:
        self.assertEqual(len(self.sections), 12)
        cids = {s["content_id"] for s in self.sections}
        self.assertEqual(cids, set(CORE_CONTENT_IDS))
        for sec in self.sections:
            self.assertTrue(sec["full_text"])
            self.assertGreater(sec["units_count"], 0)
            self.assertGreater(sec["char_count"], 0)

    def test_kg_items_compilation_and_views(self) -> None:
        items = build_byakuyakoku_kg_items(self.full_rus)
        self.assertEqual(len(items), 18)
        for item in items:
            self.assertIn("source_genre", item.qualifiers)
            self.assertIn("epistemic", item.qualifiers)

        # Requirement 1: Verify correct participant roles
        item_ids = {it.item_id: it for it in items}
        self.assertNotIn("event:theft_before_sun_and_moon", item_ids)
        self.assertIn("event:ema_commissions_retrieval", item_ids)
        ema_event = item_ids["event:ema_commissions_retrieval"]
        self.assertEqual(ema_event.participants, ("绘真",))
        self.assertEqual(ema_event.event_type, "通报与委托")
        self.assertIn("失窃", ema_event.label)

        # Verify Vishap does not participate in island creation
        watatsumi_event = item_ids["event:create_watatsumi_island"]
        self.assertEqual(watatsumi_event.participants, ("奥罗巴斯",))

        stage = SemanticStage("stage:test", "kg-test", "sample", "complete")
        build = SemanticBuild.from_items(
            input_identity="byakuyakoku-kg-test",
            stages=[stage],
            items=items,
            ru_index=self.ru_index,
        )
        self.assertEqual(len(build.active_items), len(items))
        self.assertEqual(len(build.inactive_ledger), 0)

        views = build_views(build)
        self.assertGreaterEqual(len(views.graph.nodes), 8)
        self.assertGreaterEqual(len(views.graph.edges), 4)
        self.assertGreaterEqual(len(views.hierarchy.nodes), 5)

        # Check graph participant edges: Ema reports theft, does not execute theft
        edges = views.graph.edges
        ema_edges = [e for e in edges if e.source == "绘真"]
        self.assertEqual(len(ema_edges), 1)
        self.assertEqual(ema_edges[0].predicate, "participates_in")
        self.assertEqual(ema_edges[0].target, "event:event:ema_commissions_retrieval")

        # Check graph participant edges: Vishap does not participate in island creation
        vishap_island_edges = [e for e in edges if e.source == "深海龙蜥" and "create_watatsumi_island" in e.target]
        self.assertEqual(len(vishap_island_edges), 0)

        # Verify graph traversal
        trace = traverse_graph(
            views.graph,
            start_nodes=["奥罗巴斯"],
            route_id="route:orobashi_test",
            policy=TraversalPolicy(max_hops=2, max_nodes=10, max_edges=10, max_candidates=10),
        )
        self.assertIn("奥罗巴斯", trace.visited_nodes)
        self.assertGreater(len(trace.visited_nodes), 1)
        self.assertGreater(len(trace.selected_unit_ids), 0)

    def test_navi_continuity_state(self) -> None:
        state = build_byakuyakoku_navi_state(self.full_rus, scope_level="full_section")

        self.assertEqual(state.step, 7)
        self.assertEqual(len(state.source_ledger), 10)
        self.assertEqual(len(state.clue_events), 9)
        for entry in state.source_ledger:
            self.assertEqual(entry.scope_level, "full_section")

        active = state.derive_active_clues()
        self.assertEqual(len(active), 9)

        # Check that contrast operations preserved both clues (both in active set)
        c_novel = next(c for c in active if c.clue_id == "clue:3226:002")
        c_divine = next(c for c in active if c.clue_id == "clue:3611:001")
        self.assertEqual(c_novel.status, "contrasted")
        self.assertEqual(c_divine.status, "contrasted")
        self.assertTrue(c_novel.uncertainty_note)

        # Scope level 'slice' variant: Requirement 2 no material overstepping
        state_slice = build_byakuyakoku_navi_state(self.full_rus, scope_level="slice")
        for entry in state_slice.source_ledger:
            self.assertEqual(entry.scope_level, "slice")

        active_slice = state_slice.derive_active_clues()
        c7_slice = next(c for c in active_slice if c.clue_id == "clue:3227:001")
        c7_full = next(c for c in active if c.clue_id == "clue:3227:001")

        # Slice version must NOT contain text from omitted section parts (sacrifice conjecture)
        self.assertNotIn("牺牲", c7_slice.text)
        self.assertNotIn("折珊瑚枝救民登天", c7_slice.text)
        self.assertIn("另一个版本", c7_slice.text)
        self.assertIn("封存于渊下宫", c7_slice.text)

        # Full section version contains the full comparative analysis
        self.assertIn("牺牲", c7_full.text)
        self.assertIn("折珊瑚枝救民登天", c7_full.text)

        # Requirement 3: Explicit labeling in summary
        self.assertIn("本地确定性整理样例", state.research_summary)
        self.assertIn("本地确定性整理样例", state_slice.research_summary)

        # Reference validation against target UIDs
        state.validate_references(known_unit_ids=set(self.target_uids))
        state_slice.validate_references(known_unit_ids=set(self.target_uids))

    def test_e2_baseline_and_excerpts(self) -> None:
        baseline = build_e2_baseline_findings(E2_SESSION_PATH)
        self.assertEqual(len(baseline["verbatim_e2_clues"]), 4)
        self.assertTrue(baseline["verbatim_e2_final_memory"]["text"])

        excerpts = build_official_source_excerpts(self.full_rus, self.sections)
        self.assertEqual(len(excerpts["pillars"]), 6)

        # Rigorous check: Every quote must be an exact literal substring of the source RU text!
        all_ru_text = {r["unit_id"]: r["retrieval_visible_text"] for r in self.full_rus}
        all_sec_text = {s["section_id"]: s["full_text"] for s in self.sections}

        for p_name, pillar in excerpts["pillars"].items():
            p_rus = [all_ru_text[uid] for uid in pillar["primary_ru_ids"] if uid in all_ru_text]
            p_secs = [all_sec_text[sid] for sid in pillar["primary_section_ids"] if sid in all_sec_text]
            corpus = "\n".join(p_rus + p_secs)
            for q in pillar["official_quotes"]:
                q_text = q["text"]
                self.assertIn(
                    q_text,
                    corpus,
                    f"Quote in {p_name} is not a verbatim substring of source material:\n{q_text}",
                )

    def test_materialize_all_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "byakuyakoku-samples"
            result = materialize_all(
                e2_session_path=E2_SESSION_PATH,
                ru_manifest_path=RU_MANIFEST_PATH,
                canonical_manifest_path=CANONICAL_MANIFEST_PATH,
                output_dir=out_path,
            )
            self.assertTrue(out_path.exists())
            self.assertTrue((out_path / "e2_read_slices.json").exists())
            self.assertTrue((out_path / "full_retrieval_units.json").exists())
            self.assertTrue((out_path / "full_sections.json").exists())
            self.assertTrue((out_path / "e2_baseline_findings.json").exists())
            self.assertTrue((out_path / "official_source_excerpts.json").exists())
            self.assertTrue((out_path / "navi_continuity_state.json").exists())
            self.assertTrue((out_path / "navi_continuity_slice_state.json").exists())
            self.assertTrue((out_path / "sample_manifest.json").exists())
            self.assertTrue((out_path / "kg_build" / "manifest.json").exists())
            self.assertTrue((out_path / "kg_build" / "semantic_build.json").exists())
            self.assertTrue((out_path / "kg_build" / "graph.json").exists())
            self.assertTrue((out_path / "kg_build" / "hierarchy.json").exists())

            loaded_kg = load_semantic_build(out_path / "kg_build")
            self.assertEqual(
                loaded_kg["manifest"]["counts"]["active_items"],
                result["manifest"]["kg_build"]["active_items"],
            )
            self.assertEqual(result["manifest"]["sample_nature"], "local_deterministic_curated_sample")
            self.assertIn("methodology_disclaimer", result["manifest"])
            self.assertEqual(result["manifest"]["kg_build"]["sample_nature"], "local_deterministic_curated_sample")
            self.assertEqual(result["manifest"]["navi_continuity"]["sample_nature"], "local_deterministic_curated_sample")


if __name__ == "__main__":
    unittest.main()
