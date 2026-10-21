"""Small checked-in Canonical fixture; no local corpus, provider or network."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval import candidate_retrieval as lexical, retrieval_units as ru
from genshin_corpus.retrieval.navi_exploration import ExplorationError, LocalExplorationTools
from genshin_corpus.retrieval.navi_reading import (
    HYPOTHESIS, VerifiedReadingSources, build_release_catalog, partition_record,
)
from genshin_corpus.rag.navi_v_experiment import ReadingBudget, ReadingSession
from genshin_corpus.rag.navi_v_research import HistoricalReplay
from genshin_corpus.rag import navi_v_live_research as live_research
from genshin_corpus.rag import navi_v_expanded_research as expanded_research

FIXTURE = Path(__file__).parents[1] / "fixtures/retrieval/canonical-rag-w1-record.json"


def listing(versions=None, entries=None):
    versions = versions or ["1.0", "5.8", "月之一", "月之二", "7.0"]
    entries = entries or [
        ("0", ["任务类型/世界任务", "版本号/1.0"]),
        ("2", ["任务类型/魔神任务", "版本号/1.0"]),
    ]
    return {"data": {"list": [{
        "id": 43, "ch_ext": json.dumps([{"attribute_key": "filter", "value": json.dumps([
            {"label": "版本号", "children": [{"label": v} for v in versions]}
        ])}]),
        "list": [{"content_id": cid, "title": f"Library {cid}",
                  "ext": json.dumps({"c_43": {"filter": {"text": json.dumps(tags)}}})}
                 for cid, tags in entries],
    }]}}


def make_sources(root):
    base = json.loads(FIXTURE.read_bytes())
    entries, records = [], []
    path = root / "canonical/metadata/manifest.json"
    path.parent.mkdir(parents=True)
    for number in range(3):
        record = deepcopy(base)
        record["record_id"] = f"record-{number}"
        key = f"mihoyo_obc:zh-cn:{number}"
        record["source_identity"]["components"]["content_id"] = str(number)
        record["source_identity"]["key"] = key
        record["record_metadata"].update(name=f"Library {number}",
                                          channel_memberships=["43"] if number != 1 else ["68"])
        section = record["sections"][0]
        section["source_metadata"].update(name="剧情对话", layout_observations=[{
            "tab_index": 0, "group_index": 0, "tab_id": "t", "group_id": "g", "placement_index": 0,
        }])
        section["source_metadata"]["source_module_id"] = "m"
        record["record_metadata"]["source_template_layout"] = {"tab": [{
            "tab_id": "t", "module_group": [{"module_group_id": "g", "module": [{"id": "m"}]}],
        }]}
        section["component_contexts"][0]["source_component_id"] = "interactive_dialogue"
        section["units"][0]["value"]["entry_references"] = [{"content_id": "1", "name": "Library 1"}]
        if number == 1:
            extra = deepcopy(section)
            extra["ordinal"], extra["section_id"] = 1, "section:1"
            extra["source_metadata"]["source_module_id"] = "m-extra"
            extra["source_metadata"]["layout_observations"][0].update(group_index=1, group_id="extra")
            record["sections"].append(extra)
            record["record_metadata"]["source_template_layout"]["tab"][0]["module_group"].append({
                "module_group_id": "extra", "module": [{"id": "m-extra"}],
            })
        target = root / f"canonical/record-{number}.json"
        body = canonical_json_bytes(record)
        target.write_bytes(body)
        records.append(record)
        entries.append({"record_id": record["record_id"], "canonical_record_path": str(target),
                        "canonical_record_sha256": sha256(body).hexdigest(), "canonical_status": "canonical",
                        "parsed_identity_key": key})
    path.write_bytes(canonical_json_bytes({
        "status": "complete", "canonical_run_id": "v-fixture", "source": "mihoyo_obc", "locale": "zh-cn",
        "input_record_count": 3, "accounted_record_count": 3, "input_integrity_failure_count": 0,
        "dependencies": {"canonical_versions": base["versions"]}, "records": entries,
    }))
    ru.build_retrieval_units(path, root / "ru",
                            config=ru.RetrievalUnitBuildConfig(text_fragment_chars=10, structured_fragment_chars=1200))
    ru_path = root / "ru/metadata/manifest.json"
    lexical.build_lexical_index(ru_path, root / "lexical")
    tools = LocalExplorationTools(path, ru_path, root / "lexical/metadata/manifest.json")
    return VerifiedReadingSources(tools), records


def update(clue_id, unit_ids, op="add", target=None):
    return {"operation": op, "target_clue_id": target, "reason": "fixture comparison" if target else None,
            "clue": {"clue_id": clue_id, "text": f"Fixture interpretation {clue_id}",
                     "source_unit_ids": unit_ids, "source_genre": "unclassified",
                     "epistemic_status": "unclassified", "narrative_significance": "fixture only"}}


class ReleaseCatalogTests(unittest.TestCase):
    def test_official_moon_order_and_listing_ties(self):
        data = listing(entries=[("8", ["版本号/7.0"]), ("7", ["版本号/月之二"]),
                                ("6", ["版本号/月之一"]), ("5", ["版本号/5.8"]), ("4", ["版本号/5.8"])])
        result = build_release_catalog(data, source="mihoyo_obc", locale="zh-cn")
        self.assertEqual([r["content_id"] for r in result["queue"]], ["5", "4", "6", "7", "8"])
        self.assertEqual(result, build_release_catalog(deepcopy(data), source="mihoyo_obc", locale="zh-cn"))

    def test_missing_conflicting_unknown_and_malformed_versions_are_accounted(self):
        data = listing(entries=[("0", []), ("1", ["版本号/1.0", "版本号/5.8"]),
                                ("2", ["版本号/6.0"]), ("3", ["版本号/1.0"])])
        data["data"]["list"][0]["list"][-1]["ext"] = "invalid"
        result = build_release_catalog(data, source="mihoyo_obc", locale="zh-cn")
        self.assertEqual([r["reason"] for r in result["pending_metadata"]],
                         ["missing_version", "conflicting_versions", "unknown_version", "invalid_filter"])
        self.assertEqual(result["counts"]["pages"], 4)
        self.assertEqual(result["counts"]["queued"], 0)

    def test_type_membership_is_not_exclusive(self):
        data = listing(entries=[("0", ["任务类型/世界任务", "任务类型/限时任务", "版本号/1.0"])])
        counts = build_release_catalog(data, source="mihoyo_obc", locale="zh-cn")["counts"]
        self.assertEqual(counts["pages"], 1)
        self.assertEqual(sum(counts["type_occurrences"].values()), 2)

    def test_duplicate_ids_and_unknown_version_order_fail(self):
        with self.assertRaisesRegex(ExplorationError, "duplicate"):
            build_release_catalog(listing(entries=[("0", []), ("0", [])]), source="mihoyo_obc", locale="zh-cn")
        with self.assertRaisesRegex(ExplorationError, "version order"):
            build_release_catalog(listing(versions=["1.0", "1.0"]), source="mihoyo_obc", locale="zh-cn")


class ReadingSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources, self.records = make_sources(self.root)
        self.catalog = build_release_catalog(listing(), source="mihoyo_obc", locale="zh-cn")
        self.key = "mihoyo_obc:zh-cn:0"

    def session(self, name="session", **budget):
        return ReadingSession.create(self.sources, self.catalog, self.root / name, baseline_sources=[self.key],
                                     budget=ReadingBudget(**budget))

    def topic(self, session):
        return next(iter(session.state["topics"]))

    def test_full_canonical_unit_maps_all_official_fragments(self):
        plan = self.sources.plan(self.key)
        units = plan["batches"][0]["units"]
        self.assertEqual([u["text"] for u in units], ["ABCDEFGHIJK", "next leaf"])
        self.assertEqual(len(units[0]["unit_ids"]), 2)
        self.assertEqual(len(plan["batches"]), 1)
        self.assertEqual(len(plan["dispositions"]), 6)

    def test_source_graph_order_branches_orphans_and_cycles_are_preserved(self):
        record, rows = self.sources.record(self.key)
        section = record["sections"][0]
        rich = [u for u in section["units"] if u["kind"] == "rich_text" and u["value"]["normalized_text"]]
        for unit, node in zip(rich, ["end", "start"]):
            unit.setdefault("metadata", {})["parsed_identity"] = {"components": {"source_id": f"rich_text:/contents/{node}/dialogue"}}
        graph = next(u for u in section["units"] if u["kind"] == "dialogue_graph")
        graph["value"]["groups"] = [{
            "root_id": "start", "source_path": "", "nodes": [{"source_id": n, "option": n}
                                                               for n in ["end", "start", "orphan"]],
            "edges": [{"parent_id": "start", "child_id": "end", "ordering": 0},
                      {"parent_id": "end", "child_id": "start", "ordering": 0}],
            "diagnostics": [{"code": "fixture_cycle"}],
        }]
        other = [u for u in section["units"] if u["kind"] == "dialogue_graph" and u is not graph]
        for unit in other:
            unit["value"]["groups"] = []
        plan = partition_record(record, rows, snapshot_identity=self.sources.identity)
        self.assertEqual([u["canonical_unit_ordinal"] for u in plan["batches"][0]["units"]], [1, 0])
        context = plan["groups"][0]["graph_context"][0]
        self.assertEqual(context["unreachable_node_ids"], ["orphan"])
        self.assertEqual(len(context["edges"]), 2)
        self.assertEqual(context["diagnostics"][0]["code"], "fixture_cycle")

    def test_layout_controls_group_order_not_section_array(self):
        record, rows = self.sources.record(self.key)
        record["sections"][0]["source_metadata"]["layout_observations"][0]["group_index"] = 3
        record["sections"][0]["source_metadata"]["layout_observations"][0]["group_id"] = "later"
        copy = deepcopy(record["sections"][0])
        copy["ordinal"] = 7
        copy["source_metadata"]["layout_observations"][0].update(group_index=0, group_id="earlier")
        record["sections"].append(copy)
        record["record_metadata"]["source_template_layout"]["tab"][0]["module_group"] = [
            {"module_group_id": "earlier", "module": [{"id": "m"}]},
            {"module_group_id": "unused1", "module": []},
            {"module_group_id": "unused2", "module": []},
            {"module_group_id": "later", "module": [{"id": "m"}]},
        ]
        extra = deepcopy(rows)
        for row in extra:
            row["source"]["canonical_address"]["section_ordinal"] = 7
        plan = partition_record(record, rows + extra, snapshot_identity=self.sources.identity)
        self.assertEqual([g["layout_order"] for g in plan["groups"]], [[0, 0], [0, 3]])
        self.assertTrue(plan["batches"][0]["group_id"].endswith("earlier"))

    def test_missing_layout_and_mapping_remain_explicit(self):
        record, rows = self.sources.record(self.key)
        record["sections"][0]["source_metadata"]["layout_observations"] = []
        plan = partition_record(record, rows, snapshot_identity=self.sources.identity)
        self.assertEqual(plan["batches"], [])
        self.assertEqual(len(plan["issues"]), 1)
        record = self.records[0]
        rows = [r for r in rows if r["source"]["canonical_address"]["canonical_unit_ordinal"] != 0]
        with self.assertRaisesRegex(ExplorationError, "incomplete official RU"):
            partition_record(record, rows, snapshot_identity=self.sources.identity)

    def test_partial_batch_is_not_complete_reading(self):
        session = self.session(max_new_body_chars=12)
        receipt = session.read_next()
        self.assertTrue(receipt["partial_batch"])
        self.assertEqual(receipt["units"][0]["text"], "ABCDEFGHIJK")
        self.assertEqual(session.coverage_summary()["scoped_unread_occurrences"], 1)
        self.assertEqual(next(iter(session.coverage_summary()["groups"].values()))["status"], "partial")
        session.read_next()
        self.assertEqual(session.state["status"], "budget_exhausted")
        self.assertTrue(session.state["queues"]["baseline"])

    def test_oversized_unit_is_deferred_without_false_group_completion(self):
        session = self.session(max_batch_body_chars=10)
        receipt = session.read_next()
        self.assertEqual(receipt["units"][0]["text"], "next leaf")
        summary = session.coverage_summary()
        self.assertEqual(summary["deferred_units"], 1)
        self.assertEqual(next(iter(summary["groups"].values()))["status"], "partial")

    def test_delivery_survives_failed_understanding_and_resume_without_reread(self):
        session = self.session()
        receipt = session.read_next()
        bad = update("bad", ["invented"])
        session.understand(updates=[bad], summary="invalid", receipt_ids=[receipt["receipt_id"]])
        used = dict(session.state["used"])
        resumed = ReadingSession.resume(self.sources, self.catalog, session.root)
        self.assertEqual(resumed.state["used"], used)
        self.assertEqual(resumed.coverage_summary()["pending_understanding_receipts"], [receipt["receipt_id"]])
        good = update("good", receipt["units"][0]["unit_ids"])
        resumed.understand(updates=[good], summary="Attributed fixture understanding.", receipt_ids=[receipt["receipt_id"]])
        self.assertEqual(resumed.state["used"]["new_body_chars"], used["new_body_chars"])
        self.assertEqual(len(resumed.state["continuity"]["active_clues"]), 1)
        self.assertEqual(len(resumed.state["continuity"]["source_ledger"]), 3)

    def test_invalid_operations_and_missing_targets_do_not_become_add(self):
        session = self.session()
        receipt = session.read_next()
        ids = receipt["units"][0]["unit_ids"]
        for bad in [update("a", ids, "guess"), update("b", ids, "supplement", "missing")]:
            result = session.understand(updates=[bad], summary="Rejected update", receipt_ids=[receipt["receipt_id"]])
            self.assertIn("reason", result)
        self.assertEqual(session.state["continuity"]["clue_events"], [])
        self.assertEqual(len(session.state["rejections"]), 2)

    def test_revision_supersession_and_contrast_share_one_history(self):
        session = self.session()
        receipt = session.read_next()
        ids = receipt["units"][0]["unit_ids"]
        changes = [update("a", ids), update("b", ids, "contrast", "a"),
                   update("c", ids, "revise", "a"), update("d", ids, "supersede", "b")]
        session.understand(updates=changes, summary="Fixture-only revision chain.", receipt_ids=[receipt["receipt_id"]])
        self.assertEqual([c["clue_id"] for c in session.state["continuity"]["active_clues"]], ["c", "d"])
        self.assertEqual(len(session.state["continuity"]["clue_events"]), 4)

    def test_nonquest_seed_and_three_lane_fairness(self):
        session = self.session(max_batch_body_chars=12)
        topic = self.topic(session)
        session.enqueue("mihoyo_obc:zh-cn:1", lane="independent", question="An independent book question",
                        origin={"kind": "independent_seed", "reason": "fixture research entry"})
        first = session.read_next()
        session.enqueue("mihoyo_obc:zh-cn:0", lane="related", topic_id=topic, anchor_section=0,
                        origin={"kind": "read_source", "coverage_key": first["units"][0]["coverage_key"]})
        related = session.read_next()
        independent = session.read_next()
        baseline = session.read_next()
        self.assertEqual([r["lane"] for r in [first, related, independent, baseline]],
                         ["baseline", "related", "independent", "baseline"])
        self.assertEqual(independent["source"], "mihoyo_obc:zh-cn:1")

    def test_independent_nonquest_can_start_without_any_quest_baseline(self):
        session = ReadingSession.create(self.sources, self.catalog, self.root / "book-only", baseline_sources=[])
        session.enqueue("mihoyo_obc:zh-cn:1", lane="independent", question="Study the book itself",
                        origin={"kind": "independent_seed", "reason": "No quest ownership required"})
        receipt = session.read_next()
        self.assertEqual(receipt["lane"], "independent")
        self.assertEqual(session.manifest["baseline_sources"], [])
        self.assertEqual(len(session.state["topics"]), 1)

    def test_cross_topic_reuse_avoids_repeat_body_and_duplicate_ledger(self):
        session = self.session()
        first = session.read_next()
        session.enqueue(self.key, lane="independent", question="Different topic, same source",
                        origin={"kind": "read_source", "coverage_key": first["units"][0]["coverage_key"]})
        reused = session.read_next()
        self.assertFalse(reused["units"])
        self.assertEqual(len(reused["reused_coverage_keys"]), 2)
        self.assertEqual(session.state["used"]["new_body_chars"], 20)
        self.assertEqual(len(session.state["continuity"]["source_ledger"]), 3)
        self.assertEqual(len(session.state["coverage"][first["units"][0]["coverage_key"]]["topics"]), 2)
        session.understand(updates=[update("reused_topic", first["units"][0]["unit_ids"])],
                           summary="A different question on already delivered source.",
                           receipt_ids=[reused["receipt_id"]])
        self.assertEqual(session.state["continuity"]["active_clues"][0]["clue_id"], "reused_topic")

    def test_template_disagreement_is_audited_and_does_not_deliver(self):
        record, rows = self.sources.record(self.key)
        record["record_metadata"]["source_template_layout"]["tab"][0]["module_group"][0]["module"][0]["id"] = "different"
        plan = partition_record(record, rows, snapshot_identity=self.sources.identity)
        self.assertEqual(plan["batches"], [])
        self.assertEqual(plan["issues"][0]["reason"], "layout_template_mismatch")

    def test_malformed_update_rejected_atomically(self):
        session = self.session()
        receipt = session.read_next()
        good = update("good", receipt["units"][0]["unit_ids"])
        malformed = update("bad", receipt["units"][0]["unit_ids"], "supplement", "good")
        malformed["reason"] = 42
        session.understand(updates=[good, malformed], summary="invalid batch",
                           receipt_ids=[receipt["receipt_id"]])
        self.assertEqual(session.state["continuity"]["clue_events"], [])
        self.assertEqual(len(session.state["rejections"]), 1)

    def test_injected_decisions_execute_and_resume_preserves_return_position(self):
        session = self.session(max_batch_body_chars=12)
        decisions = iter([{"action": "read"}, {"action": "read"}, None])
        session.run(lambda context: next(decisions))
        resumed = ReadingSession.resume(self.sources, self.catalog, session.root)
        self.assertEqual(resumed.state, session.state)
        self.assertEqual(resumed.state["queues"]["baseline"], [])

    def test_historical_coordinates_are_proposals_resolved_only_after_delivery(self):
        session = self.session()
        history = self.root / "history"
        step = history / "steps/step_01"
        step.mkdir(parents=True)
        raw = update("historical", ["not-used"])["clue"]
        raw.pop("source_unit_ids")
        raw.update(operation="add", target_clue_id=None, reason="historical example",
                   uncertainty_note="unreviewed", source_reference="fixture historical proposal")
        (step / "parsed_output.json").write_bytes(canonical_json_bytes({"clue_updates": [raw]}))
        (history / "derived_source_index.json").write_bytes(canonical_json_bytes({"clues": [{
            "clue_id": "historical", "local_component_keys": ["0:s0:u0"], "overall_status": "section_only",
        }]}))
        replay = HistoricalReplay(history)
        receipt = session.read_next()
        replay.apply(session, receipt)
        clue = session.state["continuity"]["active_clues"][0]
        self.assertEqual(clue["source_unit_ids"], receipt["units"][0]["unit_ids"])
        self.assertEqual(len(clue["source_unit_ids"]), 2)
        self.assertNotIn("0:s0:u0", clue["source_unit_ids"])
        self.assertEqual(replay.report()["updates"]["historical"]["historical_resolution_status"], "section_only")

    def test_explicit_reread_has_reason_and_separate_cost(self):
        session = self.session()
        first = session.read_next()
        session.enqueue(self.key, lane="related", topic_id=self.topic(session), reread_reason="Check an exact qualifier",
                        origin={"kind": "read_source", "coverage_key": first["units"][0]["coverage_key"]})
        session.read_next()
        self.assertEqual(session.state["used"]["reread_body_chars"], 20)
        self.assertEqual(session.state["used"]["new_body_chars"], 20)
        self.assertTrue(all(e["read_count"] == 2 for e in session.state["continuity"]["source_ledger"]))

    def test_search_hits_do_not_mark_read_and_must_bind_frontier(self):
        session = self.session()
        topic = self.topic(session)
        result = session.search("next leaf", topic_id=topic)
        self.assertEqual(session.coverage_summary()["pages_delivered"], 0)
        self.assertEqual(session.state["coverage"], {})
        hit = result["result"]["hits"][0]
        with self.assertRaisesRegex(ExplorationError, "recorded search hit"):
            session.enqueue(self.key, lane="related", topic_id=topic, anchor_section=0,
                            origin={"kind": "search_hit", "unit_id": "not_a_hit"})
        session.enqueue(hit["canonical_address"]["source_identity_key"], lane="related", topic_id=topic,
                        anchor_section=hit["canonical_address"]["section_ordinal"],
                        origin={"kind": "search_hit", "unit_id": hit["unit_id"]})

    def test_only_delivered_source_links_can_enqueue(self):
        session = self.session()
        receipt = session.read_next()
        origin = {"kind": "source_link", "coverage_key": receipt["units"][0]["coverage_key"]}
        session.enqueue("mihoyo_obc:zh-cn:1", lane="related", topic_id=self.topic(session), origin=origin)
        with self.assertRaisesRegex(ExplorationError, "not present"):
            session.enqueue("mihoyo_obc:zh-cn:2", lane="related", topic_id=self.topic(session), origin=origin)

    def test_anchor_delivery_inventories_other_page_groups_as_unread(self):
        session = self.session()
        receipt = session.read_next()
        session.enqueue("mihoyo_obc:zh-cn:1", lane="related", topic_id=self.topic(session), anchor_section=0,
                        origin={"kind": "source_link", "coverage_key": receipt["units"][0]["coverage_key"]})
        book_receipt = session.read_next()
        summary = session.coverage_summary()
        self.assertEqual(summary["scoped_unread_occurrences"], 2)
        extra = [g for key, g in summary["groups"].items() if key.endswith("group:extra")]
        self.assertEqual(extra[0]["status"], "unread")
        self.assertEqual(session.state["used"]["new_body_chars"], 40)
        unread_ids = next(iter(summary["unread_occurrences"].values()))["unit_ids"]
        session.understand(updates=[update("unread_but_real", unread_ids)],
                           summary="Existing formal IDs in inventory are not delivered evidence.",
                           receipt_ids=[book_receipt["receipt_id"]])
        self.assertEqual(len(session.state["rejections"]), 1)

    def test_page_and_action_budgets_preserve_pending_work(self):
        session = self.session(max_pages=1, max_actions=3)
        session.read_next()
        session.enqueue("mihoyo_obc:zh-cn:1", lane="independent", question="another source",
                        origin={"kind": "independent_seed", "reason": "fixture"})
        session.read_next()
        self.assertEqual(session.state["status"], "budget_exhausted")
        self.assertTrue(session.state["queues"]["independent"])
        self.assertEqual(session.state["used"]["actions"], 3)

    def test_checkpoint_tamper_and_changed_inputs_fail_closed(self):
        session = self.session()
        session.read_next()
        path = session.root / "checkpoints/000001.json"
        value = json.loads(path.read_bytes())
        value["state"]["used"]["new_body_chars"] = 0
        path.write_bytes(canonical_json_bytes(value))
        with self.assertRaisesRegex(ExplorationError, "tamper"):
            ReadingSession.resume(self.sources, self.catalog, session.root)
        other = self.session("other")
        changed = deepcopy(self.catalog)
        changed["catalog_identity"] = "changed"
        with self.assertRaisesRegex(ExplorationError, "identity mismatch"):
            ReadingSession.resume(self.sources, changed, other.root)

    def test_model_context_excludes_earlier_body_and_recovery_consumes_no_network(self):
        session = self.session(max_batch_body_chars=12)
        session.read_next()
        second = session.read_next()
        context = session.decision_context()
        self.assertEqual(len(context["latest_pending_receipts"]), 1)
        self.assertEqual(context["latest_pending_receipts"][0]["receipt_id"], second["receipt_id"])
        self.assertEqual(len(context["pending_receipt_ids"]), 2)
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            recovered = ReadingSession.resume(self.sources, self.catalog, session.root)
        self.assertEqual(recovered.previous_hash, session.previous_hash)
        self.assertEqual(recovered.manifest["interpretation_status"], HYPOTHESIS)

    def test_reading_session_rejects_non_string_decision_mode(self):
        with self.assertRaisesRegex(ExplorationError, "explicit non-empty decision_mode required"):
            ReadingSession.create(
                self.sources, self.catalog, self.root / "bad_mode",
                baseline_sources=[self.key], decision_mode=None
            )
        with self.assertRaisesRegex(ExplorationError, "explicit non-empty decision_mode required"):
            ReadingSession.create(
                self.sources, self.catalog, self.root / "empty_mode",
                baseline_sources=[self.key], decision_mode="   "
            )


class MockClient:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.recorded_calls: list[dict[str, Any]] = []

    def __call__(self, stem: Path, body: dict, run_identity: str) -> dict:
        round_name = stem.name
        self.recorded_calls.append({"stem": stem, "body": body, "round": round_name})
        resp_text = self.responses.get(round_name, "{}")
        return {
            "complete": True,
            "wire_model": "deepseek-v4.1-flash",
            "content": resp_text,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "latency_ms": 150.0,
        }


class LiveResearchRepairTests(unittest.TestCase):
    """Targeted tests for the 6 execution layer repairs in navi_v_live_research."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sources, self.records = make_sources(self.root)
        self.catalog = build_release_catalog(listing(), source=self.sources.tools.canonical["source"],
                                             locale=self.sources.tools.canonical["locale"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_fix1_round2_prompt_has_no_answer_leakage(self):
        live_script = Path(__file__).parents[2] / "src/genshin_corpus/rag/navi_v_live_research.py"
        content = live_script.read_text(encoding="utf-8")
        self.assertNotIn('"chosen_candidate_index": 8', content)
        self.assertIn('"chosen_candidate_index": null', content)

    def test_fix2_full_text_delivered_without_silent_truncation(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hit_target = self.sources.search("next")["hits"][0]
        target_uid = hit_target["unit_id"]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [{"candidate_index": 1, "source_nature": "primary_book", "reason": "匹配"}],
                "decision": {"action": "read", "chosen_candidate_index": 1, "chosen_unit_id": target_uid, "selection_rationale": "选择"}
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "新线索",
                                  "clue": {"clue_id": "clue_02", "text": "线索2", "source_unit_ids": [target_uid],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_repair_full_delivery"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertTrue(report["reading_and_understanding"]["closed_loop_verified"])
        r3_call = next(c for c in client.recorded_calls if c["round"] == "round_03")
        r3_input = r3_call["body"]["input"]
        # Verify text was delivered with char_count and not sliced with [:1500]
        self.assertIn('"char_count":', r3_input)
        self.assertIn('"content_nature":', r3_input)

    def test_fix3_duplicate_clue_id_halts_fail_closed_and_retains_pending_receipt(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hit_target = self.sources.search("next")["hits"][0]
        target_uid = hit_target["unit_id"]

        # Round 3 repeats clue_01 (colliding with Round 1's clue_01)
        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [{"candidate_index": 1, "source_nature": "primary_book", "reason": "匹配"}],
                "decision": {"action": "read", "chosen_candidate_index": 1, "chosen_unit_id": target_uid, "selection_rationale": "选择"}
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "重复ID线索",
                                  "clue": {"clue_id": "clue_01", "text": "冲突线索", "source_unit_ids": [target_uid],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_repair_dup_id"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        self.assertIn("duplicate clue_id clue_01", report["reading_and_understanding"]["rejection_reason"])
        self.assertEqual(report["reading_and_understanding"]["rejections_count"], 1)
        # Pending receipts must still include the unconfirmed reading receipt
        pending_receipts = report["coverage_summary"]["pending_understanding_receipts"]
        self.assertEqual(len(pending_receipts), 1)

    def test_fix4_invalid_candidate_selection_fails_closed_without_hits0_fallback(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                # Invalid candidate index 999
                "decision": {"action": "read", "chosen_candidate_index": 999, "chosen_unit_id": "nonexistent_uid", "selection_rationale": "无效选择"}
            }),
        })

        out = self.root / "run_repair_invalid_candidate"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        self.assertIn("Candidate selection failed", report["reading_and_understanding"]["rejection_reason"])
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_fix4_invalid_ru_citation_rejected_without_silent_replacement(self):
        # Round 1 returns an unread/invalid RU ID
        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": ["hallucinated_ru_id_12345"],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
        })

        out = self.root / "run_repair_invalid_ru"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        self.assertIn("clue references unread or unresolved official RU", report["reading_and_understanding"]["rejection_reason"])

    def test_fix5_prompt_context_includes_active_clue_ids_and_summary(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hit_target = self.sources.search("next")["hits"][0]
        target_uid = hit_target["unit_id"]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_seed_alpha", "text": "阿尔法线索", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [{"candidate_index": 1, "source_nature": "primary_book", "reason": "匹配"}],
                "decision": {"action": "read", "chosen_candidate_index": 1, "chosen_unit_id": target_uid, "selection_rationale": "选择"}
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "新线索",
                                  "clue": {"clue_id": "clue_seed_beta", "text": "贝塔线索", "source_unit_ids": [target_uid],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_repair_prompt_context"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertTrue(report["reading_and_understanding"]["closed_loop_verified"])
        r3_call = next(c for c in client.recorded_calls if c["round"] == "round_03")
        r3_input = r3_call["body"]["input"]
        r3_instr = r3_call["body"]["instructions"]
        # Verify active_clue_ids passed in prompt
        self.assertIn("clue_seed_alpha", r3_input)
        self.assertIn("clue_seed_alpha", r3_instr)

    def test_fix6_and_7_report_structure_and_estimated_cost_pricing(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hit_target = self.sources.search("next")["hits"][0]
        target_uid = hit_target["unit_id"]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [{"candidate_index": 1, "source_nature": "primary_book", "reason": "匹配"}],
                "decision": {"action": "read", "chosen_candidate_index": 1, "chosen_unit_id": target_uid, "selection_rationale": "选择"}
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "新线索",
                                  "clue": {"clue_id": "clue_02", "text": "线索2", "source_unit_ids": [target_uid],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_repair_report_check"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertEqual(report["pricing"]["accounting_basis"], "estimated_from_preset_rates")
        self.assertIn("basis_note", report["pricing"])
        self.assertEqual(len(report["reading_receipts"]), 2)
        # Verify receipt RU inventory
        r1_units = report["reading_receipts"][0]["units"]
        self.assertEqual(r1_units[0]["unit_ids"], plan["batches"][0]["units"][0]["unit_ids"])
        self.assertGreater(r1_units[0]["char_count"], 0)

    def test_candidate_selection_conflict_fails_closed_without_preference(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        self.assertGreaterEqual(len(hits), 2, "Need at least 2 hits for conflict test")
        hit1 = hits[0]
        hit2 = hits[1]

        # Model provides candidate_index=1, but unit_id of candidate 2!
        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1,
                    "chosen_unit_id": hit2["unit_id"],  # Mismatch with index 1!
                    "selection_rationale": "冲突选择测试"
                }
            }),
        })

        out = self.root / "run_repair_candidate_conflict"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection_reason = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate selection conflict", rejection_reason)
        self.assertIn("chosen_index=1", rejection_reason)
        self.assertIn(hit2["unit_id"], rejection_reason)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_consistent_index_and_uid_succeeds(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        # Model provides candidate_index=1, and unit_id of candidate 1 (consistent)
        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1,
                    "chosen_unit_id": hit1["unit_id"],  # Matches index 1
                    "selection_rationale": "一致选择测试"
                }
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "新线索",
                                  "clue": {"clue_id": "clue_02", "text": "线索2", "source_unit_ids": [hit1["unit_id"]],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_repair_candidate_consistent"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertTrue(report["reading_and_understanding"]["closed_loop_verified"])
        self.assertIsNotNone(report["reading_and_understanding"]["chosen_hit"])
        self.assertEqual(report["reading_and_understanding"]["chosen_hit"]["unit_id"], hit1["unit_id"])

    def test_decision_mode_mock_client_records_mock_mode_and_not_live(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {"action": "read", "chosen_candidate_index": 1, "chosen_unit_id": hit1["unit_id"], "selection_rationale": "选择"}
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "新线索",
                                  "clue": {"clue_id": "clue_02", "text": "线索2", "source_unit_ids": [hit1["unit_id"]],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_repair_decision_mode"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        # Mock client run MUST NOT be marked as live_model_decisions
        self.assertEqual(report["decision_mode"], "mock_model_decisions")
        self.assertNotEqual(report["decision_mode"], "live_model_decisions")
        manifest_path = out / "session" / "manifest.json"
        manifest_data = json.loads(manifest_path.read_text("utf-8"))
        self.assertEqual(manifest_data["decision_mode"], "mock_model_decisions")
        self.assertNotEqual(manifest_data["decision_mode"], "live_model_decisions")

        # Also verify that default ReadingSession.create retains offline replay identifier
        offline_session = ReadingSession.create(
            self.sources, self.catalog, self.root / "offline_session_check",
            baseline_sources=["mihoyo_obc:zh-cn:0"]
        )
        self.assertEqual(offline_session.manifest["decision_mode"], "injected_recorded_or_fixture_decisions_only")

    def test_mock_client_cannot_be_marked_as_live_model_decisions(self):
        client = MockClient({})
        out = self.root / "run_mock_client_live_tag_rejected"
        with self.assertRaisesRegex(ExplorationError, "mock_client run must not be marked as live_model_decisions"):
            live_research.run_live_experiment(
                out, mock_client=client, sources=self.sources, catalog=self.catalog,
                start_content_id="0", decision_mode="live_model_decisions"
            )
        # Also verify with surrounding whitespace
        with self.assertRaisesRegex(ExplorationError, "mock_client run must not be marked as live_model_decisions"):
            live_research.run_live_experiment(
                self.root / "run_mock_client_live_tag_rejected_ws", mock_client=client,
                sources=self.sources, catalog=self.catalog,
                start_content_id="0", decision_mode="  live_model_decisions  "
            )

    def test_decision_mode_live_route_records_live_model_decisions(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        mock_responses = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {"action": "read", "chosen_candidate_index": 1, "chosen_unit_id": hit1["unit_id"], "selection_rationale": "选择"}
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "新线索",
                                  "clue": {"clue_id": "clue_02", "text": "线索2", "source_unit_ids": [hit1["unit_id"]],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_simulated_real_live_call"
        # When mock_client is None, live route is taken. We patch E2SDK so no network/cost occurs.
        with patch.object(live_research, "E2SDK", return_value=mock_responses):
            report = live_research.run_live_experiment(
                out, mock_client=None, env={"JIZHI_API_KEY": "dummy_key", "JIZHI_BASE_URL": "https://dummy"},
                sources=self.sources, catalog=self.catalog, start_content_id="0"
            )
            self.assertEqual(report["decision_mode"], "live_model_decisions")
            manifest_path = out / "session" / "manifest.json"
            manifest_data = json.loads(manifest_path.read_text("utf-8"))
            self.assertEqual(manifest_data["decision_mode"], "live_model_decisions")

    def test_build_final_report_inherits_manifest_decision_mode_and_never_defaults_to_live(self):
        # Test that build_final_report inherits exact decision_mode from session
        out1 = self.root / "runner_out1"
        session1 = ReadingSession.create(
            self.sources, self.catalog, out1 / "session",
            baseline_sources=["mihoyo_obc:zh-cn:0"], decision_mode="custom_test_mode"
        )
        runner1 = live_research.LiveResearchRunner(
            out1, self.sources, self.catalog, {}, mock_client=MockClient({})
        )
        seed_receipt1 = session1.read_next()
        report1 = live_research.build_final_report(
            session1, runner1, out1, seed_receipt1, [], {}
        )
        self.assertEqual(report1["decision_mode"], "custom_test_mode")

        # Test fallback when manifest has no decision_mode or None
        out2 = self.root / "runner_out2"
        session2 = ReadingSession.create(
            self.sources, self.catalog, out2 / "session",
            baseline_sources=["mihoyo_obc:zh-cn:0"], decision_mode="temp_mode"
        )
        runner2 = live_research.LiveResearchRunner(
            out2, self.sources, self.catalog, {}, mock_client=MockClient({})
        )
        seed_receipt2 = session2.read_next()
        session2.manifest.pop("decision_mode", None)
        report2 = live_research.build_final_report(
            session2, runner2, out2, seed_receipt2, [], {}
        )
        self.assertEqual(report2["decision_mode"], "injected_recorded_or_fixture_decisions_only")
        self.assertNotEqual(report2["decision_mode"], "live_model_decisions")

        # Test that build_final_report strictly rejects live_model_decisions when runner has mock_client
        session_tampered = ReadingSession.create(
            self.sources, self.catalog, self.root / "tampered_session" / "session",
            baseline_sources=["mihoyo_obc:zh-cn:0"], decision_mode="custom_mode"
        )
        session_tampered.manifest["decision_mode"] = "live_model_decisions"
        runner_mock = live_research.LiveResearchRunner(
            self.root / "tampered_session", self.sources, self.catalog, {}, mock_client=MockClient({})
        )
        seed_tampered = session_tampered.read_next()
        with self.assertRaisesRegex(ExplorationError, "mock_client run must not be marked as live_model_decisions"):
            live_research.build_final_report(
                session_tampered, runner_mock, self.root / "tampered_session", seed_tampered, [], {}
            )

    def test_candidate_selection_boolean_index_rejected_even_with_valid_uid(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": True,  # Bool rejected!
                    "chosen_unit_id": hit1["unit_id"],
                    "selection_rationale": "布尔索引应严格拒绝"
                }
            }),
        })

        out = self.root / "run_reject_bool_candidate_index"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate index must be a strict integer", rejection)
        self.assertIn("rejected bool", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_invalid_index_type_rejected_with_valid_uid(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": "1",  # String instead of int
                    "chosen_unit_id": hit1["unit_id"],
                    "selection_rationale": "字符串索引应拒绝"
                }
            }),
        })

        out = self.root / "run_reject_str_candidate_index"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate index must be a strict integer", rejection)
        self.assertIn("rejected str", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_out_of_bounds_index_fails_closed_with_valid_uid(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 999,  # Out of bounds
                    "chosen_unit_id": hit1["unit_id"],
                    "selection_rationale": "越界索引应拒绝不能退化为UID"
                }
            }),
        })

        out = self.root / "run_reject_oob_index_with_uid"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate index out of bounds", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_invalid_uid_fails_closed_with_valid_index(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1,
                    "chosen_unit_id": True,  # Illegal unit_id type
                    "selection_rationale": "非法UID类型应拒绝不能退化为Index"
                }
            }),
        })

        out = self.root / "run_reject_invalid_uid_type"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate unit_id must be a non-empty string", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_index_only_strict_integer_succeeds(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1,
                    "chosen_unit_id": None,
                    "selection_rationale": "纯合规整型索引选材"
                }
            }),
            "round_03": json.dumps({
                "research_purpose": "正文研读",
                "clue_updates": [{"operation": "add", "target_clue_id": None, "reason": "线索",
                                  "clue": {"clue_id": "clue_idx_only", "text": "纯索引线索", "source_unit_ids": [hit1["unit_id"]],
                                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}}],
                "updated_summary": "终读总结",
                "unresolved_questions": [],
                "next_action": {"action": "stop", "reason": "完成"}
            }),
        })

        out = self.root / "run_candidate_index_only_success"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertTrue(report["reading_and_understanding"]["closed_loop_verified"])
        self.assertIsNotNone(report["reading_and_understanding"]["chosen_hit"])
        self.assertEqual(report["reading_and_understanding"]["chosen_hit"]["unit_id"], hit1["unit_id"])

    def test_candidate_selection_boolean_false_index_rejected_even_with_valid_uid(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": False,  # Bool False must also be strictly rejected!
                    "chosen_unit_id": hit1["unit_id"],
                    "selection_rationale": "布尔False严格拒绝"
                }
            }),
        })

        out = self.root / "run_reject_bool_false_candidate_index"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate index must be a strict integer", rejection)
        self.assertIn("rejected bool", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_zero_index_fails_closed_with_valid_uid(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 0,  # 0 is falsy int, must fail bounds check!
                    "chosen_unit_id": hit1["unit_id"],
                    "selection_rationale": "0索引越界"
                }
            }),
        })

        out = self.root / "run_reject_zero_candidate_index"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate index out of bounds", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_float_index_rejected_with_valid_uid(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1.0,  # float rejected!
                    "chosen_unit_id": hit1["unit_id"],
                    "selection_rationale": "浮点数严格拒绝"
                }
            }),
        })

        out = self.root / "run_reject_float_candidate_index"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate index must be a strict integer", rejection)
        self.assertIn("rejected float", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_conflict_fails_closed(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        hit1 = hits[0]
        hit2 = hits[1]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1,  # points to hit1
                    "chosen_unit_id": hit2["unit_id"],  # points to hit2
                    "selection_rationale": "冲突判定"
                }
            }),
        })

        out = self.root / "run_reject_conflict_candidate_selection"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Candidate selection conflict", rejection)
        self.assertIn("Index and unit_id must point to the same candidate", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_both_invalid_fails_closed(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 999,  # out of bounds
                    "chosen_unit_id": "nonexistent_uid_value",  # not in hits
                    "selection_rationale": "两者均非法"
                }
            }),
        })

        out = self.root / "run_reject_both_invalid_selection"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("Both candidate index and unit_id are invalid", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])

    def test_candidate_selection_neither_index_nor_uid_provided_fails_closed(self):
        plan = self.sources.plan("mihoyo_obc:zh-cn:0")
        seed_uid = plan["batches"][0]["units"][0]["unit_ids"][0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "种子正文",
                "clues": [{"clue_id": "clue_01", "text": "线索1", "source_unit_ids": [seed_uid],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": None,
                    "chosen_unit_id": None,
                    "selection_rationale": "无索引也无UID"
                }
            }),
        })

        out = self.root / "run_reject_neither_index_nor_uid"
        report = live_research.run_live_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, start_content_id="0"
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        rejection = report["reading_and_understanding"]["rejection_reason"]
        self.assertIn("neither chosen_candidate_index nor chosen_unit_id was provided", rejection)
        self.assertIsNone(report["reading_and_understanding"]["chosen_hit"])


