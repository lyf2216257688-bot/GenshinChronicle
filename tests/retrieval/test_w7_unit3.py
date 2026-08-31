from __future__ import annotations

import copy
import hashlib
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval import w7_unit3
from genshin_corpus.retrieval.w7_unit3 import (
    EXPECTED_OVERLAP_ENTRY_COUNT,
    FINAL_QUOTAS,
    QUEUE_ALLOCATIONS,
    QUEUE_ORDER,
    Unit3Blocked,
    _attempt_record,
    _stage_and_accept_review_pack,
    build_review_pack,
    finalize_candidates,
    freeze_semantic_state,
    parse_overlap_index_bytes,
    persist_query_attempt,
    persist_query_quality_result,
    restricted_c1_check,
    validate_attempt_history,
    validate_frozen_semantic_state,
    validate_query_quality_result,
    validate_review_pack,
    verify_checkpoint_generator_binding,
)


PACK_DEPENDENCY = {
    "review_pack_manifest_sha256": "a" * 64,
    "review_pack_artifact_sha256": "b" * 64,
}


def _raw_ref(index: int) -> dict[str, object]:
    return {
        "source": "mihoyo_obc", "locale": "zh-cn", "run_id": "synthetic-run",
        "content_id": f"content-{index:03d}", "artifact_kind": "detail",
        "artifact_path": f"artifacts/{index:03d}.json", "artifact_sha256": "c" * 64,
        "json_pointer": "/data", "embedded_json_pointer": "/embedded", "source_value_sha256": None,
    }


def _lineage(index: int) -> dict[str, object]:
    return {"evidence_scope": "direct_raw", "parsed_json_pointer": "/x", "raw_refs": [_raw_ref(index)], "dependency_locator": None}


def _row(index: int) -> dict[str, object]:
    key = f"occ-{index:03d}"
    return {
        "occurrence_key": key, "candidate_key": f"candidate-{index:03d}", "family_key": f"family-{index:03d}",
        "entity_key": ["source", "zh-cn", f"entity-{index:03d}"], "topic_key": [f"record-{index:03d}", 0],
        "occurrence_address": {"record_id": f"record-{index:03d}", "section_ordinal": 0, "component_observation_key": "component", "unit_ordinal": 0, "lineage": _lineage(index)},
        "text": f"synthetic evidence {index}", "lineage": _lineage(index), "raw_ref": _raw_ref(index),
    }


def _unit2_fixture() -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    rows = [_row(index) for index in range(48)]
    queues: list[dict[str, object]] = []
    bundles: list[dict[str, object]] = []
    views: list[dict[str, object]] = []
    index = 0
    for queue in QUEUE_ORDER:
        selected = []
        for _ in range(QUEUE_ALLOCATIONS[queue]):
            row = rows[index]
            candidate: dict[str, object] = {"anchor_occurrence_key": row["occurrence_key"]}
            if queue in ("WR", "HN"):
                pair_key = f"pair-{index:03d}"
                candidate["wr_hn_relation_views"] = [{"pair_key": pair_key}]
                views.append({"pair_key": pair_key, "anchor_occurrence_key": row["occurrence_key"], "anchor_gold_bundle_key": row["occurrence_key"], "relation_type": "same_entity_cross_section_cross_component", "anchor_family_key": row["family_key"], "related_family_key": row["family_key"], "related_representative_occurrence_key": row["occurrence_key"], "pair_relevant_occurrence_keys": [row["occurrence_key"]]})
            selected.append(candidate)
            bundles.append({"anchor_occurrence_key": row["occurrence_key"], "anchor_family_key": row["family_key"], "neighborhood_family_keys": [row["family_key"]], "occurrence_keys": [row["occurrence_key"]], "occurrence_addresses": [row["occurrence_address"]], "gold_review_occurrence_count": 1, "status": "VALID", "subreason": None})
            index += 1
        queues.append({"queue": queue, "rows": selected})
    return queues, rows, bundles, views


def _pack() -> list[dict[str, object]]:
    return build_review_pack(*_unit2_fixture())


