"""Reproduce archived evidence loss and register an isolated paired G5 evaluation."""

import argparse
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from random import SystemRandom
import statistics
import sys
import time
from unittest.mock import patch

from genshin_corpus.evidence_io import digest, inside, read, require, sha, verify, write


BASELINE = "gc-g5-official-4"
OPTIMIZED = "gc-g5-official-5"
ATTEMPTS_PER_ARM = 6
SECONDS_PER_ARM = 7200
FORMAT = "gc-g5-evidence-evaluation-1"
CONFIGS = ("clean-retrieval-v1.json", "index-selected-v1.json", "reranking-selected-v1.json",
           "clean-generation-v1.json")
CLEAN_ROOT = "data/clean/mihoyo_obc/zh-cn/clean-v1-full-20261106"
G7_ROOT = "data/generation/g7-70q-baseline-20261117"
G6_ROOT = "data/generation/g6-live-20261117-remaining"
FIXTURE = Path(__file__).with_name("g5_evidence_cases.json")
SCORES = ("supported_directly", "supported_inference", "correctly_left_unknown",
          "missing", "unsupported", "contradicted", "not_delivered")
G7_WITNESSES = {
    "Q013": [(3, "专心从事武器锻造")],
    "Q020": [(4, "又将女儿希琳许配给了他")],
    "Q046": [(3, "纯水精灵")],
    "Q050": [(16, "优恩尼娅"), (26, "奥里略斯")],
    "Q068": [(43, "明晨之镜")],
}


def checked(root, ref):
    path = inside(root, ref["path"])
    require(sha(path) == ref["sha256"], "Artifact hash changed")
    return read(path)


def assets(repo):
    from genshin_corpus.retrieval.clean_based.indexes import selected

    config = read(repo / "config/clean-retrieval-v1.json")
    _, manifests = selected(repo, config)
    paths = [repo / "config" / name for name in CONFIGS]
    paths.extend(path for path, _ in manifests.values())
    paths.extend([Path(manifests["dense"][1]["input_manifest"]).resolve(),
                  repo / CLEAN_ROOT / "metadata/manifest.json"])
    return {path.relative_to(repo).as_posix(): sha(path) for path in paths}


def readiness(repo, credential_root):
    from genshin_corpus.local_chat.credentials import read_credential

    verify(repo / "data/generation/g5-official-diagnostic-20261114/call")
    verify(repo / "data/generation/g3-official-thinking-20261114/audit")
    return {"credential_available": bool(read_credential(credential_root).key),
            "selected_assets": assets(repo), "protocol_receipts_verified": True}


def g7_index(repo):
    root = repo / G7_ROOT
    from genshin_corpus.evidence_io import strict_json

    items = [strict_json(line) for line in (root / "index.jsonl").read_text(encoding="utf8").splitlines()]
    by_id = {item["question_id"]: item for item in items}
    require(len(items) == len(by_id) == 70, "Unexpected G7 question inventory")
    return by_id


def registered_cases(repo, chosen):
    cases = read(FIXTURE)
    require(len(chosen) == len(set(chosen)) and set(chosen) <= set(cases), "Unknown or duplicate evaluation case")
    ids = chosen or list(cases)
    g7 = g7_index(repo)
    clean_root = repo / CLEAN_ROOT
    manifest = read(clean_root / "metadata/manifest.json")
    records = {r["content_id"]: r for r in manifest["records"]}
    from genshin_corpus.clean import iter_blocks, iter_text_values

    result = []
    for case_id in ids:
        item = cases[case_id]
        if case_id in g7:
            question = g7[case_id]["question"]
        else:
            question = item["question"]
        verified_gold = []
        for anchor in item.get("gold", []):
            binding = records[anchor["content_id"]]
            record = checked(clean_root, {"path": binding["record_path"], "sha256": binding["record_sha256"]})
            block = next(b for b in iter_blocks(record) if b["block_id"] == anchor["block_id"])
            value = next(v for v in iter_text_values(block) if anchor["quote"] in v["text"])
            start = value["text"].index(anchor["quote"])
            verified_gold.append({**anchor, "observation_id": binding["observation_id"],
                                  "record_sha256": binding["record_sha256"], "value_pointer": value["value_pointer"],
                                  "char_start": start, "char_end": start + len(anchor["quote"])})
        result.append({"id": case_id, "question": question, "claims": item["claims"], "gold": verified_gold})
    return result