class TestNaviVExpandedResearch(unittest.TestCase):
    """Targeted tests for ExpandedResearchRunner and multi-task progression in navi_v_expanded_research."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sources, self.records = make_sources(self.root)
        self.catalog = build_release_catalog(listing(), source=self.sources.tools.canonical["source"],
                                             locale=self.sources.tools.canonical["locale"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_mock_client_cannot_be_marked_as_live_model_decisions(self):
        client = MockClient({})
        out = self.root / "run_expanded_mock_live_rejected"
        with self.assertRaisesRegex(ExplorationError, "mock_client run must not be marked as live_model_decisions"):
            expanded_research.run_expanded_experiment(
                out, mock_client=client, sources=self.sources, catalog=self.catalog,
                baseline_content_ids=("0", "2"), decision_mode="live_model_decisions"
            )

    def test_expanded_research_four_stage_closure_and_lane_return(self):
        plan0 = self.sources.plan("mihoyo_obc:zh-cn:0")
        uid0 = plan0["batches"][0]["units"][0]["unit_ids"][0]

        hits = self.sources.search("next")["hits"]
        hit = hits[0]
        hit_uid = hit["unit_id"]

        plan2 = self.sources.plan("mihoyo_obc:zh-cn:2")
        uid2 = plan2["batches"][0]["units"][0]["unit_ids"][0]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "任务0初读",
                "clues": [{
                    "clue_id": "clue_01",
                    "text": "任务0初始事实",
                    "source_unit_ids": [uid0],
                    "source_genre": "official_duty_dialogue",
                    "epistemic_status": "duty_testimony"
                }],
                "summary": "任务0总结",
                "next_search": {"query": "next", "reason": "查找下一篇"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1,
                    "chosen_unit_id": hit_uid,
                    "selection_rationale": "选择候选1"
                }
            }),
            "round_03": json.dumps({
                "research_purpose": "旁支研读",
                "clue_updates": [{
                    "operation": "supplement",
                    "target_clue_id": "clue_01",
                    "reason": "补充信息",
                    "clue": {
                        "clue_id": "clue_02",
                        "text": "旁支补充事实",
                        "source_unit_ids": [hit_uid],
                        "source_genre": "official_duty_dialogue",
                        "epistemic_status": "duty_testimony"
                    }
                }],
                "updated_summary": "旁支研读总结",
                "unresolved_questions": []
            }),
            "round_04": json.dumps({
                "research_purpose": "任务2跨任务研读",
                "clue_updates": [{
                    "operation": "contrast",
                    "target_clue_id": "clue_01",
                    "reason": "对比信息",
                    "clue": {
                        "clue_id": "clue_03",
                        "text": "任务2对比事实",
                        "source_unit_ids": [uid2],
                        "source_genre": "official_duty_dialogue",
                        "epistemic_status": "duty_testimony"
                    }
                }],
                "updated_summary": "全景研究总结",
                "unresolved_questions": []
            }),
        })

        out = self.root / "run_expanded_success"
        report = expanded_research.run_expanded_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, baseline_content_ids=("0", "2")
        )

        self.assertTrue(report["baseline_progression"]["version_progression_verified"])
        self.assertEqual(report["baseline_progression"]["tasks_delivered_count"], 2)
        self.assertTrue(report["lane_return"]["lane_return_verified"])
        self.assertEqual(report["lane_return"]["related_delivered_count"], 1)
        self.assertTrue(report["reading_and_understanding"]["closed_loop_verified"])
        self.assertEqual(report["decision_mode"], "mock_model_decisions")
        self.assertEqual(report["reading_and_understanding"]["cross_task_operations_count"], 2)
        self.assertEqual(len(report["reading_and_understanding"]["active_clues"]), 3)
        self.assertTrue(report["resume_verified"])

    def test_expanded_candidate_selection_conflict_fails_closed(self):
        plan0 = self.sources.plan("mihoyo_obc:zh-cn:0")
        uid0 = plan0["batches"][0]["units"][0]["unit_ids"][0]
        hits = self.sources.search("next")["hits"]
        self.assertGreaterEqual(len(hits), 2)
        hit1 = hits[0]
        hit2 = hits[1]

        client = MockClient({
            "round_01": json.dumps({
                "research_purpose": "任务0初读",
                "clues": [{"clue_id": "clue_01", "text": "线索", "source_unit_ids": [uid0],
                           "source_genre": "official_duty_dialogue", "epistemic_status": "duty_testimony"}],
                "summary": "初读总结",
                "next_search": {"query": "next", "reason": "测试"}
            }),
            "round_02": json.dumps({
                "research_purpose": "候选判定",
                "evaluations": [],
                "decision": {
                    "action": "read",
                    "chosen_candidate_index": 1,
                    "chosen_unit_id": hit2["unit_id"],  # Conflicting with index 1
                    "selection_rationale": "冲突判定"
                }
            }),
        })

        out = self.root / "run_expanded_conflict"
        report = expanded_research.run_expanded_experiment(
            out, mock_client=client, sources=self.sources, catalog=self.catalog, baseline_content_ids=("0", "2")
        )
        self.assertFalse(report["reading_and_understanding"]["closed_loop_verified"])
        self.assertIn("Candidate selection conflict", report["reading_and_understanding"]["rejection_reason"])


if __name__ == "__main__":
    unittest.main()
