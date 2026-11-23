"""Local protocol, Packet admission and binding regressions for the new G5 profile."""

from copy import deepcopy
from errno import ECONNRESET
import json
import unittest
from urllib.error import URLError

from genshin_corpus.evidence_io import read
from genshin_corpus.generation.clean_evidence import EVIDENCE_QUALITY_ADMISSION, assemble, fragment_content
from genshin_corpus.rag import clean_research as research
from genshin_corpus.rag.clean_research_answer import bind_answer
from genshin_corpus.retrieval.clean_based.core import covered_by
from tests.generation import test_clean_evidence as evidence_fixtures
from tests.generation import test_clean_research as fixtures


class QualityResearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.settings.update(generation_profile=research.EVIDENCE_QUALITY_PROFILE, tool_error_corrections=2)
        self.fixture.sid = self.fixture.store.create_chat("G5 evidence quality", self.fixture.settings)

    def test_directory_does_not_count_as_no_gain_and_final_notes_do_not_bind_old_numbers(self):
        tid = self.fixture.turn()
        selected = []

        def list_pages(manifest):
            body = json.loads(manifest["wire"]["messages"][1]["content"])
            self.assertTrue(body["candidate_catalog"]["navigation_only"])
            self.assertGreater(body["candidate_catalog"]["total_candidates"], 0)
            selected.append(next(entry["fragment_id"] for entry in body["catalog"] if entry["citation_ids"]))
            self.assertEqual(manifest["wire"]["tools"][-1]["function"]["name"], "list_candidates")
            action = ("list_candidates", {"query_id": "original", "after_rank": 0})
            return fixtures.ToolOpener([action, action], prefix="list")

        def select_and_finish(manifest):
            tool_results = [json.loads(m["content"]) for m in manifest["wire"]["messages"] if m["role"] == "tool"]
            self.assertEqual([entry["status"] for entry in tool_results], ["candidate_page", "candidate_page"])
            self.assertEqual(tool_results[-1]["remaining"]["reads"], tool_results[0]["remaining"]["reads"])
            finish = fixtures.finish()
            finish[1]["hypotheses"] = ["旧任务 [E99]"]
            return fixtures.ToolOpener([("select", {"gap": "保留直接依据", "fragment_ids": selected}), finish], prefix="select")

        def final_answer(manifest):
            self.assertNotIn("tools", manifest["wire"])
            self.assertNotIn("[E99]", manifest["wire"]["messages"][1]["content"])
            self.assertEqual(manifest["wire"]["messages"][0]["content"], research.QUALITY_SYNTHESIS)
            return fixtures.MockOpener("依据原文。（E01）")

        result = self.fixture.execute(tid, [list_pages, select_and_finish, final_answer])
        self.assertEqual(result["stop_reason"], "sufficient")
        self.assertEqual(result["answer_text"], "依据原文。[E01]")
        self.assertEqual(result["citation_validation"]["status"], "PASS")
        self.assertEqual(len(self.fixture.pipeline.queries), 1)
        packet = self.fixture.store.load(result["sources_ref"])["packet"]
        self.assertEqual(packet["policy"]["admission"], EVIDENCE_QUALITY_ADMISSION)
        self.assertEqual(packet["policy"]["content_projection"], "shared_identical_context_and_chunk_fields_v1")
        self.assertEqual(packet["selection_ids"], selected)

    def test_partial_answer_and_undefined_citation_are_distinct(self):
        good = self.fixture.execute(self.fixture.turn("partial"), [fixtures.MockOpener("只提到 E01（E01 是编号）。")])
        self.assertEqual(good["status"], "partial")
        self.assertEqual(good["citation_validation"]["status"], "MISSING")
        self.assertEqual(good["citations"], [])
        self.assertNotIn("parent_context_ref", good)
        result = self.fixture.execute(self.fixture.turn("invalid"), [fixtures.MockOpener("声称有出处。［E01，E99］")])
        self.assertIsNone(result["answer_text"])
        self.assertEqual(result["citation_validation"]["status"], "FAIL")
        self.assertEqual(result["stop_reason"], "citation_validation_failed")
        packet = self.fixture.store.load(good["sources_ref"])["packet"]
        text, identities, _ = bind_answer(packet, "依据【E01】（E01），保留说明（E01 是编号）。")
        self.assertEqual(identities, ["E01"])
        self.assertEqual(text, "依据[E01][E01]，保留说明（E01 是编号）。")

    def test_undispatched_tool_body_is_admitted_but_recorded_as_unshown(self):
        self.fixture.settings["initial_reads"] = 1
        self.fixture.sid = self.fixture.store.create_chat("One initial read", self.fixture.settings)
        tid = self.fixture.turn()
        requested = []

        def read_and_finish(manifest):
            body = json.loads(manifest["wire"]["messages"][1]["content"])
            candidate = next(item for item in body["candidate_catalog"]["rows"] if item["body_status"] == "not_read")
            requested.append(candidate["rank"])
            read = ("read_source", {"gap": "补齐对话条件", "handle": candidate["handle"], "scope": "chunk"})
            return fixtures.ToolOpener([read, fixtures.finish()])

        result = self.fixture.execute(tid, [read_and_finish, fixtures.MockOpener("依据原文。[E01]")])
        packet = self.fixture.store.load(result["sources_ref"])["packet"]
        detail = packet["dispositions"][requested[0] - 1]["fragments"][0]
        self.assertEqual(detail["admission_tier"], "requested_read")
        self.assertEqual(detail["body_exposure"], "not_shown")

    def test_second_operation_transport_unknown_survives_checkpoint_replay(self):
        tid = self.fixture.turn()

        class ResetOpener:
            calls = 0

            def open(self, _request, *, timeout):
                self.calls += 1
                raise URLError(ConnectionResetError(ECONNRESET, "fixture reset"))

        reset = ResetOpener()

        def crash_after_unknown_seal(stage):
            if stage == "chat_sealed_before_checkpoint" and reset.calls:
                raise fixtures.Crash()

        with self.assertRaises(fixtures.Crash):
            self.fixture.execute(tid, [fixtures.ToolOpener([fixtures.search()]), reset], fault=crash_after_unknown_seal)
        batch = self.fixture.bridge().batch
        before = batch.accounting()
        replay = ResetOpener()
        result = self.fixture.execute(tid, [replay], continue_turn=True)
        self.assertEqual(replay.calls, 0)
        self.assertEqual(reset.calls, 1)
        self.assertEqual(result["stop_reason"], "unknown")
        self.assertIsNone(result["answer_text"])
        self.assertEqual(batch.accounting()["request_reservations"], before["request_reservations"])
        self.assertEqual(batch.accounting()["unknown_usage_requests"], 1)
        attempt = read(self.fixture.bridge().root / "calls" / result["operation_id"] / "attempts/0001/result.json")
        self.assertEqual(attempt["state"], "UNKNOWN")
        self.assertEqual(attempt["transport_diagnostics"]["failure"]["cause_type"], "ConnectionResetError")

    def test_compact_projection_preserves_text_and_exact_tool_body_references(self):
        def capture(manifest):
            payload = json.loads(manifest["wire"]["messages"][1]["content"])
            current = next(entry for entry in payload["catalog"] if entry["citation_ids"])
            self.assertTrue(next(entry for entry in payload["packet"]["evidence"]
                                 if entry["citation_id"] in current["citation_ids"])["content"]["source_item"])
            return fixtures.ToolOpener([("read_source", {"gap": "核验已展示原句", "handle": current["handle"],
                                                        "scope": "chunk"})])

        def confirm(manifest):
            tool = json.loads(manifest["wire"]["messages"][-1]["content"])
            self.assertEqual(tool["sources"][0]["model_body"], "exact_body_already_visible_in_this_task")
            self.assertNotIn("content", tool["sources"][0])
            return fixtures.ToolOpener([fixtures.finish()])

        result = self.fixture.execute(self.fixture.turn(), [capture, confirm, fixtures.MockOpener("原文。[E01]")])
        self.assertEqual(result["citation_validation"]["status"], "PASS")


