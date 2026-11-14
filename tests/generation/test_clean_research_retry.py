"""Explicit single retry: original fingerprint/ledger/turn/Batch stay authoritative."""
import os
from unittest.mock import patch
import unittest

from genshin_corpus.evidence_io import inventory, read
from genshin_corpus.rag import clean_research as research
from genshin_corpus.rag import clean_research_retry as retry
from genshin_corpus.rag.clean_mock import MockOpener
from . import test_clean_research as fixtures


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ResearchTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.live_fixture()
        self.tid = self.f.turn()
        with patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", return_value=MockOpener("", disconnect=True)):
            self.result = research.run_turn(self.f.store, self.tid, self.f.pipeline)
        self.opid = self.result["operation_id"]
        self.bridge = retry.origin_bridge(self.f.store)
        self.origin = self.bridge.root / "calls" / self.opid
        self.before = inventory(self.origin)

    def prepare(self): return retry.prepare(self.f.store, self.opid, "OFFLINE_FIXTURE_EXPLICIT_ONE_UNCERTAIN_RETRY")

    def run_retry(self, sender):
        with patch("genshin_corpus.rag.clean_research_retry.build_opener", return_value=sender):
            return retry.run(self.f.store, self.opid, api_key="fixture")

    def test_same_batch_origin_preserved_and_closed_call_replay_no_network(self):
        grant = self.prepare(); sender = MockOpener("abcdefghijkl" * 2 + "[E01]")
        result = self.run_retry(sender)
        self.assertEqual(sender.calls, 1)
        self.assertEqual(inventory(self.origin), self.before)
        self.assertEqual(result["retry_result"]["state"], "CONTENT_VERIFIED")
        self.assertEqual(result["combined_accounting"]["request_reservations"], 2)
        self.assertEqual(result["combined_accounting"]["unknown_usage_requests"], 1)
        self.assertEqual(result["combined_accounting"]["known_tokens"], 30)
        self.assertEqual(result["combined_accounting"]["total_tokens"], "UNKNOWN")
        self.assertEqual(result["citation_validation"]["status"], "PASS")
        self.assertEqual(len([e for e in self.f.store.events(self.tid) if e.get("explicit_retry")]), 3)
        recovered = self.bridge.root / "recovered_calls" / self.opid
        self.assertEqual(read(recovered / "plan.json"), self.bridge.operation(self.opid))
        self.assertEqual(grant["budget_root"], str(self.bridge.batch.root))
        with patch.dict(os.environ, {}, clear=True), patch.object(retry, "code_identity", return_value={}):
            self.assertEqual(retry.run(self.f.store, self.opid), result)
            self.assertEqual(research.run_turn(self.f.store, self.tid, self.f.pipeline), self.result)
        self.assertEqual(sender.calls, 1)
        self.assertEqual(len(self.bridge.reconcile(self.opid)["explicit_recoveries"]), 1)

    def test_second_unknown_consumes_only_one_retry_and_no_third_request(self):
        self.prepare(); sender = MockOpener("", disconnect=True)
        result = self.run_retry(sender)
        self.assertEqual(sender.calls, 1)
        self.assertEqual(result["combined_accounting"]["unknown_usage_requests"], 2)
        self.assertEqual(self.run_retry(sender), result)
        self.assertEqual(sender.calls, 1)
        with self.assertRaisesRegex(ValueError, "RECOVERY_ALREADY_EXISTS"):
            self.prepare()

    def test_missing_authorization_or_key_never_reserves_retry(self):
        with self.assertRaises(ValueError): retry.prepare(self.f.store, self.opid, "")
        self.prepare()
        with self.assertRaisesRegex(ValueError, "TOKENMETRO_API_KEY_REQUIRED"):
            retry.run(self.f.store, self.opid)
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 1)

    def test_expired_retry_grant_never_reopens_original_campaign(self):
        grant = self.prepare()
        with patch("time.time", return_value=grant["expires_at"] + 1):
            with self.assertRaisesRegex(ValueError, "RETRY_EXPIRED"):
                self.run_retry(MockOpener("must not send"))
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 1)
        self.assertTrue((self.f.store.root / "live/closed.json").is_file())

    def test_changed_code_before_retry_blocks_without_reservation(self):
        self.prepare()
        with patch.object(retry, "code_identity", return_value={}):
            with self.assertRaisesRegex(ValueError, "RETRY_SCOPE_OR_CODE_CHANGED"):
                self.run_retry(MockOpener("must not send"))
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 1)


if __name__ == "__main__": unittest.main()
