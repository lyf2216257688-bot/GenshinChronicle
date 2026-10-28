"""One isolated old-Prompt Responses JSON-mode probe; no formal F2 work."""
import argparse
import json
import time
from pathlib import Path
from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1, navi_f2_preflight as pre, navi_f2_sdk_transport as transport
from . import navi_v02_call_recovery as recorder
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_v02_message_verifier import inspect

BASE = Path("data/retrieval")
PRE = BASE / "p05-navi-f2-preflight-20261026-r1"
F1 = BASE / "p05-navi-f1-20261026-r2"
PAIR = "6c2d16a80dac01dc3f8c"
CASE = PRE / "requests" / (PAIR + "-legacy.json")
OUT = BASE / "p05-navi-f2-legacy-json-mode-20261028-r1"
CAP = 16000


def build():
    case = read(CASE)
    if case["pair_id"] != PAIR or case["arm"] != "legacy" or case["mode"] != "generation":
        raise ValueError("Wrong frozen case")
    if value_hash(case["messages"]) != case["messages_sha256"]:
        raise ValueError("Frozen case messages changed")
    prompt, data = [m["content"] for m in case["messages"]]
    wire = {
        "model": c1.MODEL, "stream": True, "max_output_tokens": CAP,
        "input": [{"role": "user", "content": prompt + "\n\n" + data}],
        "text": {"format": {"type": "json_object"}}
    }
    if "instructions" in wire:
        raise ValueError("Unexpected top-level instructions")
    return case, wire


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-once", action="store_true")
    args = parser.parse_args()
    case, wire = build()
    if OUT.exists():
        raise RuntimeError("Diagnostic already reserved; no replay/retry")
    print(json.dumps({"case": case["request_id"], "stream": wire["stream"],
        "message_chars": len(wire["input"][0]["content"]), "max_output_tokens": CAP,
        "text_format": wire["text"], "sent": args.send_once,
        "formal_f2_slots": 0}, ensure_ascii=False), flush=True)
    if not args.send_once:
        return
    env = environment()
    key = env.get("JIZHI_API_KEY")
    if not key or env.get("JIZHI_BASE_URL", "").rstrip("/") != c1.BASE_URL:
        raise ValueError("Unexpected route or missing key")
    pre.verify_preflight(PRE)
    OUT.mkdir(parents=True, exist_ok=False)
    put(OUT / "plan.json", {"one_request_max": 1, "auto_retry": False,
        "formal_f2_slots": 0, "case_sha256": sha(CASE),
        "wire_sha256": value_hash(wire), "cap": CAP,
        "changes_vs_r3": ["input becomes explicit user item", "text.format=json_object"],
        "legacy_prompt_sha256": case["prompt_sha256"], "epoch": time.time()})
    recorder.ROOT = OUT
    recorder.CASE = CASE
    def only_request(kind, stream=True):
        if kind != "user_fallback" or stream is not True:
            raise ValueError("Unexpected diagnostics dispatch")
        return wire
    recorder.request = only_request
    print("DISPATCH: one standalone Responses JSON-format probe", flush=True)
    result = recorder.run_one("legacy_json_mode", "user_fallback", True, key)
    folder = OUT / "legacy_json_mode"
    obs = {"state": result.get("state"), "http_status": result.get("http_status"),
           "usage": result.get("usage", "UNKNOWN"), "actual_fee": "UNKNOWN",
           "formal_f2_slots": 0, "retry": False,
           "mechanical_status": "NOT_CHECKED", "semantic_quality": "NOT_VALIDATED",
           "human_acceptance": "NOT_REVIEWED", "navigation_status": "INACTIVE"}
    if result.get("state") == "COMPLETED":
        try:
            enc = read(folder / "http.json")["headers"].get("content-encoding", "")
            raw = c1.sse_events(transport.decoded_body(
                (folder / "response.body.raw").read_bytes(), enc))
            sdk = [json.loads(line) for line in
                   (folder / "sdk-events.jsonl").read_text(encoding="utf8").splitlines()]
            check = inspect(raw, sdk)
            verdict = pre.validate_content(
                check["message_text"], case,
                read(F1 / "deliveries" / (PAIR + ".json")),
                read(F1 / "evidence.json"),
                None, pre.native_legacy_validator(PRE / "contracts"))
            obs.update({"state": "CONTENT_VERIFIED_PROTOCOL_WARNING"
                        if verdict["status"] == "VALIDATED_MECHANICAL_ONLY"
                        and check["reasoning_id_conflicts"] else
                        "CONTENT_VERIFIED" if verdict["status"] == "VALIDATED_MECHANICAL_ONLY"
                        else "CONTENT_INVALID",
                        "validation_status": verdict["status"],
                        "mechanical_status": "PASS" if
                        verdict["status"] == "VALIDATED_MECHANICAL_ONLY" else "FAIL",
                        "full_stream": check["full_stream"],
                        "reasoning_id_conflicts": len(check["reasoning_id_conflicts"]),
                        "usage": check["usage"]})
        except Exception as ex:
            obs.update({"state": "LOCAL_VALIDATION_FAILED",
                        "error_type": type(ex).__name__})
    if result.get("state") == "UNKNOWN" and result.get("http_status") == 200:
        obs["diagnosis"] = "HTTP200_NO_VERIFIED_CONTENT_OR_USAGE"
    put(OUT / "observation.json", obs)
    put(OUT / "STOP.json", {"one_attempt_finished": True,
        "no_retry": True, "formal_f2_slots": 0, "state": obs["state"]})
    print("OBSERVATION", json.dumps({k: obs.get(k) for k in
        ("state", "http_status", "mechanical_status", "validation_status",
         "full_stream", "reasoning_id_conflicts", "usage")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
