from __future__ import annotations

from dataclasses import replace
import hashlib
import unittest

from genshin_corpus.canonical import (
    CanonicalObservation,
    CanonicalRecord,
    CanonicalSection,
    CanonicalUnit,
    ComponentContext,
    LineageLink,
    canonical_record_id,
    serialize_canonical_record,
    source_identity_from_parsed_input,
)
from genshin_corpus.parser.contracts import ContractMetadata, Diagnostic, ParsedIdentity, RawRef
from genshin_corpus.parser.identity import detail_identity
from genshin_corpus.parser.models import ParsedDetail


class CanonicalContractFixtures:
    raw_ref = RawRef(
        source="mihoyo_obc",
        locale="zh-cn",
        run_id="raw-fixture",
        artifact_kind="details",
        artifact_path="responses/details/fixture-1.json",
        artifact_sha256="a" * 64,
        content_id="fixture-1",
        json_pointer="/data/page",
    )

    @classmethod
    def observation(cls, *, parsed_status: str = "parsed", parsed_run_id: str = "parsed-fixture") -> CanonicalObservation:
        return CanonicalObservation(
            parsed_run_id=parsed_run_id,
            parsed_manifest_path=f"runs/{parsed_run_id}/metadata/manifest.json",
            parsed_manifest_sha256="b" * 64,
            parsed_record_path=f"runs/{parsed_run_id}/records/fixture.json",
            parsed_record_sha256="c" * 64,
            parsed_schema_version="phase02-draft-0.1",
            parsed_parser_version="obc-foundation-0.2",
            parsed_pipeline_version="obc-parsed-run-0.2",
            parsed_status=parsed_status,
            parsed_semantic_fingerprint="d" * 64,
            parsed_rule_versions=(("classification_ruleset", "phase02-classification-0.1"),),
        )

    @classmethod
    def normal_detail(cls) -> ParsedDetail:
        return ParsedDetail(
            identity=detail_identity("mihoyo_obc", "zh-cn", "fixture-1"),
            metadata=ContractMetadata(
                raw_refs=(cls.raw_ref,),
                source_fingerprint="e" * 64,
                parsed_fingerprint="f" * 64,
            ),
            source="mihoyo_obc",
            locale="zh-cn",
            run_id="raw-fixture",
            content_id="fixture-1",
            page_id="fixture-1",
            name="fixture",
            page_type="Default",
        )

    @classmethod
    def blocked_input(cls, *, raw_refs: list[dict] | None, include_source_fields: bool = False) -> dict:
        value = {
            "identity": {
                "kind": "detail_observation",
                "key": "blocked:fixture-1:dependency-fixture",
                "stability": "snapshot_only",
                "components": {"content_id": "fixture-1", "dependency": "dependency-fixture"},
            },
            "metadata": {
                "parse_status": "blocked_integrity",
                "raw_refs": [] if raw_refs is None else raw_refs,
                "parsed_fingerprint": "d" * 64,
            },
            "content_id": "fixture-1",
            "channels": ["43", "99"],
            "error": "RAW_METADATA_INTEGRITY_MISMATCH",
        }
        if include_source_fields:
            value.update({"source": "mihoyo_obc", "locale": "zh-cn", "manifest": {"source": "mihoyo_obc", "locale": "zh-cn"}})
        return value

    @classmethod
    def record(cls, *, parsed_identity: ParsedIdentity, observation: CanonicalObservation, status: str, source_identity: ParsedIdentity | None, lineage: LineageLink, **kwargs: object) -> CanonicalRecord:
        return CanonicalRecord(
            record_id=canonical_record_id(
                parsed_run_id=observation.parsed_run_id,
                parsed_identity_key=parsed_identity.key,
                parsed_record_sha256=observation.parsed_record_sha256,
            ),
            parsed_identity=parsed_identity,
            observation=observation,
            status=status,
            lineage=lineage,
            source_identity=source_identity,
            **kwargs,
        )


