"""Offline tests for independently archived Navi user-input smoke evidence."""
import json
from pathlib import Path
import unittest

from genshin_corpus.retrieval import navi_f2_c1 as c1
from genshin_corpus.retrieval import navi_f2_sdk_transport as transport
from genshin_corpus.retrieval import navi_f2_content_recovery as recovery
from genshin_corpus.retrieval.navi_f1_prepare import read

ROOT = Path("data/retrieval")
PRE = ROOT / "p05-navi-f2-preflight-20261026-r1"
PAIR = "6c2d16a80dac01dc3f8c"
LIVE = ROOT / "p05-navi-v02-user-smoke-20261028-r1"
ATTEMPT = LIVE / "user_input_navi"


class UserInputSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (LIVE / "STOP.json").exists():
            raise unittest.SkipTest("Independent Navi smoke archive not present")
        cls.case = read(PRE / "requests" / (PAIR + "-rc1.json"))
        cls.schema = read(PRE / "contracts/p05-navi-b-v02.schema.json")
        cls.wire = read(ATTEMPT / "request.json")
        cls.result = read(ATTEMPT / "result.json")
        cls.observation = read(LIVE / "observation.json")
        encoding = read(ATTEMPT / "http.json")["headers"].get("content-encoding", "")
        raw = transport.decoded_body((ATTEMPT / "response.body.raw").read_bytes(), encoding)
        cls.events = c1.sse_events(raw)
        cls.sdk = [json.loads(line) for line in
                   (ATTEMPT / "sdk-events.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_real_receipt_completed_and_no_formal_slot(self):
        self.assertEqual(self.result["state"], "COMPLETED")
        self.assertEqual(self.result["http_status"], 200)
        self.assertTrue(self.result["schema_valid"])
        self.assertEqual(self.result["binding_status"], "PASS")
        self.assertTrue(self.result["stream_structure_consistent"])
        self.assertEqual(self.observation["formal_f2_execution_authorized"], False)

    def test_replay_mechanical_verification_and_headroom_warning(self):
        status = recovery.assess_content(
            self.events, self.sdk, self.case, request_wire=self.wire, schema=self.schema
        )
        self.assertEqual(status["state"], "CONTENT_VERIFIED")
        self.assertEqual(status["mechanical_status"], "PASS")
        self.assertEqual(status["full_stream_status"], "PASS")
        self.assertIsNone(status["protocol_warning"])
        self.assertTrue(status["near_output_limit"])
        self.assertEqual(status["output_token_headroom"], 98)
        self.assertEqual(status["semantic_quality"], "NOT_VALIDATED")

    def test_both_prompt_arms_have_identical_wire_fields(self):
        legacy = read(PRE / "requests" / (PAIR + "-legacy.json"))
        rc1 = self.case
        a = recovery.render_user_wire(legacy, max_output_tokens=30000)
        b = recovery.render_user_wire(rc1, max_output_tokens=30000)
        self.assertEqual(set(a), set(b))
        for key in ("model", "stream", "max_output_tokens"):
            self.assertEqual(a[key], b[key])
        self.assertNotEqual(a["input"], b["input"])


if __name__ == "__main__":
    unittest.main()
