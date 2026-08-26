from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unittest

from genshin_corpus.parser.obc.pipeline import OBCParsedRunPipeline, ParseDependencies, _diagnostic_codes
from genshin_corpus.parser.storage import ParsedRunStore


class OBCParsedRunPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/parsed/.obc-pipeline-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.raw_root = self.root / "raw" / "mihoyo_obc" / "zh-cn" / "fixture-raw"
        self.output_root = self.root / "parsed"
        self._write_raw_run()

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _detail_body(self, content_id: str, text: str) -> bytes:
        payload = {
            "data": {
                "page": {
                    "id": content_id,
                    "name": f"fixture-{content_id}",
                    "modules": [{
                        "id": "m1",
                        "components": [{"component_id": "unknown_fixture", "data": {"text": text}}],
                    }],
                },
            },
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _write_raw_run(self, *, invalid_metadata_for: str | None = None) -> None:
        details = []
        paths = {}
        for content_id, text in (("one", "first"), ("two", "second")):
            raw_path = self.raw_root / "responses" / "details" / f"{content_id}.json"
            meta_path = raw_path.with_suffix(".meta.json")
            body = self._detail_body(content_id, text)
            digest = hashlib.sha256(body).hexdigest()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(body)
            metadata = {
                "kind": "details",
                "key": content_id,
                "sha256": "0" * 64 if content_id == invalid_metadata_for else digest,
                "ok": True,
                "status": 200,
            }
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")
            details.append({"content_id": content_id, "channels": ["43"], "status": "completed"})
            paths[f"details:{content_id}"] = {"raw": str(raw_path), "metadata": str(meta_path), "sha256": digest}
        manifest = {
            "source_system": "mihoyo_obc",
            "locale": "zh-cn",
            "run_id": "fixture-raw",
            "status": "complete",
            "details": details,
            "paths": paths,
        }
        metadata_dir = self.raw_root / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        (metadata_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _mutate_second_raw_observation(self) -> None:
        manifest_path = self.raw_root / "metadata" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_path = Path(manifest["paths"]["details:two"]["raw"])
        meta_path = Path(manifest["paths"]["details:two"]["metadata"])
        body = self._detail_body("two", "changed")
        digest = hashlib.sha256(body).hexdigest()
        raw_path.write_bytes(body)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["sha256"] = digest
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        manifest["paths"]["details:two"]["sha256"] = digest
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def _set_second_channels(self, channels: list[str]) -> None:
        manifest_path = self.raw_root / "metadata" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        detail = next(item for item in manifest["details"] if item["content_id"] == "two")
        detail["channels"] = channels
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def _set_raw_run_id(self, raw_run_id: str) -> None:
        manifest_path = self.raw_root / "metadata" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["run_id"] = raw_run_id
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_non_complete_raw_manifest_is_rejected_before_parsed_output(self) -> None:
        manifest_path = self.raw_root / "metadata" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "partial"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "complete Raw manifest"):
            OBCParsedRunPipeline(
                raw_run_root=self.raw_root,
                output_root=self.output_root,
                parsed_run_id="partial-raw",
            ).run()

        parsed_manifest = self.output_root / "mihoyo_obc" / "zh-cn" / "partial-raw" / "metadata" / "manifest.json"
        self.assertFalse(parsed_manifest.exists())

    def test_reuses_completed_same_run_and_prior_observations(self) -> None:
        calls = []

        def parser(*args, **kwargs):
            calls.append(kwargs["content_id"])
            from genshin_corpus.parser.obc.adapter import parse_obc_detail
            return parse_obc_detail(*args, **kwargs)

        first = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="first",
            detail_parser=parser,
        ).run()
        self.assertEqual(first["input_detail_count"], 2)
        self.assertEqual(first["accounted_detail_count"], 2)
        self.assertEqual(first["reparse_count"], 2)
        self.assertEqual(calls, ["one", "two"])

        repeated = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="first",
            detail_parser=lambda *args, **kwargs: self.fail("same completed run must not parse again"),
        ).run()
        self.assertEqual(repeated, first)

        first_manifest = self.output_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        reuse = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="reuse",
            reuse_manifest_path=first_manifest,
            detail_parser=lambda *args, **kwargs: self.fail("unchanged observation must be reused"),
        ).run()
        self.assertEqual(reuse["reuse_count"], 2)
        self.assertEqual(reuse["reparse_count"], 0)

    def test_completed_run_fast_reuse_rechecks_dependencies_and_raw_integrity(self) -> None:
        OBCParsedRunPipeline(raw_run_root=self.raw_root, output_root=self.output_root, parsed_run_id="first").run()
        raw_manifest_path = self.raw_root / "metadata" / "manifest.json"
        raw_manifest_body = raw_manifest_path.read_bytes()
        raw_manifest = json.loads(raw_manifest_body.decode("utf-8"))
        store = ParsedRunStore(self.output_root, "mihoyo_obc", "zh-cn", "first")
        current = OBCParsedRunPipeline(raw_run_root=self.raw_root, output_root=self.output_root, parsed_run_id="first")
        inputs = current._raw_inputs(raw_manifest)
        self.assertIsNotNone(current._completed_manifest_if_current(
            store=store,
            inputs=inputs,
            raw_manifest_sha256=hashlib.sha256(raw_manifest_body).hexdigest(),
            raw_run_id="fixture-raw",
        ))

        manifest_body = store.manifest_path.read_text(encoding="utf-8")
        stale = json.loads(manifest_body)
        stale["records"][1]["dependency_fingerprint"] = "stale"
        store.manifest_path.write_text(json.dumps(stale), encoding="utf-8")
        self.assertIsNone(current._completed_manifest_if_current(
            store=store,
            inputs=inputs,
            raw_manifest_sha256=hashlib.sha256(raw_manifest_body).hexdigest(),
            raw_run_id="fixture-raw",
        ))
        store.manifest_path.write_text(manifest_body, encoding="utf-8")

        version_changed = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="first",
            dependencies=ParseDependencies(parser_version="fixture-parser-v2"),
        )
        self.assertIsNone(version_changed._completed_manifest_if_current(
            store=store,
            inputs=inputs,
            raw_manifest_sha256=hashlib.sha256(raw_manifest_body).hexdigest(),
            raw_run_id="fixture-raw",
        ))

        raw_path = Path(raw_manifest["paths"]["details:two"]["raw"])
        raw_path.write_bytes(self._detail_body("two", "tampered-without-manifest-update"))
        self.assertIsNone(current._completed_manifest_if_current(
            store=store,
            inputs=inputs,
            raw_manifest_sha256=hashlib.sha256(raw_manifest_body).hexdigest(),
            raw_run_id="fixture-raw",
        ))

    def test_channel_membership_change_reparses_only_affected_observation(self) -> None:
        first = OBCParsedRunPipeline(raw_run_root=self.raw_root, output_root=self.output_root, parsed_run_id="first").run()
        first_manifest = self.output_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        self._set_second_channels(["43", "99"])
        calls = []

        def parser(*args, **kwargs):
            calls.append(kwargs["content_id"])
            from genshin_corpus.parser.obc.adapter import parse_obc_detail
            return parse_obc_detail(*args, **kwargs)

        changed = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="channels-changed",
            reuse_manifest_path=first_manifest,
            detail_parser=parser,
        ).run()
        self.assertEqual(first["records"][1]["raw_artifact_sha256"], changed["records"][1]["raw_artifact_sha256"])
        self.assertEqual(changed["reuse_count"], 1)
        self.assertEqual(changed["reparse_count"], 1)
        self.assertEqual(calls, ["two"])
        record = next(item for item in changed["records"] if item["content_id"] == "two")
        value = json.loads(Path(record["record_path"]).read_text(encoding="utf-8"))
        self.assertEqual(value["channel_memberships"], ["43", "99"])

    def test_different_raw_run_id_does_not_reuse_old_rawref_record(self) -> None:
        OBCParsedRunPipeline(raw_run_root=self.raw_root, output_root=self.output_root, parsed_run_id="first").run()
        first_manifest = self.output_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        self._set_raw_run_id("fixture-raw-next")
        calls = []

        def parser(*args, **kwargs):
            calls.append(kwargs["content_id"])
            from genshin_corpus.parser.obc.adapter import parse_obc_detail
            return parse_obc_detail(*args, **kwargs)

        result = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="next-raw-run",
            reuse_manifest_path=first_manifest,
            detail_parser=parser,
        ).run()
        self.assertEqual(result["reuse_count"], 0)
        self.assertEqual(result["reparse_count"], 2)
        self.assertEqual(calls, ["one", "two"])
        record = next(item for item in result["records"] if item["content_id"] == "one")
        value = json.loads(Path(record["record_path"]).read_text(encoding="utf-8"))
        self.assertEqual(value["metadata"]["raw_refs"][0]["run_id"], "fixture-raw-next")

    def test_raw_change_and_version_change_reparse_only_affected_dependencies(self) -> None:
        first = OBCParsedRunPipeline(raw_run_root=self.raw_root, output_root=self.output_root, parsed_run_id="first").run()
        first_manifest = self.output_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        self._mutate_second_raw_observation()
        changed_calls = []

        def changed_parser(*args, **kwargs):
            changed_calls.append(kwargs["content_id"])
            from genshin_corpus.parser.obc.adapter import parse_obc_detail
            return parse_obc_detail(*args, **kwargs)

        changed = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="raw-changed",
            reuse_manifest_path=first_manifest,
            detail_parser=changed_parser,
        ).run()
        self.assertEqual(changed["reuse_count"], 1)
        self.assertEqual(changed["reparse_count"], 1)
        self.assertEqual(changed_calls, ["two"])

        version_calls = []

        def version_parser(*args, **kwargs):
            version_calls.append(kwargs["content_id"])
            from genshin_corpus.parser.obc.adapter import parse_obc_detail
            return parse_obc_detail(*args, **kwargs)

        changed_manifest = self.output_root / "mihoyo_obc" / "zh-cn" / "raw-changed" / "metadata" / "manifest.json"
        dependencies = ParseDependencies(
            schema_version="fixture-schema-v2",
            parser_version="fixture-parser-v2",
            pipeline_version="fixture-pipeline-v2",
            rule_versions=(("fixture-rule", "v2"),),
        )
        version_changed = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="version-changed",
            reuse_manifest_path=changed_manifest,
            dependencies=dependencies,
            detail_parser=version_parser,
        ).run()
        self.assertEqual(version_changed["reuse_count"], 0)
        self.assertEqual(version_changed["reparse_count"], 2)
        self.assertEqual(version_calls, ["one", "two"])

    def test_integrity_failure_is_accounted_without_dropping_other_detail(self) -> None:
        self._write_raw_run(invalid_metadata_for="two")
        result = OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.output_root,
            parsed_run_id="integrity",
        ).run()
        self.assertEqual(result["input_detail_count"], 2)
        self.assertEqual(result["accounted_detail_count"], 2)
        self.assertEqual(result["counts"]["parsed"], 1)
        self.assertEqual(result["counts"]["blocked_integrity"], 1)
        blocked = next(item for item in result["records"] if item["content_id"] == "two")
        self.assertEqual(blocked["diagnostic_codes"], ["PARSED_INPUT_INTEGRITY"])
        self.assertTrue(Path(blocked["record_path"]).is_file())

    def test_nested_contract_diagnostics_are_deduplicated_per_detail(self) -> None:
        value = {
            "metadata": {"diagnostics": [{"code": "OUTER"}]},
            "modules": [{
                "metadata": {"diagnostics": [{"code": "INNER"}, {"code": "OUTER"}]},
                "components": [],
            }],
        }
        self.assertEqual(_diagnostic_codes(value), ("INNER", "OUTER"))


if __name__ == "__main__":
    unittest.main()
