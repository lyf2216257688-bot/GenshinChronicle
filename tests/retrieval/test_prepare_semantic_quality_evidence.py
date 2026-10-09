from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path

from genshin_corpus.retrieval.prepare_semantic_quality_evidence import (
    KNOWN_REVIEWED,
    _category,
    _source_location,
    _write_exclusive,
    prepare,
    _find_attempt,
    verify,
    _sha256,
)


class PrepareSemanticQualityEvidenceTest(unittest.TestCase):
    def test_source_location_keeps_segment_and_dialogue_provenance(self) -> None:
        payload = {
            "record_key": "mihoyo_obc:zh-cn:test",
            "title": "测试",
            "segments": [{
                "segment_id": "seg-1:p2",
                "value": {
                    "component": "interactive_dialogue",
                    "content_role": ["dialogue"],
                    "section": "对话",
                    "dialogue": {
                        "source_path": "/list/0",
                        "split_parent_segment_id": "seg-1",
                        "split_index": 2,
                        "split_count": 3,
                    },
                },
            }],
        }
        self.assertEqual(_category(payload), ["interactive_dialogue", "role:dialogue"])
        location = _source_location(payload)
        self.assertEqual(location["segments"][0]["segment_id"], "seg-1:p2")
        self.assertEqual(location["segments"][0]["dialogue_source_path"], "/list/0")
        self.assertEqual(location["segments"][0]["dialogue_split"]["split_index"], 2)

    def test_exclusive_write_rejects_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            _write_exclusive(path, b"one")
            with self.assertRaises(FileExistsError):
                _write_exclusive(path, b"two")

    def test_real_output_root_is_refused_before_reading_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                prepare(Path(directory), output)

    def test_validation_ledger_does_not_include_selected_validation_ids(self) -> None:
        selected_validation = {"505240", "5310", "6473", "508046", "502342", "508276"}
        self.assertEqual(selected_validation & KNOWN_REVIEWED, set())

    def test_attempt_mapping_uses_issued_logical_id_and_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt = root / "units/physical-hash/attempt-001"
            attempt.mkdir(parents=True)
            (attempt / "issued.json").write_text(json.dumps({"unit_id": "logical-id"}), encoding="utf-8")
            unit = {"_root": str(root), "compilation_unit_id": "logical-id", "payload": {"record_key": "test:1"}}
            self.assertEqual(_find_attempt(unit), attempt)
            duplicate = root / "units/other-physical-hash/attempt-001"
            duplicate.mkdir(parents=True)
            (duplicate / "issued.json").write_text(json.dumps({"unit_id": "logical-id"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "multiple persisted attempts"):
                _find_attempt(unit)

    def test_integrity_detects_output_tamper_and_original_evidence_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root / "sample"
            sample.mkdir()
            original = root / "original.json"
            original.write_bytes(b"{}")
            copied = sample / "canonical_output.json"
            copied.write_bytes(original.read_bytes())
            source = sample / "source_payload.json"
            source.write_text(json.dumps({"record_key": "test:1"}), encoding="utf-8")
            (sample / "provenance.json").write_text(json.dumps({
                "source_payload_sha256": _sha256(source),
                "artifacts": {"canonical_output": {"path": str(original), "source_sha256": _sha256(original)}},
            }), encoding="utf-8")
            (root / "immutable-inputs.json").write_text(json.dumps({"before_sha256": {str(original): _sha256(original)}}), encoding="utf-8")
            (root / "index.json").write_text(json.dumps({
                "rows": [{"sample_id": "one", "record_id": "1", "record_key": "test:1", "sample_root": "sample"}],
                "inputs": {"manifests": [], "replay_report": str(original)},
                "artifacts_sha256": {"sample/canonical_output.json": _sha256(copied)},
            }), encoding="utf-8")
            self.assertEqual(verify(root)["status"], "PASS")
            copied.write_bytes(b'{"tampered":true}')
            self.assertEqual(verify(root)["status"], "FAIL")
            copied.write_bytes(b"{}")
            original.write_bytes(b'{"changed":true}')
            self.assertEqual(verify(root)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