def new_output(repo, out):
    generation = (repo / "data/generation").resolve()
    require(out.parent == generation and not out.exists(), "Use a fresh immediate child of data/generation")
    out.mkdir()


def preregister(repo, out, chosen, prior_reservations=0):
    cases = registered_cases(repo, chosen)
    from genshin_corpus.rag.clean_research import code_identity
    from genshin_corpus.retrieval.clean_based.runtime_v2 import code_identity as upstream_identity

    max_attempts = len(cases) * 2 * ATTEMPTS_PER_ARM
    require(type(prior_reservations) is int and 0 <= prior_reservations <= max_attempts - ATTEMPTS_PER_ARM,
            "Prior reservations leave no complete arm allowance")
    registration = {"format": FORMAT, "cases": cases, "assets": assets(repo), "fixture_sha256": sha(FIXTURE),
                    "code": code_identity(), "upstream_code": upstream_identity(), "combination": "S0",
                    "profiles": [BASELINE, OPTIMIZED], "attempts_per_case_per_arm": ATTEMPTS_PER_ARM,
                    "seconds_per_case_per_arm": SECONDS_PER_ARM, "max_total_attempts": max_attempts,
                    "prior_request_reservations": prior_reservations,
                    "rating_protocol": {"allowed_ratings": SCORES,
                                        "score_unit": "preregistered essential conclusion or uncertainty",
                                        "extra_claims": "record unsupported material claims separately",
                                        "original_answer": "unchanged supplied text; publication/citation/semantic states separate"}}
    new_output(repo, out)
    write(out / "registration.json", registration)
    return {"registration_sha256": sha(out / "registration.json"), "max_total_attempts": max_attempts,
            "prior_request_reservations": prior_reservations, "case_ids": [c["id"] for c in cases]}


def summarize_arm(store, result):
    from genshin_corpus.rag.clean_conversation_graph import verify_sources

    evidence = {}
    if result.get("sources_ref"):
        sources = verify_sources(store, result["sources_ref"])
        packet = sources["packet"]
        for citation in result["citations"]:
            cid = citation["citation_id"]
            binding = packet["citations"][cid]
            receipt = store.load(sources["reads"][binding["receipt_id"]])
            fragment = next(f for f in receipt["fragments"] if f["fragment_id"] == binding["fragment_id"])
            body = next(item["content"] for item in packet["evidence"] if item["citation_id"] == cid)
            scope = fragment["scope"]
            evidence[cid] = {"model_visible_content": body, "scope": scope, "source_item": fragment["source_item"],
                             "clean_refs": [t["clean_ref"] for t in fragment["texts"]]}
    return {"status": result["status"], "stop_reason": result["stop_reason"], "answer": result["answer_text"],
            "bound_citations": evidence, "citation_validation": result.get("citation_validation", {}).get("status")}


def attempt_statuses(session):
    statuses = []
    for path in sorted((session.batch.root / "requests").glob("*/reservation.json")):
        attempt = Path(read(path)["attempt"]).resolve()
        campaign = attempt.parent.parent.parent.parent
        require(attempt.parent.name == "attempts" and attempt.parent.parent.parent.name == "calls"
                and campaign.samefile(session.root), "Attempt left its isolated arm root")
        if (attempt / "complete.json").exists():
            verify(attempt)
            outcome = read(attempt / "result.json")
            statuses.append({"http_status": outcome.get("http_status"), "state": outcome["state"]})
        else:
            statuses.append({"http_status": None, "state": "UNKNOWN"})
    return statuses


def arm_session(store, settings):
    from genshin_corpus.rag.clean_research_live import ResearchSessions, permit_for

    return ResearchSessions(store, settings["campaign"], settings["limits"], permit=permit_for(store, settings))


