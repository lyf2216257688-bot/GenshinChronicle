"""Reproducible, socket-blocked two-question G6 offline study.

Run with PYTHONPATH=src and the existing Generation interpreter. Creates a new
output directory; never edits old evidence or opens a Live store/credential.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import socket
from unittest.mock import patch

from genshin_corpus.evidence_io import copy, digest, inventory, read, require, seal, sha, verify, write
from genshin_corpus.generation.clean_evidence import EvidenceReader, assemble, estimate_tokens, fragment_content
from genshin_corpus.rag.clean_sparse_study import supplement, protected_assembly, envelope_assembly
from genshin_corpus.generation.clean_chat import encoded
from genshin_corpus.rag.clean_research import request_for
from genshin_corpus.rag.clean_conversation_graph import messages
from genshin_corpus.retrieval.clean_based.source import InputReader


def run(repo, out):
    require(not out.exists(), "Create-only study output already exists")
    old = repo / "data/generation/g6-offline-20261116"
    live = repo / "data/generation/g6-live-20261117-remaining"
    paid_roots = [repo / ("data/generation/" + name) for name in (
        "g6-live-20261116", "g6-live-20261117-remaining", "g6-fixed-packet-20261117")]
    prior = {str(p.relative_to(repo)): inventory(p) for p in paid_roots}
    verify(old)
    settings = read(live / "live/permit.json")["settings"]
    selection = read(repo / "config/reranking-selected-v1.json")
    plan = read(repo / "data/retrieval/recall-p4-70q-v1-20261112/plan.json")
    out.mkdir(parents=True)
    copy(__file__, out / "study_s0_sparse.py")
    copy(repo / "src/genshin_corpus/rag/clean_sparse_study.py", out / "clean_sparse_study.py")
    write(out / "protocol.json", {"questions": ["Q046", "Q050"], "new_inference_pairs": 0, "remote_calls": 0,
        "candidate_policy": "all_S0_unchanged_plus_all_exact_cached_raw_sparse_only_no_manual_filter",
        "read_policies": ["joint_first6", "joint_first48_diagnostic", "S0_first6_plus_all_cached_sparse_only_max48"],
        "budgets": {"initial": [60000, 8192], "final": [196608, 4096]},
        "assembly": ["unchanged_greedy", "protect_S0_baseline_packet_then_residual_sparse"],
        "limits": "two_previous_debug_cases_not_blind_quality_acceptance;no_model_selection_or_answer_prediction",
        "old_complete_sha256": sha(old / "complete.json"), "selection_sha256": sha(repo / "config/reranking-selected-v1.json")})
    reports = []
    count = 0
    with InputReader(plan["input_manifest"]) as reader:
        for qid in ("Q046", "Q050"):
            folder = out / qid
            folder.mkdir()
            assets = {combo: {k: read(old / qid / combo / (k + ".json"))
                              for k in ("candidates", "scores", "ranked")} for combo in ("S0", "S2")}
            pool, union, baseline = supplement(assets["S0"]["candidates"], assets["S2"]["candidates"],
                assets["S0"]["scores"], assets["S2"]["scores"], selection)
            require(baseline == assets["S0"]["ranked"], "S0 historical BGE replay differs")
            write(folder / "pool.json", pool)
            write(folder / "ranked.json", union)
            ranks = {"S0": baseline, "S2": assets["S2"]["ranked"], "U": union}
            receipts = {}
            for combo in ("S0", "S2"):
                for p in (old / qid / combo / "reads").glob("*.json"):
                    r = read(p)
                    require(r["chunk_id"] not in receipts or receipts[r["chunk_id"]] == r, "Conflicting source read")
                    receipts[r["chunk_id"]] = r
            er = EvidenceReader(reader)
            handles = {}
            for g in ranks.values():
                handles.update(er.register(g))
            base6 = [c["chunk_id"] for c in baseline["candidates"][:6]]
            supplemental = base6 + pool["study"]["added_ids"]
            require(len(supplemental) <= settings["max_reads"] == 48, "Read allowance exceeded")
            need = set(supplemental) | {c["chunk_id"] for g in ranks.values() for c in g["candidates"][:48]}
            for cid in sorted(need):
                fresh = er.read(handles[cid])
                require(fresh == receipts[cid], "Fresh original source differs from historical receipt")
                write(folder / "reads" / (fresh["receipt_id"] + ".json"), fresh)
                count += 1
            case = read(live / "cases" / (qid + "-S0.json"))
            initial_path = live / "chat_artifacts" / case["turn"] / (digest("g5-initial-sources") + ".json")
            initial = read(initial_path)
            context = initial["assembly_context"]
            write(folder / "common-context.json", context)
            packets, rows = {}, []

            def record(label, g, rs, stage, selected=None, protected=None):
                budget = {"context_tokens": 60000 if stage == "initial" else 196608,
                          "output_tokens": 8192 if stage == "initial" else 4096, "thinking_tokens": 0}
                if protected is None:
                    packet = assemble([g], rs, selection_ids=selected, context=context, budget=budget)
                else:
                    packet, admission = protected_assembly(g, rs, baseline, protected, context=context, budget=budget)
                    write(folder / (label + "-admission.json"), admission)
                # Actual existing request-envelope guard, no prepare/dispatch.
                envelope = "PASS"
                try:
                    request_for(messages(context, packet["evidence"]), settings, final=stage == "final").manifest()
                except ValueError as exc:
                    if str(exc) != "WHOLE_MESSAGE_BUDGET_EXCEEDED":
                        raise
                    # Record this independent boundary failure. Do not silently
                    # shrink just one side of a fixed-budget Assembly comparison.
                    envelope = "WHOLE_MESSAGE_BUDGET_EXCEEDED_not_generation_ready"
                write(folder / (label + "-packet.json"), packet)
                packets[label] = packet
                included = {e["receipt_id"] for e in packet["evidence"]}
                result = {"label": label, "packet_id": packet["packet_id"], "read_count": len(rs),
                    "evidence_count": len(packet["evidence"]), "request_envelope": envelope,
                    "estimated_bytes": packet["estimated_message_tokens"],
                    "reserve": packet["reserved_tokens"], "budget": budget["context_tokens"],
                    "remaining_bytes": budget["context_tokens"] - packet["estimated_message_tokens"] - packet["reserved_tokens"],
                    "included_rows": [c["input_row"] for c in g["candidates"] if c["chunk_id"] in
                                      {r["chunk_id"] for r in rs if r["receipt_id"] in included}]}
                rows.append(result)
                return packet

            for stage in ("initial", "final"):
                for depth in (6, 48):
                    for combo, g in ranks.items():
                        rs = [receipts[c["chunk_id"]] for c in g["candidates"][:depth]]
                        record(f"{combo}-read{depth}-{stage}", g, rs, stage)
                rs = [receipts[cid] for cid in supplemental]
                record(f"U-supplement_reads-{stage}", union, rs, stage)
                record(f"P-supplement_reads-{stage}", union, rs, stage,
                       protected=packets[f"S0-read6-{stage}"])

            # Replay real Q046, then remove ONLY its restrictive model selection.
            live_trace = []
            if qid == "Q046":
                for combo in ("S0", "S2"):
                    result = read(live / "case-reports" / (qid + "-" + combo + ".json"))["result"]
                    ref = result["sources_ref"]
                    require(sha(live / ref["path"]) == ref["sha256"], "Live source artifact changed")
                    sources = read(live / ref["path"])
                    refs = sources["ranked_refs"] + list(sources["reads"].values())
                    require(all(sha(live / r["path"]) == r["sha256"] for r in refs), "Live read/rank hash changed")
                    gs = [read(live / r["path"]) for r in sources["ranked_refs"]]
                    by_id = {r["receipt_id"]: r for ref in sources["reads"].values() if (r := read(live / ref["path"]))}
                    ctx, actual = sources["assembly_context"], sources["packet"]
                    # Source validation later adds full-Parent receipts to this
                    # inventory. Replay only the Packet's ORIGINAL exact input
                    # receipt list, preserving its order and hash identity.
                    rs = [by_id[rid] for rid in actual["read_receipt_ids"]]
                    replay = assemble(gs, rs, selection_ids=actual["selection_ids"], context=ctx,
                                      budget=actual["policy"]["budget"], source_failures=sources["source_failures"])
                    require(replay == actual, "Real final Packet replay differs")
                    unselected = assemble(gs, rs, context=ctx, budget=actual["policy"]["budget"],
                                          source_failures=sources["source_failures"])
                    request_for(messages(ctx, unselected["evidence"]), settings, final=True).manifest()
                    write(folder / (combo + "-actual-final-packet.json"), actual)
                    write(folder / (combo + "-actual-final-without-selection-packet.json"), unselected)
                    write(folder / (combo + "-actual-initial-packet.json"), read(live / "chat_artifacts" /
                        result["turn_id"] / (digest("g5-initial-sources") + ".json"))["packet"])
                    write(folder / (combo + "-actual-answer.json"), {"answer_text": result["answer_text"],
                        "sources_ref": ref, "warning": "archived_original_not_an_answer_to_new_packet"})
                    live_trace.append({"combo": combo, "actual_packet": actual["packet_id"],
                        "without_selection_packet": unselected["packet_id"], "original_answer": result["answer_text"]})

            key_rows = [63315, 63311] if qid == "Q046" else [58774, 58797, 16380, 22197, 65285]
            candidates = {c["input_row"]: c for g in ranks.values() for c in g["candidates"]}
            trace = []
            for n in key_rows:
                c = candidates[n]; cid = c["chunk_id"]; r = receipts[cid]; f = r["fragments"][0]
                content = fragment_content(f)
                trace.append({"row": n, "chunk_id": cid, "text": c["source"]["dense_text"],
                    "source_item": c["source"]["source_item"], "raw_hits": c["hits"],
                    "content_bytes": estimate_tokens(content),
                    "content_field_bytes": {k: estimate_tokens(v) for k, v in content.items()},
                    "source_equals_parent_context": content["source_context"] == content["parent_context"],
                    "ranks": {name: next((x["reranking"]["final_rank"] for x in g["candidates"] if x["chunk_id"] == cid), None)
                              for name, g in ranks.items()},
                    "packets": {label: next(d["disposition"] for d in p["dispositions"] if d["chunk_id"] == cid)
                                if cid in {d["chunk_id"] for d in p["dispositions"]} else "not_a_candidate"
                                for label, p in packets.items()}})
            write(folder / "key-trace.json", trace)
            base_order = [c["chunk_id"] for c in baseline["candidates"]]
            filtered = [c["chunk_id"] for c in union["candidates"] if c["chunk_id"] in set(base_order)]
            report = {"qid": qid, "study": pool["study"], "S0_post_BGE_relative_order_unchanged": base_order == filtered,
                      "packets": rows, "key_trace": trace, "live_selection_replay": live_trace}
            reports.append(report)
            write(folder / "report.json", report)
            print(qid, len(union["candidates"]), "candidates", len(pool["study"]["unscored_unexamined_ids"]), "unexamined", flush=True)
    require(prior == {str(p.relative_to(repo)): inventory(p) for p in paid_roots}, "Prior paid evidence was modified")
    write(out / "report.json", {"status": "OFFLINE_COMPLETE_NOT_ANSWER_ACCEPTANCE", "remote_calls": 0,
        "new_inference_pairs": 0, "fresh_source_readbacks": count, "prior_paid_trees_unchanged": True, "cases": reports})
    write(out / "prior-paid-hashes.json", prior)
    seal(out)


def run_envelope(repo, source, out):
    """Use sealed source receipts; no new source-reader/index/model work."""
    require(not out.exists(), "Create-only envelope output already exists")
    verify(source)
    previous = read(source / "report.json")
    require(previous["status"] == "OFFLINE_COMPLETE_NOT_ANSWER_ACCEPTANCE", "Incomplete base study")
    prior = read(source / "prior-paid-hashes.json")
    require(all(inventory(repo / p) == hashes for p, hashes in prior.items()), "Prior paid trees changed")
    old = repo / "data/generation/g6-offline-20261116"
    require(sha(old / "complete.json") == read(source / "protocol.json")["old_complete_sha256"], "Old study changed")
    verify(old)
    settings = read(repo / "data/generation/g6-live-20261117-remaining/live/permit.json")["settings"]
    selection = read(repo / "config/reranking-selected-v1.json")
    out.mkdir(parents=True)
    copy(__file__, out / "study_s0_sparse.py")
    copy(repo / "src/genshin_corpus/rag/clean_sparse_study.py", out / "clean_sparse_study.py")
    write(out / "protocol.json", {"source": str(source), "source_seal_sha256": sha(source / "complete.json"),
        "purpose": "existing_G5_guard_replay_after_fixed_budget_comparison;not_fusion_or_budget_tuning",
        "remote_calls": 0, "new_inference_pairs": 0, "new_source_reads": 0})
    reports = []
    for case in previous["cases"]:
        qid = case["qid"]
        folder = out / qid
        folder.mkdir()
        b, d = [read(old / qid / c / "candidates.json") for c in ("S0", "S2")]
        bs, ds = [read(old / qid / c / "scores.json") for c in ("S0", "S2")]
        pool, union, base = supplement(b, d, bs, ds, selection)
        require(pool == read(source / qid / "pool.json") and union == read(source / qid / "ranked.json"),
                "Candidate or ranking replay differs")
        ranks = {"S0": base, "S2": read(old / qid / "S2/ranked.json"), "U": union, "P": union}
        receipts = {r["chunk_id"]: r for p in (source / qid / "reads").glob("*.json") if (r := read(p))}
        context = read(source / qid / "common-context.json")
        rows = []
        for original in case["packets"]:
            label = original["label"]
            combo = label.split("-")[0]
            stage = label.split("-")[-1]
            g = ranks[combo]
            if "read6-" in label or "read48-" in label:
                depth = 6 if "read6-" in label else 48
                rs = [receipts[c["chunk_id"]] for c in g["candidates"][:depth]]
            else:
                rs = [receipts[cid] for cid in [c["chunk_id"] for c in base["candidates"][:6]] + pool["study"]["added_ids"]]
            budget = {"context_tokens": 60000 if stage == "initial" else 196608,
                      "output_tokens": 8192 if stage == "initial" else 4096, "thinking_tokens": 0}
            def check(packet):
                manifest = request_for(messages(context, packet["evidence"]), settings, final=stage == "final").manifest()
                return {"status": "PASS", "wire_bytes": len(encoded(manifest["wire"])),
                        "output_reserve": budget["output_tokens"], "request_context": settings["context_tokens"]}
            protection = {"baseline_ranked": base, "baseline_packet": read(source / qid / f"S0-read6-{stage}-packet.json")} if combo == "P" else {}
            packet, audit = envelope_assembly(g, rs, context=context, budget=budget, check=check, **protection)
            write(folder / (label + "-packet.json"), packet)
            write(folder / (label + "-admission.json"), audit)
            by_receipt = {r["receipt_id"]: r["chunk_id"] for r in rs}
            included = {by_receipt[e["receipt_id"]] for e in packet["evidence"]}
            rows.append({"label": label, "read_count": len(rs), "evidence_count": len(packet["evidence"]),
                "admission_cap": audit["effective_budget"]["context_tokens"], "guard_trials": len(audit["trials"]),
                "wire_bytes": audit["check_result"]["wire_bytes"], "output_reserve": budget["output_tokens"],
                "packet_estimated_bytes": packet["estimated_message_tokens"],
                "included_rows": [c["input_row"] for c in g["candidates"] if c["chunk_id"] in included],
                "key_dispositions": {str(t["row"]): next((x["disposition"] for x in packet["dispositions"]
                    if x["chunk_id"] == t["chunk_id"]), "not_a_candidate") for t in case["key_trace"]}})
        report = {"qid": qid, "packets": rows}
        reports.append(report)
        write(folder / "report.json", report)
        print(qid, [(x["label"], x["admission_cap"], x["key_dispositions"]) for x in rows if x["label"].endswith("final")], flush=True)
    require(all(inventory(repo / p) == hashes for p, hashes in prior.items()), "Prior paid evidence changed")
    write(out / "report.json", {"status": "COMPLETE_ALL_32_REQUEST_ENVELOPES_PASS_NOT_ANSWER_ACCEPTANCE",
        "remote_calls": 0, "new_inference_pairs": 0, "new_source_reads": 0, "cases": reports})
    seal(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--from-study", type=Path, help="Replay the existing G5 request guard over a sealed fixed-budget study")
    args = parser.parse_args()
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Offline study: network prohibited")):
        if args.from_study:
            run_envelope(Path.cwd(), args.from_study.resolve(), args.output.resolve())
        else:
            run(Path.cwd(), args.output.resolve())
