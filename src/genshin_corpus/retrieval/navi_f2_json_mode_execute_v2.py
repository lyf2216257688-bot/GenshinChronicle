"""Isolated F2 Responses JSON-object runner; no dispatch without two opt-in flags.

Reuses the audited raw SDK/SSE recorder and pure dual-arm content verifier.
Never resumes a prior attempt; never changes the original F2 experiment.
"""
import argparse
import json
from pathlib import Path

from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1
from . import navi_f2_preflight as pre
from . import navi_f2_sdk_transport as transport
from . import navi_v02_call_recovery as recorder
from . import navi_f2_json_mode_preflight as plan16
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_f2_content_recovery import render_user_json_mode_wire, assess_content

BASE = Path("data/retrieval")
F1 = BASE / "p05-navi-f1-20261026-r2"
PRE = BASE / "p05-navi-f2-preflight-20261026-r1"
OUT = BASE / "p05-navi-f2-json-mode-execution-20261028-r1"
PLAN = BASE / "p05-navi-f2-json-mode-prepared-30k-20261028-r1"


def verify_plan(plan_root=PLAN):
    pre.verify_preflight(PRE)
    manifest = read(plan_root / "prepared-manifest.json")
    if manifest.get("state") != "PREPARED_NOT_AUTHORIZED":
        raise ValueError("Unexpected plan state")
    if manifest.get("formal_model_requests_sent") != 0:
        raise ValueError("Plan is not an unsent freeze")
    if sha(PRE / "base-request-manifest.json") != manifest["preflight_manifest_sha256"]:
        raise ValueError("Frozen F2 preflight differs")
    records = manifest["records"]
    if len(records) != 48 or len({x["request_id"] for x in records}) != 48:
        raise ValueError("Wrong F2 denominator")
    cap = manifest["max_output_tokens"]
    if type(cap) is not int or cap not in (16000, 30000):
        raise ValueError("Unapproved output cap")
    for row in records:
        case = read(PRE / "requests" / (row["request_id"] + ".json"))
        if case.get("send_authorized") is not False:
            raise ValueError("Preflight case was unexpectedly authorized")
        wire = render_user_json_mode_wire(case, max_output_tokens=cap)
        if value_hash(wire) != row["wire_fingerprint"]:
            raise ValueError("F2 request hash changed")
    old = read(BASE / "p05-navi-f2-experiment-20261027-r1" / "STOP.json")
    if old["slot"] != 1 or old["remaining_not_sent"] != 47:
        raise ValueError("Original failed F2 record changed")
    return manifest


def verify_archived_content(folder, case, *, evidence, legacy_validator, schema):
    enc = read(folder / "http.json")["headers"].get("content-encoding", "")
    raw = c1.sse_events(transport.decoded_body(
        (folder / "response.body.raw").read_bytes(), enc))
    sdk = [json.loads(line) for line in
           (folder / "sdk-events.jsonl").read_text(encoding="utf-8").splitlines()]
    packet = read(F1 / "deliveries" / (case["pair_id"] + ".json"))
    return assess_content(raw, sdk, case, request_wire=read(folder / "request.json"),
                          packet=packet, evidence=evidence, legacy_validator=legacy_validator,
                          schema=schema)