def live_arm(repo, root, case, profile, authorization, credential_root, credential_revision, *, continuing=False):
    from genshin_corpus.local_chat.credentials import worker_credential
    from genshin_corpus.rag.clean_conversation import ConversationStore
    from genshin_corpus.rag.clean_conversation_graph import saved_result
    from genshin_corpus.rag.clean_research import run_turn
    from genshin_corpus.rag.clean_research_live import ResearchPipeline, prepare
    from genshin_corpus.rag.clean_store import put

    resuming = root.exists()
    require(not resuming or continuing, "Existing arm requires explicit continuation")
    if not resuming:
        root.mkdir()
    store = ConversationStore(root)
    if resuming:
        permit = read(root / "live/permit.json")
        settings = permit["settings"]
        chats = store.chats()
        require(len(chats) == 1 and chats[0]["title"] == case["id"]
                and store.chat(chats[0]["id"])["settings"] == settings and settings["generation_profile"] == profile
                and settings["limits"]["campaign_requests"] == settings["limits"]["turn_requests"] == ATTEMPTS_PER_ARM
                and permit["authorization"] == authorization, "Cannot continue a different question or permit")
        jobs = store.jobs(chats[0]["id"])
        require(len(jobs) == 1, "Cannot continue a different arm")
        tid = jobs[0]["turn_id"]
        require(store.turn(tid)["question"] == case["question"], "Cannot continue a different question")
    else:
        settings = prepare(store, repo, authorization, requests=ATTEMPTS_PER_ARM, seconds=SECONDS_PER_ARM, profile=profile)
        session_id = store.create_chat(case["id"], settings)
        tid = store.enqueue(session_id, "registered-question", case["question"])
    session = arm_session(store, settings)
    result = saved_result(store, tid) if resuming else None
    pipeline = None
    started = time.perf_counter()
    try:
        try:
            if result is None:
                can_start = not resuming or (session.batch.accounting()["request_reservations"] == 0
                    and not (root / "live/closed.json").exists() and time.time() < permit["expires_at"])
                if can_start:
                    pipeline = ResearchPipeline(store, tid, repo, staged_local_queries=True, explicit_resume=resuming)
                    with worker_credential(credential_root, credential_revision):
                        result = run_turn(store, tid, pipeline, continue_turn=resuming)
            if result is not None:
                review = summarize_arm(store, result)
            else:
                review = {"status": "interrupted", "stop_reason": "prior_incomplete_arm_retained", "answer": None,
                          "bound_citations": {}, "citation_validation": None}
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            # The original worker/ledger artifacts remain in the arm root. Do not relay exception text.
            review = {"status": "interrupted", "stop_reason": type(exc).__name__, "answer": None,
                      "bound_citations": {}, "citation_validation": None}
    finally:
        try:
            if pipeline is not None:
                pipeline.close()
        finally:
            # Ctrl+C leaves a zero-reservation arm resumable under its existing permit.
            if sys.exc_info()[0] is not KeyboardInterrupt and not (root / "live/closed.json").exists():
                put(root / "live/closed.json", {"reason": "evaluation_arm_closed", "turn_id": tid})
    accounting = session.batch.accounting()
    statuses = attempt_statuses(session)
    require(len(statuses) == accounting["request_reservations"], "Arm attempt audit count changed")
    ranked_root = root / "local_queries" / tid / "original"
    original_rank = None
    if (ranked_root / "complete.json").is_file():
        verify(ranked_root)
        ranked = read(ranked_root / "ranked.json")
        original_rank = {"ordered_chunk_ids": [c["chunk_id"] for c in ranked["candidates"]],
                         "query": ranked["query"]["search_text"], "input_bundle_id": ranked["input_bundle_id"]}
    return review, {"turn_id": tid, "elapsed_seconds": time.perf_counter() - started, "accounting": accounting,
                    "original_rank": original_rank, "attempt_statuses": statuses}


def run_live(repo, out, credential_root, expected_hash, approved_attempts, authorization, *, continuing=False):
    from genshin_corpus.local_chat.credentials import read_credential
    from genshin_corpus.rag.clean_research import code_identity
    from genshin_corpus.retrieval.clean_based.runtime_v2 import code_identity as upstream_identity

    require(out.parent == (repo / "data/generation").resolve(), "Registration must be under data/generation")
    registration_file = out / "registration.json"
    require(sha(registration_file) == expected_hash, "Registration hash does not match approval")
    registration = read(registration_file)
    require(registration["format"] == FORMAT and registration["profiles"] == [BASELINE, OPTIMIZED]
            and registration["attempts_per_case_per_arm"] == ATTEMPTS_PER_ARM
            and registration["seconds_per_case_per_arm"] == SECONDS_PER_ARM and registration["combination"] == "S0",
            "Unexpected evaluation protocol")
    cases = registration["cases"]
    require(cases == registered_cases(repo, [case["id"] for case in cases]) and sha(FIXTURE) == registration["fixture_sha256"],
            "Preregistered questions or gold changed")
    require(authorization.strip() and approved_attempts == registration["max_total_attempts"]
            == len(cases) * ATTEMPTS_PER_ARM * 2, "Approve the preregistered maximum number of requests")
    prior = registration.get("prior_request_reservations", 0)
    require(type(prior) is int and 0 <= prior <= approved_attempts - ATTEMPTS_PER_ARM, "Invalid prior request reservations")
    require(registration["assets"] == assets(repo) and registration["code"] == code_identity()
            and registration["upstream_code"] == upstream_identity(), "Preregistered code/assets changed")
    require(readiness(repo, credential_root)["credential_available"], "Official credential unavailable")
    credential = read_credential(credential_root)
    require(bool(credential.key) and bool(credential.revision), "Official credential unavailable")
    credential_revision = credential.revision
    del credential

    from genshin_corpus.model_calls import locked

    with locked(out / "run.lock"):
        return execute_live(repo, out, cases, expected_hash, approved_attempts, authorization, credential_root,
                            credential_revision, continuing=continuing, prior_reservations=prior)


