"""G3 tools-first finite probe. Preparation is offline; live needs a new exact approval."""
import argparse
from dataclasses import replace
import os
from pathlib import Path
import re
import time

from ..evidence_io import digest, read, require, seal, sha, strict_json, verify, write
from ..model_calls import locked
from ..rag.clean_store import Store, put
from .clean_chat import ChatRequest, Decoder, ENDPOINT, MODEL
from .clean_chat_transport import ChatSessions, VERSION, assistant_from_seal, code_identity, tool_reply
from . import clean_probe

PROBE_VERSION = "gc-clean-tools-probe-1"
LIMITS = {"campaign_requests": 5, "campaign_tokens": 46080, "turn_requests": 2, "turn_tokens": 18432}
VALUES = {"amber": 23, "tide": 41}
QUESTION = "请在同一轮分别用工具读取 amber 和 tide，各调用一次，再根据两个工具回执计算数值之和。不要猜测数值。最终只输出十进制整数。"
TEXT = "星海记录：流式校验完成。"


def requests():
    for name, stream, tools, question, expected in (
        ("N01-tools-off", False, True, QUESTION, "64"),
        ("N02-stream-text-off", True, False, "原样输出以下文字，不添加其他内容：" + TEXT, TEXT),
        ("N03-stream-tools-off", True, True, QUESTION, "64"),
    ):
        yield name, ChatRequest(
            [{"role": "system", "content": clean_probe.SYSTEM}, {"role": "user", "content": question}],
            clean_probe.TOOLS if tools else [], "disabled", stream, 1024, 8192, 32768, 120, PROBE_VERSION
        ), expected


def runner_identity():
    return {"runner": sha(__file__), "fixture_definitions": sha(clean_probe.__file__)}


def prepare(root):
    root = Path(root).resolve()
    require(root.parent == (Path.cwd() / "data/generation").resolve() and not root.exists(), "NEW_GENERATION_CHILD_REQUIRED")
    root.mkdir(parents=True)
    cases = [{"id": name, "initial_manifest": req.manifest(), "expected_final": expected,
              "max_requests": 2 if req.tools else 1} for name, req, expected in requests()]
    plan = {"probe_version": PROBE_VERSION, "version": VERSION, "endpoint": ENDPOINT, "model": MODEL,
            "campaign": "g3-tools-stream-20261114", "data_root": str(root), "limits": LIMITS,
            "adapter_code": code_identity(), "runner_code": runner_identity(), "cases": cases,
            "fixture_values": VALUES, "max_requests": 5, "max_attempts_per_operation": 1,
            "unknown_retries": 0, "format_retries": 0, "wall_clock_seconds": 1200,
            "fee_control": "requests_and_conservative_tokens_actual_fee_unknown", "actual_fee": "UNKNOWN",
            "live_enabled": False, "approval_status": "PENDING_OPERATOR_APPROVAL",
            "continuation_rule": "Only exact sealed assistant + both validated native tool calls' synthetic receipts; no new user/system messages; at most one continuation per tool case.",
            "stop_batch_on": ["UNKNOWN", "HTTP_or_protocol_failure", "usage_overrun", "identity_mismatch", "cancel", "deadline", "local_evidence_failure"],
            "case_only_failure": "Complete identity/usage-valid unexpected result stops this case; continue only the remaining predefined independent cases, never retry it.",
            "thinking_enabled": "NOT_TESTED_THIS_BATCH; prior NOT_QUALIFIED retained separately",
            "interruption_check": "Zero-network replay/corruption of COPIES plus offline transport fault fixtures; no intentional paid stream abort.",
            "scope": "synthetic_native_tools_and_SSE_only; no_corpus_or_research_quality_acceptance"}
    write(root / "preparation/plan.json", plan)
    for case in cases: write(root / "preparation/previews" / (case["id"] + ".json"), case["initial_manifest"])
    seal(root / "preparation")
    return {"root": str(root), "preparation_sha256": sha(root / "preparation/complete.json"),
            "max_requests": 5, "live_enabled": False}


def prepared(root):
    verify(root / "preparation")
    p = read(root / "preparation/plan.json")
    require(p["adapter_code"] == code_identity() and p["runner_code"] == runner_identity(), "PREPARATION_CODE_CHANGED")
    require(p["limits"] == LIMITS and p["fixture_values"] == VALUES and p["cases"] == [
        {"id": n, "initial_manifest": q.manifest(), "expected_final": e, "max_requests": 2 if q.tools else 1}
        for n, q, e in requests()], "PREPARATION_CASES_CHANGED")
    return p


