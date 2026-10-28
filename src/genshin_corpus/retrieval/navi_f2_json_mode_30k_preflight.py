"""Prepare a new unsent 30k F2 two-arm manifest, preserving all older runs."""
import argparse
import json
from pathlib import Path
from . import navi_f2_json_mode_preflight as prior
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_f2_content_recovery import render_user_json_mode_wire

ROOT = Path("data/retrieval")
PRE = prior.PRE
OUT = ROOT / "p05-navi-f2-json-mode-prepared-30k-20261028-r1"
CAP = 30000
PROOF = ROOT / "p05-navi-f2-rc1-json-mode-30k-20261028-r1"


def build_plan():
    base = prior.build_plan()
    observed = read(PROOF / "observation.json")
    if (observed.get("state") != "CONTENT_VERIFIED" or
            observed.get("mechanical_status") != "PASS" or
            observed.get("full_stream") != "PASS" or
            observed.get("reasoning_id_conflicts") != 0):
        raise ValueError("The 30k live response failed verification")
    rc1_case = read(PRE / "requests" / (prior.PAIRED_CASE + "-rc1.json"))
    wire30 = render_user_json_mode_wire(rc1_case, max_output_tokens=CAP)
    if read(PROOF / "rc1_json_mode/request.json") != wire30:
        raise ValueError("30k proof is not the identical frozen input wire")
    first16 = read(prior.RC1 / "rc1_json_mode/request.json")
    if not (first16["max_output_tokens"] == 16000 and
            {k:v for k,v in first16.items() if k!="max_output_tokens"} ==
            {k:v for k,v in wire30.items() if k!="max_output_tokens"}):
        raise ValueError("The 30k proof varied beyond the token cap")
    records = []
    by_pair = {}
    seen = set()
    for r in base["records"]:
        case = read(PRE / "requests" / (r["request_id"] + ".json"))
        wire = render_user_json_mode_wire(case, max_output_tokens=CAP)
        fingerprint = value_hash(wire)
        if fingerprint in seen: raise ValueError("Repeated request fingerprint")
        seen.add(fingerprint)
        row = dict(r, wire_fingerprint=fingerprint)
        records.append(row)
        by_pair.setdefault(row["pair_id"], []).append((row, wire))
    if len(records) != 48 or len(by_pair) != 24:
        raise ValueError("Wrong F2 scope")
    for pair, rows in by_pair.items():
        if len(rows) != 2 or {x[0]["arm"] for x in rows} != {"legacy", "rc1"}:
            raise ValueError("Unbalanced pair " + pair)
        a, b = rows
        if a[0]["source_payload_sha256"] != b[0]["source_payload_sha256"]:
            raise ValueError("Source mismatch")
        if any(a[1][k] != b[1][k] for k in
               ("model", "stream", "max_output_tokens", "text")):
            raise ValueError("Different generation settings")
    return {
        "state": "PREPARED_NOT_AUTHORIZED",
        "version": "F2-Responses-user-item-json-object-30k-v2",
        "provider": "jizhi", "model": "deepseek-v4.1-flash",
        "api": "Responses", "stream": True,
        "text_format": {"type": "json_object"},
        "input_strategy": "single user message = frozen prompt + two newlines + frozen material",
        "max_output_tokens": CAP,
        "paired_live_proofs": base["paired_live_proofs"],
        "additional_rc1_30k_proof": {
            "request_sha256": sha(PROOF / "rc1_json_mode/request.json"),
            "response_sha256": sha(PROOF / "rc1_json_mode/response.body.raw"),
            "usage": observed["usage"],
            "state": observed["state"],
        },
        "legacy_30k_live_test": "NOT_TESTED; legacy json_object succeeded at 16k",
        "proof_scope": "single matched source pair; cannot certify all F2 samples",
        "preflight_manifest_sha256": sha(PRE / "base-request-manifest.json"),
        "formal_model_requests_sent": 0,
        "original_F2_untouched": True,
        "auto_retries": 0,
        "semantic_quality": "NOT_VALIDATED",
        "human_acceptance": "NOT_REVIEWED",
        "navigation_status": "INACTIVE",
        "counts": base["counts"],
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    plan = build_plan()
    if OUT.exists():
        raise ValueError("Independent 30k preparation already exists")
    print(json.dumps({"state": plan["state"], "slots": len(plan["records"]),
        "cap": plan["max_output_tokens"], "live_rc1_pass": True,
        "live_legacy_30k": "NOT_TESTED", "write": args.write},
        ensure_ascii=False), flush=True)
    if args.write:
        OUT.mkdir(parents=True,exist_ok=False)
        put(OUT / "prepared-manifest.json", plan)
        put(OUT / "STOP.json", {
            "state": "PREPARED_NOT_AUTHORIZED",
            "formal_calls_sent": 0, "no_auto_resume": True,
            "no_retry": True, "run_requires_separate_explicit_approval": True
        })


if __name__ == "__main__":
    main()
