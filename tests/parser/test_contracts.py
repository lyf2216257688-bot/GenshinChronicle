from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from genshin_corpus.parser.contracts import (
    Classification,
    Diagnostic,
    RawRef,
    SourcePosition,
)
from genshin_corpus.parser.fingerprints import parsed_fingerprint, source_fingerprint
from genshin_corpus.parser.identity import (
    component_identity,
    content_unit_identity,
    detail_identity,
    module_identity,
)
from genshin_corpus.parser.models import ParsedUnknown
from genshin_corpus.parser.storage import ParsedRunStore, blank_manifest


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_ref = RawRef(
            source="mihoyo_obc",
            locale="zh-cn",
            run_id="fixture-run",
            artifact_kind="details",
            artifact_path="responses/details/fixture.json",
            artifact_sha256="a" * 64,
            content_id="fixture-1",
            json_pointer="/data/page/modules/0/components/0",
            embedded_json_pointer="/nested",
            source_value_sha256="b" * 64,
        )

    def test_identity_stability_is_explicit_and_position_is_not_identity(self) -> None:
        detail = detail_identity("mihoyo_obc", "zh-cn", "fixture-1")
        module = module_identity("fixture-1", "module-7")
        component = component_identity("fixture-1", "unknown_fixture", 0)
        unit = content_unit_identity(component.key, None, 0)

        self.assertEqual(detail.stability, "logical")
        self.assertEqual(module.stability, "candidate")
        self.assertEqual(component.stability, "candidate")
        self.assertEqual(unit.stability, "snapshot_only")
        self.assertNotIn("array_index", detail.key)

    def test_fingerprints_are_deterministic_and_distinguish_source_and_projection(self) -> None:
        left = {"b": [1, 2], "a": "文本"}
        right = {"a": "文本", "b": [1, 2]}
        self.assertEqual(source_fingerprint(left), source_fingerprint(right))
        self.assertEqual(parsed_fingerprint(left), parsed_fingerprint(right))
        self.assertNotEqual(source_fingerprint(left), parsed_fingerprint({"text": "文本"}))

    def test_unknown_round_trip_preserves_exact_decoded_value_and_traceability(self) -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "parsed" / "contract-detail.json"
        value = json.loads(fixture_path.read_text(encoding="utf-8"))["module"]["component"]["data"]
        unknown = ParsedUnknown.from_value(
            identity=component_identity("fixture-1", "unknown_fixture", 0),
            raw_value=value,
            raw_refs=(self.raw_ref,),
            source_position=SourcePosition("/data/page/modules/0/components/0", array_index=0),
            reason="component handler has not been promoted",
            context={"component_id": "unknown_fixture"},
            diagnostics=(Diagnostic("UNSUPPORTED_COMPONENT", "retained for a later handler"),),
        )
        result = unknown.to_dict()
        self.assertEqual(result["raw_value"], value)
        self.assertEqual(result["metadata"]["parse_status"], "preserved_unsupported")
        self.assertEqual(result["metadata"]["raw_refs"][0]["artifact_sha256"], "a" * 64)
        self.assertEqual(result["metadata"]["source_position"]["json_pointer"], "/data/page/modules/0/components/0")

    def test_classification_keeps_provenance_and_role_separate(self) -> None:
        provenance = Classification(state="classified", labels=("official_game_text",), basis=("explicit_source_rule",))
        role = Classification()
        self.assertEqual(provenance.labels, ("official_game_text",))
        self.assertEqual(role.state, "unknown")
        self.assertNotEqual(provenance.to_dict(), role.to_dict())

    def test_parsed_store_rejects_conflicting_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ParsedRunStore(Path(directory), "mihoyo_obc", "zh-cn", "parsed-fixture")
            first = store.write_record("detail:fixture-1", {"value": 1})
            self.assertTrue(Path(first["path"]).exists())
            with self.assertRaises(FileExistsError):
                store.write_record("detail:fixture-1", {"value": 2})

    def test_manifest_carries_raw_dependency_and_status_counts(self) -> None:
        manifest = blank_manifest(
            source="mihoyo_obc",
            locale="zh-cn",
            parsed_run_id="parsed-fixture",
            raw_run_id="fixture-run",
            raw_manifest_sha256="c" * 64,
        )
        self.assertEqual(manifest["raw_run_id"], "fixture-run")
        self.assertEqual(manifest["raw_manifest_sha256"], "c" * 64)
        self.assertIn("preserved_unsupported", manifest["counts"])


if __name__ == "__main__":
    unittest.main()
