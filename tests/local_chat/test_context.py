import json
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import digest
from genshin_corpus.local_chat import context, contract
from genshin_corpus.local_chat.store import ProductStore
from genshin_corpus.rag.clean_context import SYSTEM, assembly_context
from genshin_corpus.rag.clean_conversation_graph import fixture_settings, messages
from tests.scratch import scratch_root


def message(turn, role, content, **extra):
    return {"turn_id": digest(turn), "message_id": digest([turn, role]), "role": role, "content": content, **extra}


class Timeline:
    def __init__(self, rows, *, question="追问", notes=(), artifacts=None):
        self.rows = rows
        self.question = question
        self.active_notes = list(notes)
        self.artifacts = artifacts or {}

    def turn(self, tid):
        return {"session": "chat", "question": self.question}

    def history(self, session, *, before):
        return self.rows

    def notes(self, session, *, before):
        return self.active_notes

    def load(self, ref):
        return self.artifacts[ref]


class ProductContextTests(unittest.TestCase):
    def test_long_history_retains_local_addresses_and_prioritizes_recent_user_text(self):
        rows = [message(turn, role, "甲") for turn in range(220) for role in ("user", "assistant")]
        rows += [message(220, "user", "只讨论须弥。"), message(220, "assistant", "乙" * 22000)]
        store = Timeline(rows)
        value = context.organize(store, "follow", fixture_settings(), system=SYSTEM)
        model_context = assembly_context(store.turn("follow"), value)
        wire = json.loads(messages(model_context, [])[1]["content"])
        self.assertIn("只讨论须弥。", {row["content"] for row in wire["conversation"]["recent"]})
        self.assertEqual(value["organization"]["original_message_ids"], [row["message_id"] for row in rows])
        self.assertIn(rows[0]["message_id"], value["organization"]["omitted_message_ids"])
        self.assertNotIn(rows[0]["message_id"], str(wire))
        self.assertEqual(context.estimate_tokens({"question": wire["question"], "conversation": wire["conversation"]}),
                         value["organization"]["dialogue_bytes"])
        self.assertLessEqual(value["organization"]["dialogue_bytes"], fixture_settings()["history_bytes"])

    def test_prior_research_only_requires_review_for_affected_turns_and_consumes_budget(self):
        rows = [message(1, "user", "旧问题"), message(1, "assistant", "旧结论", result_ref="first"),
                message(2, "user", "只讨论须弥。"), message(2, "assistant", "被纠正的答复", result_ref="second")]
        stale = {"note_id": rows[3]["message_id"], "message_ids": [rows[3]["message_id"]],
                 "turn_id": rows[3]["turn_id"], "status": "needs_review"}
        artifacts = {
            "first": {"research_ref": "r1", "stop_reason": "sufficient"},
            "second": {"research_ref": "r2", "stop_reason": "sufficient"},
            "r1": {"groups": [], "reads": {}, "research": {"hypothesis": "独立的旧假说"}},
            "r2": {"groups": [], "reads": {}, "research": {"hypothesis": "被纠正的旧假说"}},
        }
        store = Timeline(rows, question="先核对新的剧情前提。", notes=[stale], artifacts=artifacts)
        value = context.organize(store, "follow", fixture_settings(), system=SYSTEM, include_research=True)
        recent = {row["message_id"]: row for row in value["recent"]}
        self.assertEqual(recent[rows[2]["message_id"]]["content"], rows[2]["content"])
        self.assertTrue(recent[rows[3]["message_id"]]["requires_review"])
        prior = value["research_state"]["prior_research"]
        self.assertEqual(prior[0]["research"]["hypothesis"], "独立的旧假说")
        self.assertEqual(prior[1]["status"], "needs_review")
        self.assertEqual(prior[1]["research"], {})

        artifacts["r1"]["research"]["hypothesis"] *= 5000
        value = context.organize(store, "follow", fixture_settings(), system=SYSTEM, include_research=True)
        self.assertEqual([entry["research_ref"] for entry in value["research_state"]["prior_research"]], ["r2"])

    def test_many_plot_corrections_preserve_current_scope_and_audit_omissions(self):
        rows, notes = [], []
        requirement = message("scope", "user", "只讨论须弥。")
        rows.append(requirement)
        notes.append({"note_id": requirement["message_id"], "turn_id": requirement["turn_id"],
                      "message_ids": [requirement["message_id"]], "status": "active",
                      "kind": "requirement", "text": requirement["content"]})
        for turn in range(40):
            target = message(turn, "assistant", "已撤回的断言")
            revision = message(f"corr-{turn}", "user", "待核验的剧情修正" * 160)
            rows.extend([target, revision])
            notes.extend([{"note_id": target["message_id"], "turn_id": target["turn_id"],
                           "message_ids": [target["message_id"]], "status": "needs_review"},
                          {"note_id": revision["message_id"], "turn_id": revision["turn_id"],
                           "message_ids": [revision["message_id"]], "status": "active",
                           "kind": "correction_fact", "text": revision["content"]}])
        settings = fixture_settings()
        store = Timeline(rows, notes=notes)
        self.assertLess(context.check_context_budget(store.question, notes, current_message_id="current",
                                                     history_bytes=settings["history_bytes"]), 1500)
        value = context.organize(store, "follow", settings, system=SYSTEM)
        self.assertEqual(value["research_state"]["notes"][0]["text"], requirement["content"])
        self.assertTrue(value["organization"]["omitted_note_ids"])
        self.assertEqual(len(value["organization"]["requires_review_message_ids"]), 40)
        self.assertNotIn("已撤回的断言", str(assembly_context(store.turn("follow"), value)))

    def test_snapshot_replays_file_before_event_without_reading_new_history(self):
        scratch = scratch_root()
        with TemporaryDirectory(dir=("\\\\?\\" + str(scratch)) if os.name == "nt" else scratch) as folder:
            store = ProductStore(folder)
            sid = store.new_chat("MOCK", "上下文恢复")["id"]
            settings = fixture_settings()
            binding = {"id": "offline-context-fixture", "campaign": "offline-context-fixture", "settings": settings}

            def submit(text):
                data = contract.Draft(**{**store.get_draft(sid), "text": text})
                tid, _ = store.submit_draft(sid, data, lambda mode: binding)
                store.ensure_submission(tid)
                return tid

            first = submit("旧问题")
            later = submit("追问")
            with patch.object(store, "event", side_effect=OSError("interrupted before context event")):
                with self.assertRaises(OSError):
                    context.snapshot(store, later, settings, system=SYSTEM)
            original = store.published_artifact(later, "context")
            one = store.load(original)
            late_answer = store.message(first, "assistant", "恢复旧轮后才产生的回复")
            self.assertNotIn(late_answer, one["organization"]["original_message_ids"])
            self.assertEqual(context.snapshot(store, later, settings, system=SYSTEM), original)
            self.assertEqual(store.load(original), one)
            (store.root / original["path"]).write_text("{}", encoding="utf8")
            with self.assertRaisesRegex(ValueError, "Published product context changed"):
                context.snapshot(store, later, settings, system=SYSTEM)
