from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.generation.generation import project_generation_request
from genshin_corpus.rag.adaptive import (
    AnswerDisposition,
    EvidenceAssessmentResult,
    EvidenceCondition,
    OrchestrationAction,
)
from genshin_corpus.rag.p05_navigation import run_p05_navigation, write_navigation_result
from genshin_corpus.retrieval.evidence_assembly import EVIDENCE_PACKET_SCHEMA_VERSION
from genshin_corpus.retrieval.semantic_compilation import (
    SemanticBuild,
    SemanticItem,
    SemanticStage,
    SourceBinding,
    TraversalPolicy,
    build_views,
    load_semantic_build,
    merge_navigation_candidate_rows,
    traverse_graph,
    write_semantic_build,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "retrieval" / "p05-story-rus.json"


def _packet(unit_ids: list[str]) -> dict:
    return {
        "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
        "assembly_version": "phase04-assembly-fixture",
        "retrieval_unit_build": {"build_identity": "ru-fixture"},
        "assembly_config_identity": "assembly-fixture",
        "selection_policy_identity": "selection-fixture",
        "evidence": [
            {"evidence_id": f"E{index:02d}", "text": f"evidence {unit_id}", "members": [{"unit_id": unit_id}]}
            for index, unit_id in enumerate(unit_ids, 1)
        ],
    }


def _assessment(condition: EvidenceCondition, action: OrchestrationAction, disposition: AnswerDisposition = AnswerDisposition.NONE) -> EvidenceAssessmentResult:
    return EvidenceAssessmentResult(
        assessment_request_identity="assessment-fixture",
        condition=condition,
        action=action,
        answer_disposition=disposition,
        missing_information=("missing hop",) if condition is EvidenceCondition.INCOMPLETE else (),
        supplemental_query="find the missing hop" if action is OrchestrationAction.SUPPLEMENT_ONCE else None,
    )


class _Decision:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def decide(self, question, *, build_identity, packet_binding):
        self.calls += 1
        return self.value


class P05SemanticNavigationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("tmp") / f"p05-semantic-{uuid4().hex}"
        self.root.mkdir(parents=True)
        self.rows = {row["unit_id"]: row for row in json.loads(FIXTURE.read_text(encoding="utf-8"))}
        bindings = {
            unit_id: SourceBinding(
                unit_id=unit_id,
                canonical_address=row["source"]["canonical_address"],
                lineage=row["source"]["lineage"],
                quoted_text=row["retrieval_visible_text"],
            )
            for unit_id, row in self.rows.items()
        }
        self.items = [
            SemanticItem("i1", "relation", "A trusts B", (bindings["story-a-u1"],), semantic_acceptance_status="curated_recorded", topic_path=("story", "arc"), subject_ref="A", object_ref="B", predicate="trusts"),
            SemanticItem("i2", "relation", "B helps C", (bindings["story-b-u2"],), semantic_acceptance_status="accepted_for_fixture_test", topic_path=("story", "arc"), subject_ref="B", object_ref="C", predicate="helps"),
            SemanticItem("i3", "relation", "C leads D", (bindings["story-c-u3"],), semantic_acceptance_status="accepted_for_fixture_test", topic_path=("story", "arc"), subject_ref="C", object_ref="D", predicate="leads_to"),
            SemanticItem("i4", "relation", "D returns A", (bindings["story-d-u4"],), semantic_acceptance_status="accepted_for_fixture_test", topic_path=("story", "arc"), subject_ref="D", object_ref="A", predicate="returns_to"),
            SemanticItem("bad", "fact", "unaccepted but source-bound", (bindings["story-a-u1"],), semantic_acceptance_status="not_accepted", topic_path=("story", "arc"), subject_ref="A", object_ref="B", predicate="unaccepted"),
            SemanticItem("unsupported", "fact", "unsupported compiler output", (bindings["story-a-u1"],), semantic_acceptance_status="unsupported", compiler_stage_identity="extract-v1", compiler_run_identity="run-1", raw_response_identity="raw-unsupported-1", topic_path=("story", "arc"), subject_ref="A", object_ref="B", predicate="unsupported"),
            SemanticItem("ambiguous", "fact", "ambiguous compiler output", (bindings["story-b-u2"],), semantic_acceptance_status="ambiguous", compiler_stage_identity="extract-v1", compiler_run_identity="run-1", raw_response_identity="raw-ambiguous-1", topic_path=("story", "arc"), subject_ref="B", object_ref="C", predicate="ambiguous"),
            SemanticItem("binding-ambiguous", "fact", "ambiguous source binding", (bindings["story-c-u3"],), source_binding_status="ambiguous", semantic_acceptance_status="curated_recorded", compiler_stage_identity="extract-v1", compiler_run_identity="run-1", raw_response_identity="raw-binding-ambiguous-1", topic_path=("story", "arc"), subject_ref="C", object_ref="D", predicate="ambiguous_binding"),
            SemanticItem("wrong", "fact", "wrong quote", (SourceBinding("story-a-u1", bindings["story-a-u1"].canonical_address, bindings["story-a-u1"].lineage, "not authoritative"),), semantic_acceptance_status="curated_recorded", topic_path=("story", "arc"), subject_ref="A", object_ref="B", predicate="wrong"),
        ]
        self.build = SemanticBuild.from_items(
            input_identity="story-fixture-input",
            stages=(SemanticStage("fixture", "fixture-stage-v1", "story-fixture-input", "complete", {"provider_calls": 0}),),
            items=self.items,
            ru_index=self.rows,
        )
        self.views = build_views(self.build)

    def tearDown(self):
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_binding_and_acceptance_are_separate_and_rejected_items_are_inactive(self):
        self.assertEqual({item.item_id for item in self.build.active_items}, {"i1", "i2", "i3", "i4"})
        inactive = {row["item"]["item_id"]: row for row in self.build.inactive_ledger}
        self.assertEqual(inactive["bad"]["source_binding_status"], "verified")
        self.assertEqual(inactive["bad"]["semantic_acceptance_status"], "not_accepted")
        self.assertEqual(inactive["unsupported"]["source_binding_status"], "verified")
        self.assertEqual(inactive["unsupported"]["raw_response_identity"], "raw-unsupported-1")
        self.assertEqual(inactive["ambiguous"]["semantic_acceptance_status"], "ambiguous")
        self.assertEqual(inactive["binding-ambiguous"]["source_binding_status"], "ambiguous")
        self.assertEqual(inactive["wrong"]["source_binding_status"], "rejected")
        self.assertNotIn("bad", [item.item_id for item in self.build.active_items])

    def test_views_share_build_but_hierarchy_is_cross_record_and_graph_is_distinct(self):
        self.assertEqual(self.views.hierarchy.build_identity, self.views.graph.build_identity)
        self.assertNotEqual(self.views.hierarchy.view_identity, self.views.graph.view_identity)
        arc = next(node for node in self.views.hierarchy.nodes.values() if node.path == ("story", "arc"))
        self.assertEqual(set(arc.source_unit_ids), {"story-a-u1", "story-b-u2", "story-c-u3", "story-d-u4"})
        self.assertEqual(len(self.views.graph.edges), 4)

    def test_bounded_cycle_traversal_produces_two_two_hop_and_one_three_hop_routes(self):
        def trace(start, hops):
            return traverse_graph(self.views.graph, [start], route_id=f"route-{start}-{hops}", policy=TraversalPolicy(max_hops=hops, max_nodes=10, max_edges=10, max_candidates=10))
        two_a = trace("A", 2)
        two_b = trace("B", 2)
        three = trace("A", 3)
        self.assertEqual(set(two_a.selected_unit_ids), {"story-a-u1", "story-b-u2"})
        self.assertEqual(set(two_b.selected_unit_ids), {"story-b-u2", "story-c-u3"})
        self.assertEqual(set(three.selected_unit_ids), {"story-a-u1", "story-b-u2", "story-c-u3"})
        self.assertLessEqual(len(three.visited_nodes), 10)

    def test_persistence_reuse_and_overwrite_refusal(self):
        views = self.views
        first = write_semantic_build(self.root / "build", self.build, views)
        second = write_semantic_build(self.root / "build", self.build, views)
        self.assertEqual(first, second)
        loaded = load_semantic_build(self.root / "build")
        self.assertEqual(loaded["manifest"]["build_identity"], self.build.build_identity)
        tampered = self.root / "build" / "graph.json"
        tampered.write_bytes(b"tampered")
        with self.assertRaisesRegex(Exception, "refusing to overwrite"):
            write_semantic_build(self.root / "build", self.build, views)
        with self.assertRaisesRegex(Exception, "cannot load semantic build"):
            load_semantic_build(self.root / "build")

    def test_item_input_order_is_incidental_to_build_identity(self):
        reversed_build = SemanticBuild.from_items(
            input_identity="story-fixture-input",
            stages=(SemanticStage("fixture", "fixture-stage-v1", "story-fixture-input", "complete", {"provider_calls": 0}),),
            items=tuple(reversed(self.items)),
            ru_index=self.rows,
        )
        self.assertEqual(reversed_build.build_identity, self.build.build_identity)

    def test_partial_stage_and_reuse_lineage_are_persisted(self):
        partial = SemanticBuild.from_items(
            input_identity="story-fixture-input-v2",
            stages=(SemanticStage("extract", "extract-v1", "story-fixture-input-v2", "partial_failed", {"provider_calls": 0}, failure_reason="fixture interruption"),),
            items=self.items[:4],
            ru_index=self.rows,
            reused_item_ids=("i1",),
        )
        manifest = write_semantic_build(self.root / "partial", partial, build_views(partial))["manifest"]
        self.assertEqual(manifest["counts"]["reused_items"], 1)
        self.assertEqual(manifest["reused_item_ids"], ["i1"])
        self.assertEqual(manifest["stages"][0]["status"], "partial_failed")
        self.assertEqual(load_semantic_build(self.root / "partial")["manifest"]["stages"][0]["failure_reason"], "fixture interruption")
        recovered = SemanticBuild.from_items(
            input_identity="story-fixture-input-v2",
            stages=(SemanticStage("extract", "extract-v1", "story-fixture-input-v2", "complete", {"provider_calls": 0}),),
            items=self.items[:4],
            ru_index=self.rows,
            reused_item_ids=("i1", "i2", "i3", "i4"),
        )
        recovered_manifest = write_semantic_build(self.root / "recovered", recovered, build_views(recovered))["manifest"]
        self.assertEqual(recovered_manifest["counts"]["reused_items"], 4)
        self.assertEqual(recovered_manifest["reused_item_ids"], ["i1", "i2", "i3", "i4"])
        self.assertEqual(load_semantic_build(self.root / "recovered")["manifest"]["counts"]["active_items"], 4)

    def test_invalid_reused_item_identity_is_rejected(self):
        with self.assertRaisesRegex(Exception, "reused_item_ids"):
            SemanticBuild.from_items(
                input_identity="story-fixture-input",
                stages=(SemanticStage("fixture", "fixture-stage-v1", "story-fixture-input", "complete", {"provider_calls": 0}),),
                items=self.items[:4],
                ru_index=self.rows,
                reused_item_ids=("not-active",),
            )

    def test_persisted_reuse_identity_is_validated_on_reload(self):
        root = self.root / "reuse-validation"
        write_semantic_build(root, self.build, self.views)
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["reused_item_ids"] = ["not-active"]
        manifest["counts"]["reused_items"] = 1
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(Exception, "reused_item_ids are not active"):
            load_semantic_build(root)

    def test_reuse_lineage_is_bound_to_build_identity(self):
        reused = SemanticBuild.from_items(
            input_identity="story-fixture-input",
            stages=(SemanticStage("fixture", "fixture-stage-v1", "story-fixture-input", "complete", {"provider_calls": 0}),),
            items=self.items[:4],
            ru_index=self.rows,
            reused_item_ids=("i1",),
        )
        self.assertNotEqual(reused.build_identity, self.build.build_identity)

    def test_sufficient_baseline_does_not_call_navigation(self):
        decision = _Decision({"action": "navigate", "route_id": "should-not-run", "graph_start_nodes": ["A"]})
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.SUFFICIENT, OrchestrationAction.ANSWER_NOW, AnswerDisposition.FULL),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet([row["unit_id"] for row in rows]),
        )
        self.assertEqual(result.navigation_status, "not_required")
        self.assertEqual(result.answer_disposition, "full")
        self.assertEqual(decision.calls, 0)

    def test_navigation_failure_is_explicit_and_not_full_answer(self):
        decision = _Decision({"action": "navigate", "route_id": "missing", "graph_start_nodes": ["unknown"]})
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet([row["unit_id"] for row in rows]),
        )
        self.assertEqual(result.navigation_status, "failed")
        self.assertEqual(result.answer_disposition, "none")
        self.assertNotEqual(result.answer_disposition, "full")
        self.assertEqual(result.packet_binding.visible_unit_ids, ("story-a-u1",))

    def test_navigation_with_no_resolved_units_cannot_succeed_on_baseline_rows(self):
        root_node = next(node for node in self.views.hierarchy.nodes.values() if node.path == ())
        decision = _Decision({
            "action": "navigate",
            "route_id": "root-depth-one",
            "hierarchy_node_ids": [root_node.node_id],
            "policy": {"max_hops": 1, "max_nodes": 10, "max_edges": 10, "max_candidates": 10},
        })
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet([row["unit_id"] for row in rows]),
            baseline_candidates=({"unit_id": "story-a-u1", "rank": 1},),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.navigation_status, "failed")
        self.assertEqual(result.resolved_unit_ids, ())
        self.assertEqual(result.error["category"], "navigation_no_progress")
        self.assertNotEqual(result.answer_disposition, "full")

    def test_baseline_overlap_without_novel_evidence_is_explicit_failure(self):
        decision = _Decision({"action": "navigate", "route_id": "overlap-only", "graph_start_nodes": ["A"], "policy": {"max_hops": 1}})
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet([row["unit_id"] for row in rows]),
            baseline_candidates=({"unit_id": "story-a-u1", "rank": 1},),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.navigation_status, "failed")
        self.assertEqual(result.resolved_unit_ids, ("story-a-u1",))
        self.assertEqual(result.baseline_overlap_unit_ids, ("story-a-u1",))
        self.assertEqual(result.novel_resolved_unit_ids, ())
        self.assertEqual(result.error["category"], "navigation_no_progress")

    def test_hierarchy_root_respects_hop_bound(self):
        root_node = next(node for node in self.views.hierarchy.nodes.values() if node.path == ())
        story_node = next(node for node in self.views.hierarchy.nodes.values() if node.path == ("story",))
        arc_node = next(node for node in self.views.hierarchy.nodes.values() if node.path == ("story", "arc"))
        self.assertEqual(root_node.source_unit_ids, ())
        self.assertEqual(story_node.source_unit_ids, ())
        self.assertEqual(set(arc_node.source_unit_ids), set(self.rows))

    def test_hierarchy_only_navigation_persists_route_trace(self):
        arc_node = next(node for node in self.views.hierarchy.nodes.values() if node.path == ("story", "arc"))
        decision = _Decision({"action": "navigate", "route_id": "hierarchy-only", "hierarchy_node_ids": [arc_node.node_id], "policy": {"max_hops": 1, "max_nodes": 4, "max_candidates": 4}})
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet([row["unit_id"] for row in rows]),
        )
        self.assertEqual(result.status, "succeeded")
        self.assertIsNotNone(result.hierarchy_trace)
        self.assertEqual(result.hierarchy_trace.seed_node_ids, (arc_node.node_id,))
        self.assertEqual(result.hierarchy_trace.termination, "budget_exhausted")

    def test_hierarchy_edge_budget_is_hard_and_audited(self):
        root_node = next(node for node in self.views.hierarchy.nodes.values() if node.path == ())
        decision = _Decision({
            "action": "navigate",
            "route_id": "hierarchy-edge-budget",
            "hierarchy_node_ids": [root_node.node_id],
            "policy": {"max_hops": 3, "max_nodes": 10, "max_edges": 1, "max_candidates": 10},
        })
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet([row["unit_id"] for row in rows]),
        )
        self.assertEqual(result.status, "failed")
        self.assertIsNotNone(result.hierarchy_trace)
        self.assertLessEqual(len(result.hierarchy_trace.traversed_edges), 1)
        self.assertTrue(result.hierarchy_trace.truncated)
        self.assertEqual(result.hierarchy_trace.termination, "budget_exhausted")

    def test_navigation_candidates_are_packet_visible_and_route_trace_is_not_generation_input(self):
        decision = _Decision({"action": "navigate", "route_id": "three-hop", "graph_start_nodes": ["A"], "policy": {"max_hops": 3, "max_nodes": 10, "max_edges": 10, "max_candidates": 3}})
        captured = {}

        def rebuild(rows, audit):
            captured["audit"] = audit
            return _packet([row["unit_id"] for row in rows])

        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=rebuild,
            baseline_candidates=({"unit_id": "story-a-u1", "rank": 1, "retrieval": {"mode": "hybrid"}},),
        )
        self.assertEqual(result.status, "succeeded")
        self.assertIn("story-c-u3", result.packet_binding.visible_unit_ids)
        self.assertEqual(set(result.resolved_unit_ids), {"story-a-u1", "story-b-u2", "story-c-u3"})
        self.assertEqual(set(result.admitted_unit_ids), set(result.resolved_unit_ids))
        self.assertEqual(set(result.visible_unit_ids), set(result.resolved_unit_ids))
        self.assertEqual(result.omitted_unit_ids, ())
        self.assertEqual(set(result.novel_visible_unit_ids), {"story-b-u2", "story-c-u3"})
        self.assertIn("trace", captured["audit"]["p05_navigation"])
        generation_request = project_generation_request(result.packet, question="question")
        projection = str(generation_request.semantic_projection())
        self.assertNotIn("p05_navigation", projection)
        self.assertNotIn("three-hop", projection)

    def test_navigation_result_overwrite_refusal(self):
        decision = _Decision({"action": "stop", "route_id": "stop-route"})
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet([row["unit_id"] for row in rows]),
        )
        write_navigation_result(self.root / "navigation", result)
        write_navigation_result(self.root / "navigation", result)
        tampered = self.root / "navigation" / "p05_navigation_result.json"
        tampered.write_bytes(b"tampered")
        with self.assertRaisesRegex(Exception, "refusing to overwrite"):
            write_navigation_result(self.root / "navigation", result)

    def test_rebuilt_packet_cannot_introduce_non_authoritative_units(self):
        decision = _Decision({"action": "navigate", "route_id": "bad-packet", "graph_start_nodes": ["B"], "policy": {"max_hops": 1}})
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet(["not-an-official-ru"]),
        )
        self.assertEqual(result.navigation_status, "failed")
        self.assertEqual(result.error["category"], "navigation_execution_failure")

    def test_admitted_navigation_candidate_omitted_from_packet_is_failure(self):
        decision = _Decision({"action": "navigate", "route_id": "omitted", "graph_start_nodes": ["B"], "policy": {"max_hops": 1}})
        result = run_p05_navigation(
            "question",
            round0_packet=_packet(["story-a-u1"]),
            assessment=_assessment(EvidenceCondition.INCOMPLETE, OrchestrationAction.SUPPLEMENT_ONCE),
            decision_provider=decision,
            views=self.views,
            ru_index=self.rows,
            candidate_builder=lambda candidates, baseline: merge_navigation_candidate_rows(candidates, baseline),
            rebuild_packet=lambda rows, audit: _packet(["story-a-u1"]),
            baseline_candidates=({"unit_id": "story-a-u1", "rank": 1},),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.navigation_status, "failed")
        self.assertEqual(set(result.resolved_unit_ids), {"story-b-u2"})
        self.assertEqual(result.baseline_overlap_unit_ids, ())
        self.assertEqual(result.novel_resolved_unit_ids, ("story-b-u2",))
        self.assertEqual(set(result.admitted_unit_ids), {"story-b-u2"})
        self.assertEqual(result.novel_admitted_unit_ids, ("story-b-u2",))
        self.assertEqual(result.visible_unit_ids, ())
        self.assertEqual(result.novel_visible_unit_ids, ())
        self.assertEqual(set(result.omitted_unit_ids), {"story-b-u2"})
        self.assertEqual(result.error["category"], "navigation_packet_visibility_failure")


if __name__ == "__main__":
    unittest.main()