def execute_live(repo, out, cases, expected_hash, approved_attempts, authorization, credential_root,
                 credential_revision, *, continuing, prior_reservations=0):
    execution = out / "execution"
    run_result = execution / "run-result.json"
    require(not run_result.exists(), "This evaluation already finished")
    require(execution.is_dir() == continuing, "Use --continue only for an existing interrupted evaluation")
    case_roots = execution / "arms"
    review_root = out / "blind-review"
    mask = execution / "mask-key.json"
    if not continuing:
        case_roots.mkdir(parents=True)
        review_root.mkdir()
        rng = SystemRandom()
        offset = rng.randrange(2)
        schedule = {}
        for i, case in enumerate(cases):
            execution_order = [BASELINE, OPTIMIZED] if (i + offset) % 2 == 0 else [OPTIMIZED, BASELINE]
            masked = rng.sample(["A", "B"], 2)
            schedule[case["id"]] = list(zip(masked, execution_order, strict=True))
        write(mask, {"registration_sha256": expected_hash, "schedule": schedule, "approved_attempts": approved_attempts,
                     "prior_request_reservations": prior_reservations})
    existing = read(mask)
    schedule = existing["schedule"]
    require(existing["registration_sha256"] == expected_hash and existing["approved_attempts"] == approved_attempts
            and existing.get("prior_request_reservations", 0) == prior_reservations
            and list(schedule) == [case["id"] for case in cases]
            and all({label for label, _ in pair} == {"A", "B"}
                    and {profile for _, profile in pair} == {BASELINE, OPTIMIZED}
                    and len(pair) == 2 for pair in schedule.values()), "Masked execution schedule changed")
    complete_arms = 0
    total_used = 0
    budget_skipped_arms = 0
    paused = None
    try:
        for case in cases:
            case_id = case["id"]
            case_root = case_roots / case_id
            case_root.mkdir(exist_ok=True)
            for label, profile in schedule[case_id]:
                audit_path = execution / "audit" / case_id / f"{label}.json"
                view_path = review_root / case_id / f"{label}.json"
                if view_path.exists():
                    require(audit_path.is_file(), "Existing mask output lost its arm audit")
                    previous = read(audit_path)
                    total_used += previous["accounting"]["request_reservations"]
                    budget_skipped_arms += previous.get("budget_skipped", False)
                    complete_arms += 1
                    continue
                print(f"{case_id} / {label}", flush=True)
                if not (case_root / label).exists() and total_used + prior_reservations + ATTEMPTS_PER_ARM > approved_attempts:
                    review = {"status": "not_started", "stop_reason": "aggregate_budget_insufficient", "answer": None,
                              "bound_citations": {}, "citation_validation": None}
                    audit = {"budget_skipped": True, "accounting": {"request_reservations": 0}, "attempt_statuses": []}
                    budget_skipped_arms += 1
                else:
                    require(total_used + prior_reservations + ATTEMPTS_PER_ARM <= approved_attempts,
                            "Prior arm could exceed remaining aggregate budget")
                    review, audit = live_arm(repo, case_root / label, case, profile, authorization,
                                             credential_root, credential_revision, continuing=continuing)
                if not audit_path.exists():
                    write(audit_path, audit)
                else:
                    require(read(audit_path)["accounting"] == audit["accounting"], "Original arm audit changed")
                write(view_path, {"case_id": case_id, "arm": label, "question": case["question"], **review})
                complete_arms += 1
                total_used += audit["accounting"]["request_reservations"]
                print(f"{case_id} / {label}: {review['status']}", flush=True)
                if any(attempt["http_status"] in {401, 403} for attempt in audit["attempt_statuses"]):
                    paused = "credential_rejected"
                    break
            if paused:
                break
    except KeyboardInterrupt:
        paused = "operator_interrupted"
    if paused:
        progress = execution / "progress"
        result = {"status": "PAUSED", "reason": paused, "recorded_arms": complete_arms,
                  "planned_arms": 2 * len(cases), "recorded_request_reservations": total_used,
                  "prior_request_reservations": prior_reservations,
                  "registration_sha256": expected_hash}
        write(progress / f"{len(list(progress.glob('*.json'))) + 1:04d}.json", result)
        return result
    rating_sheet = {"allowed_ratings": list(SCORES), "cases": [
        {"case_id": case["id"], "question": case["question"], "reference_gold": case["gold"],
         "ratings": {label: [{"claim": claim, "rating": None, "citation_ids": [], "reason": ""}
                             for claim in case["claims"]] for label in ("A", "B")},
         "unsupported_material_claims": {"A": [], "B": []}} for case in cases]}
    rating_path = review_root / "rating-sheet.json"
    if rating_path.exists():
        require(read(rating_path) == rating_sheet, "Rating template changed before evaluation closed")
    else:
        write(rating_path, rating_sheet)
    result = {"status": "COMPLETE", "recorded_arms": complete_arms, "planned_arms": 2 * len(cases),
              "budget_skipped_arms": budget_skipped_arms, "prior_request_reservations": prior_reservations,
              "recorded_request_reservations": total_used, "review_status": "NOT_REVIEWED",
              "registration_sha256": expected_hash}
    write(run_result, result)
    return result