def _multi_family_pack() -> list[dict[str, object]]:
    queues, rows, bundles, views = _unit2_fixture()
    anchor = rows[24]
    related = _row(48)
    related["entity_key"] = list(anchor["entity_key"])
    for raw_ref in (related["raw_ref"], related["lineage"]["raw_refs"][0], related["occurrence_address"]["lineage"]["raw_refs"][0]):
        raw_ref["content_id"] = anchor["raw_ref"]["content_id"]
    related["topic_key"] = ["related-record", 1]
    related["family_key"] = "related-family"
    related["occurrence_address"]["record_id"] = "related-record"
    related["occurrence_address"]["section_ordinal"] = 1
    rows.append(related)
    bundles[24]["occurrence_keys"].append(related["occurrence_key"])
    bundles[24]["occurrence_addresses"].append(related["occurrence_address"])
    bundles[24]["neighborhood_family_keys"].append(related["family_key"])
    bundles[24]["gold_review_occurrence_count"] = 2
    view = next(item for item in views if item["pair_key"] == "pair-024")
    view["related_family_key"] = related["family_key"]
    view["related_representative_occurrence_key"] = related["occurrence_key"]
    view["pair_relevant_occurrence_keys"] = [anchor["occurrence_key"], related["occurrence_key"]]
    return build_review_pack(queues, rows, bundles, views)


def _state_input(record: dict[str, object]) -> dict[str, object]:
    gold = [item["occurrence_key"] for item in record["gold_occurrences"]]
    pair_views = record["pair_views"]
    judgments = [{"pair_key": item["pair_key"], "decision": "VALID", "reason_code": None} for item in pair_views]
    return {
        "frozen_gold_occurrence_keys": gold, "accepted_gold_occurrence_keys": [record["anchor_occurrence_key"]],
        "reviewed_non_gold_occurrence_keys": [key for key in gold if key != record["anchor_occurrence_key"]],
        "occurrence_reviews": [{"occurrence_key": key, "judgment": "ACCEPTED_GOLD" if key == record["anchor_occurrence_key"] else "REVIEWED_NON_GOLD", "reason_code": "W7_LOCAL_SYNTHETIC_REVIEW", "provenance_caveat": None} for key in gold],
        "anchor_review_result": "ACCEPTED_GOLD",
        "gameplay_build_exclusion_status": "NOT_GAMEPLAY_BUILD_PRIMARY",
        "queue_semantic_validity": "VALID",
        "semantic_status": "ACCEPT",
        "semantic_reason": None,
        "gold_review_complete": True, "accepted_gold_sufficient": True,
        "target_proposition_status": "FROZEN", "target_proposition": "synthetic fact",
        "anchor_source_span": {"start": 0, "end": 2}, "sentence_or_clause_basis": "synthetic basis",
        "query_intent": "synthetic intent", "answer_proposition": "synthetic answer",
        "pair_judgments": judgments, "selected_pair_key": pair_views[0]["pair_key"] if pair_views else None,
    }


def _freeze(record: dict[str, object], **overrides: object) -> dict[str, object]:
    state = _state_input(record)
    state.update(overrides)
    return freeze_semantic_state(state, record=record, review_pack_dependency=PACK_DEPENDENCY)


