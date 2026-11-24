"""Rebuild first reads and validated G5 requests for a sealed candidate study."""

import argparse
from collections import Counter
from pathlib import Path
from random import Random
from statistics import median, quantiles
from time import perf_counter

from audit_g5_evidence_cost import Archive, extended_path, user_json
from audit_g5_upstream import original_candidates
from capture_rerank_disagreement import registered
from evaluate_g5_evidence import BASELINE, OPTIMIZED, archived_g7, g7_index
from rerank_disagreement_policy import ARMS, apply_policy, source_labels

from genshin_corpus.evidence_io import digest, read, require, seal, sha, verify, write
from genshin_corpus.generation.clean_chat import encoded
from genshin_corpus.generation.clean_evidence import EvidenceReader
from genshin_corpus.indexing.benchmark_io import offline_only
from genshin_corpus.rag.clean_research import (decision_feedback, evidence_quality, messages,
    packet_for, request_for, source_catalog)
from genshin_corpus.rag.clean_research_catalog import candidate_page
from genshin_corpus.retrieval.clean_based.indexes import selected
from genshin_corpus.retrieval.clean_based.rerank_v2 import rank_v2
from genshin_corpus.retrieval.clean_based.source import InputReader


class ReplayStore:
    def __init__(self):
        self.values = {}

    def put(self, value):
        key = digest(value)
        self.values[key] = value
        return key

    def load(self, key):
        return self.values[key]


def rank_binding(group, candidates, score, selection):
    require(digest(candidates) == group["reranking_run"]["candidate_set_sha256"]
            and digest(score) == group["reranking_run"]["score_result_digest"], "Foreign score/input")
    reproduced = rank_v2(candidates, score, selection)
    require(reproduced["candidates"] == group["candidates"] and reproduced["policy_id"] == group["policy_id"],
            "Archived structural order changed")


def initial_read(reader, ranked, quality, cache):
    er = EvidenceReader(reader, verified_parent_ranges=quality)
    handles = er.register(ranked)
    receipts = []
    for cid, handle in list(handles.items())[:6]:
        key = (quality, handle)
        if key not in cache:
            cache[key] = er.read(handle, "chunk")
        require(cache[key]["chunk_id"] == cid, "Read handle changed")
        receipts.append(cache[key])
    return handles, receipts


def request_projection(store, data, context, packet, settings):
    remaining_requests = min(settings["limits"]["turn_requests"], settings["limits"]["campaign_requests"])
    remaining = {"requests": remaining_requests, "research_requests_before_reserved_final": remaining_requests - 1,
                 "searches": settings["max_searches"] - 1, "reads": settings["max_reads"] - data["read_count"],
                 "tools": settings["max_tools"], "correction_rounds": settings.get("tool_error_corrections", 0)}
    initial = messages(context, packet["evidence"])
    catalog = source_catalog(store, data, packet)
    head = {"packet": user_json(initial[1]), "query_id": "original", "catalog": catalog, "remaining": remaining}
    if settings.get("stop_policy"):
        head["decision_feedback"] = decision_feedback(data)

    def project(body):
        return [initial[0], {**initial[1], "content": encoded(body).decode()}]

    quality = evidence_quality(settings)
    page = None
    if quality:
        def fits(value):
            try:
                request_for(project({**head, "candidate_catalog": value}), settings).manifest()
                return True
            except ValueError as exc:
                if str(exc) != "WHOLE_MESSAGE_BUDGET_EXCEEDED":
                    raise
                return False

        page = candidate_page(store, data, "original", packet=packet, fits=fits)
        if page is not None:
            head["candidate_catalog"] = page
        else:
            head["candidate_catalog_cursor"] = {"query_id": "original", "next_after_rank": 0}
    elif len(encoded(project(head))) > settings["context_tokens"] - 10000:
        head = {"packet": head["packet"], "catalog": [row for row in catalog if row["citation_ids"]],
                **{key: value for key, value in head.items() if key not in {"packet", "catalog"}}}
    wire = request_for(project(head), settings).manifest()["wire"]
    measured = {"request_sha256": digest(wire), "wire_bytes": len(encoded(wire)),
                "read_catalog_rows": len(head["catalog"]), "candidate_catalog_through_rank": page["through_rank"] if page else 0}
    return measured


