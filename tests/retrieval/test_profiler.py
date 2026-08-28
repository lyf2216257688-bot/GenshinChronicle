from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unittest

from genshin_corpus.retrieval.profiler import CanonicalProfileError, profile_canonical_run


class CanonicalProfilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.canonical-profile-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.record_path = self.root / "record.json"
        record = {
            "record_id": "record-1",
            "status": "canonical",
            "lineage": {"parsed_json_pointer": "", "evidence_scope": "inherited_parent_raw", "raw_refs": []},
            "record_metadata": {
                "channel_memberships": ["43"],
                "source_template_layout": {
                    "tab": [{
                        "tab_name": "页签1",
                        "module_group": [{"name": "剧情", "layout": "l1r1"}],
                    }],
                },
            },
            "sections": [{
                "ordinal": 0,
                "lineage": {"parsed_json_pointer": "/modules/0", "evidence_scope": "direct_raw", "raw_refs": [{"artifact_path": "detail.json"}]},
                "source_metadata": {"name": "剧情对话"},
                "component_contexts": [{
                    "observation_key": "dialogue-component",
                    "source_component_id": "interactive_dialogue",
                    "unit_count": 2,
                    "lineage": {"parsed_json_pointer": "/modules/0/components/0", "evidence_scope": "direct_raw", "raw_refs": [{"artifact_path": "detail.json"}]},
                    "diagnostics": [{"code": "DIALOGUE_MULTIPLE_PARENT"}],
                }],
                "units": [{
                    "ordinal": 0,
                    "kind": "rich_text",
                    "parent_component_key": "dialogue-component",
                    "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/0", "evidence_scope": "direct_raw", "raw_refs": [{"artifact_path": "detail.json"}]},
                    "diagnostics": [],
                    "value": {"normalized_text": "重复文本"},
                }, {
                    "ordinal": 1,
                    "kind": "dialogue_graph",
                    "parent_component_key": "dialogue-component",
                    "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/1", "evidence_scope": "direct_raw", "raw_refs": [{"artifact_path": "detail.json"}]},
                    "diagnostics": [],
                    "value": {"groups": [{"ordering": 0, "diagnostics": [], "nodes": [{"option": "选项", "dialogue": "回答"}], "edges": [{"parent_id": "a", "child_id": "b"}]}]},
                }],
            }],
        }
        body = json.dumps(record, ensure_ascii=False).encode("utf-8")
        self.record_path.write_bytes(body)
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps({
            "status": "complete",
            "canonical_run_id": "fixture",
            "source": "mihoyo_obc",
            "locale": "zh-cn",
            "input_record_count": 1,
            "accounted_record_count": 1,
            "input_integrity_failure_count": 0,
            "records": [{
                "record_id": "record-1",
                "canonical_record_path": str(self.record_path),
                "canonical_record_sha256": hashlib.sha256(body).hexdigest(),
            }],
        }), encoding="utf-8")

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def test_reports_aggregate_evidence_without_record_dump(self) -> None:
        profile = profile_canonical_run(self.manifest_path, top_n=5)
        self.assertEqual(profile["accounting"]["records"], 1)
        self.assertEqual(profile["accounting"]["units"], 2)
        self.assertEqual(profile["unit_kinds"], {"dialogue_graph": 1, "rich_text": 1})
        self.assertEqual(profile["accounting"]["dialogue_nodes"], 1)
        self.assertEqual(profile["searchable_views"]["rich_text_normalized_length"]["count"], 1)
        self.assertEqual(profile["top_observed_structure"]["template_module_group_names"], {"distinct_count": 1, "top": [{"value": "剧情", "count": 1}]})
        self.assertNotIn("record-1", json.dumps(profile, ensure_ascii=False))

    def test_rejects_manifest_record_hash_mismatch(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["records"][0]["canonical_record_sha256"] = "0" * 64
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(CanonicalProfileError, "SHA-256 mismatch"):
            profile_canonical_run(self.manifest_path)


if __name__ == "__main__":
    unittest.main()
