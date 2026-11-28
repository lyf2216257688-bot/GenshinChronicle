"""Finite official Chat dispatch for isolated DSH evidence evaluation."""

from dataclasses import dataclass, field
from decimal import Decimal
from http.client import HTTPException, IncompleteRead
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from genshin_corpus import model_calls
from genshin_corpus.evidence_io import digest, read, require, seal, strict_json, write
from genshin_corpus.generation import clean_chat
from genshin_corpus.generation.generation import _RejectRedirectHandler


ROUTE = clean_chat.ROUTES[clean_chat.DEFAULT_ROUTE]
INPUT_LIMIT = 8_000_000
INPUT_RESERVATION = 1_048_576
OUTPUT_PER_CALL = 2048
CALLS_PER_SIDE = 12
BATCH_CALLS = 144
SIDE_SECONDS = 1200
FEE_LIMIT_USD = Decimal("5")
INPUT_RATE_PER_MILLION = Decimal("0.30")
OUTPUT_RATE_PER_MILLION = Decimal("1.20")
TOOL_PREFIX = "mcp__gc__"


def maximum_fee(inputs, outputs):
    return (INPUT_RATE_PER_MILLION * inputs + OUTPUT_RATE_PER_MILLION * outputs) / 1_000_000


def wire_request(request):
    require(request["model"] == ROUTE["model"] and request["stream"] is True, "DSH_ROUTE_MISMATCH")
    messages = request["messages"]
    require(bool(messages) and all(m["role"] in {"system", "user", "assistant", "tool"}
            and (m.get("content") is None or isinstance(m.get("content"), str)) for m in messages),
            "DSH_TEXT_MESSAGES_REQUIRED")
    tools = [tool for tool in request.get("tools", []) if tool["type"] == "function"
             and tool["function"]["name"].startswith(TOOL_PREFIX)]
    require(bool(tools), "DSH_MCP_TOOLS_MISSING")
    require(all(c["function"]["name"].startswith(TOOL_PREFIX)
                for m in messages for c in m.get("tool_calls", [])), "DSH_FOREIGN_TOOL_HISTORY")
    return {"model": ROUTE["model"], "messages": messages, "tools": tools, "stream": True,
            "stream_options": {"include_usage": True}, "max_tokens": OUTPUT_PER_CALL,
            "thinking": {"type": "disabled"}}


def stream_usage(raw, offered):
    """Validate a complete official stream before relaying it to the host."""
    data = [line[6:] for line in raw.decode("utf-8").splitlines() if line.startswith("data: ")]
    require(bool(data) and data[-1] == "[DONE]" and "[DONE]" not in data[:-1], "INCOMPLETE_DSH_STREAM")
    finish, usage, response_id = None, None, None
    tool_names = {}
    for payload in data[:-1]:
        frame = strict_json(payload)
        require(frame.get("model") == ROUTE["model"] and frame.get("object") == "chat.completion.chunk"
                and isinstance(frame.get("id"), str) and (response_id is None or frame["id"] == response_id),
                "FOREIGN_DSH_RESPONSE")
        response_id = frame["id"]
        choices = frame["choices"]
        require(len(choices) <= 1 and all(c["index"] == 0 for c in choices), "AMBIGUOUS_DSH_RESPONSE")
        if choices:
            for call in choices[0]["delta"].get("tool_calls", []):
                index = call["index"]
                require(type(index) is int and 0 <= index < 16, "INVALID_DSH_TOOL_INDEX")
                tool_names[index] = tool_names.get(index, "") + call.get("function", {}).get("name", "")
        if choices and choices[0]["finish_reason"] is not None:
            require(finish is None, "REPEATED_DSH_FINISH")
            finish = choices[0]["finish_reason"]
        if frame.get("usage") is not None:
            require(usage is None and finish is not None, "UNEXPECTED_DSH_USAGE")
            usage = clean_chat.normalize_usage(frame["usage"])
    require(finish in {"stop", "tool_calls", "length", "content_filter"} and usage is not None, "MISSING_DSH_RECEIPT")
    require(usage["input_tokens"] <= INPUT_RESERVATION and usage["output_tokens"] <= OUTPUT_PER_CALL,
            "DSH_PROVIDER_USAGE_EXCEEDS_RESERVATION")
    require(all(name in offered for name in tool_names.values()) and (finish == "tool_calls") == bool(tool_names),
            "DSH_FOREIGN_RESPONSE_TOOL")
    return usage, finish, list(tool_names.values())