def seal_ratings(out):
    """Check blind references and freeze reviewer judgments before revealing treatments."""
    registration = read(out / "registration.json")
    execution = out / "execution"
    run = read(execution / "run-result.json")
    registration_hash = sha(out / "registration.json")
    require(run["registration_sha256"] == registration_hash and run["recorded_arms"] == run["planned_arms"],
            "Need the complete registered set of arm records")
    blind = out / "blind-review"
    reviews = sorted((blind / "ratings").glob("*.json"))
    registered = {case["id"]: case for case in registration["cases"]}
    cases, counts = {}, {}
    for path in reviews:
        review = read(path)
        for row in review["cases"]:
            cid = row["case_id"]
            require(cid in registered and cid not in cases and set(row["arms"]) == {"A", "B"},
                    "Unexpected, duplicate or incomplete blind case rating")
            cases[cid] = path.relative_to(out).as_posix()
            counts[cid] = {}
            for arm, score in row["arms"].items():
                arm_path = blind / cid / f"{arm}.json"
                require(score["answer_sha256"] == sha(arm_path), "Blind answer changed after review")
                output = read(arm_path)
                ratings = score["ratings"]
                require([rating["claim"] for rating in ratings] == registered[cid]["claims"] and
                        all(isinstance(rating["reason"], str) and rating["reason"].strip() for rating in ratings),
                        "Review differs from preregistered essential claims")
                for rating in ratings:
                    require(rating["rating"] in SCORES and
                            (rating["rating"] == "not_delivered") == (not output["answer"]) and
                            set(rating["citation_ids"]) <= set(output["bound_citations"]), "Invalid claim rating or reference")
                counts[cid][arm] = {"has_answer": bool(output["answer"]),
                                    "ratings": dict(Counter(row["rating"] for row in ratings)),
                                    "unsupported_material_claims": len(score["unsupported_material_claims"])}
    require(set(cases) == set(registered), "Missing blind ratings")
    seal = {"format": FORMAT, "registration_sha256": registration_hash,
            "run_result_sha256": sha(execution / "run-result.json"),
            "mask_key_sha256": sha(execution / "mask-key.json"),
            "rating_sha256": {path.relative_to(out).as_posix(): sha(path) for path in reviews},
            "cases": cases, "counts": counts, "treatments_revealed": False}
    path = execution / "ratings-seal.json"
    write(path, seal)
    return {"seal_sha256": sha(path), "cases": sorted(cases)}


class Archive:
    def __init__(self, root, turn_id):
        self.root = root
        self.turn_id = turn_id

    def load(self, ref):
        return checked(self.root, ref)

    def artifact(self, key):
        return read(self.root / "chat_artifacts" / self.turn_id / (digest(key) + ".json"))


