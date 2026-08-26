from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from genshin_corpus.parser.contracts import RawRef
from genshin_corpus.parser.obc.adapter import OBCIntegrityError, parse_obc_detail


class OBCAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(__file__).parents[1] / "fixtures" / "parsed" / "obc-detail.json"
        self.body = self.path.read_bytes()
        self.ref = RawRef(
            source="mihoyo_obc",
            locale="zh-cn",
            run_id="fixture-run",
            artifact_kind="details",
            artifact_path="responses/details/fixture.json",
            artifact_sha256=hashlib.sha256(self.body).hexdigest(),
            content_id="fixture-1",
        )

    def test_structure_and_layout_order_are_preserved(self) -> None:
        detail = parse_obc_detail(self.body, raw_ref=self.ref, content_id="fixture-1", channel_memberships=("43", "25", "43"))
        self.assertEqual(detail.content_id, "fixture-1")
        self.assertEqual(detail.channel_memberships, ("43", "25"))
        self.assertEqual([module.source_module_id for module in detail.modules], ["m1"])
        self.assertEqual(detail.modules[0].layout_observations[0]["position"], "left")
        self.assertEqual(detail.modules[0].components[0].source_component_id, "unknown_fixture")
        self.assertEqual(detail.modules[0].components[0].metadata.parse_status, "preserved_unsupported")
        self.assertEqual(detail.modules[0].components[0].metadata.raw_refs[0].json_pointer, "/data/page/modules/0/components/0")

    def test_generic_payload_and_rich_text_preserve_markup_and_references(self) -> None:
        detail = parse_obc_detail(self.body, raw_ref=self.ref, content_id="fixture-1")
        units = detail.modules[0].components[0].units
        self.assertEqual(units[0].value["raw"], json.loads(self.body)["data"]["page"]["modules"][0]["components"][0]["data"])
        rich = next(unit.value for unit in units if unit.value.get("kind") == "rich_text")
        self.assertEqual(rich["normalized_text"], "保留文本\n链接")
        self.assertEqual(rich["links"], [{"href": "/entry/1"}])
        self.assertEqual(rich["raw_markup"], "<p><strong>保留</strong>文本</p><p><a href=\"/entry/1\">链接</a></p>")
        self.assertTrue(rich["text_segments"])
        self.assertEqual(rich["entry_references"], [])

    def test_page_identity_conflict_is_blocking(self) -> None:
        with self.assertRaises(OBCIntegrityError):
            parse_obc_detail(self.body, raw_ref=self.ref, content_id="other")

    def test_artifact_hash_mismatch_is_blocking(self) -> None:
        bad_ref = RawRef(
            source=self.ref.source,
            locale=self.ref.locale,
            run_id=self.ref.run_id,
            artifact_kind=self.ref.artifact_kind,
            artifact_path=self.ref.artifact_path,
            artifact_sha256="0" * 64,
            content_id=self.ref.content_id,
        )
        with self.assertRaises(OBCIntegrityError):
            parse_obc_detail(self.body, raw_ref=bad_ref, content_id="fixture-1")

    def test_parsed_fingerprint_ignores_raw_run_path_metadata(self) -> None:
        other_ref = RawRef(
            source=self.ref.source,
            locale=self.ref.locale,
            run_id="another-run",
            artifact_kind=self.ref.artifact_kind,
            artifact_path="another/path/detail.json",
            artifact_sha256=self.ref.artifact_sha256,
            content_id=self.ref.content_id,
        )
        left = parse_obc_detail(self.body, raw_ref=self.ref, content_id="fixture-1")
        right = parse_obc_detail(self.body, raw_ref=other_ref, content_id="fixture-1")
        self.assertEqual(left.metadata.parsed_fingerprint, right.metadata.parsed_fingerprint)
        self.assertNotEqual(left.metadata.raw_refs[0].run_id, right.metadata.raw_refs[0].run_id)

    def test_non_object_structure_is_preserved_as_unsupported(self) -> None:
        payload = json.loads(self.body)
        page = payload["data"]["page"]
        page["modules"].append("module anomaly")
        page["modules"][0]["components"].append(["component anomaly"])
        mutated_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        mutated_ref = RawRef(
            source=self.ref.source,
            locale=self.ref.locale,
            run_id=self.ref.run_id,
            artifact_kind=self.ref.artifact_kind,
            artifact_path=self.ref.artifact_path,
            artifact_sha256=hashlib.sha256(mutated_body).hexdigest(),
            content_id=self.ref.content_id,
        )
        detail = parse_obc_detail(
            mutated_body,
            raw_ref=mutated_ref,
            content_id="fixture-1",
        )
        self.assertEqual(detail.unsupported_modules[0].raw_value, "module anomaly")
        self.assertEqual(detail.modules[0].unsupported[0].raw_value, ["component anomaly"])
        self.assertEqual(detail.metadata.parse_status, "parsed_with_anomalies")

    def test_malformed_component_container_is_preserved(self) -> None:
        payload = json.loads(self.body)
        payload["data"]["page"]["modules"][0]["components"] = {"unexpected": True}
        mutated_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        mutated_ref = RawRef(
            source=self.ref.source,
            locale=self.ref.locale,
            run_id=self.ref.run_id,
            artifact_kind=self.ref.artifact_kind,
            artifact_path=self.ref.artifact_path,
            artifact_sha256=hashlib.sha256(mutated_body).hexdigest(),
            content_id=self.ref.content_id,
        )
        detail = parse_obc_detail(mutated_body, raw_ref=mutated_ref, content_id="fixture-1")
        self.assertEqual(detail.modules[0].unsupported[0].raw_value, {"unexpected": True})


if __name__ == "__main__":
    unittest.main()
