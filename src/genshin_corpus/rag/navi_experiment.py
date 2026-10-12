"""Recorded E1 exploration and isolated P05-W1/Deferred Assembly wiring."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.retrieval.navi_exploration import (
    ExplorationBudget, ExplorationBudgetExceeded, ExplorationError, LocalExplorationTools, response_chars,
)
from genshin_corpus.retrieval.evidence_assembly import (
    EvidenceAssemblyConfig, PreparedAssemblyContext, assemble_deferred_footprint_charge_packet, retrieval_unit_records,
)
from genshin_corpus.retrieval.semantic_compilation import (
    SemanticViews, TraversalPolicy, merge_navigation_candidate_rows,
)
from .adaptive import (
    AnswerDisposition, EvidenceAssessmentResult, EvidenceCondition, OrchestrationAction,
    bind_evidence_packet,
)
from .p05_navigation import run_p05_navigation


def _save(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    state["checkpoint_identity"] = sha256_json({k: v for k, v in state.items() if k != "checkpoint_identity"})
    atomic_write(root / "session.json", canonical_json_bytes(state))
    return state


def _counts(events: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    result = {"rounds": len(events), "searches": 0, "reads": 0, "context_chars": 0, "clues": 0}
    for event in events:
        kind = event["action"]["action"]
        result["searches"] += kind == "search"
        result["reads"] += kind == "read"
        result["clues"] += kind == "clue"
        result["context_chars"] += event["delivered_chars"]
    return result


def _validate_action(action: Mapping[str, Any]) -> None:
    if not isinstance(action, Mapping):
        raise ExplorationError("action must be an object")
    fields = {
        "search": {"action", "query", "top_k"},
        "read": {"action", "unit_id", "hit", "max_chars", "neighbors"},
        "clue": {"action", "text", "source_unit_ids"},
        "stop": {"action", "reason"},
    }
    kind = action.get("action")
    if kind not in fields or set(action) - fields[kind]:
        raise ExplorationError("unsupported action or fields")
    if kind == "read" and ("hit" in action) == ("unit_id" in action):
        raise ExplorationError("read requires exactly one RU or prior hit reference")


def _resolve_read(action: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    if "unit_id" in action:
        uid = action["unit_id"]
        if not isinstance(uid, str) or not uid:
            raise ExplorationError("invalid unit identity")
        return uid
    hit = action["hit"]
    if not isinstance(hit, Mapping) or set(hit) != {"round", "index"}:
        raise ExplorationError("invalid search hit reference")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in hit.values()):
        raise ExplorationError("invalid search hit coordinates")
    try:
        event = events[hit["round"]]
        if event["action"]["action"] != "search":
            raise ExplorationError("hit references a non-search action")
        return event["result"]["hits"][hit["index"]]["unit_id"]
    except (IndexError, KeyError) as exc:
        raise ExplorationError("search hit absent") from exc


def run_recorded_exploration(
    tools: LocalExplorationTools, *, question: str, decisions: Sequence[Mapping[str, Any]],
    output_root: Path, budget: ExplorationBudget | None = None, action_limit: int | None = None,
) -> dict[str, Any]:
    """Resume only matching recorded actions; completed actions are not executed again.

    Pending local read/search actions can be replayed deterministically. This
    function has no provider seam and makes no provider execution claims.
    """
    tools.verify_snapshot()
    budget = budget or ExplorationBudget()
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ExplorationError("invalid research question")
    if action_limit is not None and (not isinstance(action_limit, int) or isinstance(action_limit, bool) or action_limit <= 0):
        raise ExplorationError("invalid action limit")
    root = Path(output_root)
    if any(root.resolve().is_relative_to(p.parent.parent) for p in (tools.canonical_path, tools.ru_path, tools.lexical_path)):
        raise ExplorationError("output cannot be inside an input snapshot")
    for action in decisions:
        _validate_action(action)
    binding = {"question": question, "tools_identity": tools.identity, "ru_build_identity": tools.ru_identity, "budget": asdict(budget), "decisions": list(decisions), "driver": "recorded_provider_free_not_autonomous"}
    identity = sha256_json(binding)
    path = root / "session.json"
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("checkpoint_identity") != sha256_json({k: v for k, v in state.items() if k != "checkpoint_identity"}):
            raise ExplorationError("checkpoint integrity mismatch")
        if state.get("session_identity") != identity or state.get("binding") != binding:
            raise ExplorationError("resume identity mismatch")
        events = state["events"]
        if state["budget_used"] != _counts(events):
            raise ExplorationError("replay budget mismatch")
        for index, event in enumerate(events):
            if event["action"] != decisions[index] or event["round"] != index:
                raise ExplorationError("replay action mismatch")
            expected_chars = response_chars(event["result"]) if event["action"]["action"] in {"search", "read", "clue"} else 0
            if event["delivered_chars"] != expected_chars:
                raise ExplorationError("replay context accounting mismatch")
        if state["read_sources"] != [e["result"] for e in events if e["action"]["action"] == "read"]:
            raise ExplorationError("replay source receipt mismatch")
        if state["pending_clues"] != [e["result"] for e in events if e["action"]["action"] == "clue"]:
            raise ExplorationError("replay clue mismatch")
        if state["status"] != "running":
            return state
    else:
        if root.exists() and any(root.iterdir()):
            raise ExplorationError("new session root must be empty")
        state = {"schema_version": "p05-navi-e1-recorded-session-0.1", "session_identity": identity,
                 "binding": binding, "status": "running", "events": [], "pending_action": None,
                 "budget_used": _counts([]), "read_sources": [], "pending_clues": [],
                 "provider_calls": 0, "network_calls": 0, "semantic_acceptance_status": "not_accepted"}
        _save(root, state)
    completed_here = 0
    while len(state["events"]) < len(decisions):
        index = len(state["events"])
        action = dict(decisions[index])
        kind = action["action"]
        counts = state["budget_used"]
        if state["pending_action"] not in (None, action):
            raise ExplorationError("pending action identity mismatch")
        try:
            if counts["rounds"] >= budget.max_rounds:
                raise ExplorationBudgetExceeded("round budget exhausted")
            remaining = budget.max_context_chars - counts["context_chars"]
            if kind == "search" and counts["searches"] >= budget.max_searches:
                raise ExplorationBudgetExceeded("search budget exhausted")
            if kind == "read" and counts["reads"] >= budget.max_reads:
                raise ExplorationBudgetExceeded("read budget exhausted")
            if kind == "clue" and counts["clues"] >= budget.max_clues:
                raise ExplorationBudgetExceeded("clue budget exhausted")
            if kind in {"search", "read"} and remaining <= 0:
                raise ExplorationBudgetExceeded("context budget exhausted")
            state["pending_action"] = action
            _save(root, state)
            delivered = 0
            if kind == "search":
                query = action.get("query")
                if not isinstance(query, str) or not query.strip() or len(query) > budget.max_query_chars:
                    raise ExplorationError("query budget/shape violation")
                k = action.get("top_k", budget.max_candidates)
                if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= budget.max_candidates:
                    raise ExplorationError("candidate budget violation")
                # Metadata plus snippets are charged before execution.
                # The tool response is capped by top-k snippets and metadata;
                # keep this preflight below the actual JSON response ceiling.
                result = tools.search(query, top_k=k)
                result["omitted_hit_count"] = 0
                while result["hits"] and response_chars(result) > remaining:
                    result["hits"].pop()
                    result["omitted_hit_count"] += 1
                delivered = response_chars(result)
                if delivered > remaining:
                    raise ExplorationBudgetExceeded("search response exceeded context budget")
            elif kind == "read":
                uid = _resolve_read(action, state["events"])
                requested = action.get("max_chars", budget.max_read_chars)
                neighbors = action.get("neighbors", 0)
                if not isinstance(requested, int) or isinstance(requested, bool) or not 1 <= requested <= budget.max_read_chars:
                    raise ExplorationError("read character budget violation")
                if not isinstance(neighbors, int) or isinstance(neighbors, bool) or not 0 <= neighbors <= budget.max_neighbors:
                    raise ExplorationError("neighbor budget violation")
                result = tools.read(uid, max_chars=min(requested, remaining), neighbors=neighbors)
                delivered = response_chars(result)
            elif kind == "clue":
                text = action.get("text")
                refs = action.get("source_unit_ids", [])
                if not isinstance(text, str) or not text.strip() or len(text) > 1000 or not isinstance(refs, list):
                    raise ExplorationError("invalid clue")
                if not set(refs) <= {r["unit_id"] for r in state["read_sources"]}:
                    raise ExplorationError("clue references unread sources")
                result = {"text": text, "source_unit_ids": refs, "status": "unverified_hypothesis_not_fact"}
                delivered = response_chars(result)
                if delivered > remaining:
                    raise ExplorationBudgetExceeded("clue context budget exhausted")
            else:
                reason = action.get("reason")
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
                    raise ExplorationError("invalid stop reason")
                result = {"reason": reason}
            state["events"].append({"round": index, "action": action, "result": result, "delivered_chars": delivered})
            state["budget_used"] = _counts(state["events"])
            if kind == "read":
                state["read_sources"].append(result)
            elif kind == "clue":
                state["pending_clues"].append(result)
            elif kind == "stop":
                state["status"] = "stopped"
            state["pending_action"] = None
            _save(root, state)
        except ExplorationBudgetExceeded as exc:
            state.update(status="budget_exhausted", termination=str(exc), pending_action=None)
            return _save(root, state)
        except Exception as exc:
            state.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
            _save(root, state)
            raise
        completed_here += 1
        if state["status"] != "running" or (action_limit is not None and completed_here >= action_limit):
            return state
    state["status"] = "decisions_exhausted"
    return _save(root, state)


class _RecordedNavigation:
    def __init__(self, seeds: Sequence[str], policy: TraversalPolicy):
        self.seeds = seeds
        self.policy = policy

    def decide(self, question: str, *, build_identity: str, packet_binding: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"action": "navigate", "route_id": "e1-recorded-hierarchy", "hierarchy_node_ids": list(self.seeds), "policy": asdict(self.policy)}


def wire_recorded_navigation(
    *, question: str, session: Mapping[str, Any], views: SemanticViews,
    ru_index: Mapping[str, Mapping[str, Any]], context: PreparedAssemblyContext,
    baseline_candidates: Sequence[Mapping[str, Any]], hierarchy_seeds: Sequence[str],
    assembly_config: EvidenceAssemblyConfig | None = None, policy: TraversalPolicy | None = None,
) -> dict[str, Any]:
    """Mechanical replay control, explicitly not a production Hybrid experiment."""
    if session.get("checkpoint_identity") != sha256_json({k: v for k, v in session.items() if k != "checkpoint_identity"}):
        raise ExplorationError("session integrity mismatch")
    if question != session["binding"]["question"]:
        raise ExplorationError("wiring question differs from exploration")
    if context.retrieval_unit_build_identity != session["binding"]["ru_build_identity"]:
        raise ExplorationError("Assembly/session RU identity mismatch")
    context_rows = retrieval_unit_records(context, ru_index)
    if any(context_rows[uid] != row for uid, row in ru_index.items()):
        raise ExplorationError("navigation RU index differs from Assembly snapshot")
    config = assembly_config or EvidenceAssemblyConfig()
    def assemble(rows: Sequence[Mapping[str, Any]], audit: Mapping[str, Any]) -> Mapping[str, Any]:
        return assemble_deferred_footprint_charge_packet(context.retrieval_unit_manifest_path, rows, prepared_context=context, config=config, retrieval_audit=audit)
    baseline = assemble(baseline_candidates, {"experiment": "E1 replay mechanical baseline, not Hybrid"})
    assessment = EvidenceAssessmentResult(
        assessment_request_identity=sha256_json({"packet": bind_evidence_packet(baseline).to_dict(), "fixture": "E1 recorded incomplete"}),
        condition=EvidenceCondition.INCOMPLETE, action=OrchestrationAction.SUPPLEMENT_ONCE,
        answer_disposition=AnswerDisposition.NONE, missing_information=("recorded wiring check only",),
        supplemental_query=question,
    )
    attempted_packets = []
    def rebuild(rows: Sequence[Mapping[str, Any]], audit: Mapping[str, Any]) -> Mapping[str, Any]:
        packet = assemble(rows, audit)
        attempted_packets.append(packet)
        return packet
    nav = run_p05_navigation(
        question, round0_packet=baseline, assessment=assessment,
        decision_provider=_RecordedNavigation(hierarchy_seeds, policy or TraversalPolicy()),
        views=views, ru_index=ru_index, candidate_builder=merge_navigation_candidate_rows,
        rebuild_packet=rebuild, baseline_candidates=baseline_candidates,
    )
    baseline_visible = set(bind_evidence_packet(baseline).visible_unit_ids)
    final_visible = set(nav.packet_binding.visible_unit_ids)
    found = {h["unit_id"] for e in session["events"] if e["action"]["action"] == "search" for h in e["result"]["hits"]}
    attempted = attempted_packets[-1] if attempted_packets else baseline
    return {
        "control": "recorded_mechanical_not_hybrid_not_autonomous_not_semantic_gain",
        "assembly_read_scope": dict(context.build_manifest).get("read_scope", "full_snapshot"),
        "baseline_packet": baseline, "navigation": nav.to_dict(), "final_packet": dict(nav.packet),
        "attempted_packet": attempted,
        "audit": {
            "baseline_visible_unit_ids": sorted(baseline_visible), "search_found_unit_ids": sorted(found),
            "read_unit_ids": sorted({r["unit_id"] for r in session["read_sources"]}),
            "navigation_resolved_unit_ids": list(nav.resolved_unit_ids),
            "baseline_overlap_unit_ids": sorted(set(nav.resolved_unit_ids) & baseline_visible),
            "assembly_input_accepted_unit_ids": list(nav.admitted_unit_ids),
            "formal_assembly_dispositions": attempted.get("admission_contract", {}).get("direct_footprints", []),
            "final_packet_visible_unit_ids": sorted(final_visible),
            "novel_navigation_packet_visible_unit_ids": sorted((set(nav.resolved_unit_ids) & final_visible) - baseline_visible),
            "omitted_navigation_unit_ids": sorted(set(nav.resolved_unit_ids) - final_visible),
            "baseline_lost_unit_ids": sorted(baseline_visible - final_visible),
            "semantic_relevance": "UNKNOWN", "answer_gain": "UNKNOWN", "provider_calls": 0,
        },
    }
