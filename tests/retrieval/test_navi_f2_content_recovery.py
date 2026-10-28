"""Offline only: validate the real archived v0.2 recovery without a model call."""
import json
from copy import deepcopy
from pathlib import Path
import unittest

from genshin_corpus.retrieval import (
    navi_f2_c1 as c1, navi_f2_sdk_transport as transport,
    navi_f2_content_recovery as recovery,
)
from genshin_corpus.retrieval.navi_f1_prepare import read


BASE = Path("data/retrieval")
FROZEN = BASE / "p05-navi-f2-preflight-20261026-r1"
ARCHIVE = BASE / "p05-navi-v02-call-recovery-20261028-r1" / "user_fallback"


class ContentRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ARCHIVE.is_dir():
            raise unittest.SkipTest("Archived recovery proof unavailable")
        cls.case = read(FROZEN / "requests/8978e15f60f91e0e1db1-rc1.json")
        cls.schema = read(FROZEN / "contracts/p05-navi-b-v02.schema.json")
        cls.request_wire = read(ARCHIVE / "request.json")
        enc = read(ARCHIVE / "http.json")["headers"].get("content-encoding", "")
        raw = transport.decoded_body((ARCHIVE / "response.body.raw").read_bytes(), enc)
        cls.events = c1.sse_events(raw)
        cls.sdk = [json.loads(line) for line in
                   (ARCHIVE / "sdk-events.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_wire_matches_successful_archived_request(self):
        wire = recovery.render_user_wire(self.case)
        self.assertEqual(wire, read(ARCHIVE / "request.json"))
        self.assertEqual(set(wire), {"model", "stream", "max_output_tokens", "input"})
        self.assertNotIn("instructions", wire)

    def test_actual_archived_navi_content_is_verified_with_protocol_warning(self):
        outcome = recovery.assess_content(
            self.events, self.sdk, self.case, request_wire=self.request_wire, schema=self.schema
        )
        self.assertEqual(outcome["mechanical_status"], "PASS")
        self.assertEqual(outcome["state"], "CONTENT_VERIFIED_PROTOCOL_WARNING")
        self.assertEqual(outcome["full_stream_status"], "FAIL")
        self.assertEqual(outcome["protocol_warning"], "REASONING_ITEM_ID_CONFLICT")
        self.assertTrue(outcome["reasoning_id_conflicts"])
        self.assertEqual(outcome["validation"]["status"], "VALIDATED_MECHANICAL_ONLY")
        self.assertEqual(outcome["human_acceptance"], "NOT_REVIEWED")
        self.assertFalse(outcome["formal_f2_execution_authorized"])

    def test_invalid_contract_not_promoted(self):
        wrong_schema = {"type": "object", "required": ["__unavailable_field__"]}
        result = recovery.assess_content(
            self.events, self.sdk, self.case, request_wire=self.request_wire, schema=wrong_schema
        )
        self.assertEqual(result["state"], "CONTENT_INVALID")
        self.assertEqual(result["mechanical_status"], "FAIL")

    def test_message_or_sdk_mismatch_fails_closed(self):
        sdk = deepcopy(self.sdk)
        sdk[-2]["type"] = "unrecognized"
        with self.assertRaises(ValueError):
            recovery.assess_content(self.events, sdk, self.case, request_wire=self.request_wire, schema=self.schema)

    def test_frozen_prompt_tampering_is_rejected(self):
        case = deepcopy(self.case)
        case["messages"][0]["content"] += "altered"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            recovery.render_user_wire(case)


if __name__ == "__main__":
    unittest.main()
