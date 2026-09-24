from __future__ import annotations

import gzip
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.semantic_compiler_u1 import (
    CAP_PROFILE_SOFT_CAPS,
    CompilerContract,
    ProjectionPolicy,
    SEMANTIC_OUTPUT_SCHEMA_VERSION,
    SemanticCompilerU1Error,
    binding_materialization_identity,
    build_u1,
    decide_reuse,
    semantic_input_identity,
    validate_semantic_output_envelope,
    view_identity,
)
from genshin_corpus.parser.rich_text import parse_rich_text
from genshin_corpus.retrieval.semantic_compiler_u1 import CanonicalProvenance, _segment_from_unit, _select_sample


FIXTURE = Path(__file__).parents[1] / "fixtures" / "retrieval" / "canonical-rag-w1-record.json"


def _address(record: dict, section: dict, context: dict, unit: dict) -> dict:
    return {
        "source_identity_key": record["source_identity"]["key"],
        "record_id": record["record_id"],
        "section_ordinal": section["ordinal"],
        "component_observation_key": context["observation_key"],
        "component_ordinal": context["ordinal"],
        "canonical_unit_kind": unit["kind"],
        "canonical_unit_ordinal": unit["ordinal"],
        "parsed_json_pointer": unit["lineage"]["parsed_json_pointer"],
    }


