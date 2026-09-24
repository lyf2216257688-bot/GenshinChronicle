from __future__ import annotations

import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_IDENTITY, semantic_input_identity
from genshin_corpus.retrieval.semantic_live_preflight import SemanticLivePreflightError, build_live_preflight


def _gzip_rows(rows: list[dict]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as handle:
        for row in rows:
            handle.write(canonical_json_bytes(row) + b"\n")
    return output.getvalue()


class SemanticLivePreflightTests(unittest.TestCase):
    def _u1(self, root: Path, *, predecessor: bool = False, unbound: bool = False) -> Path:
        root.mkdir(parents=True)
        items, units, sidecars = [], [], []
        for index in range(42):
            unit_id = f"u{index}"
            segment_id = f"s{index}"
            payload = {"schema_version": "fixture", "record_key": f"r{index}", "title": "fixture", "segments": [{"segment_id": segment_id, "value": {"kind": "rich_text", "text": f"text {index}"}}], "omission_summary": {"count": 0, "reasons": {}, "partial_input": False}}
            input_id = semantic_input_identity(payload)
            paired = index < 18
            if index < 14:
                rationale = ["challenger_high_risk:category:dialogue_branch"]
            elif index < 18:
                rationale = [f"challenger_matched_control:{index - 13}"]
            elif index < 30:
                rationale = ["semantic_quality:coverage_fill"]
            else:
                rationale = ["projection_contract:bounded_fill"]
            if predecessor and index in {14, 15}:
                unit_id = f"old-control-{index}"
                rationale = [f"challenger_matched_control:{index - 13}"]
            if predecessor and index in {0, 18}:
                unit_id = f"old-oversized-{index}"
            item = {"compilation_unit_id": unit_id, "record_id": f"r{index}", "segment_id": segment_id, "semantic_input_identity": input_id, "serialized_chars": 20_001 if predecessor and index in {0, 18} else 200, "projection_contract_member": True, "semantic_quality_member": index < 30, "challenger_paired": paired, "selection_rationale": rationale, "ru_binding": {"eligible_for_live_sample": True, "source_segment_count": 1, "ru_bound_segment_count": 1, "ru_unbound_segment_count": 0, "retrieval_unit_id_count": 1, "retrieval_unit_ids_sha256": "fixture"}}
            items.append(item)
            units.append({"compilation_unit_id": unit_id, "record_id": f"r{index}", "segment_ids": [segment_id], "provider_payload": payload, "semantic_input_identity": input_id})
            sidecars.append({"segment_id": segment_id, "retrieval_unit_ids": [] if unbound and index == 0 else [f"ru{index}"]})
        (root / "manifest.json").write_bytes(canonical_json_bytes({"semantic_build_identity": "build"}))
        (root / "identity.json").write_bytes(canonical_json_bytes({"semantic_output_schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY}))
        (root / "semantic_output_schema.json").write_bytes(canonical_json_bytes({"schema": "fixture"}))
        (root / "sample_manifest.json").write_bytes(canonical_json_bytes({"schema_version": "fixture", "membership_counts": {"projection_contract": 42, "projection_only": 12, "semantic_quality": 30}, "items": items}))
        (root / "compilation_units.jsonl.gz").write_bytes(_gzip_rows(units))
        (root / "projection_sidecar.jsonl.gz").write_bytes(_gzip_rows(sidecars))
        return root

    def test_freezes_exact_bounded_sets_and_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self._u1(base / "r5", predecessor=True)
            corrected = self._u1(base / "r7")
            output = base / "preflight"
            result = build_live_preflight(corrected, output, review_source_u1_root=source)
            self.assertEqual(result["sample_membership"], {"gemini_semantic_quality": 30, "deepseek_paired": 18, "paired_high_risk": 14, "paired_matched_control": 4})
            self.assertEqual(result["request_ceiling"]["combined_maximum_requests"], 48)
            self.assertEqual(result["provider_calls_executed"], 0)
            self.assertEqual(result["output_contract"]["dry_run"]["semantic_build_dispositions"], {"active": 1, "inactive": 1, "binding_rejected": 1})
            self.assertEqual(result["control_review"]["replaced_count"], 2)
            self.assertEqual(len(result["sample_correction"]["removed_oversized_unit_ids"]), 2)
            self.assertEqual(build_live_preflight(corrected, output, review_source_u1_root=source)["preflight_identity"], result["preflight_identity"])

    def test_unbound_selected_unit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self._u1(base / "r5", predecessor=True)
            corrected = self._u1(base / "r7", unbound=True)
            with self.assertRaisesRegex(SemanticLivePreflightError, "lacks RU binding"):
                build_live_preflight(corrected, base / "preflight", review_source_u1_root=source)


if __name__ == "__main__":
    unittest.main()