def _persist_with_quality(
    state: dict[str, object],
    attempt_number: int,
    query: str,
    quality_status: str = "PASS",
    quality_reason: str | None = None,
    *,
    record: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    attempt = persist_query_attempt(
        state,
        attempt_number,
        query,
        record=record,
        review_pack_dependency=PACK_DEPENDENCY,
    )
    return attempt, persist_query_quality_result(attempt, quality_status, quality_reason)


def _overlap_entry(opaque_id: str, query: str) -> dict[str, object]:
    normalized = w7_unit3._normalise(query)
    return {"opaque_legacy_id": opaque_id, "normalized_query_sha256": w7_unit3._hash_text(normalized), "normalized_continuous_8char_window_sha256": sorted(w7_unit3._windows(normalized, 8)), "normalized_unique_char_3gram_sha256": sorted(w7_unit3._grams(normalized))}


def _overlap_bytes(query: str = "zzzzzz") -> bytes:
    index = {"schema_version": "p04-w7-legacy-query-overlap-index-v1", "FOR_UNIT3_ONLY": True, "FOR_UNIT2_CANDIDATE_SELECTION": False, "normalization_version": "c1-nfkc-whitespace-collapse-trim-casefold-v1", "normalization_metadata": {"unicode_form": "NFKC", "whitespace": "unicode_whitespace_collapse_to_ascii_space", "trim": True, "casefold": True, "hash_encoding": "UTF-8", "codepoint_basis": "Unicode_code_points", "continuous_window_length": 8, "unique_gram_length": 3}, "source_benchmark_sha256": "d" * 64, "accounting": {"legacy_query_count": EXPECTED_OVERLAP_ENTRY_COUNT}, "entries": [_overlap_entry(f"legacy-{index:02d}", query if index == 0 else f"other-{index}-zzzz") for index in range(EXPECTED_OVERLAP_ENTRY_COUNT)]}
    return canonical_json_bytes(index)


class W7Unit3FocusedTests(unittest.TestCase):
    def test_review_pack_is_frozen_48_ordered_nested_allowlisted_and_silent(self) -> None:
        pack = _pack()
        self.assertEqual(validate_review_pack(pack), {"candidate_count": 48, "forbidden_field_count": 0, "outside_scope_text_count": 0, "legacy_c1_sensitive_field_count": 0})
        self.assertEqual([row["queue"] for row in pack[:16]], ["semantic"] * 16)
        self.assertEqual([row["global_review_order"] for row in pack], list(range(1, 49)))

    def test_review_pack_rejects_renumbered_queue_block_swap(self) -> None:
        pack = _pack()
        swapped = [*pack[16:24], *pack[:16], *pack[24:]]
        for global_order, row in enumerate(swapped, 1):
            row["global_review_order"] = global_order
        with self.assertRaises(Unit3Blocked):
            validate_review_pack(swapped)

    def test_review_pack_leaks_and_pair_scope_fail_closed_without_text(self) -> None:
        pack = _pack()
        secret = "SECRET_EVIDENCE_MUST_NOT_APPEAR"
        pack[0]["anchor"]["lineage"]["legacy_query"] = secret
        with self.assertRaises(Unit3Blocked) as raised:
            validate_review_pack(pack)
        self.assertNotIn(secret, str(raised.exception))
        pack = _pack()
        pack[24]["pair_views"][0]["pair_relevant_occurrence_keys"] = ["outside"]
        with self.assertRaises(Unit3Blocked):
            validate_review_pack(pack)

    def test_ta01_multi_family_gold_scope_preserves_per_occurrence_identity(self) -> None:
        record = _multi_family_pack()[24]
        self.assertEqual(len(record["gold_occurrences"]), 2)
        anchor, related = record["gold_occurrences"]
        self.assertEqual(anchor["entity_key"], related["entity_key"])
        self.assertNotEqual(anchor["topic_key"], related["topic_key"])
        self.assertNotEqual(anchor["evidence_family_key"], related["evidence_family_key"])
        self.assertEqual(record["pair_views"][0]["related_family_key"], related["evidence_family_key"])
        self.assertEqual(record["pair_views"][0]["related_representative_occurrence_key"], related["occurrence_key"])
        wrong_entity = copy.deepcopy(record)
        wrong_entity["gold_occurrences"][1]["entity_key"] = ["wrong", "entity", "key"]
        with self.assertRaises(Unit3Blocked):
            w7_unit3.review_pack_record_sha256(wrong_entity)
        wrong_pair = copy.deepcopy(record)
        wrong_pair["pair_views"][0]["related_family_key"] = anchor["evidence_family_key"]
        with self.assertRaises(Unit3Blocked):
            w7_unit3.review_pack_record_sha256(wrong_pair)

    def test_semantic_state_binds_exact_pack_record_and_dependency_identity(self) -> None:
        record = _pack()[0]
        state = _freeze(record)
        validate_frozen_semantic_state(state, record=record, review_pack_dependency=PACK_DEPENDENCY)
        variants: list[tuple[dict[str, object], dict[str, str]]] = []
        for field, replacement in (("candidate_key", "wrong"), ("queue", "control"), ("global_review_order", 2), ("queue_review_order", 2), ("anchor_occurrence_key", "wrong"), ("entity_key", ["wrong"]), ("topic_key", ["wrong", 0]), ("evidence_family_key", "wrong")):
            changed = copy.deepcopy(record)
            changed[field] = replacement
            variants.append((changed, PACK_DEPENDENCY))
        changed = copy.deepcopy(record)
        changed["anchor"]["text"] = "tampered"
        variants.append((changed, PACK_DEPENDENCY))
        for changed, dependency in variants:
            with self.subTest(changed=changed):
                with self.assertRaises(Unit3Blocked):
                    validate_frozen_semantic_state(state, record=changed, review_pack_dependency=dependency)
        with self.assertRaises(Unit3Blocked):
            validate_frozen_semantic_state(state, record=record, review_pack_dependency={**PACK_DEPENDENCY, "review_pack_artifact_sha256": "c" * 64})
        changed_state = dict(state)
        changed_state["pack_record_sha256"] = "d" * 64
        with self.assertRaises(Unit3Blocked):
            validate_frozen_semantic_state(changed_state, record=record, review_pack_dependency=PACK_DEPENDENCY)

    def test_gold_partition_anchor_and_pair_order_are_mechanically_enforced(self) -> None:
        record = copy.deepcopy(_pack()[24])
        second = copy.deepcopy(record["pair_views"][0])
        second["pair_key"] = "pair-second"
        record["pair_views"].append(second)
        input_state = _state_input(record)
        input_state["pair_judgments"] = [{"pair_key": record["pair_views"][0]["pair_key"], "decision": "REJECT", "reason_code": "NOT_SEMANTICALLY_VALID"}, {"pair_key": "pair-second", "decision": "VALID", "reason_code": None}]
        input_state["selected_pair_key"] = "pair-second"
        state = freeze_semantic_state(input_state, record=record, review_pack_dependency=PACK_DEPENDENCY)
        validate_frozen_semantic_state(state, record=record, review_pack_dependency=PACK_DEPENDENCY)
        input_state["selected_pair_key"] = record["pair_views"][0]["pair_key"]
        with self.assertRaises(Unit3Blocked):
            freeze_semantic_state(input_state, record=record, review_pack_dependency=PACK_DEPENDENCY)
        normal = _pack()[0]
        no_anchor = _state_input(normal)
        no_anchor["accepted_gold_occurrence_keys"] = []
        no_anchor["reviewed_non_gold_occurrence_keys"] = no_anchor["frozen_gold_occurrence_keys"]
        no_anchor["occurrence_reviews"] = [{"occurrence_key": normal["anchor_occurrence_key"], "judgment": "REVIEWED_NON_GOLD", "reason_code": "W7_LOCAL_SYNTHETIC_REVIEW", "provenance_caveat": None}]
        no_anchor["anchor_review_result"] = "REVIEWED_NON_GOLD"
        with self.assertRaises(Unit3Blocked):
            freeze_semantic_state(no_anchor, record=normal, review_pack_dependency=PACK_DEPENDENCY)
        duplicate_accepted = _state_input(normal)
        duplicate_accepted["accepted_gold_occurrence_keys"] *= 2
        with self.assertRaises(Unit3Blocked):
            freeze_semantic_state(duplicate_accepted, record=normal, review_pack_dependency=PACK_DEPENDENCY)

    def test_target_ambiguity_and_anchor_source_span_remain_mechanical_only(self) -> None:
        record = _pack()[0]
        ambiguous = _state_input(record)
        ambiguous.update({"target_proposition_status": "TARGET_PROPOSITION_AMBIGUOUS", "target_proposition": None, "anchor_source_span": None, "sentence_or_clause_basis": None, "query_intent": None, "answer_proposition": None, "pair_judgments": [], "selected_pair_key": None})
        ambiguous.update({"queue_semantic_validity": "INVALID", "semantic_status": "REJECT", "semantic_reason": "TARGET_PROPOSITION_AMBIGUOUS"})
        state = freeze_semantic_state(ambiguous, record=record, review_pack_dependency=PACK_DEPENDENCY)
        with self.assertRaises(Unit3Blocked):
            persist_query_attempt(state, 1, "not allowed", record=record, review_pack_dependency=PACK_DEPENDENCY)
        mismatched_ambiguity = dict(ambiguous)
        mismatched_ambiguity["semantic_reason"] = "ANCHOR_NOT_VALID_POSITIVE"
        with self.assertRaises(Unit3Blocked):
            freeze_semantic_state(mismatched_ambiguity, record=record, review_pack_dependency=PACK_DEPENDENCY)
        ordinary_reject = _state_input(record)
        ordinary_reject.update({"queue_semantic_validity": "INVALID", "semantic_status": "REJECT", "semantic_reason": "QUEUE_SEMANTIC_INVALID"})
        rejected_state = freeze_semantic_state(ordinary_reject, record=record, review_pack_dependency=PACK_DEPENDENCY)
        self.assertEqual(rejected_state["semantic_status"], "REJECT")
        invalid_span = _state_input(record)
        invalid_span["anchor_source_span"] = {"start": 19, "end": 21}
        with self.assertRaises(Unit3Blocked):
            freeze_semantic_state(invalid_span, record=record, review_pack_dependency=PACK_DEPENDENCY)

    def test_stage_aware_early_rejects_do_not_forge_proposition_or_pairs(self) -> None:
        def early_reject(record: dict[str, object], reason: str, *, accepted_anchor: bool = False) -> dict[str, object]:
            state = _state_input(record)
            gold = list(state["frozen_gold_occurrence_keys"])
            state.update({
                "accepted_gold_occurrence_keys": [record["anchor_occurrence_key"]] if accepted_anchor else [],
                "reviewed_non_gold_occurrence_keys": [key for key in gold if accepted_anchor is False or key != record["anchor_occurrence_key"]],
                "occurrence_reviews": [
                    {"occurrence_key": key, "judgment": "ACCEPTED_GOLD" if accepted_anchor and key == record["anchor_occurrence_key"] else "REVIEWED_NON_GOLD", "reason_code": "W7_LOCAL_SYNTHETIC_REVIEW", "provenance_caveat": None}
                    for key in gold
                ],
                "anchor_review_result": "ACCEPTED_GOLD" if accepted_anchor else "REVIEWED_NON_GOLD",
                "queue_semantic_validity": "INVALID",
                "semantic_status": "REJECT",
                "semantic_reason": reason,
                "gold_review_complete": True,
                "accepted_gold_sufficient": False,
                "target_proposition_status": "NOT_REACHED",
                "target_proposition": None,
                "anchor_source_span": None,
                "sentence_or_clause_basis": None,
                "query_intent": None,
                "answer_proposition": None,
                "pair_judgments": [],
                "selected_pair_key": None,
            })
            return freeze_semantic_state(state, record=record, review_pack_dependency=PACK_DEPENDENCY)

        anchor_reject = early_reject(_pack()[0], "ANCHOR_NOT_VALID_POSITIVE")
        self.assertEqual(anchor_reject["target_proposition_status"], "NOT_REACHED")
        gold_reject = early_reject(_pack()[1], "GOLD_NOT_COLLECTIVELY_SUFFICIENT", accepted_anchor=True)
        self.assertEqual(gold_reject["semantic_status"], "REJECT")
        wr_reject = early_reject(_pack()[24], "PRIMARY_GOLD_GAMEPLAY_BUILD")
        self.assertEqual(wr_reject["pair_judgments"], [])
        provenance_reject = early_reject(_pack()[2], "PROVENANCE_UNRESOLVED")
        self.assertEqual(provenance_reject["target_proposition_status"], "NOT_REACHED")
        uncertain_reject = early_reject(_pack()[3], "REVIEWER_UNCERTAIN")
        self.assertEqual(uncertain_reject["semantic_status"], "REJECT")
        scope_reject = early_reject(_pack()[4], "GOLD_SCOPE_INCOMPLETE")
        self.assertTrue(scope_reject["gold_review_complete"])
        incomplete_scope = _state_input(_pack()[5])
        incomplete_scope.update({
            "accepted_gold_occurrence_keys": [],
            "reviewed_non_gold_occurrence_keys": [],
            "occurrence_reviews": [],
            "anchor_review_result": "REVIEWED_NON_GOLD",
            "queue_semantic_validity": "INVALID",
            "semantic_status": "REJECT",
            "semantic_reason": "GOLD_SCOPE_INCOMPLETE",
            "gold_review_complete": True,
            "accepted_gold_sufficient": False,
            "target_proposition_status": "NOT_REACHED",
            "target_proposition": None,
            "anchor_source_span": None,
            "sentence_or_clause_basis": None,
            "query_intent": None,
            "answer_proposition": None,
            "pair_judgments": [],
            "selected_pair_key": None,
        })
        with self.assertRaises(Unit3Blocked):
            freeze_semantic_state(incomplete_scope, record=_pack()[5], review_pack_dependency=PACK_DEPENDENCY)
        with self.assertRaises(Unit3Blocked):
            early_reject(_pack()[24], "NO_VALID_WRONG_ROLE_PAIR")
        with self.assertRaises(Unit3Blocked):
            early_reject(_pack()[36], "NO_VALID_HARD_NEGATIVE_PAIR")
        with self.assertRaises(Unit3Blocked):
            persist_query_attempt(anchor_reject, 1, "not allowed", record=_pack()[0], review_pack_dependency=PACK_DEPENDENCY)

        pair_record = _pack()[24]
        pair_reject = _state_input(pair_record)
        pair_reject.update({
            "queue_semantic_validity": "INVALID",
            "semantic_status": "REJECT",
            "semantic_reason": "NO_VALID_WRONG_ROLE_PAIR",
            "pair_judgments": [{"pair_key": pair_record["pair_views"][0]["pair_key"], "decision": "REJECT", "reason_code": "NOT_SEMANTICALLY_VALID"}],
            "selected_pair_key": None,
        })
        later_reject = freeze_semantic_state(pair_reject, record=pair_record, review_pack_dependency=PACK_DEPENDENCY)
        self.assertEqual(later_reject["target_proposition_status"], "FROZEN")

        malformed = _state_input(_pack()[25])
        malformed.update({
            "queue_semantic_validity": "INVALID", "semantic_status": "REJECT", "semantic_reason": "ANCHOR_NOT_VALID_POSITIVE",
            "target_proposition_status": "NOT_REACHED", "target_proposition": None, "anchor_source_span": None,
            "sentence_or_clause_basis": None, "query_intent": None, "answer_proposition": None,
            "selected_pair_key": None,
        })
        with self.assertRaises(Unit3Blocked):
            freeze_semantic_state(malformed, record=_pack()[25], review_pack_dependency=PACK_DEPENDENCY)

    def test_authored_attempt_quality_results_are_separate_and_limit_retries(self) -> None:
        record = _pack()[0]
        state = _freeze(record)
        raw = _overlap_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        first = persist_query_attempt(state, 1, "first question", record=record, review_pack_dependency=PACK_DEPENDENCY)
        with self.assertRaises(Unit3Blocked):
            restricted_c1_check([first], [], raw, expected_index_sha256=sha)
        first_quality = persist_query_quality_result(first, "REJECT", "QUERY_NOT_NATURAL")
        with self.assertRaises(Unit3Blocked):
            restricted_c1_check([first], [first_quality], raw, expected_index_sha256=sha)
        mutated_attempt = dict(first)
        mutated_attempt["query"] = "replacement is not permitted"
        with self.assertRaises(Unit3Blocked):
            validate_query_quality_result(mutated_attempt, first_quality)
        tampered_quality = dict(first_quality)
        tampered_quality["quality_reason"] = "QUERY_INTENT_DRIFT"
        with self.assertRaises(Unit3Blocked):
            validate_query_quality_result(first, tampered_quality)

        second = persist_query_attempt(state, 2, "second question", record=record, review_pack_dependency=PACK_DEPENDENCY)
        second_quality = persist_query_quality_result(second, "REJECT", "PAIR_QUERY_INCONSISTENT")
        self.assertEqual(
            len(validate_attempt_history(state, [first, second], [first_quality, second_quality], [], record=record, review_pack_dependency=PACK_DEPENDENCY)),
            2,
        )
        with self.assertRaises(Unit3Blocked):
            persist_query_attempt(state, 3, "third question", record=record, review_pack_dependency=PACK_DEPENDENCY)

    def test_attempt_hash_c1_binding_and_two_attempt_retry(self) -> None:
        record = _pack()[0]
        state = _freeze(record)
        index_bytes = _overlap_bytes("first question")
        index_sha = hashlib.sha256(index_bytes).hexdigest()
        first, first_quality = _persist_with_quality(state, 1, "first question", record=record)
        c1_results, feedback = restricted_c1_check([first], [first_quality], index_bytes, expected_index_sha256=index_sha)
        self.assertEqual(feedback, [{"attempt_id": first["attempt_id"], "overall": "REJECT"}])
        second, second_quality = _persist_with_quality(state, 2, "different question", record=record)
        self.assertEqual(len(validate_attempt_history(state, [first, second], [first_quality, second_quality], c1_results, record=record, review_pack_dependency=PACK_DEPENDENCY)), 2)
        mutated_result = dict(c1_results[0])
        mutated_result["overall"] = "PASS"
        with self.assertRaises(Unit3Blocked):
            validate_attempt_history(state, [first, second], [first_quality, second_quality], [mutated_result], record=record, review_pack_dependency=PACK_DEPENDENCY)
        mutated = dict(first)
        mutated["query"] = "mutated persisted query"
        with self.assertRaises(Unit3Blocked):
            restricted_c1_check([mutated], [first_quality], index_bytes, expected_index_sha256=index_sha)
        with self.assertRaises(Unit3Blocked):
            persist_query_attempt(state, 3, "third attempt", record=record, review_pack_dependency=PACK_DEPENDENCY)

    def test_c1_raw_bytes_binding_redaction_and_exact_half_threshold(self) -> None:
        record = _pack()[0]
        state = _freeze(record)

        def result(query: str, legacy: str) -> tuple[dict[str, object], bytes, str]:
            raw = _overlap_bytes(legacy)
            sha = hashlib.sha256(raw).hexdigest()
            attempt, quality = _persist_with_quality(state, 1, query, record=record)
            audits, feedback = restricted_c1_check([attempt], [quality], raw, expected_index_sha256=sha)
            self.assertEqual(feedback, [{"attempt_id": attempt["attempt_id"], "overall": audits[0]["overall"]}])
            self.assertNotIn("opaque_legacy_id", audits[0])
            self.assertNotIn("intersection", audits[0])
            return audits[0], raw, sha

        self.assertEqual(result("abcdefghij", "abcde")[0]["overall"], "PASS")
        exact_half, raw, sha = result("abcdefghij", "abcdef")
        self.assertEqual(exact_half["overall"], "REJECT")
        self.assertTrue(exact_half["char_3gram_threshold_rule"])
        self.assertEqual(result("abcdefghij", "abcdefg")[0]["overall"], "REJECT")
        self.assertEqual(parse_overlap_index_bytes(raw, expected_sha256=sha)["schema_version"], w7_unit3.OVERLAP_SCHEMA_VERSION)
        normalized_raw = _overlap_bytes("a b cdef")
        normalized_sha = hashlib.sha256(normalized_raw).hexdigest()
        normalized_attempt, normalized_quality = _persist_with_quality(state, 1, "Ａ\u2003B CDEF", record=record)
        normalized_audit, _ = restricted_c1_check([normalized_attempt], [normalized_quality], normalized_raw, expected_index_sha256=normalized_sha)
        self.assertTrue(normalized_audit[0]["exact_rule"])
        malformed_attempt, malformed_quality = _persist_with_quality(state, 1, "abcdefghij", record=record)
        with self.assertRaises(Unit3Blocked):
            restricted_c1_check([malformed_attempt], [malformed_quality], raw + b" ", expected_index_sha256=sha)

    def test_c1_schema_contract_fails_after_raw_sha_gate(self) -> None:
        raw = _overlap_bytes()
        parsed = w7_unit3.json.loads(raw)
        parsed["normalization_metadata"]["unique_gram_length"] = 4
        malformed = canonical_json_bytes(parsed)
        with self.assertRaises(Unit3Blocked):
            parse_overlap_index_bytes(malformed, expected_sha256=hashlib.sha256(malformed).hexdigest())

    def test_finalizer_requires_full_contract_and_exact_frozen_order(self) -> None:
        pack = _pack()
        raw = _overlap_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        ledgers = []
        for record in pack:
            index = record["queue_review_order"]
            status = "ACCEPT" if index <= FINAL_QUOTAS[record["queue"]] + 1 else "REJECT"
            if status == "ACCEPT":
                state = _freeze(record)
                attempt, quality = _persist_with_quality(state, 1, f"question-{record['global_review_order']}", record=record)
                c1_results, _ = restricted_c1_check([attempt], [quality], raw, expected_index_sha256=sha)
            else:
                state = _freeze(record, queue_semantic_validity="INVALID", semantic_status="REJECT", semantic_reason="QUEUE_SEMANTIC_INVALID")
                attempt, quality, c1_results = None, None, []
            ledgers.append({"pack_record": record, "review_pack_dependency": PACK_DEPENDENCY, "semantic_state": state, "attempts": [] if attempt is None else [attempt], "query_quality_results": [] if quality is None else [quality], "c1_results": c1_results})
        result = finalize_candidates(ledgers, accepted_review_pack_dependency=PACK_DEPENDENCY, expected_overlap_index_sha256=sha)
        self.assertEqual(len(result["selected"]), 24)
        self.assertEqual(result["queue_accounting"]["semantic"]["valid_not_selected_quota"], 1)
        incomplete = copy.deepcopy(ledgers)
        incomplete[0]["c1_results"] = []
        incomplete[FINAL_QUOTAS["semantic"]]["c1_results"] = []
        self.assertEqual(finalize_candidates(incomplete, accepted_review_pack_dependency=PACK_DEPENDENCY, expected_overlap_index_sha256=sha)["queue_accounting"]["semantic"]["selected"], 7)
        with self.assertRaises(Unit3Blocked):
            finalize_candidates([*ledgers[1:2], ledgers[0], *ledgers[2:]], accepted_review_pack_dependency=PACK_DEPENDENCY, expected_overlap_index_sha256=sha)
        wrong_dependency = copy.deepcopy(ledgers)
        wrong_dependency[0]["review_pack_dependency"]["review_pack_artifact_sha256"] = "f" * 64
        with self.assertRaises(Unit3Blocked):
            finalize_candidates(wrong_dependency, accepted_review_pack_dependency=PACK_DEPENDENCY, expected_overlap_index_sha256=sha)
        with self.assertRaises(Unit3Blocked):
            finalize_candidates(ledgers, accepted_review_pack_dependency=PACK_DEPENDENCY, expected_overlap_index_sha256="f" * 64)
        swapped = copy.deepcopy(ledgers)
        swapped[:24] = [*swapped[16:24], *swapped[:16]]
        for number, ledger in enumerate(swapped, 1):
            ledger["pack_record"]["global_review_order"] = number
        with self.assertRaises(Unit3Blocked):
            finalize_candidates(swapped, accepted_review_pack_dependency=PACK_DEPENDENCY, expected_overlap_index_sha256=sha)

    def test_checkpoint_generator_binding_and_pre_freeze_attempt_accounting(self) -> None:
        module_bytes = Path(w7_unit3.__file__).read_bytes()
        resolved = b"f" * 40 + b"\n"
        with patch("genshin_corpus.retrieval.w7_unit3.subprocess.run", side_effect=[subprocess.CompletedProcess([], 0, resolved), subprocess.CompletedProcess([], 0, module_bytes)]):
            binding = verify_checkpoint_generator_binding("f" * 40)
        self.assertEqual(binding["generator_sha256"], hashlib.sha256(module_bytes).hexdigest())
        with patch("genshin_corpus.retrieval.w7_unit3.subprocess.run", side_effect=[subprocess.CompletedProcess([], 0, resolved), subprocess.CompletedProcess([], 0, b"wrong")]):
            with self.assertRaises(Unit3Blocked):
                verify_checkpoint_generator_binding("f" * 40)
        failure = _attempt_record(1, "MECHANICAL_FAILURE", "DISK_IO_FAILURE", binding)
        complete = _attempt_record(2, "COMPLETE", None, binding)
        self.assertEqual(failure["attempt_number"], 1)
        self.assertEqual(complete["attempt_number"], 2)

    def test_staged_acceptance_cleans_failures_and_blocks_replacement(self) -> None:
        class FakeFilesystem:
            accepted = False
            staging = False
            fail_manifest_write = False
            fail_replace = False

        filesystem = FakeFilesystem()

        class FakePath:
            def __init__(self, name: str) -> None:
                self.name = name

            def __truediv__(self, child: str) -> "FakePath":
                return FakePath(f"{self.name}/{child}")

            @property
            def parent(self) -> "FakePath":
                return FakePath(self.name.rsplit("/", 1)[0])

            def exists(self) -> bool:
                return filesystem.accepted if self.name.endswith("unit3_review_pack_accepted") else filesystem.staging

            def mkdir(self, **_: object) -> None:
                filesystem.staging = True

            def write_bytes(self, _: bytes) -> None:
                if filesystem.fail_manifest_write and self.name.endswith("review_pack_manifest.json"):
                    raise OSError("synthetic manifest failure")
                filesystem.staging = True

            def replace(self, target: "FakePath") -> None:
                if filesystem.fail_replace:
                    raise OSError("synthetic replace failure")
                filesystem.staging = False
                filesystem.accepted = target.name.endswith("unit3_review_pack_accepted")

        def writer(path: FakePath, _: object) -> dict[str, object]:
            filesystem.staging = True
            return {"path": path.name.rsplit("/", 1)[-1], "sha256": "e" * 64, "byte_count": 1, "row_count": 1}

        def cleanup(_: FakePath) -> None:
            filesystem.staging = False

        manifest = {"schema_version": "synthetic"}
        integrity = {"candidate_count": 48, "forbidden_field_count": 0, "outside_scope_text_count": 0, "legacy_c1_sensitive_field_count": 0}
        with patch("genshin_corpus.retrieval.w7_unit3._write_jsonl_gzip", side_effect=writer), patch("genshin_corpus.retrieval.w7_unit3._read_jsonl_gzip", return_value=[{}]), patch("genshin_corpus.retrieval.w7_unit3._sha256_path", return_value="e" * 64), patch("genshin_corpus.retrieval.w7_unit3.validate_review_pack", return_value=integrity), patch("genshin_corpus.retrieval.w7_unit3.shutil.rmtree", side_effect=cleanup):
            with self.assertRaises(Unit3Blocked):
                _stage_and_accept_review_pack(FakePath("root"), attempt_number=1, pack=[{}], manifest=manifest, fault_injector=lambda phase: (_ for _ in ()).throw(OSError("synthetic pack-to-manifest failure")) if phase == "after_pack_write" else None)
            self.assertFalse(filesystem.accepted)
            self.assertFalse(filesystem.staging)
            filesystem.fail_manifest_write = True
            with self.assertRaises(Unit3Blocked):
                _stage_and_accept_review_pack(FakePath("root"), attempt_number=2, pack=[{}], manifest=manifest)
            self.assertFalse(filesystem.accepted)
            self.assertFalse(filesystem.staging)
            filesystem.fail_manifest_write = False
            filesystem.fail_replace = True
            with self.assertRaises(Unit3Blocked):
                _stage_and_accept_review_pack(FakePath("root"), attempt_number=3, pack=[{}], manifest=manifest)
            self.assertFalse(filesystem.accepted)
            self.assertFalse(filesystem.staging)
            filesystem.fail_replace = False
            _, accepted_manifest = _stage_and_accept_review_pack(FakePath("root"), attempt_number=4, pack=[{}], manifest=manifest)
            self.assertTrue(filesystem.accepted)
            self.assertIn("review_pack", accepted_manifest)
            with self.assertRaises(Unit3Blocked):
                _stage_and_accept_review_pack(FakePath("root"), attempt_number=5, pack=[{}], manifest=manifest)

    def test_mechanical_serialization_is_deterministic(self) -> None:
        self.assertEqual(canonical_json_bytes({"schema": "x", "queue_order": list(QUEUE_ORDER)}), canonical_json_bytes({"queue_order": list(QUEUE_ORDER), "schema": "x"}))


if __name__ == "__main__":
    unittest.main()
