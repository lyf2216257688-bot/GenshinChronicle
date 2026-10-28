"""One isolated F2 legacy-arm call using the proven Responses user-input wrapper.

Diagnostic only: does NOT resume the historical F2 or authorize bulk calls.
"""
import argparse
import json
import time
from pathlib import Path

from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1, navi_f2_preflight as pre, navi_f2_sdk_transport as transport
from . import navi_v02_call_recovery as diagnostic
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_f2_content_recovery import render_user_wire, assess_content

PAIR = "6c2d16a80dac01dc3f8c"
BASE = Path("data/retrieval")
PRE = BASE / "p05-navi-f2-preflight-20261026-r1"
F1 = BASE / "p05-navi-f1-20261026-r2"
CASE = PRE / "requests" / (PAIR + "-legacy.json")
RC1_CASE = PRE / "requests" / (PAIR + "-rc1.json")
PRIOR = BASE / "p05-navi-v02-user-smoke-20261028-r1"
OUT = BASE / "p05-navi-f2-legacy-user-smoke-20261028-r1"
CAP = 30000


def prepare():
    case = read(CASE)
    rc1 = read(RC1_CASE)
    assert case["pair_id"] == PAIR and case["mode"] == "generation" and case["arm"] == "legacy"
    assert rc1["pair_id"] == PAIR and rc1["arm"] == "rc1"
    assert case["source_payload_sha256"] == rc1["source_payload_sha256"]
    previous = read(PRIOR / "observation.json")
    assert previous["state"] == "CONTENT_VERIFIED" and previous["full_stream_status"] == "PASS"
    wire = render_user_wire(case, max_output_tokens=CAP)
    other = render_user_wire(rc1, max_output_tokens=CAP)
    assert set(wire) == set(other)
    assert all(wire[k] == other[k] for k in ("model", "stream", "max_output_tokens"))
    assert wire["input"] != other["input"]
    return case, wire


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-once", action="store_true")
    args = parser.parse_args()
    case, wire = prepare()
    if OUT.exists():
        raise RuntimeError("Already reserved; never replay a previous request")
    print(json.dumps({"pair_id": PAIR, "arm": case["arm"],
        "stream": True, "input_characters": len(wire["input"]),
        "cap": CAP, "diagnostic_only": True, "send": args.send_once}, ensure_ascii=False), flush=True)
    if not args.send_once:
        return
    env = environment()
    key = env.get("JIZHI_API_KEY")
    if not key or env.get("JIZHI_BASE_URL", "").rstrip("/") != c1.BASE_URL:
        raise RuntimeError("Unknown provider or missing credential")
    pre.verify_preflight(PRE)
    OUT.mkdir(parents=True, exist_ok=False)
    put(OUT / "plan.json", {
        "formal_slots": 0, "max_calls": 1, "retry": False,
        "case_sha256": sha(CASE), "rc1_reference_sha256": sha(RC1_CASE),
        "f1_pair": PAIR, "max_output_tokens": CAP,
        "request_fingerprint": value_hash(wire), "time_epoch": time.time(),
        "reason": "Prove old Prompt can follow the exact user-input transport shape used by rc1"
    })
    # Only this diagnostic process changes the in-memory isolated recorder.
    diagnostic.CASE = CASE
    diagnostic.ROOT = OUT
    original_request = diagnostic.request

    def independent_request(kind, stream=True):
        req = original_request(kind, stream)
        req["max_output_tokens"] = CAP
        if kind != "user_fallback" or req != wire:
            raise ValueError("Legacy wire unexpectedly changed")
        return req
    diagnostic.request = independent_request
    print("DISPATCH: one legacy user-input Responses diagnostic", flush=True)
    result = diagnostic.run_one("legacy_user_input", "user_fallback", True, key)
    folder = OUT / "legacy_user_input"
    observation = {"execution_state": result["state"], "http_status": result.get("http_status"),
                   "usage": result.get("usage", "UNKNOWN"), "formal_slots": 0,
                   "auto_retry": False, "mechanical_status": "NOT_CHECKED",
                   "semantic_quality": "NOT_VALIDATED", "human_acceptance": "NOT_REVIEWED"}
    if result["state"] == "COMPLETED":
        try:
            encoding = read(folder / "http.json")["headers"].get("content-encoding", "")
            events = c1.sse_events(transport.decoded_body((folder / "response.body.raw").read_bytes(), encoding))
            sdk = [json.loads(line) for line in
                   (folder / "sdk-events.jsonl").read_text(encoding="utf-8").splitlines()]
            packet = read(F1 / "deliveries" / (PAIR + ".json"))
            evidence = read(F1 / "evidence.json")
            legacy_validator = pre.native_legacy_validator(PRE / "contracts")
            assessed = assess_content(events, sdk, case, request_wire=read(folder / "request.json"),
                                      packet=packet, evidence=evidence,
                                      legacy_validator=legacy_validator)
            observation.update({
                "state": assessed["state"],
                "mechanical_status": assessed["mechanical_status"],
                "full_stream_status": assessed["full_stream_status"],
                "protocol_warning": assessed["protocol_warning"],
                "reasoning_id_conflict_count": len(assessed["reasoning_id_conflicts"]),
                "output_token_headroom": assessed["output_token_headroom"],
                "near_output_limit": assessed["near_output_limit"],
                "message_sha256": assessed["message_sha256"],
                "validation_status": assessed["validation"]["status"]
            })
            # Legacy model output remains in the immutable SDK/raw event archive.
            # Do not label a content PASS as human acceptance or provider protocol PASS.
        except Exception as exc:
            observation.update(state="LOCAL_VALIDATION_FAILED", exception_type=type(exc).__name__)
    else:
        observation["state"] = result["state"]
    put(OUT / "observation.json", observation)
    put(OUT / "STOP.json", {"reason": "ONE_DIAGNOSTIC_COMPLETED_OR_STOPPED",
                            "calls": result.get("calls"), "state": observation["state"],
                            "formal_slots": 0, "no_retry": True})
    print("OBSERVATION", json.dumps({
        k: observation.get(k) for k in ("state", "http_status", "mechanical_status",
                                         "validation_status", "full_stream_status",
                                         "protocol_warning", "usage", "near_output_limit")
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