def archived_g7(repo, item):
    base = repo / G7_ROOT
    archive = Archive(inside(base, item["store"]), item["turn_id"])
    result = read(inside(base, item["result_path"]))["result"]
    require(result["turn_id"] == item["turn_id"], "G7 archived result is for another turn")
    return archive, result


def catalog_replay(archive):
    from genshin_corpus.evidence_io import strict_json
    from genshin_corpus.generation.clean_chat import encoded
    from genshin_corpus.rag.clean_research_catalog import candidate_page

    request = archive.artifact("g5-request-0")
    initial = archive.load(request["data_ref"])
    sources = archive.load(request["sources_ref"])
    manifest = request["manifest"]
    wire = manifest["wire"]
    message = next(row for row in wire["messages"] if row["role"] == "user")
    payload = strict_json(message["content"])

    def wire_bytes(page):
        message_contents = encoded({**payload, "candidate_catalog": page}).decode()
        messages = [{**row, "content": message_contents} if row is message else row for row in wire["messages"]]
        return len(encoded({**wire, "messages": messages}))

    page = candidate_page(archive, initial, "original", packet=sources["packet"],
                          fits=lambda value: wire_bytes(value) <= manifest["limits"]["input_bytes"])
    require(page is not None, "Archived original request has no catalog allowance")
    collected = list(page["rows"])
    cursor = page["next_after_rank"]
    while cursor is not None:
        followup = candidate_page(archive, initial, "original", after_rank=cursor, max_rows=20, max_bytes=8000)
        require(followup is not None and followup["through_rank"] > cursor, "Catalog pagination stopped early")
        collected.extend(followup["rows"])
        cursor = followup["next_after_rank"]
    require([row["rank"] for row in collected] == list(range(1, len(initial["handles"]) + 1)),
            "Catalog omitted or reordered archived candidates")
    return {"registered": len(initial["handles"])}, {
        "wire_before": len({c["handle"] for c in payload["catalog"]}), "first_page_after": page["through_rank"],
        "reachable_after": len(collected), "first_page_overlay_bytes": wire_bytes(page),
        "archived_wire_input_limit": manifest["limits"]["input_bytes"],
        "witness_previews": [row for row in page["rows"] if row["rank"] in {16, 26, 43}],
    }


def parent_gaps(receipt):
    fragment = receipt["fragments"][0]
    positions = [part["clean_ref"] for unit in fragment["structure"]["units"] for part in unit["parts"]]
    intervals = {}
    for value in fragment["texts"]:
        ref = value["clean_ref"]
        key = (ref["observation_id"], ref["block_id"], ref["value_pointer"])
        intervals.setdefault(key, []).append((ref["char_start"], ref["char_end"]))
    missing = 0
    for ref in positions:
        key = (ref["observation_id"], ref["block_id"], ref["value_pointer"])
        start, end = ref["char_start"], ref["char_end"]
        for left, right in sorted(intervals.get(key, ())):
            if left > start:
                break
            start = max(start, right)
        if start < end:
            missing += 1
    return {"positions": len(positions), "uncovered": missing}


def packet_inputs(archive, result):
    from genshin_corpus.generation.clean_evidence import assemble

    sources = archive.load(result["sources_ref"])
    archived = sources["packet"]
    groups = [archive.load(ref) for ref in sources["ranked_refs"]]
    receipts = [archive.load(sources["reads"][rid]) for rid in archived["read_receipt_ids"]]
    parameters = {"selection_ids": archived["selection_ids"], "context": sources["assembly_context"],
                  "source_failures": sources["source_failures"], "budget": archived["policy"]["budget"]}
    require(assemble(groups, receipts, **parameters) == archived, "Legacy Packet replay differs from archived Packet")
    return archived, groups, receipts, parameters


def clean_quote(receipt, needle):
    for fragment in receipt["fragments"]:
        for value in fragment["texts"]:
            if needle in value["text"]:
                ref = value["clean_ref"]
                start = ref["char_start"] + value["text"].index(needle)
                return {"quote": needle, "receipt_id": receipt["receipt_id"], "fragment_id": fragment["fragment_id"],
                        "clean_ref": {**ref, "char_start": start, "char_end": start + len(needle)}}
    raise ValueError("Expected original text missing from source receipt")