def execute_one(row, cap, key, evidence, legacy_validator, schema):
    """One attempt per slot; if it has ever been reserved, refuse all replay."""
    case_path = PRE / "requests" / (row["request_id"] + ".json")
    case = read(case_path)
    wire = render_user_json_mode_wire(case, max_output_tokens=cap)
    if value_hash(wire) != row["wire_fingerprint"]:
        raise ValueError("Request fingerprint differs from plan")
    folder = OUT / row["request_id"]
    if folder.exists():
        raise ValueError("Previously issued slot cannot be sent again")
    # The isolated diagnostic recorder stores raw HTTP bytes and SDK events
    # atomically, with no retry. It is not the original F2 runner.
    old_root, old_case, old_request = recorder.ROOT, recorder.CASE, recorder.request
    try:
        recorder.ROOT = OUT
        recorder.CASE = case_path
        def exact_request(kind, stream=True):
            if kind != "user_fallback" or stream is not True:
                raise ValueError("Dispatch does not match F2 plan")
            return wire
        recorder.request = exact_request
        result = recorder.run_one(row["request_id"], "user_fallback", True, key)
    finally:
        recorder.ROOT, recorder.CASE, recorder.request = old_root, old_case, old_request
    summary = {"request_id": row["request_id"],
               "pair_id": row["pair_id"], "arm": row["arm"],
               "state": result["state"], "http_status": result.get("http_status"),
               "usage": result.get("usage", "UNKNOWN"),
               "retries": 0, "actual_fee": "UNKNOWN",
               "human_acceptance": "NOT_REVIEWED",
               "navigation_status": "INACTIVE"}
    if result["state"] == "COMPLETED":
        try:
            checked = verify_archived_content(folder, case, evidence=evidence,
                                             legacy_validator=legacy_validator, schema=schema)
            summary.update({
                "state": checked["state"],
                "mechanical_status": checked["mechanical_status"],
                "full_stream_status": checked["full_stream_status"],
                "protocol_warning": checked["protocol_warning"],
                "output_token_headroom": checked["output_token_headroom"],
                "near_output_limit": checked["near_output_limit"],
                "message_sha256": checked["message_sha256"],
                "usage": checked["usage"],
                "semantic_quality": checked["semantic_quality"],
            })
        except Exception as exc:
            summary.update(state="VALIDATION_UNKNOWN",
                           error_type=type(exc).__name__,
                           retry_allowed=False)
    put(OUT / "validation" / (row["request_id"] + ".json"), summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-authorized", action="store_true")
    parser.add_argument("--ack-new-f2-48-request-batch", action="store_true")
    parser.add_argument("--max-requests", type=int, default=48)
    args = parser.parse_args()
    manifest = verify_plan()
    cap = manifest["max_output_tokens"]
    print(json.dumps({
        "state": "DRY_RUN" if not args.execute_authorized else "EXECUTION_REQUESTED",
        "slots": len(manifest["records"]), "cap": cap,
        "provider": manifest["provider"],
        "record_set": str(PLAN), "old_F2_untouched": True,
        "no_auto_retry": True}, ensure_ascii=False), flush=True)
    if not args.execute_authorized:
        return
    if not args.ack_new_f2_48_request_batch:
        raise ValueError("Explicit 48-request approval flag required")
    if args.max_requests != 48:
        raise ValueError("Never run a partial official F2 cohort")
    if OUT.exists():
        raise ValueError("Run directory exists; no replay or automatic resume")
    env = environment()
    key = env.get("JIZHI_API_KEY")
    if not key or env.get("JIZHI_BASE_URL", "").rstrip("/") != c1.BASE_URL:
        raise ValueError("Route mismatch or missing credential")
    OUT.mkdir(parents=True, exist_ok=False)
    put(OUT / "run-config.json", {
        "execution_version": manifest["version"],
        "prepared_manifest_sha256": sha(PLAN / "prepared-manifest.json"),
        "cap": cap, "order": [r["request_id"] for r in manifest["records"]],
        "planned_requests": 48, "retries": 0,
        "new_run": True, "original_F2_untouched": True,
        "human_acceptance": "NOT_REVIEWED", "navigation_status": "INACTIVE"
    })
    (OUT / "validation").mkdir(exist_ok=False)
    evidence = read(F1 / "evidence.json")
    schema = read(PRE / "contracts/p05-navi-b-v02.schema.json")
    legacy_validator = pre.native_legacy_validator(PRE / "contracts")
    for index, row in enumerate(manifest["records"], start=1):
        print(json.dumps({"dispatch_slot": index,
                          "request_id": row["request_id"]}), flush=True)
        result = execute_one(row, cap, key, evidence, legacy_validator, schema)
        print(json.dumps({"slot": index, "state": result["state"],
                          "mechanical": result.get("mechanical_status"),
                          "usage": result["usage"]}, ensure_ascii=False), flush=True)
        if result["state"] not in ("CONTENT_VERIFIED", "CONTENT_VERIFIED_PROTOCOL_WARNING"):
            put(OUT / "STOP.json", {"slot": index, "state": result["state"],
                                   "remaining_not_sent": 48 - index,
                                   "no_retry": True, "fee": "UNKNOWN"})
            return
    put(OUT / "COMPLETE.json", {"requests_complete": 48,
                              "human_acceptance": "NOT_REVIEWED",
                              "navigation_status": "INACTIVE"})


if __name__ == "__main__":
    main()
