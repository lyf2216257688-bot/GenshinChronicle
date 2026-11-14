"""Raw-first, retry-free Chat attempts through the existing session budget bridge."""
from contextlib import nullcontext
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from .. import model_calls as calls
from ..evidence_io import digest, read, require, seal, sha, write
from ..model_call_sessions import SessionCalls, TurnBatch
from ..rag.clean_store import put
from ..retrieval.local_qwen3_build import offline_only
from . import clean_chat as protocol
from .generation import _RejectRedirectHandler

VERSION = "gc-clean-chat-transport-1"
RESPONSE_BYTES = 8 * 1024 * 1024


def code_identity():
    return {"protocol": sha(protocol.__file__), "transport": sha(__file__)}


def attempt(root, manifest, *, key, opener, cancelled, emit):
    """One HTTP exchange. Caller supplies approval, shared scheduling and budget."""
    body = protocol.encoded(manifest["wire"])
    with (root / "request.body.bin").open("xb") as f:
        f.write(body); f.flush(); os.fsync(f.fileno())
    endpoint = manifest["profile"]["endpoint"]
    require(any(r["endpoint"] == endpoint and r["model"] == manifest["wire"]["model"]
                for r in protocol.ROUTES.values()), "UNSUPPORTED_CHAT_ROUTE")
    write(root / "request-metadata.json", {"endpoint": endpoint, "method": "POST",
        "transport_retries": 0, "redirects": False, "trust_env": False, "profile": manifest["profile"]})
    result = {"state": "UNKNOWN", "kind": "invalid", "calls": 0, "request_count": 0,
              "usage": "UNKNOWN", "provider_reported_usage": "UNKNOWN", "actual_fee": "UNKNOWN",
              "http_status": None, "error_codes": [], "content_validation": {"status": "NOT_COMPLETED"},
              "protocol_validation": {"status": "NOT_COMPLETED"}, "answer_text": None,
              "semantic_acceptance": "NOT_REVIEWED"}
    decoder = protocol.Decoder(manifest, emit)
    response = None; complete = False; started = time.monotonic()
    try:
        if cancelled(): raise calls.Deferred("CANCELLED_BEFORE_SEND")
        req = Request(endpoint, data=body, method="POST", headers={"Authorization": "Bearer " + key,
                      "Content-Type": "application/json", "Accept-Encoding": "identity"})
        result.update(calls=1, request_count="UNKNOWN")
        try: response = opener.open(req, timeout=manifest["limits"]["timeout_seconds"])
        except HTTPError as exc: response = exc
        result["http_status"] = response.status if hasattr(response, "status") else response.code
        result["request_count"] = 1
        headers = {k: response.headers[k] for k in ("content-type", "content-encoding", "retry-after") if k in response.headers}
        write(root / "http.json", {"status": result["http_status"], "headers": headers})
        size = 0; parse_error = None
        valid_http = result["http_status"] == 200
        supported_encoding = headers.get("content-encoding", "identity") in {"", "identity"}
        stream = manifest["wire"]["stream"]
        media = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        valid_media = media == ("text/event-stream" if stream else "application/json")
        # Archive every chunk and fsync BEFORE passing it to the decoder/event sink.
        with (root / "response.body.raw").open("xb") as raw:
            while True:
                if cancelled(): raise calls.Deferred("CANCELLED_IN_FLIGHT")
                if time.monotonic() - started > manifest["limits"]["timeout_seconds"]:
                    raise TimeoutError()
                chunk = response.read1(4096) if hasattr(response, "read1") else response.read(4096)
                if not chunk: break
                raw.write(chunk); raw.flush(); os.fsync(raw.fileno()); size += len(chunk)
                require(size <= RESPONSE_BYTES, "RESPONSE_SIZE_LIMIT")
                if stream and valid_http and supported_encoding and valid_media and parse_error is None:
                    try: decoder.feed(chunk)
                    except (ValueError, TypeError, KeyError) as exc: parse_error = exc
            complete = True
        require(valid_http, "HTTP_REJECTED")
        require(supported_encoding and valid_media, "UNSUPPORTED_RESPONSE_ENCODING_OR_MEDIA")
        if parse_error: raise parse_error
        parsed = decoder.finish(None if stream else (root / "response.body.raw").read_bytes())
        result.update(parsed)
        result["provider_reported_usage"] = decoder.usage
        result["protocol_validation"] = {"status": "PASS"}
        if parsed["kind"] in {"final_text", "tool_calls", "tool_argument_errors", "refusal"}:
            result.update(state="CONTENT_VERIFIED", content_validation={"status": "PASS", "scope": "protocol_only"})
        else:
            result.update(state="PROTOCOL_OR_CONTENT_INVALID", content_validation={"status": "FAIL"})
            result["error_codes"] = ["OUTPUT_TRUNCATED"]
    except Exception as exc:
        # Persist controlled codes/types only; never exception/provider echoes or auth headers.
        code = str(exc) if type(exc) in {ValueError, calls.Deferred} else type(exc).__name__
        safe = code if code and all(c.isupper() or c in "_0123456789" for c in code) else type(exc).__name__
        result["error_codes"].append(safe)
        result["failure_type"] = type(exc).__name__
        if isinstance(exc, calls.Deferred): result["kind"] = "cancelled"
        if result["calls"] == 0: result["state"] = "CANCELLED_BEFORE_SEND"
        elif not complete:
            result.update(state="UNKNOWN", stream_validation={"status": "FAIL"})
        else:
            result.update(state="PROTOCOL_OR_CONTENT_INVALID", content_validation={"status": "FAIL"})
            if manifest["wire"]["stream"] and (not decoder.done or decoder.finish_reason is None):
                result["stream_validation"] = {"status": "FAIL"}
    finally:
        if decoder.usage is not None:
            result["provider_reported_usage"] = decoder.usage
            try: result["usage"] = protocol.normalize_usage(decoder.usage)
            except ValueError: pass
        if response is not None:
            try: response.close()
            except Exception as exc: result["close_failure_type"] = type(exc).__name__
    # BaseException (process crash) intentionally leaves an unsealed UNKNOWN attempt.
    result["elapsed_seconds"] = time.monotonic() - started
    write(root / "decoded.json", {"assistant_message": result.pop("assistant_message", None),
                                  "reasoning_text": result.pop("reasoning_text", ""),
                                  "draft_text": result.pop("draft_text", "")})
    write(root / "trace.json", {"events": []})  # urllib does not prove TCP/TLS pre-send state.
    write(root / "result.json", result); seal(root)
    return result