def evidence_row(packet, candidate, witness):
    cid = candidate["chunk_id"]
    disposition = next(row for row in packet["dispositions"] if row["group"] == 0 and row["chunk_id"] == cid)
    bindings = [e["citation_id"] for e in packet["evidence"] if e["fragment_id"] == witness["fragment_id"]]
    return {"disposition": disposition, "packet_citation_ids": bindings}


def replay_case(archive, result, reader, witnesses):
    from genshin_corpus.generation.clean_evidence import EVIDENCE_QUALITY_ADMISSION, assemble

    before, groups, receipts, parameters = packet_inputs(archive, result)
    handles = reader.register(groups[0])
    scripted, report = [], []
    existing_chunks = {r["chunk_id"]: r for r in receipts if r["scope"] == "chunk"}
    for rank, quote in witnesses:
        candidate = groups[0]["candidates"][rank - 1]
        require(candidate["reranking"]["final_rank"] == rank, "Witness rank changed")
        cid = candidate["chunk_id"]
        receipt = reader.read(handles[cid])
        before_read = cid in existing_chunks
        if before_read:
            require(receipt == existing_chunks[cid], "Readback differs from original")
        else:
            receipts.append(receipt)
            scripted.extend(f["fragment_id"] for f in receipt["fragments"])
        report.append((candidate, {"rank": rank, "content_id": candidate["source"]["source_item"]["content_id"],
                                   "read_before": before_read, "read_after": True, **clean_quote(receipt, quote)}))
    after = assemble(groups, receipts, **parameters, admission=EVIDENCE_QUALITY_ADMISSION,
                     priority_ids=scripted, exposed_ids=scripted)
    require(after["input_group_digests"] == before["input_group_digests"], "Replay altered ranked inputs")
    return {"old_packet": before["packet_id"], "new_packet": after["packet_id"],
            "historical_answer": result["answer_text"], "new_answer": None,
            "old_budget": before["policy"]["budget"], "new_budget": after["policy"]["budget"],
            "anchors": [{**witness, "before": evidence_row(before, candidate, witness),
                         "after": evidence_row(after, candidate, witness)} for candidate, witness in report]}


