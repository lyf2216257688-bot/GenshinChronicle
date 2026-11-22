"""G4 offline conversations, immutable evidence, cancellation and crash recovery."""
from copy import deepcopy
from pathlib import Path
import socket
import shutil
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import read, write
from genshin_corpus.generation.clean_chat import validate_messages
from genshin_corpus.generation.clean_chat_transport import ChatSessions
from genshin_corpus.rag.clean_conversation import ConversationStore
from genshin_corpus.rag.clean_context import organize
from genshin_corpus.rag.clean_conversation_graph import fixture_settings, run_turn, saved_result, citation_view
from genshin_corpus.rag.clean_executor import LocalExecutor
from genshin_corpus.rag.clean_mock import FixturePipeline, fixture_inputs, MockOpener, mock_answer
from genshin_corpus.retrieval.clean_based.source import InputReader
from tests.scratch import scratch_root

REPO = Path(__file__).resolve().parents[2]


class Crash(BaseException): pass


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=scratch_root())
        self.addCleanup(self.temp.cleanup)
        self.store = ConversationStore(self.temp.name)
        self.addCleanup(lambda: shutil.rmtree(self.store.root) if self.store.root.exists() else None)
        self.settings = fixture_settings()
        self.sid = self.store.create_chat("Test Mock", self.settings, session="chat-one")
        manifest = fixture_inputs(REPO, self.store.root / "mock_corpus")
        self.reader = InputReader(manifest); self.addCleanup(self.reader.close)
        self.pipeline = FixturePipeline(self.reader, REPO)
        self.senders = []
        blocked = patch.object(socket.socket, "connect", side_effect=AssertionError("G4 has no network"))
        mock = blocked.start(); self.addCleanup(blocked.stop); self.addCleanup(mock.assert_not_called)

    def turn(self, token="one", text="原文的条件是什么？", **kwargs):
        return self.store.enqueue(self.sid, token, text, **kwargs)

    def opener(self, packet, scenario):
        sender = MockOpener(mock_answer(packet, scenario), disconnect=scenario == "disconnect")
        self.senders.append(sender); return sender

    def run_turn(self, tid, **kwargs):
        return run_turn(self.store, tid, self.pipeline, opener_factory=self.opener, **kwargs)

    def bridge(self):
        return ChatSessions(self.store, self.store.chat(self.sid)["campaign"], self.settings["limits"])

    def test_full_dialogue_reopen_correction_and_new_chat_isolation(self):
        first = self.turn(note_kind="user_assertion"); self.run_turn(first)
        premise = self.store.notes(self.sid)[0]["note_id"]
        for i, text in enumerate(("依据在哪里？", "我补充了一条尚待核验的材料。"), 2):
            self.run_turn(self.turn(str(i), text))
        correction = self.turn("4", "撤回旧前提，重新分析。", note_kind="correction_fact", targets=[premise], scenario="retract")
        result = self.run_turn(correction)
        self.assertIn("撤回", result["answer_text"])
        self.store = ConversationStore(self.store.root)
        resumed = self.turn("5", "关闭后重开，继续查看。")
        result = self.run_turn(resumed)
        ctx = self.store.load(result["context_ref"])
        self.assertEqual(ctx["research_state"]["notes"][0]["kind"], "correction_fact")
        self.assertTrue(ctx["research_state"]["needs_review"])
        other = self.store.create_chat("New", self.settings)
        self.assertEqual(self.store.history(other), [])
        self.assertEqual(self.store.notes(other), [])
        self.assertEqual(len(self.store.history(self.sid)), 10)
        with self.assertRaises(ValueError): self.store.enqueue(other, "x", "Foreign", note_kind="correction_fact", targets=[premise])

    def test_submit_refresh_double_click_and_stable_draft(self):
        d = self.store.edit_draft(self.sid, "hello")
        a = self.store.enqueue(self.sid, d["token"], "hello")
        b = self.store.enqueue(self.sid, self.store.draft(self.sid)["token"], "hello")
        self.assertEqual(a, b)
        self.run_turn(a); self.run_turn(b)
        self.assertEqual(sum(s.calls for s in self.senders), 1)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)
        self.assertEqual(len(self.store.history(self.sid)), 2)
        self.assertEqual(ConversationStore(self.store.root).draft(self.sid), d)
        self.assertNotEqual(self.store.edit_draft(self.sid, "next")["token"], d["token"])
        with self.assertRaises(ValueError): self.store.enqueue(self.sid, d["token"], "different")
        with self.assertRaises(ValueError): self.store.enqueue(self.sid, d["token"], "hello", scenario="limited")

    def test_citations_are_turn_and_packet_scoped_with_parent_readback(self):
        a = self.turn(); ra = self.run_turn(a)
        b = self.turn("two", "继续"); rb = self.run_turn(b)
        va = citation_view(self.store, a, "E01", packet_id=ra["citations"][0]["packet_id"])
        vb = citation_view(self.store, b, "E01", packet_id=rb["citations"][0]["packet_id"])
        self.assertNotEqual(va["packet_id"], vb["packet_id"])
        self.assertEqual(va["parent_status"], "available")
        self.assertEqual(va["parent"]["source_snapshot"], va["receipt"]["source_snapshot"])
        with self.assertRaises(ValueError): citation_view(self.store, a, "E01", packet_id=vb["packet_id"])
        with self.assertRaises(ValueError): citation_view(self.store, a, "E999")

    def test_automatic_extractive_organization_keeps_full_local_history(self):
        self.settings.update(history_bytes=6500, excerpt_chars=50, recent_turns=1)
        # New scope fixes these fixture values, without mutating the old chat.
        self.sid = self.store.create_chat("Small context", self.settings)
        for i in range(4):
            self.run_turn(self.turn(str(i), "比较资料中的条件。" * 50))
        tid = self.turn("last", "它的条件呢？")
        ctx = organize(self.store, tid, history_bytes=6500, recent_turns=1, excerpt_chars=50)
        self.assertTrue(ctx["organization"]["compressed"])
        self.assertEqual(len(ctx["organization"]["original_message_ids"]), 8)
        self.assertEqual(len(self.store.history(self.sid)), 9)
        self.assertTrue(all(s["message_id"] for s in ctx["summary"]))
        self.assertEqual(ctx["research_state"]["supported_judgments"], [])

    def test_correction_invalidates_dependency_chain_and_unscoped_premises(self):
        t = self.turn(note_kind="user_assertion"); self.run_turn(t)
        premise = self.store.notes(self.sid)[0]["note_id"]
        tid = self.turn("two", "一个依赖旧前提的假说", note_kind="hypothesis", targets=[premise]); self.run_turn(tid)
        corrected = self.turn("three", "否定旧前提", note_kind="correction_fact")
        notes = self.store.notes(self.sid)
        self.assertEqual([n["status"] for n in notes], ["needs_review", "needs_review", "active"])
        ctx = organize(self.store, corrected, history_bytes=24000, recent_turns=2, excerpt_chars=50)
        self.assertEqual(len(ctx["research_state"]["notes"]), 1)
        self.assertNotIn("一个依赖旧前提的假说", str(ctx["recent"]))
        self.assertEqual(ctx["research_state"]["notes"][0]["authority"], "unverified_user_or_hypothesis")

    def test_user_intent_adopted_but_story_assertion_remains_unverified(self):
        self.run_turn(self.turn(note_kind="requirement"))
        self.run_turn(self.turn("two", "请简短", note_kind="correction_intent"))
        tid = self.turn("three", "我认为条件已成立", note_kind="user_assertion", scenario="source_conflict")
        result = self.run_turn(tid)
        active = [n for n in self.store.notes(self.sid) if n["status"] == "active"]
        self.assertEqual(active[0]["authority"], "user_requirement")
        self.assertEqual(active[1]["authority"], "unverified_user_or_hypothesis")
        self.assertIn("不得覆盖", result["answer_text"])

    def test_complete_tool_protocol_unit_is_never_sliced(self):
        tid = self.turn()
        tools = [{"type": "function", "function": {"name": "read", "description": "Read fixture", "parameters": {"type": "object",
            "properties": {}, "required": [], "additionalProperties": False}}}]
        unit = [{"role": "assistant", "content": None, "reasoning_content": "preserve exactly",
                 "tool_calls": [{"id": "c", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c", "content": "result"}]
        ctx = organize(self.store, tid, history_bytes=24000, recent_turns=1, excerpt_chars=50,
                       protocol_history=unit, tools=tools)
        self.assertEqual(ctx["protocol_history"], unit)
        with self.assertRaises(ValueError): organize(self.store, tid, history_bytes=24000, recent_turns=1,
                                                    excerpt_chars=50, protocol_history=unit[:1], tools=tools)

    def test_oversized_user_requirement_fails_without_silent_truncation(self):
        tid = self.turn(text="必须保留的完整要求" * 1000, note_kind="requirement")
        with self.assertRaisesRegex(ValueError, "CONTEXT.*LIMIT"):
            organize(self.store, tid, history_bytes=2000, recent_turns=1, excerpt_chars=50)
        self.assertEqual(self.store.history(self.sid)[0]["content"], "必须保留的完整要求" * 1000)

    def test_drafts_have_real_ordered_events_and_never_claim_final(self):
        tid = self.turn(); result = self.run_turn(tid)
        events = self.store.events(tid)
        seqs = [e["sequence"] for e in events]
        self.assertEqual(seqs, sorted(set(seqs)))
        drafts = [e for e in events if e["kind"] == "answer_draft"]
        self.assertTrue(drafts)
        self.assertTrue(all(e["final"] is False for e in drafts))
        checking = next(e["sequence"] for e in events if e.get("stage") == "checking")
        final = next(e["sequence"] for e in events if e["kind"] == "g4_result_ready")
        self.assertLess(max(e["sequence"] for e in drafts), checking)
        self.assertLess(checking, final)
        self.assertEqual(self.store.events(tid, after=seqs[-2]), events[-1:])
        self.assertEqual(result["semantic_acceptance"], "NOT_REVIEWED")

    def test_stream_disconnect_retains_sources_and_only_draft(self):
        tid = self.turn(scenario="disconnect"); result = self.run_turn(tid)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["answer_text"])
        self.assertTrue(result["sources_ref"])
        self.assertTrue(any(e["kind"] == "answer_draft" for e in self.store.events(tid)))
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)
        self.run_turn(tid)
        self.assertEqual(sum(s.calls for s in self.senders), 1)

    def test_cancel_before_dispatch_has_zero_reservations(self):
        tid = self.turn(); self.store.cancel(tid)
        result = self.run_turn(tid)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)
        with self.assertRaises(ValueError): self.store.request_continue(tid)

    def test_cancel_after_packet_stops_new_admission_and_retains_sources(self):
        tid = self.turn()
        def cancel(point):
            if point == "packet_saved_before_checkpoint": self.store.cancel(tid)
        result = self.run_turn(tid, fault=cancel)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["sources_ref"])
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)

    def test_cancel_during_stream_retains_unknown_reservation(self):
        tid = self.turn()
        original = self.store.event
        def event(tid, key, value):
            seq = original(tid, key, value)
            if value["kind"] == "answer_draft": self.store.cancel(tid)
            return seq
        with patch.object(self.store, "event", side_effect=event): result = self.run_turn(tid)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["answer_text"])
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)

    def test_known_sealed_result_survives_later_cancellation(self):
        tid = self.turn()
        def cancel(point):
            if point == "chat_sealed_before_checkpoint": self.store.cancel(tid)
        result = self.run_turn(tid, fault=cancel)
        self.assertEqual(result["call_state"], "CONTENT_VERIFIED")
        self.assertTrue(result["answer_text"])
        self.assertEqual(result["stop_reason"], "user_cancelled")

    def test_graph_durable_boundaries_replay_without_duplicate_send(self):
        points = ["context_saved_before_checkpoint", "packet_saved_before_checkpoint", "chat_prepared_before_checkpoint",
                  "chat_sealed_before_checkpoint", "result_saved_before_checkpoint"]
        for i, point in enumerate(points):
            with self.subTest(point=point):
                tid = self.turn("crash-" + str(i))
                def crash(at):
                    if at == point: raise Crash()
                with self.assertRaises(Crash): self.run_turn(tid, fault=crash)
                with self.assertRaisesRegex(ValueError, "explicit continue"): self.run_turn(tid)
                self.run_turn(tid, continue_turn=True)
                self.assertEqual(self.bridge().batch.accounting()["request_reservations"], i + 1)
        self.assertEqual(sum(s.calls for s in self.senders), 5)

    def test_missing_source_and_result_files_fail_closed(self):
        tid = self.turn(); result = self.run_turn(tid)
        sources = self.store.load(result["sources_ref"])
        ref = next(iter(sources["reads"].values()))
        (self.store.root / ref["path"]).write_text("{}", encoding="utf8")
        with self.assertRaises(ValueError): citation_view(self.store, tid, "E01")
        ready = next(e for e in self.store.events(tid) if e["kind"] == "g4_result_ready")
        (self.store.root / ready["ref"]["path"]).unlink()
        with self.assertRaises(FileNotFoundError): saved_result(self.store, tid)

    def test_citation_failure_cannot_publish_final_answer(self):
        tid = self.turn()
        result = run_turn(self.store, tid, self.pipeline, opener_factory=lambda p, s: MockOpener("fabricated [E999]"))
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["answer_text"])
        self.assertEqual(result["citation_validation"]["status"], "FAIL")
        self.assertEqual(result["stop_reason"], "citation_validation_failed")
        self.assertNotIn("fabricated", self.store.history(self.sid)[-1]["content"])

    def test_campaign_budget_survives_turns_and_reopen(self):
        settings = fixture_settings(); settings["limits"]["campaign_requests"] = 1
        self.sid = self.store.create_chat("One reservation", settings)
        self.run_turn(self.turn())
        self.store = ConversationStore(self.store.root)
        result = self.run_turn(self.turn("second"))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stop_reason"], "budget_or_admission_deferred")
        self.assertEqual(sum(s.calls for s in self.senders), 1)

    def test_source_failure_is_audited_and_does_not_forge_complete_answer(self):
        tid = self.turn()
        from genshin_corpus.generation.clean_evidence import EvidenceReader
        original = EvidenceReader.read
        def fail_parent(reader, handle, scope="chunk"):
            if scope == "natural_parent": raise ValueError("source failure")
            return original(reader, handle, scope)
        with patch.object(EvidenceReader, "read", fail_parent): result = self.run_turn(tid)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(self.store.load(result["sources_ref"])["source_failures"])

    def test_unsealed_reserved_call_stays_unknown_and_is_not_resent(self):
        tid = self.turn()
        with patch("genshin_corpus.generation.clean_chat_transport.attempt", side_effect=Crash()):
            with self.assertRaises(Crash): self.run_turn(tid)
        result = self.run_turn(tid, continue_turn=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["call_state"], "UNKNOWN")
        self.assertEqual(result["stop_reason"], "unknown_requires_reconciliation")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)
        self.assertEqual(sum(s.calls for s in self.senders), 0)

    def test_executor_queues_chats_and_reopening_does_not_run_old_jobs(self):
        # Do not allow the executor to close the fixture reader owned by this test.
        queued = self.turn()
        worker = LocalExecutor(self.store.root, REPO, delay=0)
        self.addCleanup(worker.close)
        time.sleep(.15)
        self.assertIsNone(saved_result(self.store, queued))
        worker.schedule(queued, continue_turn=True)
        sid = self.store.create_chat("Another", self.settings)
        second = self.store.enqueue(sid, "two", "新对话")
        worker.schedule(second)
        self.assertTrue(worker.wait_idle(15))
        self.assertEqual(self.store.job(queued)["status"], "completed")
        self.assertEqual(self.store.job(second)["status"], "completed")
        worker.close()
        self.assertFalse(worker.thread.is_alive())
        with LocalExecutorContext(self.store.root, REPO) as reopened:
            self.assertIsNone(reopened.active)
            self.assertFalse(reopened.pending)

    def test_application_shutdown_requires_explicit_continue(self):
        tid = self.turn()
        worker = LocalExecutor(self.store.root, REPO, delay=.1)
        worker.schedule(tid)
        end = time.monotonic() + 10
        while not any(e["kind"] == "answer_draft" for e in self.store.events(tid)) and time.monotonic() < end:
            time.sleep(.03)
        worker.close()
        self.assertFalse(self.store.cancelled(tid))
        self.assertEqual(self.store.job(tid)["status"], "interrupted")
        self.run_turn(tid, continue_turn=True)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)

    def test_live_mode_and_second_executor_are_rejected(self):
        bad = deepcopy(self.settings); bad["mode"] = "LIVE"
        with self.assertRaisesRegex(ValueError, "G4_LIVE_DISABLED"): self.store.create_chat("bad", bad)
        worker = LocalExecutor(self.store.root, REPO, delay=0)
        self.addCleanup(worker.close)
        with self.assertRaises(OSError): LocalExecutor(self.store.root, REPO, delay=0)

    def test_cancel_after_uncheckpointed_sources_reuses_published_packet(self):
        tid = self.turn()
        def crash(point):
            if point == "packet_saved_before_checkpoint": raise Crash()
        with self.assertRaises(Crash): self.run_turn(tid, fault=crash)
        self.store.cancel(tid)
        result = self.run_turn(tid, continue_turn=True)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(self.store.load(result["sources_ref"])["packet"]["evidence"])
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)

    def test_late_cancel_preserves_result_published_before_checkpoint(self):
        tid = self.turn()
        def crash(point):
            if point == "result_saved_before_checkpoint": raise Crash()
        with self.assertRaises(Crash): self.run_turn(tid, fault=crash)
        self.store.cancel(tid)
        result = self.run_turn(tid, continue_turn=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stop_reason"], "fixture_complete")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)

    def test_stale_editor_does_not_rotate_next_submit_token_twice(self):
        old = self.store.edit_draft(self.sid, "first")
        self.store.enqueue(self.sid, old["token"], "first")
        first = self.store.edit_draft(self.sid, "next")
        with patch.object(self.store, "draft", return_value=old):
            second = self.store.edit_draft(self.sid, "next")
        self.assertEqual(first, second)

    def test_submission_publication_gap_preserves_correction_on_continue(self):
        with patch.object(self.store, "message", side_effect=Crash()):
            with self.assertRaises(Crash): self.turn(note_kind="correction_fact")
        tid = self.store.jobs(self.sid)[0]["turn_id"]
        result = self.run_turn(tid, continue_turn=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.store.notes(self.sid)[0]["kind"], "correction_fact")

    def test_committed_submission_without_job_recovers_metadata_without_dispatch(self):
        original = self.store.submit
        def crash(*args):
            original(*args)
            raise Crash()
        with patch.object(self.store, "submit", side_effect=crash):
            with self.assertRaises(Crash): self.turn(note_kind="correction_intent")
        self.store = ConversationStore(self.store.root)
        self.assertEqual(self.store.jobs(self.sid)[0]["status"], "queued")
        self.assertEqual(self.store.notes(self.sid)[0]["kind"], "correction_intent")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)

    def test_result_published_before_completion_event_repairs_without_send(self):
        tid = self.turn()
        original = self.store.event
        def crash(t, key, value):
            if key == "turn_complete": raise Crash()
            return original(t, key, value)
        with patch.object(self.store, "event", side_effect=crash):
            with self.assertRaises(Crash): self.run_turn(tid)
        self.assertIsNotNone(saved_result(self.store, tid))
        self.run_turn(tid)
        self.assertEqual(len(self.store.history(self.sid)), 2)
        self.assertEqual(sum(s.calls for s in self.senders), 1)
        self.assertEqual(self.store.job(tid)["status"], "completed")

    def test_incompatible_graph_code_blocks_resume(self):
        tid = self.turn()
        def crash(point):
            if point == "chat_prepared_before_checkpoint": raise Crash()
        with self.assertRaises(Crash): self.run_turn(tid, fault=crash)
        with patch("genshin_corpus.rag.clean_conversation_graph.code_identity", return_value={"other": "code"}):
            with self.assertRaisesRegex(ValueError, "INCOMPATIBLE_G4_CODE"):
                self.run_turn(tid, continue_turn=True)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)


class LocalExecutorContext:
    def __init__(self, root, repo): self.worker = LocalExecutor(root, repo, delay=0)
    def __enter__(self): return self.worker
    def __exit__(self, *args): self.worker.close()


if __name__ == "__main__": unittest.main()