def approve(root, preparation_sha256, authorization):
    """Called only after the operator approves this preparation; never sends or reads a key."""
    root = Path(root).resolve(); p = prepared(root)
    require(sha(root / "preparation/complete.json") == preparation_sha256 and bool(authorization.strip()), "EXACT_PREPARATION_APPROVAL_REQUIRED")
    permit = {k: p[k] for k in ("version", "endpoint", "model", "campaign", "data_root", "limits", "adapter_code", "unknown_retries", "fee_control")}
    permit.update(live_enabled=True, authorization=authorization, expires_at=time.time() + p["wall_clock_seconds"],
                  preparation_sha256=preparation_sha256, runner_code=p["runner_code"],
                  profiles=[c["initial_manifest"]["profile"] for c in p["cases"]],
                  request_limits=[c["initial_manifest"]["limits"] for c in p["cases"]],
                  case_messages=[c["initial_manifest"]["wire"]["messages"] for c in p["cases"]])
    put(root / "authorization.json", permit)
    return {"approved": True, "preparation_sha256": preparation_sha256, "expires_at": permit["expires_at"]}


def stream_replay_checks(manifest, raw):
    """Local-only: original bytes, bytewise fragmentation, incomplete and missing-usage copies."""
    reference = Decoder(manifest); reference.feed(raw); result = reference.finish()
    fragmented = Decoder(manifest)
    for byte in raw: fragmented.feed(bytes([byte]))
    require(fragmented.finish() == result, "FRAGMENTED_REPLAY_CHANGED")
    frames = re.split(br"\r\n\r\n|\n\n|\r\r", raw)
    parsed = []
    for frame in frames:
        lines = [line[5:] for line in frame.splitlines() if line.startswith(b"data:")]
        data = b"\n".join(line[1:] if line.startswith(b" ") else line for line in lines)
        obj = strict_json(data.decode()) if data and data != b"[DONE]" else {}
        parsed.append((frame, data, obj))
    without_usage = b"\n\n".join(frame for frame, _, obj in parsed if obj.get("usage") is None)
    without_done = b"\n\n".join(frame for frame, data, _ in parsed if data != b"[DONE]")
    first_data = next(frame for frame, data, _ in parsed if data and data != b"[DONE]")
    cut = raw[:raw.index(first_data) + max(1, len(first_data) // 2)]
    variants = {"midstream_cut": cut, "missing_done": without_done,
                "missing_usage": without_usage}
    failures = {}
    for name, body in variants.items():
        decoder = Decoder(manifest)
        try:
            decoder.feed(body); decoder.finish()
        except (ValueError, UnicodeError): failures[name] = "REJECTED_NO_FINAL_OR_EXECUTABLE_TOOLS"
        else: raise ValueError("INCOMPLETE_REPLAY_ACCEPTED")
    return {"scope": "LOCAL_COPIES_ONLY_NOT_LIVE_INTERRUPTION", "bytewise_equal": True, "rejections": failures}


def run(root, *, continue_probe=False, notify=lambda value: print(value, flush=True)):
    root = Path(root).resolve(); p = prepared(root)
    with locked(root / "locks/probe.lock"):
        if (root / "report/complete.json").exists():
            verify(root / "report"); return read(root / "report/result.json")
        permit = read(root / "authorization.json")
        require(permit["preparation_sha256"] == sha(root / "preparation/complete.json")
                and permit["runner_code"] == runner_identity(), "PERMIT_CHANGED")
        if (root / "started.json").exists(): require(continue_probe, "EXPLICIT_CONTINUE_REQUIRED")
        put(root / "started.json", {"permit_sha256": sha(root / "authorization.json")})
        store = Store(root); bridge = ChatSessions(store, p["campaign"], p["limits"], permit=permit)
        completed = []
        for name, initial, expected in requests():
            case_root = root / "case-results" / name
            if case_root.exists():
                verify(case_root); item = read(case_root / "result.json")
            else:
                tid = store.submit("g3-tools-probe", name, initial.messages[1]["content"], p["campaign"])
                item = {"case": name, "turn_id": tid, "status": "NOT_QUALIFIED", "reason": None,
                        "stop_batch": False, "operations": [], "outcomes": [], "tool_receipts": [],
                        "stream_replay_checks": [], "profile": initial.manifest()["profile"]}
                messages = initial.messages
                try:
                    with store.execute(tid):
                        for step in range(2 if initial.tools else 1):
                            req = replace(initial, messages=messages)
                            op = bridge.prepare_chat(tid, name + f"-{step}", req, mock=False)
                            item["operations"].append(op)
                            result = bridge.execute_chat(op, api_key=os.environ.get("TOKENMETRO_API_KEY"))
                            outcome = {"operation_id": op, **{k: result.get(k) for k in (
                                "kind", "state", "usage", "actual_fee", "finish_reason", "thinking_observation", "error_codes")}}
                            item["outcomes"].append(outcome); notify({"case": name, "step": step, **outcome})
                            require(result["state"] == "CONTENT_VERIFIED", "PROTOCOL_OR_TRANSPORT_FAILURE")
                            if initial.stream:
                                call_root = bridge.root / "calls" / op
                                verify(call_root)
                                raw = (call_root / "attempts/0001/response.body.raw").read_bytes()
                                item["stream_replay_checks"].append(stream_replay_checks(req.manifest(), raw))
                            if initial.tools and step == 0:
                                tool_calls = result["tool_calls"]
                                if not (result["kind"] == "tool_calls" and len(tool_calls) == 2
                                        and {c["arguments"]["handle"] for c in tool_calls} == set(VALUES)):
                                    item["reason"] = "EXPECTED_TWO_NATIVE_TOOL_CALLS_NOT_OBSERVED"; break
                                messages = initial.messages + [assistant_from_seal(bridge, op)]
                                for c in tool_calls:
                                    require(not store.cancelled(tid), "CANCELLED_BEFORE_TOOL")
                                    value = {"handle": c["arguments"]["handle"], "value": VALUES[c["arguments"]["handle"]],
                                             "authority": "SYNTHETIC_PROTOCOL_FIXTURE_NOT_CORPUS"}
                                    messages.append(tool_reply(root / "tool-receipts", c["id"], c["name"], c["arguments"], value, origin=op))
                                    item["tool_receipts"].append(digest([op, c["id"]]))
                            elif result["kind"] == "final_text" and result["answer_text"].strip() == expected:
                                item["status"] = "OBSERVED_LIMITED_PASS"
                            else: item["reason"] = "EXPECTED_SYNTHETIC_FINAL_NOT_OBSERVED"
                except Exception as exc:
                    # Never echo provider/credential-bearing exception text; raw evidence owns details.
                    item.update(status="STOPPED", reason=type(exc).__name__, stop_batch=True)
                item["evidence"] = []
                for op in item["operations"]:
                    call_root = bridge.root / "calls" / op
                    if (call_root / "complete.json").exists():
                        verify(call_root)
                        item["evidence"].append({"operation_id": op, "path": str(call_root.relative_to(root)),
                                                 "complete_sha256": sha(call_root / "complete.json")})
                write(case_root / "result.json", item); seal(case_root)
            store.event(item["turn_id"], "turn_complete", {"kind": "probe_case_complete", "status": item["status"]})
            completed.append(item)
            if item["stop_batch"]: break
        result = {"probe_version": PROBE_VERSION, "status": "STOPPED" if any(c["stop_batch"] for c in completed) else "COMPLETE",
                  "cases": completed, "accounting": bridge.batch.accounting(), "actual_fee": "UNKNOWN",
                  "thinking_tools": "NOT_TESTED; prior thinking NOT_QUALIFIED retained",
                  "active_research": "NOT_EVALUATED", "answer_quality": "NOT_EVALUATED",
                  "remaining_permission": "closed_at_report_no_automatic_reuse",
                  "preparation_sha256": sha(root / "preparation/complete.json")}
        write(root / "report/result.json", result); seal(root / "report")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "approve", "run"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--preparation-sha256")
    parser.add_argument("--authorization")
    parser.add_argument("--continue-probe", action="store_true")
    args = parser.parse_args()
    if args.mode == "prepare": result = prepare(args.root)
    elif args.mode == "approve":
        require(args.preparation_sha256 and args.authorization, "EXACT_APPROVAL_REQUIRED")
        result = approve(args.root, args.preparation_sha256, args.authorization)
    else: result = run(args.root, continue_probe=args.continue_probe)
    print({k: v for k, v in result.items() if k != "cases"}, flush=True)


if __name__ == "__main__": main()
