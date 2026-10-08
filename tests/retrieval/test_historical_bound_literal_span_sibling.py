from __future__ import annotations

import json
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.retrieval import historical_bound_literal_span_sibling as sibling
from genshin_corpus.retrieval.historical_atomic_replay_audit import _validate_with_source_anchor
from genshin_corpus.retrieval.semantic_two_stage_experiment import validate_atomic_inventory

from genshin_corpus.retrieval.historical_bound_literal_span_sibling import (
    _span_candidates, _verify_anchor,
)


class BoundLiteralSpanSiblingTest(unittest.TestCase):
    def payload(self, value):
        return {"segments": [{"segment_id": "s1", "value": value}]}

    def item(self, quote="quoted"):
        return {"local_id": "f1", "source_segment_ids": ["s1"],
                "qualifiers": {"source_quote": quote}}

    def test_unique_bound_literal_span_round_trips(self):
        payload = self.payload({"decoded": {"attr": "<p>quoted text</p>"}})
        item = self.item("quoted")
        anchors = _span_candidates(payload, "quoted", ["s1"])
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["start"], 3)
        self.assertEqual(anchors[0]["end"], 9)
        self.assertTrue(_verify_anchor(payload, item, anchors[0])[0])

    def test_multiple_occurrences_fail_closed(self):
        payload = self.payload({"decoded": {"attr": "quoted quoted"}})
        anchors = _span_candidates(payload, "quoted", ["s1"])
        self.assertEqual(len(anchors), 2)
        self.assertFalse(_verify_anchor(payload, self.item(), anchors[0])[0])

    def test_multiple_values_and_bound_segments_fail_closed(self):
        payload = self.payload({"decoded": {"a": "quoted", "b": "quoted"}})
        anchor = _span_candidates(payload, "quoted", ["s1"])[0]
        self.assertFalse(_verify_anchor(payload, self.item(), anchor)[0])
        payload = self.payload({"decoded": {"a": "quoted"}})
        payload["segments"].append({"segment_id": "s2", "value": {"decoded": {"a": "quoted"}}})
        item = self.item()
        item["source_segment_ids"].append("s2")
        self.assertFalse(_verify_anchor(payload, item, anchor)[0])

    def test_pointer_value_hash_and_span_tamper_fail_closed(self):
        payload = self.payload({"decoded": {"attr": "quoted text"}})
        item = self.item("quoted")
        anchor = _span_candidates(payload, "quoted", ["s1"])[0]
        for field, value in (("json_pointer", "/value/decoded/missing"),
                             ("json_pointer", None),
                             ("value_sha256", "0" * 64),
                             ("start", anchor["start"] + 1),
                             ("end", anchor["end"] + 1),
                             ("source_quote", "other")):
            tampered = dict(anchor)
            tampered[field] = value
            self.assertFalse(_verify_anchor(payload, item, tampered)[0], field)
        payload["segments"][0]["value"]["decoded"]["attr"] += " changed"
        self.assertFalse(_verify_anchor(payload, item, anchor)[0])

    def test_item_binding_and_technical_paths_are_excluded(self):
        payload = self.payload({"decoded": {"section": "quoted", "tab_name": "quoted",
                                             "component": "quoted", "image": "quoted"}})
        self.assertEqual(_span_candidates(payload, "quoted", ["s1"]), [])
        item = self.item("quoted")
        anchor = {"kind": "bound_literal_span", "segment_id": "s2",
                  "json_pointer": "/value/decoded/name", "value_sha256": "0" * 64,
                  "start": 0, "end": 6, "span_unit": "unicode_codepoint",
                  "source_quote": "quoted"}
        self.assertEqual(_verify_anchor(payload, item, anchor)[1], "anchor segment is not item-bound")

    def test_attribute_literals_are_not_html_attribute_extension(self):
        payload = self.payload({"decoded": {"materials": '<span data-entry-name="fish">fish</span>'}})
        self.assertEqual(_span_candidates(payload, 'data-entry-name="fish"', ["s1"]), [])

    def test_unicode_codepoint_span_is_explicit(self):
        payload = self.payload({"decoded": {"value": "\U0001f600quoted\u4e2d"}})
        anchor = _span_candidates(payload, "quoted", ["s1"])[0]
        self.assertEqual(anchor["span_unit"], "unicode_codepoint")
        self.assertEqual(anchor["start"], 1)
        self.assertEqual(anchor["end"], 7)

    def test_empty_quote_duplicate_segment_and_boolean_span_fail_closed(self):
        payload = self.payload({"decoded": {"value": "quoted"}})
        self.assertEqual(_span_candidates(payload, "", ["s1"]), [])
        anchor = _span_candidates(payload, "quoted", ["s1"])[0]
        tampered = {**anchor, "start": False}
        self.assertFalse(_verify_anchor(payload, self.item(), tampered)[0])
        payload["segments"].append(copy.deepcopy(payload["segments"][0]))
        self.assertFalse(_verify_anchor(payload, self.item(), anchor)[0])

    def route_row(self, payload, item):
        uid = next(iter(sibling.TARGET_ITEMS))
        item["local_id"] = "i114"
        anchor = _span_candidates(payload, item["qualifiers"]["source_quote"], item["source_segment_ids"])[0]
        return {"unit_id": uid, "local_id": "i114", "item": copy.deepcopy(item),
                "identity": {"item": "sha256:" + sha256_json(item)},
                "source_quote": item["qualifiers"]["source_quote"],
                "scope": "remaining_failed", "route": "CONTRACT_FIXABLE",
                "operation_candidate": sibling.TARGET_OPERATION,
                "failure_class": "source_projection_or_quote_binding",
                "evidence": {"content_locations": [{"bound": True, "segment_id": anchor["segment_id"],
                    "json_pointer": anchor["json_pointer"], "value_sha256": anchor["value_sha256"],
                    "match_offsets": [anchor["start"]]}]}}

    def test_approval_requires_exact_scope_item_and_frozen_evidence(self):
        payload = self.payload({"decoded": {"a": "quoted"}})
        item = self.item()
        route = self.route_row(payload, item)
        self.assertEqual(sibling._approve_item(payload, item, route)["verdict"], "PASS_BOUND_LITERAL_SPAN_V0_1")
        for field, bad in (("route", "MODEL_REPAIRABLE"), ("route", "UNKNOWN"),
                           ("unit_id", "outside"), ("operation_candidate", "research_html_name_amount_attribute_anchor"),
                           ("failure_class", "fabricated_or_unbound_quote"),
                           ("failure_class", "metadata_serialized_json_contamination")):
            self.assertEqual(sibling._approve_item(payload, item, {**route, field: bad})["verdict"], "FAIL")
        bad = copy.deepcopy(route)
        bad["evidence"]["content_locations"][0]["match_offsets"] = [100]
        self.assertEqual(sibling._approve_item(payload, item, bad)["verdict"], "FAIL")
        bad = copy.deepcopy(route)
        bad["item"]["qualifiers"]["source_quote"] = "changed"
        self.assertEqual(sibling._approve_item(payload, item, bad)["verdict"], "FAIL")

    def test_selection_is_exact_six_units_nineteen_ids(self):
        report = {"units": [], "items": []}
        for uid, ids in sibling.TARGET_ITEMS.items():
            report["units"].append({"unit_id": uid, "routing_verdict": "CONTRACT_FIXABLE",
                                    "route_set": ["CONTRACT_FIXABLE"], "remaining_failed_item_count": len(ids)})
            for key in ids:
                report["items"].append({"unit_id": uid, "local_id": key, "scope": "remaining_failed",
                                        "route": "CONTRACT_FIXABLE", "operation_candidate": sibling.TARGET_OPERATION,
                                        "failure_class": "source_projection_or_quote_binding"})
        self.assertEqual(len(sibling._select_targets(report)), 19)
        report["items"][0]["local_id"] = "unapproved"
        with self.assertRaisesRegex(ValueError, "scope"):
            sibling._select_targets(report)

    def test_offline_replay_controls_and_artifact_tamper(self):
        # Small synthetic payloads exercise the frozen scope/control accounting,
        # not real-corpus or semantic acceptance.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            def write(path, body):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(body), encoding="utf-8")
            manifest_units, source_rows, route_items, route_units = [], [], [], []
            unit_ids = [(uid, sorted(ids)) for uid, ids in sibling.TARGET_ITEMS.items()]
            unit_ids += [("control" + str(i), ["f1"]) for i in range(107)]
            for index, (uid, ids) in enumerate(unit_ids):
                is_target = uid in sibling.TARGET_ITEMS
                quotes = {key: "quote-" + str(index) + "-" + key for key in ids}
                value = {"decoded": {key: text for key, text in quotes.items()}} if is_target else {"text": quotes[ids[0]]}
                payload = self.payload(value)
                items = [{"local_id": key, "kind": "fact", "label": text, "source_segment_ids": ["s1"],
                          "topic_path": [], "qualifiers": {"source_quote": text}} for key, text in quotes.items()]
                output_body = {"schema_version": "phase05-w2-semantic-output-0.1", "items": items,
                               "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}]}
                path = root / "units" / uid / "attempt-001" / "canonical_output.json"
                write(path, output_body)
                write(path.parent / "terminal.json", {"unit_id": uid, "disposition": "accepted_for_local_contract"})
                manifest_units.append({"compilation_unit_id": uid, "payload": payload})
                failures = [{"item": item, "source_quote": item["qualifiers"]["source_quote"],
                             "failure_class": "source_projection_or_quote_binding"} for item in items] if is_target else []
                classes = {row["item"]["local_id"]: row["failure_class"] for row in failures}
                v01 = _validate_with_source_anchor(output_body, payload, failed_item_classes=classes)
                error = ""
                try:
                    validate_atomic_inventory(output_body, payload)
                except ValueError as exc:
                    error = str(exc)
                source_rows.append({"unit_id": uid, "title": uid, "source_payload_identity": "sha256:" + sha256_json(payload),
                    "canonical_output_sha256": sibling._sha256(path), "failed_items": failures,
                    "validator_identity": "validate_atomic_inventory:sha256:" + sibling._sha256(Path(sibling.__file__).with_name("semantic_two_stage_experiment.py")),
                    "source_anchor_v01_verdict_identity": {"policy_implementation_identity": "sha256:" + sibling._sha256(Path(sibling.__file__).with_name("historical_atomic_replay_audit.py"))},
                    "historical_runtime_verdict": "accepted_for_local_contract", "current_replay_verdict": "FAIL" if error else "PASS",
                    "validator_error": error, "source_anchor_v01_verdict": v01["status"],
                    "source_anchor_v01_item_verdicts": v01["item_verdicts"]})
                if is_target:
                    route_units.append({"unit_id": uid, "routing_verdict": "CONTRACT_FIXABLE",
                                        "route_set": ["CONTRACT_FIXABLE"], "remaining_failed_item_count": len(ids)})
                    for item in items:
                        route = self.route_row(payload, copy.deepcopy(item))
                        route.update(unit_id=uid, local_id=item["local_id"], item=item,
                                     identity={"item": "sha256:" + sha256_json(item), "payload": "sha256:" + sha256_json(payload),
                                               "canonical_output": "sha256:" + sibling._sha256(path)})
                        route_items.append(route)
            manifest = base / "manifest.json"
            contract = {"units": manifest_units}
            write(manifest, {"contract": contract, "identity": sha256_json(contract)})
            source_path = base / "source.json"
            write(source_path, {"inputs": {"manifests": [str(manifest)], "roots": [str(root)]}, "rows": source_rows,
                                "counts": {"logical": 113, "pass": 107, "canonical_fail": 6, "no_canonical": 0, "provider_omitted": 0}})
            source_hash = sibling._sha256(source_path)
            routing_path = base / "routing.json"
            write(routing_path, {"identity": {"replay": "sha256:" + source_hash}, "units": route_units, "items": route_items,
                                 "inputs": [{"path": str(source_path), "sha256": source_hash},
                                            {"path": str(manifest), "sha256": sibling._sha256(manifest)}]})
            output = base / "sibling.json"
            with patch.object(sibling, "REPLAY_SHA256", source_hash), patch.object(sibling, "ROUTING_SHA256", sibling._sha256(routing_path)), \
                    patch("socket.socket.connect", side_effect=AssertionError("network prohibited")):
                result = sibling.replay(source_anchor_replay=source_path, routing=routing_path, output=output)
                self.assertEqual(result["after_sibling"]["recovered_units"], 6)
                self.assertEqual(result["after_sibling"]["new_recovered_items"], 19)
                self.assertEqual(result["controls"]["strict_PASS_replayed"], 107)
                self.assertEqual(result["after_sibling"]["canonical_fail"], 0)
                self.assertTrue(result["immutable_inputs"]["unchanged"])
                with self.assertRaisesRegex(ValueError, "already exists"):
                    sibling.replay(source_anchor_replay=source_path, routing=routing_path, output=output)
                manifest.write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "immutable input"):
                    sibling.replay(source_anchor_replay=source_path, routing=routing_path, output=base / "bad.json")


if __name__ == "__main__":
    unittest.main()