class PriorityAdmissionTests(unittest.TestCase):
    def test_compact_content_shares_only_equal_values_and_preserves_qualifiers(self):
        fixture = evidence_fixtures.EvidenceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        _, _, receipts = fixture.receipts()
        fragment = deepcopy(receipts[0]["fragments"][0])
        first = {**fragment["structure"]["parts"][0], "text": "相同原文", "speaker": None,
                 "field_labels": ["共同来源字段" * 20], "condition_sources": [{"text": "第一条件"}],
                 "node_id": "node-1", "option_context": [{"text": "同一节点的选项" * 20}]}
        second = {**first, "speaker": "另一说话者", "condition_sources": [{"text": "第二条件"}]}
        third = {**first, "node_id": "node-2", "option_context": [{"text": "另一节点的选项"}]}
        fragment["structure"]["parts"] = [first, second, third]
        fragment["parent_context"] = deepcopy(fragment["source_context"])
        original = deepcopy(fragment)
        compact = fragment_content(fragment, compact=True)
        full = fragment_content(fragment)
        self.assertEqual(fragment, original)
        self.assertTrue(compact.pop("parent_context_same_as_source_context"))
        compact["parent_context"] = compact["source_context"]
        self.assertEqual(compact.pop("source_item"), fragment["source_item"])
        body = compact["source_text_and_structure"]
        shared = body.pop("shared_fields_for_all_parts")
        self.assertIn("field_labels", shared)
        self.assertEqual([p["speaker"] for p in body["parts"]], [None, "另一说话者", None])
        self.assertEqual([p["condition_sources"][0]["text"] for p in body["parts"]], ["第一条件", "第二条件", "第一条件"])
        ranges = body.pop("shared_fields_by_part_range")
        self.assertEqual(ranges, [{"first_part": 1, "last_part": 2,
                                   "fields": {"node_id": "node-1", "option_context": first["option_context"]}}])
        for item in ranges:
            for i in range(item["first_part"] - 1, item["last_part"]):
                body["parts"][i] = {**item["fields"], **body["parts"][i]}
        body["parts"] = [{**shared, **part} for part in body["parts"]]
        self.assertEqual(compact, full)
        fragment["parent_context"]["separate_context"] = "保留不同来源语境"
        self.assertIn("parent_context", fragment_content(fragment, compact=True))

    def test_pressure_protects_selection_or_requested_read_without_reordering_publication(self):
        fixture = evidence_fixtures.EvidenceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        _, _, receipts = fixture.receipts()
        candidates = fixture.ranked["candidates"]
        second = next(i for i, item in enumerate(candidates[1:], 1)
                      if not covered_by(item["chunk"], candidates[0]["chunk"])
                      and not covered_by(candidates[0]["chunk"], item["chunk"]))
        reads = [receipts[0], receipts[second]]
        first_id, late_id = (r["fragments"][0]["fragment_id"] for r in reads)
        groups = [fixture.ranked]
        context, budget = evidence_fixtures.CONTEXT, evidence_fixtures.BUDGET
        sizes = [assemble(groups, [r], context=context, budget=budget)["estimated_message_tokens"] for r in reads]
        tight = {**budget, "context_tokens": max(sizes) + budget["output_tokens"]}
        for options in ({"selection_ids": [late_id]}, {"priority_ids": [late_id]}):
            with self.subTest(options=options):
                chosen = assemble(groups, reads, context=context, budget=tight,
                                  admission=EVIDENCE_QUALITY_ADMISSION, **options)
                self.assertEqual([row["fragment_id"] for row in chosen["evidence"]], [late_id])
                self.assertEqual(chosen["dispositions"][0]["disposition"], "budget_excluded")
        compact_sizes = [assemble(groups, [r], context=context, budget=budget,
                                  compact_content=True)["estimated_message_tokens"] for r in reads]
        compact_tight = {**budget, "context_tokens": max(compact_sizes) + budget["output_tokens"]}
        preserved = assemble(groups, reads, context=context, budget=compact_tight,
                             admission=EVIDENCE_QUALITY_ADMISSION, compact_content=True, preserve_ids=[late_id])
        self.assertEqual([row["fragment_id"] for row in preserved["evidence"]], [late_id])
        self.assertEqual(preserved["selection_ids"], [])
        self.assertEqual(preserved["dispositions"][second]["fragments"][0]["admission_tier"], "initial_admission_preserved")
        roomy = assemble(groups, reads, context=context, budget=budget, selection_ids=[late_id],
                         admission=EVIDENCE_QUALITY_ADMISSION, exposed_ids=[late_id])
        self.assertEqual([row["fragment_id"] for row in roomy["evidence"]], [first_id, late_id])
        self.assertEqual(roomy["dispositions"][0]["fragments"][0]["body_exposure"], "not_shown")
        self.assertEqual(roomy["dispositions"][second]["fragments"][0]["body_exposure"], "shown")
