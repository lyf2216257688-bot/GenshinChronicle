"""Regression checks for browsing already-ranked evidence candidates."""
from copy import deepcopy
import unittest

from genshin_corpus.evidence_io import digest
from genshin_corpus.generation.clean_chat import encoded
from genshin_corpus.rag.clean_research_catalog import candidate_page, catalog_argument_issue


class FixtureStore(dict):
    def load(self, ref):
        return deepcopy(self[ref])


def candidate(number, rank, *, condition=None, parts=("仅供定位",)):
    return {
        "chunk_id": f"chunk-{number}",
        "source": {
            "source_item": {"source": "mihoyo_obc", "locale": "zh-cn", "content_id": str(number)},
            "source_context": {"label_evidence": {"document_title": {"text": "古老的碑铭"},
                                                   "section_title": {"text": "交互文本"}}},
            "conditions": [{"sources": [{"text": condition}]}] if condition else [],
        },
        "chunk": {"parts": [{"text": text} for text in parts]},
        "reranking": {"final_rank": rank},
    }


def add_group(store, data, query_id, rows):
    query = {"query_id": query_id, "search_text": "四位大调律师分别是谁？"}
    store[query_id] = {
        "version": "gc-clean-ranked-candidates-2",
        "status": "complete" if rows else "empty",
        "query": query,
        "candidates": rows,
    }
    data["queries"].append(query)
    data["groups"].append(query_id)
    for row in rows:
        chunk = row["chunk_id"]
        data["handles"][digest(chunk)] = {"chunk_id": chunk, "query_id": query_id}