def reassemble(ranked, reader, context, settings, cache):
    require(settings["initial_reads"] == 6, "Initial read limit changed")
    store = ReplayStore()
    started = perf_counter()
    handles, receipts = initial_read(reader, ranked, evidence_quality(settings), cache)
    read_seconds = perf_counter() - started
    data = {"groups": [store.put(ranked)], "queries": [ranked["query"]],
            "reads": {r["receipt_id"]: store.put(r) for r in receipts}, "stop": "researching",
            "failures": [], "handles": {handle: {"query_id": "original", "chunk_id": cid} for cid, handle in handles.items()},
            "read_keys": {digest([r["handle"], r["scope"]]): r["receipt_id"] for r in receipts},
            "tool_count": 0, "search_count": 1, "read_count": len(receipts), "no_gain": 0}
    turn = {"question": ranked["query"]["original_question"]}
    started = perf_counter()
    ctx, packet = packet_for(store, turn, store.put(context), data, settings)
    request = request_projection(store, data, ctx, packet, settings)
    receipt_by_id = {r["receipt_id"]: r for r in receipts}
    included = [receipt_by_id[entry["receipt_id"]]["chunk_id"] for entry in packet["evidence"]]
    result = {**request, "packet_id": packet["packet_id"], "budget": packet["policy"]["budget"],
              "first_reads": [r["chunk_id"] for r in receipts], "included": included,
              "packet_bytes": packet["estimated_message_tokens"],
              "registration_and_read_seconds": read_seconds, "assemble_and_request_seconds": perf_counter() - started}
    return result, packet, receipts


def part_contains(parts, anchor):
    for part in parts:
        for ref in [part["clean_ref"], *part.get("alias_clean_refs", ())]:
            if all(ref[k] == anchor[k] for k in ("observation_id", "block_id", "value_pointer")):
                start = anchor["char_start"] - ref["char_start"]
                end = anchor["char_end"] - ref["char_start"]
                if 0 <= start < end <= len(part["text"]) and part["text"][start:end] == anchor["quote"]:
                    return True
    return False


def compare_claims(ranked, included, claims):
    selected = set(included)
    results = []
    for claim in claims:
        witnesses = []
        for anchor in claim["all"]:
            seen = [row for row in ranked["candidates"] if row["chunk"]["observation_id"] == anchor["observation_id"]
                    and part_contains(row["chunk"]["parts"], anchor)]
            witnesses.append({"anchor": [anchor[k] for k in ("content_id", "block_id", "value_pointer", "char_start", "char_end")],
                              "ranks": [r["reranking"]["final_rank"] for r in seen],
                              "in_packet": any(r["chunk_id"] in selected for r in seen)})
        results.append({"claim": claim["text"], "all_in_first_packet": all(v["in_packet"] for v in witnesses),
                        "all_in_candidate_pool": all(v["ranks"] for v in witnesses), "witnesses": witnesses})
    return results


def verify_permutation(group, ranked, trace):
    if trace["promoted"] is None:
        require(ranked is group, "Abstention altered the group")
        return
    original = group["candidates"]
    result = ranked["candidates"]
    ids = [r["chunk_id"] for r in original]
    final_ids = [r["chunk_id"] for r in result]
    require(final_ids[:5] == ids[:5] and final_ids[5] == trace["promoted"]
            and final_ids[6] == trace["displaced"] == ids[5]
            and [cid for cid in final_ids if cid != trace["promoted"]] == [cid for cid in ids if cid != trace["promoted"]],
            "Single change or relative order violated")
    original_rows = {r["chunk_id"]: r for r in original}
    for slot, row in enumerate(result, 1):
        expected = original_rows[row["chunk_id"]]
        rank = row["reranking"]
        require(rank["final_rank"] == slot and rank["structural_rank"] == expected["reranking"]["final_rank"]
                and {k: v for k, v in row.items() if k != "reranking"} == {k: v for k, v in expected.items() if k != "reranking"}
                and {k: v for k, v in rank.items() if k not in {"final_rank", "structural_rank"}}
                == {k: v for k, v in expected["reranking"].items() if k != "final_rank"}, "Ordering altered evidence or score")


