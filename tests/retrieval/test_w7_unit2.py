from __future__ import annotations

import gzip
import hashlib
import json
import inspect
import shutil
import unittest
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.w7_unit2 import (
    FAMILY_KEY_SCHEMA_VERSION,
    MAX_GOLD_OCCURRENCES,
    OCCURRENCE_KEY_SCHEMA_VERSION,
    SCIENTIFIC_CONTRACT,
    Unit2Blocked,
    _grams,
    _jaccard_at_least_080,
    _normalise,
    execute_documents,
    family_key,
    occurrence_key,
)


def _raw(content_id: str = "page-1") -> dict[str, object]:
    return {
        "source": "mihoyo_obc", "locale": "zh-cn", "run_id": "run", "content_id": content_id,
        "artifact_kind": "details", "artifact_path": "raw.json", "artifact_sha256": "a" * 64,
        "json_pointer": "/data/page", "embedded_json_pointer": "/rich_text", "source_value_sha256": None,
    }


def _document(index: int, text: str = "这是一个足够长的测试叙事文本，用于验证结构和采样规则。") -> dict[str, object]:
    coverage = {
        "record_id": f"record-{index}", "section_ordinal": 0,
        "component_observation_key": "component-a", "unit_ordinal": 0,
        "lineage": {"evidence_scope": "direct_raw", "parsed_json_pointer": f"/u/{index}", "raw_refs": [_raw(f"page-{index}")], "dependency_locator": None},
    }
    return {"schema_version": "phase04-retrieval-document-0.1", "document_id": f"doc-{index}", "representation_version": "phase04-derived-representation-0.2", "arm": "contextualized_leaf", "text": text, "source_coverage": [coverage], "metadata": {"record_title": "title", "section_name": "section", "component_id": "component-a", "unit_kind": "rich_text"}}


def _same_family_document(index: int, *, content_id: str = "page-1", component_id: str = "component-a", section: int = 0, text: str | None = None) -> dict[str, object]:
    document = _document(index, text or ("entry-" + str(index) + "-" + (chr(0x410 + index % 32) * 24)))
    coverage = document["source_coverage"][0]
    coverage["record_id"] = "shared-record"
    coverage["section_ordinal"] = section
    coverage["component_observation_key"] = "shared-component"
    coverage["unit_ordinal"] = index
    coverage["lineage"]["raw_refs"][0]["content_id"] = content_id
    document["metadata"]["component_id"] = component_id
    document["metadata"]["section_name"] = "shared-section"
    return document


def _projection() -> dict[str, object]:
    return {"schema_version": "p04-w7-v03-identity-only-projection-v1", "dependencies": {"canonical_manifest_sha256": "be2ce30d7cb759a3598b8ac90776abaa01f6db46d6f56603360fcb1e3a66b1e9", "r02_manifest_sha256": "e62cb7ca142f3fddbdb6d109313abf0dfa55b131d45a88c1dee8aac4f6822f56", "contextualized_leaf_artifact_sha256": "297d413b75734dbbc716e9daf157639103e95eccd3f862855ef59a44bff527b9", "representation_version": "phase04-derived-representation-0.2", "arm": "contextualized_leaf", "occurrence_key_schema_version": OCCURRENCE_KEY_SCHEMA_VERSION, "evidence_family_key_schema_version": FAMILY_KEY_SCHEMA_VERSION}, "accounting": {}, "legacy_occurrence_exclusion_keys": [], "legacy_evidence_family_exclusion_keys": []}


