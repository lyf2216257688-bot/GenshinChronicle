"""Offline checks of the new F2 two-arm transport freeze; never sends API calls."""
import unittest

from genshin_corpus.retrieval import navi_f2_user_preflight as p
from genshin_corpus.retrieval.navi_f1_prepare import read, value_hash
from genshin_corpus.retrieval.navi_f2_content_recovery import render_user_wire


class F2UserInputPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = p.build_plan()
        cls.stored = read(p.OUT / "prepared-manifest.json")

    def test_stored_manifest_reproducible_and_unsent(self):
        self.assertEqual(self.plan, self.stored)
        self.assertEqual(self.plan["state"], "PREPARED_NOT_AUTHORIZED")
        self.assertEqual(self.plan["formal_calls_sent"], 0)
        self.assertTrue(read(p.OUT / "STOP.json")["auto_resume"] is False)

    def test_48_slots_share_one_transport_strategy(self):
        rows = self.stored["records"]
        self.assertEqual(len(rows), 48)
        self.assertEqual(len({r["wire_fingerprint"] for r in rows}), 48)
        for row in rows:
            c = read(p.PRE / "requests" / (row["request_id"] + ".json"))
            wire = render_user_wire(c, max_output_tokens=p.MAX_OUTPUT)
            self.assertEqual(row["wire_fingerprint"], value_hash(wire))
            self.assertEqual(wire["stream"], True)
            self.assertNotIn("instructions", wire)
            self.assertNotIn("text", wire)

    def test_prompt_arms_remain_source_pair_identical(self):
        by_pair = {}
        for row in self.stored["records"]:
            by_pair.setdefault(row["pair_id"], []).append(row)
        self.assertEqual(len(by_pair), 24)
        for a, b in by_pair.values():
            self.assertEqual({a["arm"], b["arm"]}, {"legacy", "rc1"})
            self.assertEqual(a["source_payload_sha256"], b["source_payload_sha256"])
            self.assertEqual(a["original_text_sha256"], b["original_text_sha256"])

    def test_old_F2_failure_and_47_unsent_preserved(self):
        original = read(p.BASE / "p05-navi-f2-experiment-20261027-r1" / "STOP.json")
        self.assertEqual(original["slot"], 1)
        self.assertEqual(original["remaining_not_sent"], 47)
        self.assertEqual(original["automatic_retry"], False)

    def test_legacy_live_attempt_failed_before_request_send(self):
        attempt = p.BASE / "p05-navi-f2-legacy-user-smoke-20261028-r1"
        result = read(attempt / "legacy_user_input" / "result.json")
        trace = read(attempt / "legacy_user_input" / "trace.json")
        self.assertEqual(result["state"], "NOT_SENT")
        self.assertIsNone(result["http_status"])
        self.assertIn("connection.start_tls.failed", trace["events"])
        self.assertTrue(read(attempt / "STOP.json")["no_retry"])

    def test_legacy_second_attempt_produced_invalid_provider_envelope(self):
        root = p.BASE / "p05-navi-f2-legacy-user-smoke-20261028-r2"
        original = read(root / "legacy_user_input" / "result.json")
        observed = read(root / "offline-observation.json")
        self.assertEqual(original["http_status"], 200)
        self.assertEqual(original["state"], "UNKNOWN")
        self.assertEqual(original["raw_event_count"], 2)
        self.assertIsNone(observed.get("usage"))
        self.assertEqual(observed["classification"],
                         "PROVIDER_ENVELOPE_INVALID_NO_CONTENT_NO_USAGE")
        self.assertFalse(observed["message_text_present"])
        self.assertFalse(observed["usage_present"])
        self.assertTrue(read(root / "STOP.json")["no_retry"])


if __name__ == "__main__":
    unittest.main()
