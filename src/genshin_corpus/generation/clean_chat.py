"""Versioned Clean Chat protocol. Parsing never executes a tool or accepts an answer semantically."""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re

from ..evidence_io import digest, require, strict_json

VERSION = "gc-clean-chat-1"
ENDPOINT = "https://tokenmetro.com/v1/chat/completions"
MODEL = "deepseek-v4.1-flash-1017"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def schema_check(schema, value=None, *, definition=False):
    """Deliberately bounded tool-schema dialect; unsupported keywords fail closed.

    No remote strict-schema enforcement is assumed. Every supported constraint
    is checked locally, including nested objects, enums and bounded arrays.
    """
    require(isinstance(schema, dict), "SCHEMA_OBJECT_REQUIRED")
    kind = schema.get("type")
    allowed = {"type", "description", "enum"}
    allowed |= {"object": {"properties", "required", "additionalProperties"},
                "array": {"items", "minItems", "maxItems"},
                "string": {"minLength", "maxLength"},
                "integer": {"minimum", "maximum"}, "number": {"minimum", "maximum"},
                "boolean": set()}.get(kind, set())
    require(kind in {"object", "array", "string", "integer", "number", "boolean"}
            and set(schema) <= allowed, "UNSUPPORTED_TOOL_SCHEMA")
    if kind == "object":
        props = schema.get("properties")
        required = schema.get("required")
        require(isinstance(props, dict) and isinstance(required, list)
                and all(isinstance(k, str) for k in required) and len(set(required)) == len(required)
                and set(required) <= set(props) and schema.get("additionalProperties") is False,
                "CLOSED_OBJECT_SCHEMA_REQUIRED")
        for child in props.values(): schema_check(child, definition=True)
    if kind == "array":
        schema_check(schema.get("items"), definition=True)
        require(type(schema.get("maxItems")) is int and schema["maxItems"] >= 0, "BOUNDED_ARRAY_REQUIRED")
    for low, high in (("minItems", "maxItems"), ("minLength", "maxLength"), ("minimum", "maximum")):
        for key in (low, high):
            if key in schema:
                n = schema[key]
                require(type(n) in (int, float) and math.isfinite(n), "INVALID_SCHEMA_BOUND")
                if key not in {"minimum", "maximum"}: require(type(n) is int and n >= 0, "INVALID_SCHEMA_BOUND")
        if low in schema and high in schema: require(schema[low] <= schema[high], "INVALID_SCHEMA_BOUND")
    if "enum" in schema:
        require(isinstance(schema["enum"], list) and bool(schema["enum"]), "INVALID_SCHEMA_ENUM")
        for item in schema["enum"]:
            schema_check({k: v for k, v in schema.items() if k != "enum"}, item)
    if definition: return
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float) and math.isfinite(value), "boolean": type(value) is bool}
    require(valid[kind], "TOOL_ARGUMENT_TYPE")
    if "enum" in schema: require(any(type(value) is type(v) and value == v for v in schema["enum"]), "TOOL_ARGUMENT_ENUM")
    if kind == "object":
        require(set(value) <= set(props) and set(required) <= set(value), "TOOL_ARGUMENT_KEYS")
        for key, item in value.items(): schema_check(props[key], item)
    if kind == "array":
        require(schema.get("minItems", 0) <= len(value) <= schema["maxItems"], "TOOL_ARGUMENT_LENGTH")
        for item in value: schema_check(schema["items"], item)
    if kind == "string":
        require(schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", math.inf), "TOOL_ARGUMENT_LENGTH")
    if kind in {"integer", "number"}:
        require(schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf), "TOOL_ARGUMENT_RANGE")


