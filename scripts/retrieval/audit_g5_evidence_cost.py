"""Measure sealed G5 requests and current offline projections without a caller."""

import argparse
from collections import Counter
import os
from pathlib import Path
from statistics import median
import time
from unittest.mock import patch

from genshin_corpus.evidence_io import digest, inside, read, require, sha, strict_json, verify, write
from genshin_corpus.generation.clean_chat import encoded
from genshin_corpus.generation.clean_evidence import fragment_content
from genshin_corpus.rag.clean_research_catalog import candidate_page


def byte_count(value):
    return len(encoded(value))


def extended_path(path):
    value = str(path.resolve())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    return Path(value)


class Archive:
    def __init__(self, root, turn_id):
        self.root = root
        self.turn = inside(root, f"chat_artifacts/{turn_id}")

    def artifact(self, key):
        return read(self.turn / f"{digest(key)}.json")

    def load(self, ref):
        path = inside(self.root, ref["path"])
        require(sha(path) == ref["sha256"], "Changed chat artifact")
        return read(path)


def user_json(message):
    return strict_json(message["content"])


def request_bytes(wire, index, value):
    messages = list(wire["messages"])
    messages[index] = {**messages[index], "content": encoded(value).decode()}
    return byte_count({**wire, "messages": messages})


def request_parts(wire):
    role_bytes = Counter()
    user_fields, catalogs = [], []
    assistant_reasoning_bytes = 0
    for index, message in enumerate(wire["messages"]):
        role_bytes[message["role"]] += byte_count(message)
        assistant_reasoning_bytes += len(message.get("reasoning_content", "").encode())
        if message["role"] not in {"user", "tool"}:
            continue
        value = user_json(message)
        if message["role"] == "user":
            user_fields.append({key: byte_count(item) for key, item in value.items()})
        if "candidate_catalog" in value:
            catalog = value["candidate_catalog"]
            catalogs.append({"role": message["role"], "query": catalog["query_id"], "rows": len(catalog["rows"]),
                             "sha256": digest(catalog), "encoded_bytes": byte_count(catalog),
                             "wire_contribution_bytes": byte_count(wire) - request_bytes(wire, index,
                                 {key: item for key, item in value.items() if key != "candidate_catalog"})})
    tools_bytes = byte_count(wire["tools"]) if "tools" in wire else 0
    return {"role_message_object_bytes": dict(role_bytes), "wire_tool_schema_bytes": tools_bytes,
            "routing_and_delimiters_bytes": byte_count(wire) - sum(role_bytes.values()) - tools_bytes,
            "assistant_reasoning_content_utf8_bytes": assistant_reasoning_bytes,
            "user_field_value_bytes": user_fields, "candidate_catalogs": catalogs}


def replay_final(archive, envelope, packet, sources, settings, published):
    from genshin_corpus.rag.clean_research import messages, packet_for, request_for

    data = archive.load(envelope["data_ref"])
    question = {"question": sources["assembly_context"]["question"]}
    context, current = packet_for(archive, question, envelope["context_ref"], data, settings, final=True)
    manifest = request_for(messages(context, current["evidence"]), settings, final=True,
                           analysis=data["analysis"]).manifest()
    old = {entry["citation_id"]: entry["fragment_id"] for entry in packet["evidence"]}
    current_ids = {entry["fragment_id"]: entry["citation_id"] for entry in current["evidence"]}
    return {"wire_bytes": byte_count(manifest["wire"]), "evidence_count": len(current_ids),
            "historical_citations_with_same_fragment_still_admitted": {cid: current_ids[old[cid]]
                for cid in sorted(published) if old[cid] in current_ids},
            "historical_citations_with_fragment_excluded": sorted(cid for cid in published if old[cid] not in current_ids),
            "newly_admitted_fragment_ids": [fid for fid in current_ids if fid not in old.values()]}