class CandidateCatalogTests(unittest.TestCase):
    def setUp(self):
        self.store = FixtureStore()
        self.data = {"queries": [], "groups": [], "handles": {}, "reads": {}, "read_keys": {}, "failures": []}
        add_group(self.store, self.data, "original", [
            candidate(number, number, condition="完成前置任务前", parts=(
                "（点位2）", "「…依循大调律师优恩尼娅之令…」", "大调律师波爱修斯奉命整谐。"))
            for number in range(1, 101)
        ])

    def test_ordered_navigation_reaches_tail_and_preserves_candidate_identity(self):
        tail = candidate_page(self.store, self.data, "original", after_rank=88, max_rows=20, max_bytes=8000)
        self.assertEqual([r["rank"] for r in tail["rows"]], list(range(89, 101)))
        self.assertEqual((tail["total_candidates"], tail["through_rank"], tail["next_after_rank"]), (100, 100, None))
        row = tail["rows"][0]
        self.assertEqual(row["handle"], digest("chunk-89"))
        self.assertEqual({**tail["source_defaults"], **row["source_item"]},
                         {"source": "mihoyo_obc", "locale": "zh-cn", "content_id": "89"})
        self.assertEqual(row["condition_cue"], "完成前置任务前")
        self.assertEqual(row["preview"], ["「…依循大调律师优恩尼娅之令…」", "大调律师波爱修斯奉命整谐。"])
        self.assertEqual(row["body_status"], "not_read")
        self.assertTrue(tail["navigation_only"])
        self.assertNotIn("citation_id", row)
        self.assertEqual(self.data["reads"], {})

    def test_repeated_chunk_keeps_each_query_local_position(self):
        add_group(self.store, self.data, "query-1", [candidate(89, 1)])
        original = candidate_page(self.store, self.data, "original", after_rank=88)["rows"][0]
        repeated = candidate_page(self.store, self.data, "query-1")["rows"][0]
        self.assertEqual(original["handle"], repeated["handle"])
        self.assertEqual((original["rank"], repeated["rank"]), (89, 1))

    def test_single_long_matching_line_also_has_its_literal_lead(self):
        body = "遥远的海潮起落。" * 10 + "我早已知晓大调律师的嘱托。"
        add_group(self.store, self.data, "query-1", [candidate(101, 1, parts=(body,))])
        previews = candidate_page(self.store, self.data, "query-1")["rows"][0]["preview"]
        self.assertEqual(previews[0], body[:48])
        self.assertIn("大调律师", previews[1])
        self.assertIn(previews[1], body)

    def test_short_matched_line_keeps_speaker_and_literal_subsection(self):
        body = "角客：远古游历时我曾向那几位大调律师学习过。"
        item = candidate(101, 1, parts=(body,))
        item["source"]["source_context"]["group_title"] = {"value": {"text": "盐潮往事"}}
        add_group(self.store, self.data, "query-1", [item])
        row = candidate_page(self.store, self.data, "query-1")["rows"][0]
        self.assertEqual(row["preview"], [body])
        self.assertEqual(row["subsection"], "盐潮往事")
        self.assertNotIn("condition_cue", row)

    def test_source_defaults_never_combine_distinct_source_identities(self):
        alternate = candidate(102, 2)
        alternate["source"]["source_item"]["locale"] = "en"
        add_group(self.store, self.data, "query-1", [candidate(101, 1), alternate])
        page = candidate_page(self.store, self.data, "query-1")
        self.assertEqual(page["source_defaults"], {"source": "mihoyo_obc"})
        self.assertEqual([row["source_item"]["locale"] for row in page["rows"]], ["zh-cn", "en"])

    def test_actual_packet_and_history_exposure_differ_from_reads_and_failures(self):
        for number in (1, 2, 3):
            receipt = f"receipt-{number}"
            self.store[receipt] = {"fragments": [{"fragment_id": f"fragment-{number}"}]}
            self.data["read_keys"][digest([digest(f"chunk-{number}"), "chunk"])] = receipt
            self.data["reads"][receipt] = receipt
        self.data["failures"] = [{"chunk_id": "chunk-4"}]
        packet = {"evidence": [{"receipt_id": "receipt-1"}]}
        page = candidate_page(self.store, self.data, "original", max_rows=5, packet=packet,
                              exposed_ids=["fragment-1", "fragment-2"])
        self.assertEqual([row["body_status"] for row in page["rows"]],
                         ["in_current_packet", "exposed_in_history", "read_not_exposed", "read_failed", "not_read"])
        self.assertNotIn("preview", page["rows"][0])
        self.assertNotIn("preview", page["rows"][1])
        self.assertIn("preview", page["rows"][2])
        self.assertEqual(self.data["reads"], {f"receipt-{n}": f"receipt-{n}" for n in (1, 2, 3)})

    def test_other_visible_scope_does_not_suppress_chunk_preview(self):
        handle = digest("chunk-1")
        self.store["parent"] = {"fragments": [{"fragment_id": "parent-fragment"}]}
        self.data["read_keys"][digest([handle, "parent"])] = "parent"
        self.data["reads"]["parent"] = "parent"
        row = candidate_page(self.store, self.data, "original", max_rows=1,
                             exposed_ids=["parent-fragment"])["rows"][0]
        self.assertEqual(row["body_status"], "exposed_in_history")
        self.assertIn("preview", row)

    def test_byte_and_envelope_bounds_return_a_precise_cursor(self):
        single = candidate_page(self.store, self.data, "original", max_rows=1)
        budget = len(encoded(single))
        self.assertEqual(candidate_page(self.store, self.data, "original", max_bytes=budget), single)
        self.assertIsNone(candidate_page(self.store, self.data, "original", max_bytes=budget - 1))
        first = candidate_page(self.store, self.data, "original", fits=lambda page: len(page["rows"]) <= 12)
        self.assertEqual(first["through_rank"], 12)
        second = candidate_page(self.store, self.data, "original", after_rank=first["next_after_rank"], max_rows=10)
        self.assertEqual([r["rank"] for r in second["rows"]], list(range(13, 23)))

    def test_unknown_query_and_invalid_cursor_are_actionable(self):
        for args, message in [
            ({"query_id": "missing", "after_rank": 0}, "UNKNOWN_CANDIDATE_QUERY"),
            ({"query_id": "original", "after_rank": 101}, "CANDIDATE_CURSOR_OUT_OF_RANGE"),
            ({"query_id": "original", "after_rank": True}, "CANDIDATE_CURSOR_OUT_OF_RANGE"),
        ]:
            with self.subTest(args=args):
                self.assertEqual(catalog_argument_issue(self.store, self.data, args)["code"], message)
                with self.assertRaisesRegex(ValueError, message):
                    candidate_page(self.store, self.data, **args)
        add_group(self.store, self.data, "query-1", [])
        empty = candidate_page(self.store, self.data, "query-1")
        self.assertEqual((empty["rows"], empty["total_candidates"], empty["next_after_rank"]), ([], 0, None))

    def test_rank_and_handle_integrity_fail_closed(self):
        del self.data["handles"][digest("chunk-1")]
        with self.assertRaisesRegex(ValueError, "no registered handle"):
            candidate_page(self.store, self.data, "original")
        self.store["original"]["candidates"].reverse()
        with self.assertRaisesRegex(ValueError, "ordering changed"):
            candidate_page(self.store, self.data, "original")
