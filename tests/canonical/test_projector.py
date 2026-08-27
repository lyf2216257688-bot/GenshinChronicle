from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from genshin_corpus.canonical import (
    CanonicalObservation,
    CanonicalVersions,
    LineageLink,
    PROJECTOR_POLICY_VERSION,
    project_parsed_detail,
    project_parsed_input,
    serialize_canonical_record,
)
from genshin_corpus.parser.contracts import Classification, ContractMetadata, Diagnostic, ParsedIdentity, RawRef, SourcePosition
from genshin_corpus.parser.fingerprints import parsed_fingerprint, source_fingerprint
from genshin_corpus.parser.identity import component_identity, content_unit_identity, detail_identity, module_identity
from genshin_corpus.parser.models import ParsedComponent, ParsedContentUnit, ParsedDetail, ParsedModule, ParsedUnknown
from genshin_corpus.parser.obc.adapter import parse_obc_detail


class ProjectorTests(unittest.TestCase):
    raw_ref = RawRef(
        source="mihoyo_obc",
        locale="zh-cn",
        run_id="raw-fixture",
        artifact_kind="details",
        artifact_path="responses/details/fixture.json",
        artifact_sha256="a" * 64,
        content_id="fixture-1",
    )

    def _observation(self, status: str = "parsed") -> CanonicalObservation:
        return CanonicalObservation(
            parsed_run_id="parsed-fixture",
            parsed_manifest_path="runs/parsed-fixture/metadata/manifest.json",
            parsed_manifest_sha256="b" * 64,
            parsed_record_path="runs/parsed-fixture/records/fixture.json",
            parsed_record_sha256="c" * 64,
            parsed_schema_version="phase02-draft-0.1",
            parsed_parser_version="obc-foundation-0.2",
            parsed_pipeline_version="obc-parsed-run-0.2",
            parsed_status=status,
            parsed_semantic_fingerprint="d" * 64,
        )

    def _parsed_fixture(self) -> ParsedDetail:
        path = Path(__file__).parents[1] / "fixtures" / "parsed" / "obc-detail.json"
        body = path.read_bytes()
        self.fixture_ref = RawRef(**{**self.raw_ref.to_dict(), "artifact_sha256": hashlib.sha256(body).hexdigest()})
        return parse_obc_detail(body, raw_ref=self.fixture_ref, content_id="fixture-1", channel_memberships=("43", "99"))

    def _dialogue_fixture(self) -> ParsedDetail:
        dialogue = {
            "root_id": "missing-root",
            "child_ids": {
                "root": ["left", "right"],
                "left": ["shared"],
                "right": ["shared"],
                "missing-parent": ["root"],
            },
            "contents": {
                "root": {"option": "<em>开始</em>", "dialogue": "<p>阿罗夏：你好</p>", "icon": "i", "extra": {"x": 1}},
                "left": {"option": "左", "dialogue": "左文本", "icon": ""},
                "right": {"option": "右", "dialogue": "右文本", "icon": ""},
                "shared": {"option": "共同", "dialogue": "共同文本", "icon": ""},
                "orphan": {"option": "孤立", "dialogue": "孤立文本", "icon": ""},
            },
        }
        payload = {
            "data": {
                "page": {
                    "id": "dialogue-fixture",
                    "name": "对话样例",
                    "modules": [{
                        "id": "dialogue-module",
                        "components": [{
                            "component_id": "interactive_dialogue",
                            "data": json.dumps(dialogue, ensure_ascii=False),
                        }],
                    }],
                },
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ref = RawRef(
            source="mihoyo_obc",
            locale="zh-cn",
            run_id="dialogue-fixture-run",
            artifact_kind="details",
            artifact_path="responses/details/dialogue-fixture.json",
            artifact_sha256=hashlib.sha256(body).hexdigest(),
            content_id="dialogue-fixture",
        )
        return parse_obc_detail(body, raw_ref=ref, content_id="dialogue-fixture")

    def _non_object_dialogue_fixture(self) -> ParsedDetail:
        payload = {
            "data": {
                "page": {
                    "id": "dialogue-non-object-fixture",
                    "modules": [{
                        "id": "dialogue-module",
                        "components": [{
                            "component_id": "interactive_dialogue",
                            "data": json.dumps(["not", "an", "object"], ensure_ascii=False),
                        }],
                    }],
                },
            },
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ref = RawRef(
            source="mihoyo_obc",
            locale="zh-cn",
            run_id="dialogue-fixture-run",
            artifact_kind="details",
            artifact_path="responses/details/dialogue-non-object-fixture.json",
            artifact_sha256=hashlib.sha256(body).hexdigest(),
            content_id="dialogue-non-object-fixture",
        )
        return parse_obc_detail(body, raw_ref=ref, content_id="dialogue-non-object-fixture")

    def _blocked(self, *, with_raw_ref: bool) -> dict:
        raw_refs = [] if not with_raw_ref else [self.raw_ref.to_dict()]
        return {
            "identity": {
                "kind": "detail_observation",
                "key": "blocked:fixture-1:dependency-fixture",
                "stability": "snapshot_only",
                "components": {"content_id": "fixture-1", "dependency": "dependency-fixture"},
            },
            "metadata": {
                "parse_status": "blocked_integrity",
                "raw_refs": raw_refs,
                "parsed_fingerprint": "d" * 64,
                "diagnostics": [],
            },
            "content_id": "fixture-1",
            "channels": ["43", "99"],
            "error": "RAW_METADATA_INTEGRITY_MISMATCH",
        }

    def _unsupported_module_detail(self) -> ParsedDetail:
        detail = self._parsed_fixture()
        unknown = ParsedUnknown.from_value(
            identity=ParsedIdentity(
                kind="module",
                key="content:fixture-1:module:unknown:1",
                stability="snapshot_only",
                components=(("content_id", "fixture-1"), ("array_index", "1")),
            ),
            raw_value=["unsupported-module"],
            raw_refs=(self.fixture_ref,),
            source_position=SourcePosition("/data/page/modules/42", array_index=42, ordering=42),
            reason="module is not an object",
            context={"module_index": 1},
            diagnostics=(Diagnostic("NON_OBJECT_MODULE", "non-object module retained as unsupported", "error"),),
        )
        return ParsedDetail(
            identity=detail.identity,
            metadata=replace_metadata(detail.metadata, parse_status="parsed_with_anomalies"),
            source=detail.source,
            locale=detail.locale,
            run_id=detail.run_id,
            content_id=detail.content_id,
            page_id=detail.page_id,
            name=detail.name,
            page_type=detail.page_type,
            source_metadata=detail.source_metadata,
            source_template_layout=detail.source_template_layout,
            modules=detail.modules,
            unsupported_modules=(unknown,),
            channel_memberships=detail.channel_memberships,
        )

    def test_normal_fixture_projects_ordered_module_component_units_and_lineage(self) -> None:
        detail = self._parsed_fixture()
        record = project_parsed_detail(detail, observation=self._observation())

        self.assertEqual(record.status, "canonical")
        self.assertEqual(record.lineage.parsed_json_pointer, "")
        self.assertEqual(record.record_metadata["channel_memberships"], ["43", "99"])
        self.assertEqual(record.metadata_lineage[0][0], "channel_memberships")
        self.assertEqual(record.metadata_lineage[0][1].evidence_scope, "parsed_dependency")
        self.assertEqual(record.metadata_lineage[0][1].raw_refs, ())
        self.assertEqual(record.sections[0].ordinal, 0)
        self.assertEqual(record.sections[0].source_metadata["module_index"], 0)
        self.assertEqual(record.sections[0].lineage.parsed_json_pointer, "/modules/0")
        self.assertEqual(record.sections[0].lineage.evidence_scope, "direct_raw")
        self.assertEqual(record.sections[0].lineage.raw_refs[0].json_pointer, "/data/page/modules/0")
        context = record.sections[0].component_contexts[0]
        self.assertEqual(context.observation_key, detail.modules[0].components[0].identity.key)
        self.assertEqual(context.ordinal, 0)
        self.assertEqual(context.source_position, detail.modules[0].components[0].metadata.source_position)
        self.assertEqual(
            context.to_dict()["source_position"],
            detail.modules[0].components[0].metadata.source_position.to_dict(),
        )
        self.assertEqual(context.unit_count, len(record.sections[0].units))
        self.assertEqual([unit.ordinal for unit in record.sections[0].units], list(range(context.unit_count)))
        self.assertEqual(record.sections[0].units[0].lineage.parsed_json_pointer, "/modules/0/components/0/units/0")
        self.assertEqual(record.sections[0].units[0].lineage.evidence_scope, "inherited_parent_raw")
        self.assertEqual(record.sections[0].units[0].lineage.raw_refs[0].artifact_sha256, self.fixture_ref.artifact_sha256)
        self.assertEqual(dict(record.metadata_lineage)["source_metadata"].parsed_json_pointer, "/source_metadata")
        self.assertEqual(dict(record.metadata_lineage)["source_template_layout"].parsed_json_pointer, "/source_template_layout")
        self.assertEqual(dict(record.metadata_lineage)["parsed_metadata"].parsed_json_pointer, "/metadata")

    def test_projector_policy_is_explicit_and_output_is_deterministic(self) -> None:
        detail = self._parsed_fixture()
        first = project_parsed_detail(detail, observation=self._observation())
        second = project_parsed_detail(detail, observation=self._observation())

        self.assertEqual(first.versions.transform_version, PROJECTOR_POLICY_VERSION)
        self.assertEqual(serialize_canonical_record(first), serialize_canonical_record(second))
        self.assertEqual(first.parsed_identity, detail.identity)
        self.assertEqual(first.source_identity, detail.identity)

    def test_batch3_preserves_generic_rich_text_and_dialogue_graph_relationships(self) -> None:
        detail = self._dialogue_fixture()
        record = project_parsed_detail(detail, observation=self._observation(detail.metadata.parse_status))
        section = record.sections[0]
        context = section.component_contexts[0]
        units = section.units
        generic = next(unit for unit in units if unit.kind == "structured_observation")
        rich_text_units = tuple(unit for unit in units if unit.kind == "rich_text")
        dialogue = next(unit for unit in units if unit.kind == "dialogue_graph")

        self.assertEqual(record.status, "canonical")
        self.assertEqual(generic.value, detail.modules[0].components[0].units[0].value)
        self.assertEqual(generic.value["decoded"]["root_id"], "missing-root")
        self.assertTrue(rich_text_units)
        self.assertEqual(rich_text_units[0].value["raw_markup"], "<em>开始</em>")
        self.assertEqual(rich_text_units[0].value["normalized_text"], "开始")
        self.assertTrue(all(unit.parent_component_key == context.observation_key for unit in units))
        self.assertEqual(tuple(unit.ordinal for unit in units), context.child_unit_ordinals)

        graph = dialogue.value
        self.assertEqual([node.source_id for node in graph.groups[0].nodes], ["root", "left", "right", "shared", "orphan"])
        self.assertEqual(
            [(edge.parent_id, edge.child_id) for edge in graph.groups[0].edges],
            [("root", "left"), ("root", "right"), ("left", "shared"), ("right", "shared"), ("missing-parent", "root")],
        )
        self.assertIsNone(graph.groups[0].nodes[0].speaker)
        self.assertEqual(graph.groups[0].nodes[0].raw_fields, {"extra": {"x": 1}})
        self.assertEqual(graph.groups[0].nodes[0].raw_ref.embedded_json_pointer, "/contents/root")
        self.assertEqual(graph.groups[0].edges[1].raw_ref.embedded_json_pointer, "/child_ids/root/1")
        codes = {diagnostic.code for diagnostic in dialogue.diagnostics}
        self.assertTrue({"DIALOGUE_MULTIPLE_PARENT", "DIALOGUE_ORPHAN_NODE", "DIALOGUE_PARENT_MISSING", "DIALOGUE_ROOT_NOT_FOUND"} <= codes)
        self.assertEqual(
            {diagnostic.code for diagnostic in context.diagnostics},
            codes,
        )
        self.assertEqual(dialogue.lineage.raw_refs, detail.modules[0].components[0].units[-1].metadata.raw_refs)

        serialized = dialogue.to_dict()["value"]
        self.assertEqual(serialized["kind"], "dialogue_graph")
        self.assertEqual(serialized["groups"][0]["nodes"][0]["dialogue_rich_text"]["raw_markup"], "<p>阿罗夏：你好</p>")
        self.assertEqual(serialize_canonical_record(record), serialize_canonical_record(record))

    def test_batch3_preserves_non_object_dialogue_anomaly_without_blocking_projection(self) -> None:
        detail = self._non_object_dialogue_fixture()
        record = project_parsed_detail(detail, observation=self._observation(detail.metadata.parse_status))
        context = record.sections[0].component_contexts[0]
        generic, graph_unit = record.sections[0].units

        self.assertEqual(record.status, "canonical")
        self.assertEqual(generic.kind, "structured_observation")
        self.assertEqual(generic.value["decoded"], ["not", "an", "object"])
        self.assertEqual(graph_unit.kind, "dialogue_graph")
        self.assertEqual(graph_unit.value.groups, ())
        self.assertEqual({diagnostic.code for diagnostic in graph_unit.diagnostics}, {"DIALOGUE_DATA_NOT_OBJECT"})
        self.assertEqual(context.diagnostics, graph_unit.diagnostics)
        self.assertEqual(graph_unit.lineage.raw_refs, detail.modules[0].components[0].units[1].metadata.raw_refs)
        self.assertEqual(graph_unit.lineage.raw_refs[0].json_pointer, "/data/page/modules/0/components/0")

    def test_zero_unit_component_is_accounted_without_artificial_unit(self) -> None:
        detail = self._parsed_fixture()
        original = detail.modules[0].components[0]
        empty_component = ParsedComponent(
            identity=original.identity,
            metadata=replace_metadata(original.metadata, parsed_fingerprint="e" * 64),
            source_component_id=original.source_component_id,
            source_data_encoding=original.source_data_encoding,
            source_layout=original.source_layout,
            source_style=original.source_style,
            units=(),
            unsupported=(),
        )
        module = ParsedModule(
            identity=detail.modules[0].identity,
            metadata=detail.modules[0].metadata,
            source_module_id=detail.modules[0].source_module_id,
            module_index=0,
            name=detail.modules[0].name,
            repeated=detail.modules[0].repeated,
            is_submodule=detail.modules[0].is_submodule,
            origin_module_id=detail.modules[0].origin_module_id,
            components=(empty_component,),
        )
        empty_detail = replace_detail(detail, modules=(module,))
        record = project_parsed_detail(empty_detail, observation=self._observation())

        self.assertEqual(record.sections[0].component_contexts[0].unit_count, 0)
        self.assertEqual(record.sections[0].units, ())
        context = record.sections[0].component_contexts[0]
        self.assertEqual(context.source_position, empty_component.metadata.source_position)
        self.assertEqual(context.to_dict()["source_position"], empty_component.metadata.source_position.to_dict())
        self.assertEqual(serialize_canonical_record(record), serialize_canonical_record(record))

    def test_unsupported_module_is_structurally_accounted_and_keeps_order(self) -> None:
        detail = self._unsupported_module_detail()
        record = project_parsed_detail(detail, observation=self._observation("parsed_with_anomalies"))

        self.assertEqual(record.status, "canonical_with_anomalies")
        self.assertEqual(len(record.sections), 2)
        self.assertEqual(record.sections[0].source_metadata["module_index"], 0)
        self.assertIn("unsupported", record.sections[1].source_metadata)
        self.assertEqual(record.sections[1].lineage.parsed_json_pointer, "/unsupported_modules/0")
        self.assertEqual(record.sections[1].source_metadata["source_position"]["array_index"], 42)
        self.assertEqual(record.sections[1].lineage.raw_refs[0].artifact_sha256, self.fixture_ref.artifact_sha256)

    def test_record_metadata_preserves_detail_classification_and_diagnostics(self) -> None:
        detail = self._parsed_fixture()
        detail = replace_detail(
            detail,
            metadata=replace_metadata(
                detail.metadata,
                provenance=Classification(state="classified", labels=("source_page",), basis=("fixture",)),
                content_role=Classification(state="classified", labels=("narrative",), basis=("fixture",)),
                diagnostics=(Diagnostic("DETAIL_NOTE", "detail-level diagnostic is retained"),),
            ),
        )

        record = project_parsed_detail(detail, observation=self._observation())
        parsed_metadata = record.record_metadata["parsed_metadata"]

        self.assertEqual(parsed_metadata["provenance"], detail.metadata.provenance.to_dict())
        self.assertEqual(parsed_metadata["content_role"], detail.metadata.content_role.to_dict())
        self.assertEqual(parsed_metadata["diagnostics"], [item.to_dict() for item in detail.metadata.diagnostics])
        self.assertEqual(parsed_metadata["source_position"], detail.metadata.source_position.to_dict())
        self.assertEqual(parsed_metadata["parsed_fingerprint"], detail.metadata.parsed_fingerprint)
        self.assertEqual(dict(record.metadata_lineage)["parsed_metadata"].evidence_scope, "inherited_parent_raw")

    def test_section_metadata_preserves_module_diagnostics_and_classification(self) -> None:
        detail = self._parsed_fixture()
        module = replace_module(
            detail.modules[0],
            metadata=replace_metadata(
                detail.modules[0].metadata,
                parse_status="parsed_with_anomalies",
                provenance=Classification(state="classified", labels=("module_observation",), basis=("fixture",)),
                diagnostics=(Diagnostic("MODULE_NOTE", "module-level diagnostic is retained"),),
            ),
        )
        detail = replace_detail(
            detail,
            metadata=replace_metadata(detail.metadata, parse_status="parsed_with_anomalies"),
            modules=(module,),
        )

        record = project_parsed_detail(detail, observation=self._observation("parsed_with_anomalies"))
        parsed_metadata = record.sections[0].source_metadata["parsed_metadata"]

        self.assertEqual(parsed_metadata["parse_status"], "parsed_with_anomalies")
        self.assertEqual(parsed_metadata["provenance"], module.metadata.provenance.to_dict())
        self.assertEqual(parsed_metadata["diagnostics"], [item.to_dict() for item in module.metadata.diagnostics])
        self.assertEqual(parsed_metadata["parsed_fingerprint"], module.metadata.parsed_fingerprint)

    def test_parsed_pointers_use_serialized_collection_indices_not_source_positions(self) -> None:
        detail = self._parsed_fixture()
        component_key = component_identity("fixture-1", "fixture_component", 0).key
        unit = ParsedContentUnit(
            identity=content_unit_identity(component_key, "data", 0),
            metadata=ContractMetadata(
                raw_refs=(self.fixture_ref,),
                source_position=SourcePosition("/data/page/modules/23/components/7", array_index=7, ordering=9),
                source_fingerprint="1" * 64,
                parsed_fingerprint="2" * 64,
            ),
            value={"decoded": "normal"},
        )
        unsupported = ParsedUnknown.from_value(
            identity=component_identity("fixture-1", "unsupported_child", 0),
            raw_value={"unhandled": True},
            raw_refs=(self.fixture_ref,),
            source_position=SourcePosition("/data/page/modules/23/components/3", array_index=3, ordering=2),
            reason="fixture unsupported child",
        )
        component = ParsedComponent(
            identity=component_identity("fixture-1", "fixture_component", 0),
            metadata=ContractMetadata(
                raw_refs=(self.fixture_ref,),
                source_position=SourcePosition("/data/page/modules/23/components/7", array_index=7, ordering=9),
                source_fingerprint="3" * 64,
                parsed_fingerprint="4" * 64,
            ),
            source_component_id="fixture_component",
            source_data_encoding="json_value",
            units=(unit,),
            unsupported=(unsupported,),
        )
        module = ParsedModule(
            identity=module_identity("fixture-1", "fixture-module"),
            metadata=ContractMetadata(
                raw_refs=(self.fixture_ref,),
                source_position=SourcePosition("/data/page/modules/23", array_index=23, ordering=23),
                source_fingerprint="5" * 64,
                parsed_fingerprint="6" * 64,
            ),
            source_module_id="fixture-module",
            module_index=23,
            name=None,
            repeated=None,
            is_submodule=None,
            origin_module_id=None,
            components=(component,),
        )
        record = project_parsed_detail(replace_detail(detail, modules=(module,)), observation=self._observation())
        section = record.sections[0]
        context = section.component_contexts[0]

        self.assertEqual(section.lineage.parsed_json_pointer, "/modules/0")
        self.assertEqual(section.source_metadata["module_index"], 23)
        self.assertEqual(context.lineage.parsed_json_pointer, "/modules/0/components/0")
        self.assertEqual(context.source_position.array_index, 7)
        self.assertEqual(context.source_position.json_pointer, "/data/page/modules/23/components/7")
        self.assertEqual(context.to_dict()["source_position"]["array_index"], 7)
        self.assertEqual(section.units[0].lineage.parsed_json_pointer, "/modules/0/components/0/units/0")
        self.assertEqual(section.units[0].metadata["source_position"]["array_index"], 7)
        self.assertEqual(section.units[1].lineage.parsed_json_pointer, "/modules/0/components/0/unsupported/0")
        self.assertEqual(section.units[1].ordinal, 1)
        self.assertEqual(section.units[1].metadata["source_position"]["array_index"], 3)
        self.assertNotIn("unsupported", section.source_metadata)

    def test_blocked_projection_preserves_raw_and_no_raw_paths_without_fabrication(self) -> None:
        for with_raw_ref in (True, False):
            with self.subTest(with_raw_ref=with_raw_ref):
                blocked = self._blocked(with_raw_ref=with_raw_ref)
                record = project_parsed_input(blocked, observation=self._observation("blocked_integrity"))

                self.assertEqual(record.status, "blocked_integrity")
                self.assertEqual(record.lineage.parsed_json_pointer, "")
                self.assertIsNone(record.source_identity)
                self.assertEqual(record.sections, ())
                self.assertEqual(record.blocked_reason, blocked["error"])
                self.assertEqual(record.blocked_diagnostics, ())
                self.assertEqual(record.record_metadata["content_id"], "fixture-1")
                self.assertEqual(record.record_metadata["channel_memberships"], ["43", "99"])
                self.assertEqual(record.metadata_lineage[0][1].evidence_scope, "parsed_dependency")
                self.assertEqual(record.metadata_lineage[0][1].raw_refs, ())
                self.assertEqual(len(record.lineage.raw_refs), 1 if with_raw_ref else 0)

    def test_blocked_projection_rejects_malformed_diagnostics_without_silent_loss(self) -> None:
        for diagnostic in ({}, {"code": 1, "message": "wrong type", "severity": "warning", "path": ""}, {"code": "X", "message": "bad severity", "severity": "unknown", "path": ""}):
            with self.subTest(diagnostic=diagnostic):
                blocked = self._blocked(with_raw_ref=False)
                blocked["metadata"]["diagnostics"] = [diagnostic]

                with self.assertRaisesRegex(ValueError, "diagnostic"):
                    project_parsed_input(blocked, observation=self._observation("blocked_integrity"))

    def test_blocked_projection_preserves_contract_valid_unknown_diagnostic(self) -> None:
        blocked = self._blocked(with_raw_ref=False)
        diagnostic = {
            "code": "UNRECOGNIZED_UPSTREAM_CODE",
            "message": "保留未知但合法的诊断",
            "severity": "info",
            "path": "/unexpected",
        }
        blocked["metadata"]["diagnostics"] = [diagnostic]

        record = project_parsed_input(blocked, observation=self._observation("blocked_integrity"))

        self.assertEqual(record.to_dict()["blocked_diagnostics"], [diagnostic])

    def test_unsupported_component_becomes_accounted_unsupported_unit(self) -> None:
        detail = self._parsed_fixture()
        original = detail.modules[0].components[0]
        unknown = ParsedUnknown.from_value(
            identity=component_identity("fixture-1", "unknown", 0),
            raw_value={"unparsed": True},
            raw_refs=(self.fixture_ref,),
            source_position=SourcePosition("/data/page/modules/0/components/1", array_index=1, ordering=1),
            reason="component handler unavailable",
        )
        component = ParsedComponent(
            identity=original.identity,
            metadata=replace_metadata(original.metadata, parsed_fingerprint="e" * 64),
            source_component_id=original.source_component_id,
            source_data_encoding=original.source_data_encoding,
            source_layout=original.source_layout,
            source_style=original.source_style,
            units=(),
            unsupported=(unknown,),
        )
        module = replace_module(detail.modules[0], components=(component,))
        record = project_parsed_detail(replace_detail(detail, modules=(module,)), observation=self._observation())

        self.assertEqual(record.sections[0].component_contexts[0].unit_count, 1)
        self.assertEqual(record.sections[0].units[0].kind, "unsupported")
        self.assertEqual(record.sections[0].units[0].lineage.parsed_json_pointer, "/modules/0/components/0/unsupported/0")


def replace_metadata(metadata: ContractMetadata, **changes: object) -> ContractMetadata:
    values = {
        "schema_version": metadata.schema_version,
        "parser_version": metadata.parser_version,
        "parse_status": metadata.parse_status,
        "raw_refs": metadata.raw_refs,
        "source_position": metadata.source_position,
        "source_fingerprint": metadata.source_fingerprint or "a" * 64,
        "parsed_fingerprint": metadata.parsed_fingerprint or "b" * 64,
        "provenance": metadata.provenance,
        "content_role": metadata.content_role,
        "diagnostics": metadata.diagnostics,
    }
    values.update(changes)
    return ContractMetadata(**values)


def replace_detail(detail: ParsedDetail, **changes: object) -> ParsedDetail:
    values = {
        "identity": detail.identity,
        "metadata": detail.metadata,
        "source": detail.source,
        "locale": detail.locale,
        "run_id": detail.run_id,
        "content_id": detail.content_id,
        "page_id": detail.page_id,
        "name": detail.name,
        "page_type": detail.page_type,
        "source_metadata": detail.source_metadata,
        "source_template_layout": detail.source_template_layout,
        "modules": detail.modules,
        "unsupported_modules": detail.unsupported_modules,
        "channel_memberships": detail.channel_memberships,
    }
    values.update(changes)
    return ParsedDetail(**values)


def replace_module(module: ParsedModule, **changes: object) -> ParsedModule:
    values = {
        "identity": module.identity,
        "metadata": module.metadata,
        "source_module_id": module.source_module_id,
        "module_index": module.module_index,
        "name": module.name,
        "repeated": module.repeated,
        "is_submodule": module.is_submodule,
        "origin_module_id": module.origin_module_id,
        "components": module.components,
        "unsupported": module.unsupported,
        "layout_observations": module.layout_observations,
    }
    values.update(changes)
    return ParsedModule(**values)


if __name__ == "__main__":
    unittest.main()
