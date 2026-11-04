from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from genshin_corpus.generation.generation import project_generation_request
from genshin_corpus.rag.assembly_70q import tally_review, verify_challenger, write_json, write_jsonl, _case
from genshin_corpus.retrieval.evidence_assembly import (
    EvidenceAssemblyConfig, assemble_deferred_footprint_charge_packet,
    evidence_packet_json_bytes, prepare_evidence_assembly_context, render_evidence_packet,
)
from genshin_corpus.retrieval.evidence_compaction import assemble_retained_evidence_packet
from genshin_corpus.retrieval.evidence_projection import apply_display_edits, project_structured_unit
from genshin_corpus.retrieval.retrieval_units import RetrievalUnitBuildConfig, _structured_fragments
from tests.retrieval import test_rag_w1 as fixture_module


def structured_units(decoded, limit=1200):
    return [{"unit_id": f"unit-{index}", "content_type": "structured", "retrieval_visible_text": text,
             "nested_selector": nested, "fragment_selector": fragment,
             "source": {"canonical_address": {"record_id": "fixture", "unit": 0}}}
            for index, (text, nested, fragment, _) in enumerate(_structured_fragments(decoded, limit))]


class ProjectionTests(unittest.TestCase):
    def project(self, decoded):
        unit = structured_units(decoded)[0]
        result = project_structured_unit(unit)
        self.assertEqual(apply_display_edits(unit["retrieval_visible_text"], result["edits"]), result["text"])
        return result

    def test_layout_only_preserves_paths_words_entities_and_newlines(self):
        result = self.project({"rich_text": '<p style="text-align: left;">甲&amp;乙<br>丙<span>丁</span></p>'})
        self.assertEqual(result["text"], "/rich_text\t\n甲&amp;乙\n丙丁\n")
        self.assertEqual(result["status"], "projected")

    def test_unknown_attributes_media_links_color_and_entries_stay_exact(self):
        text = ('<p><span style="color: red">红</span><a href="https://a">链接</a>'
                '<img src="https://b" alt="图示"><span data-entry-name="名字">条目</span></p>')
        result = self.project({"content": text, "icon": "https://b", "color": "#ffffff"})
        for retained in ('<span style="color: red">红</span>', '<a href="https://a">链接</a>',
                         '<img src="https://b" alt="图示">', '<span data-entry-name="名字">条目</span>',
                         "/icon\thttps://b", "/color\t#ffffff"):
            self.assertIn(retained, result["text"])

    def test_unbalanced_html_ambiguous_paths_and_css_remain_original(self):
        for decoded in ({"a": "<p>未闭合"}, {"a": '<span style="text-align:var(--x)">甲</span>'},
                        {"a": '<p style="unknown:value">甲</p>'},
                        {"a": "<p>x</p>\n/b\tspoof", "b": "real"}):
            with self.subTest(decoded=decoded):
                unit = structured_units(decoded)[0]
                self.assertEqual(project_structured_unit(unit)["text"], unit["retrieval_visible_text"])

    def test_tag_spanning_fragments_uses_only_each_ru_original_ranges(self):
        units = structured_units({"rich_text": '前文<p style="text-align: left;">甲乙丙丁</p>后文'}, limit=26)
        results = [project_structured_unit(unit, fragment_chain=units) for unit in units]
        for unit, result in zip(units, results):
            self.assertEqual(apply_display_edits(unit["retrieval_visible_text"], result["edits"]), result["text"])
            for edit in result["edits"]:
                self.assertLessEqual(edit["end"], len(unit["retrieval_visible_text"]))
        self.assertTrue(any(result["status"] == "projected" for result in results))
        self.assertIn("甲乙", "".join(result["text"] for result in results))
        self.assertTrue(any(len(context["unit_ids"]) > 1 for result in results
                            for context in result.get("scalar_contexts", [])))

    def test_missing_or_tampered_fragment_chain_fails_closed(self):
        units = structured_units({"rich_text": '<p style="text-align: left;">甲乙丙丁</p>'}, limit=12)
        for chain in (units[:-1], [dict(units[0], retrieval_visible_text="bad")] + units[1:]):
            result = project_structured_unit(units[1], fragment_chain=chain)
            self.assertEqual(result["status"], "preserved_uncertain")
            self.assertEqual(result["text"], units[1]["retrieval_visible_text"])

    def test_empty_fragment_preserves_entire_scalar_consistently(self):
        units = structured_units({"rich_text": '<p style="text-align: left;">甲乙丙丁</p>'}, limit=12)
        for unit in units:
            result = project_structured_unit(unit, fragment_chain=units)
            self.assertEqual(result["status"], "preserved_uncertain")
            self.assertEqual(result["text"], unit["retrieval_visible_text"])

    def test_bad_display_range_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_display_edits("甲乙", [{"start": 0, "end": 1, "source_text": "丙", "replacement": ""}])


