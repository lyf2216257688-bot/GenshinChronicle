from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.retrieval.historical_atomic_repair_routing import (
    _sha256, aggregate, classify, investigate,
)


class RepairRoutingTest(unittest.TestCase):
    def item(self, quote, failure="source_projection_or_quote_binding", refs=None):
        return {"source_quote": quote, "failure_class": failure,
                "item": {"local_id": "f1", "source_segment_ids": refs or ["s1"],
                         "qualifiers": {"source_quote": quote}}}

    def payload(self, decoded):
        return {"segments": [{"segment_id": "s1", "value": {"decoded": decoded}}]}

    def test_bound_unique_span_is_contract_candidate_not_approved(self):
        result = classify(self.item("quoted text"), self.payload({"attr": "<p>quoted text</p>"}))
        self.assertEqual(result["route"], "CONTRACT_FIXABLE")
        self.assertFalse(result["mechanically_approved_by_routing"])
        self.assertEqual(result["semantic_correctness"], "UNKNOWN")
        loc = result["evidence"]["content_locations"][0]
        self.assertEqual(loc["match_offsets"], [3])
        self.assertTrue(loc["bound"])

    def test_duplicate_scalars_and_occurrences_are_unknown(self):
        for decoded in ({"a": "Q", "b": "Q"}, {"a": "Q Q"}):
            self.assertEqual(classify(self.item("Q"), self.payload(decoded))["route"], "UNKNOWN")

    def test_unicode_pointer_tokens_are_compared_exactly(self):
        payload = self.payload({"name/value~": "abc"})
        loc = classify(self.item("abc"), payload)["evidence"]["content_locations"][0]
        self.assertEqual(loc["json_pointer"], "/value/decoded/name~1value~0")

    def test_unbound_natural_text_needs_output_binding_intervention(self):
        payload = {"segments": [{"segment_id": "s1", "value": {"decoded": {"name": "A"}}},
                                {"segment_id": "s2", "value": {"text": "Q"}}]}
        result = classify(self.item("Q"), payload)
        self.assertEqual(result["route"], "MODEL_REPAIRABLE")
        self.assertEqual(result["evidence"]["unbound_natural_segments"], ["s2"])

    def test_legacy_fabricated_and_metadata_never_become_contract_fixable(self):
        for failure in ("fabricated_or_unbound_quote", "metadata_serialized_json_contamination"):
            self.assertEqual(classify(self.item("Q", failure), self.payload({"name": "Q"}))["route"],
                             "MODEL_REPAIRABLE")

    def test_header_names_and_segment_id_coincidences_remain_unknown(self):
        payload = {"segments": [{"segment_id": "s1", "value": {"section": "Q"}}]}
        self.assertEqual(classify(self.item("Q"), payload)["route"], "UNKNOWN")
        self.assertEqual(classify(self.item("s1"), payload)["route"], "UNKNOWN")

    def test_technical_json_needs_actual_key_value_evidence(self):
        item = self.item('"component":"collapse_panel"', "fabricated_or_unbound_quote")
        payload = {"segments": [{"segment_id": "s1", "value": {"component": "collapse_panel"}}]}
        self.assertEqual(classify(item, payload)["route"], "NON_SEMANTIC_METADATA")
        payload["segments"][0]["value"]["component"] = "other"
        self.assertEqual(classify(item, payload)["route"], "MODEL_REPAIRABLE")

    def test_html_resource_attribute_is_metadata_name_amount_is_structured(self):
        payload = self.payload({"materials": '<span data-entry-id="10" data-entry-name="fish" data-entry-amount="3">fish</span>'})
        self.assertEqual(classify(self.item('data-entry-id="10"', "structured_value_quote_projection_boundary"),
                                  payload)["route"], "NON_SEMANTIC_METADATA")
        result = classify(self.item('data-entry-name="fish" data-entry-amount="3"',
                                    "structured_value_quote_projection_boundary"), payload)
        self.assertEqual(result["route"], "CONTRACT_FIXABLE")
        self.assertEqual(result["operation_candidate"], "research_html_name_amount_attribute_anchor")

    def test_image_url_is_resource_not_natural_content(self):
        payload = self.payload({"image": "https://example.invalid/image.png"})
        self.assertEqual(classify(self.item("https://example.invalid/image.png"), payload)["route"],
                         "NON_SEMANTIC_METADATA")

    def test_unit_aggregation_retains_blocking_unknown(self):
        self.assertEqual(aggregate(Counter({"UNKNOWN": 1, "MODEL_REPAIRABLE": 10})), "UNKNOWN")
        self.assertEqual(aggregate(Counter({"CONTRACT_FIXABLE": 1, "MODEL_REPAIRABLE": 1})), "MODEL_REPAIRABLE")
        self.assertEqual(aggregate(Counter({"CONTRACT_FIXABLE": 1, "NON_SEMANTIC_METADATA": 1})),
                         "NON_SEMANTIC_METADATA")
        self.assertEqual(aggregate(Counter()), "NO_REMAINING_FAILED_ITEMS")

    def test_investigation_binds_artifacts_is_provider_free_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            attempt = base / "root" / "units" / "u" / "attempt-001"
            attempt.mkdir(parents=True)
            def write(path, body):
                path.write_text(json.dumps(body), encoding="utf-8")
            payload = self.payload({"attr": "<p>Q</p>"})
            failure = self.item("Q")
            canonical = attempt / "canonical_output.json"
            write(canonical, {"items": [failure["item"]]})
            write(attempt / "terminal.json", {"unit_id": "u", "disposition": "accepted"})
            write(attempt / "validation.json", {"status": "historical"})
            manifest = base / "manifest.json"
            contract = {"units": [{"compilation_unit_id": "u", "payload": payload}]}
            write(manifest, {"contract": contract, "identity": sha256_json(contract)})
            ledger = base / "ledger.json"
            write(ledger, {})
            replay = base / "replay.json"
            body = {"inputs": {"classification_ledger": str(ledger), "classification_ledger_sha256": _sha256(ledger),
                                "manifests": [str(manifest)], "roots": [str(base / "root")]},
                    "counts": {"provider_omitted": 0},
                    "source_anchor_v01": {"failed_items": 1, "recovered_items": 0},
                    "rows": [{"unit_id": "u", "title": "fixture",
                        "record_key": "record", "source_type": "fixture", "validator_error": "quote failure",
                        "source_payload_identity": "sha256:" + sha256_json(payload),
                        "canonical_output_sha256": _sha256(canonical), "historical_runtime_verdict": "accepted",
                        "current_replay_verdict": "FAIL", "source_anchor_v01_verdict": "FAIL",
                        "failed_items": [failure], "source_anchor_v01_item_verdicts": [{"local_id": "f1", "verdict": "FAIL"}]}]}
            write(replay, body)
            output = base / "report.json"
            with patch("socket.socket.connect", side_effect=AssertionError("network prohibited")):
                result = investigate(replay=replay, output=output)
            self.assertEqual(result["counts"]["remaining_item_routes"], {"CONTRACT_FIXABLE": 1})
            self.assertTrue(result["input_hashes_unchanged"])
            self.assertEqual(result["units"][0]["mechanical_routing_verdict"], "FAIL")
            with self.assertRaisesRegex(ValueError, "already exists"):
                investigate(replay=replay, output=output)
            body["rows"][0]["source_payload_identity"] = "wrong"
            write(replay, body)
            with self.assertRaisesRegex(ValueError, "payload identity mismatch"):
                investigate(replay=replay, output=base / "bad-report.json")


if __name__ == "__main__":
    unittest.main()