class SemanticCompilerU1Tests(unittest.TestCase):
    def _inputs(self) -> tuple[Path, Path, tempfile.TemporaryDirectory[str]]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        record = json.loads(FIXTURE.read_text(encoding="utf-8"))
        record_path = root / "records" / "record.json"
        record_path.parent.mkdir()
        record_body = canonical_json_bytes(record)
        record_path.write_bytes(record_body)
        canonical_manifest = {
            "status": "complete",
            "canonical_run_id": "fixture-canonical-run",
            "input_record_count": 1,
            "accounted_record_count": 1,
            "input_integrity_failure_count": 0,
            "records": [{
                "record_id": record["record_id"],
                "canonical_record_path": str(record_path),
                "canonical_record_sha256": hashlib.sha256(record_body).hexdigest(),
            }],
        }
        canonical_manifest_path = root / "canonical" / "manifest.json"
        canonical_manifest_path.parent.mkdir()
        canonical_manifest_path.write_bytes(canonical_json_bytes(canonical_manifest))

        rows = []
        for section in record["sections"]:
            contexts = {context["observation_key"]: context for context in section["component_contexts"]}
            for unit in section["units"]:
                context = contexts[unit["parent_component_key"]]
                rows.append({
                    "unit_id": f"ru:{unit['unit_id']}",
                    "retrieval_visible_text": "fixture",
                    "source": {"canonical_address": _address(record, section, context, unit), "lineage": unit["lineage"]},
                })
        compressed = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as handle:
            for row in rows:
                handle.write(canonical_json_bytes(row) + b"\n")
        ru_root = root / "ru"
        (ru_root / "artifacts").mkdir(parents=True)
        artifact = ru_root / "artifacts" / "retrieval_units.jsonl.gz"
        artifact.write_bytes(compressed.getvalue())
        ru_manifest = {
            "status": "complete",
            "build_identity": "fixture-ru-build",
            "artifacts": {"retrieval_units": {"path": "artifacts/retrieval_units.jsonl.gz", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}},
        }
        ru_manifest_path = ru_root / "metadata" / "manifest.json"
        ru_manifest_path.parent.mkdir()
        ru_manifest_path.write_bytes(canonical_json_bytes(ru_manifest))
        return canonical_manifest_path, ru_manifest_path, temp

    def test_provider_free_build_is_deterministic_and_sidecar_bound(self) -> None:
        canonical_manifest, ru_manifest, temp = self._inputs()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name) / "u1"
        first = build_u1(canonical_manifest, ru_manifest, output, sample_target=3)
        second = build_u1(canonical_manifest, ru_manifest, output, sample_target=3)
        self.assertEqual(first, second)
        self.assertEqual(first["accounting"]["provider_calls"], 0)
        self.assertIsNone(json.loads((output / "profile.json").read_text(encoding="utf-8"))["provider_token_counts"])
        projection_line = gzip.open(output / "projection.jsonl.gz", "rt", encoding="utf-8").readline()
        projection = json.loads(projection_line)
        self.assertNotIn("canonical_record_sha256", projection["provider_payload"])
        sidecar = json.loads(gzip.open(output / "projection_sidecar.jsonl.gz", "rt", encoding="utf-8").readline())
        self.assertIn("canonical_record_sha256", sidecar["canonical_provenance"])
        self.assertTrue((output / "semantic_output_schema.json").exists())
        sample = json.loads((output / "sample_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sample["selected_count"], min(3, sample["available_compilation_unit_count"]))
        self.assertGreaterEqual(sample["challenger_paired_count"], 0)

    def test_semantic_identity_excludes_local_provenance_sidecar(self) -> None:
        payload = {"schema_version": "projection", "segments": [{"segment_id": "s000001", "value": {"text": "same"}}]}
        self.assertEqual(semantic_input_identity(payload), semantic_input_identity(dict(payload)))

    def test_unknown_revision_reuses_artifact_without_equivalence_claim(self) -> None:
        contract = CompilerContract(provider="provider", model="model", model_revision=None)
        decision = decide_reuse(
            existing_input_identity="input",
            requested_input_identity="input",
            existing_contract=contract,
            requested_contract=contract,
            raw_response_available=False,
            output_schema_unchanged=True,
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.provider_call_required)
        self.assertEqual(decision.equivalence_status, "UNKNOWN")

    def test_local_reparse_does_not_require_provider_call_when_raw_is_preserved(self) -> None:
        contract = CompilerContract(provider="provider", model="model", model_revision="rev-1")
        decision = decide_reuse(
            existing_input_identity="input",
            requested_input_identity="input",
            existing_contract=contract,
            requested_contract=contract,
            raw_response_available=True,
            output_schema_unchanged=True,
            local_parser_changed=True,
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.local_reparse_required)
        self.assertFalse(decision.provider_call_required)

    def test_provider_contract_change_cannot_be_hidden_by_local_parser_flag(self) -> None:
        existing = CompilerContract(provider="provider", model="model", model_revision="rev-1")
        requested = CompilerContract(provider="provider", model="model-v2", model_revision="rev-1")
        decision = decide_reuse(
            existing_input_identity="input",
            requested_input_identity="input",
            existing_contract=existing,
            requested_contract=requested,
            raw_response_available=True,
            output_schema_unchanged=True,
            local_parser_changed=True,
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.provider_call_required)
        self.assertEqual(decision.reason, "provider_request_contract_changed")

    def test_local_validator_change_reuses_preserved_raw_response(self) -> None:
        existing = CompilerContract(provider="provider", model="model", model_revision="rev-1")
        requested = CompilerContract(provider="provider", model="model", model_revision="rev-1", semantic_validator_identity="validator-v2")
        decision = decide_reuse(
            existing_input_identity="input",
            requested_input_identity="input",
            existing_contract=existing,
            requested_contract=requested,
            raw_response_available=True,
            output_schema_unchanged=True,
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.local_reparse_required)
        self.assertFalse(decision.provider_call_required)

    def test_local_reparse_fails_closed_without_raw_response(self) -> None:
        existing = CompilerContract(provider="provider", model="model", model_revision="rev-1")
        requested = CompilerContract(provider="provider", model="model", model_revision="rev-1", semantic_validator_identity="validator-v2")
        decision = decide_reuse(
            existing_input_identity="input",
            requested_input_identity="input",
            existing_contract=existing,
            requested_contract=requested,
            raw_response_available=False,
            output_schema_unchanged=True,
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.provider_call_required)

    def test_cap_profile_is_recorded_as_configurable_operating_points(self) -> None:
        canonical_manifest, ru_manifest, temp = self._inputs()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name) / "u1"
        build_u1(canonical_manifest, ru_manifest, output, sample_target=3)
        profile = json.loads((output / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual([row["soft_cap"] for row in profile["cap_profile"]], list(CAP_PROFILE_SOFT_CAPS))
        self.assertTrue(all("compilation_unit_count" in row and "oversized_unresolved_count" in row for row in profile["cap_profile"]))

    def test_nonprovider_canonical_metadata_and_serialization_do_not_change_semantic_input(self) -> None:
        canonical_manifest, ru_manifest, temp = self._inputs()
        self.addCleanup(temp.cleanup)
        first_root = Path(temp.name) / "first"
        first = build_u1(canonical_manifest, ru_manifest, first_root, sample_target=3)
        record_path = Path(json.loads(canonical_manifest.read_text(encoding="utf-8"))["records"][0]["canonical_record_path"])
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.setdefault("record_metadata", {})["channel_memberships"] = ["metadata-only-change"]
        record_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        manifest = json.loads(canonical_manifest.read_text(encoding="utf-8"))
        manifest["records"][0]["canonical_record_sha256"] = hashlib.sha256(record_path.read_bytes()).hexdigest()
        canonical_manifest.write_bytes(canonical_json_bytes(manifest))
        second_root = Path(temp.name) / "second"
        second = build_u1(canonical_manifest, ru_manifest, second_root, sample_target=3)
        self.assertEqual(first["semantic_build_identity"], second["semantic_build_identity"])
        self.assertNotEqual(first["canonical_manifest_sha256"], second["canonical_manifest_sha256"])
        first_units = [json.loads(line) for line in gzip.open(first_root / "compilation_units.jsonl.gz", "rt", encoding="utf-8")]
        second_units = [json.loads(line) for line in gzip.open(second_root / "compilation_units.jsonl.gz", "rt", encoding="utf-8")]
        self.assertEqual([row["semantic_input_identity"] for row in first_units], [row["semantic_input_identity"] for row in second_units])

    def test_ru_rebinding_and_view_policy_do_not_change_semantic_input(self) -> None:
        binding_a = binding_materialization_identity(
            semantic_artifact_identity="artifact",
            canonical_provenance_identity="canonical",
            retrieval_unit_build_identity="ru-a",
        )
        binding_b = binding_materialization_identity(
            semantic_artifact_identity="artifact",
            canonical_provenance_identity="canonical",
            retrieval_unit_build_identity="ru-b",
        )
        self.assertNotEqual(binding_a, binding_b)
        hierarchy_a = view_identity(semantic_build_identity="build", view_kind="hierarchy", view_policy_identity="policy-a")
        hierarchy_b = view_identity(semantic_build_identity="build", view_kind="hierarchy", view_policy_identity="policy-b")
        graph = view_identity(semantic_build_identity="build", view_kind="graph", view_policy_identity="policy-a")
        self.assertNotEqual(hierarchy_a, hierarchy_b)
        self.assertNotEqual(hierarchy_a, graph)

    def test_actual_ru_only_rebinding_preserves_semantic_build(self) -> None:
        canonical_manifest, ru_manifest, temp = self._inputs()
        self.addCleanup(temp.cleanup)
        first_root = Path(temp.name) / "ru-first"
        first = build_u1(canonical_manifest, ru_manifest, first_root, sample_target=3)
        manifest = json.loads(ru_manifest.read_text(encoding="utf-8"))
        artifact = ru_manifest.parent.parent / manifest["artifacts"]["retrieval_units"]["path"]
        rows = [json.loads(line) for line in gzip.open(artifact, "rt", encoding="utf-8")]
        for row in rows:
            row["unit_id"] = f"rebound:{row['unit_id']}"
        compressed = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as handle:
            for row in rows:
                handle.write(canonical_json_bytes(row) + b"\n")
        artifact.write_bytes(compressed.getvalue())
        manifest["build_identity"] = "fixture-ru-build-v2"
        manifest["artifacts"]["retrieval_units"]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        ru_manifest.write_bytes(canonical_json_bytes(manifest))
        second_root = Path(temp.name) / "ru-second"
        second = build_u1(canonical_manifest, ru_manifest, second_root, sample_target=3)
        self.assertEqual(first["semantic_build_identity"], second["semantic_build_identity"])
        self.assertNotEqual(first["retrieval_unit_build_identity"], second["retrieval_unit_build_identity"])
        first_sidecar = json.loads(gzip.open(first_root / "projection_sidecar.jsonl.gz", "rt", encoding="utf-8").readline())
        second_sidecar = json.loads(gzip.open(second_root / "projection_sidecar.jsonl.gz", "rt", encoding="utf-8").readline())
        self.assertNotEqual(first_sidecar["retrieval_unit_ids"], second_sidecar["retrieval_unit_ids"])
        self.assertEqual(first_sidecar["projection_identity"], second_sidecar["projection_identity"])

    def test_source_meaningful_dialogue_split_preserves_parent_lineage(self) -> None:
        canonical_manifest, ru_manifest, temp = self._inputs()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name) / "split"
        build_u1(
            canonical_manifest,
            ru_manifest,
            output,
            policy=ProjectionPolicy(record_soft_cap=100, hard_cap=200),
            sample_target=3,
        )
        units = [json.loads(line) for line in gzip.open(output / "compilation_units.jsonl.gz", "rt", encoding="utf-8")]
        split_ids = [segment_id for row in units for segment_id in row.get("segment_ids", []) if ":p" in segment_id]
        self.assertTrue(split_ids)
        sidecars = {
            row["segment_id"]: row
            for row in (json.loads(line) for line in gzip.open(output / "projection_sidecar.jsonl.gz", "rt", encoding="utf-8"))
        }
        for split_id in split_ids:
            parent_id = split_id.split(":p", 1)[0]
            self.assertEqual(sidecars[split_id]["split_parent_segment_id"], parent_id)
            self.assertEqual(sidecars[split_id]["canonical_address"], sidecars[parent_id]["canonical_address"])
        split_payloads = [
            segment["value"]["dialogue"]
            for row in units
            for segment in row["provider_payload"]["segments"]
            if ":p" in segment["segment_id"]
        ]
        self.assertTrue(all("boundary_edges" in payload and "split_parent_segment_id" in payload for payload in split_payloads))
        profile = json.loads((output / "profile.json").read_text(encoding="utf-8"))
        self.assertGreater(profile["oversized_unresolved_count"], 0)

    def test_semantic_output_requires_explicit_complete_segment_accounting(self) -> None:
        envelope = {
            "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
            "items": [{
                "local_id": "i1",
                "kind": "relation",
                "label": "A helps B",
                "source_segment_ids": ["seg-a"],
                "topic_path": ["story"],
                "subject_ref": "A",
                "object_ref": "B",
                "predicate": "helps",
                "event_type": None,
                "participants": [],
                "qualifiers": {"epistemic": "asserted"},
            }],
            "segment_coverage": [
                {"segment_id": "seg-a", "disposition": "covered", "reason": None},
                {"segment_id": "seg-b", "disposition": "no_navigation_material", "reason": "heading only"},
            ],
        }
        normalized = validate_semantic_output_envelope(envelope, expected_segment_ids=("seg-a", "seg-b"))
        self.assertEqual(normalized["items"][0]["local_id"], "i1")
        with self.assertRaisesRegex(SemanticCompilerU1Error, "account for every input segment"):
            validate_semantic_output_envelope(
                {**envelope, "segment_coverage": envelope["segment_coverage"][:1]},
                expected_segment_ids=("seg-a", "seg-b"),
            )
        with self.assertRaisesRegex(SemanticCompilerU1Error, "only covered segments"):
            validate_semantic_output_envelope(
                {**envelope, "items": [{**envelope["items"][0], "source_segment_ids": ["seg-b"]}]},
                expected_segment_ids=("seg-a", "seg-b"),
            )

    def test_projection_policy_rejects_invalid_caps(self) -> None:
        with self.assertRaises(ValueError):
            ProjectionPolicy(record_soft_cap=16_000, hard_cap=8_000)

    def test_sample_uses_overlapping_quality_and_paired_memberships(self) -> None:
        units = []
        sidecars = {}
        categories = [
            "category:dialogue_branch", "category:pronoun_omitted_subject", "category:identity_role",
            "category:temporal_order", "category:causality", "category:knowledge_belief",
            "category:rumor_reveal", "category:cross_record_reference", "category:narrative_long",
        ]
        for index in range(60):
            segment_id = f"s{index}"
            unit_id = f"u{index}"
            if index < 20:
                tags = {"kind:rich_text", categories[index % len(categories)]}
            elif index < 24:
                tags = {"kind:rich_text", "category:ordinary_control"}
            elif index == 24:
                tags = {"kind:structured_observation", "structured"}
            elif index == 25:
                tags = {"kind:dialogue_graph", "dialogue"}
            else:
                tags = {"kind:rich_text"}
            units.append({
                "compilation_unit_id": unit_id,
                "segment_ids": [segment_id],
                "record_id": f"record-{index}",
                "unit_scope": "segment",
                "semantic_input_identity": f"input-{index}",
                "serialized_chars": 100 + index,
            })
            sidecars[segment_id] = {"tags": sorted(tags), "retrieval_unit_ids": [f"ru-{index}"]}
        sample = _select_sample(units, sidecars, target=42)
        self.assertEqual(sample["membership_counts"], {"projection_contract": 42, "semantic_quality": 30, "projection_only": 12})
        self.assertEqual(sample["challenger_paired_count"], 18)
        paired = [item for item in sample["items"] if item["challenger_paired"]]
        self.assertEqual(sum("challenger_high_risk" in " ".join(item["selection_rationale"]) for item in paired), 14)
        self.assertEqual(sum("challenger_matched_control" in " ".join(item["selection_rationale"]) for item in paired), 4)
        self.assertTrue(all(item["projection_contract_member"] for item in sample["items"]))
        controls = [item for item in paired if any(r.startswith("challenger_matched_control:") for r in item["selection_rationale"])]
        self.assertTrue(all(not any(tag.startswith("category:") and tag != "category:ordinary_control" for tag in item["tags"]) for item in controls))

    def test_sample_requires_all_segments_to_have_ru_binding(self) -> None:
        units = [
            {"compilation_unit_id": "bound", "segment_ids": ["s1", "s2"], "record_id": "r1", "unit_scope": "section", "semantic_input_identity": "i1", "serialized_chars": 10},
            {"compilation_unit_id": "unbound", "segment_ids": ["s3", "s4"], "record_id": "r2", "unit_scope": "section", "semantic_input_identity": "i2", "serialized_chars": 10},
        ]
        sidecars = {
            "s1": {"tags": ["kind:rich_text", "category:ordinary_control"], "retrieval_unit_ids": ["ru-1"]},
            "s2": {"tags": ["kind:rich_text", "category:ordinary_control"], "retrieval_unit_ids": ["ru-2"]},
            "s3": {"tags": ["kind:rich_text", "category:ordinary_control"], "retrieval_unit_ids": []},
            "s4": {"tags": ["kind:rich_text", "category:ordinary_control"], "retrieval_unit_ids": ["ru-4"]},
        }
        sample = _select_sample(units, sidecars, target=1)
        self.assertEqual(sample["considered_compilation_unit_count"], 2)
        self.assertEqual(sample["ru_ineligible_compilation_unit_count"], 1)
        self.assertEqual(sample["selected_count"], 1)
        item = sample["items"][0]
        self.assertTrue(item["ru_binding"]["eligible_for_live_sample"])
        self.assertEqual(item["ru_binding"]["source_segment_count"], 2)
        self.assertEqual(item["ru_binding"]["ru_bound_segment_count"], 2)
        self.assertEqual(item["ru_binding"]["ru_unbound_segment_count"], 0)

    def test_real_rich_text_projection_keeps_paths_without_lexical_duplication(self) -> None:
        value = {"kind": "rich_text", **parse_rich_text("<p>保留<strong>文本</strong></p><p>链接</p>")}
        segment, omissions = _segment_from_unit(
            record={"source_identity": {"key": "source"}, "record_id": "record"},
            section={"source_metadata": {"name": "section"}},
            context={"source_component_id": "component", "observation_key": "component", "ordinal": 0},
            unit={"kind": "rich_text", "unit_id": "unit", "value": value, "lineage": {}, "content_role": {}},
            provenance=CanonicalProvenance("record", "sha", None, None, "run", "manifest"),
            policy=ProjectionPolicy(),
        )
        self.assertFalse(omissions)
        projected = segment[0].provider_value
        self.assertEqual(projected["text"], "保留文本\n链接")
        self.assertEqual(projected["text_segments"], [{"path": "/0/0", "char_count": 2}, {"path": "/0/1/0", "char_count": 2}, {"path": "/1/0", "char_count": 2}])
        self.assertTrue(all("text" not in entry for entry in projected["text_segments"]))


if __name__ == "__main__":
    unittest.main()