def final_entries(archive, result, envelope, wire, settings):
    sources = archive.load(envelope["sources_ref"])
    packet = sources["packet"]
    require(packet["input_group_digests"] == [digest(archive.load(ref)) for ref in sources["ranked_refs"]],
            "Packet ranks differ")
    published = {citation["citation_id"] for citation in result["citations"]}
    wire_user = user_json(wire["messages"][1])
    evidence = wire_user["evidence"]
    require(evidence == [{"citation_id": item["citation_id"], "content": item["content"]}
                         for item in packet["evidence"]], "Packet body differs from sealed request")
    dispositions = {(entry["group"], entry["rank"], item["fragment_id"]): item
                    for entry in packet["dispositions"] for item in entry.get("fragments", [])
                    if item["disposition"] in {"included", "context_expansion"}}
    compact_evidence, projected_evidence, items = [], [], []
    for entry, wire_item in zip(packet["evidence"], evidence, strict=True):
        receipt = archive.load(sources["reads"][entry["receipt_id"]])
        fragment = next(f for f in receipt["fragments"] if f["fragment_id"] == entry["fragment_id"])
        compact_item = {**wire_item, "content": fragment_content(fragment, compact=True)}
        compact_evidence.append(compact_item)
        content = dict(wire_item["content"])
        structure = dict(content["source_text_and_structure"])
        retrieval_bytes = byte_count(structure.pop("retrieval_text")) if "retrieval_text" in structure else 0
        duplicate_context_bytes = 0
        if "parent_context" in content and content["parent_context"] == content.get("source_context"):
            duplicate_context_bytes = byte_count(content.pop("parent_context"))
        content["source_text_and_structure"] = structure
        projected_evidence.append({**wire_item, "content": content})
        admission = [{"group": group, "rank": rank, "tier": disposition.get("admission_tier"),
                      "body_exposure": disposition.get("body_exposure")}
                     for (group, rank, fid), disposition in dispositions.items() if fid == entry["fragment_id"]]
        items.append({"citation_id": entry["citation_id"], "cited": entry["citation_id"] in published,
                      "admission": admission, "scope": receipt["scope"], "source_item": fragment["source_item"],
                      "text_locator_count": len(fragment["texts"]), "encoded_bytes": byte_count(wire_item),
                      "compact_encoded_bytes": byte_count(compact_item),
                      "retrieval_text_value_bytes": retrieval_bytes,
                      "duplicate_parent_context_value_bytes": duplicate_context_bytes})
    projected_bytes = request_bytes(wire, 1, {**wire_user, "evidence": projected_evidence})
    report = {"entries": items, "compact_projection_wire_bytes_same_fragments":
            request_bytes(wire, 1, {**wire_user, "evidence": compact_evidence}),
            "what_if_wire_bytes_without_retrieval_text_and_exact_duplicate_parent_context": projected_bytes}
    if settings["generation_profile"] == "gc-g5-official-5":
        report["offline_current_policy_final_reassembly"] = replay_final(archive, envelope, packet, sources, settings, published)
    return report


def compact_catalog(archive, envelope, wire):
    data = archive.load(envelope["data_ref"])
    packet = archive.load(envelope["sources_ref"])["packet"]
    wire_user = user_json(wire["messages"][1])
    previous = wire_user["candidate_catalog"]
    def projected_bytes(value):
        return request_bytes(wire, 1, {**wire_user, "candidate_catalog": value})

    limit = envelope["manifest"]["limits"]["input_bytes"]
    cpu = []
    for _ in range(3):
        start = time.process_time()
        page = candidate_page(archive, data, "original", packet=packet,
                              fits=lambda value: projected_bytes(value) <= limit)
        cpu.append(time.process_time() - start)
    require(page is not None, "Compact catalog unexpectedly absent")
    count = min(len(page["rows"]), len(previous["rows"]))
    def ordered_handles(value):
        return [(row["rank"], row["handle"]) for row in value["rows"][:count]]

    require(ordered_handles(page) == ordered_handles(previous), "Compact catalog changed sealed ranking")
    return {"archived_top_rank": previous["through_rank"], "compact_top_rank": page["through_rank"],
            "archived_value_bytes": byte_count(previous), "compact_value_bytes": byte_count(page),
            "projected_wire_bytes": projected_bytes(page), "warm_process_median_seconds": median(cpu)}