class ChatSessions(SessionCalls):
    """New adapter; original G1 Mock bridge and all frozen identities stay unchanged."""
    def __init__(self, store, campaign, limits, *, permit=None):
        super().__init__(store, campaign, limits, policy=calls.Policy(max_attempts=1, unknown_retries=0, format_retries=0))
        self.permit = permit

    def prepare_chat(self, tid, key, request, *, mock=True):
        manifest = request.manifest()
        body = {"manifest": manifest, "adapter_code": code_identity(), "mode": "MOCK" if mock else "LIVE",
                "permit": self.permit if not mock else None}
        return self.prepare(tid, key, body, allowance=request.input_bytes + request.output_tokens,
                            channel="MOCK" if mock else manifest["profile"]["endpoint"], schema=protocol.VERSION)

    def _admit_live(self, plan):
        p = self.permit
        require(isinstance(p, dict) and p.get("live_enabled") is True and p.get("version") == VERSION
                and p.get("campaign") == self.campaign and p.get("data_root") == str(self.store.root)
                and p.get("limits") == self.limits and p.get("adapter_code") == code_identity()
                and p.get("endpoint") == protocol.ENDPOINT and p.get("model") == protocol.MODEL
                and isinstance(p.get("authorization"), str) and bool(p["authorization"].strip())
                and p.get("unknown_retries") == 0 and p.get("fee_control") == "requests_and_conservative_tokens_actual_fee_unknown"
                and type(p.get("expires_at")) in (int, float) and math.isfinite(p["expires_at"])
                and time.time() < p["expires_at"], "LIVE_PERMIT_MISSING_OR_EXPIRED")
        m = plan["body"]["manifest"]
        require(m["profile"] in p.get("profiles", []) and m["limits"] in p.get("request_limits", [])
                and m["wire"]["messages"][:2] in p.get("case_messages", [])
                and all(x["role"] in {"assistant", "tool"} for x in m["wire"]["messages"][2:])
                and plan["body"]["permit"] == p, "LIVE_REQUEST_OUTSIDE_PERMIT")
        put(self.root / "live-permit.json", p)

    def execute_chat(self, opid, *, opener=None, api_key=None):
        plan = self.operation(opid); body = plan["body"]
        mock = body["mode"] == "MOCK"
        endpoint = body["manifest"]["profile"]["endpoint"]
        require((mock and plan["channel"] == "MOCK" and opener is not None)
                or (not mock and plan["channel"] == endpoint and opener is None), "MOCK_LIVE_TRANSPORT_SEPARATION")
        state = self.reconcile(opid)
        if state["status"] not in {"sealed", "pending_finalize", "UNKNOWN"}:
            require(body["adapter_code"] == code_identity(), "INCOMPATIBLE_CHAT_ADAPTER")
        # Sealed results can reconcile/replay even after approval expires. No new send.
        if not mock and state["status"] not in {"sealed", "pending_finalize", "UNKNOWN"}:
            self._admit_live(plan)
            require(isinstance(api_key, str) and bool(api_key.strip()), "MODEL_API_KEY_REQUIRED")
        manifest = body["manifest"]
        sequence = 0

        def cancelled():
            return self.store.cancelled(plan["turn_id"])

        def emit(event):
            nonlocal sequence
            sequence += 1
            self.store.event(plan["turn_id"], f"chat-{opid}-{sequence:06d}",
                {**event, "operation_id": opid})

        def send(folder, headers):
            require(not headers, "IDEMPOTENCY_NOT_VERIFIED")
            if not mock:
                self._admit_live(plan)
                require(isinstance(api_key, str) and bool(api_key.strip()), "MODEL_API_KEY_REQUIRED")
            return attempt(folder, manifest, key="fixture-secret" if mock else api_key,
                           opener=opener if mock else build_opener(ProxyHandler({}), _RejectRedirectHandler()),
                           cancelled=cancelled, emit=emit)

        with offline_only() if mock else nullcontext():
            result = calls.run(self.root / "calls" / opid, plan, self.root / "ledger", send,
                channel=plan["channel"], policy=self.policy, batch=TurnBatch(self), token_allowance=plan["allowance"])
        self.reconcile(opid)
        return result


def tool_reply(root, call_id, name, arguments, result, *, origin):
    """Create/reuse an immutable receipt; caller executes only after full call validation."""
    value = {"version": VERSION, "origin": origin, "tool_call_id": call_id, "name": name,
             "arguments": arguments, "result": result}
    target = Path(root) / digest([origin, call_id])
    put(target / "receipt.json", value)
    if (target / "complete.json").exists():
        from ..evidence_io import verify
        verify(target)
    else: seal(target)
    return {"role": "tool", "tool_call_id": call_id, "content": protocol.encoded(result).decode()}


def assistant_from_seal(bridge, opid):
    require(bridge.reconcile(opid)["status"] == "sealed", "SEALED_CALL_REQUIRED")
    root = bridge.root / "calls" / opid
    require(read(root / "result.json")["state"] == "CONTENT_VERIFIED", "UNVERIFIED_ASSISTANT_MESSAGE")
    return read(root / "decoded.json")["assistant_message"]