class RetentionTests(unittest.TestCase):
    def test_backend_defaults_to_existing_combined_and_formal_replays_control(self):
        from dataclasses import replace
        from unittest.mock import patch
        from tests.retrieval import test_single_question_backend as backend_fixture
        from genshin_corpus.rag.backend import SingleQuestionBackendConfig, run_single_question
        fixture = backend_fixture.SingleQuestionBackendTests()
        fixture.setUp()
        candidates = self.candidates([self.rich, self.dialogue])
        fixture.retriever.candidates_for_query = lambda query, vector, **kwargs: dict.fromkeys(
            ("lexical", "dense", "hybrid"), candidates)
        state = replace(fixture.prepared, retrieval_unit_manifest_path=self.manifest, assembly_context=self.context)
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, dialogue_hops=0,
                                        per_block_chars=1000, total_context_chars=22)
        for backend in ("combined", "formal"):
            selected = {} if backend == "combined" else {"assembly_backend": "formal"}
            with patch("genshin_corpus.rag.backend.encode_qwen_query", side_effect=fixture._embedding):
                result = run_single_question(state, "问题", embedding_transport=backend_fixture._EmbeddingTransport(), execution_mode="evidence_only",
                                             config=SingleQuestionBackendConfig(reranker_enabled=False,
                                                                                assembly_config=config, **selected))
            self.assertEqual(result["status"], "succeeded", result.get("error"))
            expected = (assemble_retained_evidence_packet(self.manifest, candidates, variant="combined", config=config,
                                                         prepared_context=self.context) if backend == "combined" else
                        assemble_deferred_footprint_charge_packet(self.manifest, candidates, config=config,
                                                                  prepared_context=self.context))
            actual = result["evidence_packet"]
            self.assertEqual(actual["evidence"], expected["evidence"])
            self.assertEqual(actual["assembly_version"], expected["assembly_version"])

    def setUp(self):
        self.fixture = fixture_module.RagW1Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.units, output, self.rich, self.dialogue = self.fixture._dialogue_alias_units()
        self.manifest = output / "metadata/manifest.json"
        self.context = prepare_evidence_assembly_context(self.manifest)

    def assemble(self, candidates, budget=1000, variant="aliases", **kwargs):
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, dialogue_hops=0,
                                        per_block_chars=1000, total_context_chars=budget, **kwargs)
        control = assemble_deferred_footprint_charge_packet(self.manifest, candidates, config=config, prepared_context=self.context)
        packet = assemble_retained_evidence_packet(self.manifest, candidates, variant=variant,
                                                   config=config, prepared_context=self.context)
        verify_challenger(control, packet, "fixture question")
        return control, packet

    def candidates(self, units):
        return [{"unit_id": unit["unit_id"], "rank": index + 1, "retrieval": {"fixture": index}}
                for index, unit in enumerate(units)]

    def test_visible_alias_releases_space_and_refills_full_root(self):
        extra = next(unit for unit in self.units if unit["content_type"] == "structured")
        candidates = self.candidates([self.rich, self.dialogue, extra])
        budget = 21 + len(extra["retrieval_visible_text"])
        control, packet = self.assemble(candidates, budget=budget)
        self.assertEqual(len(control["evidence"]), 2)
        self.assertEqual(packet["admission_contract"]["alias_released_chars"], 11)
        self.assertIn(extra["unit_id"], packet["admission_contract"]["final_evidence_by_unit"])
        self.assertEqual(packet["admission_contract"]["aliases"][0]["representative_unit_id"], self.rich["unit_id"])
        _, repeat = self.assemble(candidates, budget=budget)
        self.assertEqual(evidence_packet_json_bytes(packet), evidence_packet_json_bytes(repeat))

    def test_invisible_representative_is_never_suppressed_and_default_is_unchanged(self):
        candidates = self.candidates([self.rich, self.dialogue])
        control, packet = self.assemble(candidates, budget=11)
        self.assertEqual(packet["admission_contract"]["aliases"], [])
        self.assertEqual(project_generation_request(control, question="q").evidence,
                         project_generation_request(packet, question="q").evidence)

    def test_changed_source_or_visible_speaker_preserves_equal_text(self):
        from genshin_corpus.retrieval import evidence_assembly as module
        state = module._prepared_state(self.context)
        for changed in ("speaker", "source"):
            with self.subTest(changed=changed):
                original = deepcopy(state.units_by_id[self.dialogue["unit_id"]])
                modified = deepcopy(original)
                if changed == "speaker":
                    modified["structure"]["dialogue"]["speaker"] = "different"
                else:
                    modified["structure"]["dialogue"]["node_raw_ref"]["artifact_sha256"] = "f" * 64
                state.units_by_id[self.dialogue["unit_id"]].update(modified)
                _, packet = self.assemble(self.candidates([self.rich, self.dialogue]))
                self.assertEqual(packet["admission_contract"]["aliases"], [])
                state.units_by_id[self.dialogue["unit_id"]].clear()
                state.units_by_id[self.dialogue["unit_id"]].update(original)

    def test_retention_floor_prevents_greedy_displacement_of_late_independent_root(self):
        # A missing large root must not displace the smaller independent root
        # already admitted by the formal control after that omission.
        structured = next(unit for unit in self.units if unit["content_type"] == "structured")
        third = next(unit for unit in self.units if unit["content_type"] == "dialogue_node"
                     and unit["unit_id"] != self.dialogue["unit_id"])
        control, packet = self.assemble(self.candidates([self.rich, self.dialogue, structured, third]), budget=40)
        originals = {m["unit_id"] for b in control["evidence"] for m in b["members"]}
        final = set(packet["admission_contract"]["final_evidence_by_unit"])
        aliases = {a["suppressed_unit_id"] for a in packet["admission_contract"]["aliases"]}
        self.assertEqual(originals - final, aliases)
        self.assertIn(third["unit_id"], originals & final)
        self.assertNotIn(structured["unit_id"], final)
        self.assertLessEqual(len(self.rich["retrieval_visible_text"]) + len(structured["retrieval_visible_text"]), 40)

    def test_full_footprint_limit_is_preserved_for_short_root(self):
        self.fixture.config = RetrievalUnitBuildConfig(text_fragment_chars=4, structured_fragment_chars=1000)
        _, units, root = self.fixture._build("short-root-limit")
        chain = [u for u in units if u["content_type"] == "rich_text"
                 and u["fragment_selector"].get("fragment_count") == 3]
        config = EvidenceAssemblyConfig(neighbor_before=1, neighbor_after=1, dialogue_hops=0,
                                        per_block_chars=8, total_context_chars=100)
        packet = assemble_retained_evidence_packet(root / "metadata/manifest.json", self.candidates([chain[1]]),
                                                   variant="combined", config=config)
        self.assertEqual(packet["evidence"], [])
        self.assertEqual(packet["admission_contract"]["refill"], [])

    def test_range_and_lineage_tampering_are_rejected(self):
        control, packet = self.assemble(self.candidates([self.rich]))
        packet["evidence"][0]["members"][0]["lineage"]["raw_refs"] = []
        with self.assertRaisesRegex(ValueError, "original RU evidence"):
            verify_challenger(control, packet, "question")

    def test_html_entry_point_charges_display_preserves_original_and_exports_omissions(self):
        self.fixture.record["sections"][0]["units"][2]["value"]["decoded"] = {
            "a": '<p style="text-align: left;">原文内容</p>'}
        self.fixture._write_record()
        self.fixture._write_manifest()
        _, units, root = self.fixture._build("html-retention")
        structured = next(unit for unit in units if unit["content_type"] == "structured")
        rich = next(unit for unit in units if unit["content_type"] == "rich_text")
        candidates = self.candidates([structured, rich])
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, dialogue_hops=0,
                                        per_block_chars=1000, total_context_chars=len(structured["retrieval_visible_text"]))
        context = prepare_evidence_assembly_context(root / "metadata/manifest.json")
        control = assemble_deferred_footprint_charge_packet(root / "metadata/manifest.json", candidates,
                                                           config=config, prepared_context=context)
        packet = assemble_retained_evidence_packet(root / "metadata/manifest.json", candidates, variant="html",
                                                   config=config, prepared_context=context)
        verify_challenger(control, packet, "question", prepared_context=context)
        block = next(b for b in packet["evidence"] if b["members"][0]["unit_id"] == structured["unit_id"])
        self.assertEqual(block["members"][0]["text"], structured["retrieval_visible_text"])
        self.assertEqual(block["text"], "/a\t\n原文内容\n")
        self.assertEqual(len(packet["evidence"]), 2)
        self.assertEqual(len(control["evidence"]), 1)
        json_body, markdown = render_evidence_packet(packet)
        self.assertEqual(json.loads(json_body), packet)
        self.assertIn("原文内容", markdown.decode("utf-8"))
        # Exercise the existing renderer when refill still has omissions.
        _, omitted = self.assemble(self.candidates([self.rich, self.dialogue, self.units[2]]), budget=11)
        render_evidence_packet(omitted)