def packet_gold(archive, sources, packet, gold):
    entries = []
    keys = ("observation_id", "block_id", "value_pointer")
    for expected in gold:
        included = []
        for item in packet["evidence"]:
            receipt = archive.load(sources["reads"][item["receipt_id"]])
            fragment = next(f for f in receipt["fragments"] if f["fragment_id"] == item["fragment_id"])
            if fragment["source_item"]["content_id"] != expected["content_id"]:
                continue
            for span in fragment["texts"]:
                ref = span["clean_ref"]
                start = expected["char_start"] - ref["char_start"]
                end = expected["char_end"] - ref["char_start"]
                if (all(ref[key] == expected[key] for key in keys) and 0 <= start < end <= len(span["text"])
                        and span["text"][start:end] == expected["quote"]):
                    included.append(item["citation_id"])
                    break
        entries.append({**{key: expected[key] for key in (*keys, "content_id", "char_start", "char_end")},
                        "included_citations": included})
    return entries


def replay_initial(archive, envelope, wire, settings, gold):
    from genshin_corpus.rag.clean_research import messages, packet_for, request_for, source_catalog

    data = archive.load(envelope["data_ref"])
    sources = archive.load(envelope["sources_ref"])
    question = {"question": sources["assembly_context"]["question"]}
    context, packet = packet_for(archive, question, envelope["context_ref"], data, settings)
    base = user_json(wire["messages"][1])
    projected = messages(context, packet["evidence"])
    base.update(packet=user_json(projected[1]), catalog=source_catalog(archive, data, packet))

    def build(key, value):
        return request_for([projected[0], {**projected[1], "content": encoded({**base, key: value}).decode()}],
                           settings).manifest()["wire"]

    def fits(value):
        try:
            build("candidate_catalog", value)
        except ValueError as exc:
            if str(exc) != "WHOLE_MESSAGE_BUDGET_EXCEEDED":
                raise
            return False
        return True

    page = candidate_page(archive, data, "original", packet=packet, fits=fits)
    current = build("candidate_catalog", page) if page else build("candidate_catalog_cursor",
                                        {"query_id": "original", "next_after_rank": 0})
    prior_ranks = {item["rank"] for item in sources["packet"]["dispositions"] if item["disposition"] == "included"}
    current_ranks = {item["rank"] for item in packet["dispositions"] if item["disposition"] == "included"}
    return {"wire_bytes": byte_count(current), "old_packet_included_ranks": sorted(prior_ranks),
            "current_packet_included_ranks": sorted(current_ranks),
            "current_catalog_through_rank": page["through_rank"] if page else 0,
            "old_packet_gold_locators": packet_gold(archive, sources, sources["packet"], gold),
            "current_packet_gold_locators": packet_gold(archive, sources, packet, gold)}


def replay_omitted_tools(archive, settings, final_envelope):
    sources = archive.load(final_envelope["sources_ref"])
    projected_sources = {}
    for ref in sources["reads"].values():
        receipt = archive.load(ref)
        for fragment in receipt["fragments"]:
            fid = fragment["fragment_id"]
            projected_sources[fid] = {"handle": receipt["handle"], "scope": receipt["scope"], "fragment_id": fid,
                "source_item": fragment["source_item"], "content": fragment_content(fragment, compact=True)}
    rows, seen = [], set()
    for index in range(final_envelope["round"]):
        envelope = archive.artifact(f"g5-request-{index}")
        for message in envelope["manifest"]["wire"]["messages"]:
            if message["role"] != "tool" or message["tool_call_id"] in seen:
                continue
            seen.add(message["tool_call_id"])
            value = user_json(message)
            if value.get("status") not in {"source_read", "already_read"}:
                continue
            for fid in value["omitted_fragments"]:
                expected = projected_sources.get(fid)
                if expected is None:
                    continue
                replay = {**value, "sources": [*value["sources"], expected],
                          "omitted_fragments": [other for other in value["omitted_fragments"] if other != fid]}
                if not replay["omitted_fragments"]:
                    replay.pop("body_delivery", None)
                size = byte_count(replay)
                rows.append({"first_seen_in_request": index + 1, "fragment_id": fid,
                             "source_item": expected["source_item"], "archived_feedback_value_bytes": byte_count(value),
                             "projected_unescaped_whole_feedback_bytes": size,
                             "configured_feedback_cap_bytes": settings["feedback_bytes"],
                             "fits_feedback_cap": size <= settings["feedback_bytes"]})
    return rows


