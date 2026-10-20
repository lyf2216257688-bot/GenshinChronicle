"""Reproducible provider-free candidate V research on local official artifacts.

Example (PowerShell): $env:PYTHONPATH='src'; python -m
genshin_corpus.rag.navi_v_research --output data/retrieval/p05-navi-v-local-r1
Each output directory must be new. Historical decisions are replayed, not new
model autonomy; source-index coordinates are locator proposals, never RU IDs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.retrieval.navi_exploration import ExplorationError, LocalExplorationTools
from genshin_corpus.retrieval.navi_reading import VerifiedReadingSources, build_release_catalog, filter_tags
from .navi_v_experiment import ReadingSession, _once


def _load(path):
    return json.loads(Path(path).read_bytes())


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _tree(root):
    return {p.relative_to(root).as_posix(): _digest(p)
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def catalog_audit(listing_path, sources):
    """Verify metadata joins, not a full-corpus narrative/semantic run."""
    listing_path = Path(listing_path)
    listing = _load(listing_path)
    raw_meta = _load(listing_path.with_suffix(".meta.json"))
    if not raw_meta["ok"] or raw_meta["sha256"] != _digest(listing_path):
        raise ExplorationError("Raw Listing metadata SHA mismatch")
    canonical = sources.tools.canonical
    parsed_path = Path(canonical["parsed_manifest_path"])
    if _digest(parsed_path) != canonical["parsed_manifest_sha256"]:
        raise ExplorationError("Parsed manifest SHA mismatch")
    parsed = {r["record_identity_key"]: r for r in _load(parsed_path)["records"]}
    catalog = build_release_catalog(listing, source=canonical["source"], locale=canonical["locale"])
    joins = []
    for row in sorted(catalog["queue"] + catalog["pending_metadata"], key=lambda r: r["listing_position"]):
        key = row["source_identity_key"]
        ce, pe = sources.by_source[key], parsed[key]
        if (_digest(ce["canonical_record_path"]) != ce["canonical_record_sha256"]
                or _digest(pe["record_path"]) != pe["record_sha256"]
                or ce["parsed_record_sha256"] != pe["record_sha256"]):
            raise ExplorationError("metadata join record integrity mismatch")
        cr, pr = _load(ce["canonical_record_path"]), _load(pe["record_path"])
        if cr["source_identity"]["key"] != key or pr["identity"]["key"] != key:
            raise ExplorationError("metadata join identity mismatch")
        ctags = filter_tags(cr["record_metadata"]["source_metadata"]["ext"]["fe_ext"], "43")
        ptags = filter_tags(pr["source_metadata"]["ext"]["fe_ext"], "43")
        if ctags != ptags or ctags != row["source_tags"]:
            raise ExplorationError("Listing/Parsed/Canonical version tags disagree")
        joins.append({"source": key, "record_id": ce["record_id"],
                      "canonical_sha256": ce["canonical_record_sha256"], "parsed_sha256": pe["record_sha256"]})
    families = Counter()
    for row in catalog["queue"]:
        version = row["version"]
        families["月之系列" if version.startswith("月之") else version.split(".")[0] + ".x"] += 1
    return catalog, {
        "raw_listing_path": str(listing_path), "raw_listing_sha256": _digest(listing_path),
        "raw_metadata_sha256": _digest(listing_path.with_suffix(".meta.json")),
        "canonical_manifest_sha256": _digest(sources.tools.canonical_path),
        "parsed_manifest_sha256": _digest(parsed_path), "verified_joins": joins,
        "version_families": dict(families), "counts": catalog["counts"],
        "multi_type_pages": sum(len(r["task_types"]) > 1 for r in catalog["queue"] + catalog["pending_metadata"]),
        "caveat": "snapshot metadata census only; source version labels are not verified release dates",
    }


class HistoricalReplay:
    """Bind recorded B locator proposals to delivered official occurrences."""

    def __init__(self, root):
        self.root = Path(root)
        self.index = {c["clue_id"]: c for c in _load(self.root / "derived_source_index.json")["clues"]}
        self.updates = [u for p in sorted((self.root / "steps").glob("step_*/parsed_output.json"))
                        for u in _load(p)["clue_updates"]]
        self.audit = {}

    def apply(self, session, receipt):
        available = {(o["source"].rsplit(":", 1)[1], o["section_ordinal"], o["canonical_unit_ordinal"]): o
                     for o in session.state["coverage"].values() if o["read_status"] == "complete"}
        accepted_ids = {e["clue"]["clue_id"] for e in session.state["continuity"]["clue_events"]}
        changes = []
        for raw in self.updates:
            cid = raw["clue_id"]
            if cid in accepted_ids:
                continue
            proposals = self.index.get(cid, {}).get("local_component_keys", [])
            resolved, missing = [], []
            for locator in proposals:
                match = re.fullmatch(r"([^:]+):s(\d+):u(\d+)", locator)
                address = (match[1], int(match[2]), int(match[3])) if match else None
                if address not in available:
                    missing.append(locator)
                else:
                    resolved.extend(available[address]["unit_ids"])
            target = raw.get("target_clue_id")
            if not proposals or missing or (target and target not in accepted_ids):
                self.audit[cid] = {"status": "pending", "missing_or_unread_addresses": missing,
                                   "missing_target": target if target not in accepted_ids else None}
                continue
            clue = {k: raw[k] for k in ("clue_id", "text", "source_genre", "epistemic_status",
                                       "narrative_significance", "uncertainty_note")}
            clue["source_unit_ids"] = list(dict.fromkeys(resolved))
            changes.append({"operation": raw["operation"], "target_clue_id": target,
                            "reason": raw.get("reason"), "clue": clue})
            accepted_ids.add(cid)
            self.audit[cid] = {
                "status": "replayed_locator_bound_semantics_unreviewed", "locator_proposals": proposals,
                "official_unit_ids": clue["source_unit_ids"],
                "historical_resolution_status": self.index[cid]["overall_status"],
                "source_reference": raw["source_reference"],
            }
        existing = session.state["continuity"]["active_clues"]
        summary_items = [c["text"] for c in existing] + [u["clue"]["text"] for u in changes]
        summary = ("历史 B 更新的离线回放；下列解释未获语义验收，定位提案经本次已交付来源解析。\n"
                   + "\n".join(summary_items))[-session.budget.max_summary_chars:]
        # Empty changes are explicitly "no new interpretation"; not model output.
        result = session.understand(
            updates=changes, summary=summary, receipt_ids=[receipt["receipt_id"]],
            unresolved_questions=["回放仅核实来源交付及状态演进；解释与所引片段是否充分相符仍待语义复核。"])
        if "reason" in result:
            raise ExplorationError("historical replay rejected: " + result["reason"])

    def report(self):
        return {"mode": "recorded_B_updates_not_new_model_output", "updates": self.audit,
                "counts": dict(Counter(v["status"] for v in self.audit.values())),
                "caveat": "historical coordinates are proposed locators; exact RU binding is not semantic endorsement"}


def _read_and_replay(session, replay):
    receipt = session.read_next()
    if "receipt_id" in receipt:
        replay.apply(session, receipt)
    return receipt


def byakuyakoku(sources, catalog, root, historical_a, historical_b):
    key = f"{catalog['source']}:{catalog['locale']}:3429"
    ordered = [r["source_identity_key"] for r in catalog["queue"]]
    # One real next catalog entry tests return to version progress; no curated support list.
    baseline = ordered[ordered.index(key):ordered.index(key) + 2]
    session = ReadingSession.create(sources, catalog, root, baseline_sources=baseline)
    replay = HistoricalReplay(historical_b)
    seed = _read_and_replay(session, replay)
    seed_text = "\n".join(u["text"] for u in seed["units"])
    history = _load(Path(historical_a) / "session.json")
    searches = [e["action"] for e in history["events"] if e["action"]["action"] == "search"]
    named = []
    for decision in searches:
        query = decision["query"]
        match = re.fullmatch(r"《(.+)》", query)
        if match and match[1] in seed_text and query not in named:
            named.append(query)
    decisions = []
    independent_seed = None
    queries = list(named[:session.budget.max_searches])
    refinements = set()
    for query in queries:
        if session.state["used"]["searches"] >= session.budget.max_searches:
            break
        topic = seed["topic_id"]
        result = session.search(query, topic_id=topic, top_k=5)
        # Replay policy: first returned rich-text hit on a non-baseline source.
        # No historical seven-page allowlist and no title-to-page ID lookup.
        hit = next((h for h in result["result"]["hits"]
                    if h["canonical_address"]["canonical_unit_kind"] == "rich_text"
                    and h["canonical_address"]["source_identity_key"] not in baseline), None)
        decisions.append({"query": query, "origin": ("historical_A_recorded_refinement" if query in refinements
                                                   else "historical_A_named_search_present_in_delivered_seed"),
                          "selected_hit": hit["unit_id"] if hit else None,
                          "selection_policy": "first_nonbaseline_rich_text_hit; replay_policy_not_AI_autonomy"})
        if hit is None:
            continue
        address = hit["canonical_address"]
        session.enqueue(address["source_identity_key"], lane="related", topic_id=topic,
                        anchor_section=address["section_ordinal"],
                        origin={"kind": "search_hit", "unit_id": hit["unit_id"]})
        related = _read_and_replay(session, replay)
        name_match = re.fullmatch(r"《(.+)》", query)
        if name_match and "receipt_id" in related:
            delivered = related["record_title"] + "\n" + "\n".join(o["text"] for o in related["units"])
            if name_match[1] not in delivered:
                refinement = next((d["query"] for d in searches
                                   if name_match[1] in d["query"] and d["query"] not in queries), None)
                decisions[-1]["delivery_gap"] = "requested_name_absent_from_selected_title_and_body"
                decisions[-1]["refinement_query"] = refinement
                if refinement and refinement not in refinements:
                    queries.append(refinement)
                    refinements.add(refinement)
        if independent_seed is None and "receipt_id" in related and related["units"]:
            independent_seed = related
            # Same non-quest source can become its own topic without rereading it.
            session.enqueue(related["source"], lane="independent",
                            anchor_section=related["units"][0]["section_ordinal"],
                            question="独立研究此资料的叙述立场与流传版本，不强制归属某项任务。",
                            origin={"kind": "read_source", "coverage_key": related["units"][0]["coverage_key"]})
            _read_and_replay(session, replay)
        # Scheduler may have returned to another lane. Drain remaining scoped work,
        # retaining action capacity for the next search.
        while (any(session.state["queues"].values()) and session.state["status"] == "in_progress"
               and session.state["used"]["actions"] < session.budget.max_actions - 9):
            _read_and_replay(session, replay)
    if session.state["status"] == "in_progress":
        session.stop("bounded offline recorded-decision replay complete; unresolved work preserved")
    resumed = ReadingSession.resume(sources, catalog, root)
    if resumed.state != session.state:
        raise ExplorationError("Byakuyakoku resume state mismatch")
    report = {"coverage": session.coverage_summary(), "decisions": decisions,
              "replay": replay.report(), "resume_equal": True,
              "seed_source_unit_order": [u["canonical_unit_ordinal"] for u in seed["units"]],
              "delivered_sources": session.state["pages"],
              "operations": dict(Counter(e["operation"] for e in session.state["continuity"]["clue_events"]))}
    _once(root / "result.json", report)
    return report


def caribert(sources, catalog, root):
    key = f"{catalog['source']}:{catalog['locale']}:6236"
    session = ReadingSession.create(sources, catalog, root, baseline_sources=[key])
    before = None
    receipt = session.read_next()
    # Deliberately fail an interpretation after persisting delivery.
    rejection = session.understand(updates=[{
        "operation": "add", "clue": {"clue_id": "invalid_probe", "text": "unread-reference probe",
        "source_unit_ids": ["not-an-official-delivered-RU"], "source_genre": "unclassified",
        "epistemic_status": "unclassified", "narrative_significance": "mechanical rejection fixture"},
    }], summary="Mechanical rejection probe.", receipt_ids=[receipt["receipt_id"]])
    if "reason" not in rejection:
        raise ExplorationError("invalid reference probe was accepted")
    before = session.state
    session = ReadingSession.resume(sources, catalog, root)
    if session.state != before:
        raise ExplorationError("Caribert delivery recovery mismatch")
    order = []
    while session.state["status"] == "in_progress":
        if "receipt_id" in receipt:
            order.append({"group_id": receipt["group_context"]["group_id"],
                          "body_chars": sum(len(o["text"]) for o in receipt["units"]),
                          "unit_ids": [uid for o in receipt["units"] for uid in o["unit_ids"]]})
            session.understand(updates=[], summary=(
                "机械研究：已交付的来源分组按模板顺序推进，尚未生成剧情解释。"
                f"已处理 {len(order)} 批；理解失败后从阅读凭据恢复，不重复交付正文。"),
                receipt_ids=[receipt["receipt_id"]])
        if not any(session.state["queues"].values()):
            break
        receipt = session.read_next()
    if session.state["status"] == "in_progress":
        session.stop("four source groups mechanically delivered; semantic evaluation not performed")
    report = {"coverage": session.coverage_summary(), "delivery_order": order,
              "resume_after_rejection_equal": True, "new_body_chars_before_resume": before["used"]["new_body_chars"],
              "interpretation_mode": "explicit_no_new_semantic_interpretation"}
    _once(root / "result.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, default=Path(
        "data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/metadata/manifest.json"))
    parser.add_argument("--ru-manifest", type=Path, default=Path(
        "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json"))
    parser.add_argument("--lexical-manifest", type=Path, default=Path(
        ".local/p04-block-a-closure-20260914-r2/field-aware-lexical/metadata/manifest.json"))
    parser.add_argument("--listing", type=Path, default=Path(
        "data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/responses/listings/"
        "44cb730c420480a0477b505ae68af508fb90f96cf0ec54c6ad16949dd427f13a.json"))
    parser.add_argument("--historical-a", type=Path, default=Path(
        "data/retrieval/p05-navi-e2-deepseek-jizhi-research-20261019-103633-r1"))
    parser.add_argument("--historical-b", type=Path, default=Path(
        "data/retrieval/p05-navi-byakuyakoku-continuity-deepseek-20261019-r1"))
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    code_paths = [Path(__file__), Path(__file__).with_name("navi_v_experiment.py"),
                  Path(__file__).parents[1] / "retrieval/navi_reading.py",
                  Path(__file__).parents[1] / "retrieval/clue_state.py"]
    code_hashes = {p.name: _digest(p) for p in code_paths}
    _once(args.output / "implementation_hashes.json", code_hashes)
    before = {"A": _tree(args.historical_a), "B": _tree(args.historical_b)}
    _once(args.output / "historical_hashes_before.json", before)
    sources = VerifiedReadingSources(LocalExplorationTools(
        args.canonical_manifest, args.ru_manifest, args.lexical_manifest))
    catalog, audit = catalog_audit(args.listing, sources)
    print(f"Metadata joins verified: {len(audit['verified_joins'])}", flush=True)
    _once(args.output / "catalog.json", catalog)
    _once(args.output / "catalog_audit.json", audit)
    white = byakuyakoku(sources, catalog, args.output / "byakuyakoku", args.historical_a, args.historical_b)
    print("Byakuyakoku recorded replay complete.", flush=True)
    caribert_result = caribert(sources, catalog, args.output / "caribert")
    after = {"A": _tree(args.historical_a), "B": _tree(args.historical_b)}
    if before != after:
        raise ExplorationError("historical artifacts changed")
    if code_hashes != {p.name: _digest(p) for p in code_paths}:
        raise ExplorationError("research implementation changed during run; use a fresh run")
    a = _load(args.historical_a / "session.json")
    reads = [e for e in a["events"] if e["action"]["action"] == "read"]
    b = _load(args.historical_b / "derived_continuity_state.json")
    result = {
        "schema_version": "phase05-navi-v-offline-evidence-0.1",
        "sources_identity": sources.identity, "catalog_identity": catalog["catalog_identity"],
        "implementation_hashes": code_hashes,
        "provider_calls": 0, "network_calls": 0, "semantic_acceptance_status": "not_accepted",
        "historical_hashes_unchanged": True, "historical_tree_identity": sha256_json(before),
        "byakuyakoku": white, "caribert": caribert_result,
        "comparison_baselines": {
            "A": {"status": a["status"], "reads": len(reads),
                  "distinct_RU": len({e["action"]["unit_id"] for e in reads}),
                  "partial_receipts": sum(bool(e["result"].get("partial")) for e in reads),
                  "searches": a["used"]["searches"],
                  "caveat": "partial may omit payload metadata even when RU text is complete; duplicates not all waste"},
            "B": {"operations": dict(Counter(e["operation"] for e in b["clue_events"])),
                  "selection_policy": "historical_human_preselected_seven_pages"},
        },
    }
    _once(args.output / "result.json", result)
    print(json.dumps({"output": str(args.output), "provider_calls": 0, "counts": audit["counts"],
                      "byakuyakoku": white["coverage"], "caribert": caribert_result["coverage"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
