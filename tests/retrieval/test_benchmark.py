from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unittest
import gzip

from genshin_corpus.retrieval.benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkValidationError,
    resolve_benchmark_locations,
    validate_benchmark,
)


class BenchmarkContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.benchmark-contract-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.record_path = self.root / "record.json"
        self.record = {
            "record_id": "record-1",
            "lineage": {
                "parsed_json_pointer": "",
                "raw_refs": [{"artifact_path": "record.json"}],
            },
            "sections": [{
                "ordinal": 0,
                "lineage": {
                    "parsed_json_pointer": "/modules/0",
                    "raw_refs": [{"artifact_path": "section.json"}],
                },
                "component_contexts": [{
                    "observation_key": "component-1",
                    "lineage": {
                        "parsed_json_pointer": "/modules/0/components/0",
                        "raw_refs": [{"artifact_path": "component.json"}],
                    },
                }],
                "units": [{
                    "ordinal": 0,
                    "parent_component_key": "component-1",
                    "kind": "structured_observation",
                    "lineage": {
                        "parsed_json_pointer": "/modules/0/components/0/units/0",
                        "raw_refs": [{"artifact_path": "detail.json", "artifact_sha256": "a" * 64}],
                    },
                    "value": {
                        "decoded": {"attr": [{"key": "生日", "value": ["8月22日"]}]},
                    },
                }, {
                    "ordinal": 1,
                    "parent_component_key": "component-1",
                    "kind": "dialogue_graph",
                    "lineage": {
                        "parsed_json_pointer": "/modules/0/components/0/units/1",
                        "raw_refs": [{"artifact_path": "dialogue.json"}],
                    },
                    "value": {"groups": [{
                        "ordering": 0,
                        "nodes": [
                            {"source_id": "a"},
                            {"source_id": "b"},
                            {"source_id": "c"},
                        ],
                        "edges": [{"parent_id": "a", "child_id": "b"}],
                    }]},
                }, {
                    "ordinal": 2,
                    "parent_component_key": "component-1",
                    "kind": "rich_text",
                    "lineage": {
                        "parsed_json_pointer": "/modules/0/components/0/units/2",
                        "raw_refs": [{"artifact_path": "rich.json"}],
                    },
                    "value": {"normalized_text": "自然改写的来源文本"},
                }],
            }],
        }
        body = json.dumps(self.record, ensure_ascii=False).encode("utf-8")
        self.record_path.write_bytes(body)
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps({
            "status": "complete",
            "records": [{
                "record_id": "record-1",
                "canonical_record_path": str(self.record_path),
                "canonical_record_sha256": hashlib.sha256(body).hexdigest(),
            }],
        }), encoding="utf-8")
        self.retrieval_manifest_path = self.root / "retrieval-manifest.json"
        artifacts = {}
        locations = {
            "structured_path_value": {"record_id": "record-1", "section_ordinal": 0, "component_observation_key": "component-1", "unit_ordinal": 0, "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/0", "raw_refs": [{"artifact_path": "detail.json", "artifact_sha256": "a" * 64}]}, "decoded_json_pointers": ["/attr/0/value/0"]},
            "dialogue_graph_local": {"record_id": "record-1", "section_ordinal": 0, "component_observation_key": "component-1", "unit_ordinal": 1, "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/1", "raw_refs": [{"artifact_path": "dialogue.json"}]}, "dialogue": {"group_ordering": 0, "node_source_ids": ["a", "b"], "edges": [{"parent_id": "a", "child_id": "b"}]}},
            "naked_leaf": {"record_id": "record-1", "section_ordinal": 0, "component_observation_key": "component-1", "unit_ordinal": 2, "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/2", "raw_refs": [{"artifact_path": "rich.json"}]}},
        }
        for arm, coverage in locations.items():
            path = self.root / f"{arm}.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps({"document_id": arm, "arm": arm, "source_coverage": [coverage]}, ensure_ascii=False) + "\n")
            artifact_body = path.read_bytes()
            artifacts[arm] = {"path": str(path), "sha256": hashlib.sha256(artifact_body).hexdigest(), "document_count": 1}
        self.retrieval_manifest_path.write_text(json.dumps({"status": "complete", "artifacts": artifacts}), encoding="utf-8")

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _benchmark(self) -> dict:
        return {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "benchmark_id": "fixture",
            "queries": [{
                "query_id": "birthday",
                "query": "生日？",
                "benchmark_track": "both",
                "slices": ["structured_attribute_value"],
                "evidence": [{
                    "evidence_id": "field",
                    "relevance": "direct",
                    "location": {
                        "record_id": "record-1",
                        "section_ordinal": 0,
                        "component_observation_key": "component-1",
                        "unit_ordinal": 0,
                        "parsed_json_pointer": "/modules/0/components/0/units/0",
                        "decoded_json_pointer": "/attr/0/value/0",
                        "raw_ref": {"artifact_path": "detail.json"},
                    },
                }],
                "primary_sufficient_evidence_sets": [["field"]],
            }],
        }

    def test_validates_and_resolves_existing_canonical_evidence(self) -> None:
        benchmark = self._benchmark()
        validate_benchmark(benchmark)
        self.assertEqual(
            resolve_benchmark_locations(benchmark, self.manifest_path),
            {"query_count": 1, "evidence_location_count": 1, "record_count": 1},
        )

    def test_rejects_non_resolving_structured_pointer(self) -> None:
        benchmark = self._benchmark()
        benchmark["queries"][0]["evidence"][0]["location"]["decoded_json_pointer"] = "/missing"
        with self.assertRaisesRegex(BenchmarkValidationError, "does not resolve"):
            resolve_benchmark_locations(benchmark, self.manifest_path)

    def test_rejects_unknown_location_dialogue_and_edge_selectors(self) -> None:
        cases = (
            ("location", "unit_ordina", 0),
            ("dialogue", "node_source_typo", "b"),
            ("edge", "edge_typo", "x"),
        )
        for scope, field, value in cases:
            with self.subTest(scope=scope, field=field):
                benchmark = self._benchmark()
                location = benchmark["queries"][0]["evidence"][0]["location"]
                if scope == "location":
                    location[field] = value
                else:
                    location.update({
                        "unit_ordinal": 1,
                        "parsed_json_pointer": "/modules/0/components/0/units/1",
                        "raw_ref": {"artifact_path": "dialogue.json"},
                        "decoded_json_pointer": None,
                        "dialogue": {
                            "group_ordering": 0,
                            "node_source_id": "b",
                            "edge": {"parent_id": "a", "child_id": "b"},
                        },
                    })
                    if scope == "dialogue":
                        location["dialogue"][field] = value
                    else:
                        location["dialogue"]["edge"][field] = value
                with self.assertRaisesRegex(BenchmarkValidationError, "unsupported selector field"):
                    validate_benchmark(benchmark)

    def test_empty_decoded_pointer_resolves_root_and_rejects_missing_decoded(self) -> None:
        benchmark = self._benchmark()
        location = benchmark["queries"][0]["evidence"][0]["location"]
        location["decoded_json_pointer"] = ""
        resolve_benchmark_locations(benchmark, self.manifest_path)
        location.update({
            "unit_ordinal": 1,
            "parsed_json_pointer": "/modules/0/components/0/units/1",
            "raw_ref": {"artifact_path": "dialogue.json"},
        })
        with self.assertRaisesRegex(BenchmarkValidationError, "does not resolve"):
            resolve_benchmark_locations(benchmark, self.manifest_path)

    def test_rejects_boolean_structural_ordinals(self) -> None:
        for field in ("section_ordinal", "unit_ordinal"):
            with self.subTest(field=field):
                benchmark = self._benchmark()
                benchmark["queries"][0]["evidence"][0]["location"][field] = True
                with self.assertRaisesRegex(BenchmarkValidationError, "non-negative integer"):
                    validate_benchmark(benchmark)

    def test_rejects_evidence_set_with_unknown_identifier(self) -> None:
        benchmark = self._benchmark()
        benchmark["queries"][0]["primary_sufficient_evidence_sets"] = [["missing"]]
        with self.assertRaisesRegex(BenchmarkValidationError, "primary evidence"):
            validate_benchmark(benchmark)

    def test_rejects_deeper_coordinates_without_required_parent_scope(self) -> None:
        cases = (
            ("component_observation_key", "component-1", "requires section_ordinal"),
            ("unit_ordinal", 0, "requires section_ordinal"),
            ("decoded_json_pointer", "/attr/0", "requires unit_ordinal"),
            ("dialogue", {"group_ordering": 0}, "requires unit_ordinal"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                benchmark = self._benchmark()
                location = benchmark["queries"][0]["evidence"][0]["location"]
                for key in ("section_ordinal", "component_observation_key", "unit_ordinal", "decoded_json_pointer"):
                    location.pop(key, None)
                location[field] = value
                with self.assertRaisesRegex(BenchmarkValidationError, message):
                    resolve_benchmark_locations(benchmark, self.manifest_path)

    def test_resolves_lineage_selectors_at_record_section_context_and_unit_scope(self) -> None:
        locations = (
            ({"record_id": "record-1", "parsed_json_pointer": "", "raw_ref": {"artifact_path": "record.json"}}, "record.json"),
            ({"record_id": "record-1", "section_ordinal": 0, "parsed_json_pointer": "/modules/0", "raw_ref": {"artifact_path": "section.json"}}, "section.json"),
            ({"record_id": "record-1", "section_ordinal": 0, "component_observation_key": "component-1", "parsed_json_pointer": "/modules/0/components/0", "raw_ref": {"artifact_path": "component.json"}}, "component.json"),
            (self._benchmark()["queries"][0]["evidence"][0]["location"], "detail.json"),
        )
        for location, artifact_path in locations:
            with self.subTest(location=location):
                benchmark = self._benchmark()
                benchmark["queries"][0]["evidence"][0]["location"] = location
                resolve_benchmark_locations(benchmark, self.manifest_path)
                wrong = dict(location)
                wrong["raw_ref"] = {"artifact_path": "wrong.json"}
                benchmark["queries"][0]["evidence"][0]["location"] = wrong
                with self.assertRaisesRegex(BenchmarkValidationError, "RawRef does not match"):
                    resolve_benchmark_locations(benchmark, self.manifest_path)
                wrong["raw_ref"] = {"artifact_path": artifact_path}
                wrong["parsed_json_pointer"] = "/wrong"
                with self.assertRaisesRegex(BenchmarkValidationError, "Parsed pointer does not match"):
                    resolve_benchmark_locations(benchmark, self.manifest_path)

    def test_dialogue_node_and_edge_must_be_coherent(self) -> None:
        benchmark = self._benchmark()
        location = benchmark["queries"][0]["evidence"][0]["location"]
        location.update({
            "unit_ordinal": 1,
            "parsed_json_pointer": "/modules/0/components/0/units/1",
            "raw_ref": {"artifact_path": "dialogue.json"},
            "decoded_json_pointer": None,
            "dialogue": {
                "group_ordering": 0,
                "node_source_id": "b",
                "edge": {"parent_id": "a", "child_id": "b"},
            },
        })
        resolve_benchmark_locations(benchmark, self.manifest_path)
        location["dialogue"]["node_source_id"] = "c"
        with self.assertRaisesRegex(BenchmarkValidationError, "does not participate"):
            resolve_benchmark_locations(benchmark, self.manifest_path)

    def test_hard_negative_cannot_be_sufficient_but_multi_evidence_remains_valid(self) -> None:
        for field, message in (
            ("primary_sufficient_evidence_sets", "primary sufficient evidence"),
            ("alternative_sufficient_evidence_sets", "alternative sufficient evidence"),
        ):
            with self.subTest(field=field):
                benchmark = self._benchmark()
                benchmark["queries"][0]["evidence"].append({
                    "evidence_id": "negative",
                    "relevance": "hard_negative",
                    "location": self._benchmark()["queries"][0]["evidence"][0]["location"],
                })
                benchmark["queries"][0][field] = [["negative"]]
                with self.assertRaisesRegex(BenchmarkValidationError, message):
                    validate_benchmark(benchmark)
        benchmark = self._benchmark()
        benchmark["queries"][0]["evidence"][0]["relevance"] = "supporting"
        benchmark["queries"][0]["evidence"].append({
            "evidence_id": "statement",
            "relevance": "direct",
            "location": self._benchmark()["queries"][0]["evidence"][0]["location"],
        })
        benchmark["queries"][0]["primary_sufficient_evidence_sets"] = [["field", "statement"]]
        validate_benchmark(benchmark)

    def test_w5_typed_eligibility_rejects_mislabeled_or_uncovered_evidence(self) -> None:
        cases = (
            ("structured", "structured_attribute_value", 2, {}, "structured slice requires"),
            ("dialogue-kind", "dialogue_branch", 0, {"dialogue": {"group_ordering": 0, "node_source_id": "b", "edge": {"parent_id": "a", "child_id": "b"}}}, "dialogue group does not exist"),
            ("dialogue-edge", "dialogue_branch", 1, {"dialogue": {"group_ordering": 0, "node_source_id": "b"}}, "requires a dialogue edge"),
            ("paraphrase", "semantic_paraphrase", 0, {}, "semantic_paraphrase requires"),
        )
        for name, slice_name, ordinal, extra, message in cases:
            with self.subTest(name=name):
                benchmark = self._benchmark()
                benchmark["scope"] = {"eligibility_version": "phase04-benchmark-evidence-eligibility-0.1"}
                location = benchmark["queries"][0]["evidence"][0]["location"]
                location.update({"unit_ordinal": ordinal, "parsed_json_pointer": f"/modules/0/components/0/units/{ordinal}"})
                location.update(extra)
                location.pop("decoded_json_pointer", None)
                location.pop("raw_ref", None)
                benchmark["queries"][0]["slices"] = [slice_name]
                if slice_name == "semantic_paraphrase":
                    pass
                elif ordinal == 1:
                    location["raw_ref"] = {"artifact_path": "dialogue.json"}
                with self.assertRaisesRegex(BenchmarkValidationError, message):
                    resolve_benchmark_locations(benchmark, self.manifest_path, self.retrieval_manifest_path)

    def test_w5_typed_eligibility_accepts_real_kind_and_r02_coverage(self) -> None:
        cases = (
            ("structured_attribute_value", 0, {"decoded_json_pointer": "/attr/0/value/0"}),
            ("dialogue_branch", 1, {"dialogue": {"group_ordering": 0, "node_source_id": "b", "edge": {"parent_id": "a", "child_id": "b"}}}),
            ("semantic_paraphrase", 2, {}),
        )
        for slice_name, ordinal, extra in cases:
            with self.subTest(slice_name=slice_name):
                benchmark = self._benchmark()
                benchmark["scope"] = {"eligibility_version": "phase04-benchmark-evidence-eligibility-0.1"}
                location = benchmark["queries"][0]["evidence"][0]["location"]
                location.update({"unit_ordinal": ordinal, "parsed_json_pointer": f"/modules/0/components/0/units/{ordinal}"})
                location.update(extra)
                if slice_name != "structured_attribute_value":
                    location.pop("decoded_json_pointer", None)
                if slice_name == "semantic_paraphrase":
                    location["raw_ref"] = {"artifact_path": "rich.json"}
                elif slice_name == "dialogue_branch":
                    location["raw_ref"] = {"artifact_path": "dialogue.json"}
                benchmark["queries"][0]["slices"] = [slice_name]
                self.assertEqual(resolve_benchmark_locations(benchmark, self.manifest_path, self.retrieval_manifest_path)["evidence_location_count"], 1)

    def test_declared_eligibility_requires_known_version_and_retrieval_manifest(self) -> None:
        benchmark = self._benchmark()
        benchmark["scope"] = {"eligibility_version": "phase04-benchmark-evidence-eligibility-0.1"}
        with self.assertRaisesRegex(BenchmarkValidationError, "requires a Retrieval manifest"):
            resolve_benchmark_locations(benchmark, self.manifest_path)
        benchmark["scope"]["eligibility_version"] = "unknown-eligibility-version"
        with self.assertRaisesRegex(BenchmarkValidationError, "unsupported benchmark evidence eligibility_version"):
            resolve_benchmark_locations(benchmark, self.manifest_path, self.retrieval_manifest_path)
        benchmark.pop("scope")
        self.assertEqual(resolve_benchmark_locations(benchmark, self.manifest_path)["evidence_location_count"], 1)

    def test_w5_rejects_hard_negative_at_positive_unit_scope_and_duplicate_positives(self) -> None:
        benchmark = self._benchmark()
        benchmark["queries"][0]["evidence"].append({
            "evidence_id": "negative", "relevance": "hard_negative", "location": dict(benchmark["queries"][0]["evidence"][0]["location"]),
        })
        with self.assertRaisesRegex(BenchmarkValidationError, "different Canonical scope"):
            validate_benchmark(benchmark)
        benchmark = self._benchmark()
        benchmark["unique_positive_locations_required"] = True
        copied = json.loads(json.dumps(benchmark["queries"][0]))
        copied["query_id"] = "duplicate"
        benchmark["queries"].append(copied)
        with self.assertRaisesRegex(BenchmarkValidationError, "reuses an existing deepest"):
            validate_benchmark(benchmark)


if __name__ == "__main__":
    unittest.main()