def attempt_row(archive, result, reservation, index, gold):
    record = read(reservation)
    attempt = inside(archive.root, record["attempt"])
    require(attempt.parent.name == "attempts" and attempt.parents[2].name == "calls"
            and attempt.parents[3].samefile(reservation.parents[3]), "Attempt left its campaign")
    verify(attempt)
    saved_outcome = read(reservation.with_name("outcome.json"))
    require(saved_outcome["attempt_complete_sha256"] == sha(attempt / "complete.json"), "Ledger seal differs")
    response = read(attempt / "result.json")
    envelope = archive.artifact(f"g5-request-{index - 1}")
    plan = read(attempt.parents[1] / "plan.json")
    manifest = plan["body"]["manifest"]
    body = (attempt / "request.body.bin").read_bytes()
    wire = manifest["wire"]
    settings = plan["body"]["permit"]["settings"]
    require(plan["fingerprint"] == record["fingerprint"] and envelope["manifest"] == manifest
            and encoded(wire) == body, "Prepared request differs from dispatched bytes")
    usage = response["usage"]
    known = usage["total_tokens"] if isinstance(usage, dict) else None
    require(known == saved_outcome["observed_tokens"], "Ledger usage differs")
    reported = response["provider_reported_usage"]
    raw = attempt / "response.body.raw"
    parts = request_parts(wire)
    row = {"request": index, "attempt_complete_sha256": saved_outcome["attempt_complete_sha256"],
           "stage": envelope["phase"], "role": envelope["role"]["role"], "state": response["state"],
           "http_status": response["http_status"], "failure_type": response.get("failure_type"),
           "configured_timeout_seconds": manifest["limits"]["timeout_seconds"],
           "request_wire_bytes": len(body), "response_raw_bytes": raw.stat().st_size if raw.is_file() else None,
           "attempt_seconds": response["elapsed_seconds"], "usage": usage, "usage_known": isinstance(usage, dict),
           "prompt_cache": {key: reported.get(key) for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens")}
           if isinstance(reported, dict) else None, **parts}
    if envelope["phase"] == "final" and result.get("answer_text"):
        row["final_packet"] = final_entries(archive, result, envelope, wire, settings)
        if settings["generation_profile"] == "gc-g5-official-5":
            row["offline_prior_tool_feedback_omissions"] = replay_omitted_tools(archive, settings, envelope)
    if index == 1 and parts["candidate_catalogs"]:
        row["offline_initial_catalog_projection"] = compact_catalog(archive, envelope, wire)
        row["offline_current_policy_initial_reassembly"] = replay_initial(archive, envelope, wire, settings, gold)
    return row


def arm_cost(root, case, label, gold):
    arm = inside(root, f"execution/arms/{case}/{label}")
    audit = read(inside(root, f"execution/audit/{case}/{label}.json"))
    archive = Archive(arm, audit["turn_id"])
    result = archive.artifact("result")
    reservations = sorted(arm.glob("campaigns/*/batch/requests/*/reservation.json"))
    require(len(reservations) == audit["accounting"]["request_reservations"], "Attempt count differs")
    rows = [attempt_row(archive, result, reservation, i, gold) for i, reservation in enumerate(reservations, 1)]
    workloads = []
    for path in sorted((arm / "local_queries" / audit["turn_id"]).glob("*/g5-timing.json")):
        verify(path.parent)
        timing = read(path)
        workloads.append({"query_id": path.parent.name, "pipeline_seconds": timing["total_seconds"],
                          "reader_init_seconds": timing["reader_pipeline_init_seconds"],
                          "cpu_reader_reused": timing["cpu_reader_reused"],
                          "worker_seconds": [{"module": w["module"], "ready": w["ready_seconds"],
                              "request": w["request_seconds"], "confirmed_close": w["confirmed_close_seconds"]}
                              for w in timing["workers"]]})
    initial_catalog = next(iter(rows[0]["candidate_catalogs"]), None)
    cumulative = [catalog for row in rows for catalog in row["candidate_catalogs"]
                  if initial_catalog and catalog["sha256"] == initial_catalog["sha256"]]
    return {"answer_delivered": bool(result["answer_text"]), "stop_reason": result["stop_reason"],
            "arm_wall_seconds": audit["elapsed_seconds"], "query_workloads": workloads, "attempts": rows,
            "exact_initial_catalog_occurrences_across_requests": len(cumulative),
            "exact_initial_catalog_cumulative_wire_contribution_bytes":
                sum(catalog["wire_contribution_bytes"] for catalog in cumulative)}


def summarize(arms):
    rows = [row for arm in arms for row in arm["attempts"]]
    known = [row for row in rows if row["usage_known"]]
    verified_elapsed = [row["attempt_seconds"] for row in rows if row["state"] == "CONTENT_VERIFIED"]
    return {"recorded_attempts": len(rows), "unknown_attempts": sum(r["state"] == "UNKNOWN" for r in rows),
            "answers_delivered": sum(arm["answer_delivered"] for arm in arms),
            "known_usage": {key: sum(r["usage"][key] for r in known)
                            for key in ("input_tokens", "output_tokens", "total_tokens")},
            "recorded_request_wire_bytes": sum(r["request_wire_bytes"] for r in rows),
            "median_verified_attempt_seconds": median(verified_elapsed) if verified_elapsed else None}


def inspect(root):
    files = ["registration.json", "execution/mask-key.json", "execution/run-result.json",
             "execution/ratings-seal.json", "comparison.json"]
    hashes = {name: sha(inside(root, name)) for name in files}
    registration, masks, run, seal, comparison = (read(inside(root, name)) for name in files)
    require(run["status"] == "COMPLETE" and masks["registration_sha256"] == run["registration_sha256"]
            == seal["registration_sha256"] == comparison["registration_sha256"] == hashes["registration.json"]
            and seal["run_result_sha256"] == hashes["execution/run-result.json"]
            and seal["mask_key_sha256"] == hashes["execution/mask-key.json"]
            and comparison["ratings_seal_sha256"] == hashes["execution/ratings-seal.json"], "Run bindings differ")
    gold = {case["id"]: case["gold"] for case in registration["cases"]}
    require(set(masks["schedule"]) == set(gold), "Case schedules differ")
    results = {case: {profile: arm_cost(root, case, label, gold[case]) for label, profile in pairs}
               for case, pairs in masks["schedule"].items()}
    by_profile = {profile: summarize([arms[profile] for arms in results.values()]) for profile in registration["profiles"]}
    require(sum(row["recorded_attempts"] for row in by_profile.values()) == run["recorded_request_reservations"]
            and sum(row["known_usage"]["total_tokens"] for row in by_profile.values()) == comparison["known_tokens"],
            "Usage or attempt totals differ from evaluation")
    require(all(sha(inside(root, name)) == expected for name, expected in hashes.items()), "Inputs changed while auditing")
    source_root = Path(__file__).resolve().parents[2] / "src/genshin_corpus"
    projections = ("generation/clean_chat.py", "generation/clean_evidence.py", "rag/clean_research.py",
                   "rag/clean_research_catalog.py", "rag/clean_conversation_graph.py", "rag/clean_context.py")
    return {"format": "gc-g5-evidence-cost-audit-1", "paid_calls": 0, "socket_connections_blocked": True,
            "source_sha256": hashes,
            "audit_script_sha256": sha(__file__),
            "projection_code_sha256": {name: sha(source_root / name) for name in projections},
            "measurement_notes": ["UTF-8 wire bytes differ from provider tokens and estimated pricing.",
                "Request bytes describe the sealed request even when remote receipt is unknown.",
                "Archive projection changes only the named field; future responses, cache and latency are unmeasured.",
                "Attempt elapsed includes local raw archival and decoding; it is not provider-only time."],
            "profiles": by_profile, "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    generation = (repo / "data/generation").resolve()
    archive, out = args.archive.resolve(), args.out.resolve()
    require(archive.parent == out.parent == generation and archive.is_dir() and not out.exists(),
            "Use existing archive and fresh sibling under data/generation")
    offline = AssertionError("Socket connections are disabled for this audit")
    with patch("socket.socket.connect", side_effect=offline), patch("socket.socket.connect_ex", side_effect=offline):
        report = inspect(extended_path(archive))
    out.mkdir()
    write(out / "report.json", report)
    print(f"{out / 'report.json'}")


if __name__ == "__main__":
    main()
