"""One isolated rc1 Responses JSON-mode probe, same transport as successful legacy.

No formal F2 slots, no automatic retries or overwrite.
"""
import argparse
import json
import time
from pathlib import Path
from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1, navi_f2_preflight as pre, navi_f2_sdk_transport as transport
from . import navi_v02_call_recovery as recorder
from . import navi_f2_legacy_json_mode_probe as prior
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_v02_message_verifier import inspect

BASE = Path("data/retrieval")
PRE = BASE / "p05-navi-f2-preflight-20261026-r1"
CASE = PRE / "requests" / (prior.PAIR + "-rc1.json")
SCHEMA = PRE / "contracts/p05-navi-b-v02.schema.json"
OUT = BASE / "p05-navi-f2-rc1-json-mode-20261028-r1"
CAP = prior.CAP


def build():
    case = read(CASE)
    if case["pair_id"] != prior.PAIR or case["arm"] != "rc1" or case["mode"] != "generation":
        raise ValueError("Wrong frozen rc1 case")
    if value_hash(case["messages"]) != case["messages_sha256"]:
        raise ValueError("Frozen case changed")
    successful_legacy = read(prior.OUT / "observation.json")
    if successful_legacy.get("state") != "CONTENT_VERIFIED":
        raise ValueError("Old Prompt has not proved viable with the new format")
    legacy_wire = read(prior.OUT / "legacy_json_mode/request.json")
    p, data = [m["content"] for m in case["messages"]]
    wire = {"model": c1.MODEL, "stream": True, "max_output_tokens": CAP,
            "input": [{"role": "user", "content": p + "\n\n" + data}],
            "text": {"format": {"type": "json_object"}}}
    if set(wire) != set(legacy_wire) or any(wire[k] != legacy_wire[k]
                                             for k in ("model", "stream", "max_output_tokens", "text")):
        raise ValueError("Different transport or generation parameters")
    if wire["input"][0]["content"] == legacy_wire["input"][0]["content"]:
        raise ValueError("Both prompt arms inadvertently identical")
    return case, wire


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--send-once", action="store_true")
    opt = args.parse_args()
    case, wire = build()
    if OUT.exists():
        raise RuntimeError("Attempt directory already exists; no repeat")
    print(json.dumps({"case": case["request_id"], "cap": CAP, "mode": "user_message+json_object",
                      "send": opt.send_once, "formal_slots": 0}, ensure_ascii=False), flush=True)
    if not opt.send_once:
        return
    env = environment()
    key = env.get("JIZHI_API_KEY")
    if not key or env.get("JIZHI_BASE_URL", "").rstrip("/") != c1.BASE_URL:
        raise ValueError("Provider credentials or route mismatch")
    pre.verify_preflight(PRE)
    OUT.mkdir(parents=True, exist_ok=False)
    put(OUT / "plan.json", {"attempts_max": 1, "retries": 0, "formal_slots": 0,
        "case_sha256": sha(CASE), "schema_sha256": sha(SCHEMA),
        "wire_fingerprint": value_hash(wire),
        "same_parameters_as_legacy_success": True, "time_epoch": time.time()})
    recorder.ROOT = OUT
    recorder.CASE = CASE
    def only_request(kind, stream=True):
        if kind != "user_fallback" or stream is not True:
            raise ValueError("Unexpected diagnostic route")
        return wire
    recorder.request = only_request
    print("DISPATCH: one rc1 user-message JSON-mode diagnostic", flush=True)
    result = recorder.run_one("rc1_json_mode", "user_fallback", True, key)
    folder = OUT / "rc1_json_mode"
    obs = {"state": result.get("state"), "http_status": result.get("http_status"),
           "usage": result.get("usage", "UNKNOWN"), "actual_fee": "UNKNOWN",
           "mechanical_status": "NOT_CHECKED", "semantic_quality": "NOT_VALIDATED",
           "human_acceptance": "NOT_REVIEWED", "navigation_status": "INACTIVE",
           "formal_f2_slots": 0, "retry": False}
    if result.get("state") == "COMPLETED":
        try:
            enc = read(folder / "http.json")["headers"].get("content-encoding", "")
            raw = c1.sse_events(transport.decoded_body(
                (folder / "response.body.raw").read_bytes(), enc))
            sdk = [json.loads(line) for line in
                   (folder / "sdk-events.jsonl").read_text(encoding="utf8").splitlines()]
            check = inspect(raw, sdk)
            packet = read(CASE)
            verdict = pre.validate_content(check["message_text"], packet, None,
                                           None, read(SCHEMA), None)
            ok = verdict["status"] == "VALIDATED_MECHANICAL_ONLY"
            obs.update({"state": "CONTENT_VERIFIED_PROTOCOL_WARNING"
                        if ok and check["reasoning_id_conflicts"] else
                        "CONTENT_VERIFIED" if ok else "CONTENT_INVALID",
                        "validation_status": verdict["status"], "mechanical_status":
                        "PASS" if ok else "FAIL", "full_stream": check["full_stream"],
                        "reasoning_id_conflicts": len(check["reasoning_id_conflicts"]),
                        "usage": check["usage"], "near_output_limit":
                        check["usage"]["output_tokens"] * 100 >= CAP * 95})
        except Exception as exc:
            obs.update({"state": "LOCAL_VALIDATION_FAILED", "error_type": type(exc).__name__})
    put(OUT / "observation.json", obs)
    put(OUT / "STOP.json", {"single_attempt_done": True, "no_retry": True,
                            "state": obs["state"], "formal_f2_slots": 0})
    print("OBSERVATION", json.dumps({k: obs.get(k) for k in
        ("state", "http_status", "mechanical_status", "validation_status", "full_stream",
         "reasoning_id_conflicts", "usage", "near_output_limit")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
