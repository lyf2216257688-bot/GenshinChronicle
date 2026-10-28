"""Prepare a separate, unsent Phase05 F2 Responses user-input experiment revision.

Pure preparation; no SDK calls, credentials, model requests or resume of the old F2.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from . import navi_f2_preflight as pre
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_f2_content_recovery import render_user_wire

BASE = Path("data/retrieval")
F1 = BASE / "p05-navi-f1-20261026-r2"
PRE = BASE / "p05-navi-f2-preflight-20261026-r1"
OUT = BASE / "p05-navi-f2-user-input-prepared-20261028-r1"
MAX_OUTPUT = 30000


def build_plan():
    pre.verify_preflight(PRE)
    manifest = read(PRE / "base-request-manifest.json")
    order = manifest["order"]
    if len(order) != 48 or len(set(order)) != 48:
        raise ValueError("Frozen F2 denominator and order changed")
    records = []
    seen_wires = set()
    by_pair = {}
    for slot, request_id in enumerate(order, start=1):
        path = PRE / "requests" / (request_id + ".json")
        case = read(path)
        if case["request_id"] != request_id or case.get("arm") not in ("legacy", "rc1"):
            raise ValueError("Unexpected request arm or order")
        if case.get("send_authorized") is not False:
            raise ValueError("Frozen case must remain unsent by default")
        wire = render_user_wire(case, max_output_tokens=MAX_OUTPUT)
        if set(wire) != {"model", "stream", "max_output_tokens", "input"}:
            raise ValueError("Unequal or unknown wire fields")
        fingerprint = value_hash(wire)
        if fingerprint in seen_wires:
            raise ValueError("Duplicate raw wire fingerprint")
        seen_wires.add(fingerprint)
        records.append({
            "slot": slot, "request_id": request_id, "pair_id": case["pair_id"],
            "arm": case["arm"], "mode": case["mode"],
            "case_sha256": sha(path), "messages_sha256": case["messages_sha256"],
            "wire_fingerprint": fingerprint,
            "source_payload_sha256": case["source_payload_sha256"],
            "original_text_sha256": case["original_text_sha256"],
            "prompt_sha256": case["prompt_sha256"],
            "input_chars": len(wire["input"])
        })
        by_pair.setdefault(case["pair_id"], []).append((case, wire))
    if len(by_pair) != 24:
        raise ValueError("Expected exactly 24 independent A/B source pairs")
    for pair_id, rows in by_pair.items():
        if len(rows) != 2 or {x["arm"] for x, _ in rows} != {"legacy", "rc1"}:
            raise ValueError("Unbalanced prompt arms: " + pair_id)
        a, b = rows
        if (a[0]["source_payload_sha256"] != b[0]["source_payload_sha256"] or
                a[0]["mode"] != b[0]["mode"] or
                a[0]["original_text_sha256"] != b[0]["original_text_sha256"]):
            raise ValueError("Frozen source evidence not paired fairly: " + pair_id)
        if any(a[1][k] != b[1][k] for k in ("model", "stream", "max_output_tokens")):
            raise ValueError("Execution parameters differ across arms")
    counts = Counter(x["arm"] + "_" + x["mode"] for x in records)
    if counts != {"legacy_generation": 16, "rc1_generation": 16,
                  "legacy_review": 8, "rc1_review": 8}:
        raise ValueError("Unexpected F2 prompt/mode composition")
    return {
        "state": "PREPARED_NOT_AUTHORIZED", "run_version": "F2-user-input-v1",
        "prior_original_F2_unchanged": True, "model": records and
        render_user_wire(read(PRE / "requests" / (order[0] + ".json")),
                         max_output_tokens=MAX_OUTPUT)["model"],
        "api": "Responses", "stream": True, "instructions_field": False,
        "input_strategy": "frozen_system_prompt + two_newlines + frozen_user_payload",
        "provider_schema_field": False, "local_validation": {
            "legacy": "frozen native validator", "rc1": "frozen v0.2 Schema + source binding"},
        "max_output_tokens": MAX_OUTPUT, "retries": 0, "formal_calls_sent": 0,
        "semantic_quality": "NOT_VALIDATED", "navigation_status": "INACTIVE",
        "preflight_manifest_sha256": sha(PRE / "base-request-manifest.json"),
        "f1_seal_sha256": sha(F1 / "freeze-manifest.json"),
        "records": records, "counts": dict(counts),
        "warning": "Legacy live success not yet confirmed due to TLS handshake NOT_SENT. Do not batch or publish."
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    plan = build_plan()
    print(json.dumps({
        "state": plan["state"], "total_slots": len(plan["records"]),
        "pairs": len({x["pair_id"] for x in plan["records"]}),
        "counts": plan["counts"], "cap": plan["max_output_tokens"],
        "write": args.write
    }, ensure_ascii=False), flush=True)
    if args.write:
        OUT.mkdir(parents=True, exist_ok=False)
        put(OUT / "prepared-manifest.json", plan)
        put(OUT / "STOP.json", {
            "reason": "AWAIT_LEGACY_LIVE_VERIFICATION_AND_SEPARATE_F2_AUTHORIZATION",
            "formal_calls_sent": 0, "auto_resume": False, "no_retry": True
        })


if __name__ == "__main__":
    main()