class ReviewBindingTests(unittest.TestCase):
    def test_review_keeps_pending_and_rejects_wrong_run_and_cross_packet_citation(self):
        with TemporaryDirectory(dir="data/retrieval") as directory:
            root = Path(directory)
            write_json(root / "manifest.json", {"status": "complete", "completed_challengers": 420,
                                                "run_identity": "fixture"})
            packets = [{"case_id": str(index), "packet_a": [{"evidence_id": "E01"}], "packet_b": []}
                       for index in range(420)]
            keys = [{"case_id": str(index), "side_a": "combined", "side_b": "control"} for index in range(420)]
            decisions = [{"case_id": str(index), "run_identity": "fixture", "status": "pending", "verdict": None}
                         for index in range(420)]
            write_jsonl(root / "review/blinded-packets.jsonl", packets)
            write_jsonl(root / "review/unblinding.jsonl", keys)
            decision_path = root / "review/reviewed.jsonl"
            decisions[0].update(status="reviewed", verdict="a_better", side_a_support="sufficient",
                                side_b_support="partial", evidence_ids=["A:E01"], rationale="原文补充关系。")
            write_jsonl(decision_path, decisions)
            self.assertEqual(tally_review(root, decision_path)["verdict_counts"], {"combined_better": 1, "pending": 419})
            decisions[0]["evidence_ids"] = ["B:E01"]
            write_jsonl(decision_path, decisions)
            with self.assertRaisesRegex(ValueError, "outside its Packet"):
                tally_review(root, decision_path)
            decisions[0]["run_identity"] = "other"
            write_jsonl(decision_path, decisions)
            with self.assertRaisesRegex(ValueError, "different run"):
                tally_review(root, decision_path)

    def test_frozen_case_hash_fails_before_loading_or_assembly(self):
        with TemporaryDirectory(dir="data/retrieval") as directory:
            root = Path(directory)
            path = root / "local_qwen3_1024/Q001/rag_result.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"tampered": True}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen result hash"):
                _case(root, "local_qwen3_1024", "Q001", "0" * 64)


if __name__ == "__main__":
    unittest.main()
