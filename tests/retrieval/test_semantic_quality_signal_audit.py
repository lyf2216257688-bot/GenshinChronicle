from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from genshin_corpus.retrieval.semantic_quality_signal_audit import (
    QualitySignalAuditError,
    _has_explicit_stage_marker,
    _payload_features,
)


class SemanticQualitySignalAuditTests(unittest.TestCase):
    def test_features_keep_structural_signals_separate_from_quality(self) -> None:
        payload = {
            "segments": [
                {"segment_id": "text", "value": {"component": "interactive_dialogue", "kind": "rich_text", "text": "A"}},
                {"segment_id": "map", "value": {"component": "map_desc", "kind": "structured_observation", "decoded": {"list": []}}},
            ]
        }
        output = {
            "items": [{"kind": "topic", "qualifiers": {}, "source_segment_ids": ["text"]}],
            "segment_coverage": [
                {"segment_id": "text", "disposition": "covered"},
                {"segment_id": "map", "disposition": "covered"},
            ],
        }
        features = _payload_features(payload, output, {"terminal_disposition": "accepted_for_local_contract"})
        self.assertTrue(features["input_has_map_desc"])
        self.assertEqual(features["input_segment_count"], 2)
        self.assertFalse(features["output_empty"])
        self.assertEqual(features["validator_terminal_disposition"], "accepted_for_local_contract")

    def test_immutable_write_rejects_divergent_existing_artifact(self) -> None:
        # Importing the private helper here keeps the test focused on the audit's
        # append-only boundary without constructing large source artifacts.
        from genshin_corpus.retrieval.semantic_quality_signal_audit import _immutable_write

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "artifact.json"
            _immutable_write(path, {"value": 1})
            with self.assertRaises(QualitySignalAuditError):
                _immutable_write(path, {"value": 2})

    def test_explicit_stage_requires_compiler_metadata(self) -> None:
        cap_split = {"segments": [{"segment_id": "seg-x:p509", "value": {"dialogue": {"nodes": [{"dialogue": "hello"}]}}}]}
        point_stage = {"segments": [{"segment_id": "seg-x:p0", "value": {"dialogue": {"nodes": [{"semantic_stage": {"boundary": "explicit_point_marker", "stage_index": 0}}]}}}]}
        heading_stage = {"segments": [{"segment_id": "seg-y:p0", "value": {"semantic_stage": {"boundary": "explicit_heading", "stage_index": 0}, "text": "主题：A"}}]}
        self.assertFalse(_has_explicit_stage_marker(cap_split))
        self.assertTrue(_has_explicit_stage_marker(point_stage))
        self.assertTrue(_has_explicit_stage_marker(heading_stage))
        self.assertFalse(_payload_features(cap_split, {"items": [], "segment_coverage": []}, {})["input_has_explicit_stage_marker"])
        self.assertTrue(_payload_features(point_stage, {"items": [], "segment_coverage": []}, {})["input_has_explicit_stage_marker"])


if __name__ == "__main__":
    unittest.main()