class CanonicalContractTests(unittest.TestCase):
    def test_normal_parsed_detail_copies_verified_source_identity(self) -> None:
        detail = CanonicalContractFixtures.normal_detail()

        source_identity = source_identity_from_parsed_input(detail)
        record = CanonicalContractFixtures.record(
            parsed_identity=detail.identity,
            observation=CanonicalContractFixtures.observation(),
            status="canonical",
            source_identity=source_identity,
            lineage=LineageLink("/", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
        )

        self.assertEqual(source_identity, detail.identity)
        self.assertEqual(record.to_dict()["source_identity"], detail.identity.to_dict())

    def test_blocked_integrity_with_rawref_preserves_direct_raw_lineage(self) -> None:
        blocked = CanonicalContractFixtures.blocked_input(raw_refs=[CanonicalContractFixtures.raw_ref.to_dict()])
        parsed_identity = ParsedIdentity(
            kind="detail_observation",
            key=blocked["identity"]["key"],
            stability="snapshot_only",
            components=(("content_id", "fixture-1"), ("dependency", "dependency-fixture")),
        )
        record = CanonicalContractFixtures.record(
            parsed_identity=parsed_identity,
            observation=CanonicalContractFixtures.observation(parsed_status="blocked_integrity"),
            status="blocked_integrity",
            source_identity=source_identity_from_parsed_input(blocked),
            lineage=LineageLink("", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
            record_metadata={"content_id": blocked["content_id"], "channel_memberships": blocked["channels"]},
            metadata_lineage=(
                ("channel_memberships", LineageLink("", "parsed_dependency", dependency_locator="parsed-manifest:records/fixture-1/channels")),
            ),
            blocked_reason=blocked["error"],
        )

        self.assertIsNone(record.source_identity)
        self.assertEqual(record.status, "blocked_integrity")
        self.assertEqual(record.lineage.raw_refs, (CanonicalContractFixtures.raw_ref,))
        self.assertEqual(record.sections, ())
        serialized = record.to_dict()
        self.assertEqual(serialized["blocked_reason"], blocked["error"])
        self.assertEqual(serialized["blocked_diagnostics"], [])
        self.assertEqual(serialized["record_metadata"]["content_id"], "fixture-1")
        self.assertEqual(serialized["record_metadata"]["channel_memberships"], ["43", "99"])
        self.assertEqual(serialized["metadata_lineage"]["channel_memberships"]["evidence_scope"], "parsed_dependency")
        self.assertEqual(serialized["metadata_lineage"]["channel_memberships"]["raw_refs"], [])
        self.assertEqual(serialized["observation"]["parsed_record_sha256"], "c" * 64)
        self.assertEqual(serialized["lineage"]["raw_refs"][0]["artifact_sha256"], "a" * 64)

    def test_blocked_integrity_without_rawref_uses_parsed_dependency_lineage(self) -> None:
        blocked = CanonicalContractFixtures.blocked_input(raw_refs=None)
        parsed_identity = ParsedIdentity(
            kind="detail_observation",
            key=blocked["identity"]["key"],
            stability="snapshot_only",
            components=(("content_id", "fixture-1"), ("dependency", "dependency-fixture")),
        )
        record = CanonicalContractFixtures.record(
            parsed_identity=parsed_identity,
            observation=CanonicalContractFixtures.observation(parsed_status="blocked_integrity"),
            status="blocked_integrity",
            source_identity=source_identity_from_parsed_input(blocked),
            lineage=LineageLink("", "parsed_dependency", dependency_locator="parsed-manifest:records/fixture-1"),
            record_metadata={"content_id": blocked["content_id"], "channel_memberships": blocked["channels"]},
            metadata_lineage=(
                ("channel_memberships", LineageLink("", "parsed_dependency", dependency_locator="parsed-manifest:records/fixture-1/channels")),
            ),
            blocked_reason=blocked["error"],
        )

        self.assertIsNone(record.source_identity)
        self.assertEqual(record.lineage.raw_refs, ())
        self.assertEqual(record.lineage.evidence_scope, "parsed_dependency")
        serialized = record.to_dict()
        self.assertEqual(serialized["blocked_reason"], blocked["error"])
        self.assertEqual(serialized["blocked_diagnostics"], [])
        self.assertEqual(serialized["record_metadata"]["content_id"], "fixture-1")
        self.assertEqual(serialized["record_metadata"]["channel_memberships"], ["43", "99"])
        self.assertEqual(serialized["metadata_lineage"]["channel_memberships"]["evidence_scope"], "parsed_dependency")
        self.assertEqual(serialized["metadata_lineage"]["channel_memberships"]["raw_refs"], [])
        self.assertEqual(serialized["lineage"]["raw_refs"], [])

    def test_blocked_integrity_preserves_supplied_diagnostics_without_requiring_them(self) -> None:
        blocked = CanonicalContractFixtures.blocked_input(raw_refs=None)
        diagnostic = Diagnostic("UPSTREAM_NOTE", "retained upstream diagnostic", "warning", "/error")
        parsed_identity = ParsedIdentity(
            kind="detail_observation",
            key=blocked["identity"]["key"],
            stability="snapshot_only",
            components=(("content_id", "fixture-1"), ("dependency", "dependency-fixture")),
        )
        record = CanonicalContractFixtures.record(
            parsed_identity=parsed_identity,
            observation=CanonicalContractFixtures.observation(parsed_status="blocked_integrity"),
            status="blocked_integrity",
            source_identity=None,
            lineage=LineageLink("", "parsed_dependency", dependency_locator="parsed-manifest:records/fixture-1"),
            record_metadata={"content_id": blocked["content_id"], "channel_memberships": blocked["channels"]},
            metadata_lineage=(
                ("channel_memberships", LineageLink("", "parsed_dependency", dependency_locator="parsed-manifest:records/fixture-1/channels")),
            ),
            blocked_reason=blocked["error"],
            blocked_diagnostics=(diagnostic,),
        )

        self.assertEqual(record.to_dict()["blocked_diagnostics"], [diagnostic.to_dict()])

    def test_blocked_integrity_never_synthesizes_source_identity(self) -> None:
        blocked = CanonicalContractFixtures.blocked_input(raw_refs=None, include_source_fields=True)

        self.assertIsNone(source_identity_from_parsed_input(blocked))

    def test_record_id_uses_exactly_run_identity_key_and_record_hash(self) -> None:
        expected = hashlib.sha256(
            b'["parsed-fixture","mihoyo_obc:zh-cn:fixture-1","' + b"c" * 64 + b'"]'
        ).hexdigest()
        identity = detail_identity("mihoyo_obc", "zh-cn", "fixture-1")
        same_key_different_identity_object = ParsedIdentity(
            kind="other",
            key=identity.key,
            stability="snapshot_only",
            components=(("unrelated", "value"),),
        )

        self.assertEqual(
            canonical_record_id(
                parsed_run_id="parsed-fixture",
                parsed_identity_key=identity.key,
                parsed_record_sha256="c" * 64,
            ),
            expected,
        )
        self.assertEqual(
            canonical_record_id(
                parsed_run_id="parsed-fixture",
                parsed_identity_key=same_key_different_identity_object.key,
                parsed_record_sha256="c" * 64,
            ),
            expected,
        )

    def test_channel_memberships_require_parsed_dependency_not_fabricated_rawref(self) -> None:
        detail = CanonicalContractFixtures.normal_detail()
        observation = CanonicalContractFixtures.observation()
        record = CanonicalContractFixtures.record(
            parsed_identity=detail.identity,
            observation=observation,
            status="canonical",
            source_identity=source_identity_from_parsed_input(detail),
            lineage=LineageLink("/", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
            record_metadata={"channel_memberships": ["43", "99"]},
            metadata_lineage=(
                ("channel_memberships", LineageLink("", "parsed_dependency", dependency_locator="parsed-manifest:records/fixture-1/channels")),
            ),
        )

        self.assertEqual(record.to_dict()["metadata_lineage"]["channel_memberships"]["raw_refs"], [])
        with self.assertRaisesRegex(ValueError, "channel_memberships require parsed_dependency"):
            CanonicalContractFixtures.record(
                parsed_identity=detail.identity,
                observation=observation,
                status="canonical",
                source_identity=source_identity_from_parsed_input(detail),
                lineage=LineageLink("/", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
                record_metadata={"channel_memberships": ["43"]},
                metadata_lineage=(("channel_memberships", LineageLink("", "direct_raw", (CanonicalContractFixtures.raw_ref,))),),
            )

    def test_component_context_accounts_for_zero_unit_component_without_artificial_unit(self) -> None:
        detail = CanonicalContractFixtures.normal_detail()
        context = ComponentContext(
            observation_key="component-observation-0",
            ordinal=0,
            source_component_id="unknown_fixture",
            source_data_encoding="json_value",
            source_layout="",
            source_style="",
            parsed_component_fingerprint="e" * 64,
            parsed_status="preserved_unsupported",
            provenance=detail.metadata.provenance,
            content_role=detail.metadata.content_role,
            diagnostics=(),
            lineage=LineageLink("/modules/0/components/0", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
            child_unit_ordinals=(),
            unit_count=0,
        )
        section = CanonicalSection(
            section_id="section-observation-0",
            ordinal=0,
            lineage=LineageLink("/modules/0", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
            component_contexts=(context,),
        )

        self.assertEqual(section.component_contexts[0].unit_count, 0)
        self.assertEqual(section.units, ())

    def test_serialization_is_deterministic_and_content_fingerprint_excludes_runtime_observation_fields(self) -> None:
        detail = CanonicalContractFixtures.normal_detail()
        observation = CanonicalContractFixtures.observation()
        record = CanonicalContractFixtures.record(
            parsed_identity=detail.identity,
            observation=observation,
            status="canonical",
            source_identity=source_identity_from_parsed_input(detail),
            lineage=LineageLink("/", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
        )
        other_observation = replace(
            observation,
            parsed_run_id="another-parsed-run",
            parsed_manifest_path="other/output/manifest.json",
            parsed_record_path="other/output/record.json",
            parsed_manifest_sha256="1" * 64,
            parsed_record_sha256="2" * 64,
            parsed_semantic_fingerprint="3" * 64,
        )
        other_record = CanonicalContractFixtures.record(
            parsed_identity=detail.identity,
            observation=other_observation,
            status="canonical",
            source_identity=source_identity_from_parsed_input(detail),
            lineage=LineageLink("/", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
        )

        self.assertEqual(serialize_canonical_record(record), serialize_canonical_record(record))
        self.assertEqual(record.content_fingerprint, other_record.content_fingerprint)
        self.assertNotEqual(record.dependency_fingerprint, other_record.dependency_fingerprint)
        self.assertIn("mihoyo_obc", serialize_canonical_record(record).decode("utf-8"))
        projection = record.content_projection()
        self.assertEqual(projection["lineage"]["raw_refs"][0]["artifact_path"], "responses/details/fixture-1.json")

    def test_section_requires_every_unit_to_reference_its_component_context(self) -> None:
        unit = CanonicalUnit(
            unit_id="unit-0",
            kind="structured_observation",
            ordinal=0,
            parent_component_key="missing-component",
            value={"raw": "value"},
            parsed_status="parsed",
            lineage=LineageLink("/modules/0/components/0/units/0", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
        )
        with self.assertRaisesRegex(ValueError, "must reference"):
            CanonicalSection(
                section_id="section-0",
                ordinal=0,
                lineage=LineageLink("/modules/0", "direct_raw", (CanonicalContractFixtures.raw_ref,)),
                units=(unit,),
            )


if __name__ == "__main__":
    unittest.main()