class W7Unit2FocusedTests(unittest.TestCase):
    def test_keys_are_exact_canonical_address_hashes(self) -> None:
        address = _document(1)["source_coverage"][0]
        expected = hashlib.sha256(canonical_json_bytes({"schema": OCCURRENCE_KEY_SCHEMA_VERSION, "address": address})).hexdigest()
        self.assertEqual(occurrence_key(address), expected)
        self.assertNotEqual(occurrence_key(address), family_key(address))

    def test_c1_normalization_and_threshold_boundaries(self) -> None:
        self.assertEqual(_normalise(" Ａ\u2003B\n"), "a b")
        self.assertFalse(_jaccard_at_least_080(frozenset("abc"), frozenset("abcde"))[0])
        self.assertTrue(_jaccard_at_least_080(frozenset("abcd"), frozenset("abcde"))[0])
        self.assertTrue(_jaccard_at_least_080(frozenset("abcd"), frozenset("abcde"))[2] == 5)
        self.assertTrue(_jaccard_at_least_080(frozenset("abcd"), frozenset("abcde"))[1] == 4)
        self.assertTrue(_jaccard_at_least_080(frozenset("abcde"), frozenset("abcde"))[0])

    def test_greedy_non_transitive_near_duplicate_survivors(self) -> None:
        from genshin_corpus.retrieval.w7_unit2 import _exact_and_near

        docs = []
        for index, text in enumerate(("abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvw!", "abcdefghijklmno#########"), 1):
            document = _document(index, text)
            docs.append(document)
        projection = _projection()
        result = execute_documents(docs, projection)
        self.assertEqual(sum(result["primary_disposition_counts"].values()), 3)
        self.assertGreaterEqual(len(result["pair_audit"]), 1)

    def test_prefix_join_matches_bruteforce_greedy_reference(self) -> None:
        from genshin_corpus.retrieval.w7_unit2 import _exact_and_near

        texts = [
            "abcdefghijklmnopqrstuvwx",
            "abcdefghijklmnopqrstuvw!",
            "abcdefghijklmno#########",
            "mnopqrstuvwxABCDEFGHIJKLM",
            "这是一个足够长的测试叙事文本，用于验证结构和采样规则。",
            "这是一个足够长的测试叙事文本，用于验证结构和采样规则。",
            "完全不同的另一段测试叙事内容，长度也满足采样要求。",
        ]
        records = [{"occurrence_key": f"key-{index:02d}", "text": text, "primary_disposition": "PENDING", "orthogonal_flags": []} for index, text in enumerate(texts)]
        expected_survivors: list[str] = []
        normalized_groups: dict[str, list[dict[str, object]]] = {}
        for record in records:
            normalized_groups.setdefault(_normalise(record["text"]), []).append(record)
        exact_survivors: list[dict[str, object]] = []
        for group in normalized_groups.values():
            group.sort(key=lambda item: item["occurrence_key"])
            exact_survivors.append(group[0])
        accepted: list[dict[str, object]] = []
        for record in sorted(exact_survivors, key=lambda item: item["occurrence_key"]):
            current = _grams(_normalise(record["text"]))
            if all(not _jaccard_at_least_080(current, _grams(_normalise(previous["text"])))[0] for previous in accepted):
                expected_survivors.append(record["occurrence_key"])
                accepted.append(record)
        _exact_and_near(records)
        actual_survivors = [record["occurrence_key"] for record in records if record["primary_disposition"] == "PENDING"]
        self.assertEqual(actual_survivors, expected_survivors)

    def test_fixed_seed_randomized_join_matches_bruteforce_reference(self) -> None:
        import random
        from genshin_corpus.retrieval.w7_unit2 import _exact_and_near

        for seed in range(100):
            random.seed(seed)
            texts = ["".join(random.choice("abcdefghi") for _ in range(random.randint(20, 80))) for _ in range(24)]
            records = [{"occurrence_key": f"key-{index:03d}", "text": text, "primary_disposition": "PENDING", "orthogonal_flags": []} for index, text in enumerate(texts)]
            groups: dict[str, list[dict[str, object]]] = {}
            for record in records:
                groups.setdefault(_normalise(record["text"]), []).append(record)
            exact_survivors = [sorted(group, key=lambda item: item["occurrence_key"])[0] for group in groups.values()]
            accepted: list[dict[str, object]] = []
            for record in sorted(exact_survivors, key=lambda item: item["occurrence_key"]):
                grams = _grams(_normalise(record["text"]))
                if all(not _jaccard_at_least_080(grams, _grams(_normalise(previous["text"])))[0] for previous in accepted):
                    accepted.append(record)
            expected = {record["occurrence_key"] for record in accepted}
            _exact_and_near(records)
            actual = {record["occurrence_key"] for record in records if record["primary_disposition"] == "PENDING"}
            self.assertEqual(actual, expected, f"seed={seed}")

    def test_runner_interface_has_only_authorized_inputs(self) -> None:
        from genshin_corpus.retrieval.w7_unit2 import run_unit2

        self.assertEqual(list(inspect.signature(run_unit2).parameters), ["canonical_manifest_path", "r02_manifest_path", "leaf_path", "projection_path", "output_root"])

    def test_structural_invalid_is_fail_closed_and_disposition_exclusive(self) -> None:
        bad = _document(1)
        bad["source_coverage"][0]["lineage"]["raw_refs"] = []
        result = execute_documents([bad], _projection())
        self.assertEqual(result["primary_disposition_counts"]["STRUCTURAL_INVALID"], 1)

    def test_projection_role_blind_and_legacy_flag_does_not_change_disposition(self) -> None:
        doc = _document(1)
        key = occurrence_key(doc["source_coverage"][0])
        projection = _projection()
        projection["legacy_occurrence_exclusion_keys"] = [key]
        result = execute_documents([doc], projection)
        self.assertEqual(result["records"][0]["primary_disposition"], "ELIGIBLE_SURVIVOR")
        self.assertIn("LEGACY_OCCURRENCE_EXCLUDED", result["records"][0]["orthogonal_flags"])
        self.assertEqual(result["queues"], {"semantic": [], "control": [], "WR": [], "HN": []})

    def test_family_without_sampling_representative_remains_in_gold_index(self) -> None:
        first = _document(1)
        second = _document(2)
        second["source_coverage"][0]["record_id"] = first["source_coverage"][0]["record_id"]
        second["source_coverage"][0]["section_ordinal"] = 1
        second["source_coverage"][0]["component_observation_key"] = "component-b"
        second["source_coverage"][0]["lineage"]["raw_refs"][0]["content_id"] = "page-1"
        projection = _projection()
        second_family = family_key(second["source_coverage"][0])
        projection["legacy_evidence_family_exclusion_keys"] = [second_family]
        result = execute_documents([first, second], projection)
        gold = result["gold_bundles"][0]
        self.assertIn(second_family, gold["neighborhood_family_keys"])

    def test_fifteen_structural_related_families_and_four_executable(self) -> None:
        documents = [_same_family_document(index, section=index) for index in range(16)]
        projection = _projection()
        excluded = [family_key(document["source_coverage"][0]) for document in documents[5:]]
        projection["legacy_evidence_family_exclusion_keys"] = excluded
        result = execute_documents(documents, projection)
        scope = result["structural_related_scopes"][0]
        self.assertEqual(scope["structural_related_count"], 15)
        self.assertEqual(scope["executable_related_count"], 4)
        self.assertEqual(scope["relation_status"], "VALID")
        self.assertEqual(len(result["gold_bundles"][0]["neighborhood_family_keys"]), 16)

    def test_gold_overflow_can_occur_without_relation_overflow(self) -> None:
        documents = [_same_family_document(index, section=0) for index in range(17)]
        result = execute_documents(documents, _projection())
        bundle = result["gold_bundles"][0]
        self.assertEqual(bundle["status"], "GOLD_AMBIGUITY")
        self.assertEqual(result["structural_related_scopes"][0]["relation_status"], "VALID")

    def test_pair_views_share_anchor_bundle_and_do_not_get_pair_cap(self) -> None:
        documents = [_same_family_document(index, section=index, component_id=f"component-{index}") for index in range(4)]
        result = execute_documents(documents, _projection())
        self.assertEqual(len(result["gold_bundles"]), 4)
        self.assertEqual(len(result["pair_views"]), 12)
        bundle_keys = {bundle["anchor_occurrence_key"] for bundle in result["gold_bundles"]}
        self.assertEqual({view["anchor_gold_bundle_key"] for view in result["pair_views"]}, bundle_keys)
        self.assertTrue(all(len(view["pair_relevant_occurrence_keys"]) >= 2 for view in result["pair_views"]))

    def test_wr_hn_round_robin_and_general_hash_partition_are_deterministic(self) -> None:
        related = [_same_family_document(index, section=index, component_id=f"role-{index}") for index in range(5)]
        general = [_document(100 + index) for index in range(4)]
        result = execute_documents(related + general, _projection())
        wr_keys = {row["anchor_occurrence_key"] for row in result["queues"]["WR"]}
        hn_keys = {row["anchor_occurrence_key"] for row in result["queues"]["HN"]}
        self.assertTrue(wr_keys.isdisjoint(hn_keys))
        self.assertLessEqual(len(wr_keys), 2)
        self.assertLessEqual(len(hn_keys), 2)
        for queue in ("semantic", "control"):
            for row in result["queues"][queue]:
                digest = hashlib.sha256(("w7-general-queue-v1\0" + row["anchor_occurrence_key"]).encode("utf-8")).digest()
                expected = "control" if digest[0] % 2 == 0 else "semantic"
                self.assertEqual(queue, expected)

    def test_writes_deterministic_gzip_artifacts(self) -> None:
        docs = [_document(1)]
        root = Path(".w7-unit2-test-output")
        if root.exists():
            shutil.rmtree(root)
        try:
            first = execute_documents(docs, _projection(), output_root=root / "one")
            second = execute_documents(docs, _projection(), output_root=root / "two")
            self.assertEqual(first["manifest"]["artifacts"], second["manifest"]["artifacts"])
            self.assertEqual((root / "one" / "metadata" / "manifest.json").read_bytes(), (root / "two" / "metadata" / "manifest.json").read_bytes())
            body = (root / "one" / "unit2" / "input_rows.jsonl.gz").read_bytes()
            self.assertEqual(body, (root / "two" / "unit2" / "input_rows.jsonl.gz").read_bytes())
        finally:
            if root.exists():
                shutil.rmtree(root)

    def test_one_family_multiple_survivors_yields_one_representative_anchor(self) -> None:
        first = _same_family_document(1)
        second = _same_family_document(2)
        result = execute_documents([first, second], _projection())
        family = result["families"][0]
        self.assertEqual(len(result["families"]), 1)
        self.assertEqual(family["representative_occurrence_key"], min(row["occurrence_key"] for row in result["records"]))
        self.assertEqual(len(result["gold_bundles"]), 1)
        self.assertEqual(len(result["queue_candidates"]), 1)
        self.assertEqual(result["gold_bundles"][0]["occurrence_keys"], sorted(row["occurrence_key"] for row in result["records"]))
        self.assertEqual(result["queue_candidates"][0]["anchor_occurrence_key"], family["representative_occurrence_key"])

    def test_family_identity_inconsistency_blocks(self) -> None:
        first = _same_family_document(1)
        different_entity = _same_family_document(2, content_id="other-page")
        with self.assertRaises(Unit2Blocked):
            execute_documents([first, different_entity], _projection())
        different_role = _same_family_document(2, component_id="other-component")
        with self.assertRaises(Unit2Blocked):
            execute_documents([first, different_role], _projection())

    def test_topic_multiple_family_blocks(self) -> None:
        first = _same_family_document(1)
        second = _same_family_document(2)
        second["source_coverage"][0]["component_observation_key"] = "other-observation"
        with self.assertRaises(Unit2Blocked):
            execute_documents([first, second], _projection())

    def test_bool_ordinal_and_malformed_raw_ref_reason_codes(self) -> None:
        boolean_ordinal = _document(1)
        boolean_ordinal["source_coverage"][0]["unit_ordinal"] = True
        malformed_raw = _document(2)
        malformed_raw["source_coverage"][0]["lineage"]["raw_refs"][0].pop("artifact_path")
        result = execute_documents([boolean_ordinal, malformed_raw], _projection())
        self.assertEqual(result["records"][0]["reason_code"], "INVALID_OCCURRENCE_ORDINAL")
        self.assertEqual(result["records"][1]["reason_code"], "INVALID_RAW_REF")

    def test_gold_overflow_is_two_level_reason_and_rejects_anchor(self) -> None:
        documents = [_same_family_document(index, section=index) for index in range(17)]
        result = execute_documents(documents, _projection())
        bundle = result["gold_bundles"][0]
        self.assertEqual(bundle["status"], "GOLD_AMBIGUITY")
        self.assertEqual(bundle["subreason"], "GOLD_REVIEW_SCOPE_OVERFLOW")
        self.assertEqual(bundle["gold_review_occurrence_count"], 17)
        self.assertEqual(len(bundle["occurrence_keys"]), 17)
        self.assertFalse(result["queue_candidates"][0]["anchor_gold_bundle_valid"])

    def test_related_scope_audit_and_relation_overflow_status(self) -> None:
        documents = [_same_family_document(index, section=index) for index in range(14)]
        result = execute_documents(documents, _projection())
        scope = result["structural_related_scopes"][0]
        self.assertEqual(scope["structural_related_count"], 13)
        self.assertEqual(scope["executable_related_count"], 13)
        self.assertEqual(scope["relation_status"], "RELATED_SCOPE_OVERFLOW")
        self.assertEqual(len(scope["structural_related_family_keys"]), 13)
        self.assertEqual(len(scope["executable_related_family_keys"]), 13)
        self.assertEqual(result["relations"], [])

    def test_queue_underfill_is_explicit_and_no_redistribution(self) -> None:
        result = execute_documents([_document(1)], _projection())
        for queue, allocation in {"semantic": 16, "control": 8, "WR": 12, "HN": 12}.items():
            self.assertEqual(result["queue_accounting"][queue]["allocation"], allocation)
            self.assertEqual(result["queue_accounting"][queue]["status"], "EVIDENCE_INSUFFICIENT")
        self.assertEqual(sum(len(values) for values in result["queues"].values()), 1)

    def test_manifest_generator_sha_and_artifact_row_counts(self) -> None:
        root = Path(".w7-unit2-manifest-test")
        if root.exists():
            shutil.rmtree(root)
        try:
            result = execute_documents([_document(1)], _projection(), output_root=root)
            manifest = result["manifest"]
            module_sha = hashlib.sha256(Path("src/genshin_corpus/retrieval/w7_unit2.py").read_bytes()).hexdigest()
            self.assertEqual(manifest["generator"]["sha256"], module_sha)
            self.assertEqual(manifest["artifacts"]["near_duplicate_pairs"]["row_count"], len(result["pair_audit"]))
            self.assertEqual(manifest["artifacts"]["input_rows"]["row_count"], 1)
            self.assertEqual(manifest["scientific_contract"], SCIENTIFIC_CONTRACT)
            self.assertEqual(manifest["accounting"]["input_rows"], 1)
        finally:
            if root.exists():
                shutil.rmtree(root)

    def test_executable_role_contrast_is_required_for_wr_hn(self) -> None:
        anchor = _same_family_document(1, section=0, component_id="role-a")
        unavailable_contrast = _same_family_document(2, section=1, component_id="role-b")
        executable_same_role = _same_family_document(3, section=2, component_id="role-a")
        projection = _projection()
        projection["legacy_evidence_family_exclusion_keys"] = [family_key(unavailable_contrast["source_coverage"][0])]
        result = execute_documents([anchor, unavailable_contrast, executable_same_role], projection)
        candidate = next(item for item in result["queue_candidates"] if item["anchor_occurrence_key"] == occurrence_key(anchor["source_coverage"][0]))
        scope = next(item for item in result["structural_related_scopes"] if item["anchor_occurrence_key"] == candidate["anchor_occurrence_key"])
        self.assertTrue(candidate["capabilities"]["related_role_contrast_capable"])
        self.assertEqual(scope["executable_related_count"], 1)
        self.assertEqual(len(candidate["relation_views"]), 1)
        self.assertEqual(len(candidate["wr_hn_relation_views"]), 0)
        self.assertFalse(candidate["wr_hn_candidate_eligible"])
        self.assertEqual(len(result["queues"]["WR"]), 0)
        self.assertEqual(len(result["queues"]["HN"]), 0)

    def test_executable_role_contrast_produces_wr_hn_candidate(self) -> None:
        documents = [
            _same_family_document(1, section=0, component_id="role-a"),
            _same_family_document(2, section=1, component_id="role-b"),
        ]
        result = execute_documents(documents, _projection())
        candidates = [item for item in result["queue_candidates"] if item["wr_hn_candidate_eligible"]]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(item["wr_hn_relation_views"] for item in candidates))

    def test_manifest_accounting_matches_emitted_records_and_scopes(self) -> None:
        documents = [_document(1), _document(2)]
        documents[1]["source_coverage"][0]["record_id"] = documents[0]["source_coverage"][0]["record_id"]
        documents[1]["source_coverage"][0]["section_ordinal"] = 1
        result = execute_documents(documents, _projection())
        accounting = result["accounting"]
        self.assertEqual(accounting["input_rows"], len(result["records"]))
        self.assertEqual(accounting["structural_valid"], sum("occurrence_key" in row for row in result["records"]))
        self.assertEqual(accounting["distinct_family_count"], len(result["families"]))
        self.assertEqual(accounting["anchor_count"], len(result["queue_candidates"]))
        self.assertEqual(accounting["structural_related_family_membership_count"], sum(scope["structural_related_count"] for scope in result["structural_related_scopes"]))
        self.assertEqual(accounting["executable_related_family_membership_count"], sum(scope["executable_related_count"] for scope in result["structural_related_scopes"]))
        self.assertEqual(accounting["relation_pair_count"], len(result["relations"]))
        self.assertEqual(accounting["pair_view_count"], len(result["pair_views"]))
        histogram = {}
        for bundle in result["gold_bundles"]:
            key = str(bundle["gold_review_occurrence_count"])
            histogram[key] = histogram.get(key, 0) + 1
        self.assertEqual(accounting["input_rows"], sum(result["primary_disposition_counts"].values()))
        self.assertEqual(result["gold_accounting"]["gold_review_occurrence_count_histogram"], {key: histogram[key] for key in sorted(histogram, key=int)})
        self.assertEqual(result["gold_accounting"]["gold_ambiguity_count"], result["gold_accounting"]["gold_review_scope_overflow_count"])

    def test_post_exact_duplicate_accounting_excludes_length_ineligible_rows(self) -> None:
        duplicate_text = "这是一段长度合格的完全相同测试文本，用于精确重复计数验证。"
        documents = [
            _document(1, "太短"),
            _document(2, duplicate_text),
            _document(3, duplicate_text),
            _document(4, "另一段长度合格且完全不同的测试文本，用于验证精确重复后的剩余行统计。"),
        ]
        result = execute_documents(documents, _projection())
        accounting = result["accounting"]
        self.assertEqual(accounting["structural_valid"], 4)
        self.assertEqual(accounting["text_length_eligible"], 3)
        self.assertEqual(accounting["text_length_ineligible"], 1)
        self.assertEqual(accounting["exact_duplicate_rejected"], 1)
        self.assertEqual(accounting["post_exact_duplicate_rows"], 2)
        self.assertEqual(
            accounting["text_length_eligible"],
            accounting["exact_duplicate_rejected"]
            + accounting["near_duplicate_rejected"]
            + accounting["eligible_survivor"],
        )
        emitted_post_exact = sum(
            row["primary_disposition"] in ("NEAR_DUPLICATE_REJECTED", "ELIGIBLE_SURVIVOR")
            for row in result["records"]
        )
        self.assertEqual(accounting["post_exact_duplicate_rows"], emitted_post_exact)
        self.assertEqual(
            accounting["post_exact_duplicate_rows"],
            accounting["near_duplicate_rejected"] + accounting["eligible_survivor"],
        )


if __name__ == "__main__":
    unittest.main()