def tool_registry(tools):
    require(isinstance(tools, list), "TOOLS_LIST_REQUIRED")
    registry = {}
    for tool in tools:
        require(set(tool) == {"type", "function"} and tool["type"] == "function", "FUNCTION_TOOL_REQUIRED")
        f = tool["function"]
        require(isinstance(f, dict) and set(f) == {"name", "description", "parameters"}
                and isinstance(f["description"], str) and isinstance(f["name"], str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", f["name"])
                and f["name"] not in registry, "INVALID_TOOL_DEFINITION")
        schema_check(f["parameters"], definition=True)
        require(f["parameters"]["type"] == "object", "TOOL_OBJECT_ARGUMENT_REQUIRED")
        registry[f["name"]] = f["parameters"]
    return registry


def validate_calls(tool_calls, registry):
    require(isinstance(tool_calls, list) and 0 < len(tool_calls) <= 16, "INVALID_TOOL_CALLS")
    seen, parsed = set(), []
    for call in tool_calls:
        require(isinstance(call, dict) and set(call) == {"id", "type", "function"}
                and isinstance(call["id"], str) and bool(call["id"]) and call["id"] not in seen
                and call["type"] == "function", "INVALID_TOOL_CALL_ID")
        seen.add(call["id"])
        f = call["function"]
        require(isinstance(f, dict) and set(f) == {"name", "arguments"}
                and f["name"] in registry and isinstance(f["arguments"], str), "UNKNOWN_TOOL_OR_ARGUMENTS")
        args = strict_json(f["arguments"])
        schema_check(registry[f["name"]], args)
        parsed.append({"id": call["id"], "name": f["name"], "arguments": args})
    return parsed


def validate_messages(messages, registry):
    require(isinstance(messages, list) and bool(messages), "MESSAGES_REQUIRED")
    pending, seen = set(), set()
    for message in messages:
        require(isinstance(message, dict), "INVALID_MESSAGE")
        role = message.get("role")
        allowed = {"system": {"role", "content"}, "user": {"role", "content"},
                   "assistant": {"role", "content", "reasoning_content", "tool_calls"},
                   "tool": {"role", "content", "tool_call_id"}}
        require(role in allowed and set(message) <= allowed[role], "UNSUPPORTED_MESSAGE_FIELDS")
        require(message.get("content") is None and role == "assistant" or isinstance(message.get("content"), str),
                "TEXT_MESSAGE_REQUIRED")
        if "reasoning_content" in message: require(isinstance(message["reasoning_content"], str), "INVALID_REASONING")
        if role == "tool":
            cid = message.get("tool_call_id")
            require(cid in pending, "ORPHAN_OR_DUPLICATE_TOOL_RESULT")
            pending.remove(cid)
        else:
            require(not pending, "INCOMPLETE_TOOL_PROTOCOL_UNIT")
            if message.get("tool_calls"):
                validate_calls(message["tool_calls"], registry)
                ids = {c["id"] for c in message["tool_calls"]}
                require(not seen & ids, "REUSED_TOOL_CALL_ID")
                seen |= ids; pending = ids
    require(not pending, "INCOMPLETE_TOOL_PROTOCOL_UNIT")


@dataclass(frozen=True)
class ChatRequest:
    messages: list
    tools: list
    thinking: str
    stream: bool
    output_tokens: int
    input_bytes: int
    context_tokens: int
    timeout_seconds: int
    prompt_version: str

    def manifest(self):
        require(self.thinking in {"enabled", "disabled"} and type(self.stream) is bool, "INVALID_PROFILE")
        for n in (self.output_tokens, self.input_bytes, self.context_tokens, self.timeout_seconds):
            require(type(n) is int and n > 0, "FINITE_REQUEST_LIMIT_REQUIRED")
        require(isinstance(self.prompt_version, str) and bool(self.prompt_version), "PROMPT_VERSION_REQUIRED")
        registry = tool_registry(self.tools)
        validate_messages(self.messages, registry)
        wire = {"model": MODEL, "messages": deepcopy(self.messages), "stream": self.stream,
                "thinking": {"type": self.thinking}, "max_tokens": self.output_tokens}
        if self.tools: wire.update(tools=deepcopy(self.tools), tool_choice="auto")
        if self.stream: wire["stream_options"] = {"include_usage": True}
        size = len(encoded(wire))
        require(size <= self.input_bytes and size + self.output_tokens <= self.context_tokens, "WHOLE_MESSAGE_BUDGET_EXCEEDED")
        profile = {"version": VERSION, "endpoint": ENDPOINT, "model": MODEL, "surface": "chat_completions",
                   "thinking": self.thinking, "stream": self.stream, "tool_schema_sha256": digest(self.tools),
                   "prompt_version": self.prompt_version, "protocol": "native_tools" if self.tools else "text",
                   "reasoning_history": "preserve_verbatim", "status": "NOT_LIVE_VALIDATED", "evidence": []}
        return {"version": VERSION, "profile": profile, "wire": wire,
                "limits": {"input_bytes": self.input_bytes, "output_tokens": self.output_tokens,
                           "context_tokens": self.context_tokens, "timeout_seconds": self.timeout_seconds},
                "estimated_input_tokens": size, "estimator": "conservative_utf8_bytes_not_vendor_tokenizer",
                "semantic_acceptance": "NOT_REVIEWED"}


def normalize_usage(usage):
    require(isinstance(usage, dict), "USAGE_MISSING")
    values = [usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")]
    require(all(type(n) is int and n >= 0 for n in values) and sum(values[:2]) == values[2], "USAGE_INVALID")
    details = usage.get("completion_tokens_details") or {}
    require(isinstance(details, dict), "USAGE_INVALID")
    n = details.get("reasoning_tokens", 0)
    require(type(n) is int and 0 <= n <= values[1], "USAGE_INVALID")
    return dict(zip(("input_tokens", "output_tokens", "total_tokens"), values))


class Decoder:
    """SSE frames may split anywhere, even inside UTF-8. Only finish() yields tools."""
    def __init__(self, manifest, emit=lambda event: None):
        self.manifest, self.emit = manifest, emit
        self.registry = tool_registry(manifest["wire"].get("tools", []))
        self.buffer = b""; self.done = False; self.identity = None; self.usage = None
        self.message = {"role": "assistant", "content": ""}
        self.calls = {}; self.finish_reason = None; self.role_seen = False

    def identity_check(self, obj, expected):
        require(isinstance(obj, dict) and not obj.get("error") and obj.get("object") == expected
                and obj.get("model") == MODEL and isinstance(obj.get("id"), str) and bool(obj["id"]), "RESPONSE_IDENTITY_INVALID")
        if self.identity is not None: require(self.identity == obj["id"], "RESPONSE_ID_CHANGED")
        self.identity = obj["id"]

    def usage_check(self, obj):
        if obj.get("usage") is not None:
            require(self.usage is None, "DUPLICATE_USAGE")
            self.usage = obj["usage"]

    def feed(self, chunk):
        self.buffer += chunk
        while (boundary := re.search(br"\r\n\r\n|\n\n|\r\r", self.buffer)):
            frame, self.buffer = self.buffer[:boundary.start()], self.buffer[boundary.end():]
            lines = frame.decode("utf-8").splitlines()
            data = []
            for line in lines:
                if not line or line.startswith(":"): continue
                field, _, value = line.partition(":")
                if value.startswith(" "): value = value[1:]
                require(field in {"data", "event", "id", "retry"}, "UNSUPPORTED_SSE_FIELD")
                if field == "event": require(value in {"message", ""}, "UNSUPPORTED_SSE_EVENT")
                if field == "data": data.append(value)
            if not data: continue
            require(not self.done, "SSE_AFTER_DONE")
            text = "\n".join(data)
            if text == "[DONE]": self.done = True; continue
            self.chunk(strict_json(text))

    def chunk(self, obj):
        self.identity_check(obj, "chat.completion.chunk"); self.usage_check(obj)
        choices = obj.get("choices")
        require(isinstance(choices, list), "INVALID_CHOICES")
        if not choices:
            require(obj.get("usage") is not None and self.finish_reason is not None, "UNEXPECTED_EMPTY_CHUNK")
            return
        require(len(choices) == 1 and choices[0].get("index") == 0, "AMBIGUOUS_CHOICE")
        choice = choices[0]; delta = choice.get("delta")
        require(isinstance(delta, dict) and set(delta) <= {"role", "content", "reasoning_content", "tool_calls", "refusal"},
                "UNSUPPORTED_DELTA")
        require(self.finish_reason is None, "DELTA_AFTER_FINISH")
        if "role" in delta:
            require(delta["role"] == "assistant" and not self.role_seen, "INVALID_ASSISTANT_ROLE")
            self.role_seen = True
        for key in ("content", "reasoning_content", "refusal"):
            text = delta.get(key)
            if text is None: continue
            require(isinstance(text, str), "INVALID_DELTA_TEXT")
            self.message[key] = self.message.get(key, "") + text
            if text and key == "content": self.emit({"kind": "answer_draft", "text": text, "final": False})
        if "tool_calls" in delta:
            require(isinstance(delta["tool_calls"], list), "INVALID_TOOL_DELTA")
            for part in delta["tool_calls"]:
                require(isinstance(part, dict) and set(part) <= {"index", "id", "type", "function"}
                        and type(part.get("index")) is int and 0 <= part["index"] < 16, "INVALID_TOOL_INDEX")
                target = self.calls.setdefault(part["index"], {"function": {"name": "", "arguments": ""}})
                for key in ("id", "type"):
                    if key in part:
                        require(isinstance(part[key], str) and (key not in target or target[key] == part[key]), "TOOL_ID_CHANGED")
                        target[key] = part[key]
                if "function" in part:
                    f = part["function"]
                    require(isinstance(f, dict) and set(f) <= {"name", "arguments"}, "INVALID_TOOL_FUNCTION_DELTA")
                    for key, text in f.items():
                        require(isinstance(text, str), "INVALID_TOOL_ARGUMENT_FRAGMENT")
                        target["function"][key] += text
        reason = choice.get("finish_reason")
        if reason is not None:
            require(isinstance(reason, str), "INVALID_FINISH")
            self.finish_reason = reason

    def finish(self, body=None):
        if self.manifest["wire"]["stream"]:
            require(self.done and not self.buffer.strip() and self.role_seen and self.finish_reason is not None, "STREAM_INCOMPLETE")
            require(sorted(self.calls) == list(range(len(self.calls))), "TOOL_INDEX_GAP")
            if self.calls: self.message["tool_calls"] = [self.calls[i] for i in sorted(self.calls)]
        else:
            obj = strict_json(body.decode("utf-8")); self.identity_check(obj, "chat.completion")
            self.usage_check(obj); choices = obj.get("choices")
            require(isinstance(choices, list) and len(choices) == 1 and choices[0].get("index") == 0, "AMBIGUOUS_CHOICE")
            self.message = choices[0].get("message"); self.finish_reason = choices[0].get("finish_reason")
        usage = normalize_usage(self.usage)
        limits = self.manifest["limits"]
        require(usage["output_tokens"] <= limits["output_tokens"] and usage["input_tokens"] <= limits["input_bytes"]
                and usage["total_tokens"] <= limits["context_tokens"], "PROVIDER_TOKEN_LIMIT_EXCEEDED")
        m = self.message
        require(isinstance(m, dict) and m.get("role") == "assistant"
                and set(m) <= {"role", "content", "reasoning_content", "refusal", "tool_calls"}, "UNSUPPORTED_ASSISTANT_MESSAGE")
        for key in ("content", "reasoning_content", "refusal"):
            require(m.get(key) is None or isinstance(m[key], str), "INVALID_ASSISTANT_TEXT")
        reasoning = m.get("reasoning_content") or ""
        details = self.usage.get("completion_tokens_details") or {}
        if self.manifest["wire"]["thinking"]["type"] == "disabled":
            require(not reasoning and not details.get("reasoning_tokens"), "THINKING_DISABLE_NOT_OBSERVED")
        reason = self.finish_reason; tools = m.get("tool_calls") or []
        require(reason in {"stop", "tool_calls", "length", "content_filter"}, "UNSUPPORTED_FINISH_REASON")
        parsed = []
        if reason == "tool_calls":
            require(not m.get("refusal"), "MIXED_REFUSAL_TOOLS")
            parsed = validate_calls(tools, self.registry)
            kind = "tool_calls"
        elif reason in {"length", "content_filter"}: kind = "incomplete" if reason == "length" else "refusal"
        else:
            require(not tools, "TOOLS_WITHOUT_TOOL_FINISH")
            kind = "refusal" if m.get("refusal") else "final_text"
            if kind == "final_text": require(isinstance(m.get("content"), str) and bool(m["content"].strip()), "EMPTY_ANSWER")
        history = {k: deepcopy(v) for k, v in m.items() if k in {"role", "content", "reasoning_content", "tool_calls"}
                   and (k in {"role", "content"} or v is not None)}
        return {"kind": kind, "answer_text": m.get("content") if kind == "final_text" else None,
                "draft_text": m.get("content") or "", "reasoning_text": reasoning,
                "assistant_message": history, "tool_calls": parsed, "finish_reason": reason,
                "provider_request_id": self.identity, "usage": usage,
                "thinking_observation": "visible_reasoning" if reasoning else "no_visible_reasoning",
                "semantic_acceptance": "NOT_REVIEWED"}
