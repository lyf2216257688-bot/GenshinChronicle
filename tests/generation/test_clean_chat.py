"""Offline Chat protocol/transport fixtures; no API key, model or corpus."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from genshin_corpus import model_calls as calls
from genshin_corpus.evidence_io import read, sha, verify
from genshin_corpus.generation.clean_chat import ChatRequest, Decoder, MODEL, schema_check
from genshin_corpus.generation.clean_chat_transport import (
    ChatSessions, VERSION, assistant_from_seal, code_identity, tool_reply,
)
from genshin_corpus.generation import clean_probe
from genshin_corpus.rag.clean_store import Store

TOOLS = [{"type": "function", "function": {"name": "read_probe_evidence", "description": "Read fixture evidence.",
    "parameters": {"type": "object", "properties": {"handle": {"type": "string", "enum": ["amber", "tide"]}},
                   "required": ["handle"], "additionalProperties": False}}}]
LIMITS = {"campaign_requests": 8, "campaign_tokens": 80000, "turn_requests": 2, "turn_tokens": 20000}


def request(*, stream=False, thinking="disabled", messages=None, tools=True, output=100):
    return ChatRequest(messages or [{"role": "system", "content": "Fixture only."}, {"role": "user", "content": "Read amber."}],
                       deepcopy(TOOLS) if tools else [], thinking, stream, output, 4096, 8192, 10, "fixture-1")


def call(cid="c1", name="read_probe_evidence", arguments='{"handle":"amber"}'):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": arguments}}


def response(message=None, reason="stop", **updates):
    value = {"id": "fixture-chat", "model": MODEL, "object": "chat.completion",
             "choices": [{"index": 0, "finish_reason": reason,
                          "message": message or {"role": "assistant", "content": "夹具答案。"}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    value.update(updates); return value


def chunk(delta, finish=None, **updates):
    value = {"id": "fixture-chat", "model": MODEL, "object": "chat.completion.chunk",
             "choices": [{"index": 0, "finish_reason": finish, "delta": delta}]}
    value.update(updates); return value


def sse(values, *, done=True):
    return b": keepalive\r\n\r\n" + b"".join(b"data: " + json.dumps(v, ensure_ascii=False).encode() + b"\r\n\r\n" for v in values) + (b"data: [DONE]\r\n\r\n" if done else b"")


def stream_values(*, tools=False):
    values = [chunk({"role": "assistant", "content": ""})]
    if tools:
        values += [chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                    "function": {"name": "read_probe_", "arguments": '{"handle":'}},
                   {"index": 1, "id": "c2", "type": "function", "function": {"name": "read_probe_evidence", "arguments": '{"handle":"tide"}'}}]}),
                   chunk({"tool_calls": [{"index": 0, "function": {"name": "evidence", "arguments": '"amber"}'}}]}),
                   chunk({}, "tool_calls")]
    else: values += [chunk({"content": "原文中文。"}), chunk({}, "stop")]
    values += [chunk({}, choices=[], usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})]
    return values


class Response(BytesIO):
    status = 200
    def __init__(self, body, *, stream=False, pieces=None):
        super().__init__(body); self.headers = {"content-type": "text/event-stream" if stream else "application/json"}
        self.pieces = iter(pieces) if pieces is not None else None
    def read1(self, size):
        if self.pieces is None: return self.read(size)
        item = next(self.pieces, b"")
        if isinstance(item, BaseException): raise item
        return item


class Opener:
    def __init__(self, body=None, *, stream=False, error=None, pieces=None):
        self.body = body or json.dumps(response(), ensure_ascii=False).encode()
        self.stream, self.error, self.pieces, self.calls = stream, error, pieces, []
    def open(self, req, *, timeout):
        self.calls.append((req, timeout))
        if self.error: raise self.error
        return Response(self.body, stream=self.stream, pieces=self.pieces)


class ChatTests(unittest.TestCase):
    def test_rejected_arguments_require_exact_error_receipt_and_preserve_thinking(self):
        req=replace(request(thinking="enabled"),argument_error_feedback=True)
        assistant={"role":"assistant","content":"","reasoning_content":"exact private reasoning",
                   "tool_calls":[call(arguments='{"handle":17,"extra":true}')]}
        parsed=Decoder(req.manifest()).finish(json.dumps(response(assistant,"tool_calls")).encode())
        self.assertEqual(parsed["kind"],"tool_argument_errors")
        error=parsed["tool_calls"][0]["validation_error"]
        self.assertEqual(error["issues"][0]["extra"],["extra"])
        self.assertEqual(error["issues"][1]["path"],"$.handle")
        reply={"role":"tool","tool_call_id":"c1","content":json.dumps({
            "status":"invalid_tool_arguments","executed":False,"validation_error":error})}
        history=req.messages+[parsed["assistant_message"],reply]
        self.assertEqual(replace(req,messages=history).manifest()["wire"]["messages"],history)
        for content in ('{}',json.dumps({"status":"invalid_tool_arguments","executed":True,"validation_error":error})):
            with self.assertRaisesRegex(ValueError,"INVALID_ARGUMENT_ERROR_RECEIPT"):
                replace(req,messages=history[:-1]+[{**reply,"content":content}]).manifest()
        with self.assertRaises(ValueError):replace(req,messages=history,argument_error_feedback=False).manifest()

    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / "tmp-g3"
        scratch.mkdir(exist_ok=True)
        self.tmp = TemporaryDirectory(dir=scratch); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.store = Store(self.root)
        self.tid = self.store.submit("s1", "submit", "Fixture", "probe")
        self.bridge = ChatSessions(self.store, "probe", LIMITS)
        network = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        blocked = network.start(); self.addCleanup(network.stop); self.addCleanup(blocked.assert_not_called)

    def execute(self, req=None, opener=None, key="op"):
        op = self.bridge.prepare_chat(self.tid, key, req or request())
        return op, self.bridge.execute_chat(op, opener=opener or Opener())

    def test_manifest_binds_profile_full_messages_and_no_implicit_defaults(self):
        a = request().manifest(); b = request(thinking="enabled", stream=True).manifest()
        self.assertEqual(a["profile"]["status"], "NOT_LIVE_VALIDATED")
        self.assertNotEqual(a["profile"], b["profile"])
        self.assertEqual(b["wire"]["stream_options"], {"include_usage": True})
        self.assertNotIn("strict", a["wire"]["tools"][0]["function"])
        self.assertNotIn("temperature", b["wire"])
        for field in ("input_bytes", "output_tokens", "context_tokens", "timeout_seconds"):
            values = request().__dict__ | {field: None}
            with self.assertRaises(ValueError): ChatRequest(**values).manifest()
        with self.assertRaises(ValueError): ChatRequest(**(request().__dict__ | {"input_bytes": 1})).manifest()

    def test_nonstream_raw_first_shared_replay_accounting_and_secret_exclusion(self):
        opener = Opener(); op, result = self.execute(opener=opener)
        self.assertEqual(result["kind"], "final_text"); self.assertEqual(result["semantic_acceptance"], "NOT_REVIEWED")
        root = self.bridge.root / "calls" / op
        verify(root)
        self.assertEqual((root / "response.body.raw").read_bytes(), opener.body)
        self.assertEqual((root / "request.body.bin").read_bytes(), opener.calls[0][0].data)
        self.assertEqual(self.bridge.execute_chat(op, opener=Opener(error=AssertionError())), result)
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 1)
        self.assertEqual(self.bridge.batch.accounting()["known_tokens"], 30)
        for p in self.root.rglob("*"):
            if p.is_file() and p.suffix != ".sqlite": self.assertNotIn(b"fixture-secret", p.read_bytes())

    def test_sealed_historical_adapter_replays_after_route_code_change_without_send(self):
        with patch("genshin_corpus.generation.clean_chat_transport.code_identity", return_value={"historical": "a"*64}):
            op, prior = self.execute()
        sender=Opener(error=AssertionError("No historical resend"))
        replay=self.bridge.execute_chat(op,opener=sender)
        self.assertEqual(replay,prior)
        self.assertEqual(sender.calls,[])
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"],1)

    def test_sse_every_byte_split_multitool_and_unicode(self):
        for tools in (False, True):
            raw = sse(stream_values(tools=tools)); d = Decoder(request(stream=True).manifest())
            for b in raw: d.feed(bytes([b]))
            result = d.finish()
            self.assertEqual(result["kind"], "tool_calls" if tools else "final_text")
            if tools:
                self.assertEqual([c["arguments"]["handle"] for c in result["tool_calls"]], ["amber", "tide"])
            else: self.assertEqual(result["answer_text"], "原文中文。")

    def test_stream_drafts_persist_after_raw_fsync_and_no_reasoning_exposure(self):
        values = stream_values(); values.insert(1, chunk({"reasoning_content": "private fixture reasoning"}))
        raw = sse(values); opener = Opener(raw, stream=True, pieces=[raw[i:i+3] for i in range(0, len(raw), 3)])
        original = self.store.event
        def event(tid, key, payload):
            if payload["kind"] == "answer_draft":
                saved = next((self.bridge.root / "calls").glob("*/attempts/0001/response.body.raw"))
                self.assertIn(payload["text"].encode(), saved.read_bytes())
            return original(tid, key, payload)
        with patch.object(self.store, "event", side_effect=event):
            op, result = self.execute(request(stream=True, thinking="enabled"), opener)
        self.assertEqual(result["kind"], "final_text")
        events = self.store.events(self.tid)
        self.assertTrue(any(e["kind"] == "answer_draft" and e["final"] is False for e in events))
        self.assertNotIn("private fixture reasoning", str(events) + str(result))
        self.assertIn("private fixture reasoning", str(read(self.bridge.root / "calls" / op / "decoded.json")))

    def test_tool_arguments_validate_all_before_any_execution(self):
        variants = [call(arguments='{"handle":"wrong"}'), call(arguments='{"handle":"amber","path":"secret"}'),
                    call(name="shell"), call(arguments='{"handle":"amber","handle":"tide"}'), call(arguments='{"handle":')]
        for variant in variants:
            d = Decoder(request().manifest())
            with self.assertRaises((ValueError, TypeError)):
                d.finish(json.dumps(response({"role": "assistant", "content": None, "tool_calls": [call(), variant]}, "tool_calls")).encode())

    def test_reasoning_tool_history_and_receipt_recovery_preserve_exact_message(self):
        msg = {"role": "assistant", "content": None, "reasoning_content": "opaque reasoning",
               "tool_calls": [call(), call("c2", arguments='{"handle":"tide"}')]}
        op, result = self.execute(request(thinking="enabled"), Opener(json.dumps(response(msg, "tool_calls")).encode()))
        assistant = assistant_from_seal(self.bridge, op); self.assertEqual(assistant, msg)
        replies = [tool_reply(self.root / "tools", c["id"], c["name"], c["arguments"], {"value": i}, origin=op)
                   for i, c in enumerate(result["tool_calls"])]
        before = {str(p): sha(p) for p in (self.root / "tools").rglob("*.json")}
        again = [tool_reply(self.root / "tools", c["id"], c["name"], c["arguments"], {"value": i}, origin=op)
                 for i, c in enumerate(result["tool_calls"])]
        self.assertEqual(replies, again); self.assertEqual(before, {str(p): sha(p) for p in (self.root / "tools").rglob("*.json")})
        messages = request().messages + [assistant] + replies
        self.assertEqual(request(messages=messages, thinking="enabled").manifest()["wire"]["messages"], messages)
        for broken in (messages[:-1], messages + [replies[0]], request().messages + replies):
            with self.assertRaises(ValueError): request(messages=broken).manifest()
        with self.assertRaises(ValueError):
            tool_reply(self.root / "tools", "c1", "read_probe_evidence", {}, {"value": 99}, origin=op)

    def test_identity_usage_overcap_thinking_and_unsupported_finish_fail(self):
        variants = [response(model="alias"), response(usage=None), response(usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 31}),
                    response(usage={"prompt_tokens": 10, "completion_tokens": 101, "total_tokens": 111}),
                    response({"role": "assistant", "content": "text", "reasoning_content": "unexpected"}), response(reason="unexpected")]
        for i, value in enumerate(variants):
            with self.subTest(i=i):
                # Independent roots keep test budgets from masking protocol failures.
                store = Store(self.root / str(i)); tid = store.submit("s", "t", "q", "p")
                bridge = ChatSessions(store, "p", LIMITS); op = bridge.prepare_chat(tid, "op", request())
                result = bridge.execute_chat(op, opener=Opener(json.dumps(value).encode()))
                self.assertNotEqual(result["state"], "CONTENT_VERIFIED")
                self.assertIsNone(result["answer_text"])

    def test_refusal_and_length_are_distinct_and_never_executable_tools(self):
        for reason, message, kind in (("stop", {"role": "assistant", "content": None, "refusal": "拒绝"}, "refusal"),
                                      ("length", {"role": "assistant", "content": "half", "tool_calls": [call(arguments="{")]}, "incomplete")):
            d = Decoder(request().manifest()); result = d.finish(json.dumps(response(message, reason)).encode())
            self.assertEqual(result["kind"], kind); self.assertEqual(result["tool_calls"], []); self.assertIsNone(result["answer_text"])

    def test_dropped_stream_no_done_or_finish_never_yields_tools_or_final(self):
        for i, raw in enumerate((sse(stream_values(tools=True), done=False), sse(stream_values(tools=True)[:2]))):
            op, result = self.execute(request(stream=True), Opener(raw, stream=True), key=str(i))
            self.assertNotEqual(result["state"], "CONTENT_VERIFIED")
            self.assertEqual(result["attempt_classifications"][0]["execution"], "UNKNOWN")
            self.assertFalse(result.get("tool_calls")); self.assertIsNone(result["answer_text"])
            self.bridge.execute_chat(op, opener=Opener(error=AssertionError("must not resend")))

    def test_duplicate_ids_indices_changed_identity_and_data_after_done_rejected(self):
        variants = []
        v = stream_values(tools=True); v[1]["choices"][0]["delta"]["tool_calls"][1]["id"] = "c1"; variants.append(sse(v))
        v = stream_values(); v[1]["id"] = "changed"; variants.append(sse(v))
        v = stream_values(tools=True); v[1]["choices"][0]["delta"]["tool_calls"][1]["index"] = 2; variants.append(sse(v))
        variants.append(sse(stream_values()) + b'data: {}\n\n')
        for raw in variants:
            d = Decoder(request(stream=True).manifest())
            with self.assertRaises(ValueError): d.feed(raw); d.finish()

    def test_http_429_524_timeout_and_partial_archive_once(self):
        errors = [HTTPError("https://tokenmetro.com/v1/chat/completions", n, "fixture", {}, BytesIO(b'{"error":"fixture"}')) for n in (429, 524)]
        errors += [URLError("fixture-secret"), TimeoutError("fixture-secret")]
        for i, error in enumerate(errors):
            store = Store(self.root / str(i)); tid = store.submit("s", "t", "q", "p")
            bridge = ChatSessions(store, "p", LIMITS); op = bridge.prepare_chat(tid, "op", request())
            opener = Opener(error=error); result = bridge.execute_chat(op, opener=opener)
            self.assertEqual(result["accounting"]["unknown_usage_attempts"], [1])
            bridge.execute_chat(op, opener=Opener(error=AssertionError())); self.assertEqual(len(opener.calls), 1)
        raw = sse(stream_values(tools=True))[:120]
        op, result = self.execute(request(stream=True), Opener(stream=True, pieces=[raw, ConnectionResetError("secret")]))
        self.assertEqual((self.bridge.root / "calls" / op / "response.body.raw").read_bytes(), raw)
        self.assertEqual(result["state"], "UNKNOWN")

    def test_crash_after_first_bytes_unknown_and_sealed_call_reconcile_zero_send(self):
        class Crash(BaseException): pass
        op = self.bridge.prepare_chat(self.tid, "crash", request(stream=True))
        with self.assertRaises(Crash):
            self.bridge.execute_chat(op, opener=Opener(stream=True, pieces=[b'data: {', Crash()]))
        self.assertEqual(self.bridge.reconcile(op)["status"], "UNKNOWN")
        self.assertEqual(self.bridge.execute_chat(op, opener=Opener(error=AssertionError()))["state"], "UNKNOWN")

    def test_cancel_before_admission_and_midstream_keeps_budget(self):
        op = self.bridge.prepare_chat(self.tid, "cancel", request())
        self.store.cancel(self.tid)
        with self.assertRaises(calls.Deferred): self.bridge.execute_chat(op, opener=Opener())
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 0)
        tid = self.store.submit("s2", "t", "Fixture", "probe")
        op = self.bridge.prepare_chat(tid, "inflight", request(stream=True))
        raw = sse(stream_values()); original = self.store.event
        def event(t, key, value):
            result = original(t, key, value)
            if value["kind"] == "answer_draft": self.store.cancel(t)
            return result
        with patch.object(self.store, "event", side_effect=event):
            result = self.bridge.execute_chat(op, opener=Opener(raw, stream=True, pieces=[raw]))
        self.assertEqual(result["kind"], "cancelled"); self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 1)

    def test_live_gate_rejects_missing_permit_and_transport_injection(self):
        op = self.bridge.prepare_chat(self.tid, "live", request(), mock=False)
        with self.assertRaises(ValueError): self.bridge.execute_chat(op)
        with self.assertRaises(ValueError): self.bridge.execute_chat(op, opener=Opener())
        op2 = self.bridge.prepare_chat(self.tid, "mock", request())
        with self.assertRaises(ValueError): self.bridge.execute_chat(op2)
        self.assertEqual(self.bridge.batch.accounting()["request_reservations"], 0)

    def test_schema_dialect_checks_nested_constraints_and_rejects_unsupported(self):
        schema = {"type": "object", "properties": {"ids": {"type": "array", "maxItems": 2,
                  "items": {"type": "integer", "minimum": 0}}}, "required": ["ids"], "additionalProperties": False}
        schema_check(schema, {"ids": [1, 2]})
        for value in ({"ids": [True]}, {"ids": [-1]}, {"ids": [1, 2, 3]}, {"ids": [1], "extra": 1}):
            with self.assertRaises(ValueError): schema_check(schema, value)
        with self.assertRaises(ValueError): schema_check(schema | {"anyOf": []}, definition=True)

    def permit(self, **changes):
        m = request().manifest()
        return {"version": VERSION, "endpoint": m["profile"]["endpoint"], "model": MODEL,
                "campaign": "probe", "data_root": str(self.store.root), "limits": LIMITS, "adapter_code": code_identity(),
                "live_enabled": True, "authorization": "OFFLINE_FIXTURE_NOT_AUTHORITY", "unknown_retries": 0,
                "fee_control": "requests_and_conservative_tokens_actual_fee_unknown", "expires_at": time.time() + 60,
                "profiles": [m["profile"]], "request_limits": [m["limits"]], "case_messages": [m["wire"]["messages"]], **changes}

    def test_live_path_uses_same_bridge_and_replays_after_expiry_without_credential(self):
        permit = self.permit(); bridge = ChatSessions(self.store, "probe", LIMITS, permit=permit)
        op = bridge.prepare_chat(self.tid, "live-path", request(), mock=False)
        opener = Opener()
        with patch("genshin_corpus.generation.clean_chat_transport.build_opener", return_value=opener):
            result = bridge.execute_chat(op, api_key="fixture-secret")
        self.assertEqual(result["state"], "CONTENT_VERIFIED")
        with patch("genshin_corpus.generation.clean_chat_transport.time.time", return_value=permit["expires_at"] + 1):
            self.assertEqual(bridge.execute_chat(op), result)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(bridge.batch.accounting()["request_reservations"], 1)

    def test_live_expired_wrong_profile_missing_key_and_foreign_messages_never_reserve(self):
        for i, changes in enumerate(({"expires_at": 1}, {"profiles": []}, {"case_messages": []}, {"limits": {}})):
            bridge = ChatSessions(self.store, "probe", LIMITS, permit=self.permit(**changes))
            op = bridge.prepare_chat(self.tid, str(i), request(), mock=False)
            with self.assertRaises(ValueError): bridge.execute_chat(op, api_key="fixture-secret")
        bridge = ChatSessions(self.store, "probe", LIMITS, permit=self.permit())
        op = bridge.prepare_chat(self.tid, "no-key", request(), mock=False)
        with self.assertRaises(ValueError): bridge.execute_chat(op)
        self.assertEqual(bridge.batch.accounting()["request_reservations"], 0)

    def test_full_probe_preparation_eight_shared_attempts_and_zero_send_replay(self):
        root = self.root / "data/generation/p"
        with patch.object(Path, "cwd", return_value=self.root): prepared = clean_probe.prepare(root)
        clean_probe.approve(root, prepared["preparation_sha256"], "OFFLINE_FIXTURE_NOT_AUTHORITY")
        openers = []
        for _, req, expected in clean_probe.requests():
            reasoning = {"reasoning_content": "fixture reasoning"} if req.thinking == "enabled" else {}
            if req.tools:
                tools = {"role": "assistant", "content": None, "tool_calls": [call(), call("c2", arguments='{"handle":"tide"}')], **reasoning}
                if req.stream:
                    first = stream_values(tools=True); first.insert(1, chunk(reasoning))
                    last = [chunk({"role": "assistant", "content": ""}), chunk({"content": expected, **reasoning}),
                            chunk({}, "stop"), chunk({}, choices=[], usage=response()["usage"])]
                    openers += [Opener(sse(first), stream=True), Opener(sse(last), stream=True)]
                else:
                    openers += [Opener(json.dumps(response(tools, "tool_calls")).encode()),
                                Opener(json.dumps(response({"role": "assistant", "content": expected, **reasoning})).encode())]
            else: openers += [Opener(json.dumps(response({"role": "assistant", "content": expected, **reasoning})).encode())]
        with patch.dict("os.environ", {"TOKENMETRO_API_KEY": "fixture-secret"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", side_effect=openers) as build:
            result = clean_probe.run(root, notify=lambda value: None)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(build.call_count, 8)
        self.assertEqual(result["accounting"]["request_reservations"], 8)
        with patch("genshin_corpus.generation.clean_chat_transport.build_opener", side_effect=AssertionError()):
            self.assertEqual(clean_probe.run(root), result)
        caps = read(root / "report/capabilities.json")["capabilities"]
        self.assertEqual(len(caps), 5)
        self.assertTrue(all(c["status"] == "OBSERVED_LIMITED_PASS" for c in caps))

    def test_probe_stops_at_first_capability_failure_without_using_remaining_budget(self):
        root = self.root / "data/generation/p"
        with patch.object(Path, "cwd", return_value=self.root): prepared = clean_probe.prepare(root)
        clean_probe.approve(root, prepared["preparation_sha256"], "OFFLINE_FIXTURE_NOT_AUTHORITY")
        with patch.dict("os.environ", {"TOKENMETRO_API_KEY": "fixture-secret"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", return_value=Opener(json.dumps(response(model="wrong")).encode())) as build:
            result = clean_probe.run(root, notify=lambda value: None)
        self.assertEqual(result["status"], "STOPPED"); self.assertEqual(build.call_count, 1)
        self.assertEqual(result["accounting"]["request_reservations"], 1)

    def test_enabled_without_reasoning_stops_after_two_and_never_runs_tools(self):
        root = self.root / "data/generation/p"
        with patch.object(Path, "cwd", return_value=self.root): prepared = clean_probe.prepare(root)
        clean_probe.approve(root, prepared["preparation_sha256"], "OFFLINE_FIXTURE_NOT_AUTHORITY")
        openers = [Opener(json.dumps(response({"role": "assistant", "content": answer})).encode()) for answer in ("46", "2197")]
        with patch.dict("os.environ", {"TOKENMETRO_API_KEY": "fixture-secret"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", side_effect=openers) as build:
            result = clean_probe.run(root, notify=lambda value: None)
        self.assertEqual(result["status"], "STOPPED"); self.assertEqual(build.call_count, 2)
        self.assertEqual([c["status"] for c in result["cases"]], ["PASS", "STOPPED"])
        self.assertEqual(result["accounting"]["request_reservations"], 2)
        self.assertFalse((root / "tool-receipts").exists())


if __name__ == "__main__": unittest.main()
