"""Provider-free real-source mechanical smoke; run only with explicit paths.

Search decisions contain a book title actually read from the supplied start
RU. They do not contain reference answer content IDs. No human review is
invented: the active view is an explicitly isolated mechanical fixture.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import socket
from unittest.mock import patch

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.retrieval.navi_exploration import LocalExplorationTools, ExplorationBudget, ExplorationError, reviewed_topic_build
from genshin_corpus.retrieval.semantic_compilation import build_views, write_semantic_build
from genshin_corpus.retrieval.evidence_assembly import EvidenceAssemblyConfig, prepare_evidence_assembly_context, retrieval_unit_ids, retrieval_unit_records
from genshin_corpus.generation.generation import project_generation_request
from .navi_experiment import run_recorded_exploration, wire_recorded_navigation


def run_smoke(*, canonical_manifest: Path, ru_manifest: Path, lexical_manifest: Path,
              start_ru: str, output_root: Path) -> dict:
    with patch.object(socket.socket, "connect", side_effect=AssertionError("E1 network prohibited")), patch.object(socket, "create_connection", side_effect=AssertionError("E1 network prohibited")):
        tools = LocalExplorationTools(canonical_manifest, ru_manifest, lexical_manifest)
        root = Path(output_root)
        if any(root.resolve().is_relative_to(p.parent.parent) for p in (tools.canonical_path, tools.ru_path, tools.lexical_path)):
            raise ExplorationError("smoke output must be outside input snapshots")
        if root.exists() and any(root.iterdir()):
            raise ExplorationError("smoke output root must be new and empty")
        # Preparation is explicitly logged separately; the session reads the
        # same start again under its own hard budget. Only this supplied seed
        # ID is allowed as a direct read decision; later reads use search hits.
        seed = tools.read(start_ru, max_chars=20000)
        names = re.findall(r"《([^》]{1,60})》", seed["ru_text"])
        if seed["partial"] or not names:
            raise ExplorationError("complete start source with book-title clue required")
        query = names[0]
        decisions = [
            {"action": "read", "unit_id": start_ru, "max_chars": 20000},
            {"action": "search", "query": query, "top_k": 5},
            {"action": "read", "hit": {"round": 1, "index": 2}, "max_chars": 20000},
            {"action": "clue", "text": "Same book title is a candidate reading link, not proof of identical versions or historical truth.", "source_unit_ids": [start_ru]},
            {"action": "stop", "reason": "Recorded tool/wiring smoke complete; relevance and semantic benefit require later review."},
        ]
        budget = ExplorationBudget(max_rounds=6, max_searches=1, max_reads=2,
                                   max_candidates=5, max_context_chars=50000, max_read_chars=20000)
        session = run_recorded_exploration(tools, question="从绘真的书名线索回读相关文献，保留小说、传说与推测的立场。",
                                          decisions=decisions, output_root=root / "exploration", budget=budget, action_limit=2)
        with patch.object(tools, "search", side_effect=AssertionError("completed search repeated")):
            session = run_recorded_exploration(tools, question=session["binding"]["question"], decisions=decisions,
                                              output_root=root / "exploration", budget=budget)
        if session["status"] != "stopped" or any(r["partial"] for r in session["read_sources"]):
            raise ExplorationError("smoke requires complete bounded read receipts")
        ids = list(dict.fromkeys(r["unit_id"] for r in session["read_sources"]))
        if len(ids) < 2:
            raise ExplorationError("top search hit overlaps start; no wiring progress")
        record_ids = sorted({r["source"]["canonical_address"]["record_id"] for r in session["read_sources"]})
        context = prepare_evidence_assembly_context(ru_manifest, selected_record_ids=record_ids)
        index = retrieval_unit_records(context, retrieval_unit_ids(context))
        group = {"group_id": "recorded-literature-reading", "label": "Recorded literature reading (not a factual relation)",
                 "unit_ids": ids, "topic_path": ["e1-mechanical-fixture", "literature"]}
        unreviewed = reviewed_topic_build(session, [group], [], index)
        write_semantic_build(root / "unreviewed", unreviewed, build_views(unreviewed))
        fixture_review = {
            "group_id": group["group_id"], "group_identity": sha256_json(group), "session_identity": session["checkpoint_identity"],
            "reviewer_kind": "recorded_mechanical_fixture", "reviewer": "E1 fixture driver, not human adjudication",
            "decision": "accept_navigation_only", "source_qualification": "fixture_only",
            "rationale": "Explicit mechanical topic grouping to test real RU/Assembly wiring only.",
            "narrative_caveat": "No identity, causality, history, source-role qualification or relevance is accepted.",
        }
        build = reviewed_topic_build(session, [group], [fixture_review], index, allow_mechanical_fixture=True)
        views = build_views(build)
        write_semantic_build(root / "mechanical-fixture", build, views)
        human_template = {**fixture_review, "reviewer_kind": "human", "reviewer": "PENDING",
                          "decision": "PENDING", "source_qualification": "unqualified", "rationale": "PENDING",
                          "narrative_caveat": "PENDING; preserve book/legend/uncertainty"}
        atomic_write(root / "review-request.json", canonical_json_bytes({"group": group, "review_template": human_template}))
        seeds = [node.node_id for node in views.hierarchy.nodes.values() if node.path == tuple(group["topic_path"])]
        config = EvidenceAssemblyConfig(neighbor_before=1, neighbor_after=1, dialogue_hops=1)
        result = wire_recorded_navigation(question=session["binding"]["question"], session=session, views=views, ru_index=index,
                                         context=context, baseline_candidates=[{"unit_id": start_ru, "rank": 1}], hierarchy_seeds=seeds,
                                         assembly_config=config)
        generation = project_generation_request(result["final_packet"], question=session["binding"]["question"])
        if "p05_navigation" in str(generation.semantic_projection()):
            raise ExplorationError("navigation audit leaked into Generation projection")
        summary = {
            "status": result["navigation"]["status"], "kind": "E1 real-source mechanical smoke, not human semantic acceptance",
            "start_ru": start_ru, "derived_search_query": query, "driver": "recorded_not_autonomous",
            "tools_identity": tools.identity, "ru_build_identity": tools.ru_identity,
            "budget": asdict(budget), "budget_used": session["budget_used"], "preparation_read_calls": 1,
            "read_sources": [{"unit_id": r["unit_id"], "canonical_address": r["source"]["canonical_address"],
                              "ru_text_sha256": r["ru_text_sha256"], "partial": r["partial"]} for r in session["read_sources"]],
            "unreviewed_active_items": len(unreviewed.active_items), "unreviewed_inactive_items": len(unreviewed.inactive_ledger),
            "fixture_acceptance": [item.semantic_acceptance_status for item in build.active_items], "graph_edges": len(views.graph.edges),
            "audit": result["audit"], "assembly_read_scope": result["assembly_read_scope"],
            "generation_trace_excluded": True, "provider_calls": 0, "network_calls": 0,
            "human_semantic_acceptance": "PENDING", "relevance": "UNKNOWN", "answer_gain": "UNKNOWN",
        }
        atomic_write(root / "wiring.json", canonical_json_bytes(result))
        atomic_write(root / "summary.json", canonical_json_bytes(summary))
        if summary["status"] != "succeeded" or not summary["audit"]["novel_navigation_packet_visible_unit_ids"]:
            raise ExplorationError("real-source mechanical Packet wiring failed")
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--ru-manifest", type=Path, required=True)
    parser.add_argument("--lexical-manifest", type=Path, required=True)
    parser.add_argument("--start-ru", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    summary = run_smoke(**vars(args))
    print(json.dumps({"status": summary["status"], "budget_used": summary["budget_used"],
                      "novel_visible": summary["audit"]["novel_navigation_packet_visible_unit_ids"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
