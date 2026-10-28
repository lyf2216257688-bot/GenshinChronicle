"""Verify archived paired live proofs and the unsent JSON-mode F2 preparation."""
import unittest
from genshin_corpus.retrieval import navi_f2_json_mode_preflight as run
from genshin_corpus.retrieval.navi_f1_prepare import read, value_hash
from genshin_corpus.retrieval.navi_f2_content_recovery import render_user_json_mode_wire


class JsonModePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = run.build_plan()
        cls.saved = read(run.OUT / "prepared-manifest.json")

    def test_paired_live_proof_is_complete(self):
        self.assertEqual(set(self.plan["paired_live_proofs"]), {"legacy", "rc1"})
        self.assertEqual(self.plan["paired_live_proofs"]["legacy"]["output_tokens"], 8390)
        self.assertEqual(self.plan["paired_live_proofs"]["rc1"]["output_tokens"], 15589)
        self.assertEqual(self.plan["max_output_tokens"], 16000)

    def test_48_frozen_requests_exact_and_no_formal_dispatch(self):
        self.assertEqual(self.plan, self.saved)
        self.assertEqual(self.saved["state"], "PREPARED_NOT_AUTHORIZED")
        self.assertEqual(self.saved["formal_model_requests_sent"], 0)
        self.assertFalse(read(run.OUT / "STOP.json")["auto_resume"])
        rows = self.saved["records"]
        self.assertEqual(len(rows), 48)
        self.assertEqual(len({r["wire_fingerprint"] for r in rows}), 48)
        for row in rows:
            case = read(run.PRE / "requests" / (row["request_id"] + ".json"))
            wire = render_user_json_mode_wire(case, max_output_tokens=run.CAP)
            self.assertEqual(row["wire_fingerprint"], value_hash(wire))
            self.assertEqual(set(wire),
                             {"model", "stream", "max_output_tokens", "input", "text"})
            self.assertEqual(wire["text"]["format"]["type"], "json_object")
            self.assertEqual(len(wire["input"]), 1)
            self.assertEqual(wire["input"][0]["role"], "user")
            self.assertNotIn("instructions", wire)

    def test_each_pair_uses_same_service_parameters(self):
        pairs = {}
        for row in self.saved["records"]:
            pairs.setdefault(row["pair_id"], []).append(row)
        self.assertEqual(len(pairs), 24)
        for rows in pairs.values():
            self.assertEqual({x["arm"] for x in rows}, {"legacy", "rc1"})
            self.assertEqual(rows[0]["source_payload_sha256"],
                             rows[1]["source_payload_sha256"])

    def test_prior_failure_remains_untouched(self):
        old = read(run.BASE / "p05-navi-f2-experiment-20261027-r1/STOP.json")
        self.assertEqual(old["slot"], 1)
        self.assertEqual(old["remaining_not_sent"], 47)
        self.assertFalse(old["automatic_retry"])


if __name__ == "__main__":
    unittest.main()
