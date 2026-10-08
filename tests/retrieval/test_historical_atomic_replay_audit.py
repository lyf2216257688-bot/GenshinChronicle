from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from genshin_corpus.retrieval.historical_atomic_replay_audit import (
    _source_evidence, _failure_class, _validate_with_source_anchor,
)


class HistoricalAtomicReplayAuditTest(unittest.TestCase):
    def test_quote_presence_uses_bound_natural_projection(self):
        payload = {"segments": [{"segment_id": "s1", "value": {"text": "自然文本"}}]}
        self.assertEqual(_source_evidence(payload, "自然文本", ["s1"])["quote_presence"], "natural_text")

    def test_structured_and_serialized_metadata_are_distinct(self):
        payload = {"segments": [{"segment_id": "s1", "value": {
            "decoded": {"entry_name": "结构节点"}, "references": [{"name": "元数据"}]
        }}]}
        self.assertEqual(_source_evidence(payload, "结构节点", ["s1"])["quote_presence"], "structured_value")
        evidence = _source_evidence(payload, '\"name\": \"元数据\"', ["s1"])
        self.assertEqual(evidence["quote_presence"], "serialized_metadata")
        self.assertEqual(_failure_class("atomic item x source_quote is not in source", evidence)[0],
                         "metadata_serialized_json_contamination")

    def test_nowhere_quote_is_fail_closed_and_not_anchor_candidate(self):
        evidence = _source_evidence({"segments": [{"segment_id": "s1", "value": {"text": "A"}}]}, "B", ["s1"])
        self.assertEqual(_failure_class("atomic item x source_quote is not in source", evidence),
                         ("fabricated_or_unbound_quote", False))

    def test_unique_decoded_scalar_is_accepted_as_versioned_anchor(self):
        payload = {"segments": [{"segment_id": "s1", "value": {
            "kind": "structured_observation", "decoded": {"name": "结构节点"}
        }}]}
        output = {"schema_version": "phase05-w2-semantic-output-0.1", "items": [{
            "local_id": "f1", "kind": "fact", "label": "名称是结构节点",
            "source_segment_ids": ["s1"], "topic_path": [],
            "qualifiers": {"source_quote": "结构节点"}
        }], "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}]}
        result = _validate_with_source_anchor(
            output, payload, failed_item_classes={"f1": "structured_value_quote_projection_boundary"},
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["anchors"][0]["json_pointer"], "/value/decoded/name")

    def test_decoded_value_does_not_override_ineligible_audit_class(self):
        payload = {"segments": [{"segment_id": "s1", "value": {
            "kind": "structured_observation", "decoded": {"name": "unbound claim"}
        }}]}
        output = {"schema_version": "phase05-w2-semantic-output-0.1", "items": [{
            "local_id": "f1", "kind": "fact", "label": "claim",
            "source_segment_ids": ["s1"], "topic_path": [],
            "qualifiers": {"source_quote": "unbound claim"}
        }], "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}]}
        result = _validate_with_source_anchor(
            output, payload, failed_item_classes={"f1": "fabricated_or_unbound_quote"},
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["anchors"], [])
        self.assertEqual(result["item_verdicts"][0]["failure_class"], "fabricated_or_unbound_quote")

    def test_serialized_json_fragment_and_nonunique_anchor_fail_closed(self):
        payload = {"segments": [{"segment_id": "s1", "value": {
            "kind": "structured_observation", "decoded": {"name": "结构节点", "alias": "结构节点"}
        }}]}
        output = {"schema_version": "phase05-w2-semantic-output-0.1", "items": [{
            "local_id": "f1", "kind": "fact", "label": "名称",
            "source_segment_ids": ["s1"], "topic_path": [],
            "qualifiers": {"source_quote": '"name": "结构节点"'}
        }], "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}]}
        result = _validate_with_source_anchor(output, payload)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["anchors"], [])
        self.assertEqual(result["item_verdicts"][0]["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
