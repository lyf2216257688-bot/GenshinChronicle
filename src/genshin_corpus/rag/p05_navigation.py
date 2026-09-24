"""Provider-neutral Phase 05 bounded semantic navigation orchestration.

This module owns only online decisions and route lineage.  Retrieval owns the
resolved-RU candidate primitive; Generation remains unchanged and receives only
the final Evidence Packet through its existing projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .adaptive import (
    AnswerDisposition,
    EvidenceAssessmentResult,
    EvidenceCondition,
    OrchestrationAction,
    PacketBinding,
    bind_evidence_packet,
)
from genshin_corpus.retrieval.semantic_compilation import (
    NavigationCandidateInput,
    NavigationDecisionProvider,
    NavigationTrace,
    SemanticViews,
    TraversalPolicy,
    resolve_navigation_candidates,
    traverse_graph,
)


P05_NAVIGATION_RESULT_SCHEMA_VERSION = "phase05-bounded-navigation-result-0.1"
P05_NAVIGATION_DECISION_SCHEMA_VERSION = "phase05-navigation-decision-0.1"


class P05NavigationError(ValueError):
    """Raised when a Phase 05 navigation contract is invalid."""


@dataclass(frozen=True)
class NavigationDecision:
    action: str
    route_id: str
    hierarchy_node_ids: tuple[str, ...] = ()
    graph_start_nodes: tuple[str, ...] = ()
    policy: TraversalPolicy = TraversalPolicy()

    def __post_init__(self) -> None:
        if self.action not in {"navigate", "stop"}:
            raise P05NavigationError("navigation action must be navigate or stop")
        if not isinstance(self.route_id, str) or not self.route_id.strip():
            raise P05NavigationError("navigation route_id is required")
        for label, values in (("hierarchy_node_ids", self.hierarchy_node_ids), ("graph_start_nodes", self.graph_start_nodes)):
            if not isinstance(values, tuple) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise P05NavigationError(f"{label} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise P05NavigationError(f"{label} must not contain duplicates")
        if self.action == "navigate" and not (self.hierarchy_node_ids or self.graph_start_nodes):
            raise P05NavigationError("navigate decision requires a hierarchy or graph seed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": P05_NAVIGATION_DECISION_SCHEMA_VERSION,
            "action": self.action,
            "route_id": self.route_id,
            "hierarchy_node_ids": list(self.hierarchy_node_ids),
            "graph_start_nodes": list(self.graph_start_nodes),
            "policy": self.policy.__dict__,
        }


def parse_navigation_decision(value: Mapping[str, Any]) -> NavigationDecision:
    if not isinstance(value, Mapping):
        raise P05NavigationError("navigation decision must be an object")
    policy_value = value.get("policy", {})
    if not isinstance(policy_value, Mapping):
        raise P05NavigationError("navigation decision policy must be an object")
    policy = TraversalPolicy(
        max_hops=policy_value.get("max_hops", 3),
        max_nodes=policy_value.get("max_nodes", 32),
        max_edges=policy_value.get("max_edges", 64),
        max_candidates=policy_value.get("max_candidates", 32),
    )
    def ids(name: str) -> tuple[str, ...]:
        raw = value.get(name, ())
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise P05NavigationError(f"{name} must be an array")
        return tuple(raw)

    return NavigationDecision(
        action=str(value.get("action", "")),
        route_id=str(value.get("route_id", "")),
        hierarchy_node_ids=ids("hierarchy_node_ids"),
        graph_start_nodes=ids("graph_start_nodes"),
        policy=policy,
    )


@dataclass(frozen=True)
class HierarchyTrace:
    route_id: str
    build_identity: str
    seed_node_ids: tuple[str, ...]
    visited_node_ids: tuple[str, ...]
    traversed_edges: tuple[str, ...]
    selected_unit_ids: tuple[str, ...]
    truncated: bool
    termination: str
    policy: TraversalPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "build_identity": self.build_identity,
            "seed_node_ids": list(self.seed_node_ids),
            "visited_node_ids": list(self.visited_node_ids),
            "traversed_edges": list(self.traversed_edges),
            "selected_unit_ids": list(self.selected_unit_ids),
            "truncated": self.truncated,
            "termination": self.termination,
            "policy": self.policy.__dict__,
        }


@dataclass(frozen=True)
class P05NavigationResult:
    status: str
    condition: str
    action: str
    answer_disposition: str
    navigation_status: str
    packet: Mapping[str, Any]
    packet_binding: PacketBinding
    build_identity: str | None
    decision: Mapping[str, Any] | None = None
    trace: NavigationTrace | None = None
    hierarchy_trace: HierarchyTrace | None = None
    candidate_rows: tuple[Mapping[str, Any], ...] = ()
    resolved_unit_ids: tuple[str, ...] = ()
    baseline_overlap_unit_ids: tuple[str, ...] = ()
    novel_resolved_unit_ids: tuple[str, ...] = ()
    admitted_unit_ids: tuple[str, ...] = ()
    novel_admitted_unit_ids: tuple[str, ...] = ()
    visible_unit_ids: tuple[str, ...] = ()
    novel_visible_unit_ids: tuple[str, ...] = ()
    omitted_unit_ids: tuple[str, ...] = ()
    error: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "stopped", "failed"}:
            raise P05NavigationError("navigation result status is unsupported")
        if self.navigation_status not in {"not_required", "completed", "stopped", "failed"}:
            raise P05NavigationError("navigation_status is unsupported")
        if self.answer_disposition not in {value.value for value in AnswerDisposition}:
            raise P05NavigationError("answer disposition is unsupported")
        if not isinstance(self.packet, Mapping) or not isinstance(self.packet_binding, PacketBinding):
            raise P05NavigationError("navigation result requires a Packet and binding")
        if bind_evidence_packet(self.packet) != self.packet_binding:
            raise P05NavigationError("navigation result Packet binding mismatch")
        for label, values in (
            ("resolved_unit_ids", self.resolved_unit_ids),
            ("baseline_overlap_unit_ids", self.baseline_overlap_unit_ids),
            ("novel_resolved_unit_ids", self.novel_resolved_unit_ids),
            ("admitted_unit_ids", self.admitted_unit_ids),
            ("novel_admitted_unit_ids", self.novel_admitted_unit_ids),
            ("visible_unit_ids", self.visible_unit_ids),
            ("novel_visible_unit_ids", self.novel_visible_unit_ids),
            ("omitted_unit_ids", self.omitted_unit_ids),
        ):
            if any(not isinstance(value, str) or not value for value in values):
                raise P05NavigationError(f"{label} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise P05NavigationError(f"{label} must not contain duplicates")
        resolved = set(self.resolved_unit_ids)
        if not set(self.baseline_overlap_unit_ids) <= resolved or not set(self.novel_resolved_unit_ids) <= resolved:
            raise P05NavigationError("navigation novelty lineage is inconsistent")
        if set(self.baseline_overlap_unit_ids) & set(self.novel_resolved_unit_ids):
            raise P05NavigationError("navigation novelty sets overlap")
        if resolved != set(self.baseline_overlap_unit_ids) | set(self.novel_resolved_unit_ids):
            raise P05NavigationError("navigation novelty sets do not cover resolved candidates")
        if not set(self.admitted_unit_ids) <= resolved or not set(self.novel_resolved_unit_ids) >= set(self.novel_admitted_unit_ids):
            raise P05NavigationError("navigation admission lineage is inconsistent")
        if not set(self.admitted_unit_ids) >= set(self.novel_admitted_unit_ids) or not set(self.visible_unit_ids) <= set(self.admitted_unit_ids):
            raise P05NavigationError("navigation outcome lineage is inconsistent")
        if not set(self.novel_visible_unit_ids) <= set(self.visible_unit_ids) or not set(self.novel_visible_unit_ids) <= set(self.novel_admitted_unit_ids):
            raise P05NavigationError("navigation visibility lineage is inconsistent")
        if not set(self.omitted_unit_ids) <= resolved:
            raise P05NavigationError("navigation omission lineage is inconsistent")
        if self.navigation_status == "completed" and not self.novel_visible_unit_ids:
            raise P05NavigationError("completed navigation requires visible navigation evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": P05_NAVIGATION_RESULT_SCHEMA_VERSION,
            "status": self.status,
            "condition": self.condition,
            "action": self.action,
            "answer_disposition": self.answer_disposition,
            "navigation_status": self.navigation_status,
            "build_identity": self.build_identity,
            "decision": dict(self.decision) if self.decision is not None else None,
            "trace": self.trace.to_dict() if self.trace is not None else None,
            "hierarchy_trace": self.hierarchy_trace.to_dict() if self.hierarchy_trace is not None else None,
            "candidate_rows": [dict(row) for row in self.candidate_rows],
            "resolved_unit_ids": list(self.resolved_unit_ids),
            "baseline_overlap_unit_ids": list(self.baseline_overlap_unit_ids),
            "novel_resolved_unit_ids": list(self.novel_resolved_unit_ids),
            "admitted_unit_ids": list(self.admitted_unit_ids),
            "novel_admitted_unit_ids": list(self.novel_admitted_unit_ids),
            "visible_unit_ids": list(self.visible_unit_ids),
            "novel_visible_unit_ids": list(self.novel_visible_unit_ids),
            "omitted_unit_ids": list(self.omitted_unit_ids),
            "packet_binding": self.packet_binding.to_dict(),
            "error": dict(self.error) if self.error is not None else None,
        }


def _error(category: str, message: str) -> dict[str, Any]:
    return {"stage": "navigation", "category": category, "message": message, "retryable": False}


def _baseline_result(packet: Mapping[str, Any], assessment: EvidenceAssessmentResult) -> P05NavigationResult:
    binding = bind_evidence_packet(packet)
    return P05NavigationResult(
        status="succeeded",
        condition=assessment.condition.value,
        action=assessment.action.value,
        answer_disposition=assessment.answer_disposition.value,
        navigation_status="not_required",
        packet=packet,
        packet_binding=binding,
        build_identity=None,
    )


def _failure_result(
    packet: Mapping[str, Any],
    assessment: EvidenceAssessmentResult,
    *,
    category: str,
    message: str,
    build_identity: str | None = None,
    decision: Mapping[str, Any] | None = None,
    trace: NavigationTrace | None = None,
    hierarchy_trace: HierarchyTrace | None = None,
    candidate_rows: Sequence[Mapping[str, Any]] = (),
    resolved_unit_ids: Sequence[str] = (),
    baseline_overlap_unit_ids: Sequence[str] = (),
    novel_resolved_unit_ids: Sequence[str] = (),
    admitted_unit_ids: Sequence[str] = (),
    novel_admitted_unit_ids: Sequence[str] = (),
    visible_unit_ids: Sequence[str] = (),
    novel_visible_unit_ids: Sequence[str] = (),
    omitted_unit_ids: Sequence[str] = (),
) -> P05NavigationResult:
    binding = bind_evidence_packet(packet)
    # W1 uses the narrow legal stop operating point.  A future caller may
    # provide a separately reassessed bounded-partial result; no full answer is
    # synthesized from a failed navigation attempt.
    return P05NavigationResult(
        status="failed" if category in {
            "invalid_decision",
            "navigation_provider_failure",
            "navigation_execution_failure",
            "navigation_no_progress",
            "navigation_admission_failure",
            "navigation_packet_visibility_failure",
        } else "stopped",
        condition=assessment.condition.value,
        action=assessment.action.value,
        answer_disposition=AnswerDisposition.NONE.value,
        navigation_status="failed" if category != "navigation_stopped" else "stopped",
        packet=packet,
        packet_binding=binding,
        build_identity=build_identity,
        decision=decision,
        trace=trace,
        hierarchy_trace=hierarchy_trace,
        candidate_rows=tuple(dict(row) for row in candidate_rows),
        resolved_unit_ids=tuple(resolved_unit_ids),
        baseline_overlap_unit_ids=tuple(baseline_overlap_unit_ids),
        novel_resolved_unit_ids=tuple(novel_resolved_unit_ids),
        admitted_unit_ids=tuple(admitted_unit_ids),
        novel_admitted_unit_ids=tuple(novel_admitted_unit_ids),
        visible_unit_ids=tuple(visible_unit_ids),
        novel_visible_unit_ids=tuple(novel_visible_unit_ids),
        omitted_unit_ids=tuple(omitted_unit_ids),
        error=_error(category, message),
    )


def _hierarchy_unit_ids(
    views: SemanticViews,
    node_ids: Sequence[str],
    *,
    route_id: str,
    policy: TraversalPolicy,
) -> tuple[set[str], HierarchyTrace | None]:
    result: set[str] = set()
    visited: set[str] = set()
    pending: list[tuple[str, int]] = []
    for node_id in node_ids:
        node = views.hierarchy.nodes.get(node_id)
        if node is None:
            raise P05NavigationError(f"unknown hierarchy node: {node_id}")
        pending.append((node_id, 0))
    if not pending:
        return set(), None
    seed_node_ids = tuple(node_ids)
    traversed_edges: list[str] = []
    truncated = False
    hop_bound_reached = False
    while pending:
        if len(visited) >= policy.max_nodes:
            truncated = True
            break
        current_id, depth = pending.pop(0)
        if current_id in visited:
            continue
        current = views.hierarchy.nodes.get(current_id)
        if current is None:
            raise P05NavigationError(f"hierarchy node is missing: {current_id}")
        visited.add(current_id)
        result.update(current.source_unit_ids)
        if len(result) >= policy.max_candidates:
            truncated = True
            break
        if depth >= policy.max_hops:
            if current.child_ids:
                hop_bound_reached = True
            continue
        for child_id in current.child_ids:
            if len(traversed_edges) >= policy.max_edges:
                truncated = True
                break
            traversed_edges.append(f"{current_id}->{child_id}")
            pending.append((child_id, depth + 1))
        if truncated:
            break
    termination = "budget_exhausted" if truncated else "hop_bound_reached" if hop_bound_reached else "frontier_exhausted"
    if len(result) > policy.max_candidates:
        truncated = True
        result = set(sorted(result)[: policy.max_candidates])
        termination = "budget_exhausted"
    return result, HierarchyTrace(
        route_id=route_id,
        build_identity=views.build_identity,
        seed_node_ids=seed_node_ids,
        visited_node_ids=tuple(sorted(visited)),
        traversed_edges=tuple(traversed_edges),
        selected_unit_ids=tuple(sorted(result)),
        truncated=truncated,
        termination=termination,
        policy=policy,
    )


def run_p05_navigation(
    question: str,
    *,
    round0_packet: Mapping[str, Any],
    assessment: EvidenceAssessmentResult,
    decision_provider: NavigationDecisionProvider,
    views: SemanticViews,
    ru_index: Mapping[str, Mapping[str, Any]],
    candidate_builder: Callable[[Sequence[NavigationCandidateInput], Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]],
    rebuild_packet: Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]],
    baseline_candidates: Sequence[Mapping[str, Any]] = (),
) -> P05NavigationResult:
    """Run one bounded navigation attempt around an existing round-0 Packet."""

    if not isinstance(question, str) or not question.strip():
        raise P05NavigationError("question is required")
    if assessment.condition is EvidenceCondition.SUFFICIENT and assessment.action is OrchestrationAction.ANSWER_NOW:
        return _baseline_result(round0_packet, assessment)
    if assessment.action is not OrchestrationAction.SUPPLEMENT_ONCE:
        return _failure_result(round0_packet, assessment, category="navigation_not_authorized", message="assessment did not authorize navigation")
    packet_binding = bind_evidence_packet(round0_packet)
    try:
        decision_raw = decision_provider.decide(
            question,
            build_identity=views.build_identity,
            packet_binding=packet_binding.to_dict(),
        )
        decision = parse_navigation_decision(decision_raw)
    except Exception as exc:
        return _failure_result(round0_packet, assessment, category="navigation_provider_failure", message=type(exc).__name__)
    if decision.action == "stop":
        return _failure_result(round0_packet, assessment, category="navigation_stopped", message="navigation decision stopped", decision=decision.to_dict())
    resolved_unit_ids: tuple[str, ...] = ()
    baseline_overlap_unit_ids: tuple[str, ...] = ()
    novel_resolved_unit_ids: tuple[str, ...] = ()
    admitted_unit_ids: tuple[str, ...] = ()
    novel_admitted_unit_ids: tuple[str, ...] = ()
    visible_unit_ids: tuple[str, ...] = ()
    novel_visible_unit_ids: tuple[str, ...] = ()
    omitted_unit_ids: tuple[str, ...] = ()
    candidate_rows: tuple[Mapping[str, Any], ...] = ()
    hierarchy_trace: HierarchyTrace | None = None
    trace: NavigationTrace | None = None
    try:
        hierarchy_units, hierarchy_trace = _hierarchy_unit_ids(
            views,
            decision.hierarchy_node_ids,
            route_id=decision.route_id,
            policy=decision.policy,
        )
        trace = traverse_graph(views.graph, decision.graph_start_nodes, route_id=decision.route_id, policy=decision.policy) if decision.graph_start_nodes else NavigationTrace(
            route_id=decision.route_id,
            build_identity=views.build_identity,
            selected_item_ids=(),
            selected_unit_ids=(),
            visited_nodes=(),
            traversed_edges=(),
            truncated=False,
            termination="hierarchy_only",
            policy=decision.policy,
        )
        unit_ids = tuple(sorted(hierarchy_units | set(trace.selected_unit_ids)))
        candidates = resolve_navigation_candidates(unit_ids, ru_index, route_id=decision.route_id, max_candidates=decision.policy.max_candidates)
        resolved_unit_ids = tuple(candidate.unit_id for candidate in candidates)
        if not resolved_unit_ids:
            raise P05NavigationError("navigation resolved no Retrieval Units")
        baseline_visible_ids = set(packet_binding.visible_unit_ids)
        baseline_overlap_unit_ids = tuple(unit_id for unit_id in resolved_unit_ids if unit_id in baseline_visible_ids)
        novel_resolved_unit_ids = tuple(unit_id for unit_id in resolved_unit_ids if unit_id not in baseline_visible_ids)
        if not novel_resolved_unit_ids:
            raise P05NavigationError("navigation resolved only round-0 Packet evidence")
        candidate_rows = tuple(dict(row) for row in candidate_builder(candidates, baseline_candidates))
        admitted_row_ids = {row.get("unit_id") for row in candidate_rows}
        admitted_unit_ids = tuple(unit_id for unit_id in resolved_unit_ids if unit_id in admitted_row_ids)
        novel_admitted_unit_ids = tuple(unit_id for unit_id in novel_resolved_unit_ids if unit_id in admitted_row_ids)
        omitted_unit_ids = tuple(unit_id for unit_id in resolved_unit_ids if unit_id not in admitted_row_ids)
        if not novel_admitted_unit_ids:
            raise P05NavigationError("navigation candidates were not admitted to Assembly input")
        packet = rebuild_packet(candidate_rows, {"p05_navigation": {
            "decision": decision.to_dict(),
            "trace": trace.to_dict(),
            "hierarchy_trace": hierarchy_trace.to_dict() if hierarchy_trace is not None else None,
        }})
        binding = bind_evidence_packet(packet)
        unknown_visible = sorted(set(binding.visible_unit_ids) - set(ru_index))
        if unknown_visible:
            raise P05NavigationError(
                "rebuilt Packet contains non-authoritative Retrieval Units: "
                + ",".join(unknown_visible)
            )
        visible_packet_ids = set(binding.visible_unit_ids)
        visible_unit_ids = tuple(unit_id for unit_id in admitted_unit_ids if unit_id in visible_packet_ids)
        novel_visible_unit_ids = tuple(unit_id for unit_id in novel_admitted_unit_ids if unit_id in visible_packet_ids)
        omitted_unit_ids = tuple(unit_id for unit_id in resolved_unit_ids if unit_id not in visible_packet_ids)
        if not novel_visible_unit_ids:
            raise P05NavigationError("navigation candidates were omitted from the final Evidence Packet")
        return P05NavigationResult(
            status="succeeded",
            condition=assessment.condition.value,
            action=assessment.action.value,
            answer_disposition=AnswerDisposition.NONE.value,
            navigation_status="completed",
            packet=packet,
            packet_binding=binding,
            build_identity=views.build_identity,
            decision=decision.to_dict(),
            trace=trace,
            hierarchy_trace=hierarchy_trace,
            candidate_rows=candidate_rows,
            resolved_unit_ids=resolved_unit_ids,
            baseline_overlap_unit_ids=baseline_overlap_unit_ids,
            novel_resolved_unit_ids=novel_resolved_unit_ids,
            admitted_unit_ids=admitted_unit_ids,
            novel_admitted_unit_ids=novel_admitted_unit_ids,
            visible_unit_ids=visible_unit_ids,
            novel_visible_unit_ids=novel_visible_unit_ids,
            omitted_unit_ids=omitted_unit_ids,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        category = "navigation_execution_failure"
        if "resolved no Retrieval Units" in message or "resolved only round-0 Packet evidence" in message:
            category = "navigation_no_progress"
        elif "not admitted to Assembly input" in message:
            category = "navigation_admission_failure"
        elif "omitted from the final Evidence Packet" in message:
            category = "navigation_packet_visibility_failure"
        return _failure_result(
            round0_packet,
            assessment,
            category=category,
            message=message,
            build_identity=views.build_identity,
            decision=decision.to_dict(),
            trace=trace,
            hierarchy_trace=hierarchy_trace,
            candidate_rows=candidate_rows,
            resolved_unit_ids=resolved_unit_ids,
            baseline_overlap_unit_ids=baseline_overlap_unit_ids,
            novel_resolved_unit_ids=novel_resolved_unit_ids,
            admitted_unit_ids=admitted_unit_ids,
            novel_admitted_unit_ids=novel_admitted_unit_ids,
            visible_unit_ids=visible_unit_ids,
            novel_visible_unit_ids=novel_visible_unit_ids,
            omitted_unit_ids=omitted_unit_ids,
        )


def write_navigation_result(root: Path, result: P05NavigationResult) -> dict[str, Any]:
    """Persist one result without overwriting a different execution."""

    root = Path(root)
    body = canonical_json_bytes(result.to_dict())
    path = root / "p05_navigation_result.json"
    if path.exists() and path.read_bytes() != body:
        raise P05NavigationError(f"refusing to overwrite navigation result: {path}")
    if not path.exists():
        atomic_write(path, body)
    return {"path": str(path), "sha256": sha256_json(result.to_dict()), "byte_count": len(body)}


__all__ = [
    "HierarchyTrace",
    "NavigationDecision",
    "P05NavigationError",
    "P05NavigationResult",
    "P05_NAVIGATION_DECISION_SCHEMA_VERSION",
    "P05_NAVIGATION_RESULT_SCHEMA_VERSION",
    "parse_navigation_decision",
    "run_p05_navigation",
    "write_navigation_result",
]
