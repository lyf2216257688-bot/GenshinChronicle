"""Run ONE independently archived Navi v0.2 user-input Responses smoke call.

This is diagnostic-only. It cannot advance any formal F2 slot or automatically retry.
"""
import argparse
import json
import time
from pathlib import Path

from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1
from . import navi_f2_preflight as pre
from . import navi_f2_sdk_transport as transport
from . import navi_v02_call_recovery as historical
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_f2_content_recovery import render_user_wire, assess_content

PAIR = "6c2d16a80dac01dc3f8c"
CASE = Path("data/retrieval/p05-navi-f2-preflight-20261026-r1/requests") / (PAIR + "-rc1.json")
SCHEMA = Path("data/retrieval/p05-navi-f2-preflight-20261026-r1/contracts/p05-navi-b-v02.schema.json")
PRIOR = Path("data/retrieval/p05-navi-v02-call-recovery-20261028-r1/user_fallback")
OUT = Path("data/retrieval/p05-navi-v02-user-smoke-20261028-r1")
CAP = 16000


def prepare():
    case = read(CASE)
    assert case["pair_id"] == PAIR and case["arm"] == "rc1" and case["mode"] == "generation"
    assert case["pair_id"] != "8978e15f60f91e0e1db1"
    prior_wire = read(PRIOR / "request.json")
    assert prior_wire["input"] and prior_wire["stream"] is True
    assert read(PRIOR / "independent-message-check.json")["message"] == "PASS"
    schema = read(SCHEMA)
    wire = render_user_wire(case, max_output_tokens=CAP)
    assert wire["model"] == c1.MODEL
    assert "instructions" not in wire and "text" not in wire
    assert wire["input"] != prior_wire["input"]
    return case, schema, wire


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-once", action="store_true", help="send the sole authorized diagnostic request")
    args = parser.parse_args()
    case, schema, wire = prepare()
    if OUT.exists():
        raise RuntimeError("Already reserved: no replay or overwrite")
    print(json.dumps({
        "pair_id": PAIR, "model": wire["model"], "stream": wire["stream"],
        "input_characters": len(wire["input"]), "cap": CAP,
        "formal_f2_slots": 0, "retry": False,
        "live_send": bool(args.send_once)
    }, ensure_ascii=False), flush=True)
    if not args.send_once:
        return
    env = environment()
    key = env.get("JIZHI_API_KEY")
    if not key or env.get("JIZHI_BASE_URL", "").rstrip("/") != c1.BASE_URL:
        raise RuntimeError("Unexpected provider or missing credential")
    OUT.mkdir(parents=True, exist_ok=False)
    put(OUT / "plan.json", {
        "mode": "single_live_diagnostic", "case_path": str(CASE),
        "case_sha256": sha(CASE), "schema_sha256": sha(SCHEMA),
        "wire_fingerprint": value_hash(wire), "max_calls": 1,
        "max_output_tokens": CAP, "formal_f2_slots": 0,
        "no_retry": True, "started_epoch": time.time()
    })
    # Reuse the already audited raw-tee/SDK/SSE recorder, changing the
    # diagnostic case and isolated output root only in THIS process.
    historical.CASE = CASE
    historical.ROOT = OUT
    original_request = historical.request

    def isolated_request(kind, stream=True):
        w = original_request(kind, stream)
        w["max_output_tokens"] = CAP
        if kind != "user_fallback" or w != wire:
            raise ValueError("Unexpected wire shape")
        return w
    historical.request = isolated_request

    print("DISPATCH: single independent Navi diagnostic", flush=True)
    result = historical.run_one("user_input_navi", "user_fallback", True, key)
    folder = OUT / "user_input_navi"
    observation = {"state": result["state"], "http_status": result.get("http_status"),
                   "usage": result.get("usage", "UNKNOWN"),
                   "archived_result": str(folder / "result.json"),
                   "formal_f2_slots": 0}
    if result["state"] == "COMPLETED":
        try:
            encoding = read(folder / "http.json")["headers"].get("content-encoding", "")
            raw = transport.decoded_body((folder / "response.body.raw").read_bytes(), encoding)
            events = c1.sse_events(raw)
            sdk = [json.loads(line) for line in
                   (folder / "sdk-events.jsonl").read_text(encoding="utf-8").splitlines()]
            assessed = assess_content(events, sdk, case,
                                      request_wire=read(folder / "request.json"),
                                      schema=schema)
            observation.update(assessed)
            observation["validation"] = {
                "status": assessed["validation"]["status"],
                "reasoning_conflicts": len(assessed["reasoning_id_conflicts"])
            }
            # The complete validated JSON is already in the immutable raw stream.
            # Do not duplicate potentially private evidence into a second summary.
        except Exception as e:
            observation.update(state="LOCAL_VALIDATION_FAILED",
                               exception_type=type(e).__name__)
    put(OUT / "observation.json", observation)
    put(OUT / "STOP.json", {"reason": "ONE_DIAGNOSTIC_COMPLETED_OR_STOPPED",
                            "calls": result.get("calls"), "result_state": observation["state"],
                            "no_retry": True, "formal_f2_slots": 0})
    print("OBSERVATION", json.dumps({
        k: observation.get(k) for k in ("state", "http_status", "mechanical_status",
                                        "full_stream_status", "protocol_warning", "usage")
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
