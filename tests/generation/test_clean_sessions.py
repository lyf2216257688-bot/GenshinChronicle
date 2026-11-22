"""Fault-injected G1 fixtures: no network, local corpus or model inference."""
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus import model_calls as calls
from genshin_corpus.evidence_io import read, seal, sha, write
from genshin_corpus.model_call_sessions import SessionCalls
from genshin_corpus.rag.clean_store import Store
from genshin_corpus.rag.clean_graph import mock_attempt, run_mock
from tests.scratch import scratch_root

LIMITS = {"campaign_requests": 5, "campaign_tokens": 100, "turn_requests": 2, "turn_tokens": 20}


class Crash(BaseException):
    pass


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=scratch_root())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.tid = self.store.submit("s1", "submit", "问题", "fixture")
        self.bridge = SessionCalls(self.store, "fixture", LIMITS)
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()

    def prepare(self, key="op", tid=None):
        return self.bridge.prepare(tid or self.tid, key, {"question": "问题", "mode": "MOCK"}, allowance=10)

    def test_submit_idempotence_restart_and_chat_isolation(self):
        fresh = Store(self.root)
        self.assertEqual(fresh.submit("s1", "submit", "问题", "fixture"), self.tid)
        with self.assertRaises(ValueError): fresh.submit("s1", "submit", "改了", "fixture")
        other = fresh.submit("s2", "submit", "问题", "fixture")
        self.assertNotEqual(other, self.tid)
        self.store.event(self.tid, "e", {"kind": "draft"})
        self.assertEqual(fresh.events(other), [])
        with self.assertRaises(ValueError): self.store.event(self.tid, "spoof", {"session_id": "s2"})

    def test_stable_operation_body_and_budget_root_bindings(self):
        op = self.prepare(); self.assertEqual(op, self.prepare())
        with self.assertRaises(ValueError): self.bridge.prepare(self.tid, "op", {"different": True}, allowance=10)
        with self.assertRaises(ValueError): SessionCalls(self.store, "fixture", {**LIMITS, "turn_requests": 3})
        with self.assertRaises(ValueError): SessionCalls(self.store, "other", LIMITS).prepare(self.tid, "op", {}, allowance=10)
        tid = self.store.submit("s1", "second", "问题", "fixture")
        op2 = self.prepare(tid=tid)
        self.assertNotEqual(self.bridge.operation(op)["fingerprint"], self.bridge.operation(op2)["fingerprint"])

    def test_prepared_and_sealed_replay_send_once(self):
        op = self.prepare(); count = []
        self.assertEqual(self.bridge.reconcile(op)["status"], "prepared")
        def send(p, h): count.append(1); return mock_attempt(p, h)
        a = self.bridge.execute_mock(op, send)
        b = SessionCalls(Store(self.root), "fixture", LIMITS).execute_mock(op, send)
        self.assertEqual(a, b); self.assertEqual(len(count), 1)
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 1)

    def test_reservation_crash_is_unknown_and_not_resent(self):
        op = self.prepare(); original = self.bridge.batch.reserve
        def reserve(*args): original(*args); raise Crash()
        with patch.object(self.bridge.batch, "reserve", side_effect=reserve), self.assertRaises(Crash):
            self.bridge.execute_mock(op, mock_attempt)
        self.assertEqual(self.bridge.reconcile(op)["status"], "UNKNOWN")
        result = self.bridge.execute_mock(op, lambda *a: self.fail("no resend"))
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(self.bridge.batch.accounting()["unknown_usage_requests"], 1)

    def test_first_bytes_crash_unknown_survives_restart(self):
        op = self.prepare()
        def send(p, h):
            write(p / "raw-fragment.json", {"partial": True}); raise Crash()
        with self.assertRaises(Crash): self.bridge.execute_mock(op, send)
        result = SessionCalls(Store(self.root), "fixture", LIMITS).execute_mock(op, lambda *a: self.fail("no resend"))
        self.assertEqual(result["state"], "UNKNOWN")

    def test_attempt_sealed_before_finish_repairs_zero_send(self):
        op = self.prepare()
        def send(p, h): mock_attempt(p, h); raise Crash()
        with self.assertRaises(Crash): self.bridge.execute_mock(op, send)
        self.assertEqual(self.bridge.reconcile(op)["status"], "pending_finalize")
        result = self.bridge.execute_mock(op, lambda *a: self.fail("sealed attempt must replay"))
        self.assertEqual(result["state"], "CONTENT_VERIFIED")
        self.assertEqual(self.bridge.batch.accounting()["known_tokens"], 3)

    def test_call_seal_before_ledger_outcome_repairs_and_reuses(self):
        op = self.prepare(); original = calls.seal
        def seal_then_crash(p): original(p); raise Crash()
        with patch.object(calls, "seal", side_effect=seal_then_crash), self.assertRaises(Crash):
            self.bridge.execute_mock(op, mock_attempt)
        result = self.bridge.execute_mock(op, lambda *a: self.fail("no resend"))
        self.assertEqual(result["state"], "CONTENT_VERIFIED")
        fp = self.bridge.operation(op)["fingerprint"]
        self.assertTrue((self.bridge.root / "ledger" / fp / "outcome.json").exists())

    def test_missing_derived_budget_and_ledger_receipts_repair_idempotently(self):
        op = self.prepare(); self.bridge.execute_mock(op, mock_attempt)
        slot = next((self.bridge.batch.root / "requests").glob("*/outcome.json"))
        fp = self.bridge.operation(op)["fingerprint"]
        ledger = self.bridge.root / "ledger" / fp / "outcome.json"
        a, b = sha(slot), sha(ledger)
        slot.unlink(); ledger.unlink()  # Deliberate destruction of TEST-only derived receipts.
        self.bridge.reconcile(op); self.bridge.reconcile(op)
        self.assertEqual((sha(slot), sha(ledger)), (a, b))

    def test_corrupt_budget_outcome_and_missing_evidence_block(self):
        op = self.prepare(); self.bridge.execute_mock(op, mock_attempt)
        slot = next((self.bridge.batch.root / "requests").glob("*/outcome.json"))
        slot.write_text("{}")
        with self.assertRaises(ValueError): self.bridge.reconcile(op)
        result = self.bridge.root / "calls" / op / "result.json"
        result.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)): self.bridge.execute_mock(op, mock_attempt)

    def test_pending_retry_reconcile_does_not_send(self):
        bridge = SessionCalls(self.store, "retry", LIMITS, policy=calls.Policy(max_attempts=2, initial_delay=0))
        tid = self.store.submit("s2", "r", "问题", "retry")
        op = bridge.prepare(tid, "op", {}, allowance=10)
        def send(p, h):
            write(p / "trace.json", {"events": ["connection.connect_tcp.failed"]})
            write(p / "result.json", {"state": "FAILED", "calls": 0, "request_count": 0})
            seal(p); raise Crash()
        with self.assertRaises(Crash): bridge.execute_mock(op, send)
        self.assertEqual(bridge.reconcile(op)["status"], "pending_retry")
        self.assertEqual(bridge.batch.accounting()["request_reservations"], 1)
        bridge.execute_mock(op, mock_attempt)
        self.assertEqual(bridge.batch.accounting()["request_reservations"], 2)

    def test_turn_and_campaign_budget_include_every_attempt_and_unknown(self):
        bridge = SessionCalls(self.store, "small", {"campaign_requests": 2, "campaign_tokens": 20,
                                                  "turn_requests": 1, "turn_tokens": 10})
        tid = self.store.submit("b", "1", "问题", "small")
        op = bridge.prepare(tid, "one", {}, allowance=10)
        def crash(p, h): raise Crash()
        with self.assertRaises(Crash): bridge.execute_mock(op, crash)
        next_op = bridge.prepare(tid, "two", {}, allowance=10)
        with self.assertRaises(calls.Deferred): bridge.execute_mock(next_op, mock_attempt)
        tid2 = self.store.submit("b", "2", "问题", "small")
        bridge.execute_mock(bridge.prepare(tid2, "op", {}, allowance=10), mock_attempt)
        tid3 = self.store.submit("b", "3", "问题", "small")
        with self.assertRaises(calls.Deferred): bridge.execute_mock(bridge.prepare(tid3, "op", {}, allowance=10), mock_attempt)

    def test_cancel_before_reserve_and_after_admission(self):
        op = self.prepare(); self.store.cancel(self.tid)
        with self.assertRaises(calls.Deferred): self.bridge.execute_mock(op, mock_attempt)
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 0)
        tid2 = self.store.submit("s2", "r", "问题", "fixture")
        op2 = self.prepare(tid=tid2); original = self.bridge.reserve
        def reserve(*args):
            slot = original(*args); self.store.cancel(tid2); return slot
        with patch.object(self.bridge, "reserve", side_effect=reserve):
            result = self.bridge.execute_mock(op2, lambda *a: self.fail("cancelled before send"))
        self.assertEqual(result["state"], "CANCELLED_BEFORE_SEND")
        self.assertEqual(self.bridge.batch.accounting()["unknown_usage_requests"], 1)
        events = self.store.events(tid2)
        admitted = next(e["sequence"] for e in events if e["kind"] == "dispatch_admitted")
        cancelled = next(e["sequence"] for e in events if e["kind"] == "cancel_requested")
        self.assertLess(admitted, cancelled)

    def test_cancel_does_not_overwrite_a_completed_result(self):
        op = self.prepare()
        def send(p, h):
            result = mock_attempt(p, h); self.store.cancel(self.tid); return result
        self.assertEqual(self.bridge.execute_mock(op, send)["state"], "CONTENT_VERIFIED")

    def test_events_replay_sequence_and_sealed_tool_receipt_repair(self):
        seq = self.store.event(self.tid, "tool-1", {"kind": "tool", "tool_call_id": "c1"})
        self.assertEqual(seq, self.store.event(self.tid, "tool-1", {"kind": "tool", "tool_call_id": "c1"}))
        self.assertEqual(self.store.events(self.tid, after=seq), [])
        with self.store.connect() as db: db.execute("DELETE FROM events WHERE turn=?", (self.tid,))
        self.store.event(self.tid, "tool-1", {"kind": "tool", "tool_call_id": "c1"})
        self.assertEqual(len(self.store.events(self.tid)), 1)
        ref = next((self.root / "events" / self.tid).glob("*.json")); ref.write_text("{}")
        with self.assertRaises(ValueError): self.store.events(self.tid)

    def test_application_session_gpu_locks_compete(self):
        other = Store(self.root)
        for lock in ("locks/application.lock", "locks/gpu.lock"):
            with calls.locked(self.root / lock):
                with self.assertRaises((OSError, BlockingIOError)):
                    with calls.locked(other.root / lock): pass

    def test_no_live_sender_and_no_unset_limits(self):
        with self.assertRaises(ValueError): SessionCalls(self.store, "unset", {**LIMITS, "turn_tokens": None})
        op = self.bridge.prepare(self.tid, "live", {}, allowance=10, channel="tokenmetro")
        with self.assertRaisesRegex(ValueError, "no live sender"): self.bridge.execute_mock(op, mock_attempt)

    def test_langgraph_sync_checkpoint_restart_no_duplicate_dispatch(self):
        self.store.cancel(self.tid)  # Leave only the deliberate graph turns in the queue.
        for point in ("prepared_before_checkpoint", "checkpoint_before_dispatch", "call_sealed_before_checkpoint"):
            with self.subTest(point=point):
                tid = self.store.submit("graph", point, "问题", "fixture")
                count = []
                def send(p, h): count.append(1); return mock_attempt(p, h)
                def fault(p):
                    if p == point: raise Crash()
                with self.assertRaises(Crash): run_mock(self.bridge, tid, send_once=send, fault=fault)
                with self.assertRaisesRegex(ValueError, "explicit continue"): run_mock(self.bridge, tid)
                result = run_mock(self.bridge, tid, continue_turn=True, send_once=send)
                run_mock(self.bridge, tid, send_once=lambda *a: self.fail("completed graph replay"))
                self.assertEqual(result["stop_reason"], "sufficient_fixture_only")
                self.assertEqual(len(count), 1)

    def test_queue_order_and_cross_process_application_lock(self):
        other = self.store.submit("s2", "queued", "问题", "fixture")
        self.assertEqual(self.store.next_turn(), self.tid)
        with self.assertRaisesRegex(ValueError, "TURN_QUEUED"):
            with self.store.execute(other): pass
        self.store.cancel(self.tid)
        self.assertEqual(self.store.next_turn(), other)
        import os, subprocess, sys
        command = [sys.executable, "-c", "from genshin_corpus.model_calls import locked; import sys; "
                   "\nwith locked(sys.argv[1]): pass", str(self.root / "locks/application.lock")]
        with calls.locked(self.root / "locks/application.lock"):
            result = subprocess.run(command, capture_output=True, env=dict(os.environ, PYTHONPATH="src"))
        self.assertNotEqual(result.returncode, 0)

    def test_budget_outcome_crash_reuses_same_attempt_usage(self):
        op = self.prepare(); original = self.bridge.finish
        def finish(*args): original(*args); raise Crash()
        with patch.object(self.bridge, "finish", side_effect=finish), self.assertRaises(Crash):
            self.bridge.execute_mock(op, mock_attempt)
        self.bridge.execute_mock(op, lambda *a: self.fail("no resend"))
        self.assertEqual(self.bridge.batch.accounting()["known_tokens"], 3)

    def test_claim_crash_before_call_record_recovers_without_new_identity(self):
        op = self.prepare(); original = calls.write
        def write_then_crash(path, value):
            original(path, value)
            if Path(path).name == "reservation.json" and "ledger" in Path(path).parts: raise Crash()
        with patch.object(calls, "write", side_effect=write_then_crash), self.assertRaises(Crash):
            self.bridge.execute_mock(op, mock_attempt)
        self.bridge.execute_mock(op, mock_attempt)
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 1)

    def test_call_record_before_plan_file_repaired_from_prepared_file(self):
        op = self.prepare(); original = calls.write
        def crash(path, value):
            original(path, value)
            if Path(path).name == "call.json": raise Crash()
        with patch.object(calls, "write", side_effect=crash), self.assertRaises(Crash):
            self.bridge.execute_mock(op, mock_attempt)
        result = self.bridge.execute_mock(op, mock_attempt)
        self.assertEqual(result["state"], "CONTENT_VERIFIED")
        self.assertEqual(read(self.bridge.root / "calls" / op / "plan.json"), self.bridge.operation(op))

    def test_reported_overrun_prevents_further_turn_admission(self):
        op = self.prepare()
        def overrun(p, h):
            result = {"state": "CONTENT_VERIFIED", "calls": 0,
                      "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}}
            write(p / "result.json", result); seal(p); return result
        self.bridge.execute_mock(op, overrun)
        with self.assertRaises(calls.Deferred): self.bridge.execute_mock(self.prepare("second"), mock_attempt)


if __name__ == "__main__": unittest.main()
