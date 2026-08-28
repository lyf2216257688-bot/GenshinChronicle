from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unittest

from genshin_corpus.retrieval.experiments import _slice_metrics
from genshin_corpus.retrieval.lexical import _build_index, _rank, analyze, analyze_with_bigrams, evaluate_lexical_arm
from genshin_corpus.retrieval.representations import (
    RetrievalRepresentationError,
    build_retrieval_documents,
    document_covers_location,
    iter_retrieval_documents,
    load_retrieval_documents,
)


class RetrievalRepresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("data/retrieval/.representation-test")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.record = {
            "record_id": "record-1",
            "record_metadata": {"name": "测试角色"},
            "sections": [{
                "ordinal": 0,
                "source_metadata": {"name": "剧情"},
                "component_contexts": [{
                    "observation_key": "component-1",
                    "source_component_id": "interactive_dialogue",
                }],
                "units": [{
                    "ordinal": 0,
                    "parent_component_key": "component-1",
                    "kind": "rich_text",
                    "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/0", "raw_refs": []},
                    "value": {"normalized_text": "重复文字"},
                }, {
                    "ordinal": 1,
                    "parent_component_key": "component-1",
                    "kind": "structured_observation",
                    "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/1", "raw_refs": []},
                    "value": {"decoded": {"属性": [{"名称": "生日", "值": "八月"}]}},
                }, {
                    "ordinal": 2,
                    "parent_component_key": "component-1",
                    "kind": "dialogue_graph",
                    "lineage": {"parsed_json_pointer": "/modules/0/components/0/units/2", "raw_refs": []},
                    "value": {"groups": [{
                        "ordering": 0,
                        "nodes": [{"source_id": "a", "option": "选择"}, {"source_id": "b", "dialogue": "回应"}],
                        "edges": [{"parent_id": "a", "child_id": "b"}],
                    }]},
                }],
            }],
        }
        body = json.dumps(self.record, ensure_ascii=False).encode("utf-8")
        self.record_path = self.root / "record.json"
        self.record_path.write_bytes(body)
        self.canonical_manifest = self.root / "canonical-manifest.json"
        self.canonical_manifest.write_text(json.dumps({
            "status": "complete",
            "canonical_run_id": "canonical-fixture",
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

    def test_generation_is_deterministic_and_explicitly_traceable(self) -> None:
        first = list(iter_retrieval_documents(self.record))
        second = list(iter_retrieval_documents(self.record))
        self.assertEqual(first, second)
        arms = {document["arm"] for document in first}
        self.assertEqual(arms, {"naked_leaf", "contextualized_leaf", "structured_path_value", "dialogue_graph_local"})
        self.assertEqual(len({document["document_id"] for document in first}), len(first))
        for document in first:
            self.assertEqual(document["source_coverage"][0]["record_id"], "record-1")
            self.assertIn("lineage", document["source_coverage"][0])
        changed = json.loads(json.dumps(self.record, ensure_ascii=False))
        changed["sections"][0]["units"][0]["value"]["normalized_text"] = "不同文字"
        changed_leaf = next(document for document in iter_retrieval_documents(changed) if document["arm"] == "naked_leaf")
        original_leaf = next(document for document in first if document["arm"] == "naked_leaf")
        self.assertNotEqual(original_leaf["document_id"], changed_leaf["document_id"])

    def test_gold_matching_requires_explicit_unit_decoded_and_dialogue_coverage(self) -> None:
        documents = list(iter_retrieval_documents(self.record))
        naked = next(document for document in documents if document["arm"] == "naked_leaf")
        structured = next(document for document in documents if document["arm"] == "structured_path_value")
        dialogue = next(document for document in documents if document["arm"] == "dialogue_graph_local")
        self.assertFalse(document_covers_location(naked, {"record_id": "record-1", "section_ordinal": 0, "unit_ordinal": 1}))
        self.assertTrue(document_covers_location(structured, {"record_id": "record-1", "section_ordinal": 0, "component_observation_key": "component-1", "unit_ordinal": 1, "decoded_json_pointer": "/属性/0/值"}))
        self.assertFalse(document_covers_location(naked, {"record_id": "record-1", "section_ordinal": 0, "unit_ordinal": 1, "decoded_json_pointer": "/属性/0/值"}))
        self.assertTrue(document_covers_location(dialogue, {"record_id": "record-1", "section_ordinal": 0, "unit_ordinal": 2, "dialogue": {"group_ordering": 0, "node_source_id": "b", "edge": {"parent_id": "a", "child_id": "b"}}}))
        self.assertFalse(document_covers_location(naked, {"record_id": "record-1", "section_ordinal": 0, "unit_ordinal": 2, "dialogue": {"group_ordering": 0, "node_source_id": "b", "edge": {"parent_id": "a", "child_id": "b"}}}))

    def test_run_artifact_is_integrity_checked_and_conflict_safe(self) -> None:
        output = self.root / "derived"
        first = build_retrieval_documents(self.canonical_manifest, output)
        second = build_retrieval_documents(self.canonical_manifest, output)
        self.assertEqual(first, second)
        self.assertEqual(first["verified_canonical_record_count"], 1)
        self.assertEqual(len(load_retrieval_documents(output / "metadata" / "manifest.json", "structured_path_value")), 1)
        self.record["record_metadata"]["name"] = "不同名称"
        body = json.dumps(self.record, ensure_ascii=False).encode("utf-8")
        self.record_path.write_bytes(body)
        manifest = json.loads(self.canonical_manifest.read_text(encoding="utf-8"))
        manifest["records"][0]["canonical_record_sha256"] = hashlib.sha256(body).hexdigest()
        self.canonical_manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(FileExistsError):
            build_retrieval_documents(self.canonical_manifest, output)

    def test_rejects_malformed_or_tampered_run_artifact(self) -> None:
        output = self.root / "derived"
        build_retrieval_documents(self.canonical_manifest, output)
        artifact = output / "artifacts" / "naked_leaf.jsonl.gz"
        artifact.write_bytes(b"tampered")
        with self.assertRaises(RetrievalRepresentationError):
            load_retrieval_documents(output / "metadata" / "manifest.json", "naked_leaf")

    def test_lexical_ranking_and_metrics_are_deterministic_and_not_text_identity(self) -> None:
        documents = list(iter_retrieval_documents(self.record))
        contextualized = [document for document in documents if document["arm"] == "contextualized_leaf"]
        benchmark = {"queries": [{
            "query_id": "q1",
            "query": "测试角色",
            "evidence": [{"evidence_id": "gold", "relevance": "direct", "location": {"record_id": "record-1", "section_ordinal": 0, "unit_ordinal": 0}}],
            "primary_sufficient_evidence_sets": [["gold"]],
        }]}
        first = evaluate_lexical_arm(contextualized, benchmark)
        second = evaluate_lexical_arm(contextualized, benchmark)
        self.assertEqual(first["analyzer_version"], second["analyzer_version"])
        self.assertEqual(first["document_count"], second["document_count"])
        self.assertEqual(
            [{key: value for key, value in query.items() if key != "latency_ms"} for query in first["queries"]],
            [{key: value for key, value in query.items() if key != "latency_ms"} for query in second["queries"]],
        )
        self.assertEqual(first["queries"][0]["first_positive_rank"], 1)
        duplicate = dict(contextualized[0])
        duplicate["document_id"] = "different-occurrence"
        duplicate["source_coverage"] = [dict(duplicate["source_coverage"][0], unit_ordinal=99)]
        self.assertFalse(document_covers_location(duplicate, benchmark["queries"][0]["evidence"][0]["location"]))

    def test_equal_score_ranking_uses_source_coverage_not_derivative_identity(self) -> None:
        def document(
            document_id: str,
            record_id: str,
            *,
            representation_version: str = "representation-a",
            unit_ordinal: int = 0,
        ) -> dict[str, object]:
            return {
                "document_id": document_id,
                "representation_version": representation_version,
                "text": "同分词",
                "source_coverage": [{
                    "record_id": record_id,
                    "section_ordinal": 0,
                    "component_observation_key": "component",
                    "unit_ordinal": unit_ordinal,
                }],
            }

        documents = [document("id-z", "record-z"), document("id-a", "record-a")]
        tokenized, lengths, _ = _build_index(documents)
        ranked, _ = _rank(documents, tokenized, lengths, "同分词")
        self.assertEqual([item["document_id"] for item in ranked], ["id-a", "id-z"])

        identity_changed = [
            document("another-z", "record-z", representation_version="representation-z"),
            document("another-a", "record-a", representation_version="representation-a-different"),
        ]
        changed_tokens, changed_lengths, _ = _build_index(identity_changed)
        changed_ranked, _ = _rank(identity_changed, changed_tokens, changed_lengths, "同分词")
        self.assertEqual(
            [item["source_coverage"] for item in ranked],
            [item["source_coverage"] for item in changed_ranked],
        )

    def test_equal_complete_coverage_preserves_input_occurrence_order(self) -> None:
        first = {
            "document_id": "id-z",
            "text": "同分词",
            "source_coverage": [{
                "record_id": "record-1",
                "section_ordinal": 0,
                "component_observation_key": "component",
                "unit_ordinal": 0,
            }],
        }
        second = dict(first, document_id="id-a")
        documents = [first, second]
        tokenized, lengths, _ = _build_index(documents)
        ranked, _ = _rank(documents, tokenized, lengths, "同分词")
        self.assertEqual([item["document_id"] for item in ranked], ["id-z", "id-a"])

    def test_bigram_analyzer_is_deterministic_and_version_distinct(self) -> None:
        self.assertEqual(analyze_with_bigrams("角色ABC"), ["角", "色", "角色", "abc"])
        self.assertEqual(analyze_with_bigrams("角色ABC"), analyze_with_bigrams("角色ABC"))
        self.assertNotEqual(analyze("角色ABC"), analyze_with_bigrams("角色ABC"))
        self.assertNotIn("角A", analyze_with_bigrams("角A色"))
        self.assertNotIn("角色", analyze_with_bigrams("角A色"))
        self.assertNotIn("角色", analyze_with_bigrams("角·色"))

    def test_diagnostic_slice_and_hard_negative_metrics_use_coverage(self) -> None:
        documents = [
            {
                "document_id": "positive",
                "text": "角色故事",
                "source_coverage": [{"record_id": "record-1", "section_ordinal": 0, "component_observation_key": "c", "unit_ordinal": 0}],
            },
            {
                "document_id": "negative",
                "text": "角色攻略",
                "source_coverage": [{"record_id": "record-1", "section_ordinal": 1, "component_observation_key": "c", "unit_ordinal": 0}],
            },
        ]
        benchmark = {"queries": [{
            "query_id": "q1",
            "query": "角色",
            "slices": ["wrong_role_contamination", "character_narrative"],
            "evidence": [
                {"evidence_id": "gold", "relevance": "direct", "location": {"record_id": "record-1", "section_ordinal": 0, "unit_ordinal": 0}},
                {"evidence_id": "hn", "relevance": "hard_negative", "location": {"record_id": "record-1", "section_ordinal": 1, "unit_ordinal": 0}},
            ],
            "primary_sufficient_evidence_sets": [["gold"]],
        }]}
        result = evaluate_lexical_arm(documents, benchmark)
        self.assertEqual(result["query_count"], 1)
        self.assertEqual(result["metrics"]["hard_negative_top10_query_count"], 1)
        self.assertEqual(result["queries"][0]["first_positive_rank"], 1)
        self.assertEqual(result["queries"][0]["hard_negative_ranks"], {"hn": 2})
        self.assertEqual(_slice_metrics(result, benchmark)["wrong_role_contamination"]["query_count"], 1)


if __name__ == "__main__":
    unittest.main()