def send_attempt(root, wire, body, key, timeout, opener):
    with (root / "request.body.bin").open("xb") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    request = Request(ROUTE["endpoint"], data=body, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept-Encoding": "identity"})
    result = {"state": "UNKNOWN", "http_status": None, "calls": 1, "request_count": "UNKNOWN",
              "actual_fee": "UNKNOWN", "usage": "UNKNOWN"}
    try:
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response, (root / "response.body.raw").open("xb") as raw:
            result.update(http_status=response.status, request_count=1)
            ended = time.monotonic() + timeout
            try:
                while chunk := response.read1(32768):
                    raw.write(chunk)
                    require(raw.tell() <= 8 * 1024 * 1024 and time.monotonic() <= ended,
                            "DSH_RESPONSE_SIZE_OR_DEADLINE")
            except IncompleteRead as exc:
                raw.write(exc.partial)
                raise
            finally:
                raw.flush()
                os.fsync(raw.fileno())
        if result["http_status"] == 200:
            offered = {tool["function"]["name"] for tool in wire["tools"]}
            usage, finish, tools = stream_usage((root / "response.body.raw").read_bytes(), offered)
            result.update(state="CONTENT_VERIFIED", usage=usage, finish_reason=finish, tools=tools)
    except (ValueError, OSError, TypeError, KeyError, HTTPException) as exc:
        result.update(failure_type=type(exc).__name__, stream_validation={"status": "FAIL"})
    write(root / "result.json", result)
    seal(root)
    return result


@dataclass
class Side:
    name: str
    deadline: float
    dispatched: int = 0
    opens: int = 0
    follows: int = 0
    exhausted: bool = False
    keys: set = field(default_factory=set)


class Dispatch:
    """A new process/root per approved batch. A stopped batch cannot be reopened."""

    def __init__(self, root, *, approved_registration, key=None, mock_opener=None):
        require((mock_opener is None and bool(key) and len(approved_registration) == 64)
                or (mock_opener is not None and key is None and approved_registration == "OFFLINE"),
                "DSH_GRANT_REQUIRED")
        self.root = Path(root)
        self.root.mkdir()
        self.mock = mock_opener is not None
        self.opener = mock_opener or build_opener(ProxyHandler({}), _RejectRedirectHandler())
        self.key = key or "mock-only"
        self.shared = model_calls.Batch(self.root / "shared-budget", max_requests=BATCH_CALLS)
        self.sides = {}
        self.stopped = False
        self.input_tokens = self.output_tokens = self.dispatched = 0
        write(self.root / "registration.json", {"approved_registration": approved_registration, "mock": self.mock,
            "route": ROUTE["endpoint"], "model": ROUTE["model"], "batch_calls": BATCH_CALLS,
            "input_limit": INPUT_LIMIT, "output_limit": BATCH_CALLS * OUTPUT_PER_CALL,
            "input_reservation_per_request": INPUT_RESERVATION, "output_cap_per_request": OUTPUT_PER_CALL,
            "fee_limit_usd": str(FEE_LIMIT_USD), "side_seconds": SIDE_SECONDS, "calls_per_side": CALLS_PER_SIDE})

    def open_side(self, name):
        require(name not in self.sides and len(self.sides) < 12, "DSH_SIDE_NOT_REGISTERED")
        self.sides[name] = Side(name, time.monotonic() + SIDE_SECONDS)

    def stop(self, reason):
        if not self.stopped:
            self.stopped = True
            write(self.root / "stopped.json", {"reason": reason, "attempts": self.dispatched,
                  "input_tokens_verified": self.input_tokens, "output_tokens_verified": self.output_tokens})

    def forward(self, name, request):
        require(not self.stopped, "DSH_BATCH_STOPPED")
        side = self.sides[name]
        remaining = side.deadline - time.monotonic()
        require(remaining > 0 and side.dispatched < CALLS_PER_SIDE and not side.exhausted, "DSH_SIDE_BUDGET")
        try:
            wire = wire_request(request)
        except (ValueError, KeyError, TypeError):
            self.stop("DSH_REQUEST_CONTRACT")
            raise
        body = clean_chat.encoded(wire)
        fingerprint = digest([name, wire])
        if fingerprint in side.keys:
            self.stop("DSH_DUPLICATE_REQUEST")
            raise model_calls.Deferred("DSH_DUPLICATE_REQUEST")
        max_input = self.input_tokens + INPUT_RESERVATION
        max_output = self.output_tokens + OUTPUT_PER_CALL
        if (self.dispatched >= BATCH_CALLS or max_input > INPUT_LIMIT
                or maximum_fee(max_input, max_output) > FEE_LIMIT_USD):
            self.stop("DSH_BATCH_BUDGET")
            raise model_calls.Deferred("DSH_BATCH_BUDGET")
        self.dispatched += 1
        side.dispatched += 1
        side.keys.add(fingerprint)
        path = self.root / "calls" / f"{self.dispatched:04d}"
        try:
            plan = {"fingerprint": fingerprint, "side": name, "wire_sha256": digest(wire),
                    "endpoint": ROUTE["endpoint"], "mock": self.mock}
            model_calls.run(path, plan, self.root / "ledger",
                lambda attempt, _: send_attempt(attempt, wire, body, self.key, min(remaining, 180), self.opener),
                channel="DSH_OFFLINE_MOCK" if self.mock else ROUTE["endpoint"],
                policy=model_calls.Policy(max_attempts=1), batch=self.shared,
                token_allowance=INPUT_RESERVATION + OUTPUT_PER_CALL)
            attempt, = (path / "attempts").iterdir()
            result = read(attempt / "result.json")
            require(result["state"] == "CONTENT_VERIFIED" and result["http_status"] == 200,
                    "DSH_UPSTREAM_FAILED_OR_UNKNOWN")
            self.input_tokens += result["usage"]["input_tokens"]
            self.output_tokens += result["usage"]["output_tokens"]
        except Exception as exc:
            self.stop(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
            raise
        side.opens += result["tools"].count(TOOL_PREFIX + "open_study")
        side.follows += result["tools"].count(TOOL_PREFIX + "follow_query")
        if side.opens > 1 or side.follows > 2:
            side.exhausted = True
            raise model_calls.Deferred("DSH_SIDE_QUERY_BUDGET")
        return (attempt / "response.body.raw").read_bytes()
