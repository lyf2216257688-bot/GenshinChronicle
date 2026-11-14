"""Offline actual G4 live-gate wiring; fixture sender only, zero sockets/GPU."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import shutil
import socket
import time
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import read, sha, write
from genshin_corpus.generation.clean_chat import ENDPOINT, MODEL
from genshin_corpus.rag.clean_conversation import ConversationStore
from genshin_corpus.rag.clean_conversation_graph import run_turn, saved_result, citation_view
from genshin_corpus.rag.clean_live import VERSION, LiveSessions, code_identity, validate_settings
from genshin_corpus.rag.clean_mock import fixture_inputs, FixturePipeline, MockOpener
from genshin_corpus.rag.clean_store import put
from genshin_corpus.retrieval.clean_based.source import InputReader
from genshin_corpus.retrieval.clean_based.query_v2 import validate_ancestry

REPO = Path(__file__).resolve().parents[2]


class Crash(BaseException): pass


class Pipeline(FixturePipeline):
    def run(self, query, *, parents=()):
        validate_ancestry(query, parents)
        return super().run(query)


class LiveTests(unittest.TestCase):
    def setUp(self):
        scratch = REPO / "tmp-g4-live"; scratch.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(dir=scratch)
        self.store = ConversationStore(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: shutil.rmtree(self.store.root) if self.store.root.exists() else None)
        self.settings = {"mode": "LIVE", "live_version": VERSION, "campaign": "fixture-shared-live-budget",
            "limits": {"campaign_requests": 3, "campaign_tokens": 3 * 67072, "turn_requests": 1, "turn_tokens": 67072},
            "context_tokens": 67072, "output_tokens": 1536, "timeout_seconds": 120,
            "history_bytes": 16000, "recent_turns": 2, "excerpt_chars": 160,
            "worker_timeout_seconds": 1800, "query_bytes": 2400, "thinking": "disabled"}
        proof = self.store.root / "fixture-proof.json"; write(proof, {"fixture_only": True})
        self.permit = {"version": VERSION, "live_enabled": True, "data_root": str(self.store.root),
            "campaign": self.settings["campaign"], "settings": self.settings, "repo": str(self.store.root),
            "expires_at": time.time() + 120, "code": code_identity(), "endpoint": ENDPOINT, "model": MODEL,
            "assets": {}, "protocol_evidence": {"path": str(self.store.root), "complete_sha256": sha(proof)}}
        # A tiny local proof file stands in for historical capability evidence.
        write(self.store.root / "complete.json", read(proof))
        put(self.store.root / "live/permit.json", self.permit)
        self.sid = self.store.create_chat("Fixture live path, NO NETWORK", self.settings, session="a")
        manifest = fixture_inputs(REPO, self.store.root / "corpus")
        self.reader = InputReader(manifest); self.addCleanup(self.reader.close)
        self.pipeline = Pipeline(self.reader, REPO)
        self.sender = MockOpener("条件仍须核验。[E01]")
        gate = patch("genshin_corpus.generation.clean_chat_transport.build_opener", return_value=self.sender)
        gate.start(); self.addCleanup(gate.stop)
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("No network in fixtures"))
        guard = network.start(); self.addCleanup(network.stop); self.addCleanup(guard.assert_not_called)
        env = patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture-only-not-a-credential"})
        env.start(); self.addCleanup(env.stop)

    def turn(self, token, question="原文的条件是什么？", sid=None, **kwargs):
        return self.store.enqueue(sid or self.sid, token, question, **kwargs)

    def bridge(self):
        return LiveSessions(self.store, self.settings["campaign"], self.settings["limits"], permit=self.permit)

    def execute(self, tid, **kwargs): return run_turn(self.store, tid, self.pipeline, **kwargs)

    def test_multiturn_context_correction_and_turn_scoped_citation(self):
        a = self.turn("1", "原文条件确实成立吗？", note_kind="user_assertion")
        first = self.execute(a)
        b = self.turn("2", "它的依据是什么？")
        second = self.execute(b)
        source = self.store.load(second["sources_ref"])
        self.assertEqual(source["query"]["kind"], "follow_up")
        self.assertEqual(source["query"]["original_question"], "它的依据是什么？")
        self.assertIn("原文条件确实成立吗", source["query"]["rerank_text"])
        self.assertEqual(second["mode"], "LIVE")
        self.assertNotIn("MOCK模式", self.store.load(second["context_ref"])["system"])
        self.assertEqual(citation_view(self.store, b, "E01")["parent_status"], "available")
        self.assertNotEqual(first["citations"][0]["packet_id"], second["citations"][0]["packet_id"])
        c = self.turn("3", "纠正：条件尚未成立，请重新核验。", note_kind="correction_fact")
        third = self.execute(c)
        self.assertNotIn("原文条件确实成立吗", self.store.load(third["sources_ref"])["query"]["search_text"])
        self.assertEqual(self.sender.calls, 3)

    def test_cross_chat_budget_shared_and_new_chat_isolated(self):
        self.execute(self.turn("1")); self.execute(self.turn("2")); self.execute(self.turn("3"))
        other = self.store.create_chat("Other", self.settings, session="b")
        self.assertEqual(self.store.history(other), [])
        result = self.execute(self.turn("4", sid=other))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.sender.calls, 3)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 3)

    def test_crash_after_seal_resume_after_expiry_without_key_or_network(self):
        tid = self.turn("1")
        def fault(stage):
            if stage == "chat_sealed_before_checkpoint": raise Crash()
        with self.assertRaises(Crash): self.execute(tid, fault=fault)
        with patch.dict(os.environ, {}, clear=True), patch("time.time", return_value=self.permit["expires_at"] + 1):
            self.store = ConversationStore(self.store.root)
            result = self.execute(tid, continue_turn=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.sender.calls, 1)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)

    def test_unknown_closes_all_chats_without_reissue(self):
        self.sender.disconnect = True
        tid = self.turn("1"); result = self.execute(tid)
        self.assertIsNone(result["answer_text"])
        self.assertTrue((self.store.root / "live/closed.json").exists())
        self.execute(tid)
        other = self.store.create_chat("Other", self.settings)
        with self.assertRaisesRegex(ValueError, "LIVE_CAMPAIGN_CLOSED"):
            self.execute(self.turn("2", sid=other))
        self.assertEqual(self.sender.calls, 1)
        self.assertEqual(self.bridge().batch.accounting()["unknown_usage_requests"], 1)

    def test_expired_permit_and_missing_key_never_reserve(self):
        tid = self.turn("1")
        with patch("time.time", return_value=self.permit["expires_at"] + 1):
            with self.assertRaisesRegex(ValueError, "LIVE_PERMIT"): self.execute(tid)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "MODEL_API_KEY_REQUIRED"):
                self.execute(tid, continue_turn=True)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)

    def test_source_corruption_between_preparation_and_dispatch_blocks(self):
        tid = self.turn("1")
        def fault(stage):
            if stage == "chat_prepared_before_checkpoint":
                ref = self.store.published_artifact(tid, "sources")
                (self.store.root / ref["path"]).write_text("{}", encoding="utf8")
        with self.assertRaises((ValueError, KeyError)): self.execute(tid, fault=fault)
        self.assertEqual(self.sender.calls, 0)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)

    def test_no_implicit_live_or_hard_limit_increase(self):
        for key, value in (("thinking", "enabled"), ("output_tokens", 1537), ("timeout_seconds", 121)):
            with self.assertRaises(ValueError): validate_settings({**self.settings, key: value})
        with self.assertRaises(ValueError):
            validate_settings({**self.settings, "limits": {**self.settings["limits"], "campaign_requests": 11}})
        with self.assertRaises(ValueError):
            ConversationStore(self.store.root / "new-root").create_chat("No permit", self.settings)

    def test_delivery_note_matches_completion_state(self):
        completed = self.execute(self.turn("1"))
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["delivery_note"], "")
        self.sender.answer = "现有材料不足以给出结论。"
        partial = self.execute(self.turn("2"))
        self.assertEqual(partial["status"], "partial")
        self.assertIn("部分交付", partial["delivery_note"])


if __name__ == "__main__": unittest.main()