def experiment(case_id, group, reader, context, settings, output, claims=(), arms=ARMS):
    arm_outputs, traces, projections = {}, {}, {}
    packet_dir = output / "packets" / case_id
    cache = {}
    for arm in arms:
        started = perf_counter()
        ranked, trace = apply_policy(group, arm)
        rank_seconds = perf_counter() - started
        verify_permutation(group, ranked, trace)
        ordered_ids = tuple(row["chunk_id"] for row in ranked["candidates"])
        group_hash = digest(ranked)
        if group_hash not in projections:
            result, packet, receipts = reassemble(ranked, reader, context, settings, cache)
            name = arm + ".json"
            write(packet_dir / name, {"request": result, "packet": packet,
                                      "receipts": [r["receipt_id"] for r in receipts], "settings": settings})
            projections[group_hash] = result, name
        result, name = projections[group_hash]
        arm_outputs[arm] = {**result, "rank_seconds": rank_seconds, "packet_file": f"packets/{case_id}/{name}",
                            "order_digest": digest(ordered_ids), "ordered_group_digest": group_hash,
                            "claims": compare_claims(ranked, result["included"], claims)}
        traces[arm] = trace
    baseline = arm_outputs["baseline"]
    for arm, value in arm_outputs.items():
        kept = set(value["included"])
        value["lost_packet_chunks"] = [cid for cid in baseline["included"] if cid not in kept]
        value["added_packet_chunks"] = [cid for cid in value["included"] if cid not in baseline["included"]]
        value["wire_delta"] = value["wire_bytes"] - baseline["wire_bytes"]
    return {"question": group["query"]["original_question"], "profile": settings["generation_profile"],
            "baseline_group_digest": digest(group), "candidates": len(group["candidates"]),
            "traces": traces, "arms": arm_outputs}


def export_review(output, case_id, group, result):
    """Export source text without arm names or original rank. Keep the reveal key separate."""
    traces = result["traces"]
    candidates = {row["chunk_id"]: row for row in group["candidates"]}
    changed = [name for name, trace in traces.items() if trace["promoted"] is not None]
    if not changed:
        return
    picked = {cid for arm in ["baseline", *changed] for cid in result["arms"][arm]["first_reads"]}
    rng = Random(int(digest([case_id, group["query"], "blind-read-review"])[:16], 16))
    ids = sorted(picked)
    rng.shuffle(ids)
    mask = {cid: f"S{i:02d}" for i, cid in enumerate(ids, 1)}
    sources = {}
    for cid in ids:
        row = candidates[cid]
        document, section, subsection = source_labels(row)
        sources[mask[cid]] = {"document": document, "section": section, "subsection": subsection,
                             "parts": [{k: part[k] for k in ("text", "clean_ref", "condition_status", "condition_sources") if k in part}
                                       for part in row["chunk"]["parts"]]}
    arms = ["baseline", *changed]
    rng.shuffle(arms)
    pairs = {f"set_{i + 1}": sorted(mask[cid] for cid in result["arms"][arm]["included"]) for i, arm in enumerate(arms)}
    write(output / "blind" / (case_id + ".json"), {"question": result["question"], "sources": sources,
          "first_packet_sets": pairs})
    write(output / "reveal" / (case_id + ".json"), {"chunks": {v: k for k, v in mask.items()},
          "arms": {f"set_{i + 1}": arm for i, arm in enumerate(arms)}})


def summary(cases):
    results = {}
    for arm in ARMS:
        considered = [r for r in cases.values() if arm in r["arms"]]
        values = [r["arms"][arm] for r in considered]
        trace = [r["traces"][arm] for r in considered]
        delta = [v["wire_delta"] for v in values]
        rank_ms = [v["rank_seconds"] * 1000 for v in values]
        results[arm] = {"cases": len(values), "changed_order": sum(t["promoted"] is not None for t in trace),
            "reason_counts": dict(Counter(t["reason"] for t in trace)), "shortlisted": sum(len(t["shortlist"]) for t in trace),
            "changed_packet_content": sum(bool(v["lost_packet_chunks"] or v["added_packet_chunks"]) for v in values),
            "lost_packet_chunks": sum(len(v["lost_packet_chunks"]) for v in values),
            "added_packet_chunks": sum(len(v["added_packet_chunks"]) for v in values),
            "wire_delta_min_median_max": [min(delta), median(delta), max(delta)] if values else None,
            "rank_ms_median_p95": [median(rank_ms), quantiles(rank_ms, n=20, method="inclusive")[18]] if len(values) > 1 else rank_ms,
            "registered_claims_in_pool": sum(c["all_in_candidate_pool"] for v in values for c in v["claims"]),
            "registered_claims_in_packet": sum(c["all_in_first_packet"] for v in values for c in v["claims"])}
    return results


