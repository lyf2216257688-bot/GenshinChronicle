from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

from genshin_corpus.canonical import CanonicalRunPipeline, CanonicalRunStore, CanonicalVersions
from genshin_corpus.canonical.storage import blank_manifest
from genshin_corpus.parser.obc.pipeline import OBCParsedRunPipeline


class CanonicalRunPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/parsed/.canonical-pipeline-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.raw_root = self.root / "raw" / "mihoyo_obc" / "zh-cn" / "fixture-raw"
        self.parsed_root = self.root / "parsed"
        self.canonical_root = self.root / "canonical-output"
        self._write_raw_run()
        OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=self.parsed_root,
            parsed_run_id="fixture-parsed",
        ).run()
        self.parsed_manifest = self.parsed_root / "mihoyo_obc" / "zh-cn" / "fixture-parsed" / "metadata" / "manifest.json"

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

    def _write_raw_run(self) -> None:
        details: list[dict[str, object]] = []
        paths: dict[str, dict[str, str]] = {}
        for content_id, text in (("one", "first"), ("two", "second")):
            raw_path = self.raw_root / "responses" / "details" / f"{content_id}.json"
            metadata_path = raw_path.with_suffix(".meta.json")
            body = self._detail_body(content_id, text)
            digest = hashlib.sha256(body).hexdigest()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(body)
            metadata_path.write_text(json.dumps({"kind": "details", "key": content_id, "sha256": digest, "ok": True, "status": 200}), encoding="utf-8")
            details.append({"content_id": content_id, "channels": ["43"], "status": "completed"})
            paths[f"details:{content_id}"] = {"raw": str(raw_path), "metadata": str(metadata_path), "sha256": digest}
        metadata = self.raw_root / "metadata"
        metadata.mkdir(parents=True, exist_ok=True)
        (metadata / "manifest.json").write_text(json.dumps({
            "source_system": "mihoyo_obc",
            "locale": "zh-cn",
            "run_id": "fixture-raw",
            "status": "complete",
            "details": details,
            "paths": paths,
        }), encoding="utf-8")

    def _run(self, canonical_run_id: str, **kwargs: object) -> dict:
        return CanonicalRunPipeline(
            parsed_manifest_path=self.parsed_manifest,
            output_root=self.canonical_root,
            canonical_run_id=canonical_run_id,
            **kwargs,
        ).run()

    def test_persists_deterministic_records_and_complete_manifest(self) -> None:
        manifest = self._run("first")

        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["input_record_count"], 2)
        self.assertEqual(manifest["accounted_record_count"], 2)
        self.assertEqual(manifest["input_integrity_failure_count"], 0)
        self.assertEqual(manifest["reproject_count"], 2)
        self.assertEqual(manifest["reuse_count"], 0)
        self.assertEqual(manifest["counts"]["canonical"], 2)
        self.assertEqual(manifest["dependencies"]["canonical_pipeline_version"], "canonical-run-0.1")
        for record in manifest["records"]:
            body = Path(record["canonical_record_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), record["canonical_record_sha256"])
            serialized = json.loads(body.decode("utf-8"))
            self.assertEqual(serialized["record_id"], record["record_id"])
            self.assertEqual(serialized["observation"]["parsed_manifest_sha256"], manifest["parsed_manifest_sha256"])
            self.assertEqual(serialized["dependency_fingerprint"], record["dependency_fingerprint"])

        repeated = self._run("first")
        self.assertEqual(repeated, manifest)

    def test_completed_canonical_manifest_conflict_cannot_be_overwritten(self) -> None:
        store = CanonicalRunStore(self.canonical_root, "mihoyo_obc", "zh-cn", "immutable")
        manifest = blank_manifest(
            source="mihoyo_obc",
            locale="zh-cn",
            canonical_run_id="immutable",
            parsed_run_id="fixture-parsed",
            parsed_manifest_path=str(self.parsed_manifest),
            parsed_manifest_sha256="a" * 64,
            dependencies={},
        )
        manifest["status"] = "complete"
        store.write_manifest(manifest)
        conflicting = {**manifest, "reuse_count": 1}

        with self.assertRaises(FileExistsError):
            store.write_manifest(conflicting)

        self.assertEqual(store.read_manifest(), manifest)

    def test_same_parsed_run_and_manifest_reuses_verified_records(self) -> None:
        first = self._run("first")
        reuse_path = self.canonical_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        reused = self._run("reuse", reuse_manifest_path=reuse_path)

        self.assertEqual(reused["status"], "complete")
        self.assertEqual(reused["reuse_count"], 2)
        self.assertEqual(reused["reproject_count"], 0)
        self.assertEqual(
            [record["record_id"] for record in reused["records"]],
            [record["record_id"] for record in first["records"]],
        )

    def test_serialized_parsed_identity_component_order_is_preserved(self) -> None:
        parsed_manifest = json.loads(self.parsed_manifest.read_text(encoding="utf-8"))
        record_entry = parsed_manifest["records"][0]
        parsed_path = Path(record_entry["record_path"])
        parsed_value = json.loads(parsed_path.read_text(encoding="utf-8"))
        expected_components = {
            "source": "mihoyo_obc",
            "content_id": "one",
            "locale": "zh-cn",
        }
        parsed_value["identity"]["components"] = expected_components
        body = json.dumps(parsed_value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        parsed_path.write_bytes(body)
        record_entry["record_sha256"] = hashlib.sha256(body).hexdigest()
        self.parsed_manifest.write_text(json.dumps(parsed_manifest), encoding="utf-8")

        from genshin_corpus.canonical.pipeline import project_parsed_detail as actual_projector

        captured: dict[str, object] = {}

        def capture_projected_record(detail: object, **kwargs: object) -> object:
            record = actual_projector(detail, **kwargs)
            captured[record.parsed_identity.key] = record
            return record

        with mock.patch(
            "genshin_corpus.canonical.pipeline.project_parsed_detail",
            side_effect=capture_projected_record,
        ):
            self._run("identity-order")

        canonical_record = captured["mihoyo_obc:zh-cn:one"]
        self.assertEqual(
            canonical_record.parsed_identity.components,
            tuple(expected_components.items()),
        )

    def test_changed_canonical_dependency_reprojects_instead_of_reusing(self) -> None:
        self._run("first")
        reuse_path = self.canonical_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        changed = self._run(
            "versions-changed",
            reuse_manifest_path=reuse_path,
            versions=CanonicalVersions(transform_version="obc-modules-as-sections-0.1-fixture-v2"),
        )

        self.assertEqual(changed["reuse_count"], 0)
        self.assertEqual(changed["reproject_count"], 2)

    def test_corrupt_stored_canonical_record_is_reprojected_not_reused(self) -> None:
        first = self._run("first")
        corrupt = Path(first["records"][1]["canonical_record_path"])
        corrupt.write_bytes(b"not canonical json")
        reuse_path = self.canonical_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"

        result = self._run("corrupt-prior", reuse_manifest_path=reuse_path)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["reuse_count"], 1)
        self.assertEqual(result["reproject_count"], 1)

    def test_structurally_malformed_stored_record_is_not_reused(self) -> None:
        first = self._run("first")
        corrupt = Path(first["records"][1]["canonical_record_path"])
        value = json.loads(corrupt.read_text(encoding="utf-8"))
        value["parsed_identity"] = []
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        corrupt.write_bytes(body)
        reuse_path = self.canonical_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        prior = json.loads(reuse_path.read_text(encoding="utf-8"))
        prior["records"][1]["canonical_record_sha256"] = hashlib.sha256(body).hexdigest()
        reuse_path.write_text(json.dumps(prior), encoding="utf-8")

        result = self._run("malformed-prior", reuse_manifest_path=reuse_path)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["reuse_count"], 1)
        self.assertEqual(result["reproject_count"], 1)

    def test_corrupt_completed_same_run_is_rejected_without_rewrite(self) -> None:
        first = self._run("first")
        corrupt = Path(first["records"][1]["canonical_record_path"])
        corrupt.write_bytes(b"not canonical json")

        with self.assertRaisesRegex(FileExistsError, "not reusable"):
            self._run("first")

        manifest_path = self.canonical_root / "mihoyo_obc" / "zh-cn" / "first" / "metadata" / "manifest.json"
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), first)

    def test_parsed_record_hash_mismatch_is_incomplete_without_fabricated_record(self) -> None:
        parsed_manifest = json.loads(self.parsed_manifest.read_text(encoding="utf-8"))
        corrupt_path = Path(parsed_manifest["records"][1]["record_path"])
        corrupt_path.write_bytes(b"tampered Parsed record")
        first = self._run("first")

        self.assertEqual(first["status"], "incomplete")
        self.assertEqual(first["accounted_record_count"], 1)
        self.assertEqual(first["input_integrity_failure_count"], 1)
        self.assertEqual(first["input_integrity_failures"][0]["code"], "PARSED_RECORD_SHA256_MISMATCH")
        self.assertNotIn("blocked_integrity", [record["canonical_status"] for record in first["records"]])

    def test_missing_or_unreadable_parsed_record_is_incomplete_without_fabrication(self) -> None:
        original_manifest = self.parsed_manifest.read_text(encoding="utf-8")
        for label, replacement, expected_code in (
            ("missing", str(self.root / "missing-record.json"), "PARSED_RECORD_MISSING"),
            ("unreadable", str(self.root), "PARSED_RECORD_READ_ERROR"),
        ):
            with self.subTest(label=label):
                parsed_manifest = json.loads(self.parsed_manifest.read_text(encoding="utf-8"))
                parsed_manifest["records"][1]["record_path"] = replacement
                self.parsed_manifest.write_text(json.dumps(parsed_manifest), encoding="utf-8")

                result = self._run(label)

                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["accounted_record_count"], 1)
                self.assertEqual(result["input_integrity_failure_count"], 1)
                self.assertEqual(result["input_integrity_failures"][0]["code"], expected_code)
                self.assertEqual(result["counts"]["blocked_integrity"], 0)
                self.parsed_manifest.write_text(original_manifest, encoding="utf-8")

    def test_blocked_parsed_observation_is_projected_without_synthesized_source_identity(self) -> None:
        raw_manifest_path = self.raw_root / "metadata" / "manifest.json"
        raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
        second_metadata = Path(raw_manifest["paths"]["details:two"]["metadata"])
        second_value = json.loads(second_metadata.read_text(encoding="utf-8"))
        second_value["sha256"] = "0" * 64
        second_metadata.write_text(json.dumps(second_value), encoding="utf-8")
        blocked_root = self.root / "blocked-parsed"
        OBCParsedRunPipeline(
            raw_run_root=self.raw_root,
            output_root=blocked_root,
            parsed_run_id="blocked-parsed",
        ).run()
        blocked_manifest = blocked_root / "mihoyo_obc" / "zh-cn" / "blocked-parsed" / "metadata" / "manifest.json"

        result = CanonicalRunPipeline(
            parsed_manifest_path=blocked_manifest,
            output_root=self.canonical_root,
            canonical_run_id="blocked-canonical",
        ).run()

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"]["blocked_integrity"], 1)
        blocked = next(record for record in result["records"] if record["canonical_status"] == "blocked_integrity")
        value = json.loads(Path(blocked["canonical_record_path"]).read_text(encoding="utf-8"))
        self.assertIsNone(value["source_identity"])
        self.assertEqual(value["blocked_reason"], "RAW_METADATA_INTEGRITY_MISMATCH")
        self.assertEqual(value["record_metadata"]["content_id"], "two")
        self.assertEqual(value["record_metadata"]["channel_memberships"], ["43"])
        membership = value["metadata_lineage"]["channel_memberships"]
        self.assertEqual(membership["evidence_scope"], "parsed_dependency")
        self.assertEqual(membership["raw_refs"], [])

    def test_inconsistent_complete_parsed_manifest_is_rejected_before_canonical_output(self) -> None:
        value = json.loads(self.parsed_manifest.read_text(encoding="utf-8"))
        value["accounted_detail_count"] = 1
        self.parsed_manifest.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "accounting"):
            self._run("inconsistent")

        output_manifest = self.canonical_root / "mihoyo_obc" / "zh-cn" / "inconsistent" / "metadata" / "manifest.json"
        self.assertFalse(output_manifest.exists())


if __name__ == "__main__":
    unittest.main()