def history_replay(repo, out):
    from genshin_corpus.generation.clean_evidence import EvidenceReader
    from genshin_corpus.rag.clean_research_answer import bind_answer
    from genshin_corpus.retrieval.clean_based.indexes import selected
    from genshin_corpus.retrieval.clean_based.source import InputReader

    clean_assets = assets(repo)
    g7 = g7_index(repo)
    cases = {}
    historical_answers = bound_answers = references = 0
    parents_to_read = []
    parents_to_verify = []
    for cid, item in g7.items():
        archive, result = archived_g7(repo, item)
        registered, catalog = catalog_replay(archive)
        case = {**registered, **catalog}
        if cid not in G7_WITNESSES:
            del case["witness_previews"]
        cases[cid] = case
        if not result["answer_text"]:
            continue
        historical_answers += 1
        bound_answers += bool(result["citations"])
        sources = archive.load(result["sources_ref"])
        packet = sources["packet"]
        if not result["citations"]:
            _, ids, validation = bind_answer(packet, result["answer_text"])
            case["citation_only_rebinding"] = {"before": result["citation_validation"]["status"],
                                             "after": validation["status"], "new_addresses": ids}
        for link in result["citations"]:
            references += 1
            require(link["turn_id"] == item["turn_id"] and link["packet_id"] == packet["packet_id"],
                    "Archived citation points outside the saved Packet")
            binding = packet["citations"][link["citation_id"]]
            receipt = archive.load(sources["reads"][binding["receipt_id"]])
            ref = sources["parents"].get(receipt["handle"])
            if ref:
                archive.load(ref)
            else:
                parents_to_read.append((cid, link["citation_id"], archive, sources, receipt))
        if cid in {"Q050", "Q070"}:
            for ref in sources["reads"].values():
                receipt = archive.load(ref)
                if receipt["scope"] == "parent":
                    parents_to_verify.append((cid, archive, sources, receipt))

    _, manifests = selected(repo, read(repo / "config/clean-retrieval-v1.json"))
    witness_trace = {}
    g6 = {}
    parent_trace = []
    with InputReader(manifests["dense"][1]["input_manifest"]) as source:
        reader = EvidenceReader(source, verified_parent_ranges=True)
        for cid, witnesses in G7_WITNESSES.items():
            archive, result = archived_g7(repo, g7[cid])
            witness_trace[cid] = replay_case(archive, result, reader, witnesses)
        g6_root = repo / G6_ROOT
        for combo, witnesses in {"S0": [(3, "纯水精灵")], "S2": [(4, "我给予你的赠礼"), (6, "纯水精灵")]}.items():
            result = read(g6_root / "case-reports" / f"Q046-{combo}.json")["result"]
            g6[combo] = replay_case(Archive(g6_root, result["turn_id"]), result, reader, witnesses)
        for cid, citation, archive, sources, receipt in parents_to_read:
            for ref in sources["ranked_refs"]:
                reader.register(archive.load(ref))
            fresh = reader.read(receipt["handle"], "natural_parent")
            require(fresh["source_snapshot"] == receipt["source_snapshot"], "Parent readback source changed")
            cases[cid].setdefault("parent_expansion", []).append({"citation_id": citation, "before": "not_saved",
                                                                "after": "verified_readback", "new_receipt_id": fresh["receipt_id"]})
        for cid, archive, sources, receipt in parents_to_verify:
            for ref in sources["ranked_refs"]:
                reader.register(archive.load(ref))
            fresh = reader.read(receipt["handle"], "parent")
            require(fresh["source_snapshot"] == receipt["source_snapshot"], "Structured Parent source changed")
            old, new = parent_gaps(receipt), parent_gaps(fresh)
            require(old["positions"] == new["positions"] and not new["uncovered"], "Structured Parent still has locator gaps")
            parent_trace.append({"case_id": cid, "content_id": receipt["source_snapshot"]["source_item"]["content_id"],
                                 "before": old, "after": new})
    summary = {"cases": 70, "answers": historical_answers, "answers_with_bound_ids": bound_answers,
               "bound_citation_addresses": references, "missing_parent_before": len(parents_to_read),
               "catalog_before_count": sorted({c["wire_before"] for c in cases.values()}),
               "catalog_after_first_page_min_median_max": [min(c["first_page_after"] for c in cases.values()),
                  statistics.median(c["first_page_after"] for c in cases.values()), max(c["first_page_after"] for c in cases.values())],
               "catalog_after_full_reachability": sum(c["reachable_after"] == c["registered"] for c in cases.values())}
    report = {"format": FORMAT, "assets": clean_assets, "g7_index_sha256": sha(repo / G7_ROOT / "index.jsonl"),
              "summary": summary, "g7_catalog_citation_parent": cases, "g7_packet": witness_trace, "g6_q046_packet": g6,
              "structured_parent_locator_coverage": parent_trace, "remote_calls": 0,
              "meaning": "Catalog is an overlay on archived requests. Targeted rereads are scripted. No new model selection, answer, semantic rating or upstream ranking."}
    write(out / "report.json", report)
    return summary


def replay(repo, out):
    new_output(repo, out)
    with patch("socket.socket.connect", side_effect=AssertionError("Offline replay blocked socket connect")), \
            patch("socket.socket.connect_ex", side_effect=AssertionError("Offline replay blocked socket connect_ex")):
        result = history_replay(repo, out)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--credential-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("readiness")
    reg = commands.add_parser("register")
    reg.add_argument("--out", type=Path, required=True)
    reg.add_argument("--case", action="append", default=[])
    reg.add_argument("--prior-reservations", type=int, default=0)
    live = commands.add_parser("run-live")
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--registration-sha256", required=True)
    live.add_argument("--approved-attempts", type=int, required=True)
    live.add_argument("--authorization", required=True)
    live.add_argument("--continue", dest="continuing", action="store_true")
    offline = commands.add_parser("replay")
    offline.add_argument("--out", type=Path, required=True)
    review = commands.add_parser("seal-ratings")
    review.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    credential = (args.credential_root or repo / "data/generation/personal-chat-v1").resolve()
    if args.command == "readiness":
        result = readiness(repo, credential)
    elif args.command == "register":
        result = preregister(repo, args.out.resolve(), args.case, args.prior_reservations)
    elif args.command == "replay":
        result = replay(repo, args.out.resolve())
    elif args.command == "seal-ratings":
        result = seal_ratings(args.out.resolve())
    else:
        result = run_live(repo, args.out.resolve(), credential, args.registration_sha256,
                          args.approved_attempts, args.authorization, continuing=args.continuing)
    print(__import__("json").dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
