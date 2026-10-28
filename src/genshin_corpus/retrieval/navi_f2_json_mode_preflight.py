"""Frozen and UNSENT F2 json_object Responses user-input plan.

Both prompt arms were separately smoke-tested on one matched pair. This file
has no API invocation, no retries, no formal experiment resume or publication.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from . import navi_f2_user_preflight as original
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_f2_content_recovery import render_user_json_mode_wire

BASE = original.BASE
PRE = original.PRE
OUT = BASE / "p05-navi-f2-json-mode-prepared-20261028-r1"
CAP = 16000
PAIRED_CASE = "6c2d16a80dac01dc3f8c"
LEGACY = BASE / "p05-navi-f2-legacy-json-mode-20261028-r1"
RC1 = BASE / "p05-navi-f2-rc1-json-mode-20261028-r1"


def build_plan():
    base = original.build_plan()  # verify frozen F1, 24 pairs, 48 slots, order
    verified = {}
    for label, root, folder in [
        ("legacy", LEGACY, "legacy_json_mode"),
        ("rc1", RC1, "rc1_json_mode"),
    ]:
        observation = read(root / "observation.json")
        if (observation.get("state") != "CONTENT_VERIFIED"
            or observation.get("mechanical_status") != "PASS"
            or observation.get("full_stream") != "PASS"
            or observation.get("reasoning_id_conflicts") != 0):
            raise ValueError("Real matched-pair live proof incomplete: " + label)
        case_file = PRE / "requests" / (PAIRED_CASE + "-" + label + ".json")
        case = read(case_file)
        expected = render_user_json_mode_wire(case, max_output_tokens=CAP)
        archived = read(root / folder / "request.json")
        if expected != archived:
            raise ValueError("Real prior request differs from proposed frozen API: " + label)
        verified[label] = {
            "case_sha256": sha(case_file),
            "archived_request_sha256": sha(root / folder / "request.json"),
            "raw_response_sha256": sha(root / folder / "response.body.raw"),
            "status": observation["state"],
            "input_tokens": observation["usage"]["input_tokens"],
            "output_tokens": observation["usage"]["output_tokens"],
        }
    records = []
    wire_hashes = set()
    by_pair = {}
    for old in base["records"]:
        case = read(PRE / "requests" / (old["request_id"] + ".json"))
        wire = render_user_json_mode_wire(case, max_output_tokens=CAP)
        fingerprint = value_hash(wire)
        if fingerprint in wire_hashes:
            raise ValueError("Duplicate rendered request")
        wire_hashes.add(fingerprint)
        row = dict(old, wire_fingerprint=fingerprint)
        records.append(row)
        by_pair.setdefault(row["pair_id"], []).append((row, wire))
    if len(records) != 48 or len(by_pair) != 24:
        raise ValueError("F2 frozen universe changed")
    for pair, (a, b) in by_pair.items():
        if {a[0]["arm"], b[0]["arm"]} != {"legacy", "rc1"}:
            raise ValueError("Unequal two-arm pairing")
        if a[0]["source_payload_sha256"] != b[0]["source_payload_sha256"]:
            raise ValueError("Source evidence mismatch")
        if any(a[1][key] != b[1][key] for key in
               ("model", "stream", "max_output_tokens", "text")):
            raise ValueError("Unequal provider options")
    counts = Counter(x["arm"] + "_" + x["mode"] for x in records)
    if counts != {"legacy_generation": 16, "rc1_generation": 16,
                  "legacy_review": 8, "rc1_review": 8}:
        raise ValueError("Unexpected F2 composition")
    return {
        "state": "PREPARED_NOT_AUTHORIZED",
        "version": "F2-Responses-user-item-json-object-v1",
        "provider": "jizhi", "model": "deepseek-v4.1-flash",
        "api": "Responses", "stream": True,
        "input": "one user message containing frozen prompt, two newlines, frozen material",
        "text_format": {"type": "json_object"},
        "max_output_tokens": CAP,
        "output_token_risk": "rc1 paired example consumed 15589/16000; 16000 insufficient headroom for some tasks",
        "formal_model_requests_sent": 0,
        "retry_policy": "never automatically retry UNKNOWN or issued request",
        "human_acceptance": "NOT_REVIEWED",
        "semantic_quality": "NOT_VALIDATED",
        "navigation_status": "INACTIVE",
        "prior_F2_untouched": True,
        "preflight_manifest_sha256": sha(PRE / "base-request-manifest.json"),
        "paired_live_proofs": verified,
        "counts": dict(counts),
        "records": records,
    }


def main():
    arg = argparse.ArgumentParser()
    arg.add_argument("--write", action="store_true")
    args = arg.parse_args()
    plan = build_plan()
    if OUT.exists():
        raise ValueError("Already prepared. Never silently overwrite a frozen plan.")
    print(json.dumps({"state": plan["state"], "version": plan["version"],
        "planned_slots": len(plan["records"]), "pairs": len({
        x["pair_id"] for x in plan["records"]}), "cap": CAP,
        "matched_live_proofs": list(plan["paired_live_proofs"]), "write": args.write},
        ensure_ascii=False), flush=True)
    if args.write:
        OUT.mkdir(parents=True, exist_ok=False)
        put(OUT / "prepared-manifest.json", plan)
        put(OUT / "STOP.json", {
            "reason": "NO_FORMAL_F2_AUTHORIZATION_AND_OUTPUT_HEADROOM_RISK",
            "formal_model_requests_sent": 0,
            "auto_resume": False,
            "no_retry": True,
        })


if __name__ == "__main__":
    main()