def historical(repo, reader, selection, output, calibration=False):
    cases = {}
    if calibration:
        root = extended_path(repo / "data/generation/g5-evidence-quality-ab-20261123-r02")
        mask = read(root / "execution/mask-key.json")
        label_id = next(name for name, profile in mask["schedule"]["Q068"] if profile == OPTIMIZED)
        root = root / "execution/arms/Q068" / label_id
        turn, = (path for path in (root / "chat_artifacts").iterdir() if path.is_dir())
        archive = Archive(root, turn.name)
        request = archive.artifact("g5-request-0")
        data = archive.load(request["data_ref"])
        group = archive.load(data["groups"][0])
        settings = read(root / "live/permit.json")["settings"]
        cases["Q068"] = experiment("Q068", group, reader, archive.load(request["context_ref"]), settings, output,
                                   arms=("baseline", "origin_slot"))
        return cases
    for case_id, item in g7_index(repo).items():
        archive, _ = archived_g7(repo, item)
        request = archive.artifact("g5-request-0")
        data = archive.load(request["data_ref"])
        group, = [archive.load(ref) for ref in data["groups"]]
        score_path = archive.root / "local_queries" / item["turn_id"] / "original" / "scores.json"
        score = read(score_path)
        rank_binding(group, original_candidates(group), score, selection)
        settings = read(archive.root / "live/permit.json")["settings"]
        require(settings["generation_profile"] == BASELINE, "Historical first request is outside official-4")
        result = experiment(case_id, group, reader, archive.load(request["context_ref"]), settings, output)
        archived = archive.load(request["sources_ref"])["packet"]
        result["matched_historical_packet_id"] = archived["packet_id"] == result["arms"]["baseline"]["packet_id"]
        result["matched_historical_request"] = digest(request["manifest"]["wire"]) == result["arms"]["baseline"]["request_sha256"]
        cases[case_id] = result
        export_review(output, case_id, group, result)
        if int(case_id[1:]) % 10 == 0:
            print(case_id + " replayed", flush=True)
    return cases


def independent(repo, reader, selection, output, plan, run):
    index = read(run / "independent-complete.json")["cases"]
    case_id, item = next(iter(g7_index(repo).items()))
    archive, _ = archived_g7(repo, item)
    template = archive.load(archive.artifact("g5-request-0")["context_ref"])
    settings = read(archive.root / "live/permit.json")["settings"]
    require(not template["recent"] and not template["summary"] and not template["protocol_history"]
            and not template["research_state"]["notes"] and not template["research_state"]["prior_research"], "Unexpected context history")
    cases = {}
    for case_id, case in plan["cases"].items():
        root = run / "independent" / case_id
        require(sha(root / "complete.json") == index[case_id], "Independent capture seal changed")
        capture = verify(root)
        require(capture["registration_sha256"] == sha(run / "registration.json")
                and capture["query"]["original_question"] == case["question"], "Unregistered local query")
        group = read(root / "ranked.json")
        rank_binding(group, read(root / "candidates.json"), read(root / "scores.json"), selection)
        context = {**template, "turn_id": case_id, "session_id": group["query"]["session_id"],
                   "research_state": {**template["research_state"], "current_question": case["question"]}}
        context["context_id"] = digest({k: v for k, v in context.items() if k != "context_id"})
        result = experiment(case_id, group, reader, context, settings, output, case["claims"])
        cases[case_id] = {**result, "stratum": case["stratum"]}
        export_review(output, case_id, group, result)
        if int(case_id[1:]) % 5 == 0:
            print(case_id + " replayed", flush=True)
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("historical", "calibration", "independent"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", default="r1")
    args = parser.parse_args()
    repo, run = Path.cwd().resolve(), args.out.resolve()
    require(args.label.isalnum(), "Simple run label required")
    output = run / (args.action + "-" + args.label)
    with offline_only():
        plan = registered(repo, run)
        replay_code = sha(__file__)
        output.mkdir(exist_ok=False)
        config = read(repo / "config/clean-retrieval-v1.json")
        selection = read(repo / "config/reranking-selected-v1.json")
        _, manifests = selected(repo, config)
        with InputReader(manifests["dense"][1]["input_manifest"]) as reader:
            if args.action == "independent":
                cases = independent(repo, reader, selection, output, plan, run)
            else:
                cases = historical(repo, reader, selection, output, calibration=args.action == "calibration")
        require(sha(__file__) == replay_code, "Replay code changed during execution")
        report = {"action": args.action, "registration_sha256": sha(run / "registration.json"),
                  "replay_code": replay_code, "remote_calls": 0, "cases": cases, "summary": summary(cases)}
        write(output / "report.json", report)
        seal(output)
        print(report["summary"], flush=True)


if __name__ == "__main__":
    main()
