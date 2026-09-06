from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.evidence_assembly import (
    A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION,
    A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY,
    CANDIDATE_ANCHORED_SHADOW_POLICY,
    EvidenceAssemblyConfig,
    EvidenceAssemblyDiagnostics,
    EvidenceAssemblyError,
    EvidenceSelectionPolicy,
    PreparedAssemblyContext,
    V2_DIRECT_FIRST_CONTEXT_CAP,
    V2_DIRECT_FIRST_CONTEXT_CAP_POLICY,
    assemble_evidence_packet,
    assemble_candidate_anchored_shadow_packet,
    evidence_packet_json_bytes,
    evidence_packet_markdown,
    prepare_evidence_assembly_context,
    render_evidence_packet,
    write_evidence_packet,
)
from genshin_corpus.retrieval import evidence_assembly as evidence_assembly_module
from genshin_corpus.retrieval.retrieval_units import (
    RetrievalUnitBuildConfig,
    RetrievalUnitError,
    build_retrieval_units,
    load_retrieval_units,
)


class RagW1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.rag-w1-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        fixture = Path(__file__).parents[1] / "fixtures" / "retrieval" / "canonical-rag-w1-record.json"
        self.record = json.loads(fixture.read_text(encoding="utf-8"))
        self.record_path = self.root / "canonical-record.json"
        self._write_record()
        self.manifest_path = self.root / "canonical-manifest.json"
        self._write_manifest()
        self.config = RetrievalUnitBuildConfig(text_fragment_chars=4, structured_fragment_chars=10)

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _write_record(self) -> None:
        self.record_body = canonical_json_bytes(self.record)
        self.record_path.write_bytes(self.record_body)

    def _write_manifest(self) -> None:
        self.manifest = {
            "status": "complete",
            "canonical_run_id": "canonical-rag-w1-fixture",
            "source": "mihoyo_obc",
            "locale": "zh-cn",
            "input_record_count": 1,
            "accounted_record_count": 1,
            "input_integrity_failure_count": 0,
            "dependencies": {
                "canonical_versions": {
                    "schema_version": "phase03-draft-0.1",
                    "transform_version": "obc-modules-as-sections-0.1",
                    "structural_normalization_version": "none-0.1",
                    "classification_rule_versions": {},
                }
            },
            "records": [{
                "record_id": self.record["record_id"],
                "canonical_record_path": str(self.record_path),
                "canonical_record_sha256": hashlib.sha256(self.record_body).hexdigest(),
                "canonical_status": "canonical",
            }],
        }
        self.manifest_path.write_bytes(canonical_json_bytes(self.manifest))

    def _build(self, name: str = "build") -> tuple[dict, list[dict], Path]:
        output = self.root / name
        result = build_retrieval_units(self.manifest_path, output, config=self.config)
        _, units = load_retrieval_units(output / "metadata" / "manifest.json")
        return result, list(units), output

    def _unit(self, units: list[dict], content_type: str, **selector: object) -> dict:
        for unit in units:
            if unit["content_type"] != content_type:
                continue
            structure = unit.get("structure", {})
            dialogue = structure.get("dialogue", {}) if isinstance(structure, dict) else {}
            if all(dialogue.get(key) == value for key, value in selector.items()):
                return unit
        self.fail(f"No {content_type} unit with selector {selector}")

    def _dialogue_alias_units(self) -> tuple[list[dict], Path, dict, dict]:
        self.config = RetrievalUnitBuildConfig(text_fragment_chars=1000, structured_fragment_chars=1000)
        component = self.record["sections"][0]["component_contexts"][0]
        component["source_component_id"] = "interactive_dialogue"
        rich = self.record["sections"][0]["units"][0]
        rich["value"]["normalized_text"] = "Start\nAlpha"
        rich["lineage"]["raw_refs"][0]["embedded_json_pointer"] = "/contents/a/dialogue"
        self._write_record()
        self._write_manifest()
        _, units, output = self._build("dialogue-alias")
        return units, output, self._unit(units, "rich_text"), self._unit(units, "dialogue_node", node_source_id="a")

    def test_build_is_deterministic_and_unit_identity_is_occurrence_stable(self) -> None:
        first, first_units, first_root = self._build("build-a")
        second, second_units, second_root = self._build("build-b")
        self.assertEqual(first["build_identity"], second["build_identity"])
        self.assertEqual(first_units, second_units)
        self.assertEqual(
            (first_root / "artifacts" / "retrieval_units.jsonl.gz").read_bytes(),
            (second_root / "artifacts" / "retrieval_units.jsonl.gz").read_bytes(),
        )
        changed_manifest = dict(self.manifest, canonical_run_id="changed-global-run")
        changed_path = self.root / "canonical-manifest-changed-run.json"
        changed_path.write_bytes(canonical_json_bytes(changed_manifest))
        changed_output = self.root / "build-changed-run"
        build_retrieval_units(changed_path, changed_output, config=self.config)
        _, changed_units = load_retrieval_units(changed_output / "metadata" / "manifest.json")
        self.assertNotEqual(first["build_identity"], json.loads((changed_output / "metadata" / "manifest.json").read_text(encoding="utf-8"))["build_identity"])
        self.assertEqual([item["unit_id"] for item in first_units], [item["unit_id"] for item in changed_units])
        self.assertNotIn(str(self.record_path), canonical_json_bytes(first).decode("utf-8"))

    def test_build_identity_is_not_polluted_by_manifest_record_path(self) -> None:
        first, _, _ = self._build("path-a")
        other_record_path = self.root / "relocated" / "same-record.json"
        other_record_path.parent.mkdir()
        other_record_path.write_bytes(self.record_body)
        relocated = json.loads(canonical_json_bytes(self.manifest).decode("utf-8"))
        relocated["records"][0]["canonical_record_path"] = str(other_record_path)
        relocated_path = self.root / "canonical-manifest-relocated.json"
        relocated_path.write_bytes(canonical_json_bytes(relocated))
        second = build_retrieval_units(relocated_path, self.root / "path-b", config=self.config)
        self.assertEqual(first["build_identity"], second["build_identity"])

    def test_generator_implementation_version_does_not_change_unit_identity(self) -> None:
        _, first_units, _ = self._build("generator-a")
        with patch(
            "genshin_corpus.retrieval.retrieval_units.RETRIEVAL_UNIT_GENERATOR_VERSION",
            "phase04-rag-w1-builder-test-change",
        ):
            _, changed_units, _ = self._build("generator-b")
        self.assertEqual(
            [item["unit_id"] for item in first_units],
            [item["unit_id"] for item in changed_units],
        )

    def test_rich_and_structured_fragmentation_are_deterministic(self) -> None:
        _, units, _ = self._build()
        rich = [item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] in {"ABCD", "EFGH", "IJK"}]
        self.assertEqual([item["retrieval_visible_text"] for item in rich], ["ABCD", "EFGH", "IJK"])
        self.assertEqual([item["fragment_selector"]["fragment_index"] for item in rich], [0, 1, 2])
        structured = [item for item in units if item["content_type"] == "structured"]
        self.assertEqual([item["retrieval_visible_text"] for item in structured], ["/a\tone", "/b\tvalue-t", "hat-is-lon", "g"])
        oversized = structured[1:]
        self.assertTrue(all(item["fragment_selector"]["kind"] == "oversized_scalar_line_range" for item in oversized))
        self.assertEqual("".join(item["retrieval_visible_text"] for item in oversized), "/b\tvalue-that-is-long")

    def test_assembly_restores_fragment_text_without_inserted_separator(self) -> None:
        _, units, output = self._build()
        rich = [
            item for item in units
            if item["content_type"] == "rich_text" and item["retrieval_visible_text"] in {"ABCD", "EFGH", "IJK"}
        ]
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": item["unit_id"], "rank": index + 1} for index, item in enumerate(rich)],
            config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=1000),
        )
        self.assertEqual(len(packet["evidence"]), 1)
        self.assertEqual(packet["evidence"][0]["text"], "ABCDEFGHIJK")

    def test_structured_neighbor_uses_scalar_line_order_only_within_one_unit(self) -> None:
        _, units, output = self._build()
        structured = [item for item in units if item["content_type"] == "structured"]
        first = structured[0]
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": first["unit_id"], "rank": 1}],
            config=EvidenceAssemblyConfig(
                neighbor_before=0,
                neighbor_after=0,
                structured_neighbor_before=0,
                structured_neighbor_after=1,
                per_block_chars=100,
                total_context_chars=1000,
            ),
        )
        members = [member for block in packet["evidence"] for member in block["members"]]
        self.assertEqual([item["unit_id"] for item in members], [first["unit_id"], structured[1]["unit_id"]])
        self.assertTrue(any(reason["kind"] == "structured_neighbor" for reason in members[1]["assembly_reasons"]))

    def test_structured_assembly_merges_packed_and_next_oversized_scalar_line(self) -> None:
        _, units, output = self._build()
        structured = [item for item in units if item["content_type"] == "structured"]
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": item["unit_id"], "rank": index + 1} for index, item in enumerate(structured)],
            config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=1000),
        )
        self.assertEqual(len(packet["evidence"]), 1)
        self.assertEqual(packet["evidence"][0]["text"], "/a\tone\n/b\tvalue-that-is-long")

    def test_node_first_dialogue_preserves_edges_but_never_infers_speaker(self) -> None:
        _, units, _ = self._build()
        dialogue = [item for item in units if item["content_type"] == "dialogue_node"]
        self.assertEqual({item["nested_selector"]["node_source_id"] for item in dialogue}, {"a", "b", "shared"})
        self.assertEqual(len({item["nested_selector"]["node_source_id"] for item in dialogue}), 3)
        self.assertEqual(len({(item["nested_selector"]["node_source_id"], item["fragment_selector"].get("fragment_index", 0)) for item in dialogue}), len(dialogue))
        shared = self._unit(units, "dialogue_node", node_source_id="shared")
        self.assertIsNone(shared["structure"]["dialogue"]["speaker"])
        self.assertEqual(
            [(edge["parent_id"], edge["child_id"]) for edge in shared["structure"]["dialogue"]["observed_edges"]],
            [("a", "shared"), ("b", "shared")],
        )

    def test_known_nonindexable_shapes_are_audited_skip_even_with_diagnostic(self) -> None:
        result, _, output = self._build()
        self.assertEqual(result["accounting"]["skip_reason_counts"], {
            "known_dialogue_without_text": 1,
            "known_empty_rich_text": 1,
            "known_structured_without_scalar": 1,
            "known_unsupported_canonical_unit": 1,
        })
        with gzip.open(output / "artifacts" / "skip_ledger.jsonl.gz", "rt", encoding="utf-8") as handle:
            skips = [json.loads(line) for line in handle]
        self.assertEqual({item["reason"] for item in skips}, set(result["accounting"]["skip_reason_counts"]))

    def test_unknown_kind_and_malformed_supported_shape_fail_closed(self) -> None:
        self.record["sections"][0]["units"][0]["kind"] = "future_kind"
        self._write_record()
        self._write_manifest()
        output = self.root / "unknown-kind"
        with self.assertRaisesRegex(RetrievalUnitError, "unknown Canonical unit kind"):
            build_retrieval_units(self.manifest_path, output, config=self.config)
        failure = json.loads((output / "metadata" / "failure_ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(failure["status"], "failed")
        self.assertFalse((output / "metadata" / "manifest.json").exists())

        self.record = json.loads((Path(__file__).parents[1] / "fixtures" / "retrieval" / "canonical-rag-w1-record.json").read_text(encoding="utf-8"))
        del self.record["sections"][0]["units"][0]["value"]["normalized_text"]
        self._write_record()
        self._write_manifest()
        with self.assertRaisesRegex(RetrievalUnitError, "lacks normalized_text"):
            build_retrieval_units(self.manifest_path, self.root / "malformed-rich", config=self.config)

    def test_unsupported_schema_fails_closed(self) -> None:
        self.manifest["dependencies"]["canonical_versions"]["schema_version"] = "future-canonical-schema"
        self.manifest_path.write_bytes(canonical_json_bytes(self.manifest))
        with self.assertRaisesRegex(RetrievalUnitError, "unsupported Canonical schema"):
            build_retrieval_units(self.manifest_path, self.root / "unsupported-schema", config=self.config)

    def test_assembly_is_deterministic_merges_only_real_adjacency_and_audits_budget(self) -> None:
        _, units, output = self._build()
        first_rich = next(item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] == "ABCD")
        next_leaf = next(item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] == "next")
        other_fragment = next(item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] == "EFGH")
        structured = self._unit(units, "structured")
        config = EvidenceAssemblyConfig(neighbor_before=1, neighbor_after=1, per_block_chars=7, total_context_chars=50, max_evidence_blocks=8)
        candidates = [
            {"unit_id": first_rich["unit_id"], "rank": 2, "retrieval": {"mode": "synthetic"}},
            {"unit_id": first_rich["unit_id"], "rank": 1, "retrieval": {"mode": "synthetic-duplicate"}},
            {"unit_id": structured["unit_id"], "rank": 3, "retrieval": {"mode": "synthetic"}},
        ]
        packet_one = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config, retrieval_audit={"mode": "synthetic"})
        packet_two = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config, retrieval_audit={"mode": "synthetic"})
        self.assertEqual(evidence_packet_json_bytes(packet_one), evidence_packet_json_bytes(packet_two))
        self.assertEqual(packet_one["retrieval_audit"]["deduplicated_candidates"][0]["unit_id"], first_rich["unit_id"])
        self.assertTrue(any(item["reason"] == "per_block_char_limit" for item in packet_one["budget"]["omitted_blocks"]))
        ids = [member["unit_id"] for block in packet_one["evidence"] for member in block["members"]]
        self.assertNotIn(other_fragment["unit_id"], ids)
        self.assertNotIn(next_leaf["unit_id"], ids)
        markdown = evidence_packet_markdown(packet_one)
        self.assertIn("# Evidence Packet", markdown)
        output_packet = self.root / "packet"
        first_write = write_evidence_packet(output_packet, packet_one)
        second_write = write_evidence_packet(output_packet, packet_two)
        self.assertEqual(first_write, second_write)

    def test_prepared_context_preserves_v1_packet_bytes_and_records_diagnostics(self) -> None:
        _, units, output = self._build()
        first_rich = next(item for item in units if item["content_type"] == "rich_text")
        structured = self._unit(units, "structured")
        candidates = [
            {"unit_id": first_rich["unit_id"], "rank": 1, "retrieval": {"mode": "synthetic", "score": 1.0}},
            {"unit_id": structured["unit_id"], "rank": 2, "retrieval": {"mode": "synthetic", "score": 0.5}},
        ]
        manifest = output / "metadata" / "manifest.json"
        config = EvidenceAssemblyConfig(neighbor_before=1, neighbor_after=1, per_block_chars=100, total_context_chars=1000)
        legacy = assemble_evidence_packet(manifest, candidates, config=config)
        diagnostics = EvidenceAssemblyDiagnostics()
        context = prepare_evidence_assembly_context(manifest, diagnostics=diagnostics)
        prepared = assemble_evidence_packet(manifest, candidates, config=config, prepared_context=context, diagnostics=diagnostics)
        self.assertEqual(evidence_packet_json_bytes(legacy), evidence_packet_json_bytes(prepared))
        self.assertEqual(evidence_packet_markdown(legacy), evidence_packet_markdown(prepared))
        json_body, markdown_body = render_evidence_packet(prepared, diagnostics=diagnostics)
        self.assertEqual(json_body, evidence_packet_json_bytes(prepared))
        self.assertEqual(markdown_body.decode("utf-8"), evidence_packet_markdown(prepared))
        report = diagnostics.to_dict()
        self.assertIn("retrieval_units_decompress_and_parse", report["preparation_seconds"])
        self.assertIn("row_validation", report["preparation_seconds"])
        self.assertIn("structural_index", report["preparation_seconds"])
        self.assertIn("context_expansion", report["assembly_seconds"])
        self.assertIn("block_selection", report["assembly_seconds"])
        self.assertIn("json", report["serialization_seconds"])
        self.assertEqual([item["outcome"] for item in report["selection_trace"]["candidate_outcomes"]], ["direct_candidate", "direct_candidate"])

    def test_prepared_context_public_views_cannot_mutate_verified_snapshot(self) -> None:
        _, units, output = self._build()
        candidate = next(item for item in units if item["content_type"] == "rich_text")
        manifest = output / "metadata" / "manifest.json"
        context = prepare_evidence_assembly_context(manifest)
        candidates = [{"unit_id": candidate["unit_id"], "rank": 1, "retrieval": {"mode": "synthetic"}}]
        before = evidence_packet_json_bytes(
            assemble_evidence_packet(manifest, candidates, prepared_context=context)
        )

        self.assertFalse(hasattr(context, "units_by_id"))
        self.assertFalse(hasattr(context, "fragment_chains"))
        public_build = context.build_manifest
        with self.assertRaises(TypeError):
            public_build["build_identity"] = "tampered"
        public_build["canonical_input"]["tampered"] = True

        after = evidence_packet_json_bytes(
            assemble_evidence_packet(manifest, candidates, prepared_context=context)
        )
        self.assertEqual(before, after)
        forged = PreparedAssemblyContext(
            retrieval_unit_manifest_path=context.retrieval_unit_manifest_path,
            retrieval_unit_build_identity=context.retrieval_unit_build_identity,
        )
        with self.assertRaisesRegex(EvidenceAssemblyError, "not a verified snapshot"):
            assemble_evidence_packet(manifest, candidates, prepared_context=forged)

    def test_fragment_gap_never_merges(self) -> None:
        _, units, output = self._build()
        fragments = [
            item for item in units
            if item["content_type"] == "rich_text" and item["nested_selector"].get("kind") == "rich_text"
            and item["fragment_selector"].get("kind") == "unicode_codepoint_range"
        ]
        first = next(item for item in fragments if item["fragment_selector"]["fragment_index"] == 0)
        third = next(item for item in fragments if item["fragment_selector"]["fragment_index"] == 2)
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": first["unit_id"], "rank": 1}, {"unit_id": third["unit_id"], "rank": 2}],
            config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=1000),
        )
        self.assertEqual(len(packet["evidence"]), 2)
        self.assertEqual([item["text"] for item in packet["evidence"]], ["ABCD", "IJK"])

    def test_assembly_expands_dialogue_only_through_observed_edges_and_preserves_provenance(self) -> None:
        _, units, output = self._build()
        node_a = self._unit(units, "dialogue_node", node_source_id="a")
        node_b = self._unit(units, "dialogue_node", node_source_id="b")
        shared = self._unit(units, "dialogue_node", node_source_id="shared")
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": node_a["unit_id"], "rank": 1, "retrieval": {}}],
            config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, dialogue_hops=1, per_block_chars=100, total_context_chars=1000),
        )
        members = [member for block in packet["evidence"] for member in block["members"]]
        ids = {item["unit_id"] for item in members}
        self.assertIn(node_a["unit_id"], ids)
        self.assertIn(shared["unit_id"], ids)
        self.assertNotIn(node_b["unit_id"], ids)
        shared_member = next(item for item in members if item["unit_id"] == shared["unit_id"])
        self.assertTrue(any(reason["kind"] == "observed_dialogue_edge" for reason in shared_member["assembly_reasons"]))
        self.assertIn("raw_refs", shared_member["lineage"])
        self.assertEqual(shared_member["canonical_address"]["parsed_json_pointer"], "/modules/0/components/0/units/3")

    def test_v2_direct_block_keeps_its_bounded_structural_context(self) -> None:
        _, units, output = self._build()
        structured = [item for item in units if item["content_type"] == "structured"]
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": structured[0]["unit_id"], "rank": 1, "retrieval": {"mode": "synthetic"}}],
            config=EvidenceAssemblyConfig(
                neighbor_before=0,
                neighbor_after=0,
                structured_neighbor_before=0,
                structured_neighbor_after=1,
                per_block_chars=100,
                total_context_chars=1000,
            ),
            selection_policy=V2_DIRECT_FIRST_CONTEXT_CAP,
        )
        members = [member for block in packet["evidence"] for member in block["members"]]
        self.assertEqual([member["unit_id"] for member in members], [structured[0]["unit_id"], structured[1]["unit_id"]])
        self.assertEqual(packet["budget"]["used_direct_containing_blocks"], 1)
        self.assertEqual(packet["budget"]["used_context_only_blocks"], 0)
        self.assertNotIn("max_evidence_blocks", packet["budget"])
        self.assertNotIn("max_evidence_blocks", packet["assembly_config"])
        self.assertTrue(any(reason["kind"] == "structured_neighbor" for reason in members[1]["assembly_reasons"]))

    def test_v2_context_only_saturation_cannot_starve_direct_block(self) -> None:
        direct = {
            "members": [{"unit_id": "direct"}],
            "char_count": 4,
            "source_order": [9],
            "priority": 2,
            "direct_candidate_order": [(2, 5, "direct")],
        }
        contexts = [
            {"members": [{"unit_id": "context-a"}], "char_count": 4, "source_order": [1], "priority": None, "direct_candidate_order": []},
            {"members": [{"unit_id": "context-b"}], "char_count": 4, "source_order": [2], "priority": None, "direct_candidate_order": []},
        ]
        selected, omitted, accounting = evidence_assembly_module._select_blocks_v2(
            [*contexts, direct],
            EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, total_context_chars=100, per_block_chars=100),
            EvidenceSelectionPolicy(V2_DIRECT_FIRST_CONTEXT_CAP_POLICY, context_only_block_cap=1),
        )
        self.assertIn("direct", [member["unit_id"] for block in selected for member in block["members"]])
        self.assertEqual(accounting["selected_direct_containing_blocks"], 1)
        self.assertEqual(accounting["selected_context_only_blocks"], 1)
        self.assertEqual(omitted, [{"unit_ids": ["context-b"], "reason": "context_only_block_cap", "char_count": 4, "block_kind": "context_only"}])
        self.assertEqual(accounting["admission_order"][0]["unit_ids"], ["direct"])

    def test_v2_direct_total_char_conflict_is_deterministic_and_auditable(self) -> None:
        _, units, output = self._build()
        first_rich = next(item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] == "ABCD")
        structured = self._unit(units, "structured")
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=4)
        candidates = [
            {"unit_id": first_rich["unit_id"], "rank": 1, "retrieval": {"mode": "synthetic"}},
            {"unit_id": structured["unit_id"], "rank": 2, "retrieval": {"mode": "synthetic"}},
        ]
        diagnostics = EvidenceAssemblyDiagnostics()
        first = assemble_evidence_packet(
            output / "metadata" / "manifest.json", candidates, config=config,
            selection_policy=V2_DIRECT_FIRST_CONTEXT_CAP, diagnostics=diagnostics,
        )
        second = assemble_evidence_packet(
            output / "metadata" / "manifest.json", candidates, config=config,
            selection_policy=V2_DIRECT_FIRST_CONTEXT_CAP,
        )
        self.assertEqual(evidence_packet_json_bytes(first), evidence_packet_json_bytes(second))
        self.assertTrue(any(
            item["reason"] == "direct_total_context_char_budget_conflict" and structured["unit_id"] in item["unit_ids"]
            for item in first["budget"]["omitted_blocks"]
        ))
        self.assertEqual(diagnostics.selection_trace["admission_order"][0]["outcome"], "admitted")
        self.assertEqual(diagnostics.selection_trace["admission_order"][1]["reason"], "direct_total_context_char_budget_conflict")

    def test_v2_identical_text_occurrences_remain_distinct(self) -> None:
        self.record["sections"].append(json.loads(json.dumps(self.record["sections"][0])))
        second = self.record["sections"][1]
        second["ordinal"] = 1
        second["component_contexts"][0]["ordinal"] = 0
        second["component_contexts"][0]["observation_key"] = "content:fixture-rag-1:component:fixture:ordinal:other"
        for unit in second["units"]:
            unit["parent_component_key"] = second["component_contexts"][0]["observation_key"]
        second["units"] = [second["units"][0]]
        second["component_contexts"][0]["child_unit_ordinals"] = [0]
        second["component_contexts"][0]["unit_count"] = 1
        self._write_record()
        self._write_manifest()
        _, units, output = self._build()
        same_text = [item for item in units if item["retrieval_visible_text"] == "ABCD"]
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": item["unit_id"], "rank": index + 1, "retrieval": {}} for index, item in enumerate(same_text)],
            config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=1000),
            selection_policy=V2_DIRECT_FIRST_CONTEXT_CAP,
        )
        self.assertEqual(len(packet["evidence"]), 2)
        self.assertEqual({member["unit_id"] for block in packet["evidence"] for member in block["members"]}, {item["unit_id"] for item in same_text})

    def test_candidate_anchored_shadow_consolidates_only_same_unit_id_and_keeps_observations(self) -> None:
        _, units, output = self._build()
        rich = next(item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] == "ABCD")
        structured = self._unit(units, "structured")
        candidates = [
            {"unit_id": rich["unit_id"], "rank": 2, "retrieval": {"mode": "synthetic", "observation": "later"}},
            {"unit_id": rich["unit_id"], "rank": 1, "retrieval": {"mode": "synthetic", "observation": "winner"}},
            {"unit_id": structured["unit_id"], "rank": 3, "retrieval": {"mode": "synthetic"}},
        ]
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=1000)
        diagnostics = EvidenceAssemblyDiagnostics()
        packet = assemble_candidate_anchored_shadow_packet(
            output / "metadata" / "manifest.json", candidates, config=config, diagnostics=diagnostics,
        )
        repeated_diagnostics = EvidenceAssemblyDiagnostics()
        repeated = assemble_candidate_anchored_shadow_packet(
            output / "metadata" / "manifest.json", candidates, config=config, diagnostics=repeated_diagnostics,
        )
        self.assertEqual(packet["assembly_version"], CANDIDATE_ANCHORED_SHADOW_POLICY)
        self.assertEqual(evidence_packet_json_bytes(packet), evidence_packet_json_bytes(repeated))
        self.assertEqual(diagnostics.selection_trace, repeated_diagnostics.selection_trace)
        self.assertEqual(packet["retrieval_audit"]["direct_candidate_count"], 2)
        self.assertEqual(packet["shadow_contract"]["candidate_observations"][0]["candidate"]["retrieval"]["observation"], "later")
        self.assertEqual(len(diagnostics.selection_trace["candidate_outcomes"]), 3)
        self.assertEqual(
            [item["outcome"] for item in diagnostics.selection_trace["candidate_outcomes"]],
            ["duplicate_source_occurrence", "direct_candidate", "direct_candidate"],
        )
        direct_roots = [item["anchor_unit_id"] for item in packet["shadow_contract"]["direct_footprints"]]
        self.assertEqual(direct_roots, [rich["unit_id"], structured["unit_id"]])

    def test_candidate_anchored_shadow_lower_priority_append_cannot_mutate_existing_footprint(self) -> None:
        _, units, output = self._build()
        rich = [item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] in {"ABCD", "EFGH"}]
        first, second = rich
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=1, per_block_chars=100, total_context_chars=1000)
        baseline = assemble_candidate_anchored_shadow_packet(
            output / "metadata" / "manifest.json", [{"unit_id": first["unit_id"], "rank": 1, "retrieval": {}}], config=config,
        )
        expanded = assemble_candidate_anchored_shadow_packet(
            output / "metadata" / "manifest.json",
            [
                {"unit_id": first["unit_id"], "rank": 1, "retrieval": {}},
                {"unit_id": second["unit_id"], "rank": 2, "retrieval": {}},
            ],
            config=config,
        )
        before = baseline["shadow_contract"]["direct_footprints"][0]
        after = next(item for item in expanded["shadow_contract"]["direct_footprints"] if item["anchor_unit_id"] == first["unit_id"])
        self.assertEqual(before["immutable_direct_footprint"], after["immutable_direct_footprint"])
        self.assertEqual(before["marginal_direct_chars"], after["marginal_direct_chars"])
        self.assertEqual(before["budget_after"], after["budget_after"])
        self.assertEqual(baseline["evidence"][0]["text"], expanded["evidence"][0]["text"])
        second_outcome = next(item for item in expanded["shadow_contract"]["direct_footprints"] if item["anchor_unit_id"] == second["unit_id"])
        self.assertTrue(second_outcome["root_visible_before_admission"])

    def test_candidate_anchored_shadow_records_higher_priority_budget_displacement(self) -> None:
        _, units, output = self._build()
        rich = next(item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] == "ABCD")
        lower = next(item for item in units if item["content_type"] == "rich_text" and item["retrieval_visible_text"] == "next")
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=4)
        baseline = assemble_candidate_anchored_shadow_packet(
            output / "metadata" / "manifest.json", [{"unit_id": lower["unit_id"], "rank": 2, "retrieval": {}}], config=config,
        )
        self.assertEqual(baseline["shadow_contract"]["direct_footprints"][0]["outcome"], "admitted")
        challenged = assemble_candidate_anchored_shadow_packet(
            output / "metadata" / "manifest.json",
            [
                {"unit_id": rich["unit_id"], "rank": 1, "retrieval": {}},
                {"unit_id": lower["unit_id"], "rank": 2, "retrieval": {}},
            ],
            config=config,
        )
        displaced = next(item for item in challenged["shadow_contract"]["direct_footprints"] if item["anchor_unit_id"] == lower["unit_id"])
        self.assertEqual(displaced["reason"], "direct_total_context_char_budget_conflict")
        self.assertEqual(displaced["displaced_by_anchor_ids"], [rich["unit_id"]])
        self.assertEqual(displaced["budget_before"], 4)
        self.assertEqual(displaced["budget_after"], 4)

    def test_candidate_anchored_shadow_context_membership_is_single_rendered_and_auditable(self) -> None:
        _, units, output = self._build()
        node_a = self._unit(units, "dialogue_node", node_source_id="a")
        packet = assemble_candidate_anchored_shadow_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": node_a["unit_id"], "rank": 1, "retrieval": {}}],
            config=EvidenceAssemblyConfig(
                neighbor_before=0,
                neighbor_after=0,
                dialogue_hops=1,
                per_block_chars=100,
                total_context_chars=1000,
            ),
        )
        occurrences = {item["unit_id"]: item for item in packet["shadow_contract"]["context_occurrences"]}
        context_rows = [item for item in occurrences.values() if any(
            membership["membership_kind"] == "context" for membership in item["memberships"]
        )]
        self.assertTrue(context_rows)
        for row in context_rows:
            self.assertEqual(row["presentation_owner_anchor_unit_id"], node_a["unit_id"])
            self.assertEqual(row["rendering"]["phase"], "context")
            self.assertEqual(row["rendering"]["anchor_unit_id"], node_a["unit_id"])
        rendered_ids = [member["unit_id"] for block in packet["evidence"] for member in block["members"]]
        self.assertEqual(len(rendered_ids), len(set(rendered_ids)))

    def test_a1_2_2_suppresses_only_proved_dialogue_source_occurrence_alias(self) -> None:
        units, output, rich, dialogue = self._dialogue_alias_units()
        candidates = [
            {"unit_id": dialogue["unit_id"], "rank": 2, "retrieval": {"mode": "synthetic"}},
            {"unit_id": rich["unit_id"], "rank": 1, "retrieval": {"mode": "synthetic"}},
        ]
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, dialogue_hops=0, per_block_chars=1000, total_context_chars=1000)
        diagnostics = EvidenceAssemblyDiagnostics()
        packet = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config, selection_policy=A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION, diagnostics=diagnostics)
        repeated_diagnostics = EvidenceAssemblyDiagnostics()
        repeated = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config, selection_policy=A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION, diagnostics=repeated_diagnostics)
        members = [member for block in packet["evidence"] for member in block["members"]]
        self.assertEqual([member["unit_id"] for member in members], [rich["unit_id"]])
        self.assertEqual(packet["selection_policy"]["identity"], A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY)
        self.assertEqual(packet["budget"]["used_context_chars"], len(rich["retrieval_visible_text"]))
        self.assertEqual(members[0]["lineage"], rich["source"]["lineage"])
        self.assertEqual(members[0]["canonical_address"], rich["source"]["canonical_address"])
        trace = diagnostics.selection_trace["suppressed_aliases"]
        self.assertEqual(trace[0]["suppressed_unit_id"], dialogue["unit_id"])
        self.assertEqual(trace[0]["representative_unit_id"], rich["unit_id"])
        self.assertEqual(trace[0]["proof"]["node_source_id"], "a")
        self.assertEqual(diagnostics.selection_trace["candidate_counts"], {
            "input": 2,
            "direct": 2,
            "context": 0,
            "post_suppression_direct": 1,
            "suppressed_alias_count": 1,
        })
        self.assertEqual(evidence_packet_json_bytes(packet), evidence_packet_json_bytes(repeated))
        self.assertEqual(diagnostics.selection_trace, repeated_diagnostics.selection_trace)
        self.assertEqual({unit["unit_id"] for unit in units if unit["unit_id"] in {rich["unit_id"], dialogue["unit_id"]}}, {rich["unit_id"], dialogue["unit_id"]})

    def test_a1_2_2_alias_proof_fails_closed_for_non_alias_variants(self) -> None:
        _, _, rich, dialogue = self._dialogue_alias_units()
        cases = []
        changed_node = json.loads(json.dumps(dialogue))
        changed_node["nested_selector"]["node_source_id"] = "b"
        cases.append(changed_node)
        changed_artifact = json.loads(json.dumps(dialogue))
        changed_artifact["structure"]["dialogue"]["node_raw_ref"]["artifact_sha256"] = "b" * 64
        cases.append(changed_artifact)
        changed_component = json.loads(json.dumps(dialogue))
        changed_component["structure"]["dialogue"]["node_raw_ref"]["json_pointer"] = "/data/page/modules/other/components/0"
        cases.append(changed_component)
        same_scope_only = json.loads(json.dumps(dialogue))
        same_scope_only["structure"]["dialogue"]["node_raw_ref"]["embedded_json_pointer"] = "/contents/b"
        cases.append(same_scope_only)
        fragmented = json.loads(json.dumps(dialogue))
        fragmented["fragment_selector"] = {"kind": "unicode_codepoint_range", "fragment_index": 0, "fragment_count": 2}
        cases.append(fragmented)
        fragmented_rich = json.loads(json.dumps(rich))
        fragmented_rich["fragment_selector"] = {"kind": "unicode_codepoint_range", "fragment_index": 0, "fragment_count": 2}
        self.assertIsNone(evidence_assembly_module._dialogue_source_occurrence_alias_proof(fragmented_rich, dialogue))
        missing_proof = json.loads(json.dumps(dialogue))
        missing_proof["structure"]["dialogue"]["node_raw_ref"] = None
        cases.append(missing_proof)
        for candidate in cases:
            self.assertIsNone(evidence_assembly_module._dialogue_source_occurrence_alias_proof(rich, candidate))
        contained = json.loads(json.dumps(dialogue))
        contained["retrieval_visible_text"] = "Alpha"
        self.assertIsNone(evidence_assembly_module._dialogue_source_occurrence_alias_proof(rich, contained))
        changed_value_sha = json.loads(json.dumps(dialogue))
        changed_value_sha["structure"]["dialogue"]["node_raw_ref"]["source_value_sha256"] = "c" * 64
        self.assertIsNone(evidence_assembly_module._dialogue_source_occurrence_alias_proof(rich, changed_value_sha))
        malformed_pointer = json.loads(json.dumps(dialogue))
        malformed_pointer["structure"]["dialogue"]["node_raw_ref"]["embedded_json_pointer"] = "/contents/a~2"
        malformed_pointer["structure"]["dialogue"]["node_raw_ref"]["node_source_id"] = "a"
        malformed_rich = json.loads(json.dumps(rich))
        malformed_rich["source"]["lineage"]["raw_refs"][0]["embedded_json_pointer"] = "/contents/a~2/dialogue"
        self.assertIsNone(evidence_assembly_module._dialogue_source_occurrence_alias_proof(malformed_rich, malformed_pointer))
        self.assertIsNone(evidence_assembly_module._dialogue_source_occurrence_alias_proof(rich, rich))
        self.assertIsNone(evidence_assembly_module._dialogue_source_occurrence_alias_proof(dialogue, dialogue))

    def test_a1_2_2_ambiguous_alias_group_remains_unsuppressed(self) -> None:
        _, _, rich, dialogue = self._dialogue_alias_units()
        second_dialogue = json.loads(json.dumps(dialogue))
        second_dialogue["unit_id"] = "f" * 64
        selection = {
            rich["unit_id"]: {"retrieval": {"rank": 1, "input_index": 0}, "reasons": []},
            dialogue["unit_id"]: {"retrieval": {"rank": 2, "input_index": 1}, "reasons": []},
            second_dialogue["unit_id"]: {"retrieval": {"rank": 3, "input_index": 2}, "reasons": []},
        }
        units_by_id = {rich["unit_id"]: rich, dialogue["unit_id"]: dialogue, second_dialogue["unit_id"]: second_dialogue}
        self.assertEqual(evidence_assembly_module._suppress_dialogue_source_occurrence_aliases(selection, units_by_id), [])
        self.assertEqual(set(selection), set(units_by_id))

    def test_a1_2_2_suppression_occurs_after_expansion_and_preserves_controls(self) -> None:
        _, output, rich, dialogue = self._dialogue_alias_units()
        config = EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=1, dialogue_hops=1, per_block_chars=1000, total_context_chars=1000)
        candidates = [
            {"unit_id": rich["unit_id"], "rank": 1, "retrieval": {}},
            {"unit_id": dialogue["unit_id"], "rank": 2, "retrieval": {}},
        ]
        challenger_trace = EvidenceAssemblyDiagnostics()
        challenger = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config, selection_policy=A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION, diagnostics=challenger_trace)
        v2_before = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config, selection_policy=V2_DIRECT_FIRST_CONTEXT_CAP)
        v2_after = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config, selection_policy=V2_DIRECT_FIRST_CONTEXT_CAP)
        v1_before = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config)
        v1_after = assemble_evidence_packet(output / "metadata" / "manifest.json", candidates, config=config)
        challenger_ids = {member["unit_id"] for block in challenger["evidence"] for member in block["members"]}
        self.assertIn(rich["unit_id"], challenger_ids)
        self.assertNotIn(dialogue["unit_id"], challenger_ids)
        retained_reasons = [reason["kind"] for block in challenger["evidence"] for member in block["members"] for reason in member["assembly_reasons"]]
        self.assertIn("ordinal_neighbor", retained_reasons)
        self.assertIn("observed_dialogue_edge", retained_reasons)
        self.assertEqual(evidence_packet_json_bytes(v2_before), evidence_packet_json_bytes(v2_after))
        self.assertEqual(evidence_packet_json_bytes(v1_before), evidence_packet_json_bytes(v1_after))
        self.assertEqual(challenger_trace.selection_trace["candidate_counts"]["direct"], 2)

    def test_identical_text_different_occurrences_remains_distinct_and_no_cross_context_merge(self) -> None:
        self.record["sections"].append(json.loads(json.dumps(self.record["sections"][0])))
        second = self.record["sections"][1]
        second["ordinal"] = 1
        second["component_contexts"][0]["ordinal"] = 0
        second["component_contexts"][0]["observation_key"] = "content:fixture-rag-1:component:fixture:ordinal:other"
        for unit in second["units"]:
            unit["parent_component_key"] = second["component_contexts"][0]["observation_key"]
        second["units"] = [second["units"][0]]
        second["component_contexts"][0]["child_unit_ordinals"] = [0]
        second["component_contexts"][0]["unit_count"] = 1
        self._write_record()
        self._write_manifest()
        _, units, output = self._build()
        same_text = [item for item in units if item["retrieval_visible_text"] == "ABCD"]
        self.assertEqual(len(same_text), 2)
        self.assertNotEqual(same_text[0]["unit_id"], same_text[1]["unit_id"])
        packet = assemble_evidence_packet(
            output / "metadata" / "manifest.json",
            [{"unit_id": item["unit_id"], "rank": index + 1, "retrieval": {}} for index, item in enumerate(same_text)],
            config=EvidenceAssemblyConfig(neighbor_before=0, neighbor_after=0, per_block_chars=100, total_context_chars=1000),
        )
        self.assertEqual(len(packet["evidence"]), 2)

    def test_rejects_unknown_candidate(self) -> None:
        _, _, output = self._build()
        with self.assertRaises(EvidenceAssemblyError):
            assemble_evidence_packet(output / "metadata" / "manifest.json", [{"unit_id": "not-real", "rank": 1}])

    def test_loader_rejects_tampered_skip_ledger(self) -> None:
        _, _, output = self._build()
        skip_path = output / "artifacts" / "skip_ledger.jsonl.gz"
        skip_path.write_bytes(b"tampered")
        with self.assertRaisesRegex(RetrievalUnitError, "skip_ledger artifact SHA-256 mismatch"):
            load_retrieval_units(output / "metadata" / "manifest.json")


if __name__ == "__main__":
    unittest.main()
