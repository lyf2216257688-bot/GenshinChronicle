"""Offline-only tests of F2 30k plan, dual-arm archive replay, execution guards."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from genshin_corpus.retrieval import navi_f2_json_mode_execute_v2 as run
from genshin_corpus.retrieval import navi_f2_json_mode_30k_preflight as prepare
from genshin_corpus.retrieval.navi_f1_prepare import read, value_hash
from genshin_corpus.retrieval.navi_f2_content_recovery import render_user_json_mode_wire
from genshin_corpus.retrieval import navi_f2_preflight as pre

BASE = Path("data/retrieval")


class JSONModeF2ExecuteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = run.verify_plan()
        cls.schema = read(run.PRE / "contracts/p05-navi-b-v02.schema.json")
        cls.evidence = read(run.F1 / "evidence.json")
        cls.validator = staticmethod(pre.native_legacy_validator(run.PRE / "contracts"))

    def test_48_frozen_requests_are_30k_two_arm_and_unsent(self):
        rows = self.manifest["records"]
        self.assertEqual(len(rows), 48)
        self.assertEqual(self.manifest["state"], "PREPARED_NOT_AUTHORIZED")
        self.assertEqual(self.manifest["formal_model_requests_sent"], 0)
        self.assertEqual(self.manifest["max_output_tokens"], 30000)
        self.assertTrue(read(run.PLAN / "STOP.json")["no_auto_resume"])
        for r in rows:
            case = read(run.PRE / "requests" / (r["request_id"] + ".json"))
            wire = render_user_json_mode_wire(case, max_output_tokens=30000)
            self.assertEqual(value_hash(wire), r["wire_fingerprint"])
            self.assertEqual(wire["input"][0]["role"], "user")
            self.assertEqual(wire["text"], {"format": {"type": "json_object"}})
            self.assertNotIn("instructions", wire)

    def test_exact_raw_replay_legacy_and_rc1_16k_and_rc1_30k(self):
        cases = [
            ("p05-navi-f2-legacy-json-mode-20261028-r1", "legacy_json_mode", "legacy", 16000),
            ("p05-navi-f2-rc1-json-mode-20261028-r1", "rc1_json_mode", "rc1", 16000),
            ("p05-navi-f2-rc1-json-mode-30k-20261028-r1", "rc1_json_mode", "rc1", 30000),
        ]
        for root_name, folder, arm, cap in cases:
            with self.subTest(arm=arm, cap=cap):
                case = read(run.PRE / "requests" / ("6c2d16a80dac01dc3f8c-" + arm + ".json"))
                actual = run.verify_archived_content(
                    BASE / root_name / folder, case,
                    evidence=self.evidence, legacy_validator=self.validator, schema=self.schema)
                self.assertEqual(actual["state"], "CONTENT_VERIFIED")
                self.assertEqual(actual["mechanical_status"], "PASS")
                self.assertEqual(actual["full_stream_status"], "PASS")
                self.assertEqual(actual["max_output_tokens"], cap)
                self.assertFalse(actual["reasoning_id_conflicts"])

    def test_guard_requires_explicit_batch_approval(self):
        with patch("sys.argv", ["runner", "--execute-authorized"]), \
             patch.object(run, "execute_one") as execute:
            with self.assertRaisesRegex(ValueError, "approval"):
                run.main()
        execute.assert_not_called()

    def test_guard_rejects_partial_official_cohort(self):
        with patch("sys.argv", ["runner", "--execute-authorized",
                                "--ack-new-f2-48-request-batch", "--max-requests", "1"]), \
             patch.object(run, "execute_one") as execute:
            with self.assertRaisesRegex(ValueError, "partial"):
                run.main()
        execute.assert_not_called()

    def test_existing_output_root_blocks_resume(self):
        with patch("sys.argv", ["runner", "--execute-authorized", "--ack-new-f2-48-request-batch"]), \
             patch.object(run.Path, "exists", return_value=True), \
             patch.object(run, "verify_plan", return_value={"records": [], "max_output_tokens": 30000, "provider": "fixture"}), \
             patch.object(run, "execute_one") as execute:
            with self.assertRaisesRegex(ValueError, "no replay or automatic resume"):
                run.main()
        execute.assert_not_called()

    def test_original_F2_failure_still_untouched(self):
        old = read(BASE / "p05-navi-f2-experiment-20261027-r1/STOP.json")
        self.assertEqual(old["slot"], 1)
        self.assertEqual(old["remaining_not_sent"], 47)
        self.assertFalse(old["automatic_retry"])

    def test_protocol_warning_stops_before_next_slot(self):
        manifest = {"records": [{"request_id": f"slot-{i}"} for i in range(48)],
                    "max_output_tokens": 30000, "provider": "jizhi", "version": "test"}
        written = []
        with patch("sys.argv", ["runner", "--execute-authorized", "--ack-new-f2-48-request-batch"]), \
             patch.object(run, "verify_plan", return_value=manifest), \
             patch.object(run, "environment", return_value={"JIZHI_API_KEY": "fixture", "JIZHI_BASE_URL": run.c1.BASE_URL}), \
             patch.object(run.Path, "exists", return_value=False), \
             patch.object(run.Path, "mkdir"), \
             patch.object(run, "sha", return_value="fixture"), \
             patch.object(run, "read", return_value={}), \
             patch.object(run.pre, "native_legacy_validator", return_value=None), \
             patch.object(run, "put", side_effect=lambda path, obj: written.append((path, obj))), \
             patch.object(run, "execute_one", return_value={"state": "CONTENT_VERIFIED_PROTOCOL_WARNING", "full_stream_status": "FAIL", "usage": {}}) as execute:
            run.main()
        self.assertEqual(execute.call_count, 1)
        stop = next(obj for path, obj in written if path.name == "STOP.json")
        self.assertEqual(stop["remaining_not_sent"], 47)
        self.assertTrue(stop["no_retry"])


if __name__ == "__main__":
    unittest.main()
